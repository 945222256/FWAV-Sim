# ============================================================================
# 【 archived 】2.3m PHOENIX 气动几何整套配置快照 —— 2026-09-20 存档
# 内容：WingGeometryConfig 半展 1.15m/总面积 0.69m²（对标 HIT-Phoenix
#   2.3m/650g）+ 与之自洽的全部辨识真值（L̄=1.95f²、B_ELEV/B_dA、
#   FREQ_HOVER=1.90、PITCH_TRIM 等）+ 滑流盘面积 1.61m²。
# 原因：论文定案 SparrowHawk 翼展 0.6m（必读1 TII2026），2.3m 为上一代
#   平台参数误标。恢复方法：整文件覆盖回 config/sim_config.py。
# ============================================================================
"""
sim_config.py — Flapping-Wing (扑翼) MAV 仿真全局配置
=====================================================
从 isaac_usv_sim 的 config 演化而来：剥离全部水动力 / 波浪 / 尾迹参数，
改为扑翼 MAV 所需的空气动力学、姿态、翼面机构参数占位。

设计原则：
  • 路径 / 时间步 / 传感器 / 障碍物 / 网络配置 —— 直接沿用 USV 版（平台无关）
  • Fossen 水动力段 → 完全删除，替换为 AeroConfig 占位
  • 保留 SIM_APP_CONFIG 全套 Isaac Sim 启动参数
  • 所有单位 SI（米、千克、秒、弧度）
"""

import numpy as np

# ============================================================
# 1. 路径配置 (Path Configuration)
# ============================================================

# Isaac Sim 5.1.0 安装根目录
ISAAC_SIM_PATH: str = r"g:\Playground\isaac_sim_5.1.0"

# 扑翼 MAV 三维模型文件 (USD 优先；STEP/STL 兜底，需经 URDF/Mesh importer 转换)
# USD 未建：Isaac 侧走 URDF Importer 路线（用下方 FLAPPER_URDF_PATH）
FLAPPER_USD_PATH: str = r"g:\Playground\isaac_flapper_sim\assets\flapper\flapper_mav.usda"
# 被控样机（2026-09-15 定案）：对方组（徐文福组）提供的 fwav_15dof
# 浮动基座6 + 驱动关节9：flap_L/R(±x) twist_L/R(+y攻角) fold_L/R(±z折叠)
# tail_yaw + tail_L/tail_R(差动舵面)。REP-103 x前y左z上，总重~430g。
# 我方早期 flapper_15dof.urdf（15全驱动，y前坐标）留作生成器工具，已弃用。
FLAPPER_URDF_PATH: str = r"g:\Playground\isaac_flapper_sim\assets\flapper\fwav_15dof_from_group.urdf"
# URDF Importer 转换产物（tools/import_fwav_urdf.py 生成，fix_base=False 浮动基座版）
FLAPPER_URDF_USD: str = r"g:\Playground\isaac_flapper_sim\assets\flapper\fwav_15dof.usd"
# 关节样机驱动增益（700g 翼惯量标定：ωn≈300rad/s ζ≈1，D=3 会重过阻尼跟不住 2.5Hz）
JOINT_DRIVE_STIFFNESS: float = 60.0
JOINT_DRIVE_DAMPING: float = 0.25

# 拍动驱动参数（WingKinematics，与 tools/preview_fwav_15dof.py 一致）
# flap_L=+φ / flap_R=-φ（两轴同向+x，镜像翼反号）；twist 同号=前飞攻角；fold 限位已镜像
WING_DRIVE_CONFIG = {
    "frequency": 2.5,        # Hz（700g/2.3m 级大翼；与 FlapKinematicsConfig 同步改）
    "amplitude_deg": 35.0,   # 拍幅（与 FlapKinematicsConfig.AMPLITUDE 同步）
    "pitch_mode": "aero",    # 附着流前飞：攻角约束在 alpha_set
    "alpha_set_deg": 12.0,
    "fold_amp": 0.45,        # 折叠幅度 rad（上拍段折翼）
    "tail_yaw_amp": 0.12,    # 尾摆小幅摆动（演示用；控制律接管后置零）
    "tail_trim": 0.08,       # 尾舵配平
}

# 地形 USD（地面 / 森林场景）。留空则使用程序化默认地面平面。
TERRAIN_USD: str = r"g:\Playground\isaac_flapper_sim\assets\forest\forest_scene.usda"
TERRAIN_USD_PATH: str = TERRAIN_USD

# Stage 中的 prim 路径
FLAPPER_PRIM_PATH: str = "/World/Flapper/MAV"
TERRAIN_PRIM_PATH: str = "/World/Environment/Terrain"

# 数据输出目录
DATA_DIR: str = r"g:\Playground\isaac_flapper_sim\data"
LOG_DIR: str = r"g:\Playground\isaac_flapper_sim\logs"

# ============================================================
# 2. 环境物理参数 (Environment Physics)
# ============================================================

# 地面高度 (Ground level, m) —— 扑翼起飞基准面
GROUND_LEVEL: float = 0.0

# 地面平面尺寸 (Ground plane size)
GROUND_PLANE_SIZE: float = 400.0
GROUND_PLANE_THICKNESS: float = 0.01

# 地面材质参数
GROUND_COLOR: tuple = (0.25, 0.35, 0.18)   # 草地绿
GROUND_ROUGHNESS: float = 0.9
GROUND_METALLIC: float = 0.0

# 重力 (m/s²)
GRAVITY: list = [0.0, 0.0, -9.81]

# 空气参数 (Air properties)
AIR_DENSITY: float = 1.225      # kg/m³ (海平面 15°C)
AIR_KINEMATIC_VISCOSITY: float = 1.46e-5  # m²/s

# 风场参数 (Wind field) —— 扑翼必填，USV 没有
# 注意：生效的 WIND_ENABLED / WIND_VECTOR 定义在第 8d 节（约 388 行），
# 此处历史上重复定义过一次 WIND_ENABLED，2026-09-19 已清除该重复条目。
WIND_MEAN_VELOCITY: np.ndarray = np.array([1.0, 0.0, 0.0])   # m/s（遗留，未被使用）
WIND_TURBULENCE_INTENSITY: float = 0.3   # 湍流强度 (0~1)

