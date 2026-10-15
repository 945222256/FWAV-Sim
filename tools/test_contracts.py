# -*- coding: utf-8 -*-
"""契约自检：四个抽象基类的结构断言。

本脚本不启动 Isaac，只检验 modules/contracts.py 的契约结构。断言共
七组：四个基类均不可直接实例化；缺任一抽象方法的子类仍不可实例化；
实现全部抽象方法后可实例化；状态源返回结构含坐标系籍贯字段且校验
器能拒绝缺失籍贯的结构；机体系与世界系换算与旋转矩阵公式一致；气
动算子返回结构含五部件且合力为四部件之和；执行器返回结构覆盖九个
关节数量与名单。全部通过输出 CONTRACTS PASS，任一失败以非零码退出。
"""

import copy
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from modules.contracts import (
    AERO_RESULT_COMPONENTS,
    AERO_RESULT_VECTOR_KEYS,
    ACTUATOR_JOINT_NAMES,
    STATE_QUANTITY_FRAMES,
    AeroOperator,
    Actuator,
    SimBackend,
    StateSource,
)

BASES = (AeroOperator, Actuator, StateSource, SimBackend)


class _ProbeAero(AeroOperator):
    """气动算子的最小实现，返回常值部件力供结构断言。"""

    def __init__(self):
        self.initialized = False
        self.reset_count = 0

    def initialize(self, config):
        self.initialized = True

    def reset(self, state):
        self.reset_count += 1

    def compute(self, state):
        result = {}
        for name in AERO_RESULT_COMPONENTS:
            if name == "total":
                continue
            result[name] = {
                "force_world": [1.0, 2.0, 3.0],
                "torque_world": [0.5, 0.0, 0.0],
            }
        result["total"] = {
            "force_world": [4.0, 8.0, 12.0],
            "torque_world": [2.0, 0.0, 0.0],
        }
        return result


class _ProbeActuator(Actuator):
    """执行器的最小实现，关节数量与名单取自契约常量。"""

    def __init__(self):
        self.reset_done = False

    def reset(self):
        self.reset_done = True

    def apply(self, commands, dt):
        return {
            "joint_targets": {name: 0.0 for name in ACTUATOR_JOINT_NAMES},
            "tail": {"elevator": 0.0, "aileron": 0.0, "rudder": 0.0},
            "wing_state": {"freq_L": 1.0, "freq_R": 1.0,
                           "gate_L": 1.0, "gate_R": 1.0, "dlag": 0.0},
            "mode_cache": 1,
        }


class _ProbeState(StateSource):
    """状态源的最小实现，按契约常量逐量标注坐标系籍贯。"""

    def read(self):
        reading = {"t": 0.0}
        for name, frame in STATE_QUANTITY_FRAMES.items():
            entry = {"value": [0.0, 0.0, 0.0], "frame": frame}
            if name == "quaternion":
                entry["value"] = [1.0, 0.0, 0.0, 0.0]
            if name.startswith("joint_"):
                entry["value"] = [0.0] * len(ACTUATOR_JOINT_NAMES)
                entry["names"] = list(ACTUATOR_JOINT_NAMES)
            reading[name] = entry
        return reading


class _ProbeBackend(SimBackend):
    """仿真后端的最小实现，观测结构复用状态源最小实现。"""

    def __init__(self):
        self._state = _ProbeState()
        self.setup_done = False
        self.closed = False

    def setup(self, config):
        self.setup_done = True

    def reset(self, seed=None):
        self._state = _ProbeState()

    def step(self, actions=None):
        return self._state.read()

    def close(self):
        self.closed = True


def _mat_transpose_vec(mat, vec):
    """三阶矩阵的转置乘三维向量，用于独立复核换算公式。"""
    return [sum(mat[r][c] * vec[r] for r in range(3)) for c in range(3)]


def _quat_to_matrix(quat):
    """按 aero_solver 同式构建机体系到世界系的旋转矩阵。"""
    w, x, y, z = quat
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ]


