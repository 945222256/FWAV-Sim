"""probe_joints.py — 独立探针：读导入 USD 里拍动关节的静止角与驱动目标（平行诊断件，零侵入）。"""
import os

from isaacsim import SimulationApp
_app = SimulationApp({"headless": True})

import omni.usd
from pxr import Usd, UsdPhysics, Gf

USD = r"G:\Playground\isaac_flapper_sim\assets\flapper\fwav_15dof.usd"
stage = Usd.Stage.Open(USD)

for prim in stage.Traverse():
    n = prim.GetName()
    if n not in ("flap_L", "flap_R", "twist_L", "twist_R", "fold_L", "fold_R"):
        continue
    attrs = []
    for a in prim.GetAttributes():
        v = a.Get()
        if v is not None and a.GetName().split(":")[0] == "physics":
            attrs.append(f"{a.GetName()}={v}")
    print(f"[probe] {n}: " + " | ".join(attrs), flush=True)

# 连杆坐标系探针：链接 prim 的 xformOp（进口器可能给连杆写静止位姿）
from pxr import UsdGeom
for prim in stage.Traverse():
    n = prim.GetName()
    if n not in ("base_link", "flapconn_L", "flapconn_R", "innerwing_L", "innerwing_R"):
        continue
    xf = UsdGeom.Xformable(prim)
    ops = []
    for op in xf.GetOrderedXformOps():
        v = op.Get()
        ops.append(f"{op.GetOpName()}={v}")
    print(f"[probe-link] {n}: " + " ; ".join(ops) if ops else f"[probe-link] {n}: (no xformOp)", flush=True)

_app.close()
