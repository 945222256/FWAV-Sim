# -*- coding: utf-8 -*-
"""sync_agent.py — 本地工地常驻代理：心跳、缓存同步、断网队列重放。

依据《云端托管_需求与约束_20260930.md》本地代理功能四件：
注册自身、心跳上报、断网队列与恢复重放、缓存同步。
本地不开放任何入站端口，一切连接主动外呼。
仅使用 Python 标准库。
"""
import datetime
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "items_registry"))
_LP_SYNC = os.path.dirname(os.path.abspath(__file__))
if _LP_SYNC not in sys.path:
    sys.path.insert(0, _LP_SYNC)
import cloud_client

# I-143：控制台采集代理（方案草案第二节采集层），五秒周期只读会话库
# 上行快照；导入失败不拖垮账本同步主职能。
try:
    import console_collector
except Exception as _exc:
    console_collector = None
    _COLLECTOR_IMPORT_ERROR = repr(_exc)

HEARTBEAT_S = 10
QUEUE_PATH = os.path.join(os.path.dirname(cloud_client.__file__), "review_queue.json")


def _log(msg):
    print("[%s] %s" % (datetime.datetime.now().isoformat(timespec="seconds"), msg), flush=True)


def _review_queue():
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("queue", [])
    except (OSError, ValueError):
        return []


def _heartbeat_body():
    body = {"info": cloud_client.agent_info()}
    queue = _review_queue()
    if queue:
        body["review_queue"] = queue     # 待审队列镜像上行，云端页面只读展示。
    # 目标库与路线配置镜像上行：云端 map 接口的目标树与泳道色组来自这里。
    # I-167：战略蓝图应然层随同上行，登记源 items_registry/blueprints.json。
    reg = os.path.dirname(cloud_client.__file__)
    # I-198：通讯录稳定人员标识镜像上行（organization/contacts.json 的
    # members 键），供云端 set_reviewer 窄入口按现行通讯录校验。
    try:
        with open(os.path.join(os.path.dirname(reg), "organization",
                               "contacts.json"), "r", encoding="utf-8") as f:
            _members = sorted((json.load(f).get("members") or {}).keys())
        if _members:
            body["contacts_members"] = _members
    except (OSError, ValueError):
        pass
    for key, name in (("goals", "goals.json"), ("lanes_conf", "lanes.json"),
                      ("logical_lines", "logical_lines.json"),
                      ("blueprints", "blueprints.json")):
        path = os.path.join(reg, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                body[key] = json.load(f)
        except (OSError, ValueError):
            pass
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, "kernel_state", "lane_queues.json"), encoding="utf-8") as stream:
            queues = json.load(stream)
        body["lane_queues"] = {"sampled_at": datetime.datetime.now().isoformat(timespec="seconds"), "queues": queues}
    except (OSError, ValueError):
        pass
    try:
        from host_service import EXECUTION_STORE, _lock_holder_alive
        body["runtime_runs"] = [v for v in EXECUTION_STORE.sessions().values()
                                if v.get("phase") == "active" and v.get("item_id")
                                and v.get("executor_pid") and _lock_holder_alive(v["executor_pid"]) is True]
    except (OSError, ValueError):
        pass
    return body


_PARAMS_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "params_registry", "params.json")
_ARCHIVE_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "params_registry", "archive.json")


def _local_params():
    try:
        with open(_PARAMS_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("params", {})
    except (OSError, ValueError):
        return None


def _local_archive():
    try:
        with open(_ARCHIVE_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("archive", [])
    except (OSError, ValueError):
        return []


def _digest(params):
    import hashlib
    blob = json.dumps(params, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sync_params_up():
    """I-130：参数镜像上行（先推后拉，与 lanes 同款纪律）。

    云端快照下行曾把 I-127 图字段从本地冲掉（云端扁平七列存不下
    嵌套节）。本通道把本地权威 params（含 graph/sample）与退役
    档案推上云端，摘要一致时零流量。"""
    params = _local_params()
    if params is None:
        return
    body = _heartbeat_body()
    body["params_digest"] = _digest(params)
    hb = cloud_client.request("POST", "/api/agent/heartbeat", body)
    cloud_digest = hb.get("params_digest") if isinstance(hb, dict) else None
    if cloud_digest == body["params_digest"]:
        return
    cloud_client.request("POST", "/api/agent/params", {
        "digest": body["params_digest"],
        "params": params,
        "archive": _local_archive(),
    })
    _log("参数镜像上行：%d 键（摘要 %s…）" % (len(params), body["params_digest"][:12]))


def once():
    """一轮完整代理动作：重放、心跳、按需同步。返回本轮是否有变化。

    下拉覆盖本地缓存只在 cloud 模式开闸：local 模式下本地文件仍是
    权威数据源，下拉覆盖会冲掉并行会话的直写内容。
    I-130：cloud 模式下先行参数镜像上行，再走事件序号判断下拉，
    保证下行覆盖永远落在内容一致的镜像上。
    """
    if cloud_client.enabled():
        try:
            _sync_params_up()
        except cloud_client.CloudUnreachable:
            raise
        except Exception:
            _log("参数镜像上行异常：\n%s" % traceback.format_exc())
    remaining, rejected = cloud_client.replay()
    if remaining or rejected:
        _log("断网队列：待重放 %d 条，被云端拒绝 %d 条" % (remaining, rejected))
    hb = cloud_client.request("POST", "/api/agent/heartbeat", _heartbeat_body())
    changed = False
    if cloud_client.enabled() and hb.get("server_seq", 0) > cloud_client.known_seq():
        cloud_client.sync_cache(force=True)
        changed = True
        _log("云端事件序号推进到 %s，本地缓存已刷新" % hb["server_seq"])
    return changed


def main():
    _log("同步代理启动：%s（模式 %s）" % (cloud_client.agent_info()["agent_id"], cloud_client.mode()))
    if console_collector is not None:
        console_collector.start_collector_thread(_log)
    else:
        _log("控制台采集模块导入失败，账本同步不受影响：%s" % _COLLECTOR_IMPORT_ERROR)
    registered = False
    while True:
        try:
            if not registered:
                cloud_client.request("POST", "/api/agent/register",
                                     {"info": cloud_client.agent_info()})
                _log("已在云端注册")
                registered = True
            once()
        except cloud_client.CloudUnreachable as exc:
            _log("云端不可达：%s（断网队列继续本地积压，恢复后自动重放）" % exc)
            registered = False
        except Exception:
            _log("代理异常：\n%s" % traceback.format_exc())
        time.sleep(HEARTBEAT_S)


if __name__ == "__main__":
    main()
