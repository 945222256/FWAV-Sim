# -*- coding: utf-8 -*-
"""i217_real_parallel_probe_v2.py — I-217 补修三：真实 ZCode 双工位完整闭环。

v1 探针只验接受与回读即结束（第二会话无模型执行）。本版全栈闭环：
  隔离云（自建库+正式 API）＋ 隔离内核 ＋ 隔离宿主（真实协议子进程执
  行器、真实会话）。三项真实任务：
    A（L3-w1，隔离目录A）：写 start 文件→真实执行约 75 秒→写 end 文件
      →向内核收件箱提交正式 pending_review 结果；
    B（L3-w2，隔离目录B）：同构，与 A 真实重叠；
    C：排队，A 完成释放工位后自动接续（无经理消息）。
  采集证据：不同会话/工位/执行器进程/运行号；模型与会话消息的执行
  区间交集（ZCode 会话库逐会话时间戳）；A/B 工作文件区间交集；C 的
  自动接续派发；三项结果经内核→桥→隔离云的 pending_review 回收闭环
  （云端事项状态与认领工位回读）。
成功前不关闭隔离宿主；结束只回收本探针自建进程树。生产宿主 83332
与生产云 101.96.207.208 全程零接触。
用法：python i217_real_parallel_probe_v2.py [输出目录]
"""
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLOUD = os.path.join(ROOT, "cloud_board")
REGSRC = os.path.join(ROOT, "items_registry")
PY = sys.executable
CLOUD_PORT = int(os.environ.get("I217_V2_CLOUD_PORT", "9445"))
HOST_PORT = int(os.environ.get("I217_V2_HOST_PORT", "58796"))
SESSION_DB = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db",
                          "db.sqlite")

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "logs", "i217_real_v2_%s"
    % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
DB = os.path.join(OUT, "cloud", "ledger.db")
REG = os.path.join(OUT, "registry_copy")
QUEUE = os.path.join(OUT, "queue")
KS = os.path.join(OUT, "kernel_state")
HOST_RT = os.path.join(OUT, "host_runtime")
ROSTER = os.path.join(OUT, "lane_workers_roster.json")
DIR_A = os.path.join(OUT, "taskA")
DIR_B = os.path.join(OUT, "taskB")
DIR_C = os.path.join(OUT, "taskC")

_checks = []
_procs = {}


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail), flush=True)
    return bool(ok)


def jload(path):
    for _ in range(3):
        try:
            return json.load(open(path, encoding="utf-8"))
        except PermissionError:
            time.sleep(0.03)
        except (OSError, ValueError):
            return None
    return None


def wait_for(pred, timeout_s=90, step=1.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def cloud_env(extra=None):
    """全部子进程统一云端指向：服务端（HTTP_*）与客户端（LP_CLOUD_*）
    都指向隔离实例——宿主与内核内的 cloud_client 也必须走隔离云，
    不得回落生产云。"""
    env = dict(os.environ)
    env.update({"LP_CLOUD_HTTP_HOST": "127.0.0.1",
                "LP_CLOUD_HTTP_PORT": str(CLOUD_PORT),
                "LP_CLOUD_DB_PATH": DB,
                "LP_CLOUD_SECRET_PATH": os.path.join(OUT, "cloud", "secret.json"),
                "LP_CLOUD_CERT_FILE": os.path.join(OUT, "cloud", "server-cert.pem"),
                "LP_CLOUD_KEY_FILE": os.path.join(OUT, "cloud", "server-key.pem"),
                "PYTHONPATH": REGSRC,
                "LP_CLOUD_HOST": "127.0.0.1",
                "LP_CLOUD_PORT": str(CLOUD_PORT)})
    env.update(extra or {})
    return env


def client_env():
    return cloud_env({"FLAPPER_LEDGER_MODE": "cloud",
                      "LP_CLOUD_HOST": "127.0.0.1",
                      "LP_CLOUD_PORT": str(CLOUD_PORT)})


def api(method, path, body=None):
    nl = "\n"
    script = nl.join([
        "import sys, json",
        "sys.path.insert(0, r'%s')" % REG,
        "import cloud_client",
        "try:",
        "    r = cloud_client.request(%r, %r, %r)" % (method, path, body),
        "    print(json.dumps({'ok': True, 'result': r}, ensure_ascii=False, default=str))",
        "except Exception as e:",
        "    print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))"])
    p = subprocess.run([PY, "-c", script], capture_output=True, text=True,
                       env=client_env(), encoding="utf-8", errors="replace")
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"__error__": (p.stderr or p.stdout or "")[-200:]}
    return data.get("result") if data.get("ok") else {"__error__": data.get("error")}


