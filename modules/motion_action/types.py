# -*- coding: utf-8 -*-
"""types.py — 完整动作对象类型（事项 I-219 返修 v2，L2 队）。

完整动作配方、节律快照、当步动作和消费结果类型；九关节逐项元数据；
节律节点及父子、耦合关系的显式格式（节律补充一节）；配方装载与校验
（未知引用、派生环、单位冲突、重复写入可定位）。

返修 v2（Fil 复核 F02/F03/F04/F06/F07/F08）要点：
一、校验先查名称唯一、必需字段、引用与坐标单位组合，再查派生关系；
    父子定义两处（节点 parent 字段与关系表）统一核对；关系图独立查
    环与重复出口；结构错误统一转换为带文件与节点位置的 RecipeError，
    不再外泄 KeyError/TypeError。
二、偏移唯一持有：通道参考相位偏移（phase_offset_rad）与派生关系偏移
    不得同时在同一通道叠加；初始相差从配置实际来源解析（offset_source
    声明 config: 引用时装载期换算），显式非零偏移须登记来源。
三、提供者绑定有唯一 binding_id，通道经 provider_binding 明确引用，
    左右节点选择不再有歧义。
四、发布帧内部映射封装为只读映射（_FrozenMapping），构造时深复制，
    外部别名与消费者都不能修改发布后的快照与当步动作。
五、九关节元数据分列机械关节限位（机构资产）、控制需求限幅与动作生
    成约束三层来源，不新增全通道限幅。
六、适配器必需导数表（ADAPTER_DERIVATIVE_REQUIREMENTS）固定各适配
    器必需量、可接受状态、不可用处理与共同帧接口；通道动作分列速度
    与加速度的值及各自有效身份，不只一个布尔值。

坐标与单位约定：拍动角（flap）为翼展平面内弧度，扭转角（twist）为
翼弦平面内弧度，折叠角（fold）为翼面折转弧度，尾舵偏角为尾翼铰链弧
度；节律坐标为累计拍数（量纲一）或相位（弧度），物理时间为秒。
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── 三态表达（判据一：缺失、未支持与明确零值分别表达） ──
VALUE_PRESENCE_MISSING = "missing"            # 通道未提供值
VALUE_PRESENCE_UNSUPPORTED = "unsupported"    # 配方声明该通道未支持
VALUE_PRESENCE_ZERO = "zero"                  # 明确写入零值
_VALUE_PRESENCE_ALL = (VALUE_PRESENCE_MISSING,
                       VALUE_PRESENCE_UNSUPPORTED, VALUE_PRESENCE_ZERO)


class RecipeError(ValueError):
    """配方错误，带通道或节点定位信息（判据六：可定位错误）。"""

    def __init__(self, message, where=None):
        self.where = where
        super().__init__("%s（位置：%s）" % (message, where or "配方根部"))


# ── 只读映射（F06：发布帧内外都不可修改） ──
class FrozenMapping(dict):
    """只读字典：发布后禁止一切变更操作，读取接口与 dict 一致。

    构造方负责先复制内容（deep_copy_mapping）；本类只保证一旦发布，
    外部别名与消费者调用清空、写入、删除均抛 TypeError。json 序列化
    与 dict 兼容。
    """

    def _frozen(self):
        raise TypeError("发布帧只读：映射已冻结，不允许修改")

    def __setitem__(self, *a, **k):
        self._frozen()

    def __delitem__(self, *a):
        self._frozen()

    def clear(self):
        self._frozen()

    def pop(self, *a, **k):
        self._frozen()

    def popitem(self):
        self._frozen()

    def setdefault(self, *a, **k):
        self._frozen()

    def update(self, *a, **k):
        self._frozen()

    def __ior__(self, *a):
        self._frozen()


def deep_copy_mapping(obj):
    """递归复制并冻结字典结构（值中的可变容器一并处理）。

    返回 FrozenMapping；嵌套 dict 递归冻结，list 复制为元组，其余类
    型原样保留（数值、字符串与冻结数据类本就不可变）。
    """
    if isinstance(obj, dict):
        return FrozenMapping(
            {k: deep_copy_mapping(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(deep_copy_mapping(v) for v in obj)
    if isinstance(obj, tuple):
        return tuple(deep_copy_mapping(v) for v in obj)
    return obj


# ── 九关节逐项元数据（判据一 + F07：三层限位来源分列） ──
@dataclass(frozen=True)
class JointChannelSpec:
    """单个作动坐标的静态元数据（F07：三层分列，不新增限幅）。

    mechanical_limit_rad 为机构资产声明的关节机械限位（下限、上限），
    来源 mechanical_limit_source 指向 URDF 关节行；control_demand_
    limit_source 为控制需求限幅层（如尾舵指令域 DELTA_MAX），仅记录
    来源不在此执行；generation_constraint_source 为动作生成约束层
    （如拍动幅值、攻角设定）。三层各自独立登记，互不冒充；本表不增
    加任何新的全通道限幅，是否限幅由配方与输出规则决定。
    """
    name: str                             # 关节名（与 ACTUATOR_JOINT_NAMES 一致）
    unit: str                             # 单位（rad）
    coordinate: str                       # 坐标说明（如"翼展平面拍动角"）
    mechanical_limit_rad: Tuple[float, float]   # 机构限位（下限、上限）
    mechanical_limit_source: str          # 机构限位来源（URDF 关节行）
    role: str                             # 角色（flap/twist/fold/tail_yaw/tail）
    side: str                             # "L"/"R"/""（尾舵无侧别）
    control_demand_limit_source: Optional[str] = None   # 控制需求限幅层来源
    generation_constraint_source: Optional[str] = None  # 动作生成约束层来源


#: 机构资产（F07 核对来源）：assets/flapper/fwav_15dof_from_group.urdf
#: 各关节 limit 行的 lower/upper 原值（弧度）。检查器对照该文件活核对。
_URDF = "assets/flapper/fwav_15dof_from_group.urdf"

#: 九关节元数据表（判据一：名称、单位、坐标、三层限位来源及角色逐项）
NINE_JOINT_CHANNELS: Tuple[JointChannelSpec, ...] = (
    JointChannelSpec(
        "flap_L", "rad", "翼展平面拍动角（左）", (-1.4, 1.4),
        "%s joint=flap_L limit" % _URDF, "flap", "L",
        None,
        "FlapKinematicsConfig.AMPLITUDE 与差动合成（动作生成约束，非机械限位）"),
    JointChannelSpec(
        "flap_R", "rad", "翼展平面拍动角（右，铰链镜像符号）", (-1.4, 1.4),
        "%s joint=flap_R limit" % _URDF, "flap", "R",
        None,
        "FlapKinematicsConfig.AMPLITUDE 与差动合成（动作生成约束，非机械限位）"),
    JointChannelSpec(
        "twist_L", "rad", "翼弦平面扭转角（左）", (-1.0, 1.0),
        "%s joint=twist_L limit" % _URDF, "twist", "L",
        None,
        "FlapKinematicsConfig.ALPHA_SET 攻角设定（动作生成约束，非机械限位）"),
    JointChannelSpec(
        "twist_R", "rad", "翼弦平面扭转角（右）", (-1.0, 1.0),
        "%s joint=twist_R limit" % _URDF, "twist", "R",
        None,
        "FlapKinematicsConfig.ALPHA_SET 攻角设定（动作生成约束，非机械限位）"),
    JointChannelSpec(
        "fold_L", "rad", "翼面折转角（左）", (0.0, 1.2),
        "%s joint=fold_L limit" % _URDF, "fold", "L",
        None,
        "WING_DRIVE_CONFIG.fold_amp 折叠幅度（动作生成约束，非机械限位）"),
    JointChannelSpec(
        "fold_R", "rad", "翼面折转角（右）", (-1.2, 0.0),
        "%s joint=fold_R limit" % _URDF, "fold", "R",
        None,
        "WING_DRIVE_CONFIG.fold_amp 折叠幅度（动作生成约束，非机械限位）"),
    JointChannelSpec(
        "tail_yaw", "rad", "尾翼偏航铰链角", (-0.6, 0.6),
        "%s joint=tail_yaw limit" % _URDF, "tail_yaw", "",
        "尾舵指令域（wing_actuation 舵偏输出，控制需求限幅层）",
        None),
    JointChannelSpec(
        "tail_L", "rad", "尾翼左舵偏角", (-0.5, 0.5),
        "%s joint=tail_L limit" % _URDF, "tail", "L",
        "升降与副翼合成舵偏（控制需求限幅层，非机械限位）",
        None),
    JointChannelSpec(
        "tail_R", "rad", "尾翼右舵偏角", (-0.5, 0.5),
        "%s joint=tail_R limit" % _URDF, "tail", "R",
        "升降与副翼合成舵偏（控制需求限幅层，非机械限位）",
        None),
)

_JOINT_NAMES = tuple(j.name for j in NINE_JOINT_CHANNELS)

#: 坐标名到单位的固定对应（F02：坐标与单位组合校验的唯一表）。
COORDINATE_UNITS: Dict[str, str] = {
    "cumulative_beats": "beats",
    "phase_rad": "rad",
    "seconds": "s",
}


# ── 节律节点与关系（节律补充一节：显式格式） ──
@dataclass(frozen=True)
class RhythmNode:
    """节律节点：唯一名称、角色、坐标、父节点、推进策略等。

    坐标（coordinate）取 COORDINATE_UNITS 的键（累计拍数、弧度相位
    或秒）；unit 必须等于该坐标的固定单位。派生视图节点无自身状态
    （derived=True），由父节点坐标经关系计算；独立状态节点由推进所
    有者积分。
    """
    name: str                       # 唯一名称，如 "chi_c"
    role: str                       # 如 common/fold/tail_hold/one_shot
    coordinate: str                 # COORDINATE_UNITS 的键
    unit: str                       # 与坐标固定对应（beats/rad/s）
    parent: Optional[str]           # 父节点名；根节点为 None
    derived: bool                   # True=派生视图；False=独立状态
    advance_owner: str              # 推进所有者标识（如 "rhythm_service"）
    initial_zero: float             # 初始零点（节点坐标值）
    segment: int                    # 段号（多段程序中区分段）
    start_stop: str                 # 起停/复位策略名（如 "advance_during_run"）


@dataclass(frozen=True)
class ParentChildRelation:
    """父子派生关系：频率比例、偏移坐标、差动来源、生效时刻。

    校验规则：父节点引用必须存在；父子派生不得成环（CouplingRelation
    允许闭环，与之区分）。offset_value 为 None 时按 offset_source 声明
    的配置来源在装载期解析（config:类.属性:换算 格式）；非零显式偏移
    必须登记 offset_source 来源，不允许无来源数值。

    导出边界（v3）：offset_source 永远保持原始机器可解析表达式（不加
    解释后缀）；装载期解析值与解释写入独立字段 offset_resolution，
    dataclasses.asdict 导出后交同一装载器可回环装载。
    """
    child: str                      # 子节点名
    parent: str                     # 父节点名
    frequency_ratio: float          # 子频率 = 比例 × 父频率（有限正数）
    offset_coordinate: str          # 偏移所在坐标（与子节点坐标一致）
    offset_value: Optional[float]   # 偏移初值；None=按来源解析
    differential_source: Optional[str]   # 差动来源（节律节点名）；可空
    effective_from_beat: float      # 关系生效时刻（有限数）
    offset_source: Optional[str] = None  # 偏移来源（纯机器引用，不加解释）
    offset_resolution: Optional[str] = None  # 装载期解析值与解释（独立字段）


@dataclass(frozen=True)
class CouplingRelation:
    """耦合关系：参与节点、误差坐标、目标关系、修正出口与采用策略。

    耦合网络允许闭环（如锁相环），与父子派生无环要求区分（节律补充）。
    """
    name: str                       # 耦合名，如 "phase_lock_LR"
    participants: Tuple[str, ...]   # 参与节点名
    error_coordinate: str            # 误差坐标（如 "phase_rad"）
    target_relation: str            # 目标关系描述（如 "epsilon→0"）
    correction_outlet: str          # 修正出口（如 "d_epsilon_advance"）
    adoption_policy: str            # 采用策略（如 "proportional_once"）


@dataclass(frozen=True)
class ProviderBinding:
    """提供者绑定：每个程序声明读取哪个节点、哪个坐标（F04：唯一键）。

    binding_id 为绑定行唯一标识（同程序可绑多个节点，每行一个 id）；
    通道经 ChannelBinding.provider_binding 明确引用某一行，左右节点
    选择不再依赖名称歧义。periodic=False 为尾舵保持与单次过渡的非周
    期身份。
    """
    binding_id: str                 # 唯一绑定标识（如 "flap_shape_v1#chi_L"）
    provider: str                   # 程序名（如 "flap_shape_v1"）
    reads_node: str                 # 读取的节点名
    reads_coordinate: str           # 读取的坐标名
    periodic: bool                  # 是否周期程序；非周期可为尾舵保持等


# ── 九通道绑定（判据二：逐项列出全部要素） ──
@dataclass(frozen=True)
class ChannelBinding:
    """单通道绑定行：提供者绑定引用、参考函数与坐标、中心与尺度来源、
    初始状态、镜像、差动、门控、软启动、参考相位偏移及关节与气动映射。

    中心与尺度分工（判据四）：output = center + scale * shape + control，
    center/scale 各有来源键，形状权重不重复吸收中心、幅值及相差。
    F03：phase_offset_rad 为通道参考函数内的相位偏移（弧度），是该通
    道偏移的唯一持有位置之一；与读取节点的派生偏移不得同时非零。
    """
    joint: str                      # 关节名（九关节之一）
    provider: str                   # 提供者（程序名）
    provider_binding: str           # 引用的绑定行 binding_id（唯一键）
    reference_function: str         # 参考函数名（如 "sin(chi)"）
    reference_coordinate: str       # 参考坐标（须等于绑定行读取坐标）
    center_source: str              # 中心来源键（如 "kinematics.MEAN"）
    scale_source: str               # 尺度来源键（如 "kinematics.AMPLITUDE"）
    initial_state: str              # 初始状态描述（如 "ramp_from_zero"）
    mirrored: bool                  # 是否镜像（右翼关节符号约定）
    differential_keys: Tuple[str, ...]   # 差动键（如 ("dA",)）；尾舵可为空
    gating_key: Optional[str]       # 门控键（如 "gate_L"）；无门控为 None
    soft_start: str                 # 软启动策略名（如 "amp_ramp_0.5s"）
    joint_mapping: str              # 关节映射说明（写入哪个目标）
    aero_mapping: str               # 气动映射说明（喂给哪个模型参数）
    derivative_policy: str          # 导数策略（见 DERIVATIVE_POLICIES）
    phase_offset_rad: float = 0.0   # 参考函数内相位偏移（弧度）
    phase_offset_source: Optional[str] = None  # 偏移来源（非零时必填）


#: 适配器必需导数表（判据五 + F08）：固定各适配器必需量、可接受状态、
#: 不可用处理与共同帧接口。具体导数算法由后续基元/轨迹事项实现。
DERIVATIVE_POLICIES = (
    "continuous_analytic",    # 连续解析导数（如 sin/chi）
    "piecewise",              # 分段导数（如分裂周期波形的接缝处）
    "legacy_approximation",   # 旧近似（如数值差分，带声明）
    "event_boundary_unavailable",  # 事件边界不可用（如门控跳变处）
)

#: 通道动作导数量的有效性身份（F08：分别表达，不只一个布尔值）。
DERIVATIVE_STATUS_AVAILABLE = "available"
DERIVATIVE_STATUS_EVENT_BOUNDARY = "event_boundary_unavailable"
DERIVATIVE_STATUS_DECLARED_APPROX = "declared_approximation"
DERIVATIVE_STATUS_NOT_REQUIRED = "not_required"
DERIVATIVE_STATUS_MISSING = "missing"

DERIVATIVE_STATUSES = (
    DERIVATIVE_STATUS_AVAILABLE, DERIVATIVE_STATUS_EVENT_BOUNDARY,
    DERIVATIVE_STATUS_DECLARED_APPROX, DERIVATIVE_STATUS_NOT_REQUIRED,
    DERIVATIVE_STATUS_MISSING,
)


@dataclass(frozen=True)
class AdapterDerivativeRequirement:
    """单个适配器的必需导数契约行（F08，v3 按真实力核分模型分开关）。

    required_derivatives 为该适配器在 condition 声明的模型与开关状态下
    求力或写入所必需的导数量；applies_to 为适用的关节通道（角运动学量
    按关节登记，"all" 表示全部九通道）；accepted_statuses 为这些量可接
    受的通道动作身份；on_unavailable 固定不可用处理；frame 为共同帧接
    口说明。本表只固定可消费接口，不实现具体算法。

    v3 要点：区分关节角运动学（拍动角速度、拍动角加速度，单位
    rad/s 与 rad/s²）与机身世界系状态（机身速度、加速度，另一组状态
    输入），二者不得混同；现行默认三维模型的附加质量力真实消费拍动
    角加速度（modules/aero/sane_dickinson_3d.py 的 phi_ddot 项），缺
    失时该消费无效。
    """
    adapter: str                             # 适配器名
    required_derivatives: Tuple[str, ...]    # 必需导数量
    accepted_statuses: Tuple[str, ...]       # 可接受身份（DERIVATIVE_STATUSES 子集）
    on_unavailable: str                      # 不可用处理
    frame: str                               # 共同帧接口说明
    applies_to: Tuple[str, ...] = ("all",)   # 适用通道（关节名或 "all"）
    condition: str = ""                      # 适用条件（模型与开关状态）


#: 拍动角速度与拍动角加速度量名（关节角运动学，与机身世界系状态分列）。
DERIV_Q_FLAP_ANGULAR_VELOCITY = "flap_angular_velocity"
DERIV_Q_FLAP_ANGULAR_ACCELERATION = "flap_angular_acceleration"
#: 扭转角速度量名（关节角运动学，rad/s；与扭转角加速度区分，后者当前
#: 力核不消费、不强制登记）。
DERIV_Q_TWIST_ANGULAR_VELOCITY = "twist_angular_velocity"

#: 已登记导数量全集（检查完整性用：表行必需量不得超出登记量名）。
DERIV_Q_ALL = (DERIV_Q_FLAP_ANGULAR_VELOCITY,
               DERIV_Q_FLAP_ANGULAR_ACCELERATION,
               DERIV_Q_TWIST_ANGULAR_VELOCITY)

#: 适配器必需导数表（F08 v4）。登记依据为现行真实消费者，各行条件只
#: 由本行自己的开关控制（旋转力与附加质量为两个独立开关，切换其一不
#: 改变另一行需求的成立）：
#: 一、wing_actuation 只写关节位置目标，导数非必需；
#: 二、aero_force（sane_dickinson_3d，附加质量开，即 config 的
#:     ENABLE_ADDED_MASS=True 现行默认）真实读取拍动角速度与拍动角加
#:     速度（phi_dot 与 phi_ddot），仅适用左右拍动通道；与旋转力开关
#:     无关；
#: 三、aero_force（附加质量关）附加质量项不参与，拍动必需量只剩拍动
#:     角速度；
#: 四、aero_force（旋转力开，即 ENABLE_ROTATIONAL=True 现行默认）真
#:     实读取扭转角速度（alpha_dot 进旋转力项），仅适用左右扭转通道；
#:     与附加质量开关无关；旋转力关闭时不强制；不登记扭转角加速度
#:     （当前力核不消费，不强制增加）；尾舵导数当前力核不消费，不登记；
#: 五、action_recorder 记录预览，导数缺失如实记录。
#: 适用边界：以上 aero_force 各行仅覆盖 sane_dickinson_3d；其他气动模
#: 型（如冻结版 sane_dickinson 二维叶素）须按其真实消费另行登记后适
#: 用，不得默认沿用本表。
ADAPTER_DERIVATIVE_REQUIREMENTS: Tuple[AdapterDerivativeRequirement, ...] = (
    AdapterDerivativeRequirement(
        adapter="wing_actuation", required_derivatives=(),
        accepted_statuses=(DERIVATIVE_STATUS_NOT_REQUIRED,),
        on_unavailable="not_applicable",
        frame="关节位置目标写入；逐帧只读 StepAction.channels[joint].value",
        applies_to=("all",), condition="任意模型"),
    AdapterDerivativeRequirement(
        adapter="aero_force",
        required_derivatives=(DERIV_Q_FLAP_ANGULAR_VELOCITY,
                              DERIV_Q_FLAP_ANGULAR_ACCELERATION),
        accepted_statuses=(DERIVATIVE_STATUS_AVAILABLE,
                           DERIVATIVE_STATUS_DECLARED_APPROX),
        on_unavailable="invalidate_consumption",
        frame="拍动关节角运动学：角速度 rad/s 与角加速度 rad/s²（phi_dot、"
              "phi_ddot）；机身世界系速度与加速度属另一组状态输入，不经"
              "本行登记；加速度缺失时对应力求值消费记无效，不填假零",
        applies_to=("flap_L", "flap_R"),
        condition="sane_dickinson_3d 且附加质量力开启"
                  "（AeroModelConfig.ENABLE_ADDED_MASS=True，现行默认；"
                  "与旋转力开关无关）"),
    AdapterDerivativeRequirement(
        adapter="aero_force",
        required_derivatives=(DERIV_Q_FLAP_ANGULAR_VELOCITY,),
        accepted_statuses=(DERIVATIVE_STATUS_AVAILABLE,
                           DERIVATIVE_STATUS_DECLARED_APPROX),
        on_unavailable="invalidate_consumption",
        frame="拍动关节角运动学：角速度 rad/s（phi_dot），与机身世界系"
              "状态分列；附加质量力关闭时拍动角加速度不参与力求值，不强"
              "制提供",
        applies_to=("flap_L", "flap_R"),
        condition="sane_dickinson_3d 且附加质量力关闭"
                  "（AeroModelConfig.ENABLE_ADDED_MASS=False）"),
    AdapterDerivativeRequirement(
        adapter="aero_force",
        required_derivatives=(DERIV_Q_TWIST_ANGULAR_VELOCITY,),
        accepted_statuses=(DERIVATIVE_STATUS_AVAILABLE,
                           DERIVATIVE_STATUS_DECLARED_APPROX),
        on_unavailable="invalidate_consumption",
        frame="扭转关节角运动学：扭转角速度 rad/s（alpha_dot 进旋转力"
              "项），与机身世界系状态分列；缺失时对应力求值消费记无效，"
              "不填假零；不强制扭转角加速度（当前力核不消费）",
        applies_to=("twist_L", "twist_R"),
        condition="sane_dickinson_3d 且旋转力开启"
                  "（AeroModelConfig.ENABLE_ROTATIONAL=True，现行默认；"
                  "与附加质量开关无关，旋转力关闭时不强制）"),
    AdapterDerivativeRequirement(
        adapter="action_recorder", required_derivatives=(),
        accepted_statuses=(DERIVATIVE_STATUS_NOT_REQUIRED,
                           DERIVATIVE_STATUS_MISSING),
        on_unavailable="record_as_missing",
        frame="记录预览；导数缺失如实记录，不影响其他消费者",
        applies_to=("all",), condition="任意模型"),
)


# ── 配方对象 ──
@dataclass(frozen=True)
class MotionRecipe:
    """完整动作配方：九通道绑定表、节律节点、派生关系、耦合与版本。"""
    name: str                            # 配方名（如 "cooperative_v1"）
    version: str                         # 参考归一化与相位零点版本
    channels: Tuple[ChannelBinding, ...]       # 九行绑定表
    rhythm_nodes: Tuple[RhythmNode, ...]
    parent_child: Tuple[ParentChildRelation, ...]
    couplings: Tuple[CouplingRelation, ...]
    provider_bindings: Tuple[ProviderBinding, ...]
    preserves_mean_dihedral: bool = True  # 中置上反保留声明


# ── 节律快照与当步动作（F06：发布后内部映射只读） ──
@dataclass(frozen=True)
class RhythmSnapshot:
    """节律快照：某步各节点坐标与派生视图值（发布后只读）。

    构造时深复制传入映射并封装为 FrozenMapping：外部原字典的后续修改
    不影响快照，消费者也无法修改快照内部内容。
    """
    step: int
    t_s: float
    node_values: Dict[str, float]        # 节点名→坐标值（冻结）
    derived_values: Dict[str, float]     # 派生视图名→值（冻结）

    def __post_init__(self):
        object.__setattr__(self, "node_values",
                           deep_copy_mapping(self.node_values))
        object.__setattr__(self, "derived_values",
                           deep_copy_mapping(self.derived_values))


@dataclass(frozen=True)
class ChannelAction:
    """单通道当步动作：目标值（或三态）、来源与分列导数身份（F08）。

    velocity 与 acceleration 各自有值与有效性身份（derivative_statuses
    按量名登记 DERIVATIVE_STATUSES 之一）；derivative_available 保留为
    求力必需量的总括布尔（wing_actuation 类消费者用），不再作为唯一
    导数表达。
    """
    joint: str
    presence: str                        # 三态或 "value"
    value: Optional[float]               # presence=="value" 时为数值
    source: str                          # 来源描述（提供者+坐标）
    derivative_available: bool           # 求力必需导数是否可用
    derivative_note: str                 # 导数策略说明
    velocity: Optional[float] = None     # 速度值（不可用时 None）
    acceleration: Optional[float] = None  # 加速度值（不可用时 None）
    derivative_statuses: Dict[str, str] = field(
        default_factory=dict)            # 量名→有效性身份（冻结）

    def __post_init__(self):
        object.__setattr__(self, "derivative_statuses",
                           deep_copy_mapping(self.derivative_statuses))


@dataclass(frozen=True)
class StepAction:
    """当步动作：九通道值与节律快照（帧发布后只读，F06）。"""
    step: int
    t_s: float
    rhythm: RhythmSnapshot
    channels: Dict[str, ChannelAction]   # 关节名→通道动作（冻结）

    def __post_init__(self):
        object.__setattr__(self, "channels",
                           deep_copy_mapping(self.channels))


@dataclass(frozen=True)
class ConsumptionResult:
    """消费结果：某消费者对当步动作的采用记录。"""
    consumer: str                        # 消费者名（如 "wing_actuation"）
    step: int
    adopted_joints: Tuple[str, ...]      # 实际采用的关节名
    skipped_joints: Tuple[str, ...]      # 跳过的关节（缺失/未支持）
    note: str                            # 消费说明（如镜像变换）


# ── 装载与校验（F02：先结构后关系，错误全部可定位） ──
def _finite(value, what, where, errs):
    """数值有限性检查；非法即登记可定位错误。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        errs.append("%s 必须为有限数值，实际为 %r%s"
                    % (what, value,
                       "" if isinstance(value, (int, float)) else "（类型非法）"))


