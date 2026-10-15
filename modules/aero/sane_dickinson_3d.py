# -*- coding: utf-8 -*-
"""
sane_dickinson_3d.py — 真三维叶素后端（平行新增，不动现行生效代码）

与冻结几何版（sane_dickinson.FlapperAeroModel，I-014 前位于
modules/aero_solver.py）的三点区别：
  1. 叶素位置随拍动角扫掠：pos = r·(sinφ, −cosφ, 0)，与速度场
     v_flap = φ̇·r·(cosφ, sinφ, 0) 严格互为导数——冻结版力臂恒为
     (0, r, 0)，把扫掠位置的周期性俯仰/偏航耦合力矩整体置零。
  2. 攻角三维化：相对流速先投影到垂直于翼展的平面，侧向流动参与
     攻角（冻结版只取 atan2(vz, |vx|)，对侧滑失明）。
  3. 法向力方向三维化：沿 n̂×v̂_perp（垂直于投影流速）而非恒沿机体
     竖直轴；弦向朝向随扭转与拍动转动。
另有翼根安装偏置 (−0.07, 0, 0)（URDF 真值）进入力臂；反向来流自然
带符号（几何方向连续翻转），不需要冻结版的镜像延拓。

启用方式：config AERO_MODEL = "sane_dickinson_3d"（默认仍为
sane_dickinson，验收基线不动）。
"""
from __future__ import annotations

import numpy as np

from .sane_dickinson import FlapperAeroModel
import config.sim_config as cfg

# URDF 真值：翼根铰链位于重心后方 7 cm（冻结版取零，挂账项）
ROOT_OFFSET_3D = np.asarray(getattr(cfg.AeroModelConfig, "ROOT_OFFSET_3D",
                                    (-0.07, 0.0, 0.0)), dtype=np.float64)