# 光照 (Lighting) —— 与 USV 版一致
DOME_LIGHT_INTENSITY: float = 1000.0
SUN_LIGHT_INTENSITY: float = 3000.0
SUN_LIGHT_ANGLE: float = 1.0

# Prim 路径 (Stage prim paths)
ENV_ROOT_PATH: str = "/World/Environment"
GROUND_PATH: str = "/World/Environment/Ground"
DOME_LIGHT_PATH: str = "/World/Environment/DomeLight"
SUN_LIGHT_PATH: str = "/World/Environment/SunLight"

# ============================================================
# 3. 仿真时间步长 (Simulation Timesteps)
# ============================================================

# 物理引擎更新频率 120 Hz (扑翼拍动频率高，需要细步)
PHYSICS_DT: float = 1.0 / 120.0

# 渲染帧率 30 Hz
RENDER_DT: float = 1.0 / 30.0

# ============================================================
# 4. 扑翼 MAV 几何与质量参数 (Geometry & Mass)
# ============================================================

# 翼展 (Wingspan, m) —— 总翼展 = 2 × SPAN
WINGSPAN: float = 0.60
# 翼弦长 (Mean chord, m)
WING_CHORD: float = 0.10
# 翼面积 (单翼, m²)
WING_AREA: float = 0.030

# 机体尺寸 (Body dimensions, m)
BODY_LENGTH: float = 0.35
BODY_WIDTH: float = 0.07
BODY_HEIGHT: float = 0.07

# 总质量 (Total mass, kg) —— 700g 量级（对标 HIT-Phoenix 2.3m/650g 一档）
MASS: float = 0.700

# 初始位姿 — 地面上方 1m，准备起飞
INITIAL_POSITION: np.ndarray = np.array([0.0, 0.0, 1.5])  # 系留悬吊位（锚3.0-绳1.5）
INITIAL_ORIENTATION: np.ndarray = np.array([1.0, 0.0, 0.0, 0.0])  # (w,x,y,z)

# ============================================================
# 5. 扑翼气动力学参数 (Aerodynamics) —— Sane & Dickinson (2002) 三分量模型
# ============================================================
# USV 原来这里是 Fossen 3-DOF 水动力（M/C/D 矩阵、附加质量、阻尼、浮力点）。
# 扑翼完全不同，使用非定常空气动力学：总气动力 = 平动力 + 旋转力 + 附加质量力。
# 具体实现在 modules/aero_solver.py。方程来源见 docs/AERO_MODEL.md。

# 选用的气动力学模型（三流型，modules/aero/ 包内实现）
#   attached_flow   — 路线A：高Re大展弦比、附着流环量主导（Wagner/Duhamel + 升力线AR修正）
#   dynamic_stall   — 路线B：大攻角受控动态失速（Leishman-Beddoes简化三状态：分离点/涡环量/涡脱）
#   sane_dickinson  — 路线C：低Re小展弦昆虫式（Robofly极曲线，蜂鸟/RoboBee区间）
AERO_MODEL: str = "sane_dickinson"   # attached_flow | dynamic_stall | sane_dickinson
# 2026-09-18 扫频辨识结论：attached_flow/dynamic_stall 静气悬停升力 ≤0.4 倍体重
# （俯仰符号逐半拍翻转导致升力自相抵消，路线 A/B 是前飞模型）；
# sane_dickinson 离线 L̄(2.5Hz)=1.8 倍体重，悬停区间落在 1.5~3.5Hz 手操量程内。


class FlapKinematicsConfig:
    """拍动运动学参数 (Flapping kinematics)。
    拍动角 φ(t) = bias + amp·sin(ω·t + phase)，ω=2π·f

    俯仰模式 PITCH_MODE:
      "flip" — α(t) = bias + amp·cos(ω·t+phase)，与 φ̇ 同号（昆虫悬停翻转俯仰）
      "aero" — α(t) 主动合成，使有效攻角稳定在设定值 α_set（附着流前飞，
               攻角限幅在失速角内，A/B 两种高AR流型用这个）
    """
    FREQUENCY: float = 2.5           # Hz —— 700g/2.3m 大翼低频；蜂鸟式为25~40
    AMPLITUDE: float = 35.0          # 度，拍动角单侧峰值（700g标定：50°→2.4x超升，35°达标）
    BIAS: float = 0.0                # 度，拍动角偏置
    FEATHER_AMPLITUDE: float = 40.0  # 度，翼面俯仰幅度(flip 模式用)
    FEATHER_BIAS: float = 0.0        # 度，翼面俯仰偏置(flip 模式用)
    PITCH_MODE: str = "aero"         # flip | aero
    ALPHA_SET: float = 15.0          # 度（15°=附着流边界；20°曾致崩但根因是签名bug，可再试）
    PHASE_OFFSET_LR: float = 0.0     # 度，左右翼相位差
    SPLIT: float = 0.56               # 二分排查
    PITCH_LAG_S: float = -0.02        # 俯仰超前 20ms（协同扫参：L̄=9.8N=1.43倍体重, T̄=0.96N）
    MEAN: float = 0.0                # 均值位置角 μ (度)：扑动平面上偏置


class WingGeometryConfig:
    """翼面几何参数 (Wing geometry)。单翼。
    700g 量级（2026-09-16 重标，对标 HIT-Phoenix 2.3m/650g 一档）：
    总展 2.3m，平均弦 0.30m → 单翼面积 0.345 m²，总面积 0.69 m²，AR ≈ 7.7。
    注意：样机 URDF 视觉仍是 0.5m 紧凑机构——气动模型与视觉几何暂不一致，
    待对方组提供 700g 级样机 URDF 后统一。
    """
    SPAN: float = 1.15              # 半展长 (m)，单侧；总翼展 2.30m
    ROOT_CHORD: float = 0.35        # 翼根弦长 (m)
    TIP_CHORD: float = 0.25         # 翼尖弦长 (m)
    N_STRIPS: int = 14              # 沿展向叶素数量
    ROOT_OFFSET: np.ndarray = np.array([0.0, 0.0, 0.0])   # 翼根相对 CG 的偏移
    HINGE_AXIS: str = "Y"           # 拍动铰链轴（绕 Y 轴拍动）


