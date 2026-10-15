# -*- coding: utf-8 -*-
"""
reanchor_scheduling.py — 215g 新被控对象控制调度量重新标定测量脚本（步骤11+13）

按与台架试验相同的求值方式（锁定机体状态、整周期平均）重新推导：
  1) 滑流动量理论标定值：v_i=√(W/(2ρA_disk))，SLIP_V_PER_HZ=v_i/f*
  2) B_dA(f)：V=4 状态锁定下，dA=±0.10 rad 的滚转力矩斜率（旧 5.2·f²，2.3m 口径）
  3) DFX_DTHETA / DFX_DV：新配平点邻域 ∂Fx/∂θ 与 ∂Fx/∂V（旧 7.40/3.17）
  4) 附加质量力占比：enable_added_mass 开关差分（处置保留第四项的验证）
产物：logs/reanchor_scheduling.json + 控制台
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
from modules.aero_solver import FlapperAeroModel, TailFuselageAero, euler_to_quat

G = 9.81
W = cfg.MASS * G
model = FlapperAeroModel()
tail = TailFuselageAero()
_T = [5000.0]
VT, FT, THT = 4.0, 1.55, -6.2


def cm(V, f, pd, dA=0.0, n=48, with_tail=True):
    model.set_wing_command(frequency=f)
    model.wing_left.amplitude = math.radians(35) + dA
    model.wing_right.amplitude = math.radians(35) - dA
    quat = euler_to_quat(0.0, math.radians(pd), 0.0)
    vel = np.array([V, 0.0, 0.0])
    T = 1.0 / f
    F = np.zeros(3)
    Tq = np.zeros(3)
    for i in range(n):
        Fw, Tw = model.compute_forces(np.zeros(3), quat, vel, np.zeros(3),
                                     _T[0] + i * T / n)
        F += Fw
        Tq += Tw
    F /= n
    Tq /= n
    if with_tail:
        v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f
        Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
        F = F + Ft
        Tq = Tq + Tt
    _T[0] += T
    return F, Tq


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "W": W, "trim": dict(V=VT, f=FT, pitch=THT)}

    # 1) 滑流动量锚（结构保留，系数按新配平点重新标定）
    a_disk = cfg.SLIP_DISK_AREA
    v_i = math.sqrt(W / (2.0 * cfg.AeroModelConfig.RHO_AIR * a_disk))
    out["slipstream"] = dict(
        anchor="v_i=sqrt(W/(2*rho*A_disk)) @ 新配平点",
        v_i=v_i, f_star=FT, slip_v_per_hz_new=v_i / FT,
        slip_v_per_hz_old=cfg.SLIP_V_PER_HZ,
        v_slip_at_trim_old=cfg.SLIP_K * cfg.SLIP_V_PER_HZ * FT,
        v_slip_at_trim_new=v_i)

    # 2) B_dA(f)
    bda = []
    for f in (1.55, 2.0, 2.5, 3.0):
        _,Tp = cm(VT, f, THT, dA=+0.10)
        _,Tm = cm(VT, f, THT, dA=-0.10)
        slope = (Tp[0] - Tm[0]) / 0.20   # 滚转力矩/弧度
        bda.append(dict(f=f, B_dA=slope, per_f2=slope / f ** 2))
    out["B_dA"] = bda
    slope_fit = float(np.mean([d["per_f2"] for d in bda]))

    # 3) DFX_DTHETA / DFX_DV（配平点邻域）
    F1, _ = cm(VT - 0.5, FT, THT)
    F2, _ = cm(VT + 0.5, FT, THT)
    dfx_dv = (F2[0] - F1[0]) / 1.0
    G1, _ = cm(VT, FT, THT - 3.0)
    G2, _ = cm(VT, FT, THT + 3.0)
    dfx_dth = (G2[0] - G1[0]) / math.radians(6.0)
    out["derivatives"] = dict(DFX_DV=dfx_dv, DFX_DTHETA=dfx_dth,
                              DFX_DV_old=3.17, DFX_DTHETA_old=7.40)

    # 4) 附加质量力占比（瞬时力差分 RMS 比）
    model.enable_added_mass = True
    _T[0] += 10.0
    f0 = FT
    model.set_wing_command(frequency=f0)
    quat = euler_to_quot = euler_to_quat(0.0, math.radians(THT), 0.0)
    vel = np.array([VT, 0.0, 0.0])
    T = 1.0 / f0
    num, den = [], []
    for i in range(96):
        t = _T[0] + i * T / 96
        Fa, _ = model.compute_forces(np.zeros(3), quat, vel, np.zeros(3), t)
        model.enable_added_mass = False
        Fn, _ = model.compute_forces(np.zeros(3), quat, vel, np.zeros(3), t)
        model.enable_added_mass = True
        num.append(float(np.linalg.norm(Fa - Fn)))
        den.append(float(np.linalg.norm(Fa)))
    _T[0] += T
    out["added_mass"] = dict(
        rms_ratio=float(np.sqrt(np.mean(np.square(num))) /
                        np.sqrt(np.mean(np.square(den)))),
        mean_ratio=float(np.mean(num) / np.mean(den)),
        note="215g 口径占比（保留四验证：灵敏度随质量切换上升）")

    with open(os.path.join(cfg.LOG_DIR, "reanchor_scheduling.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[reanchor] slip v_i={v_i:.3f} v/hz {out['slipstream']['slip_v_per_hz_new']:.3f}"
          f" (old {cfg.SLIP_V_PER_HZ})", flush=True)
    print(f"[reanchor] B_dA/f2={slope_fit:.2f} (old 5.2)；"
          f"B_dA@1.55={bda[0]['B_dA']:.1f}", flush=True)
    print(f"[reanchor] DFX_DV={dfx_dv:+.2f} (old 3.17)；"
          f"DFX_DTHETA={dfx_dth:+.2f} (old 7.40)", flush=True)
    print(f"[reanchor] added_mass rms_ratio={out['added_mass']['rms_ratio']:.3f}",
          flush=True)


if __name__ == "__main__":
    main()
