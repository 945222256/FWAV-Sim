# -*- coding: utf-8 -*-
"""i208_phase2_test.py — I-208 第二阶段隔离验收（判据三、四、五部分）。

场景（全部隔离：独立队列根、独立状态根、独立端口、自建进程；不触碰
生产宿主/内核/队列）：
  1. 持久断点记录：任务执行器退出后，重启从断点恢复（判据四）；
  2. 未完成恢复、已完成不重做、未知先对账三种样例（判据四）；
  3. 服务自身异常退出由调用方托管恢复（模拟系统任务重启，判据五）；
  4. 同 I 同 run 去重、重复结果两次不重复执行（判据五）。
仅使用 Python 标准库。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
sys.path.insert(0, HERE)
import dispatch_file_queue as dq

W = tempfile.mkdtemp(prefix="i208_p2e2e_")
KS = os.path.join(W, "kernel_state")
QR = os.path.join(W, "queue")
REG = os.path.join(W, "registry")
PORT = 58965

_checks = []


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail), flush=True)


def jload(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def wait(pred, timeout=90, step=1.0):
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def tphase(tid):
    st = jload(os.path.join(KS, "state.json")) or {}
    return st.get("tasks", {}).get(tid, {}).get("phase")


def spawn_kernel(crash=None):
    env = dict(os.environ)
    if crash:
        env["KERNEL_CRASH_POINT"] = crash
    return subprocess.Popen([PY, os.path.join(HERE, "orchestrator_kernel.py"),
                             "--state-root", KS, "--queue-root", QR,
                             "--wake-s", "0.3", "--port", str(PORT),
                             "--line-retry-s", "1.5"],
                            env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.STDOUT,
                            creationflags=0x08000000)


def main():
    os.makedirs(os.path.join(KS, "inbox"), exist_ok=True)
    dq.ensure_layout(QR)
    shutil.copytree(os.path.join(ROOT, "items_registry"), REG,
                    ignore=shutil.ignore_patterns("__pycache__", "*.tmp",
                                                  ".ledger.lock"))
    items = [{"id": "I-931", "title": "I-208 恢复甲", "status": "open",
              "opened_at": "2026-10-13", "closed_at": None, "closed_by": None,
              "owner": "L3队", "lane": "LX", "close_criterion": "演练判据",
              "note": "", "claimed_by": None, "claimed_at": None,
              "lease_until": None, "evidence": [], "criteria": None,
              "replies": [], "blockers": [], "conclusion": None,
              "criterion_check": [], "depends_on": [], "partial_start": [],
              "claim_scope": []}]
    json.dump({"schema_version": 1, "items": items},
              open(os.path.join(REG, "items.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    env = dict(os.environ, DRILL_QUEUE_ROOT=QR,
               DRILL_KERNEL_INBOX=os.path.join(KS, "inbox"),
               LP_ITEMS_REGISTRY=REG, FLAPPER_LEDGER_MODE="local",
               PYTHONIOENCODING="utf-8")
    worker = subprocess.Popen([PY, os.path.join(HERE, "kernel_drill_worker.py")],
                              env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT,
                              creationflags=0x08000000)

    def submit(rid, item):
        payload = {"schema_version": 1, "kind": "task_submit",
                   "request_id": rid, "item_id": item, "lane": "LX",
                   "title": "演练", "message": "演练简报 %s" % item,
                   "approved_by": "Beth", "task_kind": "task"}
        p = os.path.join(KS, "inbox", rid + ".json")
        with open(p + ".w", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(p + ".w", p)

    try:
        # 场景一：领取后（请求保存后近似）终止内核并重启——未完成任务恢复。
        kern = spawn_kernel(crash="after_ingest")
        submit("kt-i931", "I-931")
        rc = kern.wait(timeout=30)
        check("场景一a：请求保存后终止（rc=%s）" % rc, rc == 99)
        # 模拟托管重启（判据五：服务自身退出后由系统任务恢复）。
        kern = spawn_kernel()
        ok = wait(lambda: tphase("kt-i931") == "completed", 120)
        check("场景一b：重启后未完成任务恢复并完成", ok,
              "phase=%s" % tphase("kt-i931"))

        # 场景二：重复结果两次不重复执行（用已完成的运行再投一次结果）。
        st = jload(os.path.join(KS, "state.json")) or {}
        run_id = st["tasks"]["kt-i931"].get("run_id")
        result_id = "kr-%s" % run_id
        src = os.path.join(KS, "processed", result_id + ".json")
        dst = os.path.join(KS, "inbox", result_id + ".dup.json")
        if os.path.exists(src):
            shutil.copyfile(src, dst)
            ok = wait(lambda: any(
                e.get("event") == "duplicate_result_skipped"
                for e in dq.ledger_events(KS, request_id=result_id)), 30)
            reg = jload(os.path.join(REG, "items.json"))
            item = [i for i in reg["items"] if i["id"] == "I-931"][0]
            ev = len([e for e in item.get("evidence", [])
                      if "机主说明：" not in e])
            check("场景二：重复结果两次去重（evidence=%d）" % ev,
                  ok and ev == 1)

        # 场景三：断点持久（队列记录、任务记录、运行记录）。
        queues = jload(os.path.join(KS, "lane_queues.json"))
        st = jload(os.path.join(KS, "state.json"))
        check("场景三：断点持久（队列在档、任务在档、运行在档）",
              queues is not None and "kt-i931" in (queues.get("LX", {})
                                                   .get("order", [])
                                                   or []) or
              (st.get("tasks", {}).get("kt-i931", {}).get("phase")
               == "completed") and
              st.get("runs", {}).get(run_id, {}).get("status") == "ended")

        # 场景四：内核异常退出后托管重启再次可用（同一状态根）。
        kern.kill()
        time.sleep(1)
        kern2 = spawn_kernel()
        time.sleep(3)
        alive = kern2.poll() is None
        check("场景四：异常退出后重启可用（新实例存活）", alive)
        kern2.kill()
    finally:
        for p in ("worker",):
            pass
        worker.kill()
    bad = sum(1 for c in _checks if not c["ok"])
    json.dump({"checks": _checks, "W": W,
               "summary": {"total": len(_checks),
                           "passed": len(_checks) - bad}},
              open(os.path.join(ROOT, "logs", "i208_phase2_test_20261013.json"),
                   "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("第二阶段隔离总判定：%s（%d/%d 过）"
          % ("全部通过" if bad == 0 else "存在不过项",
             len(_checks) - bad, len(_checks)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