def _resolve_offset_from_source(source, where, errs):
    """按 config:类.属性:换算 声明解析偏移值（F04：实际来源解析）。

    换算登记换算式（如 deg/360，度转拍）。来源不可解析或属性不存在时
    登记可定位错误，返回 None。
    """
    if not (isinstance(source, str) and source.startswith("config:")):
        errs.append("%s offset_source %r 不是可解析的 config:类.属性:换算 "
                    "引用" % (where, source))
        return None
    try:
        _path, conv = source[len("config:"):].rsplit(":", 1)
        cls_name, attr = _path.split(".")
    except ValueError:
        errs.append("%s offset_source %r 结构非法（应为 config:类.属性:换算）"
                    % (where, source))
        return None
    import config.sim_config as _cfg
    cls = getattr(_cfg, cls_name, None)
    val = getattr(cls, attr, None) if cls is not None else None
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        errs.append("%s offset_source 指向的 %s.%s 不是数值（实际 %r）"
                    % (where, cls_name, attr, val))
        return None
    if conv == "deg/360":            # 度转累计拍数（左右相位差口径）
        return float(val) / 360.0
    if conv == "identity":
        return float(val)
    errs.append("%s offset_source 换算 %r 未登记（可取 deg/360、identity）"
                % (where, conv))
    return None


