# -*- coding: utf-8 -*-
"""pending_work.py — I-233 待领工作区后端契约 v2（pending-work-v2）。

v2 相对 v1 的返修要点（Beth 九项反例逐条修复）：
  1. 依赖评估复用 items_registry/dependency_policy.py 的
     unmet_dependencies 实际显式交付判断：覆盖只在
     delivery_dependencies 出现的未交付前置；已批准且版本与清单位
     匹配的交付先行（不把未关闭等同未满足）；撤回影响与授权门槛
     保持。
  2. 内核 last_error 短语不再当作无条件结构化准入来源：依赖类与
     容量类短语按当前同源事实复核（依赖用 dependency_policy、容量
     用当前持槽、冲突用 lane_workers.check_parallel_admission 无副
     作用复算），复核不符即判过期并按当前事实重分类；不可复核的
     短语保留原句但评估有效性随来源新鲜度降级。
  3. 内核源不可读或超过两天未更新（last_tick_at）时评估与容量降级
     （dispatchable 为 null 不报全部可派），快照重新生成不续期来源
     新鲜度，sample_validity 以来源时刻为锚。
  4. 容量区分云端认领、内核持槽与执行实况：云端已认领但本地无对
     上运行时该线保守降级（dispatchable=null）并说明不一致；不一边
     报冲突一边报空闲可派。
  5. 事实事件见 pending_work_events.py（test 通道不作生产认领确认、
     跨事项工位因果、分批完整上行）。
冻结契约与样例：organization/contracts/pending_work_v2/。全部查询
只读；来源路径可环境变量重定向；仅使用标准库。
"""
import datetime
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LP_SYNC = os.path.dirname(os.path.abspath(__file__))

STATE_ROOT = os.environ.get(
    "LP_KERNEL_STATE_ROOT", os.path.join(ROOT, "lp_sync", "kernel_state"))
ROSTER_PATH = os.environ.get(
    "LP_LANE_WORKERS_ROSTER",
    os.path.join(ROOT, "lp_sync", "lane_workers_roster.json"))
ITEMS_FILE = os.environ.get(
    "PW_ITEMS_FILE", os.path.join(ROOT, "items_registry", "items.json"))
ITEMS_SOURCE = os.environ.get("PW_ITEMS_SOURCE", "cloud")   # cloud | file
SAMPLE_MAX_AGE_S = int(os.environ.get("PW_SAMPLE_MAX_AGE_S", "300"))
KERNEL_MAX_AGE_S = 2 * 24 * 3600      # 内核源两天未更新即过期

CONTRACT_VERSION = "pending-work-v3"

_REGSRC = os.path.join(ROOT, "items_registry")
if _REGSRC not in sys.path:
    sys.path.insert(0, _REGSRC)
from dependency_policy import unmet_dependencies          # noqa: E402

CATEGORIES = ("ready", "waiting_conditions", "awaiting_orchestration",
              "needs_verification")

# 内核准入短语 → 代码。v2 起这些短语只是候选原因：依赖/容量/冲突类
# 须经当前事实复核（复核通过才生效），不可复核类随来源新鲜度降级。
KERNEL_REASON_CODES = (
    ("依赖未清：", "DEP_NOT_CLEARED", "waiting_conditions", "recompute_deps"),
    ("上游交付撤回影响尚未复核", "DEP_WITHDRAWN_UNREVIEWED",
     "waiting_conditions", "recompute_withdrawn"),
    ("事项状态", "ITEM_STATUS_NOT_OPEN", "waiting_conditions", "keep"),
    ("同一会话上一回合尚未结束", "SESSION_TURN_BUSY", "waiting_conditions",
     "keep"),
    ("本线在办归属未核", "OWNERSHIP_UNVERIFIED", "waiting_conditions", "keep"),
    ("本线", "LINE_CAPACITY_FULL", "awaiting_orchestration",
     "recompute_capacity"),
    ("可写范围与活跃任务", "RESOURCE_WRITABLE_OVERLAP", "waiting_conditions",
     "recompute_conflict"),
    ("独占资源与活跃任务", "RESOURCE_EXCLUSIVE_CONFLICT",
     "waiting_conditions", "recompute_conflict"),
    ("缺可写范围与独占资源声明", "DECLARATION_MISSING", "waiting_conditions",
     "keep"),
    ("执行模型限流", "RATE_LIMITED_WAIT", "waiting_conditions", "keep"),
    ("连续恢复未成功", "RECOVERY_EXHAUSTED", "needs_verification", "keep"),
    ("派发次数耗尽", "DISPATCH_EXHAUSTED", "needs_verification", "keep"),
)

