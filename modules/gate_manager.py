# -*- coding: utf-8 -*-
"""
gate_manager.py — 圆形穿越门（circular gate）程序化生成
========================================================

SparrowHawk (TII 2026) §VI.C：80 cm 直径圆孔门。本模块用 numpy 生成
矩形截面环（回转体）网格 → UsdGeom.Mesh，无需任何外部资产：

    内半径 r_in   = GATE_DIAMETER/2（80cm 孔）
    外半径 r_out  = 0.55 m（环带径向宽度 ~15cm）
    轴向厚度 depth= 0.08 m（通道深度）

门面默认法向 = 世界 +X（REP-103 x前），yaw 可调。
碰撞：CollisionAPI + kinematic RigidBody（与 obstacle_manager 同手法）。

GT 接口：get_gate_circle() 返回 (圆心世界坐标, 法向单位向量, 孔半径)，
供感知组件投影真值（perception/sim_adapter.project_circle_gt）。
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Tuple

import numpy as np

from pxr import Gf, UsdGeom, UsdPhysics, UsdShade, Sdf

logger = logging.getLogger(__name__)

GATE_ROOT_PATH = "/World/Perception/Gate"

# 默认几何（米）；被 config.sim_config 的感知段覆盖
DEFAULT_INNER_R: float = 0.40      # 80cm 直径孔
DEFAULT_OUTER_R: float = 0.55
DEFAULT_DEPTH: float = 0.08
SEGMENTS: int = 96                 # 回转分段（圆整度足够 Hough 检测）
# 哑光白：检测管线第一步是灰度化——红环对蓝天的彩色对比强但灰度对比
# 仅 ~11-33 灰阶（Canny(30) 环带边缘覆盖率实测仅 3%，Hough 全灭）；
# 白环对天空(~178)/草地(~143) 两侧都有 50+ 灰阶对比。弱光工况白色仍
# 是场景内最亮结构，保留"可检但退化"的难度梯度（论文 60% vs 80%）。
# 哑光黑剪影：穹顶光为主光源时漫反射亮度 ∝ albedo·光照 ≤ 天空亮度
# （白环对天空灰度差天花板 ~7%，实测 Hough 全灭；UsdPreviewSurface 的
# emissiveColor 在本 RTX 实时路径不生效，亮天带 >220 反而全是天空）。
# 黑环 albedo 0.03：对天空(~150-250)/草地(~130-160) 两侧 Δ≥120 灰阶；
# 弱光档天空(~30-60)仍亮于黑环，保留"可检但被噪声侵蚀"的难度梯度。
RING_COLOR: Tuple[float, float, float] = (0.03, 0.03, 0.03)
EMISSIVE_COLOR: Tuple[float, float, float] = (0.0, 0.0, 0.0)


def spawn_gate(stage,
               position: Tuple[float, float, float],
               yaw_deg: float = 0.0,
               inner_r: float = DEFAULT_INNER_R,
               outer_r: float = DEFAULT_OUTER_R,
               depth: float = DEFAULT_DEPTH,
               color: Tuple[float, float, float] = RING_COLOR,
               prim_path: str = GATE_ROOT_PATH) -> None:
    """在 stage 上生成一个静态环形门。

    position: 孔圆心的世界坐标 (x,y,z)
    yaw_deg : 门面法向的水平偏航（0 = 法向 +X，相机从 +X 侧接近）

    朝向直接烘焙进网格顶点（沿 gate_normal 的 e1/e2 平面回转），
    不用父级旋转换算——SetRotate(0,yaw,0) 在 yaw=0 时不产生任何旋转，
    环法向保持局部 +Z = 世界竖直向上，门会平躺在地上（实测踩坑：
    渲染出来是一条水平横带而非圆环）。
    """
    normal = gate_normal(yaw_deg)
    root = UsdGeom.Xform.Define(stage, prim_path)
    xform = UsdGeom.XformCommonAPI(root)
    xform.SetTranslate(Gf.Vec3d(*position))

    mesh = UsdGeom.Mesh.Define(stage, prim_path + "/Ring")
    pts, idx, counts = _ring_mesh(inner_r, outer_r, depth, normal)
    mesh.CreatePointsAttr([Gf.Vec3f(*p) for p in pts])
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(idx)
    mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    # 程序化回转环的顶点绕向不定 → 法向可能朝内（实测渲染成暗色实体）。
    # 双面渲染由渲染器翻转背面法向，光照即恢复。
    mesh.CreateDoubleSidedAttr(True)

    # 碰撞（静态运动学，穿门判定用；感知只看渲染）
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    rb = UsdPhysics.RigidBodyAPI.Apply(mesh.GetPrim())
    rb.GetKinematicEnabledAttr().Set(True)

    _bind_material(stage, prim_path + "/RingMat", mesh, color)
    logger.info("圆门已生成: %s pos=%s yaw=%.1f° 孔径=%.2fm",
                prim_path, position, yaw_deg, 2.0 * inner_r)


def _ring_mesh(inner_r: float, outer_r: float, depth: float,
               normal: Optional[np.ndarray] = None):
    """矩形截面回转环网格（顶点/面索引/每面顶点数）。

    环面位于法向 normal 的平面内（默认竖直、法向 +X）；轴向 = normal，
    径向截面 [inner_r, outer_r] × [-depth/2, +depth/2]。
    """
    n = np.asarray(normal if normal is not None else (1.0, 0.0, 0.0),
                   dtype=float)
    n = n / np.linalg.norm(n)
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(n, a); e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    # 截面四角（环面内极坐标 r，轴向 n）
    levels = [
        (inner_r, -depth / 2.0),
        (inner_r, +depth / 2.0),
        (outer_r, +depth / 2.0),
        (outer_r, -depth / 2.0),
    ]
    pts = []
    for k in range(SEGMENTS):
        th = 2.0 * math.pi * k / SEGMENTS
        d = math.cos(th) * e1 + math.sin(th) * e2
        for (r, z) in levels:
            pts.append(tuple(r * d + z * n))
    counts, idx = [], []
    for k in range(SEGMENTS):
        k_next = (k + 1) % SEGMENTS
        for lv in range(4):                       # 4 条环带面
            lv_next = (lv + 1) % 4
            counts.append(4)
            idx.extend([k * 4 + lv, k * 4 + lv_next,
                        k_next * 4 + lv_next, k_next * 4 + lv])
    return pts, idx, counts


def _bind_material(stage, mat_path: str, mesh: UsdGeom.Mesh,
                   color: Tuple[float, float, float]) -> None:
    mat = UsdShade.Material.Define(stage, mat_path)
    shader = UsdShade.Shader.Define(stage, mat_path + "/Shader")
    shader.GetImplementationSourceAttr().Set(UsdShade.Tokens.id)
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.6)
    # 自发光：穹顶光为主光源时，任何漫反射材质都≤天空亮度（albedo≤1），
    # 环对天空的灰度对比天花板 ~7%，Canny 必死（实测多轮）。LED 门是
    # drone-race 基准的现实做法：自发光使环在任意光照下都是最亮结构，
    # 弱光下退化为"可见但被传感器噪声淹没"——即论文 60% vs 80% 工况。
    shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*EMISSIVE_COLOR))
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(UsdShade.ConnectableAPI(shader), "surface")
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)


def get_gate_circle(position: Tuple[float, float, float],
                    yaw_deg: float,
                    inner_r: float = DEFAULT_INNER_R
                    ) -> Tuple[np.ndarray, np.ndarray, float]:
    """GT：孔圆心/法向/孔半径（世界系）。法向 = gate_normal(yaw)。"""
    return (np.asarray(position, dtype=float), gate_normal(yaw_deg), inner_r)


def gate_normal(yaw_deg: float) -> np.ndarray:
    """门面法向（水平偏航；yaw=0 → +X，90° → +Y）。"""
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    return np.array([c, s, 0.0])