class HighARWingConfig:
    """高展弦比翼附加参数（路线A/B 共用，升力线理论用）。"""
    # 升力线理论有限展弦比修正：有效升力线斜率 a = a0/(1 + a0/(π·AR))
    LIFT_SLOPE_2D: float = 2.0 * np.pi   # a0，薄翼理论 2π (1/rad)
    OSWALD_EFFICIENCY: float = 0.85      # 升致阻力效率 e（椭圆升力分布≈0.9~1.0）
    PROFILE_DRAG_COEFF: float = 0.02     # 型阻系数 CD0（薄翼低阻翼型）


class AttachedFlowConfig:
    """路线A：附着流环量模型参数（Wagner/Duhamel 时域实现）。

    物理来源：
      • 定常薄翼环量升力 L' = ρ·U·Γ，Γ = π·b·U·α_eff（2π 升力线）
      • Wagner 函数：翼从静止起动，环量经 Φ(τ) 渐近建立
        Φ(τ) ≈ 1 - 0.165·e^(-0.0455τ) - 0.335·e^(-0.3τ)，τ = 2Ut/c
        时域用 Duhamel 积分 → 两个一阶滞后状态（Jones 两项逼近）
      • 非环量惯性力（薄翼附加质量）：F_nc = ρ·π·b²·a_n per unit span
      • 失速保护：α_eff 硬限幅在 α_stall 内（附着流假设）
    """
    # Jones 两项 Wagner 逼近系数
    WAGNER_A1: float = 0.165        # 第一项幅值
    WAGNER_T1: float = 0.0455       # 第一项时间常数（半弦无量纲）
    WAGNER_A2: float = 0.335        # 第二项幅值
    WAGNER_T2: float = 0.3          # 第二项时间常数
    ENABLE_WAGNER: bool = True      # False 时退化为纯准定常
    ENABLE_NONCIRC: bool = True     # 非环量惯性力
    ALPHA_STALL_DEG: float = 22.0   # 失速攻角（放宽：受控动态失速区，配套 α_set 提升）
    # 翼面拍动运动学速度上限（防数值爆炸）
    MAX_WING_VEL: float = 60.0      # m/s


class DynamicStallConfig:
    """路线B：Leishman-Beddoes 简化动态失速模型参数。

    三状态：
      1. 分离点 f ∈ [0,1]（1=全附着，0=全分离）
         静态曲线 f_ss(α)，动态一阶滞后 τ_f
      2. 涡环量 C_v：α_eff 超过动态失速角 α_ds 后累积（时间常数 τ_v）
      3. 涡脱计时：无量纲涡寿命 τ_vl，到期后 C_v 指数衰减（受控涡脱）

    法向力合成：
      C_n = f^0.5·C_n,att + (1-f^0.5)·C_n,fs + C_v + C_n,nc

    参数量级取自 Leishman & Beddoes 1989 (NACA0012 振荡实验标定)。
    """
    CN_ALPHA: float = 5.73          # 法向力斜率 dCn/dα (1/rad，NACA0012≈5.73)
    ALPHA_DS_DEG: float = 13.5      # 动态失速触发角（度）
    # 分离点静态曲线 f_ss = 1 - exp((α - α_ds)/S)，α<α_ds 时 f_ss→1（全附着）
    SEPARATION_S_DEG: float = 4.0   # 分离过渡宽度（度）
    TAU_F: float = 1.7              # 分离点滞后时间常数（半弦无量纲）
    TAU_V: float = 6.0              # 涡环量增长时间常数（半弦无量纲）
    TAU_VL: float = 11.0            # 涡寿命（半弦无量纲，超时脱落）
    VORTEX_DECAY: float = 0.5       # 涡脱后 C_v 衰减率（1/半弦无量纲时间）
    ENABLE_VORTEX: bool = True      # False 时退化为纯附着流+分离滞后
    MAX_WING_VEL: float = 60.0


class AeroModelConfig:
    """Sane & Dickinson (2002) 准定常三分量模型参数（路线C，昆虫式）。
    Robofly 实测极曲线系数（C_N, C_T, C_L, C_D 的拟合常数），
    取自 Sane & Dickinson 2002 J. Exp. Biol. 205:1087-1096。
    """
    RHO_AIR: float = 1.225          # 空气密度 kg/m³（海平面 15°C）

    CN_FIT_AMP: float = 3.59        # C_N 拟合振幅
    CN_FIT_PHASE_DEG: float = 9.1   # C_N 拟合相位偏移（度）
    CT_FIT_AMP: float = 1.86        # C_T 拟合振幅
    CT_FIT_OFFSET: float = 0.86     # C_T 拟合常数项

    C_ROT: float = 1.55             # 旋转力系数 (无量纲，经验值)
    ROT_REF_CHORD_FRAC: float = 0.5 # 旋转力参考点（翼弦位置 0~1，0.5=半弦）

    ADDED_MASS_COEFF: float = 0.7   # 附加质量系数（经验，0.5~1.0）
    # 线性旋转阻尼（2026-09-19 二调，默认曾 0.6）：反阻尼源（ω×r 耦合）已
    # 整体移除，阻尼只剩物理保险职能；0.6 在真实惯量（Ixx≈2.4e-3）上是
    # 4ms 时间常数，50~120Hz 显式积分（harness/控制环）数值失稳成 ±3rad/s
    # 极限环（harness 逐环节打印实测）。0.1 对两档采样率都稳定
    WING_ROT_DAMP: float = 0.1

    ENABLE_TRANSLATIONAL: bool = True       # 平动力（必开，主导项）
    ENABLE_ROTATIONAL: bool = True          # 旋转力
    ENABLE_ADDED_MASS: bool = True          # 附加质量力

    MAX_WING_VEL: float = 50.0      # m/s，单叶素相对速度上限


# 模块级快捷别名（向后兼容旧引用）
FLAP_FREQUENCY: float = FlapKinematicsConfig.FREQUENCY
FLAP_AMPLITUDE: float = FlapKinematicsConfig.AMPLITUDE
FEATHER_AMPLITUDE: float = FlapKinematicsConfig.FEATHER_AMPLITUDE
FLAP_PHASE_OFFSET: float = FlapKinematicsConfig.PHASE_OFFSET_LR
AIR_DENSITY: float = AeroModelConfig.RHO_AIR


