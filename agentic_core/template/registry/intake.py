# -*- coding: utf-8 -*-
"""intake.py — 待审队列与收纳审理（lint）。

职责边界：
  施工队与任何 agent 只能向待审队列提交（submit）。
  架构师或机主批准后（approve），事项才进入正式清单 items.json。
  lint() 是四规则自动审理，输出建议与驳回理由，不直接决定入册。

四规则：
  R1 完整性：标题与打勾判据必填，判据不少于十个字。
  R2 去重：与在册及在审事项标题相似度超过 0.6 给出合并建议。
  R3 可检验性：判据不得只有空泛动词（完善、优化、研究、跟进），
     必须含可判真伪的内容。
  R4 归位：按 lanes.json 各路线 keywords 字段建议路线，
     标题中引用的事项编号自动转为 depends_on 建议。
仅使用 Python 标准库。
"""
import difflib
import json
import os
import re
import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(_DIR, "review_queue.json")
ITEMS_PATH = os.path.join(_DIR, "items.json")
LANES_PATH = os.path.join(_DIR, "lanes.json")

_VAGUE = ["完善", "优化", "研究一下", "跟进", "看看", "了解一下"]


def _lane_keywords():
    """归位关键词表：读 lanes.json 各路线的 keywords 字段。
    关键词由项目在路线登记时自填，本模块不内置任何项目词表。"""
    try:
        with open(LANES_PATH, "r", encoding="utf-8") as f:
            lanes = json.load(f)["lanes"]
        return {lane: cfg.get("keywords", []) for lane, cfg in lanes.items()}
    except (OSError, ValueError, KeyError):
        return {}


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def submit(title, goal_context, close_criterion, submitted_by, note="", depends_on=None):
    """任何 agent 的唯一入口：提交新事项进待审队列。返回队列编号。

    goal_context（目标上下文）为必填栏，写明在做哪个目标时遇到
    这个问题，栏位紧随问题描述（标题）之后；缺栏或过短提交被拒收。"""
    if not (goal_context or "").strip() or len(goal_context.strip()) < 6:
        raise ValueError("申报被拒收：目标上下文为必填栏（写清在做哪个目标"
                         "时遇到这个问题，不少于六个字），位置紧随问题描述之后")
    queue = _load(QUEUE_PATH, {"schema_version": 1, "queue": []})
    nums = [int(q["qid"].split("-")[1]) for q in queue["queue"]] or [0]
    qid = "Q-%03d" % (max(nums) + 1)
    queue["queue"].append({
        "qid": qid,
        "title": title,
        "goal_context": goal_context.strip(),
        "close_criterion": close_criterion,
        "submitted_by": submitted_by,
        "submitted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "depends_on": depends_on or [],
        "review": "pending",
    })
    _save(QUEUE_PATH, queue)
    return qid


def lint(qid):
    """四规则审理，返回报告字典。报告写入队列记录，供批准人参考。"""
    queue = _load(QUEUE_PATH, {"queue": []})
    items = _load(ITEMS_PATH, {"items": []})["items"]
    report = {"qid": qid, "checks": [], "verdict": "pass", "suggested_lane": "未定",
              "suggested_depends_on": [], "merge_candidates": []}
    rec = next((q for q in queue["queue"] if q["qid"] == qid), None)
    if rec is None:
        raise KeyError("队列记录不存在：%s" % qid)

    # R1 完整性
    ok = bool(rec["title"].strip()) and len(rec["close_criterion"].strip()) >= 10
    report["checks"].append(("R1 完整性", "过" if ok else "拒：标题或判据缺失，判据至少十个字"))
    if not ok:
        report["verdict"] = "reject"

    # R3 可检验性
    vague_hit = [w for w in _VAGUE if w in rec["close_criterion"]]
    ok = not vague_hit
    report["checks"].append(("R3 可检验性", "过" if ok else "拒：判据含空泛表述 %s" % vague_hit))
    if not ok:
        report["verdict"] = "reject"

    # R2 去重
    for other in items + queue["queue"]:
        ot = other.get("title", "")
        if other.get("id") == qid or other.get("qid") == qid:
            continue
        ratio = difflib.SequenceMatcher(None, rec["title"], ot).ratio()
        if ratio > 0.6:
            report["merge_candidates"].append({"with": other.get("id") or other.get("qid"),
                                               "title": ot, "similarity": round(ratio, 2)})
    report["checks"].append(("R2 去重", "过" if not report["merge_candidates"]
                             else "建议合并：%s" % [m["with"] for m in report["merge_candidates"]]))

    # R4 归位
    scores = {lane: sum(1 for kw in kws if kw in rec["title"] + rec["close_criterion"])
              for lane, kws in _lane_keywords().items()}
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] > 0:
        report["suggested_lane"] = best[0]
    report["suggested_depends_on"] = sorted(set(re.findall(r"I-\d{3}", rec["title"] + rec["note"])))
    report["checks"].append(("R4 归位", "建议路线 %s，依赖 %s" % (report["suggested_lane"], report["suggested_depends_on"])))

    # R5 语言规范
    import langcheck
    lv = langcheck.scan_text(rec["title"] + "。" + rec["close_criterion"])
    report["checks"].append(("R5 语言规范", "过" if not lv
                             else "拒：%s" % ["%s「%s」" % (c, w) for c, w, _ in lv]))
    if lv:
        report["verdict"] = "reject"

    # R6 降标准语义拦截：申报材料含降标准语义直接判拒，
    # 误拦申诉走指挥部人工复核（词表 standards_words.json 可增补）。
    sv = langcheck.scan_standards("。".join(
        [rec["title"], rec.get("goal_context", ""), rec["close_criterion"], rec.get("note", "")]))
    report["checks"].append(("R6 降标准语义", "过" if not sv
                             else "拒：%s（确属误拦走指挥部人工复核申诉）"
                                  % ["%s「%s」" % (c, w) for c, w, _ in sv]))
    if sv:
        report["verdict"] = "reject"

    rec["review"] = "linted"
    rec["lint_report"] = report
    _save(QUEUE_PATH, queue)
    return report


