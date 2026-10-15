"""Inventory ignored paths and screen a publication snapshot without printing secrets."""
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/github_backup_20261015"
OUT.mkdir(exist_ok=True, parents=True)
sys.stdout.reconfigure(encoding="utf-8")

def git(*args, input=None):
    return subprocess.run(["git", *args], cwd=ROOT, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout

tracked = set(git("ls-files", "-z").decode().split("\0")) - {""}
untracked = set(git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")) - {""}
names = sorted(p for p in tracked | untracked if (ROOT / p).is_file())
raw = ("\0".join(names) + "\0").encode()
check = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=ROOT, input=raw, stdout=subprocess.PIPE, check=False)
assert check.returncode in (0, 1)
ignored = set(check.stdout.decode().split("\0")) - {""}
selected = [p for p in names if p not in ignored]

known = set()
for source in (ROOT / "lp_sync/cloud_secret.json", ROOT / "cloud_board/secret.json"):
    if source.exists():
        obj = json.loads(source.read_text(encoding="utf-8-sig"))
        def visit(value, label=""):
            if isinstance(value, dict):
                for k,v in value.items():
                    visit(v, k)
            elif isinstance(value, str) and len(value) >= 6 and re.search(r"password|token|secret|api_key", label, re.I):
                known.add(value.encode())
        visit(obj)
for name in ("lp_sync/agent_token", "lp_sync/agent_token_b"):
    p = ROOT / name
    if p.exists() and len(p.read_bytes().strip()) >= 8:
        known.add(p.read_bytes().strip())

patterns = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider_token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,})"),
    "credential_url": re.compile(rb"https?://[^\s/:@]+:[^\s/@]+@"),
    "literal_credential": re.compile(rb'''(?i)(?:["']?(?:ssh_password|board_password|password|passwd|api_key|access_token|agent_token|bearer_token)["']?)\s*[:=]\s*["']([^"'\r\n]{4,})["']'''),
}
findings = []
large = []
for name in selected:
    p = ROOT / name
    data = p.read_bytes()
    if len(data) > 50_000_000:
        large.append({"path": name, "bytes": len(data)})
    # Binary documents are not searched using a text pattern, but exact live
    # credential matching still applies to their raw bytes.
    for value in known:
        pos = data.find(value)
        if pos >= 0:
            findings.append({"path": name, "line": data[:pos].count(b"\n") + 1, "kind": "known_live_credential"})
            break
    if b"\0" in data[:8192]:
        continue
    for kind, pattern in patterns.items():
        for match in pattern.finditer(data):
            findings.append({"path": name, "line": data[:match.start()].count(b"\n") + 1, "kind": kind})

manifest = {"selected": selected, "excluded_tracked": sorted(tracked & ignored), "excluded": sorted(ignored), "bytes": sum((ROOT/p).stat().st_size for p in selected), "findings": findings, "large": large}
(OUT / "selection.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"selected_count": len(selected), "selected_mib": round(manifest["bytes"]/1048576, 2), "excluded_tracked": len(manifest["excluded_tracked"]), "top": Counter(p.split('/')[0] if '/' in p else '(root)' for p in selected), "large": large, "findings": findings}, ensure_ascii=False, indent=2))
