# -*- coding: utf-8 -*-
"""
analyze_bench.py — 台架扩展试验分析（2026-09-19 运控建模线）
================================================================
用法：
  python tools/analyze_bench.py trim    <csv>           # Fx(θ) 配平归零角 + ∂Fx/∂θ
  python tools/analyze_bench.py windcal <csv>           # Fx(v)/L̄(v) 速压律 + ∂Fx/∂v
  python tools/analyze_bench.py kick    <csv> <axis>    # 单轴衰减 → 阻尼导数
                                                        #   axis = wx | wy | wz | pitch_x
段取尾 60% 均值（与 analyze_da.py 同口径），kick 段用全段。
"""
import csv
import math
import os
import re
import sys
from collections import defaultdict

csv.field_size_limit(2**31 - 1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASS = 0.70          # kg（cfg.MASS）
G = 9.81
IXX = 0.015          # kg·m²（URDF 0.012 + 翼/尾贡献，rl/hover_env.py 口径）


def load(csv_path):
    with open(csv_path, encoding="utf-8") as _f:
        _rd = csv.DictReader(_f)
        rows = list(_rd)
        _names = set(_rd.fieldnames or [])
        _has_frz = "Frz" in _names
        _has_frx = "Frx" in _names
        _has_fry = "Fry" in _names
    seg = defaultdict(list)
    for r in rows:
        p = r.get("phase") or ""
        try:
            d = {k: float(r[k]) for k in
                 ("t", "Fx", "Fy", "Fz", "Mx", "My", "Mz",
                  "TMx", "TMy", "TMz",
                  "roll", "pitch", "wx", "wy", "wz")
                 if k in r} | {k: float(r[k]) for k in
                               ("vx", "vy", "qw", "qz")
                               if k in r}
            # I-030/I-043：判据取列统一每步合计口径；旧记录缺合计列时
            # 按二倍分摊值回退（记录器列头声明认可的等价式，比值实测 2.0）
            d["Frz"] = float(r["Frz"]) if (_has_frz and r.get("Frz")) \
                else 2.0 * float(r["Fz"])
            d["Frx"] = float(r["Frx"]) if (_has_frx and r.get("Frx")) \
                else 2.0 * float(r["Fx"])
            d["Fry"] = float(r["Fry"]) if (_has_fry and r.get("Fry")) \
                else 2.0 * float(r["Fy"])
            seg[p].append(d)
        except (ValueError, KeyError):
            continue
    return seg


def tail_mean(vals, frac=0.6):
    if not vals:
        return float("nan")
    k = int(len(vals) * (1 - frac))
    seg = vals[k:]
    return sum(seg) / max(1, len(seg))


def do_trim(seg):
    pts = []
    for label, rows in seg.items():
        m = re.match(r"trim_p([+-]\d+)$", label)
        if m and rows:
            deg = float(m.group(1))
            # I-043：前向力取 Frx 每步合计列，Fx 为单次触发分摊值禁用
            fx = tail_mean([r["Frx"] for r in rows])
            # I-030：升力一律取 Frz 每步合计列，Fz 为单次触发分摊值禁用
            fz = tail_mean([r["Frz"] for r in rows])
            pts.append((deg, fx, fz))
    pts.sort()
    print("| θ (deg) | Frx (N) | Frz (N) |")
    print("|---|---|---|")
    for deg, fx, fz in pts:
        print(f"| {deg:+.0f} | {fx:+.3f} | {fz:+.3f} |")
    if len(pts) >= 3:
        n = len(pts)
        xs = [math.radians(d) for d, _, _ in pts]
        ys = [fx for _, fx, _ in pts]
        xm, ym = sum(xs) / n, sum(ys) / n
        a = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) \
            / sum((x - xm) ** 2 for x in xs)
        b = ym - a * xm
        theta_trim = -b / a
        print(f"\n线性拟合 Fx = {a:+.3f}·θ(rad) {b:+.3f}")
        print(f"**配平角 θ_trim = {math.degrees(theta_trim):+.2f}°**（Fx=0 处）")
        print(f"**∂Fx/∂θ = {a:+.3f} N/rad**（升力矢量倾斜理论值 ≈ -L̄ = "
              f"{-tail_mean([p[2] for p in pts]):+.2f} N/rad 量级）")
        print(f"配平点升力 Fz(θ_trim) ≈ {tail_mean([p[2] for p in pts]):+.2f} N "
              f"(体重 {MASS*G:.2f} N)")


