# -*- coding: utf-8 -*-
"""session_truth.py — I-227 会话执行状态对账：以四类事实判定状态真值。

四类事实来源（I-227 判据一）：
  一、宿主会话归属：lp_sync/runtime/execution.json 的会话绑定记录，
      含 executor_key 与 phase，phase 取值 active、idle、retired、
      stopping、stopped，其中 stopped 是停止确认事实；
  二、运行记录：lp_sync/kernel_state/state.json 的 runs 表，每条运行
      携带 run_id、session_id、worker_id、item_id 与 status；
  三、模型请求：会话库 model_usage 表的最近请求行；
  四、工具活动：会话库 part 表的工具行，行内 state.status 为
      pending 或 running 即工具在途。在途判定读取该会话全部非终态
      工具行，不受任何固定条数截断，任一在途即为在干活，较新已完成
      工具不得掩盖较早仍在途工具，长工具超过九十秒仍判在干活。
      近期完成工具行仅用于摘要展示；展示列表有条数上限，在途判定
      与在途计数始终使用完整非终态全集。

状态分档：在干活、空闲、等待额度、等待重试、等待结果入账、已中断、
停止中、已停止、未知。额度等待和中断不作为空闲，最近完成过请求
也不单独作为当前仍在工作的证据。实际活动优先于持久
旧相位：停止记录与在途工具冲突时按在干活显示并注明冲突待核对。
已停止必须有停止确认支撑，停止指令未获回执只显示停止中待核对。
活跃运行在册但最近活动超过三十分钟时按未知待核对处理（采样过期），
不用在册旧记录替代实际活跃证据。
会话纳入范围：项目目录会话按窗口口径纳入；父工作目录会话仅在
登记册、工位注册表、运行记录、宿主归属或交接记录核实为本项目
会话时纳入，未经核实不因目录相同自动纳入。
所有路径可经环境变量重定向供隔离验收使用；会话库只读；仅使用
Python 标准库。
"""
import datetime
import json
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SESSION_DB = os.environ.get(
    "LP_SESSION_DB",
    os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite"))
STATE_ROOT = os.environ.get(
    "LP_KERNEL_STATE_ROOT", os.path.join(ROOT, "lp_sync", "kernel_state"))
EXECUTION_PATH = os.environ.get(
    "LP_EXECUTION_STORE", os.path.join(ROOT, "lp_sync", "runtime", "execution.json"))
ROSTER_PATH = os.environ.get(
    "LP_LANE_WORKERS_ROSTER",
    os.path.join(ROOT, "lp_sync", "lane_workers_roster.json"))
REGISTRY_PATH = os.environ.get(
    "LP_REGISTRY_PATH", os.path.join(ROOT, "lp_sync", "dispatch_registry.json"))
HANDOFF_STATE_PATH = os.environ.get(
    "LP_HANDOFF_STATE_PATH", os.path.join(ROOT, "lp_sync", "handoff_state.json"))

PROJECT_DIR = "G:\\Playground\\isaac_flapper_sim"
PARENT_DIR = "G:\\Playground"
WORKSPACE_DIRS = (PARENT_DIR, PROJECT_DIR)
ACTIVE_WINDOW_S = 1800                  # 活跃窗口三十分钟，口径不变
WORKING_WINDOW_S = 90                   # 九十秒内最近活动算模型处理中
SAMPLE_STALE_S = 1800                   # 活跃运行采样有效期三十分钟
STOP_SHOW_S = 2100                      # 停止相关行在快照内保留三十五分钟
TOOL_OPEN_STATUSES = ("pending", "running")   # 工具行非终态即执行中
TOOL_SUMMARY_ROWS = 25                  # 近期工具行摘要窗口，仅用于展示
TOOL_IN_FLIGHT_DISPLAY = 5              # 在途工具展示条数上限，不影响判定
# 预筛模式：紧凑与带空格两种 JSON 排版都接受，提高召回；
# 预筛只圈定候选行，最终以解析出的 state.status 为准
TOOL_TYPE_LIKE_PATTERNS = ('%"type":"tool"%', '%"type": "tool"%')
TOOL_OPEN_LIKE_PATTERNS = tuple(
    "%" + text + "%"
    for status in TOOL_OPEN_STATUSES
    for text in ('"status":"%s"' % status, '"status": "%s"' % status))
WORKING = "在干活"
IDLE = "空闲"
STOPPING = "停止中"
STOPPED = "已停止"
UNKNOWN = "未知"


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _ms_iso(ms):
    if not ms:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def kernel_runs():
    """读运行记录：run_id → run 行。文件不可读返回空表并留错误说明。"""
    state = _read_json(os.path.join(STATE_ROOT, "state.json"), None)
    if state is None:
        return {}, "state.json 不可读"
    tasks = state.get("tasks") or {}
    runs = {}
    for rid, run in (state.get("runs") or {}).items():
        task = tasks.get(run.get("task_req_id")) or {}
        runs[rid] = {**run, "task_phase": task.get("phase"),
                     "task_retry_at": task.get("retry_at"),
                     "task_last_error": task.get("last_error")}
    return runs, None


def roster_workers():
    """读工位注册表：worker_id → 记录。"""
    data = _read_json(ROSTER_PATH, None)
    if data is None:
        return {}, "lane_workers_roster.json 不可读"
    return data.get("workers") or {}, None


def execution_sessions():
    """读宿主会话归属：session_id → 记录。"""
    data = _read_json(EXECUTION_PATH, None)
    if data is None:
        return {}, "execution.json 不可读"
    return data.get("sessions") or {}, None


def registry_triples():
    """读派发登记册：session_id → 三元组记录。"""
    return _read_json(REGISTRY_PATH, {}), None


def archived_entries():
    """读显式归档清单：archived 数组。"""
    return (_read_json(HANDOFF_STATE_PATH, {}).get("archived") or []), None


def _verified_ids(runs, roster, execution, registry, archived):
    """本项目已核实会话号集合：登记、工位、运行、宿主归属、交接。"""
    ok = set(registry)
    for run in runs.values():
        if run.get("session_id"):
            ok.add(run["session_id"])
    for rec in roster.values():
        if rec.get("session_id"):
            ok.add(rec["session_id"])
    ok.update(execution)
    for a in archived:
        for key in ("session_id", "successor"):
            if a.get(key):
                ok.add(a[key])
    return ok


def _candidate_sessions(runs, roster, execution, verified):
    """纳入快照的会话集合：项目目录窗口内会话、经核实的父目录窗口内
    会话、活跃运行会话、工位在册会话、近期停止相关会话的并集。"""
    cut_ms = (time.time() - ACTIVE_WINDOW_S) * 1000
    stop_cut = datetime.datetime.now() - datetime.timedelta(seconds=STOP_SHOW_S)
    cands = {}
    try:
        con = sqlite3.connect("file:" + SESSION_DB.replace("\\", "/") + "?mode=ro",
                              uri=True, timeout=8.0)
        try:
            marks = ",".join("?" * len(WORKSPACE_DIRS))
            rows = con.execute(
                "select id, title, directory, time_updated from session "
                "where directory in (%s) and time_updated > ?" % marks,
                (*WORKSPACE_DIRS, cut_ms)).fetchall()
            for sid, title, directory, tupd in rows:
                if directory == PARENT_DIR and sid not in verified:
                    continue     # 父目录未经核实会话不纳入
                cands[sid] = {"title": title, "time_updated": tupd,
                              "db_row": True}
        finally:
            con.close()
    except sqlite3.Error as exc:
        return None, "会话库读取失败：%s" % repr(exc)[:120]
    for rid, run in runs.items():
        sid = run.get("session_id")
        if sid and run.get("status") == "active":
            cands.setdefault(sid, {"db_row": False})
    for wid, rec in roster.items():
        sid = rec.get("session_id")
        if sid:
            cands.setdefault(sid, {"db_row": False})
    for sid, rec in execution.items():
        phase = rec.get("phase")
        updated = rec.get("updated_at")
        if phase in ("stopping", "stopped") and updated and updated >= stop_cut.isoformat():
            cands.setdefault(sid, {"db_row": False})
    return cands, None


def _parse_tool_part(time_created, raw):
    """解析单条工具行。损坏 JSON 返回 None，由调用方计数后跳过。"""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    state = data.get("state") or {}
    return {"tool": data.get("tool"), "status": state.get("status"),
            "started_ms": time_created}


def query_in_flight_tools(con, sid):
    """查询单会话完整非终态工具集合，不受固定条数截断。

    查询只读、按 session 限定，不读取非工具行；LIKE 仅作候选预筛，
    最终以解析出的 state.status 属于非终态为准，损坏 JSON 跳过并
    计数。近期完成工具行不进入本查询。返回
    (在途工具列表, 损坏行数, 预筛命中行数)。
    """
    type_clauses = " or ".join("data like ?" for _ in TOOL_TYPE_LIKE_PATTERNS)
    status_clauses = " or ".join("data like ?" for _ in TOOL_OPEN_LIKE_PATTERNS)
    rows = con.execute(
        "select time_created, data from part where session_id=? "
        "and (%s) and (%s) order by id desc" % (type_clauses, status_clauses),
        (sid, *TOOL_TYPE_LIKE_PATTERNS, *TOOL_OPEN_LIKE_PATTERNS)).fetchall()
    parts = []
    bad_json = 0
    for time_created, raw in rows:
        part = _parse_tool_part(time_created, raw)
        if part is None:
            bad_json += 1
            continue
        if part.get("status") in TOOL_OPEN_STATUSES:
            parts.append(part)
    return parts, bad_json, len(rows)


def _db_session_facts(con, sid):
    """单会话的库内事实：会话行、最近模型请求、近期工具行摘要与
    完整在途工具全集。在途全集单独查询，较新已完成工具不掩盖
    较早仍在途工具。"""
    facts = {"session_row": None, "model_usage": None, "context_tokens": None, "tool_part": None,
             "tool_parts": [], "in_flight_tools": [], "tool_usage": None,
             "summary_bad_json": 0, "in_flight_bad_json": 0,
             "in_flight_scanned": 0, "db_error": None}
    try:
        row = con.execute(
            "select title, time_updated from session where id=?", (sid,)).fetchone()
        facts["session_row"] = row
        mu = con.execute(
            "select status, started_at, completed_at, input_tokens from model_usage "
            "where session_id=? order by started_at desc limit 1", (sid,)).fetchone()
        facts["model_usage"] = mu
        context = con.execute(
            "select input_tokens from model_usage where session_id=? "
            "and status='completed' and input_tokens is not null "
            "order by started_at desc limit 1", (sid,)).fetchone()
        facts["context_tokens"] = context[0] if context else None
        rows = con.execute(
            "select time_created, data from part where session_id=? "
            "and data like ? order by id desc limit ?",
            (sid, '%"type":"tool"%', TOOL_SUMMARY_ROWS)).fetchall()
        parts = []
        for time_created, raw in rows:
            part = _parse_tool_part(time_created, raw)
            if part is None:
                facts["summary_bad_json"] += 1
                continue
            parts.append(part)
        facts["tool_parts"] = parts
        facts["tool_part"] = parts[0] if parts else None
        facts["in_flight_tools"], facts["in_flight_bad_json"], \
            facts["in_flight_scanned"] = query_in_flight_tools(con, sid)
        tu = con.execute(
            "select tool_name, status, started_at, completed_at, exit_code "
            "from tool_usage where session_id=? order by started_at desc limit 1",
            (sid,)).fetchone()
        facts["tool_usage"] = tu
    except sqlite3.Error as exc:
        facts["db_error"] = repr(exc)[:120]
    return facts


def _age_s(ms):
    if not ms:
        return None
    return max(0, int((time.time() * 1000 - ms) / 1000))


def gather(now_iso=None):
    """采集一轮对账事实。返回 (rows, meta)。"""
    now_iso = now_iso or _now_iso()
    runs, runs_err = kernel_runs()
    roster, roster_err = roster_workers()
    execution, exec_err = execution_sessions()
    registry, _ = registry_triples()
    archived, _ = archived_entries()
    archived_ids = {a.get("session_id") for a in archived}
    verified = _verified_ids(runs, roster, execution, registry, archived)
    run_by_sid = {}
    for rid, run in sorted(runs.items()):
        sid = run.get("session_id")
        if not sid:
            continue
        cur = run_by_sid.get(sid)
        if cur is None or run.get("status") == "active":
            run_by_sid[sid] = run
    worker_by_sid = {}
    for wid, rec in roster.items():
        sid = rec.get("session_id")
        if sid:
            worker_by_sid[sid] = {"worker_id": wid, **rec}
    cands, db_err = _candidate_sessions(runs, roster, execution, verified)
    meta = {"sampled_at": now_iso,
            "sources": {"runs": len(runs), "roster": len(roster),
                        "execution": len(execution), "registry": len(registry),
                        "archived": len(archived)},
            "source_errors": {k: v for k, v in
                              (("state.json", runs_err), ("roster", roster_err),
                               ("execution.json", exec_err), ("session_db", db_err))
                              if v},
            "verified_sessions": len(verified),
            "candidates": len(cands) if cands is not None else 0}
    rows = []
    if cands is None:
        # 采集失联：会话库整体不可读。工位与运行在册会话照常出行，
        # 状态判未知并附采集失败原因，不编造活动。
        for sid in sorted(set(worker_by_sid) | set(run_by_sid)):
            if sid in archived_ids:
                continue
            w = worker_by_sid.get(sid) or {}
            r = run_by_sid.get(sid) or {}
            rows.append(_unknown_row(
                sid, w, r, registry.get(sid) or {}, now_iso,
                "采集失联：会话库读取失败，本周期无活动采样"))
        meta["collection_error"] = db_err
        return rows, meta
    con = sqlite3.connect("file:" + SESSION_DB.replace("\\", "/") + "?mode=ro",
                          uri=True, timeout=8.0)
    try:
        for sid in sorted(cands):
            if sid in archived_ids:
                continue
            facts = _db_session_facts(con, sid)
            row = classify(sid, facts, run_by_sid.get(sid),
                           worker_by_sid.get(sid),
                           execution.get(sid) or {},
                           registry.get(sid) or {}, now_iso)
            if meta["source_errors"]:
                row["source"]["missing_sources"] = meta["source_errors"]
                if (row.get("status") or "").startswith(IDLE):
                    row["status"] = "%s（状态来源缺失，不能确认空闲）" % UNKNOWN
                    row["missing_reason"] = "; ".join(meta["source_errors"])
            rows.append(row)
    finally:
        con.close()
    return rows, meta


def _base_row(sid, worker, run, registry_rec, now_iso):
    return {
        "session_id": sid,
        "lane": (worker or {}).get("lane") or registry_rec.get("lane"),
        "item_id": ((worker or {}).get("item_id") or (run or {}).get("item_id")
                    or registry_rec.get("item_id")),
        "worker_id": (worker or {}).get("worker_id"),
        "run_id": (worker or {}).get("run_id") or (run or {}).get("run_id"),
        "status": None,
        "context_pct": None,
        "last_activity_at": None,
        "archived": False,
        "archive_reason": None,
        "last_tool": None,
        "in_flight_tools": None,
        "missing_reason": None,
        "source": {"kind": "表", "locus": None, "excerpt": None,
                   "sampled_at": now_iso},
    }


def _unknown_row(sid, worker, run, registry_rec, now_iso, reason):
    row = _base_row(sid, worker, run, registry_rec, now_iso)
    row["status"] = "%s（待核对：%s）" % (UNKNOWN, reason)
    row["missing_reason"] = reason
    row["source"]["kind"] = "文件"
    row["source"]["locus"] = "lp_sync/lane_workers_roster.json + kernel_state/state.json"
    row["source"]["excerpt"] = ("会话库本周期不可读，状态按未知上报；"
                                "工位 %s，运行 %s" %
                                (row["worker_id"] or "无", row["run_id"] or "无"))[:200]
    return row


def classify(sid, facts, run, worker, execution_rec, registry_rec, now_iso,
             token_cap=1000000):
    """单会话状态判定。判定优先级：会话行缺失、在途工具（实际活动
    优先于持久停止相位，冲突注明待核对）、停止确认、停止指令在途、
    活跃运行（九十秒内等待模型，三十分钟内运行在册，超期未知待
    核对）、已交付空闲、九十秒内活动、间隔空闲。在途判定使用完整
    非终态全集；展示列表按上限截取，计数不截断。"""
    row = _base_row(sid, worker, run, registry_rec, now_iso)
    sess = facts.get("session_row")
    mu = facts.get("model_usage")
    tool_part = facts.get("tool_part")
    tool_usage = facts.get("tool_usage")
    exec_phase = execution_rec.get("phase")
    if facts.get("db_error"):
        row["status"] = "%s（待核对：会话库查询失败 %s）" % (UNKNOWN, facts["db_error"])
        row["missing_reason"] = "会话库查询失败：%s" % facts["db_error"]
        row["source"]["locus"] = "session/%s" % sid
        row["source"]["excerpt"] = row["missing_reason"][:200]
        return row
    if sess is None:
        reason = "对话源缺失：会话库无该会话行"
        row["status"] = "%s（待核对：%s）" % (UNKNOWN, reason)
        row["missing_reason"] = reason
        row["source"]["kind"] = "表"
        row["source"]["locus"] = "session/%s" % sid
        row["source"]["excerpt"] = ("会话行不存在；工位 %s，运行 %s，来源为工位注册表"
                                    "与运行记录" % (row["worker_id"] or "无",
                                                    row["run_id"] or "无"))[:200]
        return row
    title, time_updated = sess
    mu_status = mu[0] if mu else None
    mu_started = mu[1] if mu else None
    mu_completed = mu[2] if mu else None
    mu_tokens = facts.get("context_tokens", mu[3] if mu else None)
    if mu_tokens is not None:
        row["context_pct"] = round(mu_tokens / token_cap * 100, 1)
    # 在途工具全集：兼容只带单工具行的旧形态调用
    in_flight = list(facts.get("in_flight_tools") or [])
    if not in_flight and tool_part and tool_part.get("status") in TOOL_OPEN_STATUSES:
        in_flight = [tool_part]
    tool_ages = [_age_s(p.get("started_ms")) for p in in_flight]
    oldest_age = max([a for a in tool_ages if a is not None], default=None)
    stop_conflict = exec_phase in ("stopped", "stopping") and bool(in_flight)
    recent_parts = facts.get("tool_parts") or ([tool_part] if tool_part else [])
    part_started_max = max((p.get("started_ms") or 0 for p in
                            recent_parts + in_flight), default=0)
    last_ms = max(v for v in (mu_started, mu_completed, time_updated,
                              part_started_max) if v)
    last_iso = _ms_iso(last_ms)
    row["last_activity_at"] = last_iso
    row["in_flight_tools"] = [
        {"name": p.get("tool"), "status": p.get("status"),
         "started_at": _ms_iso(p.get("started_ms")),
         "age_s": _age_s(p.get("started_ms"))} for p in in_flight[:TOOL_IN_FLIGHT_DISPLAY]]
    row["in_flight_total"] = len(in_flight)
    if in_flight:
        longest = in_flight[0]
        for p in in_flight[1:]:
            if (p.get("started_ms") or 0) < (longest.get("started_ms") or 0):
                longest = p
        long_mark = ""
        if oldest_age is not None and oldest_age > WORKING_WINDOW_S:
            long_mark = "，长工具 %d 秒" % oldest_age
        row["last_tool"] = {"name": longest.get("tool"),
                            "status": longest.get("status"),
                            "started_at": _ms_iso(longest.get("started_ms")),
                            "age_s": _age_s(longest.get("started_ms")),
                            "in_flight_count": len(in_flight)}
    else:
        row["last_tool"] = ({"name": tool_part.get("tool"),
                             "status": tool_part.get("status"),
                             "started_at": _ms_iso(tool_part.get("started_ms"))}
                            if tool_part else
                            ({"name": tool_usage[0], "status": tool_usage[1],
                              "started_at": _ms_iso(tool_usage[2]),
                              "completed_at": _ms_iso(tool_usage[3])}
                             if tool_usage else None))
    age = _age_s(last_ms)
    basis = {"execution_phase": exec_phase,
             "roster_phase": (worker or {}).get("phase"),
             "run_status": (run or {}).get("status"),
             "model_usage_status": mu_status}
    row["source"]["basis"] = basis
    row["source"]["in_flight_count"] = len(in_flight)
    # 判定主体
    if in_flight:
        conflict_mark = ""
        if stop_conflict:
            conflict_mark = "；存在停止记录与实际活动冲突，待核对"
            row["missing_reason"] = ("停止相位 %s 与在途工具冲突，实际活动优先显示，"
                                     "停止事实待核对" % exec_phase)
        row["status"] = "%s（工具 %s 运行中%s%s）" % (
            WORKING, row["last_tool"]["name"], long_mark, conflict_mark)
    elif exec_phase == "stopped":
        row["status"] = "%s（有停止确认：execution.json phase=stopped，%s）" % (
            STOPPED, execution_rec.get("updated_at") or "时间未记录")
    elif exec_phase == "stopping":
        row["status"] = "%s（待核对：停止指令已发，尚未获得停止确认）" % STOPPING
    elif execution_rec.get("failure_category") == "rate_limited":
        retry_at = execution_rec.get("retry_at") or (run or {}).get("task_retry_at")
        row["status"] = "等待额度（执行通道已限额%s）" % (
            "，可重试时间 " + str(retry_at) if retry_at else "")
    elif (run or {}).get("task_phase") == "retry_wait":
        row["status"] = "等待重试（本次执行尚未完成%s）" % (
            "，重试时间 " + str(run["task_retry_at"])
            if (run or {}).get("task_retry_at") else "")
    elif exec_phase == "interrupted":
        row["status"] = "已中断（待恢复或核对本次结果）"
    elif (run or {}).get("task_phase") in ("result_pending", "result_failed"):
        row["status"] = "等待结果入账（执行结果尚未完成对账）"
    elif exec_phase == "idle":
        if (run or {}).get("status") == "active":
            row["status"] = "%s（宿主空闲与本次运行在办不一致，待核对结果）" % UNKNOWN
            row["missing_reason"] = "宿主与内核运行状态冲突"
        elif mu_status in ("in_progress", "running", "pending", "started"):
            row["status"] = "%s（宿主空闲但模型请求未结束，待核对）" % UNKNOWN
            row["missing_reason"] = "宿主与模型请求状态冲突"
        else:
            row["status"] = "%s（宿主确认本次执行已结束）" % IDLE
    elif (run or {}).get("status") == "active":
        if (exec_phase == "active" and
                mu_status in ("in_progress", "running", "pending", "started") and
                age is not None and age <= SAMPLE_STALE_S):
            row["status"] = "%s（本次模型请求进行中）" % WORKING
        else:
            reason = ("运行 %s 在册，当前无可确认的模型或工具执行事实"
                      % (run or {}).get("run_id"))
            row["status"] = "%s（待核对：%s）" % (UNKNOWN, reason)
            row["missing_reason"] = reason
    elif (worker or {}).get("phase") == "idle" and (run or {}).get("status") == "ended":
        row["status"] = "%s（已交付：工位 %s 空闲于 %s）" % (
            IDLE, (worker or {}).get("worker_id"), (worker or {}).get("updated_at"))
    elif (exec_phase == "active" and
          mu_status in ("in_progress", "running", "pending", "started") and
          age is not None and age <= SAMPLE_STALE_S):
        row["status"] = "%s（模型请求进行中）" % WORKING
    else:
        row["status"] = "%s（缺少本次执行结束或进行中的确认）" % UNKNOWN
        row["missing_reason"] = "不能仅由最近活动的时间间隔确认空闲"
    row["source"]["locus"] = ("part/%s#tool_rows[%d]（在途 %d）；model_usage/%s#latest；"
                              "kernel_state/state.json#runs[%s]" %
                              (sid[:13], len(facts.get("tool_parts") or []),
                               len(in_flight), sid[:13],
                               (run or {}).get("run_id") or "无"))
    row["source"]["excerpt"] = ("工具行 %d 条，在途 %d（最早在途 %s 秒）；模型请求 %s；"
                                "工位 %s 相位 %s；运行 %s 状态 %s；宿主相位 %s" % (
                                    len(facts.get("tool_parts") or []),
                                    len(in_flight),
                                    oldest_age if oldest_age is not None else "无",
                                    mu_status or "无请求",
                                    row["worker_id"] or "无",
                                    basis["roster_phase"] or "无",
                                    row["run_id"] or "无",
                                    basis["run_status"] or "无",
                                    exec_phase or "无"))[:200]
    return row


def legacy_status(row):
    """旧口径对照：仅按最近活动是否在九十秒内分在干活与空闲。
    供验收对照取证，不用于上报。"""
    if not row.get("last_activity_at"):
        return UNKNOWN
    started = datetime.datetime.fromisoformat(row["last_activity_at"]).timestamp()
    age = time.time() - started
    return WORKING if age <= WORKING_WINDOW_S else IDLE


def collect_body(token_cap=1000000):
    """组装完整上行包体。会话库整体不可读时仍输出未知行与错误说明。"""
    rows, meta = gather()
    now_ms = time.time() * 1000
    realtime = 0
    for r in rows:
        if not r.get("last_activity_at"):
            continue
        try:
            age = now_ms - datetime.datetime.fromisoformat(
                r["last_activity_at"]).timestamp() * 1000
            if 0 <= age <= 300_000:
                realtime += 1
        except ValueError:
            continue
    body = {"sessions": rows, "realtime_active": realtime,
            "parallel_limit_default": 15}
    return body, meta
