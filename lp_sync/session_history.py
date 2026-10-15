# -*- coding: utf-8 -*-
"""session_history.py — I-227 持久会话历史索引与只读查询。

索引合并五类已核实的本地来源，按会话号去重：
  一、会话登记：lp_sync/dispatch_registry.json 的派发三元组；
  二、认领与派发事件：items_registry/items.json 的认领字段与
      lp_sync/kernel_state/state.json 的 tasks 及 runs 运行记录；
  三、交接记录：lp_sync/handoff_state.json 的归档清单与
      lp_sync/handoff_events 目录的切换事件；
  四、保留快照：会话库 session 表的行更新时刻（只读）与
      logs/console_collector_timeline.jsonl 的采集时间线；
  五、宿主会话归属：lp_sync/runtime/execution.json 的绑定与相位。

保留类型三分：显式归档（归档清单在册）、空闲超时保留（最后活动
超过三十五分钟且无归档记录）、无来源（仅被他处引用而无自身记录，
或全部来源消失后按上次索引完整保留并另记来源缺失）。

重建合并（返修条款）：上次索引在册而本次来源消失的会话按上次索引
完整保留历次事项、工位、运行、交接卡与链及归档来源，只追加来源
缺失标记；同会话换新事项重建时与旧条目合并，旧事项与交接记录不
丢失，运行与交接按身份去重，来自上次索引的活跃相位标记 stale，
不当作当前事实。历次认领时刻随事项槽保留。
会话纳入范围：项目目录会话直接纳入；父工作目录会话须在登记、
工位、运行、宿主归属或交接来源中核实，否则不纳入。
查询为纯只读：不恢复会话、不执行会话、不写会话库。缺失会话源
的对话摘要返回明确失败原因，不返回正常空摘要。仅使用标准库。
"""
import copy
import datetime
import glob
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATE_ROOT = os.environ.get(
    "LP_KERNEL_STATE_ROOT", os.path.join(ROOT, "lp_sync", "kernel_state"))
EXECUTION_PATH = os.environ.get(
    "LP_EXECUTION_STORE", os.path.join(ROOT, "lp_sync", "runtime", "execution.json"))
REGISTRY_PATH = os.environ.get(
    "LP_REGISTRY_PATH", os.path.join(ROOT, "lp_sync", "dispatch_registry.json"))
HANDOFF_STATE_PATH = os.environ.get(
    "LP_HANDOFF_STATE_PATH", os.path.join(ROOT, "lp_sync", "handoff_state.json"))
HANDOFF_EVENTS_DIR = os.environ.get(
    "LP_HANDOFF_EVENTS_DIR", os.path.join(ROOT, "lp_sync", "handoff_events"))
TIMELINE_PATH = os.environ.get(
    "LP_COLLECTOR_TIMELINE",
    os.path.join(ROOT, "logs", "console_collector_timeline.jsonl"))
ITEMS_PATH = os.environ.get(
    "LP_ITEMS_REGISTRY", os.path.join(ROOT, "items_registry", "items.json"))
