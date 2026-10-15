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
def claim(ctx):
    item_id = ctx.body.get("item_id")
    worker = ctx.body.get("worker")
    lease_hours = int(ctx.body.get("lease_hours", 4))
    _, lanes = auth.caller(ctx)
    # I-031 第四期修正：认领的读取与三条强制检查移入 IMMEDIATE 写事务内，
    # 消除检查与写入分离的竞态窗口（双代理同项并发实测双双成功后修复）
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        item = repo._row_to_item(row)
        all_items = [repo._row_to_item(r) for r in
                     conn.execute("SELECT * FROM items ORDER BY id").fetchall()]
        has_open_deps = rules.check_claim(item, worker, all_items)
        now = datetime.datetime.now()
        item["status"] = "in_progress"
        item["claimed_by"] = worker
        item["claimed_at"] = now.isoformat(timespec="seconds")
        item["lease_until"] = (now + datetime.timedelta(hours=lease_hours)).isoformat(timespec="seconds")
        if has_open_deps and item.get("partial_start"):
            item["claim_scope"] = item["partial_start"]
        repo.upsert_item(conn, item)
        repo.append_event(conn, "claim", item_id=item_id, actor=worker,
                          detail="认领，租约 %s 小时%s"
                                 % (lease_hours,
                                    "，先行范围：" + str(item.get("claim_scope")) if has_open_deps else ""))
    return {"ok": True, "result": {"claimed": item_id}}


@router.route("POST", "/api/dispatch/release")
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
