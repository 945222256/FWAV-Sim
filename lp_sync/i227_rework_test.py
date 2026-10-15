# -*- coding: utf-8 -*-
"""i227_rework_test.py — I-227 返修隔离验收。

覆盖三部分：Beth 四反例按原调用方式复现、返修新增检查（停止冲突、
采样有效期、多在途工具、增量重建去重、历次认领、父目录核实、缺失
会话源）、原 28 项检查复跑。全部在自建夹具、独立端口（58798）与
自建进程上运行，不接触生产状态、队列、端口与进程；查询服务子进程
仅由本脚本创建并在结束时回收，进程号记录进结果。结果写入
logs/i227_rework_20261014/rework_acceptance_results.json。仅使用
Python 标准库。
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
OUT_DIR = os.path.join(ROOT, "logs", "i227_rework_20261014")
FIXTURE = os.path.join(OUT_DIR, "fixture")
RESULTS_PATH = os.path.join(OUT_DIR, "rework_acceptance_results.json")
SERVER_PORT = 58798

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


# ── 夹具构建（在 v1 夹具基础上扩充返修场景）────────────────────

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
    ws = "G:\\Playground\\isaac_flapper_sim"
    parent = "G:\\Playground"

    def sess(sid, updated_s_ago, directory=ws, title="夹具会话"):
        con.execute("insert into session values(?,?,?,?,?)",
                    (sid, title, directory, ms_ago(updated_s_ago + 3600),
                     ms_ago(updated_s_ago)))

    def part_row(sid, tool, status, started_ago):
        payload = json.dumps({"type": "tool", "tool": tool,
                              "state": {"status": status}},
                             separators=(",", ":"))
        con.execute("insert into part(session_id,data,time_created) "
                    "values(?,?,?)", (sid, payload, ms_ago(started_ago)))

    def tool(sid, tool_name, status, started_ago, completed_ago=None):
        payload = json.dumps({"type": "tool", "tool": tool_name,
                              "state": {"status": status}},
                             separators=(",", ":"))
        con.execute("insert into part(session_id,data,time_created) "
                    "values(?,?,?)", (sid, payload, ms_ago(started_ago)))
        con.execute("insert into tool_usage(session_id,tool_name,status,"
                    "started_at,completed_at,exit_code) values(?,?,?,?,?,?)",
                    (sid, tool_name,
                     "completed" if status == "completed" else status,
                     ms_ago(started_ago),
                     ms_ago(completed_ago) if completed_ago else None, 0))

    def mu(sid, status, started_ago, completed_ago=None, tokens=400000):
        con.execute("insert into model_usage(session_id,status,started_at,"
                    "completed_at,input_tokens) values(?,?,?,?,?)",
                    (sid, status, ms_ago(started_ago),
                     ms_ago(completed_ago) if completed_ago else None,
                     tokens))
    # 原样例一：长工具 120 秒在途
    sess("sess_fx_longtool", 120)
    part_row("sess_fx_longtool", "Bash", "running", 120)
    tool("sess_fx_longtool", "Bash", "running", 120)
    mu("sess_fx_longtool", "completed", 260, 200)
    # 原样例二：等待模型
    sess("sess_fx_work", 10)
    tool("sess_fx_work", "Read", "completed", 15, 14)
    mu("sess_fx_work", "completed", 30, 10)
    # 原样例三：已交付空闲
    sess("sess_fx_delivered", 300)
    tool("sess_fx_delivered", "Bash", "completed", 310, 300)
    mu("sess_fx_delivered", "completed", 330, 310)
    # 原样例四甲：停止确认
    sess("sess_fx_stop", 1200)
    tool("sess_fx_stop", "Bash", "completed", 1250, 1200)
    mu("sess_fx_stop", "completed", 1300, 1200)
    # 原样例四乙：静默不冒充停止
    sess("sess_fx_nostop", 600)
    tool("sess_fx_nostop", "Bash", "completed", 650, 600)
    mu("sess_fx_nostop", "completed", 700, 600)
    # 交接链
    sess("sess_fx_chain_old", 10800)
    tool("sess_fx_chain_old", "TodoWrite", "completed", 10800, 10800)
    mu("sess_fx_chain_old", "completed", 11000, 10800)
    sess("sess_fx_chain_new", 45)
    tool("sess_fx_chain_new", "Read", "completed", 60, 50)
    mu("sess_fx_chain_new", "completed", 80, 60)
    # 门槛样例
    for mark, age_s in (("29m", 1740), ("31m", 1860), ("34m", 2040),
                        ("36m", 2160)):
        sid = "sess_fx_idle_" + mark
        sess(sid, age_s)
        tool(sid, "Bash", "completed", age_s + 10, age_s)
        mu(sid, "completed", age_s + 20, age_s)
    # 分页填充
    for i in range(56):
        sid = "sess_fx_page_%02d" % i
        sess(sid, 600 + i * 30)
        mu(sid, "completed", 700 + i * 30, 650 + i * 30)
    if include_del:
        sess("sess_fx_del", 600)
        mu("sess_fx_del", "completed", 700, 650)
    # 外工作区会话
    con.execute("insert into session values(?,?,?,?,?)",
                ("sess_fx_foreign", "外部会话", "C:\\Elsewhere",
                 ms_ago(7200), ms_ago(60)))
    # 返修：父目录未经核实会话（不应纳入）与父目录已登记会话（应纳入）
    con.execute("insert into session values(?,?,?,?,?)",
                ("sess_fx_parent_unverified", "父目录未核实", parent,
                 ms_ago(7200), ms_ago(30)))
    con.execute("insert into session values(?,?,?,?,?)",
                ("sess_fx_parent_verified", "父目录已登记", parent,
                 ms_ago(7200), ms_ago(30)))
    # 返修补充场景（独立会话，避免干扰原样例）
    # 多在途工具：一个 150 秒、一个 30 秒，另有一个较新的已完成工具
    sess("sess_fx_multi_tool", 25)
    part_row("sess_fx_multi_tool", "Bash", "running", 150)
    part_row("sess_fx_multi_tool", "Bash", "running", 30)
    part_row("sess_fx_multi_tool", "Read", "completed", 25)
    mu("sess_fx_multi_tool", "completed", 200, 160)
    # 停止冲突：停止确认在案但工具仍在途
    sess("sess_fx_stop_conflict", 40)
    part_row("sess_fx_stop_conflict", "Bash", "running", 95)
    mu("sess_fx_stop_conflict", "completed", 150, 120)
    # 停止中待回执
    sess("sess_fx_stopping", 300)
    tool("sess_fx_stopping", "Bash", "completed", 350, 300)
    mu("sess_fx_stopping", "completed", 400, 300)
    # 采样过期：活跃运行在册但四千秒无活动
    sess("sess_fx_stale_run", 4000)
    tool("sess_fx_stale_run", "Bash", "completed", 4100, 4000)
    mu("sess_fx_stale_run", "completed", 4200, 4000)
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
                         "worker_id": "L2-w1"},
        "run_fx_stale": {"run_id": "run_fx_stale", "task_req_id": "fx_t5",
                         "item_id": "I-905", "session_id": "sess_fx_stale_run",
                         "started_at": iso_ago(4300), "status": "active",
                         "worker_id": "L3-w3"}},
        "tasks": {"fx_t1": {"lane": "L3"}, "fx_t2": {"lane": "L2"},
                  "fx_t4": {"lane": "L4"}, "fx_t5": {"lane": "L3"}}}
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
                  "item_id": "I-901", "run_id": "run_fx_long"},
        "L3-w3": {"lane": "L3", "session_id": "sess_fx_stale_run",
                  "phase": "active", "updated_at": iso_ago(4300),
                  "item_id": "I-905", "run_id": "run_fx_stale"}}}
    execution = {"sessions": {
        "sess_fx_work": {"phase": "active", "updated_at": iso_ago(400)},
        "sess_fx_stop": {"phase": "stopped", "updated_at": iso_ago(600)},
        "sess_fx_nostop": {"phase": "active", "updated_at": iso_ago(600)},
        "sess_fx_stopping": {"phase": "stopping", "updated_at": iso_ago(300)},
        "sess_fx_stop_conflict": {"phase": "stopped",
                                  "updated_at": iso_ago(200)},
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
                              "dispatched_at": iso_ago(1000)},
        "sess_fx_parent_verified": {"item_id": "I-906", "lane": "L3",
                                    "title_head": "父目录已登记夹具",
                                    "origin": "dispatch",
                                    "dispatched_at": iso_ago(200)}}
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
        {"id": "I-904", "claimed_at": iso_ago(900)},
        {"id": "I-906", "claimed_at": iso_ago(200)}]}
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
    os.environ.update({
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
    })
    sys.path.insert(0, LP_SYNC)


# ── 第一部分：Beth 四反例复现（按原调用方式）────────────────────

def beth_probe_checks():
    import session_truth as truth
    import sqlite3
    # 反例一：停止待回执不得标已停止（classify 直呼，facts 与 Beth 探针同构）
    stop_facts = {"session_row": ("private", ms_ago(120)), "model_usage": None,
                  "tool_part": None, "tool_usage": None, "db_error": None}
    stop = truth.classify("sess_probe_stopping", stop_facts, None, {},
                          {"phase": "stopping", "updated_at": iso_ago(10)},
                          {}, iso_ago(0))
    check("B1-stop-no-confirm", "Beth反例一：停止待回执显示停止中待核对，不标已停止",
          not stop["status"].startswith("已停止")
          and stop["status"].startswith("停止中")
          and "待核对" in stop["status"],
          {"status": stop["status"]})
    # 反例二：较早工具在途不被较新完成工具掩盖（原样例数据复刻）
    con = sqlite3.connect(os.path.join(FIXTURE, "db.sqlite"))
    sid = "sess_probe_parallel"
    con.execute("insert into session values(?,?,?,?,?)",
                (sid, "probe", "G:\\Playground\\isaac_flapper_sim",
                 ms_ago(3600), ms_ago(110)))
    for ident, status, age in ((1, "running", 150), (2, "completed", 110)):
        payload = json.dumps({"type": "tool", "tool": "Bash",
                              "state": {"status": status}},
                             separators=(",", ":"))
        con.execute("insert into part(session_id,data,time_created) "
                    "values(?,?,?)", (sid, payload, ms_ago(age)))
    con.execute("insert into model_usage(session_id,status,started_at,"
                "completed_at,input_tokens) values(?,?,?,?,?)",
                (sid, "completed", ms_ago(180), ms_ago(160), 700000))
    con.commit()
    facts = truth._db_session_facts(con, sid)
    parallel = truth.classify(sid, facts, None,
                              {"worker_id": "L3-w1", "lane": "L3",
                               "phase": "active"},
                              {"phase": "active"}, {"lane": "L3"}, iso_ago(0))
    con.execute("delete from session where id=?", (sid,))
    con.execute("delete from part where session_id=?", (sid,))
    con.execute("delete from model_usage where session_id=?", (sid,))
    con.commit()
    con.close()
    check("B2-older-tool-running", "Beth反例二：较早工具仍运行时不判空闲",
          parallel["status"].startswith("在干活")
          and parallel["in_flight_tools"]
          and parallel["last_tool"]["status"] == "running",
          {"status": parallel["status"],
           "in_flight": parallel["in_flight_tools"],
           "selected_latest": facts["tool_part"]})
    # 反例三与四：索引重建（Beth 原流程复刻；附一条活跃运行供 stale 检查）
    import session_history as history
    old = "sess_probe_history"
    successor = "sess_probe_successor"
    reg_path = os.path.join(FIXTURE, "registry.json")
    handoff_path = os.path.join(FIXTURE, "handoff_state.json")
    state_path = os.path.join(FIXTURE, "state.json")
    orig_reg = json.load(open(reg_path, encoding="utf-8"))
    orig_handoff = json.load(open(handoff_path, encoding="utf-8"))
    orig_state = json.load(open(state_path, encoding="utf-8"))
    state_full = json.loads(json.dumps(orig_state))
    state_full["runs"]["run_probe_history"] = {
        "run_id": "run_probe_history", "task_req_id": "fx_t1",
        "item_id": "I-900", "session_id": old,
        "started_at": iso_ago(100), "status": "active", "worker_id": "L3-w9"}
    reg_full = dict(orig_reg)
    reg_full[old] = {"lane": "L3", "item_id": "I-900",
                     "dispatched_at": iso_ago(100)}
    handoff_full = json.loads(json.dumps(orig_handoff))
    handoff_full["archived"] = orig_handoff["archived"] + [{
        "session_id": old, "successor": successor,
        "archive_reason": "probe 70% handoff", "archived_at": iso_ago(50)}]
    for path, obj in ((reg_path, reg_full), (handoff_path, handoff_full),
                      (state_path, state_full)):
        json.dump(obj, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    history.build_index()
    for path, obj in ((reg_path, orig_reg), (handoff_path, orig_handoff),
                      (state_path, orig_state)):
        json.dump(obj, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    history.build_index()
    entry = history.load_index()["sessions"][old]
    check("B3-sources-removed-full-retain",
          "Beth反例三：来源撤空后完整保留事项、交接链与归档来源并另记缺失",
          "I-900" in entry["items"] and entry["successor"] == successor
          and entry["handovers"] and entry["missing"].get("source_missing")
          and (entry.get("retention") or {}).get("kind") == "explicit_archive",
          {"items": sorted(entry["items"]), "successor": entry["successor"],
           "missing": entry["missing"],
           "retention": entry.get("retention")})
    reg_new = dict(orig_reg)
    reg_new[old] = {"lane": "L3", "item_id": "I-901",
                    "dispatched_at": iso_ago(0)}
    json.dump(reg_new, open(reg_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    history.build_index()
    entry = history.load_index()["sessions"][old]
    json.dump(orig_reg, open(reg_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    history.build_index()
    check("B4-reused-session-merge",
          "Beth反例四：同会话换新事项重建合并保留旧事项与交接记录",
          {"I-900", "I-901"}.issubset(set(entry["items"]))
          and entry["successor"] == successor,
          {"items": sorted(entry["items"]), "successor": entry["successor"]})


# ── 第二部分：返修新增检查 ──────────────────────────────────────

def rework_new_checks():
    import session_truth as truth
    import session_history as sh
    rows, meta = truth.gather()
    by_sid = {r["session_id"]: r for r in rows}

    r = by_sid.get("sess_fx_stopping")
    check("N1-stopping-state", "停止指令待回执显示停止中且注明待核对",
          r is not None and r["status"].startswith("停止中")
          and "待核对" in r["status"], {"status": r and r["status"]})

    r = by_sid.get("sess_fx_stop_conflict")
    check("N2-stop-conflict", "停止确认与在途工具冲突时实际活动优先并注明冲突",
          r is not None and r["status"].startswith("在干活")
          and "冲突" in r["status"] and r["missing_reason"],
          {"status": r and r["status"],
           "missing_reason": r and r["missing_reason"]})

    r = by_sid.get("sess_fx_stale_run")
    check("N3-sample-stale", "活跃运行在册但活动超三十分钟判未知待核对",
          r is not None and r["status"].startswith("未知")
          and "采样过期" in r["status"],
          {"status": r and r["status"]})

    r = by_sid.get("sess_fx_multi_tool")
    check("N4-multi-inflight", "多个在途工具全部读出并按最早在途标注长工具",
          r is not None and r["status"].startswith("在干活")
          and len(r["in_flight_tools"]) == 2
          and "长工具 150" in r["status"]
          and r["last_tool"]["in_flight_count"] == 2,
          {"status": r and r["status"],
           "in_flight": r and r["in_flight_tools"]})

    check("N5-parent-unverified-excluded",
          "父目录未经核实会话不进快照也不进索引，已登记父目录会话正常纳入",
          "sess_fx_parent_unverified" not in by_sid
          and by_sid.get("sess_fx_parent_verified") is not None,
          {"unverified_in_rows": "sess_fx_parent_unverified" in by_sid,
           "verified_row": (by_sid.get("sess_fx_parent_verified") or {})
           .get("status")})
    idx = sh.load_index()
    check("N6-parent-index", "索引同步排除父目录未核实会话",
          "sess_fx_parent_unverified" not in idx["sessions"]
          and "sess_fx_parent_verified" in idx["sessions"],
          {"unverified_in_index":
           "sess_fx_parent_unverified" in idx["sessions"]})

    detail = sh.get_session("sess_fx_noclaim_dbmiss")
    check("N7-dialog-missing-reason", "缺失会话源的对话摘要返回明确失败原因",
          detail["ok"] and detail["dialog_summary"]["ok"] is False
          and "会话库无该会话行" in detail["dialog_summary"]["reason"],
          {"dialog_summary": detail.get("dialog_summary")})

    # 增量重建去重与过时相位标记（交接记录在旧会话与新会话两侧各记
    # 一条属正常，去重口径为单会话内不重复）
    before = sh.load_index()
    sh.build_index()
    sh.build_index()
    after = sh.load_index()
    run_ids = [r["run_id"] for e in after["sessions"].values()
               for r in e.get("runs", [])]
    dup_in_session = 0
    for e in after["sessions"].values():
        keys = [("%s|%s|%s|%s" % (h.get("at"), h.get("from"), h.get("to"),
                                  h.get("source")))
                for h in e.get("handovers", [])]
        dup_in_session += len(keys) - len(set(keys))
    stale_probe = after["sessions"].get("sess_probe_history", {}).get("runs", [])
    check("N8-incremental-rebuild", "连续重建会话数稳定、运行零重复、单会话交接零重复",
          len(before["sessions"]) == len(after["sessions"])
          and len(run_ids) == len(set(run_ids))
          and dup_in_session == 0,
          {"before": len(before["sessions"]), "after": len(after["sessions"]),
           "runs": len(run_ids), "unique_runs": len(set(run_ids)),
           "dup_handovers_in_session": dup_in_session})

    # 历次认领对应：旧事项认领时刻随结转保留
    probe_items = (after["sessions"].get("sess_probe_history", {})
                   .get("items", {}))
    check("N9-claim-history", "来源撤空后历次认领时刻随事项槽保留",
          probe_items.get("I-900", {}).get("sources") == ["prior_index"]
          or "prior_index" in probe_items.get("I-900", {}).get("sources", []),
          {"probe_items": probe_items})

    check("N10-stale-run-flag", "结转条目中过时活跃运行标记 stale 不当当前事实",
          all(r.get("stale") for r in stale_probe
              if r.get("source") == "prior_index"
              and r.get("status") == "active") and
          any(r.get("source") == "prior_index" for r in stale_probe),
          {"runs": stale_probe})

    # 来源失联记录：损毁工位注册表后采集仍出行且记录来源错误
    roster_path = os.environ["LP_LANE_WORKERS_ROSTER"]
    orig = open(roster_path, "rb").read()
    open(roster_path, "wb").write(b"{corrupt")
    try:
        rows2, meta2 = truth.gather()
    finally:
        open(roster_path, "wb").write(orig)
    err_text = json.dumps(meta2.get("source_errors") or {},
                          ensure_ascii=False)
    check("N11-source-loss", "单来源失联不崩溃且来源错误留痕",
          bool(rows2) and ("roster" in err_text
                           or "lane_workers_roster" in err_text),
          {"source_errors": meta2.get("source_errors"),
           "rows": len(rows2)})


# ── 第三部分：原 28 项检查复跑 ──────────────────────────────────

def legacy_rerun_checks():
    import session_truth
    import session_history as sh
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
          {"rows": payload["rows"][:3],
           "collection_error": payload.get("collection_error")})
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
          "最近工具活动、采样时刻、来源与缺失原因", not bad, {"missing": bad})
    w1 = by_sid.get("sess_fx_work")
    w2 = by_sid.get("sess_fx_delivered")
    check("F2-dual-worker", "同线两会话工位、事项、运行归属逐项一致",
          w1 and w1["worker_id"] == "L3-w1" and w1["item_id"] == "I-903"
          and w1["run_id"] == "run_fx_work"
          and w2 and w2["worker_id"] == "L3-w2" and (w2["run_id"] is None),
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
                    and any(t.get("status") in ("pending", "running")
                            for t in (r.get("in_flight_tools") or []))]
    check("F3-zero-misjudge", "误判停止次数为 0 且在途工具不判空闲",
          not fake_stop and not idle_running,
          {"fake_stop": fake_stop, "idle_running": idle_running})

    # 历史部分（先重建一次干净索引）
    index, report = sh.build_index(keep_prior=False)
    sessions = index["sessions"]
    reg = json.load(open(os.path.join(FIXTURE, "registry.json"),
                         encoding="utf-8"))
    missing_reg = [sid for sid in reg if sid not in sessions]
    check("H1-registry-merged", "登记册会话全部入索引且数量一致",
          not missing_reg
          and report["sources"]["dispatch_registry"]["records"] == len(reg),
          {"missing": missing_reg})
    run_sids = {r["session_id"] for r in
                json.load(open(os.path.join(FIXTURE, "state.json"),
                               encoding="utf-8"))["runs"].values()
                if r.get("session_id")}
    check("H2-runs-merged", "运行记录会话全部入索引",
          not [sid for sid in run_sids if sid not in sessions], {})
    detail = sh.get_session("sess_fx_chain_old")
    check("H3-chain", "交接链双向可查且交接卡可读",
          detail["ok"] and detail["successor_chain"] == ["sess_fx_chain_new"]
          and sh.get_session("sess_fx_chain_new")["predecessor_chain"]
          == ["sess_fx_chain_old"]
          and detail["handover_cards"]
          and detail["handover_cards"][0]["card_excerpt"]
          and "70%" in detail["handover_cards"][0]["reason"],
          {"successor_chain": detail.get("successor_chain")})

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
    sh.build_index()
    total_after = sh.list_sessions(page_size=200)["total"]
    ids_after = [i["session_id"] for i in
                 sh.list_sessions(page_size=200)["items"]]
    dup_ids = [sid for sid in set(ids_after) if ids_after.count(sid) > 1]
    check("H7-restart-rebuild", "重启重建后会话数与顺序稳定且重复数为 0",
          total_before == total_after and ids_before == ids_after
          and not dup_ids,
          {"before": total_before, "after": total_after})
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
          {"walked": len(seen), "total": total_after})
    r_lane = sh.list_sessions({"lane": "L2"})
    r_worker = sh.list_sessions({"worker_id": "L3-w1"})
    r_item = sh.list_sessions({"item_id": "I-902"})
    r_date = sh.list_sessions({"date_from": iso_ago(300)})
    check("H9-filters", "按线、工位、事项、日期筛选结果正确",
          all(i["lane"] == "L2" for i in r_lane["items"])
          and r_lane["total"] == 2 and r_worker["total"] == 1
          and r_worker["items"][0]["session_id"] == "sess_fx_work"
          and r_item["total"] == 2 and r_date["total"] > 0,
          {"lane": r_lane["total"], "worker": r_worker["total"],
           "item": r_item["total"], "date": r_date["total"]})
    check("H10-foreign-excluded", "外工作区与未登记会话不进入索引",
          "sess_fx_foreign" not in sessions
          and sh.get_session("sess_fx_unregistered")["ok"] is False,
          {})
    del_entry = sessions.get("sess_fx_del")
    check("H12-keep-prior", "首次构建包含删除样例会话",
          del_entry is not None, {})
    reasons = set()
    for e in sessions.values():
        for v in (e.get("missing") or {}).values():
            reasons.add(v)
    check("H13-pre-delete-reasons", "构建初期缺失原因不含暂无历史",
          not any("暂无历史" in r for r in reasons), {"reasons": sorted(reasons)})


def deleted_case():
    import session_history as sh
    make_db(os.path.join(FIXTURE, "db.sqlite"), include_del=False)
    sh.build_index()
    entry = sh.load_index()["sessions"].get("sess_fx_del")
    check("H11-deleted-retain", "来源消失的会话完整保留条目并标记已删除",
          entry is not None and "已删除" in (entry.get("missing") or {}).get(
              "deleted", ""),
          {"missing": (entry or {}).get("missing")})
    reasons = set()
    for e in sh.load_index()["sessions"].values():
        for v in (e.get("missing") or {}).values():
            reasons.add(v)
    need = ("会话库无该会话行", "无认领记录", "已删除", "上行失败", "全部来源缺失")
    check("H14-missing-reasons", "五种缺失原因各自明确且不统一写暂无历史",
          all(any(n in r for r in reasons) for n in need)
          and not any("暂无历史" in r for r in reasons),
          {"reasons": sorted(reasons)})


def server_checks():
    import session_history as sh
    global LAST_SERVER_PROCESS
    env = dict(os.environ)
    env["LP_SESSION_HISTORY_PORT"] = str(SERVER_PORT)
    proc = subprocess.Popen([sys.executable, "session_history_server.py"],
                            cwd=LP_SYNC, env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    db_path = os.path.join(FIXTURE, "db.sqlite")
    db_stat_before = os.stat(db_path)
    base = "http://127.0.0.1:%d" % SERVER_PORT
    proc_info = {"pid": proc.pid, "port": SERVER_PORT,
                 "note": "本脚本自建隔离进程，结束即回收"}
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
        check("R1-server-up", "只读查询服务在独立端口启动", ready,
              {"process": proc_info})
        with urllib.request.urlopen(
                base + "/api/session_history/sessions?page=1&page_size=10",
                timeout=5) as r:
            listing = json.loads(r.read())["result"]
        check("R2-server-list", "服务端列表分页与本地一致",
              listing["total"] == sh.list_sessions(page_size=10)["total"],
              {"total": listing["total"]})
        with urllib.request.urlopen(
                base + "/api/session_history/sessions/sess_probe_history",
                timeout=5) as r:
            detail = json.loads(r.read())["result"]
        check("R3-server-detail-merged", "服务端单会话详情含合并后旧事项与交接链",
              detail["ok"] and "I-900" in detail.get("items", {})
              and detail.get("successor_chain") == ["sess_probe_successor"],
              {"items": sorted((detail.get("items") or {}).keys()),
               "successor_chain": detail.get("successor_chain")})
        post = urllib.request.Request(base + "/api/session_history/sessions",
                                      data=b"{}", method="POST")
        post_code = None
        try:
            urllib.request.urlopen(post, timeout=5)
        except urllib.error.HTTPError as exc:
            post_code = exc.code
        check("R4-server-readonly", "服务拒绝写请求（POST 返回 405）",
              post_code == 405, {"post_status": post_code})
        db_stat_after = os.stat(db_path)
        check("R5-no-writes", "查询过程不写会话库",
              db_stat_before.st_size == db_stat_after.st_size
              and db_stat_before.st_mtime == db_stat_after.st_mtime, {})
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    LAST_SERVER_PROCESS = proc_info


LAST_SERVER_PROCESS = None


def main():
    build_fixture()
    set_env()
    beth_probe_checks()
    rework_new_checks()
    server_checks()          # 索引仍带结转条目时验证服务端合并详情
    legacy_rerun_checks()
    deleted_case()
    proc_info = LAST_SERVER_PROCESS
    passed = sum(1 for c in CHECKS if c["pass"])
    result = {"item_id": "I-227", "mode": "返修隔离验收（四反例+新增+原28项复跑）",
              "run_id": "run-beth-i227-rework-20261014-a1",
              "fixture_root": FIXTURE,
              "isolated_process": proc_info,
              "passed": passed, "total": len(CHECKS),
              "all_pass": passed == len(CHECKS), "checks": CHECKS}
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("结果写入 %s：%d/%d 通过" % (RESULTS_PATH, passed, len(CHECKS)))
    sys.exit(0 if result["all_pass"] else 1)


if __name__ == "__main__":
    main()
