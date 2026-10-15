"""
sane_dickinson.py — 路线C：Sane & Dickinson (2002) 准定常昆虫式模型
=====================================================================
本文件是路线C 两个模型类的唯一实现地：
  • SaneDickinsonModel  — 早期版（AeroModelBase 子类，仅测试与登记表可达）
  • FlapperAeroModel    — 冻结生产版（I-014 自 modules/aero_solver.py
                          原文迁入，物理逐位不变；sane_dickinson_3d 的
                          基类，modules/aero_solver.py 经包导入再导出）
适用流型：低 Re、小展弦比（AR~1-2）、LEV 主导的蜂鸟/昆虫区间。

三分量：平动力（Robofly 极曲线）+ 旋转力（∝α̇）+ 附加质量力（∝φ̈）。
翻转俯仰（α 与 φ̇ 同号）使上下半拍升力同向。
"""

from __future__ import annotations

import numpy as np
from typing import Optional, Tuple

from config import sim_config as cfg
from .common import AeroModelBase, quat_to_rotation_matrix
from .kinematics import WingKinematics


class SaneDickinsonModel(AeroModelBase):
    """路线C：Sane & Dickinson 2002 三分量准定常模型（昆虫流型）。"""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        p = params or {}
        a = cfg.AeroModelConfig

        self.cn_fit_amp = float(p.get("cn_fit_amp", a.CN_FIT_AMP))
        self.cn_fit_phase = np.radians(float(p.get("cn_fit_phase_deg", a.CN_FIT_PHASE_DEG)))
        self.ct_fit_amp = float(p.get("ct_fit_amp", a.CT_FIT_AMP))
        self.ct_fit_offset = float(p.get("ct_fit_offset", a.CT_FIT_OFFSET))
        self.c_rot = float(p.get("c_rot", a.C_ROT))
        self.added_mass_coeff = float(p.get("added_mass_coeff", a.ADDED_MASS_COEFF))
        self.max_wing_vel = float(p.get("max_wing_vel", a.MAX_WING_VEL))

        self.enable_translational = bool(p.get("enable_translational", a.ENABLE_TRANSLATIONAL))
        self.enable_rotational = bool(p.get("enable_rotational", a.ENABLE_ROTATIONAL))
        self.enable_added_mass = bool(p.get("enable_added_mass", a.ENABLE_ADDED_MASS))

        # 昆虫流型用 flip 俯仰 + 高频小振幅默认
        k = cfg.FlapKinematicsConfig
        self.wing_left = WingKinematics(
            frequency=k.FREQUENCY if k.PITCH_MODE == "flip" else 25.0,
            amplitude_deg=k.AMPLITUDE if k.PITCH_MODE == "flip" else 35.0,
            pitch_mode="flip",
        )
        self.wing_right = WingKinematics(
            frequency=k.FREQUENCY if k.PITCH_MODE == "flip" else 25.0,
            amplitude_deg=k.AMPLITUDE if k.PITCH_MODE == "flip" else 35.0,
            pitch_mode="flip",
        )
        self.phase_offset_lr = np.radians(float(p.get("phase_offset_lr", k.PHASE_OFFSET_LR)))

    def _C_N(self, alpha_rad):
        return self.cn_fit_amp * np.sin(2.0 * alpha_rad + self.cn_fit_phase)

    def _C_T(self, alpha_rad):
        return self.ct_fit_amp * np.cos(2.0 * alpha_rad + self.cn_fit_phase) - self.ct_fit_offset

    def _wing_forces(self, r_centers, wing, wing_state, v_body, omega_body) -> Tuple[np.ndarray, np.ndarray]:
        r_body, v_rel, v_rel_mag = self._strip_geometry_and_velocity(
            r_centers, wing_state, v_body, omega_body
        )
        n = len(r_centers)
        dr = self._dr
        r_abs = np.abs(r_centers)
        alpha = wing_state["alpha"]
        alpha_dot = wing_state["alpha_dot"]
        phi_ddot = wing_state["phi_ddot"]

        v_chord = v_rel[:, 0]
        v_norm = v_rel[:, 2]
        inflow = np.arctan2(v_norm, np.maximum(np.abs(v_chord), 1e-6))
        alpha_eff = alpha + inflow

        q = 0.5 * self.rho * v_rel_mag**2
        C_N = self._C_N(alpha_eff)
        C_T = self._C_T(alpha_eff)

        dF_trans = np.zeros((n, 3), dtype=np.float64)
        if self.enable_translational:
            dF_n = q * np.abs(C_N) * self._chords * dr
            dF_trans[:, 2] = dF_n * np.cos(alpha_eff)
            dF_t = q * C_T * self._chords * dr
            v_dir = v_rel / np.maximum(v_rel_mag[:, None], 1e-9)
            dF_trans += dF_t[:, None] * v_dir

        dF_rot = np.zeros((n, 3), dtype=np.float64)
        if self.enable_rotational:
            dF_rot[:, 2] = self.c_rot * self.rho * self._chords * v_rel_mag * alpha_dot * dr

        dF_added = np.zeros((n, 3), dtype=np.float64)
        if self.enable_added_mass:
            v_added = self._strip_areas * self.added_mass_coeff
            a_flap = phi_ddot * r_abs
            dF_added[:, 0] = -self.rho * v_added * a_flap * np.cos(wing_state["phi"])
            dF_added[:, 1] = -self.rho * v_added * a_flap * np.sin(wing_state["phi"])

        dF = dF_trans + dF_rot + dF_added
        F_wing = np.sum(dF, axis=0)
        T_wing = np.sum(np.cross(r_body, dF), axis=0)
        return F_wing, T_wing


