# -*- coding: utf-8 -*-
"""
hover_env_v2.py — 扑翼悬停 Gymnasium 环境 V2（台架真值参数 + 完整域随机化契约）
================================================================================
相对 V1 的变更（2026-09-20，对应第三梯队任务单三项）：

1. 真值依据（docs/IDENT_20260919）：
   - 滚转力矩分配依据 B_dA(f) ≈ 5.2·f² N·m/rad（静气台架真值表，
     悬停点 19.0）——注意：环境的"被控对象"本身由气动模型供阻尼
     L_p≈−0.60 N·m·s/rad（模型真值，另一会话第四节的同一套测量），
     WX_DAMP 只是模型之外的寄生粘性项（0.02），不是总阻尼。
     （对"WX_DAMP 比真值小 12 倍"读数的书面更正：环境总有效阻尼
     = 0.60（模型自带）+ 0.02（寄生项），不存在 12 倍偏差。）

2. 域随机化完整契约（PPT 第 17 页目标栏）：
   质量 ±10%（0.215 口径）、惯量 Ixx ∈ [0.0037, 0.0062]（215g 缩放）、升力系数 ±30%、
   执行器（拍频一阶滞后 τ_f + 差动一阶滞后 τ_a）、
   分通道传感噪声（z/vz/roll/wx 不同 σ）、恒定风扰 v_wind ∈ [-3,3] m/s。

3. 脚手架（等主线 α 辨识表落地后开通）：
   - 观测增加拍内相位 sinφ/cosφ（环形变量的正确编码）；
   - 动作通道可配置 action_channels，支持 ("freq","dA") 与
     ("freq","dA","alpha") 两种，α 通道默认关闭（有效性待主线辨识）。

动力学口径与 V1 一致：周期平均（cycle-averaged）+ 50Hz 控制 +
垂直速度动量修正 + 力矩 2 N·m 限幅 + |wx|≤3 包线守卫。
"""
import math
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import gymnasium as gym
from gymnasium import spaces

from modules.aero_solver import FlapperAeroModel

DT_CTRL = 0.02                   # 控制周期 50Hz
Z_REF = 1.5
FREQ_MID = 2.5
FREQ_HALF = 1.0
FREQ_SLEW = 2.0                  # Hz/s 拍频速率限制（传动惯性）
DA_MAX = 0.25                    # rad
# 翼幅值冻结在 09-18 悬停辨识契约值 35 度（2026-10-05 I-071 排障结论）：
# 此前本行写 math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)，而主线
# 39cfb9a（09-26）把该配置从 35.0 改到 17.73，环境实际拍幅随主线漂移，
# 逆映射数据集 wave_inverse.npz 与拒采前代 waveform_inverse.forward 仍
# 锚定 35 度，预测与实际力旋量脱钩，闭环基线 15/16 在现版本不再复现。
# 幅值与 split/lag/mean 一样属于"冻结契约与 config 演进解耦"的参数，
# 此前是唯一漏冻结的一个。彻底解法（改从参数登记库读取）属 I-073。
A0 = math.radians(35.0)
WX_DAMP = 0.02                   # 寄生粘性阻尼（模型外；模型自带 L_p≈0.60）
EPISODE_S = 10.0
ALPHA_MAX = math.radians(10.0)   # α 通道脚手架限幅（待主线辨识表）