SCOPE_EXCLUSIONS = ("requirement", "claimed", "pending_review", "closed",
                    "test_channel")


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, ValueError) as exc:
        return default, repr(exc)[:160]


def _file_version(path):
    try:
        st = os.stat(path)
        return {"mtime_ms": int(st.st_mtime * 1000), "size": st.st_size}
    except OSError:
        return None


def _age_s(iso):
    if not iso:
        return None
    try:
        ts = datetime.datetime.fromisoformat(str(iso)[:26])
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.astimezone()
    return max(0.0, (datetime.datetime.now(ts.tzinfo) - ts).total_seconds())


def load_items():
    """正式事项集合。优先云端（唯一权威），失联回退本地只读缓存并
    明确降级；隔离验收可指向夹具文件。返回 (items, source_meta)。"""
    if ITEMS_SOURCE == "file":
        data, err = _read_json(ITEMS_FILE, None)
        items = (data or {}).get("items") if isinstance(data, dict) else data
        return items or [], {"origin": "file", "path": ITEMS_FILE,
                             "read_error": err,
                             "version": _file_version(ITEMS_FILE)}
    import cloud_client
    try:
        snap = cloud_client.request("GET", "/api/agent/snapshot")
        items = snap.get("items") or []
        return items, {"origin": "cloud", "generated_at": snap.get("generated_at"),
                       "count": len(items), "read_error": None}
    except Exception as exc:
        data, err = _read_json(ITEMS_FILE, None)
        items = (data or {}).get("items") if isinstance(data, dict) else data
        return items or [], {"origin": "cloud_unreachable_file_cache",
                             "read_error": repr(exc)[:160],
                             "cache_version": _file_version(ITEMS_FILE),
                             "degraded": True}


def load_kernel_state():
    data, err = _read_json(os.path.join(STATE_ROOT, "state.json"), None)
    return data or {}, err


def load_lane_config():
    data, err = _read_json(os.path.join(STATE_ROOT, "lane_workers.json"), None)
    return (data or {"default": 1, "lanes": {}, "missing": True,
                     "read_error": err}), err


def load_roster():
    data, err = _read_json(ROSTER_PATH, None)
    return (data or {}).get("workers") or {}, err


def _dep_view(item, items, by_id):
    """dependency_policy 同源依赖视图：unmet 与逐项状态。"""
    unmet = unmet_dependencies(item, items)
    deps = []
    for dep in dict.fromkeys(list(item.get("depends_on") or []) +
                             [v.get("item_id") for v in
                              item.get("delivery_dependencies") or []]):
        if not dep:
            continue
        upstream = by_id.get(dep) or {}
        binding = next((v for v in item.get("delivery_dependencies") or []
                        if v.get("item_id") == dep), None)
        deps.append({"item_id": dep,
                     "status": upstream.get("status") or "unknown",
                     "title": upstream.get("title"),
                     "requires": (binding or {}).get("requires"),
                     "unmet": dep in unmet})
    return unmet, deps