# ============================================================
# FlapperAeroModel — 冻结生产版（I-014 自 modules/aero_solver.py 原文迁入）
# ============================================================

class FlapperAeroModel:
    """
    Sane & Dickinson (2002) 准定常三分量扑翼气动力模型（冻结版）。

    架构镜像 USV FossenUSVModel：
      • 完全 Isaac-free，可独立测试
      • 持有翼面叶素网格，沿展向积分气动力

    输入（世界系，来自 PhysX）：
      pos_world [3], quat_world [4]=(w,x,y,z), vel_linear [3], vel_angular [3], t
    输出（世界系总力/力矩）：F_world [3], T_world [3]
    """

    def __init__(self, params: Optional[dict] = None) -> None:
        p = params or {}

        # ── 空气参数 ──
        self.rho = float(p.get("rho_air", cfg.AeroModelConfig.RHO_AIR))

        # ── 翼面几何（叶素网格）──
        g = cfg.WingGeometryConfig
        self.span = float(p.get("span", g.SPAN))
        self.root_chord = float(p.get("root_chord", g.ROOT_CHORD))
        self.tip_chord = float(p.get("tip_chord", g.TIP_CHORD))
        self.n_strips = int(p.get("n_strips", g.N_STRIPS))
        self.root_offset = np.asarray(p.get("root_offset", g.ROOT_OFFSET), dtype=np.float64)
        self.hinge_axis = str(p.get("hinge_axis", g.HINGE_AXIS))

        # ── 气动系数（Robofly 拟合）──
        ac = cfg.AeroModelConfig
        self.cn_fit_amp = float(p.get("cn_fit_amp", ac.CN_FIT_AMP))
        self.cn_fit_phase = np.radians(float(p.get("cn_fit_phase_deg", ac.CN_FIT_PHASE_DEG)))
        self.ct_fit_amp = float(p.get("ct_fit_amp", ac.CT_FIT_AMP))
        self.ct_fit_offset = float(p.get("ct_fit_offset", ac.CT_FIT_OFFSET))
        self.c_rot = float(p.get("c_rot", ac.C_ROT))
        self.added_mass_coeff = float(p.get("added_mass_coeff", ac.ADDED_MASS_COEFF))
        self.max_wing_vel = float(p.get("max_wing_vel", ac.MAX_WING_VEL))

        # ── 开关 ──
        self.enable_translational = bool(p.get("enable_translational", ac.ENABLE_TRANSLATIONAL))
        self.enable_rotational = bool(p.get("enable_rotational", ac.ENABLE_ROTATIONAL))
        self.enable_added_mass = bool(p.get("enable_added_mass", ac.ENABLE_ADDED_MASS))

        # ── 拍动运动学（左右翼各一份）──
        self.wing_left = WingKinematics()
        self.wing_right = WingKinematics()
        self.phase_offset_lr = np.radians(float(p.get("phase_offset_lr",
                                                       cfg.FlapKinematicsConfig.PHASE_OFFSET_LR)))

        # ── 预计算叶素网格 ──
        self._build_strips()

        # ── 状态缓存 ──
        self.current_F_world = np.zeros(3)
        self.current_T_world = np.zeros(3)
        self.current_F_body = np.zeros(3)
        self.current_T_body = np.zeros(3)

    def _build_strips(self) -> None:
        """沿展向构建 n_strips 个叶素（梯形翼线性弦长收缩）。"""
        dr = self.span / self.n_strips
        r = (np.arange(self.n_strips) + 0.5) * dr
        c = self.root_chord + (self.tip_chord - self.root_chord) * (r / self.span)
        self._r_centers = r
        self._chords = c
        self._dr = dr
        self._strip_areas = c * dr
        self.wing_area = float(np.sum(self._strip_areas))   # 单翼

    def _C_N(self, alpha_rad):
        """法向力系数 C_N(α) = 3.59·sin(2α + 9.1°)。"""
        return self.cn_fit_amp * np.sin(2.0 * alpha_rad + self.cn_fit_phase)

    def _C_T(self, alpha_rad):
        """切向力系数 C_T(α) = 1.86·cos(2α + 9.1°) - 0.86。"""
        return self.ct_fit_amp * np.cos(2.0 * alpha_rad + self.cn_fit_phase) - self.ct_fit_offset

    def _wing_forces(self, r_centers, chords_unused, wing_state, v_body_at_hinge, omega_body):
        """
        单翼叶素积分（体坐标系，关于 CG）。

        对每个叶素：
          1. r_body = root_offset + (0, r_center, 0)
          2. v_body_induced = v_body + ω × r_body
          3. 拍动速度 v_flap 水平 = φ̇·r·(cos φ, sin φ, 0)
          4. v_rel = -(v_body_induced + v_flap)，限幅防爆
          5. α_eff = α + 来流偏角
          6. 法向力 |C_N|·cos(α_eff) → 垂直分量（翻转俯仰保证向上）
             切向力 C_T 沿来流方向 v̂_rel（阻力）
          7. + 旋转力(∝α̇) + 附加质量力(∝φ̈)
          8. F = ΣdF，T = Σ r×dF
        """
        n = len(r_centers)
        dr = self._dr
        r_abs = np.abs(r_centers)

        phi = wing_state["phi"]
        alpha = wing_state["alpha"]
        phi_dot = wing_state["phi_dot"]
        alpha_dot = wing_state["alpha_dot"]
        phi_ddot = wing_state["phi_ddot"]

        r_body = np.zeros((n, 3), dtype=np.float64)
        r_body[:, 1] = r_centers
        r_body += self.root_offset

        # 2026-09-19：去掉机体转速对翼面来流的耦合（原 v_body+ω×r）。
        # 离线扫描证实：|ω|<3rad/s 时它给出 -0.6 N·m·s/rad 的正常滚转阻尼，
        # 但 |C_N| 饱和使阻尼在 |ω|>3 后反号成自激（+0.43 N·m·s/rad@4rad/s）
        # ——自由飞滚转发散的被控对象层面的根源。台架辨识从未激励过转速项，故此
        # 项既无辨识依据又有害；线性阻尼在 compute_forces 里显式补回。
        v_body_induced = v_body_at_hinge

        # 2026-09-20 夜间开发（处置改动一）：有符号来流镜像延拓。
        # 净水平来流深度反向（|v_x| 超过拍动特征速度 1.2 倍）时，在
        # 镜像参考系 x→−x 内求解（来流折算正向），输出按反射张量映射回
        # 机体系：F→(−Fx,Fy,Fz)、T→(Tx,−Ty,Tz)。已辨识包线（v_x≥0）
        # 镜像不激活，行为逐位不变。推导与限制：docs/有符号C_N推导.md。
        # 消灭旧行为在反向来流下的整流伪推力（vx=−13 时 Fwx=−19N 同向泵）。
        # 特征速度取拍动正弦尺度 2π·f·amp·⟨r⟩（拍周期常量，不随相位振荡；
        # 首版误用瞬时 |φ̇cosφ| 致拍内门限抖动，本版修正）。
        _f_now = float(getattr(self.wing_left, "frequency", 1.55))
        _amp_now = float(getattr(self.wing_left, "amplitude", 0.61))
        v_flap_char = 2.0 * np.pi * _f_now * _amp_now * \
            float(np.mean(r_abs)) + 1e-6
        _mirror = bool(v_body_induced[0] < -1.2 * v_flap_char)
        self._mirror_last_state = _mirror
        if _mirror:
            v_body_induced = v_body_induced.copy()
            v_body_induced[0] = -v_body_induced[0]

        v_flap = np.zeros((n, 3), dtype=np.float64)
        v_flap[:, 0] = phi_dot * r_abs * np.cos(phi)
        # 2026-09-19 镜像修复：Y 分量用带符号半径。右翼（r<0）的拍动速度
        # 必须是左翼关于机体 XZ 对称面的镜像 (vx,-vy,vz)；原实现两翼 Y 向
        # 速度同号叠加，在"对称悬停"下整流出 +8.2N 直流侧力（大于体重，
        # 自由飞 vy ±20m/s、台架 Fy 直流 ≈+4N@2.5Hz 双重实测证实）。
        # X 分量与 |v_rel| 不受影响，升力/前向力逐位不变（辨识真值保留）。
        v_flap[:, 1] = phi_dot * r_centers * np.sin(phi)

        v_rel = -(v_body_induced + v_flap)
        v_rel_mag = np.linalg.norm(v_rel, axis=1)
        scale = np.where(v_rel_mag > self.max_wing_vel,
                         self.max_wing_vel / np.maximum(v_rel_mag, 1e-9), 1.0)
        v_rel = v_rel * scale[:, None]
        v_rel_mag = np.linalg.norm(v_rel, axis=1)

        v_horizontal = v_rel[:, 0]
        v_vertical = v_rel[:, 2]
        inflow_angle = np.arctan2(v_vertical, np.maximum(np.abs(v_horizontal), 1e-9))
        alpha_eff = alpha + inflow_angle

        q_dyn = 0.5 * self.rho * v_rel_mag**2
        C_N = self._C_N(alpha_eff)
        C_T = self._C_T(alpha_eff)

        dF_normal_mag = q_dyn * np.abs(C_N) * self._chords * dr
        dF_trans = np.zeros((n, 3), dtype=np.float64)
        dF_trans[:, 2] = dF_normal_mag * np.cos(alpha_eff)
        v_rel_dir = v_rel / np.maximum(v_rel_mag[:, None], 1e-9)
        dF_tangent_mag = q_dyn * C_T * self._chords * dr
        dF_trans += dF_tangent_mag[:, None] * v_rel_dir

        dF_rot = np.zeros((n, 3), dtype=np.float64)
        if self.enable_rotational:
            dF_rot[:, 2] = self.c_rot * self.rho * self._chords * v_rel_mag * alpha_dot * dr

        dF_added = np.zeros((n, 3), dtype=np.float64)
        if self.enable_added_mass:
            v_added = self._strip_areas * self.added_mass_coeff
            a_flap = phi_ddot * r_abs
            dF_added[:, 0] = -self.rho * v_added * a_flap * np.cos(phi)
            # 同上镜像修复：Y 分量带符号（右翼取镜像方向），消除附加质量
            # 项的周期平均非零（sinφ·φ̈ 整流）直流侧力
            dF_added[:, 1] = -self.rho * v_added * phi_ddot * r_centers * np.sin(phi)

        dF = dF_trans + dF_rot + dF_added
        F_wing_body = np.sum(dF, axis=0)
        T_wing_body = np.sum(np.cross(r_body, dF), axis=0)
        if _mirror:
            # 镜像参考系输出映射（docs/有符号C_N推导.md 3.2/3.5）：
            # y/z 分量按反射张量 F→(−Fx,Fy,Fz) 沿用镜像解；x 分量不取镜像
            # 翻转——求解系内 α=40° 时 C_T<0（推力性），净 Fx 是大量对消
            # 的小差值，翻转后仍可能微弱泵能。反区水平力改取逐叶素切向
            # 阻力绝对值累积：方向恒为阻动、量级为真实阻力尺度、无对消
            # 脆弱性（推力的阻动贡献未建模，保守近似，见推导文档限制四）。
            F_wing_body = np.array([float(np.sum(np.abs(dF_tangent_mag))),
                                    F_wing_body[1], F_wing_body[2]])
            T_wing_body = np.array([T_wing_body[0], -T_wing_body[1],
                                    -T_wing_body[2]])
        # 叶素有效攻角统计（2026-09-20 第10轮记录缺口③）：均值/峰值/
        # 超45°占比——混合态深度直接可统计（镜像参考系口径）
        self._aeff_stats = (float(np.mean(alpha_eff)),
                            float(np.max(np.abs(alpha_eff))),
                            float(np.mean(np.abs(alpha_eff) > np.pi / 4.0)))
        return F_wing_body, T_wing_body

    def compute_forces(self, pos_world, quat_world, vel_linear, vel_angular, t,
                       *, action_view=None):
        if action_view is not None:
            from modules.motion_action.aero_adapter import compute_external
            return compute_external(self, action_view, pos_world, quat_world,
                                    vel_linear, vel_angular, t)
        _external = getattr(self, "_motion_external_view", None)
        """计算扑翼 MAV 世界系总气动力/力矩。重力由 PhysX 提供，不在此处理。"""
        # 原子快照（事项 I-129）：闸门与幅值这类左右成对的离散输入，由
        # 驱动线以单次元组赋值发布（元组赋值为原子操作，回调线程要么
        # 读到旧对要么读到新对，不可能读到左右混配的中间态）；本线程
        # 在求值入口一次性快照到两翼，两翼属性从此只有本线程写。历史
        # 上驱动线逐翼赋值两条语句之间存在回调插入窗口，逐拍差动在拍
        # 边界翻转闸门对时窗口内有实效应，是分岔事故的根因。
        try:
            _g = self.sd_gate_cmd if _external is None else None
            if _g is None:
                raise AttributeError("external view owns motion gates")
            self.wing_left.stroke_gate = _g[0]
            self.wing_right.stroke_gate = _g[1]
        except AttributeError:
            pass
        try:
            _a = self.amp_cmd if _external is None else None
            if _a is None:
                raise AttributeError("external view owns motion amplitude")
            self.wing_left.amplitude = _a[0]
            self.wing_right.amplitude = _a[1]
        except AttributeError:
            pass
        R_bw = quat_to_rotation_matrix(quat_world)
        R_wb = R_bw.T
        v_body = R_wb @ np.asarray(vel_linear, dtype=np.float64)
        omega_body = R_wb @ np.asarray(vel_angular, dtype=np.float64)

        wing_state_L = (_external.state("L", self) if _external is not None else
                        self.wing_left.evaluate(t, phase_offset_rad=0.0))
        F_L, T_L = self._wing_forces(
            +self._r_centers.copy(), self._chords, wing_state_L, v_body, omega_body)
        self._last_aeff_L = self._aeff_stats
        # 逐叶素快照（2026-09-28 完整记录）：三维后端在包装层落槽，
        # 冻结版无此槽取 None；仅记录用途零行为变化
        self._last_strips_L = getattr(self, "_strip_cache_L", None)

        wing_state_R = (_external.state("R", self) if _external is not None else
                        self.wing_right.evaluate(t, phase_offset_rad=self.phase_offset_lr))
        F_R, T_R = self._wing_forces(
            -self._r_centers.copy(), self._chords, wing_state_R, v_body, omega_body)
        self._last_aeff_R = self._aeff_stats
        self._last_strips_R = getattr(self, "_strip_cache_R", None)

        F_body = F_L + F_R
        T_body = T_L + T_R
        # 线性旋转阻尼（2026-09-19）：取原 ω 耦合在原点处的小信号值
        # （0.6 N·m·s/rad，离线扫描），替代其 |ω|>3rad/s 后反号的非线性版
        T_body = T_body - getattr(cfg.AeroModelConfig, "WING_ROT_DAMP", 0.6) \
            * np.asarray(omega_body, dtype=float)

        F_world = R_bw @ F_body
        T_world = R_bw @ T_body

        # 爬升自激修正因子（2026-09-19，从 hover_env/harness 口径移植进模型；
        # 项目内通称"爬升守卫"，规范名称为洛伦兹型经验修正因子）：
        # 准定常叶素模型在垂直爬升时高估升力——来流从下方抬高 α_eff 的正
        # 反馈（在环实测：拍频压在下限 1.5Hz 机体仍净爬升失控至 27m）。
        # 该因子仅作用于爬升方向，下降方向不修改（避免引入下坠失控）
        _vz_air = float(np.asarray(vel_linear, dtype=float)[2])
        if _vz_air > 0.0:
            F_world = F_world.copy()
            F_world[2] *= 1.0 / (1.0 + (_vz_air / 2.5) ** 2)

        self.current_F_body = F_body
        self.current_T_body = T_body
        self.current_F_world = F_world
        self.current_T_world = T_world
        return F_world, T_world

    def set_wing_command(self, frequency=None, amplitude_deg=None,
                         phase_offset_lr_deg=None, pitch_bias_deg=None,
                         pitch_amplitude_deg=None, alpha_set_deg=None):
        """运行时调整拍动参数。None 表示保持。

        alpha_set_deg（第十二班 α 管线）：aero 俯仰模式的目标攻角运行时
        透传——管线先行，模型保真升级后再开环验证（B 路线否决的是冻结
        模型上的 α 通道，管线与裁决互不阻塞）。
        """
        for wing in (self.wing_left, self.wing_right):
            if frequency is not None:
                wing.set_frequency(frequency)
            if amplitude_deg is not None:
                wing.amplitude = np.radians(float(amplitude_deg))
            if pitch_bias_deg is not None:
                wing.pitch_bias = np.radians(float(pitch_bias_deg))
            if pitch_amplitude_deg is not None:
                wing.pitch_amplitude = np.radians(float(pitch_amplitude_deg))
            if alpha_set_deg is not None:
                wing.alpha_set = np.radians(float(alpha_set_deg))
        if phase_offset_lr_deg is not None:
            self.phase_offset_lr = np.radians(float(phase_offset_lr_deg))
