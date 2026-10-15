# -*- coding: utf-8 -*-
"""I-186：记录同次运行的钉住、释放及实际对象读数。

只读挂点与原动作的成功/失败回执，不增加物理步或控制写入。配置取值、
消费者采用值与实测状态分别保存；隔离桩检查只证明接口，不能冒充飞行。
"""
import json
import math
import os
import time
from modules.experiment_pipeline import scene_witness as SW

ENV_ARCHIVE = "I186_ARCHIVE_DIR"
ENV_EXPERIMENT = "I186_EXPERIMENT_ID"
ENV_RUN = "I186_RUN_ID"
TOOL = "modules/experiment_pipeline/runtime_observer.py"
_CONTROLLER_ATTRS = ("_ei", "_freq_last", "_pitch_tgt_filt", "_pitch_i", "_pv_filt",
                     "_pd_last", "_xi", "_zi", "_roll_i", "_beta_i")
_RELEASE_ACTIONS = ("launch_velocity", "aero_reset", "controller_reset", "wing_snapshot",
                    "filter_phase_reset", "pitch_anchor", "launch_attitude", "perturb_qw", "perturb_roll")


def identity_from_env():
    values = tuple(os.environ.get(k) for k in (ENV_ARCHIVE, ENV_EXPERIMENT, ENV_RUN))
    return values if all(values) else None


def _guarded(fn, label):
    try:
        return fn(), None
    except Exception as exc:
        return None, "%s 读取失败：%r" % (label, exc)


def _json_value(value):
    """Normalize numpy values at the archive boundary, without changing values."""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("nonfinite runtime value")
        return value
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    raise TypeError("unsupported runtime value %s" % type(value).__name__)


def read_mav_state(mav_view):
    import numpy as np
    def read():
        pos, quat = mav_view.get_world_poses()
        pos = np.asarray(pos, dtype=np.float64).reshape(-1)
        quat = np.asarray(quat, dtype=np.float64).reshape(-1)
        vel = np.asarray(mav_view.get_velocities(), dtype=np.float64).reshape(-1)
        if pos.size != 3 or quat.size != 4 or vel.size != 6:
            raise ValueError("single-body position/orientation/velocity shape mismatch")
        if not all(np.isfinite(v).all() for v in (pos, quat, vel)) or not np.any(quat != 0):
            raise ValueError("nonfinite body state or zero quaternion")
        return {"position_world_m": pos.tolist(), "orientation_quaternion_wxyz": quat.tolist(),
                "linear_velocity": vel[:3].tolist(), "angular_velocity": vel[3:].tolist()}
    return _guarded(read, "mav_view 位姿/速度")


def read_controller_state(autopilot, cfg_mod=None):
    def read():
        if autopilot is None:
            raise ValueError("autopilot 对象不存在（None）")
        state = {name: _json_value(getattr(autopilot, name, None)) for name in _CONTROLLER_ATTRS}
        for name in ("_ei", "_freq_last"):
            if not isinstance(state[name], (int, float)) or isinstance(state[name], bool):
                raise ValueError("required controller state %s absent/invalid" % name)
        out = {"state": state}
        if cfg_mod is not None:
            cls = getattr(cfg_mod, "FlightControllerConfig", None)
            if cls is None:
                raise ValueError("effective FlightControllerConfig missing")
            parameters = {name: _json_value(getattr(cls, name)) for name in dir(cls) if name.isupper()}
            if not parameters:
                raise ValueError("effective controller parameter set empty")
            out["parameters"] = parameters
        out["instance_parameters"] = {name: _json_value(getattr(autopilot, name))
                                      for name in ("f0", "zr", "xr", "yr", "yaw_ref")
                                      if hasattr(autopilot, name)}
        return out
    return _guarded(read, "autopilot 活对象状态及生效参数")


def read_inflow(cfg_mod):
    def read():
        enabled = getattr(cfg_mod, "WIND_ENABLED", None)
        if not isinstance(enabled, bool):
            raise ValueError("WIND_ENABLED missing or nonboolean")
        configured = _json_value(getattr(cfg_mod, "WIND_VECTOR", None))
        if not isinstance(configured, list) or len(configured) != 3 or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in configured):
            raise ValueError("WIND_VECTOR missing or invalid")
        return {"wind_enabled": enabled, "configured_flow_world_m_per_s": configured,
                "ambient_flow_world_m_per_s": configured if enabled else [0.0, 0.0, 0.0]}
    return _guarded(read, "config.WIND_ENABLED/WIND_VECTOR 实际采用条件")


def read_dof_state(pinned):
    # Only after actual receipt checks: this is the software holding schedule,
    # not a USD joint-lock query or a claim of measured physical controllability.
    return {k: not pinned for k in ("tx", "ty", "tz", "rx", "ry", "rz")}


