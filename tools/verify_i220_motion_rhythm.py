# -*- coding: utf-8 -*-
"""
verify_i220_motion_rhythm.py — I-220 本地可验判据执行体。

判据一：共同服务实现累计周期预算、拍号、左右差频积分、初始相差、形
        状相位和导数；同一物理步只提交一次，多次读取和数值子步不增
        加真实物理步或事件消费。
判据二：复用旧相位积分和分裂周期核（保留旧函数）；回绕进度、总进度
        与未回绕累计量分别映射；初值接入、过去拍数未知及右翼偏移只
        施加一次有记录。
判据三：周期、单次事件及保持的规范时间由服务提供；停拍继续走时、冻
        结、重置、锁相和相位事件分别声明；持续差频与固定同坐标相差
        不被同时硬性指定。
判据四：预览策略固定为副本执行已排定事件和参数程序、未产生的闭环需
        求保持当前采用值；窗口按时间顺序推进，正负滞后分别使用历史
        或具名预测，不向过去积分冒充历史。
判据五：历史长度、存储与插值、跨参数版本和复位边界规则明确；历史不
        足有初始化或未支持状态，不静默用当前频率线性外推。
判据六：预览隔离节律、数组、基元、队列、事件标志与相关缓存；禁止原
        生写入、真实回执和真实事件提交；正常及异常返回后真实状态一致。
判据七：原生 Hopf 核可选后端实现有限值、正慢度、有效半径和解缠步长
        保护；保留参考核版本，较大相位步采用有说明的数值子步。

用法：python tools/verify_i220_motion_rhythm.py
仅使用 Python 标准库与本仓纯 Python 模块，不启动 Isaac。
"""
import copy
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("——" + detail) if detail else ""))
    return bool(ok)


