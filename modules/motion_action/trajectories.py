# -*- coding: utf-8 -*-
"""trajectories.py — 共同轨迹采样接口与提供者（事项 I-221 先行施工，L4 队）。

先行范围（2026-10-15 机主批准的 partial_start，原 I-220 依赖保留）：
本文件实现接收外部相位与规范进度的共同轨迹采样接口；解析、动态基元、
保持及过渡提供者实现同一采样接口（原判据三的先行部分）；轨迹装配完整
计算时变中心、尺度、控制修正及相位的链式导数（原判据四的先行部分）。
本文件以已验收 I-219 对象契约为依据，复用 I-219 parameters 的同一输出
规则；不修改 rhythm.py、runtime.py 或 kinematics.py；解析提供者只读复
用 modules/aero/kinematics.py 的 split_phase_map 公共纯函数（I-220 追加
的只读公共出口）。

分工（立项三节与 I-219 判据四）：提供者只产生归一化形状及其导数；装
配层计算中心、尺度、控制修正与镜像登记；输出规则
q = sat(center + scale*shape + control)，链式导数按同一规则展开：

    q̇  = sat′(x) (μ̇ + ȧ z + a ż + ċ)
    q̈  = sat′(x) (μ̈ + ä z + 2 ȧ ż + a z̈ + c̈)

饱和区内 sat′=0 为该输出规则的真实导数；恰在限幅边界时左右导数不同，
导数判为不可用；任一必需导数缺失时对应输出为 None，不填假零。

时间纪律：所有提供者的 advance 每物理步只提交一次，sample 只读；规范
相位与规范进度一律来自外部时钟样本（CanonicalClockSample，先行版本仅
放行 check_fixture: 前缀）；解析提供者不积累任何相位状态。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from modules.aero.kinematics import split_phase_map
from modules.motion_action.primitives import (
    CanonicalClockSample, CouplingInput, DiscretePrimitive, PrimitiveSample,
    RhythmicPrimitive, validate_clock_source)
from modules.motion_action.types import (
    DERIV_Q_FLAP_ANGULAR_ACCELERATION, DERIV_Q_FLAP_ANGULAR_VELOCITY,
    DERIV_Q_TWIST_ANGULAR_VELOCITY, DERIVATIVE_STATUS_AVAILABLE,
    DERIVATIVE_STATUS_DECLARED_APPROX, DERIVATIVE_STATUS_EVENT_BOUNDARY,
    DERIVATIVE_STATUS_MISSING, DERIVATIVE_STATUS_NOT_REQUIRED,
    ChannelAction, VALUE_PRESENCE_MISSING, VALUE_PRESENCE_UNSUPPORTED,
    VALUE_PRESENCE_ZERO)
from modules.motion_action.parameters import output_with_derivative


class TrajectoryError(ValueError):
    """轨迹提供者与装配错误（带定位信息）。"""

    def __init__(self, message, where=None):
        self.where = where
        super().__init__("%s（位置：%s）" % (message, where or "共同轨迹"))


# ── 提供者采样输出（归一化形状及其导数） ──
@dataclass(frozen=True)
class ShapeSample:
    """提供者一次采样的只读读数：归一化形状值与物理时间导数。

    z 为归一化形状（装配层以 q=center+scale*z+control 恢复绝对目标）；
    z_dot 与 z_ddot 为对物理时间的导数，不可用时为 None（不填假零）。
    presence 沿用 I-219 三态加普通值：value 为普通数值（含数值上恰好为
    零的输出，不作明确零身份）；zero 为明确零形状（有效输出，按同一装
    配公式与中心、尺度及控制修复合成，不把形状零直接当最终关节目标
    零）；unsupported 为配方声明未支持；missing 为缺失。返修 F03：四
    状态经装配与通道构造逐级保留，不得互相合并。
    first_order_status 与 second_order_status 为两个导数的显式身份
    （返修 F01）：值非 None 时登记 available 或 declared_approximation；
    值为 None 时登记 missing（无来源）或 event_boundary_unavailable（分
    段边界等不可微处），消费者按显式身份处理，不由值是否为空猜测。
    """
    step: int
    z: Optional[float]
    z_dot: Optional[float]
    z_ddot: Optional[float]
    presence: str                     # value / zero / unsupported / missing
    source: str                       # 提供者与来源说明
    derivative_note: str              # 导数可用性说明（装配不得覆盖）
    canonical_value: Optional[float] = None   # 本步 χ 或 p
    canonical_rate: Optional[float] = None    # 本步 ν̇ 或 ṡ
    first_order_status: str = DERIVATIVE_STATUS_AVAILABLE
    second_order_status: str = DERIVATIVE_STATUS_AVAILABLE


# ── 共同采样接口 ──
class TrajectoryProviderBase:
    """共同采样接口基类：advance（每物理步一次提交）＋sample（只读）。

    子类须实现 _advance_impl 与 _sample_impl；本基类统一执行时钟来源守
    卫、步号单调检查与单一提交纪律（同一物理步重复 advance 拒绝）。
    """

    def __init__(self, name: str, kind: str, phase_coordinate: str,
                 periodic: bool):
        self.name = str(name)
        self.kind = str(kind)
        self.phase_coordinate = str(phase_coordinate)
        self.periodic = bool(periodic)
        self._step: int = -1
        self._advance_count: int = 0
        self._last_clock: Optional[CanonicalClockSample] = None

    # 子类覆盖以下三个方法
    def _advance_impl(self, clock: CanonicalClockSample,
                      coupling: Optional[CouplingInput]) -> None:
        raise NotImplementedError

    def _sample_impl(self) -> ShapeSample:
        raise NotImplementedError

    def _clone_impl(self) -> "TrajectoryProviderBase":
        raise NotImplementedError

    def advance(self, clock: CanonicalClockSample,
                coupling: Optional[CouplingInput] = None) -> ShapeSample:
        """提交一个真实物理步（唯一推进入口；转发给内部实现）。"""
        where = "%s(%s).advance" % (type(self).__name__, self.name)
        validate_clock_source(clock.source, where)
        if clock.step <= self._step:
            raise TrajectoryError(
                "物理步号必须单调前进：请求步 %r 不大于已提交步 %r（单一提"
                "交纪律；读取请用 sample）" % (clock.step, self._step),
                where=where)
        self._advance_impl(clock, coupling)
        self._step = int(clock.step)
        self._advance_count += 1
        self._last_clock = clock
        return self.sample()

    def sample(self) -> ShapeSample:
        """只读采样（不推进；未提交过时拒绝）。"""
        if self._step < 0:
            raise TrajectoryError(
                "尚未提交任何物理步，无法采样（先 advance 一步）",
                where="%s(%s).sample" % (type(self).__name__, self.name))
        return self._sample_impl()

    def clone_for_preview(self) -> "TrajectoryProviderBase":
        """深复制提供者（预览在副本推进；接续归 I-220 修复验收后事项）。"""
        twin = self._clone_impl()
        twin._step = self._step
        twin._advance_count = self._advance_count
        twin._last_clock = self._last_clock
        return twin

    def state(self) -> Dict[str, object]:
        """提供者状态只读字典（一致性对照用）。"""
        return {"name": self.name, "kind": self.kind, "step": self._step,
                "advance_count": self._advance_count,
                "periodic": self.periodic,
                "phase_coordinate": self.phase_coordinate}


# ── 解析提供者（旧解析程序作为具名形状资产；不积累相位状态） ──
#: 解析形状注册表（先行版本登记的具名解析函数与来源说明）。
ANALYTIC_SHAPE_KINDS = ("sin_phase", "cos_phase", "split_sin_phase",
                        "fold_sin_phase", "aero_pitch")


class AnalyticProvider(TrajectoryProviderBase):
    """解析提供者：从外部时钟样本按解析公式求归一化形状及其导数。

    形状种类（ANALYTIC_SHAPE_KINDS）：
    - sin_phase / cos_phase：z=sin(2πχ+φ0)／cos(2πχ+φ0)；φ0 为参考函数
      内相位偏移（弧度，配方 phase_offset_rad 唯一持有；fold_sin_phase
      的 2.2 rad 偏移来源为已批准首配方保留）。
    - split_sin_phase：z=sin(ph)，ph 由 χ 经旧分裂周期核
      split_phase_map 得到（与旧解析波形 sin(ph) 同式，只读复用）；
      求值含分段线性映射，导数按分段给出，接缝处 dph/dτ 跳变。
    - aero_pitch：z=sign(cos(ph_l))·α_set·|cos(ph_l)|+pitch_bias（aero
      模式俯仰程序）；ph_l 为滞后相位（χ−lag_beats）；尖点处导数不可
      用（分段边界），二阶导数不提供（旧实现为数值差分近似，此处如实
      登记不填假零）。
    """
    #: aero 俯仰尖点判定阈（|cos(ph_l)| 小于该值视为分段边界）。
    CUSP_EPS = 1e-9
    #: 分裂接缝判定阈（τ 与接缝相距小于该值视为实际接缝；返修 F01）。
    SEAM_EPS = 1e-9

    def __init__(self, name: str, shape_kind: str,
                 phase_offset_rad: float = 0.0,
                 phase_offset_source: str = "",
                 split: float = 0.5,
                 alpha_set_rad: float = 0.0,
                 pitch_bias_rad: float = 0.0,
                 lag_beats: float = 0.0):
        if shape_kind not in ANALYTIC_SHAPE_KINDS:
            raise TrajectoryError(
                "未知解析形状种类 %r（可取 %s）"
                % (shape_kind, list(ANALYTIC_SHAPE_KINDS)),
                where="AnalyticProvider(%s)" % name)
        if shape_kind == "fold_sin_phase" and abs(phase_offset_rad - 2.2) \
                > 1e-12:
            raise TrajectoryError(
                "fold_sin_phase 的偏移固定为已批准首配方保留的 2.2 rad，"
                "不允许另行改写（当前 %r）" % (phase_offset_rad,),
                where="AnalyticProvider(%s)" % name)
        super().__init__(name, kind="analytic",
                         phase_coordinate="cumulative_beats", periodic=True)
        self.shape_kind = shape_kind
        self.phase_offset_rad = float(phase_offset_rad)
        self.phase_offset_source = str(phase_offset_source)
        self.split = float(split)
        self.alpha_set_rad = float(alpha_set_rad)
        self.pitch_bias_rad = float(pitch_bias_rad)
        self.lag_beats = float(lag_beats)

    def _advance_impl(self, clock: CanonicalClockSample,
                      coupling: Optional[CouplingInput]) -> None:
        # 解析提供者无内部状态：只保留最近时钟样本供采样求值。
        return None

    def _shape(self, chi: float, chi_dot: float,
               chi_ddot: Optional[float]
               ) -> Tuple[float, Optional[float], Optional[float], str,
                          str, str]:
        """按形状种类求 (z, ż, z̈, note, 一阶身份, 二阶身份)。

        返修 F01：分裂形状在非对称 split 的实际接缝（τ=s 与整数拍回绕）
        处两侧 dph/dτ 不相等，导数左右不同，一阶与二阶均判
        event_boundary_unavailable 并保留原因；对称 split 时两侧速率相
        等、导数按实际连续性判可用，不机械把所有拍边判无效；分段内部
        恒为解析可用。
        """
        two_pi = 2.0 * math.pi
        if self.shape_kind in ("sin_phase", "cos_phase", "fold_sin_phase"):
            arg = two_pi * chi + self.phase_offset_rad
            if self.shape_kind == "cos_phase":
                z = math.cos(arg)
                dz_dchi = -two_pi * math.sin(arg)
                d2z_dchi2 = -two_pi ** 2 * math.cos(arg)
            else:
                z = math.sin(arg)
                dz_dchi = two_pi * math.cos(arg)
                d2z_dchi2 = -two_pi ** 2 * math.sin(arg)
            z_dot = dz_dchi * chi_dot
            z_ddot = None if chi_ddot is None else \
                d2z_dchi2 * chi_dot ** 2 + dz_dchi * chi_ddot
            note = "解析链式导数（chi_ddot 缺省时二阶为 None）" \
                if chi_ddot is None else "解析链式导数 z̈=z''ν̇²+z'ν̈"
            second = DERIVATIVE_STATUS_AVAILABLE if z_ddot is not None \
                else DERIVATIVE_STATUS_MISSING
            return z, z_dot, z_ddot, note, DERIVATIVE_STATUS_AVAILABLE, second
        if self.shape_kind == "split_sin_phase":
            s = min(max(self.split, 0.05), 0.95)
            tau = chi % 1.0
            ph, dph_dtau = split_phase_map(tau, s)
            z = math.sin(ph)
            dz_dchi = math.cos(ph) * dph_dtau
            # 接缝位置与两侧速率：τ=s 为分裂接缝，τ=0/1 为整数拍回绕接
            # 缝；两侧分段速率相等时导数实际连续（对称 split），判可用。
            rate_lo = math.pi / s
            rate_hi = math.pi / (1.0 - s)
            seam_continuous = abs(rate_lo - rate_hi) \
                <= 1e-9 * max(rate_lo, rate_hi)
            near_split_seam = abs(tau - s) < self.SEAM_EPS
            near_wrap_seam = min(tau, 1.0 - tau) < self.SEAM_EPS
            if (near_split_seam or near_wrap_seam) and not seam_continuous:
                where_seam = ("分裂接缝 τ=s=%r" % (s,) if near_split_seam
                              else "整数拍回绕接缝 τ=0/1")
                note = ("分裂波形%s处两侧 dph/dτ 分别为 %0.6f 与 %0.6f，不"
                        "相等：导数左右不同，判事件边界不可用，不把单侧数"
                        "值当普通可用导数（返修 F01）"
                        % (where_seam, rate_lo, rate_hi))
                return (z, None, None, note,
                        DERIVATIVE_STATUS_EVENT_BOUNDARY,
                        DERIVATIVE_STATUS_EVENT_BOUNDARY)
            z_dot = dz_dchi * chi_dot
            if chi_ddot is None:
                z_ddot = None
                note = "split 分段导数；chi_ddot 缺省，二阶为 None"
            else:
                # ph 对 χ 的二阶导在分段内为零（线性映射），接缝处跳变。
                z_ddot = -math.sin(ph) * dph_dtau ** 2 * chi_dot ** 2 \
                    + math.cos(ph) * dph_dtau * chi_ddot
                note = "split 分段内线性映射的链式导数；接缝 dph/dτ 跳变"
            second = DERIVATIVE_STATUS_AVAILABLE if z_ddot is not None \
                else DERIVATIVE_STATUS_MISSING
            return z, z_dot, z_ddot, note, DERIVATIVE_STATUS_AVAILABLE, second
        # aero_pitch：sign(cos(ph_l))·α_set·|cos(ph_l)|+bias
        if self.shape_kind != "aero_pitch":     # pragma: no cover 防御
            raise TrajectoryError("形状种类未实现：%r" % self.shape_kind,
                                  where="AnalyticProvider(%s)" % self.name)
        chi_l = chi - self.lag_beats
        ph_l, dph_dtau = split_phase_map(chi_l % 1.0, self.split)
        c = math.cos(ph_l)
        sgn = 1.0 if c >= 0.0 else -1.0
        z = sgn * self.alpha_set_rad * abs(c) + self.pitch_bias_rad
        if abs(c) < self.CUSP_EPS:
            note = ("aero 俯仰分段边界（|cos(ph)|<%g）：导数左右不同，判"
                    "事件边界不可用，不填假零（返修 F01 同规则）"
                    % self.CUSP_EPS)
            return (z, None, None, note,
                    DERIVATIVE_STATUS_EVENT_BOUNDARY,
                    DERIVATIVE_STATUS_EVENT_BOUNDARY)
        dz_dchi = self.alpha_set_rad * (-math.sin(ph_l)) * dph_dtau
        z_dot = dz_dchi * chi_dot
        # 二阶导数：旧实现为数值差分近似且加速度记零约定，此处不提供，
        # 由消费者按声明近似身份处理（不填假零）。
        return (z, z_dot, None,
                "aero 俯仰解析一阶导数；二阶不提供（旧实现为数值差分近"
                "似，登记为声明近似身份）",
                DERIVATIVE_STATUS_AVAILABLE,
                DERIVATIVE_STATUS_DECLARED_APPROX)

    def _sample_impl(self) -> ShapeSample:
        clock = self._last_clock
        z, z_dot, z_ddot, note, first_st, second_st = self._shape(
            clock.chi, clock.chi_dot, clock.chi_ddot)
        return ShapeSample(
            step=self._step, z=z, z_dot=z_dot, z_ddot=z_ddot,
            presence="value",
            source="analytic:%s(%s)" % (self.name, self.shape_kind),
            derivative_note=note, canonical_value=clock.chi,
            canonical_rate=clock.chi_dot,
            first_order_status=first_st, second_order_status=second_st)

    def _clone_impl(self) -> "AnalyticProvider":
        twin = AnalyticProvider(
            self.name, self.shape_kind, self.phase_offset_rad,
            self.phase_offset_source, self.split, self.alpha_set_rad,
            self.pitch_bias_rad, self.lag_beats)
        return twin


# ── 动态基元提供者（周期） ──
class PrimitiveProvider(TrajectoryProviderBase):
    """周期基元提供者：转发外部时钟样本给 RhythmicPrimitive。

    advance 把时钟样本与耦合输入转发给内部基元（基元自身的单一提交与
    子步纪律不变）；sample 只读转发基元读数。形状权重与内部状态由基元
    持有，提供者不复制一份。
    """

    def __init__(self, primitive: RhythmicPrimitive):
        super().__init__(primitive.name, kind="primitive",
                         phase_coordinate=primitive.phase_coordinate,
                         periodic=True)
        self.primitive = primitive

    def _advance_impl(self, clock: CanonicalClockSample,
                      coupling: Optional[CouplingInput]) -> None:
        self.primitive.advance(clock, coupling)

    def _sample_impl(self) -> ShapeSample:
        s: PrimitiveSample = self.primitive.sample()
        return ShapeSample(
            step=s.step, z=s.z, z_dot=s.z_dot, z_ddot=s.z_ddot,
            presence="value", source="primitive:%s" % self.name,
            derivative_note=s.derivative_note,
            canonical_value=s.canonical_value,
            canonical_rate=s.canonical_rate)

    def _clone_impl(self) -> "PrimitiveProvider":
        return PrimitiveProvider(self.primitive.clone_for_preview())


# ── 保持提供者（非周期身份；尾舵与停拍保持） ──
class HoldProvider(TrajectoryProviderBase):
    """保持提供者：输出常值；零为其真实导数（保持常数的真实微分）。"""

    def __init__(self, name: str, value: Optional[float],
                 source_note: str = "保持提供者"):
        super().__init__(name, kind="hold", phase_coordinate="seconds",
                         periodic=False)
        self.value = None if value is None else float(value)
        self.source_note = str(source_note)

    def _advance_impl(self, clock: CanonicalClockSample,
                      coupling: Optional[CouplingInput]) -> None:
        return None

    def _sample_impl(self) -> ShapeSample:
        if self.value is None:
            return ShapeSample(
                step=self._step, z=None, z_dot=None, z_ddot=None,
                presence="missing", source="hold:%s" % self.name,
                derivative_note="保持值缺失（三态 missing，不填零）")
        return ShapeSample(
            step=self._step, z=self.value, z_dot=0.0, z_ddot=0.0,
            presence="value", source="hold:%s（%s）" % (self.name,
                                                        self.source_note),
            derivative_note="保持常值：零为其真实导数，非假零")

    def _clone_impl(self) -> "HoldProvider":
        return HoldProvider(self.name, self.value, self.source_note)


# ── 过渡提供者（单次基元；初始化／释放过渡） ──
class TransitionProvider(TrajectoryProviderBase):
    """过渡提供者：单次基元的外部进度驱动与绝对起终点登记。

    起点为对应边界的实际关节状态（x0），终点为新参考动作（g）；两者保
    存于基元 endpoints 与归一化登记。规范进度由外部时钟样本提供（已声
    明事件时间或拍数预算派生）；时长来源（现行启动斜坡 STARTUP_AMP_
    RAMP_S）由调用方解析为进度预算，本提供者不再乘一遍旧线性斜坡。
    装配层以 center=x0、scale=g-x0 恢复绝对目标（不再额外叠加旧斜坡）。
    """

    def __init__(self, primitive: DiscretePrimitive,
                 endpoints: Tuple[float, float]):
        if primitive.endpoints is None:
            raise TrajectoryError(
                "过渡基元未登记起终点（endpoints 为空），拒绝构造",
                where="TransitionProvider(%s)" % primitive.name)
        if abs(primitive.endpoints[0] - endpoints[0]) > 1e-12 \
                or abs(primitive.endpoints[1] - endpoints[1]) > 1e-12:
            raise TrajectoryError(
                "过渡起终点与基元登记不一致：提供者 %r，基元 %r"
                % (endpoints, primitive.endpoints),
                where="TransitionProvider(%s)" % primitive.name)
        super().__init__(primitive.name, kind="transition",
                         phase_coordinate="canonical_progress", periodic=False)
        self.primitive = primitive
        self.endpoints = (float(endpoints[0]), float(endpoints[1]))

    def _advance_impl(self, clock: CanonicalClockSample,
                      coupling: Optional[CouplingInput]) -> None:
        self.primitive.advance(clock, coupling)

    def _sample_impl(self) -> ShapeSample:
        s = self.primitive.sample()
        done = "（进度已完成，f(1)=0，系统收敛终点不重置）" \
            if self.primitive.completed else ""
        return ShapeSample(
            step=s.step, z=s.z, z_dot=s.z_dot, z_ddot=s.z_ddot,
            presence="value", source="transition:%s（x0=%+.6f, g=%+.6f）"
            % (self.name, self.endpoints[0], self.endpoints[1]),
            derivative_note=s.derivative_note + done,
            canonical_value=s.canonical_value,
            canonical_rate=s.canonical_rate)

    def _clone_impl(self) -> "TransitionProvider":
        return TransitionProvider(self.primitive.clone_for_preview(),
                                  self.endpoints)


def make_spring_transition(name: str, x0: float, g: float,
                           alpha: float = 25.0, beta: float = 6.25,
                           tau_p: float = 1.0) -> TransitionProvider:
    """无参考形状的过渡构造：零权重弹簧阻尼从起点平滑到终点。

    零权重时 f(p)=0，变换系统为标准弹簧阻尼（起点 0 到终点 1 的归一化
    坐标）；这是显式的默认形状选择，不冒充参考导入。参考形状导入用
    fit_reference_discrete 后自行包装 TransitionProvider。
    """
    prim = DiscretePrimitive(
        name, alpha, beta, tau_p, [0.2, 0.5, 0.8], [200.0] * 3,
        weights=[0.0, 0.0, 0.0], endpoints=(float(x0), float(g)),
        origin="零权重弹簧阻尼过渡（显式默认形状，非参考导入）")
    prim.reset(0.0, 0.0, 0.0, note="过渡初值：z0=0（起点），u0=0")
    return TransitionProvider(prim, (float(x0), float(g)))


def transition_progress_steps(duration_s: float, dt: float) -> int:
    """由过渡时长与步长折算进度预算总步数（时长来源由调用方解析）。

    现行启动斜坡时长来源为 config/sim_config.py 的 STARTUP_AMP_RAMP_S；
    本函数只做折算，不读取全局、不接时钟。进度序列由共同服务或检查夹
    具按预算派生（声明的事件时间预算，不另起无关时钟）。
    """
    if not (math.isfinite(duration_s) and duration_s > 0.0):
        raise TrajectoryError("过渡时长必须为正有限数，实际 %r"
                              % (duration_s,), where="transition_progress_steps")
    if not (math.isfinite(dt) and dt > 0.0):
        raise TrajectoryError("步长必须为正有限数，实际 %r" % (dt,),
                              where="transition_progress_steps")
    return int(round(duration_s / dt))


# ── 轨迹装配：时变中心、尺度、控制修正与相位的链式导数 ──
@dataclass(frozen=True)
class ChannelTrajectoryOutput:
    """单通道装配输出：请求值与采用值按统一约束输出（判据四先行部分）。

    value 为限幅后的采用值；velocity 与 acceleration 为同一输出规则的
    链式导数，不可用时为 None；notes 记录饱和、边界与缺项原因并保留形
    状原有边界说明（返修 F01：不覆盖）。
    返修 F03／续修一：shape_presence 表达输入形状身份（value／zero／
    unsupported／missing）；presence 表达合成后最终目标的身份——非零目
    标按普通数值身份输出，明确零形状合成后实际为零的目标保留明确零身
    份，普通数值零不作明确零身份，未支持与缺失分别保留各自身份；不得
    通过清零目标掩盖身份错误。velocity_status 与 acceleration_status
    为装配后两个导数的显式身份：数值存在时采用形状声明（精确 available
    或声明近似 declared_approximation），数值存在不自动升级为普通可用
    （续修二）；数值缺失时保留事件边界等不可用身份。
    """
    value: Optional[float]
    velocity: Optional[float]
    acceleration: Optional[float]
    notes: str
    unsaturated: Optional[float] = None       # 限幅前请求值（对照用）
    presence: str = "value"                   # 最终目标身份（按合成结果）
    shape_presence: str = "value"             # 输入形状状态（分别记录）
    velocity_status: Optional[str] = None     # 一阶导数显式身份
    acceleration_status: Optional[str] = None  # 二阶导数显式身份


def _inherit_unavailable_status(declared: str) -> str:
    """值缺失时继承形状声明的非可用身份（返修 F01：状态经装配保留）。

    声明为 available 却无值属于违规组合，防御性记 missing；事件边界与
    声明近似等身份如实传播到通道动作，不由值是否为空猜测。
    """
    return declared if declared != DERIVATIVE_STATUS_AVAILABLE \
        else DERIVATIVE_STATUS_MISSING


def assemble_channel_trajectory(
        shape: ShapeSample, center: float, scale: float,
        control: float = 0.0,
        d_center: float = 0.0, d_scale: float = 0.0, d_control: float = 0.0,
        dd_center: float = 0.0, dd_scale: float = 0.0,
        dd_control: float = 0.0,
        limits: Optional[Tuple[float, float]] = None
        ) -> ChannelTrajectoryOutput:
    """按统一输出规则装配通道轨迹并求链式导数（判据四先行部分）。

    输出规则 q=sat(center+scale*z+control)；一阶导数与 I-219 的
    output_with_derivative 同式（sat′ 因子、饱和真实零导数、边界不可
    用），二阶导数按同一规则的链式展开 q̈=sat′(μ̈+ä z+2ȧ ż+a z̈+c̈)。
    缺项规则：形状缺失（missing）或未支持（unsupported）时输出值与导
    数均为 None，两种身份分别保留不合并；明确零形状（zero）为有效输
    出，仍按同一公式与中心、尺度及控制修合成最终目标（不把形状零直
    接当最终关节目标零）；ż 缺失时 velocity 与 acceleration 为 None；
    z̈ 缺失时仅 acceleration 为 None；均不填假零。
    最终目标身份（续修一）：presence 描述合成后最终目标——非零目标
    （含饱和后非零）按普通数值身份输出；明确零形状经全零合成得到的实
    际零目标保留明确零身份；普通数值零不作明确零身份；不通过清零目
    标掩盖身份错误。输入形状身份保留在 shape_presence。
    导数身份（续修二）：数值存在时采用形状声明的身份（精确 available
    或声明近似 declared_approximation），不因数值非空自动升级为普通可
    用；数值缺失时保留事件边界等不可用身份；限幅边界处左右导数不同，
    velocity 与 acceleration 均判不可用并注明分段。
    """
    where = "assemble_channel_trajectory"
    if shape.presence in (VALUE_PRESENCE_MISSING, VALUE_PRESENCE_UNSUPPORTED):
        label = "缺失" if shape.presence == VALUE_PRESENCE_MISSING else "未支持"
        return ChannelTrajectoryOutput(
            value=None, velocity=None, acceleration=None,
            notes="形状%s（presence=%r）：请求与采用值均不输出，身份保留"
                  "不合并为缺失" % (label, shape.presence),
            unsaturated=None, presence=shape.presence,
            shape_presence=shape.presence,
            velocity_status=DERIVATIVE_STATUS_MISSING,
            acceleration_status=DERIVATIVE_STATUS_MISSING)
    if shape.presence not in ("value", VALUE_PRESENCE_ZERO) \
            or shape.z is None:
        return ChannelTrajectoryOutput(
            value=None, velocity=None, acceleration=None,
            notes="形状状态非法（presence=%r 且 z=%r）：拒绝输出"
                  % (shape.presence, shape.z),
            unsaturated=None, presence="missing",
            shape_presence=shape.presence,
            velocity_status=DERIVATIVE_STATUS_MISSING,
            acceleration_status=DERIVATIVE_STATUS_MISSING)
    x = center + scale * shape.z + control
    # 最终目标身份（续修一）：以未饱和合成值判定实际零目标——限幅截出
    # 的零不是明确写入零值，按普通数值身份；明确零身份只随全零合成保留。
    if shape.presence == VALUE_PRESENCE_ZERO:
        final_presence = VALUE_PRESENCE_ZERO if abs(x) < 1e-12 else "value"
    else:
        final_presence = "value"
    zero_note = ""
    if shape.presence == VALUE_PRESENCE_ZERO:
        if final_presence == "value":
            zero_note = ("；明确零形状与非零中心、尺度及控制修合成非零最"
                         "终目标 %+.6f，按普通数值身份输出（形状零身份保留"
                         "在 shape_presence）" % x)
        else:
            zero_note = ("；明确零形状经全零合成得到实际零目标，保留明确"
                         "零身份；形状状态与最终目标身份分别记录")
    if limits is None:
        y = x
        gate = 1.0
        sat_note = "未限幅"
    else:
        lo, hi = min(limits), max(limits)
        y = min(max(x, lo), hi)
        if (x < lo or x > hi) and abs(y - x) > 0.0:
            gate = 0.0
            sat_note = "限幅[%+0.4f,%+0.4f]（饱和，导数为该输出规则真实" \
                       "零）" % (lo, hi)
        elif abs(x - lo) < 1e-12 or abs(x - hi) < 1e-12:
            gate = None
            sat_note = "限幅[%+0.4f,%+0.4f]（恰在边界，左右导数不同，不" \
                       "可用）" % (lo, hi)
        else:
            gate = 1.0
            sat_note = "限幅[%+0.4f,%+0.4f]（未饱和）" % (lo, hi)
    if shape.z_dot is None:
        return ChannelTrajectoryOutput(
            value=y, velocity=None, acceleration=None,
            notes=sat_note + "；一阶导数不可用（%s），不填假零" % shape.derivative_note,
            unsaturated=x, presence=final_presence,
            shape_presence=shape.presence,
            velocity_status=_inherit_unavailable_status(
                shape.first_order_status),
            acceleration_status=_inherit_unavailable_status(
                shape.second_order_status))
    dx = d_center + d_scale * shape.z + scale * shape.z_dot + d_control
    velocity = None if gate is None else gate * dx
    # 数值存在时采用形状声明身份，不自动升级为普通可用（续修二）
    vel_status = shape.first_order_status if velocity is not None \
        else DERIVATIVE_STATUS_EVENT_BOUNDARY
    if shape.z_ddot is None or velocity is None:
        return ChannelTrajectoryOutput(
            value=y, velocity=velocity, acceleration=None,
            notes=sat_note + "；二阶导数不可用（%s），不填假零" % shape.derivative_note,
            unsaturated=x, presence=final_presence,
            shape_presence=shape.presence, velocity_status=vel_status,
            acceleration_status=_inherit_unavailable_status(
                shape.second_order_status))
    ddx = (dd_center + dd_scale * shape.z + 2.0 * d_scale * shape.z_dot
           + scale * shape.z_ddot + dd_control)
    acceleration = None if gate is None else gate * ddx
    acc_status = shape.second_order_status if acceleration is not None \
        else DERIVATIVE_STATUS_EVENT_BOUNDARY
    return ChannelTrajectoryOutput(
        value=y, velocity=velocity, acceleration=acceleration,
        notes=sat_note + zero_note + "；链式导数 q̇=μ̇+ȧz+aż+ċ、"
        "q̈=μ̈+äz+2ȧż+az̈+c̈；形状导数说明保留：%s" % shape.derivative_note,
        unsaturated=x, presence=final_presence,
        shape_presence=shape.presence, velocity_status=vel_status,
        acceleration_status=acc_status)


def crosscheck_first_order_with_i219(shape: ShapeSample, center: float,
                                     scale: float, control: float = 0.0,
                                     limits: Optional[Tuple[float, float]] = None
                                     ) -> Tuple[Optional[float],
                                                Optional[float], str]:
    """一阶分支与 I-219 output_with_derivative 的同式对照出口。

    返回 I-219 规则的 (y, dy, note)，供检查器核对先行装配与已验收对象
    契约的一阶一致性（同一输出规则，不另立口径）。
    """
    return output_with_derivative(
        center=center, scale=scale, shape=shape.z,
        shape_deriv=shape.z_dot, control=control, limits=limits)


# ── 通道动作构造（与 I-219 帧对象契约咬合） ──
def _role_of(joint: str) -> str:
    if joint.startswith("flap"):
        return "flap"
    if joint.startswith("twist"):
        return "twist"
    if joint.startswith("fold"):
        return "fold"
    return "tail"


def build_channel_action(joint: str, output: ChannelTrajectoryOutput,
                         source: str) -> ChannelAction:
    """把装配输出构造为 I-219 的 ChannelAction（导数身份分列）。

    量名身份按 I-219 需求表角色映射：拍动通道分列拍动角速度与拍动角加
    速度；扭转通道分列扭转角速度；折翼与尾舵通道导数非必需。
    返修 F01：导数身份取装配输出的显式状态（velocity_status 与
    acceleration_status），事件边界登记为 event_boundary_unavailable，
    不由值是否为空猜测。
    返修 F03／续修一：presence 直接取装配输出的最终目标身份（非零合
    成目标为 value，明确零目标为 zero，未支持与缺失分别保留），输入
    形状身份不在本层重复判定，不通过清零目标掩盖身份错误。
    续修二：有数值的声明近似导数保留 declared_approximation 身份，不
    因数值非空升级为普通可用。
    """
    role = _role_of(joint)
    statuses: Dict[str, str] = {}
    velocity = None if output.velocity is None else float(output.velocity)
    acceleration = None if output.acceleration is None \
        else float(output.acceleration)
    vel_st = output.velocity_status if output.velocity_status is not None \
        else DERIVATIVE_STATUS_MISSING
    acc_st = output.acceleration_status \
        if output.acceleration_status is not None \
        else DERIVATIVE_STATUS_MISSING
    if role == "flap":
        statuses[DERIV_Q_FLAP_ANGULAR_VELOCITY] = vel_st
        statuses[DERIV_Q_FLAP_ANGULAR_ACCELERATION] = acc_st
        derivative_available = velocity is not None \
            and acceleration is not None
    elif role == "twist":
        statuses[DERIV_Q_TWIST_ANGULAR_VELOCITY] = vel_st
        derivative_available = velocity is not None
    else:
        statuses["derivatives"] = DERIVATIVE_STATUS_NOT_REQUIRED
        derivative_available = True
    presence = output.presence if output.presence in (
        "value", VALUE_PRESENCE_ZERO, VALUE_PRESENCE_MISSING,
        VALUE_PRESENCE_UNSUPPORTED) else "missing"
    return ChannelAction(
        joint=joint,
        presence=presence,
        value=None if output.value is None else float(output.value),
        source=source, derivative_available=derivative_available,
        derivative_note=output.notes, velocity=velocity,
        acceleration=acceleration, derivative_statuses=statuses)
