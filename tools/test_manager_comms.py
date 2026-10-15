"""Meaningful isolated checks; never sends to a real recipient."""
import asyncio
import contextlib
import copy
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import manager_message_service as service
import send_manager_message as legacy

BASE = {"sender": "elsa", "to": "Doro", "purpose": "执行任务", "item_id": "I-186",
        "summary": "二队已开始修正输入来源与结果记录的对应错误。",
        "action": "请独立复核所附交付材料，并登记结论。",
        "authorization": {"basis": "隔离检查依据，不向真实会话投递"}}


class CommunicationTests(unittest.TestCase):
    def setUp(self):
        temp_root = service.ROOT / "logs/hq_intake/elsa_comms_registration_20261014/test_tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.db = Path(self.temp.name) / "audit.sqlite3"
    def tearDown(self):
        self.temp.cleanup()

    def test_preview_and_counts(self):
        prepared = service.prepare(BASE)
        self.assertEqual(prepared["message"],
                         "Doro，我是 Elsa。关于 I-186：" + BASE["summary"] + "\n" + BASE["action"]
                         + '\n请直接执行，返回结果或具体阻碍；无需确认收悉。')
        self.assertEqual(prepared["character_counts"]["summary"], len(BASE["summary"]))
        self.assertNotIn("authorization", prepared["message"])
        self.assertNotIn("thread_id", prepared["message"])

    def test_single_send_accepts_all_fields_without_preview(self):
        payload = BASE | {"details": "验收要求保持不变。", "evidence": ["organization/contacts.json"]}
        calls = []
        def transport(address, message):
            calls.append((address, message))
            return {"exit_code": 0}
        result = service.send(payload, db_path=self.db, transport=transport)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(calls), 1)
        message = calls[0][1]
        self.assertIn(payload["details"], message)
        self.assertIn(payload["evidence"][0], message)
        self.assertNotIn("/80字", message)
        self.assertNotIn("｜", message)
        self.assertEqual(result["purpose"], payload["purpose"])
        self.assertEqual(result["character_counts"]["details"], len(payload["details"]))
        with service.connect(self.db) as conn:
            record = json.loads(conn.execute("SELECT record FROM messages").fetchone()[0])
        self.assertEqual(record["authorization"], payload["authorization"])
        self.assertNotIn(payload["authorization"]["basis"], message)

    def test_role_profile_rejects_other_sender(self):
        with patch.dict(service.os.environ, {"MANAGER_COMMS_ROLE": "elsa"}):
            self.assertEqual(service.prepare(BASE)["sender_identity"], "configuration_bound")
            with self.assertRaises(service.MessageError):
                service.send(BASE | {"sender": "beth"}, db_path=self.db,
                             transport=lambda *_: self.fail("other sender sent"))

    def test_two_purposes_and_no_passive_actions(self):
        actions = ['等待通知。', '请等待二队交付。', '交付后，请再复核。', '收到材料后再验收。',
                   '修复交付后，请你独立复核。', '二队交付后，请你独立复核。',
                   '请在收到交付材料后独立验收。', '先等二队提交后再复核。', '请确认收到。', '请回复收到。', '无需操作。']
        for action in actions:
            with self.subTest(action=action), self.assertRaises(service.MessageError):
                service.send(BASE | {'action': action}, db_path=self.db,
                             transport=lambda *_: self.fail('passive action sent'))
        for purpose in ['通知', '请求', '交接', '反馈', '阻塞']:
            with self.subTest(purpose=purpose), self.assertRaises(service.MessageError):
                service.prepare(BASE | {'purpose': purpose})
        self.assertFalse(self.db.exists())

    def test_owner_information_transfer_needs_no_reply(self):
        payload = BASE | {'purpose': '机主信息移交', 'action': '无需操作，无需回复。',
                          'authorization': {'basis': '隔离样例：机主说将这条信息转交 Doro；不真实发送'}}
        prepared = service.prepare(payload)
        self.assertIn('无需操作，无需回复。', prepared['message'])
        self.assertNotIn('请直接执行', prepared['message'])
        self.assertNotIn(payload['authorization']['basis'], prepared['message'])
        with self.assertRaises(service.MessageError):
            service.prepare(payload | {'action': '请回复收到。'})

    def test_invalid_messages_stop_before_transport(self):
        changes = [dict(summary="字" * 81), dict(action=""), dict(summary="已完成原第四条决定"),
                   dict(summary="accepted=true"), dict(details="11:49:02.503"), dict(to="不存在的经理"),
                   dict(sender="不存在的人"), dict(purpose="随便"), dict(extra="未定义字段"),
                   dict(evidence=["missing-no-such-evidence.json"]), dict(summary="一句\n另一句")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(service.MessageError):
                service.send(BASE | change, db_path=self.db,
                             transport=lambda *_: self.fail("invalid input reached transport"))
        with self.assertRaises(service.MessageError):
            service.send({k: v for k, v in BASE.items() if k != "authorization"}, db_path=self.db)
        self.assertFalse(self.db.exists())

    def test_atomic_duplicate_during_concurrent_calls(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def transport(*args):
            calls.append(args)
            entered.set()
            release.wait(5)
            return {"exit_code": 0}
        payload = BASE | {"request_id": "isolated-concurrent-1"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(service.send, payload, db_path=self.db, transport=transport)
            self.assertTrue(entered.wait(5))
            duplicate = service.send(payload, db_path=self.db, transport=transport)
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["status"], "pending")
            release.set()
            self.assertEqual(future.result()["status"], "queued")
        self.assertEqual(len(calls), 1)

    def test_unclear_result_never_retries_same_request(self):
        calls = []
        def transport(*args):
            calls.append(args)
            raise subprocess.TimeoutExpired("isolated", 45)
        result = service.send(BASE, db_path=self.db, transport=transport)
        self.assertEqual(result["status"], "result_unclear")
        replay = service.send(BASE, db_path=self.db, transport=transport)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(len(calls), 1)

    def test_conflicting_request_rejected(self):
        payload = BASE | {"request_id": "isolated-conflict-1"}
        service.send(payload, db_path=self.db, transport=lambda *_: {"exit_code": 0})
        with self.assertRaises(service.MessageError):
            service.send(payload | {"action": "请复核交付材料并登记失败项。"}, db_path=self.db,
                         transport=lambda *_: self.fail("conflict resent"))

    def test_committed_pending_survives_recovery(self):
        p = service.prepare(BASE)
        with service.connect(self.db) as conn:
            conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,NULL)",
                         (p["request_id"], p["payload_hash"], "pending", service.now(), service.now(), json.dumps(p)))
        result = service.send(BASE, db_path=self.db, transport=lambda *_: self.fail("pending resent"))
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["duplicate"])

    def test_contact_change_uses_new_address(self):
        contacts = Path(self.temp.name) / "contacts.json"
        book = json.loads(service.CONTACTS.read_text(encoding="utf-8"))
        addresses = []
        def transport(address, message):
            addresses.append(address["thread_id"])
            return {"exit_code": 0}
        contacts.write_text(json.dumps(book), encoding="utf-8")
        service.send(BASE | {"request_id": "first-address-1"}, contacts_path=contacts, db_path=self.db, transport=transport)
        book["members"]["doro"]["contact"]["thread_id"] = "00000000-0000-0000-0000-000000000001"
        contacts.write_text(json.dumps(book), encoding="utf-8")
        service.send(BASE | {"request_id": "second-address-1"}, contacts_path=contacts, db_path=self.db, transport=transport)
        self.assertNotEqual(*addresses)

    def test_legacy_argument_values_preserved(self):
        calls = []
        original = legacy.send
        legacy.send = lambda *a, **kw: (calls.append((a, kw)) or (0, {}))
        try:
            legacy._main(["Anni", "隔离检查", "--authorization", "已有授权", "--request-id", "isolated-id", "--json", "output.json"])
            self.assertEqual(calls[-1][1]["authorization"], "已有授权")
            self.assertEqual(calls[-1][1]["request_id"], "isolated-id")
            self.assertEqual(calls[-1][1]["json_out"], "output.json")
            legacy._main(["Anni", "隔离检查", "--contacts", "fixture.json", "--resolve-only"])
            self.assertEqual(calls[-1][1]["contacts_path"], "fixture.json")
            self.assertTrue(calls[-1][1]["resolve_only"])
        finally:
            legacy.send = original

    def test_real_mcp_discovery_and_preview(self):
        async def exercise():
            params = StdioServerParameters(command=sys.executable,
                                           args=[str(service.ROOT / "tools/manager_comms_mcp.py")])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    self.assertIn("默认使用", init.instructions)
                    tools = await session.list_tools()
                    self.assertEqual({t.name for t in tools.tools}, {"send_manager_message", "preview_manager_message"})
                    preview = await session.call_tool("preview_manager_message", {k: v for k, v in BASE.items() if k != "authorization"})
                    self.assertFalse(preview.is_error)
                    data = preview.structured_content
                    self.assertEqual(data["status"], "preview_only")
                    self.assertIn(BASE['action'], data["message"])
                    self.assertEqual(next(t for t in tools.tools if t.name == 'send_manager_message').input_schema['properties']['purpose']['enum'], ['执行任务', '机主信息移交'])
                    passive = await session.call_tool('send_manager_message', BASE | {'action': '修复交付后，请你独立复核。'})
                    self.assertTrue(passive.is_error)
                    self.assertEqual(passive.structured_content['status'], 'rejected')
                    information = await session.call_tool('preview_manager_message', BASE | {'purpose': '机主信息移交', 'action': '无需操作，无需回复。'})
                    self.assertFalse(information.is_error)
                    self.assertIn('无需操作，无需回复。', information.structured_content['message'])
                    reject = await session.call_tool("send_manager_message", BASE | {"summary": "accepted=true"})
                    self.assertTrue(reject.is_error)
                    self.assertEqual(reject.structured_content["status"], "rejected")
        asyncio.run(asyncio.wait_for(exercise(), timeout=25))


if __name__ == "__main__":
    unittest.main(verbosity=2)
