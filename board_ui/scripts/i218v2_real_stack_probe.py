# -*- coding: utf-8 -*-
"""i218v2_real_stack_probe.py — I-218 返修（lane-workers-ui-v2）真实双会话页面联调探针。

复用 i217_real_parallel_probe_v2.py 的隔离全栈入口（隔离云自建库+正式 API、
隔离内核、隔离宿主真实协议子进程执行器与真实会话），在其上补齐观察窗
数据链路（生产同路径）：
  采集 mimic：按 console_collector 同口径（会话库只读、最近完成请求
    令牌数、九十秒在干活窗口）每五秒把内核在册活跃任务的真实会话行
    上行 /api/console/ingest；
  同步 mimic：每三秒把隔离内核 kernel_state/lane_queues.json 与
    lanes.json 经 /api/agent/heartbeat 上行（lane_queue_snapshot 与
    lanes 云端配置，同生产 sync_agent 路径）。
页面侧：静态代理（4175）服务 board_ui/dist 并把 /api 反代到隔离云；
puppeteer 真页面检查（i218v2_real_page_check.cjs 阶段一/二）：
  阶段一（A/B 双工位执行中）：真实回读三方一致（内核快照=云端
    lane_queues 接口=页面两张卡）、注入/停止/履历/出处完整会话号定
    向、云端 console_tasks 只指向本工位；
  阶段二（D 活跃+另一工位驻留灰卡并存）：Ciri 反例二在真实栈消失。
任务与 i217 同构（写 start→真实 sleep→写 end→正式 result_submit），
A/B 各 110 秒真实重叠。驻留语义演示：D 派发后把另一工位最后会话行
以四十四分钟前 updated_at 直写隔离库（模拟超时驻留的真实语义边界，
身份与事项均为真实值，证据中如实标注）。
只回收本探针自建进程树（内核、宿主树、隔离云、静态代理）；生产宿主
83332 与生产云 101.96.207.208 零接触零写。
用法：python i218v2_real_stack_probe.py [输出目录]
"""
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))            # board_ui/scripts
BOARD_UI = os.path.dirname(HERE)
ROOT = os.path.dirname(BOARD_UI)                             # isaac_flapper_sim
CLOUD = os.path.join(ROOT, "cloud_board")
REGSRC = os.path.join(ROOT, "items_registry")
LP_SYNC = os.path.join(ROOT, "lp_sync")
PY = sys.executable
CLOUD_PORT = int(os.environ.get("I218V2_CLOUD_PORT", "9447"))
HOST_PORT = int(os.environ.get("I218V2_HOST_PORT", "58798"))
PROXY_PORT = int(os.environ.get("I218V2_PROXY_PORT", "4175"))
SESSION_DB = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite")

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "logs", "i218_ui_real_%s" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
DB = os.path.join(OUT, "cloud", "ledger.db")
REG = os.path.join(OUT, "registry_copy")
QUEUE = os.path.join(OUT, "queue")
KS = os.path.join(OUT, "kernel_state")
HOST_RT = os.path.join(OUT, "host_runtime")
ROSTER = os.path.join(OUT, "lane_workers_roster.json")
DIRS = {t: os.path.join(OUT, "task" + t) for t in "ABCD"}
ITEMS = {"A": "I-970", "B": "I-971", "C": "I-972", "D": "I-973"}
SLEEPS = {"A": 110, "B": 110, "C": 10, "D": 25}
REQS = {"A": "kt-ui-a", "B": "kt-ui-b", "C": "kt-ui-c", "D": "kt-ui-d"}

_checks = []
_procs = {}
_stop = threading.Event()
EVIDENCE = {"started_at": datetime.datetime.now().isoformat(
    timespec="seconds"), "out": OUT, "checks": _checks}


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
    return cloud_env({"FLAPPER_LEDGER_MODE": "cloud"})


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
        return {"__error__": (p.stderr or p.stdout or "")[-300:]}
    return data.get("result") if data.get("ok") else {"__error__": data.get("error")}


