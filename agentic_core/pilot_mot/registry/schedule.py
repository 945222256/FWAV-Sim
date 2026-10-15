# -*- coding: utf-8 -*-
"""schedule.py — 依赖图与排序：任务清单排序的标准表达。

模型：事项为节点，depends_on 为有向边，全体构成有向无环图
（Directed Acyclic Graph，DAG；指边有方向且不存在环的图）。
提供四个标准运算：
  validate()     校验无环、引用存在；
  ready()        当前可开工事项：全部依赖已关闭；
  topo_layers()  拓扑分层：第零层无依赖，第 N 层只依赖前 N-1 层；
  depth()        每个事项的未关闭依赖链长度，最长链即关键路径。
看板排序规则：先按拓扑层、再按编号。lint 归位以依赖图为准。
仅使用 Python 标准库。
"""
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
ITEMS_PATH = os.path.join(_DIR, "items.json")


def _items():
    with open(ITEMS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["items"]


def _by_id(items):
    return {i["id"]: i for i in items}


def validate():
    """返回错误列表，空列表表示图合法。"""
    items = _items()
    by_id = _by_id(items)
    errors = []
    for i in items:
        for d in i.get("depends_on", []):
            if d not in by_id:
                errors.append("%s 依赖不存在的 %s" % (i["id"], d))
    # 环检测：深度优先，灰标记在栈中节点
    color = {}
    def visit(n, stack):
        color[n] = 1
        for d in by_id[n].get("depends_on", []):
            if d not in by_id:
                continue
            if color.get(d) == 1:
                errors.append("依赖环：%s" % " -> ".join(stack + [d]))
            elif color.get(d) is None:
                visit(d, stack + [d])
        color[n] = 2
    for i in items:
        if color.get(i["id"]) is None:
            visit(i["id"], [i["id"]])
    return errors


def topo_layers():
    """返回 {事项编号: 层号}。依赖已关闭视同依赖不存在。"""
    items = _items()
    by_id = _by_id(items)
    closed = {i["id"] for i in items if i["status"] == "closed"}
    layer = {}

    def get(n):
        if n in layer:
            return layer[n]
        open_deps = [d for d in by_id[n].get("depends_on", [])
                     if d in by_id and d not in closed]
        layer[n] = 0 if not open_deps else 1 + max(get(d) for d in open_deps)
        return layer[n]

    for i in items:
        get(i["id"])
    return layer


def ready():
    """当前可开工事项：未关闭、未认领、全部依赖已关闭。按拓扑层与编号排序。"""
    items = _items()
    by_id = _by_id(items)
    closed = {i["id"] for i in items if i["status"] == "closed"}
    layer = topo_layers()
    out = []
    for i in items:
        if i["status"] != "open" or i.get("claimed_by"):
            continue
        deps = [d for d in i.get("depends_on", []) if d in by_id]
        if all(d in closed for d in deps):
            out.append(i)
    out.sort(key=lambda i: (layer[i["id"]], i["id"]))
    return out


def depth():
    """返回 {事项编号: 未关闭依赖链长度}。最大值对应的事项链即关键路径。"""
    items = _items()
    by_id = _by_id(items)
    closed = {i["id"] for i in items if i["status"] == "closed"}
    memo = {}

    def get(n):
        if n in memo:
            return memo[n]
        if n in closed:
            memo[n] = 0
            return 0
        open_deps = [d for d in by_id[n].get("depends_on", [])
                     if d in by_id and d not in closed]
        memo[n] = 1 if not open_deps else 1 + max(get(d) for d in open_deps)
        return memo[n]

    for i in items:
        get(i["id"])
    return memo


if __name__ == "__main__":
    errors = validate()
    print("依赖图校验：%s" % ("通过，无环无悬空引用" if not errors else errors))
    print("当前可开工：")
    for i in ready():
        print("  [%s] %s" % (i["id"], i["title"]))
    d = depth()
    crit = max(d.items(), key=lambda kv: kv[1])
    print("关键路径末端：%s，未关闭依赖链长度 %d" % crit)
