# -*- coding: utf-8 -*-
"""api_intake.py — 新问题收纳接口：待审队列、审理、批准入册。

与本地 intake.py 的函数一一对应。四条审理规则在服务端执行；
人工改判强制填理由并写入 overrides。仅使用 Python 标准库。
"""
import datetime

import auth
import db
import repo
import rules
import router
from httpd import ApiError


@router.route("POST", "/api/intake/submit")
def submit(ctx):
    title = (ctx.body.get("title") or "").strip()
    if not title:
        raise ApiError("申报被拒绝：标题不能为空")
    # I-124 规一：目标上下文为必填栏，紧随问题描述之后，缺栏拒收
    goal_context = (ctx.body.get("goal_context") or "").strip()
    if not goal_context or len(goal_context) < 6:
        raise ApiError("申报被拒绝：目标上下文为必填栏（写清在做哪个目标时"
                       "遇到这个问题，不少于六个字），位置紧随问题描述之后")
    actor, _ = auth.caller(ctx)
    with db.transaction() as conn:
        entry = {
            "id": repo.next_inbox_id(conn),
            "title": title,
            "goal_context": goal_context,
            "owner": ctx.body.get("owner") or "待指派",
            "close_criterion": ctx.body.get("close_criterion") or "",
            "criteria": [c for c in (ctx.body.get("criteria") or []) if c and c.strip()],
            "note": ctx.body.get("note") or "",
            "submitted_by": ctx.body.get("submitted_by") or actor,
            "submitted_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "decision": None,
            "overrides": [],
            # I-198：审核接收负责人（稳定人员标识）；缺失或空白按 None
            # 存（旧链路兼容），入册前由 decide 侧策略与客户端入口校验。
            "reviewer": (ctx.body.get("reviewer") or "").strip() or None,
        }
        entry["lint"] = rules.run_lint(entry, repo.all_items(), rules.known_lanes())
        repo.put_inbox(conn, entry)
        repo.append_event(conn, "submit", item_id=entry["id"], actor=entry["submitted_by"],
                          detail=title[:100])
        repo.append_event(conn, "lint", item_id=entry["id"], actor="lint",
                          detail=entry["lint"]["summary"] + "；规则结论：" +
                                  "、".join("%s=%s" % (r["rule"], r["verdict"])
                                            for r in entry["lint"]["rules"]))
    return {"ok": True, "result": {"entry": _entry_view(entry)}}


def _entry_view(entry):
    return {"id": entry["id"], "title": entry["title"],
            "lint": entry.get("lint"), "decision": entry.get("decision")}


@router.route("POST", "/api/intake/decide")
def decide(ctx):
    entry_id = ctx.body.get("entry_id")
    action = ctx.body.get("action")
    lane = ctx.body.get("lane") or ""
    merge_into = ctx.body.get("merge_into") or ""
    reason = ctx.body.get("reason") or ""
    actor, _ = auth.caller(ctx)

    entry = next((e for e in repo.all_inbox() if e["id"] == entry_id), None)
    if entry is None:
        raise ApiError("待审条目不存在：%s" % entry_id)
    chosen_lane, has_fail, differs = rules.check_decide(entry, action, lane, reason)

    # I-124 规二：决定材料（批准理由与合并说明）过降标准语义拦截，
    # 误拦申诉走指挥部人工复核（overrides 通道留痕放行）。
    _sv = rules.scan_standards(reason)
    if _sv:
        raise ApiError("决定材料被拦截退回：%s（确属误拦走指挥部人工复核申诉）"
                       % ["%s「%s」" % (c, w) for c, w, _ in _sv])

    now = datetime.datetime.now().isoformat(timespec="seconds")
    if action == "reject":
        entry["decision"] = {"action": "reject", "by": actor, "at": now, "reason": reason}
        with db.transaction() as conn:
            repo.put_inbox(conn, entry)
            repo.append_event(conn, "reject", item_id=entry_id, actor=actor,
                              detail=("%s：%s" % (entry["title"][:60], reason)) if reason
                              else entry["title"][:80])
        return {"ok": True, "result": {"entry": _entry_view(entry)}}

    if action == "merge":
        if not merge_into:
            raise ApiError("合并被拒绝：必须指定合并目标事项编号")
        entry["decision"] = {"action": "merge", "by": actor, "at": now,
                             "merge_into": merge_into}
        with db.transaction() as conn:
            repo.put_inbox(conn, entry)
            repo.append_event(conn, "merge", item_id=merge_into, actor=actor,
                              detail="待审 %s（%s）合并入本事项" % (entry_id, entry["title"][:60]))
        return {"ok": True, "result": {"entry": _entry_view(entry)}}

    # approve：改判留痕后入册并派发。
    if differs or has_fail:
        entry["overrides"].append({
            "at": now, "by": actor,
            "lint_suggested": entry["lint"]["suggested_lane"],
            "human_decided": chosen_lane, "over_fail": has_fail, "reason": reason})
    with db.transaction() as conn:
        new_id = repo.next_item_id(conn)
        item = {
            "id": new_id, "title": entry["title"], "status": "open",
            "opened_at": datetime.date.today().isoformat(),
            "closed_at": None, "closed_by": None,
            "owner": entry.get("owner") or "待指派", "lane": chosen_lane,
            "close_criterion": entry.get("close_criterion") or "",
            "note": entry.get("note") or "",
            "claimed_by": None, "claimed_at": None, "lease_until": None,
            "evidence": [],
            "criteria": [{"text": t, "verified": False, "evidence": ""}
                         for t in entry.get("criteria", [])] or None,
            "replies": [],
            "blockers": rules.parse_blockers(entry.get("note") or ""),
            # I-198：审核接收负责人随入册保存（稳定人员标识，客户端在
            # 申报时校验过通讯录；服务端只存不猜）。
            "reviewer": entry.get("reviewer") or None,
        }
        repo.upsert_item(conn, item)
        entry["decision"] = {"action": "approve", "by": actor, "at": now,
                             "lane": chosen_lane, "result_item": new_id}
        repo.put_inbox(conn, entry)
        repo.append_event(conn, "approve", item_id=new_id, actor=actor,
                          detail="待审 %s 批准入册，路线 %s%s"
                                 % (entry_id, chosen_lane,
                                    "，改判理由：" + reason if (differs or has_fail) else ""))
    return {"ok": True, "result": {"entry": _entry_view(entry), "item": new_id}}
