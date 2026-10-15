# -*- coding: utf-8 -*-
"""rhythm.py — 统一内部节律服务（事项 I-220，L2 队）。

节律节点按 I-219 配方声明（chi_c 共模、epsilon 差动、派生视图与折翼
视图），服务负责累计周期预算、拍号、左右差频积分、初始相差、形状相
位与导数。已批准公式（节律补充一节）：

    dot(chi_c) = f_c
    dot(epsilon) = dfreq
    chi_L = chi_c + epsilon
    chi_R = chi_c - epsilon + delta_0

判据一（单一提交）：同一物理步只经 advance 提交一次；read 与数值子步
不推进状态、不消费事件。判据二（复用旧核）：形状相位经
modules.aero.kinematics.split_phase_map 复用旧分裂周期核（公共纯函数
与旧内部核同式，等价性由检查器网格核对；旧函数保留原位不改）；回绕
进度、未回绕总进度与拍号分别映射；过去拍数未知时按初值规则接入并声
明；右翼偏移 delta_0 在派生视图中只施加一次。

判据三（规范时间与事件）：规范时间为已提交步数乘步长，单调不回退；
停拍继续走时（闸门只作用于输出不冻结相位）、冻结、重置、锁相修正与
拍相位事件分别声明；持续差频与固定同坐标相差不得同时硬性指定（锁相
声明与连续差频共存即报错）。

判据七（原生 Hopf 可选后端）：HopfBackend 复用 ltv/wavegen_family 参
考核（只读），外裹有限值、正慢度、有效半径与解缠步长保护；较大相位
步采用有说明的数值子步，子步不增加真实物理步或事件消费；min_H 为有
限采样检查，记录采样密度与安全余量，不冒称连续区间严格证明。
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from modules.aero.kinematics import split_phase_map

#: 参考核版本：ltv/wavegen_family.py 内容散列（只读复用，不改其实现）。
_WAVEGEN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.
                                                             dirname(
    os.path.abspath(__file__)))), "ltv", "wavegen_family.py")
try:
    with open(_WAVEGEN_PATH, "rb") as _fh:
        REFERENCE_CORE_VERSION = "wavegen_family:" \
            + hashlib.sha256(_fh.read()).hexdigest()[:16]
except OSError:
    REFERENCE_CORE_VERSION = "wavegen_family:unavailable"


class RhythmServiceError(ValueError):
    """节律服务错误（带节点或事件定位）。"""

    def __init__(self, message, where=None):
        self.where = where
        super().__init__("%s（位置：%s）" % (message, where or "节律服务"))


#: 事件种类（判据三：分别声明）
EVENT_BEAT = "beat"                    # 拍相位事件（过拍边）
EVENT_STOP_FLAP = "stop_flap"          # 停拍（继续走时，输出闸门）
EVENT_RESUME_FLAP = "resume_flap"      # 复拍
EVENT_FREEZE = "freeze"                # 冻结（相位不再推进）
EVENT_UNFREEZE = "unfreeze"            # 解冻
EVENT_RESET = "reset"                  # 重置回初始零点
EVENT_PHASE_LOCK = "phase_lock"        # 锁相修正（出口 d_epsilon_advance）


@dataclass(frozen=True)
class RhythmEvent:
    """单条已排定事件（预览副本执行；真实事件由真实步提交）。"""
    kind: str                  # EVENT_* 之一
    at_step: int               # 规范步号（真实提交）；预览在副本按步执行
    payload: Dict[str, float] = field(default_factory=dict)  # 事件参数


#: 参数程序可排定的参数名（判据四补证：已排定参数程序经生产预览入口
#: 在副本按步执行，改变副本采用值与样本；原件与真实提交不受预览影响）
PARAM_PROGRAM_KEYS = ("freq_hz", "dfreq_hz")


@dataclass(frozen=True)
class ParamProgramStep:
    """单条已排定参数程序步：在 at_step 步首把参数改为 value。

    freq_hz 改变本步起相位积分所用采用频率；dfreq_hz 改变差动累计采
    用值。排定时校验类型与有限性（频率须为正）；执行与事件同在步首、
    同受原子回滚保护；预览只在副本应用，原件待办不受影响。
    """
    at_step: int                  # 生效步号（真实提交或预览副本步）
    param: str                    # PARAM_PROGRAM_KEYS 之一
    value: float                  # 目标值


@dataclass(frozen=True)
class RhythmSample:
    """一次提交后的节律读数（只读；含判据一要求的量与导数）。

    三类进度分别映射（判据二）：wrapped_tau 为回绕进度（量纲一，
    [0,1)）；chi_c 为未回绕累计总进度（累计拍数）；beat_index 为拍号。
    形状相位 ph 与 dph/dtau 来自旧分裂周期核（复用，同式）。
    """
    step: int                          # 已提交物理步号
    t_s: float                         # 规范时间 = 步号 × 步长
    chi_c: float                       # 共模未回绕累计拍数
    epsilon: float                     # 差动未回绕累计拍数
    beat_index: int                    # 拍号（floor(chi_c)）
    wrapped_tau: float                 # 回绕进度（chi_c 的小数部分）
    chi_L: float                       # 左视图 = chi_c + epsilon
    chi_R: float                       # 右视图 = chi_c - epsilon + delta_0
    chi_fold: float                    # 折翼视图 = chi_c + fold_offset
    shape_phase: float                 # 形状相位 ph（旧分裂核，rad）
    shape_phase_rate: float            # dph/dtau（旧分裂核）
    frequency_hz: float                # 本步共模频率（采用后的预算）
    dfreq_hz: float                    # 本步差频请求（登记不叠加）
    delta_0: float                     # 右翼初始相差（只施加一次）
    stroke_gate: float                 # 输出闸门（1 拍动 / 0 停拍）
    frozen: bool                       # 冻结状态
    events: Tuple[str, ...] = ()         # 本次实际提交消费的事件；只读，可供生成器复位

    def beat_fraction(self) -> float:
        return self.wrapped_tau


class RhythmService:
    """共模与左右差动的统一内部节律服务。

    构造入参全部显式（不读全局）：频率、步长、分裂比、右翼初始相差
    delta_0（beats）、折翼派生偏移（beats，零偏移时折翼相位偏移由通
    道参考函数唯一持有，见 I-219 F03）、共模初值 chi_c0 与过去拍数未
    知声明。advance 为唯一真实提交入口。
    """

    def __init__(self, frequency_hz: float, dt: float, split: float,
                 delta_0_beats: float = 0.0, fold_offset_beats: float = 0.0,
                 chi_c0: float = 0.0, epsilon0: float = 0.0,
                 past_beats_known: bool = False):
        if not (math.isfinite(frequency_hz) and frequency_hz > 0.0):
            raise RhythmServiceError("共模频率必须为正有限数，实际 %r"
                                     % (frequency_hz,), where="构造")
        if not (math.isfinite(dt) and dt > 0.0):
            raise RhythmServiceError("步长必须为正有限数，实际 %r"
                                     % (dt,), where="构造")
        self.dt = float(dt)
        self.split = float(split)
        self.delta_0 = float(delta_0_beats)
        self.fold_offset = float(fold_offset_beats)
        self._chi_c = float(chi_c0)
        self._epsilon = float(epsilon0)
        self._chi_c0 = float(chi_c0)
        self._epsilon0 = float(epsilon0)
        #: 过去拍数未知声明（判据二）：初值之前的历史不存在，历史查询
        #: 对该区间返回未初始化，不由当前频率外推补齐。
        self.past_beats_known = bool(past_beats_known)
        self._step = 0
        self._freq = float(frequency_hz)
        self._dfreq_last = 0.0
        self._gate = 1.0
        self._frozen = False
        self._pending_events: List[RhythmEvent] = []
        self._consumed_events: List[RhythmEvent] = []
        self._pending_params: List[ParamProgramStep] = []
        self._applied_params: List[ParamProgramStep] = []
        self._last_applied: List[str] = []
        self._last_sample: Optional[RhythmSample] = None
        #: 锁相声明与连续差频不得同时硬性指定（判据三）
        self._phase_lock_declared = False
        self._continuous_dfreq = False

    # ── 状态与提交（判据一） ──
    def state(self) -> Dict[str, float]:
        """只读状态（克隆与一致性比较用）。"""
        return {"step": self._step, "chi_c": self._chi_c,
                "epsilon": self._epsilon, "freq": self._freq,
                "gate": self._gate, "frozen": float(self._frozen),
                "pending": float(len(self._pending_events)),
                "consumed": float(len(self._consumed_events)),
                "pending_params": float(len(self._pending_params)),
                "applied_params": float(len(self._applied_params))}

    def read(self) -> RhythmSample:
        """读当前状态为样本（不推进、不消费事件；判据一）。"""
        if self._last_sample is not None and self._last_sample.step == \
                self._step:
            return self._last_sample
        return self._sample()

    def _sample(self) -> RhythmSample:
        chi_c = self._chi_c
        tau = chi_c % 1.0
        ph, dph = split_phase_map(tau, self.split)
        return RhythmSample(
            step=self._step, t_s=self._step * self.dt,
            chi_c=chi_c, epsilon=self._epsilon,
            beat_index=int(math.floor(chi_c)), wrapped_tau=tau,
            chi_L=chi_c + self._epsilon,
            chi_R=chi_c - self._epsilon + self.delta_0,
            chi_fold=chi_c + self.fold_offset,
            shape_phase=ph, shape_phase_rate=dph,
            frequency_hz=self._freq, dfreq_hz=self._dfreq_last,
            delta_0=self.delta_0, stroke_gate=self._gate,
            frozen=self._frozen)

    def advance(self, frequency_hz: Optional[float] = None,
                dfreq_hz: Optional[float] = 0.0) -> RhythmSample:
        """提交一个真实物理步（唯一推进入口；判据一）。

        冻结时相位不推进但仍消耗一步（停拍继续走时同类：时间单调，状
        态按声明处理）。事件过拍边在本步提交一次；数值子步与多次读取
        不额外产生步号或事件。

        判据四补证（None 语义）：frequency_hz 为 None 表示本步没有新
        的闭环频率需求——保持当前采用频率（含已排定参数程序在本步首
        改写后的值）；显式数值为新闭环采用，与程序同一步时显式值（较
        新信息）胜出。
        dfreq_hz 为 None 同理保持上次采用差频；显式 0.0 为明确零值。
        旧调用形态（显式两参）行为不变。

        返修 F05（原子性）：本步内任何事件或程序处理失败都整体回滚
        ——保留最后有效步、尚未消费的事件与未执行程序，步号、频率、
        相位、闸门与消费记录不留部分提交。非法配置组合（锁相与持续差
        频同时声明）下拒绝发布已采用结果；积分产生非有限相位时拒绝
        发布无效相位视图。
        """
        if frequency_hz is not None and not (
                math.isfinite(frequency_hz) and frequency_hz > 0.0):
            raise RhythmServiceError("本步频率必须为正有限数，实际 %r"
                                     % (frequency_hz,), where="advance")
        if dfreq_hz is not None and not math.isfinite(dfreq_hz):
            raise RhythmServiceError("差频请求必须为有限数，实际 %r"
                                     % (dfreq_hz,), where="advance")
        if self._phase_lock_declared and self._continuous_dfreq:
            raise RhythmServiceError(
                "非法配置组合：锁相声明与持续差频同时生效，推进入口拒"
                "绝发布已采用结果（分别声明，允许相差演化时不锁相）",
                where="advance")
        snapshot = (self._step, self._chi_c, self._epsilon, self._freq,
                    self._dfreq_last, self._gate, self._frozen,
                    list(self._pending_events), list(self._consumed_events),
                    list(self._pending_params), list(self._applied_params),
                    list(self._last_applied), self._last_sample)
        try:
            # 事件与参数程序在步首执行（本步提交即生效）；程序先执行，
            # 显式闭环需求（较新信息）随后覆盖同一步的程序值；冻结当
            # 步不再推进相位，重置当步自初值起走，停拍当步闸门即闭。
            # 步号仍每提交一次加一。
            self._step += 1
            self._run_due_param_steps()
            if frequency_hz is not None:
                self._freq = float(frequency_hz)
            if dfreq_hz is not None:
                self._dfreq_last = float(dfreq_hz)
            self._run_due_events()
            if not self._frozen:
                self._chi_c += self._freq * self.dt
                self._epsilon += self._dfreq_last * self._dt_epsilon()
            if not (math.isfinite(self._chi_c)
                    and math.isfinite(self._epsilon)):
                raise RhythmServiceError(
                    "本步积分或事件产生非有限相位（chi_c=%r epsilon=%r），"
                    "拒绝发布无效相位视图" % (self._chi_c, self._epsilon),
                    where="advance")
            sample = replace(self._sample(), events=tuple(
                event.kind for event in self._consumed_events[len(snapshot[8]):]))
        except Exception as exc:
            (self._step, self._chi_c, self._epsilon, self._freq,
             self._dfreq_last, self._gate, self._frozen,
             self._pending_events, self._consumed_events,
             self._pending_params, self._applied_params,
             self._last_applied, self._last_sample) = snapshot
            if isinstance(exc, RhythmServiceError):
                raise
            raise RhythmServiceError("本步事件处理失败，已整体回滚保留最"
                                     "后有效步与未消费事件：%r" % (exc,),
                                     where="advance") from exc
        self._last_sample = sample
        return sample

    def _dt_epsilon(self) -> float:
        return self.dt

    # ── 参数程序（判据四补证） ──
    def schedule_param_step(self, step: ParamProgramStep) -> None:
        """排定一条参数程序步（排定时校验；预览只在副本应用）。

        与事件同受 F05 式原子回滚保护：执行失败整体回滚，待办程序保
        留。真实服务与预览副本共用本排定语义。
        """
        if step.param not in PARAM_PROGRAM_KEYS:
            raise RhythmServiceError("未知参数程序参数名 %r（可取 %s）"
                                     % (step.param, PARAM_PROGRAM_KEYS),
                                     where="schedule_param_step")
        if isinstance(step.at_step, bool) or not isinstance(step.at_step,
                                                            int):
            raise RhythmServiceError("参数程序步号必须为整数，实际 %r"
                                     % (step.at_step,), where="schedule_"
                                     "param_step")
        v = step.value
        if isinstance(v, bool) or not isinstance(v, (int, float))                 or not math.isfinite(float(v)):
            raise RhythmServiceError("参数程序值必须为有限数值，实际 %r"
                                     % (v,), where="schedule_param_step")
        if step.param == "freq_hz" and float(v) <= 0.0:
            raise RhythmServiceError("参数程序频率必须为正，实际 %r"
                                     % (v,), where="schedule_param_step")
        self._pending_params.append(step)

    def _run_due_param_steps(self) -> None:
        """执行本步到期的参数程序（步首、先于事件；原子回滚内）。"""
        due = [q for q in self._pending_params if q.at_step <= self._step]
        self._pending_params = [q for q in self._pending_params
                                if q.at_step > self._step]
        self._last_applied = []
        for q in due:
            if q.param == "freq_hz":
                self._freq = float(q.value)
            elif q.param == "dfreq_hz":
                self._dfreq_last = float(q.value)
            self._applied_params.append(q)
            self._last_applied.append("%s=%r@%d" % (q.param, q.value,
                                                    q.at_step))

    def adopt(self, frequency_hz: float) -> None:
        """显式采用一个频率值（预览引擎播种副本采用值用；带校验）。"""
        if not (math.isfinite(frequency_hz) and frequency_hz > 0.0):
            raise RhythmServiceError("采用频率必须为正有限数，实际 %r"
                                     % (frequency_hz,), where="adopt")
        self._freq = float(frequency_hz)

    # ── 事件（判据三） ──
    def schedule_event(self, event: RhythmEvent) -> None:
        """排定事件（真实事件由真实步提交；预览只在副本执行）。

        返修 F05：事件字段在排定时校验类型与有限性——锁相修正量必须
        为有限数值，列表等非法类型与非有限值在排定时即拒绝，不进入待
        办队列。
        """
        if event.kind not in (EVENT_BEAT, EVENT_STOP_FLAP, EVENT_RESUME_FLAP,
                              EVENT_FREEZE, EVENT_UNFREEZE, EVENT_RESET,
                              EVENT_PHASE_LOCK):
            raise RhythmServiceError("未知事件种类 %r" % (event.kind,),
                                     where="schedule_event")
        if event.kind == EVENT_PHASE_LOCK:
            corr = event.payload.get("d_epsilon_advance", 0.0)
            if isinstance(corr, bool) or not isinstance(corr, (int, float)):
                raise RhythmServiceError(
                    "锁相修正量 d_epsilon_advance 必须为数值，实际 %r（类"
                    "型 %s）；非法事件在排定时拒绝" % (corr,
                                                        type(corr).__name__),
                    where="schedule_event")
            if not math.isfinite(float(corr)):
                raise RhythmServiceError(
                    "锁相修正量 d_epsilon_advance 必须为有限数值，实际 %r"
                    % (corr,), where="schedule_event")
        self._pending_events.append(event)

    def declare_phase_lock(self, declared: bool = True) -> None:
        """声明锁相（目标相差固定为硬约束）。

        返修 F04：采用候选设置前先校验互斥关系——失败保留上一有效配
        置（标志不被置位），两种声明顺序一致。
        """
        if bool(declared) and self._continuous_dfreq:
            raise RhythmServiceError(
                "持续差频与固定同坐标相差被同时硬性指定：锁相声明与连续"
                "差频请求互斥（节律补充：分别声明，允许相差演化时不锁"
                "相）；失败保留上一有效配置",
                where="declare_phase_lock")
        self._phase_lock_declared = bool(declared)

    def set_continuous_dfreq(self, continuous: bool) -> None:
        """登记差频是否为持续驱动（判据三互斥检查；返修 F04 先验后改）。"""
        if bool(continuous) and self._phase_lock_declared:
            raise RhythmServiceError(
                "持续差频与固定同坐标相差被同时硬性指定：连续差频请求与"
                "锁相声明互斥；失败保留上一有效配置",
                where="set_continuous_dfreq")
        self._continuous_dfreq = bool(continuous)

    def _run_due_events(self) -> None:
        due = [e for e in self._pending_events if e.at_step <= self._step]
        self._pending_events = [e for e in self._pending_events
                                if e.at_step > self._step]
        for event in due:
            self._apply_event(event)
            self._consumed_events.append(event)

    def _apply_event(self, event: RhythmEvent) -> None:
        if event.kind == EVENT_STOP_FLAP:
            self._gate = 0.0           # 停拍继续走时：闸门只作用于输出
        elif event.kind == EVENT_RESUME_FLAP:
            self._gate = 1.0
        elif event.kind == EVENT_FREEZE:
            self._frozen = True
        elif event.kind == EVENT_UNFREEZE:
            self._frozen = False
        elif event.kind == EVENT_RESET:
            self._chi_c = self._chi_c0
            self._epsilon = self._epsilon0
            self._gate = 1.0
            self._frozen = False
        elif event.kind == EVENT_PHASE_LOCK:
            # 锁相修正出口：误差进入相对推进（epsilon 修正），不只共同
            # 调频（节律补充二节）。排定时已校验；此处防御性复查，非法
            # 值经 advance 的原子回滚整体拒绝（返修 F05）。
            corr = event.payload.get("d_epsilon_advance", 0.0)
            if isinstance(corr, bool) or not isinstance(corr, (int, float)) \
                    or not math.isfinite(float(corr)):
                raise RhythmServiceError(
                    "锁相修正量非法（%r，类型 %s），本步整体回滚"
                    % (corr, type(corr).__name__), where="_apply_event")
            self._epsilon += float(corr)
        # EVENT_BEAT：过拍边事件本身无状态副作用，消费即登记。

    # ── 克隆（判据六：预览隔离的副本来源） ──
    def clone(self) -> "RhythmService":
        """深复制服务（预览只在副本上推进；原件不被触碰）。"""
        twin = RhythmService(
            frequency_hz=self._freq, dt=self.dt, split=self.split,
            delta_0_beats=self.delta_0,
            fold_offset_beats=self.fold_offset, chi_c0=self._chi_c0,
            epsilon0=self._epsilon0,
            past_beats_known=self.past_beats_known)
        twin._chi_c = self._chi_c
        twin._epsilon = self._epsilon
        twin._step = self._step
        twin._gate = self._gate
        twin._frozen = self._frozen
        twin._dfreq_last = self._dfreq_last
        twin._pending_events = list(self._pending_events)
        twin._consumed_events = list(self._consumed_events)
        twin._pending_params = list(self._pending_params)
        twin._applied_params = list(self._applied_params)
        twin._last_applied = list(self._last_applied)
        twin._phase_lock_declared = self._phase_lock_declared
        twin._continuous_dfreq = self._continuous_dfreq
        twin._last_sample = self._last_sample
        return twin


# ── 判据七：原生 Hopf 可选后端（参考核只读复用加保护壳） ──
#: min_H 采样密度与安全余量（有限采样检查的登记口径，不冒称连续区间
#: 严格证明）。
HOPF_MIN_H_SAMPLES = 20001
HOPF_SLOWNESS_SAFETY_MARGIN = 1e-6
#: 单步允许的最大形状相位增量（弧度）：超过即按说明走数值子步。
HOPF_MAX_PHASE_STEP_RAD = 0.35
#: 解缠子步数上限（保护失败显式报告，不悄悄截断）。
HOPF_MAX_SUBSTEPS = 64


class HopfBackend:
    """原生 Hopf 节律后端（可选；兼容合作配方不依赖本类）。

    复用 ltv/wavegen_family 的 H_profile、min_H 与 hopf_step（参考核，
    只读；版本按文件内容散列登记）。装载与每步采用时校验：状态有限、
    慢度剖面最小值为正（含安全余量；有限采样，非严格证明）、初始半
    径有效（大于零）与单步相位增量上限；较大相位步按说明拆数值子步
    ——子步不增加真实物理步或事件消费（真实步号仍由 RhythmService 唯
    一提交）。
    """

    def __init__(self, state: Tuple[float, float], f_hz: float,
                 b1: float, b3: float, amplitude_a: float,
                 kappa: float):
        import numpy as _np
        from ltv import wavegen_family as _wf
        self._wf = _wf
        x, y = float(state[0]), float(state[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise RhythmServiceError("Hopf 初始状态必须为有限值，实际 "
                                     "(%r, %r)" % (x, y), where="HopfBackend")
        r = math.hypot(x, y)
        if not (math.isfinite(r) and r > 0.0):
            raise RhythmServiceError("Hopf 初始半径必须有效（大于零），"
                                     "实际 %r；零半径不提供有效形状相位"
                                     % (r,), where="HopfBackend")
        for name, v in (("f_hz", f_hz), ("b1", b1), ("b3", b3),
                        ("amplitude_a", amplitude_a), ("kappa", kappa)):
            if not math.isfinite(float(v)):
                raise RhythmServiceError("Hopf 参数 %s 必须为有限数，实际"
                                         " %r" % (name, v), where="HopfBackend")
        grid = _np.linspace(0.0, 1.0, HOPF_MIN_H_SAMPLES)
        h_min = float(_np.min(_wf.H_profile(grid, float(b1), float(b3))))
        if h_min <= HOPF_SLOWNESS_SAFETY_MARGIN:
            raise RhythmServiceError(
                "慢度剖面最小值非正（有限采样 %d 点，min=%r，安全余量 "
                "%r）：正慢度校验失败，不悄悄套用参考核截断"
                % (HOPF_MIN_H_SAMPLES, h_min, HOPF_SLOWNESS_SAFETY_MARGIN),
                where="HopfBackend")
        #: 有限采样检查登记（不冒称连续区间严格证明）
        self.slowness_check = {"samples": HOPF_MIN_H_SAMPLES,
                               "min_h": h_min,
                               "margin": HOPF_SLOWNESS_SAFETY_MARGIN,
                               "claim": "有限采样检查，非连续区间严格证明"}
        self.state = (float(x), float(y))
        self.f_hz = float(f_hz)
        self.b1, self.b3 = float(b1), float(b3)
        self.amplitude_a = float(amplitude_a)
        self.kappa = float(kappa)
        self._phase_unwrapped = math.atan2(y, x)
        self.substeps_last = 0
        self.reference_core = REFERENCE_CORE_VERSION

    def _principal_delta(self, prev: Tuple[float, float],
                         new: Tuple[float, float]) -> float:
        """相邻两次状态间的主值角增量（最短弧；单子步转角小于 π）。"""
        delta = math.atan2(new[1], new[0]) - math.atan2(prev[1], prev[0])
        while delta > math.pi:
            delta -= 2.0 * math.pi
        while delta < -math.pi:
            delta += 2.0 * math.pi
        return delta

    def step(self, dt: float) -> Dict[str, float]:
        """推进一个真实步（内部按需拆数值子步；步号不由本类管理）。

        返修 F06：解缠在每个受保护子步内增量累计——子步数按实际最大
        角速率（名义角速率除以慢度最小值，正慢度界限）估计，保证每个
        子步转角小于 π，主值增量求和不丢整圈；不再只在终点做一次最短
        弧解缠。数值子步不产生真实物理步或事件消费。返回平面状态、累
        计解缠相位、半径与子步数。
        """
        if not (math.isfinite(dt) and dt > 0.0):
            raise RhythmServiceError("Hopf 步长必须为正有限数，实际 %r"
                                     % (dt,), where="HopfBackend.step")
        import numpy as _np
        # 实际最大角速率：慢度越小相位越快，用构造期有限采样得到的
        # min_H 作正慢度下界（登记口径：有限采样，非严格证明）
        h_min = self.slowness_check["min_h"]
        omega_max = 2.0 * math.pi * self.f_hz / h_min
        est_step = omega_max * dt
        n_sub = 1
        if est_step > HOPF_MAX_PHASE_STEP_RAD:
            n_sub = int(math.ceil(est_step / HOPF_MAX_PHASE_STEP_RAD))
            if n_sub > HOPF_MAX_SUBSTEPS:
                raise RhythmServiceError(
                    "单步相位增量上界 %r rad（按实际最大角速率估计）超出"
                    "子步上限 %d（解缠步长保护失败，显式报告不悄悄截断）"
                    % (est_step, HOPF_MAX_SUBSTEPS),
                    where="HopfBackend.step")
        h = dt / n_sub
        cur = _np.asarray(self.state, dtype=float)
        prev = self.state
        accumulated = self._phase_unwrapped
        for _ in range(n_sub):
            cur = self._wf.hopf_step(cur, h, self.amplitude_a, self.f_hz,
                                     self.b1, self.b3, self.kappa)
            if not (_np.all(_np.isfinite(cur))):
                raise RhythmServiceError("Hopf 状态发散为非有限值，显式报"
                                         "告", where="HopfBackend.step")
            new = (float(cur[0]), float(cur[1]))
            accumulated += self._principal_delta(prev, new)
            prev = new
        self.state = prev
        self._phase_unwrapped = accumulated
        self.substeps_last = n_sub
        return {"x": prev[0], "y": prev[1],
                "phase_unwrapped": accumulated,
                "radius": math.hypot(*prev), "substeps": n_sub}

    def clone(self) -> "HopfBackend":
        """克隆后端（预览隔离）。"""
        twin = HopfBackend(self.state, self.f_hz, self.b1, self.b3,
                           self.amplitude_a, self.kappa)
        twin._phase_unwrapped = self._phase_unwrapped
        twin.substeps_last = self.substeps_last
        return twin
