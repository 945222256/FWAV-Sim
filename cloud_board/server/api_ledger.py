# -*- coding: utf-8 -*-
"""api_ledger.py — 事项账本写接口：登记、证据、关闭、答复、申报、判据子项。

与本地 ledger.py 的函数一一对应，函数语义与拒绝条件完全一致；
一切校验走 rules.py，一切写入走事务并在事件流留痕。
仅使用 Python 标准库。
"""
import datetime

import auth
import db
import repo
import rules
import router
from httpd import ApiError


def _actor(ctx):
    name, lanes = auth.caller(ctx)
    return name


@router.route("POST", "/api/ledger/add")
def add(ctx):
    title = (ctx.body.get("title") or "").strip()
    if not title:
        raise ApiError("登记被拒绝：标题不能为空")
    actor = _actor(ctx)
    criteria = [c for c in (ctx.body.get("criteria") or []) if c and c.strip()]
    with db.transaction() as conn:
        new_id = repo.next_item_id(conn)
        item = {
            "id": new_id, "title": title, "status": "open",
            "opened_at": datetime.date.today().isoformat(),
            "closed_at": None, "closed_by": None,
            "owner": ctx.body.get("owner") or "待指派",
            "lane": ctx.body.get("lane"),
            "close_criterion": ctx.body.get("close_criterion") or "",
            "note": ctx.body.get("note") or "",
            "claimed_by": None, "claimed_at": None, "lease_until": None,
            "evidence": [],
            "criteria": [{"text": t, "verified": False, "evidence": ""} for t in criteria] or None,
            "replies": [],
            "blockers": rules.parse_blockers(ctx.body.get("note") or ""),
            "depends_on": ctx.body.get("depends_on") or [],
        }
        repo.upsert_item(conn, item)
        repo.append_event(conn, "add", item_id=new_id, actor=actor, detail=title[:100])
    return {"ok": True, "result": {"id": new_id}}


@router.route("POST", "/api/ledger/append_evidence")
def append_evidence(ctx):
    item_id = ctx.body.get("item_id")
    raw = ctx.body.get("evidence")
    # I-069 类型统一：单条通道只收字符串，列表形态明确引导走 attach
    if not isinstance(raw, str):
        raise ApiError("单条证据须为字符串（列表形态走 attach）")
    text = raw.strip()
    actor = _actor(ctx)
    item = repo.get_item(item_id)
    rules.check_evidence_text(item_id, text, item["evidence"] if item else [])
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    with db.transaction() as conn:
        item["evidence"] = item["evidence"] + [text]
        repo.upsert_item(conn, item)
        repo.append_event(conn, "evidence", item_id=item_id, actor=actor, detail=text[:120])
    return {"ok": True, "result": {"appended": item_id}}


@router.route("POST", "/api/ledger/attach")
def attach(ctx):
    item_id = ctx.body.get("item_id")
    # I-069 类型统一：字符串自动包为单元素列表，杜绝按字符拆裂入账
    # （373 条单字废条目的根因）；其余非列表非字符串形态拒绝
    raw = ctx.body.get("evidence")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ApiError("证据须为字符串或字符串列表")
    evidences = [e.strip() for e in raw if isinstance(e, str) and e.strip()]
    if not evidences:
        raise ApiError("证据列表为空被拒绝")
    actor = _actor(ctx)
    item = repo.get_item(item_id)
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    with db.transaction() as conn:
        item["evidence"] = item["evidence"] + evidences
        repo.upsert_item(conn, item)
        repo.append_event(conn, "evidence", item_id=item_id, actor=actor,
                          detail="批量追加 %d 条" % len(evidences))
    return {"ok": True, "result": {"appended": item_id}}


