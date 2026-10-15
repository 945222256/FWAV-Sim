# -*- coding: utf-8 -*-
"""i228_build_fixture.py — I-228 隔离核验夹具索引构建（写入私有目录，
不触生产索引）。夹具形态逐字段取自真实索引
lp_sync/session_history/index.json 的条目结构。

构成（判据一至三样例面）：
  L3 交接链 A→B→C→D（三次交接，覆盖 70% 交接后新会话在办样例）；
  L3 双工位旧会话：L3-w1（A、C 与填充）、L3-w2（B、D 与填充）；
  总量 127 条（页容量 50 → 三页，遍历不重不漏检查）；
  四类缺失样例：已删除（session_db 来源 + missing 会话行已删除）、
  无对话源（仅登记来源、无会话库来源）、无认领记录（items 空）、
  接口失败（检查中停服务制造，不进索引）；
  保留类型：retained_timeout 多数、explicit_archive 一条（L5）、
  no_source 一条（L6）、active 若干（含 D）。
会话号统一 mock_i228f 前缀的 UUID 形态，避免与真实会话号撞型。"""
import datetime
import json
import os
import uuid

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "logs", "i228_session_history_ui_20261015")
FIX = os.path.join(OUT, "fixture_index")
os.makedirs(FIX, exist_ok=True)
CARD_DIR = os.path.join(FIX, "cards")
os.makedirs(CARD_DIR, exist_ok=True)