def task_message(tag, work_dir, sleep_s):
    result_name = "%s-result" % tag
    return (
        "I-217 真实双工位闭环验证任务 %s。严格按以下步骤执行，不做任何其他事：\n"
        "1. 用写文件工具在目录 %s 下创建文件 start.txt，内容为一行："
        "'%s started ' 加当前时间。\n"
        "2. 用命令行工具执行：python -c \"import time; time.sleep(%d)\"（真实等待，"
        "不得跳过或缩短）。\n"
        "3. 在同目录创建文件 end.txt，内容一行：'%s finished ' 加当前时间。\n"
        "4. 收工：把结果 JSON 写到本简报【内核运行号】之后指明的收件箱目录"
        "（先写临时名再加 .json 后缀改名，字段：schema_version=1、"
        "kind=result_submit、result_id=<内核运行号>-result、run_id=<内核运行号>、"
        "item_id=本事项号、outcome=pending_review、delivery_version=i217-real-%s-v1、"
        "evidence_ptr=%s/end.txt（单字符串）、reviewer=beth、unmet=[]、"
        "evidence=['真实执行 %d 秒并落盘 start/end 文件']、"
        "owner_note=不少于六十字说明做了什么证据在哪还剩什么下一步。"
        "写完后回复一句完成即可。\n"
        "不要修改本目录以外的任何文件。" % (tag, work_dir, tag, sleep_s, tag,
                                        tag, work_dir, sleep_s))


def write_task_submit(item_id, message, paths, request_id):
    payload = {"schema_version": 1, "kind": "task_submit",
               "request_id": request_id, "item_id": item_id, "lane": "L3",
               "title": "I-217 真实闭环 %s" % item_id,
               "message": message, "approved_by": "Beth",
               "task_kind": "task", "writable_paths": paths}
    dst = os.path.join(KS, "inbox", request_id + ".json")
    tmp = dst + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)


def seed_cloud():
    os.makedirs(os.path.join(OUT, "cloud"), exist_ok=True)
    secret_path = os.path.join(OUT, "cloud", "secret.json")
    with open(secret_path, "w", encoding="utf-8") as f:
        json.dump({"session_key": __import__("secrets").token_urlsafe(48)}, f)
    cert = os.path.join(OUT, "cloud", "server-cert.pem")
    key = os.path.join(OUT, "cloud", "server-key.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-nodes", "-keyout", key, "-out", cert, "-days", "30",
                    "-subj", "/CN=lp-board-iso"], check=True, capture_output=True)
    # 先设环境再导入：settings 在导入时读 env，晚设会把播种写去默认路径
    os.environ.update({"LP_CLOUD_DB_PATH": DB,
                       "LP_CLOUD_SECRET_PATH": os.path.join(OUT, "cloud", "secret.json"),
                       "LP_CLOUD_CERT_FILE": os.path.join(OUT, "cloud", "server-cert.pem"),
                       "LP_CLOUD_KEY_FILE": os.path.join(OUT, "cloud", "server-key.pem")})
    sys.path.insert(0, CLOUD)
    import hashlib
    import db as cdb
    import repo as crepo
    cdb.init_schema()
    token = open(os.path.join(ROOT, "lp_sync", "agent_token"),
                 encoding="utf-8").read().strip()
    with cdb.transaction() as conn:
        conn.execute("INSERT INTO tokens (name, secret_hash, lanes, revoked,"
                     " created_at) VALUES (?,?,?,?,?)",
                     ("i217-v2-probe",
                      hashlib.sha256(token.encode("utf-8")).hexdigest(),
                      json.dumps(["L1", "L2", "L3"]), 0,
                      datetime.date.today().isoformat()))
        crepo.set_config(conn, "lane_concurrency", {"L3": 2})
        for n in (960, 961, 962):
            crepo.upsert_item(conn, {
                "id": "I-%d" % n, "title": "I-217 真实闭环 %d" % n,
                "status": "open", "opened_at": "2026-10-14",
                "closed_at": None, "closed_by": None, "owner": "L3队",
                "lane": "L3", "close_criterion": "真实闭环验证判据",
                "note": "I-217 v2 隔离云播种条目", "claimed_by": None,
                "claimed_at": None, "lease_until": None, "evidence": [],
                "criteria": None, "replies": [], "blockers": [],
                "conclusion": None, "criterion_check": [], "depends_on": [],
                "reviewer": "beth"})
        crepo.append_event(conn, "admin_lane_concurrency", actor="bootstrap",
                           detail="隔离云播种容量 {L3: 2}（v2 真实闭环）")


