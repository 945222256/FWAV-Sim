"""FW-IID 组级汇总器：读全部 logs/fw_pair_*.json，按前缀分组输出事件率、
威尔逊置信区间与首差样本表（方案第九节统计口径的执行工具）。

用法：python tools/fw_iid_summarize.py [前缀1 前缀2 ...]
不带参数时汇总全部；带前缀时只汇总匹配组。输出人读 markdown 表与
logs/fw_summary_<前缀>.json 档案。
"""

import glob
import json
import math
import os
import sys

import numpy as np


def wilson(k, n, z=1.96):
    """威尔逊得分区间：返回 (点估计, 下界, 上界)。"""
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


def main(argv):
    logs = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "logs")
    files = sorted(glob.glob(os.path.join(logs, "fw_pair_*.json")))
    if argv:
        files = [f for f in files
                 if any(os.path.basename(f)[8:-5].startswith(a)
                        for a in argv)]
    rows = []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as exc:
            print(f"[SUM] 跳过损坏档案 {f}: {exc!r}")
            continue
        tag = os.path.basename(f)[8:-5]
        rows.append({
            "tag": tag, "identical": bool(d.get("identical")),
            "s_star": d.get("s_star"), "t_star": d.get("t_star"),
            "first_channel": d.get("first_channel"),
            "bits32": d.get("first_bits32"),
            "n_diff": d.get("n_diff_steps"),
            "steps": d.get("steps_compared"),
        })
    # 分组（按 tag 去掉 -pN 对号后缀）
    groups = {}
    for r in rows:
        g = r["tag"].rsplit("-p", 1)[0] if "-p" in r["tag"] else r["tag"]
        groups.setdefault(g, []).append(r)

    print("| 组 | 对数 | 分歧对 | 事件率 | 威尔逊95%区间 |")
    print("| --- | --- | --- | --- | --- |")
    out = {}
    for g, rs in sorted(groups.items()):
        n = len(rs)
        k = sum(1 for r in rs if not r["identical"])
        p, lo, hi = wilson(k, n)
        print(f"| {g} | {n} | {k} | {p:.3f} | [{lo:.3f}, {hi:.3f}] |")
        out[g] = {"n_pairs": n, "divergent": k,
                  "rate": p, "wilson_lo": lo, "wilson_hi": hi,
                  "pairs": rs}
        # 首差样本明细（只列分歧对）
        for r in rs:
            if not r["identical"]:
                print(f"  首差：{r['tag']} s*={r['s_star']}"
                      f"(t={r['t_star']:.2f}s) c*={r['first_channel']}"
                      f" 位差32={r['bits32']}")
    for g, d in out.items():
        path = os.path.join(logs, f"fw_summary_{g}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
    print(f"[SUM] 共 {len(rows)} 对，{len(groups)} 组；组档案已写 logs/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
