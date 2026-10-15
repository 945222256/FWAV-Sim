# -*- coding: utf-8 -*-
"""
preview_15dof_pybullet.py — 15 自由度扑翼样机拍动预览（pybullet GUI）
======================================================================
加载 assets/flapper/flapper_15dof.urdf，按 flapper_15dof_drive.json 的幅度/相位
正弦驱动全部 15 个关节，机身锚定悬空，直观看拍动形态。

用法：python tools/preview_15dof_pybullet.py [频率Hz，默认3] [时长s，默认60]
关窗或 Ctrl-C 退出。关掉重力和电机阻尼，纯看运动学。
"""
import json
import math
import os
import sys
import time

import pybullet as p
import pybullet_data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URDF = os.path.join(ROOT, "assets", "flapper", "flapper_15dof.urdf")
DRIVE = os.path.join(ROOT, "assets", "flapper", "flapper_15dof_drive.json")

freq = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
duration = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0

p.connect(p.GUI)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
p.resetDebugVisualizerCamera(cameraDistance=0.9, cameraYaw=35, cameraPitch=-20,
                             cameraTargetPosition=[0, 0, 0.2])
p.configureDebugVisualizer(p.COV_ENABLE_GUI, 1)

cid = p.loadURDF(URDF, basePosition=[0, 0, 0.3], flags=p.URDF_USE_INERTIA_FROM_FILE)
n_j = p.getNumJoints(cid)
joint_names = {}
for i in range(n_j):
    info = p.getJointInfo(cid, i)
    joint_names[info[1].decode()] = i

with open(DRIVE) as f:
    drive = json.load(f)

# 机身锚定（自由浮动 + 无重力 = 原地拍动，纯看形态）
p.setGravity(0, 0, 0)
anchor = p.createConstraint(cid, -1, -1, -1, p.JOINT_FIXED,
                            [0, 0, 0], [0, 0, 0.3], [0, 0, 0.3])
p.changeConstraint(anchor, maxForce=100)

print(f"加载完成：{n_j} 个关节，驱动频率 {freq} Hz，时长 {duration} s")
print("关节驱动表：")
for name, d in drive.items():
    jid = joint_names.get(name)
    if jid is None:
        print(f"  ⚠ {name} 不在 URDF 中（跳过）")
        continue
    print(f"  {name:16s} amp={d['amp']:.2f} rad  phase={d['phase']:.2f}")

p.setRealTimeSimulation(1)
t0 = time.time()
while time.time() - t0 < duration:
    t = time.time() - t0
    for name, d in drive.items():
        jid = joint_names.get(name)
        if jid is None or d["amp"] == 0.0:
            continue
        pos = d["amp"] * math.sin(2 * math.pi * freq * t + d["phase"])
        p.setJointMotorControl2(cid, jid, p.POSITION_CONTROL,
                                targetPosition=pos,
                                force=8.0, maxVelocity=50.0)
    time.sleep(0.005)

p.disconnect()
