"""
aero_solver.py — 扑翼气动力学仿真接入层（Isaac 物理回调 + 工厂 + 兼容再导出）
==============================================================================
I-014 气动双栈合并后的定位：本文件不再实现任何气动模型与翼运动学，模型层
唯一实现在 modules/aero/ 包（kinematics / common / attached_flow /
dynamic_stall / sane_dickinson / sane_dickinson_3d / tail_fuselage）。
本文件保留三类内容：
  1. 仿真接入层：AeroPhysicsCallback（物理回调）与 create_aero_callback
     （三流型工厂），严格镜像 USV HydroPhysicsCallback 范式：
       • 读：view.get_world_poses() / get_velocities()（零 Fabric 锁热路径）
       • 写：apply_forces_and_torques_at_pos（不传 positions，PhysX 按 CG 施加）
  2. 接入层附属：HoverPDController（内嵌高度/姿态 PD，纯 NumPy）与
     det_probe_enabled / det_probe_read（READ-SPIKE 纯观测探针）。
  3. 兼容再导出：历史上定义于本文件的模型与坐标函数（WingKinematics、
     FlapperAeroModel、TailFuselageAero、quat_to_rotation_matrix、
     body_to_world、quat_to_euler、euler_to_quat）现从 modules/aero 包
     导入再导出，main_sim、modules/backend、tools 的既有 import 一行
     不改照常工作；再导出与包内实现为同一对象。

三流型（modules/aero/ 包内实现，工厂可选）：
  attached_flow    — 路线A：高Re大展弦比附着流（Wagner/Duhamel + AR修正）
  dynamic_stall    — 路线B：受控动态失速（LB 三状态）
  sane_dickinson   — 路线C：冻结版 FlapperAeroModel（默认，Sane & Dickinson
                     2002 三分量 + 翻转俯仰）
  sane_dickinson_3d — 路线C 三维叶素后端（继承冻结版）

设计原则（严格继承 USV）：
  1. Model 与 Isaac 完全解耦，`python aero_solver.py` 独立跑单元测试
  2. PhysX 积分刚体动力学，气动力作为外力注入（不覆盖位姿/速度）
  3. 不重复计算 PhysX 已有的项（刚体惯性、重力）
  4. 体坐标系算力 → world 旋转一次 → 提交（与 USV 一致）
  5. 力注入不传 positions（PhysX 按 CG 施加）
"""

from __future__ import annotations

import os
import sys
import math
import logging
from typing import Optional, Tuple, Dict

import numpy as np

# ── 项目配置（纯 Python，无 Isaac 依赖）──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from config import sim_config as cfg

# ── 兼容再导出（I-014：模型层唯一实现已迁入 modules/aero 包）──
from modules.aero import (                     # noqa: F401
    WingKinematics,
    FlapperAeroModel,
    TailFuselageAero,
    quat_to_rotation_matrix,
    body_to_world,
    quat_to_euler,
    euler_to_quat,
)

logger = logging.getLogger(__name__)


# ============================================================
# HoverPDController — 内嵌高度/姿态 PD 控制器（纯 NumPy）
# ============================================================

class HoverPDController:
    """
    最简高度 + 滚转/俯仰 PD 控制器，输出拍动参数调整。
    架构类比 USV PIDHeadingController，但输出拍动参数而非推力。
    """

    def __init__(
        self,
        target_height: float = 1.0,
        kp_height: float = 8.0,
        kd_height: float = 4.0,
        kp_roll: float = 0.5,
        kp_pitch: float = 0.3,
        kp_yaw: float = 0.2,
        nominal_freq: float = cfg.FlapKinematicsConfig.FREQUENCY,
        freq_min: float = 1.5,     # 700g 大翼域；蜂鸟尺度改 15~45
        freq_max: float = 4.5,
    ):
        self.target_height = target_height
        self.kp_h = kp_height
        self.kd_h = kd_height
        self.kp_roll = kp_roll
        self.kp_pitch = kp_pitch
        self.kp_yaw = kp_yaw
        self.nominal_freq = nominal_freq
        self.freq_min = freq_min
        self.freq_max = freq_max

    def compute(self, pos_world, quat_world, vel_linear, vel_angular, t):
        h = float(pos_world[2])
        h_err = self.target_height - h
        freq_cmd = self.nominal_freq + self.kp_h * h_err - self.kd_h * vel_linear[2]
        freq_cmd = float(np.clip(freq_cmd, self.freq_min, self.freq_max))

        roll, pitch, yaw = quat_to_euler(quat_world)
        amp_base = cfg.FlapKinematicsConfig.AMPLITUDE
        amp_delta = self.kp_roll * np.degrees(roll)
        pitch_bias = -self.kp_pitch * np.degrees(pitch)
        phase_offset_lr = self.kp_yaw * np.degrees(yaw) * 10.0

        return {
            "frequency": freq_cmd,
            "amplitude_left": amp_base + amp_delta,
            "amplitude_right": amp_base - amp_delta,
            "pitch_bias": pitch_bias,
            "phase_offset_lr": phase_offset_lr,
        }


# ============================================================
# AeroPhysicsCallback — Isaac Sim 物理回调适配层
# ============================================================
# 严格镜像 USV HydroPhysicsCallback：
#   • 读：view.get_world_poses() / get_velocities()（零 Fabric 锁热路径）
#   • 写：apply_forces_and_torques_at_pos（不传 positions，PhysX 按 CG 施加）

