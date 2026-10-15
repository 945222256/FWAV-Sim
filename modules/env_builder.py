"""
env_builder.py — 扑翼 MAV 仿真环境构建模块
============================================
从 isaac_usv_sim/modules/env_builder.py 演化而来。

剥离内容（水相关，全部删除）：
  • 水面可视化网格 / Gerstner 波浪 / JONSWAP 波场
  • 海底平面 / 水材质 (OmniGlass MDL)
  • 潜艇 / 055 目标舰测试模型
  • 尾迹求解器耦合点

保留并改造内容（平台无关）：
  • 环境根 Xform 创建 (_ensure_xform)
  • 地形加载 (_load_terrain) —— 水面地形 → 森林/地面地形
  • Dome Light + HDRI 天空 (_add_dome_light)  —— 原样保留
  • Distant Light 太阳光 (_add_sun_light)      —— 原样保留
  • PhysX 场景重力 (_set_physics_scene_gravity)—— 原样保留
  • 边界空气墙 (_create_boundary_walls)        —— 沿用，围住飞行区域

新增内容：
  • 默认地面平面 (_create_ground_plane) —— 无地形 USD 时的兜底
  • 风场占位（具体实现见 modules/aero_solver.py）
"""

from __future__ import annotations

import os
import logging
import numpy as np
from typing import TYPE_CHECKING, Optional

# ---------------------------------------------------------------------------
# Isaac Sim & USD imports
# ---------------------------------------------------------------------------
from isaacsim.core.api.objects import VisualCuboid
from isaacsim.core.utils.stage import add_reference_to_stage
from pxr import Usd, UsdLux, UsdShade, Sdf, Gf, UsdPhysics, UsdGeom, Vt
import omni.usd

if TYPE_CHECKING:
    from isaacsim.core.api import World

# ---------------------------------------------------------------------------
# 项目配置
# ---------------------------------------------------------------------------
from config.sim_config import (
    GROUND_LEVEL,
    GROUND_PLANE_SIZE,
    GROUND_PLANE_THICKNESS,
    GROUND_COLOR,
    GROUND_ROUGHNESS,
    GROUND_METALLIC,
    GROUND_PATH,
    TERRAIN_USD_PATH,
    GRAVITY,
    DOME_LIGHT_INTENSITY,
    SUN_LIGHT_INTENSITY,
    SUN_LIGHT_ANGLE,
    ENV_ROOT_PATH,
    DOME_LIGHT_PATH,
    SUN_LIGHT_PATH,
    TERRAIN_PRIM_PATH,
)

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ===========================================================================
# Public API
# ===========================================================================

def build_environment(world: "World") -> None:
    """
    构建扑翼 MAV 仿真的完整环境。

    执行步骤：
        1. 确保环境根 Xform 存在
        2. 加载森林/地面地形 USD，或回退到默认地面平面
        3. 添加 Dome Light 环境光 (含 HDRI)
        4. 添加 Distant Light 太阳光
        5. 设置 PhysX 重力
        6. 创建飞行区域边界空气墙

    Args:
        world: 已初始化但未填充内容的 Isaac Sim World 实例。

    Returns:
        None (扑翼版无需像 USV 版那样返回 wave_updater)
    """
    stage = omni.usd.get_context().get_stage()

    # 0. 环境根 Xform
    _ensure_xform(stage, ENV_ROOT_PATH)
    logger.info("环境根节点已就绪: %s", ENV_ROOT_PATH)

    # 1. 地形（森林 USD 或默认地面）
    if not _load_terrain(world, stage):
        _create_ground_plane(stage)

    # 2. Dome Light
    _add_dome_light(stage)

    # 3. Sun Light
    _add_sun_light(stage)

    # 4. PhysX 重力
    _set_physics_scene_gravity(stage)

    # 5. 边界空气墙
    _create_boundary_walls(stage)

    logger.info("=== 扑翼环境构建完成 (Flapper env build complete) ===")


# ===========================================================================
# Internal helpers
# ===========================================================================

def _ensure_xform(stage, prim_path: str) -> None:
    """确保给定路径的 Xform prim 存在；逐级创建父节点。"""
    parts = prim_path.strip("/").split("/")
    current = ""
    for part in parts:
        current += "/" + part
        if not stage.GetPrimAtPath(current).IsValid():
            UsdGeom.Xform.Define(stage, current)
            logger.debug("Xform 已创建: %s", current)


def _load_terrain(world: "World", stage) -> bool:
    """
    加载森林 / 地面 USD 地形文件。

    Returns:
        True 加载成功；False 文件不存在（调用方应回退到默认地面）。
    """
    if TERRAIN_USD_PATH and os.path.isfile(TERRAIN_USD_PATH):
        add_reference_to_stage(usd_path=TERRAIN_USD_PATH, prim_path=TERRAIN_PRIM_PATH)
        logger.info("地形已加载: %s → %s", TERRAIN_USD_PATH, TERRAIN_PRIM_PATH)
        return True

    logger.info(
        "地形 USD 不存在 (%s)，将使用默认地面平面。"
        "请放入 assets/forest/forest_scene.usda 或修改 config.TERRAIN_USD。",
        TERRAIN_USD_PATH,
    )
    return False