@router.route("POST", "/api/ledger/close")
def close(ctx):
    item_id = ctx.body.get("item_id")
    evidence = [e for e in ctx.body.get("evidence") or [] if e and e.strip()]
    closer = ctx.body.get("closer") or _actor(ctx)
    conclusion = ctx.body.get("conclusion") or ""
    criterion_check = ctx.body.get("criterion_check")
    item = repo.get_item(item_id)
    rules.check_close(item, evidence, conclusion, criterion_check)
    # I-116 判据闸：仅当事项挂了判据脚本时生效——关闭请求必须携带
    # 执行器新鲜产出（三十分钟内、脚本匹配、通过）的 check_result，
    # 否则拒绝。无脚本字段的事项走到这里与现状逐字一致。
    check_result = ctx.body.get("check_result")
    if item.get("check_script"):
        import re as _re
        if not isinstance(check_result, dict):
            raise ApiError("判据闸拒绝：本事项挂有判据脚本 %s，请先用执行器运行并随关闭请求提交 check_result"
                           % item["check_script"])
        if not check_result.get("pass"):
            raise ApiError("判据闸拒绝：判据脚本未通过（退出码 %s），不得关闭"
                           % check_result.get("exit_code"))
        if check_result.get("script") != item["check_script"]:
            raise ApiError("判据闸拒绝：执行脚本 %s 与注册脚本 %s 不一致"
                           % (check_result.get("script"), item["check_script"]))
        ran_at = check_result.get("ran_at") or ""
        try:
            age = datetime.datetime.now() - datetime.datetime.fromisoformat(ran_at)
        except ValueError:
            raise ApiError("判据闸拒绝：check_result 缺少合法 ran_at")
        if age > datetime.timedelta(minutes=30):
            raise ApiError("判据闸拒绝：执行结果已过期（%s），请重跑执行器" % ran_at)
    with db.transaction() as conn:
        item["status"] = "closed"
        item["closed_at"] = datetime.date.today().isoformat()
        item["closed_by"] = closer
        # I-060 修正：完成记录必须带执行人，认领人缺位时由结载人补位，
        # 使关闭记录的责任链自洽（claimed_at 缺失时同刻补记）
        if not item.get("claimed_by"):
            item["claimed_by"] = closer
            if not item.get("claimed_at"):
                item["claimed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        item["conclusion"] = conclusion
        item["criterion_check"] = criterion_check
        item["evidence"] = item["evidence"] + evidence
        if item.get("check_script") and isinstance(check_result, dict):
            item["check_result"] = check_result
        repo.upsert_item(conn, item)
        repo.append_event(conn, "close", item_id=item_id, actor=closer,
                          detail="打勾关闭（%s），证据 %d 条%s"
                                 % (conclusion, len(evidence),
                                    "，判据脚本通过" if item.get("check_script") else ""))
    return {"ok": True, "result": {"closed": item_id}}


@router.route("POST", "/api/ledger/block")
def block(ctx):
    item_id = ctx.body.get("item_id")
    reason = ctx.body.get("reason") or ""
    blocker_type = ctx.body.get("blocker_type") or ""
    needs = ctx.body.get("needs") or ""
    evidence = [e for e in ctx.body.get("evidence") or [] if e and e.strip()]
    reporter = ctx.body.get("reporter") or _actor(ctx)
    item = repo.get_item(item_id)
    rules.check_block(item, reason, blocker_type, needs, evidence)
    with db.transaction() as conn:
        item["status"] = "blocked"
        item["blocked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        item["blocked_by"] = reporter
        item["block_reason"] = reason
        item["blocker_type"] = blocker_type
        item["block_needs"] = needs
        item["claimed_by"] = None
        item["lease_until"] = None
        item["evidence"] = item["evidence"] + evidence
        repo.upsert_item(conn, item)
        repo.append_event(conn, "block", item_id=item_id, actor=reporter,
                          detail="卡住申报（%s）：%s" % (blocker_type, reason[:80]))
    return {"ok": True, "result": {"blocked": item_id}}


@router.route("POST", "/api/ledger/unblock")
def unblock(ctx):
    item_id = ctx.body.get("item_id")
    decision = ctx.body.get("decision") or ""
    decider = ctx.body.get("decider") or _actor(ctx)
    item = repo.get_item(item_id)
    rules.check_unblock(item, decision)
    with db.transaction() as conn:
        item["status"] = "open"
        item["unblock_decision"] = decision
        item["unblocked_by"] = decider
        item["unblocked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        repo.upsert_item(conn, item)
        repo.append_event(conn, "unblock", item_id=item_id, actor=decider,
                          detail=decision[:100])
    return {"ok": True, "result": {"unblocked": item_id}}


@router.route("POST", "/api/ledger/owner_reply")
def owner_reply(ctx):
    item_id = ctx.body.get("item_id")
    text = ctx.body.get("text") or ""
    # I-042 审查修正：身份只认凭证（会话或 Bearer 令牌），包体自带的
    # actor 字段不得短路鉴权（实测可匿名写穿，已封堵）
    actor = _actor(ctx)
    rules.check_owner_reply(text)
    item = repo.get_item(item_id)
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    reply = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
             "actor": actor, "text": text.strip()}
    with db.transaction() as conn:
        item["replies"] = item.get("replies", []) + [reply]
        repo.upsert_item(conn, item)
        repo.append_event(conn, "owner_reply", item_id=item_id, actor=actor,
                          detail=text.strip()[:120])
    return {"ok": True, "result": {"reply": reply}}


@router.route("POST", "/api/ledger/add_report")
def add_report(ctx):
    report = dict(ctx.body.get("report") or {})
    if not report.get("model"):
        raise ApiError("申报被拒绝：必须提供模型名称")
    report["reported_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    with db.transaction() as conn:
        repo.append_report(conn, report)
        repo.append_event(conn, "report", item_id=report.get("item_id", ""),
                          actor=report.get("model", ""),
                          detail="申报事项 %s，日志指针 %s"
                                 % (report.get("item_id", "无"), report.get("log_pointer", "无")))
    return {"ok": True, "result": {"reported_at": report["reported_at"]}}


@router.route("POST", "/api/ledger/set_criterion")
def set_criterion(ctx):
    item_id = ctx.body.get("item_id")
    index = int(ctx.body.get("index"))
    verified = bool(ctx.body.get("verified"))
    evidence = ctx.body.get("evidence") or ""
    # I-042 审查修正：同 owner_reply，身份只认凭证，包体 actor 不短路鉴权
    actor = _actor(ctx)
    item = repo.get_item(item_id)
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    rules.check_criterion(item, index, verified, evidence)
    with db.transaction() as conn:
        crit = item["criteria"]
        crit[index]["verified"] = verified
        crit[index]["evidence"] = evidence.strip()
        crit[index]["verified_by"] = actor
        crit[index]["verified_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        repo.upsert_item(conn, item)
        repo.append_event(conn, "criterion", item_id=item_id, actor=actor,
                          detail="子项 %d %s：%s"
                              % (index + 1, "验证" if verified else "取消验证",
                                 crit[index]["text"][:80]))
    return {"ok": True, "result": {"criterion": item["criteria"][index]}}


@router.route("POST", "/api/ledger/set_depends")
def set_depends(ctx):
    """设置事项依赖清单（待审批准归位时的依赖落账）。"""
    item_id = ctx.body.get("item_id")
    depends_on = ctx.body.get("depends_on") or []
    actor = _actor(ctx)
    item = repo.get_item(item_id)
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    with db.transaction() as conn:
        item["depends_on"] = depends_on
        repo.upsert_item(conn, item)
        repo.append_event(conn, "set_depends", item_id=item_id, actor=actor,
                          detail="依赖清单设为 %s" % depends_on)
    return {"ok": True, "result": {"depends_on": depends_on}}


@router.route("POST", "/api/ledger/set_rationale")
def set_rationale(ctx):
    """登记事项来由：从哪条需求拆出，一句话规范中文（修订一第一条）。"""
    item_id = ctx.body.get("item_id")
    rationale = (ctx.body.get("rationale") or "").strip()
    author = ctx.body.get("author") or _actor(ctx)
    if not rationale:
        raise ApiError("来由为空被拒绝：必须一句话说明从哪条需求拆出")
    if len(rationale) > 200:
        raise ApiError("来由超过两百字，必须压缩为一句")
    item = repo.get_item(item_id)
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    with db.transaction() as conn:
        item["rationale"] = rationale
        repo.upsert_item(conn, item)
        repo.append_event(conn, "rationale", item_id=item_id, actor=author,
                          detail=rationale[:120])
    return {"ok": True, "result": {"rationale": rationale}}


@router.route("POST", "/api/ledger/set_line")
def set_line(ctx):
    """登记事项逻辑线归属（L0 至 L8，固定轴，回答为什么存在）。"""
    item_id = ctx.body.get("item_id")
    line = (ctx.body.get("line") or "").strip()
    author = ctx.body.get("author") or _actor(ctx)
    import re as _re
    if not _re.fullmatch(r"L[0-8]", line):
        raise ApiError("逻辑线编号非法：%s（应为 L0 至 L8）" % line)
    item = repo.get_item(item_id)
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    with db.transaction() as conn:
        item["line"] = line
        repo.upsert_item(conn, item)
        repo.append_event(conn, "line", item_id=item_id, actor=author,
                          detail="逻辑线归属设为 %s" % line)
    return {"ok": True, "result": {"line": line}}


@router.route("POST", "/api/ledger/set_enables")
def set_enables(ctx):
    """登记启发关系：本事项的产出使哪些事项的设计成为可能（非阻断）。"""
    item_id = ctx.body.get("item_id")
    enables = ctx.body.get("enables") or []
    author = ctx.body.get("author") or _actor(ctx)
    item = repo.get_item(item_id)
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    import re as _re
    for ref in enables:
        if not _re.fullmatch(r"I-\d{3}", ref):
            raise ApiError("启发编号非法：%s" % ref)
        if repo.get_item(ref) is None:
            raise ApiError("启发目标不存在：%s" % ref)
        if ref == item_id:
            raise ApiError("启发自身被拒绝")
    with db.transaction() as conn:
        item["enables"] = enables
        repo.upsert_item(conn, item)
        repo.append_event(conn, "enables", item_id=item_id, actor=author,
                          detail="启发关系设为 %s" % enables)
    return {"ok": True, "result": {"enables": enables}}


@router.route("POST", "/api/ledger/set_check_script")
def set_check_script(ctx):
    """I-116 注册器（服务端）：事项挂判据脚本。

    脚本必须位于 tools/checks/ 目录内、以 .py 结尾；路径在本地仓库
    的存在性由执行器侧负责校验，服务端只做格式校验。
    重复注册即覆盖（脚本演进属正常迭代，事件流留全程）。
    """
    item_id = ctx.body.get("item_id")
    script = (ctx.body.get("script") or "").strip().replace("\\", "/")
    author = ctx.body.get("author") or _actor(ctx)
    import re as _re
    if not _re.fullmatch(r"tools/checks/[A-Za-z0-9_\-]+\.py", script):
        raise ApiError("脚本路径非法：%s（必须为 tools/checks/<名称>.py）" % script)
    item = repo.get_item(item_id)
    if item is None:
        raise ApiError("事项不存在：%s" % item_id)
    with db.transaction() as conn:
        item["check_script"] = script
        repo.upsert_item(conn, item)
        repo.append_event(conn, "check_script", item_id=item_id, actor=author,
                          detail="判据脚本注册为 %s" % script)
    return {"ok": True, "result": {"check_script": script}}


@router.route("GET", "/api/events")
def list_events(ctx):
    auth.caller(ctx)
    item_id = (ctx.query.split("item_id=")[-1] if "item_id=" in ctx.query else None)
    events = repo.events_for_item(item_id) if item_id else repo.all_events()
    return {"ok": True, "result": events}
