# -*- coding: utf-8 -*-
"""F07 审计：周期预览直接修改并恢复对象私有状态。

真实入口：modules.aero_solver.AeroPhysicsCallback.__call__ / _call_inner
（生产源码本体；视图用替身，模型与运动学为本体）。
取证：
  1 正常一步：预览调用量、快照恢复正确性（_t_prev 回到本步物理时刻）、
    求值缓存 current_F_world 被最后一次预览样本覆盖（非施加的平均值）。
  2 一次预览触碰的全部模型状态：以 __dict__ 快照对比列出。
  3 中途异常注入：预览第 4 次求值（k=3）抛错 → 恢复语句不执行、
    异常穿出 __call__（再抛）、两翼 _t_prev 停在未来时刻。
  4 后续影响量化：与无故障孪生回调同步推进，分定频与变频两口径测量
    相位偏差及恢复窗口；只证明软件行为，不推断飞行影响。
"""
import os

import numpy as np

import harness_common as H
from modules.aero_solver import AeroPhysicsCallback

DT = 1.0 / 120.0


def circ_dist(a, b):
    d = abs((a - b) % 1.0)
    return min(d, 1.0 - d)


def fresh_cb(sandbox):
    cb = AeroPhysicsCallback(None, None, H.ViewStub(), None)
    cb._crash_log = os.path.join(sandbox, "aero_crash.txt")
    return cb


def part1_normal_step(sandbox):
    cb = fresh_cb(sandbox)
    real_cf = cb.model.compute_forces
    returns = []

    def spy(*a, **k):
        r = real_cf(*a, **k)
        returns.append(r)
        return r
    cb.model.compute_forces = spy
    cb(DT)
    t = cb._sim_time
    wl, wr = cb.model.wing_left, cb.model.wing_right
    applied = np.asarray(cb.flapper_view.applied[-1][0])
    avg_preview = np.mean([r[0] for r in returns[1:]], axis=0)
    real_sample = returns[0][0]
    last_preview = returns[-1][0]
    cur = np.asarray(cb.model.current_F_world)
    return {
        "compute_forces_calls_per_step": len(returns),
        "structure": "1 次真实求值（结果被丢弃，仅推进状态）+ K=8 次预览求值",
        "t_prev_restored_to_sim_time": (wl._t_prev == t and wr._t_prev == t),
        "applied_equals_preview_mean": bool(
            np.allclose(applied, avg_preview, rtol=0, atol=1e-12)),
        "current_F_world_equals_last_preview_sample": bool(
            np.allclose(cur, last_preview, rtol=0, atol=1e-15)),
        "current_F_world_equals_applied_average": bool(
            np.allclose(cur, avg_preview, rtol=0, atol=1e-12)),
        "current_F_world_diff_from_applied": float(
            np.linalg.norm(cur - applied)),
        "real_sample_discarded": not np.allclose(real_sample, applied,
                                                  rtol=0, atol=1e-12),
    }


def part2_state_touched(sandbox):
    cb = fresh_cb(sandbox)
    before_model = dict(cb.model.__dict__)
    before_wl = dict(cb.model.wing_left.__dict__)
    before_wr = dict(cb.model.wing_right.__dict__)
    cb(DT)
    def _diff(before, after):
        changed = []
        for k in set(before) | set(after):
            if k not in before:
                changed.append(k + "（新增）")
                continue
            try:
                same = np.array_equal(np.asarray(after[k], dtype=object),
                                      np.asarray(before[k], dtype=object))
            except Exception:
                same = after[k] == before[k]
            if not same:
                changed.append(k)
        return sorted(changed)
    changed_model = _diff(before_model, cb.model.__dict__)
    changed_wl = _diff(before_wl, cb.model.wing_left.__dict__)
    changed_wr = _diff(before_wr, cb.model.wing_right.__dict__)
    return {
        "model_level_changed": changed_model,
        "wing_left_changed_then_restored": changed_wl,
        "wing_right_changed_then_restored": changed_wr,
        "note": "_tau_state/_t_prev 预览中被推进、循环后恢复；"
                "current_* 与 _last_* 为求值副产物，无恢复逻辑",
    }


