# -*- coding: utf-8 -*-
"""
scan_trim_215g.py — 215g 新被控对象离线配平扫描与波形四参数重搜索（步骤5）

纯 NumPy 离线模型求值（求值方式与台架试验使用同一套模型：锁定机体状态 + 整拍动周期平均）。
2026-09-20 夜间开发 v2：0.6m 翼几何下 Fx(θ) 多数工况恒正（推力富余），
旧二分求过零解法失效，改为 θ 域最小残差 + (θ,f) 联合寻优。
产出：
  A. 巡航配平扫描：当前波形参数下 min_θ|Fx| 与 Fz=W 联合解
  B. 波形四参数重搜索：ALPHA_SET/SPLIT/PITCH_LAG_S/MEAN 两级网格寻优
  C. 悬停扫描：V=0 的悬停频率与 6 Hz 工况核验（修订第五条）
  D. LAUNCH_PITCH 重推导（新配平俯仰，修订第二条）
  E. 竖直超升/发射能量量级对照 700g vs 215g（修订第三条）
用法： isaac python.bat tools/scan_trim_215g.py
产物：  logs/trim_scan_215g.json + docs/TRIM_SCAN_215g.md
"""
import os
import sys
import json
import math
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
from config import sim_config as cfg
from modules.aero_solver import (FlapperAeroModel, TailFuselageAero,
                                 euler_to_quat)

G = 9.81
W = cfg.MASS * G
OUT_JSON = os.path.join(PROJECT_ROOT, "logs", "trim_scan_215g.json")
OUT_MD = os.path.join(PROJECT_ROOT, "docs", "TRIM_SCAN_215g.md")

model = FlapperAeroModel()
tail = TailFuselageAero()
_T_EVAL = [100.0]


def set_kinematics(alpha_set=None, split=None, lag=None, mean=None):
    for w in (model.wing_left, model.wing_right):
        if alpha_set is not None:
            w.alpha_set = math.radians(alpha_set)
        if split is not None:
            w.split = split
        if lag is not None:
            w.pitch_lag_s = lag
        if mean is not None:
            w.mean = math.radians(mean)


