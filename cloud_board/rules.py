# -*- coding: utf-8 -*-
"""rules.py — 账本纪律的服务端强制，云端唯一权威。

既有纪律全部保留并在此重新实现：完成带证据、认领时限与冲突拒绝、
一路一项在制限制、依赖拦截、重复证据拒绝、重复关闭拒绝、
收纳四条审理规则、人工改判必填理由。任何客户端校验都不算数。
仅使用 Python 标准库。
"""
import difflib
import datetime
import json
import os
import re

from httpd import ApiError

LANES = ("L1", "L2", "L3")

# I-124 降标准语义拦截：词表数据文件（指挥部可增补），服务端同源副本。
_STANDARDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "standards_words.json")


def scan_standards(text):
    """含降低判据、降低坡度、降档等降标准语义的材料拦截退回。
    误拦申诉走指挥部人工复核（overrides 放行留痕）。"""
    try:
        table = json.load(open(_STANDARDS_PATH, encoding="utf-8"))
    except Exception:
        return []
    out = []
    for w in table.get("standard_lowering", []):
        if w in (text or ""):
            out.append(("降标准语义", w, "拦截退回"))
    return out


def known_lanes():
    """已知路线全集（I-066）：领地配置为准，内建三路为下限兜底。

    路线注册走 /api/admin/lanes 机主会话接口写入领地配置；
    配置为空或读取失败时退回内建元组，行为与历史一致。"""
    try:
        import repo
        cfg = repo.get_config("lanes", {}) or {}
        if cfg:
            return tuple(sorted(set(cfg.keys()) | set(LANES)))
    except Exception:
        pass
    return LANES

_VAGUE_WORDS = ["大概", "可能", "尽量", "适当", "待定", "后续再看", "看看", "关注",
                "研究一下", "尽快", "差不多", "酌情", "视情况"]
_VERIFIABLE_WORDS = ["通过", "一致", "相等", "逐格", "低于", "高于", "不超过", "跑通",
                     "入库", "关闭", "存活", "误差", "判据", "全部通过", "逐项", "留出验证",
                     "重放", "逐字节", "无残留", "为零", "完好", "可用", "解析成功",
                     "可批准", "可驳回", "可见", "可完成", "退出码", "返回", "空清单"]
_PLACEMENT_KEYS = {
    "L1": ["辨识", "补偿", "科目", "模态", "耦合", "共振", "转弯", "盘旋", "惯量",
           "控制器", "增益", "拍动", "冲量", "阻尼", "悬停", "巡航", "rl/"],
    "L2": ["拆分", "管线", "基准", "开关", "契约", "抽象", "架构", "重构", "持续集成",
           "配置对象", "main_sim", "统一运行器", "实验等于配置", "看板", "云端"],
    "L3": ["参数", "迁移", "口径", "登记库", "数据资产", "urdf", "文档", "台账",
           "表", "pdf", "论文", "取数", "销账"],
}
_PATH_KEYS = {
    "L1": ["modules/flight_controller.py", "modules/flight_state_machine.py", "rl/"],
    "L2": ["main_sim.py", "config/", "modules/", "lp_board/", "cloud_board/"],
    "L3": ["params_registry/", "items_registry/", "docs/"],
}


def _now():
    return datetime.datetime.now()


# ---------------- 事项与派发 ----------------

CONCLUSIONS = ("判据通过", "判据不通过", "证据不足无法判定")
BLOCKER_TYPES = ("缺外部输入", "等机主决策", "依赖未清", "工具或环境故障", "判据争议")


def check_close(item, evidence, conclusion, criterion_check):
    if item is None:
        raise ApiError("事项不存在")
    if not evidence:
        raise ApiError("关闭被拒绝：必须提供至少一条证据链接")
    if item["status"] == "closed":
        raise ApiError("事项 %s 已关闭，重复关闭被拒绝" % item["id"])
    if conclusion not in CONCLUSIONS:
        raise ApiError("关闭被拒绝：conclusion 必须是 %s 之一" % (CONCLUSIONS,))
    if not criterion_check or not isinstance(criterion_check, list):
        raise ApiError("关闭被拒绝：criterion_check 必须是非空列表")
    for entry in criterion_check:
        keys = set(entry.keys()) if isinstance(entry, dict) else set()
        if not {"判据", "结果", "数值出处"}.issubset(keys):
            raise ApiError("关闭被拒绝：criterion_check 每条必须含 判据/结果/数值出处 三个键")
        if entry["结果"] not in ("过", "不过"):
            raise ApiError("关闭被拒绝：判据结果只允许 过 或 不过")
    open_blockers = [b for b in item.get("blockers", [])
                     if _blocker_open(item["id"], b)]
    if open_blockers:
        raise ApiError("关闭被拒绝：事项仍阻塞于未关闭事项 %s（依赖拦截）"
                       % "、".join(open_blockers))


