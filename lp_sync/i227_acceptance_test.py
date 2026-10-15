# -*- coding: utf-8 -*-
"""i227_acceptance_test.py — I-227 隔离验收：状态对账与历史索引样例检查。

全部样例在自建夹具目录、自建会话库与独立端口上运行，不接触生产
状态目录、生产队列、生产端口与生产进程；不停止任何既有进程。夹具
覆盖判据一五种状态样例、判据三与判据四的历史样例、判据五四种缺失
原因与只读检查。真实同刻对照与真实数据回归由 i227_real_evidence.py
另行取证。仅使用 Python 标准库。
"""
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LP_SYNC = os.path.join(ROOT, "lp_sync")
FIXTURE = os.path.join(ROOT, "logs", "hq_intake",
                       "beth_session_observability_20261014",
                       "acceptance_fixture")
RESULTS_PATH = os.path.join(ROOT, "logs", "hq_intake",
                            "beth_session_observability_20261014",
                            "acceptance_results.json")
SERVER_PORT = 58799

NOW = time.time()


def iso_ago(seconds):
    return datetime.datetime.fromtimestamp(NOW - seconds).isoformat(timespec="seconds")


def ms_ago(seconds):
    return int((NOW - seconds) * 1000)


CHECKS = []


def check(cid, desc, ok, evidence=None):
    CHECKS.append({"id": cid, "desc": desc, "pass": bool(ok),
                   "evidence": evidence})
    print(("PASS " if ok else "FAIL ") + cid + " " + desc)


# ── 夹具构建 ────────────────────────────────────────────────────

def make_db(path, include_del=True):
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.executescript("""
    create table session(id text primary key, title text, directory text,
                         time_created integer, time_updated integer);
    create table part(id integer primary key autoincrement,
                      session_id text, data text, time_created integer);
    create table model_usage(id integer primary key autoincrement,
                      session_id text, status text, started_at integer,
                      completed_at integer, input_tokens integer);
    create table tool_usage(id integer primary key autoincrement,
                      session_id text, tool_name text, status text,
                      started_at integer, completed_at integer,
                      exit_code integer);
    """)
    ws = "G:\\Playground"

    def sess(sid, updated_s_ago, title="夹具会话"):
        con.execute("insert into session values(?,?,?,?,?)",
                    (sid, title, ws, ms_ago(updated_s_ago + 3600),
                     ms_ago(updated_s_ago)))

    def tool(sid, tool, status, started_ago, completed_ago=None):
        data = json.dumps({"type": "tool", "tool": tool,
                           "state": {"status": status}},
                          separators=(",", ":"))
        con.execute("insert into part(session_id,data,time_created) "
                    "values(?,?,?)", (sid, data, ms_ago(started_ago)))
        con.execute("insert into tool_usage(session_id,tool_name,status,"
                    "started_at,completed_at,exit_code) values(?,?,?,?,?,?)",
                    (sid, tool, "completed" if status == "completed"
                     else status, ms_ago(started_ago),
                     ms_ago(completed_ago) if completed_ago else None, 0))

    def mu(sid, status, started_ago, completed_ago=None, tokens=400000):
        con.execute("insert into model_usage(session_id,status,started_at,"
                    "completed_at,input_tokens) values(?,?,?,?,?)",
                    (sid, status, ms_ago(started_ago),
                     ms_ago(completed_ago) if completed_ago else None,
                     tokens))
    # 样例一：长工具运行中，已 120 秒，模型请求 200 秒前完成
    sess("sess_fx_longtool", 120)
    tool("sess_fx_longtool", "Bash", "running", 120)
    mu("sess_fx_longtool", "completed", 260, 200)
    # 样例二：等待模型，最近模型请求 10 秒前完成
    sess("sess_fx_work", 10)
    tool("sess_fx_work", "Read", "completed", 15, 14)
    mu("sess_fx_work", "completed", 30, 10)
    # 样例三：已交付空闲，最近活动 300 秒前
    sess("sess_fx_delivered", 300)
    tool("sess_fx_delivered", "Bash", "completed", 310, 300)
    mu("sess_fx_delivered", "completed", 330, 310)
    # 样例四甲：有停止确认，20 分钟前停止
    sess("sess_fx_stop", 1200)
    tool("sess_fx_stop", "Bash", "completed", 1250, 1200)
    mu("sess_fx_stop", "completed", 1300, 1200)
    # 样例四乙：无停止确认的静默会话，10 分钟无活动
    sess("sess_fx_nostop", 600)
    tool("sess_fx_nostop", "Bash", "completed", 650, 600)
    mu("sess_fx_nostop", "completed", 700, 600)
    # 交接链与新会话
    sess("sess_fx_chain_old", 10800)
    tool("sess_fx_chain_old", "TodoWrite", "completed", 10800, 10800)
    mu("sess_fx_chain_old", "completed", 11000, 10800)
    sess("sess_fx_chain_new", 45)
    tool("sess_fx_chain_new", "Read", "completed", 60, 50)
    mu("sess_fx_chain_new", "completed", 80, 60)
    # 门槛样例：29、31、34、36 分钟前最后活动
    for mark, age_s in (("29m", 1740), ("31m", 1860), ("34m", 2040),
                        ("36m", 2160)):
        sid = "sess_fx_idle_" + mark
        sess(sid, age_s)
        tool(sid, "Bash", "completed", age_s + 10, age_s)
        mu(sid, "completed", age_s + 20, age_s)
    # 分页填充会话
    for i in range(56):
        sid = "sess_fx_page_%02d" % i
        sess(sid, 600 + i * 30)
        mu(sid, "completed", 700 + i * 30, 650 + i * 30)
    # 删除样例会话
    if include_del:
        sess("sess_fx_del", 600)
        mu("sess_fx_del", "completed", 700, 650)
    # 外工作区会话：不进入索引
    con.execute("insert into session values(?,?,?,?,?)",
                ("sess_fx_foreign", "外部会话", "C:\\Elsewhere",
                 ms_ago(7200), ms_ago(60)))
    con.commit()
    con.close()