def _create_ground_plane(stage) -> None:
    """无地形 USD 时的兜底：创建带碰撞的静态地面平面。"""
    VisualCuboid(
        prim_path=GROUND_PATH,
        name="Ground",
        position=np.array([0.0, 0.0, GROUND_LEVEL]),
        scale=np.array([GROUND_PLANE_SIZE, GROUND_PLANE_SIZE, GROUND_PLANE_THICKNESS]),
        color=np.array(GROUND_COLOR),
    )
    # 附加静态碰撞
    ground_prim = stage.GetPrimAtPath(GROUND_PATH)
    if ground_prim.IsValid():
        UsdPhysics.CollisionAPI.Apply(ground_prim)
    _apply_ground_material(stage)
    logger.info(
        "默认地面平面已创建: %s  尺寸=%.0fm×%.0fm",
        GROUND_PATH, GROUND_PLANE_SIZE, GROUND_PLANE_SIZE,
    )


def _apply_ground_material(stage) -> None:
    """草地/泥土材质 (UsdPreviewSurface 兜底)。"""
    mat_path = "/World/Environment/Materials/GroundMaterial"
    shader_path = mat_path + "/Shader"
    _ensure_xform(stage, "/World/Environment/Materials")

    material = UsdShade.Material.Define(stage, mat_path)
    shader = UsdShade.Shader.Define(stage, shader_path)
    shader.GetImplementationSourceAttr().Set(UsdShade.Tokens.id)
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*GROUND_COLOR)
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(GROUND_ROUGHNESS)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(GROUND_METALLIC)
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(
        UsdShade.ConnectableAPI(shader), "surface"
    )

    ground_prim = stage.GetPrimAtPath(GROUND_PATH)
    if ground_prim.IsValid():
        UsdShade.MaterialBindingAPI.Apply(ground_prim)
        UsdShade.MaterialBindingAPI(ground_prim).Bind(material)


def _add_dome_light(stage) -> None:
    """添加 Dome Light 作为环境漫射光照，附带 HDRI 天空贴图。"""
    dome_light = UsdLux.DomeLight.Define(stage, DOME_LIGHT_PATH)
    dome_light.CreateIntensityAttr(DOME_LIGHT_INTENSITY)

    # HDRI 天空贴图（沿用 USV 项目同一份 EXR）
    _HDRI_PATH = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), os.pardir,
        "assets", "hdri", "kloofendal_48d_partly_cloudy_puresky_4k.exr"
    ))
    if os.path.isfile(_HDRI_PATH):
        dome_light.CreateTextureFileAttr(_HDRI_PATH)
        dome_light.CreateTextureFormatAttr("latlong")
        logger.info("HDRI 天空贴图已加载: %s", _HDRI_PATH)
    else:
        dome_light.CreateTextureFileAttr("")
        dome_light.CreateColorAttr(Gf.Vec3f(0.5, 0.7, 1.0))   # 天空蓝兜底
        logger.info("HDRI 文件不存在 (%s)，使用纯色天空蓝", _HDRI_PATH)

    logger.info("Dome Light 已添加: %s  intensity=%.0f",
                DOME_LIGHT_PATH, DOME_LIGHT_INTENSITY)


def _add_sun_light(stage) -> None:
    """添加 Distant Light 模拟太阳平行光。"""
    sun_light = UsdLux.DistantLight.Define(stage, SUN_LIGHT_PATH)
    sun_light.CreateIntensityAttr(SUN_LIGHT_INTENSITY)
    sun_light.CreateAngleAttr(SUN_LIGHT_ANGLE)

    xformable = UsdGeom.Xformable(sun_light.GetPrim())
    xformable.ClearXformOpOrder()
    rot_op = xformable.AddXformOp(
        UsdGeom.XformOp.TypeRotateXYZ, UsdGeom.XformOp.PrecisionDouble
    )
    rot_op.Set(Gf.Vec3d(-45.0, 0.0, 30.0))
    logger.info("Distant Light (太阳光) 已添加: %s  intensity=%.0f",
                SUN_LIGHT_PATH, SUN_LIGHT_INTENSITY)


