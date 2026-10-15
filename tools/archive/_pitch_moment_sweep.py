# -*- coding: utf-8 -*-
"""离线扫描：S&D 机翼气动模型的周期平均力矩 vs 姿态角（悬停工况）。

目的：判定 2026-09-19 七次自由飞共同俯仰发散的裸机稳定性来源。
口径：与运行时一致——FlapperAeroModel() 默认参数（sane_dickinson），
拍频 1.9 Hz，力矩取世界系、关于机体原点，风速以相对速度形式传入
（静气=(0,0,0)；5 m/s 迎风=(+5,0,0)，复刻回调 v_air=vel-WIND_VECTOR）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from modules.aero_solver import FlapperAeroModel, euler_to_quat

FREQ = 1.9
NCYC = 20
SPC = 120          # 每周期采样数（228 Hz @ 1.9 Hz，远超振荡频率）


def sweep(model, axis, angles, vel):
    model.set_wing_command(frequency=FREQ)
    dt = 1.0 / (FREQ * SPC)
    ts = np.arange(0.0, NCYC / FREQ, dt)
    rows = []
    for a in angles:
        roll, pitch = (0.0, a) if axis == "pitch" else (a, 0.0)
        quat = euler_to_quat(roll, pitch, 0.0)
        Fs = np.zeros(3)
        Ts = np.zeros(3)
        for t in ts:
            F, Tq = model.compute_forces(np.array([0.0, 0.0, 1.5]),
                                         quat, vel, np.zeros(3), t)
            Fs += F
            Ts += Tq
        n = len(ts)
        rows.append((a, Ts[0] / n, Ts[1] / n, Ts[2] / n,
                     Fs[0] / n, Fs[2] / n))
    return rows


def report(title, rows, comp, comp_name):
    print("=" * 78)
    print(title)
    print("  角度(deg)   Mx(N·m)     My(N·m)     Mz(N·m)     Fx(N)      Fz(N)")
    for a, mx, my, mz, fx, fz in rows:
        mark = "  <-- %s" % comp_name if abs(a) < 1e-9 else ""
        print("  %+7.2f  %+9.4f  %+9.4f  %+9.4f  %+8.3f  %+8.3f%s"
              % (np.degrees(a), mx, my, mz, fx, fz, mark))
    ang = np.array([r[0] for r in rows])
    m = np.array([r[comp] for r in rows])
    # 零点与斜率（线性插值 + 中央差分）
    for i in range(len(m) - 1):
        if m[i] == 0.0 or (m[i] < 0.0) != (m[i + 1] < 0.0):
            z = ang[i] - m[i] * (ang[i + 1] - ang[i]) / (m[i + 1] - m[i])
            k = (m[i + 1] - m[i]) / (ang[i + 1] - ang[i])
            print("  >> %s 零点在 %+0.3f rad（%+0.2f deg），该处斜率 %+0.3f N·m/rad"
                  % (comp_name, z, np.degrees(z), k))
    k0 = (m[2] - m[0]) / (ang[2] - ang[0])
    print("  >> 原点处 %s = %+0.4f N·m，原点斜率 %+0.3f N·m/rad"
          % (comp_name, m[len(m) // 2 if False else 0] if False else m[np.argmin(np.abs(ang))], k0))


m = FlapperAeroModel()
angles = np.radians(np.arange(-30.0, 30.0 + 1e-9, 2.5))

report("[1] 俯仰扫描 | 静气悬停 vel=(0,0,0)",
       sweep(m, "pitch", angles, np.zeros(3)), 1, "My")
report("[2] 俯仰扫描 | 5 m/s 迎风 vel=(+5,0,0)",
       sweep(m, "pitch", angles, np.array([5.0, 0.0, 0.0])), 1, "My")
report("[3] 横滚扫描 | 静气悬停 vel=(0,0,0)",
       sweep(m, "roll", angles, np.zeros(3)), 0, "Mx")
report("[4] 横滚扫描 | 5 m/s 迎风 vel=(+5,0,0)",
       sweep(m, "roll", angles, np.array([5.0, 0.0, 0.0])), 0, "Mx")

print("=" * 78)
print("判读：俯仰通道静稳定要求 dMy/dpitch<0（抬头扰动产生低头恢复力矩）；")
print("      My(0)不等于零说明悬停姿态 0 rad 不是配平点，存在恒定俯仰力矩。")
