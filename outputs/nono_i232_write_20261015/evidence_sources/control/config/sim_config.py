"""
sim_config.py — 扑翼微型飞行器（Micro Aerial Vehicle，MAV）仿真全局配置
=====================================================
本文件从 isaac_usv_sim 的全局配置演化而来：本文件删除了全部水动力、波浪、
尾迹参数，改为扑翼微型飞行器所需的空气动力学、姿态、翼面机构参数占位。

设计原则：
  • 路径 / 时间步 / 传感器 / 障碍物 / 网络配置 —— 直接沿用无人水面艇
    （Unmanned Surface Vehicle，USV）版本（平台无关）
  • Fossen 水动力段 → 完全删除，替换为 AeroConfig 占位
  • 保留 SIM_APP_CONFIG 全套 Isaac Sim 启动参数
  • 所有单位采用 SI 单位制（米、千克、秒、弧度）
"""

import os

import numpy as np

# ============================================================
# 1. 路径配置 (Path Configuration)
# ============================================================

# Isaac Sim 5.1.0 安装根目录
ISAAC_SIM_PATH: str = r"g:\Playground\isaac_sim_5.1.0"

# 扑翼 MAV 三维模型文件（USD（Universal Scene Description）格式优先；
# STEP/STL 格式作为备选，需经 URDF/Mesh importer 转换）
# USD 文件尚未建立：Isaac 侧走 URDF Importer 路线（用下方 FLAPPER_URDF_PATH）
FLAPPER_USD_PATH: str = r"g:\Playground\isaac_flapper_sim\assets\flapper\flapper_mav.usda"
# 被控样机（2026-09-15 确定选用）：对方组（徐文福组）提供的 fwav_15dof
# 机构构成为浮动基座 6 自由度 + 驱动关节 9 个：flap_L/R(±x) twist_L/R(+y攻角) fold_L/R(±z折叠)
# tail_yaw + tail_L/tail_R(差动舵面)。坐标系遵循 REP-103（x前y左z上），总重约 430g。
# 我方早期 flapper_15dof.urdf（15 关节全驱动，y 前坐标）留作生成器工具，已弃用。
FLAPPER_URDF_PATH: str = r"g:\Playground\isaac_flapper_sim\assets\flapper\fwav_15dof_from_group.urdf"
# URDF Importer 转换产物（tools/import_fwav_urdf.py 生成，fix_base=False 浮动基座版）
FLAPPER_URDF_USD: str = r"g:\Playground\isaac_flapper_sim\assets\flapper\fwav_15dof.usd"
# 关节样机驱动增益（2026-09-20 夜间开发重整定，docs/驱动重整定_215g.md）：
# 增益扫描确定取值 K=60/D=2.0（摆幅比 1.002）；在环复验发现 D=2.0 的关节反作用
# 力矩在 0.0074 kg·m² 机体上激发剧烈姿态振荡（160200 系列实录），因此第二方案
# 取 D=1.0（摆幅比 1.161、峰值 0.716、反作用力矩减半）——该取值是保真与在环可行
# 的折中，摆幅比 1.16 相对旧值 1.83 的失真降低 36%。30 点扫描矩阵见
# logs/drive_sweep_*.json。ωn·dt≈2.2 rad/步（该取值由离散数值稳定性主导）。
JOINT_DRIVE_STIFFNESS: float = 60.0
JOINT_DRIVE_DAMPING: float = 1.0

# 小惯量关节增益重标定（2026-09-28 非确定性根因定案的修复项）。设计
# 准则沿用上方拍动关节的"ωn·dt 每步约二点二弧度"（离散数值稳定性
# 主导），按各关节惯量重算刚度并取近临界阻尼；扭转/折叠关节惯量比
# 拍动链小一至两个数量级，沿用全局 K/D 造成欠分辨近十倍且过阻尼百
# 余倍——踢击证据（关节力矩尖峰）恰落于扭转关节（见
# docs/非确定性根因定案_欠分辨刚性驱动_评审版.md）。默认关闭
# （FLAPPER_DRIVE_RESCALE=1 启用），关闭态与原行为逐位一致。
# 数值推导：K = I·(ωn·dt 准则 × 1/dt)²，D ≈ 2·sqrt(K·I) 的近临界值。
import math as _math
import os as _os
DRIVE_RESCALE_ON: bool = _os.environ.get("FLAPPER_DRIVE_RESCALE", "") == "1"
_RES_CRIT = 2.2 / (1.0 / 120.0)          # ωn 目标（rad/s），准则×步频
JOINT_DRIVE_GAINS_RESCALED = {
    "twist": (round(1.0e-5 * _RES_CRIT ** 2, 4), round(2.0 * _math.sqrt(1.0e-5 * 1.0e-5 * _RES_CRIT ** 2) * 1.9, 4)),
    "fold":  (round(1.0e-4 * _RES_CRIT ** 2, 3),  round(2.0 * _math.sqrt(1.0e-4 * 1.0e-4 * _RES_CRIT ** 2) * 1.9, 3)),
    "tail":  (round(5.0e-6 * _RES_CRIT ** 2, 4),  round(2.0 * _math.sqrt(5.0e-6 * 5.0e-6 * _RES_CRIT ** 2) * 1.9, 4)),
}

# 拍动驱动参数（WingKinematics，与 tools/preview_fwav_15dof.py 一致）
# flap_L=+φ / flap_R=-φ（两轴同向+x，镜像翼反号）；twist 同号=前飞攻角；fold 限位已镜像
WING_DRIVE_CONFIG = {
    "frequency": 2.5,        # Hz 运动学对象初始频率，飞行中每物理步被控制器输出覆盖，仅
                             # 启动段与钉固段取此值。数值以登记库 wave.drive.frequency 为准。
    "amplitude_deg": 17.73,   # 拍幅（与 FlapKinematicsConfig.AMPLITUDE 同步）
    "pitch_mode": "aero",    # 附着流前飞：攻角约束在 alpha_set
    "alpha_set_deg": 12.0,   # 关节侧位置目标层攻角；气动模型侧另用
                             # 波形配置 32.3169 度，两层独立，力权威
                             # 与关节视觉不一致为已登记待办。
    "fold_amp": 0.45,        # 折叠幅度 rad（上拍段折翼）
    "tail_yaw_amp": 0.12,    # 尾摆小幅摆动（演示用途；控制律接管后置零）
    "tail_trim": 0.08,       # 尾舵配平
}

# 地形 USD（地面 / 森林场景）。留空则使用程序化默认地面平面。
TERRAIN_USD: str = r"g:\Playground\isaac_flapper_sim\assets\forest\forest_scene.usda"
TERRAIN_USD_PATH: str = TERRAIN_USD

# Stage 中的 prim 路径
FLAPPER_PRIM_PATH: str = "/World/Flapper/MAV"
TERRAIN_PRIM_PATH: str = "/World/Environment/Terrain"

# 数据输出目录。两目录相对仓库根解析，检出在何处产物就写入何处，
# 不再固定主仓库的绝对路径，检出复跑不再覆写主仓库的日志与数据。
_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR: str = os.path.join(_REPO_ROOT, "data")
LOG_DIR: str = os.path.join(_REPO_ROOT, "logs")

# ============================================================
# 2. 环境物理参数 (Environment Physics)
# ============================================================

# 地面高度 (Ground level, m) —— 扑翼起飞基准面
GROUND_LEVEL: float = 0.0

# 地面平面尺寸 (Ground plane size)
GROUND_PLANE_SIZE: float = 800.0  # 第十班：400→800（墙内面195m<65s航程292m，024435/024713/025026 三轮同刻t≈45.3撞墙x=194.80冻结逐位吻合）
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

# 物理引擎更新频率 120 Hz（扑翼拍动频率高，物理仿真需要较小的时间步长）
PHYSICS_DT: float = float(os.environ.get("FLAPPER_PHYS_DT", 0)) or (1.0 / 120.0)
# 子步加密入口（2026-09-28 根因修复 R2 臂）：FLAPPER_PHYS_DT 覆盖步长，
# 不设时与原值逐位一致；渲染节拍按 RENDER_DT/PHYSICS_DT 自动换算不受影响

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

# 总质量 (Total mass, kg) —— 215g（表一（Table I）口径，2026-09-20 夜间开发切换；
# 来源=用户工作令裁定，论文 PDF 不在库、取数缺口见 docs/TABLE_I_取数记录.md；
# 旧值 0.700=机构真实质量口径，回退标签 pre-mass-switch-700g）
MASS: float = 0.215

# 初始位姿 — 初始位置在地面上方 1.5 m（系留悬吊位）
INITIAL_POSITION: np.ndarray = np.array([0.0, 0.0, 1.5])  # 系留悬吊位（锚3.0-绳1.5）
INITIAL_ORIENTATION: np.ndarray = np.array([1.0, 0.0, 0.0, 0.0])  # (w,x,y,z)

# ============================================================
# 5. 扑翼气动力学参数 (Aerodynamics) —— Sane & Dickinson (2002) 三分量模型
# ============================================================
# USV 版本原来此处是 Fossen 3-DOF 水动力（M/C/D 矩阵、附加质量、阻尼、浮力点）。
# 扑翼飞行器与水面艇完全不同：扑翼飞行器使用非定常空气动力学，总气动力 = 平动力 + 旋转力 + 附加质量力。
# 具体实现在 modules/aero_solver.py。方程来源见 docs/AERO_MODEL.md。

# 选用的气动力学模型（三种流动类型模型，modules/aero/ 包内实现）
#   attached_flow   — 路线A：高Re大展弦比、附着流环量主导（Wagner/Duhamel + 升力线AR修正）
#   dynamic_stall   — 路线B：大攻角受控动态失速（Leishman-Beddoes简化三状态：分离点/涡环量/涡脱）
#   sane_dickinson  — 路线C：低Re小展弦昆虫式（Robofly极曲线，蜂鸟/RoboBee区间）
AERO_MODEL: str = "sane_dickinson_3d"   # attached_flow | dynamic_stall | sane_dickinson
# 2026-09-18 扫频辨识结论：attached_flow/dynamic_stall 静气悬停升力 ≤0.4 倍体重
# （俯仰符号逐半拍翻转导致升力自相抵消，路线 A/B 是前飞模型）；
# sane_dickinson 离线 L̄(2.5Hz)=1.8 倍体重，悬停区间落在 1.5~3.5Hz 手动操作频率量程内。