def task_message(tag, work_dir, sleep_s):
    return (
        "I-218 真实双会话页面联调任务 %s。严格按以下步骤执行，不做任何其他事：\n"
        "1. 用写文件工具在目录 %s 下创建文件 start.txt，内容为一行："
        "'%s started ' 加当前时间。\n"
        "2. 用命令行工具执行：python -c \"import time; time.sleep(%d)\"（真实等待，"
        "不得跳过或缩短）。\n"
        "3. 在同目录创建文件 end.txt，内容一行：'%s finished ' 加当前时间。\n"
        "4. 收工：把结果 JSON 写到本简报【内核运行号】之后指明的收件箱目录"
        "（先写临时名再加 .json 后缀改名，字段：schema_version=1、"
        "kind=result_submit、result_id=<内核运行号>-result、run_id=<内核运行号>、"
        "item_id=本事项号、outcome=pending_review、delivery_version=i218-ui-real-%s-v1、"
        "evidence_ptr=%s/end.txt（单字符串）、reviewer=beth、unmet=[]、"
        "evidence=['真实执行 %d 秒并落盘 start/end 文件']、"
        "owner_note=不少于六十字说明做了什么证据在哪还剩什么下一步。"
        "写完后回复一句完成即可。\n"
        "不要修改本目录以外的任何文件。" % (tag, work_dir, tag, sleep_s, tag,
                                        tag, work_dir, sleep_s))


def write_task_submit(tag):
    payload = {"schema_version": 1, "kind": "task_submit",
               "request_id": REQS[tag], "item_id": ITEMS[tag], "lane": "L3",
               "title": "I-218 真实联调 %s" % ITEMS[tag],
               "message": task_message(tag, DIRS[tag], SLEEPS[tag]),
               "approved_by": "Beth",
               "task_kind": "task", "writable_paths": [DIRS[tag]]}
    dst = os.path.join(KS, "inbox", REQS[tag] + ".json")
    tmp = dst + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)


def seed_cloud():
    os.makedirs(os.path.join(OUT, "cloud"), exist_ok=True)
    with open(os.path.join(OUT, "cloud", "secret.json"), "w", encoding="utf-8") as f:
        json.dump({"session_key": __import__("secrets").token_urlsafe(48)}, f)
    cert = os.path.join(OUT, "cloud", "server-cert.pem")
    key = os.path.join(OUT, "cloud", "server-key.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-nodes", "-keyout", key, "-out", cert, "-days", "30",
                    "-subj", "/CN=lp-board-iso"], check=True, capture_output=True)
    os.environ.update({"LP_CLOUD_DB_PATH": DB,
                       "LP_CLOUD_SECRET_PATH": os.path.join(OUT, "cloud", "secret.json"),
                       "LP_CLOUD_CERT_FILE": cert,
                       "LP_CLOUD_KEY_FILE": key})
    sys.path.insert(0, CLOUD)
    import hashlib
    import db as cdb
    import repo as crepo
    cdb.init_schema()
    token = open(os.path.join(LP_SYNC, "agent_token"), encoding="utf-8").read().strip()
    with cdb.transaction() as conn:
        conn.execute("INSERT INTO tokens (name, secret_hash, lanes, revoked,"
                     " created_at) VALUES (?,?,?,?,?)",
                     ("i218-ui-probe",
                      hashlib.sha256(token.encode("utf-8")).hexdigest(),
                      json.dumps(["L1", "L2", "L3"]), 0,
                      datetime.date.today().isoformat()))
        crepo.set_config(conn, "lane_concurrency", {"L3": 2})
        for tag, iid in ITEMS.items():
            crepo.upsert_item(conn, {
                "id": iid, "title": "I-218 真实联调 %s" % iid,
                "status": "open", "opened_at": "2026-10-14",
                "closed_at": None, "closed_by": None, "owner": "L5队",
                "lane": "L3", "close_criterion": "真实联调验证判据",
                "note": "I-218 v2 隔离云播种条目（页面联调）", "claimed_by": None,
                "claimed_at": None, "lease_until": None, "evidence": [],
                "criteria": None, "replies": [], "blockers": [],
                "conclusion": None, "criterion_check": [], "depends_on": [],
                "reviewer": "beth"})
        crepo.append_event(conn, "admin_lane_concurrency", actor="bootstrap",
                           detail="隔离云播种容量 {L3: 2}（I-218 页面联调）")


