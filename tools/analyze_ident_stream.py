# -*- coding: utf-8 -*-
"""
analyze_ident_stream.py — 高密度试验记录（ident_stream.csv）分段统计与拟合
============================================================================
与 run_ident.py 的区别：数据来自 main_sim 进程内 30Hz 落盘（无 HTTP 采样瓶颈），
每个相位段有数百个样本，取段尾 40% 做均值并给出标准差（不确定性量化）。

用法：python tools/analyze_ident_stream.py [csv_path]
"""
import csv
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "logs", "ident_stream.csv")
sys.path.insert(0, ROOT)
import config.sim_config as cfg  # noqa: E402

MG = cfg.MASS * 9.81


def load():
    with open(CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        has_frz = "Frz" in (reader.fieldnames or [])
    keys = ("t", "freq", "Fx", "Fy", "Fz", "Mx", "My", "Mz",
            "TMx", "TMy", "TMz", "z", "roll", "pitch",
            "wx", "wy", "wz", "tension")
    extra = [c for c in ("Frz", "Frx", "Fry") if c in (reader.fieldnames or [])]
    keys = keys + tuple(extra)
    for r in rows:
        for k in keys:
            r[k] = float(r[k])
        # I-030/I-043：判据取列统一每步合计口径；旧时代记录缺合计列时
        # 按二倍分摊值回退（记录器列头声明认可的等价式，两列之比实测 2.0）
        if "Frz" not in extra:
            r["Frz"] = 2.0 * r["Fz"]
        if "Frx" not in extra:
            r["Frx"] = 2.0 * r["Fx"]
        if "Fry" not in extra:
            r["Fry"] = 2.0 * r["Fy"]
    return rows


def segments(rows, prefix):
    segs, cur, key = [], [], None
    for r in rows:
        if r["phase"].startswith(prefix):
            if r["phase"] != key and cur:
                segs.append((key, cur))
                cur = []
            key = r["phase"]
            cur.append(r)
        elif cur:
            segs.append((key, cur))
            cur, key = [], None
    if cur:
        segs.append((key, cur))
    return segs


def tail_stats(seg, col, frac=0.6):
    n0 = int(len(seg) * (1 - frac))
    vals = [r[col] for r in seg[n0:]] or [r[col] for r in seg]
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / max(1, n - 1)
    return mean, math.sqrt(var), n


def main():
    rows = load()
    print(f"# ident_stream 分析：{len(rows)} 行 @30Hz，体重 = {MG:.2f} N\n")

    # ── 扫频拟合 ──
    sw = segments(rows, "sweep_f")
    if sw:
        print("## 扫频（尾 40% 均值 ± 标准差）\n")
        print("| f_cmd (Hz) | f_实测 | L̄ (N) | σ_L | L̄/mg | T̄=Frx (N) | z (m) | 样本数 |")
        print("|---|---|---|---|---|---|---|---|")
        data = []
        for key, seg in sw:
            f_cmd = float(key.split("_f")[1])
            fm, _, _ = tail_stats(seg, "freq")
            # I-030/I-043：升力与前向力一律取每步合计列，
            # Fz 与 Fx 为单次触发分摊值禁用
            L, sL, n = tail_stats(seg, "Frz")
            Tx, _, _ = tail_stats(seg, "Frx")
            z, _, _ = tail_stats(seg, "z")
            data.append((fm, L, Tx))
            print(f"| {f_cmd:.2f} | {fm:.3f} | {L:+.2f} | {sL:.2f} | "
                  f"{L / MG:+.2f} | {Tx:+.2f} | {z:.2f} | {n} |")
        # 用实测频率拟合 L = k f^2（过原点）与 L = a f^2 + b
        num = sum(f * f * L for f, L, _ in data)
        den = sum(f ** 4 for f, _, _ in data)
        k0 = num / den if den else 0.0
        n_ = len(data)
        sf2 = sum(f * f for f, _, _ in data)
        sf4 = sum(f ** 4 for f, _, _ in data)
        sf2L = sum(f * f * L for f, L, _ in data)
        sL_ = sum(L for _, L, _ in data)
        det = n_ * sf4 - sf2 * sf2
        a = (n_ * sf2L - sf2 * sL_) / det if det else 0.0
        b = (sL_ - a * sf2) / n_ if n_ else 0.0
        f_hover = math.sqrt((MG - b) / a) if a > 0 else float("nan")
        resid = [L - (a * f * f + b) for f, L, _ in data]
        rms = math.sqrt(sum(r * r for r in resid) / n_)
        print(f"\n- 过原点拟合：**L̄ = {k0:.3f}·f²**，f_hover(L=mg) = "
              f"{math.sqrt(MG / k0) if k0 > 0 else float('nan'):.2f} Hz")
        print(f"- 二参数拟合：**L̄ = {a:.3f}·f² {'+' if b >= 0 else '-'} {abs(b):.2f}**，"
              f"残差 RMS = {rms:.2f} N，**f_hover = {f_hover:.2f} Hz**")

    # ── 舵效（含正负阶跃、基线 settle）──
    ru = segments(rows, "rud_")
    ru = [(k, s) for k, s in ru if not k.endswith("_back")]
    if ru:
        st = segments(rows, "settle")
        base = st[0][1] if st else []
        b0 = {c: tail_stats(base, c)[0] if base else 0.0 for c in ("Mx", "My", "Mz")}
        print("\n## 舵效：ΔM/δ（基线=settle 段均值，尾 40%）\n")
        print("| 通道 | δ (rad) | ΔMx | ΔMy | ΔMz | 本通道增益 ± σ (N·m/rad) | 样本数 |")
        print("|---|---|---|---|---|---|---|")
        for key, seg in ru:
            parts = key.split("_")
            ch, amp = parts[1], float(parts[2])
            out = {}
            for c in ("Mx", "My", "Mz"):
                m, s, n = tail_stats(seg, c)
                out[c] = (m - b0[c], s)
            gain_col = {"elevator": "My", "aileron": "Mx", "rudder": "Mz"}[ch]
            g, gs = out[gain_col]
            print(f"| {ch} | {amp:+.2f} | {out['Mx'][0]:+.3f} | {out['My'][0]:+.3f} | "
                  f"{out['Mz'][0]:+.3f} | {g:+.3f} ± {gs:.3f} | {n} |")


if __name__ == "__main__":
    main()
