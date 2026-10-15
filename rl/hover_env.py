# -*- coding: utf-8 -*-
"""
hover_env.py — 扑翼悬停 Gymnasium 环境（周期平均动力学版）
============================================================================
设计动机（面试口径）：isaac_flapper_sim 的气动模型 FlapperAeroModel 是纯 NumPy、
零 Isaac 依赖的，因此可以脱离渲染引擎以 ~10kHz 等效速度 rollout —— 这正是
"内环映射用 RL/模仿学习来做"的先决条件（训练吞吐不被物理引擎卡住）。

动力学口径 = SparrowHawk 论文的 cycle-averaged 建模（重点思考2的素材）：
  每个控制周期（50Hz）内，在固定机体状态下对当前拍动整周期积分取平均力，
  再用平均力推进刚体。拍频波纹不进入刚体积分——这避免了"波纹-沉浮-流入
  角"的参数自激（纯 NumPy 逐步积分实证发散，属模型瞬时外推失真，已记录）。
  瞬时波纹的建模与辨识是题目"重点思考2"的下一步工作，不是本环境的目标。

受控通道（与 2026-09-18 台架辨识的物理边界一致）：
  高度通道：拍频 f ∈ [1.5, 3.5] Hz（L̄ = 1.95·f² N，辨识值）
  滚转通道：翼差动 dA ∈ [-0.25, 0.25] rad（静气悬停尾舵无效，不用）
  俯仰/偏航假设配平（需下洗/桨涡耦合建模，下一步）。

域随机化（reset 时）：升力系数缩放 s∈[0.7,1.3]、质量 ∈[0.63,0.77] kg ——
2026-09-20 夜间开发（215g/0.6m 口径重新标定）：质量域 0.215±10%；k_f 记载更正——0.6m 几何重标后旧 k_f=1.95（2.3m 口径）失效，对称波形悬停曲线 k≈0.041 N/Hz²（W/f_h²，f_h≈7.2Hz，docs/TRIM_SCAN_215g.md 附录二）。
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

import config.sim_config as cfg
from modules.aero_solver import FlapperAeroModel

DT_CTRL = 0.02                   # 控制周期 50Hz（内环典型值）
Z_REF = 1.5                      # 目标悬停高度 (m)
FREQ_MID = 0.5 * (cfg.MANUAL_FREQ_MIN + cfg.MANUAL_FREQ_MAX)   # 2.5 Hz
FREQ_HALF = 0.5 * (cfg.MANUAL_FREQ_MAX - cfg.MANUAL_FREQ_MIN)  # 1.0 Hz
FREQ_SLEW = 2.0                  # Hz/s 执行器速率限制（传动惯性）
DA_MAX = 0.25                    # rad，翼差动限幅（同 FlightControllerConfig）
A0 = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)          # 35° 拍幅
IXX = 0.0074                     # kg·m²（215g 质量比缩放 0.024×0.307，与 tools/test_flight_controller 取值来源相同）
WX_DAMP = 0.05                   # 滚转粘性阻尼 N·m·s
EPISODE_S = 10.0


class FlapperHoverEnv(gym.Env):
    """悬停+滚转稳定 2 通道环境。

    obs = [z, vz, roll, wx, freq_actual]（5 维）
    act = [freq_n, dA_n] ∈ [-1,1]²
    """

    metadata = {"render_modes": []}

    def __init__(self, randomize: bool = True, obs_noise: float = 0.0, seed=None):
        super().__init__()
        self.observation_space = spaces.Box(
            low=np.array([0.0, -8.0, -1.5, -15.0, 1.0], dtype=np.float32),
            high=np.array([3.0, 8.0, 1.5, 15.0, 4.0], dtype=np.float32))
        self.action_space = spaces.Box(-1.0, 1.0, shape=(2,), dtype=np.float32)
        self.randomize = randomize
        self.obs_noise = obs_noise
        self._rng = np.random.default_rng(seed)
        self.model = None
        self.mass = cfg.MASS
        self.t = 0.0
        self.freq_actual = 1.9
        self.state = np.zeros(4)   # z, vz, roll, wx

    # ---------------- 域随机化 ----------------
    def _randomize_dynamics(self):
        # 几何冻结契约（2026-09-20）：主线已切 SparrowHawk 0.6m 平台
        # （span 0.32/面积 0.109m²，config/archive 存有 2.3m 存档），
        # 本环境是 09-18 悬停辨识口径的 2.3m/700g Phoenix 悬停契约，
        # 显式传几何参数与 config 演进解耦
        self.model = FlapperAeroModel({
            "span": 1.15, "root_chord": 0.35, "tip_chord": 0.25,
            "n_strips": 14})
        # 运动学冻结契约（2026-09-20）：主线已切巡航配平运动学，
        # 本环境是 09-18 悬停辨识口径的静气悬停任务，冻结之
        for w in (self.model.wing_left, self.model.wing_right):
            w.split = 0.56
            w.pitch_lag_s = -0.02
            w.mean = 0.0
            w.alpha_set = math.radians(15.0)
            w.pitch_mode = "aero"
        if self.randomize:
            s = float(self._rng.uniform(0.7, 1.3))          # 升力系数不确定性
            self.model.cn_fit_amp *= s
            self.model.ct_fit_amp *= s
            self.mass = float(self._rng.uniform(0.194, 0.237))  # 215g±10%
        else:
            self.mass = cfg.MASS

    # ---------------- 周期平均力 ----------------
    def _cycle_mean_wrench(self, z, vz, roll, wx, freq, dA):
        """固定机体状态，对当前拍动整周期（含 SPLIT 非对称）平均 F/T。"""
        self.model.set_wing_command(frequency=freq)
        self.model.wing_left.amplitude = A0 + dA
        self.model.wing_right.amplitude = A0 - dA
        quat = np.array([math.cos(roll / 2), math.sin(roll / 2), 0.0, 0.0])
        v_lin = np.array([0.0, 0.0, vz])
        v_ang = np.array([wx, 0.0, 0.0])
        # 相位状态复位（2026-09-20）：主线把 WingKinematics 改为积分相位态，
        # 预览序列会拖动内部状态、破坏"整周期采样"假设。每批求均值前复位，
        # 恢复"按绝对时间无状态求值"的口径（对离线环境两语义等价）。
        for _w in (self.model.wing_left, self.model.wing_right):
            _w._t_prev = None
            _w._tau_state = 0.0
        n = 24                                  # 每周期 24 个采样点
        F_sum = np.zeros(3)
        T_sum = np.zeros(3)
        for i in range(n):
            F, T = self.model.compute_forces(np.array([0.0, 0.0, z]), quat,
                                             v_lin, v_ang,
                                             self.t + i / (n * freq))
            F_sum += F
            T_sum += T
        # 力矩限幅（飞行配置 2 N·m，作用于周期平均量）
        T_mean = T_sum / n
        t_norm = float(np.linalg.norm(T_mean))
        if t_norm > 2.0:
            T_mean = T_mean * (2.0 / t_norm)
        # 垂直速度的动量理论修正：爬升时诱导功率上升、升力下降，快速下降
        # （涡环态）同样掉升力。S&D 恒正 |C_N| 约定在大 |vz| 下给出向上递增
        # 升力（准定常外推失真，会自激），该因子把均值映射拉回物理方向。
        F_mean = F_sum / n
        F_mean = F_mean.copy()
        F_mean[2] *= 1.0 / (1.0 + (vz / 2.5) ** 2)
        return F_mean, T_mean

    # ---------------- gym 契约 ----------------
    def reset(self, *, seed=None, options=None):
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._randomize_dynamics()
        self.t = 0.0
        self.freq_actual = 1.9     # 从悬停配平拍频起动
        self.state = np.array([
            self._rng.uniform(0.9, 2.1),      # z
            self._rng.uniform(-0.6, 0.6),     # vz
            self._rng.uniform(-0.2, 0.2),     # roll
            self._rng.uniform(-0.4, 0.4),     # wx（有效包线内采样）
        ])
        return self._obs(), {}

    def _obs(self):
        o = np.concatenate([self.state, [self.freq_actual]]).astype(np.float32)
        if self.obs_noise > 0:
            o += self._rng.normal(0, self.obs_noise, size=5).astype(np.float32)
        return o

    def step(self, action):
        a = np.clip(np.asarray(action, dtype=float), -1.0, 1.0)
        freq_cmd = FREQ_MID + FREQ_HALF * a[0]
        dA = DA_MAX * a[1]
        # 执行器速率限制
        self.freq_actual += float(np.clip(freq_cmd - self.freq_actual,
                                          -FREQ_SLEW * DT_CTRL,
                                          FREQ_SLEW * DT_CTRL))
        freq = self.freq_actual

        z, vz, roll, wx = self.state
        F, T = self._cycle_mean_wrench(z, vz, roll, wx, freq, dA)

        # 刚体积分（50Hz 半隐式欧拉，周期平均力）
        vz += (F[2] / self.mass - 9.81) * DT_CTRL
        z += vz * DT_CTRL
        wx += (T[0] - WX_DAMP * wx) / IXX * DT_CTRL
        wx = float(np.clip(wx, -3.0, 3.0))   # 模型有效包线守卫：|wx|>3 区
                                             # 准定常流入角失真、阻尼反号
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
    return FlapperHoverEnv(**kw)
