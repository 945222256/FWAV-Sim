"""
flapper_loader.py — 扑翼 MAV 加载器
=====================================
从 isaac_usv_sim/modules/usv_loader.py 演化而来。

与 USV 版的关键差异：
  • USV 版加载单体刚体船 + 水动力学回调
  • 扑翼版必须加载 **多刚体铰接机构** (Articulation)：机身 + 左翼 + 右翼（+ 尾翼）
    → 用 Isaac Sim Articulation 而不是普通 RigidPrim
  • 删除 setup_hydro() —— 改为 setup_aero() 占位（气动力回调见 modules/aero_solver.py）
  • 物理 / 传感器 / pose 查询方法与 USV 版完全一致，便于上层复用

资产说明：
  当前 FLAPPER_USD_PATH 指向占位路径。优先使用 USD (推荐包含 Articulation + Joints)，
  其次可用 URDF（经 Isaac Sim URDF Importer 转换）。
  推荐来源见 docs/ASSET_SOURCES.md。
"""

import os
import sys
import numpy as np

# ── Isaac Sim imports (must be after SimulationApp creation) ──
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.prims import RigidPrim, XFormPrim
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Gf, Sdf
import omni.usd

# ── Config ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from config.sim_config import (
    FLAPPER_USD_PATH,
    FLAPPER_URDF_USD,
    FLAPPER_PRIM_PATH,
    INITIAL_POSITION,
    MASS,
    GROUND_LEVEL,
    JOINT_DRIVE_STIFFNESS,
    JOINT_DRIVE_DAMPING,
    # 向后兼容别名（与 usv_loader 同名）
    USV_PRIM_PATH,
    USV_INITIAL_POSITION,
)

# 传感器模块（直接沿用 USV 版，未改名）
from modules.sensor_setup import USVSensorSuite


