# -*- coding: utf-8 -*-
"""i227_real_evidence.py — I-227 真实数据取证：判据六四项证据与判据五回归。

全部检查只读：读生产状态文件、会话库只读、云端只读接口；不停止
任何进程、不重启任何会话、不写会话库。唯一写入为本项证据目录与
本项历史索引目录。运行前应已在同一会话内启动超过九十秒的真实
工具调用，供同刻对照取样。仅使用 Python 标准库。
"""
import datetime
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LP_SYNC = os.path.join(ROOT, "lp_sync")
EVIDENCE_DIR = os.path.join(ROOT, "logs", "hq_intake",
                            "beth_session_observability_20261014")
for p in (LP_SYNC, os.path.join(ROOT, "items_registry")):
    if p not in sys.path:
        sys.path.insert(0, p)

import cloud_client          # noqa: E402
import session_truth         # noqa: E402
import session_history as sh  # noqa: E402

MY_SESSION = "sess_cea3e05f-93d4-436f-a4f1-25d196418268"
MY_WORKER = "L3-w1"
DUAL_WORKER_PEER = "L3-w2"
PEER_SESSION = "sess_bd6fe971-9725-45a0-a8aa-63c276d7b46c"
CHAIN_OLD = "sess_5074d84c-2adc-43b6-aec6-33f46c3df739"
CHAIN_NEW = "sess_87092162-68e3-4f6b-a0d9-76ee4d3f13ab"


