# -*- coding: utf-8 -*-
"""
experiment.py — 系留台架自动试验控制器（与 ManualInput 同接口）
================================================================
模式：
  sweep  — 扑频扫阶：1.5→3.5Hz 逐点驻留，测周期平均升力曲线
  rudder — 舵效阶跃：悬停配平后对 δe/δa/δr 逐项打阶跃，测力矩响应

当前试验阶段写 logs/experiment_phase.json（kit 吞 stdout，文件是唯一可靠通道），
tools/run_ident.py 据此分段记录与拟合。
"""
import json
import math
import os
import time

import config.sim_config as cfg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHASE_FILE = os.path.join(ROOT, "logs", "experiment_phase.json")

Z0 = {"throttle": 0.0, "elevator": 0.0, "aileron": 0.0, "rudder": 0.0}

# 各试验模式的台架默认（main_sim 在试验初始化时应用到 cfg）：
#   wind — 是否开来流风；free — 台架释放轴（kick 段才解锁，见 main_sim）
MODE_DEFAULTS = {
    "sweep":        dict(wind=False, free=()),
    "rudder":       dict(wind=True,  free=()),   # 尾舵辨识需要 5m/s 速压（2026-09-18 工况）
    "dastep":       dict(wind=False, free=()),   # dA 主用场景=静气悬停，必须静气（2026-09-19 修正）
    "trim":         dict(wind=False, free=()),
    "windcal":      dict(wind=True,  free=()),
    "kick_roll":    dict(wind=False, free=("roll",)),
    "kick_pitch":   dict(wind=False, free=("pitch",)),
    "kick_yaw":     dict(wind=False, free=("yaw",)),
    "kick_pitch_x": dict(wind=False, free=("pitch", "x")),  # 俯仰-速度链验证
    "rate_roll":    dict(wind=False, free=("roll",)),
    "rate_pitch":   dict(wind=False, free=("pitch",)),
    "rate_yaw":     dict(wind=False, free=("yaw",)),
    "stepx":        dict(wind=False, free=("x",)),  # 俯仰阶跃→前向速度链验证
    "rudder_v":     dict(wind=True,  free=()),      # 尾翼 B(v) 速压调度（第 4 条）
}


def _thr_of_freq(f):
    """拍频 → 油门杆量（与 main_sim 手动映射互逆）。"""
    return (f - cfg.MANUAL_FREQ_MIN) / (cfg.MANUAL_FREQ_MAX - cfg.MANUAL_FREQ_MIN) \
        * 2.0 - 1.0


