# -*- coding: utf-8 -*-
"""probe3 — probe + 地面 + flapper_loader 三件手术（补碰撞/关节碰撞禁写/质量等比）"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DIAG = os.path.join(ROOT, "logs", "probe_step3.txt")


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
from pxr import Usd, UsdGeom, UsdPhysics

USD = os.path.join(ROOT, "assets/flapper/fwav_15dof.usd").replace("\\", "/")
omni.usd.get_context().new_stage()
add_reference_to_stage(usd_path=USD, prim_path="/World/Test")
stage = omni.usd.get_context().get_stage()

# 地面（带碰撞，复刻 env_builder 关键路径）
ground = UsdGeom.Plane.Define(stage, "/World/Ground")
ground.CreateAxisAttr("Z")
if not ground.GetPrim().HasAPI(UsdPhysics.CollisionAPI):
    UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
log("地面+CollisionAPI")

# loader 三件手术（绕过类封装，逐段复刻）
MODEL = "/World/Test"
n = 0
for prim in stage.Traverse():
    p = str(prim.GetPath())
    if not p.startswith(MODEL):
        continue
    tn = prim.GetTypeName()
    if tn == "PhysicsRevoluteJoint":
        if not prim.HasAPI(UsdPhysics.DriveAPI, "angular"):
            UsdPhysics.DriveAPI.Apply(prim, "angular")
        d = UsdPhysics.DriveAPI.Get(prim, "angular")
        d.GetStiffnessAttr().Set(30.0)
        d.GetDampingAttr().Set(3.0)
        j = UsdPhysics.RevoluteJoint(prim)
        j.GetCollisionEnabledAttr().Set(False)
        n += 1
    if "/collisions" in p and not prim.HasAPI(UsdPhysics.CollisionAPI):
        UsdPhysics.CollisionAPI.Apply(prim)
log(f"手术1 关节驱动+碰撞禁写 {n} 个")
c = sum(1 for prim in stage.Traverse()
        if str(prim.GetPath()).startswith(MODEL)
        and "/collisions" in str(prim.GetPath()))
log(f"手术2 补碰撞体（含已有）共 {c} 个")
items = []
for prim in stage.Traverse():
    if str(prim.GetPath()).startswith(MODEL) and prim.HasAPI(UsdPhysics.MassAPI):
        m = UsdPhysics.MassAPI(prim).GetMassAttr().Get()
        if m and m > 0:
            items.append((prim, float(m)))
total = sum(m for _, m in items)
for prim, m in items:
    UsdPhysics.MassAPI(prim).GetMassAttr().Set(m * 0.700 / total)
log(f"手术3 质量等比 {total:.3f}→0.700kg")

world = World(stage_units_in_meters=1.0, physics_dt=1.0 / 120.0, rendering_dt=1.0 / 120.0)
art = Articulation("/World/Test")
world.scene.add(art)
world.reset()

names = list(art.joint_names or [])
idx = {nm: i for i, nm in enumerate(names)}
tgt = [0.0] * len(names)
tgt[idx["fold_L"]] = 0.5
for i in range(120):
    art.set_joint_position_targets(
        positions=np.asarray([tgt], dtype=np.float32), joint_names=names)
    world.step(render=False)
    if i % 40 == 39:
        p = np.asarray(art.get_joint_positions()).flatten()
        pos_z = None
        _pos, _q = art.get_world_poses()
        pos_z = float(np.asarray(_pos).flatten()[2])
        log(f"t={i/120:.2f}s fold_L={p[idx['fold_L']]:+.4f} flap_L={p[idx['flap_L']]:+.4f} z={pos_z:+.3f}")

p = np.asarray(art.get_joint_positions()).flatten()
log(f"结束 fold_L={p[idx['fold_L']]:+.4f}（目标0.5）→ "
    + ("✅ 手术不破坏驱动" if abs(p[idx['fold_L']] - 0.5) < 0.15 else "✘ 手术破坏了驱动"))
app.close()
