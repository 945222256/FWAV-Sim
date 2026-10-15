# -*- coding: utf-8 -*-
"""repo.py — 存取层：表行与领域字典的互转与全部 SQL。

上层只面对字典，不面对行与 JSON 列。事件只追加不修改。
仅使用 Python 标准库。
"""
import datetime
import json

import db


def operation_hash(action, payload):
    import hashlib
    return hashlib.sha256(json.dumps([action, payload], sort_keys=True,
                          ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def idempotent_write(action):
    """Commit the side effect and its original reply in one SQLite transaction."""
    import functools
    def decorate(handler):
        @functools.wraps(handler)
        def wrapped(ctx):
            operation_id = ctx.body.get("operation_id")
            if not operation_id:
                return handler(ctx)
            import auth
            from httpd import ApiError
            actor, lanes = auth.caller(ctx)
            digest = operation_hash(action, ctx.body)
            with db.transaction() as conn:
                prior = conn.execute("SELECT * FROM operation_receipts WHERE operation_id=?",
                                     (operation_id,)).fetchone()
                if prior:
                    if prior["actor"] != actor or prior["action"] != action or prior["request_hash"] != digest:
                        raise ApiError("原请求号的身份、动作或正文冲突，禁止覆盖", 409)
                    item = get_item(prior["item_id"]) if prior["item_id"] else None
                    if item and not auth.lane_allowed(lanes, item.get("lane")):
                        raise ApiError("令牌无原事项路线权限", 403)
                    return json.loads(prior["result"])
                result = handler(ctx)
                conn.execute("INSERT INTO operation_receipts VALUES (?,?,?,?,?,?,?)",
                    (operation_id, actor, action, digest, ctx.body.get("item_id"),
                     json.dumps(result, ensure_ascii=False), datetime.datetime.now().isoformat()))
                return result
        return wrapped
    return decorate

_JSON_FIELDS = ("evidence", "criteria", "replies", "blockers", "criterion_check",
                "depends_on", "partial_start", "claim_scope", "enables",
                "check_result", "pending_review", "delivery_dependencies", "delivery_impact")


def _row_to_item(row):
    if row is None:
        return None
    item = dict(row)
    for f in _JSON_FIELDS:
        v = item.get(f)
        item[f] = json.loads(v) if v else (None if f in ("criteria", "pending_review") else [])
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
                "rationale", "line", "enables", "check_script", "check_result",
                "kind", "goal", "reviewer", "pending_review", "delivery_dependencies", "claim_session_id", "claim_worker_id", "delivery_impact"]


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
                       "submitted_by", "submitted_at", "reviewer")},
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
    full = get_config("params_full_document")
    if isinstance(full, dict):
        return full
    return {r["key"]: dict(r) for r in db.query("SELECT * FROM params")}


def replace_params(conn, params):
    """Preserve the full existing mirror payload, including nested graph fields."""
    set_config(conn, "params_full_document", params)
    conn.execute("DELETE FROM params")
    for key, payload in params.items():
        payload = payload if isinstance(payload, dict) else {"value": payload}
        values = [payload.get(k) for k in ("value", "unit", "status", "source", "since", "note")]
        if values[0] is None:
            values[0] = "null"
        if values[2] is None:
            values[2] = "unknown"
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in values]
        conn.execute("INSERT INTO params (key,value,unit,status,source,since,note) VALUES (?,?,?,?,?,?,?)", [key] + values)
    return len(params)


def replace_params_archive(conn, archive):
    conn.execute("DELETE FROM params_archive")
    for entry in archive:
        conn.execute("INSERT INTO params_archive (param,payload) VALUES (?,?)",
                      (str(entry.get("param", entry.get("key", ""))), json.dumps(entry, ensure_ascii=False)))
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
        "params_archive": [json.loads(r["payload"]) for r in db.query("SELECT payload FROM params_archive ORDER BY seq")],
        "lanes": get_config("lanes", {}),
        "server_seq": db.server_seq(),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
