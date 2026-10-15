"""
kinematics.py — 拍动运动学生成器（零 Isaac 依赖，唯一实现）
====================================================
本文件是 WingKinematics 的唯一实现地（I-014 气动双栈合并：自
modules/aero_solver.py 原文迁入，物理逐位不变）。modules/aero_solver.py
经包导入再导出，外部既有导入路径不改动。

两种俯仰模式：
  flip — α(t) = bias + amp·cos(ωt+phase)，与 φ̇ 同号（昆虫悬停翻转俯仰）
  aero — 主动俯仰，使有效攻角稳定在 ALPHA_SET（附着流/动态失速前飞型，
         攻角由控制器约束在失速边界内或受控越过）
"""

from __future__ import annotations

import numpy as np
from typing import Dict

from config import sim_config as cfg


class WingKinematics:
    """
    生成单翼的拍动角 φ(t) 和翼面俯仰角 α(t)。

    拍动：φ(t) = bias + amp·sin(ω·t + phase)，ω=2π·f

    俯仰模式 PITCH_MODE：
      "flip" — α(t) = bias + amp·cos(ω·t + phase)，与 φ̇ 同号。
               翻转俯仰(feathering)的物理本质：上下半拍都产生向上升力
               （蜂鸟/昆虫悬停机制）。
      "aero" — 主动俯仰，使有效攻角稳定在 ALPHA_SET 附近
               （附着流前飞型，攻角约束在失速边界内）。
    """

    def __init__(
        self,
        frequency: float = cfg.FlapKinematicsConfig.FREQUENCY,
        amplitude_deg: float = cfg.FlapKinematicsConfig.AMPLITUDE,
        bias_deg: float = cfg.FlapKinematicsConfig.BIAS,
        pitch_amplitude_deg: float = cfg.FlapKinematicsConfig.FEATHER_AMPLITUDE,
        pitch_bias_deg: float = cfg.FlapKinematicsConfig.FEATHER_BIAS,
        pitch_mode: str = cfg.FlapKinematicsConfig.PITCH_MODE,
        alpha_set_deg: float = cfg.FlapKinematicsConfig.ALPHA_SET,
        u_ref: float = 5.0,
        split: float = None,
        mean_deg: float = None,
        pitch_lag_s: float = None,
    ):
        self.frequency = float(frequency)
        self.omega = 2.0 * np.pi * self.frequency
        self.amplitude = np.radians(amplitude_deg)
        self.bias = np.radians(bias_deg)
        # 分裂周期比 s：0.5=对称（与原行为逐位一致）；均值位置角 μ (rad)
        self.split = cfg.FlapKinematicsConfig.SPLIT if split is None else float(split)
        # 俯仰相对拍动的时间偏移(s)：正=滞后，负=超前；默认读 config
        self.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S if pitch_lag_s is None else float(pitch_lag_s)
        self.mean = np.radians(cfg.FlapKinematicsConfig.MEAN
                               if mean_deg is None else mean_deg)
        self.pitch_amplitude = np.radians(pitch_amplitude_deg)
        self.pitch_bias = np.radians(pitch_bias_deg)
        self.pitch_mode = pitch_mode
        self.alpha_set = np.radians(alpha_set_deg)
        self.u_ref = float(u_ref)
        # 相位积分状态（2026-09-20：改频不再跳相，见 _phase_sc 注释）
        self._t_prev = None
        self._tau_state = 0.0
        # 逐拍启停闸门（2026-09-27 冲量积分线）：1.0=拍动（默认，
        # 行为逐位不变）；0.0=本拍停拍。停拍输出钉中立位（第十五班
        # 停扑建模修正口径：phi=偏置+均值行程中心，翼面攻角取周期
        # 均值 alpha_set·2/π），相位积分照常推进（时间单调纪律，
        # 停拍不冻结不重置），恢复起拍的伺服瞬态由位置驱动带宽吸收。
        self.stroke_gate = 1.0

    def set_frequency(self, freq: float) -> None:
        """运行时修改拍动频率（控制器调用）。"""
        self.frequency = float(freq)
        self.omega = 2.0 * np.pi * self.frequency

    def stroke_unit(self, t: float, phase_offset_rad: float = 0.0) -> float:
        """归一化拍动波形 sin(ph)（事项 I-190 公共增量规则）。

        供翼幅差动按幅值语义生成增量：增量 = ±dA·sin(ph)。只读相位状态，
        与 evaluate 在同一 t 调用时 _phase_sc 的时间差为零、不写状态，
        每步相位推进次数仍为一次。停拍闸门开启时返回 0.0 并按 evaluate
        同纪律推进相位——与力模型停拍钉中立位一致：幅值变化在停拍段无效应。
        """
        if self.stroke_gate < 0.5:
            self._phase_sc(t, phase_offset_rad)
            return 0.0
        ph, _ = self._phase_sc(t, phase_offset_rad)
        return float(np.sin(ph))

    def _phase_sc(self, t: float, phase_offset_rad: float, advance: bool = True):
        """分裂周期相位重映射（S.P. Sane 经典不对称步态）。

        τ∈[0,s) → ph∈[0,π)（下扑），τ∈[s,1) → ph∈[π,2π)（上扑）。
        s<0.5 = 下扑快上扑慢；s=0.5 时 ph=2πτ 与标准正弦逐位一致。
        返回 (ph, dph/dτ)。

        2026-09-20 相位改为积分状态（另一会话独立分析发现、本班核实）：原
        τ=(f·t)mod1 用绝对时间，运行时改频相位瞬跳 t·Δf 且随飞行时间
        线性放大（t=10s、Δf=0.05Hz 即跳 180°）——高度环以拍频为主控
        量，闭环一开相位每步都在跳，K=60 位置驱动追逐跳变目标对机体
        连续放冲量；台架/harness 定频求值不暴露。现在 τ 随仿真时间积
        分，set_frequency 只改频率不改相位。要求 t 单调递增（同一 t 的
        重复求值不推进相位）。
        """
        if self._t_prev is None:
            self._tau_state = (self.frequency * t) % 1.0
            self._t_prev = t
            tau0 = self._tau_state
        else:
            _dt = t - self._t_prev
            # 投影相位：无论预览时刻在未来还是过去，都从已写入状态外推；
            # 只有 advance=True 且时间前进的真实求值才写状态。预览调用
            # （evaluate 的攻角超前/滞后与数值微分，2026-09-20 另一会话发现：
            # 预览也推进状态导致波形 2.42 倍速）必须 advance=False
            tau0 = (self._tau_state + self.frequency * _dt) % 1.0
            if advance and _dt > 0.0:
                self._tau_state = tau0
                self._t_prev = t
        tau = (tau0 + phase_offset_rad / (2.0 * np.pi)) % 1.0
        s = min(max(self.split, 0.05), 0.95)
        if tau < s:
            return np.pi * tau / s, np.pi / s
        return np.pi + np.pi * (tau - s) / (1.0 - s), np.pi / (1.0 - s)

    def evaluate(self, t: float, phase_offset_rad: float = 0.0) -> Dict[str, float]:
        """返回 {phi, alpha, phi_dot, alpha_dot, phi_ddot, alpha_ddot}。"""
        if self.stroke_gate < 0.5:
            # 停拍分支（逐拍差动）：相位状态照常积分，输出钉中立位
            self._phase_sc(t, phase_offset_rad)
            return {"phi": float(self.bias + self.mean),
                    "alpha": float(self.alpha_set * 2.0 / np.pi + self.pitch_bias),
                    "phi_dot": 0.0, "alpha_dot": 0.0,
                    "phi_ddot": 0.0, "alpha_ddot": 0.0}
        ph, dph = self._phase_sc(t, phase_offset_rad)
        phd = dph * self.frequency            # dph/dt
        phi = self.bias + self.mean + self.amplitude * np.sin(ph)
        phi_dot = self.amplitude * phd * np.cos(ph)
        phi_ddot = -self.amplitude * phd**2 * np.sin(ph)

        if self.pitch_mode == "flip":
            # α 用 cos 与 φ̇ 同号，实现翻转俯仰
            alpha = self.pitch_bias + self.pitch_amplitude * np.cos(ph)
            alpha_dot = -self.pitch_amplitude * phd * np.sin(ph)
            alpha_ddot = -self.pitch_amplitude * phd**2 * np.cos(ph)
        else:
            # aero 模式：目标攻角符号跟随 φ̇，|cos| 平滑过渡
            ph_l, dph_l = self._phase_sc(t - self.pitch_lag_s, phase_offset_rad,
                                          advance=False)
            phi_dot_l = self.amplitude * dph_l * self.frequency * np.cos(ph_l)
            s = np.abs(np.cos(ph_l))
            sign = 1.0 if phi_dot_l >= 0 else -1.0
            alpha = sign * self.alpha_set * s + self.pitch_bias
            dt = 1e-4
            alpha_ahead = self._alpha_aero(t + dt - self.pitch_lag_s, phase_offset_rad)
            alpha_dot = (alpha_ahead - alpha) / dt
            alpha_ddot = 0.0

        return {
            "phi": float(phi),
            "alpha": float(alpha),
            "phi_dot": float(phi_dot),
            "alpha_dot": float(alpha_dot),
            "phi_ddot": float(phi_ddot),
            "alpha_ddot": float(alpha_ddot),
        }

    def _alpha_aero(self, t: float, phase_offset_rad: float) -> float:
        ph, dph = self._phase_sc(t, phase_offset_rad, advance=False)
        phi_dot = self.amplitude * dph * self.frequency * np.cos(ph)
        s = np.abs(np.cos(ph))
        sign = 1.0 if phi_dot >= 0 else -1.0
        return sign * self.alpha_set * s + self.pitch_bias