# ============================================================
# 6. 传感器配置 (Sensor Configuration) —— 直接沿用 USV 版
# ============================================================

ENABLE_SENSORS: bool = True
RECORD_SENSOR_DATA: bool = False
ENABLE_LIDAR: bool = True
ENABLE_STEREO: bool = True
ENABLE_SURROUND_CAMERAS: bool = False
ENABLE_TOPDOWN_CAMERA: bool = False
ENABLE_BEV_STITCH: bool = False
ENABLE_HUD_CAMERA: bool = False

# 场景模式
SCENE_MODE: str = "default"   # default | hud_avoid | forest_nav

# ============================================================
# 7. 障碍物配置 (Obstacles) —— 沿用 USV 版，改成"柱状树干/柱子"
# ============================================================

# 静态柱状障碍 (Static cylindrical obstacles / tree trunks)
STATIC_OBSTACLE_COUNT: int = 20
STATIC_OBSTACLE_RADIUS: float = 80.0
BUOY_DEFAULT_RADIUS: float = 0.3      # 圆柱半径(m) —— 既是浮标也是柱子
BUOY_DEFAULT_HEIGHT: float = 4.0      # 圆柱高度(m) —— 树干高度
BUOY_COLORS = [
    (0.35, 0.22, 0.12),   # 树皮棕
    (0.45, 0.30, 0.15),
    (0.25, 0.18, 0.10),
    (0.40, 0.25, 0.13),
]

# 动态障碍物 (Dynamic obstacles — 飞鸟/其他 MAV)
DYNAMIC_OBSTACLE_COUNT: int = 0       # 默认关，需要时开启
DYNAMIC_OBSTACLE_RADIUS: float = 60.0
DYNAMIC_OBSTACLE_SIZE = (0.3, 0.3, 0.3)   # m
DYNAMIC_OBSTACLE_SPEED_MIN: float = 1.0
DYNAMIC_OBSTACLE_SPEED_MAX: float = 5.0
DYNAMIC_OBSTACLE_BOUND: float = 150.0

# ============================================================
# 8. 网络配置 (UDP Control Bridge) —— 直接沿用 USV 版
# ============================================================

TELEMETRY_PORT: int = 9001
COMMAND_PORT: int = 9002

# 关节示波器（在线看电机控制波形）：main_sim 以 30Hz 把关节实际角+目标角
# 发到本机 9010；tools/joint_scope.py 绑同端口实时画波形（虚线=目标，实线=实际）
ENABLE_JOINT_SCOPE: bool = True
JOINT_SCOPE_PORT: int = 9010
JOINT_SCOPE_WINDOW_S: float = 8.0   # 示波器时间窗（秒）

# 手动行程示波器：四通道杆量 + 映射输出（频率/尾舵），tools/ctrl_scope.py
ENABLE_CTRL_SCOPE: bool = True
CTRL_SCOPE_PORT: int = 9011

# Web 看板（Isaac 实时影子，浏览器开 http://localhost:8090）
ENABLE_DASHBOARD: bool = True
DASHBOARD_PORT: int = 8090

# ============================================================
# 8b. 尾翼/机身附加气动（控制面 + 阻力开关）
# ============================================================

class TailAeroConfig:
    """尾翼平板气动：俯仰配平（同向舵）+ 滚转（差动舵）+ 偏航（摆尾）。"""
    AREA: float = 0.030        # m² 双舵面总面积（700g 级）
    LEVER_ARM: float = 0.45    # m 尾压心到 CG（沿机身后向）
    PANEL_Y: float = 0.15      # m 舵面半展（差动滚转力臂）
    CN_SLOPE: float = 3.5      # 法向力系数斜率 (1/rad)，平板小角
    STALL_ANGLE: float = 20.0  # 度，法向力钳位角

ENABLE_FUSELAGE_DRAG: bool = True       # 机身阻力开关
FUSELAGE_DRAG_COEFF: float = 0.8        # 圆柱迎风阻力系数
FUSELAGE_FRONTAL_AREA: float = 0.0038   # m²（r=0.035m 圆柱正面积）

# ── 拍动滑流（2026-09-19 运控建模线新增，解决悬停俯仰/偏航无舵面权限问题）──
# 真机的尾翼在悬停时浸泡在拍动下洗/滑流里（Robofly/SparrowHawk 均如此），
# 本模型此前无此项 → 静气悬停时尾舵 authority 为零、俯仰通道双积分器失控。
# 一阶动量理论：诱导速度 v_i = sqrt(T̄/(2ρA))，T̄=1.95f²（台架辨识值），
# A=双翼扫掠面积 2×½R²Φ（R=1.15m，Φ=2×0.61rad）→ v_i ≈ 0.70·f m/s。
# 尾翼有效速压按能量叠加：v_eff² = v_air² + v_slip²。
# ⚠️ SLIP_K=1.0 是一阶理论值，量级未经台架/真机标定（Real2Sim 待定项）。
# 2026-09-19：滑流重开+标定。实测权限刻度（升降舵 ±0.5rad 的俯仰力矩）：
# v_slip=1.33→±0.018 N·m；4→±0.162；8→±0.647。悬停俯仰刚度只能来自尾翼
# （机翼模型俯仰力矩结构上恒零），SLIP_K=5.5 给出 v_slip≈7.35、权限
# ±0.54 N·m，对应真机翼尖滑流量级（~8 m/s）。待 Real2Sim 精标。
ENABLE_TAIL_SLIPSTREAM: bool = True
SLIP_DISK_AREA: float = 1.61      # m² 双翼扫掠面积
SLIP_V_PER_HZ: float = 0.703      # m/s/Hz = sqrt(1.95/(2·1.225·1.61))
SLIP_K: float = 4.0               # 滑流标定系数（资产修复后二分重测：翼面
                                  # 质量 127g→54g 预期扰动缩 3 倍，4.0×0.703×
                                  # 1.9≈5.3 m/s → 权限约 ±0.2 N·m 对残差）

# ============================================================
# 8c. 手动输入（摇杆/键盘 → 四通道，映射见 main_sim._drive_wing_joints）
# ============================================================

