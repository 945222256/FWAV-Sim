# -*- coding: utf-8 -*-
"""registry.py — 参数登记库唯一读写入口。

纪律：任何代码、URDF、文档不允许直接抄写参数数值，只允许通过本模块按编号读取。
旧值永远不删除，只通过 retire() 退役进 archive.json。
仅使用 Python 标准库，Isaac Sim 自带解释器可直接运行。
"""
import json
import os
import datetime
from copy import deepcopy

_DIR = os.path.dirname(os.path.abspath(__file__))
PARAMS_PATH = os.path.join(_DIR, "params.json")
ARCHIVE_PATH = os.path.join(_DIR, "archive.json")


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def put(param_id, value, unit, source):
    """首次登记新参数。已存在且数值不同即拒绝——改值必须走 retire() 留档。"""
    params_obj = _load(PARAMS_PATH)
    if param_id in params_obj["params"]:
        old = params_obj["params"][param_id]["value"]
        if old != value:
            raise ValueError("参数 %s 已登记为 %r，改值必须走 retire() 留档"
                             % (param_id, old))
        return
    params_obj["params"][param_id] = {
        "value": value,
        "unit": unit,
        "status": "current",
        "source": source,
        "since": datetime.date.today().isoformat(),
    }
    _save(PARAMS_PATH, params_obj)


def get(param_id):
    """读取参数当前值。不存在即抛错，不允许静默缺省。"""
    params = _load(PARAMS_PATH)["params"]
    if param_id not in params:
        raise KeyError("参数未登记：%s（禁止临时造数，先入册再使用）" % param_id)
    return params[param_id]["value"]


def get_record(param_id):
    """读取参数的完整记录（数值、单位、来源、状态）。"""
    params = _load(PARAMS_PATH)["params"]
    if param_id not in params:
        raise KeyError("参数未登记：%s" % param_id)
    return params[param_id]


def history(param_id):
    """列出某参数的全部退役旧值，按时间排列。"""
    archive = _load(ARCHIVE_PATH)["archive"]
    return [e for e in archive if e["param"] == param_id]


def retire(param_id, new_value, source, retired_reason, status="current"):
    """退役旧值入档案区，写入新值为当前值。这是修改参数的唯一合法途径。

    I-127 图结构传导：数值变更后，全部下游节点（传递闭包）的图状态
    自动置为待重算，传导留痕入事件侧车（graph_events.jsonl 追加）。"""
    params_obj = _load(PARAMS_PATH)
    archive_obj = _load(ARCHIVE_PATH)
    today = datetime.date.today().isoformat()
    old = params_obj["params"].get(param_id, {})
    new_record = deepcopy(old)
    if param_id in params_obj["params"]:
        old = params_obj["params"][param_id]
        archive_obj["archive"].append({
            "param": param_id,
            "value": old["value"],
            "unit": old.get("unit", ""),
            "valid_period": (old.get("since") or "未知") + " 至 " + today,
            "source": old.get("source", ""),
            "retired_reason": retired_reason,
            "replaced_by": str(new_value),
            "retired_at": today,
            "record_snapshot": deepcopy(old),
        })
    new_record.update({
        "value": new_value,
        "unit": old.get("unit", ""),
        "status": status,
        "source": source,
        "since": today,
    })
    new_record.setdefault("graph", None)
    # I-232: preserve all extensions, including the original mirror in the
    # archived snapshot. I-130 still reads first-level extra core projections.
    # Refresh only projections already present, or a retired value would win
    # again at payload readback. Never create a mirror or decode deeper fields.
    extra = new_record.get("extra")
    if isinstance(extra, str):
        try:
            mirror = json.loads(extra)
        except (ValueError, RecursionError):
            mirror = None
        if isinstance(mirror, dict):
            refreshed = False
            for field in ("value", "unit", "status", "source", "since"):
                if field in mirror:
                    mirror[field] = deepcopy(new_record[field])
                    refreshed = True
            if refreshed:
                new_record["extra"] = json.dumps(mirror, ensure_ascii=False)
    params_obj["params"][param_id] = new_record
    _save(ARCHIVE_PATH, archive_obj)
    _save(PARAMS_PATH, params_obj)
    # 传导必须在新值落盘之后：mark 内部独立加载保存，先后写会覆盖
    affected = mark_downstream_stale(param_id)
    if affected:
        _graph_log(param_id, "retire", affected)


# ── I-127 图结构与多样本 ──────────────────────────────────────
# 节点结构：params[key]["graph"] = {
#   "level": 0-9（参数分级架构_20261008.md 的级），
#   "authority": 权威来源，"valid_domain": 有效域标注，
#   "downstream": [依赖本参数的键名清单]，
#   "status": 已完成 | 部分 | 缺失 | 待重算 }
# 多样本：params[key]["sample"] = common | 215g | 700g（默认 215g）。
# 顶层 status 字段是数值生命周期（current/superseded），与图状态分立。

GRAPH_STATUSES = ("已完成", "部分", "缺失", "待重算")
SAMPLES = ("common", "215g", "700g")


