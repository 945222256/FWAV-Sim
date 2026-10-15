# -*- coding: utf-8 -*-
"""dispatch.py — 任务派发层：认领、租约、看板。

状态机：open → claimed（in_progress，带租约）→ closed。
租约到期未关闭自动退回 open，防止事项被认领后遗忘。
施工队只从看板认领任务，不接收口头任务。
仅使用 Python 标准库。
"""
import json
import os
import re
import sys
import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
ITEMS_PATH = os.path.join(_DIR, "items.json")
LANES_PATH = os.path.join(_DIR, "lanes.json")


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, obj):
    """原子写：先写临时文件再整体替换，写入中途进程被杀时原文件保持完好。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


import contextlib
import time

import cloud_client

LOCK_PATH = os.path.join(_DIR, ".ledger.lock")


@contextlib.contextmanager
def _lock(timeout_s=10):
    """原子锁：同一时刻只允许一个会话改写总账。

    锁的载体是锁文件的排他创建，操作系统保证同一瞬间只有一个创建者
    成功，因此两个会话同一秒认领同一事项时，只有先拿到锁的能写入。
    """
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


def assign(item_id, lane, owner):
    """架构师专用：把事项派给某路某人。"""
    lanes = _load(LANES_PATH)["lanes"]
    if lane not in lanes:
        raise KeyError("路线不存在：%s（可选 %s）" % (lane, list(lanes)))
    if cloud_client.enabled():
        return cloud_client.write("/api/dispatch/assign",
                                  {"item_id": item_id, "lane": lane, "owner": owner})
    with _lock():
        obj = _load(ITEMS_PATH)
        for item in obj["items"]:
            if item["id"] == item_id:
                item["lane"] = lane
                item["owner"] = owner
                _save(ITEMS_PATH, obj)
                return
    raise KeyError("事项不存在：%s" % item_id)


def worker_lane(worker):
    """从认领人名提取路线号：L3队→L3、HQ架构师→HQ、L3看板会话→L3。
    提不出路线号时返回 None（如机主本人）。"""
    m = re.match(r"^(L\d+|HQ)", (worker or "").strip())
    return m.group(1) if m else None


def check_lane_consistency(item, worker):
    """I-093 路线一致性：认领人路线与事项路线不一致直接拒绝。
    事项未派发路线时不拦（认领后由派发归位）。"""
    wl = worker_lane(worker)
    il = item.get("lane")
    if wl and il and wl != il:
        raise ValueError(
            "路线不一致：事项 %s 属 %s 路线，认领人 %s 属 %s 路线；"
            "各队只许认领本路线事项，跨路线协作走登记新事项交给该路"
            % (item["id"], il, worker, wl))


def claim(item_id, worker, lease_hours=4, session_id=None):
    """施工队认领事项。五条强制：
    1. 已被他人认领且认领时限未过期的事项拒绝认领；
    2. 在制限制：同一认领人同时只允许持有一个进行中事项；
    3. 依赖未清的事项拒绝认领，除非事项写有 partial_start 可先行范围，
       此时认领人只许在先行范围内工作；
    4. 路线一致性：认领人路线与事项路线不一致拒绝（I-093）；
    5. 会话归属（I-174）：session_id 为施工会话自报的会话号，写入
       认领事件详情，控制台映射以认领记录为权威来源。
    """
    if cloud_client.enabled():
        return cloud_client.write("/api/dispatch/claim", {
            "item_id": item_id, "worker": worker, "lease_hours": lease_hours,
            "session_id": session_id})
    with _lock():
        obj = _load(ITEMS_PATH)
        now = datetime.datetime.now()
        for other in obj["items"]:
            if other["status"] == "in_progress" and other.get("claimed_by") == worker \
                    and other["id"] != item_id:
                raise ValueError("在制限制：%s 手里还有 %s 未完成未申报卡住，"
                                 "一路同时只许持有一个进行中事项" % (worker, other["id"]))
        from dependency_policy import unmet_dependencies
        for item in obj["items"]:
            if item["id"] != item_id:
                continue
            if item["status"] == "closed":
                raise ValueError("事项 %s 已关闭" % item_id)
            if item["status"] == "pending_review":
                raise ValueError("事项 %s 已提交待验收" % item_id)
            check_lane_consistency(item, worker)
            lease = item.get("lease_until")
            if item.get("claimed_by") and lease:
                if datetime.datetime.fromisoformat(lease) > now:
                    raise ValueError("事项 %s 正由 %s 认领中，认领时限至 %s" % (item_id, item["claimed_by"], lease))
            open_deps = unmet_dependencies(item, obj["items"])
            if open_deps and not item.get("partial_start"):
                raise ValueError("事项 %s 依赖未清（%s），不可认领；"
                                 "确需先行须由架构师写明 partial_start 范围" % (item_id, open_deps))
            item["status"] = "in_progress"
            item["claim_count"] = item.get("claim_count", 0) + 1
            item["claimed_by"] = worker
            item["claimed_at"] = now.isoformat(timespec="seconds")
            item["claim_session_id"] = session_id
            item["lease_until"] = (now + datetime.timedelta(hours=lease_hours)).isoformat(timespec="seconds")
            if open_deps and item.get("partial_start"):
                item["claim_scope"] = item["partial_start"]
            _save(ITEMS_PATH, obj)
            return
    raise KeyError("事项不存在：%s" % item_id)


def release(item_id, worker):
    """主动释放认领，事项退回 open。"""
    if cloud_client.enabled():
        return cloud_client.write("/api/dispatch/release",
                                  {"item_id": item_id, "worker": worker})
    with _lock():
        obj = _load(ITEMS_PATH)
        for item in obj["items"]:
            if item["id"] == item_id:
                if item.get("claimed_by") != worker:
                    raise ValueError("只有认领人 %s 可以释放该事项" % item.get("claimed_by"))
                item["status"] = "open"
                item["claimed_by"] = None
                item["lease_until"] = None
                _save(ITEMS_PATH, obj)
                return
    raise KeyError("事项不存在：%s" % item_id)


def sweep():
    """清理过期租约：租约到期未关闭的事项自动退回 open。返回被退回的事项编号。"""
    if cloud_client.enabled():
        try:
            result = cloud_client.request("POST", "/api/dispatch/sweep", {})
            return result.get("expired", [])
        except cloud_client.CloudUnreachable:
            return []
    obj = _load(ITEMS_PATH)
    now = datetime.datetime.now()
    expired = []
    for item in obj["items"]:
        lease = item.get("lease_until")
        if item["status"] == "in_progress" and lease:
            if datetime.datetime.fromisoformat(lease) <= now:
                expired.append(item["id"])
                item["status"] = "open"
                item["claimed_by"] = None
                item["lease_until"] = None
    if expired:
        _save(ITEMS_PATH, obj)
    return expired


def board(lane_filter=None):
    """看板：按路线分组列出未关闭事项。每个会话开工必须先运行本函数。

    I-093 路线参数：带 lane_filter 时只打印该路线队列与未派发计数，
    各队只看自己的板；不带参数时输出与历史完全一致（逐字不变）。
    """
    obj = _load(ITEMS_PATH)
    lanes = _load(LANES_PATH)["lanes"]
    if lane_filter is not None and lane_filter not in lanes:
        raise SystemExit("[dispatch] 路线不存在：%s（可选 %s）"
                         % (lane_filter, sorted(lanes)))
    out = {}
    for item in obj["items"]:
        if item["status"] == "closed":
            continue
        lane = item.get("lane") or "未派发"
        out.setdefault(lane, []).append(item)
    import schedule as _sched
    layer = _sched.topo_layers()
    show = {lane_filter: out.get(lane_filter, [])} if lane_filter else out
    for lane in sorted(show):
        if lane_filter and not show[lane]:
            print("== %s %s ==" % (lane, lanes[lane].get("name", "")))
            print("  （本路线当前无未关闭事项）")
            continue
        name = lanes.get(lane, {}).get("name", "")
        print("== %s %s ==" % (lane, name))
        for i in sorted(show[lane], key=lambda x: (layer.get(x["id"], 0), x["id"])):
            claimer = i.get("claimed_by") or "空闲"
            ruling = "〔HQ 已有裁决，读本事项备注〕" if ("HQ 裁决" in (i.get("note") or "") or "HQ 决定" in (i.get("note") or "")) else ""
            blocked = "，依赖未清" if layer.get(i["id"], 0) > 0 and i["status"] == "open" and not i.get("claimed_by") else ""
            budget = "〔认领超预算%d次，需HQ决定拆分〕" % i["claim_count"] if i.get("claim_count", 0) >= 3 else ""
            print("  [%s] %s（%s，认领人 %s%s）%s" % (i["id"], i["title"], i["status"], claimer, blocked + budget, ruling))
            note = i.get("note") or ""
            brief = ""
            if "【机主说明】" in note:
                brief = note.split("【机主说明】", 1)[1].split("\n【", 1)[0].strip()
            else:
                for ev in reversed(i.get("evidence") or []):
                    if isinstance(ev, str) and ev.startswith("机主说明："):
                        brief = ev.split("机主说明：", 1)[1].strip()
                        break
            if brief:
                print("    机主说明：%s" % brief[:180])
    if lane_filter:
        unassigned = len(out.get("未派发", []))
        print("未派发：%d 条（本路视线外；python items_registry/dispatch.py 看全板）"
              % unassigned)
    return out


if __name__ == "__main__":
    expired = sweep()
    if expired:
        print("租约到期退回 open：%s" % expired)
    lane_arg = sys.argv[1] if len(sys.argv) > 1 else None
    board(lane_filter=lane_arg)