MANUAL_INPUT_ENABLED: bool = True
MANUAL_FREQ_MIN: float = 1.5    # Hz 油门最低拍频
MANUAL_FREQ_MAX: float = 3.5    # Hz 油门最高拍频
MANUAL_ELEV_GAIN: float = 0.35  # rad 升降舵满杆偏角（同向）
MANUAL_AIL_GAIN: float = 0.30   # rad 差动舵满杆偏角
MANUAL_RUDD_GAIN: float = 0.40  # rad 摆尾满杆偏角
MANUAL_KEY_RATE: float = 1.5    # 键盘模式杆量变化速率 (1/s)

# 视口跟踪相机：主视口始终对准机器人（眼点 = 机器人位置 + 偏移）
VIEWPORT_FOLLOW: bool = True
VIEWPORT_OFFSET = (4.5, -4.5, 2.0)   # 相机相对机器人的偏移 (m)

# ============================================================
# 8d. 系留台架 + 定高 DP（悬停试验第一步；绳力/DP 全部走力回调，不写位姿）
# ============================================================

# 系留绳：软绳，仅当 |p-锚|>绳长 时产生拉力+阻尼（只拉不推，模拟真绳）
TETHER_ENABLED: bool = False
TETHER_ANCHOR = (0.0, 0.0, 3.0)   # 锚点（世界系）
TETHER_LENGTH: float = 1.5        # 绳长 (m)
TETHER_K: float = 60.0            # 绳刚度 (N/m)
TETHER_C: float = 30.0            # 绳阻尼 (N·s/m)（10→30：接住上冲机体时的
                                  # 瞬时冲击近似减半，泵能回路的放大环节之一）

# 定高 DP：高度误差 PD → 拍频（升力∝f²）。手动油门偏离中位时 DP 让位。
# 【甲项隔离实验 2026-09-20，临时】关闭飞控与定高，实验后恢复 True
DP_HEIGHT_HOLD: bool = True
DP_TARGET_Z: float = 1.5          # 目标高度 (m)
DP_KP: float = 1.5                # Hz/m
DP_KD: float = 1.2                # Hz/(m/s)
DP_FREQ_NOMINAL: float = 1.90     # 悬停配平拍频（原 2.5 落在限幅窗 [1.7,2.2]
                                  # 之外，输出恒贴 2.2 上限超推、环路停在静差
                                  # 点；与 FREQ_HOVER 同源，取辨识悬停值）
# DP 防泵能三件套（2026-09-19）：输入低通 + 输出限幅在悬停邻域 + 输出斜坡。
# 泵能实录见 docs/IDENT_20260919.md 第六节：DP 读瞬时速度会把拍频振荡直接
# 放大成 1.5↔3.5Hz 的油门猛打，弹射-绳拽-再弹射。
DP_FREQ_MIN: float = 1.7          # Hz DP 输出下限（升力≈5.6N，低于体重，不托底过猛）
DP_FREQ_MAX: float = 2.2          # Hz DP 输出上限（升力≈9.4N=1.37 倍体重，不弹射）
DP_SLEW: float = 0.8              # Hz/s DP 输出变化率限制
DP_FILT_TAU: float = 0.3          # s DP 输入 z/vz 的 EWMA 时间常数

# 拍幅软启动：拍幅/扭转/折叠从 0 斜坡到全幅的时长（消除 t=0 驱动冲击）
STARTUP_AMP_RAMP_S: float = 0.5   # s
# 起飞先钉后放（2026-09-19 二次启用）：内环低通+增益软化后环已稳定
# （205202 run：不再飞丢、翻滚后有界恢复），但拍幅全幅瞬间机翼驱动反
# 作用仍给 −30rad/s 级冲击（0.17N·m 量级）超出软化环的响应能力。钉固
# 渡过软启动窗口，释放时只剩 ~−2.7rad/s 冲击，软化环可直接吸收
# 【甲项隔离实验 2026-09-20，临时】全钉固+无控制器+恒频2.5Hz：定位单翼
# 驱动失效是否与控制器/机体运动无关。实验后恢复 0.9
STARTUP_PIN_S: float = 0.9

# 来流风（风洞式系留试验用；气动按相对风速 v_air = v_body - wind 计算）
# 2026-09-19 第一门（静气悬停验收）：WIND_ENABLED=False。5m/s 迎风 +
# 漂移会把空速推到包线外（在环实测爬到 136m）；静气是变量最少的验收
# 环境。第三门再回到迎风（试验模式仍由 MODE_DEFAULTS 按模式覆盖）
WIND_ENABLED: bool = False
WIND_VECTOR = (-5.0, 0.0, 0.0)    # m/s（负号=迎面来流！之前正号成了顺风，dA力矩符号因此翻转）

# 正常飞行时也落 30Hz 数据流（飞控闭环在环测试用；文件命名 flight_<ts>）
FLIGHT_STREAM_LOG: bool = True

# 自动试验（modules/experiment.py；tools/run_ident.py 记录拟合）。""=关
EXPERIMENT_MODE: str = ""   # 2026-09-19 全部辨识完成，恢复关闭    # "" | "sweep" | "rudder" | "dastep" | "trim" | "windcal" | "kick_roll" | "kick_pitch" | "kick_yaw" | "kick_pitch_x" | "rate_roll" | "rate_pitch" | "rate_yaw" | "stepx" | "rudder_v"
EXP_SETTLE_S: float = 8.0         # 试验前稳态时间
EXP_SWEEP_FMIN: float = 1.5       # 扫频下限 (Hz)
EXP_SWEEP_FMAX: float = 3.5       # 扫频上限 (Hz)（抖动大收到 3.0）
EXP_SWEEP_STEP: float = 0.25      # 扫频步长 (Hz)
EXP_DWELL_S: float = 10.0          # 每频率点最短驻留
EXP_MIN_CYCLES: int = 10          # 每频率点至少扑动周期数
EXP_STEP_ELEV: float = 0.10       # 升降舵阶跃幅值 (rad)
EXP_STEP_AIL: float = 0.10        # 差动舵阶跃幅值 (rad)
EXP_STEP_RUD: float = 0.15        # 摆尾阶跃幅值 (rad)
EXP_STEP_S: float = 6.0           # 阶跃保持 (s)
# 翼差动舵效试验（dastep）：B_dA(f) 辨识，滚转主通道真值
EXP_DA_FREQS = (1.9, 2.5, 3.0)    # 拍频点 (Hz)
EXP_DA_AMPS = (0.06, 0.12, 0.18)  # dA 阶跃幅值 (rad)
EXP_DA_STEP_S = 5.0               # 每阶保持 (s)
EXP_DA_GAP_S = 3.0                # 回零间隔 (s)
EXP_DA_SETTLE_S = 5.0             # 每频率点稳定 (s)
EXP_GAP_S: float = 4.0            # 阶跃间隔回零 (s)

