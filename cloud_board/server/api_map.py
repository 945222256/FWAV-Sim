# -*- coding: utf-8 -*-
"""api_map.py — 全景作战地图只读接口：目标树、逻辑线、依赖图、关键路径。

数据纪律：一切数字实时读自账本；层完成度=已完成事项数除以事项总数
（修订一第四条），判据子项口径保留在事项级下钻；依赖读 depends_on
（阻断，实线），启发读 enables（不阻断，虚线）；禁止自报百分比。
仅使用 Python 标准库。
"""
import datetime
import json
import os

import auth
import db
import repo
import router

_DIR = os.path.dirname(os.path.abspath(__file__))
_REG = os.path.join(os.path.dirname(_DIR), "items_registry")

# 逻辑线关键词兜底映射：仅当事项未登记 line 字段时使用。
_KEYWORD_LINES = {
    "L0": ["基准", "判据", "科目", "五科目", "持续集成", "窄缝"],
    "L1": ["确定性", "回归门禁", "双胞胎", "门禁", "退出码", "装配错位", "符号未提交", "硬编码"],
    "L2": ["架构", "抽象", "拆分", "开关", "硬件抽象", "契约", "基类", "桥", "模态层", "规格"],
    "L3": ["管线", "配置文件", "重放", "一次性脚本", "统一运行器", "收编归档"],
    "L4": ["参数", "登记库", "迁移", "口径", "销账", "总账", "取数", "urdf", "惯量", "质量", "注释", "数据列", "力矩接口"],
    "L5": ["辨识", "多正弦", "频域", "留出", "导数矩阵", "补偿", "共振", "模态", "盘旋", "配平",
           "激励", "估计器", "弗洛凯", "谐波", "滑流", "拍锁", "对拍"],
    "L6": ["代理实物", "接驳", "真机", "闭环演练"],
    "L7": ["学习", "强化学习", "向量化", "气动契约"],
    "L8": ["看板", "协作", "治理", "规则", "派发", "lanes", "域名", "可安装", "响应式", "自检", "云端化", "审批", "收编归档"],
}