def validate_recipe(recipe: MotionRecipe) -> List[str]:
    """校验配方结构，返回可定位错误列表（空列表为通过）。

    顺序（F02）：一、通道齐与关节名合法；二、节点名唯一与坐标单位组
    合；三、绑定行唯一与引用存在；四、父子两处定义统一、关系图无环、
    无重复出口；五、通道引用绑定唯一键与坐标一致；六、偏移唯一持有；
    七、导数策略在表内。
    """
    errs: List[str] = []
    # 一、通道完整与关节名合法
    chan_joints = [c.joint for c in recipe.channels]
    if sorted(chan_joints) != sorted(_JOINT_NAMES):
        missing = set(_JOINT_NAMES) - set(chan_joints)
        extra = set(chan_joints) - set(_JOINT_NAMES)
        errs.append("九通道不全：缺 %s 多 %s" % (sorted(missing), sorted(extra)))
    if len(set(chan_joints)) != len(chan_joints):
        dups = sorted({j for j in chan_joints if chan_joints.count(j) > 1})
        errs.append("存在重复通道绑定：%s" % dups)

    # 二、节点名唯一、坐标单位组合、字段类型
    nodes: Dict[str, RhythmNode] = {}
    for i, n in enumerate(recipe.rhythm_nodes):
        where = "rhythm_nodes[%d] %r" % (i, n.name)
        if n.name in nodes:
            errs.append("节律节点名重复：%s（第 %d 个与先前定义冲突）"
                        % (n.name, i))
            continue
        nodes[n.name] = n
        if n.coordinate not in COORDINATE_UNITS:
            errs.append("%s 坐标 %r 未登记（可取 %s）"
                        % (where, n.coordinate, sorted(COORDINATE_UNITS)))
        elif n.unit != COORDINATE_UNITS[n.coordinate]:
            errs.append("%s 单位冲突：坐标 %s 的固定单位为 %s，声明为 %s"
                        % (where, n.coordinate,
                           COORDINATE_UNITS[n.coordinate], n.unit))
        _finite(n.initial_zero, "%s initial_zero" % where, where, errs)
        if not isinstance(n.segment, int) or isinstance(n.segment, bool):
            errs.append("%s segment 必须为整数" % where)

    # 三、提供者绑定：唯一键、重复行、引用与坐标
    bindings: Dict[str, ProviderBinding] = {}
    for i, pb in enumerate(recipe.provider_bindings):
        where = "provider_bindings[%d] %r" % (i, pb.binding_id)
        if pb.binding_id in bindings:
            errs.append("提供者绑定唯一标识重复：%s" % pb.binding_id)
            continue
        same = [(j, b) for j, b in enumerate(recipe.provider_bindings)
                if b.provider == pb.provider and b.reads_node == pb.reads_node
                and b.reads_coordinate == pb.reads_coordinate]
        if len(same) > 1:
            errs.append("提供者绑定重复声明同一读取（%s 读 %s@%s 出现 %d 次）"
                        % (pb.provider, pb.reads_node, pb.reads_coordinate,
                           len(same)))
        bindings[pb.binding_id] = pb
        if pb.reads_node not in nodes:
            errs.append("%s 读取节点不存在：%s" % (where, pb.reads_node))
            continue
        if pb.reads_coordinate != nodes[pb.reads_node].coordinate:
            errs.append("%s 单位冲突：读取坐标 %s 与节点 %s 坐标 %s 不一致"
                        % (where, pb.reads_coordinate, pb.reads_node,
                           nodes[pb.reads_node].coordinate))

    # 四、父子关系：两处定义统一、引用存在、有限性、差动来源、图无环
    rel_by_child: Dict[str, ParentChildRelation] = {}
    for i, rel in enumerate(recipe.parent_child):
        where = "parent_child[%d] child=%s" % (i, rel.child)
        if rel.child in rel_by_child:
            errs.append("同一子节点重复声明父子关系：%s（父子关系每子唯一，"
                        "重复出口非法）" % rel.child)
            continue
        rel_by_child[rel.child] = rel
        if rel.parent not in nodes:
            errs.append("%s 父节点不存在：%s" % (where, rel.parent))
        if rel.child not in nodes:
            errs.append("%s 子节点不存在：%s" % (where, rel.child))
        _finite(rel.frequency_ratio, "%s frequency_ratio" % where, where, errs)
        if isinstance(rel.frequency_ratio, (int, float)) \
                and not isinstance(rel.frequency_ratio, bool) \
                and math.isfinite(float(rel.frequency_ratio)) \
                and float(rel.frequency_ratio) <= 0:
            errs.append("%s frequency_ratio 必须为正数（子频率=比例×父频率）"
                        % where)
        _finite(rel.effective_from_beat, "%s effective_from_beat" % where,
                where, errs)
        child_node = nodes.get(rel.child)
        if child_node is not None:
            if rel.offset_coordinate != child_node.coordinate:
                errs.append("%s 单位冲突：偏移坐标 %s 与子节点坐标 %s 不一致"
                            % (where, rel.offset_coordinate,
                               child_node.coordinate))
        if rel.offset_value is not None:
            _finite(rel.offset_value, "%s offset_value" % where, where, errs)
            if rel.offset_value not in (None, 0.0):
                src_txt = rel.offset_source if isinstance(
                    rel.offset_source, str) else ""
                if not src_txt.strip():
                    errs.append("%s 非零偏移 %r 未登记 offset_source 来源；"
                                "显式数值须作为明确配方选择登记"
                                % (where, rel.offset_value))
                elif not (src_txt.startswith("config:")
                          or src_txt.startswith("recipe_choice")):
                    errs.append("%s 非零偏移 %r 的来源 %r 既非 config: 实际"
                                "来源引用，也非 recipe_choice 明确配方选择"
                                "登记；另选新数值不得称保留既有值"
                                % (where, rel.offset_value, src_txt[:40]))
        if rel.differential_source is not None \
                and rel.differential_source not in nodes:
            errs.append("%s 差动来源 %r 不是已声明节律节点（差动来源命名空"
                        "间为节律节点名）" % (where, rel.differential_source))
    # 节点 parent 字段与关系表统一：一处声明必须有另一处对应
    for name, n in nodes.items():
        if n.parent is None:
            if name in rel_by_child:
                errs.append("节点 %s 声明无父，但关系表存在其父子关系（两处"
                            "父子定义不一致）" % name)
        else:
            if n.parent not in nodes:
                errs.append("节点 %s 的 parent 引用不存在：%s"
                            % (name, n.parent))
            rel = rel_by_child.get(name)
            if rel is None:
                errs.append("节点 %s 声明父节点 %s，但关系表缺其父子关系"
                            "（两处父子定义不一致）" % (name, n.parent))
            elif rel.parent != n.parent:
                errs.append("节点 %s 两处父子定义不一致：节点字段 %s，关系"
                            "表 %s" % (name, n.parent, rel.parent))
    # 关系图独立查环（覆盖仅经关系表构成的派生环）
    parent_of = {rel.child: rel.parent for rel in recipe.parent_child}
    for start in nodes:
        seen, cur = set(), start
        while cur is not None:
            if cur in seen:
                errs.append("父子派生关系成环：经 %s（关系图）" % start)
                break
            seen.add(cur)
            cur = parent_of.get(cur, nodes[cur].parent if cur in nodes
                                else None)

    # 五、耦合参与节点与坐标
    for i, cp in enumerate(recipe.couplings):
        where = "couplings[%d] %r" % (i, cp.name)
        for p in cp.participants:
            if p not in nodes:
                errs.append("%s 参与节点不存在：%s" % (where, p))
        if cp.error_coordinate not in COORDINATE_UNITS:
            errs.append("%s 误差坐标 %r 未登记" % (where, cp.error_coordinate))

    # 六、通道：绑定引用、坐标一致、差动键、偏移唯一持有、导数策略
    for c in recipe.channels:
        where = "channels[%s]" % c.joint
        pb = bindings.get(c.provider_binding)
        if pb is None:
            errs.append("%s 引用的提供者绑定不存在：%s"
                        % (where, c.provider_binding))
        else:
            if pb.provider != c.provider:
                errs.append("%s 提供者 %s 与绑定行 %s 的提供者 %s 不一致"
                            % (where, c.provider, pb.binding_id, pb.provider))
            if c.reference_coordinate != pb.reads_coordinate:
                errs.append("%s 单位冲突：参考坐标 %s 与绑定行 %s 读取坐标 "
                            "%s 不一致"
                            % (where, c.reference_coordinate, pb.binding_id,
                               pb.reads_coordinate))
            # F03：偏移唯一持有——通道参考相位偏移与读取节点派生偏移
            # 不得同时非零（同一通道只允许一处持有偏移）
            node_offset = 0.0
            rel = rel_by_child.get(pb.reads_node)
            if rel is not None and rel.offset_value is not None:
                node_offset = float(rel.offset_value)
            both = (abs(c.phase_offset_rad) > 0.0 and abs(node_offset) > 0.0)
            if both:
                errs.append("%s 偏移两处重复定义：通道参考相位偏移 %s rad 与"
                            "读取节点 %s 的派生偏移 %s 拍同时非零，偏移只许"
                            "一处持有" % (where, c.phase_offset_rad,
                                            pb.reads_node, node_offset))
        if c.phase_offset_rad not in (0.0,) and not (
                isinstance(c.phase_offset_source, str)
                and c.phase_offset_source.strip()):
            errs.append("%s 非零参考相位偏移 %r 未登记 phase_offset_source "
                        "来源" % (where, c.phase_offset_rad))
        _finite(c.phase_offset_rad, "%s phase_offset_rad" % where, where, errs)
        if c.derivative_policy not in DERIVATIVE_POLICIES:
            errs.append("%s 导数策略不在必需导数表内：%s"
                        % (where, c.derivative_policy))
        if c.joint not in _JOINT_NAMES:
            errs.append("通道绑定关节名非法：%s" % c.joint)
    # 绑定行全部被引用（无孤儿绑定）
    used = {c.provider_binding for c in recipe.channels}
    for bid in bindings:
        if bid not in used:
            errs.append("提供者绑定未被任何通道引用（孤儿绑定）：%s" % bid)
    return errs


