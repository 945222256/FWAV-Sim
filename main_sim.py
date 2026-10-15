"""
main_sim.py — 扑翼微型飞行器（Micro Aerial Vehicle，MAV）仿真主入口
===================================================
本文件从 isaac_usv_sim/main_sim.py 演化而来。

功能流程：
  1. 启动 SimulationApp（必须在所有 isaacsim 模块导入之前）
  2. 创建 World，设置物理与渲染时间步长
  3. 构建仿真环境（地面/森林、天空、光照）
  4. 加载扑翼 MAV 模型及传感器（LiDAR、Camera）
  5. 生成静态/动态障碍物（柱状树干、飞鸟）
  6. 注册气动力学物理回调（占位实现）
  7. 运行仿真主循环（含 Timeline 无限时长的看护逻辑）
  8. 退出时执行清理

运行方式：
  g:\\Playground\\isaac_sim_5.1.0\\python.bat main_sim.py

从 USV 版保留的关键经验：
  • SimulationApp 必须最先启动，再 import isaacsim
  • world.reset() 会断开物理回调订阅，必须先 remove 再 add
  • Timeline 默认 endTimeCode 会在 8 秒后冻结物理 → 必须强制设为较大值
  • Camera 必须 world.scene.add()，否则会被 world.step 当作垃圾回收
  • RigidPrim 张量视图直写 PhysX（不经过 Fabric 加锁）
  • 永久 play() 看护逻辑：检测到 is_playing 变为 False 时自动恢复

从 USV 版剥离的内容：
  • 波浪 / 尾迹 / 水花粒子 / Gerstner / JONSWAP
  • HydroPhysicsCallback → 替换为 AeroCallback（占位）
  • HUD 管线 / BEV 拼接相关主循环调用
  • 水面材质 / 海底 / 潜艇 / 055 测试目标
"""

import sys
import codecs
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')

import math
import os
import time as _time
import numpy as np

# ============================================================
# 0. 路径准备
# ============================================================
PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ============================================================
# 1. 加载配置（纯 Python，不依赖 Isaac Sim）
# ============================================================
from config.sim_config import (
    SIM_APP_CONFIG,
    PHYSICS_DT,
    RENDER_DT,
    DATA_DIR,
    LOG_DIR,
    LOG_LEVEL,
    RECORD_SENSOR_DATA,
    ENABLE_SENSORS,
    FLAPPER_PRIM_PATH,
    SCENE_MODE,
    VIEWPORT_FOLLOW,
    VIEWPORT_OFFSET,
    INITIAL_POSITION,
)
INITIAL_POSITION_RESPAWN = np.asarray(INITIAL_POSITION, dtype=np.float32) + \
    np.asarray([0.0, 0.0, 1.0], dtype=np.float32)   # 重生点：初始位置上方 1m

# ============================================================
# 2. 启动 SimulationApp — 必须在任何 isaacsim 模块之前
# ============================================================
from isaacsim import SimulationApp

print("[main_sim] 正在启动 SimulationApp ...")
# 夜间开发（2026-09-20）：FLAPPER_HEADLESS=1 时强制无头模式（在环验收口径，
# 该模式下时基不膨胀，docs/口径对齐时基复核_215g.md 第三节）
_app_cfg = dict(SIM_APP_CONFIG)
if os.environ.get("FLAPPER_HEADLESS"):
    _app_cfg["headless"] = True

# 在环实验标注必填卡（2026-09-28 用户令，纪律第九条的执行机制）：
# 开数据流（FLAPPER_FLIGHT_LOG=1）的在环运行必须在启动时声明
# FLAPPER_TAG 与 FLAPPER_DESC（本次验证什么假设、结论口径），缺一拒绝
# 运行；元数据随数据流自动落 sidecar。豁免口 FLAPPER_ALLOW_NO_TAG=1
# 仅限纯排障（不产数下结论的运行）。
if os.environ.get("FLAPPER_FLIGHT_LOG") == "1" \
        and not os.environ.get("FLAPPER_ALLOW_NO_TAG"):
    _missing = [k for k in ("FLAPPER_TAG", "FLAPPER_DESC")
                if not os.environ.get(k, "").strip()]
    if _missing:
        raise SystemExit(
            "[TAG-FAIL] 在环实验标注必填：缺少 " + "、".join(_missing) +
            "。请以 FLAPPER_TAG=\"<标签>\" FLAPPER_DESC=\"<验证的假设与结论口径>\" "
            "启动；纯排障运行可设 FLAPPER_ALLOW_NO_TAG=1 豁免（其数据不得用于下结论）。")

simulation_app = SimulationApp(_app_cfg)
print("[main_sim] SimulationApp 启动完成。")

# ============================================================
# 3. 导入 Isaac Sim 核心模块（必须在 SimulationApp 创建之后）
# ============================================================
from isaacsim.core.api import World

from modules import env_builder
from modules import flapper_loader
from modules import obstacle_manager
from modules import lidar_diag
from modules.flapper_controller import TrajectoryTracker
from modules.aero_solver import (det_probe_enabled as _det_on,
                                 det_probe_read as _det_read)
# T6.3 拆分（2026-09-30）：九十三列数据流记录器迁至 modules/flight_recorder.py
from modules.flight_recorder import (_ident_stream_write,
                                     _ident_stream_close)
# T6.3 拆分（2026-09-30）：相机内录类迁至 modules/cam_recorder.py
from modules.cam_recorder import _CamRecorder
# T6.3 拆分（2026-09-30）：遥测与示波器外发迁至 modules/telemetry_bridge.py
from modules.telemetry_bridge import (_send_ctrl_scope,
                                      _send_joint_scope,
                                      _send_telemetry)
# T6.3 拆分（2026-09-30）：main() 5.6—5.7 环境变量桥接段迁至 modules/sim_env_bridge.py
from modules import sim_env_bridge
# T6.3 拆分（2026-09-30）：拍动关节驱动迁至 modules/wing_actuation.py
from modules.wing_actuation import _drive_wing_joints