def det_probe_enabled() -> bool:
    """READ-SPIKE 探测器总开关（2026-09-28 排障：默认关，只打印不改行为）。"""
    import os
    return os.environ.get("FLAPPER_DET_PROBE") == "1"


def _i210_aero_event(cb, dt, payload):
    """Record API receipts, keeping callback time separate from main time."""
    if os.environ.get("FLAPPER_I210_EVIDENCE") != "1":
        return
    try:
        from modules import i210_evidence
        host = sys.modules.get("main_sim") or sys.modules["__main__"]
        i210_evidence.observe(host.__dict__.get("_FW_IID_REC"), "aero_apply", {
            "callback_index": cb._step_count,
            "callback_t_s": cb._sim_time, "callback_dt_s": dt,
            "last_completed_main_step": getattr(cb, "main_step", None),
            "stage": "aero_callback_apply_receipt"}, payload)
    except Exception as exc:
        logger.warning("I-210 气动观测失败: %s", exc)


def _i210_model_snapshot(cb, pos, quat, v_air, angular, force, torque,
                          read_source, read_error):
    try:
        return {
            "model_type": type(cb.model).__module__ + "." + type(cb.model).__name__,
            "model_input": {"position_world_m": pos.copy(),
                "quaternion_wxyz": quat.copy(), "air_velocity_world_m_s": v_air.copy(),
                "angular_velocity_world_rad_s": angular.copy()},
            "body_read_source": read_source, "body_read_error": read_error,
            "wing_readback": {side: {key: getattr(wing, key, None)
                for key in ("frequency", "omega", "amplitude", "pitch_lag_s",
                            "stroke_gate", "_tau_state", "_t_prev")}
                for side, wing in (("L", cb.model.wing_left), ("R", cb.model.wing_right))},
            "instantaneous_model_F_world_N": force.copy(),
            "instantaneous_model_T_world_Nm": torque.copy(),
            "units_frame": "forces N / torques N*m, world frame; API receipt is not a force sensor"}
    except Exception as exc:
        return {"observation_errors": {"model_snapshot": repr(exc)}}


def det_probe_read(tag, pose, vel, prev_pose, prev_vel):
    """纯观测探测器：对一次状态读取做合理性检验，违规只打印、绝不改值。

    阈值取"相邻两次读取之间物理不可能"量级（回调 fire 间隔约半步，巡航
    速度米每秒级）：连续两次读取间出现半米级位置跳变、两米每秒级线速度
    跳变、五弧度每秒级角速度跳变、高度从半米以上瞬落到零点零五米以下、
    四元数模偏离一，任一即违规。目的：抓"读到零或垃圾"的现行并留全指纹。
    """
    try:
        if prev_pose is None:
            return
        p = np.asarray(pose, dtype=float)
        pp = np.asarray(prev_pose, dtype=float)
        v = np.asarray(vel, dtype=float) if vel is not None else None
        pv = np.asarray(prev_vel, dtype=float) if prev_vel is not None else None
        bad = []
        if not np.isfinite(p).all():
            bad.append("pose_nonfinite")
        if (v is not None) and (not np.isfinite(v).all()):
            bad.append("vel_nonfinite")
        dp = float(np.linalg.norm(p[0:3] - pp[0:3]))
        if dp > 0.5:
            bad.append(f"dpos={dp:.3f}m")
        if abs(float(p[2])) < 0.05 and abs(float(pp[2])) > 0.5:
            bad.append(f"z_drop={pp[2]:.3f}->{p[2]:.3f}")
        qn = float(np.linalg.norm(p[3:7])) if p.size >= 7 else 1.0
        if abs(qn - 1.0) > 0.3:
            bad.append(f"qnorm={qn:.3f}")
        if v is not None and pv is not None and v.size == pv.size and v.size >= 3:
            dv = float(np.linalg.norm(v[0:3] - pv[0:3]))
            if dv > 2.0:
                bad.append(f"dvel={dv:.3f}m/s")
            if v.size >= 6:
                dw = float(np.linalg.norm(v[3:6] - pv[3:6]))
                if dw > 5.0:
                    bad.append(f"dw={dw:.3f}rad/s")
        if bad:
            print(f"[READ-SPIKE] {tag} | {' '.join(bad)} | "
                  f"prev_pos={np.round(pp, 5).tolist()} pos={np.round(p, 5).tolist()} | "
                  f"prev_vel={np.round(pv, 5).tolist() if pv is not None else None} "
                  f"vel={np.round(v, 5).tolist() if v is not None else None}",
                  flush=True)
            return True
        return False
    except Exception:
        return False


