# -*- coding: utf-8 -*-
"""i191_pipeline.py — I-191 逐键语义填写管线（首班基线）。

第一步（本脚本）：对 audit_rows 的 319 键做依据引用的机器核验——
每键的「原依据」里提取 文件:行号 或 文件 引用，核对该引用在仓库中
存在；产出逐键基线 i191_baseline.json（引用可解析性、同名组归属、
非重名首批清单），语义填写在其上分批进行。
仅使用 Python 标准库。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROWS = os.path.join(ROOT, "logs", "hq_intake", "doro_tools_review_20261012",
                    "param_meaning_audit", "audit_rows.json")

_REF_RE = re.compile(r"([A-Za-z0-9_./\\\-]+\.(?:py|json|md|urdf|mjs|ts))"
                     r"(?::(\d+))?")


def check_reference(text):
    """核对依据文本中的文件引用（存在性 + 行号界内）。"""
    results = []
    for m in _REF_RE.finditer(text or ""):
        path, line = m.group(1), m.group(2)
        cand = path.replace("\\", "/")
        full = None
        for base in (ROOT,):
            for probe in (os.path.join(base, cand), cand):
                if os.path.exists(probe):
                    full = probe
                    break
            if full:
                break
        status = {"ref": path, "line": int(line) if line else None,
                  "exists": bool(full)}
        if full and line:
            try:
                n = sum(1 for _ in open(full, encoding="utf-8",
                                        errors="ignore"))
                status["line_in_range"] = 1 <= int(line) <= n
                status["file_lines"] = n
            except OSError:
                status["line_in_range"] = None
        results.append(status)
    return results


def main():
    data = json.load(open(ROWS, encoding="utf-8"))
    rows = data["rows"]
    baseline = []
    for r in rows:
        refs = check_reference(r.get("原依据", ""))
        baseline.append({
            "参数键": r.get("参数键"),
            "原中文名称": r.get("原中文名称"),
            "参数族": r.get("参数族"),
            "同名组键数": r.get("同名组键数", 0),
            "问题类型": r.get("问题类型", ""),
            "单位": r.get("原单位"),
            "refs": refs,
            "refs_ok": bool(refs) and all(x["exists"] for x in refs),
            "依据含文件": bool(refs),
        })
    ok = sum(1 for b in baseline if b["refs_ok"])
    no_ref = sum(1 for b in baseline if not b["依据含文件"])
    broken = sum(1 for b in baseline if b["依据含文件"] and not b["refs_ok"])
    dup = [b for b in baseline if (b["同名组键数"] or 0) > 1]
    first_batch = [b["参数键"] for b in baseline
                   if (b["同名组键数"] or 0) == 0 and b["refs_ok"]]
    out = {"generated_at": __import__("datetime").datetime.now().isoformat(
               timespec="seconds"),
           "summary": {"总键数": len(baseline), "依据引用可解析": ok,
                       "依据无文件引用": no_ref, "引用破损": broken,
                       "重名组成员": len(dup), "非重名且引用可解析（首批）": len(first_batch)},
           "first_batch_keys": first_batch,
           "baseline": baseline}
    dst = os.path.join(ROOT, "logs", "i191_pipeline_baseline_20261013.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))
    print("基线已存：%s" % os.path.relpath(dst, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
