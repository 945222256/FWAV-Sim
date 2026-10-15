# -*- coding: utf-8 -*-
"""lane_workers_drill_worker.py — I-217 演练的隔离队列工作器（工位感知）。

在 kernel_drill_worker 基础上增加：
  - dispatch 请求尊重 worker_id：每工位一个稳定假会话（首次生成后驻留），
    会话号、工位号、运行号、时间戳逐条落 dispatch_log.json（重叠证据）；
  - 每工位拉起一个真实执行器占位子进程（lane_workers_drill_executor.py），
    其 PID 写入工位注册表 executor_pid 与派发日志（判据二执行器进程号）；
  - dispatch_resume 同通道受理（断点接续走原会话）。

环境变量：
  DRILL_QUEUE_ROOT         隔离队列根（必填）
  DRILL_KERNEL_INBOX       内核收件箱（必填）
  DRILL_DISPATCH_LOG       派发日志路径（必填）
  DRILL_EXEC_DIR           执行器占位进程的工作目录（必填）
  LP_LANE_WORKERS_ROSTER   工位注册表（重定向到隔离件）
  FAKE_EXEC_MS             假执行耗时毫秒（缺省 800）
仅使用 Python 标准库。"""
import json
import os
import re
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import dispatch_file_queue as dq
import lane_workers as lw

QUEUE_ROOT = os.environ["DRILL_QUEUE_ROOT"]
KERNEL_INBOX = os.environ["DRILL_KERNEL_INBOX"]
DISPATCH_LOG = os.environ["DRILL_DISPATCH_LOG"]
EXEC_DIR = os.environ["DRILL_EXEC_DIR"]
EXEC_MS = int(os.environ.get("FAKE_EXEC_MS", "800"))

_lock = threading.Lock()
_worker_sessions = {}
_worker_execs = {}          # worker_id → (Popen, stop_file)
_session_seq = [0]

OWNER_NOTE = ("演练机主说明：本说明由 I-217 工位感知演练工作器生成，回答"
              "做了什么、结果是什么、为什么是这个结果、下一步是什么四问，"
              "用于满足结果校验的字数要求。")


def _log_dispatch(rec):
    with _lock:
        entries = []
        if os.path.exists(DISPATCH_LOG):
            try:
                entries = json.load(open(DISPATCH_LOG, encoding="utf-8"))
            except (OSError, ValueError):
                entries = []
        entries.append(rec)
        tmp = DISPATCH_LOG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DISPATCH_LOG)


def _ensure_worker_exec(worker_id):
    """每工位一个真实执行器占位子进程：常驻直到 stop 文件出现。"""
    with _lock:
        if worker_id in _worker_execs:
            popen, _ = _worker_execs[worker_id]
            if popen.poll() is None:
                return popen
        os.makedirs(EXEC_DIR, exist_ok=True)
        stop_file = os.path.join(EXEC_DIR, worker_id + ".stop")
        if os.path.exists(stop_file):
            os.remove(stop_file)
        env = dict(os.environ)
        env["DRILL_EXEC_STOP"] = stop_file
        popen = subprocess.Popen(
            [sys.executable, os.path.join(_HERE,
                                          "lane_workers_drill_executor.py"),
             "--worker", worker_id],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _worker_execs[worker_id] = (popen, stop_file)
        return popen


def _session_for(worker_id):
    with _lock:
        sid = _worker_sessions.get(worker_id)
        if sid is None:
            _session_seq[0] += 1
            sid = "sess_drill_%s_%02d_0000000000000000000000" % (
                worker_id.replace("-", "0"), _session_seq[0])
            _worker_sessions[worker_id] = sid
        return sid


def _write_result(run_id, item_id, outcome):
    os.makedirs(KERNEL_INBOX, exist_ok=True)
    rid = "kr-%s" % run_id
    if outcome == "evidence_appended":
        payload = {"schema_version": 1, "kind": "result_submit",
                   "result_id": rid, "run_id": run_id, "item_id": item_id,
                   "outcome": "evidence_appended",
                   "evidence": "I-217 演练阶段证据：工位执行完成 %s" % run_id}
    else:
        payload = {"schema_version": 1, "kind": "result_submit",
                   "result_id": rid, "run_id": run_id, "item_id": item_id,
                   "outcome": "closed", "conclusion": "判据通过",
                   "evidence": ["I-217 演练证据：%s 构造性运行完成" % run_id],
                   "criterion_check": [{"判据": "演练判据", "结果": "过",
                                        "数值出处": "I-217 演练脚本"}],
                   "owner_note": OWNER_NOTE, "closer": "L3队"}
    dst = os.path.join(KERNEL_INBOX, rid + ".json")
    tmp = dst + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)


