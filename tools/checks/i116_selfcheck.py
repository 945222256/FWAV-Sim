# -*- coding: utf-8 -*-
"""I-116 判据脚本自检：验证执行器本体与三规则行为。
退出码零=执行器与规则自检通过。"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXECUTOR = os.path.join(HERE, "executor.py")
ROOT = os.path.dirname(os.path.dirname(HERE))

assert os.path.isfile(EXECUTOR), "执行器本体缺失"
assert os.path.isdir(os.path.join(ROOT, "tools", "checks")), "tools/checks 目录缺失"


def run(script, timeout=30):
    r = subprocess.run([sys.executable, EXECUTOR, "run", script, "--timeout", str(timeout)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        result = json.loads(r.stdout)
    except Exception:
        raise AssertionError("执行器输出不是 JSON：%s" % (r.stdout + r.stderr)[:200])
    return result


# 规则一：退出码零=通过。
r1 = run(os.path.join(HERE, "demo_pass.py"))
assert r1["pass"] is True and r1["exit_code"] == 0, "demo_pass 判定错误：%s" % r1

# 规则二：非零=不通过。
r2 = run(os.path.join(HERE, "demo_fail.py"))
assert r2["pass"] is False and r2["exit_code"] == 3, "demo_fail 判定错误：%s" % r2

# 规则三：超时=不通过。
r3 = run(os.path.join(HERE, "demo_timeout.py"), timeout=5)
assert r3["pass"] is False and r3["exit_code"] == -2, "demo_timeout 判定错误：%s" % r3

# 规则四：异常=不通过（脚本文件不存在按不通过计）。
r4 = run(os.path.join(HERE, "no_such_script_demo.py"))
assert r4["pass"] is False and r4["exit_code"] == -1, "缺脚本判定错误：%s" % r4

print("I-116 自检：四规则（过/非零/超时/异常）全部按冻结口径判定")