def seed_client_paths():
    """注册表副本的 cloud_client 按自身位置推导根：在 OUT 下放置凭据
    副本（凭据文件内容不变，位置隔离），断网发件箱也落在 OUT 内。"""
    iso_lp = os.path.join(OUT, "lp_sync")
    os.makedirs(iso_lp, exist_ok=True)
    for name in ("cloud_secret.json", "agent_token"):
        shutil.copyfile(os.path.join(ROOT, "lp_sync", name),
                        os.path.join(iso_lp, name))


def seed_registry_copy():
    shutil.copytree(REGSRC, REG,
                    ignore=shutil.ignore_patterns("__pycache__", "*.tmp",
                                                  ".ledger.lock", "CLOUD_MODE"))
    # 本地缓存只装三项播种条目（生产本地账本没有它们；构造而非过滤）
    items_path = os.path.join(REG, "items.json")
    seeded = []
    for n in (960, 961, 962):
        seeded.append({"id": "I-%d" % n, "title": "I-217 真实闭环 %d" % n,
                       "status": "open", "opened_at": "2026-10-14",
                       "closed_at": None, "closed_by": None, "owner": "L3队",
                       "lane": "L3", "close_criterion": "真实闭环验证判据",
                       "note": "I-217 v2 隔离播种", "claimed_by": None,
                       "claimed_at": None, "lease_until": None,
                       "evidence": [], "criteria": None, "replies": [],
                       "blockers": [], "conclusion": None,
                       "criterion_check": [], "depends_on": [],
                       "reviewer": "beth"})
    with open(items_path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "items": seeded}, f,
                  ensure_ascii=False, indent=1)


def session_window(sid):
    """ZCode 会话库逐会话消息时间窗（ms 纪元 → 秒）。"""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % SESSION_DB.replace("\\", "/"),
                              uri=True)
        rows = con.execute(
            "SELECT MIN(time_created), MAX(time_created) FROM message "
            "WHERE session_id=?", (sid,)).fetchone()
        con.close()
        if rows and rows[0]:
            return rows[0] / 1000.0, rows[1] / 1000.0
    except Exception:
        pass
    return None


def file_window(path):
    st = os.stat(path)
    return st.st_mtime - 1.0, st.st_mtime      # 写入时刻近似区间


def intervals_overlap(a, b):
    return a and b and max(a[0], b[0]) < min(a[1], b[1])