class FlapperLoader:
    """扑翼 MAV 加载器 — 加载铰接机构、配置物理、挂载传感器。"""

    def __init__(self, world):
        self.world = world
        self.stage = omni.usd.get_context().get_stage()
        self.flapper_prim = None
        self.rigid_prim = None
        self.sensor_suite = None
        self.aero_callback = None

    def load_flapper(self):
        """
        加载扑翼 MAV 到场景。

        若 USD 文件不存在，会回退到 _create_placeholder() 生成一个最小
        可用的胶囊体 + 双翼占位机构，方便先跑通仿真主循环。

        Returns:
            刚体 prim 引用（RigidPrim 或 XFormPrim）。
        """
        print("[Flapper Loader] 开始加载扑翼 MAV...")

        # ── 让 MAV 根节点成为原生 Xform，外部 USD 作为子节点引用 ──
        # 这样可以在 MAV 节点下动态创建传感器（与 USV 版同一手法）
        from isaacsim.core.utils.prims import define_prim
        define_prim(FLAPPER_PRIM_PATH, "Xform")

        # ══ 路线1：关节样机（URDF Importer 转换产物）═════════════════
        # 关键：不剥物理 API、不套壳刚体、跳过 _setup_physics ——
        # articulation 的物理自洽，USV 式"剥 API+外壳再套刚体"会把树拆散。
        self.articulated = False
        self.articulation_root_api_path = None   # ArticulationRootAPI 所在 prim
        self.root_link_path = None               # base_link（位姿/速度读写的根连杆）
        self.joint_names = []                    # 关节名（URDF 定义序）

        if os.path.isfile(FLAPPER_URDF_USD):
            print(f"  [1/5] 加载关节样机 USD: {FLAPPER_URDF_USD}")
            model_path = f"{FLAPPER_PRIM_PATH}/Model"
            add_reference_to_stage(usd_path=FLAPPER_URDF_USD, prim_path=model_path)
            self.articulated = True

            # 普遍化发现：扫 ArticulationRootAPI / base_link / 关节
            for prim in self.stage.Traverse():
                p = str(prim.GetPath())
                if not p.startswith(model_path):
                    continue
                if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                    self.articulation_root_api_path = p
                if prim.GetName() == "base_link" and self.root_link_path is None:
                    self.root_link_path = p
                if prim.GetTypeName() in ("PhysicsRevoluteJoint", "PhysicsJoint"):
                    if prim.GetName():
                        self.joint_names.append(prim.GetName())

            # 关节驱动增益直接写进 USD DriveAPI（比运行时 API 稳）
            self._author_joint_drive_gains(model_path)
            # 显式补 CollisionAPI（防导入层丢失导致穿地）
            self._ensure_collisions(model_path)
            # 质量与惯量：URDF 即唯一来源（I-002 几何法重算 2026-09-30，
            # 旧 _unify_mass 运行时覆写已整体删除，数值链 params_registry）
            print(f"  [1/5] 关节样机就绪: {len(self.joint_names)} 关节, "
                  f"root_api={self.articulation_root_api_path}, "
                  f"root_link={self.root_link_path}")

        # ══ 路线2：单体 USD（USV 时代遗产）════════════════════════════
        elif os.path.isfile(FLAPPER_USD_PATH):
            usd_path = FLAPPER_USD_PATH
            print(f"  [1/5] Loading USD: {usd_path}")
            model_path = f"{FLAPPER_PRIM_PATH}/Model"
            add_reference_to_stage(usd_path=usd_path, prim_path=model_path)

            # ── 移除 Model 子节点上自带的嵌套 RigidBodyAPI / MassAPI ──
            # 与 USV 版同一坑：外部 USD 根 prim 带物理 API，加载为子节点后
            # 会与父节点形成嵌套刚体，PhysX 各跑各的。
            model_prim = self.stage.GetPrimAtPath(model_path)
            if model_prim.IsValid():
                if model_prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    model_prim.RemoveAPI(UsdPhysics.RigidBodyAPI)
                    print(f"  [FIX] 已移除 {model_path} 上的嵌套 RigidBodyAPI")
                if model_prim.HasAPI(UsdPhysics.MassAPI):
                    model_prim.RemoveAPI(UsdPhysics.MassAPI)
                    print(f"  [FIX] 已移除 {model_path} 上的嵌套 MassAPI")
        else:
            print(f"  [1/5] USD 不存在 ({usd_path})，生成占位机构")
            self._create_placeholder()

        # ── 2. 获取 prim 引用 ──
        self.flapper_prim = self.stage.GetPrimAtPath(FLAPPER_PRIM_PATH)
        if not self.flapper_prim.IsValid():
            raise RuntimeError(f"Failed to load flapper at {FLAPPER_PRIM_PATH}")
        print(f"  [2/5] Prim loaded: {FLAPPER_PRIM_PATH}")

        # ── 3. 物理属性（关节样机自带物理，跳过；仅占位/单刚体路线需要）──
        if self.articulated:
            print("  [3/5] Physics: 由 articulation USD 自带（跳过 _setup_physics）")
        else:
            self._setup_physics()
            print(f"  [3/5] Physics configured: mass={MASS} kg")

        # ── 4. 初始位姿 ──
        self._set_initial_pose()
        print(f"  [4/5] Initial pose set: {INITIAL_POSITION}")

        # ── 5. RigidPrim 包装（关节样机 → 包 base_link 根连杆；其余路线照旧）──
        wrap_path = self.root_link_path if (self.articulated and self.root_link_path) \
            else FLAPPER_PRIM_PATH
        try:
            self.rigid_prim = RigidPrim(wrap_path, name="flapper_mav")
            self.world.scene.add(self.rigid_prim)
            if self.articulated:
                print(f"  [5/5] RigidPrim 包装根连杆: {wrap_path}")
        except Exception as e:
            print(f"  WARNING: RigidPrim wrapping failed: {e}")
            try:
                self.rigid_prim = XFormPrim(wrap_path, name="flapper_mav")
                self.world.scene.add(self.rigid_prim)
            except Exception as e2:
                print(f"  WARNING: XFormPrim also failed: {e2}, using raw prim")
                self.rigid_prim = None

        print(f"  [5/5] MAV registered in World scene")
        print("[Flapper Loader] 扑翼 MAV 加载完成!")
        return self.rigid_prim

    def _author_joint_drive_gains(self, model_path: str):
        """把关节驱动增益直接写进引用层级的 USD DriveAPI。

        导入器只带 POSITION 驱动类型，增益缺省不可控；这里在 session 层
        统一写刚度/阻尼/最大力，PhysX 编译 articulation 时生效。
        """
        n = 0
        # 小惯量关节增益重标定（2026-09-28 根因修复，FLAPPER_DRIVE_RESCALE=1
        # 启用，默认关=行为逐位不变）：twist/fold/tail 按惯量重算的 (K,D)
        # （准则 ωn·dt≈2.2/步，近临界阻尼），flap 沿用全局值（其 ωn·dt
        # 本在设计准则内）。依据：docs/非确定性根因定案_欠分辨刚性驱动_评审版.md
        _import_csr = __import__("config.sim_config", fromlist=["x"])
        _rescale = getattr(_import_csr, "DRIVE_RESCALE_ON", False)
        _gains = getattr(_import_csr, "JOINT_DRIVE_GAINS_RESCALED", {}) or {}
        for prim in self.stage.Traverse():
            p = str(prim.GetPath())
            if not p.startswith(model_path):
                continue
            if prim.GetTypeName() not in ("PhysicsRevoluteJoint", "PhysicsJoint"):
                continue
            _k, _d = JOINT_DRIVE_STIFFNESS, JOINT_DRIVE_DAMPING
            if _rescale:
                _p_low = p.lower()
                for _key, _gd in _gains.items():
                    if _key in _p_low:
                        _k, _d = _gd
                        break
            # DriveAPI 是多实例 API：旋转关节的驱动实例名为 "angular"
            if not prim.HasAPI(UsdPhysics.DriveAPI, "angular"):
                UsdPhysics.DriveAPI.Apply(prim, "angular")
            drive = UsdPhysics.DriveAPI.Get(prim, "angular")
            if drive.GetStiffnessAttr() is not None:
                drive.GetStiffnessAttr().Set(_k)
            if drive.GetDampingAttr() is not None:
                drive.GetDampingAttr().Set(_d)
            if drive.GetMaxForceAttr() is not None:
                drive.GetMaxForceAttr().Set(5.0)
            # 关闭相邻连杆碰撞（翼拍进机身会让求解器对撞爆掉）
            if prim.GetTypeName() in ("PhysicsRevoluteJoint", "PhysicsJoint"):
                joint = UsdPhysics.RevoluteJoint(prim) \
                    if prim.GetTypeName() == "PhysicsRevoluteJoint" \
                    else UsdPhysics.Joint(prim)
                attr = joint.GetCollisionEnabledAttr()
                if attr is not None:
                    attr.Set(False)
            n += 1
        print(f"  [1/5] 关节驱动增益已写入: {n} 个 (K={JOINT_DRIVE_STIFFNESS}, "
              f"D={JOINT_DRIVE_DAMPING})")

    def _unify_mass_removed(self):
        """I-002 施工占位说明（2026-09-30 删除）。

        原 _unify_mass 按 cfg.MASS_TABLE 运行时覆写质量并把惯量按质量比
        线性缩放，是生效惯量虚高 6~13 倍的来源（items_registry I-002）。
        构造级整改：该错误路径整体删除，URDF 声明值（几何法重算产物，
        工具 tools/inertia_geometric_recalc.py）为唯一来源，质量分布入
        params_registry。此方法仅留档说明，无任何执行内容。
        """
        return None

    def _ensure_collisions(self, model_path: str):
        """给 collisions 子层补 CollisionAPI（导入器可能漏，漏了会穿地）。"""
        n = 0
        for prim in self.stage.Traverse():
            p = str(prim.GetPath())
            if not p.startswith(model_path):
                continue
            if "/collisions" in p and not prim.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.CollisionAPI.Apply(prim)
                n += 1
        if n:
            print(f"  [1/5] 补 CollisionAPI: {n} 个碰撞体")

    def _create_placeholder(self):
        """
        生成最小可用占位机构：机身(胶囊) + 左翼 + 右翼(薄方块)。
        翼几何与 config/sim_config.py 的 WingGeometryConfig 对齐
        (半展 0.10m，根弦 0.06m，尖弦 0.03m)，让视觉和气动一致。
        真实扑翼 USD 就位后此方法可删除。
        """
        import config.sim_config as sim_config
        span = sim_config.WingGeometryConfig.SPAN
        root_chord = sim_config.WingGeometryConfig.ROOT_CHORD
        body_len = sim_config.BODY_LENGTH

        # 机身（沿 X 轴胶囊）
        body = UsdGeom.Capsule.Define(self.stage, f"{FLAPPER_PRIM_PATH}/Model/Body")
        body.CreateHeightAttr(body_len)
        body.CreateRadiusAttr(0.015)
        body.GetAxisAttr().Set("X")
        body.GetDisplayColorAttr().Set([Gf.Vec3f(0.2, 0.2, 0.25)])

        # 左右翼（薄方块，展向沿 Y，弦向沿 X）
        for side, sign in (("Left", 1), ("Right", -1)):
            wing = UsdGeom.Cube.Define(self.stage, f"{FLAPPER_PRIM_PATH}/Model/Wing_{side}")
            wing.GetSizeAttr().Set(2.0)
            xf = UsdGeom.XformCommonAPI(wing)
            # 翼根偏置到机身两侧，翼面中心在半展处
            xf.SetTranslate(Gf.Vec3d(0.0, sign * span * 0.5, 0.0))
            # 尺寸：弦向(X)=平均弦，展向(Y)=半展，厚度(Z)=1mm
            mean_chord = (root_chord + sim_config.WingGeometryConfig.TIP_CHORD) * 0.5
            xf.SetScale(Gf.Vec3f(mean_chord * 0.5, span * 0.5, 0.0005))
            wing.GetDisplayColorAttr().Set([Gf.Vec3f(0.8, 0.8, 0.85)])

    def _setup_physics(self):
        """
        配置 RigidBody 和质量属性。
        注意：真实扑翼需要 Articulation + Joints（拍动铰链）。
        当前占位仅设单体刚体；待真实 USD 到位后此处需重写为
        显式 Joint 配置（驱动模式、PD 增益、力矩限幅）。
        """
        if not self.flapper_prim.HasAPI(UsdPhysics.RigidBodyAPI):
            UsdPhysics.RigidBodyAPI.Apply(self.flapper_prim)
        rigid_api = UsdPhysics.RigidBodyAPI(self.flapper_prim)
        rigid_api.GetRigidBodyEnabledAttr().Set(True)

        if not self.flapper_prim.HasAPI(UsdPhysics.MassAPI):
            UsdPhysics.MassAPI.Apply(self.flapper_prim)
        mass_api = UsdPhysics.MassAPI(self.flapper_prim)
        mass_api.GetMassAttr().Set(float(MASS))

        # 碰撞体降阶：逐三角形 → 凸包（沿用 USV 版性能优化）
        for child in Usd.PrimRange(self.flapper_prim):
            if child.HasAPI(UsdPhysics.MeshCollisionAPI):
                UsdPhysics.MeshCollisionAPI(child).GetApproximationAttr().Set("convexHull")

        # 物理材质（空中飞行，摩擦系数对扑翼影响不大，沿用中等值）
        mat_path = f"{self.flapper_prim.GetPath()}/BodyPhysicsMaterial"
        UsdShade.Material.Define(self.stage, mat_path)
        mat_prim = self.stage.GetPrimAtPath(mat_path)
        phys_mat = UsdPhysics.MaterialAPI.Apply(mat_prim)
        phys_mat.CreateStaticFrictionAttr(0.3)
        phys_mat.CreateDynamicFrictionAttr(0.2)
        phys_mat.CreateRestitutionAttr(0.3)
        material = UsdShade.Material(mat_prim)
        for child in Usd.PrimRange(self.flapper_prim):
            if child.HasAPI(UsdPhysics.CollisionAPI):
                UsdShade.MaterialBindingAPI.Apply(child).Bind(material, "physics")

    def _set_initial_pose(self):
        """设置扑翼 MAV 初始位姿（地面上方悬停准备起飞）。"""
        xform = UsdGeom.Xformable(self.flapper_prim)
        xform.ClearXformOpOrder()
        translate_op = xform.AddTranslateOp()
        pos = INITIAL_POSITION
        translate_op.Set(Gf.Vec3d(float(pos[0]), float(pos[1]), float(pos[2])))

    def setup_sensors(self, record_data: bool = False):
        """
        挂载传感器套件。直接复用 USVSensorSuite（API 与命名沿用）。
        record_data=True 时附加 Annotator/CopyToHost pipeline。
        """
        print("[Flapper Loader] 配置传感器...")
        self.sensor_suite = USVSensorSuite(self.world, FLAPPER_PRIM_PATH)

        import config.sim_config as sim_config

        if getattr(sim_config, "ENABLE_LIDAR", True):
            self.sensor_suite.setup_lidar(record_data=record_data)

        if getattr(sim_config, "ENABLE_STEREO", True):
            cam_l, cam_r = self.sensor_suite.setup_stereo_cameras(record_data=record_data)
            self.world.scene.add(cam_l)
            self.world.scene.add(cam_r)

        if getattr(sim_config, "ENABLE_SURROUND_CAMERAS", False):
            surround_cams = self.sensor_suite.setup_surround_cameras(record_data=record_data)
            for cam in surround_cams.values():
                self.world.scene.add(cam)

        if getattr(sim_config, "ENABLE_TOPDOWN_CAMERA", False):
            td_cam = self.sensor_suite.setup_topdown_camera(record_data=record_data)
            self.world.scene.add(td_cam)

        print("[Flapper Loader] 传感器配置完成!")
        return self.sensor_suite

    def setup_aero(self, flapper_view=None):
        """
        注册气动力学物理回调（Sane & Dickinson 2002 三分量模型）。
        USV 版这里调用 usv_hydro.HydroPhysicsCallback；扑翼版用
        aero_solver.AeroPhysicsCallback（镜像 USV 解耦架构）。

        Args:
            flapper_view: RigidPrimView 张量视图（热路径，零 Fabric 锁）。
                          传入后 callback 用它读写 PhysX；否则回退到单体 RigidPrim。
        """
        print("[Flapper Loader] 注册气动力学回调 (aero_solver) ...")
        try:
            from modules.aero_solver import create_aero_callback
            self.aero_callback = create_aero_callback(
                world=self.world,
                flapper_prim=self.rigid_prim,
                flapper_view=flapper_view,
            )
            self.world.add_physics_callback("flapper_aero", self.aero_callback)
            print("[Flapper Loader] 气动力学回调已注册!")
        except ImportError:
            print("[Flapper Loader] aero_solver 未就绪，跳过气动力（仅重力+PhysX默认）")
        except Exception as e:
            print(f"[Flapper Loader] 气动力学回调注册失败: {e}")
        return self.aero_callback

    # ── 位姿/速度查询（与 USV 版 API 完全一致，便于上层复用）──
    def get_pose(self):
        """获取 MAV 当前位姿 [x, y, z, qw, qx, qy, qz]"""
        if self.rigid_prim is not None:
            try:
                pos, quat = self.rigid_prim.get_world_poses()
                return np.concatenate([pos[0], quat[0]])
            except Exception as e:
                print(f"[FlapperLoader] get_world_poses failed: {e}")
                return None
        return None

    def get_velocity(self):
        """获取 MAV 当前速度 [vx, vy, vz, wx, wy, wz]"""
        if self.rigid_prim is not None:
            try:
                vel = self.rigid_prim.get_velocities()[0]
                return np.asarray(vel, dtype=np.float64)
            except Exception as e:
                print(f"[FlapperLoader] get_velocities failed: {e}")
                return None
        return None


# ── 公共接口函数（供 main_sim.py 调用，签名与 usv_loader 一致）──

_loader = None

def load_flapper(world):
    """加载扑翼 MAV 到世界场景。"""
    global _loader
    _loader = FlapperLoader(world)
    return _loader.load_flapper()

def setup_sensors(world, record_data: bool = False):
    """配置传感器。"""
    global _loader
    if _loader is None:
        raise RuntimeError("Flapper not loaded yet. Call load_flapper() first.")
    return _loader.setup_sensors(record_data=record_data)

def setup_aero(world, flapper_view=None):
    """注册气动力学回调。"""
    global _loader
    if _loader is None:
        raise RuntimeError("Flapper not loaded yet. Call load_flapper() first.")
    return _loader.setup_aero(flapper_view=flapper_view)

def get_loader():
    """获取加载器实例。"""
    return _loader
