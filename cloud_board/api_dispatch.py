# -*- coding: utf-8 -*-
"""api_dispatch.py — 派发接口：认领、释放、改派、租约清理。

与本地 dispatch.py 的函数一一对应。认领时强制一路一项在制限制；
租约清理每次心跳都会触发。仅使用 Python 标准库。
"""
import datetime

import auth
import db
import repo
import rules
import router
from httpd import ApiError


@router.route("POST", "/api/dispatch/claim")
@repo.idempotent_write("claim")
def claim(ctx):
    item_id = ctx.body.get("item_id")
    worker = ctx.body.get("worker")
    lease_hours = int(ctx.body.get("lease_hours", 4))
    # I-174 会话归属：认领自报会话号，写入认领事件详情，
    # 控制台映射以认领记录为权威来源
    claim_session = (ctx.body.get("session_id") or "").strip()
    # I-217 工位归属：认领可选自报工位号；署名仍为路线队名，业务线与
    # reviewer 不因工位并行而改变，不借工位字段绕过容量检查。
    worker_slot = (ctx.body.get("worker_id") or "").strip()
    _, lanes = auth.caller(ctx)
    # I-031 第四期修正：认领的读取与三条强制检查移入 IMMEDIATE 写事务内，
    # 消除检查与写入分离的竞态窗口（双代理同项并发实测双双成功后修复）
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        item = repo._row_to_item(row)
        all_items = [repo._row_to_item(r) for r in
                     conn.execute("SELECT * FROM items ORDER BY id").fetchall()]
        # I-217 补修：每线容量取管理员配置（缺省一），与工位归属、会话
        # 唯一性在同一事务内验证；客户端自报容量无效。
        capacity_cfg = repo.get_config("lane_concurrency", {}) or {}
        lane_cap = rules.lane_capacity_of(capacity_cfg, item.get("lane") if item else None)
        has_open_deps = rules.check_claim(item, worker, all_items,
                                          lane_capacity=lane_cap,
                                          claim_worker_id=worker_slot or None,
                                          claim_session_id=claim_session or None)
        now = datetime.datetime.now()
        item["status"] = "in_progress"
        item["claimed_by"] = worker
        item["claimed_at"] = now.isoformat(timespec="seconds")
        item["claim_session_id"] = claim_session or None
        if worker_slot:
            item["claim_worker_id"] = worker_slot
        item["lease_until"] = (now + datetime.timedelta(hours=lease_hours)).isoformat(timespec="seconds")
        if has_open_deps and item.get("partial_start"):
            item["claim_scope"] = item["partial_start"]
        repo.upsert_item(conn, item)
        repo.append_event(conn, "claim", item_id=item_id, actor=worker,
                          detail="认领，租约 %s 小时%s%s%s"
                                 % (lease_hours,
                                    "，先行范围：" + str(item.get("claim_scope")) if has_open_deps else "",
                                    "，会话 %s" % claim_session if claim_session else "",
                                    "，工位 %s" % worker_slot if worker_slot else ""))
    return {"ok": True, "result": {"claimed": item_id,
                                    "worker_id": worker_slot or None}}


@router.route("POST", "/api/dispatch/release")
@repo.idempotent_write("release")
def release(ctx):
    item_id = ctx.body.get("item_id")
    worker = ctx.body.get("worker")
    auth.caller(ctx)
    item = repo.get_item(item_id)
    rules.check_release(item, worker)
    with db.transaction() as conn:
        item["status"] = "open"
        item["claimed_by"] = None
        item["lease_until"] = None
        repo.upsert_item(conn, item)
        repo.append_event(conn, "release", item_id=item_id, actor=worker, detail="主动释放认领")
    return {"ok": True, "result": {"released": item_id}}


@router.route("POST", "/api/dispatch/rebind_session")
def rebind_session(ctx):
    import re
    item_id = ctx.body.get("item_id")
    old_sid, new_sid = ctx.body.get("old_session_id"), ctx.body.get("session_id")
    _, lanes = auth.caller(ctx)
    if not isinstance(new_sid, str) or not new_sid.startswith("sess_"):
        raise ApiError("新会话地址缺失或非法")
    with db.transaction() as conn:
        item = repo.get_item(item_id)
        if not item or item.get("status") != "in_progress":
            raise ApiError("只有进行中事项可交接会话")
        if not auth.lane_allowed(lanes, item.get("lane")):
            raise ApiError("令牌无事项路线权限", 403)
        current = item.get("claim_session_id")
        if not current:
            row = conn.execute("SELECT detail FROM events WHERE item_id=? AND action='claim' ORDER BY seq DESC LIMIT 1", (item_id,)).fetchone()
            match = re.search(r"会话 (sess_[0-9a-f-]+)", row[0]) if row else None
            current = match.group(1) if match else None
        if current == new_sid:
            return {"ok": True, "result": {"item_id": item_id, "session_id": new_sid, "reconciled": True}}
        if current != old_sid:
            raise ApiError("原认领会话不一致，禁止覆盖其他交接")
        item["claim_session_id"] = new_sid
        repo.upsert_item(conn, item)
        repo.append_event(conn, "claim", item_id=item_id, actor=item["claimed_by"],
                          detail="接续换绑，旧地址=%s，会话 %s" % (old_sid, new_sid))
    return {"ok": True, "result": {"item_id": item_id, "session_id": new_sid}}


@router.route("POST", "/api/dispatch/assign")
def assign(ctx):
    item_id = ctx.body.get("item_id")
    lane = ctx.body.get("lane")
    owner = ctx.body.get("owner")
    _, lanes = auth.caller(ctx)
    rules.check_assign(lane)
    if not auth.lane_allowed(lanes, lane):
        raise ApiError("令牌无 %s 路线权限" % lane, 403)
    item = repo.get_item(item_id)
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    actor = auth.session_user(ctx) or "agent"
    with db.transaction() as conn:
        item["lane"] = lane
        item["owner"] = owner
        repo.upsert_item(conn, item)
        repo.append_event(conn, "assign", item_id=item_id, actor=actor,
                          detail="派发到 %s，负责人 %s" % (lane, owner))
    return {"ok": True, "result": {"assigned": item_id}}


@router.route("POST", "/api/dispatch/sweep")
def sweep(ctx):
    auth.caller(ctx)
    now = datetime.datetime.now()
    expired = []
    with db.transaction() as conn:
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
    return {"ok": True, "result": {"expired": expired}}