class AeroPhysicsCallback:
    """扑翼气动力学物理回调。把 Model 接入 Isaac Sim 物理步进。"""

    def __init__(self, world, flapper_prim, flapper_view=None, config=None):
        self.world = world
        self.flapper_prim = flapper_prim
        self.flapper_view = flapper_view
        self.model = FlapperAeroModel(params=config)
        self.controller = HoverPDController()
        # HoverPD 默认关：20g 蜂鸟时代标定，700g 场景下增益过激（会顶满频）。
        # 控制权在 main_sim 的 WingKinematics 驱动 + flapper_controller。
        self._controller_enabled = False
        self.tail_body = TailFuselageAero()   # 尾翼+机身阻力（main_sim 注入舵偏）

        self._last_pos = None
        self._v_air_filt = None   # （已弃用，保留字段兼容）
        self._v_air_hold = None    # 拍周期同步采样保持值（第二班）
        self._pl_phase = 0.0        # 拍动相位累加器（第二班）
        self.held_body_vel = None    # 控制器输入速度（第四班二版=τ0.1滤波）
        self._ctrl_vel_filt = None  # 控制速度滤波状态
        self._fa_buf = None       # 拍周期平均力滑窗（在环-harness 被控对象对齐）
        self._last_quat = None
        self._last_vel_lin = None
        self._last_vel_ang = None

        self._sim_time = 0.0
        self._step_count = 0
        self._crash_log = os.path.join(cfg.LOG_DIR, "aero_crash.txt")

    def set_wing_command(self, **kwargs):
        self.model.set_wing_command(**kwargs)

    def set_controller_target(self, height=None):
        if height is not None:
            self.controller.target_height = float(height)

    def enable_controller(self, enabled):
        self._controller_enabled = bool(enabled)

    def __call__(self, step_size: float):
        self._fire_count = getattr(self, "_fire_count", 0) + 1
        self._fire_dts = (getattr(self, "_fire_dts", ()) + (step_size,))[-4:]
        dt = step_size
        self._sim_time += dt
        self._step_count += 1
        try:
            self._call_inner(dt)
        except Exception as _exc:
            _execution=getattr(self,"motion_execution",None)
            if _execution is not None:
                from modules.motion_action.journal import ActionRecordingError
                if isinstance(_exc,ActionRecordingError):
                    _execution.recording_error=repr(_exc)
                elif _execution.failed is None:
                    _execution.failed="aerodynamic callback failed: %r"%_exc
            if os.environ.get("FLAPPER_I210_EVIDENCE") == "1":
                _i210_aero_event(self, dt, {"status": "callback_failed",
                    "error": repr(_exc), "api_status": "unknown"})
            import traceback
            try:
                with open(self._crash_log, "a", encoding="utf-8") as _hf:
                    _hf.write(f"=== t={self._sim_time:.3f} step={self._step_count} ===\n")
                    _hf.write(traceback.format_exc() + "\n")
            except Exception:
                pass
            logger.error("气动力学回调崩溃: %s", _exc, exc_info=True)
            raise

    def _call_inner(self, dt):
        _motion = getattr(self, "motion_execution", None)
        if _motion is not None:
            if _motion.failed is not None:
                raise RuntimeError("action execution stopped before aerodynamic consumption")
            _motion.read()  # Binding is valid only after first frame publication.
        _i210_on = os.environ.get("FLAPPER_I210_EVIDENCE") == "1"
        _i210_read_source = "live_api"
        _i210_read_error = None
        # 1. 读 PhysX 状态
        try:
            if (getattr(self, "_suppress_read", False)
                    and self._last_pos is not None):
                _i210_read_source = "held_suppress_read"
                # 安全火力模式（FLAPPER_POST_STEP_CB=1）第二次派发：结果写出
                # 窗口内的读取有撕裂风险，沿用上一拍已确认状态
                pos_world = self._last_pos.copy()
                quat_world = self._last_quat.copy()
                vel_linear = self._last_vel_lin.copy()
                vel_angular = self._last_vel_ang.copy()
            elif self.flapper_view is not None:
                _pos, _quat = self.flapper_view.get_world_poses()
                pos_world = np.asarray(_pos[0], dtype=np.float64).flatten()
                quat_world = np.asarray(_quat[0], dtype=np.float64).flatten()
                _vel = self.flapper_view.get_velocities()
                vel_linear = np.asarray(_vel[0, :3], dtype=np.float64).flatten()
                vel_angular = np.asarray(_vel[0, 3:], dtype=np.float64).flatten()
            else:
                _pos, _quat = self.flapper_prim.get_world_poses()
                pos_world = np.asarray(_pos[0], dtype=np.float64).flatten()
                quat_world = np.asarray(_quat[0], dtype=np.float64).flatten()
                _vel = self.flapper_prim.get_velocities()
                vel_linear = np.asarray(_vel[0][:3], dtype=np.float64).flatten()
                vel_angular = np.asarray(_vel[0][3:], dtype=np.float64).flatten()

            if det_probe_enabled():
                det_probe_read(
                    f"AERO fire#{self._fire_count} t={self._sim_time:.4f} "
                    f"step={self._step_count}",
                    np.concatenate([pos_world, quat_world]),
                    np.concatenate([vel_linear, vel_angular]),
                    (None if self._last_pos is None else
                     np.concatenate([self._last_pos, self._last_quat])),
                    (None if self._last_vel_lin is None else
                     np.concatenate([self._last_vel_lin, self._last_vel_ang])))

            self._last_pos = pos_world.copy()
            self._last_quat = quat_world.copy()
            self._last_vel_lin = vel_linear.copy()
            self._last_vel_ang = vel_angular.copy()
        except Exception as e:
            _i210_read_source, _i210_read_error = "cached_after_read_failure", repr(e)
            logger.error("读取 PhysX 状态失败: %s", e, exc_info=True)
            if self._last_pos is not None:
                pos_world = self._last_pos.copy()
                quat_world = self._last_quat.copy()
                vel_linear = self._last_vel_lin.copy()
                vel_angular = self._last_vel_ang.copy()
            else:
                if _i210_on:
                    _i210_aero_event(self, dt, {"status": "state_read_failed",
                        "error": repr(e), "api_status": "not_attempted"})
                return

        # 2. 内嵌控制器
        if self._controller_enabled:
            cmd = self.controller.compute(pos_world, quat_world, vel_linear,
                                          vel_angular, self._sim_time)
            self.model.wing_left.amplitude = np.radians(cmd["amplitude_left"])
            self.model.wing_right.amplitude = np.radians(cmd["amplitude_right"])
            self.model.set_wing_command(
                frequency=cmd["frequency"],
                pitch_bias_deg=cmd["pitch_bias"],
                phase_offset_lr_deg=cmd["phase_offset_lr"],
            )

        # 3. Model 计算气动力（相对风速 = 机体速度 - 来流风）
        v_air = vel_linear
        try:
            if cfg.WIND_ENABLED:
                v_air = vel_linear - np.asarray(cfg.WIND_VECTOR, dtype=float)
        except Exception:
            pass
        # 台架试验（2026-09-18）：力模型看到的机体状态必须恒定。
        # 运动学锁定位姿发生在 world.step 之后，而本回调读到的是上一步积分后的
        # 速度（气动→速度→气动的单步反馈会虚升 3~4 倍），因此试验模式下
        # 直接把送入力模型的状态锁定，测量等于纯模型求值。
        # 2026-09-19 选择性状态锁定：试验段 release 且 BENCH_FREE 非空时，释放轴
        # 送入真实状态（阻尼/俯仰-速度耦合只能来自真实积分），其余轴保持锁定；
        # 俯仰锁定值取 cfg.BENCH_PITCH_DEG（trim 试验的配平偏置）。
        if getattr(cfg, "EXPERIMENT_MODE", ""):
            _free = tuple(getattr(cfg, "BENCH_FREE", ())) \
                if getattr(cfg, "BENCH_RELEASE", False) else ()
            _r, _p, _y = quat_to_euler(quat_world)
            if "roll" not in _free:
                _r = 0.0
            if "pitch" not in _free:
                _p = np.radians(float(getattr(cfg, "BENCH_PITCH_DEG", 0.0)))
            if "yaw" not in _free:
                _y = 0.0
            quat_world = euler_to_quat(_r, _p, _y)
            pos_world = np.array([0.0, 0.0, cfg.DP_TARGET_Z])
            _vl = np.asarray(vel_linear, dtype=float).copy()
            if "x" not in _free:
                _vl[0] = 0.0
            _vl[1] = 0.0
            _vl[2] = 0.0
            _va = np.asarray(vel_angular, dtype=float).copy()
            if "roll" not in _free:
                _va[0] = 0.0
            if "pitch" not in _free:
                _va[1] = 0.0
            if "yaw" not in _free:
                _va[2] = 0.0
            vel_angular = _va
            v_air = _vl - np.asarray(cfg.WIND_VECTOR, dtype=float) \
                if cfg.WIND_ENABLED else _vl
        self.last_v_air = v_air.copy()
        self.last_vel_lin = vel_linear.copy()
        # 2026-09-19 力模型输入状态滤波（在环-harness 口径对齐的最后一环）：
        # ±20N 拍频振荡力让机体以 ±2.4m/s 拍频真实振荡，准定常力律的非线性
        # （|C_N|、攻角、限幅）对振荡状态整流出 -17N 级直流水平力（全量记录
        # Fwx 列实测；俯仰正时爆到 +154N）——非线性系统周期平均 ≠ 平均状
        # 态求值。力模型只吃低通状态（τ=0.1s：滤拍频分量、保机动带宽），
        # 与 harness 固定状态口径一致（该口径下闭环全绿）。记录仍存原始值
        # 2026-09-20 第二班（拍周期同步采样反馈，FIXLOG"下一包"第 1 项）：
        # 按拍动相位边沿（周期起点）采样 v_air 并保持整周期。原理：输入状态
        # 的拍频振荡分量在固定相位点采样后混叠为常值偏置，不再经非线性力律
        # 整流成直流伪力（旧 τ=0.1 EWMA 在 f=1.55Hz 只衰减 28% 且带 0.1s
        # 滞后；窗口加倍实验已证明滞后路径引发非指令深俯冲，故弃滤波改采样）。
        # 采样点=周期边沿（tau=0，行程换向处 φ̇=0，振荡速度近零）。该口径
        # 与 harness"固定状态整周期平均"按构造对齐。
        _f_pl = max(float(getattr(self.model.wing_left, "frequency", 1.55)),
                    0.2)
        _pl_prev = getattr(self, "_pl_phase", 0.0)
        self._pl_phase = ((_motion.service.read().chi_L % 1.)
            if _motion is not None else (_pl_prev + _f_pl * dt) % 1.0)
        if self._pl_phase < _pl_prev or self._v_air_hold is None:
            # 相位回绕=新周期起点：采样并保持（初始首步无条件采样一次但
            # 状态锁定期真实速度≈0，不存在锁存失效问题）
            self._v_air_hold = np.asarray(v_air, dtype=float).copy()
        # 控制器输入（第四班二版）：弃相位保持（0.5s 陈旧在环振荡主源，
        # 173706 实录），改 τ=0.1 轻滤波——控制器要带宽，力模型要纯度，
        # 两者分口径供数
        if self._ctrl_vel_filt is None:
            self._ctrl_vel_filt = np.asarray(vel_linear, dtype=float).copy()
        else:
            self._ctrl_vel_filt = self._ctrl_vel_filt + min(1.0, dt / 0.1) * (
                np.asarray(vel_linear, dtype=float) - self._ctrl_vel_filt)
        self.held_body_vel = self._ctrl_vel_filt.copy()
        # 第五班（力模型输入二版）：相位保持退役、改 τ=0.1 滤波——
        # 同步周期平均（第四班）已从输出侧消除纹波混叠，相位保持的剩余
        # 作用只剩 0.55s 陈旧（快瞬态角落在环坠落 vs 试验架竖直超升化的
        # 分歧来源）；与控制器同口径，向试验架真值态靠拢
        _v_body_f = self._ctrl_vel_filt
        v_air = (_v_body_f - np.asarray(cfg.WIND_VECTOR, dtype=float)
                 if getattr(cfg, "WIND_ENABLED", False) else np.asarray(_v_body_f, dtype=float)).copy()
        # 2026-09-19 包线守卫：准定常力律只在悬停+前飞包线内辨识（台架
        # 迎风扫频全部是正向来流）。全量记录实测：倒飞区外推产生同向
        # "推力"（vx=-13m/s 时 Fwx=-19N，随运动加速——水平通道发散的
        # 直接根源）。模型输入的水平分量钳在辨识包线内，原始值照记。
        # Real2Sim 待办：反向流工况补测后替换
        # 2026-09-20 夜间调试第3轮：包络钳位补丁按禁令移除（2.3m 时代的
        # 对症补丁；0.6m 几何重标后力级已物理合理，无需钳位）
        if _motion is not None:
            from modules.motion_action.aero_adapter import ExternalActionView
            F_world, T_world = self.model.compute_forces(
                pos_world, quat_world, v_air, vel_angular, _motion.read().t_s,
                action_view=ExternalActionView.from_execution(_motion))
        else:
            F_world, T_world = self.model.compute_forces(
                pos_world, quat_world, v_air, vel_angular, self._sim_time)
        _i210_payload = None
        if _i210_on:
            # Snapshot only values already used; never call evaluate again.
            _i210_payload = _i210_model_snapshot(self, pos_world, quat_world,
                v_air, vel_angular, F_world, T_world,
                _i210_read_source, _i210_read_error)
        # 2026-09-20 第四班（同步周期平均·结论）：废除滑动窗，每步在当前
        # 保持状态下对整周期 K 相位同步求值取平均——即 harness cycle_mean
        # 的在线版，在环与试验架口径按构造一致。滑动窗三代方案（计数窗/
        # 时间窗/加倍窗）的共同缺陷：断续事件（释放/镜像切换）后重建期
        # 输出部分周期均值（±5N 假力实录），且固有整周期滞后，使发射段
        # 先下坠后竖直超升（172405 系列）。同步平均零滞后、无跨 regime 混染。
        # 相位状态在求值前后快照恢复（多相位预览不得推进 _tau_state）。
        if _motion is not None:
            from modules.motion_action.aero_adapter import compute_action_window
            F_world,T_world,self.motion_force_window=compute_action_window(
                self.model,_motion,pos_world,quat_world,v_air,vel_angular)
            _K=8
            _T_per=self.motion_force_window["reference_period_s"]
        else:
            _f_now = float(getattr(self.model.wing_left, "frequency", 1.55))
            _wl, _wr = self.model.wing_left, self.model.wing_right
            _snap = ((_wl._tau_state, _wl._t_prev), (_wr._tau_state, _wr._t_prev))
            _K = 8
            _T_per = 1.0 / max(_f_now, 0.2)
            _Fs = np.zeros(3)
            _Ts = np.zeros(3)
            for _k in range(_K):
                _Fk, _Tk = self.model.compute_forces(
                    pos_world, quat_world, v_air, vel_angular,
                    self._sim_time + _k * _T_per / _K)
                _Fs += _Fk
                _Ts += _Tk
            (_wl._tau_state, _wl._t_prev), (_wr._tau_state, _wr._t_prev) = _snap
            F_world, T_world = _Fs / _K, _Ts / _K
        if _i210_payload is not None:
            _i210_payload.update({"wing_cycle_mean_F_world_N": F_world.copy(),
                "wing_cycle_mean_T_world_Nm": T_world.copy(),
                "preview": {"K": _K, "origin_callback_t_s": self._sim_time,
                    "period_s": _T_per, "formula": "origin+k*period/K, k=0..K-1",
                    "phase_state_restored": True}})
        # caches temporarily disabled for bisect

        # 3b. 尾翼 + 机身阻力（控制面/阻力开关，同用相对风速）
        # 2026-09-19：拍动滑流——真机尾翼悬停时浸泡在下洗流中，
        # v_slip = SLIP_K·SLIP_V_PER_HZ·f（一阶动量理论，待 Real2Sim 标定）
        Ft = Tt = np.zeros(3)
        _i210_tail_status, _i210_tail_error = "not_attempted", None
        try:
            _v_slip = 0.0
            if getattr(cfg, "ENABLE_TAIL_SLIPSTREAM", False):
                _f_now = (_motion.service.read().frequency_hz+_motion.service.read().dfreq_hz
                    if _motion is not None else float(getattr(self.model.wing_left, "frequency", 0.0)))
                _v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * _f_now
            Ft, Tt = self.tail_body.compute(quat_world, v_air, v_slip=_v_slip)
            F_world = F_world + Ft
            T_world = T_world + Tt
            # 全量记录（2026-09-19 重开，原"temporarily disabled for bisect"）：
            # 尾翼/机翼分量分离落盘，坠机归因不再需要推断
            self.last_tail_F = Ft.copy()
            self.last_tail_T = Tt.copy()
            self.last_v_slip = float(_v_slip)
            _i210_tail_status = "computed"
        except Exception as e:
            _i210_tail_status, _i210_tail_error = "failed", repr(e)
            logger.debug("尾翼/机身气动计算失败: %s", e)
        # tail caches temporarily disabled for bisect

        # 3c. 缓存最新合力（看板/遥测用）；限幅防翻滚态数值爆炸
        #     （翻滚速度下准定常模型本就失效，3×重力/2 N·m 是正则化不是物理）
        # 台架试验时放宽限幅：锁定的机体不会数值爆炸，3×mg 限幅会把高频段的
        # 瞬时峰值削平、把 L̄(f) 辨识曲线压成非单调（2026-09-18 实测确认）；
        # 力矩限幅同理放宽到 60——3.0Hz+±0.18rad 差动工况瞬时峰值约 31 N·m，
        # 20 N·m 会把大幅值段增益削出假下垂（2026-09-19 复核确认）。
        # 钳位前原值与触限标志（2026-09-20 第10轮记录缺口②）
        self.last_F_raw = F_world.copy()
        self.last_T_raw = T_world.copy()
        f_max = (30.0 if getattr(cfg, "EXPERIMENT_MODE", "") else 3.0) * cfg.MASS * 9.81
        f_norm = float(np.linalg.norm(F_world))
        if f_norm > f_max:
            F_world = F_world * (f_max / f_norm)
        t_max = 60.0 if getattr(cfg, "EXPERIMENT_MODE", "") else 2.0
        t_norm = float(np.linalg.norm(T_world))
        if t_norm > t_max:
            T_world = T_world * (t_max / t_norm)
        self.last_clipped = bool(f_norm > f_max or t_norm > t_max)
        self.last_F_world = F_world
        self.last_T_world = T_world

        # 4. 写回 PhysX（不传 positions）
        # 第七班根因修复：本回调在 main_sim 运行时被每个 world.step() 触发
        # 多次（实测约 1.75 次/步，频率随渲染率漂移），外力累积器按步清算
        # →施加力被放大该倍数（+0.9 N 未登记力、表观质量 0.123 kg、与时基
        # 异常为同一成因）。触发率随渲染率漂移，固定除数不可靠；改由主循环经
        # main_step 通报步界，本回调实测"每次主步的触发数"并取其倒数缩放，
        # 使每个 world.step() 的合计施加量恒等于模型输出。
        _ms = getattr(self, "main_step", None)
        if _ms is not None:
            if not hasattr(self, "_ms_seen"):
                self._ms_seen = _ms
                self._fires = 0
                self._fpms = 1.0
            self._fires += 1
            if _ms != self._ms_seen:
                _delta = _ms - self._ms_seen
                _inst = self._fires / max(_delta, 1)
                self._fpms = 0.9 * self._fpms + 0.1 * _inst
                self._fires = 0
                self._ms_seen = _ms
            _rate_scale = 1.0 / max(self._fpms, 1.0)
        else:
            _rate_scale = 1.0
        F_apply = F_world * _rate_scale
        T_apply = T_world * _rate_scale
        if _motion is not None:
            _motion_sources=list(getattr(self,"_motion_pending_sources",[])) + [dict(
                run_id=_motion.run_id,segment=_motion.segment,global_step=_motion._global_step,
                frame_step=_motion.read().step,frame_t_s=_motion.read().t_s,
                callback_step=self._step_count,callback_t_s=self._sim_time,
                suppress_read=bool(getattr(self,"_suppress_read",False)),
                suppress_apply=bool(getattr(self,"_suppress_apply",False)),
                body_read_source=_i210_read_source,body_read_error=_i210_read_error,
                rate_scale=_rate_scale,window=self.motion_force_window)]
            self._motion_pending_sources=[]
        _i210_pending = getattr(self, "_safe_pending", None) if _i210_on else None
        # 安全火力模式：合并上一拍（结果写出窗口内）暂存的半力，使每步
        # 求解器收到的合计与基线一致；若本拍处于写出窗口则只暂存不施加
        if getattr(self, "_safe_pending", None) is not None:
            F_apply = F_apply + self._safe_pending[0]
            T_apply = T_apply + self._safe_pending[1]
            self._safe_pending = None
        self.last_F_world = F_apply
        self.last_T_world = T_apply
        if _i210_payload is not None:
            _i210_payload.update({"tail_status": _i210_tail_status,
                "tail_error": _i210_tail_error,
                "tail_F_world_N": Ft, "tail_T_world_Nm": Tt,
                "total_before_clip_F_world_N": self.last_F_raw,
                "total_before_clip_T_world_Nm": self.last_T_raw,
                "total_after_clip_F_world_N": F_world,
                "total_after_clip_T_world_Nm": T_world,
                "clipped": self.last_clipped, "callback_rate_scale": _rate_scale,
                "pending_before": _i210_pending,
                "combined_submit_F_float64_N": F_apply,
                "combined_submit_T_float64_Nm": T_apply})
        if getattr(self, "_suppress_apply", False):
            self._safe_pending = (F_world * _rate_scale,
                                  T_world * _rate_scale)
            if _motion is not None:
                self._safe_pending=(F_apply.copy(),T_apply.copy())
                self._motion_pending_sources=_motion_sources
                self.motion_last_force_receipt=dict(status="deferred",source_frames=_motion_sources,
                    solver_consumption_step="unknown",force=F_apply.tolist(),torque=T_apply.tolist())
                from modules.motion_action.journal import record_force_receipt
                record_force_receipt(_motion,self.motion_last_force_receipt)
            if _i210_payload is not None:
                _i210_payload.update({"status": "deferred", "api_status": "not_attempted",
                    "force_api_status": "not_attempted", "torque_api_status": "not_attempted",
                    "submitted_F_float32_N": None, "submitted_T_float32_Nm": None})
                _i210_aero_event(self, dt, _i210_payload)
            return
        _i210_force_status = _i210_torque_status = "not_attempted"
        _i210_submit_f = _i210_submit_t = None
        _i210_apply_error = None
        try:
            if self.flapper_view is not None:
                _api_f = F_apply.reshape(1, 3).astype(np.float32)
                _api_t = T_apply.reshape(1, 3).astype(np.float32)
                if _i210_on:
                    _i210_submit_f, _i210_submit_t = _api_f.copy(), _api_t.copy()
                _i210_force_status = _i210_torque_status = "attempted"
                self.flapper_view.apply_forces_and_torques_at_pos(
                    forces=_api_f,
                    torques=_api_t,
                    is_global=True,
                )
                _i210_force_status = _i210_torque_status = "api_succeeded"
            else:
                _api_f = F_apply.astype(np.float32)
                if _i210_on:
                    _i210_submit_f = _api_f.copy()
                _i210_force_status = "attempted"
                self.flapper_prim.apply_force(_api_f, is_global=True)
                _i210_force_status = "api_succeeded"
                _api_t = T_apply.astype(np.float32)
                if _i210_on:
                    _i210_submit_t = _api_t.copy()
                _i210_torque_status = "attempted"
                self.flapper_prim.apply_torque(_api_t, is_global=True)
                _i210_torque_status = "api_succeeded"
            # 诊断（两被控对象矛盾调和）：施加点旁证记录——旁证值必须与 CSV 的
            # Fz 列一致；不一致则 CSV 口径有误，一致则未登记力在回调之外
            if os.environ.get("FLAPPER_APPLY_DIAG") and self._step_count % 60 == 0:
                with open(os.path.join(cfg.LOG_DIR, "aero_apply_diag.txt"), "a",
                          encoding="utf-8") as _df:
                    _df.write(f"{self._sim_time:.3f},{F_apply[2]:.4f},"
                              f"{float(vel_linear[2]):.4f},{_rate_scale:.3f}\n")
        except Exception as e:
            _i210_apply_error = repr(e)
            logger.error("气动力施加失败: %s", e)
        finally:
            if _i210_payload is not None:
                # Diagnostic-log failure after successful submission must not
                # relabel the physical API as failed; split statuses preserve it.
                _ok = (_i210_force_status == _i210_torque_status == "api_succeeded")
                if _i210_force_status == "attempted":
                    _i210_force_status = "raised_unknown_effect"
                if _i210_torque_status == "attempted":
                    _i210_torque_status = "raised_unknown_effect"
                _i210_payload.update({"status": "api_succeeded" if _ok else "api_failed",
                    "api_status": "api_succeeded" if _ok else "api_failed",
                    "force_api_status": _i210_force_status,
                    "torque_api_status": _i210_torque_status,
                    "apply_or_diagnostic_error": _i210_apply_error,
                    "submitted_F_float32_N": _i210_submit_f,
                    "submitted_T_float32_Nm": _i210_submit_t})
                _i210_aero_event(self, dt, _i210_payload)


        if _motion is not None:
            self.motion_last_force_receipt=dict(status="api_accepted" if
                _i210_force_status==_i210_torque_status=="api_succeeded" else "api_failed",
                force_api_status=_i210_force_status,torque_api_status=_i210_torque_status,
                error=_i210_apply_error,source_frames=_motion_sources,
                solver_consumption_step="unknown",force=F_apply.tolist(),torque=T_apply.tolist())
            from modules.motion_action.journal import record_force_receipt
            record_force_receipt(_motion,self.motion_last_force_receipt)
            if self.motion_last_force_receipt["status"]=="api_failed":
                _motion.failed="force API failed; previous native effects retained"
                raise RuntimeError(_motion.failed)