def check_block(item, reason, blocker_type, needs, evidence):
    if blocker_type not in BLOCKER_TYPES:
        raise ApiError("卡住申报被拒绝：blocker_type 必须是 %s 之一" % (BLOCKER_TYPES,))
    if not reason or len(reason) < 10:
        raise ApiError("卡住申报被拒绝：reason 至少十个字")
    if not needs:
        raise ApiError("卡住申报被拒绝：必须写明解除阻塞需要什么")
    if not evidence:
        raise ApiError("卡住申报被拒绝：必须提供已完成工作的证据")
    if item is None:
        raise ApiError("事项不存在")
    if item["status"] == "closed":
        raise ApiError("事项 %s 已关闭，不能申报卡住" % item["id"])


def check_unblock(item, decision):
    if item is None:
        raise ApiError("事项不存在")
    if item["status"] != "blocked":
        raise ApiError("事项 %s 不是 blocked 状态" % item["id"])
    if not decision or len(decision) < 10:
        raise ApiError("解除卡住必须写明处置决定，至少十个字")


def _blocker_open(self_id, blocker_id):
    import repo
    target = repo.get_item(blocker_id)
    return target is not None and target["status"] != "closed"


_WORKER_ID_RE = re.compile(r"^(L\d{1,2}|HQ)-w[1-9]\d*$")


def lane_capacity_of(config, lane, upper=2):
    """I-217 补修：每线并发容量取管理员配置（lane_concurrency），
    截到 [1, upper]；缺省一。客户端自报的容量一律无效。"""
    try:
        raw = int((config or {}).get(lane) or (config or {}).get("default") or 1)
    except (TypeError, ValueError):
        raw = 1
    return max(1, min(upper, raw))


def check_claim(item, worker, all_items, lane_capacity=1, claim_worker_id=None,
                claim_session_id=None):
    """认领强制检查。I-217 补修后口径：
    一、已被他人认领且认领时限未过期的事项拒绝认领（不变）；
    二、在制限制按每线容量与固定工位：容量一维持一路一项；容量二时
        允许同一认领人持有第二项，前提是已有在办与本认领都报固定工位
        （<lane>-wN、线一致、序号在容量内）、工位互不重复、会话不与
        其他在办重复；容量由管理员配置决定，客户端不可自报；
    三、依赖未清拒绝，partial_start 先行范围除外（不变）。
    返回值含义同旧版：bool(open_deps)（认领方写先行范围用）。
    """
    if item is None:
        raise ApiError("事项不存在")
    if item["status"] == "closed":
        raise ApiError("事项 %s 已关闭" % item["id"])
    if item["status"] == "pending_review":
        raise ApiError("事项 %s 已提交待验收，不能重新施工认领" % item["id"])
    if any(v.get("review_required") for v in item.get("delivery_impact") or []) and not item.get("partial_start"):
        raise ApiError("事项上游交付撤回影响尚未复核，不能新开工")
    # 工位号形态与线一致性（报了就核，任何容量下都核）
    if claim_worker_id:
        if not _WORKER_ID_RE.match(claim_worker_id):
            raise ApiError("工位号形态非法：%r（须 <lane>-wN）" % claim_worker_id)
        if claim_worker_id.rsplit("-w", 1)[0] != item.get("lane"):
            raise ApiError("工位号与事项路线不一致：%s 不属 %s"
                           % (claim_worker_id, item.get("lane")))
        slot_index = int(claim_worker_id.rsplit("-w", 1)[1])
        if slot_index > lane_capacity:
            raise ApiError("工位 %s 超出本线容量 %d 的固定工位集"
                           % (claim_worker_id, lane_capacity))
    active = [other for other in all_items
              if other["status"] == "in_progress"
              and other.get("claimed_by") == worker
              and other["id"] != item["id"]]
    if active:
        if lane_capacity < 2:
            raise ApiError("在制限制：%s 手里还有 %s 未完成未申报卡住，"
                           "一路同时只许持有一个进行中事项"
                           % (worker, [o["id"] for o in active]))
        # 容量二：固定工位归属是并行持有的必要条件
        if not claim_worker_id:
            raise ApiError("本线容量为 %d，第二项认领必须报固定工位号"
                           % lane_capacity)
        held_workers = [o.get("claim_worker_id") for o in active]
        if any(not w for w in held_workers):
            raise ApiError("已有在办缺固定工位归属（%s），不允许再加第二项；"
                           "先补齐归属或等待完成"
                           % [o["id"] for o, w in zip(active, held_workers) if not w])
        if claim_worker_id in held_workers:
            raise ApiError("工位 %s 已持有在办事项，一工位一事项" % claim_worker_id)
        if len(active) + 1 > lane_capacity:
            raise ApiError("容量限制：%s 在办 %d 项已达本线上限 %d"
                           % (worker, len(active), lane_capacity))
    # 会话唯一性：同一会话不得同时持有两个在办事项（一工位一会话一运行）
    if claim_session_id:
        same_session = [other["id"] for other in all_items
                        if other["status"] == "in_progress"
                        and other.get("claim_session_id") == claim_session_id
                        and other["id"] != item["id"]]
        if same_session:
            raise ApiError("会话 %s 已持有在办事项 %s，一会话一事项"
                           % (claim_session_id, same_session))
    lease = item.get("lease_until")
    if item.get("claimed_by") and lease:
        if datetime.datetime.fromisoformat(lease) > _now():
            raise ApiError("事项 %s 正由 %s 认领中，认领时限至 %s"
                           % (item["id"], item["claimed_by"], lease))
    from dependency_policy import unmet_dependencies
    open_deps = unmet_dependencies(item, all_items)
    if open_deps and not item.get("partial_start"):
        raise ApiError("事项 %s 依赖未清（%s），不可认领；"
                       "确须先行须由架构师写明 partial_start 范围"
                       % (item["id"], open_deps))
    # I-093 路线一致性：认领人路线与事项路线不一致直接拒绝。
    # 与本地 dispatch.check_lane_consistency 同口径，服务端为强制权威。
    wl_m = re.match(r"^(L\d+|HQ)", (worker or "").strip())
    wl = wl_m.group(1) if wl_m else None
    il = item.get("lane")
    if wl and il and wl != il:
        raise ApiError(
            "路线不一致：事项 %s 属 %s 路线，认领人 %s 属 %s 路线；"
            "各队只许认领本路线事项，跨路线协作走登记新事项交给该路"
            % (item["id"], il, worker, wl))
    return bool(open_deps)


