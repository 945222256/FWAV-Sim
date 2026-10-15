# -*- coding: utf-8 -*-
"""
mock_backend.py 纯 Python 模拟仿真后端
======================================

本文件实现 modules.contracts.SimBackend 契约的模拟后端。MockBackend 类以
内置的单刚体六自由度积分器与九关节运动学替代 Isaac Sim 与 PhysX 的求解器。
本后端运行全程不导入任何 Isaac 模块。

复用声明如下。FlightController 类来自 modules.flight_controller，本后端按
main_sim.py 主循环的现行用法构造与调用它，不修改它的任何行为。气动求值复
用 modules.aero 包与 modules.aero_solver 模块的既有模型类，本后端不重新实
现力律。命令映射复刻 modules.wing_actuation._drive_wing_joints 的自动驾驶
仪分支。

一、单步时序。step 方法按主循环的同构次序执行六件事。第一件事为气动力求
值，求值协议复刻 aero_solver.AeroPhysicsCallback._call_inner 的第三段至第
三c段，含速度滤波、周期同步八点平均、尾翼滑流与力矩限幅。第二件事为刚体
积分。第三件事为场景钩子，供运行器在积分后与调度前改写状态，主循环的钉住
与释放序列经该钩子复现。第四件事为观测组装。第五件事为控制器调度，含主循
环持有的外部俯仰积分器。第六件事为命令映射。观测在积分后组装，因此观测已
含本步施加力的积分效果。

二、坐标系纪律。观测结构为 StateSource.read 的同构结构，八个必备量各带
frame 籍贯字段，取值与 modules.contracts.STATE_QUANTITY_FRAMES 的声明一致。
机体系与世界系之间的换算统一调用 StateSource 类的两个静态方法。

三、惯量与积分。单刚体惯量取对角阵，三个主惯量出自
docs/INERTIA_GEOMETRIC_20260930.md 的整机合成行，即二点七零二零四六乘十
的负三次方、四点二零五六零二乘十的负三次方与六点八零五三一一乘十的负三次
方千克平方米。积分格式为半隐式欧拉，线运动在世界系推进，角运动在机体系推
进，四元数按世界系角速度推进后归一化。物理步长取 config.sim_config 的
PHYSICS_DT 现行值。

四、与 Isaac 后端的已知差异。第一项为关节取理想位置随动，关节状态直接等
于位置目标，因此无伺服反作用力矩与关节瞬态。第二项为单刚体对角惯量，无连
杆相对运动的惯量耦合。第三项为每个物理步恰执行一次气动求值，无 Isaac 回
调的多次派发与倍率补偿机构。第四项为无地面接触、无系留绳与无障碍物。第五
项为气动力对刚体的施加点取质心，与现行回调不传位置的语义一致。
"""

from __future__ import annotations
from pathlib import Path

import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

import config.sim_config as cfg
from modules.contracts import (
    FRAME_BODY,
    FRAME_BODY_TO_WORLD,
    FRAME_JOINT,
    FRAME_WORLD,
    STATE_QUANTITY_FRAMES,
    SimBackend,
    StateSource,
)
from modules.aero_solver import (
    TailFuselageAero,
    WingKinematics,
    euler_to_quat,
    quat_to_rotation_matrix,
)


# 九关节名单按 fwav 十五自由度模型的 URDF 声明顺序排列。
JOINT_NAMES: Tuple[str, ...] = (
    "flap_L", "twist_L", "fold_L",
    "flap_R", "twist_R", "fold_R",
    "tail_yaw", "tail_L", "tail_R",
)

# 整机主惯量取自 docs/INERTIA_GEOMETRIC_20260930.md 的整机合成行。
INERTIA_DIAGONAL: Tuple[float, float, float] = (
    2.702046e-03, 4.205602e-03, 6.805311e-03)


def build_aero_model(aero_cfg: Optional[dict] = None):
    """按配置挑选气动模型类并构造实例。

    本函数复刻 modules.aero_solver.create_aero_callback 的模型选择逻辑。
    aero_cfg 字典可含 model_name 键与 params 键。缺省时模型名取
    config.sim_config.AERO_MODEL，参数取各模型类的缺省值。返回值为模型实例
    与模型名组成的二元组。
    """
    cfg_dict = dict(aero_cfg or {})
    name = cfg_dict.get("model_name") or cfg.AERO_MODEL
    params = cfg_dict.get("params")
    if name in ("attached_flow", "dynamic_stall", "sane_dickinson_3d"):
        from modules.aero import MODEL_REGISTRY
        cls = MODEL_REGISTRY[name]
        return cls(params=params), name
    from modules.aero_solver import FlapperAeroModel
    return FlapperAeroModel(params=params), name