def fake_dispatcher(req):
    """异常防护壳：任何异常都落日志并返回可重试失败，绝不带死桥线程。"""
    try:
        return _fake_dispatch_inner(req)
    except Exception:
        import traceback
        with open(os.path.join(os.path.dirname(DISPATCH_LOG),
                               "drill_worker_error.log"), "a",
                  encoding="utf-8") as f:
            f.write("[%s] %s\n%s\n" % (time.strftime("%H:%M:%S"),
                                       req.get("request_id"),
                                       traceback.format_exc()))
        return {"ok": False, "error": "drill dispatcher exception; see log",
                "retryable": True}


def _fake_dispatch_inner(req):
    msg = req.get("message") or ""
    worker_id = req.get("worker_id")
    if req["op"] == "dispatch_resume":
        sid = req.get("sid")
        _log_dispatch({"ts": time.time(), "iso": time.strftime(
            "%Y-%m-%dT%H:%M:%S"), "op": "dispatch_resume",
            "request_id": req["request_id"], "worker_id": worker_id,
            "session": sid, "run_id": req.get("run_id"),
            "item_id": req.get("item_id")})
        exec_ms = 800
        m_ms = re.search(r"执行毫秒[:：](\d+)", msg)
        if m_ms:
            exec_ms = int(m_ms.group(1))

        def execute():
            time.sleep(exec_ms / 1000.0)
            _write_result(req.get("run_id") or "run-unknown",
                          req.get("item_id") or "I-999", "evidence_appended")
        threading.Thread(target=execute, daemon=True).start()
        return {"ok": True, "session": sid, "channel": "resume",
                "accepted": True, "readback_verified": True}
    # dispatch_new
    _session_seq[0] += 1
    sid = _session_for(worker_id) if worker_id else \
        "sess_drill_plain%02d_00000000000000000000" % _session_seq[0]
    m_out = re.search(r"结果类型[:：]([A-Za-z_]+)", msg)
    outcome = m_out.group(1) if m_out else "evidence_appended"
    m_ms = re.search(r"执行毫秒[:：](\d+)", msg)
    exec_ms = int(m_ms.group(1)) if m_ms else EXEC_MS
    m = re.search(r"run-[A-Za-z0-9_.\-]+", msg)
    run_id = m.group(0) if m else "run-unknown"
    executor_pid = None
    if worker_id:
        popen = _ensure_worker_exec(worker_id)
        executor_pid = popen.pid
        lw.worker_bind(worker_id, req.get("lane"), session_id=sid,
                       executor_pid=executor_pid, phase="active",
                       item_id=req.get("item_id"), run_id=run_id)
    _log_dispatch({"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "op": "dispatch_new", "request_id": req["request_id"],
                   "worker_id": worker_id, "session": sid, "run_id": run_id,
                   "item_id": req.get("item_id"),
                   "executor_pid": executor_pid})

    def execute():
        time.sleep(exec_ms / 1000.0)
        _write_result(run_id, req.get("item_id") or "I-999", outcome)
    threading.Thread(target=execute, daemon=True).start()
    return {"ok": True, "session": sid, "channel": "worker" if worker_id
            else "test", "accepted": True, "readback_verified": True}


def main():
    worker = dq.QueueWorker(root=QUEUE_ROOT, dispatcher=fake_dispatcher,
                            poll_interval_s=0.5, stable_grace_s=0.2)
    print("[I-217 演练工作器] 隔离队列 %s" % QUEUE_ROOT, flush=True)

    class _HeartbeatWorker(threading.Thread):
        """桥线程心跳 + 僵死看门狗：心跳留痕；队列台账停走超过二十秒
        判僵死，自退出交外层自愈重拉（磁盘状态可恢复，重启零损失）。"""
        def run(self):
            last_mtime = 0
            while True:
                time.sleep(5)
                print("[心跳] %s alive" % time.strftime("%H:%M:%S"), flush=True)
                try:
                    m = os.path.getmtime(os.path.join(QUEUE_ROOT, "ledger.json"))
                except OSError:
                    continue
                if m > last_mtime:
                    last_mtime = m
                    continue
                if time.time() - last_mtime > 20:
                    print("[看门狗] 队列台账停走超二十秒，自退出待重拉",
                          flush=True)
                    os._exit(81)
    _HeartbeatWorker(daemon=True).start()
    worker.run()
    print("[I-217 演练工作器] run() 退出", flush=True)


if __name__ == "__main__":
    main()