def do_windcal(seg):
    pts = []
    for label, rows in seg.items():
        m = re.match(r"wind_v(\d+\.\d+)$", label)
        if m and rows:
            v = float(m.group(1))
            # I-043：前向力取 Frx 每步合计列
            fx = tail_mean([r["Frx"] for r in rows])
            # I-030：升力一律取 Frz 每步合计列
            fz = tail_mean([r["Frz"] for r in rows])
            pts.append((v, fx, fz))
    pts.sort()
    print("| v_air (m/s) | Frx (N) | Frz (N) |")
    print("|---|---|---|")
    for v, fx, fz in pts:
        print(f"| {v:.1f} | {fx:+.3f} | {fz:+.3f} |")
    if len(pts) >= 3:
        n = len(pts)
        xs = [p[0] for p in pts]
        fxm = [p[1] for p in pts]
        xm, ym = sum(xs) / n, sum(fxm) / n
        a = sum((x - xm) * (y - ym) for x, y in zip(xs, fxm)) \
            / sum((x - xm) ** 2 for x in xs)
        print(f"\n**∂Fx/∂v ≈ {a:+.3f} N/(m/s)**（全段线性拟合；速度阻尼主项）")
        l0 = pts[0][2]
        print(f"升力增量 ΔL̄(v) = L̄(v) − L̄(0)，L̄(0) = {l0:+.2f} N：")
        for v, _, fz in pts[1:]:
            print(f"  v={v:.1f}: ΔL̄={fz-l0:+.2f} N, ΔL̄/v²={(fz-l0)/v/v:+.3f} "
                  f"(常数则平方律成立)")


def do_kick(seg, axis):
    rows = seg.get("free") or []
    if not rows:
        print("无 free 段数据")
        return
    rows = sorted(rows, key=lambda r: r["t"])
    t0 = rows[0]["t"]
    col = {"wx": "wx", "wy": "wy", "wz": "wz"}.get(axis)
    if col is None:  # pitch_x：俯仰角漂移 + 前向速度链验证
        ts = [r["t"] - t0 for r in rows]
        # pitch 列存的是四元数 qy 分量：纯俯仰下 θ = 2·asin(qy)
        thetas = [2.0 * math.asin(max(-1.0, min(1.0, r["pitch"])))
                  for r in rows]
        vxs = [r.get("vx", float("nan")) for r in rows]
        print(f"free 段 {len(rows)} 行 {ts[-1]:.1f}s：θ 首 "
              f"{math.degrees(thetas[0]):+.2f}° 末 {math.degrees(thetas[-1]):+.2f}°")
        print(f"θ 轨迹（每 2s）：" + " ".join(
            f"{math.degrees(th):+.1f}°" for th, t in zip(thetas, ts)
            if abs(t % 2.0) < 0.04))
        if not math.isnan(vxs[0]):
            print(f"vx 首 {vxs[0]:+.3f} 末 {vxs[-1]:+.3f} m/s")
            # 末段 1/3 的 vx 斜率 = 实测 Fx(θ)/m
            k0 = int(len(ts) * 2 / 3)
            n = len(ts) - k0
            if n > 5:
                tm = sum(ts[k0:]) / n
                vm = sum(vxs[k0:]) / n
                slope = sum((t - tm) * (v - vm)
                            for t, v in zip(ts[k0:], vxs[k0:])) \
                    / sum((t - tm) ** 2 for t in ts[k0:])
                thm = math.degrees(sum(thetas[k0:]) / n)
                print(f"末段：θ̄={thm:+.2f}°，dvx/dt={slope:+.3f} m/s² "
                      f"→ 实测 Fx(θ)/m；对照 trim 表 Fx({thm:.0f}°) 预测值")
        return
    ts = [r["t"] - t0 for r in rows]
    ws = [r[col] for r in rows]
    print(f"free 段 {len(rows)} 行 {ts[-1]:.2f}s：{col} 首 {ws[0]:+.3f} "
          f"末 {ws[-1]:+.4f} rad/s")
    # w(t) = a·exp(-t/τ) + b 最小二乘
    try:
        from scipy.optimize import curve_fit
        import numpy as np

        def model(t, a, tau, b):
            return a * np.exp(-t / tau) + b
        p0 = [ws[0], 1.0, 0.0]
        popt, _ = curve_fit(model, np.asarray(ts), np.asarray(ws), p0=p0,
                            maxfev=20000)
        a, tau, b = popt
        resid = [w - model(t, *popt) for t, w in zip(ts, ws)]
        rms = math.sqrt(sum(r * r for r in resid) / len(resid))
        print(f"拟合 w(t) = {a:+.3f}·exp(-t/{tau:.3f}s) {b:+.4f}，残差 RMS {rms:.4f}")
        print(f"**时间常数 τ = {tau:.3f} s**")
        print(f"**A 矩阵速率系数（如 L_p/I_xx）= -1/τ = {-1/tau:+.3f} 1/s**")
        print(f"若取 I_xx = {IXX} kg·m²：阻尼导数 ≈ {-IXX/tau:+.4f} N·m·s/rad")
        print(f"残存平衡速率 w_ss = {b:+.4f} rad/s（非零=有恒定扰动力矩）")
    except ImportError:
        # 无 scipy 的兜底：对 |w-b| 取对数线性拟合（b 用末段均值）
        b = sum(ws[-20:]) / min(20, len(ws))
        import numpy as np
        logs = [(t, math.log(abs(w - b))) for t, w in zip(ts, ws)
                if abs(w - b) > 1e-4]
        n = len(logs)
        xm = sum(t for t, _ in logs) / n
        ym = sum(l for _, l in logs) / n
        slope = sum((t - xm) * (l - ym) for t, l in logs) \
            / sum((t - xm) ** 2 for t, _ in logs)
        print(f"对数线性拟合：衰减率 {slope:+.3f} 1/s → τ = {-1/slope:.3f} s "
              f"(w_ss={b:+.4f})")


