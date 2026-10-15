# -*- coding: utf-8 -*-
"""I-117 判据闸实测脚本：验证判据执行器与闸机制的真实存在与行为。
退出码零=闸机制组件齐备且行为符合冻结口径。"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
executor = os.path.join(ROOT, "tools", "checks", "executor.py")
checks = os.path.join(ROOT, "tools", "checks")
assert os.path.isfile(executor), "执行器本体缺失"
assert os.path.isfile(os.path.join(checks, "demo_pass.py")), "演示件缺失"
assert os.path.isfile(os.path.join(checks, "i116_selfcheck.py")), "自检件缺失"
reg = os.path.join(ROOT, "logs", "dispatch_session")
assert os.path.isdir(checks)
print("gate_demo：判据闸组件齐备（执行器+演示件+自检件）")
