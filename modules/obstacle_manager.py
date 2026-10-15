# -*- coding: utf-8 -*-
"""
obstacle_manager.py — 障碍物管理器 (Obstacle Manager)

本模块负责在仿真场景中生成和管理静态/动态障碍物:
    - 静态浮标 (Fixed Cylinder buoys)
    - 动态移动船只 (Dynamic Cuboid boats)

依赖:
    - Isaac Sim 5.1.0 (isaacsim.core.api.objects)
    - NumPy
"""

from __future__ import annotations

import math
import os
import random
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ── Isaac Sim 物体 API ───────────────────────────────────────────────────────
from isaacsim.core.api.objects import (
    DynamicCuboid,
    FixedCuboid,
)

# Isaac Sim 5.1.0 不直接导出 FixedCylinder, 我们通过几何 prim 工具创建圆柱体
from pxr import Gf, UsdGeom, UsdPhysics, UsdShade, Sdf

# ── 本项目配置 ────────────────────────────────────────────────────────────────
from config import sim_config


# =============================================================================
# 辅助函数 (Helper Functions)
# =============================================================================

def _create_fixed_cylinder_prim(
    stage,
    prim_path: str,
    position: Tuple[float, float, float],
    radius: float,
    height: float,
    color: Tuple[float, float, float],
) -> UsdGeom.Cylinder:
    """
    在 USD Stage 上创建一个带碰撞的固定圆柱体 prim。

    Isaac Sim 5.1.0 的 ``isaacsim.core.api.objects`` 不包含
    ``FixedCylinder`` 快捷类, 因此我们直接操作 USD Schema:
        1. 创建 UsdGeom.Cylinder
        2. 附加 UsdPhysics.CollisionAPI (碰撞)
        3. 附加 UsdPhysics.RigidBodyAPI 并将 kinematicEnabled 设为 True
           (使其不受重力影响但参与碰撞检测)
        4. 设置显示颜色 (displayColor)

    Args:
        stage:     当前 USD Stage.
        prim_path: 目标 prim 路径.
        position:  世界坐标 (X, Y, Z).
        radius:    圆柱半径 (m).
        height:    圆柱高度 (m).
        color:     显示颜色 (R, G, B) ∈ [0, 1].

    Returns:
        UsdGeom.Cylinder: 创建的圆柱体 prim.
    """
    cylinder = UsdGeom.Cylinder.Define(stage, prim_path)
    cylinder.GetRadiusAttr().Set(radius)
    cylinder.GetHeightAttr().Set(height)
    cylinder.GetAxisAttr().Set("Z")  # 竖直朝上

    # 设置位置 (translate)
    xformable = UsdGeom.Xformable(cylinder.GetPrim())
    xformable.ClearXformOpOrder()
    translate_op = xformable.AddTranslateOp()
    translate_op.Set(Gf.Vec3d(*position))

    # 碰撞体 (collision)
    UsdPhysics.CollisionAPI.Apply(cylinder.GetPrim())

    # ── 低摩擦物理材质 (Low-friction physics material) ──────────────
    # 水中湿滑表面：静摩擦 ≈ 0.02，动摩擦 ≈ 0.01，高回弹 0.6
    # 防止船体碰撞后被摩擦力锁死在障碍物表面
    # Wet surface in water: near-zero friction, moderate restitution
    # Prevents USV from getting permanently stuck on obstacle
    mat_path = f"{prim_path}/PhysicsMaterial"
    UsdShade.Material.Define(stage, mat_path)
    mat_prim = stage.GetPrimAtPath(mat_path)
    physics_mat = UsdPhysics.MaterialAPI.Apply(mat_prim)
    physics_mat.CreateStaticFrictionAttr(0.02)   # 近乎零静摩擦（湿滑 FRP/金属）
    physics_mat.CreateDynamicFrictionAttr(0.01)   # 近乎零动摩擦
    physics_mat.CreateRestitutionAttr(0.6)         # 中高回弹，碰撞后弹开
    # 绑定材质到碰撞体 / Bind material to collision body
    UsdShade.MaterialBindingAPI.Apply(cylinder.GetPrim()).Bind(
        UsdShade.Material(mat_prim), "physics"
    )

    # 设为运动学刚体 (kinematic rigid body — 固定但参与碰撞)
    rigid_api = UsdPhysics.RigidBodyAPI.Apply(cylinder.GetPrim())
    rigid_api.GetKinematicEnabledAttr().Set(True)

    # 显示颜色
    cylinder.GetDisplayColorAttr().Set([Gf.Vec3f(*color)])

    return cylinder