class FlapKinematicsConfig:
    """拍动运动学参数 (Flapping kinematics)。
    拍动角 φ(t) = bias + amp·sin(ω·t + phase)，ω=2π·f

    俯仰模式 PITCH_MODE:
      "flip" — α(t) = bias + amp·cos(ω·t+phase)，与 φ̇ 同号（昆虫悬停翻转俯仰）
      "aero" — α(t) 主动合成，使有效攻角稳定在设定值 α_set（附着流前飞，
               攻角限幅在失速角内，A/B 两种高AR流型用这个）
    """
    FREQUENCY: float = 1.8          # Hz 启动/默认拍频；现行 1.80 出自提交 39cfb9a 六十秒巡航配置。
                                     # 配平频率引用史：0921 轮 1.55、扩域轮 1.25，均已作废。
                                     # 数值以登记库 wave.kinematics.frequency 为准。
    AMPLITUDE: float = 17.73          # 度，拍动角单侧峰值（700g 标定：50°给出 2.4 倍体重超升，35°满足要求）
    BIAS: float = 0.0                # 度，拍动角偏置
    FEATHER_AMPLITUDE: float = 40.0  # 度，翼面俯仰幅度(flip 模式用)
    FEATHER_BIAS: float = 0.0        # 度，翼面俯仰偏置(flip 模式用)
    PITCH_MODE: str = "aero"         # flip | aero
    # 波形四参数现行值 32.3169/0.176/-0.0674/19.8105 出自提交
    # 39cfb9a 六十秒巡航配置。0920 首标点四参数 40/0.25/-0.02/0 与
    # 0921 工况重锚定均为历史存档，不再描述现行值。悬停需对称波形
    # split=0.5 与约 7.2Hz，超出自动驾驶仪拍频域。数值以登记库
    # wave.kinematics 族为准。
    ALPHA_SET: float = 32.3169          # 度
    PHASE_OFFSET_LR: float = 0.0     # 度，左右翼相位差
    SPLIT: float = 0.176              # 下扑快（215g 重搜索确定的取值）
    PITCH_LAG_S: float = -0.0674       # 俯仰超前20ms（215g 重搜索确定的取值）
    MEAN: float = 19.8105                # 扑动面偏置（215g 重搜索确定的取值，旧−15°作废）
    # dA 关节目标幅值语义开关（事项 I-190，默认关）：契约定义 dA 为左翼幅值
    # 加 dA、右翼幅值减 dA（rad）。开启时关节拍动目标的差动增量与气动侧
    # amp_cmd 同一定义，即增量 = ±dA·sin(ph)；关闭时保持旧角度偏置语义
    # （flap=phi±dA），行为逐位不变。默认启用与否由 HQ 在本项验收后登记决定。
    DA_JOINT_AMP_ON: bool = False


class ActionExpressionConfig:
    """动作表达配置（事项 I-219；Fil 审计 §4.1 与送审方案 §4.1）。

    只提供配方选择、配方路径与动作记录选择；节律后端、形状和事件放在
    配方（config/motion_recipes/*.json）中，配置与配方不同时掌握同一
    变量。默认选择旧配方，生产入口不装载任何新对象，行为与基线逐位
    一致（回归铁律）。187—218 行波形数值、599 行起控制器参数与现有
    两个发生器开关不在本类重复或改写。
    """
    # 配方标识。默认 "legacy_current" 表示旧配方（现有解析波形路径）；
    # 取其他值时由 modules/sim_env_bridge.resolve_action_recipe 生产入
    # 口装载 config/motion_recipes/<RECIPE_ID>.json。未知配方显式报错，
    # 不回退为另一动作。
    RECIPE_ID: str = "legacy_current"
    # 显式配方文件路径（含文件名）。None 时按 RECIPE_ID 在 RECIPES_DIR
    # 解析；非 None 时优先按路径装载。
    RECIPE_PATH = None
    # 配方目录。相对路径按仓库根解析（生产入口负责定位）。
    RECIPES_DIR: str = "config/motion_recipes"
    # 详细动作记录开关（A05）。False 只关重型逐帧数据；新配方必需的最
    # 小身份与消费来源不受本开关控制，始终保存。
    RECORD_ACTIONS: bool = False


