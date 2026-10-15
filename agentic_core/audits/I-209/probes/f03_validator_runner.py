# -*- coding: utf-8 -*-
"""F03 运行器：在受控条件下运行当前版本 I-190 验证器。

用法：python f03_validator_runner.py baseline|block_import|break_driver
- baseline    ：直接运行验证器。
- block_import：安装元路径钩子禁止 modules.wing_actuation 导入后运行。
- break_driver：先把真实 _drive_wing_joints 替换为抛 NameError 的损坏实现
                （模拟 2026-09-19 机翼停摆事故形态），再运行验证器。

验证器本体为 tools/i190_da_semantics_check.py 的逐字节副本（sha256 相同，
探针主脚本负责比对）；副本仅 __file__ 位置不同，其产物落在 I-209 审计
目录内，不改写生产目录。
"""
import hashlib
import importlib.abc
import json
import os
import sys

PROBE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(PROBE_DIR, "..", "..", "..", ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, PROBE_DIR)

ORIG = os.path.join(REPO, "tools", "i190_da_semantics_check.py")
COPY = os.path.join(PROBE_DIR, "i190_validator_copy.py")


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


mode = sys.argv[1]
out = {"mode": mode,
       "validator_sha256_orig": sha256(ORIG),
       "validator_sha256_copy": sha256(COPY),
       "copy_identical": sha256(ORIG) == sha256(COPY)}

driver_calls = {"n": 0}

if mode == "block_import":
    class BlockWingActuation(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "modules.wing_actuation":
                raise ImportError("注入：modules.wing_actuation 被禁止导入")
            return None
    sys.meta_path.insert(0, BlockWingActuation())
elif mode == "break_driver":
    import modules.wing_actuation as wa

    def broken_driver(*a, **k):
        driver_calls["n"] += 1
        raise NameError("注入：_drive_wing_joints 损坏（2026-09-19 事故形态）")
    wa._drive_wing_joints = broken_driver

import i190_validator_copy as chk  # noqa: E402

# 副本的 ROOT 解析到 I-209 审计目录（probes 上两级），产物落其 logs/ 子目录
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(COPY)), "logs"),
            exist_ok=True)

rc = chk.main()
n_pass = sum(1 for r in chk.RESULTS if r["pass"])
out.update({
    "validator_exit_code": rc,
    "checks_total": len(chk.RESULTS),
    "checks_pass": n_pass,
    "verdict": "pass" if n_pass == len(chk.RESULTS) else "fail",
    "real_driver_calls_during_validation": driver_calls["n"],
    "wing_actuation_imported": "modules.wing_actuation" in sys.modules,
})
print("RUNNER_RESULT_JSON>" + json.dumps(out))
