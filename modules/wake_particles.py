# -*- coding: utf-8 -*-
"""
wake_particles.py — 船首/船尾白沫与水花粒子系统
(Bow Spray & Stern Foam Particle System)

在 USV 的船首和船尾各创建一个 Omniverse 原生粒子发射器，
根据船速动态调整发射率，模拟破浪水花和螺旋桨尾迹泡沫。

可插拔: ENABLE_WAKE_PARTICLES=False 时不加载。
"""

import logging
import math
import numpy as np

logger = logging.getLogger("wake_particles")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(name)s %(levelname)s] %(message)s"))
    logger.addHandler(_handler)


class WakeParticleSystem:
    """
    管理 USV 船首和船尾的粒子发射器。

    根据船体速度动态调整发射率:
        - speed_u < 0.5 m/s → 不发射
        - speed_u > 3.0 m/s → 最大发射率
        - 中间线性插值
    """

    def __init__(
        self,
        stage,
        usv_prim_path: str,
        bow_offset: tuple = (5.0, 0.0, 0.2),
        stern_offset: tuple = (-4.0, 0.0, -0.3),
        max_emit_rate: float = 200.0,
        particle_lifespan: float = 1.5,
    ):
        """
        Parameters
        ----------
        stage : Usd.Stage
            USD Stage 引用。
        usv_prim_path : str
            USV 根 prim 路径。
        bow_offset : tuple
            船首发射器相对于 USV 中心的偏移 (body frame, m)。
        stern_offset : tuple
            船尾发射器相对于 USV 中心的偏移 (body frame, m)。
        max_emit_rate : float
            最大粒子发射率 (particles/s)。
        particle_lifespan : float
            粒子存活时间 (s)。
        """
        from pxr import UsdGeom, Gf, Sdf, UsdPhysics

        self.stage = stage
        self.usv_prim_path = usv_prim_path
        self.bow_offset = np.array(bow_offset, dtype=np.float64)
        self.stern_offset = np.array(stern_offset, dtype=np.float64)
        self.max_emit_rate = max_emit_rate
        self.particle_lifespan = particle_lifespan

        # 发射器 prim 路径
        self.bow_emitter_path = f"{usv_prim_path}/BowSpray"
        self.stern_emitter_path = f"{usv_prim_path}/SternFoam"

        # 创建粒子系统
        self._bow_particles = None
        self._stern_particles = None
        self._create_emitters(stage)

        logger.info(
            "WakeParticleSystem 初始化完成 | "
            "船首偏移=%s | 船尾偏移=%s | 最大发射率=%.0f/s | 存活时间=%.1fs",
            bow_offset, stern_offset, max_emit_rate, particle_lifespan,
        )

    def _create_emitters(self, stage):
        """创建船首和船尾的粒子发射器 Prim。"""
        from pxr import UsdGeom, Gf

        # 船首喷雾
        self._bow_xform = UsdGeom.Xform.Define(stage, self.bow_emitter_path)
        self._bow_xform.AddTranslateOp().Set(
            Gf.Vec3d(*self.bow_offset.tolist())
        )

        # 船尾泡沫
        self._stern_xform = UsdGeom.Xform.Define(stage, self.stern_emitter_path)
        self._stern_xform.AddTranslateOp().Set(
            Gf.Vec3d(*self.stern_offset.tolist())
        )

        # 注: Omniverse 的 ParticleSystem 需要通过 OmniGraph 或
        # omni.physx.particle 扩展创建。这里先创建 Xform 占位节点,
        # 后续可以手动在 Omniverse GUI 中添加粒子系统属性,
        # 或通过 omni.physx API 动态创建。
        #
        # 基础实现: 使用小球体集群模拟泡沫粒子
        self._bow_spheres = []
        self._stern_spheres = []
        self._max_particles = 20

        for i in range(self._max_particles):
            # 船首粒子
            bow_path = f"{self.bow_emitter_path}/Particle_{i:03d}"
            sphere = UsdGeom.Sphere.Define(stage, bow_path)
            sphere.GetRadiusAttr().Set(0.08)
            sphere.GetDisplayColorAttr().Set([Gf.Vec3f(0.9, 0.95, 1.0)])  # 白色水花
            sphere.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
            self._bow_spheres.append(sphere)

            # 船尾粒子
            stern_path = f"{self.stern_emitter_path}/Particle_{i:03d}"
            sphere = UsdGeom.Sphere.Define(stage, stern_path)
            sphere.GetRadiusAttr().Set(0.06)
            sphere.GetDisplayColorAttr().Set([Gf.Vec3f(0.8, 0.9, 1.0)])  # 淡蓝泡沫
            sphere.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
            self._stern_spheres.append(sphere)

        # 粒子状态
        self._particle_states = []  # (age, world_pos, velocity)
        self._time_since_emit = 0.0

    def update(
        self,
        speed_u: float,
        speed_v: float,
        ship_x: float = 0.0,
        ship_y: float = 0.0,
        ship_psi: float = 0.0,
        dt: float = 0.067,
    ) -> None:
        """
        根据船速更新粒子发射率和位置。

        Parameters
        ----------
        speed_u : float
            纵向速度 (surge) [m/s]。
        speed_v : float
            横向速度 (sway) [m/s]。
        ship_x, ship_y : float
            船体世界坐标 [m]。
        ship_psi : float
            船体航向 [rad]。
        dt : float
            时间步长 [s]。
        """
        from pxr import UsdGeom, Gf

        speed = abs(speed_u)

        # 计算发射率
        if speed < 0.5:
            emit_rate = 0.0
        elif speed > 3.0:
            emit_rate = self.max_emit_rate
        else:
            emit_rate = self.max_emit_rate * (speed - 0.5) / 2.5

        # 旋转矩阵 (body → world)
        cos_psi = math.cos(ship_psi)
        sin_psi = math.sin(ship_psi)

        # 更新现有粒子的年龄和位置
        new_states = []
        for age, pos, vel in self._particle_states:
            age += dt
            if age < self.particle_lifespan:
                # 简单重力 + 阻力
                vel[2] -= 9.81 * dt  # 重力
                vel *= 0.95           # 空气阻力
                pos += vel * dt
                new_states.append((age, pos, vel))
        self._particle_states = new_states

        # 发射新粒子
        self._time_since_emit += dt
        if emit_rate > 0:
            emit_interval = 1.0 / emit_rate
            while self._time_since_emit >= emit_interval and len(self._particle_states) < self._max_particles:
                self._time_since_emit -= emit_interval

                # 船首粒子: 向前上方喷射
                bow_world = np.array([
                    ship_x + cos_psi * self.bow_offset[0] - sin_psi * self.bow_offset[1],
                    ship_y + sin_psi * self.bow_offset[0] + cos_psi * self.bow_offset[1],
                    self.bow_offset[2],
                ])
                bow_vel = np.array([
                    cos_psi * speed * 0.3 + np.random.uniform(-0.5, 0.5),
                    sin_psi * speed * 0.3 + np.random.uniform(-0.5, 0.5),
                    np.random.uniform(0.5, 2.0),
                ])
                self._particle_states.append((0.0, bow_world.copy(), bow_vel))
        else:
            self._time_since_emit = 0.0

        # 同步到 USD prim 可见性和位置
        for i, sphere in enumerate(self._bow_spheres):
            if i < len(self._particle_states):
                age, pos, vel = self._particle_states[i]
                sphere.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
                xformable = UsdGeom.Xformable(sphere.GetPrim())
                ops = xformable.GetOrderedXformOps()
                if ops:
                    ops[0].Set(Gf.Vec3d(float(pos[0]), float(pos[1]), float(pos[2])))
                else:
                    xformable.AddTranslateOp().Set(
                        Gf.Vec3d(float(pos[0]), float(pos[1]), float(pos[2]))
                    )
            else:
                sphere.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
