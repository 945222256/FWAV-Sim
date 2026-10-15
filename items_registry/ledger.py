# -*- coding: utf-8 -*-
"""ledger.py — 事项总账唯一读写入口（打勾机制）。

纪律（写在 items.json 的 rules 字段，此处只做强制）：
  1. 关闭事项必须附证据与关闭时间，缺一拒绝。
  2. 汇报与提交只允许引用事项编号。
  3. 旧记录永不删除，关闭只打勾。
仅使用 Python 标准库。
"""
import json
import os
import re
import sys
import datetime
import contextlib
import time

import events
import cloud_client

_DIR = os.path.dirname(os.path.abspath(__file__))
ITEMS_PATH = os.path.join(_DIR, "items.json")


def _load():
    with open(ITEMS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(obj):
    """原子写：先写临时文件再整体替换，写入中途进程被杀时原文件保持完好。"""
    tmp = ITEMS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ITEMS_PATH)


def add(title, owner, close_criterion, note="", criteria=None, depends_on=None,
        kind="task", goal=None, enables=None, reviewer=None):
    """登记新事项，返回分配的编号。任何怀疑必须先走这里。

    criteria 为判据子项列表，每项是一个字符串。登记时列出子项，
    进度口径才有依据；不传则该事项进度显示为未定义。
    depends_on 为依赖事项编号列表，供依赖图与认领拦截使用。
    """
    if kind not in ("task", "requirement"):
        raise ValueError("kind 只允许 task 或 requirement")
    if goal and not re.match(r"^G-[0-9]{3}$", goal):
        raise ValueError("goal 须为 G-xxx 编号")
    if cloud_client.enabled():
        result = cloud_client.write("/api/ledger/add", {
            "title": title, "owner": owner, "close_criterion": close_criterion,
            "note": note, "criteria": criteria or [],
            "depends_on": depends_on or [],
            "kind": kind, "goal": goal,
            "reviewer": reviewer, "enables": enables or [],
            "reviewer": reviewer}, needs_result=True)
        return result["id"]
    obj = _load()
    nums = [int(i["id"].split("-")[1]) for i in obj["items"]]
    # I-133：空账本首次登记编号分配崩溃修复（按十队模板侧同型修法）
    new_id = "I-%03d" % ((max(nums) + 1) if nums else 1)
    item = {
        "id": new_id,
        "title": title,
        "status": "open",
        "opened_at": datetime.date.today().isoformat(),
        "closed_at": None,
        "owner": owner,
        "evidence": [],
        "close_criterion": close_criterion,
        "note": note,
        "kind": kind,
    }
    if goal:
        item["goal"] = goal
    if enables:
        item["enables"] = enables
    if criteria:
        item["criteria"] = [{"text": t, "verified": False, "evidence": ""} for t in criteria if t and t.strip()]
    if depends_on:
        item["depends_on"] = depends_on
    obj["items"].append(item)
    _save(obj)
    return new_id


def attach(item_id, evidence):
    """向 open 事项追加证据链接（不打勾；阶段产物按纪律即时登记）。

    I-069 类型防御：接受单条字符串或字符串列表，字符串自动包为单元素
    列表，杜绝按字符拆裂入账；其余类型与空项拒绝。"""
    if isinstance(evidence, str):
        evidence = [evidence]
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("证据须为非空字符串或非空字符串列表")
    if not all(isinstance(e, str) and e.strip() for e in evidence):
        raise ValueError("证据列表含空项或非字符串项，被拒绝")
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/attach",
                                  {"item_id": item_id, "evidence": evidence})
    obj = _load()
    for item in obj["items"]:
        if item["id"] == item_id:
            item["evidence"] = item.get("evidence", []) + evidence
            _save(obj)
            return
    raise KeyError("事项不存在：%s" % item_id)


def append_evidence(item_id, evidence):
    """向指定事项追加单条证据字符串，不改变 status（打勾必须走 close()）。

    I-069 类型防御：只收字符串，列表形态走 attach，堵两通道形态双标。"""
    if not isinstance(evidence, str):
        raise ValueError("证据须为字符串（列表形态走 attach）")
    if not evidence:
        raise ValueError("证据为空字符串被拒绝")
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/append_evidence",
                                  {"item_id": item_id, "evidence": evidence})
    obj = _load()
    for item in obj["items"]:
        if item["id"] == item_id:
            if evidence in item.get("evidence", []):
                raise ValueError("证据 %s 已登记在 %s，重复追加被拒绝" % (evidence, item_id))
            item["evidence"] = item.get("evidence", []) + [evidence]
            _save(obj)
            return
    raise KeyError("事项不存在：%s" % item_id)