def part3_injection(sandbox):
    cb = fresh_cb(sandbox)
    real_cf = cb.model.compute_forces
    calls = {"n": 0}
    FAIL_AT = 5   # 第 1 次真实求值后，预览 k=3（第 5 次调用）

    def spy(*a, **k):
        calls["n"] += 1
        if calls["n"] == FAIL_AT:
            raise RuntimeError("注入：预览中途求值失败（k=3）")
        return real_cf(*a, **k)
    cb.model.compute_forces = spy
    raised = None
    try:
        cb(DT)
    except Exception as e:
        raised = repr(e)
    t = cb._sim_time
    f = float(cb.model.wing_left.frequency)
    T = 1.0 / max(f, 0.2)
    wl, wr = cb.model.wing_left, cb.model.wing_right
    return {
        "raised_out_of_call": raised,
        "re_raise_is_design": "aero_solver.py:235 `raise`（崩溃记录后重抛）",
        "crash_log_written": os.path.exists(cb._crash_log),
        "wl_t_prev": wl._t_prev, "wr_t_prev": wr._t_prev,
        "sim_time": t,
        "t_prev_lead_seconds": wl._t_prev - t,
        "expected_lead": 2 * T / 8.0,
        "restore_executed": False,
        "calls_before_failure": calls["n"],
    }


def part4_twin(sandbox, freq_schedule):
    """无故障孪生对比：返回逐步相位圆距与恢复窗口。"""
    FAIL_STEP = 3
    cbA = fresh_cb(sandbox)
    cbB = fresh_cb(sandbox)
    real_cf = cbA.model.compute_forces
    state = {"step": 0, "c": 0}

    def spy(*a, **k):
        state["c"] += 1
        if state["step"] == FAIL_STEP and state["c"] == 5:
            raise RuntimeError("注入：预览中途求值失败（k=3）")
        return real_cf(*a, **k)
    cbA.model.compute_forces = spy

    dists, leads = [], []
    for step, f in enumerate(freq_schedule, start=1):
        for cb in (cbA, cbB):
            cb.model.set_wing_command(frequency=f)
        state["step"], state["c"] = step, 0
        if step == FAIL_STEP:
            try:
                cbA(DT)
            except Exception:
                pass
            cbB(DT)
        else:
            cbA(DT)
            cbB(DT)
        tauA = cbA.model.wing_left._tau_state
        tauB = cbB.model.wing_left._tau_state
        dists.append(circ_dist(tauA, tauB))
        leads.append(cbA.model.wing_left._t_prev - cbA._sim_time)
    n_recover = next((i + 1 for i, d in enumerate(dists)
                      if d < 1e-9 and i + 1 > FAIL_STEP), None)
    return {
        "injected_at_step": FAIL_STEP,
        "phase_circular_distance_per_step": [round(d, 6) for d in dists],
        "max_phase_distance": max(dists),
        "t_prev_lead_first_steps": [round(x, 5) for x in leads[:6]],
        "first_recovered_step": n_recover,
        "recovered_permanently": (n_recover is not None
                                  and max(dists[n_recover - 1:]) < 1e-9),
    }


def main():
    sandbox = os.path.join(H.RESULTS_DIR, "f07_sandbox")
    os.makedirs(sandbox, exist_ok=True)
    f0 = float(__import__("config.sim_config", fromlist=["x"])
               .FlapKinematicsConfig.FREQUENCY)
    n = 120
    result = {
        "probe": "F07 周期预览修改并恢复私有状态",
        "entry": "modules.aero_solver.AeroPhysicsCallback._call_inner 396-410 行",
        "part1_normal_step": part1_normal_step(sandbox),
        "part2_state_touched": part2_state_touched(sandbox),
        "part3_injection": part3_injection(sandbox),
        "part4_twin_constant_freq": part4_twin(
            sandbox, [f0] * n),
        "part4_twin_freq_change_after_failure": part4_twin(
            sandbox, [f0] * 3 + [f0 + 0.3] * (n - 3)),
        "config_frequency": f0,
    }
    print("正常步:", json.dumps(result["part1_normal_step"], ensure_ascii=False))
    print("注入:", json.dumps({k: v for k, v in
                               result["part3_injection"].items()}, ensure_ascii=False))
    print("孪生定频 max 相位差:", result["part4_twin_constant_freq"]["max_phase_distance"],
          "| 恢复步:", result["part4_twin_constant_freq"]["first_recovered_step"])
    print("孪生变频 max 相位差:", result["part4_twin_freq_change_after_failure"]["max_phase_distance"],
          "| 恢复步:", result["part4_twin_freq_change_after_failure"]["first_recovered_step"])
    H.save_result("f07_cycle_preview.json", result)


if __name__ == "__main__":
    import json
    H.run_probe(main)