class WingGeometryConfig:
    """翼面几何参数 (Wing geometry)。单翼。
    2026-09-20 按论文确定口径重新标注：复刻对象为 SparrowHawk（TII2026），翼展 60cm。
    2.3m 翼展为上一代 HIT-Phoenix 参数的误标（整套参数已存档 config/archive/
    sim_config_2p3m_PHOENIX_20260920.py）。新几何参数按 fwav 机构碰撞盒取值：
    半展 0.32、内板 0.20×0.18×2 + 外板 0.12×0.16×2 = 双翼 0.109 m²，
    梯形翼根/尖弦 0.20/0.14（平均 0.17），AR≈3.7
    """
    SPAN: float = 0.32              # 半展长 (m)
    ROOT_CHORD: float = 0.20        # 翼根弦长 (m)
    TIP_CHORD: float = 0.14        # 翼尖弦长 (m)（0.6m 口径重新标注）
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
      • 非环量惯性力（薄翼附加质量）：F_nc = ρ·π·b²·a_n（单位展长）
      • 失速保护：α_eff 硬限幅在 α_stall 内（附着流假设）
    """
    # Jones 两项 Wagner 逼近系数
    WAGNER_A1: float = 0.165        # 第一项幅值
    WAGNER_T1: float = 0.0455       # 第一项时间常数（半弦无量纲）
    WAGNER_A2: float = 0.335        # 第二项幅值
    WAGNER_T2: float = 0.3          # 第二项时间常数
    ENABLE_WAGNER: bool = True      # False 时退化为纯准定常
    ENABLE_NONCIRC: bool = True     # 非环量惯性力
    ALPHA_STALL_DEG: float = 22.0   # 失速攻角（该取值已放宽：处于受控动态失速区，与 α_set 的提升相配合）
    # 翼面拍动运动学速度上限（防止数值发散）
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
    # 线性旋转阻尼（2026-09-19 第二次调整，默认值曾为 0.6）：反向阻尼的来源
    # （ω×r 耦合项）已经整体移除，该阻尼只剩物理保护职能；取值 0.6 在真实
    # 惯量（Ixx≈2.4e-3）下对应 4ms 时间常数，50~120Hz 显式积分（试验架/控制环）
    # 会数值失稳并形成 ±3rad/s 极限环（试验架逐环节打印实测）。取值 0.1 对
    # 两档采样率均保持稳定
    WING_ROT_DAMP: float = 0.005

    ENABLE_TRANSLATIONAL: bool = True       # 平动力（必须开启，该力是主导项）
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
# 7. 障碍物配置 (Obstacles) —— 沿用 USV 版，几何形状改为柱状树干与柱子
# ============================================================

# 静态柱状障碍 (Static cylindrical obstacles / tree trunks)
STATIC_OBSTACLE_COUNT: int = int(os.environ.get("FLAPPER_OBSTACLE_COUNT", "") or 20)
# 柱子数量环境覆盖（2026-09-29 机主定睛：随机柱林=非确定性注入源候选。
# 摆放为无种子随机（obstacle_manager.spawn_static_obstacles，
# random.uniform 距离∈[10,80]m 方位全圆），每次运行布局不同；柱高四米
# 穿过三米巡航高度、带碰撞体。FLAPPER_OBSTACLE_COUNT=0 清空用于
# 判别实验，不设时与原值一致）
STATIC_OBSTACLE_RADIUS: float = 80.0
BUOY_DEFAULT_RADIUS: float = 0.3      # 圆柱半径(m) —— 该圆柱既用作浮标也用作柱子
BUOY_DEFAULT_HEIGHT: float = 4.0      # 圆柱高度(m) —— 树干高度
BUOY_COLORS = [
    (0.35, 0.22, 0.12),   # 树皮棕
    (0.45, 0.30, 0.15),
    (0.25, 0.18, 0.10),
    (0.40, 0.25, 0.13),
]

# 动态障碍物 (Dynamic obstacles — 飞鸟/其他 MAV)
DYNAMIC_OBSTACLE_COUNT: int = 0       # 默认关闭，需要时开启
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

# 关节示波器（在线查看电机控制波形）：main_sim 以 30Hz 把关节实际角与目标角
# 发送到本机 9010 端口；tools/joint_scope.py 绑定同一端口实时绘制波形（虚线为目标，实线为实际）
ENABLE_JOINT_SCOPE: bool = True
JOINT_SCOPE_PORT: int = 9010
JOINT_SCOPE_WINDOW_S: float = 8.0   # 示波器时间窗（秒）

# 手动行程示波器：显示四通道杆量与映射输出（频率/尾舵），绘制工具为 tools/ctrl_scope.py
ENABLE_CTRL_SCOPE: bool = True
CTRL_SCOPE_PORT: int = 9011

# Web 看板（Isaac 状态的实时镜像，浏览器访问 http://localhost:8090）
ENABLE_DASHBOARD: bool = True
DASHBOARD_PORT: int = 8090

# ============================================================
# 8b. 尾翼/机身附加气动（控制面 + 阻力开关）
# ============================================================

class TailAeroConfig:
    """尾翼平板气动：俯仰配平（同向舵面）、滚转（差动舵面）、偏航（方向舵偏转）。"""
    AREA: float = 0.0225       # m² 双舵面总面积（2026-10-06 I-095 权限补强：
                               # 0.015→0.0225 增五成，依据 I-094 缺口真值二点
                               # 二三倍目标与竖直耦合账面线内插，推导与回归见
                               # logs/I-095_尾翼变更推导与回归_20261006.md；
                               # 历史两点：0.030 耦合+0.8 净阻尼近零病态、
                               # 0.015 耦合+0.4 净阻尼负0.6 稳定，本值账面
                               # 耦合+0.6 净阻尼负0.4，最小够用）
    LEVER_ARM: float = -0.45   # m 尾压心到 CG（夜间调试第3轮符号修正：正号实测 dMy/dpitch=+3.0 静不稳定——尾翼位于重心之后时该力臂必须取负值；配套 K_B_TAIL_* 同步翻号）
    PANEL_Y: float = 0.224     # m 舵面半展（差动滚转力臂）（2026-10-06
                               # I-095：0.15→0.224 增约四成九，与面积五成
                               # 共凑二点二四倍滚转出力；尾展零点四四八米
                               # 为机翼展七成，骨架同步在 I-096；出处同上档）
    CN_SLOPE: float = 3.5      # 法向力系数斜率 (1/rad)，平板小角
    STALL_ANGLE: float = 20.0  # 度，法向力钳位角

    # ── 固定垂尾气动（2026-09-27 补全，默认关闭）──
    # URDF 鳍片（tail_base 上 0.07×0.055 竖直薄板）此前只有视觉无受力。
    # 参数估计：面积=URDF 几何直读；力臂=与尾面同位；CN 斜率与失速角
    # 取平板小角同款；压心高 z=0.0595（几何中心）产生滚转侧项。
    # 对称面内 → 对 Fz/Fx/My 严格零贡献，纵向配平不动。
    VFIN_AERO_ON: bool = False
    VFIN_AREA: float = 0.00385      # m²（URDF 0.07×0.055）
    VFIN_LEVER: float = -0.45       # m（压心同尾面位）
    VFIN_CN_SLOPE: float = 3.5      # 1/rad（平板小角）
    VFIN_STALL_DEG: float = 20.0    # 度
    VFIN_Z_CP: float = 0.0595       # m 压心高（滚转侧项力臂）

ENABLE_FUSELAGE_DRAG: bool = True       # 机身阻力开关
FUSELAGE_DRAG_COEFF: float = 0.8        # 圆柱迎风阻力系数
FUSELAGE_FRONTAL_AREA: float = 0.0038   # m²（r=0.035m 圆柱正面积）

# ── 拍动滑流（2026-09-19 运控建模线新增，解决悬停俯仰/偏航无舵面权限问题）──
# 真机的尾翼在悬停时受到拍动下洗与滑流的作用（Robofly/SparrowHawk 均如此），
# 本模型此前无此项 → 静气悬停时尾舵控制权限为零、俯仰通道双积分器失控。
# 一阶动量理论：诱导速度 v_i = sqrt(T̄/(2ρA))，T̄=1.95f²（台架辨识值），
# A=双翼扫掠面积 2×½R²Φ（R=1.15m，Φ=2×0.61rad）→ v_i ≈ 0.70·f m/s。
# 尾翼处的有效动压按能量叠加方式计算：v_eff² = v_air² + v_slip²。
# ⚠️ SLIP_K=1.0 是一阶理论值，量级未经台架/真机标定（Real2Sim（实物到仿真）数据链标定为待定事项）。
# 2026-09-19：滑流项重新启用并标定。实测权限刻度（升降舵 ±0.5rad 的俯仰力矩）：
# v_slip=1.33→±0.018 N·m；4→±0.162；8→±0.647。悬停俯仰刚度只能来自尾翼
# （机翼模型俯仰力矩结构上恒零），SLIP_K=5.5 给出 v_slip≈7.35、权限
# ±0.54 N·m，对应真机翼尖滑流量级（~8 m/s）。该项等待 Real2Sim 数据链的精确标定。
ENABLE_TAIL_SLIPSTREAM: bool = True
SLIP_DISK_AREA: float = 1.61      # m² 双翼扫掠面积
SLIP_V_PER_HZ: float = 0.472     # m/s/Hz（2026-09-20 夜间开发 215g 重新标定：动量理论
                                  # v_i=√(W/(2ρA_disk))=0.731 m/s @ W=2.109N/A=1.61m²，
                                  # 除以 f*=1.55Hz；旧值 1.25 是 700g/2.3m 巡航配平点的标定结果）
SLIP_K: float = 1.0               # 滑流标定系数（4.0→1.0：旧值 4.0 是悬停时代为凑合尾舵效能而采用的放大系数，
                                  # 巡航 f=4 时该旧值给出 11.25m/s 的滑流速度误差并使尾翼失速，产生 −1.92N 的
                                  # 升力误差；本次取值修正与钳位补丁的移除禁令相配合）
                                  # 质量从 127g 降到 54g 时，预期扰动幅度缩小 3 倍；按
                                  # 4.0×0.703×1.9≈5.3 m/s 计算，控制权限约为 ±0.2 N·m（相对残差））

# ============================================================
# 8c. 手动输入（摇杆/键盘 → 四通道，映射见 main_sim._drive_wing_joints）
# ============================================================

MANUAL_INPUT_ENABLED: bool = True
MANUAL_FREQ_MIN: float = 1.2    # Hz 油门最低拍频（215g：f*=1.55，下限下探以覆盖配平点）
MANUAL_FREQ_MAX: float = 3.5    # Hz 油门最高拍频（215g：2.26×f* 爬升余量；悬停 7.2Hz 不在手动操作频率域内）
MANUAL_ELEV_GAIN: float = 0.35  # rad 升降舵满杆偏角（同向）
MANUAL_AIL_GAIN: float = 0.30   # rad 差动舵满杆偏角
MANUAL_RUDD_GAIN: float = 0.40  # rad 方向舵满杆偏角
MANUAL_KEY_RATE: float = 1.5    # 键盘模式杆量变化速率 (1/s)

# 视口跟踪相机：主视口始终对准机器人（视点 = 机器人位置 + 偏移）
VIEWPORT_FOLLOW: bool = True
VIEWPORT_OFFSET = (4.5, -4.5, 2.0)   # 相机相对机器人的偏移 (m)

# 循迹可视化（modules/trail_viz.py，Mesh 竖直条带，零控制管线侵入）：
# 青色为期望轨迹，橙色为实际尾迹；两条图元位于 /World/Viz 路径下。
ENABLE_TRAIL_VIZ: bool = True
# 无外部控制桥接时，启动即绘制演示巡航圆（r=5m z=1.5，CJA2022 圆航线型占位）；
# 真实的 pos_cmd/path 数据到达后自动替换演示内容
TRAIL_VIZ_DEMO: bool = True

# ============================================================
# 8d. 系留台架 + 定高 DP（悬停试验第一步；绳力与 DP 输出全部通过力回调施加，不直接改写位姿）
# ============================================================

# 系留绳：软绳，仅当 |p-锚|>绳长 时产生拉力+阻尼（该绳只产生拉力不产生推力，用于模拟真实绳索）
TETHER_ENABLED: bool = False
TETHER_ANCHOR = (0.0, 0.0, 3.0)   # 锚点（世界系）
TETHER_LENGTH: float = 1.5        # 绳长 (m)
TETHER_K: float = 60.0            # 绳刚度 (N/m)
TETHER_C: float = 30.0            # 绳阻尼 (N·s/m)（10→30：该取值使上冲机体的
                                  # 瞬时冲击近似减半；该环节是泵能回路的放大环节之一）

# 定高 DP：高度误差 PD → 拍频（升力∝f²）。手动油门偏离中位时，DP 控制让位于手动输入。
# 【甲项隔离实验 2026-09-20，临时】该实验关闭飞控与定高，实验结束后恢复为 True
DP_HEIGHT_HOLD: bool = True
DP_TARGET_Z: float = 3.0          # m 目标高度（1.5→3.0 夜间调试第4轮：巡航瞬态需要高度余量——推力剩余+1N加速→升力超额→爬升1.4m/s 的自然瞬态在1.5m余量内必然触地）
DP_KP: float = 1.5                # Hz/m
DP_KD: float = 1.2                # Hz/(m/s)
DP_FREQ_NOMINAL: float = 1.8     # 巡航配平拍频（215g 重搜索确定的配平频率 f*，docs/TRIM_SCAN_215g.md；
                                  # 旧 1.90 为 700g 口径悬停值）
# DP 防泵能三项措施（2026-09-19）：输入低通 + 输出限幅在配平邻域 + 输出斜坡。
# 泵能过程记录见 docs/IDENT_20260919.md 第六节：DP 读取瞬时速度会把拍频振荡直接
# 放大为 1.5↔3.5Hz 的油门剧烈摆动，形成弹射—绳拽—再弹射的循环。
# 2026-09-20 夜间开发（215g）：新被控对象巡航带内 ∂Fz/∂f<0（TRIM_SCAN 附录一），
# DP"高度误差→+拍频"的符号在新被控对象上反转，DP 通路在在环复验前视为失效
# 状态（TECS 路径不受影响）；输出频带按 f*=1.55 的旧比例（0.9×/1.16×）重新设定。
DP_FREQ_MIN: float = 1.0          # Hz DP 输出下限
DP_FREQ_MAX: float = 3.5          # Hz DP 输出上限
DP_SLEW: float = 0.8              # Hz/s DP 输出变化率限制
DP_FILT_TAU: float = 0.3          # s DP 输入 z/vz 的 EWMA 时间常数

# 拍幅软启动：拍幅/扭转/折叠从 0 斜坡到全幅的时长（消除 t=0 时刻的驱动冲击）
STARTUP_AMP_RAMP_S: float = 0.5   # s
# 起飞先锁定状态后释放（2026-09-19 二次启用）：内环采用低通滤波与增益软化后，环路已经稳定
# （205202 run：机体不再飞行丢失，翻滚后能够有界恢复），但拍幅到达全幅的瞬间，机翼驱动反
# 作用仍产生 −30rad/s 级的角速度冲击（力矩量级为 0.17N·m），该冲击超出软化增益内环的响应
# 能力。机体状态锁定渡过软启动窗口；释放时刻只剩约 −2.7rad/s 的冲击，软化增益内环可以直接吸收
# 【甲项隔离实验 2026-09-20，临时】全状态锁定+无控制器+恒频 2.5Hz：该实验用于定位单翼
# 驱动失效是否与控制器或机体运动无关。实验结束后恢复为 0.9
STARTUP_PIN_S: float = 0.9
# 发射注入（手上发射等价）：初速取巡航配平速度；俯仰取新被控对象气动配平点
# 2026-09-21 第七班收尾工作完成工况点重新标定（提交号为 0f61a1f）：新配平
# 工况点为 V=4.5 m/s、f=1.55 Hz、θ*=−0.05 rad，因此 LAUNCH_VX 取 4.5、
# LAUNCH_PITCH 取 −0.05；选择新工况点的依据是新工况点的升力裕度为 35%。
# 2026-09-20 首次标定的工况点（4.0 m/s、−6.2°、LAUNCH_PITCH=−0.108）的
# 推导记录见 docs/TRIM_SCAN_215g.md 附录一；更早的 −0.20 取值是 700g/2.3m
# 口径下 V=8 m/s 的配平结果，该取值已经作废。PITCH_TRIM=0 仍属于力矩配平
# 口径，使用时不得与气动配平口径混用。
LAUNCH_VX: float = 3.95
LAUNCH_PITCH: float = -0.143246

# 来流风（风洞式系留试验用；气动按相对风速 v_air = v_body - wind 计算）
# 2026-09-19 第一道验收关（静气悬停验收）：WIND_ENABLED=False。5m/s 迎风加
# 漂移会把机体推出飞行包线（在环实测高度爬升到 136m）；静气环境是变量最少
# 的验收环境。第三道验收关再回到迎风工况（试验模式仍由 MODE_DEFAULTS 按模式覆盖）
WIND_ENABLED: bool = False
WIND_VECTOR = (-5.0, 0.0, 0.0)    # m/s（负号表示迎面来流；此前误用正号导致顺风，差动力矩符号因此翻转）

# 正常飞行时也记录 30Hz 数据流（飞控闭环在环测试用；文件命名 flight_<ts>）
FLIGHT_STREAM_LOG: bool = True

# Isaac 内部相机内录（第15轮新增，用于成功与失败的记录留档）：跟随视角 Camera
# 传感器逐渲染帧采集 RGBA，数据管道送入 ffmpeg 编码为 mp4——不依赖桌面抓屏，
# headless/GUI 模式均可用。视频保存于 logs/camera_rec_<ts>.mp4。
CAMERA_REC_ENABLED: bool = False
CAMERA_REC_FPS: int = 30
CAMERA_REC_RES = (1280, 720)

# 自动试验（modules/experiment.py 实现；tools/run_ident.py 记录拟合结果）。取空字符串表示关闭
EXPERIMENT_MODE: str = ""   # 2026-09-19 全部辨识完成，恢复关闭    # "" | "sweep" | "rudder" | "dastep" | "trim" | "windcal" | "kick_roll" | "kick_pitch" | "kick_yaw" | "kick_pitch_x" | "rate_roll" | "rate_pitch" | "rate_yaw" | "stepx" | "rudder_v"
EXP_SETTLE_S: float = 8.0         # 试验前稳态时间
EXP_SWEEP_FMIN: float = 1.5       # 扫频下限 (Hz)
EXP_SWEEP_FMAX: float = 6.0       # Hz 扫频上限（3.5→6.0：0.6m 翼的悬停拍频按面积折算，预计 0.7kg 机体约 4.7Hz）
EXP_SWEEP_STEP: float = 0.25      # 扫频步长 (Hz)
EXP_DWELL_S: float = 10.0          # 每频率点最短驻留
EXP_MIN_CYCLES: int = 10          # 每频率点至少扑动周期数
EXP_STEP_ELEV: float = 0.10       # 升降舵阶跃幅值 (rad)
EXP_STEP_AIL: float = 0.10        # 差动舵阶跃幅值 (rad)
EXP_STEP_RUD: float = 0.15        # 方向舵阶跃幅值 (rad)
EXP_STEP_S: float = 6.0           # 阶跃保持 (s)
# 翼差动舵效试验（dastep）：辨识 B_dA(f)，用于取得滚转主通道的真值
EXP_DA_FREQS = (1.9, 2.5, 3.0)    # 拍频点 (Hz)
EXP_DA_AMPS = (0.06, 0.12, 0.18)  # dA 阶跃幅值 (rad)
EXP_DA_STEP_S = 5.0               # 每阶保持 (s)
EXP_DA_GAP_S = 3.0                # 回零间隔 (s)
EXP_DA_SETTLE_S = 5.0             # 每频率点稳定 (s)
EXP_GAP_S: float = 4.0            # 阶跃间隔回零 (s)

# ── 台架扩展（2026-09-19 运控建模线：配平/风速/单轴释放试验）──
BENCH_FREE: tuple = ()            # 台架释放轴，取值为 ("roll","pitch","yaw","x") 四轴的子集；
                                  # 该参数由 experiment.MODE_DEFAULTS 按试验模式自动覆盖
BENCH_PITCH_DEG: float = 0.0      # 台架俯仰偏置（trim 试验逐段改写，单位为度）
# trim 试验：俯仰偏置扫阶（其余自由度保持锁定状态，静气悬停拍频）
EXP_TRIM_PITCHES = (0.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0)   # deg
EXP_TRIM_STEP_S: float = 5.0      # 每角度保持 (s)
EXP_TRIM_GAP_S: float = 2.0       # 回零间隔 (s)
# windcal 试验：来流速度扫阶（机体状态锁定，验证 Fx(v)/L̄(v) 的动压规律）
EXP_WIND_SPEEDS = (0.0, 1.5, 3.0, 5.0, 6.5, 8.0)            # m/s 迎面
EXP_WIND_STEP_S: float = 6.0      # 每风速驻留 (s)
# kick_* 系列：单轴释放衰减试验（施加初始角速率脉冲 → 自由衰减 → 辨识阻尼导数）
# ⚠️ 2026-09-19 实测：本模型存在瞬时反阻尼窗口（占周期的 41~48%），自由释放下
# 该窗口会按相位锁定方式泵入能量并形成限幅极限环，自由衰减法不可用 → 改用 rate_* 定速台架
EXP_KICK_SETTLE_S: float = 8.0    # 释放前状态锁定稳态段时长 (s)
EXP_KICK_FREE_S: float = 12.0     # 释放后自由衰减记录 (s)
EXP_KICK_W: float = 0.6           # 初始角速率脉冲 (rad/s)
EXP_KICK_HOLD_S: float = 0.3      # 脉冲段时长 (s)
# rate_* 系列：定速台架（按运动学方式给定角速率 → 测量周期平均力矩斜率 = 阻尼导数）
EXP_RATES = (0.0, 0.25, -0.25, 0.5, -0.5, 1.0, -1.0, 2.0, -2.0)  # rad/s
EXP_RATE_SETTLE_S: float = 6.0    # 定速前状态锁定稳态段时长 (s)
EXP_RATE_STEP_S: float = 4.0      # 每速率驻留 (s)
# rudder_v 试验：尾翼舵效的动压调度（第 4 条），逐风速点做三通道阶跃
EXP_RUDV_SPEEDS = (0.0, 3.0, 5.0, 8.0)   # m/s 迎面（0 m/s 以原点为速度基准点）



# ============================================================
# 8e. 飞行控制器（modules/flight_controller.py，串级 PID + 简化总能量控制系统（TECS-lite））
# ============================================================

class FlightControllerConfig:
    """增益起点：舵效直接测量值(2026-09-17) + 惯量约 0.015 kg·m²。增益经系留调参后再进行自由飞。"""
    # 舵效（N·m/rad，分项力矩直测）
    B_ELEV: float = 0.724    # 升降舵→俯仰（2026-09-18 状态锁定辨识=解析值）
    B_AIL: float = 0.241     # 差动→滚转（同上；旧值 1.0 属于测量伪影）
    B_RUD: float = 0.723     # 方向舵→偏航（同上）
    # 姿态环
    KP_A: float = 2.6        # 角度外环 (1/s)（第七班重整定 1.2→0.7 把姿态需求压到尾翼权限内；旧值见 Git）（第13轮试验 2.4 使发射段俯仰摆动、9 秒坠地且静差未收敛，已回退；6.0 曾引起 bang-bang 极限环）
    KI_A: float = 0.15       # 俯仰角误差积分 (1/s²)（第14轮）：消除比例环的静差
                             # （整定史：0.3 过冲漂高、0.2 激发耦合发散、0.1 为旧轮结论；
                             # 现行 0.15 出自提交 39cfb9a 控制器整定链。数值以登记库
                             # ctrl.gain.ki_a 为准。）
    KI_A_MAX: float = 2.5    # 积分状态限幅 (rad·s)，即抗饱和本体：输出权限上限
                             # KI_A×2.5=0.25rad/s ≥ 在环稳态需求 0.23rad/s。
                             # 不采用 pitch_des 触限时冻结积分——外环饱和恰好是积分
                             # 应当顶到权限的时刻（在环实录表明触限冻结会造成积分锁死）
    PITCH_INT_GATE: float = 0.25  # rad 俯仰条件积分误差门（E4 参数化，
                             # 2026-09-28 机主签字；历史硬编码值即此默认，
                             # 不设环境变量行为逐位不变）。44 轮登记"积分门
                             # 与稳态误差不匹配"：E3 在环实测指令-18°时误差
                             # 0.17~0.31rad 恒在门外积分全程冻结；放宽经
                             # FLAPPER_PITCH_INT_GATE 覆盖（E4 取 0.6）
    KP_R: float = 0.25       # 角速度内环（0.5→0.25 软化）：俯仰通道是零自然阻尼
                             # 双积分器（M_q≡0），速率环是唯一阻尼源；
                             # KP_R·B_tail(v_eff)/I ≈ 3 1/s 悬停阻尼（旧值 0.02
                             # 是 9-17 伪影增益下的经验性降额，等效阻尼≈0.12 1/s）
    DELTA_MAX: float = 0.30  # 舵偏限幅 (rad)（0.22→0.30 增益调整配合：俯仰通道在爬升状态
                             # 被尾翼抬头力矩顶在 −0.22 限幅 8 秒（0920_123505 实录），
                             # 行程权限放大 36%；副翼与方向舵同步受益）
    # TECS-lite
    KP_Z: float = 1.5        # 高度→拍频 (Hz/m)
    KD_Z: float = 0.7        # (Hz/(m/s))（1.2→0.7：+0.5m/s 的释放冲量被旧值
                             # 压到拍频下限，掉高 −1.4m/s 建立后斜坡无法跟随）
    KP_V: float = 0.8        # 速度误差→加速度需求 (1/s)（1.5 在环更差已回退）
                             # 2026-09-19 由 0.10 上调——原值使速度环 τ=10s，
                             # 比位置外环还慢，串级带宽倒挂致位置振荡发散）
    # 位置外环
    KP_XY: float = 0.15      # 位置误差→期望速度 (1/s)（第八班退回 0.15：0.40 是增益普遍放大
                             # 时期的提速取值，外环带宽仍远小于速度环
                             # 的 0.3 1/s；旧值 0.8 是内环的 8 倍，闭环实测发散）
    KI_XY: float = 0.15      # 位置积分 (1/s²)——消 Fx(θ) 截距残差的常值静差
                             # （0.08→0.15 提速）
    XI_MAX: float = 5.0      # 积分限幅 (m·s)，pitch 触限时冻结积分。
                             # 截距残差~0.09N≈0.01rad 俯仰当量，积分权限需
                             # 覆盖该残差（旧值 2.0 实测余量不足，0.08m/s 的爬行速度不能消除）
    TILT_MAX: float = 0.55   # 期望姿态角限幅 (rad)（第13轮试验0.45无效:静差同步扩大净低头不变,已回退）
                             # 悬停配平前倾 0.206 rad + 机动余量，否则速度环无权限）
    # 设定点（系留锚点附近的定点悬停，承受 5m/s 风速）
    X_REF: float = 0.0
    Y_REF: float = 0.0
    FREQ_SLEW: float = 2.5   # Hz/s 拍频斜坡（对称限幅。相位已经改为积分状态，
                             # 相位跳变不再存在，因此斜坡可以取更高速率；旧值 1.0
                             # 实测无法跟随释放冲量造成的掉高——试验架冲量场景 4.9 秒坠地）
    K_DA: float = 0.04         # 翼差动角增益（0.12→0.04：Ixx≈1.7e-3 下防止
                               # 单步把角速率推过阻尼反转门槛）
    K_DA_R: float = 0.008      # 翼差动速率阻尼（对 120Hz 控制频率保留离散裕度）
    DA_MAX: float = 0.08       # 翼差动限幅（满偏≈0.9N·m≈单步 5rad/s，该分辨率仍然较粗）
    Z_REF: float = 3.0             # m（与 DP_TARGET_Z 一致取 3.0：两参数不一致时高度环会主动俯冲——第4轮实录）
    YAW_REF_DEG: float = 0.0
    # ── 舵效调度律（2026-09-19 台架真值，IDENT_20260919）──
    # 尾翼：B(v) = k·v_air²（动压平方律，0/3/5/8 m/s 四点验证该系数恒定）
    # I-096 构型跟随重标（2026-10-06，授权链=机主构型变更同步令）：
    # I-095 面积 0.015→0.0225（×1.5），差动力臂 0.15→0.224（×1.493）。
    # 升降与方向舵只吃面积比 ×1.5；差动吃积 ×2.24。旧值随先例
    # （"随尾面积减半"）同法退役：
    K_B_TAIL_ELEV: float = -0.02175  # −0.0145×1.5（I-096 跟随）
    K_B_TAIL_AIL: float = 0.01082   # 0.00483×2.24（I-096 跟随）
    K_B_TAIL_RUD: float = -0.02175  # −0.0145×1.5（I-096 跟随）
    V_TAIL_MIN: float = 1.0    # m/s 低于此空速尾舵无控制权限，通道关闭（防止除零与无效输出）
    CRUISE_VX: float = 3.95     # m/s 巡航速度设定（0=位置保持；>0=速度保持）。现行 3.95 出自
                               # 提交 39cfb9a 巡航部署，与后续辨识采集实测合流速度约 3.98 互证；
                               # 0921 重锚定值 4.5 已作废。数值以登记库 ctrl.ref.cruise_vx 为准。
    # 巡航配对增益（夜间调试第3轮）：高度→俯仰（升力通道）、速度→拍频
    # （推力通道）——与悬停配对相反，依据全机配平表（z 通道 8.4 N/rad、
    # f3.5→4.5 推力 +0.43→+1.59N）
    KC_PZ: float = 0.05        # rad/m（0.05/0.5 组合在环 60 秒：z 稳定于 7.1m 无坠机——剩余问题为高度的稳态平衡偏差）
    KC_DZ: float = 0.50        # rad/(m/s)（同上）
    KC_FV: float = 0.40        # Hz/(m/s) 速度误差→拍频（0.15 经试验无效，已回退）
    KC_IZ: float = 0.008       # rad/(m·s) 高度误差积分→俯仰（0.004 时 50 秒收敛到 4.9m 仍出验收带，因此取值加倍；
                               # 稳态高度偏差属于平衡态偏差，由慢积分消除）
    # ── 总能量控制（TECS，2026-09-20）：替代巡航配对单回路 ──
    # 总比能误差 e_E = g·(zr−z) + ½(vxd²−vx²)（J/kg；比能形式与质量解耦，
    # 后续 215g 质量切换后本区增益免重整）→ 拍频；能量分配误差
    # e_B = g·(zr−z) − ½(vxd²−vx²) → 俯仰。冲突象限（高且慢）内两路同向
    # 压低机头而拍频保持中立，该结构从机理上消除旧配对"速度环加油门=加升力"的
    # 正反馈（0920_104813 在环失速实录）。KC_* 旧配对参数保留未删除，
    # 需要回退时把控制器 2C 分支切换回去即可。
    TECS_KF_P: float = 0.0    # Hz/(J/kg) 总比能误差→拍频（0.05 档拍频在 1.5↔4.7
                               # 秒级来回摆动，驱动掉高—拉起极限环，0920_123505 实录）
    TECS_KF_I: float = 0.0   # Hz/(J/kg·s) 比能积分→拍频（第九班 0.005→0.010 用于补偿残余阻力；旧值见 Git）（用于消除配平截距稳态差）
    TECS_EI_MAX: float = 60.0  # J/kg·s 比能积分限幅（输出权限 0.3Hz）
    TECS_VF_K: float = 0.0     # Hz/(m/s) 垂速→拍频直接释放升力增益（候选A 关闭：
                                # 两个被控对象的拍频-升力符号不一致，该通道无法在两者上同时成立）
    TECS_VFLOOR_ON: bool = False  # 低速防能量耗散保护（第六班升级：vx<V_CMD_MIN 时
                                 # 按缺口比例升频补推力，替代旧 vx<2 开关档）
    TECS_VMIN_K: float = 0.5   # Hz/(m/s) 防能量耗散升频增益（f>f* 两个被控对象均为正 Fx）
    TECS_VMIN_FMAX: float = 2.0  # Hz 防能量耗散升频封顶（在环负升力区下沿；封顶以上
                                  # 巡航波形升力转负，kimi2 在环实录 t=65 坠落）
    TECS_ZV_K: float = 0.30    # (m/s)/m 高度优先速度基准增益（2026-09-20 夜间开发：高度误差
                                # 经速度基准释放升力，新被控对象的升力主变量为速度；该取值由试验架确定）
    # ── 速度下限保护（2026-09-20 第六班，在环坠机主要故障链条的根治措施）──
    # 主要故障链条：爬升消耗速度→高度优先基准把目标速度降到 2.0（旧下限，低于升力
    # 所需的最低速度）→升力随速度塌缩→能量分配通道执行"低头换速度"→低速无升力→直接坠落。
    # 两项措施：①目标速度下限=验收带下沿 3.0（速度指令不允许低于验收带）；
    # ②俯仰下压权限随速度线性衰减，3.5 以下开始、3.0 归零（禁止低头换速度）。
    V_CMD_MIN: float = 3.0     # m/s 目标速度下限。2026-09-20 第六班设定该参数时，
                                # 该数值等于旧速度验收带 4.0±1.0 的下沿 3.0；2026-09-21
                                # 工况点重新标定之后，现行速度验收带 4.5±1.0 的下沿为 3.5，
                                # 但本参数值没有随重新标定更新（两者相差 0.5）；该项差异
                                # 列为遗留未决事项，等待裁定。设定意图：速度指令只允许
                                # 使用已经验证的包线内的速度值。
    V_PITCH_FREE: float = 3.5  # m/s 高于此速度俯仰下压权限不打折
    V_PITCH_LOCK: float = 3.0  # m/s 到此速度俯仰下压权限收到浅俯冲余量
    V_DIVE_ALLOW: float = 0.08  # rad 低速浅俯冲余量（低速恢复速度的唯一物理
                                # 通道；深俯冲仍然禁止——在环致命俯冲角为 −0.55）
    TECS_KP_E: float = -0.004   # rad/(J/kg) 分配误差→俯仰（收尾施工步骤二三档证伪后回到基线：
                               # 0.010→末5s z=+5.27 超出高度契约、0.020→z=+4.72 仍超出、0.004→z=+3.27
                               # 通过；增益与高度漂移非单调（低头加速→e_kin 更负→freq 更低→
                               # ∂Fz/∂f<0 升力反增的间接正反馈），单变量提增益不可达离线诊断的
                               # 阻尼需求（docs/速度环不响应诊断.md 供给/需求=0.09），已按第六节
                               # 上报结构更换。第七班取值记录：0.003 高度出带/0.005 限环取中）
    TECS_KD_V: float = 0.02    # rad/(m/s) 垂直速度阻尼（0.3~0.4 档的 vz 项顶满限幅形成继电器式振荡，因此大幅降低）
    TECS_EB_MAX: float = 60.0  # J/kg 分配误差限幅——俯仰指令增量权限封顶
                               # ±0.24 rad，禁止能量分配通道下达深俯冲指令
                               # （0920_122055 俯冲段滚转权限耗尽的教训）
    ALPHA_FF_GAIN: float = 0.12  # 尾翼攻角前馈加深系数（第10轮标定：前馈浅
                                # 0.044 rad，典型爬升攻角 ~0.14 rad → ×1.3）
    # ── 偏航抵消组合通道（2026-09-27，默认关闭，生效行为不变）──
    # 实测依据（docs/侧滑工况横侧通道效能.md）：幅值差动偏航副作用
    # +0.2093 N·m/rad 与滞后差动偏航效能 −0.2092 N·m/s 等大反号，
    # 按比值混合可在保留滚转效能的同时消除偏航注入。
    WING_MIX_ON: bool = False  # 组合通道开关：开时翼差动输出附带滞后差动
    WING_MIX_RATIO: float = 0.5  # I-100/I-103重整定:组合通道减半(I-103网格第三坐标,孤峰21.8s创于此值)  # dlag[s] = ratio × dA[rad]（实测效能比）
    WING_MIX_DLAG_MAX: float = 0.02  # s，时机差动已验证域上限
    # ── 相位域波形发生器（I-172 接入，I-139 定稿五自由度 Hopf 基底，
    # 预登记 ltv/i172_wavegen_prereg.md，默认关闭行为逐位不变）──
    WAVEGEN_ON: bool = False    # 发生器开关：关=现行正弦波形零改动
    WAVEGEN_KAPPA: float = 4.0  # Hopf 幅值自稳收敛率（I-139 预登记默认）
    WAVEGEN_B1: float = 0.0     # 上行程不对称（0=对称，与现行一致）
    WAVEGEN_B3: float = 0.0     # 下行程不对称（0=对称，与现行一致）
    WAVEGEN_AMBDA: float = -1.0 # 幅值自稳目标（弧度）；负值哨兵=用现行
    # Hopf 输出接入实际翼驱动（事项 I-210，默认关）：开启且自动模式时，
    # wing_actuation 与 mock_backend 经统一有效频率选择消费控制器的
    # wavegen_freq（关节侧与气动侧实际采用），关闭时与原路径逐位一致。
    # 默认启用与否由 HQ 验收后另行登记决定。
    WAVEGEN_DRIVE_ON: bool = False
                                # AMPLITUDE 换算，保证关闭态语义不变
    # ── 定坡度盘旋试验（2026-09-27，默认关闭，生效行为不变）──
    # 判据与方案先于数据：docs/压坡度转弯试验_判据与方案.md
    BANK_TEST_ON: bool = False   # 定坡度盘旋试验开关
    BANK_TEST_DEG: float = 10.0  # 度，常值坡度指令（任务目标自定值）
    BANK_TEST_T: float = 5.0     # s，坡度指令生效时刻
    # ── 高度环机动预算（事项 I-138，默认零即现行行为不变）──
    # 转弯期间允许按预算换高度：预算为正数时坡度段内高度环目标从
    # Z_REF 放宽到 Z_REF±ALT_BUDGET_M，超出预算仍硬限。预算为零时
    # 高度环目标与现行完全一致，行为逐位不变。
    ALT_BUDGET_ON: bool = False  # 机动预算开关（默认关=行为不变）
    ALT_BUDGET_M: float = 0.5   # m，转弯期间允许的高度变化预算（正数）
    YAW_FF_ADAPT: bool = False   # 偏航前馈增益慢自校准（消稳态过转）
    YAW_FF_ADAPT_TAU: float = 8.0  # s 自校准时间常数（自定、可调）
    PITCH_FB_FILT_TAU: float = 0.0  # s 俯仰反馈低通（0=直读=历史行为；
                                    # 滤欧拉耦合快分量，守门轮三整定值）
    BANK_FREQ_FREEZE: bool = False  # 盘旋段拍频冻结（切断 TECS-频率-
                                    # 俯仰矩三路自激；默认关=历史行为）
    BANK_SLEW: float = 999.0     # rad/s，坡度指令覆盖斜坡率（999=阶跃=
                                  # 历史行为；0.3 为科目一整定自定值，
                                  # 防切入瞬态误差打满滚转权限 bang-bang）
    YAW_FF_TURN: bool = False    # 协调转弯率前馈（BANK_TEST 激活段
                                  # wzd=ω_coordinator，抵消偏航阻尼对稳态
                                  # 转弯率的对抗；R13 重判定案的修复项）
    YAW_FF_DIR: float = -1.0     # 前馈方向系数（在环实测 +10° 坡度对应
                                  # wz 为负；默认 -1，若判据口径翻转取 +1）
    COORD_KB: float = 0.0        # 侧滑角闭环增益（0=纯前馈行为；R14 教训
                                  # 机头超 trajectories 前积累，2.0 为整定
                                  # 自定值：把机头锁到速度矢量）
    COORD_KI: float = 0.0        # 侧滑静差积分增益（0=纯 P 行为；R48 后
                                  # 自我核对：β 静差缓漂是先导，积分顶掉）
    COORD_I_MAX: float = 0.3     # rad/s 侧滑积分输出限幅（自定、可调）
    BANK_FREQ_CAP: float = 2.2  # Hz，定坡度试验拍频上限（归因测量定案：
                                # 拍频越过约2.4Hz进入工作点失效区——升力
                                # 塌穿0.95、Fx配平根消失、滞后差动效能塌
                                # 83%、偏航抵消比失效；试验内把上限压回
                                # 失效区之下，仅 BANK_TEST_ON 时读取，
                                # 关闭态行为逐位不变）
    # 坡度段俯仰保持（2026-09-28 权限包线定案，默认关闭）：密度通道
    # 权限在机体俯仰约 −4° 处过零，巡航实际俯仰约 0° 恰在反号侧
    # （冒烟巡航回放实测 −0.0073 N·m）；坡度段把俯仰指令压到
    # BANK_PITCH_HOLD_DEG，使机体进入密度通道正权限区（−10° 处
    # +0.053 N·m）。取值 −10° 为自定、可调。仅 BANK_TEST_ON 时读取。
    BANK_PITCH_HOLD_ON: bool = False
    BANK_PITCH_HOLD_DEG: float = -10.0
    # ── 通道分幅共存与三维重标定（2026-09-28，默认全关；依据
    # docs/科目一重跑_俯仰保持版_根因定位.md）──
    DA_SCHED_3D_ON: bool = False  # 翼差动三维效能映射（力矩需求直达）
    B_DA_3D: float = 0.046        # N·m/rad（dA 语义），三维实测巡航姿态
                                  # 俯仰 −13°~+3° 全程正号稳健
    DA_MAX_3D: float = 0.175      # rad（参数总表编号 6 建议域；已验证域 ±0.10）
    DA3D_VMIN: float = 0.0        # m/s 翼差动直通的最小 v_eff（0=门控恒放行
                                  # 行为与门控前一致；R1 教训：低速爬升段满幅
                                  # 差动摇翻机体，差动只在巡航动压域放出，
                                  # 取值 2.5 为自定、可调）
    DA3D_TMIN: float = 0.0        # s 翼差动直通的最小仿真时刻（0=恒放行；
                                  # R2 教训：空中释放 v_eff 恒高、VMIN 门控
                                  # 无效，释放瞬态 wx 尖峰驱动满幅差动摇翻
                                  # 机体；差动在坡度机动段才放出，取值与
                                  # BANK_TEST_T 对齐，自定、可调）
    DA_LPF_TAU: float = 0.0       # s 滚转执行量 dA/ail 一阶低通时间常数
                                  # （0=不滤波=行为不变；R3 教训：wx 5.73Hz
                                  # 反作用纹波直通反馈把满幅修正打成换边
                                  # 极限环，0.10 为整定自定值）
    ROLL_FILT_TAU: float = 0.0    # s 滚转速率反馈专用低通时间常数（0=用
                                  # 三轴共用 RATE_FILT_TAU 滤波值=行为不变；
                                  # R4 教训：wx 纹波幅±1.5 rad/s 淹没机动
                                  # 需求 0.3~0.8 rad/s，0.12 为整定自定值）
    ATT_QUAT_ERR: bool = False    # 姿态误差四元数化（体轴系误差，
                                  # 绕过欧拉耦合；默认关=历史行为）
    ROLL_BP_K: float = 0.0         # 横侧模态带通阻尼增益（0=关）
    ROLL_BP_F: float = 1.0         # Hz 带通中心频率（整定自定值）
    ROLL_LEAD_STAGES: int = 2      # 超前级联级数（1=单级旧行为）
    AIL_PROBE_AMP: float = 0.0     # rad 差动正弦注入口幅值（0=关）
    ELEV_PROBE_AMP: float = 0.0    # rad 升降舵正弦注入口幅值（0=关）
    RUD_PROBE_AMP: float = 0.0     # rad 方向舵正弦注入口幅值（0=关）
    MULTISINE_ON: bool = False     # 多正弦激励模式（辨识产线）
    MULTISINE_FREQS: str = "0.2,0.5,1.0,1.5,2.33,3.0,5.0,8.0"  # Hz 网格
    FREQ_PROBE_AMP: float = 0.0    # Hz 拍频调制注入口幅值（五通道之四，0=关）
    DA_PROBE_AMP: float = 0.0      # rad 翼幅值差动注入口幅值（五通道之五，0=关）
    ROLL_TRIM_M: float = 0.0       # N·m 定常滚转矩前馈配平（盘旋工况实测
                                   # 常驻扰矩的数据值，I-005 源头调查并行；
                                   # 0=关；G3/G4 准稳态窗测得约-0.01量级）
    AIL_PROBE_FREQ: float = 2.33   # Hz 注入频率（模态频）
    ROLL_LEAD_FMOD: float = 2.4    # Hz 模态频率（带通中心族）
    ROLL_LEAD_BP_K: float = 1.0    # 带通超前分量叠加增益
    PITCH_LEAD_BP_K: float = 1.0   # 俯仰带通叠加增益（矩阵设计独立档,
                                   # 负号=该通道共振段反馈反号）
    ROLL_LEAD_GATE: float = 0.15   # rad 超前网络启用误差门（大瞬态走
                                  # 原始路径防过猛）
    ROLL_STROKE_AVG: bool = False  # 拍同步平均反馈（宽带边带解耦，
                                   # 滑动窗=当前拍周期）
    ROLL_NOTCH_F: float = 0.0     # Hz 滚转反馈边带陷波中心（0=关）
    ROLL_NOTCH_Q: float = 8.0     # 陷波品质因数（窄而深）
    MODAL_CROSS_K: float = 0.0     # 模态交叉阻尼增益（0=关；两轴反相
                                  # 耦合振荡模态坐标的直接阻尼）
    PITCH_LEAD_TD: float = 0.0    # s 俯仰率反馈超前时间常数（0=关；
                                  # 两轴联合阻尼耦合振荡模态）
    ROLL_LEAD_TD: float = 0.0     # s 滚转速率反馈超前时间常数（0=关；
                                  # 横侧~1Hz模态频段阻尼相位补偿）
    ROLL_KI: float = 0.0          # 滚转误差慢积分增益（0=关=行为不变；
                                  # 吸收盘旋中扑翼重力矩整流的持续外滚矩，
                                  # 转弯线整定自定值）
    ROLL_INT_GATE: float = 0.15   # rad 滚转条件积分误差门（自定、可调）
    ROLL_I_MAX: float = 0.3       # rad/s 滚转积分输出限幅（自定、可调）
    DA3D_DIR: float = 1.0         # 翼差动直通方向系数（+1=历史行为；
                                  # R5/R6 在环判别：dA 正偏置出负滚转力矩
                                  # 与离线标定差一个镜像，转弯线取 -1）
    ROLL_DBG: bool = False        # 滚转环指令链探针（转弯线判别用，默认关）
    DM_COEXIST_ON: bool = False   # 幅值/密度分幅共存：占空比开启不旁路 dA
    DUTY_PITCH_GATE_ON: bool = False  # 密度通道俯仰门控
    DM_PITCH_ZERO_DEG: float = -4.0   # 俯仰高于此值密度指令归零（零穿越实测）
    DM_PITCH_FULL_DEG: float = -6.0   # 俯仰低于此值全额（门限自定、可调）

    # ── 逐拍差动控制（2026-09-27 冲量积分线，默认关闭）──
    # 拍边界决策：读滚转误差与滚转角速率，输出下一拍两翼各自的
    # 拍/停闸门；老幅值差动通道 dA 在本开关下旁路（两差动不打架）。
    # 决策节拍为控制器侧计时器（1/freq_last），与翼相位的对齐误差
    # 至多半拍，首版口径登记。
    STROKE_DIFF_ON: bool = False
    SD_KP: float = 8.0        # 滚转误差(rad)→拍密度差指令
    SD_KD: float = 1.5        # 滚转角速率(rad/s)→拍密度差阻尼
    SD_DEAD: float = 0.2      # 死区（|δρ|<此值双翼全拍，防小误差抖动）
    SD_ROLL_DIR: float = 1.0  # 符号标定系数（左停右拍出正/负滚转，首跑标定）
    # ── 不定频率完全差动线（2026-09-27 阶段二，默认全关；判据与约束见
    # docs/不定频率完全差动_判据与方案.md，参数登记见参数总表第四部分）──
    # 占空比连续调制：取代二值闸门的密度差动（与 STROKE_DIFF_ON 互斥且
    # 优先）；前馈线性依据=密度线性断言（停拍数与冲量严格线性，比值
    # 2.0000，docs/冲量积分线_阶段一测量报告.md 第二节）。
    DUTY_MOD_ON: bool = False
    DM_KP: float = 4.0        # 滚转误差(rad)→密度差 δρ 比例（自定初值，待整定）
    DM_KD: float = 0.8        # 滚转角速率(rad/s)→δρ 阻尼（自定初值，待整定）
    DM_DEAD: float = 0.05     # δρ 死区（连续口径，低于此值双翼全拍）
    # 频率差动偏航通道：左翼 f+δf、右翼 f−δf；权限依据=阶段一实测
    # ΔJ_Tz≈+0.0052 N·m·s/拍 每 Δf=0.1Hz（巡航点）；每翼频率硬约束
    # [FREQ_DIFF_FLO, FREQ_DIFF_FHI]（2.2 Hz=失效区边界，约束一）。
    FREQ_DIFF_ON: bool = False
    FD_KP: float = 0.0        # 偏航误差(rad)→频率差（自定初值；先纯阻尼）
    FD_KD: float = 0.5        # 偏航角速率(rad/s)→频率差阻尼（自定初值，待整定）
    FD_DIR: float = 1.0       # 符号标定系数（待离线方向标定确认）
    FD_ROLL_SOURCE: bool = False  # 频率差动滚转源档（转弯线：滚转误差
                                   # 驱动频率差做连续滚转执行，默认关）
    FD_MAX: float = 0.4       # 频率差半幅上限 Hz（f=1.8 时每翼不超 2.2）
    FREQ_DIFF_FLO: float = 1.0   # 每翼频率下界（执行器域，约束二）
    FREQ_DIFF_FHI: float = 2.2   # 每翼频率上界（失效区边界，约束一）
    GROUND_Z: float = 0.3      # m 接地联锁高度：低于此高度且速度小时进入怠速并停止主动驱动
                               # （0920_123505 坠地后拍频 5.5Hz 空转 22 秒，造成数据污染）
    FREQ_AP_MIN: float = 1.0   # Hz 巡航保护性拍频下限（2026-09-20 夜间开发第三次裁定：
                               # 第二次裁定 1.55=f* 使 Fx 平衡点被频率下限钳死——高度优先速度
                               # 基准（TECS_ZV_K）需要 f<f* 产生负 Fx 减速释放升力，
                               # 1.55 下限把减速通路整个堵死（z 停在 6.9m 失败实录
                               # logs/harness_215g_v5.log）。1.0 为手动操作频率域下沿，
                               # 旧"防升力骤降"语义已反转失效（∂Fz/∂f<0），防失速
                               # 由速度环与高度优先基准闭环承担）
    LAT_CHI_CASCADE: bool = False # 横向级联开关（第八班证伪：0.6/0.15 两档均带宽倒挂劣化，默认关闭）
    KP_CHI: float = 0.15         # 航向差→滚转增益（rad/rad）（0.6 带宽倒挂 25 秒发散，已降低取值）
    YAW_HOLD: bool = False    # 航向保持开关（第八班默认关闭：协同转弯结构，由坡度控制航向）
    # ── 结构更换（第十一班）：能量分配误差 → 垂直速度通道 ──
    TECS_KV_F: float = -2.0      # Hz/(m/s) 速度误差→拍频油门阻尼（第十一班极点配置；在环实测档）
    TECS_EB_TO_VZ: bool = False  # 结构开关（默认关=旧结构 e_B→俯仰；开=e_B→垂速）
    TECS_EB_V_GAIN: float = 0.1   # (m/s)/(J/kg) 分配误差→期望垂直速度
    TECS_VZ_P: float = 0.15      # rad/(m/s) 垂直速度误差→俯仰（比例）
    TECS_VZ_I: float = 0.03      # rad/(m·s) 垂直速度误差积分→俯仰
    TECS_VZI_MAX: float = 0.5    # m/s 垂速积分限幅（防积分发散历史复现）
    AIL_AUTH: float = 1.0     # 尾翼差动权限映射系数（第九班：0.15 折扣取值是滚转
                              # 权限的泄漏点，尾翼差动是 215 g 唯一有效的滚转通道，因此取全额映射）
    YAW_DAMP: float = 2.5    # 偏航角速率阻尼系数（第七班收尾新增调节参数；2.0 在环不足以压制发散，留作下一班的调整入口）
    ROLL_DAMP: float = 0.45  # 滚转角速率阻尼系数（[67班] 滚转速率反馈独立标度，缺省 1.0=旧行为；
                              # 定位与依据见 modules/flight_controller.py [67班] 注释：215g 尾面积
                              # 减半后 b_ail≈0.077 N·m/rad，共享速率增益在该效能下构成继电器
                              # 极限环（run3 wx 谱峰 10.58 Hz、ail 饱和 49~86%）。
                              # 部署 0.30：速率路径增益除以 3.3，姿态环 ζ≈0.26 保持）
    PITCH_DAMP: float = 0.3  # 俯仰角速率阻尼系数（第九班新增，用于浮沉治理；1.0 为原行为取值）
    RATE_FILT_TAU: float = 0.03  # s 内环角速率低通（0.15 试验在环更差已回退；拍频纹波经尾翼
                                  # 滑流整流的机理待第10轮核查；1.9Hz≈12 rad/s，τ=0.08s
                                  # 对约 12.5 rad/s 以上的成分截止，用于防止姿态通道泵能）
    OUTER_FILT_TAU: float = 0.08  # s 外环速度 EWMA（与相位积分状态配套；0.15 实测使试验架
                                  # 冲量场景测试失败，0.08 与速率环取同档
                                  # 折中——二次拍频分量压制与瞬态响应的矛盾待周期采样
                                  # 方案在相位修复后重测）
    # 翼差动（2026-09-20 夜间开发 215g/0.6m 重新标定，logs/reanchor_scheduling.json）：
    # 旧值 5.2·f²/19.0 属于 2.3m 口径。新被控对象的 B_dA(f) 塌缩两个量级且在巡航带内
    # 变号（1.55Hz +0.041 / 2.0 +0.015 / 2.5 −0.039 / 3.0 −0.118 N·m/rad），
    # f² 调度律因此失效——本组常数在配平邻域（f*≈1.55Hz）取值，差动滚转通道在
    # f>2.2Hz 视为不可信（符号翻转），滚转主通道在新被控对象上为尾翼差动；
    # 该事项的在环裁定列为遗留未决（docs/滑流与调度重锚_215g.md）
    B_DA_SLOPE: float = 0.0169  # N·m/rad/Hz²（配平邻域等效）
    B_DA_REF: float = 0.0406    # N·m/rad 增益整定参考点（f*=1.55Hz，新被控对象的实测值）
    # ── TECS 速度通道实测导数（2026-09-19 trim/windcal/stepx）──
    DFX_DTHETA: float = 0.67   # N/rad 俯仰→前向力（215g/0.6m 新配平点
                               # 邻域重新推导，工况 V=4/f=1.55/θ=−6.2°，证据文件为
                               # logs/reanchor_scheduling.json；旧值 7.40 属于 2.3m 口径）
    DFX_DV: float = 0.18       # N/(m/s) 速度阻尼（同上重新推导；旧值 3.17 属于
                               # 2.3m 口径——0.6m 翼的阻力比 2.3m 口径下降一个数量级）
    PITCH_TRIM: float = -0.143246   # rad 巡航配平俯仰。现行 -0.143246 出自提交 39cfb9a；台架扫阶
                               # 拟合值 11.8 度与 0921 值 -0.05 均已作废。数值以登记库
                               # ctrl.trim.pitch 为准。
                               # 台架扫阶拟合值 11.8° 受分段噪声影响而偏高）
    FREQ_HOVER: float = 1.8   # Hz 巡航拍频基准（215g：=f* 配平点，docs/TRIM_SCAN_215g.md；
                               # 真正的悬停需要对称波形 7.2Hz，该频率超出自动驾驶仪拍频域，见附录二）
    FREQ_MAX_AP: float = 3.50  # Hz 自动驾驶仪拍频上限（215g：2.26×f* 爬升余量；
                               # 悬停 7.2Hz 不在自动驾驶仪拍频域；
                               # 弹射权限说明：3.5×W 的弹射权限不得进入高度环）
    PITCH_TGT_FILT_TAU: float = 0.0  # s 期望俯仰角低通时间常数（第十班候选方案甲；0 表示关闭。历史反面记录：0.5 的慢斜坡曾导致速度环相位滞后）
    PITCH_DES_SLEW: float = 1.2   # rad/s pitch_des 斜坡速率（单变量重试：全量
                                  # 记录 232852 run 直接显示 0.5 的斜坡是速度
                                  # 环相位滞后的主因——俯仰跟踪是瓶颈；
                                  # 上次 1.2 更差是与 KP_V/TILT_MAX 捆绑导致变量
                                  # 无法归因，本次单变量验证。该斜坡同时是俯仰
                                  # 通道的防俯冲措施。注意下限：太慢会让后向
                                  # 推力把机体拖入反向流包线外（模型在该区
                                  # 失真），0.15 实测失控，0.5 闭环通过）
    DFX_FF_MAX: float = 1.0      # N 速度阻尼前馈限幅（≈0.11 rad 等效俯仰；
                                  # 全量前馈会抵消物理阻尼、在饱和区泵入能量并发散）

# 质量分布已迁出（I-002 施工 2026-09-30）：运行时覆写 _unify_mass 已整体
# 删除，URDF 声明值为唯一来源（几何法重算，tools/inertia_geometric_recalc.py），
# 数值链与退役史见 params_registry/（vehicle.mass.link.* 与 archive.json）。
# 历史口径（54g 翼组、base_link 补差）的证据链存 docs/INERTIA_TABLE_215g.md。

# 【甲项隔离实验 2026-09-20，临时】关闭飞控，实验后恢复 True
FLIGHT_CONTROLLER_ENABLED: bool = True

# 循迹模式（bishop 的 pos_cmd 桥接进来后如何驱动机体）：
#   "kinematic" — 运动学跟踪：位置P环+速度前馈 → 直写刚体速度（绕过气动模型，供桥接联调使用）
#   "aero"      — 气动跟踪：不写速度，pos_cmd 的高度/偏航喂 AeroPhysicsCallback
#                 的 HoverPD（后续在此路径上接 TECS+串级PID，见 SparrowHawk V.B）
# 两种模式互斥：kinematic 的 set_linear_velocities 会覆盖 PhysX 气动积分结果，
# 与 AeroPhysicsCallback 的力注入/HoverPD 相互冲突，禁止在 aero 模式下使用。
TRACKING_MODE: str = "kinematic"

# ============================================================
# 8f. 门洞感知组件（perception/，复刻 SparrowHawk TII 2026 §V.A）
#     默认全部关闭；ENABLE_GATE_PERCEPTION=True 时 main_sim 生成门洞并随行采集
# ============================================================

ENABLE_GATE_PERCEPTION: bool = False
GATE_POSITION = (10.0, 0.0, 1.6)   # 门孔圆心世界坐标 (m)
GATE_YAW_DEG: float = 0.0          # 门面法向水平偏航（0 = +X）
GATE_INNER_R: float = 0.40         # 门洞内径 80cm（论文 §VI.C）
GATE_OUTER_R: float = 0.55         # 环带外半径 (m)
GATE_DEPTH: float = 0.08           # 通道轴向深度 (m)

# 采集/光照（normal | lowlight；lowlight 弱光档复现论文 60% 光照工况）
PERCEPTION_LIGHT_PRESET: str = "normal"
PERCEPTION_COLLECT_DIR: str = r"g:\Playground\isaac_flapper_sim\data\ridealong"
# 检测结果 UDP 外发端口（perception/live.py，JSON 协议见该文件）
PERCEPTION_UDP_PORT: int = 9012

# ============================================================
# 9. SimulationApp 启动配置 —— 直接沿用 USV 版
# ============================================================

SIM_APP_CONFIG: dict = {
    "headless": bool(os.environ.get("FLAPPER_HEADED", "0") == "0"),  # 默认无头模式（GUI 模式时基膨胀 1.75 倍的根因）；FLAPPER_HEADED=1 时回到 GUI 模式
    "width": 1920,
    "height": 1080,
    "title": "Flapping-Wing MAV Simulation — Isaac Sim 5.1.0",
    "anti_aliasing": 3,                      # TAA
}

# 日志级别
LOG_LEVEL: str = "INFO"

# 常规模式飞行全量记录开关（夜间开发 2026-09-20：在环验收使用；
# EXPERIMENT_MODE 非空时该开关恒为开启，此处只控制常规模式）
FLIGHT_STREAM_LOG: bool = bool(os.environ.get("FLAPPER_FLIGHT_LOG"))


# ============================================================
# 11. 传感器参数类 (Sensor Config Classes) —— 直接沿用 USV 版
# ============================================================
# 这些 dataclass 风格的配置被 modules/sensor_setup.py 引用，原样保留。
# 安装位置坐标按 USV 船体尺寸标定（米）；扑翼 MAV 体积小很多，
# 实际联调时需按 BODY_LENGTH/WINGSPAN 重新标定 MOUNT_POSITION。

class LidarConfig:
    """RTX LiDAR 传感器参数 — 基于 Ouster OS1-64 规格。"""

    SENSOR_TYPE: str = "Ouster_OS1"

    MOUNT_POSITION: np.ndarray = np.array([0.0, 0.0, 0.05])   # 扑翼尺寸缩小后：安装于机身顶部 5cm 处
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
    MOUNT_POSITION: np.ndarray = np.array([0.0, 0.0, 10.0])   # 安装于机身上方高空处
    COVERAGE_M: float = 6.0


class HUDCameraConfig:
    """HUD 第三人称视角相机。"""

    RESOLUTION: tuple = (1280, 720)
    FOV: float = 90.0
    MOUNT_POSITION: np.ndarray = np.array([-0.3, 0.0, 0.15])   # 安装于机身后上方
    MOUNT_ORIENTATION: np.ndarray = np.array([0.5, 0.5, -0.5, -0.5])


# 模块级快捷别名（sensor_setup.py 引用）
LIDAR_MOUNT_FORWARD: float = LidarConfig.MOUNT_POSITION[0]
LIDAR_MOUNT_HEIGHT: float = LidarConfig.MOUNT_POSITION[2]
CAMERA_RESOLUTION_W: int = CameraConfig.RESOLUTION[0]
CAMERA_RESOLUTION_H: int = CameraConfig.RESOLUTION[1]


# ============================================================
# 10. 向后兼容别名 (Backward-compat aliases)
# ============================================================
# 本段使直接沿用自 USV 工程的模块（如 usv_loader、obstacle_manager、sensor_setup）
# 中的 `from config.sim_config import USV_*`、`WATER_LEVEL` 这类引用仍然能够 import 成功：
# 本段把这些引用重定向到新的扑翼语义变量。后续逐步清除这些模块中的 USV/WATER 命名后，
# 本段即可删除。
WATER_LEVEL: float = GROUND_LEVEL                              # 兼容 obstacle_manager
WATER_PLANE_SIZE: float = GROUND_PLANE_SIZE
WATER_PLANE_THICKNESS: float = GROUND_PLANE_THICKNESS
USV_PRIM_PATH: str = FLAPPER_PRIM_PATH                         # 兼容 usv_loader / main_sim
USV_INITIAL_POSITION: np.ndarray = INITIAL_POSITION
USV_MASS: float = MASS
USV_USD_PATH: str = FLAPPER_USD_PATH
