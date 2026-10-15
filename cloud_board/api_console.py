# -*- coding: utf-8 -*-
"""api_console.py — 编排控制台云端模块（I-142，第一期数据面）。

三张表：console_sessions 会话快照、console_events 控制台事件、
console_tasks 下查任务。四个接口：ingest 上行、state 页面状态、
source 下查登记、action 干预记录。共用既有服务进程与端口，
鉴权沿用 api_auth（上行走代理令牌，页面走会话或令牌）。

出处指针纪律（方案草案第七节）：接口返回的每一格数据携带出处
指针，四字段为种类（表、文件、接口）、定位、摘录（不超过二百字）、
采样时刻。页面每格渲染出处标记，点击开出处抽屉。
仅使用 Python 标准库。
"""
import datetime
import hashlib
import os
import json

import auth
import db
import repo
import router
from httpd import ApiError

_SOURCE_FIELDS = ("kind", "locus", "excerpt", "sampled_at")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _public_dt(value):
    """Old console timestamps were emitted by the Shanghai host without offsets."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    return parsed


def _public_iso(value):
    parsed = _public_dt(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def _valid_source(src):
    if not isinstance(src, dict) or not all(k in src for k in _SOURCE_FIELDS):
        raise ApiError("出处指针须含四字段：%s" % list(_SOURCE_FIELDS))
    if len(str(src.get("excerpt") or "")) > 200:
        raise ApiError("出处摘录不得超过二百字")
    return src


@router.route("POST", "/api/console/ingest")
def ingest(ctx):
    """采集代理上行会话快照（I-142 判据通道）。

    包体 {"sessions": [{session_id, lane, item_id, status,
    context_pct, last_activity_at, archived, archive_reason,
    source}], "parallel_limit": int}。逐行校验出处指针后整行
    覆盖写；返回受纳行数与服务器时间。
    """
    _, _lanes = auth.caller(ctx)
    sessions = ctx.body.get("sessions") or []
    if not isinstance(sessions, list) or not sessions:
        raise ApiError("sessions 须为非空列表")
    limit = ctx.body.get("parallel_limit")
    now = _now()
    n = 0
    with db.transaction() as conn:
        for s in sessions:
            sid = (s.get("session_id") or "").strip()
            if not sid:
                raise ApiError("session_id 不能为空")
            _valid_source(s.get("source"))
            conn.execute(
                "INSERT OR REPLACE INTO console_sessions "
                "(session_id, lane, item_id, status, context_pct, "
                " last_activity_at, archived, archive_reason, source, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, s.get("lane"), s.get("item_id"), s.get("status"),
                 s.get("context_pct"), s.get("last_activity_at"),
                 1 if s.get("archived") else 0, s.get("archive_reason"),
                 json.dumps(s.get("source"), ensure_ascii=False), now))
            n += 1
        # I-166：并行上限仅在云端尚无配置时以代理兜底默认引导一次，
        # 之后归页面设置管辖（实时额度分子由 state 接口按口径现算）。
        if isinstance(limit, int) and repo.get_config("console_parallel_limit") is None:
            repo.set_config(conn, "console_parallel_limit", limit)
    return {"ok": True, "result": {"ingested": n, "server_at": now}}


def _ptr(kind, locus, excerpt, sampled_at):
    """出处指针构造（四字段齐全的统一形态）。"""
    return {"kind": kind, "locus": locus,
            "excerpt": str(excerpt)[:200], "sampled_at": _public_iso(sampled_at)}


@router.route("GET", "/api/console/state")
def state(ctx):
    """页面状态接口（I-142 判据：覆盖三个页面所需全部格子）。

    返回四节：goals（指挥部页目标需求事项三级链）、lanes（战情室
    任务线）、sessions（观察窗会话卡与归档抽屉）、footer（并行额度
    与采样时刻）。每个数据节点携带 source 出处指针。
    """
    auth.caller(ctx)
    now = _now()
    items = repo.all_items()

    # ── 指挥部页：目标 → 需求 → 事项 三级链 ──
    goals_cfg = repo.get_config("goals") or {}
    # 心跳镜像侧 goals 可能存为数组形态（api_agent 上行 get("goals") 列表），
    # 双形态兼容：数组直接用，对象取 goals 键
    if isinstance(goals_cfg, list):
        goals_cfg = {"goals": goals_cfg}
    requirements = [i for i in items if i.get("kind") == "requirement"]
    by_goal = {}
    for r in requirements:
        by_goal.setdefault(r.get("goal") or "未挂目标", []).append(r)
    goals_out = []
    for g in (goals_cfg.get("goals") or []):
        gid = g.get("id")
        reqs = []
        for r in by_goal.get(gid, []):
            enabled = [i for i in items if i["id"] in (r.get("enables") or [])]
            reqs.append({
                "id": r["id"], "title": r["title"], "status": r["status"],
                "enables": [{"id": e["id"], "title": e["title"],
                             "status": e["status"],
                             "source": _ptr("表", "items/" + e["id"],
                                            e["title"][:80], now)}
                            for e in enabled],
                "source": _ptr("表", "items/" + r["id"],
                               "%s（需求，挂 %s）" % (r["title"][:60], gid), now),
            })
        goals_out.append({
            "id": gid, "text": g.get("text", ""), "status": g.get("status"),
            "requirements": reqs,
            "source": _ptr("文件", "items_registry/goals.json",
                           g.get("text", "")[:80], now),
        })

    # ── 战情室：任务线 + 在办事项 + 会话点 ──
    lanes_cfg = repo.get_config("lanes") or {}
    live_sessions = db.query(
        "SELECT * FROM console_sessions WHERE archived=0")
    sess_by_lane = {}
    for s in live_sessions:
        sess_by_lane.setdefault(s["lane"], []).append(s["session_id"])
    lanes_out = []
    for lid, lconf in lanes_cfg.items():
        lane_items = [i for i in items
                      if i.get("lane") == lid and i["status"] != "closed"]
        cards = []
        for i in lane_items:
            crit = i.get("criteria") or []
            cards.append({
                "id": i["id"], "title": i["title"], "status": i["status"],
                "progress": {"verified": sum(1 for c in crit if c.get("verified")),
                             "total": len(crit)} if crit else None,
                "lease_until": i.get("lease_until"),
                "source": _ptr("表", "items/" + i["id"], i["title"][:80], now),
            })
        lanes_out.append({
            "id": lid, "name": lconf.get("name", ""),
            "items": cards,
            "active_sessions": sess_by_lane.get(lid, []),
            "source": _ptr("文件", "items_registry/lanes.json#" + lid,
                           lconf.get("name", ""), now),
        })

    # ── 观察窗：会话卡（活跃 + 保留 + 归档） ──
    all_sessions = db.query(
        "SELECT * FROM console_sessions ORDER BY updated_at DESC")
    cards_out, retained_out, archived_out, owner_out = [], [], [], []
    # I-166：活跃行新鲜度过滤——采集只推三十分钟窗口，云上行超过
    # 三十五分钟未被刷新的行视为过期出清（防静默会话永久占卡与
    # 旧挂接冒充活跃）；归档行不受限。
    fresh_cut = (datetime.datetime.now()
                 - datetime.timedelta(minutes=35)).isoformat(timespec="seconds")
    # I-207：当前事项认领核实——认领事件（I-174 会话号自报）是当前
    # 事项的权威来源；console_sessions.item_id 来自采集侧派发登记册
    # 快照，只作历史来源，不得覆盖新认领。
    claim_item = {}
    for ev in repo.all_events():
        if ev.get("action") != "claim":
            continue
        detail = ev.get("detail") or ""
        mark = detail.find("会话 sess_")
        if mark < 0:
            continue
        sid = detail[mark + 3:].split("，")[0].strip()
        if sid.startswith("sess_") and ev.get("item_id"):
            claim_item[sid] = ev["item_id"]      # 事件流本身按时间正序推进
    for s in all_sessions:
        src = json.loads(s["source"])
        card = {
            "session_id": s["session_id"], "lane": s["lane"],
            "item_id": s["item_id"], "status": s["status"],
            "current_item_id": claim_item.get(s["session_id"]),
            "context_pct": s["context_pct"],
            "last_activity_at": _public_iso(s["last_activity_at"]),
            "updated_at": _public_iso(s["updated_at"]),
            "group": "owner",   # I-174：默认机主会话组，施工证据在册才归施工组
            "source": _ptr("表", "console_sessions/" + s["session_id"],
                           src.get("excerpt", ""), src.get("sampled_at", now)),
        }
        # I-174 映射权威来源：认领记录（认领事件 detail 带会话号）或
        # 派发登记册（I-143 三元组）。两处在册才归施工组，否则机主组。
        _reg_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "..", "lp_sync",
            "dispatch_registry.json")
        _in_registry = False
        try:
            _in_registry = s["session_id"] in json.load(
                open(_reg_path, encoding="utf-8"))
        except Exception:
            _in_registry = False
        card["_is_worker"] = bool(
            (s["lane"] and s["item_id"]) or _in_registry)
        if s["archived"]:
            archived_out.append(card)
        elif s["updated_at"] >= fresh_cut:
            (cards_out if card["_is_worker"] else owner_out).append(card)
        else:
            # I-207：超时未归档行进保留节（灰态驻留依据）——行本体与
            # 最近采样读数保留，页面按超时保留表达；不计入活跃额度。
            retained_out.append(card)

    # ── 页脚：并行额度与采样时刻 ──
    # I-166：额度分子只计实时活跃会话——最近五分钟有活动（驱动中的
    # 会话必有模型请求活动，故活动口径已覆盖驱动在册情形；纯状态字
    # 段不参与计数，防陈旧行冒充活跃，Q-075 幽灵行教训）；上限读配
    # 置（页面可设并留痕）。
    limit = repo.get_config("console_parallel_limit") or 15
    realtime = 0
    for c in cards_out:
        if c["last_activity_at"]:
            try:
                age = (datetime.datetime.now(datetime.timezone.utc)
                       - _public_dt(c["last_activity_at"])).total_seconds()
                if 0 <= age <= 300:
                    realtime += 1
            except ValueError:
                pass
    footer = {
        "parallel": {"active": realtime, "tracked": len(cards_out), "limit": limit},
        "sampled_at": _public_iso(max((s["updated_at"] for s in all_sessions),
                                      default=now)),
        "source": _ptr("表", "console_sessions 实时口径",
                       "实时活跃 %d（近五分钟有活动），在跟踪 %d，上限 %d"
                       % (realtime, len(cards_out), limit), now),
    }
    return {"ok": True, "result": {
        "goals": goals_out, "lanes": lanes_out,
        "lane_queues": repo.get_config("lane_queue_snapshot", {"sampled_at": None, "queues": {}}),
        "sessions": {"active": cards_out, "retained": retained_out,
                     "archived": archived_out, "owner": owner_out},
        "footer": footer,
    }}


@router.route("POST", "/api/console/source")
def source(ctx):
    """下查登记（方案第七节第二类追溯）：云端登记查询任务，
    采集代理下周期取走，本机只读查询后回填摘录，页面轮询取结果。"""
    auth.caller(ctx)
    qkind = ctx.body.get("query_kind") or ""
    if qkind not in ("transcript", "file", "session_row"):
        raise ApiError("query_kind 只允许 transcript、file、session_row")
    locus = (ctx.body.get("locus") or "").strip()
    if not locus:
        raise ApiError("locus 不能为空（表行定位或文件路径）")
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO console_tasks (created_at, requested_by, query_kind,"
            " session_id, locus, status) VALUES (?,?,?,?,?, 'pending')",
            (_now(), auth.caller(ctx)[0], qkind,
             ctx.body.get("session_id"), locus[:300]))
        task_id = cur.lastrowid
    return {"ok": True, "result": {"task_id": task_id,
                                   "status": "pending",
                                   "note": "采集代理下一周期取走，上限三十秒"}}


@router.route("GET", "/api/console/task")
def task_query(ctx):
    """页面出处抽屉轮询（I-148）：按下查任务编号取状态与摘录。

    pending 为代理尚未取走（页面继续等待，上限三十秒）；done 携带
    摘录；failed 携带失败原因。返回即时，无长连接。"""
    auth.caller(ctx)
    q = ctx.query
    import re as _re
    m = _re.search(r"task_id=([0-9]+)", q)
    if not m:
        raise ApiError("缺少 task_id 查询参数")
    row = db.query_one("SELECT * FROM console_tasks WHERE task_id=?", (int(m.group(1)),))
    if row is None:
        raise ApiError("下查任务不存在")
    return {"ok": True, "result": {
        "task_id": row["task_id"], "status": row["status"],
        "query_kind": row["query_kind"], "locus": row["locus"],
        "excerpt": row["excerpt"],
        "elapsed_hint": "上限三十秒" if row["status"] in ("pending", "taken") else "已完成",
    }}


@router.route("POST", "/api/console/action")
def action(ctx):
    """干预动作（I-147）：台账记录加执行任务下发。

    页面确认后的动作写 console_events 台账；inject 与 stop 同时落一条
    console_tasks 动作任务（query_kind=action，locus 存执行载荷），
    采集代理五秒内取走经宿主服务执行，结果经
    /api/console/tasks/result 回填。"""
    actor, _lanes = auth.caller(ctx)
    act = (ctx.body.get("action") or "").strip()
    if act not in ("inject", "stop", "archive", "switch", "note"):
        raise ApiError("action 只允许 inject、stop、archive、switch、note")
    reason = (ctx.body.get("reason") or "").strip()
    if not reason:
        raise ApiError("干预动作必须附理由")
    session_id = ctx.body.get("session_id")
    now = _now()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO console_events (ts, actor, action, session_id, detail)"
            " VALUES (?,?,?,?,?)",
            (now, actor, act, session_id, reason[:400]))
        task_id = None
        if act in ("inject", "stop") and session_id:
            payload = json.dumps({"op": act,
                                  "message": (ctx.body.get("message") or "").strip()},
                                 ensure_ascii=False)
            cur = conn.execute(
                "INSERT INTO console_tasks (created_at, requested_by, query_kind,"
                " session_id, locus, status) VALUES (?,?,?,?,?, 'pending')",
                (now, actor, "action", session_id, payload))
            task_id = cur.lastrowid
    return {"ok": True, "result": {"recorded": act, "task_id": task_id,
                                    "note": "采集代理五秒内取走执行" if task_id else "仅记录"}}


@router.route("GET", "/api/console/tasks")
def tasks(ctx):
    """任务通道：claim=1 为采集代理认领（pending → taken，防重复执行），
    不带 claim 为只读巡查（近五十条全状态，验收与页面用）。"""
    auth.caller(ctx)
    claim = ctx.query.split("claim=1") if "claim=1" in ctx.query else None
    if claim is not None:
        rows = db.query("SELECT * FROM console_tasks"
                             " WHERE status='pending' ORDER BY task_id LIMIT 10")
        ids = [r["task_id"] for r in rows]
        if ids:
            marks = ",".join("?" * len(ids))
            with db.transaction() as conn:
                conn.execute("UPDATE console_tasks SET status='taken', taken_at=?"
                             " WHERE task_id IN (%s)" % marks,
                             (_now(), *ids))
        return {"ok": True, "result": [dict(r) for r in rows]}
    rows = db.query("SELECT * FROM console_tasks"
                         " ORDER BY task_id DESC LIMIT 50")
    return {"ok": True, "result": [dict(r) for r in rows]}


@router.route("GET", "/api/console/config")
def get_config_ep(ctx):
    """守门与额度配置读取（I-149/I-166）。"""
    auth.caller(ctx)
    return {"ok": True, "result": {
        "handoff_mode": repo.get_config("console_gate_mode", "auto"),
        "gate_pct": 70,
        "parallel_limit": repo.get_config("console_parallel_limit", 15),
    }}


@router.route("POST", "/api/console/config")
def set_config_ep(ctx):
    """守门与额度配置写入（机主会话专用），全部留痕。

    handoff_mode：auto（自动切换）、confirm（切前需机主确认）、off（停用）。
    parallel_limit：并行额度上限（I-166：页面可设，事件台账留痕）。"""
    user = auth.session_user(ctx)
    if not user:
        raise ApiError("配置只允许机主会话调整", 403)
    mode = ctx.body.get("handoff_mode")
    limit = ctx.body.get("parallel_limit")
    if mode is None and limit is None:
        raise ApiError("至少提供 handoff_mode 或 parallel_limit 之一")
    with db.transaction() as conn:
        if mode is not None:
            mode = (mode or "").strip()
            if mode not in ("auto", "confirm", "off"):
                raise ApiError("handoff_mode 只允许 auto、confirm、off")
            repo.set_config(conn, "console_gate_mode", mode)
            repo.append_event(conn, "note", item_id=None, actor=user,
                              detail="切换总开关调整为 %s" % mode)
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                raise ApiError("parallel_limit 须为整数")
            if not 1 <= limit <= 100:
                raise ApiError("parallel_limit 须在 1 至 100 之间")
            old = repo.get_config("console_parallel_limit", 15)
            repo.set_config(conn, "console_parallel_limit", limit)
            repo.append_event(conn, "note", item_id=None, actor=user,
                              detail="并行额度上限由 %s 调整为 %d" % (old, limit))
    return {"ok": True, "result": {
        "handoff_mode": repo.get_config("console_gate_mode", "auto"),
        "parallel_limit": repo.get_config("console_parallel_limit", 15),
    }}


@router.route("POST", "/api/console/message")
def post_message(ctx):
    """指挥部消息通道（I-158）：成员按路线互发留痕消息。

    每条消息写 console_messages 消息表（发送方、接收方、时刻、
    关联事项号、正文五要素），并同步写一条 console_events 台账
    （判据：全部消息入事件台账可查）。"""
    actor, _lanes = auth.caller(ctx)
    recipient = (ctx.body.get("recipient") or "").strip()
    body = (ctx.body.get("body") or "").strip()
    item_id = (ctx.body.get("item_id") or "").strip() or None
    if not recipient:
        raise ApiError("接收方路线不能为空")
    if not body:
        raise ApiError("消息正文不能为空")
    if len(body) > 2000:
        raise ApiError("消息正文不得超过两千字")
    now = _now()
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO console_messages (ts, sender, recipient, item_id, body)"
            " VALUES (?,?,?,?,?)", (now, actor, recipient, item_id, body))
        seq = cur.lastrowid
        conn.execute(
            "INSERT INTO console_events (ts, actor, action, session_id, detail)"
            " VALUES (?,?,?,?,?)",
            (now, actor, "note", item_id,
             "消息#%d 发送方 %s → 接收方 %s：%s" % (seq, actor, recipient, body[:150])))
    return {"ok": True, "result": {"seq": seq, "ts": now}}


@router.route("GET", "/api/console/messages")
def get_messages(ctx):
    """消息流（群聊视图拉取）：近二百条按时间正序，附事件台账尾
    （判据：全部消息入事件台账可查）。"""
    auth.caller(ctx)
    rows = db.query("SELECT * FROM console_messages"
                         " ORDER BY seq DESC LIMIT 200")
    events = db.query("SELECT * FROM console_events"
                           " ORDER BY seq DESC LIMIT 100")
    return {"ok": True, "result": {
        "messages": [dict(r) for r in reversed(rows)],
        "events": [dict(r) for r in reversed(events)],
    }}


@router.route("POST", "/api/console/tasks/result")
def task_result(ctx):
    """采集代理回填执行结果（I-147）：状态 done 或 failed 加摘录。"""
    auth.caller(ctx)
    task_id = ctx.body.get("task_id")
    ok = bool(ctx.body.get("ok"))
    excerpt = (ctx.body.get("excerpt") or "").strip()
    if not task_id or not excerpt:
        raise ApiError("task_id 与 excerpt 不能为空")
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE console_tasks SET status=?, excerpt=?, done_at=? WHERE task_id=?",
            ("done" if ok else "failed", excerpt[:400], _now(), task_id))
        if cur.rowcount == 0:
            raise ApiError("任务不存在：%s" % task_id)
    return {"ok": True, "result": {"task_id": task_id,
                                    "status": "done" if ok else "failed"}}


# ── I-233 待领工作区只读端点（pending-work-v1 数据面）──────────────
# 数据面写仅存储快照与事件（同 lane_queue_snapshot 镜像模式）；
# 读取只读，不认领、不派发、不释放工位、不触发调度副作用。

_PENDING_SNAPSHOT_KEY = "pending_work_snapshot"
_PENDING_EVENTS_KEY = "pending_work_events"


@router.route("POST", "/api/console/pending_work/ingest")
def pending_work_ingest(ctx):
    """采集侧上行待领快照（I-233）。只校验契约身份后整包存储。"""
    actor, _lanes = auth.caller(ctx)
    body = ctx.body or {}
    version = body.get("contract_version")
    versions = ("pending-work-v1", "pending-work-v2", "pending-work-v3")
    if version not in versions:
        raise ApiError("未知的待领契约版本：%r；当前接受 %s（v1/v2 为遗留兼容），未知版本明确拒绝" % (version,
                                                      "pending-work-v3"))
    if not body.get("snapshot_id") or not body.get("sampled_at"):
        raise ApiError("缺少契约身份字段（snapshot_id/sampled_at）")
    body = dict(body)
    body["pushed_at"] = _now()
    body["pushed_by"] = actor
    body["received_contract_version"] = version
    # 审计随配置行保存（pushed_by/pushed_at），不写 events 事件表：
    # 服务端事件消费者会被 events 插入唤醒并在本机开发实例上遗留
    # 写锁阻塞后续写请求；生产上行审计由内核账本承担。
    with db.transaction() as conn:
        repo.set_config(conn, _PENDING_SNAPSHOT_KEY, body)
    return {"ok": True, "result": {"stored": body.get("snapshot_id"),
                                    "pushed_at": body["pushed_at"]}}


@router.route("GET", "/api/console/pending_work")
def pending_work_get(ctx):
    """读取最近一次上行的待领快照（只读）。未上行过时如实说明。"""
    auth.caller(ctx)
    snap = repo.get_config(_PENDING_SNAPSHOT_KEY)
    if snap is None:
        return {"ok": True, "result": {"available": False,
                                       "note": "尚无待领快照上行，等待采集侧推送"}}
    snap = dict(snap)
    snap["available"] = True
    return {"ok": True, "result": snap}


@router.route("POST", "/api/console/pending_work/events/ingest")
def pending_work_events_ingest(ctx):
    """采集侧上行调度因果事件（I-233 v3）。按 event_id 去重合并，存储
    收到的契约版本；返回逐批可核验回执（after/first/last/count/digest
    与 added+deduped==count），发送方核对无误才推进持久游标。"""
    actor, _lanes = auth.caller(ctx)
    body = ctx.body or {}
    version = body.get("contract_version")
    versions = ("pending-work-v1", "pending-work-v2", "pending-work-v3")
    if version not in versions:
        raise ApiError("未知的待领事件契约版本：%r；当前接受 %s（v1/v2 为"
                       "遗留兼容），未知版本明确拒绝" % (version,
                                                        "pending-work-v3"))
    events = body.get("events")
    if not isinstance(events, list):
        raise ApiError("events 须为列表")
    stored = repo.get_config(_PENDING_EVENTS_KEY) or {"events": [],
                                                      "next_cursor": 0,
                                                      "gaps": []}
    known = {e.get("event_id") for e in stored.get("events", [])}
    merged = list(stored.get("events", []))
    added = 0
    for ev in events:
        eid = ev.get("event_id")
        if not eid or eid in known:
            continue
        known.add(eid)
        merged.append(ev)
        added += 1
    merged.sort(key=lambda e: (e.get("cursor") or 0, e.get("event_id")))
    keep = merged[-2000:]
    import hashlib as _hl
    cursors = sorted(e.get("cursor") or 0 for e in events) or [0]
    digest = _hl.sha256("".join(
        str(e.get("event_id")) for e in events).encode("utf-8")).hexdigest()
    stored_next = max(stored.get("next_cursor") or 0,
                      body.get("next_cursor") or 0,
                      max((e.get("cursor") or 0 for e in keep), default=0))
    record = {"contract_version": version,
              "events": keep,
              "next_cursor": stored_next,
              "total_events": max(stored.get("total_events") or 0,
                                  body.get("total_events") or len(keep)),
              "gaps": body.get("gaps") or stored.get("gaps") or [],
              "pushed_at": _now()}
    with db.transaction() as conn:
        repo.set_config(conn, _PENDING_EVENTS_KEY, record)
    return {"ok": True, "result": {
        "contract_version": version,
        "after_cursor": body.get("after_cursor"),
        "first_cursor": cursors[0] if events else None,
        "last_cursor": cursors[-1] if events else None,
        "count": len(events),
        "batch_digest": digest,
        "added": added,
        "deduped": len(events) - added,
        "stored_next_cursor": stored_next,
        "total_stored": len(keep)}}

@router.route("GET", "/api/console/pending_work/events")
def pending_work_events_get(ctx):
    """读取因果事件（只读，支持 after_cursor 增量与去重核对）。"""
    auth.caller(ctx)
    import re as _rex
    m = _rex.search(r"after_cursor=([0-9]+)", ctx.query or "")
    after = int(m.group(1)) if m else 0
    stored = repo.get_config(_PENDING_EVENTS_KEY)
    if stored is None:
        return {"ok": True, "result": {"available": False, "events": [],
                                       "note": "尚无因果事件上行"}}
    events = [e for e in stored.get("events", [])
              if (e.get("cursor") or 0) > after]
    ids = [e.get("event_id") for e in stored.get("events", [])]
    cursors = sorted(e.get("cursor") or 0 for e in stored.get("events", []))
    return {"ok": True, "result": {
        "available": True, "events": events,
        "next_cursor": stored.get("next_cursor"),
        "total_events": stored.get("total_events"),
        "gaps": stored.get("gaps"),
        "stored_unique": len(set(ids)) == len(ids),
        "dedup_key": "event_id",
        "retention": {"window_events": 2000,
                      "kept_events": len(cursors),
                      "earliest_cursor_kept": cursors[0] if cursors else None,
                      "note": "服务端只保留最近 2000 条事件；"
                              "earliest_cursor_kept 之前的事件已被保留"
                              "窗口淘汰，消费端需自行持久化或回补"}}}