# =============================================================================
# ObstacleManager 类
# =============================================================================

class ObstacleManager:
    """
    障碍物管理器 — 负责在仿真世界中生成和更新各类障碍物。

    Typical usage:
        >>> mgr = ObstacleManager(world)
        >>> mgr.spawn_static_obstacles(num=5, radius=100.0)
        >>> mgr.spawn_dynamic_obstacles(num=3, radius=80.0)
        >>> # 每个物理步中:
        >>> mgr.update_dynamic_obstacles(dt=1/60)
    """

    def __init__(self, world) -> None:
        """
        初始化障碍物管理器。

        Args:
            world: Isaac Sim World 实例.
        """
        self._world = world
        self._stage = world.stage  # USD Stage 引用

        # 障碍物注册表 (registries)
        self._buoys: List[Dict[str, Any]] = []            # 静态浮标列表
        self._dynamic_obstacles: List[Dict[str, Any]] = []  # 动态障碍物列表

        # 全局计数器 (用于唯一命名)
        self._buoy_counter: int = 0
        self._dynamic_counter: int = 0

    # ──────────────────────────────────────────────────────────────────────
    # 静态浮标 (Static Buoy)
    # ──────────────────────────────────────────────────────────────────────
    def spawn_buoy(
        self,
        position: Tuple[float, float, float],
        radius: float = sim_config.BUOY_DEFAULT_RADIUS,
        color: Tuple[float, float, float] = (1.0, 0.0, 0.0),
    ) -> str:
        """
        在指定位置创建一个固定圆柱体浮标。

        浮标以圆柱体表示, 竖直放置, 底面与水面齐平。
        带有碰撞体, 为运动学刚体 (不受重力影响)。

        Args:
            position: 浮标世界坐标 (X, Y, Z).
            radius:   浮标半径 (m), 默认来自 sim_config.
            color:    浮标显示颜色 (R, G, B).

        Returns:
            str: 浮标的 USD prim 路径.
        """
        prim_path = f"/World/Obstacles/Buoy_{self._buoy_counter:03d}"
        height = sim_config.BUOY_DEFAULT_HEIGHT

        _create_fixed_cylinder_prim(
            stage=self._stage,
            prim_path=prim_path,
            position=position,
            radius=radius,
            height=height,
            color=color,
        )

        # 注册到内部列表
        self._buoys.append({
            "prim_path": prim_path,
            "position": np.array(position, dtype=np.float64),
            "radius": radius,
            "color": color,
        })
        self._buoy_counter += 1

        print(
            f"[ObstacleManager] 浮标 {prim_path} 已生成  "
            f"pos=({position[0]:.1f}, {position[1]:.1f}, {position[2]:.1f})  "
            f"r={radius:.2f} m  color={color}"
        )
        return prim_path

    def spawn_static_obstacles(
        self,
        num: int = sim_config.STATIC_OBSTACLE_COUNT,
        radius: float = sim_config.STATIC_OBSTACLE_RADIUS,
    ) -> List[str]:
        """
        随机生成若干静态浮标, 分布在原点周围的圆形区域内。

        浮标放置在水面 (Z = WATER_LEVEL) 上, 颜色从
        ``sim_config.BUOY_COLORS`` 中循环选取。

        Args:
            num:    浮标数量, 默认 ``sim_config.STATIC_OBSTACLE_COUNT``.
            radius: 分布半径 (m), 默认 ``sim_config.STATIC_OBSTACLE_RADIUS``.

        Returns:
            List[str]: 创建的所有浮标 prim 路径列表.
        """
        paths: List[str] = []
        colors = sim_config.BUOY_COLORS

        # 布局种子（2026-09-29 机主定睛：无种子随机柱林是非确定性注入
        # 源之一）。FLAPPER_OBSTACLE_SEED 指定时固定布局——同种子两跑
        # 柱位逐位一致，柱撞在双胞胎间成为确定性相同事件；不设时保持
        # 原随机行为。
        _seed = os.environ.get("FLAPPER_OBSTACLE_SEED")
        if _seed:
            random.seed(int(_seed))
            print(f"[ObstacleManager] 布局种子已固定: {_seed}", flush=True)

        for i in range(num):
            # 极坐标随机采样 → 笛卡尔坐标
            r = random.uniform(10.0, radius)       # 距原点距离 (至少 10 m)
            theta = random.uniform(0.0, 2.0 * math.pi)
            x = r * math.cos(theta)
            y = r * math.sin(theta)
            z = sim_config.WATER_LEVEL + sim_config.BUOY_DEFAULT_HEIGHT * 0.5  # 半高露出水面

            color = colors[i % len(colors)]
            path = self.spawn_buoy(position=(x, y, z), color=color)
            paths.append(path)

        print(
            f"[ObstacleManager] 共生成 {num} 个静态浮标  "
            f"(分布半径={radius:.0f} m)"
        )
        return paths

    # ──────────────────────────────────────────────────────────────────────
    # 动态障碍物 (Dynamic Obstacle)
    # ──────────────────────────────────────────────────────────────────────
    def spawn_dynamic_obstacle(
        self,
        idx: int,
        position: Tuple[float, float, float],
        velocity: Tuple[float, float, float],
    ) -> str:
        """
        创建一个动态移动的方块障碍物 (模拟小型船只)。

        使用 ``DynamicCuboid`` 并赋予初始线速度。
        尺寸按 ``sim_config.DYNAMIC_OBSTACLE_SIZE`` 配置
        (默认 3 m × 1 m × 1 m)。

        Args:
            idx:      障碍物序号 (用于唯一命名).
            position: 初始世界坐标 (X, Y, Z).
            velocity: 初始线速度 (Vx, Vy, Vz) m/s.

        Returns:
            str: 动态障碍物的 USD prim 路径.
        """
        prim_path = f"/World/Obstacles/DynBoat_{self._dynamic_counter:03d}"
        size_l, size_w, size_h = sim_config.DYNAMIC_OBSTACLE_SIZE

        cuboid = DynamicCuboid(
            prim_path=prim_path,
            name=f"dyn_boat_{self._dynamic_counter:03d}",
            position=np.array(position, dtype=np.float64),
            # DynamicCuboid 的 scale 以半长度表示, 但 size 参数是全长度
            scale=np.array([size_l, size_w, size_h], dtype=np.float64),
            color=np.array([0.4, 0.4, 0.6]),  # 灰蓝色 (gray-blue boat)
            mass=500.0,                         # 约 500 kg 小艇
        )

        # 注册到内部列表, 携带速度信息以便 update 使用
        self._dynamic_obstacles.append({
            "prim_path": prim_path,
            "cuboid": cuboid,
            "position": np.array(position, dtype=np.float64),
            "velocity": np.array(velocity, dtype=np.float64),
            "idx": idx,
        })
        self._dynamic_counter += 1

        print(
            f"[ObstacleManager] 动态障碍 {prim_path} 已生成  "
            f"pos=({position[0]:.1f}, {position[1]:.1f}, {position[2]:.1f})  "
            f"vel=({velocity[0]:.2f}, {velocity[1]:.2f}, {velocity[2]:.2f}) m/s"
        )
        return prim_path

    def spawn_dynamic_obstacles(
        self,
        num: int = sim_config.DYNAMIC_OBSTACLE_COUNT,
        radius: float = sim_config.DYNAMIC_OBSTACLE_RADIUS,
    ) -> List[str]:
        """
        批量生成动态障碍物, 随机初始位置和速度。

        障碍物沿水面运动 (Vz = 0), 速度大小在
        ``[DYNAMIC_OBSTACLE_SPEED_MIN, DYNAMIC_OBSTACLE_SPEED_MAX]`` 之间。

        Args:
            num:    障碍物数量.
            radius: 分布半径 (m).

        Returns:
            List[str]: 所有动态障碍物 prim 路径.
        """
        paths: List[str] = []

        for i in range(num):
            # 随机位置 (极坐标 → 笛卡尔)
            r = random.uniform(20.0, radius)
            theta = random.uniform(0.0, 2.0 * math.pi)
            x = r * math.cos(theta)
            y = r * math.sin(theta)
            z = sim_config.WATER_LEVEL + sim_config.DYNAMIC_OBSTACLE_SIZE[2] * 0.5

            # 随机速度方向 (水平面)
            speed = random.uniform(
                sim_config.DYNAMIC_OBSTACLE_SPEED_MIN,
                sim_config.DYNAMIC_OBSTACLE_SPEED_MAX,
            )
            heading = random.uniform(0.0, 2.0 * math.pi)
            vx = speed * math.cos(heading)
            vy = speed * math.sin(heading)
            vz = 0.0

            path = self.spawn_dynamic_obstacle(
                idx=i,
                position=(x, y, z),
                velocity=(vx, vy, vz),
            )
            paths.append(path)

        print(
            f"[ObstacleManager] 共生成 {num} 个动态障碍  "
            f"(分布半径={radius:.0f} m, "
            f"速度范围={sim_config.DYNAMIC_OBSTACLE_SPEED_MIN:.1f}–"
            f"{sim_config.DYNAMIC_OBSTACLE_SPEED_MAX:.1f} m/s)"
        )
        return paths

    # ──────────────────────────────────────────────────────────────────────
    # 动态障碍物更新 (Physics-Step Update)
    # ──────────────────────────────────────────────────────────────────────
    def update_dynamic_obstacles(self, dt: float) -> None:
        """
        每个物理步调用一次, 沿各动态障碍物的速度向量做线性推进,
        并在超出边界时执行环绕 (wrap-around)。

        环绕逻辑:
            当障碍物 X 或 Y 坐标超出
            ``±sim_config.DYNAMIC_OBSTACLE_BOUND`` 时,
            将该坐标映射到对侧边界, 实现无限循环巡逻效果。

        Args:
            dt: 物理步长 (s), 通常为 ``sim_config.PHYSICS_DT``
                或 ``1.0 / 60.0``.
        """
        bound = sim_config.DYNAMIC_OBSTACLE_BOUND

        for entry in self._dynamic_obstacles:
            pos = entry["position"]
            vel = entry["velocity"]

            # ── 线性推进 ────────────────────────────────────────────────
            pos[0] += vel[0] * dt
            pos[1] += vel[1] * dt
            # Z 保持水面高度不变
            pos[2] = (
                sim_config.WATER_LEVEL
                + sim_config.DYNAMIC_OBSTACLE_SIZE[2] * 0.5
            )

            # ── 环绕边界处理 ────────────────────────────────────────────
            for axis in (0, 1):  # X, Y
                if pos[axis] > bound:
                    pos[axis] = -bound + (pos[axis] - bound)
                elif pos[axis] < -bound:
                    pos[axis] = bound - (-bound - pos[axis])

            # ── 应用新位姿到 USD prim ──────────────────────────────────
            cuboid = entry["cuboid"]
            cuboid.set_world_pose(
                position=pos.copy(),
                orientation=np.array([1.0, 0.0, 0.0, 0.0]),  # 保持默认朝向
            )

    # ──────────────────────────────────────────────────────────────────────
    # 查询接口 (Query Interface)
    # ──────────────────────────────────────────────────────────────────────
    def get_obstacle_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        返回所有障碍物的当前位置信息, 供路径规划器使用。

        Returns:
            dict: 以 prim 路径为键, 值为包含位置和类型信息的子字典。

            示例:
                {
                    "/World/Obstacles/Buoy_000": {
                        "type": "static",
                        "position": np.array([10.0, 20.0, 0.5]),
                        "radius": 0.3,
                    },
                    "/World/Obstacles/DynBoat_000": {
                        "type": "dynamic",
                        "position": np.array([50.0, -30.0, 0.5]),
                        "velocity": np.array([2.0, 1.0, 0.0]),
                        "size": (3.0, 1.0, 1.0),
                    },
                }
        """
        result: Dict[str, Dict[str, Any]] = {}

        # ── 静态浮标 ────────────────────────────────────────────────────
        for buoy in self._buoys:
            result[buoy["prim_path"]] = {
                "type": "static",
                "position": buoy["position"].copy(),
                "radius": buoy["radius"],
            }

        # ── 动态障碍物 ──────────────────────────────────────────────────
        for dyn in self._dynamic_obstacles:
            result[dyn["prim_path"]] = {
                "type": "dynamic",
                "position": dyn["position"].copy(),
                "velocity": dyn["velocity"].copy(),
                "size": sim_config.DYNAMIC_OBSTACLE_SIZE,
            }

        return result


# ============================================================
# Public API (for main_sim.py)
# ============================================================

_manager = None

def spawn_obstacles(world):
    """Spawn all obstacles (static and dynamic)."""
    global _manager
    _manager = ObstacleManager(world)
    _manager.spawn_static_obstacles()
    _manager.spawn_dynamic_obstacles()
    return _manager

def get_manager():
    """Get the obstacle manager instance."""
    return _manager
