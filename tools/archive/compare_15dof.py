# -*- coding: utf-8 -*-
"""
compare_15dof.py — 对方组 fwav_15dof vs 我方 flapper_15dof 同框对比图
====================================================================
pybullet DIRECT 离屏渲染，两台样机并排摆好位形，一帧出图 + PIL 标注。
输出 docs/compare_15dof.png
"""
import os
import pybullet as p

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "compare_15dof.png")
URDF_GROUP = os.path.join(ROOT, "assets", "flapper", "fwav_15dof_from_group.urdf")
URDF_OURS = os.path.join(ROOT, "assets", "flapper", "flapper_15dof.urdf")

p.connect(p.DIRECT)

# ── 左：对方组样机（REP-103，x 前 y 左，放 y=+0.55 一侧，摆展翼位形）──
g = p.loadURDF(URDF_GROUP, basePosition=[0, 0.55, 0.35], useFixedBase=True)
POSE_G = {  # 展翼巡航位形：flap 略上反、fold 半折、twist 给攻角
    "flap_L": 0.25, "flap_R": 0.25,
    "twist_L": -0.15, "twist_R": 0.15,
    "fold_L": 0.45, "fold_R": -0.45,
    "tail_yaw": 0.12, "tail_L": 0.10, "tail_R": -0.06,
}
info = {p.getJointInfo(g, i)[1].decode(): i for i in range(p.getNumJoints(g))}
for name, pos in POSE_G.items():
    if name in info:
        p.resetJointState(g, info[name], pos)

# ── 右：我方样机（y 前 x 左，放 y=-0.55 一侧，摆相近的展翼位形）──
o = p.loadURDF(URDF_OURS, basePosition=[0, -0.55, 0.35], useFixedBase=True)
POSE_O = {
    "wing_l_flap": -0.25, "wing_r_flap": -0.25,   # 轴向不同，视觉近似上反
    "wing_l_twist": 0.15, "wing_r_twist": -0.15,
    "wing_l_elbow": -0.35, "wing_r_elbow": 0.35,
    "wing_l_wrist": -0.20, "wing_r_wrist": 0.20,
    "wing_l_sweep": 0.0, "wing_r_sweep": 0.0,
    "tail_pitch": 0.10, "tail_yaw": -0.12, "tail_roll": 0.0,
    "neck_pitch": 0.0, "neck_yaw": 0.0,
}
info_o = {p.getJointInfo(o, i)[1].decode(): i for i in range(p.getNumJoints(o))}
for name, pos in POSE_O.items():
    if name in info_o:
        p.resetJointState(o, info_o[name], pos)

# ── 渲染（3/4 俯视，正面朝画面左前）──
view = p.computeViewMatrixFromYawPitchRoll(
    cameraTargetPosition=[0.1, 0, 0.3],
    distance=1.9, yaw=55, pitch=-32, roll=0, upAxisIndex=2)
proj = p.computeProjectionMatrixFOV(fov=48, aspect=1.7, nearVal=0.02, farVal=8)

w, h = 1700, 1000
_, _, rgb, _, _ = p.getCameraImage(w, h, view, proj,
                                    renderer=p.ER_TINY_RENDERER)
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("需要 Pillow 出图：pip install pillow")

import numpy as np
arr = np.asarray(rgb, dtype=np.uint8).reshape(h, w, 4)
img = Image.fromarray(arr[:, :, :3].copy())
d = ImageDraw.Draw(img)
try:
    font_big = ImageFont.truetype("msyh.ttc", 34)
    font_sml = ImageFont.truetype("msyh.ttc", 24)
except Exception:
    font_big = font_sml = ImageFont.load_default()
# 顶部标题 + 两侧标注（对方在 y+，画面左侧偏后；我方 y- 在右）
d.rectangle([0, 0, w, 92], fill=(24, 26, 32))
d.text((24, 14), "15-DOF FWAV 虚拟样机对比  (同框 1:1 尺度)", font=font_big, fill=(240, 240, 240))
d.text((30, 104), "▼ 对方组 fwav_15dof —— 9 驱动关节 + 浮动基座6 = 15", font=font_big, fill=(255, 150, 60))
d.text((70, 148), "每翼3(拍动/扭转/折叠)×2 ｜ 尾3(偏航+左右差动舵面) ｜ REP-103(x前) ｜ 总重~430g ｜ 340g机身+头锥",
       font=font_sml, fill=(200, 200, 205))
d.text((30, h - 150), "▲ 我方 flapper_15dof —— 15 全驱动关节(不含基座)", font=font_big, fill=(90, 170, 255))
d.text((70, h - 106), "每翼5(拍动/前后摆/扭转/肘/腕)×2 ｜ 尾3(俯仰/偏航/滚转) ｜ 颈2 ｜ y前坐标 ｜ 蜂鸟尺度~20g",
       font=font_sml, fill=(200, 200, 205))
img.save(OUT)
print("OK", OUT)

p.disconnect()
