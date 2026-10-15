# -*- coding: utf-8 -*-
"""i227_inflight_complete_test.py — I-227 v3 完整在途工具识别隔离检查。

对同一夹具分别运行基线（合入前 v2 副本）与合入后源码：基线预期
保留五项截断类失败作对照，合入后十二项全部通过。夹具、状态目录、
索引目录全部重定向到 logs/i227_inflight_v3_20261015/ 私有目录；
会话库为自建 sqlite；生产历史索引与生产服务零接触。仅使用 Python
标准库。
"""
import datetime
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LP_SYNC = os.path.join(ROOT, "lp_sync")
OUT_DIR = os.path.join(ROOT, "logs", "i227_inflight_v3_20261015")
FIXTURE = os.path.join(OUT_DIR, "fixture")
BASELINE_SRC = os.path.join(OUT_DIR, "baseline", "session_truth_v2.py")
RESULTS_PATH = os.path.join(OUT_DIR, "inflight_complete_results.json")
NOW = time.time()


def iso_ago(seconds):
    return datetime.datetime.fromtimestamp(NOW - seconds).isoformat(
        timespec="seconds")


def ms_ago(seconds):
    return int((NOW - seconds) * 1000)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tool_payload(tool, status, spaced=False):
    if spaced:
        return json.dumps({"type": "tool", "tool": tool,
                           "state": {"status": status}},
                          indent=None, separators=(", ", ": "))
    return json.dumps({"type": "tool", "tool": tool,
                       "state": {"status": status}},
                      separators=(",", ":"))


def make_db(path):
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

    def sess(sid, age_s):
        con.execute("insert into session values(?,?,?,?,?)",
                    (sid, "v3夹具", ws, ms_ago(age_s + 3600), ms_ago(110)))

    def part(sid, payload, age_s):
        con.execute("insert into part(session_id,data,time_created) "
                    "values(?,?,?)", (sid, payload, ms_ago(age_s)))

    def running(sid, tool, age_s, spaced=False):
        part(sid, tool_payload(tool, "running", spaced=spaced), age_s)

    def completed(sid, tool, age_s, spaced=False):
        part(sid, tool_payload(tool, "completed", spaced=spaced), age_s)

    def mu(sid, age_s=200):
        con.execute("insert into model_usage(session_id,status,started_at,"
                    "completed_at,input_tokens) values(?,?,?,?,?)",
                    (sid, "completed", ms_ago(age_s + 30), ms_ago(age_s),
                     400000))
    # 原反例二：较早运行中被较新完成掩盖（两条）
    sess("sess_v3_probe", 110)
    running("sess_v3_probe", "Bash", 150)
    completed("sess_v3_probe", "Bash", 110)
    mu("sess_v3_probe")
    # 30 条较新完成之后的运行中工具
    sess("sess_v3_over30", 110)
    running("sess_v3_over30", "Bash", 150)
    for i in range(30):
        completed("sess_v3_over30", "Read", 109 - i * 0.1)
    mu("sess_v3_over30")
    # 100 条较新完成之后的运行中工具
    sess("sess_v3_over100", 110)
    running("sess_v3_over100", "Bash", 148)
    for i in range(100):
        completed("sess_v3_over100", "Read", 107 - i * 0.05)
    mu("sess_v3_over100")
    # 带空格 JSON 排版的运行中工具
    sess("sess_v3_spaced", 110)
    running("sess_v3_spaced", "Spaced", 150, spaced=True)
    for i in range(30):
        completed("sess_v3_spaced", "Read", 109 - i * 0.1)
    mu("sess_v3_spaced")
    # 六个在途工具排在三十条完成之前
    sess("sess_v3_multi6", 110)
    for k in range(6):
        running("sess_v3_multi6", "Tool%d" % (k + 1), 300 - k * 10)
    for i in range(30):
        completed("sess_v3_multi6", "Read", 200 - i * 0.1)
    mu("sess_v3_multi6")
    # 损坏 JSON 行 + 真实运行中工具
    sess("sess_v3_badjson", 110)
    running("sess_v3_badjson", "Bash", 150)
    for _ in range(3):
        part("sess_v3_badjson",
             '{"type": "tool", "tool": "Broken", "state": {"status": '
             '"running"', 140)
    for i in range(28):
        completed("sess_v3_badjson", "Read", 120 - i * 0.1)
    mu("sess_v3_badjson")
    # 全部完成与无工具记录
    sess("sess_v3_allcompleted", 110)
    for i in range(10):
        completed("sess_v3_allcompleted", "Bash", 130 - i)
    mu("sess_v3_allcompleted")
    sess("sess_v3_notools", 110)
    mu("sess_v3_notools")
    con.commit()
    con.close()


