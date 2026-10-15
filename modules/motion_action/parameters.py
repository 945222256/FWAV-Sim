# -*- coding: utf-8 -*-
"""parameters.py — 参数装配（事项 I-219 返修 v2，L2 队）。

参数装配显式接收自动拍频、手动拍频、备用拍频（备用定高）及基准快照；
各通道优先级、缺省、归零、保持和模式退出规则可执行；装配层不读取主
程序全局字典（输入全部经构造参数显式传入）；差频专用限制不增加为集
体拍频的第二层限制（集体拍频只受自动驾驶仪界限一次限幅）。

返修 v2（Fil 复核 F01/F04/F05）要点：
一、尾舵绝对命令与相对配平身份分开：无指令时配平采用一次（目标=配
    平），有显式升降指令（含明确零值）时按旧语义取升降加减副翼的绝
    对目标，不再叠加第二次配平；命令的唯一持有者为参数装配的控制修
    正通道，程序侧（tail_hold_v1）不再自行加配平。
二、逐通道策略接入实际装配：channel_policies/last_adopted/mode_
    baselines 逐通道登记并传入，缺省、归零、保持与模式退出在装配入
    口真实执行，采用记录逐通道写明身份（缺省/明确零值/绝对命令/保
    持/退出回归）。
三、门控与差频固定为独立透传输入：翼面通道按配方的 gating_key 与差
    动键把门控值和差频请求登记入通道参数（gate_value、
    differential_frequency_hz），装配层只登记不叠加（协调出口在
    I-220）。
四、差动参与由配方绑定表驱动：recipe_channels 给出时，dA/dlag 是否
    参与左右通道按该表 differential_keys 处理；未给表时按 I-138 契
    约默认两翼都参与。左右扭转滞后与差动键同一表声明，装配与配方不
    再各自描述。

分工（判据四）：output = center + scale * shape + control_correction；
center 与 scale 各有来源键；幅值差动（amp differential）与旧角偏置
（legacy angular offset）分列；形状权重不重复吸收中心、幅值及相差。

反馈归属（节律补充二节）：现有 freq 及有效拍频选择结果（I-210）作为
采用后的速度预算接入，同一能量或姿态反馈不在节律层重复相加——装配
层只登记来源身份（"现有闭环采用值"），不伪造分量。

导数规则（判据五）：output_with_derivative 按同一输出规则
y = sat(center + scale*shape + control) 的链式展开求导；必需导数不可
用时返回 None，不填假零。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

#: 频率来源优先级（判据三：手动 > 自动 > 备用 > 基准保持）
FREQ_PRIORITY = ("manual", "auto", "backup", "baseline_hold")

#: 通道缺省策略名
CHANNEL_DEFAULT_POLICY = "use_command_default"   # 用指令缺省值
CHANNEL_ZERO_POLICY = "zero"                     # 归零（如释放后尾舵）
CHANNEL_HOLD_POLICY = "hold_last"                # 保持上一采用值
CHANNEL_MODE_EXIT_POLICY = "revert_to_baseline"  # 模式退出回归基准

#: 采用身份（F01/F05：缺省、明确零值、绝对命令等分别表达）
ADOPT_MISSING_DEFAULT = "missing_default"        # 无指令，取缺省
ADOPT_ABSOLUTE_COMMAND = "absolute_command"      # 显式指令（含零值），绝对目标
ADOPT_TRIM_ONCE = "trim_adopted_once"            # 无指令，配平采用一次
ADOPT_ZERO = "zero"                              # 归零策略
ADOPT_HOLD_LAST = "hold_last"                    # 保持上一采用值
ADOPT_MODE_EXIT = "revert_to_baseline"           # 模式退出回归基准


@dataclass(frozen=True)
class FreqSource:
    """单个频率来源的显式身份（不读全局字典）。"""
    kind: str                    # manual/auto/backup/baseline_hold
    value_hz: Optional[float]    # 该来源的值；不可用为 None
    version: str = ""            # 来源版本（如备用定高队列版本）


# ── 反馈归属表（节律补充二节：逐项登记来源、名义值、反馈包含、修正、
#    单位、最终采用者、限幅与生效时刻；I-219 参数职责表承接） ──
@dataclass(frozen=True)
class FeedbackAttribution:
    """单条反馈输入的归属登记行。"""
    key: str                     # 输入键（freq/dfreq/phase_error/…）
    request_source: str          # 请求来源
    nominal: str                 # 原值／名义值表达
    existing_feedback_included: bool  # 已有闭环反馈是否已包含在该值内
    correction_outlet: str       # 修正量及注入出口
    unit: str                    # 单位
    final_adopter: str           # 最终采用者
    limiting: str                # 限幅说明
    effective_at: str            # 生效时刻


#: 首个合作配方的反馈归属表（节律补充二节五行；同一能量／姿态反馈不在
#: 节律层重复相加，禁止新旧路径同时注入同一差动）。
FEEDBACK_ATTRIBUTION_TABLE: Tuple[FeedbackAttribution, ...] = (
    FeedbackAttribution(
        key="freq", request_source="现有闭环输出及 I-210 有效拍频选择结果",
        nominal="现有闭环采用值（无法分解时保留真实身份，不伪造分量）",
        existing_feedback_included=True,
        correction_outlet="作为采用后的速度预算接入节律，不再叠加",
        unit="Hz", final_adopter="参数装配 resolved_freq",
        limiting="自动驾驶仪频率域一次限幅；差频限制不构成第二层",
        effective_at="模式采用步"),
    FeedbackAttribution(
        key="dfreq", request_source="控制器左右差动请求",
        nominal="名义差动值",
        existing_feedback_included=False,
        correction_outlet="协调规则产出两翼实际采用速度并记录；单点注入",
        unit="Hz", final_adopter="节律协调（I-220）",
        limiting="FREQ_DIFF 域仅约束差频通道",
        effective_at="模式采用步"),
    FeedbackAttribution(
        key="phase_error", request_source="相位关系误差（参与节点间）",
        nominal="按已声明周期及坐标回绕的误差",
        existing_feedback_included=False,
        correction_outlet="进入相对推进或耦合修正出口 d_epsilon_advance",
        unit="rad", final_adopter="耦合修正（I-220/I-221）",
        limiting="按配方采用规则处理与差频请求的冲突并记录",
        effective_at="耦合求值步（统一提交一次）"),
    FeedbackAttribution(
        key="mean_amp_twist_tail", request_source="中置、幅值、扭转与尾舵修正",
        nominal="原分配口径",
        existing_feedback_included=True,
        correction_outlet="继续按原分配及明确控制权装配，不新增增益",
        unit="rad（滞后为 s）", final_adopter="参数装配 assemble",
        limiting="原限幅（DELTA_MAX 等）保持",
        effective_at="本步装配"),
    FeedbackAttribution(
        key="primitive_events", request_source="基元反馈、拍边或复位事件",
        nominal="内部状态与相位修正分列",
        existing_feedback_included=False,
        correction_outlet="事件保持原段号、唯一消费、预览隔离及过渡规则",
        unit="-", final_adopter="节律与基元（I-220/I-221）",
        limiting="声明过渡或分段，不用假零补齐",
        effective_at="事件边界步"),
)


# ── 同一输出规则的导数（判据五后半） ──
ASSEMBLY_DERIVATIVE_RULE = "same_output_chain_rule"


def output_with_derivative(center: float, scale: float, shape: float,
                           shape_deriv: Optional[float],
                           control: float = 0.0,
                           d_center: float = 0.0, d_scale: float = 0.0,
                           d_control: float = 0.0,
                           limits: Optional[Tuple[float, float]] = None
                           ) -> Tuple[float, Optional[float], str]:
    """按同一输出规则求装配输出及其导数。

    输出规则 y = sat(center + scale*shape + control)，sat 为限幅（可空）。
    导数按同一规则的链式展开：dy = sat'(x) * (d_center + d_scale*shape
    + scale*shape_deriv + d_control)，其中 x 为未限幅装配值。规则要点：
    一、shape_deriv 为 None（必需导数不可用）时返回导数 None，不填假零；
    二、限幅饱和区内 sat' 取零是该输出规则的真实导数，不是假零；
    三、恰在限幅边界时左右导数不同，导数判为不可用（None）并注明分段；
    四、软启动与过渡作为时变 scale（d_scale 项）进入同一展开。
    返回 (y, dy, note)。
    """
    x = center + scale * shape + control
    if limits is None:
        y, note = x, "未限幅"
        gate = 1.0
    else:
        lo, hi = min(limits), max(limits)
        y = min(max(x, lo), hi)
        note = "限幅[%+0.4f,%+0.4f]" % (lo, hi)
        if (x < lo or x > hi) and abs(y - x) > 0.0:
            gate = 0.0            # 饱和区内：输出的真实导数为零
            note += "（饱和，dy=0 为该输出规则真实导数）"
        elif abs(x - lo) < 1e-12 or abs(x - hi) < 1e-12:
            gate = None           # 恰在边界：左右导数不同，不可用
            note += "（边界，导数不可用：分段）"
        else:
            gate = 1.0
    if shape_deriv is None:
        return y, None, note + "；形状导数不可用，不填假零"
    dx = d_center + d_scale * shape + scale * shape_deriv + d_control
    if gate is None:
        return y, None, note
    return y, gate * dx, note


@dataclass
class ChannelParameters:
    """装配后的单通道参数集（分工四元组 + v2 采用身份与透传输入）。"""
    joint: str
    center: float                     # 中心（来源键记录）
    center_source: str
    scale: float                      # 尺度（幅值）
    scale_source: str
    shape_weight: float = 1.0         # 形状权重（不吸收中心幅值相差）
    amplitude_differential: float = 0.0   # 幅值差动（分列，不作中心）
    legacy_angular_offset: float = 0.0    # 旧角偏置（分列，phi±dA 旧语义，rad）
    lag_s: float = 0.0                    # 俯仰滞后（秒；dlag 差动叠加，不混入角度）
    control_correction: float = 0.0       # 控制修正（尾舵类）
    presence: str = "value"               # 三态：missing/unsupported/zero
    command_identity: str = ADOPT_ABSOLUTE_COMMAND  # 本步采用身份（F01/F05）
    trim_reference: Optional[float] = None    # 配平基准登记（尾舵；仅采用一次）
    gate_value: Optional[float] = None        # 门控透传值（按配方 gating_key）
    differential_frequency_hz: Optional[float] = None  # 差频请求透传（不叠加）


@dataclass
class ParameterAssembly:
    """参数装配器：显式输入，产出九通道参数与采用记录。

    输入（判据三 + F05）：
    - freq_sources: 各来源频率（含备用定高拍频及版本）
    - baseline: 基准快照（中心、尺度、门控、滞后等现行值）
    - command: 当前指令字典（十二键，I-138 契约）
    - channel_policies: 逐通道策略（joint → 策略名；缺省
      use_command_default）
    - last_adopted: 逐通道上一采用值（hold_last 用）
    - mode_baselines: 逐通道模式退出基准（revert_to_baseline 用）
    - recipe_channels: 配方九行绑定表（joint → ChannelBinding）；
      差动参与（dA/dlag）与门控键按该表驱动，未给表时按 I-138 契约
      两翼默认参与。
    输出：assemble() 产出各通道参数与频率、逐通道采用记录。
    """
    freq_sources: Dict[str, FreqSource]
    baseline: Dict[str, float]
    command: Dict[str, float]
    adoption: Dict[str, str] = field(default_factory=dict)
    channel_policies: Dict[str, str] = field(default_factory=dict)
    last_adopted: Dict[str, float] = field(default_factory=dict)
    mode_baselines: Dict[str, float] = field(default_factory=dict)
    recipe_channels: Optional[Dict[str, object]] = None

    def resolved_freq(self) -> Tuple[str, float, str]:
        """按优先级取频率；返回 (来源名, 值, 说明)。

        差频限制不加第二层：集体拍频不因 dfreq 存在而额外收窄；dfreq
        只在翼面通道内生效（与 FREQ_DIFF_FLO/HI 域的关系由消费层处理）。
        """
        for kind in FREQ_PRIORITY:
            src = self.freq_sources.get(kind)
            if src is not None and src.value_hz is not None:
                note = ("现有闭环采用值（含 I-210 有效频率选择结果），"
                        "同一能量/姿态反馈不重复相加" if kind == "auto"
                        else "备用定高拍频（版本 %s）" % (src.version or "?")
                        if kind == "backup" else kind)
                return kind, float(src.value_hz), note
        base = self.baseline.get("freq_hz")
        if base is not None:
            return "baseline_hold", float(base), "全部来源不可用，保持基准"
        raise ValueError("参数装配失败：无任何可用频率来源")

    def _table_keys(self, joint: str) -> Tuple[str, ...]:
        """该关节在配方绑定表声明的差动键（无表时按契约返回空表示默认）。"""
        row = (self.recipe_channels or {}).get(joint)
        if row is None:
            return ()  # 无表：默认契约（由调用侧按默认两翼参与处理）
        return tuple(getattr(row, "differential_keys", ()) or ())

    def _table_gating_key(self, joint: str) -> Optional[str]:
        row = (self.recipe_channels or {}).get(joint)
        if row is None:
            return None
        return getattr(row, "gating_key", None)

    #: 支持逐通道策略的关节（v3 缺口一：折翼无指令适用量，声明策略即
    #: 定位拒绝，不静默忽略）
    POLICY_SUPPORTED_JOINTS = ("flap_L", "flap_R", "twist_L", "twist_R",
                               "tail_yaw", "tail_L", "tail_R")
    KNOWN_POLICIES = (CHANNEL_DEFAULT_POLICY, CHANNEL_ZERO_POLICY,
                      CHANNEL_HOLD_POLICY, CHANNEL_MODE_EXIT_POLICY)

    def _validate_channel_policies(self):
        """逐项校验传入的通道策略（v3 缺口一：所有声明通道都定位拒绝）。"""
        for joint, policy in self.channel_policies.items():
            if joint not in self.POLICY_SUPPORTED_JOINTS:
                raise ValueError(
                    "通道 %r 不支持逐通道策略（折翼通道无指令适用量；支持"
                    "策略的通道：%s）" % (joint,
                                         ", ".join(self.POLICY_SUPPORTED_JOINTS)))
            if policy not in self.KNOWN_POLICIES:
                raise ValueError("未知通道策略：%r（关节 %s；合法：%s）"
                                 % (policy, joint, ", ".join(
                                     self.KNOWN_POLICIES)))

    def _adopt(self, joint: str, candidate: float,
               command_present: bool) -> Tuple[float, str]:
        """对最终关节候选目标采用该通道策略，返回 (采用值, 采用身份)。

        v3 缺口一：策略作用在最终关节目标（尾舵为升降加减副翼后的该侧
        目标，翼面为该侧差动量），上一采用值与模式退出基准都对应同一最
        终目标，不再当作升降分量二次混合。
        """
        policy = self.channel_policies.get(joint, CHANNEL_DEFAULT_POLICY)
        if policy == CHANNEL_DEFAULT_POLICY:
            return (float(candidate),
                    ADOPT_ABSOLUTE_COMMAND if command_present
                    else ADOPT_MISSING_DEFAULT)
        if policy == CHANNEL_ZERO_POLICY:
            return 0.0, ADOPT_ZERO
        if policy == CHANNEL_HOLD_POLICY:
            last = self.last_adopted.get(joint)
            if last is None:
                return float(candidate), ADOPT_MISSING_DEFAULT
            return float(last), ADOPT_HOLD_LAST
        if policy == CHANNEL_MODE_EXIT_POLICY:
            base = self.mode_baselines.get(joint)
            if base is None:
                return float(candidate), ADOPT_MISSING_DEFAULT
            return float(base), ADOPT_MODE_EXIT
        raise ValueError("未知通道策略：%r（关节 %s；合法：%s）"
                         % (policy, joint, ", ".join(self.KNOWN_POLICIES)))

    def assemble(self) -> Dict[str, ChannelParameters]:
        """装配九通道参数（显式输入，不读全局）。

        尾舵（F01 保持）：无升降指令时配平采用一次（目标=配平）；有显
        式升降指令（含明确零值）时目标为升降加减副翼的绝对命令，不再叠
        加配平。命令唯一持有者为本控制修正通道。

        逐通道策略（v3 缺口一）：先形成左右各自候选目标，再对每个关节
        分别采用策略；右尾策略真实读取；左右可声明不同策略；保持与退
        出对应最终关节目标。翼面通道的适用量为该侧差动值（拍动 dA、扭
        转 dlag），策略真实执行；折翼通道无指令适用量，声明策略在装配
        前即被定位拒绝；未知策略在所有声明通道均定位拒绝。
        """
        self._validate_channel_policies()
        kind, freq_hz, note = self.resolved_freq()
        self.adoption["freq"] = "%s=%.4fHz（%s）" % (kind, freq_hz, note)
        cmd = self.command
        bl = self.baseline
        # dA 的两种旧语义分列（I-190 DA_JOINT_AMP_ON）：开启时为幅值差动
        # （±dA·sin(ph)），关闭时为旧角偏置（flap=phi±dA）。两值不同时
        # 生效，装配按基准快照的开关显式选择，不静默合并。
        da_amp_on = bool(bl.get("da_joint_amp_on", False))
        # dlag 为秒单位的俯仰滞后差动（契约：左翼加 dlag、右翼减 dlag），
        # 与弧度角度量分列，不得塞进角偏置字段。
        pitch_lag_s = float(bl.get("pitch_lag_s", 0.0) or 0.0)
        out: Dict[str, ChannelParameters] = {}
        # 翼面六通道：中心=MEAN（中置上反保留），尺度=AMPLITUDE。
        # 差动参与按配方表驱动（F04）；差动值经逐关节策略采用（v3）。
        da_present = "dA" in cmd
        da_candidate = float(cmd.get("dA", 0.0) or 0.0) if da_present else 0.0
        dlag_present = "dlag" in cmd
        dlag_candidate = (float(cmd.get("dlag", 0.0) or 0.0)
                          if dlag_present else 0.0)
        for side in ("L", "R"):
            sgn = +1.0 if side == "L" else -1.0
            flap_joint = "flap_%s" % side
            twist_joint = "twist_%s" % side
            flap_keys = self._table_keys(flap_joint)
            twist_keys = self._table_keys(twist_joint)
            da_participates = "dA" in flap_keys if self.recipe_channels \
                else True
            dlag_participates = "dlag" in twist_keys if self.recipe_channels \
                else True
            adopted_da, da_identity = self._adopt(flap_joint, da_candidate,
                                                  da_present)
            adopted_dlag, dlag_identity = self._adopt(twist_joint,
                                                      dlag_candidate,
                                                      dlag_present)
            out[flap_joint] = ChannelParameters(
                joint=flap_joint,
                center=bl["mean_rad"], center_source=bl.get("mean_source",
                                                            "kinematics.MEAN"),
                scale=bl["amplitude_rad"],
                scale_source=bl.get("amplitude_source",
                                    "kinematics.AMPLITUDE"),
                amplitude_differential=(sgn * adopted_da
                                        if da_amp_on and da_participates
                                        else 0.0),
                legacy_angular_offset=(sgn * adopted_da
                                       if not da_amp_on and da_participates
                                       else 0.0),
                command_identity=da_identity,
                gate_value=(float(cmd[self._table_gating_key(flap_joint)])
                            if self._table_gating_key(flap_joint) in cmd
                            and self._table_gating_key(flap_joint)
                            is not None else None),
                differential_frequency_hz=(
                    float(cmd["dfreq"]) if "dfreq" in cmd else None))
            self.adoption["channel:%s" % flap_joint] = (
                "identity=%s 差动采用值=%+0.4f rad 参与=%s gate=%s dfreq=%s"
                % (da_identity, adopted_da,
                   "表声明" if "dA" in flap_keys else
                   ("默认契约" if not self.recipe_channels else "表未声明"),
                   out[flap_joint].gate_value,
                   out[flap_joint].differential_frequency_hz))
            out[twist_joint] = ChannelParameters(
                joint=twist_joint,
                center=0.0, center_source="零中心（aero 模式俯仰合成）",
                scale=1.0, scale_source="无量纲（形状函数自含幅度）",
                lag_s=pitch_lag_s + (sgn * adopted_dlag
                                     if dlag_participates else 0.0),
                command_identity=dlag_identity)
            self.adoption["channel:%s" % twist_joint] = (
                "identity=%s 差动采用值=%+0.4f s 参与=%s lag_s=%+0.4f s"
                % (dlag_identity, adopted_dlag,
                   "表声明" if "dlag" in twist_keys else
                   ("默认契约" if not self.recipe_channels else "表未声明"),
                   out[twist_joint].lag_s))
            out["fold_%s" % side] = ChannelParameters(
                joint="fold_%s" % side,
                center=bl.get("fold_center_rad", 0.0),
                center_source="WING_DRIVE_CONFIG 折叠基线",
                scale=bl.get("fold_scale", 1.0),
                scale_source="fold_amp/2（现行生产口径）",
                command_identity=ADOPT_MISSING_DEFAULT)
            self.adoption["channel:fold_%s" % side] = (
                "identity=%s（折翼无指令键，不支持逐通道策略）"
                % ADOPT_MISSING_DEFAULT)
        # 尾舵三通道（F01+v3 缺口一）：先形成左右各自候选目标（升降分量
        # 无指令时取配平一次），再逐关节采用策略。
        trim = float(bl.get("tail_trim_rad", 0.0) or 0.0)
        elev_present = cmd.get("elevator") is not None
        elev_component = (float(cmd["elevator"]) if elev_present else trim)
        ail = float(cmd.get("aileron", 0.0) or 0.0)
        candidates = {"tail_L": elev_component + ail,
                      "tail_R": elev_component - ail}
        for joint in ("tail_L", "tail_R"):
            target, identity = self._adopt(joint, candidates[joint],
                                           elev_present)
            if identity == ADOPT_MISSING_DEFAULT \
                    and abs(candidates[joint] - trim) < 1e-15:
                identity = ADOPT_TRIM_ONCE
            out[joint] = ChannelParameters(
                joint=joint, center=0.0,
                center_source="零中心；配平经采用进入指令，不作第二中心",
                scale=1.0, scale_source="无量纲",
                control_correction=target,
                presence="value",
                command_identity=identity,
                trim_reference=trim)
            self.adoption["channel:%s" % joint] = (
                "identity=%s 目标=%+0.4f rad 候选=%+0.4f rad（配平基准 %s "
                "仅在无指令候选中采用一次）"
                % (identity, target, candidates[joint], trim))
        rudder_present = cmd.get("rudder") is not None
        rudder_candidate = float(cmd["rudder"]) if rudder_present else 0.0
        rudder_adopted, rudder_identity = self._adopt(
            "tail_yaw", rudder_candidate, rudder_present)
        out["tail_yaw"] = ChannelParameters(
            joint="tail_yaw", center=0.0, center_source="零中心",
            scale=1.0, scale_source="无量纲",
            control_correction=rudder_adopted,
            presence="value" if rudder_present
            or rudder_identity in (ADOPT_HOLD_LAST, ADOPT_MODE_EXIT,
                                   ADOPT_ZERO)
            else "missing",
            command_identity=rudder_identity)
        self.adoption["channel:tail_yaw"] = (
            "identity=%s 目标=%+0.4f rad"
            % (rudder_identity, rudder_adopted))
        self.adoption["tail"] = ("尾舵绝对命令唯一持有者=参数装配控制修正"
                                 "通道；无指令配平采用一次，显式指令（含零"
                                 "值）为绝对目标，程序侧不再加配平；策略逐"
                                 "关节作用于最终目标")
        self.adoption["mean"] = "中置上反保留（center=%s）" \
            % out["flap_L"].center_source
        self.adoption["dA"] = ("幅值差动与旧角偏置分列（DA_JOINT_AMP_ON=%s）："
                               "amp_diff=%+0.4f rad，legacy_offset=%+0.4f rad"
                               % (da_amp_on,
                                  out["flap_L"].amplitude_differential,
                                  out["flap_L"].legacy_angular_offset))
        self.adoption["dlag"] = ("俯仰滞后差动（秒）：左采用 %s，右采用 %s，"
                                 "叠加基准 %0.4f s，不混入角度量"
                                 % (out["twist_L"].lag_s - pitch_lag_s,
                                    -(out["twist_R"].lag_s - pitch_lag_s),
                                    pitch_lag_s))
        self.adoption["dfreq"] = ("差频请求透传登记于 flap 通道 "
                                  "differential_frequency_hz，装配层不叠加"
                                  "（协调出口在 I-220，不加集体第二层）")
        self.adoption["feedback_attribution"] = "; ".join(
            "%s→%s" % (row.key, row.final_adopter)
            for row in FEEDBACK_ATTRIBUTION_TABLE)
        return out

    @staticmethod
    def resolve_with_policy(policy: str, requested: Optional[float],
                            default: float = 0.0,
                            last_adopted: Optional[float] = None,
                            baseline: Optional[float] = None) -> float:
        """执行通道级缺省／归零／保持／模式退出策略（判据三：可执行）。

        use_command_default：请求为 None 时取缺省值（缺省与零值区别）；
        zero：归零（释放后尾舵等）；
        hold_last：保持上一采用值，无历史时回落缺省；
        revert_to_baseline：模式退出回归基准值，无基准时回落缺省。
        """
        if policy == CHANNEL_DEFAULT_POLICY:
            return default if requested is None else float(requested)
        if policy == CHANNEL_ZERO_POLICY:
            return 0.0
        if policy == CHANNEL_HOLD_POLICY:
            return default if last_adopted is None else float(last_adopted)
        if policy == CHANNEL_MODE_EXIT_POLICY:
            return default if baseline is None else float(baseline)
        raise ValueError("未知通道策略：%r（合法：%s）"
                         % (policy, (CHANNEL_DEFAULT_POLICY, CHANNEL_ZERO_POLICY,
                                     CHANNEL_HOLD_POLICY,
                                     CHANNEL_MODE_EXIT_POLICY)))


def assemble_parameters(freq_sources: Dict[str, FreqSource],
                        baseline: Dict[str, float],
                        command: Dict[str, float],
                        channel_policies: Optional[Dict[str, str]] = None,
                        last_adopted: Optional[Dict[str, float]] = None,
                        mode_baselines: Optional[Dict[str, float]] = None,
                        recipe_channels: Optional[Dict[str, object]] = None,
                        ) -> Tuple[Dict[str, ChannelParameters],
                                   Dict[str, str]]:
    """便捷入口：装配九通道参数并返回采用记录。"""
    pa = ParameterAssembly(freq_sources=freq_sources, baseline=baseline,
                           command=command,
                           channel_policies=channel_policies or {},
                           last_adopted=last_adopted or {},
                           mode_baselines=mode_baselines or {},
                           recipe_channels=recipe_channels)
    return pa.assemble(), pa.adoption