def check_instantiation():
    """案一与案二：不可直接实例化，补全方法后可实例化。"""
    ok = True
    for cls in BASES:
        try:
            cls()
            print(f"案一 {cls.__name__} 直接实例化未被拒绝: FAIL")
            ok = False
        except TypeError:
            print(f"案一 {cls.__name__} 直接实例化被拒绝: PASS")
    for cls in BASES:
        partial = type(f"_Partial{cls.__name__}", (cls,), {})
        try:
            partial()
            print(f"案二 {cls.__name__} 缺方法子类实例化未被拒绝: FAIL")
            ok = False
        except TypeError:
            print(f"案二 {cls.__name__} 缺方法子类实例化被拒绝: PASS")
    probes = (_ProbeAero(), _ProbeActuator(), _ProbeState(), _ProbeBackend())
    names = ("气动算子", "执行器", "状态源", "仿真后端")
    for probe, name in zip(probes, names):
        good = isinstance(probe, object)
        print(f"案二 {name} 补全方法后实例化成功: "
              f"{'PASS' if good else 'FAIL'}")
        ok = ok and good
    return ok


def check_state_frames():
    """案三：状态源结构含坐标系籍贯且校验器拒绝坏结构。"""
    ok = True
    reading = _ProbeState().read()
    for name in STATE_QUANTITY_FRAMES:
        entry = reading.get(name)
        if not (isinstance(entry, dict) and "frame" in entry):
            print(f"案三 状态量 {name} 缺坐标系籍贯字段: FAIL")
            ok = False
    problems = StateSource.verify_frames(reading)
    print(f"案三 合法结构校验器返回空清单: "
          f"{'PASS' if not problems else 'FAIL ' + str(problems)}")
    ok = ok and not problems

    bad_missing = copy.deepcopy(reading)
    del bad_missing["position"]["frame"]
    p1 = StateSource.verify_frames(bad_missing)
    print(f"案三 缺籍贯字段的样本被拒绝: "
          f"{'PASS' if p1 else 'FAIL'}")
    ok = ok and bool(p1)

    bad_wrong = copy.deepcopy(reading)
    bad_wrong["velocity_linear"]["frame"] = "body"
    p2 = StateSource.verify_frames(bad_wrong)
    print(f"案三 籍贯取值错误的样本被拒绝: "
          f"{'PASS' if p2 else 'FAIL'}")
    ok = ok and bool(p2)

    bad_absent = copy.deepcopy(reading)
    del bad_absent["joint_positions"]
    p3 = StateSource.verify_frames(bad_absent)
    print(f"案三 缺失状态量的样本被拒绝: "
          f"{'PASS' if p3 else 'FAIL'}")
    ok = ok and bool(p3)
    return ok


def check_frame_math():
    """案四：换算方法与旋转矩阵公式一致且往返还原。"""
    ok = True
    half = math.sqrt(0.5)
    quat = [half, 0.0, 0.0, half]
    mat = _quat_to_matrix(quat)
    vec_world = [1.0, 2.0, -3.0]
    expect_body = _mat_transpose_vec(mat, vec_world)
    got_body = StateSource.world_to_body(vec_world, quat)
    close = all(abs(a - b) < 1e-12 for a, b in zip(expect_body, got_body))
    print(f"案四 世界系到机体系换算与矩阵公式一致: "
          f"{'PASS' if close else 'FAIL'}")
    ok = ok and close

    axis_world = [1.0, 0.0, 0.0]
    axis_expect = [0.0, -1.0, 0.0]
    axis_got = StateSource.world_to_body(axis_world, quat)
    axis_ok = all(abs(a - b) < 1e-12
                  for a, b in zip(axis_expect, axis_got))
    print(f"案四 偏航直角工况的手算值复核一致: "
          f"{'PASS' if axis_ok else 'FAIL'}")
    ok = ok and axis_ok

    expect_world = [sum(mat[i][j] * got_body[j] for j in range(3))
                    for i in range(3)]
    got_world = StateSource.body_to_world(got_body, quat)
    w_ok = all(abs(a - b) < 1e-12 for a, b in zip(expect_world, got_world))
    print(f"案四 机体系到世界系换算与矩阵公式一致: "
          f"{'PASS' if w_ok else 'FAIL'}")
    ok = ok and w_ok

    round_trip = StateSource.world_to_body(
        StateSource.body_to_world(vec_world, quat), quat)
    r_ok = all(abs(a - b) < 1e-12 for a, b in zip(round_trip, vec_world))
    print(f"案四 往返换算还原原向量: {'PASS' if r_ok else 'FAIL'}")
    ok = ok and r_ok
    return ok