def create_aero_callback(world, flapper_prim, flapper_view=None, config=None,
                         model_name=None):
    """
    工厂函数（三流型选择）。

    model_name / cfg.AERO_MODEL:
      attached_flow / dynamic_stall — 走 modules/aero/ 包（路线A/B）
      sane_dickinson（默认/包不可用兜底）— 本文件 FlapperAeroModel（路线C）
    """
    name = model_name or cfg.AERO_MODEL
    if name in ("attached_flow", "dynamic_stall", "sane_dickinson_3d"):
        try:
            from modules.aero import MODEL_REGISTRY
            cls = MODEL_REGISTRY[name]
            cb = AeroPhysicsCallback(world, flapper_prim, flapper_view, config)
            cb.model = cls(params=config)
            cb.model_name = name
            return cb
        except ImportError:
            logger.warning("modules/aero 包不可用，回退 FlapperAeroModel (sane_dickinson)")
    return AeroPhysicsCallback(world, flapper_prim, flapper_view, config)


# ============================================================
# 独立单元测试（无需 Isaac Sim，纯 NumPy）
# 运行：python aero_solver.py
# ============================================================

def _run_standalone_test() -> None:
    """Model 独立单元测试：验证气动力量级和方向。"""
    print("=" * 60)
    print("FlapperAeroModel 独立单元测试（纯 NumPy，无 Isaac）")
    print("=" * 60)

    # 昆虫级几何（本模型的匹配流型）
    model = FlapperAeroModel({"span": 0.10, "root_chord": 0.06, "tip_chord": 0.03})
    for w in (model.wing_left, model.wing_right):
        w.set_frequency(30.0)               # 必须用 set_frequency 同步 omega
        w.amplitude = np.radians(35)
        w.pitch_mode = "flip"
        w.pitch_amplitude = np.radians(40)

    mass = 0.020
    weight = mass * 9.81
    print(f"\n[几何] 单翼展长={model.span}m, 叶素数={model.n_strips}, "
          f"单翼面积={model.wing_area*1e4:.1f} cm²")
    print(f"[拍动] 频率={model.wing_left.frequency}Hz, "
          f"幅度={np.degrees(model.wing_left.amplitude):.1f}°")

    print("\n--- 测试 1: 静止悬停（v=0，仅拍动产生力）---")
    F, T = model.compute_forces(
        np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
        np.array([0, 0, 0.0]), np.array([0, 0, 0.0]), 0.0)
    print(f"  F_world = [{F[0]:+.4f}, {F[1]:+.4f}, {F[2]:+.4f}] N")
    print(f"  T_world = [{T[0]:+.4f}, {T[1]:+.4f}, {T[2]:+.4f}] N·m")
    print(f"  体重 = {weight:.4f} N (mass={mass*1e3:.1f}g)")

    print("\n--- 测试 2: 一个拍动周期内的平均升力 ---")
    period = 1.0 / model.wing_left.frequency
    n_samples = 100
    F_z_samples = []
    for i in range(n_samples):
        t_i = i * period / n_samples
        F_i, _ = model.compute_forces(
            np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
            np.array([0, 0, 0.0]), np.array([0, 0, 0.0]), t_i)
        F_z_samples.append(F_i[2])
    lift_avg = float(np.mean(F_z_samples))
    print(f"  周期平均升力 = {lift_avg:.4f} N")
    print(f"  平均升力/体重 = {lift_avg/weight:.2f}x")

    print("\n--- 测试 3: 前飞 v_x=5 m/s ---")
    # 纯阻力（拍动关闭）
    m0 = FlapperAeroModel({"span": 0.10, "root_chord": 0.06, "tip_chord": 0.03})
    m0.set_wing_command(frequency=0.001, amplitude_deg=0.001, pitch_amplitude_deg=0.001)
    F_d, _ = m0.compute_forces(
        np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
        np.array([5.0, 0, 0.0]), np.array([0, 0, 0.0]), 0.0)
    print(f"  关拍动纯阻力 Fx = {F_d[0]:+.4f} N （应为负=向后）")
    # 开拍动
    F_fwd, _ = model.compute_forces(
        np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
        np.array([5.0, 0, 0.0]), np.array([0, 0, 0.0]), 0.0)
    print(f"  开拍动 Fx = {F_fwd[0]:+.4f} N （正=推力>阻力，扑翼加速前飞）")

    # ── 测试 4: 700g 量级标定验收（读 config 几何/运动学，目标 1.0–1.3x）──
    print("\n--- 测试 4: 700g 量级标定（config 几何 + aero 攻角模式）---")
    m7 = FlapperAeroModel({
        "span": cfg.WingGeometryConfig.SPAN,
        "root_chord": cfg.WingGeometryConfig.ROOT_CHORD,
        "tip_chord": cfg.WingGeometryConfig.TIP_CHORD,
    })
    for w in (m7.wing_left, m7.wing_right):
        w.set_frequency(cfg.FlapKinematicsConfig.FREQUENCY)
        w.amplitude = np.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
        w.pitch_mode = cfg.FlapKinematicsConfig.PITCH_MODE
        w.alpha_set = np.radians(cfg.FlapKinematicsConfig.ALPHA_SET)
    mass7 = cfg.MASS
    weight7 = mass7 * 9.81
    period7 = 1.0 / cfg.FlapKinematicsConfig.FREQUENCY
    n7 = 300
    samples7 = []
    for i in range(n7):
        t_i = i * period7 / n7
        F_i, _ = m7.compute_forces(
            np.array([0, 0, 1.0]), np.array([1, 0, 0, 0]),
            np.array([0, 0, 0.0]), np.array([0, 0, 0.0]), t_i)
        samples7.append(F_i[2])
    lift7 = float(np.mean(samples7))
    print(f"  [几何] 半展={m7.span}m 单翼面积={m7.wing_area:.3f}m² ×2")
    print(f"  [拍动] f={cfg.FlapKinematicsConfig.FREQUENCY}Hz "
          f"amp={cfg.FlapKinematicsConfig.AMPLITUDE}° "
          f"α_set={cfg.FlapKinematicsConfig.ALPHA_SET}° (aero)")
    print(f"  体重 = {weight7:.2f} N (mass={mass7*1e3:.0f}g)")
    print(f"  周期平均升力 = {lift7:.2f} N → 升力/体重 = {lift7/weight7:.2f}x "
          f"({'✅ 达标 1.0~1.3' if 1.0 <= lift7/weight7 <= 1.3 else '⚠ 未达标，调 FREQUENCY/AMPLITUDE'})")

    print("\n" + "=" * 60)
    print("单元测试完成。")
    print("=" * 60)


if __name__ == "__main__":
    _run_standalone_test()