def _binding_from_json(d: dict, where: str) -> ChannelBinding:
    """单通道绑定构造；缺键、类型非法抛 RecipeError（带文件与通道位置）。

    v3 第三项：类型在转换前按真实 JSON 类型校验——布尔字段必须是布尔
    （字符串 "false" 不静默转为真）、数值字段必须是数值（列表等非法类
    型带位置拒绝）、字符串字段必须是字符串，不外泄普通 TypeError。
    """
    required = ("joint", "provider", "provider_binding", "reference_function",
                "reference_coordinate", "center_source", "scale_source",
                "initial_state", "mirrored", "soft_start", "joint_mapping",
                "aero_mapping", "derivative_policy")
    missing = [k for k in required if k not in d]
    if missing:
        raise RecipeError("通道绑定缺必需键：%s" % missing, where=where)
    joint = d["joint"]
    loc = "%s channels.%s" % (where, joint)

    def _need_str(key):
        if not isinstance(d[key], str) or not d[key]:
            raise RecipeError("字段 %s 必须为非空字符串，实际为 %r（类型 "
                              "%s）" % (key, d[key], type(d[key]).__name__),
                              where=loc)

    for key in ("joint", "provider", "provider_binding",
                "reference_function", "reference_coordinate",
                "center_source", "scale_source", "initial_state",
                "soft_start", "joint_mapping", "aero_mapping",
                "derivative_policy"):
        _need_str(key)
    if not isinstance(d["mirrored"], bool):
        raise RecipeError("字段 mirrored 必须为布尔值，实际为 %r（类型 "
                          "%s）；字符串 \"false\" 会被通用真值转换成真，"
                          "不接受静默转换"
                          % (d["mirrored"], type(d["mirrored"]).__name__),
                          where=loc)
    raw_offset = d.get("phase_offset_rad", 0.0)
    if "phase_offset_rad" in d and (isinstance(raw_offset, bool)
                                     or not isinstance(raw_offset,
                                                       (int, float))):
        raise RecipeError("字段 phase_offset_rad 必须为数值（缺省 0.0），实"
                          "际为 %r（类型 %s），不做静默转换"
                          % (raw_offset, type(raw_offset).__name__),
                          where=loc)
    dk = d.get("differential_keys", [])
    if not isinstance(dk, list) or any(not isinstance(k, str) or not k
                                       for k in dk):
        raise RecipeError("字段 differential_keys 必须为字符串列表，实际"
                          "为 %r" % (dk,), where=loc)
    gk = d.get("gating_key")
    if gk is not None and (not isinstance(gk, str) or not gk):
        raise RecipeError("字段 gating_key 必须为非空字符串或 null，实际"
                          "为 %r" % (gk,), where=loc)
    pos = d.get("phase_offset_source")
    if pos is not None and (not isinstance(pos, str) or not pos):
        raise RecipeError("字段 phase_offset_source 必须为非空字符串或 "
                          "null，实际为 %r" % (pos,), where=loc)
    return ChannelBinding(
        joint=joint, provider=d["provider"],
        provider_binding=d["provider_binding"],
        reference_function=d["reference_function"],
        reference_coordinate=d["reference_coordinate"],
        center_source=d["center_source"], scale_source=d["scale_source"],
        initial_state=d["initial_state"], mirrored=d["mirrored"],
        differential_keys=tuple(dk),
        gating_key=gk,
        soft_start=d["soft_start"], joint_mapping=d["joint_mapping"],
        aero_mapping=d["aero_mapping"],
        derivative_policy=d["derivative_policy"],
        phase_offset_rad=float(raw_offset),
        phase_offset_source=pos)