def _check_owner_note(owner_note):
    """机主说明校验：面向机主的完整解释，禁止空泛与缩写堆砌。

    规则（D-023）：非空，不少于六十字，必须通过语言检查。
    内容约定为四句：做了什么、结果是什么、为什么是这个结果、下一步是什么。
    """
    if not owner_note or not owner_note.strip():
        raise ValueError("机主说明被拒绝：内容为空。发任务与状态更改必须附面向机主的完整解释")
    if len(owner_note.strip()) < 60:
        raise ValueError("机主说明被拒绝：不足六十字，无法构成完整解释")
    import langcheck
    hits = langcheck.scan_text(owner_note)
    if hits:
        raise ValueError("机主说明未过语言检查：%s" % hits)


def close(item_id, evidence, closer="", conclusion="", criterion_check=None, owner_note=""):
    """完成关闭。这是事项离开进行中状态的两条通道之一（另一条是 block）。

    必填交付物（缺一拒绝）：
      evidence：证据链接列表，至少一条；
      conclusion：结论，只允许三种措辞之一：
        「判据通过」「判据不通过」「证据不足无法判定」；
      criterion_check：判据逐条对照列表，每条为字典，键固定为
        判据（原文）、结果（过或不过）、数值出处（含数值与文件位置）；
      owner_note：机主说明，面向机主的完整解释，不少于六十字，
        内容为做了什么、结果是什么、为什么是这个结果、下一步是什么。
    closer 为执行完成的操作者标识，写入 closed_by 字段留痕。
    """
    if not evidence:
        raise ValueError("关闭被拒绝：必须提供至少一条证据链接")
    allowed = ("判据通过", "判据不通过", "证据不足无法判定")
    if conclusion not in allowed:
        raise ValueError("关闭被拒绝：conclusion 必须是 %s 之一" % (allowed,))
    if not criterion_check or not isinstance(criterion_check, list):
        raise ValueError("关闭被拒绝：criterion_check 必须是非空列表")
    for entry in criterion_check:
        keys = set(entry.keys()) if isinstance(entry, dict) else set()
        if not {"判据", "结果", "数值出处"}.issubset(keys):
            raise ValueError("关闭被拒绝：criterion_check 每条必须含 判据/结果/数值出处 三个键")
        if entry["结果"] not in ("过", "不过"):
            raise ValueError("关闭被拒绝：判据结果只允许 过 或 不过")
    _check_owner_note(owner_note)
    evidence = ["机主说明：" + owner_note.strip()] + list(evidence)
    # I-115 证据机器复核（影子档先行，无痛三原则：影子先行、只增不改、
    # 旧事项不回溯）：复核报告只入影子档，任何异常不阻断关闭；档位
    # 全局默认记录档，指挥部拧到拒绝档后本段才可能阻断。
    try:
        import evidence_audit as _ea
        _lanes_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lanes.json")
        _lanes = json.load(open(_lanes_path, encoding="utf-8"))["lanes"]
        _lane = None
        if cloud_client.enabled():
            try:
                _lane = cloud_client.request(
                    "GET", "/api/item-detail?id=" + item_id
                )["result"]["item"].get("lane")
            except Exception:
                _lane = None
        if _lane is None:
            for _i in _load().get("items", []):
                if _i["id"] == item_id:
                    _lane = _i.get("lane")
                    break
        _rep = _ea.audit_evidence(item_id, _lane or "未派发", evidence, _lanes)
        _rep["closure_context"] = {"closer": closer, "conclusion": conclusion}
        _ea.shadow_record(_rep)
    except Exception as _e:
        # 审计失败不阻断关闭（无痛原则），但必须留痕：静默丢失在
        # I-115 与 I-124 两轮施工中各吞掉一次真实异常，此为根治。
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "audit_errors.log"), "a", encoding="utf-8") as _f:
                _f.write("%s close %s 审计异常：%r\n"
                         % (datetime.datetime.now().isoformat(timespec="seconds"),
                            item_id, _e))
        except Exception:
            pass
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/close", {
            "item_id": item_id, "evidence": evidence, "closer": closer,
            "conclusion": conclusion, "criterion_check": criterion_check})
    obj = _load()
    for item in obj["items"]:
        if item["id"] == item_id:
            if item["status"] == "closed":
                raise ValueError("事项 %s 已关闭，重复关闭被拒绝" % item_id)
            item["status"] = "closed"
            item["closed_at"] = datetime.date.today().isoformat()
            item["closed_by"] = closer
            # I-060 修正：完成记录必须带执行人，认领人缺位时由结载人补位
            if not item.get("claimed_by"):
                item["claimed_by"] = closer
                if not item.get("claimed_at"):
                    item["claimed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            item["conclusion"] = conclusion
            item["criterion_check"] = criterion_check
            item["evidence"] = item.get("evidence", []) + evidence
            _save(obj)
            return
    raise KeyError("事项不存在：%s" % item_id)


_BLOCKER_TYPES = ("缺外部输入", "等机主决策", "依赖未清", "工具或环境故障", "判据争议")


def block(item_id, reason, blocker_type, needs, evidence, reporter="", owner_note=""):
    """申报卡住。这是事项离开进行中状态的第二条通道。

    必填交付物（缺一拒绝）：
      reason：卡住的直接原因，一句话，必须可对照证据；
      blocker_type：阻塞类型，只允许五种之一：%s；
      needs：解除阻塞需要什么，必须引用事项编号或写明「机主」；
      evidence：已完成工作的证据链接，至少一条；
      owner_note：机主说明，面向机主的完整解释，不少于六十字。
    申报后认领自动释放，事项状态为 blocked，由架构师决定走向。
    """ % (_BLOCKER_TYPES,)
    if blocker_type not in _BLOCKER_TYPES:
        raise ValueError("卡住申报被拒绝：blocker_type 必须是 %s 之一" % (_BLOCKER_TYPES,))
    if not reason or len(reason) < 10:
        raise ValueError("卡住申报被拒绝：reason 至少十个字")
    if not needs:
        raise ValueError("卡住申报被拒绝：必须写明解除阻塞需要什么")
    if not evidence:
        raise ValueError("卡住申报被拒绝：必须提供已完成工作的证据")
    _check_owner_note(owner_note)
    evidence = ["机主说明：" + owner_note.strip()] + list(evidence)
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/block", {
            "item_id": item_id, "reason": reason, "blocker_type": blocker_type,
            "needs": needs, "evidence": evidence, "reporter": reporter})
    obj = _load()
    for item in obj["items"]:
        if item["id"] == item_id:
            if item["status"] == "closed":
                raise ValueError("事项 %s 已关闭，不能申报卡住" % item_id)
            item["status"] = "blocked"
            item["blocked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            item["blocked_by"] = reporter
            item["block_reason"] = reason
            item["blocker_type"] = blocker_type
            item["block_needs"] = needs
            item["claimed_by"] = None
            item["lease_until"] = None
            item["evidence"] = item.get("evidence", []) + evidence
            _save(obj)
            return
    raise KeyError("事项不存在：%s" % item_id)


def unblock(item_id, decision, decider="架构师"):
    """架构师解除卡住：写明处置决定，事项退回 open 重新进入认领。"""
    if not decision or len(decision) < 10:
        raise ValueError("解除卡住必须写明处置决定，至少十个字")
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/unblock", {
            "item_id": item_id, "decision": decision, "decider": decider})
    obj = _load()
    for item in obj["items"]:
        if item["id"] == item_id:
            if item["status"] != "blocked":
                raise ValueError("事项 %s 不是 blocked 状态" % item_id)
            item["status"] = "open"
            item["unblock_decision"] = decision
            item["unblocked_by"] = decider
            item["unblocked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            _save(obj)
            return
    raise KeyError("事项不存在：%s" % item_id)



def list_open():
    """列出全部未关闭事项。任何汇报开头必须调用本函数并附上输出。"""
    obj = _load()
    return [i for i in obj["items"] if i["status"] != "closed"]


def stale(days=7):
    """列出开启超过指定天数仍未关闭的事项，供周期审查。"""
    today = datetime.date.today()
    out = []
    for i in _load()["items"]:
        if i["status"] != "closed":
            opened = datetime.date.fromisoformat(i["opened_at"])
            if (today - opened).days >= days:
                out.append(i)
    return out


def audit():
    """校验：全部已关闭事项必须同时有证据与关闭时间。"""
    bad = []
    for i in _load()["items"]:
        if i["status"] == "closed" and (not i["evidence"] or not i["closed_at"]):
            bad.append(i["id"])
    return bad


LOCK_PATH = os.path.join(_DIR, ".ledger.lock")


@contextlib.contextmanager
def _lock(timeout_s=10):
    """原子锁：与 dispatch.py 共用同一个锁文件，保证看板并发写账互斥。"""
    deadline = time.time() + timeout_s
    fd = None
    while fd is None:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() > deadline:
                raise RuntimeError("总账被其他会话占用超过十秒，稍后再试")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        os.remove(LOCK_PATH)


def owner_reply(item_id, text, actor="机主"):
    """机主答复：追加进事项的 replies 字段，只追加不修改。"""
    if not text or not text.strip():
        raise ValueError("答复内容为空被拒绝")
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/owner_reply",
                                  {"item_id": item_id, "text": text, "actor": actor})
    with _lock():
        obj = _load()
        for item in obj["items"]:
            if item["id"] == item_id:
                reply = {
                    "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                    "actor": actor,
                    "text": text.strip(),
                }
                item["replies"] = item.get("replies", []) + [reply]
                _save(obj)
                events.append("owner_reply", item_id=item_id, actor=actor, detail=text.strip()[:120])
                return reply
    raise KeyError("事项不存在：%s" % item_id)