def _set_physics_scene_gravity(stage) -> None:
    """配置 PhysX 场景重力。沿用 USV 版 PhysX 调参。"""
    physics_scene_path = "/World/PhysicsScene"
    physics_prim = stage.GetPrimAtPath(physics_scene_path)

    if physics_prim.IsValid():
        scene_api = UsdPhysics.Scene(physics_prim)
    else:
        scene_api = UsdPhysics.Scene.Define(stage, physics_scene_path)
        logger.info("PhysicsScene 已创建: %s", physics_scene_path)

    scene_api.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0))
    scene_api.CreateGravityMagnitudeAttr(abs(GRAVITY[2]))
    logger.info("重力已设置: %s", GRAVITY)

    # PhysX 步率匹配 physics_dt (120Hz)，与 USV 一致
    physics_prim = stage.GetPrimAtPath(physics_scene_path)
    physics_prim.GetAttribute("physxScene:timeStepsPerSecond").Set(120)
    physics_prim.GetAttribute("physxScene:enableGPUDynamics").Set(False)
    # 宽相类型（2026-09-29 FW-IID 阶段二因素六）：默认 MBP 维持工程现
    # 状；FLAPPER_BROADPHASE 指定时切换（备选 SAP/ABP），供单因素矩阵。
    _bp = os.environ.get("FLAPPER_BROADPHASE", "MBP").strip().upper()
    physics_prim.GetAttribute("physxScene:broadphaseType").Set(_bp)
    print(f"[PHYS] broadphase={_bp}, GPU动力学USD层=False", flush=True)
    # 场景级 CCD 与增强确定性（2026-09-29 FW-IID 阶段二因素七与因素三
    # 的 USD 层权威开关）：FLAPPER_CCD=1 → physxScene:enableCCD=True；
    # FLAPPER_DET_OFF=1 → physxScene:enableEnhancedDeterminism=False。
    # 无论是否设置，启动一律打印两属性现值（判定与归档依据；schema
    # 默认值以 generatedSchema 为准，实际生效值以本打印为准）。
    try:
        from pxr import PhysxSchema as _PS
        _PS.PhysxSceneAPI.Apply(physics_prim)
        for _attr, _env, _val in (
                ("physxScene:enableCCD", "FLAPPER_CCD", True),
                ("physxScene:enableEnhancedDeterminism",
                 "FLAPPER_DET_OFF", False)):
            _a = physics_prim.GetAttribute(_attr)
            if not _a.IsValid():
                print(f"[PHYS] 场景属性 {_attr} 不存在（跳过）", flush=True)
                continue
            _before = _a.Get()
            if os.environ.get(_env) == "1":
                _a.Set(_val)
            print(f"[PHYS] 场景属性 {_attr}: {_before} → {_a.Get()}",
                  flush=True)
    except Exception as _e:
        print(f"[PHYS] 场景属性开关段失败: {_e}", flush=True)
    # 因素三的对称开臂（2026-09-29）：FLAPPER_DET_ON=1 显式置位增强确
    # 定性（当基线为关时的偏离臂）；与 DET_OFF 互斥时 DET_ON 后执行。
    try:
        if os.environ.get("FLAPPER_DET_ON") == "1":
            _a_on = physics_prim.GetAttribute(
                "physxScene:enableEnhancedDeterminism")
            if _a_on.IsValid():
                _a_on.Set(True)
                print("[PHYS] 场景属性 physxScene:enableEnhancedDeterminism"
                      " → True", flush=True)
    except Exception as _e_on:
        print(f"[PHYS] DET_ON 失败: {_e_on}", flush=True)


def _create_boundary_walls(stage) -> None:
    """
    创建四周的空气墙 (Invisible Collision Walls)。
    沿用 USV 版实现，围住飞行区域，防止 MAV 飞出场景边界。
    """
    half_size = GROUND_PLANE_SIZE / 2.0
    wall_thickness = 10.0
    wall_height = 50.0
    z_center = 20.0   # 墙体中心高度（飞行区域）

    walls = [
        ("Wall_North", Gf.Vec3d(0, half_size, z_center), Gf.Vec3f(half_size, wall_thickness/2, wall_height/2)),
        ("Wall_South", Gf.Vec3d(0, -half_size, z_center), Gf.Vec3f(half_size, wall_thickness/2, wall_height/2)),
        ("Wall_East",  Gf.Vec3d(half_size, 0, z_center), Gf.Vec3f(wall_thickness/2, half_size, wall_height/2)),
        ("Wall_West",  Gf.Vec3d(-half_size, 0, z_center), Gf.Vec3f(wall_thickness/2, half_size, wall_height/2)),
    ]

    walls_path = f"{ENV_ROOT_PATH}/BoundaryWalls"
    if not stage.GetPrimAtPath(walls_path).IsValid():
        UsdGeom.Xform.Define(stage, walls_path)

    for name, pos, scale in walls:
        wall_path = f"{walls_path}/{name}"
        cube = UsdGeom.Cube.Define(stage, wall_path)
        cube.GetSizeAttr().Set(2.0)
        xform = UsdGeom.XformCommonAPI(cube)
        xform.SetTranslate(pos)
        xform.SetScale(scale)
        cube.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())

    logger.info("已在 %s 创建 4 面飞行边界空气墙", walls_path)
