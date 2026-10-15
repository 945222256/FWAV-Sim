# -*- coding: utf-8 -*-
"""
analyze_da.py — 翼差动舵效分析：ident_stream.csv → B_dA(f) 表
=============================================================
用法：python tools/analyze_da.py [csv=logs/ident_stream.csv]
段取尾 60% 均值；正负阶跃配对差消除慢漂移；输出 ΔMx/Δ(2·amp)。
注意 dA 力矩在翼（总力矩 TMx 列），不在尾舵列。
"""
import csv
import os
import re
import sys
from collections import defaultdict

# 防御：脏 CSV 的超大字段不再崩解析器（2026-09-19）。
# 注意 Windows 上 sys.maxsize 超出 csv 的 C long 上限会 OverflowError，
# 必须用 2**31-1。
csv.field_size_limit(2**31 - 1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "logs", "ident_stream.csv")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "logs", "ident_result_da.md")


def tail_mean(vals, frac=0.6):
    if not vals:
        return None
    k = int(len(vals) * (1 - frac))
    seg = vals[k:]
    return sum(seg) / max(1, len(seg))


def main():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    # 列名：Mx=尾舵力矩，TMx=总力矩（dA 的滚转力矩在总列）
    seg = defaultdict(list)
    for r in rows:
        m = re.match(r"da_f(\d+\.\d+)_([+-]\d+\.\d+)$", r["phase"] or "")
        if m:
            seg[(float(m.group(1)), float(m.group(2)))].append(
                (float(r["TMx"]), float(r["Mx"]), float(r["Fz"])))
    if not seg:
        print("无 da_ 阶跃段数据")
        return
    freqs = sorted({k[0] for k in seg})
    lines = ["# 翼差动舵效 B_dA(f)（钉固台架）", "",
             "| f (Hz) | ±amp (rad) | ΔTMx (N·m) | B_dA (N·m/rad) | 配对对称性 |",
             "|---|---|---|---|---|"]
    by_f = defaultdict(list)
    for f in freqs:
        for amp in sorted({k[1] for k in seg if k[0] == f}):
            if (f, amp) not in seg or (f, -amp) not in seg:
                continue
            mx_p = tail_mean([v[0] for v in seg[(f, amp)]])
            mx_m = tail_mean([v[0] for v in seg[(f, -amp)]])
            tt_p = tail_mean([v[1] for v in seg[(f, amp)]])
            tt_m = tail_mean([v[1] for v in seg[(f, -amp)]])
            if mx_p is None or mx_m is None:
                continue
            d_mx = mx_p - mx_m
            b = d_mx / (2 * amp)
            # 对称性：单边增益应相等
            base = tail_mean([v[0] for v in seg.get((f, 0.0), [])] or [0])
            g_p = (mx_p - base) / amp if seg.get((f, 0.0)) else float("nan")
            g_m = (mx_m - base) / amp if seg.get((f, 0.0)) else float("nan")
            sym = "–" if g_p != g_p else f"{g_p:+.2f}/{g_m:+.2f}"
            lines.append(f"| {f:.2f} | {amp:.2f} | {d_mx:+.3f} | {b:+.3f} | {sym} |")
            by_f[f].append(b)
    lines += ["", "**B_dA(f) 均值（配对法）：**"]
    mg = 6.87
    for f in freqs:
        bs = by_f.get(f)
        if bs:
            import statistics
            bm = statistics.mean(bs)
            lines.append(f"- f={f:.2f} Hz: B_dA = {bm:+.3f} N·m/rad"
                         f"（f² 规律检验：{bm/(f*f):+.4f}）")
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()
