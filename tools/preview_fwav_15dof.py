# -*- coding: utf-8 -*-
"""
preview_fwav_15dof.py — 对方组 fwav_15dof 样机拍动预览（pybullet GUI）
=======================================================================
被控样机：assets/flapper/fwav_15dof_from_group.urdf（徐文福组提供，REP-103 x前y左z上）

驱动接线（按对方 URDF 注释）：
  flap_L  = +φ(t)     flap_R = -φ(t)    轴都是+x，镜像翼需反号才是对称拍动
  twist_L = twist_R = α(t)              轴都是+y，同号=左右翼攻角一致（前飞）
  fold_L  = +F(t)     fold_R = -F(t)    限位已镜像 [0,1.2]/[-1.2,0]
  tail_yaw / tail_L / tail_R            小幅配平（尾舵给控制律留）

φ/α 来自 modules/aero_solver.WingKinematics（纯 NumPy，aero 模式：攻角约束在
ALPHA_SET，附着流前飞型）——与 Isaac 侧将来驱动同一套运动学，不是视觉替身。

用法：python tools/preview_fwav_15dof.py [频率Hz=3] [时长s=600]
"""
import math
import os
import sys
import time

import pybullet as p

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modules.aero_solver import WingKinematics  # noqa: E402

URDF = os.path.join(ROOT, "assets", "flapper", "fwav_15dof_from_group.urdf")

freq = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
duration = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0

FOLD_AMP = 0.45   # 折叠幅度 rad（上拍段折翼）
TAIL_YAW_AMP = 0.12

p.connect(p.GUI)
p.resetDebugVisualizerCamera(cameraDistance=1.1, cameraYaw=40, cameraPitch=-18,
                             cameraTargetPosition=[0, 0, 0.3])

cid = p.loadURDF(URDF, basePosition=[0, 0, 0.4], useFixedBase=True)
jmap = {p.getJointInfo(cid, i)[1].decode(): i for i in range(p.getNumJoints(cid))}
need = ["flap_L", "flap_R", "twist_L", "twist_R",
        "fold_L", "fold_R", "tail_yaw", "tail_L", "tail_R"]
missing = [n for n in need if n not in jmap]
if missing:
    raise SystemExit(f"URDF 缺关节: {missing}（接线以对方注释为准）")

# 鸟尺度前飞参数（aero 模式：攻角跟随 φ̇ 符号、约束在 ALPHA_SET）
wk = WingKinematics(frequency=freq, amplitude_deg=55.0,
                    pitch_mode="aero", alpha_set_deg=12.0)

p.setGravity(0, 0, 0)
print(f"[preview] fwav_15dof 拍动预览：f={freq}Hz 拍幅55° 攻角12° 时长{duration}s")
print("[preview] 关节映射 flap=±φ, twist=α(同号), fold=±F, 尾=配平")

p.setRealTimeSimulation(1)
t0 = time.time()
while time.time() - t0 < duration:
    t = time.time() - t0
    w = wk.evaluate(t)
    phi, alpha = w["phi"], w["alpha"]
    # 折翼：上拍段（φ̇>0）折叠，下拍展开
    fold = FOLD_AMP * 0.5 * (1.0 + math.sin(wk.omega * t + 2.2))
    tail_yaw = TAIL_YAW_AMP * math.sin(wk.omega * t * 0.5)

    cmd = {
        "flap_L": phi, "flap_R": -phi,
        "twist_L": alpha, "twist_R": alpha,
        "fold_L": fold, "fold_R": -fold,
        "tail_yaw": tail_yaw, "tail_L": 0.08, "tail_R": 0.08,
    }
    for name, pos in cmd.items():
        p.setJointMotorControl2(cid, jmap[name], p.POSITION_CONTROL,
                                targetPosition=pos, force=20.0, maxVelocity=40.0)
    time.sleep(0.005)

p.disconnect()
