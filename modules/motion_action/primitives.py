# -*- coding: utf-8 -*-
"""primitives.py — 周期与单次动态运动基元（事项 I-221 先行施工，L4 队）。

先行范围（2026-10-15 机主批准的 partial_start，原 I-220 依赖保留）：
本文件实现周期与单次动态运动基元（DMP）的方程、内部状态、形状权重、
初值及导数，并实现解析／采样／示教参考的导入与归一化版本。本文件以
已验收 I-219 对象契约（modules/motion_action/types.py）为依据；不依赖
已撤回 I-220 交付的运行正确性；不修改 rhythm.py、runtime.py 或
kinematics.py。接收外部相位与规范进度的共同轨迹采样接口在
trajectories.py。

周期基元按规范相位 ν（未回绕累计拍数 χ）定义（资产文档 5.1 节）：

    G  = -k z - d v + F(θ; w) + c
    dz/dν = v        dv/dν = G
    ż = ν̇ v          v̇ = ν̇ G          z̈ = ν̈ v + ν̇² G
    F(θ; w) = Σ w_k Ψ_k(θ) / Σ Ψ_k(θ)
    Ψ_k(θ) = exp(κ_k (cos(θ - c_k) - 1))      （周期核，θ = 2πν）

单次基元按规范进度 p∈[0,1]（p=0 为起点，p=1 为终点）定义：

    τ² d²z/dp² = α (β (1 - z) - τ dz/dp) + f(p)
    f(p) = Σ ψ_k(p) w_k (1-p)
    ψ_k(p) = exp(-h_k (p - c_k)²)
    ẋ = u ṡ          ẍ = G ṡ² + u s̈

两个基元的状态都在归一化坐标中演化（周期 z=(q-center)/scale；单次
z=(q-x0)/(g-x0)，终点为 1）；中心、尺度与控制修正由装配层
（trajectories.py 与 I-219 parameters）按 q = center + scale*z + control
恢复，形状权重不重复吸收中心、幅值及相差。

时间与状态纪律（原判据一、五的先行部分）：
一、规范相位与规范进度一律由外部时钟样本（CanonicalClockSample）传
    入，基元不积分频率、不另起独立拍时钟。先行版本的时钟来源只允许
    check_fixture: 前缀；真实 RhythmService 接入等待 I-220 修复验收后
    接续，本守卫保证先行成果不能伪称真实时钟已接通。
二、内部状态（周期 ζ=(z,v)，单次 (z,u)）只在 advance 提交一次；sample
    只读不推进；周期回绕只影响周期基函数的求值，不重置内部状态。
三、规范相位速度为零或时钟声明冻结时，变换状态保持不变（冻结相位即
    冻结变换状态）；相位回退拒绝提交并保留最后有效状态。
四、ν̈ 与 s̈ 未提供时，二阶导数返回 None 并注明，不填假零。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np


class PrimitiveError(ValueError):
    """基元错误（带定位信息）。"""

    def __init__(self, message, where=None):
        self.where = where
        super().__init__("%s（位置：%s）" % (message, where or "动态运动基元"))


#: 先行版本允许的时钟来源前缀（真实 RhythmService 来源由 I-220 接续登记）
CLOCK_SOURCE_PREFIX_ALLOWED = ("check_fixture:", "rhythm_service:")

#: 单个积分子步允许的最大规范相位增量（拍）：超过即按说明拆数值子步。
#: 子步不产生真实物理步或事件消费（与 I-220 Hopf 后端同纪律）。
MAX_DPHASE_PER_SUBSTEP = 0.05
#: 数值子步数上限（保护失败显式报告，不悄悄截断）。
MAX_SUBSTEPS_PER_ADVANCE = 4096


# ── 外部时钟样本（检查夹具构造；基元只读该样本，不走时） ──
@dataclass(frozen=True)
class CanonicalClockSample:
    """一次外部时钟读数：相位、规范进度与各自速度由外部服务或检查夹具提供。

    source 标记检查夹具或 cooperative.RhythmClockAdapter 的真实节律来源。
    前缀是来源诊断标签，不是身份认证。chi 为未回绕累计
    拍数（周期规范相位 ν），canonical_progress 为单次规范进度 p。chi_ddot
    与 progress_ddot 未提供时为 None，对应二阶导数按缺省规则返回 None。
    """
    step: int
    t_s: float
    chi: float
    chi_dot: float
    source: str
    chi_ddot: Optional[float] = None
    canonical_progress: Optional[float] = None
    progress_dot: Optional[float] = None
    progress_ddot: Optional[float] = None
    frozen: bool = False
    phase_coordinate: str = "cumulative_beats"


def validate_clock_source(source: str, where: str) -> None:
    """校准时钟来源标签；真实服务适配在 cooperative 中实现。"""
    if not isinstance(source, str) or not source.startswith(
            CLOCK_SOURCE_PREFIX_ALLOWED):
        raise PrimitiveError(
            "时钟来源 %r 未登记（允许前缀：%s）"
            % (source, list(CLOCK_SOURCE_PREFIX_ALLOWED)), where=where)


# ── 耦合入口（单位与唯一控制权声明） ──
@dataclass(frozen=True)
class CouplingInput:
    """一次耦合或反馈注入：值、单位、控制权所有者与出口一并声明。

    单位必须与目标基元的耦合单位一致；owner 为唯一控制权所有者标识，
    同一基元同一物理步只接受一个耦合输入，禁止新旧路径同时注入同一
    修正量（节律补充二节）。
    """
    value: float
    unit: str
    owner: str
    outlet: str
    limit_note: str = ""


#: 周期基元耦合单位（d²z/dχ²，归一化坐标每平方拍）。
COUPLING_UNIT_PERIODIC = "shape_accel_per_beat_squared"
#: 单次基元耦合单位（d²z/dp²，归一化坐标每平方进度）。
COUPLING_UNIT_DISCRETE = "shape_accel_per_progress_squared"


def _check_coupling(coupling: Optional[CouplingInput], expect_unit: str,
                    where: str) -> float:
    """校验耦合输入并返回其值；单位不符或数值非法即拒绝。"""
    if coupling is None:
        return 0.0
    if coupling.unit != expect_unit:
        raise PrimitiveError(
            "耦合单位不符：输入 %r，基元要求 %r（单位与控制权必须唯一声明）"
            % (coupling.unit, expect_unit), where=where)
    if isinstance(coupling.value, bool) \
            or not isinstance(coupling.value, (int, float)) \
            or not math.isfinite(float(coupling.value)):
        raise PrimitiveError("耦合值必须为有限数值，实际 %r"
                             % (coupling.value,), where=where)
    return float(coupling.value)


# ── 基元样本（只读） ──
@dataclass(frozen=True)
class PrimitiveSample:
    """基元一次提交后的只读读数（含导数与可用性注记）。"""
    step: int
    z: float                          # 归一化形状值
    internal_rate: float              # 内部速率（周期 v=dz/dν；单次 u=dz/dp）
    z_dot: Optional[float]            # 对物理时间的一阶导数
    z_ddot: Optional[float]           # 对物理时间的二阶导数（缺 ν̈/s̈ 时 None）
    canonical_value: float            # 本步规范坐标值（χ 或外部进度 p_ext）
    canonical_rate: float             # 本步规范坐标速度（ν̇ 或 ṡ）
    substeps_last: int                # 本步数值子步数
    derivative_note: str              # 导数可用性说明
    advance_count: int                # 已提交物理步计数（单一提交纪律对照）
    canonical_drive_value: Optional[float] = None  # 单次驱动截断进度 p_drive


# ── 参考导入（解析／采样／示教；归一化与版本保存） ──
@dataclass(frozen=True)
class ReferenceSampleSet:
    """统一后的参考样本集：规范坐标（χ 或 p）与参考绝对值成对给出。

    source_kind 登记来源（analytic／sampled／demonstration）。示教来源的
    相位由示教标注随样本给出，本导入不自行计时、不另起拍时钟。
    """
    source_kind: str
    coordinate: str                   # "phase"（χ）或 "progress"（p）
    points: Tuple[Tuple[float, float], ...]   # (χ_k 或 p_k, q_k) 序列，坐标递增
    origin_note: str                  # 来源与构造方式说明

    @classmethod
    def from_analytic(cls, value_fn: Callable[[float], float],
                      coordinate: str, n_points: int = 241,
                      p_start: float = 0.0, p_end: float = 1.0,
                      origin_note: str = "") -> "ReferenceSampleSet":
        """从解析函数构造（在坐标区间上取等距网格点）。"""
        grid = np.linspace(p_start, p_end, int(n_points))
        pts = tuple((float(x), float(value_fn(float(x)))) for x in grid)
        return cls(source_kind="analytic", coordinate=coordinate,
                   points=pts, origin_note=origin_note or
                   ("解析参考（函数 %s，%d 个等距网格点）"
                    % (getattr(value_fn, "__name__", "callable"), len(pts))))

    @classmethod
    def from_samples(cls, pairs: Sequence[Tuple[float, float]],
                     coordinate: str,
                     origin_note: str = "") -> "ReferenceSampleSet":
        """从采样序列构造（坐标须递增且有限）。"""
        pts = [(float(a), float(b)) for a, b in pairs]
        for i, (a, _q) in enumerate(pts):
            if not (math.isfinite(a) and math.isfinite(_q)):
                raise PrimitiveError("采样参考第 %d 点含非有限值" % (i + 1),
                                     where="from_samples")
            if i > 0 and a <= pts[i - 1][0]:
                raise PrimitiveError(
                    "采样参考坐标必须严格递增（第 %d 点 %r 不大于前点 %r）"
                    % (i + 1, a, pts[i - 1][0]), where="from_samples")
        return cls(source_kind="sampled", coordinate=coordinate,
                   points=tuple(pts), origin_note=origin_note
                   or "采样参考（%d 点）" % len(pts))

    @classmethod
    def from_demonstration(cls, demo: Sequence[Tuple[float, float, float]],
                           coordinate: str,
                           origin_note: str = "") -> "ReferenceSampleSet":
        """从示教序列构造：每项为 (t_i, χ_i 或 p_i, q_i)。

        示教的相位标注随示教记录给出（时间列只作来源登记），本导入不
        由时间自行积分相位。
        """
        pts = tuple((float(a), float(q))
                    for _t, a, q in sorted(demo, key=lambda r: r[1]))
        for i, (a, _q) in enumerate(pts):
            if not (math.isfinite(a) and math.isfinite(_q)):
                raise PrimitiveError("示教参考第 %d 点含非有限值" % (i + 1),
                                     where="from_demonstration")
            if i > 0 and a <= pts[i - 1][0]:
                raise PrimitiveError(
                    "示教参考相位标注必须严格递增（第 %d 点 %r）"
                    % (i + 1, a), where="from_demonstration")
        return cls(source_kind="demonstration", coordinate=coordinate,
                   points=pts, origin_note=origin_note
                   or "示教参考（%d 点，相位由示教标注给出）" % len(pts))


@dataclass(frozen=True)
class ReferenceNormalization:
    """参考归一化登记：中心、尺度、坐标零点、版本、起终点与拟合元数据。

    周期参考保存 phase_zero（χ 零点）；单次参考在 endpoints 保存绝对起
    点与终点（x0、g），归一化坐标为 z=(q-x0)/(g-x0)。fit_metadata 保存
    拟合方法与误差（导入方法与误差作为元数据，判据二先行部分）。
    """
    center: float
    scale: float
    phase_coordinate: str
    phase_zero: float
    version: str
    source_kind: str
    origin_note: str
    endpoints: Optional[Tuple[float, float]] = None
    fit_method: str = ""
    fit_rms_shape: Optional[float] = None       # 权重最小二乘残差 RMS
    rollout_rms_shape: Optional[float] = None   # 闭环回放与参考偏差 RMS
    n_basis: int = 0
    n_samples: int = 0
    initial_state_note: str = ""


def _derivatives_on_grid(xs: np.ndarray, ys: np.ndarray,
                         analytic_first: Optional[Callable[[float], float]] = None
                         ) -> Tuple[np.ndarray, np.ndarray]:
    """网格一阶与二阶导数（解析一阶优先；否则中心差分，端点单侧）。"""
    if analytic_first is not None:
        d1 = np.asarray([analytic_first(float(x)) for x in xs], dtype=float)
    else:
        d1 = np.gradient(ys, xs)
    d2 = np.gradient(d1, xs)
    return d1, d2


# ══════════════════════════════════════════════════════════════
# 周期动态运动基元
# ══════════════════════════════════════════════════════════════
class RhythmicPrimitive:
    """周期基元：规范相位 χ 上的二阶变换状态加周期核形状驱动项。

    构造参数全部显式：刚度 k、阻尼 d、周期核中心（弧度）、集中度 κ、
    形状权重 w 与耦合单位。内部状态 ζ=(z,v) 经 reset 显式设定初值；
    不能把全部内部速度隐式设零（初值须由参考在明确初始相位处的形状
    值及其对规范相位的导数解析或导入，导入方法与误差作为元数据）。
    """

    def __init__(self, name: str, stiffness: float, damping: float,
                 basis_centers_rad: Sequence[float],
                 basis_concentration: Sequence[float],
                 weights: Optional[Sequence[float]] = None,
                 phase_coordinate: str = "cumulative_beats",
                 coupling_unit: str = COUPLING_UNIT_PERIODIC,
                 origin: str = ""):
        self.name = str(name)
        where = "RhythmicPrimitive(%s)" % self.name
        for label, v in (("stiffness", stiffness), ("damping", damping)):
            if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(float(v)) and float(v) > 0.0):
                raise PrimitiveError("%s 必须为正有限数，实际 %r"
                                     % (label, v), where=where)
        self.k = float(stiffness)
        self.d = float(damping)
        centers = [float(c) for c in basis_centers_rad]
        kappas = [float(kp) for kp in basis_concentration]
        if not centers or len(centers) != len(kappas):
            raise PrimitiveError(
                "周期核中心与集中度必须非空且等长（%d 中心、%d 集中度）"
                % (len(centers), len(kappas)), where=where)
        for c, kp in zip(centers, kappas):
            if not (math.isfinite(c) and math.isfinite(kp) and kp > 0.0):
                raise PrimitiveError(
                    "周期核中心须有限、集中度须为正有限数（中心 %r、κ %r）"
                    % (c, kp), where=where)
        self.basis_centers = tuple(centers)
        self.basis_kappa = tuple(kappas)
        if weights is None:
            w = [0.0] * len(centers)
        else:
            w = [float(x) for x in weights]
            if len(w) != len(centers):
                raise PrimitiveError(
                    "形状权重数量 %d 与周期核数量 %d 不一致"
                    % (len(w), len(centers)), where=where)
            for x in w:
                if not math.isfinite(x):
                    raise PrimitiveError("形状权重含非有限值 %r" % (x,),
                                         where=where)
        self._w = list(w)
        self.phase_coordinate = str(phase_coordinate)
        self.coupling_unit = str(coupling_unit)
        self.origin = str(origin)
        # 内部状态：未显式 reset 前为未初始化（显式初值纪律）。
        self._z: Optional[float] = None
        self._v: Optional[float] = None
        self._chi: Optional[float] = None
        self._step: int = -1
        self._advance_count: int = 0
        self._substeps_last = 0
        self._last_dot: Optional[float] = None
        self._last_ddot: Optional[float] = None
        self._last_note = "尚未 reset 初值"
        self._last_clock: Optional[CanonicalClockSample] = None
        self._initial_state_note = ""

    # ── 权重与形状驱动项 ──
    @property
    def weights(self) -> Tuple[float, ...]:
        return tuple(self._w)

    def set_weights(self, weights: Sequence[float], note: str = "") -> None:
        """设定形状权重（拟合导入后写入；数量与有限性校验同构造）。"""
        w = [float(x) for x in weights]
        if len(w) != len(self.basis_centers):
            raise PrimitiveError(
                "形状权重数量 %d 与周期核数量 %d 不一致"
                % (len(w), len(self.basis_centers)),
                where="RhythmicPrimitive(%s).set_weights" % self.name)
        for x in w:
            if not math.isfinite(x):
                raise PrimitiveError("形状权重含非有限值 %r" % (x,),
                                     where="RhythmicPrimitive(%s).set_weights"
                                     % self.name)
        self._w = w
        self.origin = self.origin + ("；" if self.origin else "") + str(note) \
            if note else self.origin

    def forcing(self, theta_rad: float) -> float:
        """归一化周期核形状驱动项 F(θ;w)=Σw_kΨ_k/ΣΨ_k（θ 以 2π 为周期）。"""
        num = 0.0
        den = 0.0
        for c, kp, wk in zip(self.basis_centers, self.basis_kappa, self._w):
            psi = math.exp(kp * (math.cos(theta_rad - c) - 1.0))
            num += wk * psi
            den += psi
        return num / den

    def g_term(self, z: float, v: float, theta_rad: float,
               coupling_value: float = 0.0) -> float:
        """右端项 G=-k z-d v+F(θ;w)+c（归一化坐标，每拍）。"""
        return -self.k * z - self.d * v + self.forcing(theta_rad) \
            + coupling_value

    # ── 初值（显式；参考导入由 fit_reference_periodic 代办） ──
    def reset(self, z0: float, v0: float, at_chi: float,
              note: str = "") -> None:
        """显式设定内部状态初值并回到未提交状态。

        v0 是形状值对规范相位 χ 的导数（dz/dχ）。把全部内部速度统一设零
        只能作为显式选择（note 说明），缺省做法是由参考导入解析或数值
        导数（立项三节：不能统一把全部内部速度设零）。
        """
        for label, v in (("z0", z0), ("v0", v0), ("at_chi", at_chi)):
            if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(float(v))):
                raise PrimitiveError("初值 %s 必须为有限数值，实际 %r"
                                     % (label, v),
                                     where="RhythmicPrimitive(%s).reset"
                                     % self.name)
        self._z, self._v, self._chi = float(z0), float(v0), float(at_chi)
        self._step = -1
        self._advance_count = 0
        self._substeps_last = 0
        self._last_dot = None
        self._last_ddot = None
        self._last_note = "已 reset 初值（尚未 advance）"
        self._last_clock = None
        self._initial_state_note = str(note)

    @property
    def initialized(self) -> bool:
        return self._z is not None

    def rebase_phase(self, chi: float) -> None:
        """Apply an explicit coordinate jump without consuming motion budget.

        Used at a committed phase-lock event before the actual interval is
        integrated. Shape state, physical-step counters and weights are retained.
        Ordinary wrapping must never call this operation.
        """
        if not self.initialized or isinstance(chi, bool) or not isinstance(chi, (int, float)) or not math.isfinite(chi):
            raise PrimitiveError("rebase requires initialized state and finite phase", where=self.name)
        self._chi = float(chi)

    # ── 单一提交入口 ──
    def advance(self, clock: CanonicalClockSample,
                coupling: Optional[CouplingInput] = None
                ) -> PrimitiveSample:
        """提交一个真实物理步（唯一推进入口；判据五先行部分）。

        相位增量 dχ 全部来自外部时钟样本；基元把 dχ 拆成不超过
        MAX_DPHASE_PER_SUBSTEP 的数值子步（RK2 中点法），子步不产生真实
        物理步。dχ=0 或时钟声明冻结时状态保持；χ 回退或步号不前进时拒
        绝并保留最后有效状态。本步积分出现非有限值时整体回滚。
        """
        where = "RhythmicPrimitive(%s).advance" % self.name
        validate_clock_source(clock.source, where)
        if not self.initialized:
            raise PrimitiveError("内部状态未初始化：先 reset 初值或经参考"
                                 "导入设定", where=where)
        c_val = _check_coupling(coupling, self.coupling_unit, where)
        if clock.step <= self._step:
            raise PrimitiveError(
                "物理步号必须单调前进：请求步 %r 不大于已提交步 %r（同一物"
                "理步只提交一次；读取请用 sample）"
                % (clock.step, self._step), where=where)
        if not (math.isfinite(clock.chi) and math.isfinite(clock.chi_dot)
                and clock.chi_dot >= 0.0):
            raise PrimitiveError(
                "时钟样本 chi/chi_dot 非法（chi=%r, chi_dot=%r；速度须非负"
                "有限）" % (clock.chi, clock.chi_dot), where=where)
        if clock.chi < self._chi:
            raise PrimitiveError(
                "规范相位回退：请求 χ=%r 小于已提交 χ=%r，拒绝提交并保留"
                "最后有效状态" % (clock.chi, self._chi), where=where)
        snapshot = (self._z, self._v, self._chi, self._step,
                    self._advance_count, self._substeps_last, self._last_dot,
                    self._last_ddot, self._last_note, self._last_clock)
        try:
            dchi = clock.chi - self._chi
            if clock.frozen or dchi == 0.0:
                # 冻结相位即冻结变换状态（状态保持，步号前进）。
                self._substeps_last = 0
                self._last_note = ("时钟声明冻结" if clock.frozen else
                                   "相位增量为零") + "，状态保持"
                self._last_dot = 0.0
                self._last_ddot = None if clock.chi_ddot is None \
                    else clock.chi_ddot * self._v
            else:
                n_sub = int(math.ceil(dchi / MAX_DPHASE_PER_SUBSTEP))
                if n_sub > MAX_SUBSTEPS_PER_ADVANCE:
                    raise PrimitiveError(
                        "相位增量 %r 需要子步数 %d 超出上限 %d（显式报告，"
                        "不悄悄截断）" % (dchi, n_sub,
                                            MAX_SUBSTEPS_PER_ADVANCE),
                        where=where)
                h = dchi / n_sub
                z, v, chi = self._z, self._v, self._chi
                theta = 2.0 * math.pi * chi
                for _ in range(n_sub):
                    # RK2 中点法：先取半步中点，再用中点斜率整步推进。
                    g1 = self.g_term(z, v, theta, c_val)
                    z_m = z + 0.5 * h * v
                    v_m = v + 0.5 * h * g1
                    theta_m = 2.0 * math.pi * (chi + 0.5 * h)
                    g_m = self.g_term(z_m, v_m, theta_m, c_val)
                    z = z + h * v_m
                    v = v + h * g_m
                    chi += h
                    theta = 2.0 * math.pi * chi
                    if not (math.isfinite(z) and math.isfinite(v)):
                        raise PrimitiveError(
                            "积分产生非有限内部状态（z=%r, v=%r），本步整体"
                            "回滚" % (z, v), where=where)
                self._z, self._v, self._chi = z, v, chi
                self._substeps_last = n_sub
                self._last_dot = clock.chi_dot * v
                if clock.chi_ddot is None:
                    self._last_ddot = None
                    self._last_note = ("二阶导数缺 ν̈（时钟未提供），z̈ 不可"
                                       "用，不填假零")
                else:
                    g_end = self.g_term(z, v, theta, c_val)
                    self._last_ddot = clock.chi_ddot * v \
                        + clock.chi_dot ** 2 * g_end
                    self._last_note = "链式导数 z̈=ν̈ v+ν̇² G"
            self._step = int(clock.step)
            self._advance_count += 1
            self._last_clock = clock
        except Exception:
            (self._z, self._v, self._chi, self._step, self._advance_count,
             self._substeps_last, self._last_dot, self._last_ddot,
             self._last_note, self._last_clock) = snapshot
            raise
        return self.sample()

    def sample(self) -> PrimitiveSample:
        """只读当前读数（不推进、不消费；单一提交纪律）。"""
        if not self.initialized:
            raise PrimitiveError("内部状态未初始化，无法采样",
                                 where="RhythmicPrimitive(%s).sample" % self.name)
        clock = self._last_clock
        return PrimitiveSample(
            step=self._step, z=self._z, internal_rate=self._v,
            z_dot=self._last_dot, z_ddot=self._last_ddot,
            canonical_value=self._chi,
            canonical_rate=clock.chi_dot if clock is not None else 0.0,
            substeps_last=self._substeps_last,
            derivative_note=self._last_note,
            advance_count=self._advance_count)

    def state(self) -> dict:
        """内部状态只读字典（克隆与一致性对照用）。"""
        return {"name": self.name, "z": self._z, "v": self._v,
                "chi": self._chi, "step": self._step,
                "advance_count": self._advance_count,
                "weights": tuple(self._w),
                "initial_state_note": self._initial_state_note}

    def clone_for_preview(self) -> "RhythmicPrimitive":
        """深复制（预览在副本推进；预览接续归 I-220 修复验收后事项）。"""
        twin = RhythmicPrimitive(
            self.name, self.k, self.d, self.basis_centers, self.basis_kappa,
            self._w, phase_coordinate=self.phase_coordinate,
            coupling_unit=self.coupling_unit, origin=self.origin)
        twin._z, twin._v, twin._chi = self._z, self._v, self._chi
        twin._step = self._step
        twin._advance_count = self._advance_count
        twin._substeps_last = self._substeps_last
        twin._last_dot = self._last_dot
        twin._last_ddot = self._last_ddot
        twin._last_note = self._last_note
        twin._last_clock = self._last_clock
        twin._initial_state_note = self._initial_state_note
        return twin


# ══════════════════════════════════════════════════════════════
# 单次动态运动基元
# ══════════════════════════════════════════════════════════════
class DiscretePrimitive:
    """单次基元：规范进度 p∈[0,1] 上的二阶变换状态加高斯核形状驱动。

    状态 (z,u) 在归一化坐标演化（z 起点 0、终点 1；绝对轨迹由装配层以
    center=x0、scale=g-x0 恢复）。规范进度由外部时钟样本提供（已声明事
    件时间或拍数预算派生，本基元不另起时钟）；f(1)=0 保证进度完成后系
    统收敛到终点，状态不重置。

    返修 F02（终点后不重复预算）：分别保存已提交的外部规范进度 p_ext
    与驱动函数求值所用的截断进度 p_drive（不超过 1）。进度增量一律取
    外部进度差（p_new−p_ext），形状驱动在 p_drive 上求值；外部进度超
    过 1 后，超出部分继续按外部差推进状态（f=0 的收敛段），但驱动求
    值坐标截在 1，不把已处理部分再次计入预算。
    """

    def __init__(self, name: str, alpha: float, beta: float, tau_p: float,
                 basis_centers: Sequence[float],
                 basis_widths_inv: Sequence[float],
                 weights: Optional[Sequence[float]] = None,
                 endpoints: Optional[Tuple[float, float]] = None,
                 coupling_unit: str = COUPLING_UNIT_DISCRETE,
                 origin: str = ""):
        self.name = str(name)
        where = "DiscretePrimitive(%s)" % self.name
        for label, v in (("alpha", alpha), ("beta", beta), ("tau_p", tau_p)):
            if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(float(v)) and float(v) > 0.0):
                raise PrimitiveError("%s 必须为正有限数，实际 %r"
                                     % (label, v), where=where)
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.tau_p = float(tau_p)
        centers = [float(c) for c in basis_centers]
        hs = [float(x) for x in basis_widths_inv]
        if not centers or len(centers) != len(hs):
            raise PrimitiveError(
                "高斯核中心与宽度参数必须非空且等长（%d 中心、%d 宽度）"
                % (len(centers), len(hs)), where=where)
        for c, hh in zip(centers, hs):
            if not (math.isfinite(c) and 0.0 <= c <= 1.0
                    and math.isfinite(hh) and hh > 0.0):
                raise PrimitiveError(
                    "高斯核中心须在 [0,1] 且宽度参数须正有限（中心 %r、h %r）"
                    % (c, hh), where=where)
        self.basis_centers = tuple(centers)
        self.basis_widths_inv = tuple(hs)
        if weights is None:
            w = [0.0] * len(centers)
        else:
            w = [float(x) for x in weights]
            if len(w) != len(centers):
                raise PrimitiveError(
                    "形状权重数量 %d 与高斯核数量 %d 不一致"
                    % (len(w), len(centers)), where=where)
            for x in w:
                if not math.isfinite(x):
                    raise PrimitiveError("形状权重含非有限值 %r" % (x,),
                                         where=where)
        self._w = list(w)
        if endpoints is not None:
            if abs(float(endpoints[1]) - float(endpoints[0])) < 1e-12:
                raise PrimitiveError(
                    "单次起终点重合（%r），不存在过渡幅度，拒绝构造"
                    % (endpoints,), where=where)
            self.endpoints = (float(endpoints[0]), float(endpoints[1]))
        else:
            self.endpoints = None
        self.coupling_unit = str(coupling_unit)
        self.origin = str(origin)
        self._z: Optional[float] = None
        self._u: Optional[float] = None
        self._p_ext: Optional[float] = None    # 已提交外部规范进度（不截断）
        self._p_drive: Optional[float] = None  # 驱动求值截断进度（≤1）
        self._step: int = -1
        self._advance_count: int = 0
        self._substeps_last = 0
        self._last_dot: Optional[float] = None
        self._last_ddot: Optional[float] = None
        self._last_note = "尚未 reset 初值"
        self._last_clock: Optional[CanonicalClockSample] = None
        self._initial_state_note = ""

    @property
    def weights(self) -> Tuple[float, ...]:
        return tuple(self._w)

    def set_weights(self, weights: Sequence[float], note: str = "") -> None:
        w = [float(x) for x in weights]
        if len(w) != len(self.basis_centers):
            raise PrimitiveError(
                "形状权重数量 %d 与高斯核数量 %d 不一致"
                % (len(w), len(self.basis_centers)),
                where="DiscretePrimitive(%s).set_weights" % self.name)
        for x in w:
            if not math.isfinite(x):
                raise PrimitiveError("形状权重含非有限值 %r" % (x,),
                                     where="DiscretePrimitive(%s).set_weights"
                                     % self.name)
        self._w = w
        if note:
            self.origin = self.origin + ("；" if self.origin else "") \
                + str(note)

    def forcing(self, p: float) -> float:
        """形状驱动项 f(p)=Σψ_k(p)w_k(1-p)；p≥1 时取零（进度完成后消失）。"""
        pc = min(max(float(p), 0.0), 1.0)
        if pc >= 1.0:
            return 0.0
        total = 0.0
        for c, hh, wk in zip(self.basis_centers, self.basis_widths_inv,
                             self._w):
            total += wk * math.exp(-hh * (pc - c) ** 2)
        return total * (1.0 - pc)

    def g_term(self, z: float, u: float, p: float,
               coupling_value: float = 0.0) -> float:
        """右端项 G=[α(β(1-z)-τu)+f(p)+c]/τ²（归一化坐标，每进度）。"""
        return (self.alpha * (self.beta * (1.0 - z) - self.tau_p * u)
                + self.forcing(p) + coupling_value) / self.tau_p ** 2

    def reset(self, z0: float, u0: float, at_p: float,
              note: str = "") -> None:
        """显式设定内部状态初值（z0 通常为 0，即起点；u0 为起点形状速度）。"""
        for label, v in (("z0", z0), ("u0", u0), ("at_p", at_p)):
            if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(float(v))):
                raise PrimitiveError("初值 %s 必须为有限数值，实际 %r"
                                     % (label, v),
                                     where="DiscretePrimitive(%s).reset"
                                     % self.name)
        if not 0.0 <= float(at_p) <= 1.0:
            raise PrimitiveError("初值进度 at_p 须在 [0,1]，实际 %r"
                                 % (at_p,),
                                 where="DiscretePrimitive(%s).reset" % self.name)
        self._z, self._u = float(z0), float(u0)
        self._p_ext = float(at_p)
        self._p_drive = min(float(at_p), 1.0)
        self._step = -1
        self._advance_count = 0
        self._substeps_last = 0
        self._last_dot = None
        self._last_ddot = None
        self._last_note = "已 reset 初值（尚未 advance）"
        self._last_clock = None
        self._initial_state_note = str(note)

    @property
    def initialized(self) -> bool:
        return self._z is not None

    @property
    def completed(self) -> bool:
        """驱动进度已到终点的只读判定（外部进度可继续超出终点）。"""
        return self._p_drive is not None and self._p_drive >= 1.0

    def advance(self, clock: CanonicalClockSample,
                coupling: Optional[CouplingInput] = None
                ) -> PrimitiveSample:
        """提交一个真实物理步（规范进度由外部时钟样本提供）。

        返修 F02：进度增量一律取外部进度差 dp=p_new−p_ext；状态 (z,u)
        按 dp 推进，形状驱动在截断进度 p_drive（≤1）上求值。外部依次
        输入 1.0、1.1、1.2 时预算分别为 1.0、0.1、0.1，不重复计入已处
        理部分；终点后 f(1)=0 的收敛段继续演化。dp=0 或时钟冻结时状态
        保持；进度回退或速度为负拒绝并保留最后有效状态。
        """
        where = "DiscretePrimitive(%s).advance" % self.name
        validate_clock_source(clock.source, where)
        if not self.initialized:
            raise PrimitiveError("内部状态未初始化：先 reset 初值或经参考"
                                 "导入设定", where=where)
        c_val = _check_coupling(coupling, self.coupling_unit, where)
        if clock.step <= self._step:
            raise PrimitiveError(
                "物理步号必须单调前进：请求步 %r 不大于已提交步 %r（同一物"
                "理步只提交一次）" % (clock.step, self._step), where=where)
        if clock.canonical_progress is None or clock.progress_dot is None:
            raise PrimitiveError(
                "单次基元要求时钟样本提供 canonical_progress 与 progress_dot"
                "（规范进度由共同服务派生，基元不另起时钟）", where=where)
        p_new = float(clock.canonical_progress)
        pdot = float(clock.progress_dot)
        if not (math.isfinite(p_new) and math.isfinite(pdot) and pdot >= 0.0):
            raise PrimitiveError(
                "时钟样本进度非法（p=%r, ṡ=%r；速度须非负有限）"
                % (p_new, pdot), where=where)
        if p_new < self._p_ext - 1e-12:
            raise PrimitiveError(
                "规范进度回退：请求 p=%r 小于已提交外部进度 p_ext=%r，拒绝"
                "提交并保留最后有效状态" % (p_new, self._p_ext), where=where)
        snapshot = (self._z, self._u, self._p_ext, self._p_drive, self._step,
                    self._advance_count, self._substeps_last, self._last_dot,
                    self._last_ddot, self._last_note, self._last_clock)
        try:
            dp = max(p_new - self._p_ext, 0.0)
            if clock.frozen or dp == 0.0:
                self._substeps_last = 0
                self._last_note = ("时钟声明冻结" if clock.frozen else
                                   "进度增量为零") + "，状态保持"
                self._last_dot = 0.0
                self._last_ddot = None if clock.progress_ddot is None \
                    else clock.progress_ddot * self._u
            else:
                n_sub = int(math.ceil(dp / MAX_DPHASE_PER_SUBSTEP))
                if n_sub > MAX_SUBSTEPS_PER_ADVANCE:
                    raise PrimitiveError(
                        "进度增量 %r 需要子步数 %d 超出上限 %d（显式报告，"
                        "不悄悄截断）" % (dp, n_sub, MAX_SUBSTEPS_PER_ADVANCE),
                        where=where)
                h = dp / n_sub
                z, u = self._z, self._u
                p_drv = self._p_drive
                for _ in range(n_sub):
                    g1 = self.g_term(z, u, p_drv, c_val)
                    z_m = z + 0.5 * h * u
                    u_m = u + 0.5 * h * g1
                    g_m = self.g_term(z_m, u_m, min(p_drv + 0.5 * h, 1.0),
                                      c_val)
                    z = z + h * u_m
                    u = u + h * g_m
                    p_drv = min(p_drv + h, 1.0)
                    if not (math.isfinite(z) and math.isfinite(u)):
                        raise PrimitiveError(
                            "积分产生非有限内部状态（z=%r, u=%r），本步整体"
                            "回滚" % (z, u), where=where)
                self._z, self._u = z, u
                self._p_drive = p_drv
                self._substeps_last = n_sub
                self._last_dot = pdot * u
                if clock.progress_ddot is None:
                    self._last_ddot = None
                    self._last_note = ("二阶导数缺 s̈（时钟未提供），ẍ 不可"
                                       "用，不填假零")
                else:
                    g_end = self.g_term(z, u, p_drv, c_val)
                    self._last_ddot = g_end * pdot ** 2 \
                        + clock.progress_ddot * u
                    self._last_note = "链式导数 ẍ=G ṡ²+u s̈"
                if p_new > 1.0 and p_drv >= 1.0:
                    self._last_note += ("；外部进度 %r 超出终点，超出部分"
                                        "按外部差推进收敛段，驱动求值截在 "
                                        "p=1（不重复预算）" % (p_new,))
            self._p_ext = p_new
            self._step = int(clock.step)
            self._advance_count += 1
            self._last_clock = clock
        except Exception:
            (self._z, self._u, self._p_ext, self._p_drive, self._step,
             self._advance_count, self._substeps_last, self._last_dot,
             self._last_ddot, self._last_note, self._last_clock) = snapshot
            raise
        return self.sample()

    def sample(self) -> PrimitiveSample:
        if not self.initialized:
            raise PrimitiveError("内部状态未初始化，无法采样",
                                 where="DiscretePrimitive(%s).sample" % self.name)
        clock = self._last_clock
        return PrimitiveSample(
            step=self._step, z=self._z, internal_rate=self._u,
            z_dot=self._last_dot, z_ddot=self._last_ddot,
            canonical_value=self._p_ext,
            canonical_rate=clock.progress_dot if clock is not None else 0.0,
            substeps_last=self._substeps_last,
            derivative_note=self._last_note,
            advance_count=self._advance_count,
            canonical_drive_value=self._p_drive)

    def state(self) -> dict:
        return {"name": self.name, "z": self._z, "u": self._u,
                "p_ext": self._p_ext, "p_drive": self._p_drive,
                "step": self._step, "advance_count": self._advance_count,
                "weights": tuple(self._w), "endpoints": self.endpoints,
                "completed": self.completed,
                "initial_state_note": self._initial_state_note}

    def clone_for_preview(self) -> "DiscretePrimitive":
        twin = DiscretePrimitive(
            self.name, self.alpha, self.beta, self.tau_p, self.basis_centers,
            self.basis_widths_inv, self._w, endpoints=self.endpoints,
            coupling_unit=self.coupling_unit, origin=self.origin)
        twin._z, twin._u = self._z, self._u
        twin._p_ext, twin._p_drive = self._p_ext, self._p_drive
        twin._step = self._step
        twin._advance_count = self._advance_count
        twin._substeps_last = self._substeps_last
        twin._last_dot = self._last_dot
        twin._last_ddot = self._last_ddot
        twin._last_note = self._last_note
        twin._last_clock = self._last_clock
        twin._initial_state_note = self._initial_state_note
        return twin


# ══════════════════════════════════════════════════════════════
# 参考导入（fit_reference：解析／采样／示教 → 权重＋初值＋归一化登记）
# ══════════════════════════════════════════════════════════════
def _default_periodic_basis(n_basis: int) -> Tuple[List[float], List[float]]:
    """默认周期核：n 个中心均匀覆盖 [0,2π)，集中度按中心间隔定宽。"""
    if n_basis < 3:
        raise PrimitiveError("周期核数量至少为 3，实际 %r" % (n_basis,),
                             where="_default_periodic_basis")
    centers = [2.0 * math.pi * i / n_basis for i in range(n_basis)]
    delta = 2.0 * math.pi / n_basis
    kappa = 2.0 / delta ** 2
    return centers, [kappa] * n_basis


def _default_discrete_basis(n_basis: int) -> Tuple[List[float], List[float]]:
    """默认高斯核：n 个中心均匀覆盖 (0,1)，宽度按间隔定宽。"""
    if n_basis < 3:
        raise PrimitiveError("高斯核数量至少为 3，实际 %r" % (n_basis,),
                             where="_default_discrete_basis")
    centers = [0.5 / n_basis + i / n_basis for i in range(n_basis)]
    delta = 1.0 / n_basis
    h = 2.0 / delta ** 2
    return centers, [h] * n_basis


def fit_reference_periodic(
        reference: ReferenceSampleSet, center: float, scale: float,
        phase_zero: float, version: str,
        stiffness: float = 4.0 * math.pi ** 2,
        damping: float = 4.0 * math.pi,
        basis: Optional[Tuple[Sequence[float], Sequence[float]]] = None,
        n_basis: int = 12,
        analytic_first: Optional[Callable[[float], float]] = None,
        name: str = "periodic_from_reference") -> Tuple[
            RhythmicPrimitive, ReferenceNormalization]:
    """周期参考导入：权重最小二乘拟合＋初值导入＋归一化登记。

    参考（解析／采样／示教统一为样本集）在归一化坐标 z=(q-center)/scale
    上给出目标强迫项 F_target=a_ref+k·z+d·v（v=dz/dχ，a=dv/dχ；一阶导数
    解析优先，否则网格差分），对归一化周期核做线性最小二乘。初值取参考
    在 phase_zero 处的形状值与一阶导数（不统一设零）。导入方法与误差写
    入 ReferenceNormalization（拟合残差 RMS 与闭环回放 RMS）。
    """
    where = "fit_reference_periodic"
    if reference.coordinate != "phase":
        raise PrimitiveError("周期导入要求样本集坐标为 phase（χ），实际 %r"
                             % (reference.coordinate,), where=where)
    if not (math.isfinite(center) and math.isfinite(scale)
            and abs(scale) > 1e-12):
        raise PrimitiveError("归一化中心须有限、尺度须非零（center=%r, "
                             "scale=%r）" % (center, scale), where=where)
    pts = reference.points
    if len(pts) < 8:
        raise PrimitiveError("周期参考样本过少（%d 点，至少 8 点）"
                             % (len(pts),), where=where)
    span = pts[-1][0] - pts[0][0]
    if span < 0.5 or span > 1.5:
        raise PrimitiveError(
            "周期参考坐标跨度 %r 偏离一个周期（应在 0.5 至 1.5 拍之间）"
            % (span,), where=where)
    if basis is None:
        centers, kappas = _default_periodic_basis(n_basis)
    else:
        centers = [float(c) for c in basis[0]]
        kappas = [float(k) for k in basis[1]]
    xs = np.asarray([p[0] for p in pts], dtype=float)
    qs = np.asarray([p[1] for p in pts], dtype=float)
    zs = (qs - center) / scale
    if analytic_first is not None:
        d1 = np.asarray([analytic_first(float(x)) / scale for x in xs])
    else:
        d1 = np.gradient(zs, xs)
    d2 = np.gradient(d1, xs)
    f_target = d2 + stiffness * zs + damping * d1
    # 设计矩阵：归一化周期核 Ψ_k(2πχ)/ΣΨ_j(2πχ)
    theta = 2.0 * math.pi * xs
    mat = np.empty((len(xs), len(centers)), dtype=float)
    for i, th in enumerate(theta):
        psis = [math.exp(kp * (math.cos(th - c) - 1.0))
                for c, kp in zip(centers, kappas)]
        den = sum(psis)
        for kk, psi in enumerate(psis):
            mat[i, kk] = psi / den
    sol, _res, _rank, _sv = np.linalg.lstsq(mat, f_target, rcond=None)
    weights = [float(x) for x in sol]
    fit_rms = float(np.sqrt(np.mean((mat @ np.asarray(weights) - f_target)
                                    ** 2)))
    primitive = RhythmicPrimitive(
        name, stiffness, damping, centers, kappas, weights,
        phase_coordinate="cumulative_beats",
        origin="参考导入（%s，%s）" % (reference.source_kind,
                                      reference.origin_note))
    # 初值导入：参考在 phase_zero 处的形状值与一阶导数（线性插值取值）。
    if not pts[0][0] <= phase_zero <= pts[-1][0]:
        raise PrimitiveError(
            "phase_zero %r 不在参考坐标覆盖区间 [%r, %r] 内"
            % (phase_zero, pts[0][0], pts[-1][0]), where=where)
    z0 = float(np.interp(phase_zero, xs, zs))
    v0 = float(np.interp(phase_zero, xs, d1))
    primitive.reset(z0, v0, phase_zero,
                    note="初值由参考导入：z0=z_ref(%r)=%r，v0=dz/dχ=%r（%s）"
                         % (phase_zero, z0, v0, reference.source_kind))
    # 闭环回放误差：从 phase_zero 起走一个周期跨度，与周期回绕后的参考
    # 逐点比较并求偏差 RMS（参考以 span 为周期）。
    prim_eval = RhythmicPrimitive(
        name + "_eval", stiffness, damping, centers, kappas, weights,
        origin="回放评估副本")
    prim_eval.reset(z0, v0, phase_zero, note="评估副本")
    rollout_errs: List[float] = []
    steps = 240
    for i in range(1, steps + 1):
        chi_i = phase_zero + span * i / steps
        clock = CanonicalClockSample(
            step=i, t_s=i / float(steps), chi=chi_i, chi_dot=1.0,
            source="check_fixture:fit_reference_rollout")
        s = prim_eval.advance(clock)
        chi_ref = (chi_i - pts[0][0]) % span + pts[0][0]
        ref_val = float(np.interp(chi_ref, xs, zs))
        rollout_errs.append(s.z - ref_val)
    rollout_rms = float(np.sqrt(np.mean(np.asarray(rollout_errs) ** 2)))
    normalization = ReferenceNormalization(
        center=center, scale=scale, phase_coordinate="cumulative_beats",
        phase_zero=phase_zero, version=version,
        source_kind=reference.source_kind, origin_note=reference.origin_note,
        endpoints=None, fit_method="least_squares_normalized_periodic_kernels",
        fit_rms_shape=fit_rms, rollout_rms_shape=rollout_rms,
        n_basis=len(centers), n_samples=len(pts),
        initial_state_note=primitive._initial_state_note)
    return primitive, normalization


def fit_reference_discrete(
        reference: ReferenceSampleSet, x0: float, g: float, version: str,
        alpha: float = 25.0, beta: float = 6.25, tau_p: float = 1.0,
        basis: Optional[Tuple[Sequence[float], Sequence[float]]] = None,
        n_basis: int = 10,
        analytic_first: Optional[Callable[[float], float]] = None,
        name: str = "discrete_from_reference") -> Tuple[
            DiscretePrimitive, ReferenceNormalization]:
    """单次参考导入：高斯核权重最小二乘＋起终点与归一化登记。

    参考（解析／采样／示教统一为样本集）在归一化坐标 z=(q-x0)/(g-x0)、
    进度 p∈[0,1] 上给出目标驱动 f_target=τ²a−α(β(1−z)−τu)（u=dz/dp，
    a=du/dp；一阶导数解析优先，否则网格差分），对 f(p)=Σψ_k w_k(1−p)
    做线性最小二乘。起点终点（绝对值）保存于 endpoints 与
    ReferenceNormalization。
    """
    where = "fit_reference_discrete"
    if reference.coordinate != "progress":
        raise PrimitiveError("单次导入要求样本集坐标为 progress（p），实际 "
                             "%r" % (reference.coordinate,), where=where)
    span_scale = float(g) - float(x0)
    if not (math.isfinite(span_scale) and abs(span_scale) > 1e-12):
        raise PrimitiveError("单次起终点幅度须非零（x0=%r, g=%r）"
                             % (x0, g), where=where)
    pts = reference.points
    if len(pts) < 8:
        raise PrimitiveError("单次参考样本过少（%d 点，至少 8 点）"
                             % (len(pts),), where=where)
    p_lo, p_hi = pts[0][0], pts[-1][0]
    if abs(p_lo) > 1e-9 or abs(p_hi - 1.0) > 1e-9:
        raise PrimitiveError(
            "单次参考进度须覆盖 [0,1]（实际 [%r, %r]）" % (p_lo, p_hi),
            where=where)
    if basis is None:
        centers, hs = _default_discrete_basis(n_basis)
    else:
        centers = [float(c) for c in basis[0]]
        hs = [float(x) for x in basis[1]]
    xs = np.asarray([p[0] for p in pts], dtype=float)
    qs = np.asarray([p[1] for p in pts], dtype=float)
    zs = (qs - float(x0)) / span_scale
    if analytic_first is not None:
        d1 = np.asarray([analytic_first(float(x)) / span_scale for x in xs])
    else:
        d1 = np.gradient(zs, xs)
    d2 = np.gradient(d1, xs)
    f_target = tau_p ** 2 * d2 - alpha * (beta * (1.0 - zs) - tau_p * d1)
    mat = np.empty((len(xs), len(centers)), dtype=float)
    for i, x in enumerate(xs):
        pc = min(max(float(x), 0.0), 1.0)
        for kk, (c, hh) in enumerate(zip(centers, hs)):
            mat[i, kk] = math.exp(-hh * (pc - c) ** 2) * (1.0 - pc)
    sol, _res, _rank, _sv = np.linalg.lstsq(mat, f_target, rcond=None)
    weights = [float(v) for v in sol]
    fit_rms = float(np.sqrt(np.mean((mat @ np.asarray(weights) - f_target)
                                    ** 2)))
    primitive = DiscretePrimitive(
        name, alpha, beta, tau_p, centers, hs, weights,
        endpoints=(float(x0), float(g)),
        origin="参考导入（%s，%s）" % (reference.source_kind,
                                      reference.origin_note))
    z0 = float(zs[0])
    u0 = float(d1[0])
    primitive.reset(z0, u0, 0.0,
                    note="初值由参考导入：z0=z_ref(0)=%r，u0=dz/dp=%r（%s）"
                         % (z0, u0, reference.source_kind))
    # 闭环回放误差：进度 0→1 在基元上回放并求偏差 RMS。
    prim_eval = DiscretePrimitive(
        name + "_eval", alpha, beta, tau_p, centers, hs, weights,
        endpoints=(float(x0), float(g)), origin="回放评估副本")
    prim_eval.reset(z0, u0, 0.0, note="评估副本")
    rollout_errs: List[float] = []
    steps = 240
    for i in range(1, steps + 1):
        p_i = i / float(steps)
        clock = CanonicalClockSample(
            step=i, t_s=p_i, chi=p_i, chi_dot=0.0,
            source="check_fixture:fit_reference_rollout",
            canonical_progress=p_i, progress_dot=1.0)
        s = prim_eval.advance(clock)
        ref_val = float(np.interp(p_i, xs, zs))
        rollout_errs.append(s.z - ref_val)
    rollout_rms = float(np.sqrt(np.mean(np.asarray(rollout_errs) ** 2)))
    normalization = ReferenceNormalization(
        center=float(x0), scale=span_scale,
        phase_coordinate="canonical_progress", phase_zero=0.0,
        version=version, source_kind=reference.source_kind,
        origin_note=reference.origin_note,
        endpoints=(float(x0), float(g)),
        fit_method="least_squares_gaussian_kernels_times_one_minus_p",
        fit_rms_shape=fit_rms, rollout_rms_shape=rollout_rms,
        n_basis=len(centers), n_samples=len(pts),
        initial_state_note=primitive._initial_state_note)
    return primitive, normalization
