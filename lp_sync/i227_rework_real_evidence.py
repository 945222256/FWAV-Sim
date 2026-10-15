# -*- coding: utf-8 -*-
"""i227_rework_real_evidence.py — I-227 返修真实数据取证。

全部检查只读生产来源（状态文件、会话库只读、云端只读接口）；新版
历史索引只写入本项私有目录 logs/i227_rework_20261014/history_index，
生产索引 lp_sync/session_history/index.json 仅读取与哈希对照，全程
不覆盖、不删除。不停止、不重启任何生产进程或会话。仅使用 Python
标准库。
"""
import datetime
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LP_SYNC = os.path.join(ROOT, "lp_sync")
EVIDENCE_DIR = os.path.join(ROOT, "logs", "i227_rework_20261014")
PRIVATE_HISTORY = os.path.join(EVIDENCE_DIR, "history_index")
PRODUCTION_INDEX = os.path.join(ROOT, "lp_sync", "session_history",
                                "index.json")

# 索引写入位置先定向到私有目录，再导入模块
os.environ["LP_SESSION_HISTORY_DIR"] = PRIVATE_HISTORY
for p in (LP_SYNC, os.path.join(ROOT, "items_registry")):
    if p not in sys.path:
        sys.path.insert(0, p)

import cloud_client          # noqa: E402
import session_truth         # noqa: E402
import session_history as sh  # noqa: E402

MY_SESSION = "sess_cea3e05f-93d4-436f-a4f1-25d196418268"
CHAIN_OLD = "sess_5074d84c-2adc-43b6-aec6-33f46c3df739"
CHAIN_NEW = "sess_87092162-68e3-4f6b-a0d9-76ee4d3f13ab"


def dump(name, payload):
    path = os.path.join(EVIDENCE_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("写入 %s" % path)
    return path


def file_hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def longtool_cloud_moment():
    """证据一：真实长工具与云端状态同刻对照（返修版含全部在途工具）。"""
    body, meta = session_truth.collect_body()
    rows = {r["session_id"]: r for r in body["sessions"]}
    cloud_cards = {}
    cloud_footer = None
    cloud_error = None
    try:
        state = cloud_client.request("GET", "/api/console/state")
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
        inflight = row.get("in_flight_tools") or []
        comparisons.append({
            "session_id": sid, "worker_id": row.get("worker_id"),
            "truth_status": row["status"], "legacy_status": legacy,
            "cloud": card or {"note": "云端无该会话卡"},
            "in_flight_tools": inflight,
            "multi_inflight_real": len(inflight) >= 2,
            "counterexample_fixed": (
                bool(inflight)
                and legacy == "空闲"
                and row["status"].startswith("在干活"))})
    fixed = [c for c in comparisons if c["counterexample_fixed"]]
    multi = [c["session_id"] for c in comparisons if c["multi_inflight_real"]]
    payload = {
        "evidence": "返修：真实长工具与云端状态同刻对照（含在途工具全集）",
        "sampled_at": meta.get("sampled_at"),
        "cloud_footer": cloud_footer, "cloud_error": cloud_error,
        "comparisons": comparisons,
        "counterexample_fixed_count": len(fixed),
        "multi_inflight_sessions": multi,
        "verified_count": meta.get("verified_sessions"),
        "candidates": meta.get("candidates"),
        "verdict_pass": bool(fixed)}
    return dump("rework_longtool_cloud_moment.json", payload)


def source_counts(prod_hash_before):
    """证据二：来源计数与生产索引零接触对照。"""
    index, report = sh.build_index(keep_prior=False)
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
    prod_hash_after = file_hash(PRODUCTION_INDEX)
    payload = {"evidence": "返修：完整历史来源计数（索引写私有目录）",
               "build_report": report, "independent_recount": recount,
               "report_matches_recount": consistent,
               "retention_counts": index.get("stats", {}).get("retention"),
               "production_index_untouched":
                   prod_hash_before == prod_hash_after,
               "production_index_sha256": prod_hash_after,
               "private_index_dir": PRIVATE_HISTORY}
    return dump("rework_history_source_counts.json", payload)


def restart_pagination():
    """证据三：私有目录内重启重建与分页稳定。"""
    totals_before = sh.list_sessions(page_size=200)
    ids_before = [i["session_id"] for i in totals_before["items"]]
    sh.build_index()
    sh.build_index()
    totals_after = sh.list_sessions(page_size=200)
    ids_after = [i["session_id"] for i in totals_after["items"]]
    run_ids = [r["run_id"] for e in
               sh.load_index()["sessions"].values()
               for r in e.get("runs", [])]
    payload = {
        "evidence": "返修：重启分页与重建稳定（私有目录）",
        "total_before": totals_before["total"],
        "total_after": totals_after["total"],
        "order_stable": ids_before == ids_after,
        "runs": len(run_ids), "unique_runs": len(set(run_ids)),
        "verdict_pass": totals_before["total"] == totals_after["total"]
        and ids_before == ids_after
        and len(run_ids) == len(set(run_ids))}
    return dump("rework_restart_pagination.json", payload)


def dual_worker_handover():
    """证据四：双工位与交接链核验（返修版含合并与认领对应）。"""
    w1 = sh.list_sessions({"worker_id": "L3-w1"})
    w2 = sh.list_sessions({"worker_id": "L3-w2"})
    old = sh.get_session(CHAIN_OLD)
    new = sh.get_session(CHAIN_NEW)
    mine = sh.get_session(MY_SESSION)
    card_ok = bool(old.get("handover_cards")
                   and old["handover_cards"][0].get("card_excerpt"))
    payload = {
        "evidence": "返修：双工位交接与历次认领核验",
        "L3-w1": {"total": w1["total"], "sessions": [i["session_id"]
                                                     for i in w1["items"]]},
        "L3-w2": {"total": w2["total"], "sessions": [i["session_id"]
                                                     for i in w2["items"]]},
        "my_items_with_claims": {
            k: v.get("claimed_at") for k, v in
            (mine.get("items") or {}).items()},
        "handover_chain": {
            "old_successor_chain": old.get("successor_chain"),
            "new_predecessor_chain": new.get("predecessor_chain"),
            "card_readable": card_ok},
        "verdict_pass": w1["total"] >= 1 and w2["total"] >= 1
        and old.get("successor_chain") == [CHAIN_NEW]
        and new.get("predecessor_chain") == [CHAIN_OLD] and card_ok}
    return dump("rework_dual_worker_handover.json", payload)


def main():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    prod_hash_before = file_hash(PRODUCTION_INDEX)
    results = {"longtool": longtool_cloud_moment()}
    results["counts"] = source_counts(prod_hash_before)
    results["restart"] = restart_pagination()
    results["dual"] = dual_worker_handover()
    results["production_index_final_check"] = {
        "sha256_before": prod_hash_before,
        "sha256_after": file_hash(PRODUCTION_INDEX),
        "untouched": prod_hash_before == file_hash(PRODUCTION_INDEX)}
    dump("rework_real_evidence_index.json", results)
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
