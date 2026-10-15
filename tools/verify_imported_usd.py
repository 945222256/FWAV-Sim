# -*- coding: utf-8 -*-
"""verify_imported_usd.py — 开箱校验导出的 USD（结果写 logs/verify_diag.txt，kit 会吞 stdout）"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USD = os.path.join(ROOT, "assets", "flapper", "fwav_15dof.usd")
DIAG = os.path.join(ROOT, "logs", "verify_diag.txt")


def log(msg):
    with open(DIAG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        f.flush()


open(DIAG, "w").close()

from isaacsim import SimulationApp
simulation_app = SimulationApp({"headless": True})

from pxr import Usd, UsdPhysics

stage = Usd.Stage.Open(USD.replace("\\", "/"))
if stage is None:
    log(f"✘ 无法打开 {USD}")
    simulation_app.close()
    sys.exit(2)

joints, links, art_root, drives = [], [], [], []
for prim in stage.Traverse():
    tn = prim.GetTypeName()
    if tn in ("PhysicsRevoluteJoint", "PhysicsJoint", "RevoluteJoint"):
        joints.append(prim.GetName())
        if prim.HasAPI(UsdPhysics.DriveAPI):
            drives.append(prim.GetName())
    elif prim.HasAPI(UsdPhysics.ArticulationRootAPI):
        art_root.append(str(prim.GetPath()))
    elif tn in ("Rigid", "RigidBody", "Mesh", "Capsule", "Cube", "Sphere", "Cylinder"):
        links.append(prim.GetName())

log(f"default prim: {stage.GetDefaultPrim()}")
log(f"joints({len(joints)}): {joints}")
log(f"articulation_root: {art_root}")
log(f"joint_drive_api({len(drives)}): {drives}")
log(f"geometry/rigid prims: {len(links)}")

need = ["flap_L", "flap_R", "twist_L", "twist_R",
        "fold_L", "fold_R", "tail_yaw", "tail_L", "tail_R"]
ok = all(j in joints for j in need)
log("✅ 9 关节齐全" if ok else f"✘ 缺 {[j for j in need if j not in joints]}")
log(("⚠ 无 ArticulationRootAPI（引用侧需手工补）") if not art_root else "ArticulationRoot ✓")

simulation_app.close()
sys.exit(0 if ok else 2)