# ============================================================
# 4. 日志配置
# ============================================================
import logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s][%(name)s][%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main_sim")

# ============================================================
# 5. 确保数据输出目录存在
# ============================================================
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
logger.info("数据目录已就绪: %s", DATA_DIR)


def main() -> None:
    """仿真主函数依次执行：构建场景、注册回调、运行循环、清理退出。"""

    # ----------------------------------------------------------
    # 5.1 创建 World
    # ----------------------------------------------------------
    logger.info("创建 World (physics_dt=%.6f s, rendering_dt=%.6f s) ...",
                PHYSICS_DT, RENDER_DT)
    world = World(
        stage_units_in_meters=1.0,
        physics_dt=PHYSICS_DT,
        rendering_dt=PHYSICS_DT,   # 与 physics_dt 一致，子步进由 _render_interval 手动控制
    )

    # ── 仿真器确定性判决支撑（2026-09-28 用户令）──
    # 启动打印物理器配置（GPU 动力学/线程）；FLAPPER_PHYS_CPU=1 时
    # 依次尝试已知接口强制 CPU 动力学+单线程（判决非确定性来源与
    # 根治验证用；不设时零触碰，行为与基线一致）
    try:
        _ph = world.get_physics_context()
        for _attr in ("use_gpu", "gpu_dynamics", "is_gpu_enabled"):
            _v = getattr(_ph, _attr, None)
            if _v is not None:
                print(f"[PHYS] physics_context.{_attr} = {_v}", flush=True)
        # 档位实读（2026-09-29 FW-IID 阶段二前置）：carb 设置表是引擎档
        # 位权威来源；上面的属性名探测在 5.1 全部失效（静默无输出实
        # 录），判定与归档以本打印为准。
        try:
            import carb
            _st0 = carb.settings.get_settings()
            for _k in ("/physics/physxUseGpuDynamics",
                       "/physics/physxSolverType",
                       "/physics/physxDeterministic",
                       "/physics/physxCpuThreadCount"):
                try:
                    print(f"[PHYS] carb(读) {_k} = {_st0.get(_k)}",
                          flush=True)
                except Exception:
                    print(f"[PHYS] carb(读) {_k} = <读取失败>", flush=True)
        except Exception as _e0:
            print(f"[PHYS] carb 档位读取失败: {_e0}", flush=True)
        # ── FW-IID 阶段二单因素开关族（2026-09-29，全部默认零触碰）──
        # 因素一 GPU 档：FLAPPER_PHYS_GPU=1 强制开 GPU 动力学；与
        # FLAPPER_PHYS_CPU 同设时 CPU 优先（见下段顺序）。
        if os.environ.get("FLAPPER_PHYS_GPU") == "1":
            try:
                import carb
                _stg = carb.settings.get_settings()
                for _p in ("/physics/physxUseGpuDynamics",
                           "physics/physxUseGpuDynamics"):
                    try:
                        _stg.set_bool(_p, True)
                        print(f"[PHYS] carb {_p} → True", flush=True)
                        break
                    except Exception:
                        continue
            except Exception as _e:
                print(f"[PHYS] GPU 档设置失败: {_e}", flush=True)
        # 因素三 确定性标志反向档：FLAPPER_DET_OFF=1 显式关闭增强确定
        # 性标志（默认档是否置位以档位实读打印为准）。
        if os.environ.get("FLAPPER_DET_OFF") == "1":
            try:
                import carb
                _std = carb.settings.get_settings()
                for _p in ("/physics/physxDeterministic",
                           "physics/physxDeterministic"):
                    try:
                        _std.set_bool(_p, False)
                        print(f"[PHYS] carb {_p} → False", flush=True)
                        break
                    except Exception:
                        continue
            except Exception as _e:
                print(f"[PHYS] 确定性标志关闭失败: {_e}", flush=True)
        # 因素二 单线程档：FLAPPER_PHYS_1THREAD=1 仅设仿真线程数为一，
        # 不动其他档位（与 FLAPPER_PHYS_CPU 的区别：CPU 档 bundled 确
        # 定性标志与 CPU 动力学）。
        if os.environ.get("FLAPPER_PHYS_1THREAD") == "1":
            try:
                _ph.set_sim_thread_count(1)
                print("[PHYS] set_sim_thread_count(1) 完成（单因素档）",
                      flush=True)
            except Exception as _e:
                print(f"[PHYS] set_sim_thread_count 不可用: {_e}", flush=True)
        # 因素五 求解迭代次数档：FLAPPER_ITERS="位置迭代,速度迭代"。
        if os.environ.get("FLAPPER_ITERS"):
            try:
                _pi, _vi = [int(x) for x in
                            os.environ["FLAPPER_ITERS"].split(",")]
                _ph.set_solver_iterations(position_iteration_count=_pi,
                                          velocity_iteration_count=_vi)
                print(f"[PHYS] solver iterations → 位置 {_pi} 速度 {_vi}",
                      flush=True)
            except Exception as _e:
                print(f"[PHYS] 迭代次数设置失败: {_e}", flush=True)
        # 求解器类型切换（2026-09-28 修复 R3 臂）：TGS 对小惯量刚性驱动
        # 是已知雷区，PGS 为备选；仅 FLAPPER_SOLVER 指定时改，默认零触碰
        if os.environ.get("FLAPPER_SOLVER"):
            try:
                import carb
                _st2 = carb.settings.get_settings()
                for _p2 in ("/physics/physxSolverType", "physics/physxSolverType"):
                    try:
                        _st2.set_string(_p2, os.environ["FLAPPER_SOLVER"])
                        print(f"[PHYS] solver type → {os.environ['FLAPPER_SOLVER']}", flush=True)
                        break
                    except Exception:
                        continue
            except Exception as _e:
                print(f"[PHYS] solver 切换失败: {_e}", flush=True)
        if os.environ.get("FLAPPER_PHYS_CPU") == "1":
            import carb
            _st = carb.settings.get_settings()
            for _p in ("/physics/physxUseGpuDynamics",
                       "physics/physxUseGpuDynamics"):
                try:
                    _before = _st.get(_p)
                    _st.set_bool(_p, False)
                    print(f"[PHYS] carb { _p }: {_before} → False",
                          flush=True)
                except Exception:
                    continue
            # 确定性模式（2026-09-28 机主令：枪毙非确定性）：PhysX 增强
            # 确定性开关逐候选尝试并打印前后值，不存在的接口静默跳过
            for _p in ("/physics/physxDeterministic",
                       "physics/physxDeterministic",
                       "/physics/physx_deterministic",
                       "/physics/deterministic"):
                try:
                    _before = _st.get(_p)
                    _st.set_bool(_p, True)
                    _after = _st.get(_p)
                    print(f"[PHYS] carb {_p}: {_before} → {_after}",
                          flush=True)
                except Exception:
                    continue
            try:
                _ph.set_sim_thread_count(1)
                print("[PHYS] set_sim_thread_count(1) 完成", flush=True)
            except Exception as _e:
                print(f"[PHYS] set_sim_thread_count 不可用: {_e}", flush=True)
    except Exception as _e:
        print(f"[PHYS] 配置打印失败: {_e}", flush=True)

    # ----------------------------------------------------------
    # 5.2 构建仿真环境（地面/森林、天空、光照）
    # ----------------------------------------------------------
    logger.info("构建仿真环境 ...")
    env_builder.build_environment(world)

    # ----------------------------------------------------------
    # 5.3 加载扑翼 MAV 模型
    # ----------------------------------------------------------
    logger.info("加载扑翼 MAV 模型 ...")
    flapper_prim = flapper_loader.load_flapper(world)

    # FW-IID 因素五 求解迭代次数档（2026-09-29 阶段二）：本版本
    # PhysicsContext 无 set_solver_iterations 接口（实测缺），迭代数走
    # USD 刚体层属性 physxRigidBody:solverIterationCounts（int2）。
    # FLAPPER_ITERS="位置迭代,速度迭代" 时遍历全部刚体设置并打印
    # 设置前的基线值（未指定时只打印基线，供加倍臂取数）；默认零触碰。
    try:
        import omni.usd as _ousd_it
        from pxr import UsdPhysics as _UP_it, Sdf as _Sdf_it
        _stage_it = _ousd_it.get_context().get_stage()
        _iters_env = os.environ.get("FLAPPER_ITERS", "").strip()
        _iters_tv = None
        if _iters_env:
            _pi2, _vi2 = [int(x) for x in _iters_env.split(",")]
            _iters_tv = (_pi2, _vi2)
        _n_it = 0
        _base_seen = set()
        for _prim_it in _stage_it.Traverse():
            if not _prim_it.HasAPI(_UP_it.RigidBodyAPI):
                continue
            _attr_it = _prim_it.GetAttribute(
                "physxRigidBody:solverIterationCounts")
            _base_seen.add(str(_attr_it.Get()) if _attr_it.IsValid()
                           else "<无属性=引擎默认>")
            if _iters_tv is not None:
                if not _attr_it.IsValid():
                    _attr_it = _prim_it.CreateAttribute(
                        "physxRigidBody:solverIterationCounts",
                        _Sdf_it.ValueTypeNames.Int2)
                _attr_it.Set(_iters_tv)
            _n_it += 1
        print(f"[PHYS] 迭代数遍历：{_n_it} 个刚体，基线值集合 "
              f"{sorted(_base_seen)}"
              + (f"，已设为 {_iters_tv}" if _iters_tv else ""), flush=True)
    except Exception as _e_it:
        print(f"[PHYS] 迭代数设置失败: {_e_it}", flush=True)

    # ----------------------------------------------------------
    # 5.4 生成障碍物（柱状树干 / 飞鸟）
    # ----------------------------------------------------------
    logger.info("生成障碍物 ...")
    # FW-IID 封锁项四（2026-09-29 方案阶段一）：固定全部随机数源种子。
    # 静态障碍（默认二十个）与动态飞鸟的位置速度由 random.uniform 生成、
    # 尾迹粒子由 np.random 生成；种子未固定时两次运行的场景本身就不同，
    # 属应用侧注入源。FLAPPER_IID_LOCK=1 时固定为零号种子；默认零触碰。
    if os.environ.get("FLAPPER_IID_LOCK") == "1":
        import random as _rnd_lock
        _rnd_lock.seed(0)
        np.random.seed(0)
        print("[IID-LOCK] 随机源已固定：random.seed(0), np.random.seed(0)",
              flush=True)
    obstacle_manager.spawn_obstacles(world)

    # ----------------------------------------------------------
    # 5.5 创建 RigidPrim 张量视图（直连 PhysX GPU 缓冲区，不经过 Fabric 加锁）
    #     关节样机视图覆盖 base_link 根连杆；占位刚体视图覆盖整个 FLAPPER_PRIM_PATH
    # ----------------------------------------------------------
    from isaacsim.core.prims import RigidPrim as RigidPrimView
    _loader = flapper_loader.get_loader()
    _mav_path = (getattr(_loader, "root_link_path", None)
                 if getattr(_loader, "articulated", False) else None) \
        or FLAPPER_PRIM_PATH
    mav_view = RigidPrimView(
        prim_paths_expr=_mav_path,
        name="mav_tensor_view",
        reset_xform_properties=False,
    )
    world.scene.add(mav_view)
    logger.info("RigidPrim 张量视图已创建: %s", _mav_path)

    # ----------------------------------------------------------
    # 5.5b 关节样机 Articulation（必须在 world.reset() 之前创建，
    #        reset 时自动 initialize——在 reset 之后创建会导致目标值写入静默失效）
    # ----------------------------------------------------------
    art = None
    if getattr(_loader, "articulated", False):
        try:
            from isaacsim.core.prims import Articulation as _Art
            _root_api = _loader.articulation_root_api_path or ""
            _art_path = _root_api.rsplit("/", 1)[0] if _root_api.endswith("root_joint") \
                else (_root_api or FLAPPER_PRIM_PATH)
            art = _Art(_art_path, name="fwav_articulation")
            world.scene.add(art)
            logger.info("关节样机 Articulation 已创建(reset前): %s", _art_path)
        except Exception as e:
            logger.warning("Articulation 创建失败（拍动驱动不可用）: %s", e)
            art = None

    # ----------------------------------------------------------
    # 5.6–5.7 环境变量桥接 + 网络/输入/飞控/看板构建
    #     （2026-09-30 T6.3 拆分：整段移至 modules/sim_env_bridge.py 的
    #      build_env_bridge，返回命名空间逐字段回填到原变量名，主循环零改动）
    # ----------------------------------------------------------
    _env_bridge = sim_env_bridge.build_env_bridge(world, mav_view)
    aero_callback = _env_bridge.aero_callback
    telemetry_sender = _env_bridge.telemetry_sender
    command_receiver = _env_bridge.command_receiver
    tracker = _env_bridge.tracker
    joint_scope_sender = _env_bridge.joint_scope_sender
    ctrl_scope_sender = _env_bridge.ctrl_scope_sender
    manual = _env_bridge.manual
    autopilot = _env_bridge.autopilot
    dashboard = _env_bridge.dashboard
    EXPERIMENT_MODE = _env_bridge.EXPERIMENT_MODE
    _motion_recipe = getattr(_env_bridge, "motion_recipe", None)
    _motion_runtime = None
    _motion_warmup = []
    globals()["_MOTION_RUNTIME"] = None
    # ----------------------------------------------------------
    # 5.8 重置 World，使所有添加的 prim 生效
    # ----------------------------------------------------------
    logger.info("执行 world.reset() ...")
    # 第六班结论（每步双触发的根因）：reset() 的 STOP 事件会清空 Python 侧
    # 回调字典，但 C++ 侧订阅不会随引用删除而退订——loader 在 reset 前
    # 注册的订阅因此泄漏存活，重新注册后同一回调被派发两次（力加倍）。
    # 修复方式：reset 前保存订阅句柄，reset 后显式 unsubscribe() 再注册。
    _pre_reset_sub = None
    try:
        _pre_reset_sub = getattr(world, "_physics_callback_functions",
                                 {}).get("flapper_aero")
    except Exception:
        pass
    world.reset()
    logger.info("World 已重置，仿真准备就绪。")
    if _pre_reset_sub is not None:
        try:
            _pre_reset_sub.unsubscribe()
            print("[SUB-FIX] 泄漏订阅已显式退订（reset 前 loader 注册）",
                  flush=True)
            if os.environ.get("FLAPPER_POST_STEP_CB") == "1":
                try:
                    _pre_reset_sub.destroy()
                    print("[SUB-FIX] 泄漏订阅句柄再destroy（判别unsubscribe是否空操作）",
                          flush=True)
                except Exception as _de:
                    print(f"[SUB-FIX] destroy失败: {_de!r}", flush=True)
        except Exception as _ue:
            try:
                _pre_reset_sub.destroy()
                print("[SUB-FIX] 泄漏订阅已 destroy()", flush=True)
            except Exception:
                print(f"[SUB-FIX] 退订失败: {_ue!r}（改由触发守护监控）",
                      flush=True)

    # ── 关键：world.reset() 后重新注册物理回调 ──
    # reset() 内部 stop()+play() 会断开 PhysX 事件订阅，
    # add_physics_callback 检测到同名条目会拒绝，必须先 remove 再 add。
    if aero_callback is not None and not os.environ.get("FLAPPER_NO_AERO"):
        try:
            world.remove_physics_callback("flapper_aero")
        except Exception:
            pass
        # ── 排障A/B围栏（2026-09-28，FLAPPER_POST_STEP_CB=1 时启用）──
        # legacy步进事件每个物理步在simulate段与fetch段各派发一次（实测
        # 每步恰2次，[SUB-FIX]退订后依旧），第一拍发生在异步求解期间，其
        # 状态读取与力施加同求解器并发，为间歇状态注入的嫌疑机制。本开关
        # 改用官方有序后置步事件（fetch_results之后恰一次），读取恒在求解
        # 完成后、力施加恒在步间隙；每步触发数由2变1，力缩放因子自洽为1。
        # 默认关：注册路径与基线逐字相同。
        if os.environ.get("FLAPPER_MAINLOOP_AERO") == "1":
            # 判别臂C：不订阅任何步事件，气动回调改由主循环在 world.step
            # 返回后（fetch_results阻塞已完成=物理全部收尾）的安全窗口直接
            # 调用两次（保持每步双算与半力合计的基线波形结构）。若本臂仍
            # 出注入事件，则物理步事件内已无任何我方读写，源头只能位于
            # PhysX内部；若清零，则我方步内操作即触发器。
            globals()["_MAINLOOP_AERO"] = True
            print("[CB-MODE] 气动回调不订阅步事件，改由主循环安全窗口直接调用"
                  "（判别臂C）", flush=True)
        elif os.environ.get("FLAPPER_POST_STEP_CB") == "1":
            def _post_step_cb(step_size: float, _fn=aero_callback):
                globals()["_POST_CB_N"] = globals().get("_POST_CB_N", 0) + 1
                _ts = globals().get("_WSTEP_T0")
                if _ts is not None:
                    globals().setdefault("_POST_CB_TS", []).append(
                        _time.perf_counter() - _ts)
                    if len(globals()["_POST_CB_TS"]) > 8:
                        globals()["_POST_CB_TS"] = globals()["_POST_CB_TS"][-8:]
                # 每步两拍都执行（保持模型状态推进与力波形结构不变）；第二拍
                # 处于fetch结果写出窗口：禁止其直接读缓冲与直接施力（撕裂读
                # 与并发写的危险窗口），其半力暂存由下一主步第一拍在求解前
                # 的安全窗口合并施加，每步求解器收到的合计与基线一致。
                _ms = getattr(_fn, "main_step", None)
                _second = (_ms is not None
                           and globals().get("_DEDUP_MS") == _ms)
                if _ms is not None:
                    globals()["_DEDUP_MS"] = _ms
                _fn._suppress_read = bool(_second)
                _fn._suppress_apply = bool(_second)
                try:
                    _fn(step_size)
                finally:
                    _fn._suppress_read = False
                    _fn._suppress_apply = False
            try:
                _phx_if = world.get_physics_context()._physx_interface
                globals()["_POST_STEP_SUB"] = _phx_if.\
                    subscribe_physics_on_step_events(_post_step_cb, False, 0)
                print("[CB-MODE] 气动回调改用有序后置步事件"
                      "（每步恰一次，fetch_results之后）", flush=True)
            except Exception as _pe:
                if _motion_recipe is not None:
                    raise RuntimeError("new action route requires the requested callback subscription") from _pe
                print(f"[CB-MODE] 有序后置订阅失败，回退legacy双派发：{_pe!r}",
                      flush=True)
                world.add_physics_callback("flapper_aero", aero_callback)
                logger.info("气动力学回调已在 world.reset() 后重新注册。")
        else:
            world.add_physics_callback("flapper_aero", aero_callback)
            logger.info("气动力学回调已在 world.reset() 后重新注册。")
        # 第六班诊断输出：回调字典现状（条目数等于活跃订阅数）
        try:
            _subs = getattr(world, "_physics_callback_functions", {})
            print(f"[SUB-DIAG] 订阅条目: {list(_subs.keys())}，"
                  f"aero 对象 id={id(aero_callback)}，"
                  f"回调实例计数={getattr(aero_callback, '_fire_count', 0)}",
                  flush=True)
        except Exception as _e:
            print(f"[SUB-DIAG] 失败: {_e!r}", flush=True)
    elif os.environ.get("FLAPPER_NO_AERO"):
        logger.warning("FLAPPER_NO_AERO=1：气动回调已禁用（判决性实验开关）")

    # ── 关键：Timeline 无限时长看护逻辑 ──
    # Stage 默认 endTimeCode 很小（如 240 帧=8 秒），到期后 is_playing() 变 False，
    # 物理完全停止且无错误日志 → 表现为看似卡死。因此强制设为较大值。
    try:
        import omni.timeline
        tl = omni.timeline.get_timeline_interface()
        tl.set_end_time(100000.0)
        tl.set_looping(False)
        print(f"[TIMELINE FIX] endTime=100000s, looping=False", flush=True)

        import omni.usd
        _stage = omni.usd.get_context().get_stage()
        if _stage.GetEndTimeCode() < 1e6:
            _stage.SetEndTimeCode(1e8)
            print(f"[TIMELINE FIX] Stage.endTimeCode → 1e8", flush=True)
    except Exception as e:
        print(f"[TIMELINE FIX] 设置失败: {e}", flush=True)

    # ── 第七班根因修复：停止 Timeline 自有步进 ──
    # 判决证据：气动回调在每个 world.step() 内被触发 1.75 次（Kit 应用自身的
    # 时间轴步进与本循环的 world.step() 形成双驱动），外力累积器按步清算 →
    # 施加力放大 1.75 倍（+0.9 N 未登记力、表观质量 0.123 kg、实测 1.75× 时基
    # 异常与该根因为同一成因）。停止时间轴自有步进后只保留 world.step() 单驱动。
    try:
        import omni.timeline as _tl2
        _tli = _tl2.get_timeline_interface()
        if _tli.is_playing():
            _tli.stop()
            print("[TIMELINE FIX] timeline 已 stop（只保留 world.step() 单驱动）", flush=True)
    except Exception as e:
        print(f"[TIMELINE FIX] stop 失败: {e}", flush=True)

    # ── 诊断：打印 World 实际 dt ──
    _actual_phys_dt = world.get_physics_dt()
    print(f"[DIAG] World.physics_dt = {_actual_phys_dt}", flush=True)

    # ----------------------------------------------------------
    # 5.9a 拍动运动学（Articulation 已在 5.5b 创建并随 reset 初始化）
    # ----------------------------------------------------------
    _wk = None
    if art is not None:
        try:
            from modules.aero_solver import WingKinematics
            from config.sim_config import WING_DRIVE_CONFIG
            _wk = WingKinematics(
                frequency=WING_DRIVE_CONFIG["frequency"],
                amplitude_deg=WING_DRIVE_CONFIG["amplitude_deg"],
                pitch_mode=WING_DRIVE_CONFIG["pitch_mode"],
                alpha_set_deg=WING_DRIVE_CONFIG["alpha_set_deg"],
            )
        except Exception as e:
            logger.warning("WingKinematics 创建失败: %s", e)
            _wk = None

    # ----------------------------------------------------------
    # 5.9 激活传感器（必须在 world.reset 之后）
    # ----------------------------------------------------------
    sensor_suite = None
    if ENABLE_SENSORS:
        logger.info("激活 LiDAR + Camera 传感器 ...")
        try:
            sensor_suite = flapper_loader.setup_sensors(world, record_data=RECORD_SENSOR_DATA)
            logger.info("传感器已激活: RTX LiDAR + RGB-D Camera")
        except Exception as e:
            import traceback
            logger.warning("传感器激活失败 (sensors disabled):\n%s", traceback.format_exc())

    # ----------------------------------------------------------
    # 5.10 预热：执行 5 帧使传感器 prim 生效
    # ----------------------------------------------------------
    _crash_log = os.path.join(LOG_DIR, "crash_log.txt")
    try:
        with open(_crash_log, "w", encoding="utf-8") as _cf:
            _cf.write("=== Step A: world.step warmup ===\n")
        for _ in range(5):
            world.step(render=True)
            _motion_warmup.append((len(_motion_warmup)+1, "legacy_initialization"))
        with open(_crash_log, "a", encoding="utf-8") as _cf:
            _cf.write("Step A PASSED: 5 frames completed\n")
    except Exception as e:
        import traceback
        with open(_crash_log, "a", encoding="utf-8") as _cf:
            _cf.write(f"Step A FAILED: {e}\n{traceback.format_exc()}\n")
        logger.warning("world.step warmup 失败: %s", e)
        if _motion_recipe is not None:
            raise RuntimeError("new action initialization aborted during warmup") from e

    if _motion_recipe is not None:
        if art is None or aero_callback is None:
            raise RuntimeError("new action route requires articulation and aerodynamic callback")
        from modules.motion_action.bridge import create_execution
        from modules.motion_action.joint_adapter import JointActionAdapter
        from modules.motion_action.aero_adapter import validate_external_model
        import config.sim_config as _motion_cfg
        validate_external_model(aero_callback.model)
        _motion_runtime = create_execution(_motion_recipe, _motion_cfg, PHYSICS_DT)
        for _index, _route in _motion_warmup:
            _motion_runtime.warmup_step(_index, route=_route)
        _motion_runtime.attach_journal(os.path.join(LOG_DIR,
            "motion_action_%s.jsonl" % _motion_runtime.run_id),
            detailed=_motion_cfg.ActionExpressionConfig.RECORD_ACTIONS)
        _motion_adapter = JointActionAdapter(art, _motion_recipe, aero_callback.tail_body)
        globals()["_MOTION_RUNTIME"] = _motion_runtime
        globals()["_MOTION_ADAPTER"] = _motion_adapter

    # Async Writer
    if sensor_suite is not None and RECORD_SENSOR_DATA:
        try:
            sensor_suite.setup_async_writer(output_dir=DATA_DIR)
            logger.info("Async Writer 已启动 → %s", DATA_DIR)
        except Exception as e:
            logger.warning("Async Writer 启动失败: %s", e)

    # ----------------------------------------------------------
    # 5.11 创建摄像头视口窗口（与 USV 版同一方法）
    # ----------------------------------------------------------
    _create_camera_viewports()

    # ----------------------------------------------------------
    # 5.11b 门洞感知（可选，config.ENABLE_GATE_PERCEPTION，默认关闭）
    #      生成圆门 + 相机更换 D435 标定 + 光照预设 + 随行采集 + UDP 检测结果外发
    # ----------------------------------------------------------
    gate_coll = None
    gate_service = None
    try:
        from config.sim_config import ENABLE_GATE_PERCEPTION
        if ENABLE_GATE_PERCEPTION and sensor_suite is not None:
            gate_coll, gate_service = _setup_gate_perception(world, sensor_suite)
    except Exception as e:
        logger.warning("门洞感知挂载失败（不影响主仿真）: %s", e)

    # ----------------------------------------------------------
    # 5.11c 循迹可视化细线（config.ENABLE_TRAIL_VIZ，BasisCurves 折线，
    #      零控制管线侵入：pos_cmd 在接收处旁路复制一份，实际尾迹按步采样）
    # ----------------------------------------------------------
    globals()["_TRAIL_VIZ"] = None
    globals()["_TRAIL_DEMO_ON"] = False
    try:
        from config.sim_config import ENABLE_TRAIL_VIZ
        if ENABLE_TRAIL_VIZ:
            import omni.usd
            from modules.trail_viz import TrailViz
            globals()["_TRAIL_VIZ"] = TrailViz(omni.usd.get_context().get_stage())
            # 无外部桥接时先绘制演示巡航圆（真实的 pos_cmd/path 到达后自动替换）
            from config.sim_config import TRAIL_VIZ_DEMO
            if TRAIL_VIZ_DEMO:
                from modules.trail_viz import demo_cruise_path
                globals()["_TRAIL_VIZ"].set_path(demo_cruise_path())
                globals()["_TRAIL_VIZ"].flush()
                globals()["_TRAIL_DEMO_ON"] = True
            print("[TRAIL] 轨迹可视化已开启: 青色为期望轨迹 橙色为实际尾迹 (/World/Viz)"
                  + ("（演示圆 r=5m 已画，外部轨迹到达后自动替换）"
                     if globals()["_TRAIL_DEMO_ON"] else ""),
                  flush=True)
    except Exception as e:
        logger.warning("轨迹可视化开启失败（不影响主仿真）: %s", e)

    # ----------------------------------------------------------
    # 5.11d Isaac 内部相机内录（config.CAMERA_REC_ENABLED）
    # ----------------------------------------------------------
    globals()["_CAM_REC"] = None
    try:
        from config.sim_config import (CAMERA_REC_ENABLED, CAMERA_REC_FPS,
                                       CAMERA_REC_RES)
        if CAMERA_REC_ENABLED:
            import time as _t3
            _out = os.path.join(LOG_DIR,
                                f"camera_rec_{_t3.strftime('%m%d_%H%M%S')}.mp4")
            globals()["_CAM_REC"] = _CamRecorder(world, _out,
                                                 CAMERA_REC_RES, CAMERA_REC_FPS)
            print(f"[CAM-REC] 内录开始 → {_out}", flush=True)
    except Exception as e:
        logger.warning("内录开启失败（不影响主仿真）: %s", e)

    # ----------------------------------------------------------
    # 6. 主循环
    # ----------------------------------------------------------
    step_count = 0
    _FIRE_GUARD = [0, None]   # [累计触发数, 上步值] 断言看护状态
    _render_interval = max(1, int(round(RENDER_DT / PHYSICS_DT)))  # ~4
    _lidar_diag = lidar_diag.LidarDiag() if hasattr(lidar_diag, 'LidarDiag') else None
    _fps_timer_start = _time.perf_counter()
    _fps_step_start = 0

    # ── FW-IID 冲量台账（2026-09-29 方案阶段零）──
    # FLAPPER_LEDGER=1 时每物理步把全通道位型（位姿/速度/关节位置速度
    # 力矩/关节目标/气动施加力）以双精度原值二进制落盘 logs/fw_ledger_*.bin，
    # 供逐位比较器定位首差步与首差通道；关闭时零触碰。
    # FLAPPER_LEDGER_ULP="步:关节名" 为仪器校验注入口（在 _drive_wing_joints
    # 内对该关节目标的 32 位位型翻转最低有效位一次，仅一次）。
    if os.environ.get("FLAPPER_LEDGER") == "1":
        try:
            from modules.fw_iid_ledger import LedgerRecorder
            _jnames = list(art.joint_names or []) if art is not None else []
            _fw_tag = os.environ.get("FLAPPER_TAG", "run").strip() or "run"
            _fw_safe = "".join(c if c.isalnum() or c in "-_"
                               else "_" for c in _fw_tag)[:48]
            _fw_rec = LedgerRecorder(
                os.path.join(LOG_DIR, "fw_ledger_%s_%s.bin"
                             % (_fw_safe, _time.strftime("%m%d_%H%M%S"))),
                _jnames, PHYSICS_DT,
                meta={"tag": os.environ.get("FLAPPER_TAG", ""),
                      "desc": os.environ.get("FLAPPER_DESC", ""),
                      "env": {k: os.environ.get(k, "")
                              for k in sorted(os.environ)
                              if k.startswith("FLAPPER_")}})
            globals()["_FW_IID_REC"] = _fw_rec
            _ulp_s = os.environ.get("FLAPPER_LEDGER_ULP", "")
            if ":" in _ulp_s:
                _us, _jn = _ulp_s.split(":", 1)
                globals()["_FW_IID_ULP"] = (int(_us), _jn.strip())
                print(f"[FW-LEDGER] 末位注入已装订：步 {_us} 关节 {_jn.strip()}",
                      flush=True)
            print(f"[FW-LEDGER] 冲量台账 → {_fw_rec.path}", flush=True)
        except Exception as _fw_e:
            globals()["_FW_IID_REC"] = None
            print(f"[FW-LEDGER] 初始化失败（不阻断仿真）: {_fw_e!r}", flush=True)

    logger.info("=== 仿真主循环开始 (render_interval=%d) ===", _render_interval)
    _man_ch = None   # 每步记录路径在首帧赋值前就会用到（第10轮 DASH-ERR 实录）

    # I-129 分岔探针输出流（默认关，见控制器调用点旁的探针块）
    _I129_PROBE_F = None
    if os.environ.get("FLAPPER_I129_PROBE") == "1":
        import config.sim_config as _cfg_probe
        _probe_tag = os.environ.get("FLAPPER_TAG", "probe")
        _I129_PROBE_F = open(
            os.path.join(_cfg_probe.LOG_DIR,
                         "i129_probe_%s.txt" % _probe_tag),
            "w", encoding="utf-8")
        print("[I129-PROBE] 探针输出 i129_probe_%s.txt" % _probe_tag,
              flush=True)

    try:
        while simulation_app.is_running():
            _need_render = (step_count % _render_interval == 0)

            # ── 永久 play() 看护逻辑：Timeline 偶尔变为 False 时自动恢复 ──
            if not world.is_playing():
                world.play()
                if step_count < 500 or _det_on():
                    print(f"[AUTO-FIX] step={step_count} t={world.current_time:.3f} playing恢复", flush=True)

            if step_count < 10:
                print(f"[LOOP DIAG] step={step_count} is_playing={world.is_playing()} "
                      f"t={world.current_time:.4f} render={_need_render}", flush=True)

            # ── 物理步进（第五班·物理真值裁定）──
            # 源码级定位（isaac_sim_5.1.0/exts/isaacsim.core.api/.../
            # simulation_context.py:694-712）：step(render=True) 走
            # app.update()——步进权交给 Kit 时间轴（set_auto_update(True)），
            # 每次 update 实际运行的物理步数由渲染节奏决定（GUI 实测均值 1.75
            # →力被同倍放大，与第12轮时间膨胀为同一成因）；step(render=False) 走
            # physics_context._step()——simulate+fetch 恰好一步，确定性。
            # 结论：主循环恒走纯物理路径，渲染单独走 world.render()（内部
            # 先置 playSimulations=False 再 update，渲染期间物理冻结）。
            # 物理真值时间=step_count×PHYSICS_DT 恒等于 PhysX 实际步数。
            if _det_on():
                globals()["_WSTEP_T0"] = _time.perf_counter()
            if _motion_runtime is not None:
                from modules.motion_action.bridge import publish_native
                _motion_frame, _motion_write = publish_native(
                    _motion_runtime, _motion_adapter, step_count+1, namespace=globals(),
                    force_model=aero_callback.model)
                aero_callback.motion_execution = _motion_runtime
            world.step(render=False)
            if _motion_runtime is not None and _motion_runtime.failed is not None:
                raise RuntimeError(_motion_runtime.failed)
            if _det_on():
                globals()["_WSTEP_T1"] = _time.perf_counter()
            if globals().get("_MAINLOOP_AERO") and aero_callback is not None:
                aero_callback(PHYSICS_DT)
                aero_callback(PHYSICS_DT)
            if _need_render:
                try:
                    world.render()
                except Exception as _re:
                    if step_count < 30:
                        print(f"[RENDER-ERR] step={step_count} {_re!r}",
                              flush=True)
            # 断言看护：气动回调本步触发次数必须恰为 1（该性质由纯物理路径的
            # 构造保证；若 Isaac 行为变化，该看护立即暴露变化而不是静默放大）
            _fires_now = getattr(aero_callback, "_fire_count", 0)                 if aero_callback is not None else _FIRE_GUARD[0]
            _FIRE_GUARD[0] = _fires_now
            if step_count >= 2 and _FIRE_GUARD[1] is not None:
                _delta = _fires_now - _FIRE_GUARD[1]
                if _delta != 1 and step_count < 14:
                    print(f"[FIRE-GUARD] step={step_count} 触发{_delta}次 "
                          f"render={_need_render} playing={world.is_playing()} "
                          f"t={world.current_time:.4f} "
                          f"dt序列={getattr(aero_callback, '_fire_dts', ())}",
                          flush=True)
                elif _delta != 1 and step_count % 240 == 0:
                    print(f"[FIRE-GUARD] step={step_count} 触发{_delta}次"
                          f"（持续）", flush=True)
            if _det_on() and _FIRE_GUARD[1] is not None:
                _delta_probe = _fires_now - _FIRE_GUARD[1]
                if _delta_probe not in (1, 2):
                    print(f"[FIRE-ANOM] step={step_count} 触发{_delta_probe}次 "
                          f"render={_need_render} playing={world.is_playing()} "
                          f"t={world.current_time:.4f}", flush=True)
                if step_count < 10:
                    _ts = globals().get("_POST_CB_TS", [])
                    _tend = (globals().get("_WSTEP_T1", 0.0)
                             - globals().get("_WSTEP_T0", 0.0))
                    print(f"[CB-TRACE] step={step_count} 气动总触发增量={_delta_probe} "
                          f"后置包装器累计={globals().get('_POST_CB_N', 0)} "
                          f"步耗时={_tend*1000:.2f}ms "
                          f"包装器时刻ms={[round(t*1000, 2) for t in _ts]}",
                          flush=True)
                    globals()["_POST_CB_TS"] = []
            _FIRE_GUARD[1] = _fires_now
            step_count += 1
            if aero_callback is not None:
                aero_callback.main_step = step_count
            # FW-IID 台账采样点（方案 5.1 口径）：本物理步求解后状态 +
            # 本步求解所用关节目标（上帧 drive 写入的缓存）+ 本步气动
            # 施加值。求解前动量由上一步记录滞后可得，无需双读。采样
            # 异常只丢行不打断仿真。
            _fwr = globals().get("_FW_IID_REC")
            if _fwr is not None:
                try:
                    _ld = flapper_loader.get_loader()
                    _ps = _ld.get_pose() if _ld is not None else None
                    _vv = _ld.get_velocity() if _ld is not None else None
                    _jp = _jv = _je = None
                    if art is not None:
                        try:
                            _jp = np.asarray(art.get_joint_positions(),
                                             dtype=float).reshape(-1)
                        except Exception:
                            pass
                        try:
                            _jv = np.asarray(art.get_joint_velocities(),
                                             dtype=float).reshape(-1)
                        except Exception:
                            pass
                        try:
                            _je = np.asarray(art.get_measured_joint_efforts(),
                                             dtype=float).reshape(-1)
                        except Exception:
                            pass
                    _fa = getattr(aero_callback, "last_F_world", None) \
                        if aero_callback is not None else None
                    _ta = getattr(aero_callback, "last_T_world", None) \
                        if aero_callback is not None else None
                    _fwr.record(step_count, {
                        "bp": _ps, "bv": _vv, "jp": _jp, "jv": _jv,
                        "je": _je,
                        "tgt": globals().get("_WING_TGT_CACHE"),
                        "Fa": _fa, "Ta": _ta})
                except Exception as _fe:
                    if step_count < 20 or step_count % 1200 == 0:
                        print(f"[FW-LEDGER] 采样异常 step={step_count}:"
                              f" {_fe!r}", flush=True)
            # 夜间开发（2026-09-20）：环境变量限制运行时长（在环验收 60 秒使用）
            _cap_s = float(os.environ.get("FLAPPER_MAX_S", "0") or 0)
            if _cap_s and step_count * PHYSICS_DT >= _cap_s:
                print(f"[RUNCAP] 达到 FLAPPER_MAX_S={_cap_s}s，正常收尾退出",
                      flush=True)
                break
            # 权威物理时钟（第12轮结论）：GUI 模式下 world.current_time 被
            # timeline/vsync 持续推高，实测每迭代膨胀 ~1.75 步（headless 模式
            # 不膨胀，探针 tools/_scale_probe*.py 三次独立验证）。物理每迭代
            # 恰好执行 1 步，因此唯一可信时间 = step_count × PHYSICS_DT。
            # 控制/波形/记录一律使用 _SIM_T，world.current_time 仅保留用于
            # 诊断打印对账。
            _SIM_T = step_count * PHYSICS_DT

            # ── 帧率统计（每 120 步 ≈ 1 秒）──
            if step_count % 120 == 0:
                _now = _time.perf_counter()
                _elapsed = _now - _fps_timer_start
                if _elapsed > 0:
                    _fps = (step_count - _fps_step_start) / _elapsed
                    print(f"\n★★★ [FPS] {_fps:.1f} fps | playing={world.is_playing()} "
                          f"t={world.current_time:.3f} ★★★\n", flush=True)
                _fps_timer_start = _now
                _fps_step_start = step_count

            # ── LiDAR / 位姿诊断 ──
            if _lidar_diag is not None:
                _lidar_diag.check(world, step_count)

            # ── 遥测发送（每 N 步）──
            if step_count % 4 == 0:
                _send_telemetry(flapper_loader.get_loader(), telemetry_sender,
                                _SIM_T)

            # ── 系留绳力 + 定高 DP（每步；DP 结果经 _drive_wing_joints 生效）──
            # 试验模式下必须每物理步锁定位姿：隔步锁定会使气动力在两步间积分出
            # ~2m/s 的锯齿速度，v_air 因此失真，升力测量虚高一个量级（2026-09-18）
            if EXPERIMENT_MODE or step_count % 2 == 0:
                _apply_tether_and_dp(mav_view, flapper_loader.get_loader())

            # ── 起飞先锁定状态后释放（2026-09-19）：拍幅软启动窗口内锁定机体，
            #    等翅膀斜坡完成、飞控收敛到配平拍频后再释放，跳过软启动
            #    半升力期的掉高（0.5s 掉 ~0.4m，初始 1.5m 余量无法承受），
            #    以及高度环全力推 → 弹射的整个过渡段。必须每步锁定（隔步锁定会
            #    积分出 ~2m/s 锯齿速度，见上方 2026-09-18 教训）──
            if not EXPERIMENT_MODE:
                import config.sim_config as _cs
                if _SIM_T < _cs.STARTUP_PIN_S:
                    # 2026-09-19 位置锁定试验已回退（该试验与拍周期采样同时修改
                    # 变量导致无法归因，两项试验均失败）：恢复全六自由度 _bench_pin
                    _i186_pin_result = {}
                    try:
                        _bench_pin(mav_view)
                        _i186_pin_result["body_pin"] = {"status": "succeeded"}
                    except Exception as _i186_pin_exc:
                        _i186_pin_result["body_pin"] = {
                            "status": "failed", "query_error": repr(_i186_pin_exc)}
                    # 钉住期翅膀运动学钉住（2026-09-29 机主令）：翅膀按解析
                    # 规定值动作，关节约束冲量历史两次运行逐位一致，求解器
                    # 热启动缓存无差异可积累；释放拍由快照释放再写一次
                    try:
                        _pin_wing_joints(art, _wk, aero_callback, float(_SIM_T))
                        _i186_pin_result["wing_pin"] = {"status": "succeeded"}
                    except Exception as _pe:
                        _i186_pin_result["wing_pin"] = {
                            "status": "failed", "query_error": repr(_pe)}
                        if step_count < 30:
                            print(f"[PIN-WING] 钉翅失败: {_pe!r}", flush=True)
                    globals()["_LAUNCH_ARMED"] = True
                    # ── I-186 观察采集（只读挂点，不改任何行为）：钉住
                    #    首步记录实际对象读数（位姿/速度/控制器活对象/
                    #    来流现值/自由度锁定）；缺归档身份（非管线运行）
                    #    则不采集；读失败记 unknown 不中断仿真 ──
                    if (not globals().get("_I186_OBS_INIT_DONE")
                            or any(v.get("status") == "failed"
                                   for v in _i186_pin_result.values())):
                        try:
                            from modules.experiment_pipeline import (
                                runtime_observer as _i186_ro)
                            _i186_observed = _i186_ro.observe_initialization(
                                mav_view, autopilot, _cs, float(_SIM_T),
                                pin_result=_i186_pin_result,
                                obstacle_manager=obstacle_manager.get_manager(),
                                runtime_context=globals())
                            globals()["_I186_OBS_INIT_DONE"] = bool(_i186_observed)
                        except Exception:
                            pass
                elif globals().get("_LAUNCH_ARMED"):
                    # Observation receipts follow existing action success/failure;
                    # they never retry an action or change the launch sequence.
                    _i186_release_results = {
                        name: {"status": "not_executed"} for name in (
                            "launch_velocity", "aero_reset", "controller_reset",
                            "wing_snapshot", "filter_phase_reset", "pitch_anchor",
                            "launch_attitude", "perturb_qw", "perturb_roll")}
                    # 2026-09-20 夜间调试第3轮：发射注入——状态锁定释放瞬间给予
                    # 巡航初速（手上发射等价；0.6m 无悬停点、平飞下限
                    # ~7.5m/s@0.7kg，发射速度取巡航配平速度，现行 LAUNCH_VX=4.5）
                    try:
                        mav_view.set_velocities(np.asarray(
                            [[float(getattr(_cs, "LAUNCH_VX", 8.0)),
                              0.0, 0.0, 0.0, 0.0, 0.0]], dtype=np.float32))
                        _i186_release_results["launch_velocity"] = {"status": "succeeded"}
                        print("[LAUNCH] 发射：注入巡航初速 Vx=%.1f m/s"
                              % float(getattr(_cs, "LAUNCH_VX", 8.0)),
                              flush=True)
                        # 2026-09-20 第二班：释放瞬间强制重采样相位保持值——
                        # 旧保持值来自状态锁定期（v≈0），模型按 V=0 求值半秒在新
                        # 被控对象上产生负升力，释放后先下坠 −3.7m/s 再过冲
                        # （164352 实录）。注入即重采样，消除跨事件滞后。
                        if aero_callback is not None:
                            aero_callback._v_air_hold = None
                            aero_callback.held_body_vel = None
                            aero_callback._fa_buf = []
                            # 第五班：控制器速度滤波器同样跨事件清零——
                            # τ=0.1 状态带着状态锁定期 ≈0 值跨越释放，0.3s 陈旧
                            # 使 e_kin 虚高 +7、释放即热启动（181115 实录
                            # f=1.74@t=1.0）
                            aero_callback._ctrl_vel_filt = None
                            _i186_release_results["aero_reset"] = {"status": "succeeded"}
                        else:
                            _i186_release_results["aero_reset"] = {
                                "status": "not_applicable", "scope": "release",
                                "reason": "aero_callback is None; reset branch not entered"}
                        # 释放热启动修正（172157 实验链结论）：状态锁定期 held
                        # vx=0 对 vxd=4 使 e_E≈+8 J/kg 预充频率与积分项，
                        # 释放即 1.8-2.2Hz 热启动→Fz 2.5×W 竖直超升→翻滚。释放
                        # 瞬间清除积分与频率状态，从配平冷启动。
                        if autopilot is not None:
                            try:
                                autopilot._ei = 0.0
                                autopilot._freq_last = float(getattr(
                                    _cs.FlightControllerConfig,
                                    "FREQ_HOVER", 1.55))
                                autopilot._pitch_tgt_filt = None  # 第十班释放清零
                                autopilot._pitch_i = 0.0        # [50班补丁①] 积分冷启动
                                globals()["_PITCH_I_EXT"] = 0.0  # [50班补丁①] 外部积分器清零
                                _i186_release_results["controller_reset"] = {"status": "succeeded"}
                            except Exception as _i186_reset_exc:
                                _i186_release_results["controller_reset"] = {
                                    "status": "failed", "query_error": repr(_i186_reset_exc)}
                        else:
                            _i186_release_results["controller_reset"] = {
                                "status": "not_applicable", "scope": "release",
                                "reason": "autopilot is None; reset branch not entered"}
                    except Exception as _i186_launch_exc:
                        _i186_release_results["launch_sequence_error"] = {
                            "status": "failed", "query_error": repr(_i186_launch_exc)}
                    # ── 快照释放（2026-09-29 机主令：永久消除同实验不一致）──
                    # 释放拍调用 _pin_wing_joints：九个关节的位置与速度、驱动侧
                    # 与力模型的拍动相位，全部写成释放时刻的解析规定值；释放后
                    # 的演化与钉住期间的历史无关，两次运行从同一规定状态出发。
                    try:
                        _pin_wing_joints(art, _wk, aero_callback, float(_SIM_T))
                        _i186_release_results["wing_snapshot"] = {"status": "succeeded"}
                        print("[LAUNCH] 快照释放：关节与相位已写规定值",
                              flush=True)
                    except Exception as _se:
                        _i186_release_results["wing_snapshot"] = {
                            "status": "failed", "query_error": repr(_se)}
                        print(f"[LAUNCH] 快照释放失败: {_se!r}", flush=True)
                    # 滤波与相位保持状态一并复位：两次运行从同一冷起点出发
                    if _motion_runtime is not None:
                        if _motion_runtime.failed is not None:
                            raise RuntimeError(_motion_runtime.failed)
                        if getattr(aero_callback,"_motion_pending_sources",None):
                            _motion_runtime.record_event(dict(kind="force_discarded_on_release",
                                source_frames=aero_callback._motion_pending_sources))
                        aero_callback._safe_pending=None
                        aero_callback._motion_pending_sources=[]
                        _motion_runtime.release()
                    try:
                        if aero_callback is not None:
                            aero_callback._pl_phase = 0.0
                        if autopilot is not None:
                            autopilot._pv_filt = None
                        _i186_release_results["filter_phase_reset"] = {"status": "succeeded"}
                    except Exception as _i186_filter_exc:
                        _i186_release_results["filter_phase_reset"] = {
                            "status": "failed", "query_error": repr(_i186_filter_exc)}
                    # 2026-09-20 增益调整配合：发射姿态同步到气动配平俯仰 −0.20 rad。
                    # 释放瞬间俯仰≈0 与 V=8 的升力配平点错配，v² 升力富余
                    # 4s 内把 vz 推到 +8.1（竖直超升），触发爬升守卫削减升力→非指令
                    # 深俯冲→滚转权限耗尽坠地（0920_122055 实录）。
                    try:
                        _lp = float(getattr(_cs, "LAUNCH_PITCH", 0.0))
                        if autopilot is not None:
                            try:
                                autopilot._pd_last = _lp  # [50班补丁②] 指令锚同步配平
                                _i186_release_results["pitch_anchor"] = {"status": "succeeded"}
                            except Exception as _i186_pitch_exc:
                                _i186_release_results["pitch_anchor"] = {
                                    "status": "failed", "query_error": repr(_i186_pitch_exc)}
                        else:
                            _i186_release_results["pitch_anchor"] = {
                                "status": "not_applicable", "scope": "release",
                                "reason": "autopilot is None; anchor branch not entered"}
                        if abs(_lp) > 1e-6:
                            _pose = flapper_loader.get_loader().get_pose()
                            if _pose is not None:
                                mav_view.set_world_poses(
                                    positions=np.asarray(
                                        [[_pose[0], _pose[1], _pose[2]]],
                                        dtype=np.float32),
                                    orientations=np.asarray(
                                        [_euler_to_quat(0.0, _lp, 0.0)],
                                        dtype=np.float32))
                                _i186_release_results["launch_attitude"] = {"status": "succeeded"}
                                print("[LAUNCH] 姿态同步：俯仰 %.2f rad（气动配平）"
                                      % _lp, flush=True)
                            else:
                                _i186_release_results["launch_attitude"] = {
                                    "status": "unknown", "query_error": "loader pose is None"}
                        else:
                            _i186_release_results["launch_attitude"] = {
                                "status": "not_applicable", "scope": "release",
                                "reason": "actual launch pitch is zero; no attitude write requested"}
                    except Exception as _i186_attitude_exc:
                        _i186_release_results["launch_attitude"] = {
                            "status": "failed", "query_error": repr(_i186_attitude_exc)}
                    # 李雅普诺夫伴随扰动注入（2026-09-28 用户令）：释放
                    # 序列末位执行（置于俯仰同步之后——首版置于其前被
                    # 同步块的干净四元数覆盖，巡航对逐位一致的教训在案），
                    # 给姿态 w 加 FLAPPER_PERTURB_QW 后归一化写入。
                    # PhysX 位姿 float32，扰动小于约 1e-7 被舍入吞掉。
                    try:
                        _eps = float(os.environ.get("FLAPPER_PERTURB_QW",
                                                    "0") or 0)
                        if abs(_eps) > 0:
                            _pq = np.asarray(
                                _euler_to_quat(
                                    0.0, float(getattr(_cs, "LAUNCH_PITCH",
                                                       0.0)), 0.0),
                                dtype=np.float64)
                            _pq[0] += _eps
                            _pq /= np.linalg.norm(_pq)
                            _pp = flapper_loader.get_loader().get_pose()
                            if _pp is not None:
                                mav_view.set_world_poses(
                                    positions=np.asarray(
                                        [[_pp[0], _pp[1], _pp[2]]],
                                        dtype=np.float32),
                                    orientations=np.asarray(
                                        [_pq], dtype=np.float32))
                                _i186_release_results["perturb_qw"] = {
                                    "status": "succeeded", "requested": float(_eps)}
                                print(f"[PERTURB] 释放姿态 qw += {_eps:.3e}"
                                      "（末位注入）", flush=True)
                            else:
                                _i186_release_results["perturb_qw"] = {
                                    "status": "unknown", "query_error": "loader pose is None",
                                    "requested": float(_eps)}
                        else:
                            _i186_release_results["perturb_qw"] = {
                                "status": "not_applicable", "scope": "release",
                                "reason": "actual perturbation request is zero", "requested": float(_eps)}
                    except Exception as _i186_qw_exc:
                        _i186_release_results["perturb_qw"] = {
                            "status": "failed", "query_error": repr(_i186_qw_exc)}
                    # FW-IID 阶段三滚转角注入口（2026-09-29 方案）：
                    # FLAPPER_PERTURB_ROLL=弧度 在释放序列末位对当前姿态
                    # 施加绕机体 x 轴的小旋转（体系四元数右乘），与 QW 注
                    # 入口同位（俯仰同步之后、释放瞬态之前），供 λ 标定
                    # 三量级（1e-2/1e-3/1e-4 rad）。默认零行为。
                    try:
                        _dr = float(os.environ.get("FLAPPER_PERTURB_ROLL",
                                                   "0") or 0)
                        if abs(_dr) > 0:
                            _pr = flapper_loader.get_loader().get_pose()
                            if _pr is not None:
                                _q = np.asarray(_pr[3:7], dtype=np.float64)
                                _c, _s = (math.cos(_dr / 2.0),
                                          math.sin(_dr / 2.0))
                                _qr = np.asarray([_c, _s, 0.0, 0.0])
                                _qn = np.asarray([
                                    _q[0]*_qr[0] - _q[1]*_qr[1]
                                    - _q[2]*_qr[2] - _q[3]*_qr[3],
                                    _q[0]*_qr[1] + _q[1]*_qr[0]
                                    + _q[2]*_qr[3] - _q[3]*_qr[2],
                                    _q[0]*_qr[2] - _q[1]*_qr[3]
                                    + _q[2]*_qr[0] + _q[3]*_qr[1],
                                    _q[0]*_qr[3] + _q[1]*_qr[2]
                                    - _q[2]*_qr[1] + _q[3]*_qr[0]])
                                _qn /= np.linalg.norm(_qn)
                                mav_view.set_world_poses(
                                    positions=np.asarray(
                                        [[_pr[0], _pr[1], _pr[2]]],
                                        dtype=np.float32),
                                    orientations=np.asarray(
                                        [_qn], dtype=np.float32))
                                _i186_release_results["perturb_roll"] = {
                                    "status": "succeeded", "requested": float(_dr)}
                                print(f"[PERTURB] 释放姿态滚转 "
                                      f"+{_dr:.3e} rad（机体x轴）",
                                      flush=True)
                            else:
                                _i186_release_results["perturb_roll"] = {
                                    "status": "unknown", "query_error": "loader pose is None",
                                    "requested": float(_dr)}
                        else:
                            _i186_release_results["perturb_roll"] = {
                                "status": "not_applicable", "scope": "release",
                                "reason": "actual perturbation request is zero", "requested": float(_dr)}
                    except Exception as _i186_roll_exc:
                        _i186_release_results["perturb_roll"] = {
                            "status": "failed", "query_error": repr(_i186_roll_exc)}
                    # ── I-186 观察采集（只读挂点，不改任何行为）：释放
                    #    序列实际执行后记录实际对象读数与复位事件（只读
                    #    上方已执行动作的现值，不改动状态）──
                    try:
                        from modules.experiment_pipeline import (
                            runtime_observer as _i186_ro)
                        _i186_ro.observe_release(
                            mav_view, autopilot, _cs, float(_SIM_T),
                            _i186_release_results,
                            obstacle_manager=obstacle_manager.get_manager(),
                            runtime_context=globals())
                    except Exception:
                        pass
                    globals()["_LAUNCH_ARMED"] = False

            # ── 视口跟踪相机（随渲染节拍对准机器人 ≈30Hz 仿真时间）──
            # 只写两个 xform 属性+一次位姿读（几十 µs），不产生额外渲染
            # pass；4Hz 时每次跳过 ~30 物理步位移，视口滞后感明显且对
            # TAA 时序累积更不利。
            if VIEWPORT_FOLLOW and _need_render:
                _update_follow_camera(flapper_loader.get_loader())
                _rec = globals().get("_CAM_REC")
                if _rec is not None:
                    _p = flapper_loader.get_loader().get_pose()
                    if _p is not None:
                        _dx, _dy, _dz = VIEWPORT_OFFSET
                        _rec.frame((_p[0]+_dx, _p[1]+_dy, _p[2]+_dz),
                                   (_p[0], _p[1], _p[2]))

            # ── 循迹可视化：实际尾迹采样 + 两条线落盘刷新（每 30 物理步）──
            if _TRAIL_VIZ is not None and step_count % 30 == 0:
                try:
                    _tp = flapper_loader.get_loader().get_pose()
                    if _tp is not None:
                        _TRAIL_VIZ.push_actual(_tp[0:3])
                        _TRAIL_VIZ.flush()
                except Exception:
                    pass

            # ── 机体脱离世界范围时自动重生（穿地或速度失控飞离的保险机制）──
            if step_count % 30 == 0:
                _respawn_if_lost(mav_view, flapper_loader.get_loader(), step_count)

            # ── Web 看板快照 + 高密度记录（2026-09-20 第10轮：记录频率提高到
            #    每物理步，看板发送保持 30Hz 节流）──
            if dashboard is not None and step_count % 4 == 0:
                _update_dashboard(dashboard, world, flapper_loader.get_loader(),
                                  art, _wk, _man_ch, aero_callback, step_count,
                                  dash_only=True)
            else:
                try:
                    _update_dashboard(None, world, flapper_loader.get_loader(),
                                      art, _wk, _man_ch, aero_callback,
                                      step_count, dash_only=False)
                except Exception as _e:
                    if step_count < 30:
                        print(f"[DASH-ERR] step={step_count} {_e!r}", flush=True)

            # ── 手动输入（120Hz 轮询，摇杆/键盘）──
            _man_ch = manual.read() if manual is not None else None

            # ── 试验通道应用（2026-09-19）：台架俯仰偏置/来流风速/
            #    单轴释放/角速率脉冲，全部由试验排程逐段驱动 ──
            if EXPERIMENT_MODE and _man_ch is not None:
                _apply_experiment_channels(_man_ch, mav_view)

            # ── 飞行控制器（手动杆量非零时让位于手动输入）──
            # 2026-09-20 第二班（拍周期同步采样反馈·控制器半边）：控制器
            # 输入改用相位保持速度（与力模型相同口径）——原始速度含 ±3~5
            # m/s 拍频振荡，控制器跟踪该振荡会使拍频与俯仰指令抖动，波形失周期后
            # 力平均失效，纹波经控制器闭环相互泵入能量（163832 实录 Fwz±15N）。
            _ap_out = None
            if autopilot is not None and not _manual_active(_man_ch):
                _pose_ap = flapper_loader.get_loader().get_pose()
                _vel_ap = flapper_loader.get_loader().get_velocity()
                if _vel_ap is not None and aero_callback is not None:
                    _vel_held = getattr(aero_callback, "held_body_vel", None)
                    if _vel_held is not None:
                        _vel_ap = [float(_vel_held[0]), float(_vel_held[1]),
                                   float(_vel_held[2]),
                                   float(_vel_ap[3]), float(_vel_ap[4]),
                                   float(_vel_ap[5])]
                if _det_on() and _pose_ap is not None:
                    _dp_dash = globals().get("_DET_DASH_POSE")
                    if _dp_dash is not None:
                        _dz = abs(float(_pose_ap[2]) - float(_dp_dash[2]))
                        _dxy = ((float(_pose_ap[0]) - float(_dp_dash[0])) ** 2
                                + (float(_pose_ap[1]) - float(_dp_dash[1])) ** 2) ** 0.5
                        if _dz > 0.1 or _dxy > 0.1:
                            print(f"[READ-XCONSUMER] CTRL step={step_count} | "
                                  f"同迭代 dash与ctrl读数不符 dz={_dz:.4f} "
                                  f"dxy={_dxy:.4f} | "
                                  f"dash={[round(float(q), 4) for q in _dp_dash]} "
                                  f"ctrl={[round(float(q), 4) for q in _pose_ap]}",
                                  flush=True)
                    _det_read(f"CTRL step={step_count}", _pose_ap,
                              (_vel_ap if _vel_ap is not None else [0.0] * 6),
                              globals().get("_DET_CTRL_POSE"),
                              globals().get("_DET_CTRL_VEL"))
                    globals()["_DET_CTRL_POSE"] = [float(q) for q in _pose_ap]
                    globals()["_DET_CTRL_VEL"] = ([float(q) for q in _vel_ap]
                                                  if _vel_ap is not None else None)
                if _pose_ap is not None and _vel_ap is not None:
                    # 双源姿态对账探针（2026-09-29 转弯线 R10：控制器经
                    # rigid_prim 读 roll 恒 0 而气动/数据流经 RigidPrimView
                    # 读机体实际滚转 -7°，两源同路径不同 API，逐拍对账
                    # 原始四元数；CTRL_QDBG=1 时每 ~0.5s 打印一次）
                    if os.environ.get("FLAPPER_CTRL_QDBG") == "1" \
                            and step_count % 60 == 0:
                        _aq = getattr(aero_callback, "_last_quat", None)
                        if _aq is not None:
                            _p2 = flapper_loader.get_loader().get_pose()
                            print(f"[Q-DBG] t={_SIM_T:.2f} "
                                  f"ctrl_pose={np.round(_pose_ap[3:7], 4)} "
                                  f"aero_view={np.round(np.asarray(_aq), 4)} "
                                  f"re_read={np.round(np.asarray(_p2[3:7]), 4)}",
                                  flush=True)
                    # [50班补丁③] 外部积分器门 GATE 覆盖（config 无键；第十~十七跑先例）
                    try:
                        autopilot._pitch_i = float(globals().get("_PITCH_I_EXT", 0.0))
                    except Exception:
                        pass
                    if _motion_runtime is None:
                        _ap_out = autopilot.update(_pose_ap, _vel_ap,
                                                   t_sim=_SIM_T)
                    else:
                        _ap_out = autopilot.update(_pose_ap, _vel_ap, t_sim=_SIM_T,
                            motion_context=_motion_runtime.controller_context())
                    # I-129 分岔探针（默认关）：逐步全精度导出控制器输入
                    # 与内部状态，双跑逐位对比定位第一个分岔变量。只读不
                    # 改，零行为扰动。
                    if os.environ.get("FLAPPER_I129_PROBE") == "1":
                        _pr = [repr(_SIM_T), repr(list(_pose_ap)),
                               repr(list(_vel_ap))]
                        for _nm in ("_freq_last", "_pv_filt", "_w_filt",
                                    "_sd_t_last", "_sd_gate_L",
                                    "_sd_gate_R", "_dm_acc_L", "_dm_acc_R",
                                    "_wx_acc", "_wx_acc_n", "_beta_i",
                                    "_bank_last", "_pd_last",
                                    "_pitch_tgt_filt", "_wx_pa"):
                            _pr.append(repr(getattr(autopilot, _nm,
                                                    None)))
                        _pr.append(repr(_ap_out.get("freq")))
                        _I129_PROBE_F.write(
                            "\x1f".join(_pr) + "\n")
                        _I129_PROBE_F.flush()
                    try:
                        import config.sim_config as _cs_i
                        _q = _pose_ap[3:7]
                        _pitch_i_q = math.asin(max(-1.0, min(1.0,
                            2.0 * (_q[0] * _q[2] - _q[3] * _q[1]))))
                        _perr_i = _ap_out.get("pitch_des", 0.0) - _pitch_i_q
                        if _perr_i > math.pi:
                            _perr_i -= 2.0 * math.pi
                        elif _perr_i < -math.pi:
                            _perr_i += 2.0 * math.pi
                        _gz = float(_cs_i.FlightControllerConfig.GROUND_Z)
                        _grounded = (float(_pose_ap[2]) < _gz
                                     and abs(float(_vel_ap[2])) < 0.5
                                     and abs(float(_vel_ap[0])) < 2.0)
                        _i0 = float(globals().get("_PITCH_I_EXT", 0.0))
                        # 俯仰率前置（2026-09-30 守门轮十三：R74 力矩账
                        # 定案总矩残差缓涨压俯仰，外部积分只认角度误差，
                        # 响应滞后一拍（elev 先降后反）；残差压俯仰的早
                        # 期信号是体轴俯仰率负向启动，前置项让积分抢跑。
                        # FLAPPER_WY_AHEAD 为前置增益，默认 0=旧行为。
                        _wy_ahead = float(os.environ.get("FLAPPER_WY_AHEAD",
                                                         "0.0"))
                        _perr_i_eff = _perr_i - _wy_ahead * float(_vel_ap[4])
                        if abs(_perr_i_eff) < 0.400000 and not _grounded:
                            globals()["_PITCH_I_EXT"] = float(
                                np.clip(_i0 + _perr_i_eff * PHYSICS_DT,
                                        -2.5, 2.5))
                    except Exception:
                        pass
            globals()["_AP_OUT_CACHE"] = _ap_out   # 全量记录：自动驾驶仪输出快照

            # ── 关节拍动驱动（每物理步；手动通道覆盖油门/尾舵）──
            if _motion_runtime is not None:
                from modules.motion_action.execution import enqueue_controller_demand
                enqueue_controller_demand(_motion_runtime, _motion_cfg,
                    after_step=step_count, ap_out=_ap_out, manual=_man_ch,
                    backup=dict(globals().get("_DP_STATE") or {}))
            elif art is not None and _wk is not None:
                _drive_wing_joints(world, art, _wk, _man_ch, aero_callback,
                                   _ap_out, step_count)

            # ── 试验完成自动退出（2026-09-19）：phase=done 持续 1.5s
            #    后保存记录并关闭进程，不再依赖人工关闭窗口（残留进程会争写数据文件）──
            if EXPERIMENT_MODE and step_count % 30 == 0 and _experiment_done():
                break

            # ── 关节波形外发（30Hz → 本机示波器 tools/joint_scope.py）──
            if joint_scope_sender is not None and step_count % 4 == 0:
                _send_joint_scope(art, joint_scope_sender, _SIM_T)

            # ── 手动行程外发（30Hz → tools/ctrl_scope.py）──
            if ctrl_scope_sender is not None and step_count % 4 == 0:
                _send_ctrl_scope(_wk, ctrl_scope_sender,
                                 _man_ch, _WING_TGT_CACHE, art, _SIM_T)

            # ── 门洞感知：随行采集 + UDP 检测结果外发（每渲染帧）──
            if gate_coll is not None and _need_render:
                try:
                    gate_coll.tick(_SIM_T)
                    fr = gate_service_cam_frame(sensor_suite)
                    if fr is not None:
                        gate_service.on_frame(fr, _SIM_T)
                except Exception:
                    pass

            # ── 接收控制指令（非阻塞；按 TRACKING_MODE 分流驱动）──
            _recv_commands(command_receiver, mav_view, tracker,
                           telemetry_sender, aero_callback)

    except KeyboardInterrupt:
        logger.info("收到 Ctrl-C，正在退出 ...")
    except Exception as _e:   # 第四班调试：捕获被 kit 隐藏的主循环异常
        import traceback
        print("[LOOP-CRASH] " + traceback.format_exc(), flush=True)
        if _motion_runtime is not None:
            raise
    finally:
        logger.info("=== 仿真结束，总步数: %d ===", step_count)
        globals()["_MOTION_RUNTIME"] = None
        if aero_callback is not None and _motion_runtime is not None:
            aero_callback.motion_execution = None
        if _motion_runtime is not None:
            try:
                _motion_runtime.close()
            except Exception as _motion_close_error:
                logger.error("动作记录收尾失败: %s", _motion_close_error)
        _fwr_c = globals().get("_FW_IID_REC")
        if _fwr_c is not None:
            _fwr_c.close()
        _rec = globals().get("_CAM_REC")
        if _rec is not None:
            _rec.close()
        simulation_app.close()


# ============================================================
# 辅助函数
# ============================================================

def _setup_gate_perception(world, sensor_suite):
    """门洞感知挂载：圆门 USD + D435 标定 + 光照预设 + 随行采集 + UDP 服务。

    相机取左目（Camera_L，安装于扑动机构上，自带拍动振动/模糊退化）。
    返回 (RideAlongCollector, GatePerceptionService)。
    """
    import omni.usd
    from config import sim_config as cfg
    from modules import gate_manager
    from perception.sim_adapter import (RideAlongCollector, apply_d435_intrinsics,
                                        apply_lighting_preset)
    from perception.live import GatePerceptionService
    import modules.usv_network as usv_network

    stage = omni.usd.get_context().get_stage()
    gate_manager.spawn_gate(stage, cfg.GATE_POSITION, yaw_deg=cfg.GATE_YAW_DEG,
                            inner_r=cfg.GATE_INNER_R,
                            outer_r=cfg.GATE_OUTER_R,
                            depth=cfg.GATE_DEPTH)
    gate_circle = gate_manager.get_gate_circle(
        cfg.GATE_POSITION, cfg.GATE_YAW_DEG, cfg.GATE_INNER_R)

    cam = sensor_suite._camera_l
    apply_d435_intrinsics(cam)
    apply_lighting_preset(stage, cfg.PERCEPTION_LIGHT_PRESET)

    coll = RideAlongCollector(cam, gate_circle, cfg.PERCEPTION_COLLECT_DIR,
                              preset=cfg.PERCEPTION_LIGHT_PRESET)
    svc = GatePerceptionService(
        udp_sender=usv_network.UDPClient(target_port=cfg.PERCEPTION_UDP_PORT,
                                         target_ip="127.0.0.1"))
    print(f"[GATE] 门洞感知已挂载: 门位置{cfg.GATE_POSITION} 孔径{2*cfg.GATE_INNER_R:.2f}m "
          f"光照={cfg.PERCEPTION_LIGHT_PRESET} 采集目录{cfg.PERCEPTION_COLLECT_DIR} "
          f"检测外发udp:127.0.0.1:{cfg.PERCEPTION_UDP_PORT}", flush=True)
    return coll, svc


def gate_service_cam_frame(sensor_suite):
    """取左目当前帧 dict（Camera.get_current_frame，键 rgb/rgba 由适配层处理）。"""
    try:
        return sensor_suite._camera_l.get_current_frame()
    except Exception:
        return None

def _respawn_if_lost(mav_view, loader, step_count):
    """z<-10 或 |p|>80 时重生到初始点（穿地或数值发散飞离的保险机制，每次触发只打印一次）。"""
    try:
        pose = loader.get_pose()
        if pose is None:
            return
        x, y, z = pose[0], pose[1], pose[2]
        if z < -10.0 or abs(x) > 500.0 or abs(y) > 500.0:  # 80→500 夜间调试第8轮：0.6m巡航8m/s×10s即会越界触发重生
            pos = INITIAL_POSITION_RESPAWN
            mav_view.set_world_poses(
                positions=np.asarray([pos], dtype=np.float32),
                orientations=np.asarray([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32))
            try:
                mav_view.set_velocities(np.zeros((1, 6), dtype=np.float32))
            except Exception:
                pass
            print(f"[RESPAWN] t={loader.world.current_time:.1f}s "
                  f"从 ({x:.1f},{y:.1f},{z:.1f}) 重生于 {pos}", flush=True)
    except Exception as e:
        logger.debug("重生检查失败: %s", e)


_WING_TGT_CACHE = None   # 最近一次拍动目标（关节名序），用于示波器外发
_TRAIL_VIZ = None        # 循迹可视化（青色为期望轨迹/橙色为实际尾迹，main() 里创建）


def _manual_active(ch):
    """任一通道偏离中位 >0.05 即视为手动接管。"""
    if not ch:
        return False
    return any(abs(ch.get(k, 0.0)) > 0.05
               for k in ("throttle", "elevator", "aileron", "rudder"))
_DASH_T0 = _time.perf_counter()
_DASH_STEPS = 0


def _update_dashboard(dashboard, world, loader, art, wk, man_ch, aero_cb,
                      step_count, dash_only=False):
    """把 Isaac 实时状态组装为看板 JSON 快照。"""
    global _DASH_T0, _DASH_STEPS
    try:
        import config.sim_config as _cfg
        pose = loader.get_pose()
        vel = loader.get_velocity()
        # 双读裁决探针（2026-09-29 转弯线 R12：CSV 姿态列与控制器同源
        # 却不同值，嫌疑=首读陈旧缓存，复读取确认值；FLAPPER_DASH_QDBG=1
        # 时每 ~0.5s 打印首读与复读的四元数）
        if os.environ.get("FLAPPER_DASH_QDBG") == "1" and pose is not None \
                and step_count % 60 == 0:
            _pose2 = loader.get_pose()
            print(f"[DASH-QDBG] t={step_count * PHYSICS_DT:.2f} "
                  f"first={np.round(np.asarray(pose[3:7]), 4)} "
                  f"second={np.round(np.asarray(_pose2[3:7]), 4)}",
                  flush=True)
        if _det_on() and pose is not None:
            if _det_read(f"DASH step={step_count}", pose,
                         (vel if vel is not None else [0.0] * 6),
                         globals().get("_DET_DASH_POSE"),
                         globals().get("_DET_DASH_VEL")):
                try:
                    print(f"[READ-HDL] DASH step={step_count} "
                          f"physics_handle_valid="
                          f"{bool(loader.rigid_prim.is_physics_handle_valid())}",
                          flush=True)
                except Exception:
                    pass
            globals()["_DET_DASH_POSE"] = [float(q) for q in pose]
            globals()["_DET_DASH_VEL"] = ([float(q) for q in vel]
                                          if vel is not None else None)
        snap = {
            "t": float(step_count * PHYSICS_DT),
            "pose": [float(x) for x in pose] if pose is not None else None,
            "vel": [float(x) for x in vel] if vel is not None else None,
            "joints": None, "ch": man_ch or {},
            "freq": float(wk.frequency) if wk is not None else 0.0,
            "F": None, "mass": float(_cfg.MASS),
            "tether": {"anchor": list(_cfg.TETHER_ANCHOR),
                       "tension": float(_DP_STATE["tension"]),
                       "mode": _DP_STATE["mode"],
                       "dp_freq": _DP_STATE["freq"]},
        }
        if art is not None:
            names = list(art.joint_names or [])
            pos = art.get_joint_positions()
            if names is not None and pos is not None:
                snap["joints"] = {"names": names,
                                  "pos": [float(x) for x in np.asarray(pos).flatten()]}
            # 关节实际输出力矩（2026-09-20 夜间调试第9轮，六项缺口的末项）：
            # 在环俯仰被卡在 −0.22 的首要候选原因是不对称扭转运动学的物理反作用矩
            # （模型 My 恒零，观测不到该矩）——此列直接测得该矩
            try:
                eff = np.asarray(art.get_measured_joint_efforts(),
                                 dtype=float).reshape(-1)
                if names and len(eff) == len(names):
                    snap["jeff"] = dict(zip(names, [float(x) for x in eff]))
            except Exception:
                pass
        if aero_cb is not None:
            f = getattr(aero_cb, "last_F_world", None)
            if f is not None:
                snap["F"] = [float(f[0]), float(f[1]), float(f[2])]
            tq = getattr(aero_cb, "last_T_world", None)
            if tq is not None:
                snap["T"] = [float(tq[0]), float(tq[1]), float(tq[2])]
            tt = getattr(aero_cb, "last_tail_T", None)
            if tt is not None:
                snap["Tt"] = [float(tt[0]), float(tt[1]), float(tt[2])]
            # 全量记录（2026-09-19）：气动内部量——相对风速/滑流/翼-尾分量
            snap["vair"] = [float(x) for x in
                            np.asarray(getattr(aero_cb, "last_v_air",
                                               np.zeros(3)), dtype=float)]
            snap["vslip"] = float(getattr(aero_cb, "last_v_slip", 0.0) or 0.0)
            _fw = getattr(aero_cb.model, "current_F_world", None) \
                if hasattr(aero_cb, "model") else None
            if _fw is not None:
                snap["Fw"] = [float(x) for x in np.asarray(_fw, dtype=float)]
            _tw = getattr(aero_cb.model, "current_T_world", None) \
                if hasattr(aero_cb, "model") else None
            if _tw is not None:
                snap["Tw"] = [float(x) for x in np.asarray(_tw, dtype=float)]
            _ft = getattr(aero_cb, "last_tail_F", None)
            if _ft is not None:
                snap["Ft"] = [float(x) for x in np.asarray(_ft, dtype=float)]
        # 全量记录（第10轮补齐缺口②③）：钳位原值/触限/攻角统计
        _Fr = getattr(aero_cb, "last_F_raw", None)
        if _Fr is not None:
            snap["Fraw"] = [float(x) for x in np.asarray(_Fr, dtype=float)]
        _Tr = getattr(aero_cb, "last_T_raw", None)
        if _Tr is not None:
            snap["Traw"] = [float(x) for x in np.asarray(_Tr, dtype=float)]
        snap["clip"] = int(bool(getattr(aero_cb, "last_clipped", False)))
        # 第11轮对照列：尾翼实际攻角/合流速度
        _vl_ph = getattr(aero_cb, "last_vel_lin", None)
        if _vl_ph is not None:
            snap["velph"] = [float(x) for x in np.asarray(_vl_ph, dtype=float)]
        _tb = getattr(aero_cb, "tail_body", None)
        if _tb is not None:
            snap["tailA"] = float(getattr(_tb, "last_alpha_body", 0.0))
            _tvf = getattr(_tb, "last_vflow", None)
            if _tvf is not None:
                snap["tvfx"] = float(_tvf[0]); snap["tvfz"] = float(_tvf[2])
        if hasattr(aero_cb, "model"):
            _aL = getattr(aero_cb.model, "_last_aeff_L", None)
            _aR = getattr(aero_cb.model, "_last_aeff_R", None)
            if _aL is not None:
                snap["aeffL"] = [float(x) for x in _aL]
            if _aR is not None:
                snap["aeffR"] = [float(x) for x in _aR]
            # 逐叶素快照（2026-09-28 完整记录）：每翼十四站的行程位姿与
            # 站上力（体轴系，右翼已镜像）；瞬时子样口径（相位同步平均
            # 的最后一个求值），非平均值
            _sl = getattr(aero_cb.model, "_last_strips_L", None)
            _sr = getattr(aero_cb.model, "_last_strips_R", None)
            if _sl is not None or _sr is not None:
                snap["strips"] = {"L": _sl, "R": _sr}
        # 关节约束力（2026-09-28 预留列；事项 I-032 于 2026-09-30 接线）：
        # I-009 判别实验改判读取接口本体无罪——无渲染、混合渲染、物理
        # 回调在场、自由刚体在场四种现场均存活并返回有限数据，返回形状
        # 恒为 1×杆件位数×6；0928 崩溃源改判为上报开关创建语句，该语句
        # 禁止出现在任何施工中。此处按 I-009 交接规格在主循环
        # world.step 返回后的本线程内直接读取，返回数组按杆件位展平为
        # 六分量序列写入 jforce 键，由记录器以既有样式序列化为
        # jforce_json 列；不开上报开关，不在物理回调线程内调用；读取
        # 抛 Python 异常时本帧该列回退为空列表，不中断其余快照字段。
        snap["jforce"] = []
        if art is not None:
            try:
                _jf = np.asarray(art.get_measured_joint_forces(),
                                 dtype=float).reshape(-1)
                if _jf.size:
                    snap["jforce"] = [float(x) for x in _jf]
            except Exception:
                pass
        # 全量记录：自动驾驶仪输出快照 + 起飞状态锁定标记
        snap["ap"] = globals().get("_AP_OUT_CACHE")
        try:
            snap["pin"] = int(_cfg.STARTUP_PIN_S > 0
                              and step_count * PHYSICS_DT < _cfg.STARTUP_PIN_S)
        except Exception:
            snap["pin"] = 0
            _va = getattr(aero_cb, "last_v_air", None)
            if _va is not None:
                snap["v_air"] = [float(_va[0]), float(_va[1]), float(_va[2])]
            _mdl = getattr(aero_cb, "model", None)
            if _mdl is not None and hasattr(_mdl, "wing_left"):
                snap["ampL"] = float(_mdl.wing_left.amplitude)
                snap["ampR"] = float(_mdl.wing_right.amplitude)
        # 帧率估计（每秒刷新一次）
        now = _time.perf_counter()
        if now - _DASH_T0 > 1.0:
            globals()["_DASH_FPS"] = (step_count - globals().get("_DASH_S0", step_count)) \
                / (now - _DASH_T0)
            globals()["_DASH_S0"] = step_count
            _DASH_T0 = now
        snap["fps"] = globals().get("_DASH_FPS", 0.0)
        if not dash_only:
            _ident_stream_write(snap, man_ch)
        if dashboard is not None:
            dashboard.update(snap)
    except Exception as e:
        logger.debug("看板快照异常: %s", e)


# ── 试验台架工具函数（2026-09-19 运控建模线）──────────────────────

def _quat_to_euler(q):
    """四元数 [w,x,y,z] → (roll, pitch, yaw) rad。"""
    import math as _m
    w, x, y, z = q
    roll = _m.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = _m.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = _m.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def _euler_to_quat(roll, pitch, yaw):
    """(roll, pitch, yaw) rad → 四元数 [w,x,y,z]。"""
    import math as _m
    cr, sr = _m.cos(roll / 2), _m.sin(roll / 2)
    cp, sp = _m.cos(pitch / 2), _m.sin(pitch / 2)
    cy, sy = _m.cos(yaw / 2), _m.sin(yaw / 2)
    return [cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy]


def _pin_wing_joints(art, wk, aero_cb, t_sim):
    """把九个关节的位置与速度写成 t_sim 时刻的解析规定值（翅膀运动学钉住）。

    钉住期每步调用、释放拍再调用一次（2026-09-29 机主令，永久消除同实验
    不一致）。钉住期机身由 _bench_pin 每步钉死，但机翼关节此前由求解器
    自由积分：关节约束的冲量历史（求解器热启动缓存）在两次运行间积累
    出不可见差异，释放拍传入机身。本函数让钉住期的翅膀成为仿真时间的
    纯函数：频率恒取 FREQ_HOVER、相位按 (f0·t) mod 1 解析给定、幅值走
    STARTUP_AMP_RAMP_S 斜坡；求解器在钉住期看到的约束状态两次运行逐位
    一致，热启动缓存没有差异可以积累。释放后恢复伺服驱动。
    """
    _runtime = globals().get("_MOTION_RUNTIME")
    if _runtime is not None:
        _receipt = globals()["_MOTION_ADAPTER"].submit(_runtime.read(), pin=True)
        _runtime.record_consumption(_receipt)
        if _receipt.failed:
            raise RuntimeError("new action pin partially failed; preceding native effects retained")
        _runtime._pinned = True
        return _receipt
    if art is None or wk is None:
        return
    import math as _m
    import numpy as _np
    import config.sim_config as _c
    from config.sim_config import WING_DRIVE_CONFIG as _wdc
    _names = list(art.joint_names or [])
    if not _names:
        return
    _f0 = float(getattr(_c.FlightControllerConfig, "FREQ_HOVER", 1.55))
    _tau0 = (_f0 * t_sim) % 1.0
    wk.set_frequency(_f0)
    wk._tau_state = _tau0
    wk._t_prev = t_sim
    _mdl = getattr(aero_cb, "model", None) if aero_cb is not None else None
    if _mdl is not None:
        for _wing in (_mdl.wing_left, _mdl.wing_right):
            _wing.set_frequency(_f0)
            _wing._tau_state = _tau0
            _wing._t_prev = t_sim
    _ev = wk.evaluate(t_sim)
    _ramp_s = max(float(_c.STARTUP_AMP_RAMP_S), 0.05)
    _ramp = min(1.0, t_sim / _ramp_s)
    _rampd = (1.0 / _ramp_s) if t_sim < _ramp_s else 0.0
    _phi, _alp = _ev["phi"] * _ramp, _ev["alpha"] * _ramp
    _phid = _ev["phi_dot"] * _ramp + _ev["phi"] * _rampd
    _alpd = _ev["alpha_dot"] * _ramp + _ev["alpha"] * _rampd
    _fold0 = _wdc["fold_amp"] * 0.5
    _foldw = 1.0 + _m.sin(wk.omega * t_sim + 2.2)
    _fold = _fold0 * _foldw * _ramp
    _foldd = _fold0 * (wk.omega * _m.cos(wk.omega * t_sim + 2.2) * _ramp
                       + _foldw * _rampd)
    _trim = _wdc["tail_trim"]
    _pos_map = {"flap_L": _phi, "flap_R": -_phi,
                "twist_L": _alp, "twist_R": _alp,
                "fold_L": _fold, "fold_R": -_fold,
                "tail_yaw": 0.0, "tail_L": _trim, "tail_R": _trim}
    _vel_map = {"flap_L": _phid, "flap_R": -_phid,
                "twist_L": _alpd, "twist_R": _alpd,
                "fold_L": _foldd, "fold_R": -_foldd,
                "tail_yaw": 0.0, "tail_L": 0.0, "tail_R": 0.0}
    art.set_joint_positions(_np.asarray(
        [[float(_pos_map.get(n, 0.0)) for n in _names]], dtype=_np.float32),
        joint_names=_names)
    art.set_joint_velocities(_np.asarray(
        [[float(_vel_map.get(n, 0.0)) for n in _names]], dtype=_np.float32),
        joint_names=_names)


def _bench_pin(mav_view):
    """试验台架选择性状态锁定。

    默认六自由度全锁定（2026-09-18 力平衡台架行为）；当试验段带 release 且
    cfg.BENCH_FREE 非空时，释放指定轴——释放轴的角度/角速率（x 轴含线位置/
    线速度）由 PhysX 自由积分，其余轴保持锁定。俯仰锁定值取 cfg.BENCH_PITCH_DEG
    （trim 试验的配平偏置）。
    """
    import config.sim_config as _c
    free = tuple(_c.BENCH_FREE) if globals().get("_BENCH_RELEASE") else ()
    pos, quat = mav_view.get_world_poses()
    pos = np.asarray(pos, dtype=np.float64).reshape(-1)[:3]
    quat = np.asarray(quat, dtype=np.float64).reshape(-1)[:4]
    vel = np.asarray(mav_view.get_velocities(), dtype=np.float64).reshape(-1)[:6]
    roll, pitch, yaw = _quat_to_euler(quat)

    new_pos = [0.0, 0.0, float(_c.DP_TARGET_Z)]
    new_vel = np.zeros(6)
    if "x" in free:
        new_pos[0] = float(pos[0])
        new_vel[0] = vel[0]
    # 定速台架（rate 通道有速度处方）：该轴速率被运动学锁定（气动力无法改变它，
    # 泵能被物理隔离）；姿态角保留实际值自由积分——曾因"姿态角每步归零"
    # 造成根连杆与未随转翼面的关节约束每步瞬移冲突、力矩顶满限幅
    # （2026-09-19 rate_roll 实测），故姿态角必须随速率自然积分。
    _rate_wx = getattr(_c, "BENCH_RATE_WX", None)
    _rate_wy = getattr(_c, "BENCH_RATE_WY", None)
    _rate_wz = getattr(_c, "BENCH_RATE_WZ", None)
    r = roll if "roll" in free else 0.0
    p = pitch if "pitch" in free else float(_c.BENCH_PITCH_DEG) * np.pi / 180.0
    y = yaw if "yaw" in free else 0.0
    if "roll" in free:
        new_vel[3] = float(_rate_wx) if _rate_wx is not None else vel[3]
    if "pitch" in free:
        new_vel[4] = float(_rate_wy) if _rate_wy is not None else vel[4]
    if "yaw" in free:
        new_vel[5] = float(_rate_wz) if _rate_wz is not None else vel[5]
    mav_view.set_world_poses(
        positions=np.asarray([new_pos], dtype=np.float32),
        orientations=np.asarray([_euler_to_quat(r, p, y)], dtype=np.float32))
    mav_view.set_velocities(new_vel.reshape(1, 6).astype(np.float32))


_EXP_KICK_LAST = {"kick_wx": 0.0, "kick_wy": 0.0, "kick_wz": 0.0}


def _apply_experiment_channels(ch, mav_view):
    """把试验排程通道落到台架：俯仰偏置/来流/单轴释放/角速率脉冲。

    通道约定（modules/experiment.py 排程）：
      pitch_deg — 锁定俯仰角（度），改写 cfg.BENCH_PITCH_DEG
      wind      — 迎面风速 (m/s)，改写 cfg.WIND_VECTOR
      release   — 1 时解锁 cfg.BENCH_FREE 指定的轴（台架选择性状态锁定）
      kick_wx/wy/wz — 角速率脉冲 (rad/s)，值变化沿触发一次写入
    """
    import config.sim_config as _c
    if "pitch_deg" in ch:
        _c.BENCH_PITCH_DEG = float(ch["pitch_deg"])
    if "wind" in ch:
        _w = abs(float(ch["wind"]))
        _c.WIND_VECTOR = (-_w, 0.0, 0.0)   # 负号=迎面来流
    _rel = bool(ch.get("release", 0))
    globals()["_BENCH_RELEASE"] = _rel
    _c.BENCH_RELEASE = _rel        # 气力回调的选择性状态锁定也读这个标志
    # 定速台架：处方角速率（None=未处方，释放轴速率取实际值）
    for _key, _attr in (("rate_wx", "BENCH_RATE_WX"),
                        ("rate_wy", "BENCH_RATE_WY"),
                        ("rate_wz", "BENCH_RATE_WZ")):
        setattr(_c, _attr, float(ch[_key]) if _key in ch else None)
    for key, idx in (("kick_wx", 3), ("kick_wy", 4), ("kick_wz", 5)):
        val = float(ch.get(key, 0.0) or 0.0)
        if val != _EXP_KICK_LAST[key]:
            _EXP_KICK_LAST[key] = val
            if abs(val) > 1e-9:
                try:
                    vel = np.asarray(mav_view.get_velocities(),
                                     dtype=np.float32).reshape(1, 6).copy()
                    vel[0, idx] = val
                    mav_view.set_velocities(vel)
                    print(f"[KICK] {key}={val} rad/s 已施加", flush=True)
                except Exception as _e:
                    print(f"[KICK] 施加失败: {_e}", flush=True)


def _experiment_done():
    """试验排程进入 done 且持续 1.5s → True（主循环据此 break 并保存记录）。"""
    try:
        import json as _json
        with open(os.path.join(LOG_DIR, "experiment_phase.json"),
                  encoding="utf-8") as _f:
            ph = _json.load(_f)
    except Exception:
        globals()["_EXP_DONE_T0"] = None
        return False
    if ph.get("phase") == "done":
        t0 = globals().get("_EXP_DONE_T0")
        if t0 is None:
            globals()["_EXP_DONE_T0"] = _time.perf_counter()
            return False
        if _time.perf_counter() - t0 > 1.5:
            print("[EXP] phase=done，试验完成，自动保存记录并退出", flush=True)
            _ident_stream_close()
            return True
    else:
        globals()["_EXP_DONE_T0"] = None
    return False


def _update_follow_camera(loader):
    """主视口对准机器人（视点=位置+固定偏移），防止视角丢失。"""
    try:
        from isaacsim.core.utils.viewports import set_camera_view
        pose = loader.get_pose()
        if pose is None:
            return
        x, y, z = float(pose[0]), float(pose[1]), float(pose[2])
        dx, dy, dz = VIEWPORT_OFFSET
        set_camera_view(eye=[x + dx, y + dy, z + dz], target=[x, y, z])
    except Exception as e:
        logger.debug("跟踪相机失败: %s", e)


def _create_camera_viewports():
    """为启用的相机创建视口窗口并停靠。沿用 USV 版方法。"""
    try:
        import omni.kit.viewport.utility as vp_utils
        import omni.ui as ui
        import omni.kit.app
        import omni.usd
        import config.sim_config as sim_config

        stage = omni.usd.get_context().get_stage()
        cam_paths = []

        # 传感器路径与 USV 版一致（sensor_setup 在 MAV prim 下创建）
        if getattr(sim_config, "ENABLE_STEREO", True):
            cam_paths.extend([
                (f"{FLAPPER_PRIM_PATH}/Sensors/Camera_L", "MAV Camera Left"),
                (f"{FLAPPER_PRIM_PATH}/Sensors/Camera_R", "MAV Camera Right"),
            ])
        if getattr(sim_config, "ENABLE_SURROUND_CAMERAS", False):
            cam_paths.extend([
                (f"{FLAPPER_PRIM_PATH}/Sensors/Surround_F", "Surround Front"),
                (f"{FLAPPER_PRIM_PATH}/Sensors/Surround_B", "Surround Rear"),
                (f"{FLAPPER_PRIM_PATH}/Sensors/Surround_L", "Surround Left"),
                (f"{FLAPPER_PRIM_PATH}/Sensors/Surround_R", "Surround Right"),
            ])

        vp_windows = []
        for path, title in cam_paths:
            prim = stage.GetPrimAtPath(path)
            if prim.IsValid():
                vp = vp_utils.create_viewport_window(title, width=320, height=320)
                vp.viewport_api.set_active_camera(str(path))
                vp_windows.append(vp)
                logger.info("摄像头视口已绑定: %s", path)
            else:
                logger.warning("Camera prim 不存在: %s", path)

        # 延迟停靠
        async def _deferred_dock():
            await omni.kit.app.get_app().next_update_async()
            await omni.kit.app.get_app().next_update_async()
            try:
                wins = [ui.Workspace.get_window(t) for _, t in cam_paths]
                wins = [w for w in wins if w is not None]
                content_win = ui.Workspace.get_window("Content")
                if not content_win or not wins:
                    return
                wins[0].dock_in(content_win, ui.DockPosition.RIGHT, ratio=0.5)
                for i in range(1, len(wins)):
                    wins[i].dock_in(wins[i-1], ui.DockPosition.RIGHT, ratio=0.5)
                logger.info("%d 个视口已停靠完毕", len(wins))
            except Exception as e:
                logger.warning("窗口停靠失败: %s", e)

        import asyncio
        asyncio.ensure_future(_deferred_dock())
    except Exception as e:
        logger.warning("视口创建失败（不影响仿真）: %s", e)


_DP_STATE = {"freq": None, "tension": 0.0, "mode": "free"}
_TETHER_ERR_N = 0


def _apply_tether_and_dp(mav_view, loader):
    """系留绳力（软绳，只产生拉力不产生推力）+ 定高 DP（高度误差→拍频）。

    DP 优先级：手动油门偏离中位 > DP > 默认拍频。结果存 _DP_STATE 供
    驱动环和看板读取。
    """
    global _DP_STATE
    # 仅登记原消费者执行结果，不重试、不另施力，也不改变原返回路径。
    globals()["_I186_TETHER_RESULT"] = {
        "status": "unknown", "selected": None,
        "observed_at_sim_t": globals().get("_SIM_T"),
        "query_error": "系留消费者本步尚未取得对象读数"}
    try:
        from config import sim_config as cfg
        pose = loader.get_pose()
        vel = loader.get_velocity()
        if pose is None or vel is None:
            return
        p = np.asarray(pose[0:3], dtype=float)
        v = np.asarray(vel[0:3], dtype=float)
        tension = 0.0

        # ── 系留绳（自由飞行/联调时启用；试验模式走运动学固定台架）──
        if cfg.TETHER_ENABLED and not cfg.EXPERIMENT_MODE:
            globals()["_I186_TETHER_RESULT"] = {
                "status": "succeeded", "selected": True,
                "observed_at_sim_t": globals().get("_SIM_T"),
                "force_applied": False, "tension_N": 0.0}
            anchor = np.asarray(cfg.TETHER_ANCHOR, dtype=float)
            length = cfg.TETHER_LENGTH
            k_s, c_s = cfg.TETHER_K, cfg.TETHER_C
            d = p - anchor
            dist = float(np.linalg.norm(d))
            if dist > length:
                n = d / dist
                stretch = dist - length
                v_r = float(np.dot(v, n))
                f_mag = k_s * stretch + c_s * max(v_r, 0.0)
                f_mag = max(f_mag, 0.0)
                tension = f_mag
                if f_mag > 0.0:
                    F = -n * f_mag
                    try:
                        mav_view.apply_forces_and_torques_at_pos(
                            forces=F.reshape(1, 3).astype(np.float32),
                            is_global=True)
                        globals()["_I186_TETHER_RESULT"].update(
                            force_applied=True, tension_N=float(tension))
                    except Exception as _fe:
                        globals()["_I186_TETHER_RESULT"].update(
                            status="failed", query_error=repr(_fe),
                            tension_N=float(tension))
                        with open(os.path.join(LOG_DIR, "tether_diag.txt"), "a", encoding="utf-8") as f:
                            f.write("APPLY-EXC: %r\n" % (_fe,))
                    globals()["_TETHER_DBG"] = globals().get("_TETHER_DBG", 0) + 1
                    if globals()["_TETHER_DBG"] % 120 == 1:
                        with open(os.path.join(LOG_DIR, "tether_diag.txt"), "a", encoding="utf-8") as f:
                            f.write("DBG dist=%.3f f_mag=%.1f z=%.2f\n" % (dist, f_mag, p[2]))
        else:
            globals()["_I186_TETHER_RESULT"] = {
                "status": "succeeded", "selected": False,
                "observed_at_sim_t": globals().get("_SIM_T"),
                "force_applied": False, "tension_N": 0.0}

        # ── 试验台架（2026-09-18 运动学固定版 → 2026-09-19 选择性释放版）──
        # 力平衡台架的标准做法：气动力由回调独立记录，不经过弹簧-摆动力学。
        # 默认六自由度全锁定；试验段带 release 且 cfg.BENCH_FREE 非空时，
        # 指定轴由 PhysX 自由积分（单轴衰减/俯仰-速度链试验用）。
        if cfg.EXPERIMENT_MODE:
            try:
                _bench_pin(mav_view)
            except Exception:
                pass

        # ── 定高 DP：freq = f0 + kp(z_ref - z) - kd·vz（2026-09-19 防泵能版）──
        # 泵能机理：拍动振荡力使瞬时 z/vz 以拍频抖动，DP 直接读瞬时值 →
        # 拍频指令在 1.5↔3.5Hz 间剧烈摆动（3.5Hz 升力=3.5 倍体重）→ 弹射→绳拽回→
        # 再弹射，参数泵能导致翻机（三组在环 A/B 实录）。三项修正措施：
        #   输入 z/vz 先经 EWMA 低通（τ=0.3s，拍频 1.9Hz 抖动被抑制）；
        #   输出限幅到悬停邻域 [1.7, 2.2]Hz（权限 ±1.3N，满足定高不满足弹射）；
        #   输出变化率斜坡 0.8Hz/s。
        freq = None
        if cfg.DP_HEIGHT_HOLD:
            _tau = cfg.DP_FILT_TAU
            _zs = globals().get("_DP_FILT_Z")
            _zs = p[2] if _zs is None else _zs + (p[2] - _zs) * (1.0 / 120.0) / _tau
            _vzs = globals().get("_DP_FILT_VZ")
            _vzs = v[2] if _vzs is None else _vzs + (v[2] - _vzs) * (1.0 / 120.0) / _tau
            globals()["_DP_FILT_Z"] = _zs
            globals()["_DP_FILT_VZ"] = _vzs
            freq = cfg.DP_FREQ_NOMINAL + cfg.DP_KP * (cfg.DP_TARGET_Z - _zs) \
                - cfg.DP_KD * _vzs
            freq = float(np.clip(freq, cfg.DP_FREQ_MIN, cfg.DP_FREQ_MAX))
            _fl = globals().get("_DP_FREQ_LAST")
            if _fl is not None:
                freq = float(np.clip(freq, _fl - cfg.DP_SLEW / 120.0,
                                     _fl + cfg.DP_SLEW / 120.0))
            globals()["_DP_FREQ_LAST"] = freq

        _DP_STATE = {"freq": freq, "tension": tension,
                     "mode": "tether" if cfg.TETHER_ENABLED else "free"}
    except Exception as e:
        globals()["_I186_TETHER_RESULT"].update(
            status="failed", query_error=repr(e))
        global _TETHER_ERR_N
        try:
            _TETHER_ERR_N += 1
        except NameError:
            _TETHER_ERR_N = 1
        if _TETHER_ERR_N <= 3:
            import traceback
            with open(os.path.join(LOG_DIR, "tether_diag.txt"), "a", encoding="utf-8") as f:
                f.write("EXC#%d: %s\n%s\n" % (_TETHER_ERR_N, e, traceback.format_exc()))
        logger.debug("系留/DP 异常: %s", e)


def _recv_commands(receiver, mav_view, tracker, telemetry_sender, aero_callback=None):
    """接收桥接 pos_cmd（JSON），按 TRACKING_MODE 驱动机体。

    数据流（对端 = WSL 侧 isaac_bridge）：
      收 9002: {"type":"pos_cmd", position/velocity/acceleration/yaw/yaw_dot/t}
               {"type":"hello", ...}  ← 1Hz 心跳，用于学习对端地址
      发 9001: 遥测回发到学到的对端地址（_send_telemetry 负责）

    驱动模式（config.TRACKING_MODE）：
      kinematic — tracker 速度直写 PhysX（桥接联调；会覆盖气动积分，不得与 aero 模式同用）
      aero      — 不写速度；pos_cmd 高度喂 AeroPhysicsCallback 的 HoverPD，
                  水平位置环留给 TECS+串级PID 增量（SparrowHawk V.B 结构）
    """
    from config.sim_config import TRACKING_MODE
    try:
        # 1) 排空接收缓冲，只留最新参考
        while True:
            data = receiver.receive()
            if data is None:
                break
            addr = receiver.get_last_addr()
            if addr and telemetry_sender.target_ip != addr[0]:
                telemetry_sender.set_target(addr[0])
                logger.info("[BRIDGE] 遥测目标已学习 → %s:%d",
                            addr[0], telemetry_sender.target_port)
            if isinstance(data, dict) and data.get("type") == "pos_cmd":
                tracker.set_reference(data)
                # 轨迹可视化旁路：期望轨迹历史（不动 tracker 内部）；
                # 首条真实数据到达时移除演示圆
                if _TRAIL_VIZ is not None:
                    try:
                        if globals().get("_TRAIL_DEMO_ON"):
                            _TRAIL_VIZ.clear_desired()
                            globals()["_TRAIL_DEMO_ON"] = False
                        _TRAIL_VIZ.push_ref(data.get("position"))
                    except Exception:
                        pass
            elif isinstance(data, dict) and data.get("type") == "path":
                # bishop 若发送整条期望轨迹，一次性覆盖青色期望轨迹线
                if _TRAIL_VIZ is not None:
                    try:
                        _TRAIL_VIZ.set_path(data.get("points", []))
                        _TRAIL_VIZ.flush()
                        globals()["_TRAIL_DEMO_ON"] = False
                    except Exception:
                        pass

        # 2) 每个物理步闭环
        if tracker.has_reference():
            loader = flapper_loader.get_loader()
            pose = loader.get_pose()
            vel = loader.get_velocity()
            if pose is not None and vel is not None:
                out = tracker.compute(pose, vel)   # 误差统计两种模式共用

                if TRACKING_MODE == "kinematic":
                    try:
                        mav_view.set_linear_velocities(
                            np.asarray([out["v_des"]], dtype=np.float32))
                        mav_view.set_angular_velocities(
                            np.asarray([out["w_des"]], dtype=np.float32))
                    except Exception as e:
                        logger.debug("速度写入失败(占位刚体阶段预期内): %s", e)

                elif TRACKING_MODE == "aero":
                    # 只向 HoverPD 提供高度目标；严禁直接写速度（会覆盖气动积分结果）
                    if aero_callback is not None:
                        try:
                            aero_callback.set_controller_target(
                                height=float(tracker.ref["position"][2]))
                        except Exception as e:
                            logger.debug("气动目标注入失败: %s", e)
    except Exception as e:
        logger.debug("指令接收异常: %s", e)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        with open(os.path.join(LOG_DIR, "fatal.txt"), "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
        raise
