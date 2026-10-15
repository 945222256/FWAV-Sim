# -*- coding: utf-8 -*-
"""F03 审计：验证器是否覆盖真实驱动入口。

三个独立证据：
1. 副本一致性：tools/i190_da_semantics_check.py 逐字节复制到本目录并
   sha256 比对（证明被测验证器与生产版本同一实现）。
2. 禁入实验：baseline / 禁止导入 modules.wing_actuation / 驱动函数替换为
   抛 NameError 的损坏实现，三种条件下运行验证器，比对判定结果。
3. 写入路径口径：同一 dA 网格下，验证器核对的 float64 目标值与真实
   _drive_wing_joints 经 np.asarray(..., dtype=float32) 实际写入值的最大
   偏差；以及验证器不覆盖的边界组合（关节名缺失、乱序、空表）。
既有独立复核：logs/hq_intake/20261012_i190_acceptance/（HQ-Anni 2026-10-12，
I-190 据此以“工具或环境故障”阻塞）。本审计只核对当前版本，不接管修复。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

import harness_common as H
from config.sim_config import PHYSICS_DT, WING_DRIVE_CONFIG, STARTUP_AMP_RAMP_S

ORIG = os.path.join(H.REPO, "tools", "i190_da_semantics_check.py")
COPY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "i190_validator_copy.py")


def run_mode(mode):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(
        [sys.executable, "f03_validator_runner.py", mode],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.abspath(__file__)), env=env, timeout=300)
    line = None
    for ln in p.stdout.splitlines():
        if ln.startswith("RUNNER_RESULT_JSON>"):
            line = ln[len("RUNNER_RESULT_JSON>"):]
    return {"returncode": p.returncode, "parsed": json.loads(line) if line else None,
            "stdout_tail": p.stdout[-600:], "stderr_tail": p.stderr[-400:]}


def written_path_check():
    """同一输入下：验证器口径（float64 表达式）vs 真实驱动写入（float32）。"""
    import math
    import numpy as np
    from modules.wing_actuation import _drive_wing_joints

    DA_MAIN = (0.05, -0.05, 0.08, -0.08)
    H.seed_host_namespace(globals())
    cb = H.make_aero_cb()
    rows = []
    # 稳态段一个完整拍周期（避开斜坡窗）；驱动内部 t=step_count*PHYSICS_DT，
    # 故 step_count 取 round(t/PHYSICS_DT) 对齐
    f0 = 1.8
    t_start, n_steps = 6.0, int(round((1.0 / f0) / PHYSICS_DT))
    worst_all = {"abs_diff": 0.0}
    for dA in DA_MAIN:
        wk2 = H.make_wk()
        art2 = H.ArtStub(H.JOINT_NAMES)
        max_diff = 0.0
        for k in range(1, n_steps + 1):
            t = t_start + k * PHYSICS_DT
            sc = int(round(t / PHYSICS_DT))
            wk2.set_frequency(f0)
            _drive_wing_joints(None, art2, wk2, None, cb,
                               H.ap_out(freq=f0, dA=dA), step_count=sc)
            # 验证器口径：float64 目标表达式（joint_target 内联式）
            phi = wk2.evaluate(t)["phi"]  # 同 t 重复求值不推进相位
            ramp = min(1.0, t / max(STARTUP_AMP_RAMP_S, 0.05))
            tgt64_L = phi * ramp + dA
            write = None
            for c in reversed(art2.calls):
                if c[0] == "set_joint_position_targets":
                    write = c[1]["values"][0][H.JOINT_NAMES.index("flap_L")]
                    break
            diff = abs(tgt64_L - write)
            max_diff = max(max_diff, diff)
            if diff > worst_all["abs_diff"]:
                worst_all = {"abs_diff": diff, "dA": dA, "t": t,
                             "tgt64": tgt64_L, "written32": write}
        rows.append({"dA": dA, "max_abs_diff_float64_vs_written": max_diff})
    write_call = None
    for c in reversed(art2.calls):
        if c[0] == "set_joint_position_targets":
            write_call = c[1]
            break
    dtype = write_call["dtype"]
    shape = write_call["shape"]

    # 边界组合：关节名缺失 / 乱序 / 空表（验证器不建模的路径）
    art_missing = H.ArtStub([n for n in H.JOINT_NAMES if n != "tail_yaw"])
    art_perm = H.ArtStub(list(reversed(H.JOINT_NAMES)))
    art_empty = H.ArtStub([])
    wkx = H.make_wk()
    _drive_wing_joints(None, art_missing, wkx, None, cb, H.ap_out(dA=0.05),
                       step_count=5000)
    _drive_wing_joints(None, art_perm, H.make_wk(), None, cb, H.ap_out(dA=0.05),
                       step_count=5001)
    ret_empty = _drive_wing_joints(None, art_empty, H.make_wk(), None, cb,
                                   H.ap_out(dA=0.05), step_count=5002)
    boundary = {
        "missing_key_default": {
            "names": art_missing.joint_names,
            "write_count": len(art_missing.calls),
            "note": "目标字典按名取值，缺失关节写 0.0 默认值，验证器不覆盖"},
        "permuted_names": {
            "names": art_perm.joint_names,
            "values_head": art_perm.calls[-1][1]["values"][0][:3],
            "note": "写入顺序跟随关节表顺序，验证器固定二元组顺序"},
        "empty_names_early_return": {
            "returned": repr(ret_empty),
            "write_count": len(art_empty.calls),
            "note": "joint_names 为空时静默跳过整步，验证器不覆盖"},
        "actual_write_dtype": dtype,
        "actual_write_shape": shape,
        "validator_tolerance_fp": 1e-15,
        "float32_quantization_scale": float(np.finfo(np.float32).eps),
    }
    return {"worst_case": worst_all, "per_dA": rows, "boundary": boundary}


def main():
    shutil.copyfile(ORIG, COPY)
    same = hashlib.sha256(open(ORIG, "rb").read()).hexdigest() == \
        hashlib.sha256(open(COPY, "rb").read()).hexdigest()
    result = {
        "probe": "F03 验证器真实入口覆盖",
        "validator_source": "tools/i190_da_semantics_check.py",
        "copy_sha256_identical": same,
        "runs": {m: run_mode(m) for m in
                 ("baseline", "block_import", "break_driver")},
        "written_path": written_path_check(),
        "prior_independent_review": {
            "location": "logs/hq_intake/20261012_i190_acceptance/",
            "reviewer": "HQ-Anni（机主授权，独立复核）",
            "verdict": "not_accepted",
            "item_status": "I-190 blocked（工具或环境故障）",
            "key_finding": "禁用真实 _drive_wing_joints 后原验证器仍 31 项全过"
                           "且入口调用 0 次",
        },
    }
    for m, r in result["runs"].items():
        print(m, "->", json.dumps(r["parsed"], ensure_ascii=False)
              if r["parsed"] else r["stdout_tail"][-200:])
    w = result["written_path"]["worst_case"]
    print("float64 目标 vs float32 写入 最大偏差 =", w["abs_diff"],
          "@ dA=", w["dA"])
    H.save_result("f03_validator_coverage.json", result)


if __name__ == "__main__":
    H.run_probe(main)
