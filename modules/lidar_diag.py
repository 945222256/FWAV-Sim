"""
lidar_diag.py — 全面诊断 LiDAR 到底在哪
"""

import numpy as np
from pxr import UsdGeom
import omni.usd


class LidarDiag:
    def __init__(self, usv_prim_path="/World/USV/ProtectorUSV"):
        self.stage = omni.usd.get_context().get_stage()
        self.usv_path = usv_prim_path
        self.sensors_path = f"{usv_prim_path}/Sensors"
        self._full_dump_done = False

    def _get_world_pos(self, prim_path):
        prim = self.stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            return None
        xformable = UsdGeom.Xformable(prim)
        world_mtx = xformable.ComputeLocalToWorldTransform(0)
        t = world_mtx.ExtractTranslation()
        return np.array([t[0], t[1], t[2]])

    def check(self, world, step_count):
        if step_count % 240 != 0:
            return
        
        t = world.current_time
        usv_world = self._get_world_pos(self.usv_path)

        print(f"\n{'='*70}")
        print(f"[LIDAR DIAG] t={t:.2f}s  step={step_count}")
        print(f"  USV 世界坐标: {usv_world}")

        # 列出 Sensors 下所有子节点
        sensors_prim = self.stage.GetPrimAtPath(self.sensors_path)
        if sensors_prim.IsValid():
            children = list(sensors_prim.GetChildren())
            print(f"  Sensors 子节点数: {len(children)}")
            for ch in children:
                pos = self._get_world_pos(str(ch.GetPath()))
                print(f"    {ch.GetPath()}  type={ch.GetTypeName()}  pos={pos}")

        # 全局搜索: 递归整个 /World, 找所有非 Xform/Mesh/Material/Shader/Scope 的 prim
        if not self._full_dump_done:
            self._full_dump_done = True
            print(f"\n  === 全 Stage 非几何 prim 搜索 ===")
            skip_types = {"Xform", "Mesh", "Material", "Shader", "Scope", 
                         "GeomSubset", "DiskLight", "DomeLight", "DistantLight",
                         "Plane", "Cube", "Sphere", "Cylinder", "Capsule", "Cone",
                         "RectLight", "SphereLight", "NodeGraph", ""}
            world_prim = self.stage.GetPrimAtPath("/World")
            if world_prim.IsValid():
                count = [0]
                self._dump_non_geo(world_prim, skip_types, count)
                print(f"  === 共找到 {count[0]} 个非几何 prim ===")
        
        print(f"{'='*70}\n")

    def _dump_non_geo(self, prim, skip_types, count):
        for child in prim.GetChildren():
            typ = child.GetTypeName()
            if typ not in skip_types:
                pos = self._get_world_pos(str(child.GetPath()))
                print(f"    {child.GetPath()}  type={typ}  pos={pos}")
                count[0] += 1
            self._dump_non_geo(child, skip_types, count)
