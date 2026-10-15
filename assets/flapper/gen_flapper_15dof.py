# -*- coding: utf-8 -*-
"""
gen_flapper_15dof.py — 15 自由度扑翼样机 URDF 生成器（参数化，改布局只改 JOINT_TABLE）
================================================================================

自由度分配（15 DOF，鸟式布局，可在 JOINT_TABLE 调整）：
  每侧翼 ×5 = 10：肩拍动 flap / 肩前后摆 sweep / 肩扭转 twist / 肘 elbow / 腕 wrist
  尾 ×3        = 3：尾俯仰 tail_pitch / 尾偏航 tail_yaw / 尾滚转 tail_roll
  颈 ×2        = 2：颈俯仰 neck_pitch / 颈偏航 neck_yaw

输出 flapper_15dof.urdf 到本目录。视觉/碰撞全部用基元（box），导入 Isaac Sim 的
URDF Importer 后每关节即成 Articulation 关节，后续由 WingKinematics 驱动。

用法：python gen_flapper_15dof.py
"""
import math
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "flapper_15dof.urdf")

# 尺寸（米）——蜂鸟~小型鸟尺度，总翼展约 0.6 m
BODY = dict(x=0.10, y=0.20, z=0.05)   # 机身（沿 Y 前向）
WING_SEG = [0.16, 0.14, 0.09]         # 翼三段展长：上臂/前臂/腕后
TAIL = dict(x=0.10, y=0.04, z=0.002)  # 尾板
HEAD = dict(r=0.03)

# 关节表：(名称, 父link, 子link, 轴xyz, 原点xyz(相对父link), 限位rad, 驱动幅度rad, 相位rad)
# 驱动幅度/相位给 preview 用：pos = amp*sin(2πft + phase)
J = []


def add_wing(side: int):
    """side: +1 左(x+) / -1 右(x-)。翼沿 ±x 展开。"""
    s = "l" if side > 0 else "r"
    sx = side
    # 肩三连转：flap(绕Y) → sweep(绕Z) → twist(绕X，翼展向)
    J.append((f"wing_{s}_flap", "body", f"shoulder_{s}",
              (0, 1, 0), (0, 0, 0), (-1.2, 1.2), 0.9, 0.0))
    J.append((f"wing_{s}_sweep", f"shoulder_{s}", f"twist_link_{s}",
              (0, 0, 1), (0, 0, 0), (-0.5, 0.5), 0.15, 1.5))
    J.append((f"wing_{s}_twist", f"twist_link_{s}", f"upper_arm_{s}",
              (sx, 0, 0), (0, 0, 0), (-0.8, 0.8), 0.45, math.pi / 2))
    # 肘、腕沿翼展
    J.append((f"wing_{s}_elbow", f"upper_arm_{s}", f"forearm_{s}",
              (0, 0, 1), (sx * WING_SEG[0], 0, 0), (-0.9, 0.9), 0.35, math.pi / 3))
    J.append((f"wing_{s}_wrist", f"forearm_{s}", f"hand_{s}",
              (0, 0, 1), (sx * WING_SEG[1], 0, 0), (-0.6, 0.6), 0.25, math.pi / 2.5))


add_wing(+1)
add_wing(-1)
# 尾三轴
J.append(("tail_pitch", "body", "tail_yaw_link", (0, 1, 0), (0, -BODY["y"] / 2, 0),
          (-0.6, 0.6), 0.10, math.pi))
J.append(("tail_yaw", "tail_yaw_link", "tail_roll_link", (0, 0, 1), (0, 0, 0),
          (-0.6, 0.6), 0.08, math.pi / 2))
J.append(("tail_roll", "tail_roll_link", "tail", (1, 0, 0), (0, 0, 0),
          (-0.5, 0.5), 0.0, 0.0))
# 颈两轴
J.append(("neck_pitch", "body", "neck_yaw_link", (0, 1, 0), (0, BODY["y"] / 2, 0.01),
          (-0.5, 0.5), 0.05, math.pi))
