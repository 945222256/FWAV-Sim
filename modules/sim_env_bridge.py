"""sim_env_bridge — 环境变量桥接与网络/输入/飞控/看板构建段
=============================================================
2026-09-30 T6.3 拆分：main() 内 5.6—5.7 段（气动回调创建、UDP 链路、
手动输入源、全部 FLAPPER_* 环境变量桥接开关族、_SIDE_PULSE_* 侧滑脉冲
参数、自动驾驶仪创建与健康自检、看板启动）整段提取为本模块的
build_env_bridge(world, mav_view)，逐字迁出，逻辑、数值、默认值、语句
顺序零改动。

命名空间适配（唯一的非逐字改动）：原段内 4 行
globals()["_SIDE_PULSE_*"] 写入迁出后改经 _G（main_sim 宿主命名空间
字典）写入同一批变量，供留守 main_sim 的 _drive_wing_joints（侧滑脉冲
施加大段）读取，运行时行为不变。

返回 SimpleNamespace，字段：aero_callback / telemetry_sender /
command_receiver / tracker / joint_scope_sender / ctrl_scope_sender /
manual / autopilot / dashboard / EXPERIMENT_MODE；main() 逐字段回填到
原变量名，主循环代码零改动。

不随本段迁移（登记为待契约迁移）：5.8 world.reset() 前后的泄漏订阅
退订与物理回调重注册段（含 FLAPPER_POST_STEP_CB / FLAPPER_MAINLOOP_AERO
分支与 _post_step_cb 闭包）——该段以初始化顺序敏感的 reset/订阅时序为
主干，与多个 main() 局部变量及 globals() 键交织，按铁律 5 留守。

依赖说明：modules.flapper_controller（TrajectoryTracker）为纯 Python，
顶层导入；modules.flapper_loader 依赖 isaacsim，函数内导入（与 main_sim
顶部导入的为同一模块对象，运行时行为不变）。
"""

import os
import sys
import logging
from types import SimpleNamespace

from config.sim_config import LOG_DIR
from modules.flapper_controller import TrajectoryTracker

logger = logging.getLogger("main_sim")

# 宿主命名空间（main_sim 以 __main__ 运行或被 import 时的模块字典）：
# _SIDE_PULSE_* 等共享缓存住在该命名空间
_G = (sys.modules.get("main_sim") or sys.modules["__main__"]).__dict__

# 仓库根目录：config/motion_recipes 等相对路径按此解析（生产主循环从
# 仓库根启动；本模块位于 modules/ 下，根为其父目录）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_action_recipe(env=None):
    """解析动作表达配方选择并装载配方（生产入口，事项 I-219）。

    读取 FLAPPER_ACTION_RECIPE 与 FLAPPER_ACTION_RECORD（env 缺省取
    os.environ），只更新 config.sim_config.ActionExpressionConfig 的选择
    字段。选择旧配方（"legacy_current" 或空）时返回 None，不装载新对
    象；选择新配方时经 modules.motion_action 的装载入口读取并校验配方，
    返回 MotionRecipe。未知配方或非法绑定抛 RecipeError（ValueError 子
    类，带可定位信息），不回退为另一动作。
    """
    _env = os.environ if env is None else env
    import config.sim_config as _cfg_ae
    _AEC = _cfg_ae.ActionExpressionConfig
    _rec = _env.get("FLAPPER_ACTION_RECORD")
    if _rec is not None and _rec.strip() != "":
        _AEC.RECORD_ACTIONS = _rec.strip().lower() not in ("0", "false", "off")
        print(f"[ACTION] 动作记录覆盖 RECORD_ACTIONS="
              f"{_AEC.RECORD_ACTIONS}", flush=True)
    _rid = (_env.get("FLAPPER_ACTION_RECIPE")
            or _AEC.RECIPE_ID or "").strip()
    if _rid in ("", "legacy_current"):
        return None
    from modules.motion_action import (load_recipe, load_recipe_by_name,
                                       RecipeError)
    _path = _AEC.RECIPE_PATH
    if _path:
        recipe = load_recipe(_path)
        # I-219 返修（Fil 复核 recipe_name_path_disagreement）：显式配方
        # 名与路径装载结果不符即报错，不允许静默装回另一配方。
        if recipe.name != _rid:
            raise RecipeError(
                "请求配方名 %r 与 RECIPE_PATH 装载的配方 %r 不符；名字与"
                "路径必须指向同一配方，不静默回退" % (_rid, recipe.name),
                where=str(_path))
    else:
        _dir = _AEC.RECIPES_DIR
        if not os.path.isabs(_dir) and not os.path.isdir(_dir):
            _dir = os.path.join(_REPO_ROOT, _dir)
        recipe = load_recipe_by_name(_dir, _rid)
    print(f"[ACTION] 动作配方装载：{recipe.name} v{recipe.version}"
          f"（九通道绑定 {len(recipe.channels)} 行，节律节点 "
          f"{len(recipe.rhythm_nodes)} 个）", flush=True)
    return recipe


