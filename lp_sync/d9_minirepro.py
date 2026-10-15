# -*- coding: utf-8 -*-
"""d9_minirepro.py — D9 独立最小复现（只跑认领到期场景，带详细读数）。

隔离件与主演练相同：独立队列根、独立状态根、隔离登记库副本、假执行器。
用法：python d9_minirepro.py [输出目录]
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
sys.path.insert(0, HERE)
import dispatch_file_queue as dq

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "logs", "i178_d9_minirepro_%d" % int(time.time()))
KS = os.path.join(OUT, "kernel_state")
QR = os.path.join(OUT, "dispatch_queue")
REG = os.path.join(OUT, "registry_copy")
PORT = 58931


def jload(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_task_file(item_id, msg, fname=None, task_kind="test"):
    rid = "kt-%s" % item_id.lower()
    payload = {"schema_version": 1, "kind": "task_submit",
               "request_id": rid, "item_id": item_id, "lane": "L3",
               "title": "演练任务 %s" % item_id, "message": msg,
               "approved_by": "Beth", "task_kind": task_kind}
    dst = os.path.join(KS, "inbox", fname or (rid + ".json"))
    tmp = dst + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)
    return dst


def main():
    os.makedirs(os.path.join(KS, "inbox"), exist_ok=True)
    dq.ensure_layout(QR)
    shutil.copytree(os.path.join(ROOT, "items_registry"), REG,
                    ignore=shutil.ignore_patterns("__pycache__", "*.tmp",
                                                  ".ledger.lock"))
    items = [{"id": "I-909", "title": "t", "status": "open", "depends_on": [],
              "evidence": []}]
    json.dump({"schema_version": 1, "items": items},
              open(os.path.join(REG, "items.json"), "w", encoding="utf-8"),
              ensure_ascii=False)

    env = dict(os.environ, DRILL_QUEUE_ROOT=QR,
               DRILL_KERNEL_INBOX=os.path.join(KS, "inbox"),
               LP_ITEMS_REGISTRY=REG, FLAPPER_LEDGER_MODE="local",
               PYTHONIOENCODING="utf-8")
    worker = subprocess.Popen([PY, os.path.join(HERE, "kernel_drill_worker.py")],
                              env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT)
    kern = subprocess.Popen([PY, os.path.join(HERE, "orchestrator_kernel.py"),
                             "--state-root", KS, "--queue-root", QR,
                             "--wake-s", "0.3", "--port", str(PORT)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        write_task_file("I-909", "演练简报：I-909 的构造性任务。执行毫秒:15000")
        print("[%.2f] 任务已投放" % (time.time() - t0), flush=True)
        # 详细读数：每 0.3 秒打印 phase 与台账事件数
        deadline = time.time() + 45
        seen_events = set()
        while time.time() < deadline:
            st = jload(os.path.join(KS, "state.json")) or {}
            t = st.get("tasks", {}).get("kt-i-909", {})
            phase = t.get("phase")
            led = dq.ledger_events(KS, request_id="kt-i-909")
            submits = [e for e in led if e.get("event") == "dispatch_submitted"]
            key = (phase, len(submits))
            if key not in seen_events:
                seen_events.add(key)
                print("[%.2f] phase=%r dispatch_submitted=%d" %
                      (time.time() - t0, phase, len(submits)), flush=True)
            if phase in ("dispatched", "result_pending"):
                print("[%.2f] 首个等待达成" % (time.time() - t0), flush=True)
                break
            time.sleep(0.3)
        # 模拟租约到期：改回 open
        obj = jload(os.path.join(REG, "items.json"))
        obj["items"][0]["status"] = "open"
        json.dump(obj, open(os.path.join(REG, "items.json"), "w",
                            encoding="utf-8"), ensure_ascii=False)
        write_task_file("I-909", "第二任务（同事项）", fname="kt-i-909b.json")
        # 等忙拒收
        deadline = time.time() + 15
        while time.time() < deadline:
            if any(e.get("event") == "item_busy_rejected"
                   for e in dq.ledger_events(KS)):
                print("[%.2f] 忙拒收已见" % (time.time() - t0), flush=True)
                break
            time.sleep(0.3)
        time.sleep(3)
        submits = [e for e in dq.ledger_events(KS, request_id="kt-i-909")
                   if e.get("event") == "dispatch_submitted"]
        second = [e for e in submits
                  if str(e.get("detail", {}).get("request_id", "")).endswith("-a2")]
        print("最终：dispatch_submitted=%d 次，二次尝试=%d，判定=%s" %
              (len(submits), len(second),
               "过" if len(submits) == 1 and not second else "拒"), flush=True)
    finally:
        for p in (kern, worker):
            if p.poll() is None:
                p.kill()
    print("输出目录：%s" % OUT)


if __name__ == "__main__":
    main()