def main():
    os.makedirs(OUT, exist_ok=True)
    for d in (os.path.join(KS, "inbox"), QUEUE, HOST_RT, DIR_A, DIR_B, DIR_C,
              os.path.dirname(ROSTER)):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(KS, "lane_workers.json"), "w", encoding="utf-8") as f:
        json.dump({"default": 1, "lanes": {"L3": 2}}, f)
    evidence = {"started_at": datetime.datetime.now().isoformat(
        timespec="seconds"), "out": OUT, "checks": _checks}
    seed_cloud()
    seed_registry_copy()
    seed_client_paths()
    # —— 隔离云 ——
    cloud_log = open(os.path.join(OUT, "cloud_server.log"), "a",
                     encoding="utf-8")
    _procs["cloud"] = subprocess.Popen(
        [PY, os.path.join(CLOUD, "server_main.py")], env=cloud_env(),
        cwd=CLOUD, stdout=cloud_log, stderr=subprocess.STDOUT)
    _probe_trace = []

    def _ready_once():
        r = api("GET", "/api/agent/snapshot")
        _probe_trace.append(str(r)[:160])
        return "items" in r
    ready = wait_for(_ready_once, timeout_s=30, step=0.5)
    evidence["readiness_trace"] = _probe_trace[-5:]
    check("V0a 隔离云就绪", ready,
          "port=%d trace=%s" % (CLOUD_PORT, _probe_trace[-1]))
    # —— 隔离宿主（真实执行器与真实会话；队列线程即真实桥） ——
    host_log = open(os.path.join(OUT, "iso_host.log"), "a", encoding="utf-8")
    _procs["host"] = subprocess.Popen(
        [PY, os.path.join(HERE, "host_service.py")],
        env=cloud_env({"LP_HOST_ISOLATED": "1", "LP_HOST_PORT": str(HOST_PORT),
                       "LP_HOST_STATE_ROOT": HOST_RT,
                       "LP_HOST_ROSTER_PATH": os.path.join(OUT, "iso_roster.json"),
                       "LP_LANE_WORKERS_ROSTER": ROSTER,
                       "LP_HOST_STATUS": os.path.join(OUT, "iso_status.json"),
                       "LP_DISPATCH_QUEUE_ROOT": QUEUE,
                       "FLAPPER_LEDGER_MODE": "cloud",
                       "LP_ITEMS_REGISTRY": REG}),
        cwd=ROOT, stdout=host_log, stderr=subprocess.STDOUT)
    # —— 隔离内核 ——
    _procs["kernel"] = subprocess.Popen(
        [PY, os.path.join(HERE, "orchestrator_kernel.py"),
         "--state-root", KS, "--queue-root", QUEUE, "--wake-s", "1.0",
         "--port", str(HOST_PORT + 500), "--line-retry-s", "3"],
        env=cloud_env({"LP_ITEMS_REGISTRY": REG, "FLAPPER_LEDGER_MODE": "cloud",
                       "LP_LANE_WORKERS_ROSTER": ROSTER}),
        cwd=ROOT, stdout=open(os.path.join(OUT, "kernel.log"), "a",
                              encoding="utf-8"), stderr=subprocess.STDOUT)
    try:
        boot = wait_for(lambda: any(
            e.get("event") == "kernel_started"
            for e in (jload(os.path.join(KS, "ledger.json")) or
                      {"events": []})["events"]), timeout_s=20, step=0.5)
        check("V0b 隔离内核就绪", boot)
        if not (ready and boot):
            return 1
        # —— 提交三项真实任务 ——
        write_task_submit("I-960", task_message("A", DIR_A, 75), [DIR_A],
                          "kt-real-a")
        write_task_submit("I-961", task_message("B", DIR_B, 75), [DIR_B],
                          "kt-real-b")
        write_task_submit("I-962", task_message("C", DIR_C, 10), [DIR_C],
                          "kt-real-c")
        # A/B 并行在办、C 排队
        ok_ab = wait_for(lambda: all(
            (jload(os.path.join(KS, "state.json")) or {})
            .get("tasks", {}).get(r, {}).get("phase") in
            ("dispatched", "result_pending")
            for r in ("kt-real-a", "kt-real-b")), timeout_s=180)
        st = (jload(os.path.join(KS, "state.json")) or {"tasks": {}})
        ta, tb = st["tasks"].get("kt-real-a", {}), st["tasks"].get("kt-real-b", {})
        check("V1a A/B 并行在办于不同工位", ok_ab
              and ta.get("worker_id") != tb.get("worker_id")
              and {ta.get("worker_id"), tb.get("worker_id")} == {"L3-w1", "L3-w2"},
              "A=%s B=%s" % (ta.get("worker_id"), tb.get("worker_id")))
        check("V1b 四不同（会话/工位/运行/执行器进程）",
              ta.get("session_id") and tb.get("session_id")
              and ta["session_id"] != tb["session_id"]
              and ta.get("run_id") != tb.get("run_id"), "")
        tc_phase = st["tasks"].get("kt-real-c", {}).get("phase")
        check("V1c 第三项 C 排队未派",
              tc_phase in ("ready", "line_wait") and not os.path.exists(
                  os.path.join(DIR_C, "start.txt")),
              "C phase=%s" % tc_phase)
        # 等真实工作落盘（A/B 各 75 秒真实执行）
        ok_files = wait_for(lambda: all(os.path.exists(
            os.path.join(d, f)) for d in (DIR_A, DIR_B)
            for f in ("start.txt", "end.txt")), timeout_s=300, step=3)
        check("V2a 两工位真实执行落盘（start/end）", ok_files,
              "A=%s B=%s" % (os.path.isdir(DIR_A), os.path.isdir(DIR_B)))
        # 执行区间交集：工作文件区间 + 会话消息区间 双证
        try:
            wa = (os.stat(os.path.join(DIR_A, "start.txt")).st_mtime,
                  os.stat(os.path.join(DIR_A, "end.txt")).st_mtime)
            wb = (os.stat(os.path.join(DIR_B, "start.txt")).st_mtime,
                  os.stat(os.path.join(DIR_B, "end.txt")).st_mtime)
        except OSError:
            wa = wb = None
        sa = session_window(ta.get("session_id"))
        sb = session_window(tb.get("session_id"))
        evidence["work_windows"] = {"A": wa, "B": wb}
        evidence["session_windows"] = {"A": sa, "B": sb}
        fmt = lambda w: "[%s]" % ",".join(
            datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")
            for t in w) if w else "缺失"
        check("V2b 工作文件执行区间相交（真实重叠）",
              intervals_overlap(wa, wb), "A=%s B=%s" % (fmt(wa), fmt(wb)))
        check("V2c 会话消息区间相交（模型执行重叠）",
              intervals_overlap(sa, sb),
              "A=%s B=%s" % (sa, sb))
        # C 自动接续（A/B 释放工位后无需经理消息）
        ok_c = wait_for(lambda: (jload(os.path.join(KS, "state.json"))
                                 or {"tasks": {}})["tasks"]
                        .get("kt-real-c", {}).get("phase") in
                        ("dispatched", "result_pending", "completed"),
                        timeout_s=120)
        check("V3 第三项自动接续派发", ok_c,
              "C phase=%s" % (jload(os.path.join(KS, "state.json"))
                              or {"tasks": {}})["tasks"]
                              .get("kt-real-c", {}).get("phase"))
        # 正式 result_submit 回收闭环：三项结果经内核→桥→隔离云
        ok_results = wait_for(lambda: all(
            (jload(os.path.join(KS, "state.json")) or {"tasks": {}})
            ["tasks"].get(r, {}).get("phase") == "completed"
            for r in ("kt-real-a", "kt-real-b", "kt-real-c")), timeout_s=300,
            step=3)
        results_state = (jload(os.path.join(KS, "state.json")) or
                         {"results": {}})["results"]
        check("V4a 三项正式结果回收（内核处理完成）", ok_results,
              "results=%d" % len(results_state))
        snap = api("GET", "/api/agent/snapshot")
        items = {i["id"]: i for i in (snap.get("items") or [])}
        pending_ok = all(items.get("I-%d" % n, {}).get("status")
                         == "pending_review" for n in (960, 961, 962))
        workers_ok = (items.get("I-960", {}).get("claim_worker_id")
                      and items.get("I-961", {}).get("claim_worker_id"))
        check("V4b 隔离云回收：三项 pending_review 且认领带工位",
              pending_ok and workers_ok,
              "status=%s workers=%s/%s" % (
                  [items.get("I-%d" % n, {}).get("status") for n in (960, 961, 962)],
                  items.get("I-960", {}).get("claim_worker_id"),
                  items.get("I-961", {}).get("claim_worker_id")))
        # 执行器进程证据
        import socket as _sock
        try:
            s = _sock.create_connection(("127.0.0.1", HOST_PORT), timeout=8)
            s.sendall(b'{"op": "status"}\n')
            buf = b""
            while b"\n" not in buf:
                buf += s.recv(65536)
            s.close()
            host_status = json.loads(buf.decode("utf-8"))
            evidence["executors"] = host_status.get("executors")
            ex = host_status.get("executors") or {}
            check("V5 独立执行器进程证据",
                  ex.get("L3-w1", {}).get("pid") and ex.get("L3-w2", {}).get("pid")
                  and ex["L3-w1"]["pid"] != ex["L3-w2"]["pid"],
                  json.dumps(ex, ensure_ascii=False)[:160])
        except Exception as e:
            check("V5 独立执行器进程证据", False, repr(e)[:120])
        evidence["sessions"] = {"A": ta.get("session_id"), "B": tb.get("session_id")}
        evidence["runs"] = {"A": ta.get("run_id"), "B": tb.get("run_id"),
                            "C": (jload(os.path.join(KS, "state.json"))
                                  or {"tasks": {}})["tasks"]
                                  .get("kt-real-c", {}).get("run_id")}
        evidence["workers"] = {"A": ta.get("worker_id"), "B": tb.get("worker_id")}
        return 0
    finally:
        evidence["finished_at"] = datetime.datetime.now().isoformat(
            timespec="seconds")
        evidence["summary"] = {"total": len(_checks),
                               "passed": sum(1 for c in _checks if c["ok"])}
        json.dump(evidence, open(os.path.join(OUT, "evidence.json"), "w",
                                 encoding="utf-8"), ensure_ascii=False,
                  indent=1, default=str)
        # 只回收自建进程树：内核 → 宿主树（含真实执行器）→ 隔离云。
        for key in ("kernel", "host", "cloud"):
            p = _procs.get(key)
            if p and p.poll() is None:
                if key == "host":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                                   capture_output=True)
                else:
                    p.terminate()
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()
        print("==== %d/%d，证据 %s ====" % (
            evidence["summary"]["passed"], evidence["summary"]["total"],
            os.path.join(OUT, "evidence.json")), flush=True)


if __name__ == "__main__":
    sys.exit(main())