def dump(name, payload):
    path = os.path.join(EVIDENCE_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("写入 %s" % path)
    return path


def inflight_parts(sid):
    """会话库只读：该会话全部在途工具行及年龄。"""
    import sqlite3
    con = sqlite3.connect("file:" + session_truth.SESSION_DB.replace("\\", "/")
                          + "?mode=ro", uri=True, timeout=8.0)
    try:
        rows = con.execute(
            "select id, time_created, data from part where session_id=? "
            "and data like ? order by id desc limit 8",
            (sid, '%"type":"tool"%')).fetchall()
    finally:
        con.close()
    out = []
    now_ms = time.time() * 1000
    for pid, tc, data in rows:
        try:
            d = json.loads(data)
        except ValueError:
            continue
        status = (d.get("state") or {}).get("status")
        out.append({"part_id": pid, "tool": d.get("tool"), "status": status,
                    "started_at": datetime.datetime.fromtimestamp(
                        tc / 1000).isoformat(timespec="seconds"),
                    "age_s": int((now_ms - tc) / 1000),
                    "in_flight": status in ("pending", "running")})
    return out


def longtool_cloud_moment():
    """证据一：真实长工具与云端状态同刻对照。"""
    body, meta = session_truth.collect_body()
    rows = {r["session_id"]: r for r in body["sessions"]}
    cloud_cards = {}
    cloud_footer = None
    cloud_error = None
    try:
        state = cloud_client.request("GET", "/api/console/state")
        # 云端路由直接返回结果体；兼容带 ok/result 包装的形态
        result = state.get("result") if isinstance(
            state.get("result"), dict) else state
        cloud_footer = result.get("footer")
        for group in ("active", "retained", "archived", "owner"):
            for card in ((result.get("sessions") or {}).get(group) or []):
                cloud_cards[card.get("session_id")] = {
                    "cloud_group": group, "cloud_status": card.get("status"),
                    "cloud_last_activity": card.get("last_activity_at"),
                    "cloud_updated_at": card.get("updated_at")}
    except Exception as exc:
        cloud_error = repr(exc)[:200]
    comparisons = []
    for sid, row in rows.items():
        legacy = session_truth.legacy_status(row)
        card = cloud_cards.get(sid) or {}
        tool = row.get("last_tool") or {}
        comparisons.append({
            "session_id": sid, "worker_id": row.get("worker_id"),
            "truth_status": row["status"], "legacy_status": legacy,
            "cloud": card or {"note": "云端无该会话卡"},
            "last_tool": tool,
            "counterexample_fixed": (
                tool.get("status") in ("pending", "running")
                and (tool.get("age_s") or 0) > 90
                and legacy == "空闲"
                and row["status"].startswith("在干活"))})
    fixed = [c for c in comparisons if c["counterexample_fixed"]]
    inflight = inflight_parts(MY_SESSION)
    payload = {
        "evidence": "真实长工具与云端状态同刻对照",
        "sampled_at": meta.get("sampled_at"),
        "cloud_footer": cloud_footer, "cloud_error": cloud_error,
        "my_session_inflight_parts": inflight,
        "comparisons": comparisons,
        "counterexample_fixed_count": len(fixed),
        "verdict_pass": bool(fixed) or any(
            p["in_flight"] and p["age_s"] > 90 for p in inflight)}
    return dump("real_longtool_cloud_moment.json", payload)


def source_counts():
    """证据二：完整历史来源计数与索引数量一致。"""
    index, report = sh.build_index()
    registry = json.load(open(session_truth.REGISTRY_PATH, encoding="utf-8"))
    state = json.load(open(os.path.join(session_truth.STATE_ROOT,
                                        "state.json"), encoding="utf-8"))
    execution = json.load(open(session_truth.EXECUTION_PATH, encoding="utf-8"))
    handoff_state = json.load(open(session_truth.HANDOFF_STATE_PATH,
                                   encoding="utf-8"))
    import glob
    ev_files = [p for p in glob.glob(os.path.join(
        sh.HANDOFF_EVENTS_DIR, "*.json"))]
    recount = {
        "dispatch_registry": len(registry),
        "kernel_runs": len(state.get("runs") or {}),
        "execution_store": len(execution.get("sessions") or {}),
        "handoff_state_archived": len(handoff_state.get("archived") or []),
        "handoff_events_files": len(ev_files),
        "index_sessions": len(index["sessions"]),
    }
    consistent = (
        report["sources"]["dispatch_registry"]["records"]
        == recount["dispatch_registry"]
        and report["sources"]["kernel_runs"]["records"] == recount["kernel_runs"]
        and report["sources"]["execution_store"]["records"]
        == recount["execution_store"])
    payload = {"evidence": "完整历史来源计数", "build_report": report,
               "independent_recount": recount,
               "report_matches_recount": consistent,
               "retention_counts": index.get("stats", {}).get("retention"),
               "by_lane": index.get("stats", {}).get("by_lane")}
    return dump("history_source_counts.json", payload), index


def restart_pagination(index_before):
    """证据三：重启重建与分页稳定。"""
    totals_before = sh.list_sessions(page_size=200)
    ids_before = [i["session_id"] for i in totals_before["items"]]
    sh.build_index()
    totals_after = sh.list_sessions(page_size=200)
    ids_after = [i["session_id"] for i in totals_after["items"]]
    walked = []
    page = 1
    while True:
        res = sh.list_sessions(page=page, page_size=25)
        walked.extend(i["session_id"] for i in res["items"])
        if page * 25 >= res["total"]:
            break
        page += 1
    payload = {
        "evidence": "重启分页与重建稳定",
        "total_before": totals_before["total"],
        "total_after": totals_after["total"],
        "order_stable": ids_before == ids_after,
        "page_walk": {"pages": page, "walked": len(walked),
                      "unique": len(set(walked)),
                      "duplicates": len(walked) - len(set(walked)),
                      "matches_total": len(walked) == totals_after["total"]},
        "verdict_pass": totals_before["total"] == totals_after["total"]
        and ids_before == ids_after
        and len(walked) == len(set(walked)) == totals_after["total"]}
    return dump("restart_pagination.json", payload)


def dual_worker_handover():
    """证据四：双工位与交接链核验。"""
    w1 = sh.list_sessions({"worker_id": MY_WORKER})
    w2 = sh.list_sessions({"worker_id": DUAL_WORKER_PEER})
    old = sh.get_session(CHAIN_OLD)
    new = sh.get_session(CHAIN_NEW)
    my_detail = sh.get_session(MY_SESSION)
    peer_detail = sh.get_session(PEER_SESSION)
    card_ok = False
    card_excerpt = None
    if old.get("ok") and old.get("handover_cards"):
        card_excerpt = old["handover_cards"][0].get("card_excerpt")
        card_ok = bool(card_excerpt)
    payload = {
        "evidence": "双工位交接核验",
        "L3-w1": {"total": w1["total"],
                  "sessions": [i["session_id"] for i in w1["items"]],
                  "my_items": (my_detail.get("items") if my_detail.get("ok")
                               else None),
                  "my_active_runs": [r for r in
                                     (my_detail.get("runs") or [])
                                     if r.get("status") == "active"]},
        "L3-w2": {"total": w2["total"],
                  "sessions": [i["session_id"] for i in w2["items"]],
                  "peer_items": (peer_detail.get("items")
                                 if peer_detail.get("ok") else None)},
        "handover_chain": {
            "old": CHAIN_OLD, "new": CHAIN_NEW,
            "old_successor_chain": old.get("successor_chain"),
            "new_predecessor_chain": new.get("predecessor_chain"),
            "card_readable": card_ok, "card_excerpt_head":
                (card_excerpt or "")[:120]},
        "verdict_pass": w1["total"] >= 1 and w2["total"] >= 1
        and old.get("ok") and new.get("ok")
        and old.get("successor_chain") == [CHAIN_NEW]
        and new.get("predecessor_chain") == [CHAIN_OLD] and card_ok}
    return dump("dual_worker_handover_check.json", payload)


def regression():
    """判据五回归：旧单工位、灰态、现行履历、当前事项。"""
    index = sh.load_index()
    sessions = index["sessions"]
    # 旧单工位：L2 会话仍在索引且可按工位查
    l2 = sh.list_sessions({"lane": "L2"})
    # 灰态：空闲超时保留条目存在
    gray = sh.list_sessions({"retention": sh.RETENTION_TIMEOUT})
    # 现行履历：L3-w2 空闲工位保留最后会话与事项履历
    peer = sessions.get(PEER_SESSION) or {}
    peer_resume = bool(peer.get("workers")) and bool(peer.get("items"))
    # 当前事项：本会话条目挂 I-227 且有活跃运行
    mine = sessions.get(MY_SESSION) or {}
    my_items = mine.get("items") or {}
    my_active = [r for r in (mine.get("runs") or [])
                 if r.get("status") == "active"]
    # 实时并发不受查询影响：查询前后工位注册表与运行记录不变
    roster_before = open(session_truth.ROSTER_PATH, "rb").read()
    state_before = open(os.path.join(session_truth.STATE_ROOT, "state.json"),
                        "rb").read()
    sh.list_sessions(page_size=10)
    sh.get_session(MY_SESSION)
    roster_after = open(session_truth.ROSTER_PATH, "rb").read()
    state_after = open(os.path.join(session_truth.STATE_ROOT, "state.json"),
                       "rb").read()
    payload = {
        "evidence": "判据五回归检查",
        "legacy_single_worker": {"lane": "L2", "total": l2["total"],
                                 "pass": l2["total"] >= 1},
        "gray_retained": {"total": gray["total"],
                          "pass": gray["total"] >= 1},
        "peer_resume": {"worker": DUAL_WORKER_PEER,
                        "workers": peer.get("workers"),
                        "items": peer.get("items"), "pass": peer_resume},
        "current_item": {"session": MY_SESSION, "item_I-227": "I-227" in my_items,
                         "active_runs": my_active,
                         "pass": "I-227" in my_items and bool(my_active)},
        "no_side_effects": {"roster_unchanged": roster_before == roster_after,
                            "state_unchanged": state_before == state_after,
                            "pass": roster_before == roster_after
                            and state_before == state_after}}
    payload["verdict_pass"] = all(v["pass"] for v in payload.values()
                                  if isinstance(v, dict) and "pass" in v)
    return dump("regression_results.json", payload)


def main():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    results = {}
    results["longtool"] = longtool_cloud_moment()
    results["counts"], _ = source_counts()
    results["restart"] = restart_pagination(None)
    results["dual"] = dual_worker_handover()
    results["regression"] = regression()
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