def check_release(item, worker):
    if item is None:
        raise ApiError("事项不存在")
    if item.get("claimed_by") != worker:
        raise ApiError("只有认领人 %s 可以释放该事项" % item.get("claimed_by"))


def check_evidence_text(item_id, text, existing):
    if not text or not text.strip():
        raise ApiError("证据为空字符串被拒绝")
    if text in existing:
        raise ApiError("证据已登记在 %s，重复追加被拒绝" % item_id)


def check_owner_reply(text):
    if not text or not text.strip():
        raise ApiError("答复内容为空被拒绝")


def check_assign(lane):
    if lane not in known_lanes():
        raise ApiError("路线不存在：%s（可选 %s）" % (lane, list(known_lanes())))


def check_criterion(item, index, verified, evidence):
    crit = item.get("criteria")
    if not crit:
        raise ApiError("事项 %s 未登记判据子项" % item["id"])
    if not 0 <= index < len(crit):
        raise ApiError("判据子项序号越界：%d" % index)
    if verified and not evidence.strip():
        raise ApiError("勾选验证必须附证据描述")


# ---------------- 收纳审理 ----------------

def _is_verifiable(text):
    if len(text.strip()) < 8:
        return False, "判据长度不足八个字符，无法构成可判真伪的陈述"
    for word in _VAGUE_WORDS:
        if word in text:
            return False, "含模糊词「%s」，判据必须可以判真伪" % word
    if text.strip().endswith("？") or text.strip().endswith("?") or "吗" in text or "是否" in text:
        return False, "判据是疑问句，不是可判真伪的陈述"
    if re.search(r"\d", text):
        return True, "判据含数值，可以判真伪"
    for word in _VERIFIABLE_WORDS:
        if word in text:
            return True, "判据含可检验动词「%s」" % word
    return False, "判据不含数值也不含可检验动词，无法判真伪"