class ExperimentController:
    def __init__(self, mode: str):
        self.source = f"experiment:{mode}"
        self._t0 = time.time()
        self._sched = []          # (起始时刻s, 标签, 通道dict)
        self._build(mode)
        self._last_label = None

    # ------------------ 试验时间表 ------------------
    def _build(self, mode):
        if mode == "sweep":
            self._sched.append((0.0, "settle", dict(Z0)))
            t = cfg.EXP_SETTLE_S
            f = cfg.EXP_SWEEP_FMIN
            while f <= cfg.EXP_SWEEP_FMAX + 1e-6:
                dwell = max(cfg.EXP_DWELL_S, cfg.EXP_MIN_CYCLES / f)
                self._sched.append((t, f"sweep_f{f:.2f}",
                                    {"throttle": _thr_of_freq(f),
                                     "elevator": 0.0, "aileron": 0.0, "rudder": 0.0}))
                t += dwell
                f += cfg.EXP_SWEEP_STEP
            self._sched.append((t, "done", dict(Z0)))
        elif mode == "rudder":
            self._sched.append((0.0, "settle", dict(Z0)))
            t = cfg.EXP_SETTLE_S
            for ch, gain_key, amp in (("elevator", "MANUAL_ELEV_GAIN", cfg.EXP_STEP_ELEV),
                                      ("aileron", "MANUAL_AIL_GAIN", cfg.EXP_STEP_AIL),
                                      ("rudder", "MANUAL_RUDD_GAIN", cfg.EXP_STEP_RUD)):
                gain = getattr(cfg, gain_key)
                for sign in (+1.0, -1.0):
                    ch_val = sign * amp / gain
                    self._sched.append((t, f"rud_{ch}_{sign*amp:+.2f}",
                                        {**Z0, "throttle": _thr_of_freq(cfg.DP_FREQ_NOMINAL),
                                         ch: ch_val}))
                    t += cfg.EXP_STEP_S
                    self._sched.append((t, f"rud_{ch}_back",
                                        {**Z0, "throttle": _thr_of_freq(cfg.DP_FREQ_NOMINAL)}))
                    t += cfg.EXP_GAP_S
            self._sched.append((t, "done", dict(Z0)))
        elif mode == "dastep":
            # 翼差动舵效：每频率点稳定后 ±dA 阶跃，出 B_dA(f)
            t = cfg.EXP_SETTLE_S
            self._sched.append((0.0, "settle", dict(Z0)))
            for f in cfg.EXP_DA_FREQS:
                thr = _thr_of_freq(f)
                self._sched.append((t, f"da_f{f:.2f}_settle",
                                    {**Z0, "throttle": thr}))
                t += cfg.EXP_DA_SETTLE_S
                for amp in cfg.EXP_DA_AMPS:
                    for sign in (+1.0, -1.0):
                        self._sched.append(
                            (t, f"da_f{f:.2f}_{sign*amp:+.2f}",
                             {**Z0, "throttle": thr, "da": sign * amp}))
                        t += cfg.EXP_DA_STEP_S
                        self._sched.append((t, f"da_f{f:.2f}_back",
                                            {**Z0, "throttle": thr}))
                        t += cfg.EXP_DA_GAP_S
            self._sched.append((t, "done", dict(Z0)))
        elif mode == "trim":
            # 俯仰配平扫阶：悬停拍频下逐角度偏置钉固姿态，测 Fx(θ) 归零角
            thr = _thr_of_freq(1.9)
            t = cfg.EXP_SETTLE_S
            self._sched.append((0.0, "settle", {**Z0, "pitch_deg": 0.0}))
            for deg in cfg.EXP_TRIM_PITCHES:
                self._sched.append((t, f"trim_p{deg:+.0f}",
                                    {**Z0, "throttle": thr, "pitch_deg": deg}))
                t += cfg.EXP_TRIM_STEP_S
                self._sched.append((t, "trim_back",
                                    {**Z0, "throttle": thr, "pitch_deg": 0.0}))
                t += cfg.EXP_TRIM_GAP_S
            self._sched.append((t, "done", dict(Z0)))
        elif mode == "windcal":
            # 速压扫阶：悬停拍频钉固台架逐档加迎风，测 Fx(v)/L̄(v)
            thr = _thr_of_freq(1.9)
            t = cfg.EXP_SETTLE_S
            self._sched.append((0.0, "settle", {**Z0, "wind": 0.0}))
            for v in cfg.EXP_WIND_SPEEDS:
                self._sched.append((t, f"wind_v{v:.1f}",
                                    {**Z0, "throttle": thr, "wind": v}))
                t += cfg.EXP_WIND_STEP_S
            self._sched.append((t, "done", dict(Z0)))
        elif mode.startswith("kick_"):
            # 单轴释放衰减：钉固稳态 → 初始角速率脉冲 → 自由衰减 → 阻尼导数
            axis = mode[len("kick_"):]           # roll | pitch | yaw | pitch_x
            thr = _thr_of_freq(1.9)
            key = {"roll": "kick_wx", "pitch": "kick_wy", "yaw": "kick_wz",
                   "pitch_x": "kick_wy"}[axis]
            t = cfg.EXP_KICK_SETTLE_S
            self._sched.append((0.0, "settle", {**Z0, "throttle": thr}))
            self._sched.append((t, "kick",
                                {**Z0, "throttle": thr, "release": 1,
                                 key: cfg.EXP_KICK_W}))
            t += cfg.EXP_KICK_HOLD_S
            self._sched.append((t, "free", {**Z0, "throttle": thr, "release": 1}))
            t += cfg.EXP_KICK_FREE_S
            self._sched.append((t, "done", dict(Z0)))
        elif mode.startswith("rate_"):
            # 定速台架：逐档处方角速率（释放轴但速率被运动学钉住），
            # 测周期平均力矩斜率 = 阻尼导数。自由衰减法不可用（模型瞬时
            # 反阻尼窗口在自由释放下泵能自激，2026-09-19 实测），故用定速法。
            axis = mode[len("rate_"):]           # roll | pitch | yaw
            key = {"roll": "rate_wx", "pitch": "rate_wy", "yaw": "rate_wz"}[axis]
            thr = _thr_of_freq(1.9)
            t = cfg.EXP_RATE_SETTLE_S
            self._sched.append((0.0, "settle", {**Z0, "throttle": thr}))
            for v in cfg.EXP_RATES:
                self._sched.append((t, f"rate_{axis}_{v:+.2f}",
                                    {**Z0, "throttle": thr, "release": 1,
                                     key: v}))
                t += cfg.EXP_RATE_STEP_S
            self._sched.append((t, "done", dict(Z0)))
        elif mode == "stepx":
            # 俯仰-速度链验证：俯仰由台架钉在阶跃角（不释放，防零阻尼漂移），
            # 仅释放 x 平移，测倾斜升力 → vx(t) 的前向加速过程。
            # 两个幅值（12°/20°）检验链路线性；终端速度由速度阻尼决定。
            thr = _thr_of_freq(1.9)
            t = cfg.EXP_KICK_SETTLE_S
            self._sched.append((0.0, "settle",
                                {**Z0, "throttle": thr, "pitch_deg": 0.0}))
            for deg in (12.0, 20.0):
                self._sched.append((t, f"stepx_p{deg:+.0f}",
                                    {**Z0, "throttle": thr, "pitch_deg": deg,
                                     "release": 1}))
                t += 8.0
                self._sched.append((t, "stepx_p+0",
                                    {**Z0, "throttle": thr, "pitch_deg": 0.0,
                                     "release": 1}))
                t += 4.0
            self._sched.append((t, "done", dict(Z0)))
        elif mode == "rudder_v":
            # 尾翼舵效速压调度（第 4 条）：每个风速点下三通道逐项 ±阶跃，
            # 出 B_elev/B_ail/B_rud 随 v 的调度表并检验 ∝v²。
            # 风速逐段经 "wind" 通道改写 cfg.WIND_VECTOR（0 点为原点锚定）。
            thr = _thr_of_freq(cfg.DP_FREQ_NOMINAL)
            t = cfg.EXP_SETTLE_S
            self._sched.append((0.0, "settle", {**Z0, "wind": 0.0}))
            for v in cfg.EXP_RUDV_SPEEDS:
                self._sched.append((t, f"rudv_v{v:.1f}_settle",
                                    {**Z0, "throttle": thr, "wind": v}))
                t += 3.0
                for ch, gain_key, amp in (
                        ("elevator", "MANUAL_ELEV_GAIN", cfg.EXP_STEP_ELEV),
                        ("aileron", "MANUAL_AIL_GAIN", cfg.EXP_STEP_AIL),
                        ("rudder", "MANUAL_RUDD_GAIN", cfg.EXP_STEP_RUD)):
                    gain = getattr(cfg, gain_key)
                    for sign in (+1.0, -1.0):
                        ch_val = sign * amp / gain
                        self._sched.append(
                            (t, f"rudv_v{v:.1f}_{ch}_{sign*amp:+.2f}",
                             {**Z0, "throttle": thr, "wind": v, ch: ch_val}))
                        t += 4.0
                        self._sched.append((t, f"rudv_v{v:.1f}_back",
                                            {**Z0, "throttle": thr,
                                             "wind": v}))
                        t += 2.0
            self._sched.append((t, "done", dict(Z0)))
        else:
            raise ValueError(f"未知试验模式: {mode}")

    # ------------------ 与 ManualInput 同接口 ------------------
    def read(self):
        t = time.time() - self._t0
        label, ch = "settle", Z0
        for t_start, lb, c in self._sched:
            if t >= t_start:
                label, ch = lb, c
            else:
                break
        if label != self._last_label:
            self._last_label = label
            try:
                with open(PHASE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"t": t, "phase": label,
                               "wall": time.time()}, f)
            except OSError:
                pass
        return dict(ch)