def do_rate(seg, axis):
    """定速台架：每速率段尾 60% 均值力矩 → 线性拟合斜率 = 阻尼导数。

    姿态角在段内自由积分（速率被处方），力矩记录是世界系——按行用四元数
    （qw/qx/qy/qz 列）旋回机体系再统计，避免大角度下的坐标系污染。
    """
    aidx = {"wx": 0, "wy": 1, "wz": 2}.get(axis)
    if aidx is None:
        print(f"未知轴 {axis}")
        return

    def body_torque(r):
        qw, qx, qy, qz = r.get("qw", 1.0), r["roll"], r["pitch"], r.get("qz", 0.0)
        # R^T · T_world（四元数 [w,x,y,z] 的逆旋转）
        tx, ty, tz = r["TMx"], r["TMy"], r["TMz"]
        bx = (1 - 2*(qy*qy + qz*qz)) * tx + 2*(qx*qy + qw*qz) * ty \
            + 2*(qx*qz - qw*qy) * tz
        by = 2*(qx*qy - qw*qz) * tx + (1 - 2*(qx*qx + qz*qz)) * ty \
            + 2*(qy*qz + qw*qx) * tz
        bz = 2*(qx*qz + qw*qy) * tx + 2*(qy*qz - qw*qx) * ty \
            + (1 - 2*(qx*qx + qy*qy)) * tz
        return (bx, by, bz)[aidx]

    pts = []
    for label, rows in seg.items():
        m = re.match(r"rate_(\w+)_([+-]\d+\.\d+)$", label)
        if m and rows:
            v = float(m.group(2))
            tm = tail_mean([body_torque(r) for r in rows])
            pts.append((v, tm, len(rows)))
    pts.sort()
    print(f"| 处方速率 {axis} (rad/s) | 机体系周期平均力矩 (N·m) | n |")
    print("|---|---|---|")
    for v, tm, n in pts:
        print(f"| {v:+.2f} | {tm:+.4f} | {n} |")
    if len(pts) >= 3:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        n = len(pts)
        xm, ym = sum(xs) / n, sum(ys) / n
        a = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) \
            / sum((x - xm) ** 2 for x in xs)
        b = ym - a * xm
        print(f"\n线性拟合 T = {a:+.4f}·w {b:+.4f}")
        print(f"**阻尼导数 ∂T/∂{axis} = {a:+.4f} N·m·s/rad**")
        print(f"**A 矩阵速率系数 = ∂T/∂w / I = {a / IXX:+.2f} 1/s**"
              f"（取 I={IXX} kg·m²）")
        print(f"零漂（常值扰动力矩）= {b:+.4f} N·m")


