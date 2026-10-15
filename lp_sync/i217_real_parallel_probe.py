# -*- coding: utf-8 -*-
"""i217_real_parallel_probe.py — I-217 真实双工位并行证据探针。

起一个完全隔离的宿主实例（新代码、独立端口/状态根/队列根/花名册/
工位注册表，LP_HOST_ISOLATED=1），经其派发两个带 worker_id 的真实
任务：每工位一个真实协议子进程执行器、一个真实会话。采集证据：
不同工位号、不同执行器进程号、不同真实会话号、两任务同时被接受并
回读核验的时间窗。全程不触碰生产宿主 83332；结束只终止本探针自建
的隔离宿主与其子进程。

用法：python i217_real_parallel_probe.py [输出目录]
仅使用 Python 标准库。"""
import datetime
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
PORT = int(os.environ.get("I217_PROBE_PORT", "58793"))

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "logs", "i217_real_parallel_%s"
    % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))


def host_call(op_payload, timeout=180):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=15)
    s.sendall((json.dumps(op_payload) + "\n").encode("utf-8"))
    buf = b""
    deadline = time.time() + timeout
    while b"\n" not in buf and time.time() < deadline:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.decode("utf-8"))


def main():
    os.makedirs(OUT, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "LP_HOST_ISOLATED": "1",
        "LP_HOST_PORT": str(PORT),
        "LP_HOST_STATE_ROOT": os.path.join(OUT, "host_runtime"),
        "LP_HOST_ROSTER_PATH": os.path.join(OUT, "iso_roster.json"),
        "LP_LANE_WORKERS_ROSTER": os.path.join(OUT, "iso_lane_workers_roster.json"),
        "LP_HOST_STATUS": os.path.join(OUT, "iso_status.json"),
        "LP_DISPATCH_QUEUE_ROOT": os.path.join(OUT, "iso_queue"),
    })
    for key in ("LP_HOST_STATE_ROOT", "LP_DISPATCH_QUEUE_ROOT"):
        os.makedirs(env[key], exist_ok=True)
    logf = open(os.path.join(OUT, "iso_host.log"), "a", encoding="utf-8")
    host = subprocess.Popen([PY, os.path.join(HERE, "host_service.py")],
                            env=env, stdout=logf, stderr=subprocess.STDOUT,
                            cwd=ROOT)
    evidence = {"started_at": datetime.datetime.now().isoformat(
        timespec="seconds"), "isolated_port": PORT, "host_pid": host.pid,
        "note": "隔离宿主实例（新代码）双工位真实派发；不触碰生产宿主 83332"}
    try:
        # 等隔离宿主监听就绪
        ready = False
        for _ in range(60):
            if host.poll() is not None:
                break
            try:
                st = host_call({"op": "status"}, timeout=10)
                ready = bool(st.get("ok"))
                break
            except Exception:
                time.sleep(1)
        evidence["host_ready"] = ready
        if not ready:
            evidence["host_log_tail"] = open(os.path.join(
                OUT, "iso_host.log"), encoding="utf-8").read()[-800:]
            return 1

        results = {}
        for worker, tag in (("L3-w1", "A"), ("L3-w2", "B")):
            t0 = datetime.datetime.now().isoformat(timespec="milliseconds")
            res = host_call({
                "op": "dispatch_new",
                "title": "I-217 真实并行证据 %s（%s）" % (tag, worker),
                "message": ("I-217 工位并行证据采集。请只回复「工位 %s 就绪」"
                            "几个字，不要执行任何其他操作、不要读写文件。"
                            % worker),
                "kind": "test", "lane": "L3", "worker_id": worker,
                "request_id": "i217-real-%s-%d" % (tag, int(time.time())),
                "verify": True})
            t1 = datetime.datetime.now().isoformat(timespec="milliseconds")
            results[worker] = {"sent_at": t0, "done_at": t1,
                               "session": res.get("session"),
                               "accepted": res.get("accepted"),
                               "readback_verified": res.get("readback_verified"),
                               "error": res.get("error")}
        evidence["dispatches"] = results
        st = host_call({"op": "status"}, timeout=15)
        evidence["executors"] = st.get("executors")
        # 工位注册表（隔离件）回读
        roster_path = env["LP_LANE_WORKERS_ROSTER"]
        if os.path.exists(roster_path):
            evidence["worker_registry"] = json.load(
                open(roster_path, encoding="utf-8")).get("workers", {})
        a, b = results.get("L3-w1", {}), results.get("L3-w2", {})
        evidence["parallel_real"] = bool(
            a.get("session") and b.get("session")
            and a["session"] != b["session"]
            and a.get("readback_verified") and b.get("readback_verified"))
        return 0
    finally:
        evidence["finished_at"] = datetime.datetime.now().isoformat(
            timespec="seconds")
        with open(os.path.join(OUT, "evidence.json"), "w", encoding="utf-8") as f:
            json.dump(evidence, f, ensure_ascii=False, indent=1)
        # 只终止自建隔离宿主的进程树（含其执行器子进程），不碰任何
        # 其他进程；生产宿主 83332 经 svc_entry 启动，不在本树内。
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(host.pid)],
                       capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