def run_lint(entry, items, lanes):
    """四条审理规则：完整性、去重、可检验性、归位。结论是建议。"""
    rules = []
    missing = []
    if not entry.get("title", "").strip():
        missing.append("标题")
    if not entry.get("close_criterion", "").strip():
        missing.append("打勾判据")
    if not [c for c in entry.get("criteria", []) if c and c.strip()]:
        missing.append("判据子项")
    rules.append({"rule": "完整性", "verdict": "fail" if missing else "pass",
                  "reason": "缺少：" + "、".join(missing) if missing
                  else "标题、打勾判据、判据子项齐全"})

    duplicate_of = []
    for it in items:
        ratio = difflib.SequenceMatcher(None, entry.get("title", ""), it["title"]).ratio()
        if ratio > 0.55 or (entry.get("title", "") and entry["title"] in it["title"]) or \
           (it["title"] in entry.get("title", "") and len(it["title"]) > 6):
            duplicate_of.append({"id": it["id"], "title": it["title"],
                                 "ratio": round(ratio, 2), "status": it["status"]})
    if duplicate_of:
        dup_text = "；".join("%s 相似度 %s" % (d["id"], d["ratio"]) for d in duplicate_of[:3])
        rules.append({"rule": "去重", "verdict": "warn",
                      "reason": "与在册事项重复度过高：%s，建议合并" % dup_text})
    else:
        rules.append({"rule": "去重", "verdict": "pass",
                      "reason": "与全部在册事项标题相似度低于门槛"})

    bad_points = []
    ok, why = _is_verifiable(entry.get("close_criterion", ""))
    if not ok:
        bad_points.append("打勾判据：" + why)
    for idx, c in enumerate(entry.get("criteria", [])):
        if not c or not c.strip():
            continue
        ok_c, why_c = _is_verifiable(c)
        if not ok_c:
            bad_points.append("子项 %d：%s" % (idx + 1, why_c))
    rules.append({"rule": "可检验性", "verdict": "fail" if bad_points else "pass",
                  "reason": "；".join(bad_points) + "。请改写为可判真伪的陈述后重新申报"
                  if bad_points else "打勾判据与全部子项均可判真伪"})

    body = " ".join([entry.get("title", ""), entry.get("close_criterion", ""),
                     entry.get("note", ""), " ".join(entry.get("criteria", []))])
    scores = {lane: 0 for lane in lanes}
    reasons = {lane: [] for lane in lanes}
    for lane, keys in _PLACEMENT_KEYS.items():
        if lane not in scores:
            continue
        for key in keys:
            if key in body:
                scores[lane] += 2
                reasons[lane].append("正文命中关键词「%s」" % key)
    for lane, paths in _PATH_KEYS.items():
        if lane not in scores:
            continue
        for p in paths:
            if p in body:
                scores[lane] += 3
                reasons[lane].append("涉及本路独占路径「%s」" % p)
    for ref in re.findall(r"I-\d{3}", body):
        for it in items:
            if it["id"] == ref and it.get("lane") in scores:
                scores[it["lane"]] += 4
                reasons[it["lane"]].append("引用在办事项 %s，该事项属本路" % ref)
    best = max(scores, key=lambda k: scores[k]) if scores else ""
    if scores.get(best, 0) > 0:
        open_count = len([i for i in items if i.get("lane") == best and i["status"] != "closed"])
        rules.append({"rule": "归位", "verdict": "pass",
                      "reason": "建议进 %s，排在该路队尾第 %d 位。依据：%s"
                                % (best, open_count + 1, "；".join(reasons[best][:3]))})
        suggested = best
    else:
        rules.append({"rule": "归位", "verdict": "warn",
                      "reason": "无依赖线索，建议架构师裁决路线，暂列未派发"})
        suggested = ""

    # I-124 规二：降标准语义拦截（词表 standards_words.json，指挥部可增补；
    # 误拦申诉走人工复核经 overrides 放行留痕）
    sv = scan_standards(body)
    rules.append({"rule": "降标准语义", "verdict": "fail" if sv else "pass",
                  "reason": "；".join("%s「%s」" % (c, w) for c, w, _ in sv) +
                            "。降标准类申报不到机主眼前，确属误拦走指挥部人工复核"
                  if sv else "无降标准语义"})

    return {"checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "rules": rules, "suggested_lane": suggested,
            "duplicate_of": duplicate_of,
            "summary": "建议%s" % ("进 " + suggested if suggested else "由架构师裁决路线")}


def check_decide(entry, action, lane, reason):
    if action not in ("approve", "reject", "merge"):
        raise ApiError("未知决定动作：%s" % action)
    if entry.get("decision"):
        raise ApiError("条目 %s 已审理完毕，重复审理被拒绝" % entry["id"])
    if action == "approve":
        chosen = lane or entry["lint"]["suggested_lane"]
        if not chosen:
            raise ApiError("批准被拒绝：lint 未给出路线建议且未人工指定路线")
        has_fail = any(r["verdict"] == "fail" for r in entry["lint"]["rules"])
        differs = chosen != entry["lint"]["suggested_lane"]
        if (has_fail or differs) and not reason.strip():
            raise ApiError("批准被拒绝：人工改判了 lint 结论，必须填写改判理由留痕")
        return chosen, has_fail, differs
    return None, False, False


def parse_blockers(note, text=""):
    """从备注文本解析阻塞关系，结构化为 blockers 字段。"""
    hit = re.findall(r"阻塞于\s*(I-\d{3})", (note or "") + " " + (text or ""))
    return sorted(set(hit))
