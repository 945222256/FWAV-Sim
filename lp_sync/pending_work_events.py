# -*- coding: utf-8 -*-
"""pending_work_events.py — I-233 调度因果事件 v3（pending-work-v3）。

v3 相对 v2 的返修要点（Beth 真实消费边界反例逐条修复）：
  一、认领确认共同对账：云端认领须与任务在事项、运行、工位与完整
  会话上一致（claim_session_id 与 claim_worker_id 同时匹配；任一字段
  在案而不一致即拒绝确认并保留未知），不再只比会话。
  二、实际开工证据：本地 run.status=active 只是派发受理时内核写入的
  状态，不构成开工事实。run_started 只在读到实际执行消费者（会话库）
  的模型或工具活动证据、且时间不早于本次运行开始时生成；受理、排队
  或本地 active 一律保留未知（result_unknown），不造心跳不升级标签。
  账本 run_started 事件同属受理产物，不再映射为开工事实。
  三、逐批回执核验：push_once 只有在服务端回执与当前批逐项匹配（契约
  版本、after/first/last 游标、数量、批摘要，且 added+deduped==count
  表示全量保存或全量去重）时才推进持久已确认游标；空回执、缺字段、
  部分保存、错批、错版本、返回类型错误与超时未知均不推进，按原请求
  对账或同事件幂等补发。回执丢失但服务端已保存时，重发同批得到全量
  去重确认即可推进。
冻结契约与样例：organization/contracts/pending_work_v3/。仅标准库。
"""
import datetime
import hashlib
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_ROOT = os.environ.get(
    "LP_KERNEL_STATE_ROOT", os.path.join(ROOT, "lp_sync", "kernel_state"))
EVENTS_DIR = os.environ.get(
    "PW_EVENTS_DIR", os.path.join(ROOT, "lp_sync", "pending_work_data"))
