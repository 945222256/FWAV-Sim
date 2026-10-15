# -*- coding: utf-8 -*-
"""events.py — 账本事件流：追加式履历日志。

用途：审计时间线的唯一数据来源。每次写账动作发生后追加一条事件，
事件只追加永不修改，谁在什么时候对哪个事项做了什么，全部留痕。
仅使用 Python 标准库。
"""
import json
import os
import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_PATH = os.path.join(_DIR, "events.json")

_ACTION_WORDS = {
    "submit": "新事项申报入待审队列",
    "lint": "待审事项过收纳审理规则",
    "approve": "待审事项批准入册",
    "reject": "待审事项驳回",
    "merge": "待审事项合并入在册事项",
    "claim": "事项被认领",
    "release": "事项认领被释放",
    "sweep": "过期租约被清理退回",
    "owner_reply": "机主答复",
    "report": "施工 agent 申报",
    "close": "事项打勾关闭",
    "evidence": "证据被追加",
    "criterion": "判据子项验证状态变更",
    "git_evidence": "提交被自动挂载为证据",
    "assign": "事项被派发到路线",
    "set_depends": "事项依赖清单被登记",
    "set_rationale": "事项来由被登记",
    "set_line": "事项逻辑线归属被登记",
    "set_enables": "事项启发关系被登记",
}


def _load():
    if not os.path.exists(EVENTS_PATH):
        return {"schema_version": 1, "events": []}
    with open(EVENTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_save(obj):
    tmp = EVENTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, EVENTS_PATH)


def append(action, item_id="", actor="", detail=""):
    """追加一条事件。动作必须是本模块动作词表内的词。

    I-132 持久保证：云端模式下事件写入走云端追加接口（断网经代理
    发件箱排队重放），本地文件只是云端投影缓存，直写会被下拉覆盖；
    云端不可达且代理未接管时退回本地直写，由缓存下拉的并集保留
    兜底（cloud_client._write_cache 侧）。"""
    if action not in _ACTION_WORDS:
        raise ValueError("未知事件动作：%s（可用：%s）" % (action, sorted(_ACTION_WORDS)))
    try:
        import cloud_client
        if cloud_client.enabled():
            cloud_client.write("/api/events", {
                "action": action, "item_id": item_id,
                "actor": actor, "detail": detail})
            return True
    except Exception:
        pass  # 云端通道异常退回本地直写（并集保留兜底）
    obj = _load()
    obj["events"].append({
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "action_word": _ACTION_WORDS[action],
        "item_id": item_id,
        "actor": actor,
        "detail": detail,
    })
    _atomic_save(obj)
    return True


def for_item(item_id):
    """列出与指定事项相关的全部事件，按时间升序。"""
    return [e for e in _load()["events"] if e.get("item_id") == item_id]


def all_events():
    """返回全部事件，按时间升序。"""
    return _load()["events"]