J.append(("neck_yaw", "neck_yaw_link", "head", (0, 0, 1), (0, 0, 0),
          (-0.8, 0.8), 0.10, math.pi / 2))

assert len(J) == 15, f"关节数应为15，实际{len(J)}"


def box(name, size, mass, color="0.7 0.7 0.75 1"):
    x, y, z = size
    return f"""  <link name="{name}">
    <inertial>
      <origin xyz="0 0 0"/>
      <mass value="{mass}"/>
      <inertia ixx="{mass*(y*y+z*z)/12:.3e}" iyy="{mass*(x*x+z*z)/12:.3e}" izz="{mass*(x*x+y*y)/12:.3e}"/>
    </inertial>
    <visual>
      <origin xyz="{x/2} 0 0"/>
      <geometry><box size="{x} {y} {z}"/></geometry>
      <material name="{name}_mat"><color rgba="{color}"/></material>
    </visual>
    <collision>
      <origin xyz="{x/2} 0 0"/>
      <geometry><box size="{x} {y} {z}"/></geometry>
    </collision>
  </link>
"""


def joint(name, parent, child, axis, origin, limits):
    lo, hi = limits
    return f"""  <joint name="{name}" type="revolute">
    <parent link="{parent}"/>
    <child link="{child}"/>
    <origin xyz="{origin[0]} {origin[1]} {origin[2]}"/>
    <axis xyz="{axis[0]} {axis[1]} {axis[2]}"/>
    <limit lower="{lo}" upper="{hi}" effort="5.0" velocity="30.0"/>
  </joint>
"""


def build():
    links = [box("body", (BODY["x"], BODY["y"], BODY["z"]), 0.012,
                 "0.85 0.55 0.2 1")]  # 机身金黄色
    # 翼各段（薄板，沿 x 展开）：肩/肘/腕三段
    for s, col in (("l", "0.25 0.45 0.85 0.9"), ("r", "0.25 0.45 0.85 0.9")):
        links.append(box(f"shoulder_{s}", (0.02, 0.02, 0.02), 0.001))
        links.append(box(f"twist_link_{s}", (0.02, 0.02, 0.02), 0.001))
        links.append(box(f"upper_arm_{s}", (WING_SEG[0], 0.10, 0.004), 0.002, col))
        links.append(box(f"forearm_{s}", (WING_SEG[1], 0.08, 0.004), 0.0015, col))
        links.append(box(f"hand_{s}", (WING_SEG[2], 0.05, 0.003), 0.001, col))
    links.append(box("tail_yaw_link", (0.02, 0.02, 0.02), 0.001))
    links.append(box("tail_roll_link", (0.02, 0.02, 0.02), 0.001))
    links.append(box("tail", (TAIL["x"], TAIL["y"], TAIL["z"]), 0.001, "0.6 0.3 0.3 1"))
    links.append(box("neck_yaw_link", (0.02, 0.02, 0.02), 0.0005))
    links.append(box("head", (0.05, 0.05, 0.05), 0.001, "0.9 0.2 0.2 1"))

    joints = "".join(joint(*row[0:1], row[1], row[2], row[3], row[4], row[5])
                     for row in [(
                         name, parent, child, axis, origin, limits
                     ) for name, parent, child, axis, origin, limits, *_ in J])

    urdf = ('<?xml version="1.0"?>\n<robot name="flapper_15dof">\n'
            + "".join(links) + joints + "</robot>\n")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(urdf)
    print(f"OK: {OUT}（15 关节）")


if __name__ == "__main__":
    build()
    # 同时导出驱动参数表给 preview / Isaac 关节驱动用
    import json
    drive = {row[0]: {"amp": row[6], "phase": row[7]} for row in J}
    with open(os.path.join(os.path.dirname(OUT), "flapper_15dof_drive.json"), "w") as f:
        json.dump(drive, f, indent=1)
    print("OK: flapper_15dof_drive.json（驱动幅度/相位表）")
