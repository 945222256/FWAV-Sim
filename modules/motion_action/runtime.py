# -*- coding: utf-8 -*-
"""runtime.py — 时钟与副本接口：历史、预览与隔离（事项 I-220，L2 队）。

判据四（预览策略固定）：副本执行已排定事件和参数程序；未产生的闭环
需求保持当前采用值（预览不发明未来反馈）；窗口按时间顺序从同一副本
状态推进，当前时刻样本与首次推进顺序明确；正滞后使用具名预测（副本
上按当前采用频率匀速推进，方法登记于预览元数据），负滞后只读历史，
禁止把当前动态状态向过去积分冒充历史。

判据五（历史规则明确）：登记历史长度、存储（逐已提交步定长记录）、
插值方法（相邻样本线性插值，登记）、跨参数版本与复位边界规则；查询
越过版本或纪元边界时如实返回 unsupported；历史不足时返回未初始化/
未支持状态，不以当前频率线性外推替代动态历史。

判据六（预览隔离）：预览副本隔离节律、数组、事件队列、事件标志与相
关缓存；预览结果全部带 preview 标记，不产生真实回执、不提交真实事
件、不写原生对象；正常与异常返回后真实状态逐项一致。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from modules.motion_action.rhythm import (
    RhythmEvent, RhythmSample, RhythmService, RhythmServiceError)

#: 历史插值方法登记（判据五）
HISTORY_INTERPOLATION = "linear_between_committed_samples"
#: 预测方法登记（v4：负滞后即超前预测与窗口同一规则——副本先播种
#: 当前采用频率，每步无新闭环需求保持采用值、已排定参数程序在副本步
#: 首执行；不再以固定旧频率逐步覆盖程序）
PREVIEW_PREDICTION_METHOD = "clone_window_consistent_program_aware"


@dataclass(frozen=True)
class HistoryEntry:
    """单条历史记录（对应一次真实提交步）。"""
    step: int
    t_s: float
    chi_c: float
    epsilon: float
    shape_phase: float
    frequency_hz: float
    param_version: str          # 参数版本（跨版本边界检查用）
    epoch: int                  # 复位纪元（复位事件开新纪元）


@dataclass(frozen=True)
class HistoryQueryResult:
    """历史查询结果（显式状态；不支持不静默外推）。"""
    status: str                     # present / uninitialized / unsupported
    entry: Optional[HistoryEntry] = None
    interpolated: Optional[float] = None   # 插值后的 chi_c（present 时）
    reason: str = ""


class HistoryBuffer:
    """逐已提交步的历史（判据五）。

    定长环形存储；复位（EVENT_RESET）开启新纪元；参数版本变更时逐条
    登记版本号。查询规则：请求时刻早于最早记录或初值之前（过去拍数未
    知）→ uninitialized；查询插值区间跨越参数版本或复位纪元 →
    unsupported；否则线性插值 present。
    """

    def __init__(self, max_entries: int = 4096):
        if max_entries <= 0:
            raise RhythmServiceError("历史长度必须为正", where="历史")
        self.max_entries = int(max_entries)
        self._entries: List[HistoryEntry] = []
        self._epoch = 0
        self._param_version = "v0"

    def set_param_version(self, version: str) -> None:
        """登记参数版本（跨版本边界规则：查询不得跨越）。"""
        self._param_version = str(version)

    def note_reset(self) -> None:
        """复位开启新纪元（边界规则：查询不得跨越纪元）。"""
        self._epoch += 1

    def record(self, sample: RhythmSample) -> None:
        self._entries.append(HistoryEntry(
            step=sample.step, t_s=sample.t_s, chi_c=sample.chi_c,
            epsilon=sample.epsilon, shape_phase=sample.shape_phase,
            frequency_hz=sample.frequency_hz,
            param_version=self._param_version, epoch=self._epoch))
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

    def __len__(self) -> int:
        return len(self._entries)

    def query_chi_c(self, t_s: float) -> HistoryQueryResult:
        """按时间查询共模累计拍数（判据五：不静默外推）。"""
        if not self._entries:
            return HistoryQueryResult(
                "uninitialized",
                reason="无历史记录，未初始化；不以当前频率线性外推替代")
        oldest, newest = self._entries[0], self._entries[-1]
        if t_s < oldest.t_s:
            return HistoryQueryResult(
                "uninitialized",
                reason="请求时刻 %r 早于最早记录 %r（含初值前过去拍数未知"
                       "区间）；不以当前频率线性外推替代" % (t_s, oldest.t_s))
        if t_s > newest.t_s:
            return HistoryQueryResult(
                "unsupported",
                reason="请求时刻 %r 晚于最新提交 %r：历史只覆盖已提交步，"
                       "未来由预览具名预测承担，不外推" % (t_s, newest.t_s))
        # 返修 F03：先识别精确样本——精确读取已有提交记录不涉及插值，
        # 返回该样本与其自身版本、纪元；只有确需在两条不同版本或纪元
        # 的记录之间插值时才拒绝。
        for e in self._entries:
            if abs(t_s - e.t_s) < 1e-12:
                return HistoryQueryResult("present", entry=e,
                                          interpolated=e.chi_c)
        for a, b in zip(self._entries, self._entries[1:]):
            if a.t_s <= t_s <= b.t_s:
                if a.param_version != b.param_version:
                    return HistoryQueryResult(
                        "unsupported", reason="插值区间跨参数版本 %s→%s，"
                        "不跨版本插值" % (a.param_version, b.param_version))
                if a.epoch != b.epoch:
                    return HistoryQueryResult(
                        "unsupported",
                        reason="插值区间跨复位纪元 %d→%d，不跨纪元插值"
                               % (a.epoch, b.epoch))
                if b.t_s == a.t_s:
                    return HistoryQueryResult("present", entry=a,
                                              interpolated=a.chi_c)
                frac = (t_s - a.t_s) / (b.t_s - a.t_s)
                return HistoryQueryResult(
                    "present", entry=a,
                    interpolated=a.chi_c + frac * (b.chi_c - a.chi_c))
        return HistoryQueryResult("present", entry=newest,
                                  interpolated=newest.chi_c)


@dataclass(frozen=True)
class PreviewMetadata:
    """预览元数据（判据四：策略与窗口写入元数据）。

    返修 F01：滞后语义为查询 t − lag_s——正滞后（lag>0）读过去历史，
    负滞后（超前，lag<0）用具名预测；prediction_method 登记预测方法。
    返修 F02：dfreq_assumption 登记窗口差频假设（保持上次采用值或显
    式零值）。
    """
    policy: str                     # 固定策略声明文本
    prediction_method: str          # 负滞后（超前）具名预测方法
    window_steps: int
    current_step: int
    adopted_frequency_hz: float     # 未产生闭环需求的保持采用值
    dfreq_assumption: str = ""      # 窗口差频假设（F02：区分无输入与零值）
    providers: Tuple[str, ...] = ()  # 入链有状态提供者名（判据六补证）
    preview: bool = True            # 预览标记（判据六：永不冒充真实回执）


@dataclass(frozen=True)
class PreviewSample:
    """预览窗口内单步读数（副本产物，全部带 preview 标记）。

    判据四补证字段：frequency_hz 为该步副本实际采用的频率（含参数程
    序改写）；params_applied 为本步副本执行的已排定参数程序条目。
    判据六补证字段：providers_stepped 为本步被调用的有状态提供者名。
    """
    preview: bool
    step: int
    t_s: float
    chi_c: float
    epsilon: float
    chi_L: float
    chi_R: float
    shape_phase: float
    events_executed: Tuple[str, ...]     # 副本内执行的事件种类序列
    frequency_hz: float = 0.0            # 本步副本采用频率（程序可改写）
    params_applied: Tuple[str, ...] = ()  # 本步执行的参数程序条目
    providers_stepped: Tuple[str, ...] = ()  # 本步调用的提供者名


#: 有状态提供者的预览步进钩子名（判据六补证：可选实现）
PREVIEW_PROVIDER_HOOK = "on_preview_step"


class PreviewEngine:
    """窗口预览引擎（判据四与判据六）。

    只接收副本：构造时克隆节律服务（含 Hopf 后端可选），窗口按时间顺
    序在副本上推进；已排定事件与参数程序在副本内按步执行（判据四补
    证：参数程序经本生产入口改写副本采用值与样本）；未产生的闭环需
    求保持当前采用值（频率取 adopted_frequency_hz 播种，不发明未来反
    馈）。负滞后走真实历史（HistoryBuffer），正滞后走具名预测（副本
    匀速推进）；不把当前状态向过去积分冒充历史。预览产物全部带
    preview 标记，不产生真实回执、不提交真实事件、不写原生对象。

    判据六补证（有状态提供者承接）：providers 为边界明确的有状态对
    象序列（基元、可变数组及其缓存的持有者），每个必须实现 clone()
    返回深隔离副本（内部数组与缓存一并复制，不留原对象别名）；可选
    实现 on_preview_step(sample) 钩子在窗口每步接收副本样本。窗口内
    只操作副本：执行正常、窗口结束与执行中异常后，原件状态与其内部
    别名（含外部共享数组）都不被改动；异常包装为带提供者定位的服务
    错误抛出。
    """

    def __init__(self, service: RhythmService,
                 history: HistoryBuffer,
                 adopted_frequency_hz: float,
                 providers: Optional[tuple] = None):
        self._service = service          # 原件只读：仅用于克隆与读当前值
        self._history = history          # 原件历史只读查询
        if not (math.isfinite(adopted_frequency_hz)
                and adopted_frequency_hz > 0.0):
            raise RhythmServiceError("预览采用频率必须为正有限数，实际 %r"
                                     % (adopted_frequency_hz,), where="预览")
        self._adopted_f = float(adopted_frequency_hz)
        self._providers: Tuple = tuple(providers or ())
        for prov in self._providers:
            if not callable(getattr(prov, "clone", None)):
                raise RhythmServiceError(
                    "有状态提供者 %r 未实现 clone()，不能进入预览链（边"
                    "界要求：深隔离副本，内部数组与缓存不留原对象别名）"
                    % (type(prov).__name__,), where="预览构造")

    def window(self, steps: int, dfreq_hz: Optional[float] = None
               ) -> Tuple[List[PreviewSample], PreviewMetadata]:
        """按时间顺序生成预览窗口（副本推进；真实状态零改动）。

        返修 F02：dfreq_hz 为 None（没有新差频输入）时保持上次采用差
        频——未产生的闭环需求保持当前采用值；显式传入 0.0（明确输入
        零值）则按零推进。两者都在元数据 dfreq_assumption 中登记。
        判据四补证：副本每步以 advance(None, dfreq) 推进——无新频率闭
        环需求时保持当前采用值，已排定参数程序在副本步首改写采用值并
        反映在样本 frequency_hz 与 params_applied。
        """
        if steps < 0:
            raise RhythmServiceError("预览步数必须非负，实际 %r" % (steps,),
                                     where="预览")
        if dfreq_hz is None:
            dfreq = None
            dfreq_note = ("没有新差频输入：保持上次采用差频 %+.6f Hz（固"
                          "定策略的保持当前采用值；窗口内参数程序可改写）"
                          % (self._service.read().dfreq_hz,))
        else:
            if not math.isfinite(dfreq_hz):
                raise RhythmServiceError("预览差频必须为有限数，实际 %r"
                                         % (dfreq_hz,), where="预览")
            dfreq = float(dfreq_hz)
            dfreq_note = "显式差频输入 %+.6f Hz（明确零值按零推进）" % (dfreq,)
        twin = self._service.clone()
        twin.adopt(self._adopted_f)       # 播种副本当前采用频率
        prov_clones = []
        for prov in self._providers:
            prov_clones.append((type(prov).__name__, prov.clone()))
        start_step = twin.read().step
        out: List[PreviewSample] = []
        for _ in range(steps):
            sample = twin.advance(None, dfreq)
            executed = tuple(
                e.kind for e in twin._consumed_events
                if e.at_step == sample.step)
            stepped: List[str] = []
            for name, clone in prov_clones:
                hook = getattr(clone, PREVIEW_PROVIDER_HOOK, None)
                if hook is None:
                    continue
                try:
                    hook(sample)
                except Exception as exc:
                    raise RhythmServiceError(
                        "预览执行中有状态提供者 %r 在步 %d 异常，预览中止"
                        "（只操作副本，原件与其内部别名未改动）：%r"
                        % (name, sample.step, exc),
                        where="预览窗口") from exc
                stepped.append(name)
            out.append(PreviewSample(
                preview=True, step=sample.step, t_s=sample.t_s,
                chi_c=sample.chi_c, epsilon=sample.epsilon,
                chi_L=sample.chi_L, chi_R=sample.chi_R,
                shape_phase=sample.shape_phase,
                events_executed=executed,
                frequency_hz=sample.frequency_hz,
                params_applied=tuple(twin._last_applied),
                providers_stepped=tuple(stepped)))
        meta = PreviewMetadata(
            policy=("副本执行已排定事件和参数程序（程序在副本步首改写"
                    "采用值并反映在样本）；未产生的闭环需求保持当前采用"
                    "值（含差频无新输入时保持上次采用值）；窗口按时间顺"
                    "序从同一副本状态推进；滞后语义为查询 t 减 lag：正滞"
                    "后读历史，负滞后用具名预测"),
            prediction_method=PREVIEW_PREDICTION_METHOD,
            window_steps=steps, current_step=start_step,
            adopted_frequency_hz=self._adopted_f,
            dfreq_assumption=dfreq_note,
            providers=tuple(type(p).__name__ for p in self._providers))
        return out, meta

    def lagged_shape_phase(self, lag_s: float, dt: float
                           ) -> Dict[str, object]:
        """按已批准语义查询 t − lag_s 的形状相位（返修 F01，v4 修超前）。

        正滞后（lag > 0，滞后）查询过去：只读真实历史，历史不足显式
        uninitialized，不向过去积分、不以当前频率回推冒充历史。负滞后
        （lag < 0，超前）作有名预测：一次性副本按与窗口相同的规则推进
        （v4 修复）——先播种当前采用频率，每步 advance(None, None)，
        已排定参数程序在副本步首执行、无新闭环需求保持当前采用值，
        不把旧采用频率伪装成每步新需求覆盖程序；与 window 出口同一程
        序执行与保持规则，同工况同目标时刻两出口结果一致。零值返回当
        前样本。返回 {status, value, method, preview, reason}。
        """
        current = self._service.read()
        target_t = current.t_s - lag_s
        if lag_s > 0.0:
            q = self._history.query_chi_c(target_t)
            if q.status != "present":
                return {"status": q.status, "value": None,
                        "method": "history", "preview": True,
                        "reason": q.reason,
                        "query_time": target_t}
            chi = q.interpolated
            from modules.aero.kinematics import split_phase_map
            ph, _d = split_phase_map(chi, self._service.split)
            return {"status": "present", "value": float(ph),
                    "method": "history", "preview": True,
                    "query_time": target_t}
        if lag_s < 0.0:
            steps = int(round(-lag_s / dt)) if dt > 0 else 0
            twin = self._service.clone()
            twin.adopt(self._adopted_f)
            val = current.shape_phase
            for _ in range(steps):
                val = twin.advance(None, None).shape_phase
            return {"status": "present", "value": float(val),
                    "method": PREVIEW_PREDICTION_METHOD, "preview": True,
                    "query_time": target_t}
        return {"status": "present", "value": float(current.shape_phase),
                "method": "current_sample", "preview": True,
                "query_time": target_t}


class ServiceClock:
    """规范时钟（判据三：规范时间由服务提供）。

    时间为已提交步数乘步长，单调不回退；真实推进只能经
    RhythmService.advance（单一提交入口），本类只做登记与一致性核对。
    """

    def __init__(self, dt: float, service: RhythmService):
        self.dt = float(dt)
        self._service = service

    def canonical_time(self) -> float:
        return self._service.read().t_s

    def check_monotonic(self, previous: float) -> bool:
        return self.canonical_time() >= previous