def check_aero_structure():
    """案五：气动返回结构含五部件且合力为四部件之和。"""
    ok = True
    result = _ProbeAero().compute({"commands": {}})
    keys_ok = set(result) == set(AERO_RESULT_COMPONENTS)
    print(f"案五 气动返回结构含五部件键: "
          f"{'PASS' if keys_ok else 'FAIL'}")
    ok = ok and keys_ok
    vec_ok = all(k in result["wing_left"] for k in AERO_RESULT_VECTOR_KEYS)
    print(f"案五 部件字典含力与力矩两键: "
          f"{'PASS' if vec_ok else 'FAIL'}")
    ok = ok and vec_ok
    sums_ok = True
    for vk in AERO_RESULT_VECTOR_KEYS:
        comp_sum = [sum(result[n][vk][i] for n in
                        AERO_RESULT_COMPONENTS if n != "total")
                    for i in range(3)]
        if any(abs(a - b) > 1e-12
               for a, b in zip(comp_sum, result["total"][vk])):
            sums_ok = False
    print(f"案五 合力与合力矩等于四部件之和: "
          f"{'PASS' if sums_ok else 'FAIL'}")
    ok = ok and sums_ok
    return ok


def check_actuator_structure():
    """案六：执行器返回结构覆盖九个关节数量与名单。"""
    out = _ProbeActuator().apply({"source": "idle"}, 1.0 / 120.0)
    names = set(out["joint_targets"])
    ok = names == set(ACTUATOR_JOINT_NAMES) and len(names) == 9
    print(f"案六 执行器关节目标覆盖九关节名单: "
          f"{'PASS' if ok else 'FAIL'}")
    tail_ok = set(out["tail"]) == {"elevator", "aileron", "rudder"}
    print(f"案六 执行器尾舵字典含三舵键: "
          f"{'PASS' if tail_ok else 'FAIL'}")
    return ok and tail_ok


def check_backend_structure():
    """案七：后端步进返回的观测与状态源结构同构。"""
    backend = _ProbeBackend()
    backend.setup({"physics_dt": 1.0 / 120.0})
    backend.reset(seed=0)
    obs = backend.step(None)
    ok = "t" in obs
    for name in STATE_QUANTITY_FRAMES:
        entry = obs.get(name)
        if not (isinstance(entry, dict) and "frame" in entry):
            ok = False
    problems = StateSource.verify_frames(obs)
    ok = ok and not problems
    backend.close()
    print(f"案七 后端观测结构与状态源结构同构: "
          f"{'PASS' if ok else 'FAIL'}")
    return ok


def main():
    results = [
        ("案一与案二 实例化断言", check_instantiation()),
        ("案三 坐标系籍贯断言", check_state_frames()),
        ("案四 换算公式断言", check_frame_math()),
        ("案五 气动结构断言", check_aero_structure()),
        ("案六 执行器结构断言", check_actuator_structure()),
        ("案七 后端结构断言", check_backend_structure()),
    ]
    print()
    all_ok = True
    for name, ok in results:
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
        all_ok = all_ok and ok
    print()
    if all_ok:
        print("CONTRACTS PASS 契约自检全部通过")
        return 0
    print("CONTRACTS FAIL 契约自检存在失败项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