def seed_registry_copy():
    shutil.copytree(REGSRC, REG,
                    ignore=shutil.ignore_patterns("__pycache__", "*.tmp",
                                                  ".ledger.lock", "CLOUD_MODE"))
    seeded = []
    for tag, iid in ITEMS.items():
        seeded.append({"id": iid, "title": "I-218 真实联调 %s" % iid,
                       "status": "open", "opened_at": "2026-10-14",
                       "closed_at": None, "closed_by": None, "owner": "L5队",
                       "lane": "L3", "close_criterion": "真实联调验证判据",
                       "note": "I-218 v2 隔离播种", "claimed_by": None,
                       "claimed_at": None, "lease_until": None, "evidence": [],
                       "criteria": None, "replies": [], "blockers": [],
                       "conclusion": None, "criterion_check": [], "depends_on": [],
                       "reviewer": "beth"})
    with open(os.path.join(REG, "items.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "items": seeded}, f, ensure_ascii=False, indent=1)


def seed_client_paths():
    iso_lp = os.path.join(OUT, "lp_sync")
    os.makedirs(iso_lp, exist_ok=True)
    for name in ("cloud_secret.json", "agent_token"):
        shutil.copyfile(os.path.join(LP_SYNC, name), os.path.join(iso_lp, name))


def active_tasks():
    st = jload(os.path.join(KS, "state.json")) or {"tasks": {}}
    out = {}
    for rid, t in st["tasks"].items():
        if t.get("phase") in ("dispatched", "result_pending") and t.get("session_id"):
            out[rid] = t
    return st, out


def session_metrics(sids):
    """会话库只读：真实令牌数/最近活动/在干活窗口（console_collector 同口径）。"""
    out = {}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % SESSION_DB.replace("\\", "/"),
                              uri=True)
        for sid in sids:
            mu = con.execute(
                "select input_tokens, started_at from model_usage "
                "where session_id=? and status='completed' "
                "order by started_at desc limit 1", (sid,)).fetchone()
            row = con.execute("select time_updated from session where id=?",
                              (sid,)).fetchone()
            out[sid] = {"tokens": mu[0] if mu else None,
                        "mu_started": mu[1] if mu else None,
                        "updated": row[0] if row else None}
        con.close()
    except Exception:
        pass
    return out


def collector_mimic():
    """每五秒：内核在册活跃任务的真实会话行上行 /api/console/ingest。"""
    while not _stop.is_set():
        try:
            _, act = active_tasks()
            if act:
                sids = {t["session_id"] for t in act.values()}
                met = session_metrics(sids)
                now = datetime.datetime.now().isoformat(timespec="seconds")
                rows = []
                for rid, t in act.items():
                    sid = t["session_id"]
                    m = met.get(sid) or {}
                    last_ms = max(m.get("mu_started") or 0, m.get("updated") or 0)
                    last_iso = (datetime.datetime.fromtimestamp(last_ms / 1000)
                                .isoformat(timespec="seconds")) if last_ms else None
                    age_ms = time.time() * 1000 - last_ms if last_ms else None
                    status = "在干活" if age_ms is not None and age_ms <= 90_000 else "空闲"
                    pct = (round(m["tokens"] / 1_000_000 * 100, 1)
                           if m.get("tokens") is not None else None)
                    excerpt = ("input_tokens=%s status=completed started_at=%s"
                               % (m.get("tokens"),
                                  datetime.datetime.fromtimestamp(
                                      m["mu_started"] / 1000).isoformat(timespec="seconds")
                                  if m.get("mu_started") else "无请求"))
                    rows.append({
                        "session_id": sid, "lane": t.get("lane") or "L3",
                        "item_id": t.get("item_id"), "status": status,
                        "context_pct": pct, "last_activity_at": last_iso,
                        "archived": False, "archive_reason": None,
                        "source": {"kind": "表",
                                   "locus": "model_usage/%s#latest_completed" % sid,
                                   "excerpt": excerpt[:200], "sampled_at": now}})
                if rows:
                    api("POST", "/api/console/ingest", {"sessions": rows})
        except Exception as e:
            with open(os.path.join(OUT, "collector_mimic.log"), "a",
                      encoding="utf-8") as f:
                f.write("%s %s\n" % (datetime.datetime.now().isoformat(
                    timespec="seconds"), repr(e)[:200]))
        _stop.wait(5)


def sync_mimic():
    """每三秒：队列快照与线配置上行（生产 sync_agent 同路径）。"""
    while not _stop.is_set():
        try:
            with open(os.path.join(KS, "lane_queues.json"), encoding="utf-8") as f:
                queues = json.load(f)
            with open(os.path.join(REG, "lanes.json"), encoding="utf-8") as f:
                lanes_conf = json.load(f)
            api("POST", "/api/agent/heartbeat", {
                "info": {"agent_id": "i218-ui-sync-mimic"},
                "lane_queues": {"sampled_at": datetime.datetime.now().isoformat(
                    timespec="seconds"), "queues": queues},
                "lanes_conf": lanes_conf})
        except Exception as e:
            with open(os.path.join(OUT, "sync_mimic.log"), "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (datetime.datetime.now().isoformat(
                    timespec="seconds"), repr(e)[:200]))
        _stop.wait(3)