@router.route("GET", "/api/blueprints")
def blueprints(ctx):
    """战略蓝图应然层只读接口（I-167）：十一块四要素，格格带出处指针。

    数据纪律：云端镜像优先（同步代理心跳上行，本地登记册为权威源），
    镜像缺失时回退本地文件；每块携带出处指针指向核定稿与登记册。
    """
    auth.caller(ctx)
    blocks = repo.get_config("blueprints")
    source_kind = "table"
    source_locus = "config 行 blueprints"
    if not blocks:
        path = os.path.join(_REG, "blueprints.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                blocks = json.load(f).get("blocks", [])
            source_kind = "file"
            source_locus = "items_registry/blueprints.json"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    out = []
    for b in blocks:
        prov = {
            "kind": source_kind,
            "locator": source_locus,
            "excerpt": "%s %s（%s 核定，出处 %s）"
                        % (b.get("id", ""), (b.get("name") or "")[:60],
                           b.get("approved_at", "?"), b.get("source_doc", "?")),
            "sampled_at": now,
        }
        out.append({
            "id": b.get("id"), "name": b.get("name"),
            "description": b.get("description"),
            "vision": b.get("vision") or [],
            "lanes": b.get("lanes") or [],
            "collaborations": b.get("collaborations") or [],
            "approved_at": b.get("approved_at"), "approved_by": b.get("approved_by"),
            "note": b.get("note"),
            "source": prov,
        })
    return {"ok": True, "result": {
        "generated_at": now, "count": len(out), "blocks": out,
        "prov": {"kind": source_kind, "locator": source_locus,
                 "excerpt": "战略蓝图应然层镜像，登记源 items_registry/blueprints.json",
                 "sampled_at": now},
    }}


_STATUS_WORDS = {"open": "未认领", "in_progress": "进行中", "blocked": "卡住", "closed": "完成"}
_LINE_ORDER = ["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]


def _load_lines():
    """逻辑线定义：云端镜像优先，本地文件兜底。文件是权威来源（修订一第五条）。"""
    row = db.query_one("SELECT value FROM config WHERE key='logical_lines'")
    if row is not None:
        data = json.loads(row["value"])
        if isinstance(data, dict) and data.get("lines"):
            return data
    path = os.path.join(_REG, "logical_lines.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"lines": {}}


def _line_of(item, lines_conf):
    line = (item.get("line") or "").strip()
    if line in lines_conf:
        return line
    text = item["title"] + " " + (item.get("note") or "") + " " + (item.get("close_criterion") or "")
    for lid in _LINE_ORDER:
        for key in _KEYWORD_LINES.get(lid, []):
            if key in text.lower() or key in text:
                return lid
    return "L5"


def _depth(items_by_id):
    closed = {i for i in items_by_id if items_by_id[i]["status"] == "closed"}
    memo = {}

    def get(n):
        if n in memo:
            return memo[n]
        memo[n] = 0                        # 环保护：先置零再递归。
        deps = [d for d in items_by_id[n].get("depends_on", [])
                if d in items_by_id and d not in closed]
        memo[n] = 1 + max((get(d) for d in deps), default=0)
        return memo[n]

    for i in items_by_id:
        get(i)
    return memo


def _critical_path(items_by_id, depth_memo):
    """关键路径：未关闭依赖链最长的事项链，从链尾列到链头。"""
    open_ids = [i for i in items_by_id if items_by_id[i]["status"] != "closed"]
    if not open_ids:
        return []
    deepest = max(open_ids, key=lambda x: depth_memo.get(x, 0))
    if depth_memo.get(deepest, 0) == 0:
        return []
    chain = [deepest]
    cur = deepest
    while True:
        deps = [d for d in items_by_id[cur].get("depends_on", [])
                if d in items_by_id and items_by_id[d]["status"] != "closed"]
        if not deps:
            break
        cur = max(deps, key=lambda x: depth_memo.get(x, 0))
        chain.append(cur)
    return chain if len(chain) >= 2 else []   # 单节点不成"路径"，返回空由前端隐藏。


@router.route("GET", "/api/item-detail")
def item_detail(ctx):
    """节点下钻详情：判据对照、证据链、时间线，实时读账本。"""
    auth.caller(ctx)
    item_id = (ctx.query.split("id=")[-1] if "id=" in ctx.query else "").strip()
    if not item_id:
        raise ValueError("缺少 id 查询参数")
    item = repo.get_item(item_id)
    if item is None:
        raise KeyError("事项不存在：%s" % item_id)
    return {"ok": True, "result": {
        "item": {k: item.get(k) for k in (
            "id", "title", "status", "opened_at", "closed_at", "closed_by",
            "owner", "lane", "claimed_by", "close_criterion", "note",
            "evidence", "criteria", "replies", "conclusion", "criterion_check",
            "rationale", "line", "check_script", "check_result",
            "kind", "goal", "enables", "depends_on")},
        "events": repo.events_for_item(item_id),
    }}


@router.route("GET", "/api/map")
def board_map(ctx):
    auth.caller(ctx)
    items = repo.all_items()
    items_by_id = {i["id"]: i for i in items}
    lines_conf = _load_lines()["lines"]

    goals = repo.get_config("goals", []) or []
    if not goals:
        path = os.path.join(_REG, "goals.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                goals = json.load(f).get("goals", [])

    # 第一层：目标树。层完成度=已完成事项数除以事项总数（修订一第四条）。
    layers_view = []
    for lid in _LINE_ORDER:
        if lid not in lines_conf:
            continue
        conf = lines_conf[lid]
        members = [i for i in items if _line_of(i, lines_conf) == lid]
        closed = len([i for i in members if i["status"] == "closed"])
        layers_view.append({
            "id": lid, "name": conf.get("name", lid),
            "mission": conf.get("serves", ""),
            "total": len(members), "closed": closed,
            "progress": (round(closed / len(members), 3) if members else None),
            "reason": (None if members else "该线暂无挂账事项，完成度口径不适用"),
        })

    # 第二层：依赖图。节点带逻辑线与来由；边分依赖（实线）与启发（虚线）。
    depth_memo = _depth(items_by_id)
    nodes = []
    for i in items:
        nodes.append({
            "id": i["id"], "title": i["title"], "status": i["status"],
            "status_word": _STATUS_WORDS.get(i["status"], i["status"]),
            "lane": i.get("lane") or "未派发", "owner": i.get("owner"),
            "line": _line_of(i, lines_conf),
            "rationale": i.get("rationale") or "",
            "claimed_by": i.get("claimed_by"),
            "verified": (len([c for c in i["criteria"] if c.get("verified")])
                         if i.get("criteria") else None),
            "criteria_total": len(i["criteria"]) if i.get("criteria") else None,
            "check_script": i.get("check_script") or "",
            "check_result": i.get("check_result"),
            "depth": depth_memo.get(i["id"], 0),
        })
    edges = []
    for i in items:
        for d in i.get("depends_on", []) or []:
            if d in items_by_id:
                edges.append({"from": d, "to": i["id"], "kind": "dep",
                              "open": items_by_id[d]["status"] != "closed"})
        for t in i.get("enables", []) or []:
            if t in items_by_id:
                edges.append({"from": i["id"], "to": t, "kind": "enables",
                              "open": False})

    return {
        "ok": True,
        "result": {
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "goals": [{"id": g["id"], "text": g["text"], "status": g["status"]}
                      for g in goals if g.get("status") == "active"],
            "lines": layers_view,
            "nodes": nodes, "edges": edges,
            "critical_path": _critical_path(items_by_id, depth_memo),
            "counts": {
                "total": len(items),
                "open": len([i for i in items if i["status"] == "open"]),
                "in_progress": len([i for i in items if i["status"] == "in_progress"]),
                "blocked": len([i for i in items if i["status"] == "blocked"]),
                "closed": len([i for i in items if i["status"] == "closed"]),
                "owner_pending": len([i for i in items
                                      if i["status"] != "closed" and "机主" in (i.get("owner") or "")]),
            },
        },
    }
