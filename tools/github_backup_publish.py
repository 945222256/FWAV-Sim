"""Create a screened backup commit using an isolated index; never push implicitly.

The active branch, working files and real index are untouched. The publication
branch has only screened snapshots as ancestors, never the old local history.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
REF = "refs/heads/github-backup"
OUT = ROOT / "outputs/github_backup_20261015"
screen = runpy.run_path(str(ROOT / "tools/github_backup_prepare.py"))
manifest = screen["manifest"]
if manifest["findings"] or manifest["large"]:
    raise SystemExit("备份筛选未通过；查看本地 selection.json，未生成提交。")

def git(*args, input=None, env=None):
    return subprocess.run(["git", *args], cwd=ROOT, input=input, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout

head_before = git("rev-parse", "HEAD").decode().strip()
branch_before = git("symbolic-ref", "HEAD").decode().strip()
real_index = Path(git("rev-parse", "--git-path", "index").decode().strip())
if not real_index.is_absolute():
    real_index = ROOT / real_index
index_before = hashlib.sha256(real_index.read_bytes()).hexdigest()
old_ref_result = subprocess.run(["git", "rev-parse", "--verify", REF], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
parent = old_ref_result.stdout.decode().strip() if old_ref_result.returncode == 0 else None
if parent:
    # Refuse to continue if someone attached original history to this branch.
    for root_commit in git("rev-list", "--max-parents=0", parent).decode().splitlines():
        message = git("show", "-s", "--format=%B", root_commit).decode()
        if "Screened GitHub backup snapshot" not in message:
            raise SystemExit("备份分支来源不符合筛选快照约定，停止。")

index = OUT / ("publication-index-" + str(os.getpid()))
env = dict(os.environ, GIT_INDEX_FILE=str(index))
try:
    git("read-tree", "--empty", env=env)
    paths = ("\0".join(manifest["selected"]) + "\0").encode()
    git("-c", "core.autocrlf=false", "add", "--pathspec-from-file=-", "--pathspec-file-nul", input=paths, env=env)
    submodules = []
    for record in git("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        meta, path_raw = record.split(b"\t", 1)
        mode, oid, stage = meta.decode().split()
        if mode == "160000":
            path = path_raw.decode()
            actual = git("-C", path, "rev-parse", "HEAD").decode().strip()
            if actual != oid:
                raise SystemExit("第三方固定版本与检出版本不同：" + path)
            git("update-index", "--add", "--cacheinfo", "160000," + oid + "," + path, env=env)
            dirty = bool(git("-C", path, "status", "--porcelain", "-uno").strip())
            submodules.append({"path": path, "commit": oid, "local_tracked_changes": dirty})
    # Scan the exact staged blobs, not just the worktree that can change while
    # other roles continue working. Reports contain paths/line numbers only.
    entries = []
    for record in git("ls-files", "--stage", "-z", env=env).split(b"\0"):
        if record:
            meta, path_raw = record.split(b"\t", 1)
            mode, oid, stage = meta.decode().split()
            if mode != "160000":
                entries.append((path_raw.decode(), oid))
    findings = []
    large = []
    total_bytes = 0
    batch = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for name, oid in entries:
            batch.stdin.write((oid + "\n").encode())
            batch.stdin.flush()
            header = batch.stdout.readline().decode().split()
            assert header[0] == oid and header[1] == "blob"
            size = int(header[2])
            data = batch.stdout.read(size)
            assert batch.stdout.read(1) == b"\n"
            total_bytes += size
            if size > 50_000_000:
                large.append({"path": name, "bytes": size})
            for value in screen["known"]:
                if value in data:
                    findings.append({"path": name, "kind": "known_live_credential"})
                    break
            if b"\0" not in data[:8192]:
                for kind, pattern in screen["patterns"].items():
                    for match in pattern.finditer(data):
                        findings.append({"path": name, "line": data[:match.start()].count(b"\n")+1, "kind": kind})
    finally:
        batch.stdin.close()
        batch.wait(timeout=30)
    scan = {"files": len(entries), "bytes": total_bytes, "findings": findings, "large": large, "submodules": submodules}
    (OUT / "staged_scan.json").write_text(json.dumps(scan, ensure_ascii=False, indent=2), encoding="utf-8")
    if findings or large:
        raise SystemExit("实际提交内容扫描失败，未生成提交；查看 staged_scan.json。")
    if any(s["local_tracked_changes"] for s in submodules):
        raise SystemExit("第三方仓库存在未备份的源码修改，需单独核对，未生成提交。")
    tree = git("write-tree", env=env).decode().strip()
    args = ["commit-tree", tree]
    if parent:
        args += ["-p", parent]
    message = (
        "Screened GitHub backup snapshot (2026-10-15)\n\n"
        "Back up current source, configuration, tests, design/research documents, "
        "parameter semantics and curated evidence. Exclude credentials, personal "
        "files, private runtime/session state, dependencies, caches and bulk outputs.\n\n"
        "Original local development history remains local; this publication snapshot "
        "does not certify deployment, acceptance or closure.\n"
    )
    commit = git(*args, input=message.encode()).decode().strip()
    # The compare-and-swap protects a parallel backup writer. Other development
    # commits are allowed; we never update their branch or real staging area.
    git("update-ref", REF, commit, parent or "0" * 40)
    index_after = hashlib.sha256(real_index.read_bytes()).hexdigest()
    receipt = {"commit": commit, "tree": tree, "parent": parent, "publication_ref": REF,
               "development_head_at_start": head_before, "active_branch_at_start": branch_before,
               "active_branch_after": git("symbolic-ref", "HEAD").decode().strip(),
               "real_index_unchanged": index_before == index_after,
               "files": len(entries), "bytes": total_bytes, "submodules": submodules,
               "excluded_tracked": len(manifest["excluded_tracked"]), "scan_passed": True,
               "pushed": False}
    (OUT / "publication_commit.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
finally:
    if index.exists():
        index.unlink()