def _static_evaluation(item, items, by_id):
    """未入队任务的静态同源评估：依赖用 dependency_policy 实际判定，
    状态与撤回影响同源读取；不做任何副作用。"""
    unmet, deps = _dep_view(item, items, by_id)
    reasons = []
    withdrawn = any(v.get("review_required")
                    for v in item.get("delivery_impact") or [])
    if withdrawn:
        reasons.append({"code": "DEP_WITHDRAWN_UNREVIEWED",
                        "category": "waiting_conditions",
                        "sentence": "上游交付撤回影响待复核，等待依赖重新满足",
                        "provenance": "items.delivery_impact.review_required"})
    if unmet:
        reasons.append({"code": "DEP_NOT_CLEARED", "category": "waiting_conditions",
                        "sentence": "等待依赖满足：%s" % "、".join(unmet),
                        "related": deps,
                        "recovery": "依赖交付获批或事项关闭后自动可派",
                        "provenance": "dependency_policy.unmet_dependencies"
                                      "（与任务线同源实际判定）"})
    if item.get("status") not in ("open", None):
        reasons.append({"code": "ITEM_STATUS_NOT_OPEN", "category": "waiting_conditions",
                        "sentence": "事项状态 %s，等待重读任务线" % item.get("status"),
                        "provenance": "items.status"})
    if reasons:
        category = reasons[0]["category"]
        primary = dict(reasons[0])
    else:
        category = "awaiting_orchestration"
        primary = {"code": "NOT_ENQUEUED", "category": category,
                   "sentence": "尚未进入内核派发队列，等待编排提交",
                   "recovery": "经批准的 task_submit 入队后由调度器安排",
                   "provenance": "kernel_state.tasks 无本事项请求"}
    return category, primary


def _kernel_task_view(task, item, items, by_id, state, lane_limits, ctx):
    """已入队任务视图：内核准入短语须按当前事实复核后生效。"""
    mapped = None
    for prefix, code, category, policy in KERNEL_REASON_CODES:
        text = task.get("last_error") or ""
        if text and (text.startswith(prefix) or prefix in text):
            mapped = {"code": code, "category": category, "policy": policy,
                      "source_text": text}
            break
    stale_note = None
    if mapped and mapped["policy"] == "recompute_deps":
        unmet, deps = _dep_view(item or {"id": task.get("item_id")},
                                items, by_id)
        if not unmet:
            mapped = None
            stale_note = "内核依赖未清短语已过期（当前同源判定无未满足依赖）"
        else:
            mapped["related"] = deps
    elif mapped and mapped["policy"] == "recompute_capacity":
        lane = task.get("lane")
        limit = lane_limits.get(lane)
        occupied = ctx["occupied_by_lane"].get(lane, 0)
        if limit is not None and occupied < limit:
            mapped = None
            stale_note = "内核容量等待短语已过期（当前持槽 %d/%d）" % (
                occupied, limit)
    elif mapped and mapped["policy"] == "recompute_withdrawn":
        still_withdrawn = any(v.get("review_required")
                              for v in (item or {}).get("delivery_impact") or [])
        if not still_withdrawn:
            mapped = None
            stale_note = "内核撤回短语已过期（当前交付影响无待复核项）"
    elif mapped and mapped["policy"] == "recompute_conflict":
        actives = ctx["active_tasks"].get(task.get("lane")) or []
        others = [t for t in actives if t is not task]
        ok = True
        if others:
            try:
                import lane_workers as lw
                ok, _ = lw.check_parallel_admission(task, others)
            except Exception:
                ok = None                     # 无法复核时保留但降级
        if ok is True:
            mapped = None
            stale_note = "内核冲突短语已过期（当前无并行准入冲突）"
    phase = task.get("phase")
    if mapped and mapped.get("policy") == "keep":
        # v3：不可按当前事实复核的准入短语只作诊断文本保留，不显示为
        # 已确认等待或就绪；分类进待核对并注明真正未评估与已评估区别
        category = "needs_verification"
        primary = {"code": "ADMISSION_UNVERIFIED_%s" % mapped["code"],
                   "category": category,
                   "sentence": "内核准入原因无法按当前事实核实（%s），"
                               "待核对" % mapped["code"],
                   "source_text": mapped.get("source_text"),
                   "diagnostic_only": True,
                   "provenance": "kernel_admission_text_unverified"}
        validity = "unverified"
        if stale_note:
            primary = dict(primary)
            primary["superseded_stale_reason"] = stale_note
        mapped = None                  # 不再进入已确认分类
    elif mapped:
        category = mapped["category"]
        primary = {"code": mapped["code"], "category": category,
                   "sentence": mapped.get("source_text"),
                   "source_text": mapped.get("source_text"),
                   "provenance": "kernel_admission_last_error"}
        if mapped.get("related"):
            primary["related"] = mapped["related"]
        validity = "ok"
    elif phase in ("dispatched", "submitted", "result_pending", "uncertain",
                   "retry_wait", "line_pending", "line_wait", "ready"):
        category = "awaiting_orchestration"
        primary = {"code": "IN_QUEUE_%s" % str(phase).upper(),
                   "category": category,
                   "sentence": "已入队，内核相位 %s，等待调度推进" % phase,
                   "provenance": "kernel_state.tasks.phase"}
        validity = "ok"
    else:
        category = "needs_verification"
        primary = {"code": "PHASE_UNKNOWN", "category": category,
                   "sentence": "内核相位 %s 未能归类，待核对" % phase,
                   "provenance": "kernel_state.tasks.phase"}
        validity = "ok"
    if stale_note:
        primary = dict(primary)
        primary["superseded_stale_reason"] = stale_note
    recovery = None
    if task.get("retry_at"):
        recovery = "重试时间 %s 后自动接续" % task["retry_at"]
    elif task.get("line_retry_at"):
        recovery = "任务线复核时间 %s 后自动重读" % task["line_retry_at"]
    elif primary["code"] == "LINE_CAPACITY_FULL":
        recovery = "本线工位释放后自动接续"
    elif primary["code"].startswith("DEP_"):
        recovery = "依赖满足后自动重读任务线"
    return category, primary, recovery, validity


