# -*- coding: utf-8 -*-
"""repo.py — 存取层：表行与领域字典的互转与全部 SQL。

上层只面对字典，不面对行与 JSON 列。事件只追加不修改。
仅使用 Python 标准库。
"""
import datetime
import json

import db

_JSON_FIELDS = ("evidence", "criteria", "replies", "blockers", "criterion_check",
                "depends_on", "partial_start", "claim_scope", "enables",
                "check_result")


def _row_to_item(row):
    if row is None:
        return None
    item = dict(row)
    for f in _JSON_FIELDS:
        v = item.get(f)
        item[f] = json.loads(v) if v else ([] if f != "criteria" else None)
    return item


def _item_to_row(item):
    row = dict(item)
    for f in _JSON_FIELDS:
        v = row.get(f)
        row[f] = json.dumps(v, ensure_ascii=False) if v is not None else None
    return row


ITEM_COLUMNS = ["id", "title", "status", "opened_at", "closed_at", "closed_by",
                "owner", "lane", "close_criterion", "note", "claimed_by",
                "claimed_at", "lease_until", "evidence", "criteria", "replies",
                "blockers", "conclusion", "criterion_check",
                "blocked_at", "blocked_by", "block_reason", "blocker_type",
                "block_needs", "unblock_decision", "unblocked_by", "unblocked_at",
                "depends_on", "partial_start", "claim_scope",
                "rationale", "line", "enables", "check_script", "check_result"]


def get_item(item_id):
    row = db.query_one("SELECT * FROM items WHERE id=?", (item_id,))
    return _row_to_item(row)


def all_items():
    return [_row_to_item(r) for r in db.query("SELECT * FROM items ORDER BY id")]


def upsert_item(conn, item):
    row = _item_to_row(item)
    cols = ",".join(ITEM_COLUMNS)
    qs = ",".join(["?"] * len(ITEM_COLUMNS))
    conn.execute("INSERT OR REPLACE INTO items (%s) VALUES (%s)" % (cols, qs),
                 [row.get(c) for c in ITEM_COLUMNS])


def next_item_id(conn):
    row = conn.execute("SELECT id FROM items").fetchall()
    nums = [int(r["id"].split("-")[1]) for r in row] or [0]
    return "I-%03d" % (max(nums) + 1)


def append_event(conn, action, item_id="", actor="", detail=""):
    conn.execute("INSERT INTO events (ts, action, item_id, actor, detail) VALUES (?,?,?,?,?)",
                 (datetime.datetime.now().isoformat(timespec="seconds"),
                  action, item_id, actor, detail))


def all_events():
    return [dict(r) for r in db.query("SELECT * FROM events ORDER BY seq")]


def events_for_item(item_id):
    return [dict(r) for r in db.query(
        "SELECT * FROM events WHERE item_id=? ORDER BY seq", (item_id,))]


def put_inbox(conn, entry):
    conn.execute("INSERT OR REPLACE INTO inbox (id, payload, decision, overrides, "
                 "submitted_at, lint) VALUES (?,?,?,?,?,?)",
                 (entry["id"], json.dumps(
                     {k: entry.get(k) for k in
                      ("title", "owner", "close_criterion", "criteria", "note",
                       "submitted_by", "submitted_at")},
                     ensure_ascii=False),
                  json.dumps(entry.get("decision"), ensure_ascii=False) if entry.get("decision") else None,
                  json.dumps(entry.get("overrides", []), ensure_ascii=False),
                  entry.get("submitted_at", ""),
                  json.dumps(entry.get("lint"), ensure_ascii=False) if entry.get("lint") else None))


def all_inbox():
    out = []
    for r in db.query("SELECT * FROM inbox ORDER BY id"):
        payload = json.loads(r["payload"])
        entry = dict(payload)
        entry["id"] = r["id"]
        entry["decision"] = json.loads(r["decision"]) if r["decision"] else None
        entry["overrides"] = json.loads(r["overrides"])
        entry["lint"] = json.loads(r["lint"]) if r["lint"] else None
        out.append(entry)
    return out


def next_inbox_id(conn):
    row = conn.execute("SELECT id FROM inbox").fetchall()
    nums = [int(r["id"].split("-")[1]) for r in row] or [0]
    return "P-%03d" % (max(nums) + 1)


def append_report(conn, report):
    conn.execute("INSERT INTO reports (payload, reported_at) VALUES (?,?)",
                 (json.dumps(report, ensure_ascii=False), report["reported_at"]))


def all_reports():
    return [json.loads(r["payload"]) for r in
            db.query("SELECT * FROM reports ORDER BY seq")]


def all_params():
    out = {}
    for r in db.query("SELECT * FROM params"):
        rec = dict(r)
        if rec.get("extra"):
            # I-130：extra 是整条记录的完整镜像（含 graph/sample 与空值占位），
            # 读取以它为准，保证本地推上来什么、快照就发回什么。
            try:
                full = json.loads(rec["extra"])
                if isinstance(full, dict) and full.get("key"):
                    out[full["key"]] = full
                    continue
            except ValueError:
                pass
        rec.pop("extra", None)
        out[rec["key"]] = rec
    return out


# params 表的扁平列只是兼容投影；整条记录（含 graph、sample、空值
# 占位）序列化进 extra 列，读回以 extra 为准。
_PARAM_FLAT = ("key", "value", "unit", "status", "source", "since", "note")


def replace_params(conn, params):
    """I-130：以本地登记库权威内容整体替换 params 镜像表。

    扁平七列直写（空值落空串以满足非空约束），整条记录另存 extra
    列，读回时以 extra 为准，保证摘要往返一致。只在代理上行同步
    通道调用。"""
    conn.execute("DELETE FROM params")
    for key, rec in params.items():
        row = {c: rec.get(c) for c in _PARAM_FLAT}
        row["key"] = key
        row["value"] = rec.get("value")
        if row["value"] is None:
            row["value"] = ""
        row["status"] = rec.get("status") or "current"
        stored = dict(rec)
        stored.setdefault("key", key)     # retire 改写过的记录可能缺自述键
        row["extra"] = json.dumps(stored, ensure_ascii=False)
        conn.execute(
            "INSERT OR REPLACE INTO params (key,value,unit,status,source,since,note,extra) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (row["key"], row["value"], row["unit"], row["status"],
             row["source"], row["since"], row["note"], row["extra"]))
    return len(params)


def replace_params_archive(conn, archive):
    """I-130：退役档案镜像整体替换（教学层历史取值的数据源）。"""
    conn.execute("DELETE FROM params_archive")
    for e in archive:
        conn.execute("INSERT INTO params_archive (param, payload) VALUES (?,?)",
                     (e.get("param") or "",
                      json.dumps(e, ensure_ascii=False)))
    return len(archive)


def set_config(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO config VALUES (?,?)",
                 (key, json.dumps(value, ensure_ascii=False)))


def get_config(key, default=None):
    row = db.query_one("SELECT value FROM config WHERE key=?", (key,))
    return json.loads(row["value"]) if row else default


def heartbeat(conn, agent_id, info):
    conn.execute("INSERT OR REPLACE INTO agents VALUES (?,?,?)",
                 (agent_id, json.dumps(info, ensure_ascii=False),
                  datetime.datetime.now().isoformat(timespec="seconds")))


def snapshot():
    """全量账本快照：页面与本地缓存共用这一个形状。"""
    return {
        "items": all_items(),
        "inbox": all_inbox(),
        "reports": all_reports(),
        "events": all_events(),
        "params": all_params(),
        "lanes": get_config("lanes", {}),
        "server_seq": db.server_seq(),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