def approve(qid, approver, lane=None, owner_note=""):
    """批准入册：lint 判拒的记录需先改判据再提交。返回正式事项编号。

    owner_note 为必填的机主说明（D-023）：面向机主完整说明这是什么、
    为什么要做、谁来做、做成什么样算完、依据在哪里。写入事项 note 的
    【机主说明】段，原备注保留在【工程备注】段。
    """
    import ledger
    try:
        import cloud_client
    except ImportError:
        cloud_client = None
    queue = _load(QUEUE_PATH, {"schema_version": 1, "queue": []})
    rec = next((q for q in queue["queue"] if q["qid"] == qid), None)
    if rec is None:
        raise KeyError("队列记录不存在：%s" % qid)
    if rec.get("review") == "pending":
        lint(qid)
        rec = next(q for q in queue["queue"] if q["qid"] == qid)
    report = rec.get("lint_report", {})
    if report.get("verdict") == "reject":
        raise ValueError("lint 判拒，禁止入册；请修订判据后重新提交。理由：%s"
                         % [c for c in report["checks"] if c[1].startswith("拒")])
    ledger._check_owner_note(owner_note)
    # 决定材料（机主说明）同样过降标准语义拦截
    import langcheck as _lc
    sv = _lc.scan_standards(owner_note)
    if sv:
        raise ValueError("决定材料被拦截退回：%s（确属误拦走指挥部人工复核申诉）"
                         % ["%s「%s」" % (c, w) for c, w, _ in sv])
    note = ("【机主说明】%s\n【工程备注】入册批准人 %s；lint 报告附档 %s。%s"
            % (owner_note.strip(), approver, qid, rec.get("note", "")))
    if cloud_client is not None and cloud_client.enabled():
        # 云端模式：入册与依赖落账都走云端，本地 items.json 是缓存不得直写。
        new_id = ledger.add(rec["title"], lane or report.get("suggested_lane", "未定"),
                            rec["close_criterion"],
                            note=note,
                            depends_on=report.get("suggested_depends_on") or [])
    else:
        new_id = ledger.add(rec["title"], lane or report.get("suggested_lane", "未定"),
                            rec["close_criterion"],
                            note=note)
        if report.get("suggested_depends_on"):
            items = _load(ITEMS_PATH, {"items": []})
            for i in items["items"]:
                if i["id"] == new_id:
                    i["depends_on"] = report["suggested_depends_on"]
            _save(ITEMS_PATH, items)
    rec["review"] = "approved"
    rec["approved_as"] = new_id
    rec["approved_by"] = approver
    rec["approved_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(QUEUE_PATH, queue)
    return new_id


def reject(qid, approver, reason):
    """驳回，留痕。"""
    queue = _load(QUEUE_PATH, {"queue": []})
    rec = next((q for q in queue["queue"] if q["qid"] == qid), None)
    if rec is None:
        raise KeyError("队列记录不存在：%s" % qid)
    rec["review"] = "rejected"
    rec["rejected_by"] = approver
    rec["reject_reason"] = reason
    rec["rejected_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(QUEUE_PATH, queue)


def review_package(qid):
    """为人工或模型审理生成固定大小的上下文包。

    上下文爆炸的根治方法不是精简提示词，而是固定包裹内容：
    审理者只允许看到这个包裹，不允许访问仓库。包裹五项内容，
    全部来自账本自身，与仓库规模无关。
    """
    queue = _load(QUEUE_PATH, {"queue": []})
    items = _load(ITEMS_PATH, {"items": []})["items"]
    rec = next((q for q in queue["queue"] if q["qid"] == qid), None)
    if rec is None:
        raise KeyError("队列记录不存在：%s" % qid)
    report = rec.get("lint_report") or lint(qid)
    deps = report.get("suggested_depends_on", [])
    return {
        "candidate": {"title": rec["title"], "goal_context": rec.get("goal_context", ""),
                      "close_criterion": rec["close_criterion"],
                      "note": rec["note"], "depends_on": deps},
        "lint_report": report["checks"],
        "existing_titles": [{"id": i["id"], "title": i["title"], "status": i["status"]}
                            for i in items if i["status"] != "closed"],
        "dependency_neighbors": [{"id": i["id"], "title": i["title"]}
                                 for i in items if i["id"] in deps],
        "verdict_options": ["approve", "reject", "return_for_revision"],
    }


if __name__ == "__main__":
    queue = _load(QUEUE_PATH, {"queue": []})
    pending = [q for q in queue["queue"] if q["review"] in ("pending", "linted")]
    print("待审 %d 条" % len(pending))
    for q in pending:
        print("  [%s] %s（提交人 %s）" % (q["qid"], q["title"], q["submitted_by"]))
