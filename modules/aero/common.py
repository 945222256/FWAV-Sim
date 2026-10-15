"""
common.py — 扑翼气动模型公共层（零 Isaac 依赖）
================================================
被三流型模型共用：
  • 坐标辅助（四元数→旋转矩阵、四元数→欧拉）
  • 叶素网格构建（沿展向离散）
  • AeroModelBase 基类：统一 compute_forces 契约 + 每叶素状态读取
所有模型必须保持本文件级别的纯度：仅 NumPy + config。
"""

from __future__ import annotations

import numpy as np
from typing import Tuple

from config import sim_config as cfg


# ============================================================
# 坐标辅助
# ============================================================

def quat_to_rotation_matrix(quat: np.ndarray) -> np.ndarray:
    """四元数 [w,x,y,z] → 3×3 旋转矩阵 (body→world)。"""
    w, x, y, z = float(quat[0]), float(quat[1]), float(quat[2]), float(quat[3])
    return np.array([
        [1 - 2*(y*y + z*z),     2*(x*y - w*z),       2*(x*z + w*y)],
        [2*(x*y + w*z),         1 - 2*(x*x + z*z),   2*(y*z - w*x)],
        [2*(x*z - w*y),         2*(y*z + w*x),       1 - 2*(x*x + y*y)],
    ], dtype=np.float64)


def quat_to_euler(quat: np.ndarray) -> Tuple[float, float, float]:
    """四元数 [w,x,y,z] → Euler (roll, pitch, yaw)，rad，ZYX 顺序。"""
    w, x, y, z = float(quat[0]), float(quat[1]), float(quat[2]), float(quat[3])
    roll = np.arctan2(2.0*(w*x + y*z), 1.0 - 2.0*(x*x + y*y))
    sinp = np.clip(2.0*(w*y - z*x), -1.0, 1.0)
    pitch = np.arcsin(sinp)
    yaw = np.arctan2(2.0*(w*z + x*y), 1.0 - 2.0*(y*y + z*z))
    return roll, pitch, yaw


def body_to_world(vec_body: np.ndarray, R: np.ndarray) -> np.ndarray:
    """体坐标系向量 → 世界坐标系。（I-014 自 modules/aero_solver.py 原文迁入）"""
    return R @ np.asarray(vec_body, dtype=np.float64)


