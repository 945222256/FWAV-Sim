# -*- coding: utf-8 -*-
"""
probe_grid_debug.py — 包线网格工具的诊断探针（第十六班·排障）

疑点：网格工具报告巡航点（4.0/1.75）竖直残差 2.6% 且俯仰根 +7.2°，
与第十三班修正版（+6.6°、1.5%）和分支探针（+9° 过零）不一致；
相位收敛检查不收敛。本探针逐项隔离：默认频率、根位置、起始相位
不变性、采样数收敛性。
"""
import os
import sys
import math

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
from config import sim_config as cfg
from modules.aero_solver import TailFuselageAero, euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

print("配置默认拍频 FlapKinematicsConfig.FREQUENCY =",
      cfg.FlapKinematicsConfig.FREQUENCY)

m = SaneDickinson3DModel()
tail = TailFuselageAero()
for w in (m.wing_left, m.wing_right):
    w.alpha_set = math.radians(35.0)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)


def F(V, f, pd, t0=60000.0, n=36):
    m.set_wing_command(frequency=f)
    q = euler_to_quat(0.0, math.radians(pd), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    T = 1.0 / f
    Fs = np.zeros(3)
    for i in range(n):
        Fw, _ = m.compute_forces(np.zeros(3), q, vel, om, t0 + i * T / n)
        Fs += Fw
    Fs /= n
    Ft, _ = tail.compute(q, vel, v_slip=cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f)
    return Fs + Ft


print("\n[1] 三个候选俯仰根的 Fx/Fz（n=36）")
for pd in (6.6, 7.2, 9.0):
    r = F(4.0, 1.75, pd)
    print(f"  θ={pd:+.1f}°  Fx={r[0]:+.4f}  Fz={r[2]:.4f}")

print("\n[2] 起始相位不变性（同状态、不同 t0 应逐位相同）")
r1 = F(4.0, 1.75, 9.0, t0=60000.0)
r2 = F(4.0, 1.75, 9.0, t0=60123.456)
print("  t0=60000.0  :", [round(float(x), 5) for x in r1])
print("  t0=60123.456:", [round(float(x), 5) for x in r2])

print("\n[3] 采样数收敛性（同状态 Fz）")
for n in (24, 36, 72, 144, 288):
    r = F(4.0, 1.75, 9.0, n=n)
    print(f"  n={n:4d}  Fx={r[0]:+.4f}  Fz={r[2]:.4f}")
