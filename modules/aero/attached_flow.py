"""
attached_flow.py — 路线A：高Re大展弦比附着流环量模型（零 Isaac 依赖）
=======================================================================
目标流型：惯性力主导、湍流涡脱不占主导、AR≥5、附着流环量升力。

物理组成：
  1. 定常环量升力（升力线理论 + 有限AR修正）
       每叶素 L' = ρ·U_n·Γ
       Γ_ss = π·b·U_n·α_eff·(a_eff/2π)    [a_eff = a0/(1+a0/(π·AR))]
  2. Wagner 失速延迟（时域 Duhamel 积分）
       环量不是瞬时建立：Γ(t) = Γ_ss·Φ(τ)
       Φ(τ) ≈ 1 - A1·e^(-T1·τ) - A2·e^(-T2·τ)  (Jones 两项)
       Duhamel 实现：每叶素 2 个一阶滞后状态
         x_i' = (α_eff - x_i)/Ti*，Ti* = Ti·b/U_n
         C_l,eff = a_eff·(α_eff - A1·(α_eff-x1) - A2·(α_eff-x2))
  3. 非环量惯性力（薄翼附加质量）
       dF_nc = ρ·π·b²·a_n per unit span
  4. 阻力：型阻 CD0 + 升致阻力（有限AR诱导）
       C_Di = C_L²/(π·e·AR)
  5. 失速保护：α_eff 硬限幅在 α_stall 内（附着流假设的边界）

文献：Jones (1940) Wagner 逼近；Bisplinghoff/Ashley《Aeroelasticity》Ch.5；
      Anderson《Fundamentals of Aerodynamics》升力线理论。
"""

from __future__ import annotations

import numpy as np
from typing import Tuple

from config import sim_config as cfg
from .common import AeroModelBase
from .kinematics import WingKinematics


