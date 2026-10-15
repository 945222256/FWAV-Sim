# -*- coding: utf-8 -*-
"""I-209 七项质量审计共用隔离环境。

原则：
1. 被测对象全部为生产源码本体（modules/wing_actuation.py 的
   _drive_wing_joints、modules/aero_solver.py 的 AeroPhysicsCallback 与
   create_aero_callback、modules/aero/ 包的模型与运动学），不经任何复写。
2. 仅 Isaac Sim 侧接口（Articulation 关节接口、RigidPrimView 视图）使用
   本文件内的替身；替身只记录调用与回放固定返回值。
3. 故障注入一律采用运行时属性/方法替换，事后恢复；不修改生产源码。
4. 探针自身以 __main__ 运行，wing_actuation._G 绑定到探针命名空间；
   探针按 main_sim 模块层的定义播种同名宿主变量（_DP_STATE 等），
   与生产脚本启动时 __main__ 即 main_sim 的绑定语义一致。
"""
import json
import logging
import os
import sys
import traceback

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

RESULTS_DIR = os.path.join(REPO, "agentic_core", "audits", "I-209", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

from config.sim_config import WING_DRIVE_CONFIG  # noqa: E402
from modules.aero import WingKinematics  # noqa: E402

JOINT_NAMES = ["flap_L", "flap_R", "twist_L", "twist_R",
               "fold_L", "fold_R", "tail_yaw", "tail_L", "tail_R"]


class LogCapture(logging.Handler):
    """根记录器采集：静默吞掉的异常与已记录的警告都要留原始证据。"""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append({"logger": record.name,
                             "level": record.levelname,
                             "msg": record.getMessage()})

    def dump(self):
        return list(self.records)


def attach_capture():
    cap = LogCapture()
    root = logging.getLogger()
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    return cap


class ArtStub:
    """Isaac Articulation 关节接口替身：记录调用顺序与写入载荷。"""

    def __init__(self, joint_names):
        self.joint_names = list(joint_names)
        self.calls = []
        self._get_positions_error = None

    def set_joint_position_targets(self, positions=None, joint_names=None):
        pos = None if positions is None else __import__("numpy").asarray(positions)
        self.calls.append(("set_joint_position_targets", {
            "dtype": None if pos is None else str(pos.dtype),
            "shape": None if pos is None else list(pos.shape),
            "values": None if pos is None else pos.tolist(),
            "joint_names": list(joint_names) if joint_names is not None else None}))

    def get_joint_positions(self):
        self.calls.append(("get_joint_positions", {}))
        if self._get_positions_error is not None:
            raise self._get_positions_error
        return [0.0] * len(self.joint_names)

    def inject_get_positions_error(self, exc):
        self._get_positions_error = exc


class TailProxy:
    """尾翼气动对象代理：透传真实 TailFuselageAero，可注入发布失败。"""

    def __init__(self, real):
        self._real = real
        self.calls = []
        self._fail_on_set_command = None

    def __getattr__(self, name):
        return getattr(self._real, name)

    def set_command(self, *a, **k):
        self.calls.append(("set_command", {"args": list(a), "kwargs": dict(k)}))
        if self._fail_on_set_command is not None:
            raise self._fail_on_set_command

    def inject_set_command_error(self, exc):
        self._fail_on_set_command = exc


class ModelAttrProxy:
    """气动模型代理：__getattr__ 全透传真实模型；仅指定属性的赋值注入失败。

    用于模拟驱动线 amp_cmd / sd_gate_cmd 元组发布在真实调用点抛异常。
    """

    def __init__(self, real, fail_attrs):
        self._real = real
        self._fail_attrs = set(fail_attrs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        if name in ("_real", "_fail_attrs"):
            object.__setattr__(self, name, value)
            return
        if name in self._fail_attrs:
            raise RuntimeError("注入：模型属性 %s 发布失败" % name)
        object.__setattr__(self._real, name, value)


class ViewStub:
    """RigidPrimView 替身：固定悬停位姿与零速度，记录力施加。"""

    def __init__(self):
        import numpy as np
        self.pos = np.array([[0.0, 0.0, 1.0]])
        self.quat = np.array([[1.0, 0.0, 0.0, 0.0]])
        self.vel = np.zeros((1, 6))
        self.applied = []

    def get_world_poses(self):
        return self.pos.copy(), self.quat.copy()

    def get_velocities(self):
        return self.vel.copy()

    def apply_forces_and_torques_at_pos(self, forces=None, torques=None,
                                        is_global=False):
        import numpy as np
        self.applied.append((np.asarray(forces).tolist(),
                             np.asarray(torques).tolist()))


def make_wk():
    """与 main_sim.py 5.9a 逐项相同的关节侧运动学构建。"""
    return WingKinematics(frequency=WING_DRIVE_CONFIG["frequency"],
                          amplitude_deg=WING_DRIVE_CONFIG["amplitude_deg"],
                          pitch_mode=WING_DRIVE_CONFIG["pitch_mode"],
                          alpha_set_deg=WING_DRIVE_CONFIG["alpha_set_deg"])


def make_aero_cb(view=None):
    from modules.aero_solver import AeroPhysicsCallback
    return AeroPhysicsCallback(None, None, view, None)


def seed_host_namespace(g):
    """按 main_sim 模块层与 sim_env_bridge 的定义播种宿主共享变量。"""
    g["_DP_STATE"] = {"freq": None, "tension": 0.0, "mode": "free"}
    g["_WING_TGT_CACHE"] = None
    g["_DA_CACHE"] = 0.0
    g["_FOLD_T_CACHE"] = 0.0
    g["_CTRL_MODE_CACHE"] = -1
    g["_WING_STATE_CACHE"] = None
    g["_SIDE_PULSE_ON"] = False
    g["_SIDE_PULSE_T"] = 5.0
    g["_SIDE_PULSE_DVY"] = 1.5
    g["_SIDE_PULSE_DONE"] = False
    g["_FW_IID_ULP"] = None


def ap_out(freq=2.0, elevator=0.01, aileron=0.0, rudder=0.0,
           dA=0.0, dfreq=0.0, dlag=0.0, gate_L=1.0, gate_R=1.0):
    """自动驾驶仪输出字典：生产键集与可选键的完整形态。"""
    return {"freq": freq, "elevator": elevator, "aileron": aileron,
            "rudder": rudder, "dA": dA, "dfreq": dfreq, "dlag": dlag,
            "gate_L": gate_L, "gate_R": gate_R}


def save_result(name, payload):
    path = os.path.join(RESULTS_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("[saved]", path)
    return path


def run_probe(main_fn):
    """统一入口：异常也落盘，保证原始证据完整。"""
    name = os.path.basename(main_fn.__code__.co_filename)
    print("=== 运行探针:", name, "===")
    try:
        main_fn()
    except Exception:
        err = traceback.format_exc()
        print(err)
        save_result("error_" + name.replace(".py", "") + ".txt", err)
        raise