EVENTS_PATH = os.path.join(EVENTS_DIR, "events.jsonl")
META_PATH = os.path.join(EVENTS_DIR, "meta.json")
PUSH_STATE_PATH = os.path.join(EVENTS_DIR, "push_state.json")
SESSION_DB = os.environ.get(
    "LP_SESSION_DB",
    os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite"))

CONTRACT_VERSION = "pending-work-v3"
EVENT_TYPES = ("slot_released", "dispatch_selected", "claim_confirmed",
               "run_started", "condition_wait", "recovery", "result_unknown")

# v3：run_started 是受理产物，不映射为开工事实（实际开工见执行证据）
LEDGER_MAP = {
    "lane_slot_released": "slot_released",
    "dispatch_submitted": "dispatch_selected",
    "line_wait": "condition_wait",
    "turn_recovery_resubmit": "recovery",
    "result_failed": "result_unknown",
    "result_action_definitive_failed": "result_unknown",
}


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _event_id(type_, source_kind, source_ref, identity):
    basis = json.dumps({"type": type_, "source_kind": source_kind,
                        "source_ref": source_ref, "identity": identity},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _items_claims():
    """读云端事项认领事实（claim_session_id 与 claim_worker_id）。
    来源不可读返回 (None, err)——认领确认保留未知，不猜。"""
    try:
        import pending_work
        items, meta = pending_work.load_items()
        if meta.get("read_error"):
            return None, meta.get("read_error")
        return {i.get("id"): i for i in items if i.get("id")}, None
    except Exception as exc:
        return None, repr(exc)[:160]


def _ledger_events():
    """读取内核账本派生事件（来源引用与高水位）；test 通道不进生产
    因果流；派发类事件的 item/lane/worker 经内核状态任务表补全。"""
    ledger = _read_json(os.path.join(STATE_ROOT, "ledger.json"),
                        {"events": []})
    events = ledger.get("events") or []
    state = _read_json(os.path.join(STATE_ROOT, "state.json"), {})
    tasks = state.get("tasks") or {}
    by_req = {}
    test_reqs = set()
    for rid, t in tasks.items():
        by_req[rid] = t
        if t.get("kind") == "test":
            test_reqs.add(rid)
    out = []
    for idx, ev in enumerate(events):
        etype = LEDGER_MAP.get(ev.get("event"))
        if etype is None:
            continue
        detail = ev.get("detail") or {}
        ref = ev.get("request_id") or ev.get("ref") or ""
        task = by_req.get(ref) or {}
        if ref in test_reqs or (task.get("kind") == "test"):
            continue
        identity = {"item_id": (detail.get("item_id") if isinstance(detail, dict)
                                else None) or task.get("item_id"),
                    "lane": (detail.get("lane") if isinstance(detail, dict)
                             else None) or task.get("lane"),
                    "run_id": (detail.get("run_id") if isinstance(detail, dict)
                               else None) or task.get("run_id"),
                    "request_id": ref,
                    "worker_id": (detail.get("worker_id")
                                  if isinstance(detail, dict) else None)
                    or task.get("worker_id"),
                    "session_id": task.get("session_id")}
        out.append({
            "event_id": _event_id(etype, "kernel_ledger",
                                  "%s@%s" % (ev.get("event"), ev.get("ts")),
                                  identity),
            "type": etype,
            "source_kind": "kernel_ledger",
            "source_ref": {"ledger_event": ev.get("event"), "ts": ev.get("ts")},
            "occurred_at": ev.get("ts"),
            "identity": identity,
            "state_change": _state_change(etype, detail),
            "fact_source": "lp_sync/kernel_state/ledger.json"})
    return out, len(events)


def _state_change(etype, detail):
    if not isinstance(detail, dict):
        return None
    keys = ("worker_id", "session_id", "run_id", "phase", "reason",
            "retry_at", "error", "lane", "request_id", "detail_head")
    picked = {k: detail[k] for k in keys if k in detail}
    return picked or None


def _execution_evidence(session_id, since_ms):
    """实际执行消费者的开工证据：会话库只读，取该会话在 since 之后
    的模型请求或工具/文本活动。无会话、无证据或库不可读都返回
    (None, 原因)——运行开始保留未知，不造心跳。"""
    if not session_id:
        return None, "无会话身份"
    try:
        con = sqlite3.connect(
            "file:" + SESSION_DB.replace("\\", "/") + "?mode=ro",
            uri=True, timeout=8.0)
        try:
            mu = con.execute(
                "select count(*) from model_usage where session_id=? "
                "and started_at >= ?", (session_id, since_ms or 0)
            ).fetchone()[0]
            part = con.execute(
                "select count(*) from part where session_id=? "
                "and time_created >= ?", (session_id, since_ms or 0)
            ).fetchone()[0]
        finally:
            con.close()
    except (sqlite3.Error, OSError) as exc:
        return None, "执行证据来源不可读：%s" % repr(exc)[:120]
    if mu or part:
        return {"model_requests": mu, "parts": part,
                "since_ms": since_ms}, None
    return None, "会话库无该会话在运行开始后的模型/工具活动"


def _state_events():
    """从内核状态派生认领确认与运行事实（v3 严格化）。

    claim_confirmed：任务非 test、已派发且会话在案，且云端认领与任务
    在事项、完整会话与工位上一致（claim_worker_id 在案时须与任务
    worker_id 相同）、事项状态在办；任一不满足保留未知不确认。
    run_started：本地 active 行只是受理状态；须另读执行消费者证据
    （会话库模型/工具活动不早于运行开始）才生成，否则 result_unknown。
    """
    state = _read_json(os.path.join(STATE_ROOT, "state.json"), {})
    tasks = state.get("tasks") or {}
    runs = state.get("runs") or {}
    claims_by_item, claims_err = _items_claims()
    out = []
    for rid, task in tasks.items():
        if task.get("kind") == "test":
            continue                      # 隔离通道无生产认领事实
        if not (task.get("dispatched_at") and task.get("session_id")):
            continue
        item = (claims_by_item or {}).get(task.get("item_id"))
        claim_ok = False
        claim_note = None
        if claims_by_item is None:
            claim_note = "认领事实来源不可读（%s），保留未知" % claims_err
        elif item is None:
            claim_note = "云端无该事项认领记录，保留未知"
        elif item.get("claim_session_id") != task.get("session_id"):
            claim_note = "云端认领会话与任务会话不一致，保留未知"
        elif item.get("status") not in ("in_progress",):
            claim_note = "云端事项状态 %s 非在办认领" % item.get("status")
        else:
            claim_worker = item.get("claim_worker_id")
            task_worker = task.get("worker_id")
            if claim_worker and task_worker and claim_worker != task_worker:
                claim_note = ("云端认领工位 %s 与任务工位 %s 不一致，"
                              "拒绝假确认保留未知" % (claim_worker,
                                                       task_worker))
            else:
                claim_ok = True
        identity = {"item_id": task.get("item_id"),
                    "lane": task.get("lane"),
                    "worker_id": task.get("worker_id"),
                    "session_id": task.get("session_id"),
                    "run_id": task.get("run_id"),
                    "request_id": rid}
        if claim_ok:
            out.append({
                "event_id": _event_id("claim_confirmed", "kernel_task",
                                      rid, identity),
                "type": "claim_confirmed",
                "source_kind": "kernel_task",
                "source_ref": {"task_req_id": rid,
                               "field": "dispatched_at+cloud_claim"},
                "occurred_at": task.get("dispatched_at"),
                "identity": identity,
                "state_change": {"session_id": task.get("session_id"),
                                 "worker_id": task.get("worker_id")},
                "fact_source": "kernel_state.state.json#tasks + 云端认领"
                               "（会话与工位共同对账）"})
        elif claim_note:
            out.append({
                "event_id": _event_id("result_unknown", "kernel_task_claim",
                                      rid, identity),
                "type": "result_unknown",
                "source_kind": "kernel_task_claim",
                "source_ref": {"task_req_id": rid,
                               "field": "claim_confirmation"},
                "occurred_at": task.get("dispatched_at"),
                "identity": identity,
                "state_change": None,
                "fact_source": "认领确认保留未知：" + claim_note})
    for run_id, run in runs.items():
        task = tasks.get(run.get("task_req_id")) or {}
        if task.get("kind") == "test":
            continue
        if not (run.get("started_at") and run.get("session_id")
                and run.get("worker_id")
                and run.get("status") in ("active", "ended")):
            continue
        started_ms = None
        try:
            ts = datetime.datetime.fromisoformat(
                str(run.get("started_at"))[:26])
            if ts.tzinfo is None:
                ts = ts.astimezone()
            started_ms = int(ts.timestamp() * 1000)
        except ValueError:
            pass
        evidence, ev_err = _execution_evidence(run.get("session_id"),
                                               started_ms)
        identity = {"item_id": run.get("item_id"),
                    "lane": task.get("lane") or run.get("lane"),
                    "worker_id": run.get("worker_id"),
                    "session_id": run.get("session_id"),
                    "run_id": run_id,
                    "request_id": run.get("dispatch_request_id")}
        if evidence:
            out.append({
                "event_id": _event_id("run_started", "kernel_run+execution",
                                      run_id, identity),
                "type": "run_started",
                "source_kind": "kernel_run+execution",
                "source_ref": {"run_id": run_id},
                "occurred_at": run.get("started_at"),
                "identity": identity,
                "state_change": {"status": run.get("status"),
                                 "worker_id": run.get("worker_id"),
                                 "execution_evidence": evidence},
                "fact_source": "内核运行记录 + 会话库实际执行证据"
                               "（模型/工具活动）"})
        else:
            out.append({
                "event_id": _event_id("result_unknown", "kernel_run_start",
                                      run_id, identity),
                "type": "result_unknown",
                "source_kind": "kernel_run_start",
                "source_ref": {"run_id": run_id},
                "occurred_at": run.get("started_at"),
                "identity": identity,
                "state_change": None,
                "fact_source": "运行开始保留未知（%s）：本地 active 只是"
                               "受理状态，未读到实际执行证据" % ev_err})
    return out


def _causal_link(events):
    """因果关联 v3：只按工位转移建立因果（同 v2 语义）。

    同工位（lane+worker_id 皆在案）的 slot_released 与其后跨事项的
    dispatch_selected 互为因果；同一释放后同事项只首次选择建立因果
    （重试不建）；同事项邻接、换工位、两工位交错不建立因果。"""
    by_worker = {}
    for ev in events:
        ident = ev.get("identity") or {}
        lane = ident.get("lane")
        worker = ident.get("worker_id")
        if lane and worker:
            by_worker.setdefault((lane, worker), []).append(ev)
    for group in by_worker.values():
        group.sort(key=lambda e: (e.get("occurred_at") or "", e["event_id"]))
        last_release = None
        selected_since_release = set()
        for ev in group:
            if ev["type"] == "slot_released":
                last_release = ev
                selected_since_release = set()
            elif ev["type"] == "dispatch_selected":
                prev_item = (last_release or {}).get(
                    "identity", {}).get("item_id")
                this_item = (ev.get("identity") or {}).get("item_id")
                if (last_release and prev_item and this_item
                        and prev_item != this_item
                        and this_item not in selected_since_release):
                    ev["causes"] = {
                        "event_id": last_release["event_id"],
                        "relation": "same_worker_previous_item_release"}
                    last_release["triggers"] = {
                        "event_id": ev["event_id"],
                        "relation": "same_worker_next_item_selection"}
                if this_item:
                    selected_since_release.add(this_item)
    return events


def rebuild(persist=True):
    """从来源重建事件集并与持久存储合并（按 event_id 去重，游标稳定
    递增；重复派生、重启与断线恢复不产生重复计数）。"""
    ledger_events, ledger_len = _ledger_events()
    events = _causal_link(ledger_events + _state_events())
    events.sort(key=lambda e: (e.get("occurred_at") or "", e["event_id"]))
    existing = {}
    meta = _read_json(META_PATH, {"last_cursor": 0,
                                  "seen_high_water": 0})
    if os.path.exists(EVENTS_PATH):
        with open(EVENTS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                existing[rec["event_id"]] = rec
    changed = 0
    for ev in events:
        if ev["event_id"] in existing:
            continue
        meta["last_cursor"] = meta.get("last_cursor", 0) + 1
        ev["cursor"] = meta["last_cursor"]
        existing[ev["event_id"]] = ev
        changed += 1
    gaps = []
    if ledger_len < meta.get("seen_high_water", 0):
        gaps.append({"source": "kernel_ledger",
                     "reason": "账本长度由 %d 缩至 %d，可能截断或轮转，"
                               "历史事件以持久存储为准" %
                     (meta.get("seen_high_water"), ledger_len)})
    meta["seen_high_water"] = max(meta.get("seen_high_water", 0), ledger_len)
    meta["rebuilt_at"] = _now_iso()
    meta["event_count"] = len(existing)
    if persist and changed:
        os.makedirs(EVENTS_DIR, exist_ok=True)
        tmp = EVENTS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for ev in sorted(existing.values(), key=lambda e: e["cursor"]):
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        os.replace(tmp, EVENTS_PATH)
        tmp_meta = META_PATH + ".tmp"
        with open(tmp_meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        os.replace(tmp_meta, META_PATH)
    return {"events": sorted(existing.values(), key=lambda e: e["cursor"]),
            "meta": meta, "gaps": gaps}


def read_events(after_cursor=0):
    """增量读取：返回游标之后的事件、下一游标与漏段核对。"""
    result = rebuild(persist=True)
    events = [e for e in result["events"] if e["cursor"] > after_cursor]
    return {"contract_version": CONTRACT_VERSION,
            "events": events,
            "next_cursor": result["meta"].get("last_cursor", 0),
            "total_events": result["meta"].get("event_count", 0),
            "gaps": result["gaps"],
            "dedup_key": "event_id"}


PUSH_BATCH = 500


def _batch_digest(batch):
    basis = "".join(str(e.get("event_id")) for e in batch)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _load_push_state():
    st = _read_json(PUSH_STATE_PATH, None) or {}
    return {"confirmed_cursor": int(st.get("confirmed_cursor") or 0)}


def _save_push_state(confirmed):
    os.makedirs(EVENTS_DIR, exist_ok=True)
    tmp = PUSH_STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"confirmed_cursor": int(confirmed),
                   "updated_at": _now_iso()}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PUSH_STATE_PATH)


def _verify_receipt(resp, payload, batch):
    """逐批回执核验：回执必须与当前批逐项匹配且全量保存或全量去重，
    否则返回不匹配原因（不推进游标）。空、缺字段、部分、错批、错
    版本与类型错误均不通过。"""
    if not isinstance(resp, dict):
        return "回执类型错误：%r" % type(resp).__name__
    if resp.get("ok") is False:
        return "服务端明确拒绝：%r" % (resp.get("result")
                                       or resp.get("error"))
    r = resp.get("result") if isinstance(resp.get("result"), dict) else resp
    if not isinstance(r, dict):
        return "回执缺少可核验结果体"
    required = ("contract_version", "after_cursor", "first_cursor",
                "last_cursor", "count", "batch_digest", "added", "deduped")
    missing = [k for k in required if k not in r]
    if missing:
        return "回执缺字段：%s" % "、".join(missing)
    if r.get("contract_version") != payload.get("contract_version"):
        return "回执契约版本不匹配：%r" % r.get("contract_version")
    if r.get("after_cursor") != payload.get("after_cursor"):
        return "回执 after_cursor 不匹配：%r≠%r" % (
            r.get("after_cursor"), payload.get("after_cursor"))
    if r.get("first_cursor") != batch[0].get("cursor"):
        return "回执首游标不匹配"
    if r.get("last_cursor") != batch[-1].get("cursor"):
        return "回执末游标不匹配"
    if r.get("count") != len(batch):
        return "回执数量不匹配：%r≠%d" % (r.get("count"), len(batch))
    if r.get("batch_digest") != _batch_digest(batch):
        return "回执批摘要不匹配"
    if (r.get("added") or 0) + (r.get("deduped") or 0) != len(batch):
        return "回执非全量保存或全量去重（added=%r deduped=%r 批量=%d）" \
               % (r.get("added"), r.get("deduped"), len(batch))
    return None


def _verify_receipt_legacy(resp, batch):
    """遗留计数回执（无 v3 身份字段的服务端）：仅当回执为 dict、无
    错误标记、added（或 added+deduped）与当前批量全等时按计数确认
    （契约允许范围/数量或摘要确认）；空、缺计数、部分、错版本与
    类型错误仍拒绝。返回 None 表示接受，否则返回原因。"""
    if not isinstance(resp, dict):
        return "回执类型错误：%r" % type(resp).__name__
    if resp.get("ok") is False:
        return "服务端明确拒绝"
    if "batch_digest" in resp or "first_cursor" in resp:
        # 带 v3 身份字段的回执必须通过完整核验；核验失败（如被篡改）
        # 不落入计数兜底
        return "v3 形态回执未通过完整核验"
    if resp.get("contract_version") not in (None, CONTRACT_VERSION):
        return "回执契约版本不匹配：%r" % resp.get("contract_version")
    r = resp.get("result") if isinstance(resp.get("result"), dict) else resp
    if not isinstance(r, dict) or "added" not in r:
        return "回执缺少计数确认字段"
    total = (r.get("added") or 0) + (r.get("deduped") or 0)
    if total != len(batch):
        return "回执计数与批量不一致（%r≠%d）" % (total, len(batch))
    return None


def push_once(limit=PUSH_BATCH, max_batches=50):
    """分批完整上行（v3）：从已确认游标增量取事件，按批上行，仅当
    回执与当前批逐项核验通过才推进持久游标；失败/未知不推进（原请求
    对账或同事件幂等补发）。来源 next_cursor 低于已确认游标报漏段。"""
    import cloud_client
    state = _load_push_state()
    confirmed = state["confirmed_cursor"]
    report = {"contract_version": CONTRACT_VERSION,
              "confirmed_cursor_before": confirmed,
              "batches": [], "failures": [], "gaps": [],
              "confirmed_cursor_after": confirmed,
              "dedup_key": "event_id"}
    for _ in range(max_batches):
        result = read_events(confirmed)
        pending = [e for e in (result.get("events") or [])
                   if (e.get("cursor") or 0) > confirmed]
        source_next = result.get("next_cursor") or confirmed
        if source_next < confirmed:
            report["gaps"].append({
                "source": "event_store",
                "reason": "来源 next_cursor(%d) 低于已确认游标(%d)，"
                          "事件存储可能截断，缺失范围需回补" %
                          (source_next, confirmed)})
        if not pending:
            if result.get("gaps"):
                report["gaps"].extend(result["gaps"])
            break
        batch = pending[:limit]
        payload = {"contract_version": CONTRACT_VERSION,
                   "after_cursor": confirmed,
                   "events": batch,
                   "next_cursor": batch[-1].get("cursor"),
                   "total_events": result.get("total_events"),
                   "gaps": result.get("gaps") or []}
        try:
            resp = cloud_client.request(
                "POST", "/api/console/pending_work/events/ingest", payload)
        except Exception as exc:
            report["failures"].append({"after_cursor": confirmed,
                                       "error": repr(exc)[:200],
                                       "note": "回执未知，未推进已确认游标，"
                                               "按原请求对账或幂等补发"})
            break
        mismatch = _verify_receipt(resp, payload, batch)
        receipt_mode = "full_identity"
        if mismatch is not None:
            legacy_mismatch = _verify_receipt_legacy(resp, batch)
            if legacy_mismatch is None:
                mismatch = None
                receipt_mode = "legacy_count"
        if mismatch is not None:
            report["failures"].append({"after_cursor": confirmed,
                                       "reason": mismatch,
                                       "response": resp
                                       if isinstance(resp, (dict, list))
                                       else str(resp)})
            break
        batch_max = batch[-1].get("cursor")
        report["batches"].append({
            "after_cursor": confirmed, "count": len(batch),
            "first_cursor": batch[0].get("cursor"),
            "last_cursor": batch_max,
            "verified_receipt": receipt_mode})
        confirmed = batch_max
        _save_push_state(confirmed)
        if len(pending) <= limit:
            break
    report["confirmed_cursor_after"] = confirmed
    return report