def _capacity_view(state, state_err, state_fresh, lane_cfg, cfg_err,
                   items, active_runs_by_session, items_degraded=False):
    """容量解释 v2：三种占用来源分开核账（内核持槽、云端未对账认领、
    执行实况），来源不可读或过期时 dispatchable=null 不报可派。"""
    reasons = []
    degraded = False
    if items_degraded:
        degraded = True
        reasons.append({
            "source": "items_claims", "severity": "degraded",
            "reason": "云端认领源失联（事项走本地缓存），云端占用无法"
                      "核对：已知内核持槽保留呈现，可派数未知不报"})
    if cfg_err or (lane_cfg or {}).get("missing"):
        degraded = True
        reasons.append({"source": "lane_workers_config", "severity": "degraded",
                        "reason": "并发配置不可读：%s" %
                                  (cfg_err or lane_cfg.get("read_error"))})
    if state_err or not (state or {}).get("last_tick_at"):
        degraded = True
        reasons.append({"source": "kernel_state", "severity": "degraded",
                        "reason": "内核状态不可读，持槽占用无法核对%s" %
                                  ("：" + state_err if state_err else "")})
    elif not state_fresh:
        degraded = True
        reasons.append({
            "source": "kernel_state", "severity": "degraded",
            "reason": "内核状态超过两天未更新（last_tick_at %s），"
                      "占用与容量过期，不报可派" % state.get("last_tick_at")})

    tasks = (state or {}).get("tasks") or {}
    occupied_by_lane = {}
    active_tasks_by_lane = {}
    for t in tasks.values():
        if t.get("phase") in ("completed", "dispatch_failed", "result_failed",
                              "line_rejected", "retired"):
            continue
        lane = t.get("lane")
        active_tasks_by_lane.setdefault(lane, []).append(t)
        if t.get("worker_id"):
            occupied_by_lane[lane] = occupied_by_lane.get(lane, 0) + 1
    # 云端已认领但本地无对上运行的线：保守不可派
    unreconciled_by_lane = {}
    for it in items:
        if it.get("status") != "in_progress" or not it.get("claim_session_id"):
            continue
        if it.get("claim_session_id") in active_runs_by_session:
            continue
        lane = it.get("lane")
        if lane:
            unreconciled_by_lane[lane] = unreconciled_by_lane.get(lane, 0) + 1

    limits = {"default": max(1, int((lane_cfg or {}).get("default", 1) or 1))}
    for lane, v in ((lane_cfg or {}).get("lanes") or {}).items():
        try:
            limits[lane] = max(1, min(2, int(v)))
        except (TypeError, ValueError):
            pass
    by_lane = {}
    total_limit = total_occ = 0
    any_degraded_lane = False
    for lane in ("L%d" % i for i in range(1, 11)):
        cap = limits.get(lane, limits["default"])
        occ = occupied_by_lane.get(lane, 0)
        unrecon = unreconciled_by_lane.get(lane, 0)
        total_limit += cap
        total_occ += occ
        lane_degraded = degraded
        dispatchable = max(0, cap - occ) if not degraded else None
        if unrecon and dispatchable is not None:
            # 云端认领未对账：不能宣称该槽空闲可派
            dispatchable = None
            lane_degraded = True
            reasons.append({
                "source": "cloud_claims", "severity": "degraded", "lane": lane,
                "reason": "云端已认领 %d 项但本地无对应活跃运行，"
                          "占用无法核对，保守不报可派" % unrecon})
        if lane_degraded:
            any_degraded_lane = True
        by_lane[lane] = {"effective_limit": cap,
                         "occupied": occ,
                         "dispatchable": dispatchable,
                         "reserved": None,
                         "occupancy_sources": {
                             "kernel_slots": occ,
                             "cloud_claims_unreconciled": unrecon},
                         "policy_source": "lp_sync/kernel_state/lane_workers.json"
                                          "（I-217 每线并发配置）"}
    global_dispatchable = None
    if not degraded and not any_degraded_lane:
        global_dispatchable = max(0, total_limit - total_occ)
    return {"status": "degraded" if (degraded or any_degraded_lane) else "ok",
            "degraded_reasons": reasons,
            "global": {"effective_limit": total_limit, "occupied": total_occ,
                       "dispatchable": global_dispatchable,
                       "reserved": None,
                       "policy_source": "lp_sync/kernel_state/lane_workers.json"
                                        "（I-217 每线并发配置，全局为十线之和）"},
            "by_lane": by_lane,
            "reservation_policy": {
                "status": "not_set",
                "source": "现行编排内核与工位规则（I-205/I-217）未定义预留策略",
                "note": "reserved 字段为 null 表示未设置预留策略，不是零"},
            "separate_measures_note": (
                "页面并行额度（console_parallel_limit 云端配置）与近五分钟"
                "活跃会话数为独立口径，不与本容量混算；本容量只反映内核"
                "工位槽位约束与云端认领保守核对"),
            "config_read_error": cfg_err}