class AttachedFlowModel(AeroModelBase):
    """路线A：附着流环量主导模型。"""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        p = params or {}
        a = cfg.AttachedFlowConfig
        h = cfg.HighARWingConfig

        self.a1 = float(p.get("wagner_a1", a.WAGNER_A1))
        self.t1 = float(p.get("wagner_t1", a.WAGNER_T1))
        self.a2 = float(p.get("wagner_a2", a.WAGNER_A2))
        self.t2 = float(p.get("wagner_t2", a.WAGNER_T2))
        self.enable_wagner = bool(p.get("enable_wagner", a.ENABLE_WAGNER))
        self.enable_noncirc = bool(p.get("enable_noncirc", a.ENABLE_NONCIRC))
        self.alpha_stall = np.radians(float(p.get("alpha_stall_deg", a.ALPHA_STALL_DEG)))
        self.max_wing_vel = float(p.get("max_wing_vel", a.MAX_WING_VEL))

        self.a0 = float(p.get("lift_slope_2d", h.LIFT_SLOPE_2D))
        self.e_oswald = float(p.get("oswald_efficiency", h.OSWALD_EFFICIENCY))
        self.cd0 = float(p.get("profile_drag", h.PROFILE_DRAG_COEFF))

        # 有限AR有效升力线斜率（整机一次算）
        ar = self.aspect_ratio
        self.a_eff = self.a0 / (1.0 + self.a0 / (np.pi * max(ar, 0.5)))

        # 运动学（aero 俯仰模式）
        self.wing_left = WingKinematics(pitch_mode="aero")
        self.wing_right = WingKinematics(pitch_mode="aero")
        self.phase_offset_lr = 0.0

        # Wagner Duhamel 滞后状态：每翼每叶素 2 个
        n = self.n_strips
        self._x1 = {"L": np.zeros(n), "R": np.zeros(n)}
        self._x2 = {"L": np.zeros(n), "R": np.zeros(n)}

    # ------------------------------------------------------------------
    def _wing_forces(self, r_centers, wing, wing_state, v_body, omega_body) -> Tuple[np.ndarray, np.ndarray]:
        r_body, v_rel, v_rel_mag = self._strip_geometry_and_velocity(
            r_centers, wing_state, v_body, omega_body
        )
        n = len(r_centers)
        b = self._half_chords                     # (n,) 半弦长
        dr = self._dr
        # 失速钳位（连贯）：翼面俯仰命令超失速角时，钳角度并按饱和比例
        # 缩放 α̈，使环量/方向/附加质量三者的"有效俯仰"一致
        alpha_raw = wing_state["alpha"]
        if abs(alpha_raw) > self.alpha_stall:
            sat = self.alpha_stall / max(abs(alpha_raw), 1e-6)
            wing_state = dict(wing_state)
            wing_state["alpha"] = np.copysign(self.alpha_stall, alpha_raw)
            wing_state["alpha_ddot"] *= sat
        alpha = wing_state["alpha"]

        # ── 有效攻角：翼面俯仰 + 来流偏角 ──
        # 来流相对翼面：法向分量 v_rel_z，弦向 v_rel_x
        v_chord = v_rel[:, 0]
        v_norm = v_rel[:, 2]
        # 有效攻角 = 翼面俯仰 + 来流几何偏角
        # （alpha 已含运动学的主动俯仰；来流偏角由机身运动产生）
        inflow = np.arctan2(v_norm, np.maximum(np.abs(v_chord), 1e-6))
        alpha_eff = alpha + inflow

        # ── 失速保护：附着流假设边界（硬限幅）──
        alpha_eff = np.clip(alpha_eff, -self.alpha_stall, self.alpha_stall)

        # ── Wagner Duhamel 滞后（每叶素两个状态）──
        key = "L" if r_centers[0] >= 0 else "R"
        x1 = self._x1[key]
        x2 = self._x2[key]

        # 实时间常数 T_i* = T_i·2b/U（半弦无量纲 → 实时间）
        U_n = np.maximum(v_rel_mag, 0.05)
        T1 = self.t1 * 2.0 * b / U_n
        T2 = self.t2 * 2.0 * b / U_n
        dt_est = 1.0 / 120.0
        # 精确指数离散化（一阶线性 ODE 解，无条件稳定；
        # T1 可短于 dt，显式欧拉会发散 → 必须用此形式）
        decay1 = np.exp(-dt_est / np.maximum(T1, 1e-6))
        decay2 = np.exp(-dt_est / np.maximum(T2, 1e-6))
        x1 *= decay1
        x1 += (1.0 - decay1) * alpha_eff
        x2 *= decay2
        x2 += (1.0 - decay2) * alpha_eff

        if self.enable_wagner:
            alpha_wagner = alpha_eff - self.a1 * (alpha_eff - x1) - self.a2 * (alpha_eff - x2)
        else:
            alpha_wagner = alpha_eff

        # ── 环量升力（升力线 + AR 修正）──
        q = 0.5 * self.rho * v_rel_mag**2          # (n,) 动压
        c_l = self.a_eff * alpha_wagner             # 有效升力系数
        # 升力方向：垂直于来流（在法向-弦向平面内旋转一个诱导角）
        # 简化：升力沿翼面法向（攻角小时差异 O(α²) 可忽略）
        dF_lift = q * c_l * self._chords * dr       # (n,) 法向力大小（带符号）

        dF = np.zeros((n, 3), dtype=np.float64)
        # 法向（近似翼面法向 = (sin α, 0, cos α)）
        dF[:, 0] = dF_lift * np.sin(alpha)
        dF[:, 2] = dF_lift * np.cos(alpha)

        # ── 非环量惯性力（薄翼附加质量）：F = ρπb²·a_n ──
        if self.enable_noncirc:
            alpha_ddot = wing_state["alpha_ddot"]
            phi_ddot = wing_state["phi_ddot"]
            r_abs = np.abs(r_centers)
            # 翼面法向加速度 ≈ α̈·b + φ̈·r（俯仰+拍动产生的法向加速）
            a_n = alpha_ddot * b + phi_ddot * r_abs * np.cos(wing_state["phi"])
            dF_nc = self.rho * np.pi * b**2 * a_n * dr
            dF[:, 2] += dF_nc

        # ── 阻力：型阻 + 升致阻力 ──
        c_di = c_l**2 / (np.pi * self.e_oswald * max(self.aspect_ratio, 0.5))
        c_d = self.cd0 + c_di
        dF_drag = q * c_d * self._chords * dr
        v_dir = v_rel / np.maximum(v_rel_mag[:, None], 1e-9)
        dF += dF_drag[:, None] * v_dir

        F_wing = np.sum(dF, axis=0)
        T_wing = np.sum(np.cross(r_body, dF), axis=0)
        return F_wing, T_wing
