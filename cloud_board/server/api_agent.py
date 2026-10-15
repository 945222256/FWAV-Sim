# -*- coding: utf-8 -*-
"""api_agent.py — 本地代理接口：注册、心跳、快照拉取、迁移导入。

本地工地机通过这组接口主动外连。心跳顺带触发租约清理，
返回云端事件序号供代理判断是否需要拉快照。仅使用 Python 标准库。
"""
import datetime
import json

import auth
import db
import repo
import rules
import router
import settings
from httpd import ApiError


def _agent_info(ctx):
    info = ctx.body.get("info") or {}
    required = ("agent_id",)
    for k in required:
        if not info.get(k):
            raise ApiError("代理注册信息缺字段：%s" % k)
    return info


@router.route("POST", "/api/agent/register")
def register(ctx):
    _, lanes = auth.caller(ctx)
    info = _agent_info(ctx)
    with db.transaction() as conn:
        repo.heartbeat(conn, info["agent_id"], info)
        repo.append_event(conn, "agent_register", item_id="", actor=info["agent_id"],
                          detail="模型 %s %s，路线 %s"
                                 % (info.get("model", "?"), info.get("model_version", "?"), lanes))
    return {"ok": True, "result": {"registered": info["agent_id"]}}


@router.route("POST", "/api/agent/heartbeat")
def heartbeat(ctx):
    auth.caller(ctx)
    info = _agent_info(ctx)
    expired = []
    now = datetime.datetime.now()
    with db.transaction() as conn:
        repo.heartbeat(conn, info["agent_id"], info)
        queue_mirror = ctx.body.get("review_queue")
        if queue_mirror is not None:
            repo.set_config(conn, "review_queue_mirror", queue_mirror)
        if isinstance(ctx.body.get("goals"), dict):
            repo.set_config(conn, "goals", ctx.body["goals"].get("goals", []))
        # I-092 双键缺口修复：上行统一写权威键 lanes（快照下发同键）。
        # 兼容旧形态：老代理心跳携带 lanes_conf 包裹结构也归一到权威键，
        # 不再写镜像死键。
        lanes_up = None
        if isinstance(ctx.body.get("lanes"), dict) and ctx.body["lanes"]:
            lanes_up = ctx.body["lanes"]
        elif isinstance(ctx.body.get("lanes_conf"), dict):
            lanes_up = ctx.body["lanes_conf"].get("lanes") or {}
        if isinstance(lanes_up, dict) and lanes_up:
            repo.set_config(conn, "lanes", lanes_up)
        if isinstance(ctx.body.get("logical_lines"), dict):
            repo.set_config(conn, "logical_lines", ctx.body["logical_lines"])
        # I-167：战略蓝图应然层镜像上行（与 lanes 同款先推后拉纪律，
        # 登记源是本地 items_registry/blueprints.json，云端只存 blocks）。
        if isinstance(ctx.body.get("blueprints"), dict):
            repo.set_config(conn, "blueprints", ctx.body["blueprints"].get("blocks", []))
        for item in repo.all_items():
            lease = item.get("lease_until")
            if item["status"] == "in_progress" and lease:
                if datetime.datetime.fromisoformat(lease) <= now:
                    expired.append(item["id"])
                    item["status"] = "open"
                    item["claimed_by"] = None
                    item["lease_until"] = None
                    repo.upsert_item(conn, item)
                    repo.append_event(conn, "sweep", item_id=item["id"], actor="cloud-sweep",
                                      detail="租约到期自动退回 open")
    return {"ok": True, "result": {
        "server_seq": db.server_seq(), "swept": expired,
        # I-130：参数镜像摘要随心跳下发，代理比对后决定是否上行全量。
        "params_digest": repo.get_config("params_digest", ""),
    }}