def _action_status(actions, required):
    if not isinstance(actions, dict):
        return "unknown"
    for name in required:
        receipt = actions.get(name)
        if not isinstance(receipt, dict):
            return "unknown"
        if receipt.get("query_error") or receipt.get("status") == "failed":
            return "failed"
        if receipt.get("status") == "not_applicable":
            if name in ("body_pin", "wing_pin", "launch_velocity", "wing_snapshot"):
                return "unknown"
            if not all(isinstance(receipt.get(k), str) and receipt[k].strip() for k in ("scope", "reason")):
                return "unknown"
        elif receipt.get("status") != "succeeded":
            return "unknown"
    return "failed" if any(isinstance(v, dict) and v.get("status") == "failed"
                           for v in actions.values()) else "succeeded"


def _wrapped(value, source, stage, sim_t, error=None):
    return {"value": value, "source": source, "stage": stage,
            "observed_at_sim_t": sim_t, "query_error": error}


def _environment(cfg_mod, manager, stage, sim_t, actions=None, runtime_context=None):
    flow, error = read_inflow(cfg_mod)
    source = "main_sim.py:%s actual config; selection: modules/aero_solver.py" % stage
    wind = _wrapped(flow["ambient_flow_world_m_per_s"] if flow else None, source, stage, sim_t, error)
    if flow:
        wind.update({k: v for k, v in flow.items() if k != "ambient_flow_world_m_per_s"})
    perturbations = ({k: actions[k] for k in ("perturb_qw", "perturb_roll") if k in actions}
                     if isinstance(actions, dict) else {})
    known = _action_status(perturbations, ("perturb_qw", "perturb_roll")) == "succeeded"
    context = runtime_context if isinstance(runtime_context, dict) else {}
    side_pulse_enabled = context.get("_SIDE_PULSE_ON")
    # 侧滑脉冲发生在翼驱动消费者；释放时不能把尚未执行的计划当作无扰动。
    perturbation_error = None
    if not isinstance(side_pulse_enabled, bool):
        perturbation_error = "缺少环境桥实际采用的侧滑脉冲开关"
    elif side_pulse_enabled:
        perturbation_error = "侧滑脉冲已启用，当前钉放观察无该消费者的执行回执"
    elif bool(getattr(cfg_mod, "EXPERIMENT_MODE", None)):
        perturbation_error = "实验排程通道启用，当前钉放观察不证明全部扰动执行结果"
    elif not known:
        perturbation_error = "释放扰动动作结果尚未知"
    known = known and perturbation_error is None
    inflow = {"ambient_flow_world_m_per_s": wind, "perturbation_policy": {
        "value": perturbations, "injected": (any(v.get("status") == "succeeded"
                                                for v in perturbations.values()) if known else None),
        "side_pulse_enabled": side_pulse_enabled,
        "side_pulse_scheduled_at_s": context.get("_SIDE_PULSE_T"),
        "side_pulse_requested_dvy_m_per_s": context.get("_SIDE_PULSE_DVY"),
        "source": "main_sim.py释放扰动写入回执及环境桥实际采用的侧滑开关", "stage": stage,
        "observed_at_sim_t": sim_t, "query_error": perturbation_error}}
    constraints = {}
    try:
        receipt = context.get("_I186_TETHER_RESULT")
        if not isinstance(receipt, dict) or not isinstance(receipt.get("selected"), bool):
            raise ValueError("系留消费者尚无本次执行回执")
        constraints["tether_state"] = {"present": receipt["selected"],
            "execution": _json_value(receipt),
            "configured_enabled": getattr(cfg_mod, "TETHER_ENABLED", None),
            "experiment_mode": getattr(cfg_mod, "EXPERIMENT_MODE", None),
            "source": "main_sim.py:_apply_tether_and_dp本次执行回执；present表示选用系留约束，施力结果另存",
            "stage": stage, "observed_at_sim_t": sim_t}
    except Exception as exc:
        constraints["tether_state"] = {"present": None, "query_error": repr(exc),
            "source": source, "stage": stage, "observed_at_sim_t": sim_t}
    try:
        if manager is None:
            raise ValueError("actual obstacle manager missing")
        static, dynamic = getattr(manager, "_buoys"), getattr(manager, "_dynamic_obstacles")
        if not isinstance(static, list) or not isinstance(dynamic, list):
            raise ValueError("actual obstacle registries invalid")
        constraints["obstacle_state"] = {"present": bool(static or dynamic),
            "static_count": len(static), "dynamic_count": len(dynamic),
            "source": "main_sim.py actual obstacle_manager registries after spawn_obstacles",
            "stage": stage, "observed_at_sim_t": sim_t}
    except Exception as exc:
        constraints["obstacle_state"] = {"present": None, "query_error": repr(exc),
            "source": source, "stage": stage, "observed_at_sim_t": sim_t}
    return inflow, constraints