def _claim_run_conflicts(items, state):
    conflicts = []
    active_runs = {}
    for run in (state.get("runs") or {}).values():
        if run.get("status") == "active" and run.get("item_id"):
            active_runs.setdefault(run["item_id"], []).append(run)
    for item in items:
        iid = item.get("id")
        runs = active_runs.get(iid) or []
        claim_sid = item.get("claim_session_id")
        if len(runs) > 1:
            conflicts.append({
                "item_id": iid, "code": "MULTIPLE_ACTIVE_RUNS",
                "sentence": "同一事项存在 %d 个活跃运行，待核对" % len(runs),
                "runs": [r.get("run_id") for r in runs],
                "provenance": "kernel_state.runs"})
            continue
        if runs:
            run_sid = runs[0].get("session_id")
            if claim_sid and run_sid and claim_sid != run_sid:
                conflicts.append({
                    "item_id": iid, "code": "CLAIM_RUN_SESSION_MISMATCH",
                    "sentence": "认领会话与活跃运行会话不一致，待核对",
                    "claim_session_id": claim_sid, "run_session_id": run_sid,
                    "run_id": runs[0].get("run_id"),
                    "provenance": "items.claim_session_id+kernel_state.runs"})
        elif claim_sid and item.get("status") == "open":
            conflicts.append({
                "item_id": iid, "code": "CLAIM_WITHOUT_RUN",
                "sentence": "事项状态 open 但存在认领会话记录，待核对",
                "claim_session_id": claim_sid,
                "provenance": "items.status+claim_session_id"})
    return conflicts