def add_report(report):
    """施工 agent 申报：追加进总账顶层 reports 字段，谁申报、何时、申报了什么全部留痕。

    report 必须包含 model 字段，其余字段按申报内容原样留痕。
    """
    if not report.get("model"):
        raise ValueError("申报被拒绝：必须提供模型名称")
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/add_report", {"report": report})
    entry = dict(report)
    entry["reported_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock():
        obj = _load()
        if "reports" not in obj:
            obj["reports"] = []
        obj["reports"].append(entry)
        _save(obj)
        events.append("report", item_id=entry.get("item_id", ""), actor=entry.get("model", ""),
                      detail="申报事项 %s，日志指针 %s" % (entry.get("item_id", "无"), entry.get("log_pointer", "无")))
    return entry


def set_criterion(item_id, index, verified, evidence="", actor=""):
    """判据子项验证状态变更。勾选验证必须附证据描述，取消验证留痕。"""
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/set_criterion", {
            "item_id": item_id, "index": index, "verified": verified,
            "evidence": evidence, "actor": actor})
    with _lock():
        obj = _load()
        for item in obj["items"]:
            if item["id"] == item_id:
                crit = item.get("criteria")
                if not crit:
                    raise ValueError("事项 %s 未登记判据子项" % item_id)
                if not 0 <= index < len(crit):
                    raise IndexError("判据子项序号越界：%d" % index)
                if verified and not evidence.strip():
                    raise ValueError("勾选验证必须附证据描述")
                crit[index]["verified"] = bool(verified)
                crit[index]["evidence"] = evidence.strip()
                crit[index]["verified_by"] = actor
                crit[index]["verified_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                _save(obj)
                events.append("criterion", item_id=item_id, actor=actor,
                              detail="子项 %d %s：%s" % (index + 1, "验证" if verified else "取消验证", crit[index]["text"][:80]))
                return crit[index]
    raise KeyError("事项不存在：%s" % item_id)


def set_depends(item_id, depends_on, author=""):
    """登记依赖清单（阻断关系，供认领拦截与关键路径计算）。"""
    import re
    for ref in depends_on or []:
        if not re.fullmatch(r"I-\d{3}", ref):
            raise ValueError("依赖编号非法：%s" % ref)
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/set_depends", {
            "item_id": item_id, "depends_on": depends_on or []})
    with _lock():
        obj = _load()
        for item in obj["items"]:
            if item["id"] == item_id:
                item["depends_on"] = depends_on or []
                _save(obj)
                events.append("set_depends", item_id=item_id, actor=author,
                              detail="依赖清单设为 %s" % (depends_on or []))
                return item["depends_on"]
    raise KeyError("事项不存在：%s" % item_id)