def build_fixture():
    if os.path.isdir(FIXTURE):
        shutil.rmtree(FIXTURE)
    os.makedirs(os.path.join(FIXTURE, "handoff_events"))
    os.makedirs(os.path.join(FIXTURE, "history"))
    os.makedirs(os.path.join(FIXTURE, "state"))
    make_db(os.path.join(FIXTURE, "db.sqlite"))
    state = {"runs": {
        "run_v3_work": {"run_id": "run_v3_work", "task_req_id": "v3_t1",
                        "item_id": "I-910", "session_id": "sess_v3_probe",
                        "started_at": iso_ago(400), "status": "active",
                        "worker_id": "T8-w1"}},
        "tasks": {"v3_t1": {"lane": "T8"}}}
    roster = {"workers": {
        "T8-w1": {"lane": "T8", "session_id": "sess_v3_probe",
                  "phase": "active", "updated_at": iso_ago(30),
                  "item_id": "I-910", "run_id": "run_v3_work"},
        "T8-w2": {"lane": "T8", "session_id": "sess_v3_missing",
                  "phase": "active", "updated_at": iso_ago(30),
                  "item_id": "I-911", "run_id": "run_v3_missing"}}}
    state["runs"]["run_v3_missing"] = {
        "run_id": "run_v3_missing", "task_req_id": "v3_t2", "item_id": "I-911",
        "session_id": "sess_v3_missing", "started_at": iso_ago(400),
        "status": "active", "worker_id": "T8-w2"}
    state["tasks"]["v3_t2"] = {"lane": "T8"}
    execution = {"sessions": {
        "sess_v3_probe": {"phase": "active", "updated_at": iso_ago(400)},
        "sess_v3_missing": {"phase": "active", "updated_at": iso_ago(400)},
        "sess_v3_stopping": {"phase": "stopping",
                             "updated_at": iso_ago(30)}}}
    registry = {}
    handoff_state = {"archived": []}
    items = {"items": [{"id": "I-910", "claimed_at": iso_ago(500)},
                       {"id": "I-900", "claimed_at": iso_ago(100)}]}
    dumps = {"state.json": state, "roster.json": roster,
             "execution.json": execution, "registry.json": registry,
             "handoff_state.json": handoff_state, "items.json": items}
    for name, obj in dumps.items():
        with open(os.path.join(FIXTURE, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    with open(os.path.join(FIXTURE, "timeline.jsonl"), "w",
              encoding="utf-8") as f:
        f.write(json.dumps({"ts": iso_ago(60), "ingested": 5,
                            "sessions": 5, "error": None}) + "\n")


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
    if LP_SYNC not in sys.path:
        sys.path.insert(0, LP_SYNC)


RESULTS = {"baseline": [], "v3": [], "history": []}
BASELINE_FAIL_EXPECTED = [
    "inflight_before_30_completed", "inflight_before_100_completed",
    "spaced_json_inflight", "multiple_inflight_beyond_window",
    "bad_json_compat"]


def record(group, cid, desc, ok, actual):
    RESULTS[group].append({"id": cid, "desc": desc, "pass": bool(ok),
                           "actual": actual})
    print(("PASS " if ok else "FAIL ") + "[%s] %s %s" % (group, cid, desc))


def truth_matrix(label, mod, con):
    def facts(sid):
        return mod._db_session_facts(con, sid)

    def classify(sid):
        return mod.classify(sid, facts(sid), None,
                            {"worker_id": "T8-w1", "lane": "T8",
                             "phase": "active"},
                            {"phase": "active"}, {"lane": "T8"}, iso_ago(0))
    # 原反例一：停止待回执
    stop_facts = {"session_row": ("v3", ms_ago(120)), "model_usage": None,
                  "tool_part": None, "tool_usage": None, "db_error": None}
    stop = mod.classify("sess_v3_stopping", stop_facts, None, {},
                        {"phase": "stopping", "updated_at": iso_ago(10)},
                        {}, iso_ago(0))
    record(label, "stop_without_confirmation",
           "停止待回执显示停止中待核对，不标已停止",
           not stop["status"].startswith("已停止")
           and stop["status"].startswith("停止中"), stop["status"])
    # 原反例二：较早运行中被较新完成掩盖
    row = classify("sess_v3_probe")
    record(label, "older_tool_still_running",
           "较早运行中工具不被较新完成掩盖",
           row["status"].startswith("在干活")
           and "长工具 150 秒" in row["status"], row["status"])
    for sid, cid, desc, age in (
            ("sess_v3_over30", "inflight_before_30_completed",
             "30条较新完成后仍判在干活", 150),
            ("sess_v3_over100", "inflight_before_100_completed",
             "100条较新完成后仍判在干活", 148)):
        row = classify(sid)
        record(label, cid, desc, row["status"].startswith("在干活")
               and "长工具 %d 秒" % age in row["status"], row["status"])
    row = classify("sess_v3_spaced")
    record(label, "spaced_json_inflight", "带空格JSON运行中工具进入在途全集",
           row["status"].startswith("在干活")
           and (row.get("in_flight_total") or 0) == 1
           and row["in_flight_tools"][0]["name"] == "Spaced",
           {"status": row["status"], "total": row.get("in_flight_total")})
    row = classify("sess_v3_multi6")
    shown = row.get("in_flight_tools") or []
    record(label, "multiple_inflight_beyond_window",
           "六个在途全部计入、展示有上限、最旧入选并标长工具",
           row["status"].startswith("在干活")
           and (row.get("in_flight_total") or 0) == 6
           and len(shown) <= 5
           and "长工具 300 秒" in row["status"]
           and row["last_tool"]["name"] == "Tool1",
           {"status": row["status"], "total": row.get("in_flight_total"),
            "displayed": len(shown), "last_tool": row["last_tool"]["name"]})
    row = classify("sess_v3_badjson")
    f = facts("sess_v3_badjson")
    record(label, "bad_json_compat",
           "损坏JSON行不崩溃不误计，真实在途仍识别",
           row["status"].startswith("在干活")
           and (row.get("in_flight_total") or 0) == 1
           and f.get("in_flight_bad_json", 0) >= 3,
           {"status": row["status"], "total": row.get("in_flight_total"),
            "bad_json": f.get("in_flight_bad_json")})
    row = classify("sess_v3_allcompleted")
    record(label, "all_completed_not_working", "全部完成判空闲",
           row["status"].startswith("空闲"), row["status"])
    row = classify("sess_v3_notools")
    record(label, "no_tool_records", "无工具记录不报错判空闲",
           row["status"].startswith("空闲"), row["status"])
    # 缺失会话源（gather 级：工位与运行在册）
    rows, meta = mod.gather()
    by = {r["session_id"]: r for r in rows}
    miss = by.get("sess_v3_missing")
    record(label, "session_row_missing_source",
           "缺失会话源判未知并附原因",
           miss is not None and miss["status"].startswith("未知")
           and "对话源缺失" in miss["status"],
           miss and miss["status"])


def history_checks():
    import session_history as sh
    reg_path = os.path.join(FIXTURE, "registry.json")
    handoff_path = os.path.join(FIXTURE, "handoff_state.json")
    orig_reg = json.load(open(reg_path, encoding="utf-8"))
    orig_handoff = json.load(open(handoff_path, encoding="utf-8"))
    reg_full = dict(orig_reg)
    reg_full["sess_v3_hist"] = {"item_id": "I-900", "lane": "T8",
                                "dispatched_at": iso_ago(100)}
    handoff_full = json.loads(json.dumps(orig_handoff))
    handoff_full["archived"] = orig_handoff["archived"] + [{
        "session_id": "sess_v3_hist", "successor": "sess_v3_succ",
        "archived_at": iso_ago(50)}]
    for path, obj in ((reg_path, reg_full), (handoff_path, handoff_full)):
        json.dump(obj, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    sh.build_index()
    for path, obj in ((reg_path, orig_reg), (handoff_path, orig_handoff)):
        json.dump(obj, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    sh.build_index()
    entry = sh.load_index()["sessions"]["sess_v3_hist"]
    record("history", "all_sources_removed_preserve_history",
           "来源撤空后完整保留事项、交接链并另记缺失",
           "I-900" in entry["items"]
           and entry["successor"] == "sess_v3_succ"
           and entry["missing"].get("source_missing"),
           {"items": sorted(entry["items"]), "successor": entry["successor"]})
    reg_new = dict(orig_reg)
    reg_new["sess_v3_hist"] = {"item_id": "I-901", "lane": "T8",
                               "dispatched_at": iso_ago(0)}
    json.dump(reg_new, open(reg_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    sh.build_index()
    entry = sh.load_index()["sessions"]["sess_v3_hist"]
    json.dump(orig_reg, open(reg_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    sh.build_index()
    record("history", "reused_session_preserve_old_item",
           "同会话换新事项重建合并保留旧事项",
           {"I-900", "I-901"}.issubset(set(entry["items"])),
           sorted(entry["items"]))


def main():
    build_fixture()
    set_env()
    con = sqlite3.connect(os.path.join(FIXTURE, "db.sqlite"))
    baseline = load_module("session_truth_v2_baseline", BASELINE_SRC)
    truth_matrix("baseline", baseline, con)
    import session_truth as v3
    truth_matrix("v3", v3, con)
    con.close()
    history_checks()
    base_fail = sorted(c["id"] for c in RESULTS["baseline"]
                       if not c["pass"])
    v3_fail = [c["id"] for c in RESULTS["v3"] if not c["pass"]]
    hist_fail = [c["id"] for c in RESULTS["history"] if not c["pass"]]
    summary = {
        "item_id": "I-227", "run_id": "run-beth-i227-integrate-v3-20261015-a1",
        "captured_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "fixture": FIXTURE,
        "baseline_source": BASELINE_SRC,
        "baseline_expected_failures": BASELINE_FAIL_EXPECTED,
        "baseline_actual_failures": base_fail,
        "baseline_failure_preserved": base_fail == sorted(
            BASELINE_FAIL_EXPECTED),
        "v3_total": len(RESULTS["v3"]), "v3_failed": v3_fail,
        "history_total": len(RESULTS["history"]), "history_failed": hist_fail,
        "all_pass": (base_fail == sorted(BASELINE_FAIL_EXPECTED)
                     and not v3_fail and not hist_fail),
        "results": RESULTS}
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("结果写入 %s：基线保留失败 %d/%d，v3 %d/%d，历史 %d/%d"
          % (RESULTS_PATH, len(base_fail), len(RESULTS["baseline"]),
             len(RESULTS["v3"]) - len(v3_fail), len(RESULTS["v3"]),
             len(RESULTS["history"]) - len(hist_fail),
             len(RESULTS["history"])))
    sys.exit(0 if summary["all_pass"] else 1)


if __name__ == "__main__":
    main()