class FlapperHoverEnvV2(gym.Env):
    """V2 环境。

    obs = [z, vz, roll, wx, freq_actual, sinφ, cosφ]（7 维）
    act = [freq_n, dA_n] 或 [freq_n, dA_n, alpha_n]（action_channels 决定）
    """

    metadata = {"render_modes": []}

    def __init__(self, randomize: bool = True, obs_noise: bool = True,
                 action_channels=("freq", "dA"), platform="phoenix_2p3m",
                 seed=None):
        super().__init__()
        self.action_channels = tuple(action_channels)
        n_act = len(self.action_channels)
        # 平台契约（2026-09-20/21 双平台裁决）：
        #   phoenix_2p3m    — 2.3m/700g 悬停契约（09-18 辨识 + 逆映射数据集
        #                     所在口径），RL 演示默认
        #   sparrowhawk_0p6m — 主线当前平台 0.6m/215g + 夜间战役巡航波形
        #                     （α40/split0.25/lag−0.02/mean0），其悬停辨识
        #                     未完成（α 表暂缓），仅脚手架
        self.platform = platform
        if platform == "phoenix_2p3m":
            self._geo = {"span": 1.15, "root_chord": 0.35, "tip_chord": 0.25,
                         "n_strips": 14}
            self._kin = {"split": 0.56, "pitch_lag_s": -0.02, "mean": 0.0,
                         "alpha_set_deg": 15.0}
            self._mass_rng = (0.63, 0.77)
            self._ixx_rng = (0.012, 0.020)
            self._mass_nominal = 0.70
            self._ixx_nominal = 0.015
        else:
            self._geo = {"span": 0.32, "root_chord": 0.20, "tip_chord": 0.14,
                         "n_strips": 14}
            self._kin = {"split": 0.25, "pitch_lag_s": -0.02, "mean": 0.0,
                         "alpha_set_deg": 40.0}
            self._mass_rng = (0.194, 0.237)
            self._ixx_rng = (0.0037, 0.0062)
            self._mass_nominal = 0.215
            self._ixx_nominal = 0.005
        self.observation_space = spaces.Box(
            low=np.array([0.0, -8.0, -1.5, -15.0, 1.0, -1.0, -1.0],
                         dtype=np.float32),
            high=np.array([3.0, 8.0, 1.5, 15.0, 4.0, 1.0, 1.0],
                          dtype=np.float32))
        self.action_space = spaces.Box(-1.0, 1.0, shape=(n_act,), dtype=np.float32)
        self.randomize = randomize
        self.obs_noise = obs_noise
        self._rng = np.random.default_rng(seed)
        self.model = None
        self.mass = self._mass_nominal
        self.ixx = self._ixx_nominal
        self.wind_x = 0.0
        self.tau_f = 0.04
        self.tau_a = 0.04
        self._noise_std = np.array([0.005, 0.02, 0.003, 0.05, 0.01, 0.0, 0.0],
                                   dtype=np.float32)
        self.t = 0.0
        self.freq_actual = 1.9
        self.dA_actual = 0.0
        # 全波形参数模式（闭环 CFM 用）：split/lag/mean 可由动作驱动
        self._theta_cur = None   # (split, lag_s, mean_deg) 当前生效值
        self.state = np.zeros(4)   # z, vz, roll, wx

    # ---------------- 域随机化（完整契约）----------------
    # 环境运动学冻结契约（2026-09-20）：主线已进入巡航配平运动学
    # （split0.30/α40°/lag−0.06/mean−15°，V=8 前飞），本环境是静气悬停任务，
    # 必须冻结在 09-18 悬停辨识所对应的运动学（L̄=1.95f² 的测量基础），
    # 与 config 的后续演进解耦——RL 环境的动力学契约应当自包含。
    HOVER_KINEMATICS = {"split": 0.56, "pitch_lag_s": -0.02, "mean": 0.0,
                        "alpha_set_deg": 15.0, "pitch_mode": "aero"}

    def _freeze_kinematics(self):
        for w in (self.model.wing_left, self.model.wing_right):
            w.split = self._kin["split"]
            w.pitch_lag_s = self._kin["pitch_lag_s"]
            w.mean = self._kin["mean"]
            w.alpha_set = math.radians(self._kin["alpha_set_deg"])
            w.pitch_mode = "aero"

    def _randomize_dynamics(self):
        # 几何契约按平台冻结（2026-09-20/21）：与 config 演进解耦
        self.model = FlapperAeroModel(self._geo)
        self._freeze_kinematics()
        if self.randomize:
            s = float(self._rng.uniform(0.7, 1.3))
            self.model.cn_fit_amp *= s
            self.model.ct_fit_amp *= s
            self.mass = float(self._rng.uniform(*self._mass_rng))
            self.ixx = float(self._rng.uniform(*self._ixx_rng))
            self.wind_x = float(self._rng.uniform(-1.5, 1.5))   # m/s
            # 包络说明：|wind|>2.2 时风致升力增量 0.60v² 使最低
            # 拍频升力下限超过体重，两通道权限内无解（需俯仰
            # 释放升力策略，属第三执行器范畴），故契约取 ±1.5
            self.tau_f = float(self._rng.uniform(0.02, 0.08))
            self.tau_a = float(self._rng.uniform(0.02, 0.10))
        else:
            self.mass = self._mass_nominal
            self.ixx = self._ixx_nominal
            self.wind_x = 0.0
            self.tau_f = 0.04
            self.tau_a = 0.04

    # ---------------- 周期平均力旋量 ----------------
    def _cycle_mean_wrench(self, z, vz, roll, wx, freq, dA, alpha_cmd=0.0,
                           theta_ext=None):
        self.model.set_wing_command(frequency=freq)
        self.model.wing_left.amplitude = A0 + dA
        self.model.wing_right.amplitude = A0 - dA
        if theta_ext is not None:
            # 全波形参数模式（闭环 CFM 接入用）：split/lag/mean 由外部 θ 驱动
            sp, lg, mn = theta_ext
            for w in (self.model.wing_left, self.model.wing_right):
                w.split = sp
                w.pitch_lag_s = lg
                w.mean = math.radians(mn)
        if "alpha" in self.action_channels:
            # α 通道脚手架：以攻角设定值偏移形式注入（有效性待主线 α 辨识表；
            # 注意 α_set 基准值冻结在悬停契约 15°，偏移量叠加其上）
            for w in (self.model.wing_left, self.model.wing_right):
                w.alpha_set = math.radians(
                    self._kin["alpha_set_deg"]) \
                    + ALPHA_MAX * alpha_cmd
        quat = np.array([math.cos(roll / 2), math.sin(roll / 2), 0.0, 0.0])
        v_lin = np.array([self.wind_x, 0.0, vz])
        v_ang = np.array([wx, 0.0, 0.0])
        # 相位状态复位（2026-09-20）：主线把 WingKinematics 改为积分相位态，
        # 预览序列会拖动内部状态、破坏"整周期采样"假设。每批求均值前复位，
        # 恢复"按绝对时间无状态求值"的口径（对离线环境两语义等价）。
        for _w in (self.model.wing_left, self.model.wing_right):
            _w._t_prev = None
            _w._tau_state = 0.0
        n = 24
        F_sum = np.zeros(3)
        T_sum = np.zeros(3)
        for i in range(n):
            F, T = self.model.compute_forces(np.array([0.0, 0.0, z]), quat,
                                             v_lin, v_ang,
                                             self.t + i / (n * freq))
            F_sum += F
            T_sum += T
        T_mean = T_sum / n
        t_norm = float(np.linalg.norm(T_mean))
        if t_norm > 2.0:
            T_mean = T_mean * (2.0 / t_norm)
        F_mean = F_sum / n
        F_mean = F_mean.copy()
        F_mean[2] *= 1.0 / (1.0 + (vz / 2.5) ** 2)   # 动量理论爬升修正（同 V1）
        return F_mean, T_mean

    # ---------------- gym 契约 ----------------
    def reset(self, *, seed=None, options=None):
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._randomize_dynamics()
        self.t = 0.0
        self.freq_actual = 1.9
        self.dA_actual = 0.0
        self._theta_cur = None
        self.state = np.array([
            self._rng.uniform(0.9, 2.1),
            self._rng.uniform(-0.6, 0.6),
            self._rng.uniform(-0.2, 0.2),
            self._rng.uniform(-0.4, 0.4),
        ])
        return self._obs(), {}

    def _phase(self):
        return (self.freq_actual * self.t) % 1.0

    def _obs(self):
        ph = 2.0 * math.pi * self._phase()
        o = np.concatenate([self.state, [self.freq_actual],
                            [math.sin(ph), math.cos(ph)]]).astype(np.float32)
        if self.obs_noise:
            o = o + self._rng.normal(0.0, 1.0, size=7).astype(np.float32) \
                * self._noise_std
        return o

    def step(self, action):
        a = np.clip(np.asarray(action, dtype=float), -1.0, 1.0)
        cmd = dict(zip(self.action_channels, a))
        freq_cmd = FREQ_MID + FREQ_HALF * cmd.get("freq", 0.0)
        dA_cmd = DA_MAX * cmd.get("dA", 0.0)
        alpha_cmd = float(cmd.get("alpha", 0.0))
        # 全波形参数通道（闭环 CFM 用）：范围与逆映射数据集 RANGES 一致
        theta_ext = None
        if any(k in cmd for k in ("split", "lag", "mean")):
            self._theta_cur = (
                0.55 + 0.10 * cmd.get("split", 0.0),     # [0.45, 0.65] 绝对值
                0.06 * cmd.get("lag", 0.0),              # ±0.06 s 绝对值
                8.0 * cmd.get("mean", 0.0))              # ±8°
            theta_ext = self._theta_cur

        # 执行器：拍频速率限制（传动惯性），差动一阶滞后 τ_a
        self.freq_actual += float(np.clip(freq_cmd - self.freq_actual,
                                          -FREQ_SLEW * DT_CTRL,
                                          FREQ_SLEW * DT_CTRL))
        self.dA_actual += (DT_CTRL / self.tau_a) * (dA_cmd - self.dA_actual)

        z, vz, roll, wx = self.state
        F, T = self._cycle_mean_wrench(z, vz, roll, wx, self.freq_actual,
                                       self.dA_actual, alpha_cmd,
                                       theta_ext=theta_ext)

        vz += (F[2] / self.mass - 9.81) * DT_CTRL
        z += vz * DT_CTRL
        wx += (T[0] - WX_DAMP * wx) / self.ixx * DT_CTRL
        wx = float(np.clip(wx, -3.0, 3.0))   # 模型有效包线守卫（同 V1）
        roll += wx * DT_CTRL
        roll = (roll + math.pi) % (2 * math.pi) - math.pi
        self.state = np.array([z, vz, roll, wx])
        self.t += DT_CTRL

        err = z - Z_REF
        reward = (1.0
                  - 2.0 * err * err
                  - 0.2 * vz * vz
                  - 4.0 * roll * roll
                  - 0.05 * wx * wx
                  - 0.02 * float(a @ a))

        terminated = bool(abs(err) > 1.2 or abs(roll) > 1.0 or z <= 0.02)
        truncated = bool(self.t >= EPISODE_S)
        return self._obs(), float(reward), terminated, truncated, {}


def make_env(**kw):
    return FlapperHoverEnvV2(**kw)