def set_rationale(item_id, rationale, author=""):
    """登记事项来由：从哪条需求拆出，一句话规范中文。修订一数据底座。"""
    if not rationale or not rationale.strip():
        raise ValueError("来由为空被拒绝")
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/set_rationale", {
            "item_id": item_id, "rationale": rationale, "author": author})
    with _lock():
        obj = _load()
        for item in obj["items"]:
            if item["id"] == item_id:
                item["rationale"] = rationale.strip()
                _save(obj)
                events.append("rationale", item_id=item_id, actor=author,
                              detail=rationale.strip()[:120])
                return item["rationale"]
    raise KeyError("事项不存在：%s" % item_id)


def set_line(item_id, line, author=""):
    """登记事项逻辑线归属（L0 至 L8 固定轴）。"""
    import re
    if not re.fullmatch(r"L[0-8]", line or ""):
        raise ValueError("逻辑线编号非法：%s" % line)
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/set_line", {
            "item_id": item_id, "line": line, "author": author})
    with _lock():
        obj = _load()
        for item in obj["items"]:
            if item["id"] == item_id:
                item["line"] = line
                _save(obj)
                events.append("line", item_id=item_id, actor=author,
                              detail="逻辑线归属设为 %s" % line)
                return line
    raise KeyError("事项不存在：%s" % item_id)


