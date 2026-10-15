# -*- coding: utf-8 -*-
"""probe_drive_step.py — 隔离验证关节驱动链：USD → Articulation → 目标 → 步进 → 读回"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIAG = os.path.join(ROOT, "logs", "probe_step2.txt")


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
world = World(stage_units_in_meters=1.0, physics_dt=1.0 / 120.0, rendering_dt=1.0 / 120.0)
stage = omni.usd.get_context().get_stage()

# 会话层写增益（与 flapper_loader 相同路径）
n = 0
for prim in stage.Traverse():
    if prim.GetTypeName() == "PhysicsRevoluteJoint":
        if not prim.HasAPI(UsdPhysics.DriveAPI, "angular"):
            UsdPhysics.DriveAPI.Apply(prim, "angular")
        d = UsdPhysics.DriveAPI.Get(prim, "angular")
        d.GetStiffnessAttr().Set(30.0)
        d.GetDampingAttr().Set(3.0)
        n += 1
log(f"驱动增益写入 {n} 关节")

art = Articulation("/World/Test")
world.scene.add(art)
from isaacsim.core.prims import RigidPrim as RigidPrimView
root_link = None
for prim in stage.Traverse():
    if prim.GetName() == "base_link":
        root_link = str(prim.GetPath())
mav = RigidPrimView(prim_paths_expr=root_link, name="mav_tensor_view",
                    reset_xform_properties=False)
world.scene.add(mav)
log(f"mav_view={root_link}")
world.reset()

names = list(art.joint_names or [])
log(f"关节: {names}")
idx = {nm: i for i, nm in enumerate(names)}

p0 = np.asarray(art.get_joint_positions()).flatten()
log(f"初始 fold_L={p0[idx['fold_L']]:+.4f} flap_L={p0[idx['flap_L']]:+.4f}")

# 目标：fold_L=0.5, 其余 0
tgt = [0.0] * len(names)
tgt[idx["fold_L"]] = 0.5
art.set_joint_position_targets(
    positions=np.asarray([tgt], dtype=np.float32), joint_names=names)

def _force_step(dt):
    try:
        mav.apply_forces_and_torques_at_pos(
            forces=np.array([[0.0, 0.0, 8.0]], dtype=np.float32),
            torques=np.array([[0.0, 0.02, 0.0]], dtype=np.float32),
            is_global=True)
    except Exception as e:
        log(f"force err {e}")
world.add_physics_callback("fake_aero", _force_step)
log("fake aero force 已挂(8N@CG)")
for i in range(120):
    art.set_joint_position_targets(
        positions=np.asarray([tgt], dtype=np.float32), joint_names=names)
    world.step(render=False)
    if i % 30 == 29:
        p = np.asarray(art.get_joint_positions()).flatten()
        log(f"t={i/120:.2f}s fold_L={p[idx['fold_L']]:+.4f} "
            f"flap_L={p[idx['flap_L']]:+.4f}")

p = np.asarray(art.get_joint_positions()).flatten()
log(f"结束 fold_L={p[idx['fold_L']]:+.4f}（目标 0.5）→ "
    + ("✅ 驱动链正常" if abs(p[idx['fold_L']] - 0.5) < 0.1 else "✘ 未跟踪"))
app.close()
