"""Structured manager communication: validate, render, reserve, queue, audit.

No conversation-list search or implicit authorization. A declared sender is not
an authenticated role. MCP clients retain their own human-authorization policy.
"""
from __future__ import annotations

import getpass
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator
from send_manager_message import resolve

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = ROOT / "organization/communication/message.schema.json"
RULES_FILE = ROOT / "organization/communication/expression_rules.json"
CONTACTS = ROOT / "organization/contacts.json"
AUDIT_DB = ROOT / "logs/manager_communications/messages.sqlite3"


def schema():
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MessageError(ValueError):
    pass


def prepare(payload, contacts_path=CONTACTS):
    errors = sorted(Draft202012Validator(schema()).iter_errors(payload),
                    key=lambda e: str(list(e.path)))
    if errors:
        raise MessageError("；".join(
            "%s：%s" % (".".join(map(str, e.path)) or "输入", e.message)
            for e in errors[:3]))
    rules = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    for field in ("sender", "to", "summary", "action"):
        if not payload[field].strip() or payload[field] != payload[field].strip():
            raise MessageError(f"{field} 不能为空或包含首尾空白")
    if not payload["authorization"]["basis"].strip():
        raise MessageError("authorization.basis 必须引用已有机主授权")
    text_fields = {k: payload.get(k, "") for k in ("summary", "action", "details")}
    for field, text in text_fields.items():
        if "\n" in text or "\r" in text or any(ord(c) < 32 for c in text):
            raise MessageError(f"{field} 须为一段完整短句，不嵌入日志或额外字段")
        for term, hint in rules["avoid_terms"].items():
            if term in text:
                raise MessageError(f"{field} 中的“{term}”不够清楚：{hint}")
        for rule in rules["avoid_patterns"]:
            if re.search(rule["regex"], text):
                raise MessageError(f"{field}：{rule['hint']}")
    body_length = sum(map(len, text_fields.values()))
    if body_length > rules["body_limit"]:
        raise MessageError(f"摘要、动作、补充合计 {body_length} 字，超过 {rules['body_limit']} 字")
    for pointer in payload.get("evidence", []):
        if "\n" in pointer or "\r" in pointer:
            raise MessageError("证据入口须为单行路径或链接")
        if pointer.startswith("https://"):
            continue
        path = Path(pointer)
        if path.is_absolute() or ":" in pointer or ".." in path.parts:
            raise MessageError("证据文件使用项目相对路径或 https 链接")
        target = (ROOT / path).resolve()
        if not target.is_relative_to(ROOT) or not target.is_file():
            raise MessageError(f"证据文件不存在或不属于项目：{pointer}")
    book = json.loads(Path(contacts_path).read_text(encoding="utf-8-sig"))
    sender_key = payload["sender"].lower()
    if sender_key not in book["members"]:
        raise MessageError(f"发送者 {sender_key} 未登记于通讯录")
    bound_role = os.environ.get("MANAGER_COMMS_ROLE")
    if bound_role and sender_key != bound_role:
        raise MessageError(f"本服务绑定岗位 {bound_role}，不能声明其他发送者")
    try:
        target, target_key = resolve(payload["to"], str(contacts_path))
    except (ValueError, AttributeError, TypeError) as exc:
        raise MessageError(str(exc).split("（人员标识：")[0]) from exc
    sender = book["members"][sender_key]
    counts = {"sender": len(sender_key), "to": len(payload["to"]),
              "purpose": len(payload["purpose"]),
              **{k: len(v) for k, v in text_fields.items()}, "body": body_length}
    introduction = f"{target['name']}，我是 {sender['name']}。"
    if payload.get("item_id"):
        introduction += f"关于 {payload['item_id']}："
    lines = [introduction + payload['summary'], payload['action']]
    if payload.get("details"):
        lines.append(payload['details'])
    if payload.get("evidence"):
        lines.append("依据：" + "；".join(payload["evidence"]))
    if payload['purpose'] == '执行任务':
        lines.append('请直接执行，返回结果或具体阻碍；无需确认收悉。')
    rendered = "\n".join(lines)
    counts["rendered"] = len(rendered)
    if counts["rendered"] > rules["rendered_limit"]:
        raise MessageError("消息含证据入口的总长度超过700字，请缩短路径或补充")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    rid = payload.get("request_id") or "mgr-" + fingerprint[:28]
    return {"request_id": rid, "payload_hash": fingerprint,
            "sender": sender_key, "recipient": target_key,
            "purpose": payload["purpose"], "item_id": payload.get("item_id"),
            "character_counts": counts, "message": rendered,
            "address": target["contact"], "authorization": payload["authorization"],
            "sender_identity": "configuration_bound" if bound_role else "declared",
            "os_caller": getpass.getuser()}


def public_result(prepared, status, *, duplicate=False, reason=None):
    result = {k: prepared[k] for k in ("request_id", "sender", "recipient", "purpose", "item_id", "character_counts")}
    result.update(status=status, duplicate=duplicate, sender_identity=prepared["sender_identity"])
    if reason:
        result["reason"] = reason
    return result


def queue_transport(address, message):
    executable = shutil.which("codex")
    if not executable:
        raise FileNotFoundError("codex 未在服务 PATH 中")
    result = subprocess.run([executable, "queue", "--thread", address["thread_id"],
                             "--message", message], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=45,
                            cwd=ROOT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"exit_code": result.returncode, "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:]}


@contextlib.contextmanager
def connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS messages (request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, record TEXT NOT NULL, channel_result TEXT)")
    try:
        yield conn
    finally:
        conn.close()


def send(payload, *, contacts_path=CONTACTS, db_path=AUDIT_DB, transport=queue_transport):
    prepared = prepare(payload, contacts_path)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload_hash,status,record FROM messages WHERE request_id=?", (prepared["request_id"],)).fetchone()
        if row:
            conn.commit()
            if row[0] != prepared["payload_hash"]:
                raise MessageError("同一请求号的内容已改变，拒绝重新投递；明确新意图后使用新编号")
            original = json.loads(row[2])
            return public_result(original, row[1], duplicate=True,
                                 reason="已有记录，未再次发送；pending或result_unclear须先对账")
        stamp = now()
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,NULL)",
                     (prepared["request_id"], prepared["payload_hash"], "pending", stamp, stamp,
                      json.dumps(prepared, ensure_ascii=False)))
        conn.commit()
        try:
            result = transport(prepared["address"], prepared["message"])
            status = "queued" if result["exit_code"] == 0 else "channel_rejected"
        except (subprocess.TimeoutExpired, OSError) as exc:
            status = "channel_missing" if isinstance(exc, FileNotFoundError) else "result_unclear"
            result = {"error": str(exc)[:1000]}
        except Exception as exc:
            status = "result_unclear"
            result = {"error": f"{type(exc).__name__}: {str(exc)[:800]}"}
        conn.execute("UPDATE messages SET status=?,updated_at=?,channel_result=? WHERE request_id=?",
                     (status, now(), json.dumps(result, ensure_ascii=False), prepared["request_id"]))
    reason = {"queued": "通道已受理，尚未证明收件人收到或处理",
              "channel_rejected": "发送通道拒绝，详情在后台审计；未搜索其他会话",
              "channel_missing": "本机发送通道不可用",
              "result_unclear": "发送结果不明；保留请求，先对账，不能盲目重发"}[status]
    return public_result(prepared, status, reason=reason)