def _construct_rows(rows, cls, label, path):
    """逐行构造数据类；TypeError/KeyError 与布尔类型错误统一转可定位
    RecipeError（v3 第三项：布尔字段必须是真实布尔，字符串不静默转真）。
    """
    out = []
    for i, d in enumerate(rows):
        where = "%s 第 %d 行%s（文件 %s）" % (
            label, i + 1,
            "（%s）" % d.get("name", d.get("child", d.get("binding_id", "?")))
            if isinstance(d, dict) else "", path)
        if not isinstance(d, dict):
            raise RecipeError("%s 必须为 JSON 对象，实际为 %s"
                              % (where, type(d).__name__), where=where)
        for f in getattr(cls, "__dataclass_fields__", {}).values():
            if f.type == "bool" and f.name in d \
                    and not isinstance(d[f.name], bool):
                raise RecipeError("字段 %s 必须为布尔值，实际为 %r（类型 "
                                  "%s）；字符串 \"false\" 会被通用真值转换"
                                  "成真，不接受静默转换"
                                  % (f.name, d[f.name],
                                     type(d[f.name]).__name__), where=where)
        try:
            out.append(cls(**d))
        except TypeError as exc:
            raise RecipeError("%s 存在未知或非法字段：%s" % (where, exc),
                              where=where)
        except KeyError as exc:
            raise RecipeError("%s 缺必需键：%s" % (where, exc), where=where)
    return tuple(out)


