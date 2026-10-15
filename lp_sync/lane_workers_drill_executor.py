# -*- coding: utf-8 -*-
"""lane_workers_drill_executor.py — I-217 演练的工位执行器占位子进程。

代表一个工位的独立执行器：常驻循环直到 stop 文件出现（判据五的单工位
故障隔离杀的就是这个真实进程）。心跳文件供演练核验存活与存活区间。
仅使用 Python 标准库。"""
import os
import sys
import time

worker = sys.argv[sys.argv.index("--worker") + 1]
stop = os.environ.get("DRILL_EXEC_STOP", "")
hb_dir = os.path.dirname(stop) or "."
hb = os.path.join(hb_dir, worker + ".heartbeat")

while True:
    if stop and os.path.exists(stop):
        break
    try:
        with open(hb + ".tmp", "w", encoding="utf-8") as f:
            f.write("%d\n" % time.time())
        os.replace(hb + ".tmp", hb)
    except OSError:
        pass
    time.sleep(0.5)
try:
    os.remove(hb)
except OSError:
    pass