class _MockStateSource(StateSource):
    """模拟后端的内部状态源。

    本类读取 MockBackend 持有的刚体与关节状态，并为每个量标注坐标系籍贯。
    """

    def __init__(self, backend: "MockBackend") -> None:
        self._backend = backend

    def read(self) -> dict:
        b = self._backend
        quat = [float(v) for v in b._quat]
        vel_world = [float(v) for v in b._vel_lin]
        vel_body = StateSource.world_to_body(vel_world, quat)
        omega_world = [float(v) for v in b._vel_ang]
        omega_body = StateSource.world_to_body(omega_world, quat)
        if b._ctrl_vel_filt is None:
            filt = list(vel_world)
        else:
            filt = [float(v) for v in b._ctrl_vel_filt]
        reading: Dict[str, dict] = {
            "t": float(b._t),
            "position": {"value": [float(v) for v in b._pos],
                         "frame": FRAME_WORLD},
            "quaternion": {"value": quat, "frame": FRAME_BODY_TO_WORLD},
            "velocity_linear": {"value": vel_world, "frame": FRAME_WORLD},
            "velocity_linear_body": {"value": vel_body, "frame": FRAME_BODY},
            "angular_velocity": {"value": omega_world,
                                 "frame": FRAME_WORLD},
            "angular_velocity_body": {"value": omega_body,
                                      "frame": FRAME_BODY},
            "joint_positions": {"value": [float(v) for v in b._joint_pos],
                                "frame": FRAME_JOINT,
                                "names": list(JOINT_NAMES)},
            "joint_velocities": {"value": [float(v) for v in b._joint_vel],
                                 "frame": FRAME_JOINT,
                                 "names": list(JOINT_NAMES)},
            "velocity_linear_filtered": {"value": filt,
                                         "frame": FRAME_WORLD},
        }
        return reading