def _record(stage, sections, site, sim_t):
    identity = identity_from_env()
    if identity is None:
        return None
    archive, exp, run = identity
    try:
        return SW.record_observation(archive, exp, run, stage, sections, site=site, observed_at_sim_t=sim_t)
    except Exception as exc:
        try:
            os.makedirs(archive, exist_ok=True)
            with open(os.path.join(archive, "scene_witness_errors.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"stage": stage, "site": site, "error": repr(exc), "wall_at": time.time()}) + "\n")
        except Exception:
            pass
        return None


def _mav_fields(mav_view, stage, sim_t, prefix="initial_"):
    mav, error = read_mav_state(mav_view)
    source = "main_sim.py:%s mav_view actual world pose/velocity read" % stage
    values = {prefix + name: _wrapped(value, source, stage, sim_t, error) for name, value in (
        ("position_world_m", mav["position_world_m"] if mav else None),
        ("orientation", {"quaternion_wxyz": mav["orientation_quaternion_wxyz"]} if mav else None),
        ("linear_velocity", mav["linear_velocity"] if mav else None),
        ("angular_velocity", mav["angular_velocity"] if mav else None))}
    for name, unit in (("position_world_m", "m"), ("orientation", "1"),
                       ("linear_velocity", "m/s"), ("angular_velocity", "rad/s")):
        values[prefix + name].update(
            unit=unit, frame="body_to_world" if name == "orientation" else "world",
            attribute="measured",
            sample_time="原钉住/释放写入序列之后，同一仿真时点的对象读数")
    return values


def observe_initialization(mav_view, autopilot, cfg_mod, sim_t, pin_result=None,
                           obstacle_manager=None, runtime_context=None):
    if identity_from_env() is None:
        return None
    status = _action_status(pin_result, ("body_pin", "wing_pin"))
    source = "main_sim.py:pin_branch actual action receipts"
    init = _mav_fields(mav_view, "pinned", sim_t)
    event = {"event": "pin_started" if status == "succeeded" else "unknown", "status": status,
             "pin_actions": _json_value(pin_result), "pin_started_at_sim_t": sim_t, "source": source}
    init["initialization_and_release_boundary"] = event
    inflow, constraints = _environment(cfg_mod, obstacle_manager, "pinned", sim_t,
                                      runtime_context=runtime_context)
    constraints["body_free_dofs"] = _wrapped(read_dof_state(True) if status == "succeeded" else None,
        source, "pinned", sim_t, None if status == "succeeded" else "pin execution not established")
    constraints["body_free_dofs"]["definition"] = "本挂点执行的软件钉住约束；不是USD关节锁或控制权限测量"
    constraints["pin_release_schedule"] = dict(event)
    ctl, error = read_controller_state(autopilot, cfg_mod)
    controller = {"effective_parameters": _wrapped(ctl, "main_sim.py autopilot actual state/config", "pinned", sim_t, error)}
    return _record("pinned", {"initial_state": init, "inflow": inflow,
        "physical_constraints": constraints, "controller": controller}, "main_sim.py:pin_branch", sim_t)


def _pin_history():
    identity = identity_from_env()
    if identity is None:
        return None
    archive, exp, run = identity
    try:
        with open(os.path.join(archive, SW.WITNESS_NAME), encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("experiment_id") != exp or doc.get("run_id") != run:
            return None
        return doc["facts"]["initial_state"]["initialization_and_release_boundary"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def observe_release(mav_view, autopilot, cfg_mod, sim_t, reset_actions_observed,
                    obstacle_manager=None, runtime_context=None):
    if identity_from_env() is None:
        return None
    actions = _json_value(reset_actions_observed)
    release_status = _action_status(actions, _RELEASE_ACTIONS)
    pin_history = _pin_history() or {}
    pin_actions = pin_history.get("pin_actions")
    pin_status = _action_status(pin_actions, ("body_pin", "wing_pin"))
    status = ("succeeded" if release_status == pin_status == "succeeded" else
              "failed" if "failed" in (release_status, pin_status) else "unknown")
    source = "main_sim.py:release_branch existing action success/failure receipts"
    event = {"event": "pin_then_release" if status == "succeeded" else "unknown", "status": status,
             "pin_actions": pin_actions, "release_actions_observed": actions,
             "pin_started_at_sim_t": pin_history.get("pin_started_at_sim_t"),
             "released_at_sim_t": sim_t, "source": source}
    init = _mav_fields(mav_view, "released", sim_t, prefix="post_release_")
    init["initialization_and_release_boundary"] = event
    inflow, constraints = _environment(cfg_mod, obstacle_manager, "released", sim_t,
                                      actions, runtime_context=runtime_context)
    free_path = (status == "succeeded" and constraints["tether_state"].get("present") is False
                 and getattr(cfg_mod, "EXPERIMENT_MODE", None) == "")
    constraints["body_free_dofs"] = _wrapped(read_dof_state(False) if free_path else None,
        source, "released", sim_t, None if free_path else "free-flight holding schedule not established")
    constraints["body_free_dofs"]["definition"] = "本挂点停止软件钉住且未选用系留；不证明不存在其他接触或关节约束"
    constraints["pin_release_schedule"] = dict(event)
    ctl, error = read_controller_state(autopilot, cfg_mod)
    controller = {"effective_parameters": _wrapped(ctl, "main_sim.py autopilot actual post-reset state/config", "released", sim_t, error),
        "mode_and_reset_state": {"event": "controller_reset_observed" if release_status == "succeeded" else "unknown",
            "status": release_status, "reset_actions_observed": actions, "source": source}}
    return _record("released", {"initial_state": init, "inflow": inflow,
        "physical_constraints": constraints, "controller": controller}, "main_sim.py:release_branch", sim_t)