def stale_row_write(sid, item_id):
    """驻留语义演示：把该会话行 updated_at 直写为四十四分钟前（隔离库）。

    身份、事项、读数均为真实值；仅时间戳模拟“采集停止刷新超三十五分钟
    新鲜度门槛”的真实语义边界（api_console fresh_cut 路径）。"""
    con = sqlite3.connect(DB)
    old = (datetime.datetime.now()
           - datetime.timedelta(minutes=44)).isoformat(timespec="seconds")
    with con:
        con.execute(
            "INSERT OR REPLACE INTO console_sessions "
            "(session_id, lane, item_id, status, context_pct, last_activity_at,"
            " archived, archive_reason, source, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, "L3", item_id, "空闲", None, old, 0, None,
             json.dumps({"kind": "表", "locus": "model_usage/%s#latest_completed" % sid,
                         "excerpt": "驻留语义演示行（真实会话，超时时间戳）",
                         "sampled_at": old}, ensure_ascii=False), old))
    con.close()


def session_window(sid):
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


def intervals_overlap(a, b):
    return a and b and max(a[0], b[0]) < min(a[1], b[1])


def run_page_check(phase, tasks_extra):
    token = open(os.path.join(OUT, "lp_sync", "agent_token"),
                 encoding="utf-8").read().strip()
    inp = {"proxy_port": PROXY_PORT, "cloud_port": CLOUD_PORT, "token": token,
           "ks_dir": KS, "out_dir": OUT, "tasks": tasks_extra}
    inp_path = os.path.join(OUT, "page_check_input_phase%s.json" % phase)
    with open(inp_path, "w", encoding="utf-8") as f:
        json.dump(inp, f, ensure_ascii=False, indent=1)
    r = subprocess.run(["node", os.path.join(HERE, "i218v2_real_page_check.cjs"),
                        inp_path, phase],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=BOARD_UI, timeout=420)
    print(r.stdout[-3000:], flush=True)
    if r.stderr:
        print(r.stderr[-1500:], flush=True)
    res = jload(os.path.join(OUT, "page_check_result_phase%s.json" % phase)) or {}
    for c in res.get("checks", []):
        _checks.append(c)
    return res