def _numeric_or_none(value):
    """数值或 None 判定（布尔不是数值，字符串不是数值）。"""
    return value is None or (isinstance(value, (int, float))
                             and not isinstance(value, bool))


def load_recipe(path: str) -> MotionRecipe:
    """从 JSON 装载配方；结构、类型与数值转换错误统一抛可定位
    RecipeError（不外泄原始 KeyError/TypeError，不静默改类型）。

    偏移解析（v3 导出边界）：offset_source 保持原始机器可解析表达式；
    装载期解析值与解释写入独立字段 offset_resolution，asdict 导出后交
    本装载器可回环装载。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        raise RecipeError("配方文件不存在", where=path)
    except json.JSONDecodeError as exc:
        raise RecipeError("配方 JSON 解析失败：%s" % exc, where=path)
    if not isinstance(doc, dict):
        raise RecipeError("配方根必须是 JSON 对象", where=path)
    known_top = {"name", "version", "channels", "rhythm_nodes",
                 "parent_child", "couplings", "provider_bindings",
                 "preserves_mean_dihedral"}
    unknown = set(doc) - known_top
    if unknown:
        raise RecipeError("配方含未知顶层键：%s" % sorted(unknown), where=path)
    for key in ("name", "version", "channels", "rhythm_nodes"):
        if key not in doc:
            raise RecipeError("配方缺必需键：%s" % key, where=path)
    pmd = doc.get("preserves_mean_dihedral", True)
    if not isinstance(pmd, bool):
        raise RecipeError("preserves_mean_dihedral 必须为布尔值，实际为 "
                          "%r（字符串不静默转换）" % (pmd,), where=path)
    nodes = _construct_rows(doc["rhythm_nodes"], RhythmNode,
                            "rhythm_nodes", path)
    relations_raw = _construct_rows(doc.get("parent_child", []),
                                    ParentChildRelation, "parent_child", path)
    # 数值字段类型前置校验（v3 第三项：转换前查真实类型，非法类型带位
    # 置拒绝，不外泄 float() 的 TypeError）
    for i, d in enumerate(doc.get("parent_child", [])):
        where = "parent_child 第 %d 行 child=%s（文件 %s）" % (
            i + 1, d.get("child", "?"), path)
        for fkey in ("frequency_ratio", "offset_value", "effective_from_beat"):
            if fkey in d and not _numeric_or_none(d[fkey]):
                raise RecipeError("字段 %s 必须为数值或 null，实际为 %r（类"
                                  "型 %s）" % (fkey, d[fkey],
                                               type(d[fkey]).__name__),
                                  where=where)
    # 偏移来源解析：config: 声明一律在装载期取实际配置读数，核对声明值
    # 与解析值一致；解析值与解释写独立字段，机器引用保持原样
    resolve_errs: List[str] = []
    relations = []
    for rel in relations_raw:
        if isinstance(rel.offset_source, str) \
                and rel.offset_source.startswith("config:"):
            value = _resolve_offset_from_source(
                rel.offset_source, "parent_child child=%s" % rel.child,
                resolve_errs)
            if value is None:
                continue
            if rel.offset_value is not None \
                    and abs(float(rel.offset_value) - value) > 1e-12:
                raise RecipeError(
                    "parent_child child=%s 声明偏移 %r 与来源 %s 的实际解"
                    "析值 %r 不符；config: 来源以实际读数为准，不允许另写"
                    "不一致数值" % (rel.child, rel.offset_value,
                                     rel.offset_source, value), where=path)
            import dataclasses as _dc
            rel = _dc.replace(
                rel, offset_value=value,
                offset_resolution="装载期按 %s 解析=%r（deg/360 为度转累计"
                                  "拍数换算）" % (rel.offset_source, value))
        elif rel.offset_value is None:
            raise RecipeError(
                "parent_child child=%s 的 offset_value 为空且未声明 "
                "offset_source（须给 config:类.属性:换算 来源或显式数值）"
                % rel.child, where=path)
        relations.append(rel)
    if resolve_errs:
        raise RecipeError("偏移来源解析失败：%s" % "；".join(resolve_errs),
                          where=path)
    couplings = _construct_rows(doc.get("couplings", []), CouplingRelation,
                                "couplings", path)
    bindings = _construct_rows(doc.get("provider_bindings", []),
                               ProviderBinding, "provider_bindings", path)
    channels = []
    for i, d in enumerate(doc["channels"]):
        where = "channels 第 %d 行（文件 %s）" % (i + 1, path)
        if not isinstance(d, dict):
            raise RecipeError("%s 必须为 JSON 对象" % where, where=where)
        channels.append(_binding_from_json(d, where))
    recipe = MotionRecipe(
        name=doc["name"], version=doc["version"],
        channels=tuple(channels), rhythm_nodes=nodes,
        parent_child=tuple(relations), couplings=couplings,
        provider_bindings=bindings,
        preserves_mean_dihedral=pmd)
    errs = validate_recipe(recipe)
    if errs:
        raise RecipeError("配方校验失败：%s" % "；".join(errs), where=path)
    return recipe


def load_recipe_by_name(recipes_dir: str, name: str) -> MotionRecipe:
    """按名装载配方；未知配方名抛可定位错误（判据六）。"""
    if not os.path.isfile(os.path.join(recipes_dir, name + ".json")):
        available = [f[:-5] for f in os.listdir(recipes_dir)
                     if f.endswith(".json")] \
            if os.path.isdir(recipes_dir) else []
        raise RecipeError("未知配方 %r（可用：%s）" % (name, available),
                          where=recipes_dir)
    recipe = load_recipe(os.path.join(recipes_dir, name + ".json"))
    if recipe.name != name:
        raise RecipeError("配方文件 %s.json 内声明的配方名为 %r，与请求名"
                          " %r 不符（名字与文件须一致）"
                          % (name, recipe.name, name), where=recipes_dir)
    return recipe