def build_snapshot():
    """构建一次完整快照。只读全部来源；返回契约 dict。"""
    sampled_at = _now_iso()
    items, items_meta = load_items()
    state, state_err = load_kernel_state()
    lane_cfg, cfg_err = load_lane_config()
    roster, roster_err = load_roster()
    by_id = {i.get("id"): i for i in items if i.get("id")}
    tasks = state.get("tasks") or {}

    state_age = _age_s(state.get("last_tick_at"))
    state_fresh = state_age is not None and state_age <= KERNEL_MAX_AGE_S
    completeness_reasons = []
    if items_meta.get("degraded") or items_meta.get("read_error"):
        completeness_reasons.append(
            {"source": "items", "severity": "degraded",
             "reason": "云端事项源失联，使用本地只读缓存：%s" %
                       items_meta.get("read_error")})
    if state_err or not state.get("last_tick_at"):
        completeness_reasons.append(
            {"source": "kernel_state", "severity": "degraded",
             "reason": "内核状态不可读：%s" % (state_err or "无 last_tick_at")})
    elif not state_fresh:
        completeness_reasons.append(
            {"source": "kernel_state", "severity": "degraded",
             "reason": "内核状态 %.1f 小时未更新（阈值 48 小时），评估与"
                       "容量过期" % ((state_age or 0) / 3600.0)})
    if roster_err:
        completeness_reasons.append(
            {"source": "roster", "severity": "note",
             "reason": "工位注册表不可读：%s" % roster_err})

    active_runs_by_session = {}
    for run in (state.get("runs") or {}).values():
        if run.get("status") == "active" and run.get("session_id"):
            active_runs_by_session[run["session_id"]] = run
    occupied_by_lane = {}
    active_tasks_by_lane = {}
    for t in tasks.values():
        if t.get("phase") in ("completed", "dispatch_failed", "result_failed",
                              "line_rejected", "retired"):
            continue
        active_tasks_by_lane.setdefault(t.get("lane"), []).append(t)
        if t.get("worker_id"):
            occupied_by_lane[t.get("lane")] = occupied_by_lane.get(
                t.get("lane"), 0) + 1
    lane_limits = {}
    try:
        d = max(1, int((lane_cfg or {}).get("default", 1) or 1))
        lane_limits = {"default": d}
        for lane, v in ((lane_cfg or {}).get("lanes") or {}).items():
            lane_limits[lane] = max(1, min(2, int(v)))
    except (TypeError, ValueError):
        lane_limits = {"default": 1}
    kernel_ctx = {"occupied_by_lane": occupied_by_lane,
                  "active_tasks": active_tasks_by_lane}

    exclusions = {k: {"count": 0, "note": ""} for k in SCOPE_EXCLUSIONS}
    exclusions["requirement"]["note"] = "kind=requirement 非执行任务"
    exclusions["claimed"]["note"] = "status=in_progress 且认领一致"
    exclusions["pending_review"]["note"] = "status=pending_review 待审核"
    exclusions["closed"]["note"] = "status=closed 已完成"
    exclusions["test_channel"]["note"] = "kind=test 隔离通道，不进生产待领"
    needs_verification = []
    pending = []
    conflicted_ids = set()

    for it in items:
        iid = it.get("id")
        kind = it.get("kind")
        status = it.get("status")
        if status == "pending_review":
            exclusions["pending_review"]["count"] += 1
            continue
        if status == "closed":
            exclusions["closed"]["count"] += 1
            continue
        if kind is None or kind not in ("task", "requirement", "test"):
            needs_verification.append({
                "item_id": iid, "code": "KIND_UNKNOWN",
                "sentence": "在办范围内历史类别缺失或未知（kind=%r），"
                            "不批改历史记录，待人工核对" % kind,
                "provenance": "items.kind"})
            conflicted_ids.add(iid)
            continue
        if kind == "requirement":
            exclusions["requirement"]["count"] += 1
            continue
        if kind == "test":
            exclusions["test_channel"]["count"] += 1
            continue
        claimed = status in ("in_progress",) and (it.get("claimed_by")
                                                  or it.get("claim_session_id"))
        if claimed:
            exclusions["claimed"]["count"] += 1
            continue
        if status != "open":
            needs_verification.append({
                "item_id": iid, "code": "STATUS_UNCLASSIFIED",
                "sentence": "事项状态 %s 未入待领范围分类，待核对" % status,
                "provenance": "items.status"})
            conflicted_ids.add(iid)
            continue
        pending.append(_item_entry(it, items, by_id, tasks, state,
                                   state_fresh, lane_limits, kernel_ctx))

    conflicts = _claim_run_conflicts(items, state)
    seen_conflict = set()
    for conflict in conflicts:
        iid = conflict["item_id"]
        if iid in seen_conflict:
            continue
        seen_conflict.add(iid)
        needs_verification.append(conflict)
        conflicted_ids.add(iid)
        if (by_id.get(iid) or {}).get("status") == "in_progress":
            exclusions["claimed"]["count"] -= 1
            exclusions["claimed"]["note"] += (
                "（%s 因认领与运行冲突移入待核对）" % iid)

    for entry in pending:
        if entry["item_id"] in conflicted_ids:
            entry["category"] = "needs_verification"
            entry["primary_reason"] = {
                "code": "PENDING_CONFLICT", "category": "needs_verification",
                "sentence": "该事项同时存在待核对冲突，详见待核对清单",
                "provenance": "snapshot_conflict_check"}

    summary = {"total_pending": len(pending),
               "by_category": {c: sum(1 for e in pending
                                      if e["category"] == c)
                               for c in CATEGORIES},
               "exclusions": exclusions,
               "needs_verification_count": len(needs_verification)}
    by_lane = {}
    unassigned = []
    for entry in pending:
        lane = entry["lane"]
        if lane:
            by_lane.setdefault(lane, []).append(entry["item_id"])
        else:
            unassigned.append(entry["item_id"])
    summary["by_lane"] = {lane: {"count": len(ids), "items": ids}
                          for lane, ids in sorted(by_lane.items())}
    summary["unassigned"] = {"count": len(unassigned), "items": unassigned,
                             "note": "未分配路线的正式任务独立入口，"
                                     "不伪装成业务线"}

    items_degraded = bool(items_meta.get("degraded")
                           or items_meta.get("read_error"))
    capacity = _capacity_view(state, state_err, state_fresh, lane_cfg,
                              cfg_err, items, active_runs_by_session,
                              items_degraded)

    # 采样有效期以来源时刻为锚：来源越旧，有效期越早结束；重新生成
    # 快照不续期来源新鲜度
    anchor_candidates = [state.get("last_tick_at")]
    if items_meta.get("generated_at"):
        anchor_candidates.append(items_meta["generated_at"])
    anchor_age = max([_age_s(a) or 0.0 for a in anchor_candidates] or [0.0])
    effective_age = min(anchor_age, SAMPLE_MAX_AGE_S)
    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": None,
        "sampled_at": sampled_at,
        "source_anchor": {"state_last_tick_at": state.get("last_tick_at"),
                          "items_generated_at": items_meta.get("generated_at"),
                          "oldest_source_age_s": round(anchor_age, 1)},
        "sample_validity": {
            "max_age_s": SAMPLE_MAX_AGE_S,
            "anchored_to": "最旧来源时刻（不因重新生成快照续期）",
            "expires_at": datetime.datetime.fromtimestamp(
                time.time() + max(0.0, SAMPLE_MAX_AGE_S - effective_age)
            ).astimezone().isoformat(timespec="seconds")},
        "source_versions": {
            "items": items_meta,
            "kernel_state": {"generation": state.get("generation"),
                             "last_tick_at": state.get("last_tick_at"),
                             "age_s": round(state_age, 1) if state_age is not None else None,
                             "fresh": state_fresh,
                             "tasks": len(tasks),
                             "runs": len(state.get("runs") or {}),
                             "read_error": state_err},
            "lane_workers_config": {"version": _file_version(
                os.path.join(STATE_ROOT, "lane_workers.json")),
                "read_error": cfg_err},
            "roster": {"version": _file_version(ROSTER_PATH),
                       "read_error": roster_err}},
        "completeness": {"status": "complete" if not
                         any(r["severity"] == "degraded"
                             for r in completeness_reasons) else "partial",
                         "reasons": completeness_reasons},
        "summary": summary,
        "items": pending,
        "needs_verification": needs_verification,
        "capacity": capacity,
        "read_only": True}
    digest = hashlib.sha256(json.dumps(
        {"items": sorted(e["item_id"] for e in pending),
         "summary": summary, "sources": snapshot["source_versions"],
         "nv": sorted(n["item_id"] for n in needs_verification)},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    snapshot["snapshot_id"] = "pw-%s-%s" % (
        datetime.datetime.now().strftime("%Y%m%dT%H%M%S"), digest)
    return snapshot


def _item_entry(item, items, by_id, tasks, state, state_fresh, lane_limits,
                kernel_ctx):
    iid = item.get("id")
    kernel_tasks = [(rid, t) for rid, t in tasks.items()
                    if t.get("item_id") == iid]
    requests = []
    first_enqueued = None
    attempts = 0
    worker_id = session_id = run_id = dispatch_req = None
    for rid, task in kernel_tasks:
        requests.append({"request_id": rid, "phase": task.get("phase"),
                         "dispatch_attempts": task.get("dispatch_attempts", 0)})
        first_enqueued = min(first_enqueued or task.get("created_at"),
                             task.get("created_at"))
        attempts += task.get("dispatch_attempts") or 0
        worker_id = worker_id or task.get("worker_id")
        session_id = session_id or task.get("session_id")
        run_id = run_id or task.get("run_id")
        dispatch_req = dispatch_req or task.get("dispatch_request_id")
    if kernel_tasks:
        task0 = kernel_tasks[0][1]
        category, primary, recovery, validity = _kernel_task_view(
            task0, item, items, by_id, state, lane_limits, kernel_ctx)
        primary = dict(primary)
        primary.setdefault("recovery", recovery)
        if not state_fresh:
            validity = "stale"
        evaluation = {"at": task0.get("updated_at") or task0.get("created_at"),
                      "validity": validity,
                      "evaluator": "kernel_admission_outcome_verified",
                      "provenance": "内核准入短语经当前同源事实复核"
                                    "（dependency_policy/lane_workers）"}
    else:
        category, primary = _static_evaluation(item, items, by_id)
        validity = "ok"
        if not state_fresh:
            validity = "stale"
        evaluation = {"at": None, "validity": validity,
                      "evaluator": "static_same_source",
                      "provenance": "dependency_policy 同源静态评估，"
                                    "未经内核实际准入"}
    _unmet, deps = _dep_view(item, items, by_id)
    entry = {
        "item_id": iid, "title": item.get("title"), "kind": item.get("kind"),
        "status": item.get("status"), "lane": item.get("lane"),
        "goal": item.get("goal"), "reviewer": item.get("reviewer"),
        "owner": item.get("owner"),
        "category": category, "primary_reason": primary,
        "related": {"dependencies": deps,
                    "delivery_impact": item.get("delivery_impact") or []},
        "recovery": primary.get("recovery"),
        "evaluation": evaluation,
        "enqueue": {"in_kernel_queue": bool(kernel_tasks),
                    "request_ids": requests, "first_enqueued_at": first_enqueued,
                    "total_dispatch_attempts": attempts},
        "execution_ref": {"worker_id": worker_id, "session_id": session_id,
                          "run_id": run_id,
                          "dispatch_request_id": dispatch_req,
                          "kernel_phase": (kernel_tasks[0][1].get("phase")
                                           if kernel_tasks else None)}}
    return entry


def push_once():
    """把快照推送到控制台只读端点（数据面写，只写快照存储，
    不触发任何调度副作用）。返回 (snapshot, response)。"""
    import cloud_client
    snapshot = build_snapshot()
    resp = cloud_client.request(
        "POST", "/api/console/pending_work/ingest", snapshot)
    return snapshot, resp