def build_env_bridge(world, mav_view):
    """main() 原 5.6—5.7 段整段：环境变量读取、配置覆写与对象构建。

    执行顺序、打印、异常口径与原 main() 内联版本逐字一致；
    返回 SimpleNamespace 供 main() 逐字段回填。
    """
    # 气动模型环境桥（2026-09-30 I-014 Isaac侧悬停基准）：场景气动模型
    # 选择在回调构造时读 cfg.AERO_MODEL（见 modules/aero_solver.py 的
    # create_aero_callback 与 modules/aero/callback.py 的
    # AeroPhysicsCallback.__init__），故本桥必须先于 5.6 段注册执行。
    # 不设 FLAPPER_AERO_MODEL 时不触碰配置，行为与基线逐位一致。
    if os.environ.get("FLAPPER_AERO_MODEL"):
        try:
            import config.sim_config as _cfg_am
            _v = os.environ["FLAPPER_AERO_MODEL"].strip()
            print(f"[TEST] AERO_MODEL 覆盖 "
                  f"{_cfg_am.AERO_MODEL}→{_v}", flush=True)
            _cfg_am.AERO_MODEL = _v
        except Exception as _e:
            print(f"[TEST] AERO_MODEL 覆盖失败: {_e}", flush=True)
    # 波形参数环境桥（2026-09-30 守门轮六：工作点迁移在环验证用，
    # 扩域重搜最近能力点全套参数覆盖；不设时不触碰配置）。
    # 2026-09-30 I-016 时序修正：本桥原位于 5.7 段（气动回调构造之后），
    # 而力模型的翼运动学在 FlapperAeroModel 构造时读取 SPLIT/PITCH_LAG_S/
    # MEAN（WingKinematics.__init__ 函数体），FREQUENCY/AMPLITUDE/ALPHA_SET
    # 更是在模块导入时固化的默认参数，故原位置对力模型完全不生效，只影响
    # 5.9a 之后构造的关节驱动 _wk 的三项构造期读取（视觉路径）。现移到
    # 5.6 段气动回调构造之前，使构造期读取的三项落位力模型；幅值与攻角
    # 另在回调构造后经 set_wing_command 补推（见下方块）。不设环境变量
    # 时零触碰，默认行为与原位置逐位一致。
    try:
        import config.sim_config as _cfg_wave
        _FK = _cfg_wave.FlapKinematicsConfig
        for _we, _wk in (("FLAPPER_WAVE_FREQ", "FREQUENCY"),
                         ("FLAPPER_WAVE_MEAN", "MEAN"),
                         ("FLAPPER_WAVE_SPLIT", "SPLIT"),
                         ("FLAPPER_WAVE_LAG", "PITCH_LAG_S"),
                         ("FLAPPER_WAVE_AMP", "AMPLITUDE"),
                         ("FLAPPER_WAVE_ALPHA", "ALPHA_SET")):
            if os.environ.get(_we):
                _v = float(os.environ[_we])
                print(f"[TEST] 波形覆盖 {_wk}="
                      f"{getattr(_FK, _wk)}→{_v}", flush=True)
                setattr(_FK, _wk, _v)
    except Exception as _e:
        print(f"[TEST] 波形覆盖失败: {_e}", flush=True)
    # 巡航场景工作点桥（2026-09-30 I-016 巡航科目场景接线）：三维在环
    # 确认文档第六跑 5e 第一节二十处配置值的运行时落位用。此前无环境
    # 变量桥的十一个键在此补齐，值一律由调用方（基准运行器按判据文件
    # 巡航科场景冻结值）设置，本块只透传不改值。消费者读取时机核对：
    # 模块级四键与 FlightControllerConfig 六键均为运行时属性读取或自动
    # 驾驶仪构造（本函数后段）时读取，AeroModelConfig.WING_ROT_DAMP 为
    # 气动力每步运行时读取，本块位置均在读取之前。不设任何环境变量时
    # 零触碰，默认行为逐位一致。
    try:
        import config.sim_config as _cfg_sc
        for _se, _obj, _attr in (
                ("FLAPPER_DPFREQ_NOMINAL", _cfg_sc, "DP_FREQ_NOMINAL"),
                ("FLAPPER_DPFREQ_MIN", _cfg_sc, "DP_FREQ_MIN"),
                ("FLAPPER_LAUNCH_VX", _cfg_sc, "LAUNCH_VX"),
                ("FLAPPER_LAUNCH_PITCH", _cfg_sc, "LAUNCH_PITCH"),
                ("FLAPPER_WING_ROT_DAMP",
                 _cfg_sc.AeroModelConfig, "WING_ROT_DAMP"),
                ("FLAPPER_FREQ_HOVER",
                 _cfg_sc.FlightControllerConfig, "FREQ_HOVER"),
                ("FLAPPER_CRUISE_VX",
                 _cfg_sc.FlightControllerConfig, "CRUISE_VX"),
                ("FLAPPER_TILT_MAX",
                 _cfg_sc.FlightControllerConfig, "TILT_MAX"),
                ("FLAPPER_PITCH_TRIM",
                 _cfg_sc.FlightControllerConfig, "PITCH_TRIM"),
                ("FLAPPER_KP_A",
                 _cfg_sc.FlightControllerConfig, "KP_A"),
                ("FLAPPER_KI_A",
                 _cfg_sc.FlightControllerConfig, "KI_A")):
            if os.environ.get(_se):
                _v = float(os.environ[_se])
                print(f"[TEST] 场景覆盖 {_attr}="
                      f"{getattr(_obj, _attr)}→{_v}", flush=True)
                setattr(_obj, _attr, _v)
    except Exception as _e:
        print(f"[TEST] 场景覆盖失败: {_e}", flush=True)
    # ----------------------------------------------------------
    # 5.6 注册气动力学物理回调（Sane & Dickinson 2002 模型）
    #     传入 mav_view 后启用不经过 Fabric 加锁的直接读写路径（与 USV 一致）
    # ----------------------------------------------------------
    if os.environ.get("FLAPPER_NO_AERO"):
        aero_callback = None
        logger.warning("FLAPPER_NO_AERO=1：气动回调不创建（判决性实验开关）")
    else:
        from modules import flapper_loader   # Isaac 依赖，函数内导入（与 main_sim 同一模块对象）
        aero_callback = flapper_loader.setup_aero(world, flapper_view=mav_view)
    # 波形幅值/攻角的力模型补推（2026-09-30 I-016）：WingKinematics 的
    # AMPLITUDE 与 ALPHA_SET 是模块导入时固化的默认参数，类属性覆盖到
    # 不了已构造的力模型翼实例，故 FLAPPER_WAVE_AMP / FLAPPER_WAVE_ALPHA
    # 设定时在此经 set_wing_command（与每步频率同步同一接口）直接推入
    # 两翼。频率不需要补推：钉住段 _pin_wing_joints 与释放后
    # _drive_wing_joints 每步都会把 FREQ_HOVER/控制器频率同步进力模型。
    # 不设时不触碰。
    if aero_callback is not None and hasattr(aero_callback, "model"):
        _push = {}
        if os.environ.get("FLAPPER_WAVE_AMP"):
            _push["amplitude_deg"] = float(os.environ["FLAPPER_WAVE_AMP"])
        if os.environ.get("FLAPPER_WAVE_ALPHA"):
            _push["alpha_set_deg"] = float(os.environ["FLAPPER_WAVE_ALPHA"])
        if _push:
            try:
                aero_callback.model.set_wing_command(**_push)
                print(f"[TEST] 波形力模型补推 {_push}", flush=True)
            except Exception as _e:
                print(f"[TEST] 波形力模型补推失败: {_e}", flush=True)

    # ----------------------------------------------------------
    # 动作表达配方选择桥（事项 I-219；送审方案 §4.1）：现有波形环境
    # 覆盖与力模型补推完成后解析 FLAPPER_ACTION_RECIPE 与
    # FLAPPER_ACTION_RECORD。只更新 ActionExpressionConfig 并按需装载
    # 配方；未知配方或非法绑定经 resolve_action_recipe 显式报错，不回
    # 退为另一动作。默认（未设变量且 RECIPE_ID=legacy_current）返回
    # None，不装载任何新对象，后续路径行为与基线逐位一致。
    # ----------------------------------------------------------
    motion_recipe = resolve_action_recipe()

    # ----------------------------------------------------------
    # 5.7 创建 UDP 网络链路（控制器进程 ↔ 仿真进程）
    #     bind 0.0.0.0：接收 WSL 侧 isaac_bridge 发送的 pos_cmd
    # ----------------------------------------------------------
    import modules.usv_network as usv_network
    from config.sim_config import (TELEMETRY_PORT, COMMAND_PORT, TRACKING_MODE,
                                   ENABLE_JOINT_SCOPE, JOINT_SCOPE_PORT,
                                   ENABLE_CTRL_SCOPE, CTRL_SCOPE_PORT,
                                   MANUAL_INPUT_ENABLED)
    telemetry_sender = usv_network.UDPClient(target_port=TELEMETRY_PORT)
    command_receiver = usv_network.UDPServer(port=COMMAND_PORT, bind_ip="0.0.0.0")
    tracker = TrajectoryTracker(
        csv_path=os.path.join(LOG_DIR, "track_error.csv"))
    joint_scope_sender = usv_network.UDPClient(
        target_port=JOINT_SCOPE_PORT, target_ip="127.0.0.1") \
        if ENABLE_JOINT_SCOPE else None
    ctrl_scope_sender = usv_network.UDPClient(
        target_port=CTRL_SCOPE_PORT, target_ip="127.0.0.1") \
        if ENABLE_CTRL_SCOPE else None
    manual = None
    # 2026-09-20 第四班：FLAPPER_NO_MANUAL=1 禁用手动通道——172836 运行
    # 实录 t=0.59s 起出现未登记的手动油门输入（≈+0.1→f=2.54、舵中立）并覆盖
    # 自动驾驶仪全程（mode 列 5787 样本=2），候选原因为操纵杆物理偏置或无头
    # 键盘误读。验收运行必须排除该环境扰动；交互式手动飞行不受影响。
    from config.sim_config import EXPERIMENT_MODE   # 第四班：import 提出分支
    if MANUAL_INPUT_ENABLED and not os.environ.get("FLAPPER_NO_MANUAL"):
        if EXPERIMENT_MODE:
            from modules.experiment import ExperimentController, MODE_DEFAULTS
            manual = ExperimentController(EXPERIMENT_MODE)
            # 台架模式默认（2026-09-19）：风开关与释放轴按模式自动配置到位，
            # 替代手工 sed 改配置（此前 dastep 误在 5m/s 迎风下运行）
            import config.sim_config as _cfg_exp
            _md = MODE_DEFAULTS.get(EXPERIMENT_MODE, {})
            _cfg_exp.WIND_ENABLED = bool(_md.get("wind", False))
            _cfg_exp.BENCH_FREE = tuple(_md.get("free", ()))
            print(f"[MANUAL] 输入源: {manual.source}（自动试验）", flush=True)
            print(f"[BENCH] 模式默认: WIND_ENABLED={_cfg_exp.WIND_ENABLED} "
                  f"BENCH_FREE={_cfg_exp.BENCH_FREE}", flush=True)
        else:
            from modules.manual_input import ManualInput
            manual = ManualInput()
            print(f"[MANUAL] 输入源: {manual.source}（{'左杆油门/方向 右杆升降/副翼' if manual.source == 'joystick' else 'W/S油门 ↑↓升降 ←→副翼 Q/E方向'}）", flush=True)
    # 侧滑脉冲与偏航抵消组合通道的试验开关（2026-09-27，环境变量驱动，
    # 默认全部关闭、不改变任何生效行为）：FLAPPER_WING_MIX=1 开启组合
    # 通道；FLAPPER_SIDESLIP_PULSE=1 在 FLAPPER_SIDESLIP_PULSE_T 时刻
    # 施加一次横向速度脉冲 FLAPPER_SIDESLIP_PULSE_DVY。
    if os.environ.get("FLAPPER_WING_MIX") == "1":
        try:
            import config.sim_config as _cfg_mix
            _cfg_mix.FlightControllerConfig.WING_MIX_ON = True
            print("[TEST] 偏航抵消组合通道开启", flush=True)
        except Exception as _e:
            print(f"[TEST] 组合通道开启失败: {_e}", flush=True)
    if os.environ.get("FLAPPER_BANK_TEST") == "1":
        try:
            import config.sim_config as _cfg_bank
            _cfg_bank.FlightControllerConfig.BANK_TEST_ON = True
            _cfg_bank.FlightControllerConfig.BANK_TEST_DEG = float(
                os.environ.get("FLAPPER_BANK_DEG", "10.0"))
            _cfg_bank.FlightControllerConfig.BANK_TEST_T = float(
                os.environ.get("FLAPPER_BANK_T", "5.0"))
            _cfg_bank.FlightControllerConfig.BANK_FREQ_CAP = float(
                os.environ.get("FLAPPER_BANK_FREQ_CAP", "2.2"))
            if os.environ.get("FLAPPER_BANK_FREQ_FREEZE") == "1":
                _cfg_bank.FlightControllerConfig.BANK_FREQ_FREEZE = True
                print("[TEST] 盘旋段拍频冻结开启", flush=True)
            if os.environ.get("FLAPPER_BANK_SLEW"):
                _v = float(os.environ["FLAPPER_BANK_SLEW"])
                print(f"[TEST] 坡度指令斜坡率覆盖 "
                      f"{_cfg_bank.FlightControllerConfig.BANK_SLEW}→{_v}",
                      flush=True)
                _cfg_bank.FlightControllerConfig.BANK_SLEW = _v
            else:
                # 事项 I-067 链路保真修复：坡度试验开启而斜坡率未显式
                # 给出时，默认取判据文件冻结配方的 0.25 弧度每秒。配置
                # 默认 999 为阶跃档，直跑启动器漏带斜坡率键时静默
                # 降级成阶跃切入，违反冻结配方与 R1 在环教训的斜坡
                # 处方；显式给出时仍以显式值为准。
                _cfg_bank.FlightControllerConfig.BANK_SLEW = 0.25
                print("[TEST] 坡度指令斜坡率未显式给出，按冻结配方默认 "
                      "0.25 弧度每秒", flush=True)
            if os.environ.get("FLAPPER_YAW_FF_ADAPT") == "1":
                _cfg_bank.FlightControllerConfig.YAW_FF_ADAPT = True
                print("[TEST] 偏航前馈自校准开启", flush=True)
            if os.environ.get("FLAPPER_YAW_FF") == "1":
                _cfg_bank.FlightControllerConfig.YAW_FF_TURN = True
                _cfg_bank.FlightControllerConfig.YAW_FF_DIR = float(
                    os.environ.get("FLAPPER_YAW_FF_DIR", "-1.0"))
                if os.environ.get("FLAPPER_COORD_KB"):
                    _cfg_bank.FlightControllerConfig.COORD_KB = float(
                        os.environ["FLAPPER_COORD_KB"])
                if os.environ.get("FLAPPER_COORD_KI"):
                    _v = float(os.environ["FLAPPER_COORD_KI"])
                    print(f"[TEST] 侧滑静差积分增益覆盖 "
                          f"{_cfg_bank.FlightControllerConfig.COORD_KI}→{_v}",
                          flush=True)
                    _cfg_bank.FlightControllerConfig.COORD_KI = _v
                print(f"[TEST] 协调转弯率前馈开启 方向="
                      f"{_cfg_bank.FlightControllerConfig.YAW_FF_DIR} "
                      f"β闭环KB={_cfg_bank.FlightControllerConfig.COORD_KB}",
                      flush=True)
            if os.environ.get("FLAPPER_BANK_PHOLD") == "1":
                _cfg_bank.FlightControllerConfig.BANK_PITCH_HOLD_ON = True
                _cfg_bank.FlightControllerConfig.BANK_PITCH_HOLD_DEG = float(
                    os.environ.get("FLAPPER_BANK_PHOLD_DEG", "-10.0"))
                print(f"[TEST] 坡度段俯仰保持开启: "
                      f"{_cfg_bank.FlightControllerConfig.BANK_PITCH_HOLD_DEG}°",
                      flush=True)
            print(f"[TEST] 定坡度盘旋开启: {_cfg_bank.FlightControllerConfig.BANK_TEST_DEG}° "
                  f"@t>={_cfg_bank.FlightControllerConfig.BANK_TEST_T}s", flush=True)
        except Exception as _e:
            print(f"[TEST] 盘旋开关开启失败: {_e}", flush=True)
    if os.environ.get("FLAPPER_VFIN") == "1":
        try:
            import config.sim_config as _cfg_vfin
            _cfg_vfin.TailAeroConfig.VFIN_AERO_ON = True
            print("[TEST] 固定垂尾气动开启", flush=True)
        except Exception as _e:
            print(f"[TEST] 垂尾开关开启失败: {_e}", flush=True)
    if os.environ.get("FLAPPER_STROKE_DIFF") == "1":
        try:
            import config.sim_config as _cfg_sd
            _cfg_sd.FlightControllerConfig.STROKE_DIFF_ON = True
            _cfg_sd.FlightControllerConfig.SD_ROLL_DIR = float(
                os.environ.get("FLAPPER_SD_DIR", "1.0"))
            print("[TEST] 逐拍差动开启", flush=True)
        except Exception as _e:
            print(f"[TEST] 逐拍差动开启失败: {_e}", flush=True)
    # 不定频率完全差动线（2026-09-27 阶段二，默认全关）：
    # FLAPPER_DUTY_MOD=1 占空比连续调制（与逐拍二值互斥且优先）；
    # FLAPPER_FREQ_DIFF=1 频率差动偏航通道。
    if os.environ.get("FLAPPER_DUTY_MOD") == "1":
        try:
            import config.sim_config as _cfg_dm
            _cfg_dm.FlightControllerConfig.DUTY_MOD_ON = True
            if os.environ.get("FLAPPER_DM_PGATE") == "1":
                _cfg_dm.FlightControllerConfig.DUTY_PITCH_GATE_ON = True
            if os.environ.get("FLAPPER_DM_COEXIST") == "1":
                _cfg_dm.FlightControllerConfig.DM_COEXIST_ON = True
            # 方向覆盖（2026-09-29 转弯线 R39 教训：原 SD_DIR 桥只在逐拍
            # 二值块内，占空比路径吃不到，R39 与 R38 逐位一致实录）
            if os.environ.get("FLAPPER_SD_DIR"):
                _v = float(os.environ["FLAPPER_SD_DIR"])
                print(f"[TEST] SD_ROLL_DIR 覆盖 "
                      f"{_cfg_dm.FlightControllerConfig.SD_ROLL_DIR}→{_v}",
                      flush=True)
                _cfg_dm.FlightControllerConfig.SD_ROLL_DIR = _v
            # 密度增益覆盖（2026-09-29 转弯线 R40 教训：单拍冲量约
            # 0.02 N·m·s 对 Ixx=0.012 给 Δω≈1.7 rad/s，超机动需求四倍，
            # DM_KP 须自 4.0 大幅调小）
            if os.environ.get("FLAPPER_DM_KP"):
                _v = float(os.environ["FLAPPER_DM_KP"])
                print(f"[TEST] DM_KP 覆盖 "
                      f"{_cfg_dm.FlightControllerConfig.DM_KP}→{_v}",
                      flush=True)
                _cfg_dm.FlightControllerConfig.DM_KP = _v
            if os.environ.get("FLAPPER_DM_KD"):
                _v = float(os.environ["FLAPPER_DM_KD"])
                print(f"[TEST] DM_KD 覆盖 "
                      f"{_cfg_dm.FlightControllerConfig.DM_KD}→{_v}",
                      flush=True)
                _cfg_dm.FlightControllerConfig.DM_KD = _v
            print("[TEST] 占空比连续调制开启（门控="
                  f"{_cfg_dm.FlightControllerConfig.DUTY_PITCH_GATE_ON} "
                  f"共存={_cfg_dm.FlightControllerConfig.DM_COEXIST_ON} "
                  f"方向={_cfg_dm.FlightControllerConfig.SD_ROLL_DIR}）",
                  flush=True)
        except Exception as _e:
            print(f"[TEST] 占空比调制开启失败: {_e}", flush=True)
    if os.environ.get("FLAPPER_DA3D") == "1":
        try:
            import config.sim_config as _cfg_da
            _cfg_da.FlightControllerConfig.DA_SCHED_3D_ON = True
            # 2026-09-29 转弯线：效能值与限幅的数值覆盖桥（不设时不
            # 触碰配置，行为与开关关闭态一致）。B_DA_3D 依据
            # docs/侧滑工况横侧通道效能.md 机翼幅值差动滚转效能
            # 0.0955 N·m/rad（dA 语义，钉固整周期实测）替换保守标定
            # 0.046；DA_MAX_3D 与 WING_MIX_DLAG_MAX 需同域放宽才能
            # 覆盖 dA 满幅的偏航副作用对消（1.0005×dA 上限）。
            if os.environ.get("FLAPPER_B_DA_3D"):
                _v = float(os.environ["FLAPPER_B_DA_3D"])
                print(f"[TEST] B_DA_3D 覆盖 "
                      f"{_cfg_da.FlightControllerConfig.B_DA_3D}→{_v}",
                      flush=True)
                _cfg_da.FlightControllerConfig.B_DA_3D = _v
            if os.environ.get("FLAPPER_DA_MAX_3D"):
                _v = float(os.environ["FLAPPER_DA_MAX_3D"])
                print(f"[TEST] DA_MAX_3D 覆盖 "
                      f"{_cfg_da.FlightControllerConfig.DA_MAX_3D}→{_v}",
                      flush=True)
                _cfg_da.FlightControllerConfig.DA_MAX_3D = _v
            if os.environ.get("FLAPPER_WING_MIX_DLAG_MAX"):
                _v = float(os.environ["FLAPPER_WING_MIX_DLAG_MAX"])
                print(f"[TEST] WING_MIX_DLAG_MAX 覆盖 "
                      f"{_cfg_da.FlightControllerConfig.WING_MIX_DLAG_MAX}→{_v}",
                      flush=True)
                _cfg_da.FlightControllerConfig.WING_MIX_DLAG_MAX = _v
            if os.environ.get("FLAPPER_DA3D_VMIN"):
                _v = float(os.environ["FLAPPER_DA3D_VMIN"])
                print(f"[TEST] DA3D_VMIN 覆盖 "
                      f"{_cfg_da.FlightControllerConfig.DA3D_VMIN}→{_v}",
                      flush=True)
                _cfg_da.FlightControllerConfig.DA3D_VMIN = _v
            if os.environ.get("FLAPPER_DA3D_TMIN"):
                _v = float(os.environ["FLAPPER_DA3D_TMIN"])
                print(f"[TEST] DA3D_TMIN 覆盖 "
                      f"{_cfg_da.FlightControllerConfig.DA3D_TMIN}→{_v}",
                      flush=True)
                _cfg_da.FlightControllerConfig.DA3D_TMIN = _v
            if os.environ.get("FLAPPER_DA3D_DIR"):
                _v = float(os.environ["FLAPPER_DA3D_DIR"])
                print(f"[TEST] DA3D_DIR 覆盖 "
                      f"{_cfg_da.FlightControllerConfig.DA3D_DIR}→{_v}",
                      flush=True)
                _cfg_da.FlightControllerConfig.DA3D_DIR = _v
            print("[TEST] 翼差动三维效能映射开启", flush=True)
        except Exception as _e:
            print(f"[TEST] 翼差动三维映射开启失败: {_e}", flush=True)
    # （波形参数环境桥已随 I-016 时序修正移至 5.6 段之前，见函数首段。）
    # 滚转执行量低通桥接（2026-09-29 转弯线 R32 教训：原桥接被包在
    # DA3D 开关块内，不开差动时静默失效——R32 与 R31 逐位一致实录；
    # 移出为无条件桥接，不设时不触碰配置）
    if os.environ.get("FLAPPER_DA_LPF_TAU"):
        try:
            import config.sim_config as _cfg_lpf
            _v = float(os.environ["FLAPPER_DA_LPF_TAU"])
            print(f"[TEST] DA_LPF_TAU 覆盖 "
                  f"{_cfg_lpf.FlightControllerConfig.DA_LPF_TAU}→{_v}",
                  flush=True)
            _cfg_lpf.FlightControllerConfig.DA_LPF_TAU = _v
        except Exception as _e:
            print(f"[TEST] DA_LPF_TAU 覆盖失败: {_e}", flush=True)
    # E2 增益档位桥接（2026-09-28 隔壁方案 E2 判别试验用）：环境变量
    # 不设时完全不触碰配置值（行为与基线逐位一致）
    for _gain_env, _gain_key in (("FLAPPER_KP_R", "KP_R"),
                                 ("FLAPPER_ROLL_DAMP", "ROLL_DAMP"),
                                 ("FLAPPER_ROLL_FILT_TAU", "ROLL_FILT_TAU"),
                                 ("FLAPPER_ROLL_DBG", "ROLL_DBG"),
                                 ("FLAPPER_ROLL_KI", "ROLL_KI"),
                                 ("FLAPPER_ROLL_LEAD_TD", "ROLL_LEAD_TD"),
                                 ("FLAPPER_ROLL_LEAD_BP_K",
                                  "ROLL_LEAD_BP_K"),
                                 ("FLAPPER_PITCH_LEAD_BP_K",
                                  "PITCH_LEAD_BP_K"),
                                 ("FLAPPER_ROLL_NOTCH_F",
                                  "ROLL_NOTCH_F"),
                                 ("FLAPPER_ROLL_STROKE_AVG",
                                  "ROLL_STROKE_AVG"),
                                 ("FLAPPER_ROLL_NOTCH_Q",
                                  "ROLL_NOTCH_Q"),
                                 ("FLAPPER_AIL_PROBE_AMP",
                                  "AIL_PROBE_AMP"),
                                 ("FLAPPER_ELEV_PROBE_AMP",
                                  "ELEV_PROBE_AMP"),
                                 ("FLAPPER_RUD_PROBE_AMP",
                                  "RUD_PROBE_AMP"),
                                 ("FLAPPER_MULTISINE_ON",
                                  "MULTISINE_ON"),
                                 ("FLAPPER_AIL_PROBE_FREQ",
                                  "AIL_PROBE_FREQ"),
                                 ("FLAPPER_FREQ_PROBE_AMP",
                                  "FREQ_PROBE_AMP"),
                                 ("FLAPPER_DA_PROBE_AMP",
                                  "DA_PROBE_AMP"),
                                 ("FLAPPER_ROLL_LEAD_FMOD",
                                  "ROLL_LEAD_FMOD"),
                                 ("FLAPPER_ROLL_LEAD_STAGES",
                                  "ROLL_LEAD_STAGES"),
                                 ("FLAPPER_PITCH_LEAD_TD",
                                  "PITCH_LEAD_TD"),
                                 ("FLAPPER_MODAL_CROSS_K",
                                  "MODAL_CROSS_K"),
                                 ("FLAPPER_ROLL_BP_K", "ROLL_BP_K"),
                                 ("FLAPPER_ATT_QUAT_ERR", "ATT_QUAT_ERR"),
                                 ("FLAPPER_ROLL_BP_F", "ROLL_BP_F"),
                                 ("FLAPPER_ROLL_I_MAX", "ROLL_I_MAX"),
                                 ("FLAPPER_COORD_I_MAX", "COORD_I_MAX"),
                                 ("FLAPPER_K_DA", "K_DA"),
                                 ("FLAPPER_K_DA_R", "K_DA_R"),
                                 ("FLAPPER_ROLL_TRIM_M", "ROLL_TRIM_M"),
                                 ("FLAPPER_PITCH_DAMP", "PITCH_DAMP"),
                                 ("FLAPPER_PITCH_FB_FILT_TAU",
                                  "PITCH_FB_FILT_TAU"),
                                 ("FLAPPER_YAW_DAMP", "YAW_DAMP"),
                                 ("FLAPPER_PITCH_INT_GATE", "PITCH_INT_GATE")):
        if os.environ.get(_gain_env):
            try:
                import config.sim_config as _cfg_g
                _v = float(os.environ[_gain_env])
                print(f"[TEST] 增益覆盖 {_gain_key}="
                      f"{getattr(_cfg_g.FlightControllerConfig, _gain_key)}→{_v}",
                      flush=True)
                setattr(_cfg_g.FlightControllerConfig, _gain_key, _v)
            except Exception as _e:
                print(f"[TEST] 增益覆盖失败 {_gain_env}: {_e}", flush=True)
    # 多正弦频率表桥接，事项 I-034。该键语义为逗号分隔频率列表，不走
    # 上方单一浮点通用桥。解析为浮点列表写入配置属性，与控制器侧
    # 直读路径在同一环境变量取值下产出同一组频率值。
    if os.environ.get("FLAPPER_MULTISINE_FREQS"):
        try:
            import config.sim_config as _cfg_ms
            _freqs = [float(x) for x in
                      os.environ["FLAPPER_MULTISINE_FREQS"].split(",")
                      if x.strip()]
            print(f"[TEST] 多正弦频率表覆盖 "
                  f"{getattr(_cfg_ms.FlightControllerConfig, 'MULTISINE_FREQS')}"
                  f"→{_freqs}", flush=True)
            _cfg_ms.FlightControllerConfig.MULTISINE_FREQS = _freqs
        except Exception as _e:
            print(f"[TEST] 多正弦频率表覆盖失败: {_e}", flush=True)
    if os.environ.get("FLAPPER_FREQ_DIFF") == "1":
        try:
            import config.sim_config as _cfg_fd
            _cfg_fd.FlightControllerConfig.FREQ_DIFF_ON = True
            _cfg_fd.FlightControllerConfig.FD_DIR = float(
                os.environ.get("FLAPPER_FD_DIR", "1.0"))
            # 滚转源档与增益覆盖（2026-09-29 转弯线：频率差动改做滚转
            # 执行的连续平滑通道，FLAPPER_FD_ROLL=1 启用滚转误差源）
            if os.environ.get("FLAPPER_FD_ROLL") == "1":
                _cfg_fd.FlightControllerConfig.FD_ROLL_SOURCE = True
            if os.environ.get("FLAPPER_FD_KP"):
                _v = float(os.environ["FLAPPER_FD_KP"])
                print(f"[TEST] FD_KP 覆盖 "
                      f"{_cfg_fd.FlightControllerConfig.FD_KP}→{_v}",
                      flush=True)
                _cfg_fd.FlightControllerConfig.FD_KP = _v
            if os.environ.get("FLAPPER_FD_KD"):
                _v = float(os.environ["FLAPPER_FD_KD"])
                print(f"[TEST] FD_KD 覆盖 "
                      f"{_cfg_fd.FlightControllerConfig.FD_KD}→{_v}",
                      flush=True)
                _cfg_fd.FlightControllerConfig.FD_KD = _v
            print("[TEST] 频率差动通道开启", flush=True)
        except Exception as _e:
            print(f"[TEST] 频率差动开启失败: {_e}", flush=True)
    _G["_SIDE_PULSE_ON"] = os.environ.get("FLAPPER_SIDESLIP_PULSE") == "1"
    _G["_SIDE_PULSE_T"] = float(os.environ.get("FLAPPER_SIDESLIP_PULSE_T", "5.0"))
    _G["_SIDE_PULSE_DVY"] = float(os.environ.get("FLAPPER_SIDESLIP_PULSE_DVY", "1.5"))
    _G["_SIDE_PULSE_DONE"] = False

    autopilot = None
    _ap_expected = False
    try:
        from config.sim_config import FLIGHT_CONTROLLER_ENABLED
        _ap_expected = bool(FLIGHT_CONTROLLER_ENABLED) and not EXPERIMENT_MODE
        # 试验模式下不创建飞控：试验排程通道对 dA/拍频/尾舵有完全权限，
        # 避免自动驾驶仪输出与试验指令争相写入（2026-09-19 固化此前的手工操作）
        if _ap_expected:
            from modules.flight_controller import FlightController
            autopilot = FlightController()
            # 控制器健康自检（2026-09-28，科目一重跑静默故障模式对策：
            # 补丁缩进错误曾致控制器导入失败被异常吞掉、自动驾驶仪缺席
            # 而仿真照常产数——docs/压坡度转弯_科目一修正重跑.md 登记）。
            # 创建后立即探针一次输出；失败即大声报错并终止。
            _probe = autopilot.update([0.0, 0.0, 1.5, 1.0, 0.0, 0.0, 0.0],
                                      [0.0] * 6, t_sim=0.0)
            if not _probe or abs(float(_probe.get("freq", 0.0))) < 1e-9:
                raise RuntimeError("控制器探针输出异常")
            print("[AUTO] 飞行控制器已启用，健康自检通过（手动杆量优先，"
                  "杆回中自动驾驶仪接管）", flush=True)
    except Exception as e:
        logger.warning("飞行控制器创建失败: %s", e)
        autopilot = None
        # 自动驾驶仪应为在环却缺席时，不允许静默产数（默认致命，
        # FLAPPER_ALLOW_NO_AP=1 显式豁免，如纯手动台架场景）
        if _ap_expected and not os.environ.get("FLAPPER_ALLOW_NO_AP"):
            raise SystemExit(
                "[AUTO-FAIL] 飞行控制器缺席：仿真将无控制产数，"
                "按静默故障对策终止；确需无控运行请设 FLAPPER_ALLOW_NO_AP=1")
    dashboard = None
    try:
        from config.sim_config import ENABLE_DASHBOARD, DASHBOARD_PORT
        if ENABLE_DASHBOARD:
            from modules.dashboard import DashboardServer
            dashboard = DashboardServer(port=DASHBOARD_PORT)
            dashboard.start()
            print(f"[DASH] 看板: http://localhost:{DASHBOARD_PORT}", flush=True)
    except Exception as e:
        logger.warning("看板启动失败: %s", e)
        dashboard = None
    logger.info("UDP 网络模块启动: 遥测(%d) | 指令(%d) | 循迹=%s | 关节波形(%s) | 行程(%s)",
                TELEMETRY_PORT, COMMAND_PORT, TRACKING_MODE,
                JOINT_SCOPE_PORT if joint_scope_sender else "-",
                CTRL_SCOPE_PORT if ctrl_scope_sender else "-")

    return SimpleNamespace(
        aero_callback=aero_callback,
        telemetry_sender=telemetry_sender,
        command_receiver=command_receiver,
        tracker=tracker,
        joint_scope_sender=joint_scope_sender,
        ctrl_scope_sender=ctrl_scope_sender,
        manual=manual,
        autopilot=autopilot,
        dashboard=dashboard,
        EXPERIMENT_MODE=EXPERIMENT_MODE,
        motion_recipe=motion_recipe,
    )
