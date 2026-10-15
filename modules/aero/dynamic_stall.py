"""
dynamic_stall.py — 路线B：受控动态失速模型（零 Isaac 依赖）
=============================================================
目标流型：大攻角受控 LEV——涡是受控工具而非混乱脱落。
Leishman-Beddoes (1989) 简化版，三状态 per 翼：

  状态1 分离点 f ∈ [0,1]
    静态曲线：f_ss(α) = 1 - exp((α - α_ds)/S)   [α<α_ds 时→1 全附着]
    动态滞后：df/dt = (f_ss - f)/T_f
  状态2 涡环量 C_v（LEV 强度）
    触发：α_eff > α_ds
    增长：dC_v/dt = (C_n_target - C_v)/T_v，C_n_target = C_n,α·α_eff
  状态3 涡脱计时 τ（无量纲涡寿命）
    触发后累积：dτ/dt = U/(2b)
    超过 τ_vl：C_v 指数衰减（受控脱落），τ 清零

  法向力合成（LB 经典形式）：
    C_n = f^0.5·C_n,att + (1-f^0.5)·C_n,fs + C_v
    C_n,att = C_n,α·α（附着流线性）
    C_n,fs  = C_n,α·α·(1+sign(α)·A_fs)（全分离平板近似）

物理意义：α 在失速角内 → 纯附着流（路线A 行为）；
         α 越过失速角 → LEV 环量累积增升（延迟失速红利）；
         涡寿命到期 → C_v 衰减（涡脱，升力回落）。
         通过控制 α 的相位/幅度，让 LEV 在脱落前被"用掉"——受控涡脱。

文献：Leishman & Beddoes 1989, "A Semi-Empirical Model for Dynamic Stall"；
      Leishman《Principles of Helicopter Aerodynamics》Ch.9。
"""

from __future__ import annotations

import numpy as np
from typing import Tuple

from config import sim_config as cfg
from .common import AeroModelBase
from .kinematics import WingKinematics


