# -*- coding: utf-8 -*-
"""i178_gui_probe_wait.py — GUI 关闭探针的等待与归档（I-178）。

由 i178_gui_closed_acceptance.bat 调用：等派发链完成（dispatched 即
成功：内核唤醒、桥送达、会话建立、回读核验、运行开始），归档结果与
台账。探针为测试通道简报，设计上无执行器交结果，成功判据是派发链
存活而非结果回写。
仅使用 Python 标准库。
"""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
probe = sys.argv[1]
out_dir = sys.argv[2]
state = os.path.join(HERE, "kernel_state", "state.json")

deadline = time.time() + 300
ok = False
task = {}
while time.time() < deadline:
    try:
        st = json.load(open(state, encoding="utf-8"))
        task = st.get("tasks", {}).get(probe, {})
        ph = task.get("phase")
        if ph in ("dispatched", "result_pending", "completed"):
            ok = True
            break
        if ph in ("dispatch_failed", "uncertain", "line_rejected"):
            break
    except Exception:
        pass
    time.sleep(3)
rec = {"probe": probe, "ok": ok, "phase": task.get("phase"),
       "session": task.get("session_id"), "run": task.get("run_id"),
       "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
os.makedirs(out_dir, exist_ok=True)
json.dump(rec, open(os.path.join(out_dir, "result.json"), "w",
                    encoding="utf-8"), ensure_ascii=False, indent=1)
for f in ("kernel_state/ledger.json", "kernel_state/state.json"):
    src = os.path.join(HERE, f)
    if os.path.exists(src):
        shutil.copyfile(src, os.path.join(out_dir, os.path.basename(f)))
print("probe done ok=%s phase=%s" % (ok, task.get("phase")))