# ── 台架扩展（2026-09-19 运控建模线：配平/风速/单轴释放试验）──
BENCH_FREE: tuple = ()            # 台架释放轴，子集 of ("roll","pitch","yaw","x")；
                                  # 由 experiment.MODE_DEFAULTS 按模式自动覆盖
BENCH_PITCH_DEG: float = 0.0      # 台架俯仰偏置（trim 试验逐段改写，度）
# trim：俯仰偏置扫阶（钉固其余自由度，静气悬停拍频）
EXP_TRIM_PITCHES = (0.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0)   # deg
EXP_TRIM_STEP_S: float = 5.0      # 每角度保持 (s)
EXP_TRIM_GAP_S: float = 2.0       # 回零间隔 (s)
# windcal：来流速度扫阶（钉固台架，验证 Fx(v)/L̄(v) 速压律）
EXP_WIND_SPEEDS = (0.0, 1.5, 3.0, 5.0, 6.5, 8.0)            # m/s 迎面
EXP_WIND_STEP_S: float = 6.0      # 每风速驻留 (s)
# kick_*：单轴释放衰减试验（初始角速率脉冲 → 自由衰减 → 阻尼导数）
# ⚠️ 2026-09-19 实测：本模型瞬时反阻尼窗口（周期内 41~48%）在自由释放下
# 会相位锁定泵能至限幅极限环，自由衰减法不可用 → 改用 rate_* 定速台架
EXP_KICK_SETTLE_S: float = 8.0    # 释放前钉固稳态 (s)
EXP_KICK_FREE_S: float = 12.0     # 释放后自由衰减记录 (s)
EXP_KICK_W: float = 0.6           # 初始角速率脉冲 (rad/s)
EXP_KICK_HOLD_S: float = 0.3      # 脉冲段时长 (s)
# rate_*：定速台架（运动学处方角速率 → 测周期平均力矩斜率 = 阻尼导数）
EXP_RATES = (0.0, 0.25, -0.25, 0.5, -0.5, 1.0, -1.0, 2.0, -2.0)  # rad/s
EXP_RATE_SETTLE_S: float = 6.0    # 定速前钉固稳态 (s)
EXP_RATE_STEP_S: float = 4.0      # 每速率驻留 (s)
# rudder_v：尾翼舵效速压调度（第 4 条），逐风速点三通道阶跃
EXP_RUDV_SPEEDS = (0.0, 3.0, 5.0, 8.0)   # m/s 迎面（0 为原点锚定）



# ============================================================
# 8e. 飞行控制器（modules/flight_controller.py，串级PID+TECS-lite）
# ============================================================

