"""Read-only Git backup inventory; never print credential values."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/github_backup_20261015"
OUT.mkdir(parents=True, exist_ok=True)

def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)

tracked = set(git("ls-files", "-z").decode("utf-8").split("\0")) - {""}
untracked = set(git("ls-files", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")) - {""}
rows = []
for name in sorted(tracked | untracked):
    p = ROOT / name
    if p.is_file():
        rows.append({"path": name, "bytes": p.stat().st_size, "tracked": name in tracked})
totals = defaultdict(lambda: {"files": 0, "bytes": 0})
for r in rows:
    top = r["path"].split("/")[0] if "/" in r["path"] else "(root)"
    totals[top]["files"] += 1
    totals[top]["bytes"] += r["bytes"]
secret_names = [r for r in rows if any(x in Path(r["path"]).name.lower() for x in ("secret", "token", "credential", ".env", "账单", "家宽"))]
result = {
    "head": git("rev-parse", "HEAD").decode().strip(),
    "staged_count": len(git("diff", "--cached", "--name-only", "-z").split(b"\0")) - 1,
    "tracked": len(tracked), "untracked": len(untracked),
    "totals": dict(sorted(totals.items(), key=lambda kv: -kv[1]["bytes"])),
    "large": sorted((r for r in rows if r["bytes"] > 10_000_000), key=lambda r: -r["bytes"]),
    "sensitive_filenames": secret_names,
    "extensions": Counter(Path(r["path"]).suffix.lower() for r in rows),
    "files": rows,
}
(OUT / "inventory.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: v for k,v in result.items() if k not in ("files", "extensions")}, ensure_ascii=False, indent=2))