def build_fixture():
    if os.path.isdir(FIXTURE):
        shutil.rmtree(FIXTURE)
    os.makedirs(os.path.join(FIXTURE, "handoff_events"))
    os.makedirs(os.path.join(FIXTURE, "history"))
    make_db(os.path.join(FIXTURE, "db.sqlite"))
    card = os.path.join(FIXTURE, "handoff_card_fx.md")
    with open(card, "w", encoding="utf-8") as f:
        f.write("# 夹具交接卡：上下文 70% 触发自动切换\n旧会话移交全部在办事项。\n")
    state = {"runs": {
        "run_fx_long": {"run_id": "run_fx_long", "task_req_id": "fx_t4",
                        "item_id": "I-901", "session_id": "sess_fx_longtool",
                        "started_at": iso_ago(400), "status": "active",
                        "worker_id": "L4-w1"},
        "run_fx_work": {"run_id": "run_fx_work", "task_req_id": "fx_t1",
                        "item_id": "I-903", "session_id": "sess_fx_work",
                        "started_at": iso_ago(400), "status": "active",
                        "worker_id": "L3-w1"},
        "run_fx_chain": {"run_id": "run_fx_chain", "task_req_id": "fx_t2",
                         "item_id": "I-902", "session_id": "sess_fx_chain_old",
                         "started_at": iso_ago(11000), "status": "ended",
                         "worker_id": "L2-w1"}},
        "tasks": {"fx_t1": {"lane": "L3"}, "fx_t2": {"lane": "L2"},
                  "fx_t4": {"lane": "L4"}}}
    roster = {"workers": {
        "L3-w1": {"lane": "L3", "session_id": "sess_fx_work",
                  "phase": "active", "updated_at": iso_ago(30),
                  "item_id": "I-903", "run_id": "run_fx_work"},
        "L3-w2": {"lane": "L3", "session_id": "sess_fx_delivered",
                  "phase": "idle", "updated_at": iso_ago(300)},
        "L2-w1": {"lane": "L2", "session_id": "sess_fx_chain_new",
                  "phase": "active", "updated_at": iso_ago(45),
                  "item_id": "I-902", "run_id": "run_fx_chain"},
        "L4-w1": {"lane": "L4", "session_id": "sess_fx_longtool",
                  "phase": "active", "updated_at": iso_ago(130),
                  "item_id": "I-901", "run_id": "run_fx_long"}}}
    execution = {"sessions": {
        "sess_fx_work": {"phase": "active", "updated_at": iso_ago(400)},
        "sess_fx_stop": {"phase": "stopped", "updated_at": iso_ago(600)},
        "sess_fx_nostop": {"phase": "active", "updated_at": iso_ago(600)},
        "sess_fx_chain_old": {"phase": "retired",
                              "updated_at": iso_ago(120)}}}
    registry = {
        "sess_fx_longtool": {"item_id": "I-901", "lane": "L4",
                             "title_head": "夹具长工具", "origin": "dispatch",
                             "dispatched_at": iso_ago(500)},
        "sess_fx_chain_old": {"item_id": "I-902", "lane": "L2",
                              "title_head": "夹具旧会话", "origin": "dispatch",
                              "dispatched_at": iso_ago(11200)},
        "sess_fx_chain_new": {"item_id": "I-902", "lane": "L2",
                              "title_head": "自动接续夹具", "origin": "dispatch",
                              "dispatched_at": iso_ago(120)},
        "sess_fx_noclaim_dbmiss": {"item_id": "I-904", "lane": "L3",
                                   "title_head": "无对话源夹具",
                                   "origin": "dispatch",
                                   "dispatched_at": iso_ago(900)},
        "sess_fx_delivered": {"item_id": None, "lane": "L3",
                              "title_head": "已交付夹具", "origin": "dispatch",
                              "dispatched_at": iso_ago(1000)}}
    handoff_state = {"archived": [{
        "session_id": "sess_fx_chain_old",
        "successor": "sess_fx_chain_new",
        "archived_at": iso_ago(120),
        "card": card,
        "archive_reason": "上下文 70% 触发自动切换，新会话 sess_fx_chain_new"}]}
    with open(os.path.join(FIXTURE, "handoff_events", "ev-fx.json"), "w",
              encoding="utf-8") as f:
        json.dump({"ok": True, "switched": True, "old": "sess_fx_chain_old",
                   "new": "sess_fx_chain_new", "lane": "L2", "item_id": "I-902",
                   "run_id": "run_fx_chain",
                   "kernel_reconciled": iso_ago(119)}, f, ensure_ascii=False)
    timeline = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in (
        {"ts": iso_ago(180), "ingested": 5, "sessions": 5, "error": None},
        {"ts": iso_ago(120), "ingested": 5, "sessions": 5, "error": None},
        {"ts": iso_ago(60), "ingested": 5, "sessions": 5, "error": None},
        {"ts": iso_ago(30), "ingested": None, "sessions": None,
         "error": "CloudUnreachable: 模拟上行失败"}))
    items = {"items": [
        {"id": "I-901", "claimed_at": iso_ago(500)},
        {"id": "I-902", "claimed_at": iso_ago(11200)},
        {"id": "I-904", "claimed_at": iso_ago(900)}]}
    dumps = {"state.json": state, "roster.json": roster,
             "execution.json": execution, "registry.json": registry,
             "handoff_state.json": handoff_state, "items.json": items}
    for name, obj in dumps.items():
        with open(os.path.join(FIXTURE, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    with open(os.path.join(FIXTURE, "timeline.jsonl"), "w",
              encoding="utf-8") as f:
        f.write(timeline)


def set_env():
    env = {
        "LP_SESSION_DB": os.path.join(FIXTURE, "db.sqlite"),
        "LP_KERNEL_STATE_ROOT": FIXTURE,
        "LP_EXECUTION_STORE": os.path.join(FIXTURE, "execution.json"),
        "LP_LANE_WORKERS_ROSTER": os.path.join(FIXTURE, "roster.json"),
        "LP_REGISTRY_PATH": os.path.join(FIXTURE, "registry.json"),
        "LP_HANDOFF_STATE_PATH": os.path.join(FIXTURE, "handoff_state.json"),
        "LP_HANDOFF_EVENTS_DIR": os.path.join(FIXTURE, "handoff_events"),
        "LP_COLLECTOR_TIMELINE": os.path.join(FIXTURE, "timeline.jsonl"),
        "LP_ITEMS_REGISTRY": os.path.join(FIXTURE, "items.json"),
        "LP_SESSION_HISTORY_DIR": os.path.join(FIXTURE, "history"),
    }
    os.environ.update(env)
    sys.path.insert(0, LP_SYNC)


# ── 判据一与判据二：状态对账样例 ───────────────────────────────

def state_checks():
    import session_truth
    rows, meta = session_truth.gather()
    by_sid = {r["session_id"]: r for r in rows}

    r = by_sid.get("sess_fx_longtool")
    check("S1-longtool", "长工具 120 秒仍判在干活且标注长工具",
          r is not None and r["status"].startswith("在干活")
          and "长工具" in r["status"],
          {"status": r and r["status"],
           "legacy": session_truth.legacy_status(r) if r else None})
    check("S1-longtool-legacy", "旧口径同数据判空闲（反例复现）",
          r is not None and session_truth.legacy_status(r) == "空闲",
          {"legacy": session_truth.legacy_status(r) if r else None})

    r = by_sid.get("sess_fx_work")
    check("S2-waitmodel", "等待模型样例判在干活并注明等待模型响应",
          r is not None and r["status"].startswith("在干活")
          and "等待模型" in r["status"], {"status": r and r["status"]})

    r = by_sid.get("sess_fx_delivered")
    check("S3-delivered", "已交付样例判空闲并注明已交付与工位号",
          r is not None and r["status"].startswith("空闲（已交付")
          and "L3-w2" in r["status"], {"status": r and r["status"]})

    r = by_sid.get("sess_fx_stop")
    check("S4a-stop-confirmed", "有停止确认样例判已停止并注明确认来源",
          r is not None and "已停止" in r["status"]
          and "停止确认" in r["status"], {"status": r and r["status"]})

    r = by_sid.get("sess_fx_nostop")
    check("S4b-no-fake-stop", "无停止确认的静默会话不判已停止",
          r is not None and "已停止" not in r["status"]
          and r["status"].startswith("空闲"),
          {"status": r and r["status"]})

    # 采集失联：子进程换不可读会话库路径，工位与运行在册会话判未知
    code = ("import json,sys;sys.path.insert(0,%r);import session_truth;"
            "rows,meta=session_truth.gather();"
            "print(json.dumps({'rows':[{k:r[k] for k in ('session_id',"
            "'status','missing_reason')} for r in rows],"
            "'collection_error':meta.get('collection_error')},"
            "ensure_ascii=False))" % LP_SYNC)
    env = dict(os.environ)
    env["LP_SESSION_DB"] = os.path.join(FIXTURE, "missing.sqlite")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, cwd=LP_SYNC, timeout=60)
    try:
        payload = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        payload = {"rows": [], "collection_error": out.stderr[-200:]}
    lost = [r for r in payload["rows"] if "采集失联" in (r.get("status") or "")]
    check("S5-collector-loss", "会话库不可读时在册会话判未知并附采集失联原因",
          bool(lost) and bool(payload.get("collection_error")),
          {"rows": payload["rows"], "collection_error":
           payload.get("collection_error")})

    # 判据二：字段完整性与同线双工位对账
    required = ("session_id", "worker_id", "item_id", "run_id", "status",
                "last_tool", "missing_reason")
    bad = []
    for r in rows:
        for k in required:
            if k not in r:
                bad.append((r.get("session_id"), k))
        src = r.get("source") or {}
        if not (src.get("sampled_at") and src.get("locus")
                and src.get("excerpt") is not None
                and len(str(src.get("excerpt"))) <= 200):
            bad.append((r.get("session_id"), "source"))
    check("F1-fields", "全部状态行携带会话号、工位号、事项号、运行号、"
          "最近工具活动、采样时刻、来源与缺失原因",
          not bad, {"missing": bad})

    w1 = by_sid.get("sess_fx_work")
    w2 = by_sid.get("sess_fx_delivered")
    check("F2-dual-worker", "同线两会话工位、事项、运行归属逐项一致",
          w1 and w1["worker_id"] == "L3-w1" and w1["item_id"] == "I-903"
          and w1["run_id"] == "run_fx_work"
          and w2 and w2["worker_id"] == "L3-w2"
          and (w2["run_id"] is None),
          {"w1": w1 and {k: w1[k] for k in
                         ("worker_id", "item_id", "run_id", "status")},
           "w2": w2 and {k: w2[k] for k in
                         ("worker_id", "item_id", "run_id", "status")}})

    fake_stop = [r["session_id"] for r in rows
                 if r["status"].startswith("已停止")
                 and (r["source"].get("basis") or {}).get("execution_phase")
                 not in ("stopped",)]
    idle_running = [r["session_id"] for r in rows
                    if r["status"].startswith("空闲")
                    and (r.get("last_tool") or {}).get("status")
                    in ("pending", "running")]
    check("F3-zero-misjudge", "误判停止次数为 0 且在途工具不判空闲",
          not fake_stop and not idle_running,
          {"fake_stop": fake_stop, "idle_running": idle_running})

    # 三十分钟门槛外仍可核实：34 分钟静默会话仍在索引（判据二）
    return rows, meta


# ── 判据三、四、五：历史索引与查询 ─────────────────────────────

def history_checks():
    import session_history as sh
    index, report = sh.build_index()
    sessions = index["sessions"]

    reg = json.load(open(os.path.join(FIXTURE, "registry.json"),
                         encoding="utf-8"))
    missing_reg = [sid for sid in reg if sid not in sessions]
    check("H1-registry-merged", "登记册会话全部入索引且数量一致",
          not missing_reg
          and report["sources"]["dispatch_registry"]["records"] == len(reg),
          {"missing": missing_reg,
           "records": report["sources"]["dispatch_registry"]["records"]})

    run_sids = {r["session_id"] for r in
                json.load(open(os.path.join(FIXTURE, "state.json"),
                               encoding="utf-8"))["runs"].values()
                if r.get("session_id")}
    missing_runs = [sid for sid in run_sids if sid not in sessions]
    check("H2-runs-merged", "运行记录会话全部入索引",
          not missing_runs, {"missing": missing_runs})

    detail = sh.get_session("sess_fx_chain_old")
    check("H3-chain", "交接链双向可查且交接卡可读",
          detail["ok"] and detail["successor_chain"] == ["sess_fx_chain_new"]
          and sh.get_session("sess_fx_chain_new")["predecessor_chain"]
          == ["sess_fx_chain_old"]
          and detail["handover_cards"]
          and detail["handover_cards"][0]["card_excerpt"]
          and "70%" in detail["handover_cards"][0]["reason"],
          {"successor_chain": detail.get("successor_chain"),
           "predecessor_chain": sh.get_session(
               "sess_fx_chain_new").get("predecessor_chain"),
           "card_reason": detail["handover_cards"][0].get("reason")})

    def kind(sid):
        return (sessions.get(sid, {}).get("retention") or {}).get("kind")

    check("H4-archive-kind", "显式归档、空闲超时保留、无来源三分正确",
          kind("sess_fx_chain_old") == sh.ARCHIVE_EXPLICIT
          and kind("sess_fx_idle_36m") == sh.RETENTION_TIMEOUT
          and kind("sess_fx_noclaim_dbmiss") == sh.NO_SOURCE
          and kind("sess_fx_idle_29m") == sh.ACTIVE
          and kind("sess_fx_idle_34m") == sh.ACTIVE,
          {"chain_old": kind("sess_fx_chain_old"),
           "idle_36m": kind("sess_fx_idle_36m"),
           "idle_34m": kind("sess_fx_idle_34m"),
           "idle_29m": kind("sess_fx_idle_29m"),
           "noclaim": kind("sess_fx_noclaim_dbmiss")})

    lost_old = [sid for sid in ("sess_fx_idle_31m", "sess_fx_idle_34m",
                                "sess_fx_idle_36m") if sid not in sessions]
    check("H5-thresholds", "跨三十与三十五分钟门槛旧记录不丢失",
          not lost_old, {"lost": lost_old})

    pair = {sid for sid in ("sess_fx_chain_old", "sess_fx_chain_new")
            if sid in sessions}
    check("H6-handover-70pct", "70% 交接前后会话均在索引且差值为 0",
          len(pair) == 2, {"present": sorted(pair)})

    total_before = sh.list_sessions(page_size=200)["total"]
    ids_before = [i["session_id"] for i in
                  sh.list_sessions(page_size=200)["items"]]
    index2, report2 = sh.build_index()
    total_after = sh.list_sessions(page_size=200)["total"]
    ids_after = [i["session_id"] for i in
                 sh.list_sessions(page_size=200)["items"]]
    dup_ids = [sid for sid in set(ids_after) if ids_after.count(sid) > 1]
    check("H7-restart-rebuild", "重启重建后会话数与顺序稳定且重复数为 0",
          total_before == total_after and ids_before == ids_after
          and not dup_ids and len(ids_after) == len(set(ids_after)),
          {"before": total_before, "after": total_after,
           "duplicates": dup_ids})

    seen = []
    page = 1
    while True:
        res = sh.list_sessions(page=page, page_size=10)
        seen.extend(i["session_id"] for i in res["items"])
        if page * 10 >= res["total"]:
            break
        page += 1
    check("H8-pagination", "分页总数与集合一致且跨页无重复无遗漏",
          len(seen) == total_after and len(set(seen)) == len(seen),
          {"walked": len(seen), "unique": len(set(seen)),
           "total": total_after})

    r_lane = sh.list_sessions({"lane": "L2"})
    r_worker = sh.list_sessions({"worker_id": "L3-w1"})
    r_item = sh.list_sessions({"item_id": "I-902"})
    r_date = sh.list_sessions({"date_from": iso_ago(300)})
    check("H9-filters", "按线、工位、事项、日期筛选结果正确",
          all(i["lane"] == "L2" for i in r_lane["items"])
          and r_lane["total"] == 2
          and r_worker["total"] == 1
          and r_worker["items"][0]["session_id"] == "sess_fx_work"
          and r_item["total"] == 2
          and r_date["total"] > 0,
          {"lane": r_lane["total"], "worker": r_worker["total"],
           "item": r_item["total"], "date": r_date["total"]})

    check("H10-foreign-excluded", "外工作区与未登记会话不进入索引",
          "sess_fx_foreign" not in sessions
          and sh.get_session("sess_fx_unregistered")["ok"] is False,
          {"foreign_in_index": "sess_fx_foreign" in sessions})

    del_entry = sessions.get("sess_fx_del")
    check("H12-keep-prior", "首次构建包含删除样例会话",
          del_entry is not None, {"present": del_entry is not None})
    return sh, report


def deleted_case(sh):
    make_db(os.path.join(FIXTURE, "db.sqlite"), include_del=False)
    sh.build_index()
    entry = sh.load_index()["sessions"].get("sess_fx_del")
    check("H13-deleted", "来源消失的会话保留条目并标记已删除",
          entry is not None and "已删除" in (entry.get("missing") or {}).get(
              "deleted", ""),
          {"missing": (entry or {}).get("missing")})
    reasons = set()
    for e in sh.load_index()["sessions"].values():
        for v in (e.get("missing") or {}).values():
            reasons.add(v)
    need = ("会话库无该会话行", "无认领记录", "已删除", "上行失败")
    ok_reasons = all(any(n in r for r in reasons) for n in need)
    check("H11-missing-reasons", "四种缺失原因各自明确且不统一写暂无历史",
          ok_reasons and not any("暂无历史" in r for r in reasons),
          {"reasons": sorted(reasons)})


def server_checks(sh):
    env = dict(os.environ)
    env["LP_SESSION_HISTORY_PORT"] = str(SERVER_PORT)
    proc = subprocess.Popen([sys.executable, "session_history_server.py"],
                            cwd=LP_SYNC, env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    db_stat_before = os.stat(os.path.join(FIXTURE, "db.sqlite"))
    base = "http://127.0.0.1:%d" % SERVER_PORT
    try:
        deadline = time.time() + 20
        ready = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=2) as r:
                    ready = json.loads(r.read())["ok"]
                    break
            except Exception:
                time.sleep(0.4)
        check("R1-server-up", "只读查询服务在独立端口启动", ready, {})

        with urllib.request.urlopen(
                base + "/api/session_history/sessions?page=1&page_size=10",
                timeout=5) as r:
            listing = json.loads(r.read())["result"]
        check("R2-server-list", "服务端列表分页与本地一致",
              listing["total"] == sh.list_sessions(page_size=10)["total"],
              {"total": listing["total"]})

        with urllib.request.urlopen(
                base + "/api/session_history/sessions/sess_fx_chain_old",
                timeout=5) as r:
            detail = json.loads(r.read())["result"]
        check("R3-server-detail", "服务端单会话详情含交接链",
              detail["ok"] and detail["successor_chain"]
              == ["sess_fx_chain_new"],
              {"successor_chain": detail.get("successor_chain")})

        post = urllib.request.Request(base + "/api/session_history/sessions",
                                      data=b"{}", method="POST")
        post_code = None
        try:
            urllib.request.urlopen(post, timeout=5)
        except urllib.error.HTTPError as exc:
            post_code = exc.code
        check("R4-server-readonly", "服务拒绝写请求（POST 返回 405）",
              post_code == 405, {"post_status": post_code})

        db_stat_after = os.stat(os.path.join(FIXTURE, "db.sqlite"))
        check("R5-no-writes", "查询过程不写会话库",
              db_stat_before.st_size == db_stat_after.st_size
              and db_stat_before.st_mtime == db_stat_after.st_mtime,
              {"size": [db_stat_before.st_size, db_stat_after.st_size]})
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return


def main():
    build_fixture()
    set_env()
    state_checks()
    history_checks()
    import session_history as sh
    deleted_case(sh)
    server_checks(sh)
    passed = sum(1 for c in CHECKS if c["pass"])
    result = {"item_id": "I-227", "mode": "隔离夹具验收",
              "fixture_root": FIXTURE,
              "passed": passed, "total": len(CHECKS),
              "all_pass": passed == len(CHECKS), "checks": CHECKS}
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("结果写入 %s：%d/%d 通过" % (RESULTS_PATH, passed, len(CHECKS)))
    sys.exit(0 if result["all_pass"] else 1)


if __name__ == "__main__":
    main()