def set_enables(item_id, enables, author=""):
    """登记启发关系（非阻断）：本事项产出使哪些事项的设计成为可能。"""
    import re
    for ref in enables or []:
        if not re.fullmatch(r"I-\d{3}", ref):
            raise ValueError("启发编号非法：%s" % ref)
    if cloud_client.enabled():
        return cloud_client.write("/api/ledger/set_enables", {
            "item_id": item_id, "enables": enables or [], "author": author})
    with _lock():
        obj = _load()
        for item in obj["items"]:
            if item["id"] == item_id:
                item["enables"] = enables or []
                _save(obj)
                events.append("enables", item_id=item_id, actor=author,
                              detail="启发关系设为 %s" % (enables or []))
                return item["enables"]
    raise KeyError("事项不存在：%s" % item_id)


def submit_pending_review(item_id, run_id, delivery_version, evidence_ptr, reviewer,
                          unmet=None, owner_note=""):
    body = {"item_id": item_id, "run_id": run_id, "delivery_version": delivery_version,
            "evidence_ptr": evidence_ptr, "reviewer": reviewer, "unmet": unmet or [],
            "owner_note": owner_note}
    if not all(body[k] for k in ("run_id", "delivery_version", "evidence_ptr", "reviewer")):
        raise ValueError("待验收提交缺少运行、版本、证据或审核人")
    if cloud_client.enabled():
        return cloud_client.request("POST", "/api/ledger/submit_pending_review", body)
    with _lock():
        obj = _load()
        item = next((i for i in obj["items"] if i["id"] == item_id), None)
        if item is None:
            raise KeyError(item_id)
        prior = item.get("pending_review") or {}
        if item["status"] == "pending_review" and all(prior.get(k) == body[k] for k in
                     ("run_id", "delivery_version", "evidence_ptr", "reviewer")):
            return {"item_id": item_id, "status": "pending_review", "reconciled": True}
        if item["status"] != "in_progress" or item.get("reviewer") != reviewer:
            raise ValueError("事项状态或审核人不符")
        item["status"] = "pending_review"
        item["claimed_by"] = None
        item["lease_until"] = None
        item["pending_review"] = {k: body[k] for k in
                   ("run_id", "delivery_version", "evidence_ptr", "reviewer", "unmet")}
        _save(obj)
        events.append("note", item_id=item_id, actor="pending-review",
                      detail="施工提交待验收，运行 %s，版本 %s，证据 %s" % (run_id, delivery_version, evidence_ptr))
        return {"item_id": item_id, "status": "pending_review"}


def _print_default():
    """无参数入口的打印主体，保持历史行为不变。"""
    items = _load()["items"]
    open_items = [i for i in items if i["status"] != "closed"]
    closed_items = [i for i in items if i["status"] == "closed"]
    print("事项总数：%d，未关闭：%d，已关闭：%d" % (len(items), len(open_items), len(closed_items)))
    for i in open_items:
        print("  [%s] %s（开启 %s，负责人 %s）" % (i["id"], i["title"], i["opened_at"], i["owner"]))
    bad = audit()
    print("审计：%s" % ("全部已关闭事项证据齐全" if not bad else "缺证据：" + str(bad)))


def _print_list():
    """子命令 list 的打印主体：open 与 blocked 事项摘要。"""
    items = list_open()
    for status in ("open", "blocked"):
        group = [i for i in items if i["status"] == status]
        print("%s 事项 %d 条：" % (status, len(group)))
        for i in group:
            print("  [%s] %s（开启 %s，负责人 %s）" % (i["id"], i["title"], i["opened_at"], i["owner"]))


def _print_stale(days):
    """子命令 stale 的打印主体：开启超过指定天数仍未关闭的事项。"""
    result = stale(days)
    print("开启超过 %d 天未关闭事项 %d 条：" % (days, len(result)))
    for i in result:
        print("  [%s] %s（开启 %s，负责人 %s）" % (i["id"], i["title"], i["opened_at"], i["owner"]))


def main(argv):
    """命令行入口。无参数保持原有打印；子命令：list、audit、stale <天数>。"""
    if not argv:
        _print_default()
        return 0
    cmd = argv[0]
    if cmd == "list":
        _print_list()
        return 0
    if cmd == "audit":
        bad = audit()
        print("违规编号：%s" % (str(bad) if bad else "无，全部已关闭事项证据齐全"))
        return 1 if bad else 0
    if cmd == "stale":
        days = int(argv[1]) if len(argv) > 1 else 7
        _print_stale(days)
        return 0
    print("未知子命令：%s（可用：list、audit、stale <天数>）" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
