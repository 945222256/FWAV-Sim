# -*- coding: utf-8 -*-
"""
verify_trim_215g.py — 配平终验与悬停/竖直超升重算（步骤5 收口）

scan_trim_215g.py 的两个缺陷在本脚本修正：
  1. refine 漂移：候选点用 n=64 高保真周期平均复评后全局重排；
  2. 悬停公式：θ 固定 0°（悬停姿态），扫 f 找 Fz=W 交点；6 Hz 点在
     θ=0° 与 |θ|≤15° 域内最小 |Fx| 两个口径分别核验。
竖直超升对照改用"剩余力→加速度/能量"响应表（任意 ΔF 的量级换算）。
产物：logs/verify_trim_215g.json + 控制台（写入 docs/TRIM_SCAN_215g.md 附节）
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
model = FlapperAeroModel()
tail = TailFuselageAero()
_T = [1000.0]


def set_kinematics(a, s, lg, mn):
    for w in (model.wing_left, model.wing_right):
        w.alpha_set = math.radians(a)
        w.split = s
        w.pitch_lag_s = lg
        w.mean = math.radians(mn)


def cm(V, f, pd, n=64):
    model.set_wing_command(frequency=f)
    quat = euler_to_quat(0.0, math.radians(pd), 0.0)
    vel = np.array([V, 0.0, 0.0])
    T = 1.0 / f
    F = np.zeros(3)
    for i in range(n):
        Fw, _ = model.compute_forces(np.zeros(3), quat, vel, np.zeros(3),
                                     _T[0] + i * T / n)
        F += Fw
    F /= n
    Ft, _ = tail.compute(quat, vel,
                         v_slip=cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f)
    _T[0] += T
    return F + Ft


def J_of(V, f, pd):
    F = cm(V, f, pd)
    return (float(F[0]), float(F[2]),
            math.hypot(F[0] / W, (F[2] - W) / W))


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "W": W}

    # 候选：来源 scan_trim_215g.json（|θ|≤25°、V∈[3.5,6]、f∈[1.5,2.75]）
    cands = [
        ("当前参数", dict(a=40, s=0.30, lg=-0.06, mn=-15),
         [(5.0, 1.75, -19.1), (5.0, 2.00, -15.6), (5.0, 2.25, -9.1)]),
        ("B1 α30/s0.20/lag0/m-5", dict(a=30, s=0.20, lg=0.0, mn=-5),
         [(4.0, 1.75, -23.8), (4.5, 1.75, -25.0), (4.0, 2.00, -25.0)]),
        ("B2 α35/s0.30/lag0/m0", dict(a=35, s=0.30, lg=0.0, mn=0),
         [(4.0, 2.00, -23.8), (4.5, 2.00, -25.0)]),
        ("B4 α40/s0.25/lag-0.02/m0", dict(a=40, s=0.25, lg=-0.02, mn=0),
         [(4.0, 1.50, -6.2), (4.5, 1.75, -10.0), (5.0, 1.75, -12.0)]),
    ]
    rows = []
    for tag, kp, pts in cands:
        set_kinematics(**kp)
        for (V, f, pd) in pts:
            fx, fz, J = J_of(V, f, pd)
            rows.append(dict(tag=tag, **kp, V=V, f=f, pitch=pd,
                             fx=fx, fz=fz, J=J))
    rows.sort(key=lambda d: d["J"])
    out["cands"] = rows
    best = rows[0]
    print(f"[verify] 最优: {best['tag']} V={best['V']} f={best['f']} "
          f"θ={best['pitch']:+.1f} Fx={best['fx']:+.4f} "
          f"Fz={best['fz']:.4f} J={best['J']:.4f}", flush=True)

    # 悬停：θ=0° 扫 f 找 Fz=W
    set_kinematics(best["a"], best["s"], best["lg"], best["mn"])
    hover = []
    for f in np.arange(1.5, 8.001, 0.05):
        F = cm(0.0, float(f), 0.0)
        hover.append(dict(f=float(f), fx=float(F[0]), fz=float(F[2])))
    cross = []
    for i in range(len(hover) - 1):
        a_, b_ = hover[i], hover[i + 1]
        if (a_["fz"] - W) * (b_["fz"] - W) <= 0:
            t_ = (W - a_["fz"]) / (b_["fz"] - a_["fz"])
            cross.append(a_["f"] + t_ * (b_["f"] - a_["f"]))
    out["hover_cross"] = cross
    f_h = cross[0] if cross else None
    print(f"[verify] 悬停 Fz=W 交点(θ=0°): {['%.2f' % c for c in cross]}",
          flush=True)

    # 6 Hz 核验（θ=0 与 |θ|≤15° 最小|Fx| 两口径）
    F6 = cm(0.0, 6.0, 0.0)
    fx_min, th_min = 1e9, 0.0
    for pd in np.arange(-15.0, 15.01, 1.0):
        Fx = float(cm(0.0, 6.0, float(pd))[0])
        if abs(Fx) < abs(fx_min):
            fx_min, th_min = Fx, float(pd)
    F6b = cm(0.0, 6.0, th_min)
    out["hover_6Hz"] = dict(theta0=dict(fx=float(F6[0]), fz=float(F6[2])),
                            minfx=dict(theta=th_min, fx=fx_min,
                                       fz=float(F6b[2])))
    print(f"[verify] 6Hz θ=0°: Fx={F6[0]:+.3f} Fz={F6[2]:.3f} "
          f"(Fz/W={F6[2] / W:+.2f})；|θ|≤15°最小|Fx| θ={th_min:+.0f}° "
          f"Fx={fx_min:+.3f} Fz={F6b[2]:.3f}", flush=True)

    # 巡航配平点俯仰精修（最优参数，V,f 固定，θ 黄金细化 n=64）
    lo, hi = best["pitch"] - 4.0, best["pitch"] + 4.0
    for _ in range(12):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if abs(cm(best["V"], best["f"], m1)[0]) < \
           abs(cm(best["V"], best["f"], m2)[0]):
            hi = m2
        else:
            lo = m1
    th_star = 0.5 * (lo + hi)
    fx, fz, J = J_of(best["V"], best["f"], th_star)
    out["trim"] = dict(**{k: best[k] for k in
                          ("tag", "a", "s", "lg", "mn", "V", "f")},
                       pitch=th_star, fx=fx, fz=fz, J=J,
                       launch_pitch_rad=math.radians(th_star))
    print(f"[verify] 终版配平: θ*={th_star:+.2f}° "
          f"LAUNCH_PITCH={math.radians(th_star):+.4f} rad "
          f"Fx={fx:+.4f} Fz={fz:.4f} J={J:.4f}", flush=True)

    # 竖直超升/发射能量响应表（修订三）
    bal = []
    for dF in (0.5, 1.0, 2.0):
        row = dict(dF=dF)
        for m, tag_ in ((0.700, "700g"), (cfg.MASS, "215g")):
            row[tag_] = dict(accel=dF / m,
                             ke_1s=0.5 * m * (dF / m) ** 2)
        bal.append(row)
    out["balloon"] = bal
    out["hover_curve_coarse"] = hover[::4]
    with open(os.path.join(PROJECT_ROOT, "logs", "verify_trim_215g.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[verify] 完成 → logs/verify_trim_215g.json", flush=True)


if __name__ == "__main__":
    main()
