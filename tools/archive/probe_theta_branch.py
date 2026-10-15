# -*- coding: utf-8 -*-
"""
probe_theta_branch.py — 配平俯仰分支探针（第十三班修正·辅助）

疑问：solve_pitch 的三分搜索假定 |Fx|(θ) 单阱；初版盘旋表俯仰值正负
振荡提示该地形可能双阱。本探针在扩展配平搜索近优工况
（V=4.0, f=1.75, α_set=35°）对 θ 做稠密扫描，核对两分支的
Fx/Fz/My 读数，验证扩展搜索的 θ=+7.2° 配平点是否复现。
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

model = SaneDickinson3DModel()
tail = TailFuselageAero()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(35.0)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)


def cm(V, f, pd, n=36):
    model.set_wing_command(frequency=f)
    q = euler_to_quat(0.0, math.radians(pd), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    T = 1.0 / f
    F = np.zeros(3)
    Tq = np.zeros(3)
    t0 = 60000.0
    for i in range(n):
        Fw, Tw = model.compute_forces(np.zeros(3), q, vel, om, t0 + i * T / n)
        F += Fw
        Tq += Tw
    F /= n
    Tq /= n
    Ft, Tt = tail.compute(q, vel, v_slip=cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f)
    return F + Ft, Tq + Tt


print("  θ(°)     Fx(N)     Fz(N)     My(N·m)")
for pd in np.arange(-4.0, 22.01, 1.0):
    F, T = cm(4.0, 1.75, float(pd))
    print(f"{pd:+7.1f}  {float(F[0]):+8.3f}  {float(F[2]):+8.3f}  "
          f"{float(T[1]):+9.4f}")