class DynamicStallModel(AeroModelBase):
    """路线B：LB 简化动态失速模型。"""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        p = params or {}
        d = cfg.DynamicStallConfig
        h = cfg.HighARWingConfig

        self.cn_alpha = float(p.get("cn_alpha", d.CN_ALPHA))
        self.alpha_ds = np.radians(float(p.get("alpha_ds_deg", d.ALPHA_DS_DEG)))
        self.sep_s = np.radians(float(p.get("separation_s_deg", d.SEPARATION_S_DEG)))
        self.tau_f = float(p.get("tau_f", d.TAU_F))
        self.tau_v = float(p.get("tau_v", d.TAU_V))
        self.tau_vl = float(p.get("tau_vl", d.TAU_VL))
        self.vortex_decay = float(p.get("vortex_decay", d.VORTEX_DECAY))
        self.enable_vortex = bool(p.get("enable_vortex", d.ENABLE_VORTEX))
        self.max_wing_vel = float(p.get("max_wing_vel", d.MAX_WING_VEL))

        self.a0 = float(p.get("lift_slope_2d", h.LIFT_SLOPE_2D))
        self.e_oswald = float(p.get("oswald_efficiency", h.OSWALD_EFFICIENCY))
        self.cd0 = float(p.get("profile_drag", h.PROFILE_DRAG_COEFF))
        ar = self.aspect_ratio
        self.a_eff = self.a0 / (1.0 + self.a0 / (np.pi * max(ar, 0.5)))

        # aero 俯仰模式，但允许越过失速角（受控涡）
        self.wing_left = WingKinematics(pitch_mode="aero", alpha_set_deg=20.0)
        self.wing_right = WingKinematics(pitch_mode="aero", alpha_set_deg=20.0)
        self.phase_offset_lr = 0.0

        # 每翼每叶素状态：分离点 f、涡环量 Cv、涡脱计时 tau
        n = self.n_strips
        self._f = {"L": np.ones(n), "R": np.ones(n)}      # 初始全附着
        self._Cv = {"L": np.zeros(n), "R": np.zeros(n)}
        self._tau = {"L": np.zeros(n), "R": np.zeros(n)}  # 涡寿命累积
        self._vortex_active = {"L": np.zeros(n, dtype=bool), "R": np.zeros(n, dtype=bool)}

    # ------------------------------------------------------------------
    def _static_separation(self, alpha: np.ndarray) -> np.ndarray:
        """静态分离点曲线 f_ss(α)。α<α_ds → ~1（附着）；α>α_ds → 快速降向 0。"""
        return np.clip(1.0 - np.exp((alpha - self.alpha_ds) / self.sep_s), 0.0, 1.0)

    def _wing_forces(self, r_centers, wing, wing_state, v_body, omega_body) -> Tuple[np.ndarray, np.ndarray]:
        r_body, v_rel, v_rel_mag = self._strip_geometry_and_velocity(
            r_centers, wing_state, v_body, omega_body
        )
        n = len(r_centers)
        b = self._half_chords
        dr = self._dr
        alpha = wing_state["alpha"]

        v_chord = v_rel[:, 0]
        v_norm = v_rel[:, 2]
        inflow = np.arctan2(v_norm, np.maximum(np.abs(v_chord), 1e-6))
        alpha_eff = alpha + inflow
        # 动态失速模型允许越过失速角（这是它的意义），但限幅防爆
        alpha_eff = np.clip(alpha_eff, -np.radians(45.0), np.radians(45.0))

        key = "L" if r_centers[0] >= 0 else "R"
        f = self._f[key]
        Cv = self._Cv[key]
        tau = self._tau[key]
        active = self._vortex_active[key]

        # 无量纲时间步：dτ = U/(2b)·dt（LB 用半弦无量纲时间）
        U_n = np.maximum(v_rel_mag, 0.05)
        dt = 1.0 / 120.0
        ds = U_n / (2.0 * b) * dt     # (n,) 本步无量纲时间增量

        # ── 状态1：分离点演化 ──
        f_ss = self._static_separation(np.abs(alpha_eff))
        # 滞后时间常数 T_f（无量纲）
        f += (f_ss - f) * ds / max(self.tau_f, 1e-3)
        f = np.clip(f, 0.0, 1.0)

        # ── 状态2/3：涡环量累积与脱落 ──
        if self.enable_vortex:
            # 触发：攻角越过失速角
            newly = (np.abs(alpha_eff) > self.alpha_ds) & (~active)
            active |= newly
            tau[newly] = 0.0

            # 增长期
            growing = active & (tau < self.tau_vl)
            cn_target = self.cn_alpha * np.abs(alpha_eff)
            Cv[growing] += (cn_target[growing] - Cv[growing]) * ds[growing] / max(self.tau_v, 1e-3)
            tau[growing] += ds[growing]

            # 脱落期：涡寿命到期，Cv 指数衰减
            shedding = active & (tau >= self.tau_vl)
            Cv[shedding] *= np.exp(-self.vortex_decay * ds[shedding])
            # Cv 衰减到很小后关闭涡状态，允许下个半周期重新触发
            done = shedding & (np.abs(Cv) < 0.05 * np.maximum(cn_target, 1e-3))
            active[done] = False
            tau[done] = 0.0

        # ── 法向力合成（LB）──
        # 附着流贡献（f^0.5 权重）
        cn_att = self.cn_alpha * alpha_eff
        # 全分离贡献（平板近似：斜率降为 π·sin(2α)/2 量级）
        cn_fs = np.sign(alpha_eff) * 2.0 * np.sin(2.0 * np.abs(alpha_eff)) / np.pi * (np.pi / 2)
        cn_total = f**0.5 * cn_att + (1.0 - f**0.5) * cn_fs + Cv * np.sign(alpha_eff)

        q = 0.5 * self.rho * v_rel_mag**2
        dF_n = q * cn_total * self._chords * dr    # (n,) 法向力（带符号）

        dF = np.zeros((n, 3), dtype=np.float64)
        dF[:, 0] = dF_n * np.sin(alpha_eff)
        dF[:, 2] = dF_n * np.cos(alpha_eff)

        # ── 阻力：型阻 + 升致阻力 + 分离压阻 ──
        c_l_att = self.a_eff * alpha_eff
        c_di = c_l_att**2 / (np.pi * self.e_oswald * max(self.aspect_ratio, 0.5))
        # 分离引起的压差阻力 ∝ (1-f)
        c_d_sep = (1.0 - f) * 1.2 * np.sin(np.abs(alpha_eff))**2
        c_d = self.cd0 + c_di + c_d_sep
        dF_drag = q * c_d * self._chords * dr
        v_dir = v_rel / np.maximum(v_rel_mag[:, None], 1e-9)
        dF += dF_drag[:, None] * v_dir

        F_wing = np.sum(dF, axis=0)
        T_wing = np.sum(np.cross(r_body, dF), axis=0)
        return F_wing, T_wing