def canonical_params_digest(params):
    """I-130：参数全量字典的规范化摘要，本地与云端用同一算法对账。"""
    import hashlib
    blob = json.dumps(params, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@router.route("POST", "/api/agent/params")
def push_params(ctx):
    """I-130：参数登记库镜像上行通道（代理令牌专用）。

    背景：快照下行的 params 覆盖曾把 I-127 图结构字段从本地工作区
    冲掉（云端扁平七列存不下嵌套节）。本通道按 lanes 镜像同款
    先推后拉纪律，把本地权威内容（含 graph/sample）整表推上云端，
    摘要对账防错发。"""
    name, _lanes = auth.caller(ctx)
    params = ctx.body.get("params")
    digest = ctx.body.get("digest") or ""
    if not isinstance(params, dict) or not params:
        raise ApiError("params 须为非空字典")
    if canonical_params_digest(params) != digest:
        raise ApiError("摘要不一致：正文与摘要不匹配，拒绝落库")
    archive = ctx.body.get("archive")
    if archive is not None and not isinstance(archive, list):
        raise ApiError("archive 须为列表")
    with db.transaction() as conn:
        count = repo.replace_params(conn, params)
        arc_count = repo.replace_params_archive(conn, archive or [])
        repo.set_config(conn, "params_digest", digest)
        repo.append_event(conn, "params_sync", actor=name,
                          detail="参数镜像上行：%d 键，档案 %d 条，摘要 %s…"
                                 % (count, arc_count, digest[:12]))
    return {"ok": True, "result": {"count": count, "archive": arc_count,
                                    "digest": digest}}


@router.route("GET", "/api/agent/snapshot")
def snapshot(ctx):
    auth.caller(ctx)
    return {"ok": True, "result": repo.snapshot()}


@router.route("POST", "/api/admin/reimport")
def reimport_snapshot(ctx):
    """切换对账入口：以本地最新 JSON 全量替换云端事项域。

    机主会话专用，只在本地文件账本切换为缓存模式前显式运行一次。
    events 按时间戳与动作去重追加，避免切换瞬间丢并行会话的写入。
    """
    user = auth.session_user(ctx)
    if not user:
        raise ApiError("对账导入只允许机主会话执行", 403)
    data = ctx.body.get("snapshot") or {}
    with db.transaction() as conn:
        conn.execute("DELETE FROM items")
        for item in data.get("items", []):
            row = dict(item)
            row["blockers"] = row.get("blockers") or rules.parse_blockers(
                row.get("note", ""), row.get("close_criterion", ""))
            repo.upsert_item(conn, row)
        known = {(e["ts"], e["action"], e.get("item_id") or "", (e.get("detail") or "")[:60])
                 for e in repo.all_events()}
        added = 0
        for ev in data.get("events", []):
            key = (ev.get("ts", ""), ev.get("action", ""), ev.get("item_id") or "",
                   (ev.get("detail") or "")[:60])
            if key in known:
                continue
            conn.execute("INSERT INTO events (ts, action, item_id, actor, detail) VALUES (?,?,?,?,?)",
                         (ev.get("ts", ""), ev.get("action", ""), ev.get("item_id", ""),
                          ev.get("actor", ""), ev.get("detail", "")))
            added += 1
        repo.append_event(conn, "admin_reimport", actor=user,
                          detail="切换对账：事项全量替换 %d 条，事件增量补 %d 条"
                                 % (len(data.get("items", [])), added))
    return {"ok": True, "result": {"items": len(data.get("items", [])), "events_added": added}}


@router.route("POST", "/api/admin/lanes")
def update_lanes(ctx):
    """领地配置更新（I-066）：机主会话专用，合并式增改与移除。

    包体 {"add": {路线号: 定义}, "remove": [路线号]}；定义须含 name，
    exclusive_paths 若给须为列表。写快照下发的 lanes 配置并留痕，
    使派发校验与快照消费方无需重启即见新路线。
    """
    user = auth.session_user(ctx)
    if not user:
        raise ApiError("领地配置只允许机主会话执行", 403)
    add = ctx.body.get("add") or {}
    remove = ctx.body.get("remove") or []
    if not add and not remove:
        raise ApiError("空变更被拒绝")
    import re as _re
    lane_re = _re.compile(r"^L[0-9]{1,2}$|^HQ$")
    with db.transaction() as conn:
        cur = repo.get_config("lanes", {}) or {}
        for lane_id, spec in add.items():
            if not lane_re.match(lane_id or ""):
                raise ApiError("路线编号不合法：%r" % (lane_id,))
            if not isinstance(spec, dict) or not spec.get("name"):
                raise ApiError("路线定义须为含 name 的对象：%s" % (lane_id,))
            if "exclusive_paths" in spec and not isinstance(spec["exclusive_paths"], list):
                raise ApiError("exclusive_paths 须为列表：%s" % (lane_id,))
            cur[lane_id] = spec
        for lane_id in remove:
            cur.pop(lane_id, None)
        repo.set_config(conn, "lanes", cur)
        repo.append_event(conn, "admin_lanes", actor=user,
                          detail="领地配置更新：增改 %s，移除 %s"
                                 % (sorted(add.keys()), sorted(remove)))
    return {"ok": True, "result": {"lanes": sorted(cur.keys())}}


@router.route("POST", "/api/admin/import")
def import_snapshot(ctx):
    """一次性迁移入口：本地 JSON 账本全量导入。要求机主会话。

    幂等策略：以云端为空为前提全量灌入；云端已有事项时拒绝，
    防止二次导入产生重复编号。
    """
    user = auth.session_user(ctx)
    if not user:
        raise ApiError("迁移导入只允许机主会话执行", 403)
    if repo.all_items():
        raise ApiError("导入被拒绝：云端账本非空，禁止二次全量导入")
    data = ctx.body.get("snapshot") or {}

    with db.transaction() as conn:
        for item in data.get("items", []):
            row = dict(item)
            row["blockers"] = row.get("blockers") or rules.parse_blockers(
                row.get("note", ""), row.get("close_criterion", ""))
            repo.upsert_item(conn, row)
        for entry in data.get("inbox", []):
            repo.put_inbox(conn, entry)
        for report in data.get("reports", []):
            repo.append_report(conn, report)
        for ev in data.get("events", []):
            conn.execute("INSERT INTO events (ts, action, item_id, actor, detail) "
                         "VALUES (?,?,?,?,?)",
                         (ev.get("ts", ""), ev.get("action", ""), ev.get("item_id", ""),
                          ev.get("actor", ""), ev.get("detail", "")))
        for key, rec in (data.get("params") or {}).items():
            conn.execute("INSERT OR REPLACE INTO params VALUES (?,?,?,?,?,?,?)",
                         (key, _json(rec.get("value")), rec.get("unit"),
                          rec.get("status", "current"), rec.get("source"),
                          rec.get("since"), rec.get("note")))
        if data.get("lanes"):
            repo.set_config(conn, "lanes", data["lanes"])
        repo.append_event(conn, "admin_import", actor=user,
                          detail="本地账本迁移导入：事项 %d 条，事件 %d 条，参数 %d 条"
                                 % (len(data.get("items", [])), len(data.get("events", [])),
                                    len(data.get("params") or {})))
    return {"ok": True, "result": {"imported_items": len(data.get("items", []))}}


def _json(v):
    import json
    return json.dumps(v, ensure_ascii=False)