def do_stepx(seg):
    """俯仰阶跃→x 速度链：vx(t) 初段斜率 = Fx(θ)/m，末段趋终端速度。"""
    for label in sorted(seg):
        if not label.startswith("stepx_p"):
            continue
        rows = sorted(seg[label], key=lambda r: r["t"])
        if not rows or "vx" not in rows[0]:
            continue
        t0 = rows[0]["t"]
        ts = [r["t"] - t0 for r in rows]
        vxs = [r["vx"] for r in rows]
        deg = label.replace("stepx_p", "")
        print(f"\n段 {label}（θ={deg}°，{len(rows)} 行 {ts[-1]:.1f}s）：")
        print("  vx(t) 每 1s：" + " ".join(
            f"{v:+.3f}" for v, t in zip(vxs, ts) if abs(t % 1.0) < 0.04))
        if ts[-1] > 3:
            k1 = [i for i, t in enumerate(ts) if t <= 2.0]
            k2 = [i for i, t in enumerate(ts) if t >= ts[-1] - 2.0]
            if len(k1) > 5:
                tm = sum(ts[i] for i in k1) / len(k1)
                vm = sum(vxs[i] for i in k1) / len(k1)
                slope = sum((ts[i] - tm) * (vxs[i] - vm) for i in k1) \
                    / sum((ts[i] - tm) ** 2 for i in k1)
                print(f"  初段(0~2s) dvx/dt = {slope:+.3f} m/s² "
                      f"→ 实测 Fx/m = {slope:+.3f}（Fx = {slope*MASS:+.3f} N）")
            if len(k2) > 5:
                vt = sum(vxs[i] for i in k2) / len(k2)
                print(f"  末段终端 vx ≈ {vt:+.3f} m/s"
                      f"（对照预测：Fx(θ)/3.17，3.17=windcal 速度阻尼）")


def do_rudder_v(seg):
    """尾翼 B(v)：每风速点 ±阶跃配对差/2δ，尾翼力矩列（Mx/My/Mz）。"""
    chmap = {"elevator": ("TMy", cfg_step(0.10)),
             "aileron": ("TMx", cfg_step(0.10)),
             "rudder": ("TMz", cfg_step(0.15))}
    rows_out = []
    for label, rows in seg.items():
        m = re.match(r"rudv_v(\d+\.\d+)_(\w+)_([+-]\d+\.\d+)$", label)
        if m and rows:
            v, ch, amp = float(m.group(1)), m.group(2), float(m.group(3))
            col = chmap[ch][0]
            tm = tail_mean([r[col] for r in rows])
            rows_out.append((v, ch, amp, tm))
    speeds = sorted({r[0] for r in rows_out})
    print("| v (m/s) | 通道 | B (N·m/rad) | B/v² |")
    print("|---|---|---|---|")
    gains = defaultdict(dict)
    for v in speeds:
        for ch in ("elevator", "aileron", "rudder"):
            pos = [tm for vv, c, a, tm in rows_out
                   if vv == v and c == ch and a > 0]
            neg = [tm for vv, c, a, tm in rows_out
                   if vv == v and c == ch and a < 0]
            if pos and neg:
                amp = [a for vv, c, a, tm in rows_out if vv == v and c == ch][0]
                b = (pos[0] - neg[0]) / (2 * abs(amp))
                gains[ch][v] = b
                print(f"| {v:.1f} | {ch} | {b:+.4f} | "
                      f"{(b / v / v) if v > 0 else float('nan'):+.4f} |")
    print()
    for ch in ("elevator", "aileron", "rudder"):
        g = gains[ch]
        if 5.0 in g:
            print(f"{ch}: B(5m/s)={g[5.0]:+.4f} N·m/rad（对照 9-18 单点锚定值）")


def cfg_step(x):
    return x


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    mode, csv_path = sys.argv[1], sys.argv[2]
    seg = load(csv_path)
    print(f"# {mode} 分析：{os.path.basename(csv_path)}\n")
    if mode == "trim":
        do_trim(seg)
    elif mode == "windcal":
        do_windcal(seg)
    elif mode == "kick":
        do_kick(seg, sys.argv[3] if len(sys.argv) > 3 else "wx")
    elif mode == "rate":
        do_rate(seg, sys.argv[3] if len(sys.argv) > 3 else "wx")
    elif mode == "stepx":
        do_stepx(seg)
    elif mode == "rudder_v":
        do_rudder_v(seg)
    else:
        print(f"未知分析模式 {mode}")