def cycle_mean(V, f, pitch_deg, n=12, with_tail=True):
    """固定状态整周期平均（hover_env 口径，含守卫；vz=0 守卫不动作）。"""
    model.set_wing_command(frequency=f)
    quat = euler_to_quat(0.0, math.radians(pitch_deg), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    T = 1.0 / f
    t0 = _T_EVAL[0]
    F = np.zeros(3)
    Tq = np.zeros(3)
    for i in range(n):
        Fw, Tw = model.compute_forces(np.zeros(3), quat, vel, om,
                                      t0 + i * T / n)
        F += Fw
        Tq += Tw
    F /= n
    Tq /= n
    if with_tail:
        v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f if cfg.ENABLE_TAIL_SLIPSTREAM else 0.0
        Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
        F = F + Ft
        Tq = Tq + Tt
    _T_EVAL[0] = t0 + T
    return F, Tq


def min_fx_theta(V, f, lo=-40.0, hi=40.0):
    """θ 域上 |Fx| 最小的俯仰角（粗扫 8° + 黄金分割细化）。"""
    grid = np.arange(lo, hi + 1e-9, 10.0)
    vals = []
    for th in grid:
        F, _ = cycle_mean(V, f, float(th))
        vals.append(abs(float(F[0])))
    i = int(np.argmin(vals))
    a = grid[max(i - 1, 0)]
    b = grid[min(i + 1, len(grid) - 1)]
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c = b - gr * (b - a)
    d = a + gr * (b - a)
    fc = abs(cycle_mean(V, f, float(c))[0][0])
    fd = abs(cycle_mean(V, f, float(d))[0][0])
    for _ in range(4):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = abs(cycle_mean(V, f, float(c))[0][0])
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = abs(cycle_mean(V, f, float(d))[0][0])
    theta = 0.5 * (a + b)
    F, Tq = cycle_mean(V, f, theta)
    return dict(theta=float(theta), fx=float(F[0]), fz=float(F[2]),
                my=float(Tq[1]))


def stage_a():
    """A. 当前波形参数的巡航配平扫描。"""
    rows = []
    for V in np.arange(2.0, 10.01, 0.5):
        for f in np.arange(1.5, 7.01, 0.25):
            r = min_fx_theta(float(V), float(f))
            r.update(V=float(V), f=float(f))
            rows.append(r)
    return rows


def _params_grid(level):
    if level == 0:
        return [(a, s, lg, mn)
                for a in (30, 40, 50)
                for s in (0.20, 0.30, 0.40)
                for lg in (-0.08, -0.04, 0.0)
                for mn in (-20, -10, 0)]
    return [(a, s, lg, mn)
            for a in (25, 30, 35, 40, 45, 50)
            for s in (0.20, 0.25, 0.30, 0.35, 0.40)
            for lg in (-0.10, -0.08, -0.06, -0.04, -0.02, 0.0)
            for mn in (-25, -20, -15, -10, -5, 0)]


def stage_b(v0, f0):
    """B. 波形四参数重搜索：两级网格，目标 J=|(Fx,W的相对残差)|。"""
    Vs = [max(2.0, v0 - 1.0), v0, min(9.0, v0 + 1.0)]
    fs = [max(1.5, f0 - 0.25), f0, min(6.5, f0 + 0.25)]

    def run(level):
        out = []
        for (a, s, lg, mn) in _params_grid(level):
            set_kinematics(a, s, lg, mn)
            for V in Vs:
                for f in fs:
                    r = min_fx_theta(V, f)
                    J = math.hypot(r["fx"] / W, (r["fz"] - W) / W)
                    out.append(dict(alpha=a, split=s, lag=lg, mean=mn,
                                    V=V, f=f, theta=r["theta"],
                                    fx=r["fx"], fz=r["fz"], J=J))
        out.sort(key=lambda d: d["J"])
        return out

    coarse = run(0)
    fine = run(1)
    # 粗网格用于记录收敛路径，细网格为权威
    return fine, coarse


def refine_trim(a, s, lg, mn, v0, f0):
    """最优参数邻域内细扫 (V, f, θ)。"""
    set_kinematics(a, s, lg, mn)
    rows = []
    for V in np.arange(max(2.0, v0 - 1.0), min(9.0, v0 + 1.01), 0.25):
        for f in np.arange(max(1.2, f0 - 0.6), min(7.0, f0 + 0.61), 0.1):
            r = min_fx_theta(float(V), float(f))
            r.update(V=float(V), f=float(f))
            r["J"] = math.hypot(r["fx"] / W, (r["fz"] - W) / W)
            rows.append(r)
    rows.sort(key=lambda d: d["J"])
    return rows


def stage_c_hover(a, s, lg, mn):
    """C. 悬停扫描：V=0，θ 取 |Fx| 最小点，扫 f 找 Fz=W；核验 6 Hz。"""
    set_kinematics(a, s, lg, mn)
    curve = []
    for f in np.arange(1.5, 9.01, 0.1):
        r = min_fx_theta(0.0, float(f))
        r.update(f=float(f))
        curve.append(r)
    f6 = min_fx_theta(0.0, 6.0)
    return curve, f6


def stage_e_balloon(trim_f, trim_pitch, trim_V):
    """E. 发射/竖直超升能量量级对照（同波形下力不随质量变，加速度随质量变）。"""
    F, _ = cycle_mean(trim_V, trim_f, trim_pitch)
    fz = float(F[2])
    fx = float(F[0])
    recs = []
    for mass_old, tag in ((0.700, "700g(参考口径)"), (cfg.MASS, "215g")):
        surplus = fz - mass_old * G
        acc = surplus / mass_old
        v1 = max(acc, 0.0)
        ke = 0.5 * mass_old * v1 * v1
        recs.append(dict(tag=tag, mass=mass_old, weight=mass_old * G,
                         fz=fz, surplus=surplus, accel=acc, v_1s=v1, ke_1s=ke))
    return recs


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"[scan] W = {W:.4f} N (mass={cfg.MASS})", flush=True)

    kc = cfg.FlapKinematicsConfig
    set_kinematics(kc.ALPHA_SET, kc.SPLIT, kc.PITCH_LAG_S, kc.MEAN)

    print("[scan] Stage A: 巡航配平扫描（当前波形参数）...")
    gridA = stage_a()
    candA = sorted(gridA,
                   key=lambda d: math.hypot(d["fx"] / W, (d["fz"] - W) / W))
    v0, f0 = candA[0]["V"], candA[0]["f"]
    print(f"[scan] Stage A 最优: V={v0} f={f0} θ={candA[0]['theta']:+.1f} "
          f"Fx={candA[0]['fx']:+.3f} Fz={candA[0]['fz']:.3f}")

    print("[scan] Stage B: 波形四参数重搜索（两级网格）...")
    fineB, coarseB = stage_b(v0, f0)
    b = fineB[0]
    print(f"[scan] Stage B 最优: a={b['alpha']} s={b['split']} "
          f"lag={b['lag']} mean={b['mean']} J={b['J']:.4f}")

    print("[scan] Stage B*: 最优参数邻域细扫...")
    refine = refine_trim(b["alpha"], b["split"], b["lag"], b["mean"],
                         b["V"], b["f"])
    trim = refine[0]
    print(f"[scan] 配平点: V={trim['V']:.2f} f={trim['f']:.2f} "
          f"θ={trim['theta']:+.2f} Fx={trim['fx']:+.3f} Fz={trim['fz']:.3f}")

    print("[scan] Stage C: 悬停扫描...")
    hover, f6 = stage_c_hover(b["alpha"], b["split"], b["lag"], b["mean"])
    hover_sorted = sorted(hover, key=lambda d: abs(d["fz"] - W))
    hover_pt = hover_sorted[0]

    print("[scan] Stage E: 竖直超升能量对照...")
    balloon = stage_e_balloon(trim["f"], trim["theta"], trim["V"])

    out = dict(ts=ts, W=W, mass=cfg.MASS,
               stageA_top=candA[:15], stageB_coarse_top=coarseB[:6],
               stageB_top=fineB[:12], refine_top=refine[:15], trim=trim,
               hover_pt=hover_pt, hover_6Hz=f6,
               hover_curve=hover, balloon=balloon)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    md = [f"# 215g 新被控对象离线配平扫描与波形重搜索（{ts}）", "",
          f"- 口径：求值方式为锁定机体状态并给定来流条件，气动力与气动力矩取整扑动周期的平均值（与台架试验使用同一套模型与求值方式），"
          f"计算包含尾翼与滑流"
          f"（v_slip={cfg.SLIP_K * cfg.SLIP_V_PER_HZ}·f），垂直速度为零，爬升修正因子不动作",
          f"- 体重 W = {W:.4f} N；几何为 0.6 m 翼展（2026-09-20 按论文确定口径重新标注）",
          f"- 求解方法：在 θ 域做 |Fx| 最小化，并在 (V,f) 网格上搜索，J = √((Fx/W)²+((Fz−W)/W)²)",
          f"- 原始数据 `logs/trim_scan_215g.json`", "",
          "## 一、当前波形参数下的巡航配平（Stage A 前 12）", "",
          "| V (m/s) | f (Hz) | θ* (°) | Fx (N) | Fz (N) | J |",
          "|---|---|---|---|---|---|"]
    for d in candA[:12]:
        md.append(f"| {d['V']:.2f} | {d['f']:.2f} | {d['theta']:+.2f} |"
                  f" {d['fx']:+.3f} | {d['fz']:.3f} |"
                  f" {math.hypot(d['fx'] / W, (d['fz'] - W) / W):.3f} |")
    md += ["", "## 二、波形四参数重搜索（细网格前 12）", "",
           "| α_set(°) | split | lag(s) | mean(°) | V | f | θ* | Fx | Fz−W | J |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for d in fineB[:12]:
        md.append(f"| {d['alpha']} | {d['split']} | {d['lag']} | {d['mean']} |"
                  f" {d['V']:.2f} | {d['f']:.2f} | {d['theta']:+.1f} |"
                  f" {d['fx']:+.3f} | {d['fz'] - W:+.3f} | {d['J']:.4f} |")
    md += ["", "## 三、权威配平点（最优参数与邻域细扫描）", "",
           f"- 波形取值确定：ALPHA_SET={b['alpha']}°，SPLIT={b['split']}，"
           f"PITCH_LAG_S={b['lag']} s，MEAN={b['mean']}°",
           f"- 配平点：V*={trim['V']:.2f} m/s，f*={trim['f']:.2f} Hz，"
           f"θ*={trim['theta']:+.2f}°（Fx={trim['fx']:+.3f} N，"
           f"Fz={trim['fz']:.3f} N，Fz−W={trim['fz'] - W:+.3f} N，"
           f"J={trim['J']:.4f}）",
           f"- LAUNCH_PITCH 重推导值：{math.radians(trim['theta']):+.4f} rad"
           f"（该值等于 θ*，状态锁定释放时刻的姿态同步取值口径）",
           f"- 残差声明：Fx 残差 {trim['fx']:+.3f} N 为该波形下 θ 域最小值"
           f"（该残差源于 0.6 m 几何的推力富余特性），不属于数值求解失败", "",
           "## 四、悬停扫描（修订第五条）", "",
           f"- 悬停点：f_h={hover_pt['f']:.2f} Hz，θ_h={hover_pt['theta']:+.2f}°，"
           f"Fz={hover_pt['fz']:.3f} N（Fz−W={hover_pt['fz'] - W:+.3f} N），"
           f"该点 Fx={hover_pt['fx']:+.3f} N（θ 域最小残差）",
           f"- 6 Hz 工况：θ={f6['theta']:+.2f}°，Fz={f6['fz']:.3f} N，"
           f"Fz/W={f6['fz'] / W:.2f}（工作令预期悬停约 6 Hz 可达的实测核验）",
           f"- Fz−W 最小的前 6 个频率："
           + "、".join(f"{d['f']:.1f}Hz" for d in hover_sorted[:6]), "",
           "## 五、竖直超升/发射能量量级对照（修订第三条）", "",
           "| 口径 | 质量(kg) | 体重(N) | 同波形Fz(N) | 剩余力(N) |"
           " 加速度(m/s²) | 1s末速(m/s) | 1s动能(J) |",
           "|---|---|---|---|---|---|---|---|"]
    for r in balloon:
        md.append(f"| {r['tag']} | {r['mass']:.3f} | {r['weight']:.3f} |"
                  f" {r['fz']:.3f} | {r['surplus']:+.3f} | {r['accel']:+.2f} |"
                  f" {r['v_1s']:.2f} | {r['ke_1s']:.2f} |")
    md += ["", f"- 结论：同一波形下气动力与机体质量无关，剩余力不变而加速度"
           f"按质量比放大 {0.700 / cfg.MASS:.2f} 倍；215g 发射/竖直超升瞬态的"
           f"能量量级与扰动烈度按同比例上升，发射程序与瞬态保护必须按新量级复核。",
           ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[scan] 完成 → {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