def set_graph(param_id, level=None, authority=None, valid_domain=None,
              downstream=None, status=None, sample=None):
    """写入或更新节点图字段（I-127）。只改 graph 与 sample 两处，
    不触碰数值生命周期字段。状态四档与样本三值服务端校验。"""
    params_obj = _load(PARAMS_PATH)
    if param_id not in params_obj["params"]:
        raise KeyError("参数未登记：%s（先入册再上图）" % param_id)
    rec = params_obj["params"][param_id]
    g = rec.get("graph") or {}
    if level is not None:
        if not isinstance(level, int) or not 0 <= level <= 9:
            raise ValueError("级别须为零至九整数")
        g["level"] = level
    if authority is not None:
        g["authority"] = authority
    if valid_domain is not None:
        g["valid_domain"] = valid_domain
    if downstream is not None:
        for d in downstream:
            if d not in params_obj["params"]:
                raise KeyError("下游键未登记：%s（先入册再连边）" % d)
        g["downstream"] = list(downstream)
    if status is not None:
        if status not in GRAPH_STATUSES:
            raise ValueError("图状态只允许 %s" % (GRAPH_STATUSES,))
        g["status"] = status
    rec["graph"] = g
    if sample is not None:
        if sample not in SAMPLES:
            raise ValueError("样本只允许 %s" % (SAMPLES,))
        rec["sample"] = sample
    _save(PARAMS_PATH, params_obj)
    return g


def downstream_chain(param_id, sample=None):
    """查询命令（I-127 判据）：输入参数名，输出全部下游链条。

    传递闭包按 downstream 边逐层展开；sample 给定时只走该样本与
    common 的节点。返回 [{"key", "level", "status", "via"]...]，
    via 记录传导路径首环，供逐层清单使用。"""
    params_obj = _load(PARAMS_PATH)
    out, seen, frontier = [], {param_id}, [param_id]
    via = {param_id: None}
    while frontier:
        nxt = []
        for k in frontier:
            rec = params_obj["params"].get(k) or {}
            if sample and rec.get("sample", "215g") not in (sample, "common"):
                continue
            for d in (rec.get("graph") or {}).get("downstream") or []:
                if d in seen:
                    continue
                seen.add(d)
                via[d] = k
                nxt.append(d)
                drec = params_obj["params"].get(d) or {}
                out.append({"key": d,
                            "level": (drec.get("graph") or {}).get("level"),
                            "status": (drec.get("graph") or {}).get("status"),
                            "sample": drec.get("sample", "215g"),
                            "via": via[d]})
        frontier = nxt
    return out


def mark_downstream_stale(param_id):
    """上级变更传导（I-127 判据）：把全部下游节点图状态置为待重算。

    返回被置位的键名清单；只写图状态字段，不动数值。"""
    params_obj = _load(PARAMS_PATH)
    affected = [e["key"] for e in downstream_chain(param_id)]
    for k in affected:
        rec = params_obj["params"].get(k)
        if rec is not None:
            g = rec.get("graph") or {}
            g["status"] = "待重算"
            rec["graph"] = g
    if affected:
        _save(PARAMS_PATH, params_obj)
    return affected


def _graph_log(param_id, action, affected):
    """传导留痕：追加一行到 graph_events.jsonl（只增不改）。"""
    line = json.dumps({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "param": param_id, "action": action, "affected": affected,
    }, ensure_ascii=False)
    with open(os.path.join(_DIR, "graph_events.jsonl"), "a",
              encoding="utf-8") as f:
        f.write(line + "\n")


def supersede(param_id, successor_id, reason):
    """命名空间迁移：指定参数由后继键取代，状态改为 superseded。

    只改 status 与 note，不删除条目、不动 archive.json；
    语义是键名迁移而非数值变更，数值变更必须走 retire() 留档。
    """
    params_obj = _load(PARAMS_PATH)
    if param_id not in params_obj["params"]:
        raise KeyError("参数未登记：%s" % param_id)
    rec = params_obj["params"][param_id]
    if rec.get("status") == "superseded":
        raise ValueError("参数 %s 已 superseded，重复迁移被拒绝" % param_id)
    rec["status"] = "superseded"
    mark = "superseded %s：由 %s 取代（%s）" % (
        datetime.date.today().isoformat(), successor_id, reason)
    rec["note"] = (rec["note"] + "；" + mark) if rec.get("note") else mark
    _save(PARAMS_PATH, params_obj)


def check():
    """一致性校验，供每次提交前审查。

    返回值为两节嵌套字典：
      "suspect"：状态含 suspect 的参数全量清单（与旧版返回的单层字典等价）；
      "review"：状态为 superseded 或 reference 的参数复核清单，供提交前审查。
    向后兼容说明：旧版直接返回 suspect 单层字典；新版改为两节结构，
    旧调用方取 suspect 清单应改用 check()["suspect"]。
    """
    params = _load(PARAMS_PATH)["params"]
    suspects = {k: v for k, v in params.items() if "suspect" in v.get("status", "")}
    review = {k: v for k, v in params.items()
              if v.get("status", "") in ("superseded", "reference")}
    return {"suspect": suspects, "review": review}


if __name__ == "__main__":
    import sys as _sys
    # I-127 查询命令：python params_registry/registry.py graph <参数名> [样本]
    # 输出该参数的全部下游链条（传递闭包，逐层带级别状态与传导首环）。
    if len(_sys.argv) >= 3 and _sys.argv[1] == "graph":
        _pid = _sys.argv[2]
        _smp = _sys.argv[3] if len(_sys.argv) > 3 else None
        chain = downstream_chain(_pid, sample=_smp)
        print("参数 %s 下游链条（%d 个节点，样本过滤=%s）："
              % (_pid, len(chain), _smp or "全部"))
        for e in chain:
            print("  %s%s 级=%s 状态=%s ← %s"
                  % (e["key"],
                     "" if e["sample"] in ("215g",) else "（%s）" % e["sample"],
                     e["level"], e["status"], e["via"]))
        _sys.exit(0)
    result = check()
    print("当前参数数：%d" % len(_load(PARAMS_PATH)["params"]))
    print("档案区旧值数：%d" % len(_load(ARCHIVE_PATH)["archive"]))
    print("待复核参数（suspect）：%s" % list(result["suspect"].keys()))
    print("迁移与参考复核（superseded/reference）：%s" % list(result["review"].keys()))
