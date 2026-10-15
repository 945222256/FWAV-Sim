# -*- coding: utf-8 -*-
"""probe_twist.py — twist 关节判定：静态跟踪 + 动态滞后测量"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIAG = os.path.join(ROOT, "logs", "probe_twist.txt")


def log(m):
    with open(DIAG, "a", encoding="utf-8") as f:
        f.write(m + "\n")
        f.flush()


open(DIAG, "w").close()
from isaacsim import SimulationApp
app = SimulationApp({"headless": True})

import numpy as np
import omni.usd
from isaacsim.core.api import World
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.prims import Articulation
from pxr import UsdPhysics

USD = os.path.join(ROOT, "assets/flapper/fwav_15dof.usd").replace("\\", "/")
omni.usd.get_context().new_stage()
add_reference_to_stage(usd_path=USD, prim_path="/World/Test")
stage = omni.usd.get_context().get_stage()
for prim in stage.Traverse():
    if prim.GetTypeName() == "PhysicsRevoluteJoint":
        if not prim.HasAPI(UsdPhysics.DriveAPI, "angular"):
            UsdPhysics.DriveAPI.Apply(prim, "angular")
        d = UsdPhysics.DriveAPI.Get(prim, "angular")
        d.GetStiffnessAttr().Set(60.0)
        d.GetDampingAttr().Set(0.25)

world = World(stage_units_in_meters=1.0, physics_dt=1/120.0, rendering_dt=1/120.0)
art = Articulation("/World/Test")
world.scene.add(art)
world.reset()
names = list(art.joint_names)
i_tw = names.index("twist_L")


def set_all(tw):
    tgt = [0.0] * len(names)
    tgt[i_tw] = tw
    art.set_joint_position_targets(
        positions=np.asarray([tgt], dtype=np.float32), joint_names=names)


# ── 工况1: 静态 0.3 rad，2 秒 ──
set_all(0.3)
for i in range(240):
    set_all(0.3)
    world.step(render=False)
p = np.asarray(art.get_joint_positions()).flatten()
log(f"静态: 目标 0.300 → 实际 {p[i_tw]:+.4f} "
    f"({'✅ 无静态缺陷' if abs(p[i_tw]-0.3) < 0.05 else '✘ 静态误差大'})")

# ── 工况2: ±0.175 rad @2.5Hz（等效 aero 模式量级），4 秒记录 ──
import math
freq = 2.5
recs = []
for i in range(480):
    t = i / 120.0
    tw = 0.175 * math.sin(2 * math.pi * freq * t)
    set_all(tw)
    world.step(render=False)
    p = np.asarray(art.get_joint_positions()).flatten()
    recs.append((t, tw, p[i_tw]))
# 稳态段(后3秒)幅值比与相位滞后
seg = [r for r in recs if r[0] > 1.0]
a_tgt = max(abs(r[1]) for r in seg)
a_act = max(abs(r[2]) for r in seg)
# 过零时刻差（上升沿）
zc_t = next(t for t, tw, _ in seg if tw > 0 and t > 1.0)
zc_a = next(t for t, tw, a in seg if t > zc_t and a > 0)
lag = zc_a - zc_t
log(f"动态: 目标幅值 {a_tgt:.3f} → 实际幅值 {a_act:.3f} "
    f"(比值 {a_act/a_tgt:.2f})，相位滞后 {lag*1000:.0f} ms "
    f"(= {lag*freq*360:.0f}°)")
app.close()