class FlightControllerConfig:
    """增益起点：舵效直测(2026-09-17) + 惯量 ~0.015 kg·m²。系留调参后放飞。"""
    # 舵效（N·m/rad，分项力矩直测）
    B_ELEV: float = 0.724    # 升降舵→俯仰（2026-09-18 钉态辨识=解析值）
    B_AIL: float = 0.241     # 差动→滚转（同上；旧值1.0系测量伪影）
    B_RUD: float = 0.723     # 摆尾→偏航（同上）
    # 姿态环
    KP_A: float = 1.2        # 角度外环 (1/s)（2.0→1.2：真实惯量失配下软化；
                              # 6.0 曾引起 bang-bang 极限环）
    KP_R: float = 0.25       # 角速度内环（0.5→0.25 软化）：俯仰通道是零自然阻尼
                             # 双积分器（M_q≡0），速率环是唯一阻尼源；
                             # KP_R·B_tail(v_eff)/I ≈ 3 1/s 悬停阻尼（旧值 0.02
                             # 系 9-17 伪影增益下的拍脑袋降额，等效阻尼≈0.12 1/s）
    DELTA_MAX: float = 0.22  # 舵偏限幅 (rad)
    # TECS-lite
    KP_Z: float = 1.5        # 高度→拍频 (Hz/m)
    KD_Z: float = 0.7        # (Hz/(m/s))（1.2→0.7：+0.5m/s 释放冲量被旧值
                             # 砍到拍频下限，掉高-1.4m/s 建立后斜坡追不上）
    KP_V: float = 0.8        # 速度误差→加速度需求 (1/s)（1.5 在环更差已回退）
                             # 2026-09-19 由 0.10 上调——原值使速度环 τ=10s，
                             # 比位置外环还慢，串级带宽倒挂致位置振荡发散）
    # 位置外环
    KP_XY: float = 0.40      # 位置误差→期望速度 (1/s)（0.15→0.40 同上提速；
                             # 外环带宽仍 ≪ 速度环）
                             # 0.3 1/s；旧值 0.8 是内环的 8 倍，闭环实测发散）
    KI_XY: float = 0.15      # 位置积分 (1/s²)——消 Fx(θ) 截距残差的常值静差
                             # （0.08→0.15 提速）
    XI_MAX: float = 5.0      # 积分限幅 (m·s)，pitch 触限时冻结积分。
                             # 截距残差~0.09N≈0.01rad 俯仰当量，积分权限需
                             # 覆盖（旧值 2.0 实测差一口气，爬行 0.08m/s 不止）
    TILT_MAX: float = 0.35   # 期望姿态角限幅 (rad)（0.45 在环更差已回退）
                             # 悬停配平前倾 0.206 rad + 机动余量，否则速度环无权限）
    # 设定点（系留锚点附近定点悬停，顶 5m/s 风）
    X_REF: float = 0.0
    Y_REF: float = 0.0
    FREQ_SLEW: float = 2.5   # Hz/s 拍频斜坡（对称限幅。相位已改积分状态，
                             # 跳相不再存在，斜坡可以提速；1.0 实测追不上
                             # 释放冲量掉高——harness 冲量场景 4.9s 坠地）
    K_DA: float = 0.04         # 翼差动角增益（0.12→0.04：Ixx≈1.7e-3 下防
                               # 单步把速率甩过阻尼反转门槛）
    K_DA_R: float = 0.008      # 翼差动速率阻尼（对 120Hz 控制率留离散裕度）
    DA_MAX: float = 0.08       # 翼差动限幅（满偏≈0.9N·m≈单步5rad/s，仍粗）
    Z_REF: float = 1.5
    YAW_REF_DEG: float = 0.0
    # ── 舵效调度律（2026-09-19 台架真值，IDENT_20260919）──
    # 尾翼：B(v) = k·v_air²（速压平方律，0/3/5/8 m/s 四点验证恒定）
    K_B_TAIL_ELEV: float = 0.0290   # N·m/rad/(m/s)²
    K_B_TAIL_AIL: float = 0.00965
    K_B_TAIL_RUD: float = 0.0290
    V_TAIL_MIN: float = 1.0    # m/s 低于此空速尾舵无权限，通道关闭（防除零/无效挣扎）
    RATE_FILT_TAU: float = 0.08   # s 内环角速率反馈低通时间常数（压制拍频
                                  # 成分防姿态泵能；1.9Hz≈12 rad/s，τ=0.08s
                                  # 截止约 12.5 rad/s 以上）
    OUTER_FILT_TAU: float = 0.08  # s 外环速度 EWMA（相位积分配套；0.15 实测
                                  # 把 harness 冲量场景弄挂，0.08 与速率环同档
                                  # 折中——2f 压制与瞬态响应的矛盾待周期采样
                                  # 方案在相位修复后重测）
    # 翼差动：B_dA(f) = 5.2·f²（悬停 19.0 / 2.5Hz 32.4 / 3.0Hz 45.3）
    B_DA_SLOPE: float = 5.2    # N·m/rad/Hz²
    B_DA_REF: float = 19.0     # N·m/rad 整定参考点（悬停 1.9Hz 真值）——
                               # K_DA/K_DA_R 是在该增益下整定的，调度保持环路增益恒定
    # ── TECS 速度通道实测导数（2026-09-19 trim/windcal/stepx）──
    DFX_DTHETA: float = 7.40   # N/rad 俯仰→前向力（配平邻域局部斜率，离线
                               # 确定性精扫 10.16° 零点处取值；台架扫阶拟合
                               # 8.9 受 ±0.7N 噪声污染偏陡）
    DFX_DV: float = 3.17       # N/(m/s) 速度阻尼（前飞段前馈补偿）
    PITCH_TRIM: float = 0.177  # rad 悬停配平前倾角（离线精扫零点 10.16°；
                               # 台架扫阶拟合 11.8° 受段噪声污染偏高）
    FREQ_HOVER: float = 1.90   # Hz 悬停拍频（L̄=1.95f² + 配平倾角升力损失，
                               # 台架辨识 1.88 基础上取整）
    FREQ_MAX_AP: float = 2.30  # Hz 自驾拍频上限（≈1.5×体重：够爬升不够
                               # 弹射；3.5×W 的弹射权限不得进高度环）
    PITCH_DES_SLEW: float = 1.2   # rad/s pitch_des 斜坡（单变量重试：全量
                                  # 记录 232852 run 直接显示 0.5 的斜坡是速度
                                  # 环相位滞后的主因——俯仰跟踪 bottleneck；
                                  # 上次 1.2 更差是与 KP_V/TILT_MAX 捆绑下变量
                                  # 无法归因，本次单变量验证）
                                  # 通道的防俯冲措施。注意下限：太慢会让后向
                                  # 推力把机体拖入反向流包线外（模型在该区
                                  # 失真），0.15→实测失控，0.5 闭环通过）
    DFX_FF_MAX: float = 1.0      # N 速度阻尼前馈限幅（≈0.11 rad 等效俯仰；
                                  # 全量前馈会抵消物理阻尼、在饱和区泵能发散）

# 真实质量分布（2026-09-19 资产修复）：活动翼面 54g = 7.7% MTOW。旧等比
# 放大给到 127g（18%），后掠翼面质量的拍动/扭转反作用经 x≈-0.05 力臂成
# 为符号固定俯仰扰动（多次自由飞翻滚的 PhysX 侧源头）。余量归 base_link
MASS_TABLE = {
    "flapconn_L": 0.006, "flapconn_R": 0.006,     # 肩铰/舵机
    "innerwing_L": 0.012, "innerwing_R": 0.012,   # 内翼板（碳杆+膜）
    "outerwing_L": 0.009, "outerwing_R": 0.009,   # 外翼板
    "tail_base": 0.010, "tailpanel_L": 0.008, "tailpanel_R": 0.008,
}

# 【甲项隔离实验 2026-09-20，临时】关闭飞控，实验后恢复 True
FLIGHT_CONTROLLER_ENABLED: bool = True

# 循迹模式（bishop pos_cmd 桥接进来后怎么驱动机体）：
#   "kinematic" — 运动学跟踪：位置P环+速度前馈 → 直写刚体速度（绕过气动，桥接联调用）
#   "aero"      — 气动跟踪：不写速度，pos_cmd 的高度/偏航喂 AeroPhysicsCallback
#                 的 HoverPD（后续在此路径上接 TECS+串级PID，见 SparrowHawk V.B）
# 两种模式互斥：kinematic 的 set_linear_velocities 会覆盖 PhysX 气动积分结果，
# 与 AeroPhysicsCallback 的力注入/HoverPD 打架，禁止在 aero 模式下使用。
TRACKING_MODE: str = "kinematic"

# ============================================================
# 8f. 门感知组件（perception/，SparrowHawk TII 2026 §V.A 复刻）
#     默认全关；ENABLE_GATE_PERCEPTION=True 时 main_sim 生成门并随行采集
# ============================================================

ENABLE_GATE_PERCEPTION: bool = False
GATE_POSITION = (10.0, 0.0, 1.6)   # 门孔圆心世界坐标 (m)
GATE_YAW_DEG: float = 0.0          # 门面法向水平偏航（0 = +X）
GATE_INNER_R: float = 0.40         # 80cm 孔（论文 §VI.C）
GATE_OUTER_R: float = 0.55         # 环带外半径 (m)
GATE_DEPTH: float = 0.08           # 通道轴向深度 (m)