SESSION_DB = os.environ.get(
    "LP_SESSION_DB",
    os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite"))
HISTORY_DIR = os.environ.get(
    "LP_SESSION_HISTORY_DIR", os.path.join(ROOT, "lp_sync", "session_history"))
INDEX_PATH = os.path.join(HISTORY_DIR, "index.json")

SCHEMA_VERSION = 1
DELIVERY_VERSION = "session-observability-v2"
RETENTION_TIMEOUT_S = 2100               # 空闲超时保留门槛三十五分钟
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200
ARCHIVE_EXPLICIT = "explicit_archive"
RETENTION_TIMEOUT = "retained_timeout"
NO_SOURCE = "no_source"
ACTIVE = "active"
PROJECT_DIR = "G:\\Playground\\isaac_flapper_sim"
PARENT_DIR = "G:\\Playground"


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _to_ts(iso):
    """本地 ISO 字符串转时间戳；解析失败返回 None。"""
    if not iso or not isinstance(iso, str):
        return None
    try:
        return datetime.datetime.fromisoformat(str(iso)[:26]).timestamp()
    except ValueError:
        return None


def _touch(entry, when, source):
    """推进会话条目的最后活动时刻并登记来源。"""
    ts = _to_ts(when)
    if ts is not None:
        cur = _to_ts(entry.get("last_seen_at"))
        if cur is None or ts > cur:
            entry["last_seen_at"] = str(when)[:26]
    first = _to_ts(entry.get("first_seen_at"))
    if ts is not None and (first is None or ts < first):
        entry["first_seen_at"] = str(when)[:26]


def _entry(index, sid, source):
    e = index["sessions"].get(sid)
    if e is None:
        e = {"session_id": sid, "lane": None, "title_head": None,
             "workers": {}, "items": {}, "runs": [], "handovers": [],
             "successor": None, "predecessor": None,
             "first_seen_at": None, "last_seen_at": None,
             "retention": None, "sources": [], "missing": {}}
        index["sessions"][sid] = e
    if source not in e["sources"]:
        e["sources"].append(source)
    return e


def _session_db_times(verified):
    """会话库只读：会话号到（标题、更新时刻、创建时刻）。项目目录
    会话直接纳入；父工作目录会话仅在已核实验证集合内纳入。"""
    import sqlite3
    out = {}
    err = None
    try:
        con = sqlite3.connect("file:" + SESSION_DB.replace("\\", "/") +
                              "?mode=ro", uri=True, timeout=8.0)
        try:
            rows = con.execute(
                "select id, title, directory, time_created, time_updated "
                "from session where directory in (?,?)",
                (PARENT_DIR, PROJECT_DIR)).fetchall()
        finally:
            con.close()
        for sid, title, directory, tc, tu in rows:
            if directory == PARENT_DIR and sid not in verified:
                continue
            out[sid] = {"title": title,
                        "created_iso": datetime.datetime.fromtimestamp(
                            tc / 1000).isoformat(timespec="seconds") if tc else None,
                        "updated_iso": datetime.datetime.fromtimestamp(
                            tu / 1000).isoformat(timespec="seconds") if tu else None}
    except (sqlite3.Error, OSError) as exc:
        err = repr(exc)[:160]
    return out, err


def _timeline_scan():
    """采集时间线扫描：最近成功时刻、最近错误记录与错误计数。"""
    last_ok = None
    last_err = None
    err_count = 0
    ok_count = 0
    try:
        with open(TIMELINE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("error"):
                    err_count += 1
                    last_err = rec
                elif rec.get("ingested") is not None:
                    ok_count += 1
                    last_ok = rec
    except OSError:
        return {"ok_cycles": 0, "error_cycles": 0,
                "last_ok_at": None, "last_error": None, "readable": False}
    return {"ok_cycles": ok_count, "error_cycles": err_count,
            "last_ok_at": (last_ok or {}).get("ts"),
            "last_error": (last_err or {"ts": None, "error": None}),
            "readable": True}


def _handover_key(h):
    return (h.get("at"), h.get("from"), h.get("to"), h.get("source"))


def _merge_prior(index, prior, report):
    """上次索引合并（返修条款）：来源消失的会话完整结转并标记来源
    缺失；仍在册的会话合并旧事项、旧运行、旧交接与认领时刻，按
    身份去重，来自旧索引的活跃运行相位标 stale。"""
    prior_sessions = (prior or {}).get("sessions") or {}
    built_at = (prior or {}).get("built_at")
    carried = merged = 0
    for sid, pe in prior_sessions.items():
        if sid not in index["sessions"]:
            e = copy.deepcopy(pe)
            e["session_id"] = sid
            for run in e.get("runs", []):
                run["source"] = "prior_index"
                if run.get("status") == "active":
                    run["stale"] = True
            for slot in (e.get("items") or {}).values():
                if "prior_index" not in slot.get("sources", []):
                    slot.setdefault("sources", []).append("prior_index")
            if "prior_index" not in e["sources"]:
                e["sources"].append("prior_index")
            e["_carried"] = True
            e.setdefault("missing", {})["source_missing"] = (
                "全部来源缺失：条目按上次索引（%s）完整保留" %
                (built_at or "时间未知"))
            if "session_db" in (pe.get("sources") or []):
                e["missing"]["deleted"] = (
                    "会话行已删除：曾见于上次索引（%s）" %
                    (built_at or "时间未知"))
            index["sessions"][sid] = e
            carried += 1
            continue
        e = index["sessions"][sid]
        used_prior = False
        for item, pslot in (pe.get("items") or {}).items():
            if item in e["items"]:
                if pslot.get("claimed_at") and \
                        not e["items"][item].get("claimed_at"):
                    e["items"][item]["claimed_at"] = pslot["claimed_at"]
                    used_prior = True
                continue
            slot = copy.deepcopy(pslot)
            slot["sources"] = ["prior_index"] + [
                s for s in slot.get("sources", []) if s != "prior_index"]
            e["items"][item] = slot
            used_prior = True
        new_run_ids = {r.get("run_id") for r in e.get("runs", [])}
        for prun in pe.get("runs", []):
            if prun.get("run_id") in new_run_ids:
                continue
            run = copy.deepcopy(prun)
            run["source"] = "prior_index"
            if run.get("status") == "active":
                run["stale"] = True
            e["runs"].append(run)
            used_prior = True
        for wid, pw in (pe.get("workers") or {}).items():
            w = e["workers"].setdefault(
                wid, {"first": None, "last": None, "runs": []})
            for rid in pw.get("runs", []):
                if rid not in w["runs"]:
                    w["runs"].append(rid)
                    used_prior = True
            for key in ("first", "last"):
                ts_p = _to_ts(pw.get(key))
                ts_n = _to_ts(w.get(key))
                if ts_p is not None and (ts_n is None or
                                         (key == "first" and ts_p < ts_n) or
                                         (key == "last" and ts_p > ts_n)):
                    w[key] = pw.get(key)
        existing_keys = {_handover_key(h) for h in e.get("handovers", [])}
        for ph in pe.get("handovers", []):
            if _handover_key(ph) not in existing_keys:
                e["handovers"].append(copy.deepcopy(ph))
                existing_keys.add(_handover_key(ph))
                used_prior = True
        if not e.get("successor") and pe.get("successor"):
            e["successor"] = pe["successor"]
            used_prior = True
        if not e.get("predecessor") and pe.get("predecessor"):
            e["predecessor"] = pe["predecessor"]
            used_prior = True
        for key, pick in (("first_seen_at", min), ("last_seen_at", max)):
            ts_p = _to_ts(pe.get(key))
            ts_n = _to_ts(e.get(key))
            if ts_p is not None and (ts_n is None or
                                     (pick is min and ts_p < ts_n) or
                                     (pick is max and ts_p > ts_n)):
                e[key] = pe.get(key)
        if used_prior and "prior_index" not in e["sources"]:
            e["sources"].append("prior_index")
            merged += 1
    report["sources"]["prior_index_carryover"] = {
        "records": carried, "indexed": carried, "merged_into_existing": merged}
    return carried, merged


def build_index(keep_prior=True):
    """构建或重建索引。返回 (index, report)。重复构建同一来源集合
    得到相同会话集合；带旧索引重建时合并保留历史，不产生重复记录。"""
    prior = _read_json(INDEX_PATH, None) if keep_prior else None
    index = {"schema_version": SCHEMA_VERSION,
             "delivery_version": DELIVERY_VERSION,
             "built_at": _now_iso(),
             "sessions": {}}
    report = {"built_at": index["built_at"], "sources": {}}

    # 一、会话登记
    registry = _read_json(REGISTRY_PATH, {})
    for sid, rec in registry.items():
        e = _entry(index, sid, "dispatch_registry")
        e["lane"] = e["lane"] or rec.get("lane")
        e["title_head"] = e["title_head"] or rec.get("title_head")
        item = rec.get("item_id")
        if item:
            e["items"].setdefault(item, {"first": None, "last": None,
                                         "sources": []})
            if "dispatch_registry" not in e["items"][item]["sources"]:
                e["items"][item]["sources"].append("dispatch_registry")
        _touch(e, rec.get("dispatched_at"), "dispatch_registry")
    report["sources"]["dispatch_registry"] = {
        "records": len(registry), "indexed": len(registry)}

    # 二、运行记录与任务归属
    state = _read_json(os.path.join(STATE_ROOT, "state.json"), {})
    runs = state.get("runs") or {}
    tasks = state.get("tasks") or {}
    for rid, run in runs.items():
        sid = run.get("session_id")
        if not sid:
            continue
        e = _entry(index, sid, "kernel_runs")
        item = run.get("item_id")
        if item:
            slot = e["items"].setdefault(
                item, {"first": None, "last": None, "sources": []})
            if "kernel_runs" not in slot["sources"]:
                slot["sources"].append("kernel_runs")
        e["runs"].append({"run_id": run.get("run_id"), "item_id": item,
                          "worker_id": run.get("worker_id"),
                          "started_at": run.get("started_at"),
                          "status": run.get("status")})
        wid = run.get("worker_id")
        if wid:
            w = e["workers"].setdefault(
                wid, {"first": None, "last": None, "runs": []})
            if run.get("run_id") and run["run_id"] not in w["runs"]:
                w["runs"].append(run["run_id"])
            ts = _to_ts(run.get("started_at"))
            if ts is not None:
                wfs = _to_ts(w.get("first"))
                wls = _to_ts(w.get("last"))
                if wfs is None or ts < wfs:
                    w["first"] = run.get("started_at")
                if wls is None or ts > wls:
                    w["last"] = run.get("started_at")
        if run.get("status") == "active":
            e["lane"] = e["lane"] or _lane_of_task(tasks, run.get("task_req_id"))
        _touch(e, run.get("started_at"), "kernel_runs")
    report["sources"]["kernel_runs"] = {
        "records": len(runs),
        "indexed": len({r.get("session_id") for r in runs.values()
                        if r.get("session_id")})}

    # 三、交接记录：归档清单与切换事件
    handoff_state = _read_json(HANDOFF_STATE_PATH, {})
    successors = {}
    for a in handoff_state.get("archived") or []:
        sid = a.get("session_id")
        if not sid:
            continue
        e = _entry(index, sid, "handoff_state")
        e["_archive_hint"] = {"reason": a.get("archive_reason"),
                              "at": a.get("archived_at")}
        succ = a.get("successor")
        if succ:
            e["successor"] = succ
            successors[succ] = sid
        card = a.get("card")
        e["handovers"].append({"at": a.get("archived_at"),
                               "from": sid, "to": succ,
                               "reason": a.get("archive_reason"),
                               "card": card,
                               "source": "handoff_state"})
        if succ:
            e2 = _entry(index, succ, "handoff_state")
            e2["handovers"].append({"at": a.get("archived_at"),
                                    "from": sid, "to": succ,
                                    "reason": a.get("archive_reason"),
                                    "card": card,
                                    "source": "handoff_state"})
        _touch(e, a.get("archived_at"), "handoff_state")
    event_files = sorted(glob.glob(os.path.join(HANDOFF_EVENTS_DIR, "*.json")))
    for path in event_files:
        ev = _read_json(path, {})
        old, new = ev.get("old"), ev.get("new")
        when = ev.get("kernel_reconciled") or ev.get("at")
        if old:
            e = _entry(index, old, "handoff_events")
            if new:
                e["successor"] = new
                successors[new] = old
            e["handovers"].append({"at": when, "from": old, "to": new,
                                   "reason": ev.get("reason") or
                                   "handoff 事件文件",
                                   "card": ev.get("card"),
                                   "source": os.path.basename(path)})
        if new:
            e2 = _entry(index, new, "handoff_events")
            e2["handovers"].append({"at": when, "from": old, "to": new,
                                    "reason": ev.get("reason") or
                                    "handoff 事件文件",
                                    "card": ev.get("card"),
                                    "source": os.path.basename(path)})
        for sid in (old, new):
            if sid:
                _touch(_entry(index, sid, "handoff_events"), when,
                       "handoff_events")
    for sid, pred in successors.items():
        _entry(index, sid, "handoff_events")["predecessor"] = pred
    report["sources"]["handoff_state"] = {
        "records": len(handoff_state.get("archived") or []),
        "indexed": len({a.get("session_id") for a in
                        (handoff_state.get("archived") or []) if a.get("session_id")})}
    report["sources"]["handoff_events"] = {
        "records": len(event_files),
        "indexed": len({sid for p in event_files
                        for sid in ((_read_json(p, {}) or {}).get("old"),
                                    (_read_json(p, {}) or {}).get("new"))
                        if sid})}

    # 五、宿主会话归属（先于快照，供核实集合使用）
    execution = _read_json(EXECUTION_PATH, {})
    for sid, rec in (execution.get("sessions") or {}).items():
        e = _entry(index, sid, "execution_store")
        e["execution_phase"] = rec.get("phase")
        _touch(e, rec.get("updated_at"), "execution_store")
    report["sources"]["execution_store"] = {
        "records": len(execution.get("sessions") or {}),
        "indexed": len(execution.get("sessions") or {})}

    # 四、保留快照：会话库行时刻与采集时间线
    verified = set(index["sessions"])
    db_times, db_err = _session_db_times(verified)
    for sid, info in db_times.items():
        e = _entry(index, sid, "session_db")
        e["title_head"] = e["title_head"] or (info.get("title") or "")[:60]
        _touch(e, info.get("created_iso"), "session_db")
        _touch(e, info.get("updated_iso"), "session_db")
    report["sources"]["session_db"] = {"records": len(db_times),
                                       "indexed": len(db_times),
                                       "read_error": db_err}
    timeline = _timeline_scan()
    index["collection_timeline"] = timeline
    report["sources"]["collector_timeline"] = {
        "records": timeline.get("ok_cycles", 0) + timeline.get("error_cycles", 0),
        "indexed": None,
        "error_cycles": timeline.get("error_cycles", 0),
        "last_error_at": (timeline.get("last_error") or {}).get("ts")}

    # 认领字段：事项认领时刻，用于缺认领判定与历次认领对应
    items = _read_json(ITEMS_PATH, {})
    if isinstance(items, dict):
        items = items.get("items") or []
    claim_by_item = {}
    for it in items if isinstance(items, list) else []:
        if it.get("claimed_at"):
            claim_by_item[it.get("id")] = it.get("claimed_at")
    for sid, e in index["sessions"].items():
        for item, slot in e["items"].items():
            if item in claim_by_item:
                slot["claimed_at"] = claim_by_item[item]
                _touch(e, claim_by_item[item], "items_claims")

    # 上次索引合并（认领挂接之后：当前认领权威在册时不引用旧索引，
    # 认领已撤但旧索引在册的保留旧认领时刻，属历次认领记录）
    carried, merged = _merge_prior(index, prior, report)

    # 保留类型与缺失原因判定
    now_ts = time.time()
    for sid, e in index["sessions"].items():
        hint = e.pop("_archive_hint", None)
        carried_flag = e.pop("_carried", False)
        if hint is not None:
            # 显式归档只认归档清单本行；后继会话不因被引用而归档
            e["retention"] = {"kind": ARCHIVE_EXPLICIT,
                              "reason": hint.get("reason") or "归档清单在册",
                              "at": hint.get("at")}
        elif carried_flag:
            prior_kind = ((prior or {}).get("sessions", {}).get(sid, {})
                          .get("retention") or {}).get("kind")
            if prior_kind == ARCHIVE_EXPLICIT:
                e["retention"] = {
                    "kind": ARCHIVE_EXPLICIT,
                    "reason": "显式归档事实随上次索引保留（来源已消失）",
                    "at": None}
            else:
                e["retention"] = {
                    "kind": NO_SOURCE,
                    "reason": "全部来源缺失：按上次索引完整保留",
                    "at": None}
        elif not db_times.get(sid) and "session_db" not in e["sources"] and \
                "prior_index" not in e["sources"]:
            if not [s for s in e["sources"] if s != "prior_index"]:
                e["retention"] = {"kind": NO_SOURCE,
                                  "reason": "无来源：仅被他处引用，无自身记录",
                                  "at": None}
            else:
                e["retention"] = {"kind": NO_SOURCE,
                                  "reason": "对话源缺失：会话库无该会话行",
                                  "at": None}
                e["missing"]["dialog_source"] = "会话库无该会话行"
        else:
            last = _to_ts(e.get("last_seen_at"))
            age = None if last is None else now_ts - last
            if age is not None and age > RETENTION_TIMEOUT_S:
                e["retention"] = {"kind": RETENTION_TIMEOUT,
                                  "reason": "空闲超时保留：最后活动 %s，超三十五分钟未刷新"
                                            % e.get("last_seen_at"),
                                  "at": e.get("last_seen_at")}
            else:
                e["retention"] = {"kind": ACTIVE,
                                  "reason": "最后活动 %s" % e.get("last_seen_at"),
                                  "at": e.get("last_seen_at")}
        # 缺认领判定：会话挂接了事项但事项无认领记录
        unclaimed = [item for item, slot in e["items"].items()
                     if not slot.get("claimed_at")]
        if unclaimed:
            e["missing"]["claim"] = "无认领记录：事项 %s 无 claimed_at" % \
                "、".join(unclaimed)
        # 网络与采集失败：最后成功采样之后仍有会话活动，而时间线最近
        # 记录为错误，说明该会话最新状态上行失败
        if timeline.get("last_error") and timeline.get("last_ok_at") \
                and e["retention"]["kind"] == ACTIVE:
            last_ok_ts = _to_ts(timeline["last_ok_at"])
            err_ts = _to_ts(timeline["last_error"].get("ts"))
            seen_ts = _to_ts(e.get("last_seen_at"))
            if err_ts and last_ok_ts and err_ts > last_ok_ts and \
                    seen_ts and seen_ts > last_ok_ts:
                e["missing"]["network"] = \
                    "上行失败：采集时间线最近记录为错误（%s）" % \
                    str(timeline["last_error"].get("error"))[:80]

    # 工位线归属补全：没有运行记录的登记会话从登记册取线
    for sid, e in index["sessions"].items():
        if not e["lane"]:
            e["lane"] = (registry.get(sid) or {}).get("lane")
    index["stats"] = _stats(index)
    report["index_sessions"] = len(index["sessions"])
    report["retention_counts"] = index["stats"]["retention"]
    report["prior_merge"] = {"carried": carried, "merged": merged}
    _save_index(index)
    return index, report


def _lane_of_task(tasks, req_id):
    t = tasks.get(req_id) or {}
    return t.get("lane")


def _stats(index):
    kinds = {}
    for e in index["sessions"].values():
        kind = (e.get("retention") or {}).get("kind") or NO_SOURCE
        kinds[kind] = kinds.get(kind, 0) + 1
    lanes = {}
    for e in index["sessions"].values():
        if e.get("lane"):
            lanes[e["lane"]] = lanes.get(e["lane"], 0) + 1
    return {"sessions": len(index["sessions"]), "retention": kinds,
            "by_lane": lanes}


def _save_index(index):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    os.replace(tmp, INDEX_PATH)


def load_index():
    return _read_json(INDEX_PATH, None)


# ── 只读查询（判据四、五）────────────────────────────────────────

def list_sessions(filters=None, page=1, page_size=PAGE_SIZE_DEFAULT):
    """筛选与稳定分页。排序键固定：最后活动时刻降序，会话号升序。
    total 恒等于筛选集合总数；跨页遍历无重复无遗漏。"""
    filters = filters or {}
    page = max(1, int(page or 1))
    page_size = min(PAGE_SIZE_MAX, max(1, int(page_size or PAGE_SIZE_DEFAULT)))
    index = load_index()
    if index is None:
        return {"total": 0, "page": page, "page_size": page_size,
                "items": [], "index_built_at": None,
                "note": "索引不存在：尚未构建"}
    rows = []
    date_from = _to_ts(filters.get("date_from"))
    date_to = _to_ts(filters.get("date_to"))
    for e in index["sessions"].values():
        if filters.get("lane") and e.get("lane") != filters["lane"]:
            continue
        if filters.get("worker_id") and \
                filters["worker_id"] not in e.get("workers", {}):
            continue
        if filters.get("item_id") and filters["item_id"] not in e.get("items", {}):
            continue
        if filters.get("retention") and \
                (e.get("retention") or {}).get("kind") != filters["retention"]:
            continue
        last = _to_ts(e.get("last_seen_at"))
        if date_from is not None and (last is None or last < date_from):
            continue
        if date_to is not None and (last is None or last > date_to):
            continue
        rows.append(e)
    rows.sort(key=lambda e: (-(_to_ts(e.get("last_seen_at")) or 0),
                             e["session_id"]))
    total = len(rows)
    start = (page - 1) * page_size
    items = []
    for e in rows[start:start + page_size]:
        items.append({
            "session_id": e["session_id"], "lane": e.get("lane"),
            "workers": sorted(e.get("workers", {})),
            "items": sorted(e.get("items", {})),
            "run_count": len(e.get("runs", [])),
            "successor": e.get("successor"), "predecessor": e.get("predecessor"),
            "last_seen_at": e.get("last_seen_at"),
            "first_seen_at": e.get("first_seen_at"),
            "retention": e.get("retention"),
            "sources": e.get("sources"),
            "missing": e.get("missing") or {}})
    return {"total": total, "page": page, "page_size": page_size,
            "items": items, "index_built_at": index.get("built_at"),
            "stats": index.get("stats")}


def get_session(sid):
    """单会话详情：交接链、历次事项（含认领时刻）、运行（含来源与
    stale 标记）、工位、保留类型与缺失原因，附只读对话与工具摘要。"""
    index = load_index()
    if index is None:
        return {"ok": False, "reason": "索引不存在：尚未构建"}
    e = index["sessions"].get(sid)
    if e is None:
        return {"ok": False,
                "reason": "会话不在索引：无本项目已核实来源记录"}
    chain = []
    cur = e.get("predecessor")
    while cur and cur in index["sessions"] and len(chain) < 20:
        chain.append(cur)
        cur = index["sessions"][cur].get("predecessor")
    chain.reverse()
    succ = []
    cur = e.get("successor")
    while cur and cur in index["sessions"] and len(succ) < 20:
        succ.append(cur)
        cur = index["sessions"][cur].get("successor")
    cards = []
    for h in e.get("handovers", []):
        card = {"at": h.get("at"), "from": h.get("from"), "to": h.get("to"),
                "reason": h.get("reason"), "card": h.get("card"),
                "card_excerpt": None, "source": h.get("source")}
        path = h.get("card")
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    card["card_excerpt"] = f.read(400)
            except OSError:
                card["card_excerpt"] = "交接卡文件不可读"
        elif path:
            card["card_excerpt"] = "交接卡文件不存在：%s" % path
        cards.append(card)
    detail = {"ok": True, "session_id": sid, "lane": e.get("lane"),
              "title_head": e.get("title_head"),
              "workers": e.get("workers", {}), "items": e.get("items", {}),
              "runs": e.get("runs", []), "handover_cards": cards,
              "predecessor_chain": chain, "successor_chain": succ,
              "first_seen_at": e.get("first_seen_at"),
              "last_seen_at": e.get("last_seen_at"),
              "retention": e.get("retention"),
              "execution_phase": e.get("execution_phase"),
              "sources": e.get("sources"), "missing": e.get("missing") or {},
              "read_only": True}
    detail["dialog_summary"] = _dialog_summary(sid)
    return detail


def _dialog_summary(sid, limit=5):
    """只读对话与工具摘要。会话库无该会话行时返回明确失败原因，
    不返回正常空摘要。"""
    import sqlite3
    out = {"ok": False, "texts": [], "tool_counts": {}, "reason": None}
    try:
        con = sqlite3.connect("file:" + SESSION_DB.replace("\\", "/") +
                              "?mode=ro", uri=True, timeout=8.0)
        try:
            exists = con.execute(
                "select 1 from session where id=?", (sid,)).fetchone()
            if not exists:
                out["reason"] = "对话源缺失：会话库无该会话行，无法提供摘要"
                return out
            rows = con.execute(
                "select data from part where session_id=? and data like ? "
                "order by id desc limit 40", (sid, '%"type":"text"%')).fetchall()
            texts = []
            for (d,) in rows:
                try:
                    j = json.loads(d)
                except ValueError:
                    continue
                if j.get("type") == "text" and j.get("text"):
                    texts.append(str(j["text"])[:160])
                if len(texts) >= limit:
                    break
            out["texts"] = texts
            for (name, n) in con.execute(
                    "select tool_name, count(*) from tool_usage "
                    "where session_id=? group by tool_name", (sid,)):
                out["tool_counts"][name] = n
            out["ok"] = True
            if not texts and not out["tool_counts"]:
                out["reason"] = "会话行在册但无可读文本与工具记录"
        finally:
            con.close()
    except (sqlite3.Error, OSError) as exc:
        out["reason"] = "对话源读取失败：%s" % repr(exc)[:120]
    return out


def stats():
    index = load_index()
    if index is None:
        return {"ok": False, "reason": "索引不存在：尚未构建"}
    return {"ok": True, **(index.get("stats") or {}),
            "built_at": index.get("built_at"),
            "collection_timeline": index.get("collection_timeline")}