# ============================================================
# 公共采样与克隆（事项 I-220，追加区；上方既有函数不改一行）
# ============================================================

def split_phase_map(tau: float, split: float):
    """分裂周期相位重映射的公共纯函数（事项 I-220）。

    与 WingKinematics._phase_sc 内部核同式（τ∈[0,s)→ph∈[0,π) 下扑，
    τ∈[s,1)→ph∈[π,2π) 上扑），供节律服务的形状相位视图复用旧分裂周
    期核——旧函数保留原位原行为，本函数是只读公共出口，等价性由
    I-220 检查器在网格上逐点核对。返回 (ph, dph/dτ)。
    """
    s = min(max(split, 0.05), 0.95)
    tau = tau % 1.0
    if tau < s:
        return np.pi * tau / s, np.pi / s
    return np.pi + np.pi * (tau - s) / (1.0 - s), np.pi / (1.0 - s)


def _copy_kin_state(dst: "WingKinematics", src: "WingKinematics") -> None:
    """把相位积分状态与闸门从 src 复制到 dst（同构实例间）。"""
    dst._t_prev = src._t_prev
    dst._tau_state = src._tau_state
    dst.stroke_gate = src.stroke_gate
    dst.frequency = src.frequency
    dst.omega = src.omega


def clone_kinematics(wk: WingKinematics) -> WingKinematics:
    """克隆拍动运动学生成器（事项 I-220：预览隔离的副本来源）。

    返回同参数新实例并复制相位积分状态（_t_prev、_tau_state）、闸门
    与运行时频率；原实例的其余字段为构造期常量，经同参构造复制。原
    实例不被触碰，副本的推进不影响原件（单一提交入口纪律不变）。
    """
    twin = WingKinematics(
        frequency=wk.frequency,
        amplitude_deg=float(np.degrees(wk.amplitude)),
        bias_deg=float(np.degrees(wk.bias)),
        pitch_amplitude_deg=float(np.degrees(wk.pitch_amplitude)),
        pitch_bias_deg=float(np.degrees(wk.pitch_bias)),
        pitch_mode=wk.pitch_mode,
        alpha_set_deg=float(np.degrees(wk.alpha_set)),
        u_ref=wk.u_ref,
        split=wk.split,
        mean_deg=float(np.degrees(wk.mean)),
        pitch_lag_s=wk.pitch_lag_s,
    )
    _copy_kin_state(twin, wk)
    return twin


def sample_kinematics_readonly(wk: WingKinematics, t: float,
                               phase_offset_rad: float = 0.0) -> Dict[str, float]:
    """只读采样拍动运动学（事项 I-220：采样不成为第二提交入口）。

    在保存相位积分状态后调用既有 evaluate 完成整组输出，随后逐字恢复
    状态——本函数不推进 _tau_state、不移动 _t_prev，真实推进仍只能由
    真实物理步的 evaluate 完成。停拍闸门与俯仰模式语义与 evaluate 完
    全一致。
    """
    saved = (wk._t_prev, wk._tau_state, wk.stroke_gate)
    try:
        return wk.evaluate(t, phase_offset_rad)
    finally:
        wk._t_prev, wk._tau_state, wk.stroke_gate = saved