class SaneDickinson3DModel(FlapperAeroModel):
    """真三维叶素模型：仅覆写 _wing_forces，其余（守卫、阻尼、回调、
    周期平均）全部继承冻结版。"""

    def _wing_forces(self, r_centers, chords_unused, wing_state,
                     v_body_at_hinge, omega_body):
        """镜像变换包装（第十二班终版）：右翼永远在左翼标准系里计算，
        力与力矩经镜像矩阵变换回机体系——从构造上保证左右翼响应严格
        镜像对称（首两版的右翼局部系手性反了，左右翼竖直力成对抵消，
        全场升力为负的根因）。
        镜像 M=diag(1,−1,1)（x-z 对称面）：p→Mp, F→MF, T=Σp×F→−M·T。
        角速度为轴矢量（2026-09-24 第十九班考古预警修正）：反射变换
        （det(M)=−1）下 ω 的标准系表示为 −M@ω（真矢量才是 M@ω）。
        原版此参数为死参数（ω×r 按 2026-09-19 裁决移除）故符号错误
        从未暴露；恢复 ω×r 耦合后若不修正，左右翼耦合速度同号、
        滚转/偏航耦合对消归零（变体 F 机制，实测严格零）。
        """
        sgn = 1.0 if (len(r_centers) and r_centers[0] >= 0) else -1.0
        if sgn > 0:
            out = self._wing_forces_canonical(r_centers, wing_state,
                                              v_body_at_hinge, omega_body)
            # 逐叶素缓存落左翼槽（标准系即体轴系原值）
            self._strip_cache_L = getattr(self, "_last_strip_raw", None)
            return out
        Mir = np.diag([1.0, -1.0, 1.0])
        F_loc, T_loc = self._wing_forces_canonical(np.abs(r_centers),
                                                   wing_state,
                                                   Mir @ v_body_at_hinge,
                                                   -(Mir @ omega_body))
        # 逐叶素缓存落右翼槽：站上力经镜像变换回机体系（与返回值同变换）
        raw = getattr(self, "_last_strip_raw", None)
        if raw is not None:
            self._strip_cache_R = dict(raw)
            self._strip_cache_R["dF"] = raw["dF"] * np.array([1.0, -1.0, 1.0])
        return Mir @ F_loc, -(Mir @ T_loc)

    def _wing_forces_canonical(self, r_centers, wing_state,
                               v_body_at_hinge, omega_body):
        """左翼标准系（r>0，翼梢向 −y）的真三维叶素力。"""
        n = len(r_centers)
        dr = self._dr
        phi = wing_state["phi"]
        alpha = wing_state["alpha"]
        phi_dot = wing_state["phi_dot"]
        alpha_dot = wing_state["alpha_dot"]
        phi_ddot = wing_state["phi_ddot"]

        sp, cp = np.sin(phi), np.cos(phi)
        # 扫掠几何（与速度场互为导数）：pos=r·(sinφ,−cosφ,0)，
        # v=φ̇·r·(cosφ,sinφ,0)——与冻结版速度场逐位一致
        n_hat = np.array([sp, -cp, 0.0])              # 翼展单位矢（根→梢）
        r_body = np.stack([r_centers * sp, -r_centers * cp,
                           np.zeros_like(r_centers)], axis=1) + ROOT_OFFSET_3D
        v_flap = np.stack([phi_dot * r_centers * cp,
                           phi_dot * r_centers * sp,
                           np.zeros_like(r_centers)], axis=1)
        c0 = np.array([0.0, 0.0, 1.0])
        chord0 = np.cross(c0, n_hat)                  # ĉ₀=ẑ×n̂（参考弦向）
        chord0 /= max(np.linalg.norm(chord0), 1e-9)
        m_hat = np.cross(n_hat, chord0)               # m̂=n̂×ĉ₀
        chord = np.cos(alpha) * chord0 + np.sin(alpha) * m_hat

        # ω×r 叶素耦合速度（2026-09-24 第二十班恢复，用户裁决一）：
        # 机体转动使叶素获得附加来流 np.cross(ω, r_body)。2026-09-19
        # 曾按"大角速度反号自激"裁决移除并代以线性阻尼 WING_ROT_DAMP；
        # 第十八班辨识（−0.0091）与第十九班考古（当年代码换今日几何
        # 复算 −0.00833，差 8.5%）两次独立测量互证后恢复。当年反号=
        # 旧几何悬停工况下 |C_N| 绝对值折叠假象（见
        # docs/力律修补登记_法向力系数绝对值适用边界.md），今日几何
        # 巡航工况 |ω|≤10 实测全程恢复性。ω=0 时本项严格为零，
        # 静态结论（配平/包线/通道表）与恢复前逐位一致。
        v_om = np.cross(np.asarray(omega_body, dtype=float), r_body)
        v_rel = -(v_body_at_hinge + v_flap + v_om)
        v_rel_mag = np.linalg.norm(v_rel, axis=1)
        scale = np.where(v_rel_mag > self.max_wing_vel,
                         self.max_wing_vel / np.maximum(v_rel_mag, 1e-9), 1.0)
        v_rel = v_rel * scale[:, None]
        v_rel_mag = np.linalg.norm(v_rel, axis=1)

        # 攻角：投影到垂直于翼展的平面，对俯仰后弦取角
        v_span = (v_rel @ n_hat)[:, None] * n_hat[None, :]
        v_perp = v_rel - v_span
        v_perp_mag = np.linalg.norm(v_perp, axis=1)
        vhat_perp = v_perp / np.maximum(v_perp_mag[:, None], 1e-9)
        ncross = np.cross(n_hat, chord)
        dot_c = (-v_perp) @ chord
        dot_m = (-v_perp) @ ncross
        alpha_eff = np.arctan2(dot_m, dot_c)

        q_dyn = 0.5 * self.rho * v_rel_mag ** 2
        C_N = self._C_N(alpha_eff)
        C_T = self._C_T(alpha_eff)

        # 法向力沿 v̂_perp×n̂（两行程均指向 +z，翻转俯仰保证同向升力）
        nF = np.cross(vhat_perp, n_hat[None, :])
        dF_normal_mag = q_dyn * np.abs(C_N) * self._chords * dr
        dF_trans = dF_normal_mag[:, None] * nF

        dF_tangent_mag = q_dyn * C_T * self._chords * dr
        dF_trans += dF_tangent_mag[:, None] * vhat_perp

        dF_rot = np.zeros((n, 3), dtype=np.float64)
        if self.enable_rotational:
            dF_rot = (self.c_rot * self.rho * self._chords * v_perp_mag
                      * alpha_dot * dr)[:, None] * m_hat[None, :]

        dF_added = np.zeros((n, 3), dtype=np.float64)
        if self.enable_added_mass:
            v_added = self._strip_areas * self.added_mass_coeff
            a_flap = np.stack(
                [phi_ddot * r_centers * cp - phi_dot ** 2 * r_centers * sp,
                 phi_ddot * r_centers * sp + phi_dot ** 2 * r_centers * cp,
                 np.zeros_like(r_centers)], axis=1)
            dF_added = -self.rho * v_added[:, None] * a_flap

        dF = dF_trans + dF_rot + dF_added
        F_wing_body = np.sum(dF, axis=0)
        T_wing_body = np.sum(np.cross(r_body, dF), axis=0)
        self._aeff_stats = (float(np.mean(alpha_eff)),
                            float(np.max(np.abs(alpha_eff))),
                            float(np.mean(np.abs(alpha_eff) > np.pi / 4.0)))
        # 逐叶素原始数据缓存（2026-09-28 用户令完整记录）：本函数每次
        # 求值把十四站的行程位姿与站上力存入 _last_strip_raw（标准系
        # 原值）；由 _wing_forces 包装层分左右翼落槽。仅记录用途，
        # 不参与任何力计算路径，零行为变化。
        self._last_strip_raw = {
            "phi": float(phi), "alpha": float(alpha),
            "r": np.asarray(r_centers, dtype=float),
            "aeff": np.asarray(alpha_eff, dtype=float),
            "dF": np.asarray(dF, dtype=float)}
        return F_wing_body, T_wing_body