def euler_to_quat(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Euler (roll, pitch, yaw) rad → 四元数 [w,x,y,z]，ZYX 顺序。（I-014 原文迁入）"""
    cr, sr = np.cos(roll / 2), np.sin(roll / 2)
    cp, sp = np.cos(pitch / 2), np.sin(pitch / 2)
    cy, sy = np.cos(yaw / 2), np.sin(yaw / 2)
    return np.array([cr*cp*cy + sr*sp*sy,
                     sr*cp*cy - cr*sp*sy,
                     cr*sp*cy + sr*cp*sy,
                     cr*cp*sy - sr*sp*cy], dtype=np.float64)


# ============================================================
# 叶素网格 + 模型基类
# ============================================================

class AeroModelBase:
    """
    三流型模型共用基类。

    契约（与 USV FossenUSVModel 同级别的独立性约束）：
      • compute_forces(pos, quat, v_lin, v_ang, t) → (F_world, T_world)
      • 叶素网格在 __init__ 预计算
      • 子类实现 _wing_forces(...)（单翼叶素积分）
    """

    def __init__(self, params: dict | None = None):
        p = params or {}
        self.rho = float(p.get("rho_air", cfg.AeroModelConfig.RHO_AIR))
        self.max_wing_vel = float(p.get("max_wing_vel", 60.0))

        # 翼几何
        g = cfg.WingGeometryConfig
        self.span = float(p.get("span", g.SPAN))
        self.root_chord = float(p.get("root_chord", g.ROOT_CHORD))
        self.tip_chord = float(p.get("tip_chord", g.TIP_CHORD))
        self.n_strips = int(p.get("n_strips", g.N_STRIPS))
        self.root_offset = np.asarray(p.get("root_offset", g.ROOT_OFFSET), dtype=np.float64)

        # 有限展弦比（升力线修正用）
        self.wing_area_total_pair = 2.0 * (
            0.5 * (self.root_chord + self.tip_chord) * self.span
        )   # 双翼面积
        b_total = 2.0 * self.span
        self.aspect_ratio = b_total**2 / max(self.wing_area_total_pair, 1e-6)

        self._build_strips()

        # 状态缓存（诊断/控制器读）
        self.current_F_world = np.zeros(3)
        self.current_T_world = np.zeros(3)
        self.current_F_body = np.zeros(3)
        self.current_T_body = np.zeros(3)

    def _build_strips(self) -> None:
        """沿展向构建叶素：中心半径 r、弦长 c（梯形翼线性收缩）。"""
        dr = self.span / self.n_strips
        r = (np.arange(self.n_strips) + 0.5) * dr
        c = self.root_chord + (self.tip_chord - self.root_chord) * (r / self.span)
        self._r_centers = r
        self._chords = c
        self._dr = dr
        self._strip_areas = c * dr
        self._half_chords = c * 0.5
        self.wing_area = float(np.sum(self._strip_areas))   # 单翼

    # ------------------------------------------------------------------
    # 叶素级公共量（子类在 _wing_forces 里用）
    # ------------------------------------------------------------------
    def _strip_geometry_and_velocity(
        self,
        r_centers: np.ndarray,
        wing_state: dict,
        v_body: np.ndarray,
        omega_body: np.ndarray,
    ) -> tuple:
        """返回 (r_body, v_rel, v_rel_mag)。
        r_body:(n,3) 叶素体坐标位置（左右翼由 r_centers 符号区分）
        v_rel: (n,3) 气流相对翼面速度 = -(机体诱导 + 拍动诱导)
        """
        n = len(r_centers)
        r_abs = np.abs(r_centers)
        phi = wing_state["phi"]
        phi_dot = wing_state["phi_dot"]

        r_body = np.zeros((n, 3), dtype=np.float64)
        r_body[:, 1] = r_centers
        r_body += self.root_offset

        v_body_induced = v_body + np.cross(omega_body, r_body)

        # 拍动速度：翼在垂直于展向的平面内摆动。
        # 悬停型（拍动面水平）取水平摆动；前飞型由 set_stroke_plane 的
        # stroke_axis 决定摆动方向（子类可覆盖）。
        v_flap = self._flap_velocity(r_abs, phi, phi_dot)

        v_wing = v_body_induced + v_flap
        v_rel = -v_wing

        v_rel_mag = np.linalg.norm(v_rel, axis=1)
        scale = np.where(v_rel_mag > self.max_wing_vel,
                         self.max_wing_vel / np.maximum(v_rel_mag, 1e-9), 1.0)
        v_rel = v_rel * scale[:, None]
        v_rel_mag = np.linalg.norm(v_rel, axis=1)
        return r_body, v_rel, v_rel_mag

    def _flap_velocity(self, r_abs: np.ndarray, phi: float, phi_dot: float) -> np.ndarray:
        """拍动诱导速度（水平摆动，与既有模型一致）。子类可覆盖。"""
        v = np.zeros((len(r_abs), 3), dtype=np.float64)
        v[:, 0] = phi_dot * r_abs * np.cos(phi)
        v[:, 1] = phi_dot * r_abs * np.sin(phi)
        return v

    # ------------------------------------------------------------------
    # 主入口（子类不应覆盖；模板方法）
    # ------------------------------------------------------------------
    def compute_forces(
        self,
        pos_world: np.ndarray,
        quat_world: np.ndarray,
        vel_linear: np.ndarray,
        vel_angular: np.ndarray,
        t: float,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """世界系总气动力/力矩。子类通过 _wing_forces 提供物理。"""
        R_bw = quat_to_rotation_matrix(quat_world)
        R_wb = R_bw.T
        v_body = R_wb @ np.asarray(vel_linear, dtype=np.float64)
        omega_body = R_wb @ np.asarray(vel_angular, dtype=np.float64)

        F_L, T_L = self._wing_forces(
            +self._r_centers.copy(), self.wing_left,
            self.wing_left.evaluate(t, 0.0), v_body, omega_body,
        )
        F_R, T_R = self._wing_forces(
            -self._r_centers.copy(), self.wing_right,
            self.wing_right.evaluate(t, self.phase_offset_lr), v_body, omega_body,
        )

        F_body = F_L + F_R
        T_body = T_L + T_R

        F_world = R_bw @ F_body
        T_world = R_bw @ T_body

        self.current_F_body = F_body
        self.current_T_body = T_body
        self.current_F_world = F_world
        self.current_T_world = T_world
        return F_world, T_world

    # 子类契约：wing 是 WingKinematics 实例（持有运动学参数和每翼状态）
    def _wing_forces(self, r_centers, wing, wing_state, v_body, omega_body):
        raise NotImplementedError

    def set_wing_command(self, **kwargs) -> None:
        """运行时调拍动参数（默认实现转发到两翼运动学）。"""
        for wing in (self.wing_left, self.wing_right):
            if "frequency" in kwargs and kwargs["frequency"] is not None:
                wing.set_frequency(kwargs["frequency"])
            if kwargs.get("amplitude_deg") is not None:
                wing.amplitude = np.radians(float(kwargs["amplitude_deg"]))
            if kwargs.get("pitch_bias_deg") is not None:
                wing.pitch_bias = np.radians(float(kwargs["pitch_bias_deg"]))
            if kwargs.get("pitch_amplitude_deg") is not None:
                wing.pitch_amplitude = np.radians(float(kwargs["pitch_amplitude_deg"]))
        if kwargs.get("phase_offset_lr_deg") is not None:
            self.phase_offset_lr = np.radians(float(kwargs["phase_offset_lr_deg"]))