class MockBackend(SimBackend):
    """纯 Python 模拟仿真后端。

    本类管理单刚体状态、九关节状态、气动模型、尾翼模型、拍动运动学与飞行
    控制器。step 方法推进一个物理步并返回观测字典。
    """

    def __init__(self) -> None:
        self._physics_dt: float = float(cfg.PHYSICS_DT)
        self._inertia = np.diag(np.asarray(INERTIA_DIAGONAL, dtype=float))
        self._gravity = np.asarray(cfg.GRAVITY, dtype=float)
        self._pos = np.zeros(3)
        self._quat = np.array([1.0, 0.0, 0.0, 0.0])
        self._vel_lin = np.zeros(3)
        self._vel_ang = np.zeros(3)
        self._joint_pos = np.zeros(len(JOINT_NAMES))
        self._joint_vel = np.zeros(len(JOINT_NAMES))
        self._joint_targets: Dict[str, float] = {}
        self._joint_targets_prev: Optional[Dict[str, float]] = None
        self._t = 0.0
        self._step_count = 0
        self.model = None
        self.model_name = ""
        self.tail_body = TailFuselageAero()
        self._wk: Optional[WingKinematics] = None
        self.controller = None
        self._state_source = _MockStateSource(self)
        # 气动求值协议的保持状态，语义与 AeroPhysicsCallback 的同名属性一致。
        self._v_air_hold = None
        self._pl_phase = 0.0
        self.held_body_vel = None
        self._ctrl_vel_filt = None
        self._fa_buf = None
        # 主循环持有的外部俯仰积分器状态。
        self.pitch_i_ext = 0.0
        # 诊断输出量。
        self.last_ap_out = None
        self.last_F_world = np.zeros(3)
        self.last_T_world = np.zeros(3)
        self.last_F_raw = np.zeros(3)
        self.last_T_raw = np.zeros(3)
        self.last_tail_F = np.zeros(3)
        self.last_tail_T = np.zeros(3)
        self.last_v_slip = 0.0
        self.last_clipped = False
        self.last_v_air = np.zeros(3)
        self.last_vel_lin = np.zeros(3)
        self.dA_cache = 0.0
        self.fold_cache = 0.0
        # 场景钩子在积分后与控制器调度前被调用，参数为当前仿真时刻。
        self.pre_control_hook = None
        self.motion_execution = None

    # ----------------------------------------------------------
    # 契约方法
    # ----------------------------------------------------------

    def setup(self, config: dict) -> None:
        """建立模拟世界与全部部件，每个运行期只调用一次。

        config 字典含五个键。physics_dt 为物理步长，单位为秒。scene 为场景
        配置字典。aero 为气动模型配置字典。assets 为资产路径字典，本后端不
        加载资产。headless 为布尔值，本后端无渲染，该键不产生作用。
        """
        cfg_dict = dict(config or {})
        self._physics_dt = float(cfg_dict.get("physics_dt",
                                              cfg.PHYSICS_DT))
        self.model, self.model_name = build_aero_model(
            cfg_dict.get("aero"))
        self.tail_body = TailFuselageAero()
        scene = cfg_dict.get("scene") or {}
        wdc = cfg.WING_DRIVE_CONFIG
        self._wk = WingKinematics(
            frequency=scene.get("wing_frequency", wdc["frequency"]),
            amplitude_deg=scene.get("wing_amplitude_deg",
                                    wdc["amplitude_deg"]),
            pitch_mode=wdc["pitch_mode"],
            alpha_set_deg=wdc["alpha_set_deg"],
        )
        from modules.flight_controller import FlightController
        self.controller = FlightController()

        from modules.sim_env_bridge import resolve_action_recipe
        from modules.motion_action.bridge import create_execution
        self.motion_execution = create_execution(resolve_action_recipe(),cfg,self._physics_dt)
        if self.motion_execution is not None:
            from modules.motion_action.aero_adapter import validate_external_model
            validate_external_model(self.model)

    def reset(self, seed: Optional[int] = None) -> None:
        """复位模拟世界，把时间归零并通知部件复位。

        刚体状态回到 config.sim_config 的 INITIAL_POSITION 与
        INITIAL_ORIENTATION，速度与关节状态归零。气动模型的相位积分状态、
        滤波保持状态与控制器全部回到冷启动。seed 参数被接受但不产生作用，
        原因是本后端不含随机源。
        """
        self._pos = np.asarray(cfg.INITIAL_POSITION, dtype=float).copy()
        self._quat = np.asarray(cfg.INITIAL_ORIENTATION,
                                dtype=float).copy()
        self._vel_lin = np.zeros(3)
        self._vel_ang = np.zeros(3)
        self._joint_pos = np.zeros(len(JOINT_NAMES))
        self._joint_vel = np.zeros(len(JOINT_NAMES))
        self._joint_targets = {}
        self._joint_targets_prev = None
        self._t = 0.0
        self._step_count = 0
        self._reset_aero_states()
        if self.motion_execution is not None:
            from modules.motion_action.bridge import create_execution
            self.motion_execution.close()
            self.motion_execution=create_execution(self.motion_execution.recipe,cfg,self._physics_dt)
        if self._wk is not None:
            wdc = cfg.WING_DRIVE_CONFIG
            self._wk.set_frequency(wdc["frequency"])
            self._wk._tau_state = 0.0
            self._wk._t_prev = None
        self.tail_body.set_command(0.0, 0.0, 0.0)
        if self.controller is not None:
            from modules.flight_controller import FlightController
            self.controller = FlightController()
        self.pitch_i_ext = 0.0
        self.last_ap_out = None

    def step(self, actions: Optional[dict] = None) -> dict:
        """推进一个物理步并返回观测字典。

        actions 取 None 时本后端调度内部飞行控制器。actions 为指令字典时本
        后端按其 source 键取自动驾驶仪、手动或怠速三类口径。返回值与
        StateSource.read 的返回结构同构。
        """
        dt = self._physics_dt
        if self.motion_execution is not None:
            if self.motion_execution.journal is None:
                self.motion_execution.attach_journal(Path(cfg.LOG_DIR)/("motion_action_%s.jsonl"%self.motion_execution.run_id),
                    detailed=cfg.ActionExpressionConfig.RECORD_ACTIONS)
            frame=self.motion_execution.prepare_step(self._step_count+1)
            mapping={r.joint:r for r in self.motion_execution.recipe.channels}
            self._joint_targets={j:(-1. if mapping[j].mirrored else 1.)*c.value
                for j,c in frame.channels.items() if c.presence in ("value","zero")}
            self._joint_pos=np.asarray([self._joint_targets.get(j,self._joint_pos[k]) for k,j in enumerate(JOINT_NAMES)],dtype=float)
            self._joint_vel=np.asarray([(-1. if mapping[j].mirrored else 1.)*frame.channels[j].velocity
                if frame.channels[j].velocity is not None else float("nan") for j in JOINT_NAMES])
            left,right,rudder=[frame.channels[j].value for j in ("tail_L","tail_R","tail_yaw")]
            self.tail_body.set_command((left+right)/2.,(left-right)/2.,rudder)
            from modules.motion_action.joint_adapter import JointWriteReceipt
            receipt=JointWriteReceipt(frame.step,"ideal_following",
                {j:dict(status="ideal_state_written" if j in self._joint_targets else c.presence,
                    requested=c.value,native_value=self._joint_targets.get(j)) for j,c in frame.channels.items()},
                {"ideal_following":{"status":"ideal_state_written"}},
                {"status":"api_accepted"},False)
            self.motion_execution.record_consumption(receipt)
        # 第一件事为时间推进。气动求值时刻与主循环的回调时刻同口径。
        self._t += dt
        self._step_count += 1
        # 第二件事为气动力求值。
        try:
            f_world, t_world = self._aero_evaluate(dt)
        except Exception as exc:
            if self.motion_execution is not None:
                self.motion_execution.failed="force evaluation failed: %r"%exc
            raise
        # 第三件事为刚体积分。
        self._integrate(f_world, t_world, dt)
        if self.motion_execution is not None:
            self.motion_execution.journal.append(dict(kind="mock_force_consumed",
                run_id=self.motion_execution.run_id,segment=self.motion_execution.segment,
                global_step=self._step_count,frame_step=self.motion_execution.read().step,
                force=f_world,torque=t_world,window=self.motion_force_window,
                solver="ideal_single_body_integrator",solver_consumption_step=self._step_count))
        # 第四件事为场景钩子。钉住与释放序列经该钩子作用于积分后的状态。
        if self.pre_control_hook is not None:
            self.pre_control_hook(float(self._t))
        # 第五件事为观测组装与校验。
        obs = self._state_source.read()
        problems = StateSource.verify_frames(obs)
        if problems:
            raise RuntimeError("观测坐标系籍贯校验失败：" + "; ".join(problems))
        # 第六件事为控制器调度与命令映射。
        self._schedule_control(obs, actions, dt)
        return obs

    def close(self) -> None:
        """释放资源。本后端不持有文件句柄与网络连接，本方法只清空引用。

        关闭前必须落盘未写完的记录这一契约条款对纯内存后端不适用，原因是
        本后端不产生记录。
        """
        if self.motion_execution is not None:
            self.motion_execution.close()
        self.controller = None
        self.model = None
        self._wk = None

    # ----------------------------------------------------------
    # 气动求值协议
    # ----------------------------------------------------------

    def _reset_aero_states(self) -> None:
        """清空气动求值协议的滤波与保持状态。"""
        self._v_air_hold = None
        self._pl_phase = 0.0
        self.held_body_vel = None
        self._ctrl_vel_filt = None
        self._fa_buf = None

    def _aero_evaluate(self, dt: float) -> Tuple[np.ndarray, np.ndarray]:
        """按现行回调协议求世界系气动力与气动力矩。

        本方法复刻 AeroPhysicsCallback._call_inner 的第三段至第三c段。输入
        状态取积分前的当前状态，与 Isaac 回调在步首读取的口径一致。
        """
        pos_world = self._pos.copy()
        quat_world = self._quat.copy()
        vel_linear = self._vel_lin.copy()
        vel_angular = self._vel_ang.copy()

        # 相对风速合成。
        v_air = vel_linear
        try:
            if cfg.WIND_ENABLED:
                v_air = vel_linear - np.asarray(cfg.WIND_VECTOR,
                                                dtype=float)
        except Exception:
            pass
        # 试验模式的状态锁定分支。现行配置的试验模式为空串，本分支不被触发。
        if getattr(cfg, "EXPERIMENT_MODE", ""):
            free = tuple(getattr(cfg, "BENCH_FREE", ())) \
                if getattr(cfg, "BENCH_RELEASE", False) else ()
            r, p, y = _quat_to_euler(quat_world)
            if "roll" not in free:
                r = 0.0
            if "pitch" not in free:
                p = np.radians(float(getattr(cfg, "BENCH_PITCH_DEG", 0.0)))
            if "yaw" not in free:
                y = 0.0
            quat_world = euler_to_quat(r, p, y)
            pos_world = np.array([0.0, 0.0, cfg.DP_TARGET_Z])
            vl = np.asarray(vel_linear, dtype=float).copy()
            if "x" not in free:
                vl[0] = 0.0
            vl[1] = 0.0
            vl[2] = 0.0
            va = np.asarray(vel_angular, dtype=float).copy()
            if "roll" not in free:
                va[0] = 0.0
            if "pitch" not in free:
                va[1] = 0.0
            if "yaw" not in free:
                va[2] = 0.0
            vel_angular = va
            v_air = vl - np.asarray(cfg.WIND_VECTOR, dtype=float) \
                if cfg.WIND_ENABLED else vl
        self.last_v_air = np.asarray(v_air, dtype=float).copy()
        self.last_vel_lin = vel_linear.copy()

        # 拍周期采样保持。保持值按现行代码不再进入力模型，本段为口径复刻。
        f_pl = max(float(getattr(self.model.wing_left, "frequency", 1.55)),
                   0.2)
        pl_prev = float(self._pl_phase)
        self._pl_phase = ((self.motion_execution.service.read().chi_L % 1.)
            if self.motion_execution is not None else (pl_prev + f_pl * dt) % 1.0)
        if self._pl_phase < pl_prev or self._v_air_hold is None:
            self._v_air_hold = np.asarray(v_air, dtype=float).copy()

        # 控制器与力模型共用的速度滤波，时间常数为零点一秒。
        if self._ctrl_vel_filt is None:
            self._ctrl_vel_filt = np.asarray(vel_linear,
                                             dtype=float).copy()
        else:
            self._ctrl_vel_filt = self._ctrl_vel_filt \
                + min(1.0, dt / 0.1) * (np.asarray(vel_linear, dtype=float)
                                        - self._ctrl_vel_filt)
        self.held_body_vel = self._ctrl_vel_filt.copy()
        v_air = (self._ctrl_vel_filt
                 - np.asarray(cfg.WIND_VECTOR, dtype=float)
                 if getattr(cfg, "WIND_ENABLED", False)
                 else np.asarray(self._ctrl_vel_filt, dtype=float)).copy()

        if self.motion_execution is not None:
            from modules.motion_action.aero_adapter import compute_action_window
            f_world,t_world,self.motion_force_window=compute_action_window(
                self.model,self.motion_execution,pos_world,quat_world,v_air,vel_angular)
        else:
            # 力模型首评估推进相位，其结果被后续平均覆盖。
            f_world, t_world = self.model.compute_forces(
                pos_world, quat_world, v_air, vel_angular, self._t)
            # 周期同步八点平均。相位状态在平均前后做快照与恢复。
            f_now = float(getattr(self.model.wing_left, "frequency", 1.55))
            wl, wr = self.model.wing_left, self.model.wing_right
            snap = ((wl._tau_state, wl._t_prev), (wr._tau_state, wr._t_prev))
            k_avg = 8
            t_per = 1.0 / max(f_now, 0.2)
            fs = np.zeros(3)
            ts = np.zeros(3)
            for k in range(k_avg):
                fk, tk = self.model.compute_forces(
                    pos_world, quat_world, v_air, vel_angular,
                    self._t + k * t_per / k_avg)
                fs += fk
                ts += tk
            (wl._tau_state, wl._t_prev), (wr._tau_state, wr._t_prev) = snap
            f_world, t_world = fs / k_avg, ts / k_avg

        # 尾翼与机身阻力。滑流速度按拍频一阶动量理论口径合成。
        ft = np.zeros(3)
        tt = np.zeros(3)
        try:
            v_slip = 0.0
            if getattr(cfg, "ENABLE_TAIL_SLIPSTREAM", False):
                f_slip = (self.motion_execution.service.read().frequency_hz+self.motion_execution.service.read().dfreq_hz
                    if self.motion_execution is not None else float(getattr(self.model.wing_left, "frequency",0.0)))
                v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f_slip
            ft, tt = self.tail_body.compute(quat_world, v_air,
                                            v_slip=v_slip)
            f_world = f_world + ft
            t_world = t_world + tt
            self.last_tail_F = ft.copy()
            self.last_tail_T = tt.copy()
            self.last_v_slip = float(v_slip)
        except Exception as exc:
            _log_warning("尾翼与机身气动计算失败: %s" % exc)

        # 力与力矩限幅。
        self.last_F_raw = f_world.copy()
        self.last_T_raw = t_world.copy()
        f_max = (30.0 if getattr(cfg, "EXPERIMENT_MODE", "")
                 else 3.0) * cfg.MASS * 9.81
        f_norm = float(np.linalg.norm(f_world))
        if f_norm > f_max:
            f_world = f_world * (f_max / f_norm)
        t_max = 60.0 if getattr(cfg, "EXPERIMENT_MODE", "") else 2.0
        t_norm = float(np.linalg.norm(t_world))
        if t_norm > t_max:
            t_world = t_world * (t_max / t_norm)
        self.last_clipped = bool(f_norm > f_max or t_norm > t_max)
        self.last_F_world = f_world
        self.last_T_world = t_world
        return f_world, t_world

    # ----------------------------------------------------------
    # 刚体积分
    # ----------------------------------------------------------

    def _integrate(self, f_world: np.ndarray, t_world: np.ndarray,
                   dt: float) -> None:
        """以半隐式欧拉推进单刚体六自由度状态一步。"""
        acc = f_world / cfg.MASS + self._gravity
        self._vel_lin = self._vel_lin + acc * dt
        self._pos = self._pos + self._vel_lin * dt
        r_bw = quat_to_rotation_matrix(self._quat)
        torque_body = r_bw.T @ np.asarray(t_world, dtype=float)
        omega_body = r_bw.T @ self._vel_ang
        gyro = np.cross(omega_body, self._inertia @ omega_body)
        omega_dot = np.linalg.solve(self._inertia, torque_body - gyro)
        omega_body = omega_body + omega_dot * dt
        self._vel_ang = r_bw @ omega_body
        # 四元数按世界系角速度推进。
        w0, x0, y0, z0 = (float(self._quat[0]), float(self._quat[1]),
                          float(self._quat[2]), float(self._quat[3]))
        wx, wy, wz = (float(self._vel_ang[0]), float(self._vel_ang[1]),
                      float(self._vel_ang[2]))
        qd = np.array([
            -0.5 * (x0 * wx + y0 * wy + z0 * wz),
            0.5 * (w0 * wx + y0 * wz - z0 * wy),
            0.5 * (w0 * wy + z0 * wx - x0 * wz),
            0.5 * (w0 * wz + x0 * wy - y0 * wx)])
        q_new = np.asarray(self._quat, dtype=float) + qd * dt
        q_norm = float(np.linalg.norm(q_new))
        if q_norm > 1e-9:
            self._quat = q_new / q_norm

    # ----------------------------------------------------------
    # 控制器调度与命令映射
    # ----------------------------------------------------------

    def _schedule_control(self, obs: dict,
                          actions: Optional[dict], dt: float) -> None:
        """调度飞行控制器并把指令映射为关节目标与尾舵命令。

        actions 取 None 时本方法走自动驾驶仪口径。actions 含 source 键时本
        方法按其取值分路。外部俯仰积分器的写回与更新复刻主循环的现行逻辑。
        """
        man_ch = None
        if isinstance(actions, dict) and actions.get("source") == "manual":
            man_ch = actions
        ap_out = None
        if (actions is None or
                (isinstance(actions, dict)
                 and actions.get("source") == "autopilot")):
            if self.controller is not None:
                pose7 = self._pose7(obs)
                vel6 = self._vel6(obs)
                # 外部俯仰积分器先写回控制器。
                try:
                    self.controller._pitch_i = float(self.pitch_i_ext)
                except Exception:
                    pass
                if self.motion_execution is None:
                    ap_out = self.controller.update(pose7,vel6,t_sim=float(self._t))
                else:
                    ap_out = self.controller.update(pose7,vel6,t_sim=float(self._t),
                        motion_context=self.motion_execution.controller_context())
                self._update_pitch_integrator(ap_out, pose7, vel6, dt)
        if self.motion_execution is not None and isinstance(actions,dict) and actions.get("source")=="external":
            self.motion_execution.enqueue(after_step=self._step_count,
                command=dict(actions["command"]),frequencies=dict(actions["frequencies"]),source="external")
            self.last_ap_out=None
            return
        self.last_ap_out = ap_out
        self._drive(float(self._t), ap_out, man_ch)

    def _update_pitch_integrator(self, ap_out: Optional[dict],
                                 pose7: List[float],
                                 vel6: List[float], dt: float) -> None:
        """更新主循环持有的外部俯仰积分器。

        本方法复刻 main_sim.py 主循环在外部积分器上的现行逻辑，含误差门、
        接地联锁、俯仰率前置项与正负二点五限幅。
        """
        try:
            q0, q1, q2, q3 = pose7[3], pose7[4], pose7[5], pose7[6]
            pitch_q = math.asin(max(-1.0, min(1.0,
                                              2.0 * (q0 * q2 - q3 * q1))))
            perr = float(ap_out.get("pitch_des", 0.0)) - pitch_q
            if perr > math.pi:
                perr -= 2.0 * math.pi
            elif perr < -math.pi:
                perr += 2.0 * math.pi
            ground_z = float(cfg.FlightControllerConfig.GROUND_Z)
            grounded = (float(pose7[2]) < ground_z
                        and abs(float(vel6[2])) < 0.5
                        and abs(float(vel6[0])) < 2.0)
            i0 = float(self.pitch_i_ext)
            wy_ahead = float(os.environ.get("FLAPPER_WY_AHEAD", "0.0")
                             or 0.0)
            perr_eff = perr - wy_ahead * float(vel6[4])
            if abs(perr_eff) < 0.400000 and not grounded:
                self.pitch_i_ext = float(np.clip(i0 + perr_eff * dt,
                                                 -2.5, 2.5))
        except Exception:
            pass

    def _drive(self, t: float, ap_out: Optional[dict],
               man_ch: Optional[dict]) -> None:
        """把控制器输出映射为九关节目标并注入气动模型。

        本方法复刻 _drive_wing_joints 的自动驾驶仪分支与手动分支的数值路
        径，含拍幅软启动、频率同步、频率差动注入、翼幅差动注入、闸门注入、
        滞后差动注入与尾舵命令写出。
        """
        if self.motion_execution is not None:
            from modules.motion_action.execution import enqueue_controller_demand
            enqueue_controller_demand(self.motion_execution,cfg,after_step=self._step_count,
                                      ap_out=ap_out,manual=man_ch,backup=None)
            return
        wdc = cfg.WING_DRIVE_CONFIG
        w = self._wk.evaluate(t)
        phi, alpha = w["phi"], w["alpha"]
        fold = wdc["fold_amp"] * 0.5 * (1.0 + math.sin(self._wk.omega * t
                                                       + 2.2))
        ramp = min(1.0, t / max(cfg.STARTUP_AMP_RAMP_S, 0.05))
        phi, alpha, fold = phi * ramp, alpha * ramp, fold * ramp

        if man_ch is not None and (getattr(cfg, "EXPERIMENT_MODE", "")
                                   or abs(man_ch["throttle"]) > 0.05):
            freq = cfg.MANUAL_FREQ_MIN \
                + (cfg.MANUAL_FREQ_MAX - cfg.MANUAL_FREQ_MIN) \
                * (man_ch["throttle"] + 1.0) / 2.0
            self._wk.set_frequency(freq)
        elif ap_out is not None:
            # 有效频率选择（事项 I-210）：与 wing_actuation 同一实现，
            # 默认关闭时与原路径逐位一致；下方气动同步与 dfreq 差动读
            # self._wk.frequency，随之统一。
            from modules.wing_actuation import select_effective_freq
            _f_eff, _f_nom, _f_src = select_effective_freq(ap_out)
            self._wk.set_frequency(_f_eff)
            self._freq_sel = (_f_nom, _f_eff, _f_src)

        # 力模型拍频同步。
        self.model.set_wing_command(frequency=float(self._wk.frequency))
        # 频率差动注入。
        if ap_out is not None:
            dfq = float(ap_out.get("dfreq", 0.0) or 0.0)
            if abs(dfq) > 1e-12:
                fc = float(self._wk.frequency)
                self.model.wing_left.set_frequency(fc + dfq)
                self.model.wing_right.set_frequency(fc - dfq)

        if ap_out is not None:
            elev = ap_out["elevator"]
            ail = ap_out["aileron"]
            rudd = ap_out["rudder"]
            tail_l, tail_r = elev + ail, elev - ail
            tail_yaw = rudd
        elif man_ch is not None:
            elev = cfg.MANUAL_ELEV_GAIN * man_ch["elevator"]
            ail = cfg.MANUAL_AIL_GAIN * man_ch["aileron"]
            rudd = cfg.MANUAL_RUDD_GAIN * man_ch["rudder"]
            tail_l, tail_r = elev + ail, elev - ail
            tail_yaw = rudd
        else:
            tail_yaw = wdc["tail_yaw_amp"] \
                * math.sin(self._wk.omega * t * 0.5)
            tail_l = tail_r = wdc["tail_trim"]

        da = 0.0
        if ap_out is not None:
            da = ap_out.get("dA", 0.0)
        elif man_ch is not None and "da" in man_ch:
            da = float(man_ch.get("da", 0.0))
        self.dA_cache = float(da)
        self.fold_cache = float(fold)

        # 翼幅差动注入气动模型的左右翼幅。
        a0 = float(cfg.FlapKinematicsConfig.AMPLITUDE) * math.pi / 180.0
        self.model.wing_left.amplitude = a0 * ramp + da
        self.model.wing_right.amplitude = a0 * ramp - da
        # 逐拍闸门注入。
        if ap_out is not None:
            self.model.wing_left.stroke_gate = float(
                ap_out.get("gate_L", 1.0))
            self.model.wing_right.stroke_gate = float(
                ap_out.get("gate_R", 1.0))
        # 滞后差动注入。
        if ap_out is not None and ap_out.get("dlag"):
            lag0 = float(cfg.FlapKinematicsConfig.PITCH_LAG_S)
            dl = float(ap_out["dlag"])
            self.model.wing_left.pitch_lag_s = lag0 + dl
            self.model.wing_right.pitch_lag_s = lag0 - dl

        target = {
            "flap_L": phi + da, "flap_R": -(phi - da),
            "twist_L": alpha, "twist_R": alpha,
            "fold_L": fold, "fold_R": -fold,
            "tail_yaw": tail_yaw, "tail_L": tail_l, "tail_R": tail_r,
        }
        self._write_joint_targets(target)

        # 尾舵命令写出。
        if ap_out is not None:
            self.tail_body.set_command(
                elevator=ap_out["elevator"],
                aileron=ap_out["aileron"],
                rudder=ap_out["rudder"])
        elif man_ch is not None:
            self.tail_body.set_command(
                elevator=cfg.MANUAL_ELEV_GAIN * man_ch["elevator"],
                aileron=cfg.MANUAL_AIL_GAIN * man_ch["aileron"],
                rudder=cfg.MANUAL_RUDD_GAIN * man_ch["rudder"])
        else:
            self.tail_body.set_command(0.0, 0.0, 0.0)

    def _write_joint_targets(self, target: Dict[str, float]) -> None:
        """按理想位置随动把目标写入关节状态。

        关节位置等于位置目标。关节速度取相邻两步目标的一阶差分除以步长。
        """
        prev = self._joint_targets_prev
        dt = self._physics_dt
        pos_new = np.zeros(len(JOINT_NAMES))
        vel_new = np.zeros(len(JOINT_NAMES))
        for i, name in enumerate(JOINT_NAMES):
            tgt = float(target.get(name, 0.0))
            pos_new[i] = tgt
            if prev is not None and name in prev:
                vel_new[i] = (tgt - float(prev[name])) / dt
            else:
                vel_new[i] = 0.0
        self._joint_pos = pos_new
        self._joint_vel = vel_new
        self._joint_targets_prev = dict(target)
        self._joint_targets = dict(target)

    # ----------------------------------------------------------
    # 场景辅助方法
    # ----------------------------------------------------------

    def set_body_state(self, position: List[float],
                       quaternion: List[float],
                       velocity6: List[float]) -> None:
        """直接改写刚体状态。钉住与发射序列经本方法注入状态。"""
        self._pos = np.asarray(position, dtype=float).copy()
        self._quat = np.asarray(quaternion, dtype=float).copy()
        q_norm = float(np.linalg.norm(self._quat))
        if q_norm > 1e-9:
            self._quat = self._quat / q_norm
        v6 = np.asarray(velocity6, dtype=float)
        self._vel_lin = v6[0:3].copy()
        self._vel_ang = v6[3:6].copy()

    def pin_wing_joints(self, t_sim: float) -> None:
        """把九关节状态写成 t_sim 时刻的解析规定值。

        本方法复刻 main_sim.py 的 _pin_wing_joints 函数。拍频锁定取
        FlightControllerConfig.FREQ_HOVER，相位按解析式给定，幅值走软启动
        斜坡。
        """
        if self.motion_execution is not None:
            self.motion_execution._pinned=True
            return
        if self._wk is None:
            return
        wdc = cfg.WING_DRIVE_CONFIG
        f0 = float(getattr(cfg.FlightControllerConfig, "FREQ_HOVER", 1.55))
        tau0 = (f0 * t_sim) % 1.0
        self._wk.set_frequency(f0)
        self._wk._tau_state = tau0
        self._wk._t_prev = t_sim
        if self.model is not None:
            for wing in (self.model.wing_left, self.model.wing_right):
                wing.set_frequency(f0)
                wing._tau_state = tau0
                wing._t_prev = t_sim
        ev = self._wk.evaluate(t_sim)
        ramp_s = max(float(cfg.STARTUP_AMP_RAMP_S), 0.05)
        ramp = min(1.0, t_sim / ramp_s)
        rampd = (1.0 / ramp_s) if t_sim < ramp_s else 0.0
        phi, alp = ev["phi"] * ramp, ev["alpha"] * ramp
        phid = ev["phi_dot"] * ramp + ev["phi"] * rampd
        alpd = ev["alpha_dot"] * ramp + ev["alpha"] * rampd
        fold0 = wdc["fold_amp"] * 0.5
        foldw = 1.0 + math.sin(self._wk.omega * t_sim + 2.2)
        fold = fold0 * foldw * ramp
        foldd = fold0 * (self._wk.omega * math.cos(self._wk.omega * t_sim
                                                   + 2.2) * ramp
                         + foldw * rampd)
        trim = wdc["tail_trim"]
        pos_map = {"flap_L": phi, "flap_R": -phi,
                   "twist_L": alp, "twist_R": alp,
                   "fold_L": fold, "fold_R": -fold,
                   "tail_yaw": 0.0, "tail_L": trim, "tail_R": trim}
        vel_map = {"flap_L": phid, "flap_R": -phid,
                   "twist_L": alpd, "twist_R": alpd,
                   "fold_L": foldd, "fold_R": -foldd,
                   "tail_yaw": 0.0, "tail_L": 0.0, "tail_R": 0.0}
        self._joint_pos = np.asarray(
            [float(pos_map.get(n, 0.0)) for n in JOINT_NAMES])
        self._joint_vel = np.asarray(
            [float(vel_map.get(n, 0.0)) for n in JOINT_NAMES])
        self._joint_targets = dict(pos_map)

    def reset_aero_states_for_launch(self) -> None:
        """释放时刻清空气动求值协议的保持与滤波状态。"""
        self._v_air_hold = None
        self.held_body_vel = None
        self._fa_buf = []
        self._ctrl_vel_filt = None
        self._pl_phase = 0.0

    def reset_controller_for_launch(self) -> None:
        """释放时刻复位控制器的积分与滤波状态。

        本方法复刻 main_sim.py 释放块的复位清单，含总比能积分、拍频状态、
        俯仰目标滤波、俯仰积分、俯仰指令锚点与外环速度滤波。
        """
        ap = self.controller
        if ap is None:
            return
        try:
            ap._ei = 0.0
            ap._freq_last = float(getattr(cfg.FlightControllerConfig,
                                          "FREQ_HOVER", 1.55))
            ap._pitch_tgt_filt = None
            ap._pitch_i = 0.0
            ap._pd_last = float(getattr(cfg, "LAUNCH_PITCH", 0.0))
            ap._pv_filt = None
        except Exception:
            pass
        self.pitch_i_ext = 0.0

    # ----------------------------------------------------------
    # 观测辅助
    # ----------------------------------------------------------

    @staticmethod
    def _pose7(obs: dict) -> List[float]:
        """从观测字典拼接控制器所需的七元位姿。"""
        pos = obs["position"]["value"]
        quat = obs["quaternion"]["value"]
        return [float(v) for v in pos] + [float(v) for v in quat]

    @staticmethod
    def _vel6(obs: dict) -> List[float]:
        """从观测字典拼接控制器所需的六元速度。

        线速度取滤波保持值，角速度取世界系原值，拼接口径与主循环一致。
        """
        if "velocity_linear_filtered" in obs:
            lin = obs["velocity_linear_filtered"]["value"]
        else:
            lin = obs["velocity_linear"]["value"]
        ang = obs["angular_velocity"]["value"]
        return [float(v) for v in lin] + [float(v) for v in ang]


def _quat_to_euler(quat: np.ndarray):
    """四元数 w x y z 转欧拉角，公式与 aero_solver 的同名函数一致。"""
    w, x, y, z = (float(quat[0]), float(quat[1]), float(quat[2]),
                  float(quat[3]))
    roll = np.arctan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = np.arcsin(np.clip(2.0 * (w * y - z * x), -1.0, 1.0))
    yaw = np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return roll, pitch, yaw


def _log_warning(message: str) -> None:
    """以标准库日志记录警告信息。"""
    import logging
    logging.getLogger(__name__).warning("%s", message)


__all__ = ["MockBackend", "JOINT_NAMES", "INERTIA_DIAGONAL",
           "build_aero_model"]