def main():
    os.makedirs(OUT, exist_ok=True)
    for d in (os.path.join(KS, "inbox"), QUEUE, HOST_RT,
              os.path.dirname(ROSTER), *DIRS.values()):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(KS, "lane_workers.json"), "w", encoding="utf-8") as f:
        json.dump({"default": 1, "lanes": {"L3": 2}}, f)
    seed_cloud()
    seed_registry_copy()
    seed_client_paths()

    cloud_log = open(os.path.join(OUT, "cloud_server.log"), "a", encoding="utf-8")
    _procs["cloud"] = subprocess.Popen(
        [PY, os.path.join(CLOUD, "server_main.py")], env=cloud_env(),
        cwd=CLOUD, stdout=cloud_log, stderr=subprocess.STDOUT)
    ready = wait_for(lambda: "items" in api("GET", "/api/agent/snapshot"),
                     timeout_s=30, step=0.5)
    check("R0a 隔离云就绪", ready, "port=%d" % CLOUD_PORT)

    host_log = open(os.path.join(OUT, "iso_host.log"), "a", encoding="utf-8")
    _procs["host"] = subprocess.Popen(
        [PY, os.path.join(LP_SYNC, "host_service.py")],
        env=cloud_env({"LP_HOST_ISOLATED": "1", "LP_HOST_PORT": str(HOST_PORT),
                       "LP_HOST_STATE_ROOT": HOST_RT,
                       "LP_HOST_ROSTER_PATH": os.path.join(OUT, "iso_roster.json"),
                       "LP_LANE_WORKERS_ROSTER": ROSTER,
                       "LP_HOST_STATUS": os.path.join(OUT, "iso_status.json"),
                       "LP_DISPATCH_QUEUE_ROOT": QUEUE,
                       "FLAPPER_LEDGER_MODE": "cloud",
                       "LP_ITEMS_REGISTRY": REG}),
        cwd=ROOT, stdout=host_log, stderr=subprocess.STDOUT)
    _procs["kernel"] = subprocess.Popen(
        [PY, os.path.join(LP_SYNC, "orchestrator_kernel.py"),
         "--state-root", KS, "--queue-root", QUEUE, "--wake-s", "1.0",
         "--port", str(HOST_PORT + 500), "--line-retry-s", "3"],
        env=cloud_env({"LP_ITEMS_REGISTRY": REG, "FLAPPER_LEDGER_MODE": "cloud",
                       "LP_LANE_WORKERS_ROSTER": ROSTER}),
        cwd=ROOT, stdout=open(os.path.join(OUT, "kernel.log"), "a",
                              encoding="utf-8"), stderr=subprocess.STDOUT)
    boot = wait_for(lambda: any(
        e.get("event") == "kernel_started"
        for e in (jload(os.path.join(KS, "ledger.json")) or
                  {"events": []})["events"]), timeout_s=20, step=0.5)
    check("R0b 隔离内核就绪", boot)
    _procs["proxy"] = subprocess.Popen(
        ["node", os.path.join(HERE, "i218v2_static_proxy.cjs"),
         str(CLOUD_PORT), str(PROXY_PORT)],
        cwd=BOARD_UI, stdout=open(os.path.join(OUT, "proxy.log"), "a",
                                  encoding="utf-8"), stderr=subprocess.STDOUT)
    import socket as _sock
    proxy_ok = wait_for(lambda: (_sock.create_connection(
        ("127.0.0.1", PROXY_PORT), timeout=2) and True) or False, timeout_s=15, step=0.5)
    check("R0c 静态代理就绪", proxy_ok, "port=%d dist=%s" % (PROXY_PORT, os.path.join(BOARD_UI, "dist")))
    if not (ready and boot and proxy_ok):
        return 1
    for fn in (collector_mimic, sync_mimic):
        th = threading.Thread(target=fn, daemon=True)
        th.start()

    # —— 提交 A/B/C（A/B 双工位真实并行，C 排队） ——
    for tag in "ABC":
        write_task_submit(tag)
    ok_ab = wait_for(lambda: all(
        (jload(os.path.join(KS, "state.json")) or {})
        .get("tasks", {}).get(REQS[t], {}).get("phase") in
        ("dispatched", "result_pending") for t in "AB"), timeout_s=240)
    st = jload(os.path.join(KS, "state.json")) or {"tasks": {}}
    ta, tb, tc = (st["tasks"][REQS[t]] for t in "ABC")
    check("R1a A/B 并行在办于不同工位", ok_ab
          and {ta.get("worker_id"), tb.get("worker_id")} == {"L3-w1", "L3-w2"},
          "A=%s B=%s" % (ta.get("worker_id"), tb.get("worker_id")))
    check("R1b 四不同（会话/工位/运行）",
          ta.get("session_id") and tb.get("session_id")
          and ta["session_id"] != tb["session_id"]
          and ta.get("run_id") != tb.get("run_id")
          and ta.get("worker_id") != tb.get("worker_id"), "")
    check("R1c C 排队未派",
          tc.get("phase") in ("ready", "line_wait")
          and not os.path.exists(os.path.join(DIRS["C"], "start.txt")),
          "C phase=%s" % tc.get("phase"))

    # —— 阶段一：页面真实双卡与操作核对（A/B 执行中） ——
    def _both_rows_up():
        r = api("GET", "/api/console/state")
        if not isinstance(r, dict) or "__error__" in r:
            return False
        sids = {s.get("session_id") for s in r.get("sessions", {}).get("active", [])}
        return ta.get("session_id") in sids and tb.get("session_id") in sids
    rows_up = wait_for(_both_rows_up, timeout_s=180, step=3)
    check("R1d 双会话行经采集 mimic 真实上行（console_sessions 在册）", rows_up,
          "A=%s B=%s" % (ta.get("session_id"), tb.get("session_id")))
    res1 = run_page_check("1", {
        "A": {"sid": ta.get("session_id"), "worker": ta.get("worker_id"),
              "item": ta.get("item_id")},
        "B": {"sid": tb.get("session_id"), "worker": tb.get("worker_id"),
              "item": tb.get("item_id")},
        "C": {"sid": None, "worker": None, "item": tc.get("item_id")}})

    ok_files = wait_for(lambda: all(os.path.exists(
        os.path.join(DIRS[t], f)) for t in "AB" for f in ("start.txt", "end.txt")),
        timeout_s=360, step=3)
    check("R2a 两工位真实执行落盘（start/end）", ok_files)
    try:
        wa = (os.stat(os.path.join(DIRS["A"], "start.txt")).st_mtime,
              os.stat(os.path.join(DIRS["A"], "end.txt")).st_mtime)
        wb = (os.stat(os.path.join(DIRS["B"], "start.txt")).st_mtime,
              os.stat(os.path.join(DIRS["B"], "end.txt")).st_mtime)
    except OSError:
        wa = wb = None
    sa = session_window(ta.get("session_id"))
    sb = session_window(tb.get("session_id"))
    EVIDENCE["work_windows"] = {"A": wa, "B": wb}
    EVIDENCE["session_windows"] = {"A": sa, "B": sb}
    check("R2b 工作文件执行区间相交（真实重叠）", intervals_overlap(wa, wb))
    check("R2c 会话消息区间相交（模型执行重叠）", intervals_overlap(sa, sb))

    ok_c = wait_for(lambda: (jload(os.path.join(KS, "state.json"))
                             or {"tasks": {}})["tasks"]
                    .get(REQS["C"], {}).get("phase") in
                    ("dispatched", "result_pending", "completed"), timeout_s=120)
    check("R3 第三项自动接续派发", ok_c)
    ok_results = wait_for(lambda: all(
        (jload(os.path.join(KS, "state.json")) or {"tasks": {}})
        ["tasks"].get(REQS[t], {}).get("phase") == "completed" for t in "ABC"),
        timeout_s=360, step=3)
    check("R4a 三项正式结果回收（内核处理完成）", ok_results)
    snap = api("GET", "/api/agent/snapshot")
    items = {i["id"]: i for i in (snap.get("items") or [])}
    check("R4b 隔离云回收：三项 pending_review",
          all(items.get(ITEMS[t], {}).get("status") == "pending_review"
              for t in "ABC"),
          str({t: items.get(ITEMS[t], {}).get("status") for t in "ABC"}))

    # —— 阶段二：D 活跃 + 另一工位驻留灰卡并存 ——
    write_task_submit("D")
    ok_d = wait_for(lambda: (jload(os.path.join(KS, "state.json"))
                             or {"tasks": {}})["tasks"]
                    .get(REQS["D"], {}).get("phase") in
                    ("dispatched", "result_pending"), timeout_s=120)
    st = jload(os.path.join(KS, "state.json")) or {"tasks": {}}
    td = st["tasks"].get(REQS["D"], {})
    dw = td.get("worker_id")
    last_by_worker = {}
    for t in "ABC":
        w = st["tasks"][REQS[t]].get("worker_id")
        last_by_worker[w] = st["tasks"][REQS[t]].get("session_id")
    other_worker = next(w for w in ("L3-w1", "L3-w2") if w != dw)
    stale_sid = last_by_worker.get(other_worker)
    check("R5a D 派发在办", ok_d and dw in ("L3-w1", "L3-w2") and stale_sid,
          "D=%s stale=%s(%s)" % (dw, stale_sid, other_worker))
    if stale_sid:
        stale_item = next(st["tasks"][REQS[t]].get("item_id") for t in "ABC"
                          if st["tasks"][REQS[t]].get("session_id") == stale_sid)
        stale_row_write(stale_sid, stale_item)
        check("R5b 驻留行直写隔离库（模拟超时驻留语义，真实身份与事项）",
              True, "%s -> %s" % (stale_sid, stale_item))
        time.sleep(6)                     # 等采集 mimic 推送 D 会话行后核对页面
        res2 = run_page_check("2", {
            "D": {"sid": td.get("session_id"), "worker": dw,
                  "item": td.get("item_id")},
            "stale": {"sid": stale_sid, "item": stale_item}})
        EVIDENCE["phase2"] = {"D": {"sid": td.get("session_id"), "worker": dw},
                              "stale": {"sid": stale_sid, "worker": other_worker},
                              "result": res2.get("summary")}
    ok_d_done = wait_for(lambda: (jload(os.path.join(KS, "state.json"))
                                  or {"tasks": {}})["tasks"]
                         .get(REQS["D"], {}).get("phase") == "completed",
                         timeout_s=180)
    check("R6 D 结果回收", ok_d_done)

    EVIDENCE["sessions"] = {t: st["tasks"][REQS[t]].get("session_id")
                             for t in "ABCD"}
    EVIDENCE["workers"] = {t: st["tasks"][REQS[t]].get("worker_id")
                            for t in "ABCD"}
    EVIDENCE["runs"] = {t: st["tasks"][REQS[t]].get("run_id") for t in "ABCD"}
    EVIDENCE["page_check_phase1"] = res1.get("summary")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        _stop.set()
        # 进程回收只针对本探针自建树：代理 → 内核 → 宿主树 → 隔离云。
        for key in ("proxy", "kernel", "host", "cloud"):
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
        EVIDENCE["finished_at"] = datetime.datetime.now().isoformat(
            timespec="seconds")
        EVIDENCE["summary"] = {"total": len(_checks),
                               "passed": sum(1 for c in _checks if c["ok"])}
        with open(os.path.join(OUT, "evidence.json"), "w", encoding="utf-8") as f:
            json.dump(EVIDENCE, f, ensure_ascii=False, indent=1, default=str)
        print("==== %d/%d，证据 %s ====" % (EVIDENCE["summary"]["passed"],
              EVIDENCE["summary"]["total"],
              os.path.join(OUT, "evidence.json")), flush=True)
    sys.exit(code)