# 采集/光照（normal | lowlight，弱光复现论文 60% 工况）
PERCEPTION_LIGHT_PRESET: str = "normal"
PERCEPTION_COLLECT_DIR: str = r"g:\Playground\isaac_flapper_sim\data\ridealong"
# 检测结果 UDP 外发端口（perception/live.py，JSON 协议见该文件）
PERCEPTION_UDP_PORT: int = 9012

# ============================================================
# 9. SimulationApp 启动配置 —— 直接沿用 USV 版
# ============================================================

SIM_APP_CONFIG: dict = {
    "headless": False,                       # 有头模式，显示 GUI
    "width": 1920,
    "height": 1080,
    "title": "Flapping-Wing MAV Simulation — Isaac Sim 5.1.0",
    "anti_aliasing": 3,                      # TAA
}

# 日志级别
LOG_LEVEL: str = "INFO"


# ============================================================
# 11. 传感器参数类 (Sensor Config Classes) —— 直接沿用 USV 版
# ============================================================
# 这些 dataclass 风格的配置被 modules/sensor_setup.py 引用，原样保留。
# 安装位置坐标按 USV 船体尺寸标定（米）；扑翼 MAV 体积小很多，
# 实际联调时需按 BODY_LENGTH/WINGSPAN 重新标定 MOUNT_POSITION。

class LidarConfig:
    """RTX LiDAR 传感器参数 — 基于 Ouster OS1-64 规格。"""

    SENSOR_TYPE: str = "Ouster_OS1"

    MOUNT_POSITION: np.ndarray = np.array([0.0, 0.0, 0.05])   # 扑翼尺寸缩小：机身顶部 5cm
    MOUNT_ORIENTATION: np.ndarray = np.array([1.0, 0.0, 0.0, 0.0])

    HORIZONTAL_FOV: float = 360.0
    VERTICAL_FOV_UPPER: float = 22.5
    VERTICAL_FOV_LOWER: float = -22.5
    NUM_CHANNELS: int = 64
    HORIZONTAL_RESOLUTION: float = 0.35
    MAX_RANGE: float = 120.0
    MIN_RANGE: float = 0.5
    ROTATION_RATE: float = 10.0
    RANGE_NOISE_STDDEV: float = 0.01


class CameraConfig:
    """前视 RGB 双目摄像头参数。"""

    RESOLUTION: tuple = (1280, 720)
    FOV: float = 90.0

    # 安装位置：相对机身质心 (body frame)，按扑翼尺寸标定
    MOUNT_POSITION_LEFT: np.ndarray = np.array([0.05, 0.02, 0.02])
    MOUNT_POSITION_RIGHT: np.ndarray = np.array([0.05, -0.02, 0.02])
    # Camera -Z → Body +X (前视)
    MOUNT_ORIENTATION: np.ndarray = np.array([0.5, 0.5, -0.5, -0.5])

    NEAR_CLIP: float = 0.01
    FAR_CLIP: float = 200.0

    OUTPUT_RGB: bool = True
    OUTPUT_DEPTH: bool = True
    OUTPUT_SEMANTIC: bool = False


class SurroundCameraConfig:
    """环视鱼眼摄像头参数 (独立于双目)。"""

    RESOLUTION: tuple = (1024, 1024)
    FOV: float = 190.0

    MOUNT_POSITION_FRONT: np.ndarray = np.array([0.06, 0.0, 0.03])
    MOUNT_POSITION_REAR: np.ndarray = np.array([-0.06, 0.0, 0.03])
    MOUNT_POSITION_LEFT: np.ndarray = np.array([0.0, 0.15, 0.03])
    MOUNT_POSITION_RIGHT: np.ndarray = np.array([0.0, -0.15, 0.03])

    PITCH_DOWN: float = 45.0
    FOCAL_LENGTH: float = 2.0
    HORIZONTAL_APERTURE: float = 24.0
    VERTICAL_APERTURE: float = 24.0
    PROJECTION_TYPE: str = "fisheyePolynomial"


class TopDownCameraConfig:
    """顶视正交相机参数。"""

    RESOLUTION: tuple = (1024, 1024)
    MOUNT_POSITION: np.ndarray = np.array([0.0, 0.0, 10.0])   # 机身上方高空
    COVERAGE_M: float = 6.0


class HUDCameraConfig:
    """HUD 第三人称视角相机。"""

    RESOLUTION: tuple = (1280, 720)
    FOV: float = 90.0
    MOUNT_POSITION: np.ndarray = np.array([-0.3, 0.0, 0.15])   # 机身后上方
    MOUNT_ORIENTATION: np.ndarray = np.array([0.5, 0.5, -0.5, -0.5])


# 模块级快捷别名（sensor_setup.py 引用）
LIDAR_MOUNT_FORWARD: float = LidarConfig.MOUNT_POSITION[0]
LIDAR_MOUNT_HEIGHT: float = LidarConfig.MOUNT_POSITION[2]
CAMERA_RESOLUTION_W: int = CameraConfig.RESOLUTION[0]
CAMERA_RESOLUTION_H: int = CameraConfig.RESOLUTION[1]


# ============================================================
# 10. 向后兼容别名 (Backward-compat aliases)
# ============================================================
# 让直接照搬过来的 modules（如 usv_loader、obstacle_manager、sensor_setup）
# 里 `from config.sim_config import USV_*`、`WATER_LEVEL` 这类引用仍能 import 成功，
# 把它们重定向到新的扑翼语义变量。后续逐步把这些 modules 里的 USV/WATER 字眼
# 清理掉，此段即可删除。
WATER_LEVEL: float = GROUND_LEVEL                              # 兼容 obstacle_manager
WATER_PLANE_SIZE: float = GROUND_PLANE_SIZE
WATER_PLANE_THICKNESS: float = GROUND_PLANE_THICKNESS
USV_PRIM_PATH: str = FLAPPER_PRIM_PATH                         # 兼容 usv_loader / main_sim
USV_INITIAL_POSITION: np.ndarray = INITIAL_POSITION
USV_MASS: float = MASS
USV_USD_PATH: str = FLAPPER_USD_PATH