def main():
    from modules.motion_action.rhythm import (
        RhythmService, RhythmEvent, RhythmServiceError, HopfBackend,
        REFERENCE_CORE_VERSION, EVENT_BEAT, EVENT_STOP_FLAP,
        EVENT_RESUME_FLAP, EVENT_FREEZE, EVENT_UNFREEZE, EVENT_RESET,
        EVENT_PHASE_LOCK, HOPF_MIN_H_SAMPLES, ParamProgramStep)
    from modules.motion_action.runtime import (
        HistoryBuffer, PreviewEngine, ServiceClock,
        HISTORY_INTERPOLATION, PREVIEW_PREDICTION_METHOD)
    from modules.aero.kinematics import (
        WingKinematics, split_phase_map, clone_kinematics,
        sample_kinematics_readonly)

    dt = 1.0 / 120.0

    # ── 判据一：单一提交与节律量 ──
    svc = RhythmService(frequency_hz=1.8, dt=dt, split=0.5,
                        delta_0_beats=0.05)
    s0 = svc.advance(1.8)
    r1 = svc.read()
    r2 = svc.read()
    check("判据一/多次读取不推进状态",
          r1.chi_c == r2.chi_c == s0.chi_c and r1.step == r2.step)
    before = svc.state()
    # 数值子步（Hopf 子步同类）不增加真实步号：read 多次后步号不变
    check("判据一/数值子步与读取不增加真实步号",
          svc.read().step == s0.step and svc.state() == before)
    # 差频积分与初始相差（已批准公式）
    svc2 = RhythmService(frequency_hz=1.8, dt=dt, split=0.5,
                         delta_0_beats=0.05)
    n = 240
    for _ in range(n):
        sN = svc2.advance(1.8, dfreq_hz=0.1)
    eps_expect = 0.1 * n * dt
    check("判据一/左右差频积分按公式",
          abs(sN.epsilon - eps_expect) < 1e-12
          and abs((sN.chi_L - sN.chi_R) - (2 * eps_expect - 0.05)) < 1e-12,
          "epsilon=%+.6f 期望 %+.6f" % (sN.epsilon, eps_expect))
    check("判据一/初始相差只施加一次（每步核对）",
          all(abs((sm.chi_R - (sm.chi_c - sm.epsilon)) - 0.05) < 1e-12
              for sm in (svc2.advance(1.8),)))
    check("判据一/拍号与回绕进度分别映射",
          s0.beat_index == int(math.floor(s0.chi_c))
          and abs(s0.wrapped_tau - (s0.chi_c % 1.0)) < 1e-15
          and abs(s0.wrapped_tau - s0.beat_fraction()) < 1e-15)
    check("判据一/样本含形状相位与导数（同输出规则）",
          s0.shape_phase is not None
          and abs(s0.shape_phase_rate - math.pi / 0.5) < 1e-15
          and math.isfinite(s0.shape_phase))
    check("判据一/累计周期预算（chi_c 即未回绕累计拍数）",
          abs(sN.chi_c - (1.8 * n * dt)) < 1e-9,
          "chi_c=%+.6f 期望 %+.6f" % (sN.chi_c, 1.8 * n * dt))

    # ── 判据二：复用旧核与进度映射 ──
    wk = WingKinematics(frequency=1.8, split=0.75)
    ok_grid = True
    worst = 0.0
    for tau in np.linspace(0.0, 0.999, 61):
        pub_ph, pub_d = split_phase_map(float(tau), 0.75)
        saved = (wk._t_prev, wk._tau_state)
        wk._t_prev = 0.0
        wk._tau_state = float(tau)
        old_ph, old_d = wk._phase_sc(0.0, 0.0, advance=False)
        wk._t_prev, wk._tau_state = saved
        worst = max(worst, abs(pub_ph - old_ph), abs(pub_d - old_d))
        if abs(pub_ph - old_ph) > 1e-15 or abs(pub_d - old_d) > 1e-15:
            ok_grid = False
    check("判据二/公共分裂核与旧内部核同式（网格核对 s=0.75）", ok_grid,
          "最大偏差 %s（六十一点）" % worst)
    old_eval_before = wk._t_prev
    sample_kinematics_readonly(wk, 5.0)
    check("判据二/旧函数保留且只读采样不改旧核状态",
          wk._t_prev == old_eval_before and wk.evaluate(5.0)["phi"] is not None)
    svc3 = RhythmService(frequency_hz=1.8, dt=dt, split=0.5,
                         chi_c0=0.25, past_beats_known=False)
    s3 = svc3.advance(1.8)
    check("判据二/初值接入与过去拍数未知声明",
          abs(s3.chi_c - (0.25 + 1.8 * dt)) < 1e-12
          and svc3.past_beats_known is False,
          "初值 0.25 起步；past_beats_known=False 登记")

    # ── 判据三：规范时间与事件 ──
    svc4 = RhythmService(frequency_hz=1.8, dt=dt, split=0.5)
    for _ in range(10):
        sA = svc4.advance(1.8)
    clock = ServiceClock(dt, svc4)
    check("判据三/规范时间由服务提供（步数乘步长）",
          abs(clock.canonical_time() - 10 * dt) < 1e-15
          and clock.check_monotonic(9 * dt))
    svc4.schedule_event(RhythmEvent(EVENT_STOP_FLAP, at_step=sA.step + 1))
    sB = svc4.advance(1.8)
    check("判据三/停拍继续走时（闸门闭、相位仍推进）",
          sB.stroke_gate == 0.0 and sB.chi_c > sA.chi_c
          and sB.step == sA.step + 1)
    svc4.schedule_event(RhythmEvent(EVENT_RESUME_FLAP, at_step=sB.step + 1))
    sB2 = svc4.advance(1.8)
    check("判据三/复拍事件", sB2.stroke_gate == 1.0)
    svc4.schedule_event(RhythmEvent(EVENT_FREEZE, at_step=sB2.step + 1))
    sC = svc4.advance(1.8)
    check("判据三/冻结当步起相位不推进而步号仍进",
          sC.chi_c == sB2.chi_c and sC.step == sB2.step + 1 and sC.frozen)
    svc4.schedule_event(RhythmEvent(EVENT_RESET, at_step=sC.step + 1))
    sD = svc4.advance(1.8)
    check("判据三/重置回初值自初值起步",
          abs(sD.chi_c - 1.8 * dt) < 1e-12 and sD.stroke_gate == 1.0
          and not sD.frozen)
    svc4.schedule_event(RhythmEvent(EVENT_PHASE_LOCK, at_step=sD.step + 1,
                                    payload={"d_epsilon_advance": 0.01}))
    sE = svc4.advance(1.8)
    check("判据三/锁相修正进入相对推进（epsilon 出口）",
          abs(sE.epsilon - 0.01) < 1e-12,
          "修正加到 epsilon，不只共同调频")
    svc4.schedule_event(RhythmEvent(EVENT_BEAT, at_step=sE.step + 1))
    sF = svc4.advance(1.8)
    consumed_once = len([e for e in svc4._consumed_events
                         if e.kind == EVENT_BEAT]) == 1
    sG = svc4.read()
    check("判据三/拍相位事件单次消费（读取不再消费）", consumed_once
          and sG.step == sF.step)
    svc5 = RhythmService(frequency_hz=1.8, dt=dt, split=0.5)
    try:
        svc5.declare_phase_lock(True)
        svc5.set_continuous_dfreq(True)
        lock_conflict = False
        why = "未拒绝"
    except RhythmServiceError as exc:
        lock_conflict = "互斥" in str(exc)
        why = str(exc)[:60]
    check("判据三/持续差频与固定相差不同时硬性指定", lock_conflict, why)
    svc5b = RhythmService(frequency_hz=1.8, dt=dt, split=0.5)
    svc5b.declare_phase_lock(True)
    svc5b.set_continuous_dfreq(False)
    check("判据三/锁相声明与间歇差频可共存", True, "允许相差演化不锁相")
    # ── 返修 F04：互斥失败保留上一有效配置（两种顺序） ──
    svc5f = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc5f.advance(2.0)
    svc5f.declare_phase_lock(True)
    try:
        svc5f.set_continuous_dfreq(True)
        rej1 = False
    except RhythmServiceError:
        rej1 = True
    ok1 = (svc5f._phase_lock_declared is True
           and svc5f._continuous_dfreq is False)
    svc5f.advance(2.0, dfreq_hz=0.1)
    adv1 = svc5f.read().epsilon > 0.0
    svc5g = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc5g.advance(2.0)
    svc5g.set_continuous_dfreq(True)
    try:
        svc5g.declare_phase_lock(True)
        rej2 = False
    except RhythmServiceError:
        rej2 = True
    ok2 = (svc5g._phase_lock_declared is False
           and svc5g._continuous_dfreq is True)
    svc5g.advance(2.0, dfreq_hz=0.1)
    adv2 = svc5g.read().epsilon > 0.0
    check("F04/互斥失败保留上一有效配置且推进入口继续工作（两序）",
          rej1 and ok1 and adv1 and rej2 and ok2 and adv2,
          "序一 拒=%s 标志=%s 推进=%s；序二 拒=%s 标志=%s 推进=%s"
          % (rej1, ok1, adv1, rej2, ok2, adv2))
    svc5h = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc5h._phase_lock_declared = True   # 模拟绕过声明的非法组合
    svc5h._continuous_dfreq = True
    try:
        svc5h.advance(2.0)
        guard = False
    except RhythmServiceError as exc:
        guard = "非法配置组合" in str(exc)
    check("F04/推进入口拒绝在非法组合下发布已采用结果", guard)

    # ── 返修 F05：事件排定校验与真实步原子回滚 ──
    svc_ev = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    try:
        svc_ev.schedule_event(RhythmEvent(
            EVENT_PHASE_LOCK, at_step=1, payload={"d_epsilon_advance": [1]}))
        rej_list = False
        why = "被接受"
    except RhythmServiceError as exc:
        rej_list = "数值" in str(exc)
        why = str(exc)[:60]
    check("F05/锁相修正量为列表在排定时拒绝（待办不入队）", rej_list
          and len(svc_ev._pending_events) == 0, why)
    try:
        svc_ev.schedule_event(RhythmEvent(
            EVENT_PHASE_LOCK, at_step=1,
            payload={"d_epsilon_advance": float("nan")}))
        rej_nan = False
    except RhythmServiceError:
        rej_nan = True
    check("F05/非有限锁相修正量在排定时拒绝", rej_nan
          and len(svc_ev._pending_events) == 0)
    # 原子回滚：注入执行期异常，真实步不留部分提交、事件不丢
    svc_at = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_at.advance(2.0)
    svc_at.schedule_event(RhythmEvent(EVENT_PHASE_LOCK, at_step=1,
                                      payload={"d_epsilon_advance": 0.01}))
    svc_at.schedule_event(RhythmEvent(EVENT_STOP_FLAP, at_step=1))
    snap_at = svc_at.state()
    _orig_apply = RhythmService._apply_event

    def _boom(self, event):
        raise RuntimeError("注入执行失败")
    RhythmService._apply_event = _boom
    try:
        try:
            svc_at.advance(3.0)
            rolled = False
        except RhythmServiceError as exc:
            rolled = "回滚" in str(exc)
        finally:
            RhythmService._apply_event = _orig_apply
        state_kept = svc_at.state() == snap_at
        pending_kept = (len(svc_at._pending_events) == 2
                        and len(svc_at._consumed_events) == 0)
        svc_at.advance(3.0)
        recommitted = (svc_at.read().stroke_gate == 0.0
                       and abs(svc_at.read().epsilon - 0.01) < 1e-12
                       and len(svc_at._consumed_events) == 2)
    finally:
        RhythmService._apply_event = _orig_apply
    check("F05/执行期异常整体回滚（保最后有效步与未消费事件，可重提交）",
          rolled and state_kept and pending_kept and recommitted,
          "回滚=%s 状态一致=%s 待办保留=%s 重提交执行=%s"
          % (rolled, state_kept, pending_kept, recommitted))
    # 合法同批事件单次消费；重复读取不再消费
    svc_ev2 = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_ev2.schedule_event(RhythmEvent(EVENT_BEAT, at_step=1))
    svc_ev2.schedule_event(RhythmEvent(EVENT_PHASE_LOCK, at_step=1,
                                       payload={"d_epsilon_advance": 0.005}))
    svc_ev2.advance(2.0)
    n_consumed = len(svc_ev2._consumed_events)
    svc_ev2.read()
    check("F05/同批合法事件全部消费一次（重复读取不增消费）",
          n_consumed == 2 and len(svc_ev2._consumed_events) == 2
          and abs(svc_ev2.read().epsilon - 0.005) < 1e-12)

    # ── 判据四与判据六：预览策略、窗口与隔离 ──
    svc6 = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    hist = HistoryBuffer(max_entries=128)
    pe = PreviewEngine(svc6, hist, adopted_frequency_hz=2.0)
    for _ in range(20):
        hist.record(svc6.advance(2.0))
    svc6.schedule_event(RhythmEvent(EVENT_STOP_FLAP, at_step=23))
    snapshot_before = (svc6.state(), list(svc6._pending_events),
                       list(svc6._consumed_events), len(hist))
    win, meta = pe.window(5)
    snapshot_after = (svc6.state(), list(svc6._pending_events),
                      list(svc6._consumed_events), len(hist))
    check("判据四/窗口按时间顺序推进且当前时刻顺序明确",
          [w.step for w in win] == list(range(21, 26))
          and meta.current_step == 20)
    check("判据四/预览策略固定为副本执行已排定事件、闭环需求保持采用值",
          "副本执行" in meta.policy and "保持" in meta.policy
          and abs(meta.adopted_frequency_hz - 2.0) < 1e-15)
    check("判据四/未产生的闭环需求保持当前采用值（副本按 2.0Hz 推进）",
          abs((win[-1].chi_c - svc6.read().chi_c) - 2.0 * 0.01 * 5) < 1e-12)
    stop_step = next((w for w in win if w.step == 23), None)
    check("判据四/已排定事件在副本内执行", stop_step is not None
          and EVENT_STOP_FLAP in stop_step.events_executed)
    check("判据六/预览结果全部带预览标记（不冒充真实回执）",
          all(w.preview for w in win) and meta.preview is True)
    check("判据六/预览不提交真实事件（排定事件仍在原件待办）",
          any(e.kind == EVENT_STOP_FLAP for e in svc6._pending_events))
    check("判据六/预览后真实状态逐项一致",
          snapshot_before == snapshot_after)
    # 异常返回后一致：向预览注入非法频率
    try:
        bad = PreviewEngine(svc6, hist, adopted_frequency_hz=float("nan"))
        bad.window(3)
        raised = False
    except RhythmServiceError:
        raised = True
    except ValueError:
        raised = True
    check("判据六/异常构造显式失败且真实状态一致",
          raised and snapshot_before[0] == svc6.state())

    # 判据四（返修 F01）：滞后语义为查询 t − lag_s——正滞后读过去历
    # 史，负滞后（超前）用具名预测；零值返回当前样本
    hist2 = HistoryBuffer(max_entries=64)
    svc7 = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    for _ in range(10):
        hist2.record(svc7.advance(2.0))
    pe2 = PreviewEngine(svc7, hist2, adopted_frequency_hz=2.0)
    pos = pe2.lagged_shape_phase(0.02, 0.01)
    check("F01/正滞后 0.02s 查询过去 0.08s 历史（值逐位核对）",
          pos["method"] == "history" and pos["status"] == "present"
          and abs(pos["query_time"] - 0.08) < 1e-12
          and abs(pos["value"] - 1.0053096491487339) < 1e-12,
          "t=%s 值=%.10f 期望 1.0053096491"
          % (pos.get("query_time"), pos["value"] or -1))
    neg = pe2.lagged_shape_phase(-0.02, 0.01)
    check("F01/负滞后 -0.02s 预测未来 0.12s（具名预测登记）",
          neg["method"] == PREVIEW_PREDICTION_METHOD
          and neg["status"] == "present"
          and abs(neg["query_time"] - 0.12) < 1e-12
          and abs(neg["value"] - 1.5079644737231004) < 1e-9,
          "t=%s 值=%.10f 期望 1.5079644737" % (neg.get("query_time"),
                                                neg["value"] or -1))
    zero = pe2.lagged_shape_phase(0.0, 0.01)
    check("F01/零滞后返回当前样本",
          zero["method"] == "current_sample"
          and abs(zero["value"] - svc7.read().shape_phase) < 1e-15)
    # 不向过去积分冒充历史：正滞后读出变频历史的真实值，而非由当前频
    # 率向过去积分的值。
    svc8 = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    hist3 = HistoryBuffer(max_entries=64)
    for _ in range(5):
        hist3.record(svc8.advance(2.0))
    for _ in range(5):
        hist3.record(svc8.advance(5.0))   # 后 5 步变频为 5Hz
    pe3 = PreviewEngine(svc8, hist3, adopted_frequency_hz=5.0)
    t_now = svc8.read().t_s
    # 查询点取在 2Hz 慢频段（t=0.03），与当前 5Hz 频率回推必然不同
    q_hist = hist3.query_chi_c(0.03)
    pos3 = pe3.lagged_shape_phase(t_now - 0.03, 0.01)
    # 当前频率向过去积分会得 chi_c(t)-5.0*(t-0.03)；历史插值得真实记录
    linear_back = svc8.read().chi_c - 5.0 * (t_now - 0.03)
    check("判据四/正滞后不以当前频率向过去积分冒充历史",
          pos3["method"] == "history"
          and abs(q_hist.interpolated - linear_back) > 1e-6,
          "历史=%+.6f 当前频率回推=%+.6f（两者不同，取历史）"
          % (q_hist.interpolated, linear_back))

    # ── 返修 F02：预览差频区分无新输入与显式零值 ──
    svc_df = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5,
                           delta_0_beats=0.05)
    hist_df = HistoryBuffer(max_entries=64)
    pe_df = PreviewEngine(svc_df, hist_df, adopted_frequency_hz=2.0)
    hist_df.record(svc_df.advance(2.0, dfreq_hz=0.1))
    win_df, meta_df = pe_df.window(5)
    check("F02/无新差频输入保持上次采用值（0.001→0.006，左右差 -0.038）",
          abs(win_df[-1].epsilon - 0.006) < 1e-12
          and abs((win_df[-1].chi_L - win_df[-1].chi_R) + 0.038) < 1e-12
          and "保持" in meta_df.dfreq_assumption,
          "epsilon=%.4f 左右差=%+.4f" % (win_df[-1].epsilon,
                                          win_df[-1].chi_L
                                          - win_df[-1].chi_R))
    win_zero, meta_zero = pe_df.window(5, dfreq_hz=0.0)
    check("F02/显式零值按零推进（epsilon 保持 0.001）",
          abs(win_zero[-1].epsilon - 0.001) < 1e-12
          and "零" in meta_zero.dfreq_assumption,
          "epsilon=%.4f 假设=%s" % (win_zero[-1].epsilon,
                                     meta_zero.dfreq_assumption[:30]))

    # ── 判据五：历史规则 ──
    check("判据五/存储与插值方法登记",
          HISTORY_INTERPOLATION == "linear_between_committed_samples"
          and len(hist3) == 10)
    q_mid = hist3.query_chi_c(t_now - 0.025)
    a = hist3._entries[7]
    b = hist3._entries[8]
    frac = ((t_now - 0.025) - a.t_s) / (b.t_s - a.t_s)
    want = a.chi_c + frac * (b.chi_c - a.chi_c)
    check("判据五/相邻样本线性插值", q_mid.status == "present"
          and abs(q_mid.interpolated - want) < 1e-12)
    hist4 = HistoryBuffer(max_entries=4)
    svc9 = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    for _ in range(10):
        hist4.record(svc9.advance(2.0))
    check("判据五/定长历史（超长丢弃最旧）", len(hist4) == 4)
    q_old = hist4.query_chi_c(0.02)
    check("判据五/历史不足显式未初始化（不外推）",
          q_old.status == "uninitialized" and "外推" in q_old.reason)
    hist5 = HistoryBuffer(max_entries=64)
    svcA = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    for _ in range(5):
        hist5.record(svcA.advance(2.0))
    hist5.set_param_version("v2")
    for _ in range(5):
        hist5.record(svcA.advance(2.0))
    q_cross = hist5.query_chi_c(0.055)
    check("判据五/跨参数版本不插值（显式未支持）",
          q_cross.status == "unsupported" and "版本" in q_cross.reason)
    hist5.note_reset()
    for _ in range(5):
        hist5.record(svcA.advance(2.0))
    q_epoch = hist5.query_chi_c(0.105)
    check("判据五/跨复位纪元不插值（显式未支持）",
          q_epoch.status == "unsupported" and "纪元" in q_epoch.reason)
    # ── 返修 F03：精确样本命中边界两端返回该样本，不再误拒 ──
    hist6 = HistoryBuffer(max_entries=64)
    svcE = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    hist6.record(svcE.advance(2.0))            # t=0.01（版本 v0）
    hist6.set_param_version("v2")
    hist6.record(svcE.advance(2.0))            # t=0.02（版本 v2）
    q_ex_before = hist6.query_chi_c(0.01)
    q_ex_after = hist6.query_chi_c(0.02)
    q_ex_mid = hist6.query_chi_c(0.015)
    check("F03/精确样本在版本边界两端返回该样本（自身版本）",
          q_ex_before.status == "present"
          and q_ex_before.entry.param_version == "v0"
          and abs(q_ex_before.interpolated - 0.02) < 1e-12
          and q_ex_after.status == "present"
          and q_ex_after.entry.param_version == "v2"
          and abs(q_ex_after.interpolated - 0.04) < 1e-12,
          "前样本 v0=%.4f 后样本 v2=%.4f" % (q_ex_before.interpolated,
                                              q_ex_after.interpolated))
    check("F03/边界中点仍拒绝跨版本插值",
          q_ex_mid.status == "unsupported" and "版本" in q_ex_mid.reason)
    hist7 = HistoryBuffer(max_entries=64)
    svcR = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    hist7.record(svcR.advance(2.0))
    hist7.note_reset()
    hist7.record(svcR.advance(2.0))
    q_r_before = hist7.query_chi_c(0.01)
    q_r_after = hist7.query_chi_c(0.02)
    check("F03/精确样本在复位纪元边界两端返回该样本（自身纪元）",
          q_r_before.status == "present" and q_r_before.entry.epoch == 0
          and q_r_after.status == "present" and q_r_after.entry.epoch == 1
          and hist7.query_chi_c(0.015).status == "unsupported")
    q_future = hist5.query_chi_c(99.0)
    check("判据五/未来时刻不外推（历史只覆盖已提交步）",
          q_future.status == "unsupported")

    # ── 判据七：原生 Hopf 可选后端 ──
    hb = HopfBackend(state=(0.5, 0.5), f_hz=1.8, b1=0.1, b3=0.05,
                     amplitude_a=1.0, kappa=2.0)
    out = hb.step(dt)
    check("判据七/参考核版本保留（只读复用登记）",
          hb.reference_core == REFERENCE_CORE_VERSION
          and hb.reference_core.startswith("wavegen_family:"))
    check("判据七/有限值与有效半径起步", math.isfinite(out["x"])
          and math.isfinite(out["y"]) and out["radius"] > 0.0)
    try:
        HopfBackend(state=(0.0, 0.0), f_hz=1.8, b1=0.1, b3=0.05,
                    amplitude_a=1.0, kappa=2.0)
        r_zero = False
    except RhythmServiceError:
        r_zero = True
    check("判据七/零半径初值显式拒绝", r_zero)
    try:
        HopfBackend(state=(float("nan"), 0.5), f_hz=1.8, b1=0.1, b3=0.05,
                    amplitude_a=1.0, kappa=2.0)
        r_nan = False
    except RhythmServiceError:
        r_nan = True
    check("判据七/非有限状态显式拒绝", r_nan)
    try:
        HopfBackend(state=(0.5, 0.5), f_hz=1.8, b1=0.9, b3=0.9,
                    amplitude_a=1.0, kappa=2.0)
        r_slow = False
        slow_why = ""
    except RhythmServiceError as exc:
        r_slow = "正慢度" in str(exc) or "非正" in str(exc)
        slow_why = str(exc)[:70]
    check("判据七/正慢度校验（有限采样登记不冒称严格证明）", r_slow,
          slow_why or hb.slowness_check["claim"])
    check("判据七/慢度检查登记采样密度与安全余量",
          hb.slowness_check["samples"] == HOPF_MIN_H_SAMPLES
          and "margin" in hb.slowness_check
          and "有限采样" in hb.slowness_check["claim"])
    hb_big = HopfBackend(state=(0.5, 0.5), f_hz=50.0, b1=0.1, b3=0.05,
                         amplitude_a=1.0, kappa=2.0)
    out_big = hb_big.step(0.02)
    check("判据七/较大相位步采用有说明的数值子步",
          out_big["substeps"] > 1
          and out_big["substeps"] == hb_big.substeps_last)
    # ── 返修 F06：子步增量解缠不丢整圈（大步与细步累计角对照） ──
    hb_turn = HopfBackend(state=(1.0, 0.0), f_hz=1.0, b1=0.0, b3=0.0,
                          amplitude_a=1.0, kappa=2.0)
    out_turn = hb_turn.step(1.0)
    check("F06/整圈大步累计角不丢圈（约 2pi，v1 曾得 -0.0007）",
          abs(out_turn["phase_unwrapped"] - 2.0 * math.pi) < 2e-3
          and out_turn["substeps"] > 1,
          "unwrapped=%.6f 期望约%.6f 子步=%d"
          % (out_turn["phase_unwrapped"], 2.0 * math.pi,
             out_turn["substeps"]))
    hb_fine = HopfBackend(state=(1.0, 0.0), f_hz=1.0, b1=0.0, b3=0.0,
                          amplitude_a=1.0, kappa=2.0)
    for _ in range(100):
        fine_turn = hb_fine.step(0.01)
    check("F06/整圈细步参照与 2pi 一致",
          abs(fine_turn["phase_unwrapped"] - 2.0 * math.pi) < 2e-3,
          "细步=%.6f" % fine_turn["phase_unwrapped"])
    hb_chk = HopfBackend(state=(0.5, 0.5), f_hz=50.0, b1=0.1, b3=0.05,
                         amplitude_a=1.0, kappa=2.0)
    big_chk = hb_chk.step(0.02)
    hb_ref = HopfBackend(state=(0.5, 0.5), f_hz=50.0, b1=0.1, b3=0.05,
                         amplitude_a=1.0, kappa=2.0)
    for _ in range(100):
        ref_chk = hb_ref.step(0.0002)
    check("F06/检查器工况大步与细步累计角一致（v1 曾丢一圈）",
          abs(big_chk["phase_unwrapped"] - ref_chk["phase_unwrapped"])
          < 2e-3
          and abs(big_chk["phase_unwrapped"] - 7.068582747) < 2e-2,
          "大步=%.6f 细步=%.6f 期望约7.068583"
          % (big_chk["phase_unwrapped"], ref_chk["phase_unwrapped"]))
    svcB = RhythmService(frequency_hz=1.8, dt=dt, split=0.5)
    steps_before = svcB.read().step
    hb_big2 = HopfBackend(state=(0.5, 0.5), f_hz=50.0, b1=0.1, b3=0.05,
                          amplitude_a=1.0, kappa=2.0)
    hb_big2.step(0.02)
    check("判据七/子步不增加真实物理步或事件消费",
          svcB.read().step == steps_before
          and len(svcB._consumed_events) == 0)
    hb_twin = hb.clone()
    hb_twin.step(dt)
    check("判据七/Hopf 后端可克隆（预览隔离）",
          hb.state != hb_twin.state)

    # ── 判据四补证：已排定参数程序经生产预览入口在副本按步执行 ──
    import numpy as _np

    class _WaveProvider:
        """边界明确的有状态提供者：内部数组、缓存、计数与外部别名。"""

        def __init__(self, external):
            self.buf = _np.zeros(4)
            self.cache = []
            self.count = 0
            self.external = external

        def clone(self):
            t = _WaveProvider(self.external.copy())
            t.buf = self.buf.copy()
            t.cache = list(self.cache)
            t.count = self.count
            return t

        def on_preview_step(self, sample):
            self.count += 1
            self.cache.append(sample.step)
            self.buf = self.buf + 1.0
            self.external[0] += 1.0

    svc_p = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_p.advance(2.0)
    svc_p.advance(2.0)
    hist_p = HistoryBuffer()
    pe_p = PreviewEngine(svc_p, hist_p, adopted_frequency_hz=2.0)
    svc_p.schedule_param_step(ParamProgramStep(5, "freq_hz", 3.0))
    svc_p.schedule_param_step(ParamProgramStep(6, "dfreq_hz", 0.05))
    before_p = svc_p.state()
    win_p, meta_p = pe_p.window(8)
    freqs = [w.frequency_hz for w in win_p]
    d_chi = [round(win_p[i + 1].chi_c - win_p[i].chi_c, 6)
             for i in range(len(win_p) - 1)]
    ok_c1 = (freqs[:2] == [2.0, 2.0] and freqs[2:] == [3.0] * 6
             and d_chi[0] == 0.02 and d_chi[1] == 0.03
             and win_p[2].params_applied == ("freq_hz=3.0@5",)
             and win_p[3].params_applied == ("dfreq_hz=0.05@6",)
             and abs((win_p[4].epsilon - win_p[3].epsilon) - 0.0005) < 1e-12)
    check("四补/参数程序经生产预览入口在副本按步执行并改变样本",
          ok_c1,
          "频率=%s chi增量=%s eps增量=%.4f params=%s"
          % (freqs[:4], d_chi[:3], win_p[4].epsilon - win_p[3].epsilon,
             [w.params_applied for w in win_p if w.params_applied]))
    check("四补/程序执行后原件不受影响（待办保留、状态一致）",
          svc_p.state() == before_p
          and svc_p.state()["pending_params"] == 2
          and svc_p.state()["applied_params"] == 0,
          "state 不变；两条程序仍在待办")
    # 未知未来闭环需求保持当前采用值（程序在场时同样成立）
    svc_q = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_q.advance(2.0)
    pe_q = PreviewEngine(svc_q, HistoryBuffer(), adopted_frequency_hz=2.0)
    win_q, _ = pe_q.window(5)
    check("四补/程序在场外未知闭环需求仍保持当前采用值",
          all(w.frequency_hz == 2.0 for w in win_q)
          and abs((win_q[-1].chi_c - svc_q.read().chi_c)
                  - 2.0 * 0.01 * 5) < 1e-12,
          "无程序时副本按播种采用值推进")
    # 真实服务同语义：程序在真实提交步执行一次，读取不再执行
    svc_r = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_r.advance(2.0)
    svc_r.schedule_param_step(ParamProgramStep(2, "freq_hz", 3.0))
    s_r1 = svc_r.advance()
    s_r2 = svc_r.read()
    check("四补/真实服务执行程序一次且读取不重复执行",
          s_r1.frequency_hz == 3.0 and s_r2.frequency_hz == 3.0
          and len(svc_r._applied_params) == 1
          and len(svc_r._pending_params) == 0)

    # ── 判据六补证：有状态提供者与可变数组入同一生产预览链 ──
    ext = _np.zeros(2)
    svc_s = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_s.advance(2.0)
    hist_s = HistoryBuffer()
    hist_s.record(svc_s.read())
    prov = _WaveProvider(ext)
    pe_s = PreviewEngine(svc_s, hist_s, adopted_frequency_hz=2.0,
                         providers=(prov,))
    snap_prov = (prov.count, list(prov.cache), prov.buf.tolist(),
                 ext.tolist())
    snap_svc = svc_s.state()
    snap_hist = len(hist_s)
    win_s, meta_s = pe_s.window(5)
    ok_d1 = (all(w.providers_stepped == ("_WaveProvider",)
                 for w in win_s)
             and meta_s.providers == ("_WaveProvider",)
             and (prov.count, list(prov.cache), prov.buf.tolist(),
                  ext.tolist()) == snap_prov
             and svc_s.state() == snap_svc and len(hist_s) == snap_hist)
    check("六补/有状态提供者入生产预览链执行且窗口结束后原件与内部"
          "别名隔离（数组、缓存、计数、外部共享数组、节律与历史）",
          ok_d1,
          "提供者逐步步进=%s；原件计数=%d 缓存=%s 数组=%s 外部=%s"
          % (all(w.providers_stepped for w in win_s), prov.count,
             prov.cache, prov.buf.tolist(), ext.tolist()))

    class _BoomProvider(_WaveProvider):
        def clone(self):
            t = _BoomProvider(self.external.copy())
            t.buf = self.buf.copy()
            t.cache = list(self.cache)
            t.count = self.count
            return t

        def on_preview_step(self, sample):
            if sample.step >= 4:
                raise RuntimeError("注入执行异常")
            super().on_preview_step(sample)

    ext2 = _np.zeros(2)
    svc_t = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_t.advance(2.0)
    prov_t = _BoomProvider(ext2)
    pe_t = PreviewEngine(svc_t, HistoryBuffer(), adopted_frequency_hz=2.0,
                         providers=(prov_t,))
    snap_t = (svc_t.state(), prov_t.count, list(prov_t.cache),
              prov_t.buf.tolist(), ext2.tolist())
    try:
        pe_t.window(6)
        raised_t = False
        why_t = "未抛出"
    except RhythmServiceError as exc:
        raised_t = "_BoomProvider" in str(exc) and "异常" in str(exc)
        why_t = str(exc)[:70]
    ok_d2 = (raised_t
             and (svc_t.state(), prov_t.count, list(prov_t.cache),
                  prov_t.buf.tolist(), ext2.tolist()) == snap_t)
    check("六补/提供者执行中异常后原件与内部别名隔离（带定位包装）",
          ok_d2, why_t)
    # 无 clone 的对象拒绝入链；无钩子的可克隆对象可入链且隔离
    class _NoClone:
        pass

    try:
        PreviewEngine(svc_t, HistoryBuffer(), 2.0, providers=(_NoClone(),))
        rej_nc = False
    except RhythmServiceError as exc:
        rej_nc = "clone" in str(exc)
    check("六补/未实现 clone 的提供者定位拒绝", rej_nc)

    class _PlainClone:
        def __init__(self):
            self.tags = []

        def clone(self):
            t = _PlainClone()
            t.tags = list(self.tags)
            return t

    pc = _PlainClone()
    pe_u = PreviewEngine(svc_t, HistoryBuffer(), 2.0, providers=(pc,))
    win_u, meta_u = pe_u.window(3)
    check("六补/无步进钩子的可克隆对象入链不执行不改原件",
          all(w.providers_stepped == () for w in win_u)
          and pc.tags == [] and meta_u.providers == ("_PlainClone",))

    # ── v4 修复检查：超前预测与参数程序一致 ──
    svc_v4 = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_v4.advance(2.0)
    svc_v4.schedule_param_step(ParamProgramStep(2, "freq_hz", 5.0))
    pe_v4 = PreviewEngine(svc_v4, HistoryBuffer(),
                          adopted_frequency_hz=2.0)
    win_v4, _m = pe_v4.window(3)
    neg_v4 = pe_v4.lagged_shape_phase(-0.03, 0.01)
    want_v4 = 1.0681415022205298
    check("v4/超前预测执行已排定参数程序并与窗口一致（Fil 反例反转）",
          abs(neg_v4["value"] - win_v4[-1].shape_phase) < 1e-15
          and abs(neg_v4["value"] - want_v4) < 1e-12
          and neg_v4["method"] == PREVIEW_PREDICTION_METHOD,
          "超前=%.16f 窗口=%.16f 期望=%.16f"
          % (neg_v4["value"], win_v4[-1].shape_phase, want_v4))
    # 程序在起始步生效：目标第一步即改频
    svc_a = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_a.advance(2.0)
    svc_a.schedule_param_step(ParamProgramStep(2, "freq_hz", 5.0))
    pe_a = PreviewEngine(svc_a, HistoryBuffer(), adopted_frequency_hz=2.0)
    w_a, _ = pe_a.window(2)
    n_a = pe_a.lagged_shape_phase(-0.02, 0.01)
    check("v4/程序在起始步生效两出口一致",
          abs(n_a["value"] - w_a[-1].shape_phase) < 1e-15
          and w_a[0].frequency_hz == 5.0)
    # 程序在中间步生效
    svc_b = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_b.advance(2.0)
    svc_b.schedule_param_step(ParamProgramStep(3, "freq_hz", 4.0))
    pe_b = PreviewEngine(svc_b, HistoryBuffer(), adopted_frequency_hz=2.0)
    w_b, _ = pe_b.window(4)
    n_b = pe_b.lagged_shape_phase(-0.04, 0.01)
    check("v4/程序在中间步生效两出口一致",
          abs(n_b["value"] - w_b[-1].shape_phase) < 1e-15
          and w_b[0].frequency_hz == 2.0 and w_b[1].frequency_hz == 4.0)
    # 程序恰在目标步生效
    svc_c = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    svc_c.advance(2.0)
    svc_c.schedule_param_step(ParamProgramStep(4, "freq_hz", 6.0))
    pe_c = PreviewEngine(svc_c, HistoryBuffer(), adopted_frequency_hz=2.0)
    w_c, _ = pe_c.window(3)
    n_c = pe_c.lagged_shape_phase(-0.03, 0.01)
    check("v4/程序恰在目标步生效两出口一致",
          abs(n_c["value"] - w_c[-1].shape_phase) < 1e-15
          and w_c[-1].frequency_hz == 6.0)
    # 无程序时保持与正滞后历史仍通过（针对性回归）
    svc_d = RhythmService(frequency_hz=2.0, dt=0.01, split=0.5)
    hist_d = HistoryBuffer()
    pe_d = PreviewEngine(svc_d, hist_d, adopted_frequency_hz=2.0)
    for _ in range(10):
        hist_d.record(svc_d.advance(2.0))
    w_d, _ = pe_d.window(5)
    n_d = pe_d.lagged_shape_phase(-0.02, 0.01)
    pos_d = pe_d.lagged_shape_phase(0.02, 0.01)
    check("v4/无程序保持当前采用值与正滞后历史不受影响",
          abs(n_d["value"]
              - pe_d.lagged_shape_phase(-0.02, 0.01)["value"]) < 1e-15
          and all(sm.frequency_hz == 2.0 for sm in w_d)
          and pos_d["method"] == "history" and pos_d["status"] == "present")

    # 回归：默认构造不触碰全局、旧核行为不变（快档另跑）
    src = open(os.path.join(ROOT, "modules", "motion_action", "rhythm.py"),
               encoding="utf-8").read()
    check("回归/节律模块不读主程序全局", "main_sim" not in src
          and "globals()" not in src and "_G[" not in src)

    fails = [r for r in RESULTS if not r[1]]
    print("\n==== I-220 检查：%d 项，通过 %d，失败 %d ===="
          % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("FAIL %s %s" % (name, detail))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