now = "2026-10-15T20:55:00"
T = lambda h, m=0: "2026-10-%02dT%02d:%02d:00" % (14 + (h // 24), h % 24, m)  # noqa: E731


def sid(tag):
    return "sess_%s-%s" % (tag, uuid.uuid5(uuid.NAMESPACE_DNS, "i228f:" + tag))


def card_file(tag, text):
    p = os.path.join(CARD_DIR, "handoff_%s.md" % tag)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def entry(s, lane, title, items, runs=(), workers=None, handovers=(),
           succ=None, pred=None, retention=("retained_timeout",
           "空闲超时保留：夹具样例"), sources=("dispatch_registry", "session_db"),
           missing=None, first="2026-10-14T08:00:00", last="2026-10-15T12:00:00",
           execution_phase=None):
    return {
        "session_id": s, "lane": lane, "title_head": title,
        "workers": workers or {}, "items": items, "runs": list(runs),
        "handovers": list(handovers), "successor": succ, "predecessor": pred,
        "first_seen_at": first, "last_seen_at": last,
        "retention": {"kind": retention[0], "reason": retention[1],
                      "at": last},
        "sources": list(sources), "missing": missing or {},
        "execution_phase": execution_phase,
    }


def item(claimed, *srcs):
    return {"first": None, "last": None, "sources": list(srcs),
            "claimed_at": claimed}


sessions = {}

# ── L3 交接链 A→B→C→D（三次交接） ──
A = sid("chainA"); B = sid("chainB"); C = sid("chainC"); D = sid("chainD")
cards = {
    "a": card_file("A", "# 交接卡 A→B\n\n70% 上下文达成，已取得定向停止和新会话回读回执。\n夹具交接卡样例。"),
    "b": card_file("B", "# 交接卡 B→C\n\n70% 上下文达成，自动接续派发回读在册。"),
}
def H(f, t, tag, reason, use_file, hour):
    return {"at": T(hour), "from": f, "to": t,
            "reason": reason, "card": cards[tag] if use_file else None,
            "source": "handoff_state"}

sessions[A] = entry(A, "L3", "夹具链头 A（I-999 首任）",
    {"I-999": item(T(9), "dispatch_registry", "kernel_runs")},
    runs=({"run_id": "run-i228f-a1", "item_id": "I-999", "worker_id": "L3-w1",
           "started_at": T(9), "status": "ended"},),
    workers={"L3-w1": {"last": T(9)}},
    handovers=[H(A, B, "a", "已取得定向停止和新会话回读回执", True, 10),
               H(A, B, "a2", "handoff 事件文件", False, 10)],
    succ=B, pred=None, last=T(10))
sessions[B] = entry(B, "L3", "夹具链二 B（自动接续：I-999）",
    {"I-999": item(T(10), "kernel_runs")},
    runs=({"run_id": "run-i228f-b1", "item_id": "I-999", "worker_id": "L3-w2",
           "started_at": T(10), "status": "ended"},),
    workers={"L3-w2": {"last": T(10)}},
    handovers=[H(A, B, "a", "已取得定向停止和新会话回读回执", True, 10)],
    succ=C, pred=A, last=T(11))
sessions[C] = entry(C, "L3", "夹具链三 C（I-999 续办）",
    {"I-999": item(T(11), "kernel_runs")},
    runs=({"run_id": "run-i228f-c1", "item_id": "I-999", "worker_id": "L3-w1",
           "started_at": T(11), "status": "ended"},),
    workers={"L3-w1": {"last": T(11)}},
    handovers=[H(B, C, "b", "70% 上下文达成，自动接续派发", True, 11)],
    succ=D, pred=B, last=T(12))
sessions[D] = entry(D, "L3", "夹具链四 D（交接后新会话正常工作）",
    {"I-999": item(T(12), "kernel_runs")},
    runs=({"run_id": "run-i228f-d1", "item_id": "I-999", "worker_id": "L3-w2",
           "started_at": T(12), "status": "running"},),
    workers={"L3-w2": {"last": T(12)}},
    handovers=[H(C, D, "c", "70% 上下文达成，自动接续派发", False, 12)],
    succ=None, pred=C, last=now,
    retention=("active", "运行中：活跃运行在册"),
    sources=("dispatch_registry", "session_db", "kernel_runs"),
    execution_phase="active")

# ── 四类缺失样例（判据三） ──
E = sid("deleted")     # 已删除：曾有会话库来源，现 missing 记会话行已删除
sessions[E] = entry(E, "L3", "已删除样例（原会话库来源在册后删除）",
    {"I-998": item(T(13), "dispatch_registry")},
    sources=("dispatch_registry", "session_db"),
    missing={"session_db": "会话行已删除（原在册来源，现会话库无该行）"},
    last=T(13))
F = sid("nodialog")    # 无对话源：仅登记来源，从未有会话库来源
sessions[F] = entry(F, "L4", "无对话源样例（仅派发登记来源）",
    {"I-997": item(T(13, 30), "dispatch_registry")},
    sources=("dispatch_registry",),
    missing={"dialog": "无会话库来源：该会话仅登记与运行来源，无对话源"},
    last=T(13, 30))
G = sid("noclaim")     # 无认领记录：items 空
sessions[G] = entry(G, "L6", "无认领记录样例（无任何事项来源）",
    {}, sources=("session_db",), last=T(14))
# 接口失败样例：检查中停止查询服务制造，不在索引内。

# ── 保留类型对照 ──
AR = sid("archived")
sessions[AR] = entry(AR, "L5", "显式归档样例（I-149 切换归档）",
    {"I-996": item(T(8), "dispatch_registry")},
    sources=("handoff_state", "session_db"),
    retention=("explicit_archive", "显式归档：上下文切换归档，原因 夹具显式归档样例"),
    last=T(9))
NS = sid("nosource")
sessions[NS] = entry(NS, "L6", "来源缺失样例（无已核实来源）",
    {}, sources=(), retention=("no_source", "无已核实来源：登记、运行、归属与归档均无"),
    last=T(7), missing={"all": "无本项目已核实来源记录"})

# ── 双工位与分页填充（凑足 127 条，分布在 L3/L2/L7/L1） ──
n = 0
for i in range(117):
    n += 1
    lane = ("L3" if i % 4 == 0 else "L2" if i % 4 == 1
            else "L7" if i % 4 == 2 else "L1")
    worker = "L3-w1" if lane == "L3" and i % 8 == 0 else (
             "L3-w2" if lane == "L3" else None)
    s = sid("fill%03d" % i)
    sessions[s] = entry(s, lane, "夹具填充会话 %03d" % i,
        {"I-8%02d" % (i % 90): item(T(15 + (i % 20)), "dispatch_registry")},
        runs=({"run_id": "run-i228f-f%03d" % i, "item_id": "I-8%02d" % (i % 90),
               "worker_id": worker, "started_at": T(15 + (i % 20)),
               "status": "ended"},) if i % 3 == 0 else (),
        workers={worker: {"last": T(15 + (i % 20))}} if worker else {},
        last="2026-10-1%dT%02d:00:00" % (4 + (i % 2), i % 24))

index = {
    "schema_version": 1,
    "delivery_version": "session-observability-v2",
    "built_at": now,
    "stats": {"sessions": len(sessions),
              "retention": {
                  "retained_timeout": sum(1 for e in sessions.values()
                      if e["retention"]["kind"] == "retained_timeout"),
                  "explicit_archive": 1, "no_source": 1,
                  "active": sum(1 for e in sessions.values()
                      if e["retention"]["kind"] == "active")},
              "by_lane": {ln: sum(1 for e in sessions.values() if e["lane"] == ln)
                          for ln in ("L1", "L2", "L3", "L4", "L5", "L6", "L7")}},
    "collection_timeline": {"fixture": True},
    "sessions": sessions,
}
with open(os.path.join(FIX, "index.json"), "w", encoding="utf-8") as f:
    json.dump(index, f, ensure_ascii=False, indent=1)
meta = {"total": len(sessions), "by_lane": index["stats"]["by_lane"],
        "chain": {"A": A, "B": B, "C": C, "D": D},
        "deleted": E, "nodialog": F, "noclaim": G,
        "archived": AR, "nosource": NS,
        "l3_sids": sorted(s for s, e in sessions.items() if e["lane"] == "L3"),
        "l3_w1_sids": sorted(s for s, e in sessions.items()
                             if e["lane"] == "L3" and "L3-w1" in e["workers"]),
        "all_sids": sorted(sessions)}
with open(os.path.join(OUT, "fixture_meta.json"), "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=1)
print("fixture:", len(sessions), "sessions;", json.dumps(index["stats"]["by_lane"],
                                                          ensure_ascii=False))
print("chain:", A[:16], B[:16], C[:16], D[:16])
