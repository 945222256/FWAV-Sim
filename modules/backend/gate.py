# -*- coding: utf-8 -*-
"""
gate.py 一键门禁入口
====================

运行方式为 python 减号 m modules.backend.gate。本入口串联两项检查。第一项
为 tools/test_contracts.py 的契约结构自检。第二项为
modules.backend.run_hover_bench 的模拟后端悬停基准。两项检查全部通过时本入
口返回零，否则返回一。本入口为将来持续集成的挂接点。

本入口只调用既有脚本，不修改任何文件。子进程统一带 PYTHONUTF8 等于一的环
境变量，与仓库在 Windows 上的现行调用约定一致。
"""

from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _run_step(title: str, cmd, cwd: str) -> bool:
    """运行一个子进程并转发其输出，返回其是否以零码退出。"""
    print("=" * 78)
    print("门禁步骤 %s" % title)
    print("命令 %s" % " ".join(cmd))
    print("=" * 78)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    code = subprocess.call(cmd, cwd=cwd, env=env)
    print("步骤 %s 退出码 %d" % (title, code))
    return code == 0


def main() -> int:
    """依次运行契约自检与悬停基准，汇总退出码。"""
    contracts_ok = _run_step(
        "契约结构自检",
        [sys.executable, os.path.join(_REPO_ROOT, "tools",
                                      "test_contracts.py")],
        _REPO_ROOT)
    bench_ok = _run_step(
        "模拟后端悬停基准",
        [sys.executable, "-m", "modules.backend.run_hover_bench"],
        _REPO_ROOT)
    print()
    print("=" * 78)
    print("门禁汇总 契约自检 %s，悬停基准 %s" % (
        "通过" if contracts_ok else "未通过",
        "通过" if bench_ok else "未通过"))
    if contracts_ok and bench_ok:
        print("GATE PASS 门禁通过")
        return 0
    print("GATE FAIL 门禁未通过")
    return 1


if __name__ == "__main__":
    sys.exit(main())
