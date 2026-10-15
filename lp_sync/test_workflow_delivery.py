"""Cloud consumer tests against a separate workspace-local SQLite database."""
import datetime
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "items_registry"))
sys.path.insert(0, str(ROOT / "cloud_board"))
import db
import repo
import auth
import api_ledger
import api_dispatch
import api_agent
from httpd import ApiError


class WorkflowConsumers(unittest.TestCase):
    def setUp(self):
        base = ROOT / "logs/workflow_consumer_scratch"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(db.settings, "DB_PATH", str(pathlib.Path(self.temp.name) / "isolated.sqlite"))
        self.db_patch.start();self.addCleanup(self.db_patch.stop)
        db._local = None
        db.init_schema()
        self.addCleanup(lambda: (db.connect().close(), setattr(db, "_local", None)))
        caller = patch.object(auth, "caller", return_value=("isolated", ["L3", "L5"]))
        caller.start();self.addCleanup(caller.stop)
        with db.transaction() as conn:
            for iid, lane, status in [("I-101", "L3", "in_progress"), ("I-102", "L3", "open"), ("I-103", "L5", "open")]:
                repo.upsert_item(conn, {"id": iid, "title": "isolated workflow", "status": status,
                       "opened_at": "2026-10-13", "owner": lane + "队", "lane": lane,
                       "claimed_by": "L3队" if status == "in_progress" else None,
                       "lease_until": (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat(),
                       "claim_session_id": "sess_old" if status == "in_progress" else None,
                       "reviewer": "beth", "depends_on": [], "evidence": [], "criteria": []})

    def ctx(self, body):
        return types.SimpleNamespace(body=body, headers={}, cookie=lambda _: None)

    def submit(self, **overrides):
        payload = {"item_id": "I-101", "run_id": "run-test", "delivery_version": "version-1",
                   "evidence_ptr": "fixture/delivery.json", "reviewer": "beth"}
        payload.update(overrides)
        return api_ledger.submit_pending_review(self.ctx(payload))

    def test_pending_review_is_persistent_and_releases_worker(self):
        self.submit()
        api_ledger.attach(self.ctx({"item_id": "I-101", "evidence": ["extra fixture evidence"]}))
        item = repo.get_item("I-101")
        self.assertEqual(item["status"], "pending_review")
        self.assertEqual(item["pending_review"]["delivery_version"], "version-1")
        self.assertIsNone(item["claimed_by"])
        api_dispatch.claim(self.ctx({"item_id": "I-102", "worker": "L3队", "session_id": "sess_next"}))
        self.assertEqual(repo.get_item("I-102")["status"], "in_progress")

    def test_pending_review_duplicate_is_idempotent(self):
        self.submit()
        before = len(repo.events_for_item("I-101"))
        self.submit()
        self.assertEqual(len(repo.events_for_item("I-101")), before)
        with self.assertRaises(ApiError):self.submit(delivery_version="other")

    def test_lost_reply_replays_exact_operation_without_duplicate_evidence(self):
        body = {"item_id": "I-101", "evidence": "隔离原请求证据", "operation_id": "isolated-op-001"}
        first = api_ledger.append_evidence(self.ctx(body))
        self.assertEqual(api_ledger.append_evidence(self.ctx(body)), first)
        self.assertEqual(repo.get_item("I-101")["evidence"].count(body["evidence"]), 1)
        receipt = api_agent.operation_receipt(self.ctx({"operation_id":body["operation_id"],
                              "action":"append_evidence", "payload":body}))
        self.assertTrue(receipt["result"]["recorded"])
        self.assertEqual(receipt["result"]["operation_result"], first)

    def test_same_operation_rejects_changed_body(self):
        body = {"item_id": "I-101", "evidence": "隔离原请求证据", "operation_id": "isolated-op-002"}
        api_ledger.append_evidence(self.ctx(body))
        with self.assertRaises(ApiError):
            api_ledger.append_evidence(self.ctx(dict(body,evidence="另一份隔离证据")))
        self.assertNotIn("另一份隔离证据",repo.get_item("I-101")["evidence"])

    def test_side_effect_and_receipt_rollback_together(self):
        @repo.idempotent_write("isolated_failure")
        def failing(ctx):
            with db.transaction() as conn:
                item=repo.get_item("I-101");item["evidence"].append("回滚证据")
                repo.upsert_item(conn,item)
            raise ValueError("controlled interruption before receipt")
        with self.assertRaises(ValueError):failing(self.ctx({"item_id":"I-101","operation_id":"isolated-op-003"}))
        self.assertNotIn("回滚证据",repo.get_item("I-101")["evidence"])
        self.assertIsNone(db.connect().execute("SELECT 1 FROM operation_receipts WHERE operation_id=?",("isolated-op-003",)).fetchone())

    def test_pending_receipt_survives_later_item_transition(self):
        self.submit(operation_id="isolated-op-004")
        payload={"item_id":"I-101","run_id":"run-test","delivery_version":"version-1",
                 "evidence_ptr":"fixture/delivery.json","reviewer":"beth","operation_id":"isolated-op-004"}
        with db.transaction() as conn:
            item=repo.get_item("I-101");item["status"]="open";repo.upsert_item(conn,item)
        self.assertTrue(api_agent.operation_receipt(self.ctx({"operation_id":"isolated-op-004",
                     "action":"submit_pending_review","payload":payload}))["result"]["recorded"])
        self.submit(operation_id="isolated-op-004")
        self.assertEqual(repo.get_item("I-101")["status"],"open")

    def test_invalid_evidence_list_is_a_clear_api_rejection(self):
        with self.assertRaises(ApiError):self.submit(evidence_ptr=['one','two'])
        self.assertEqual(repo.get_item('I-101')['status'],'in_progress')

    def test_hard_dependency_remains_blocked(self):
        self.submit()
        api_ledger.set_depends(self.ctx({"item_id": "I-103", "depends_on": ["I-101"]}))
        with self.assertRaises(ApiError):
            api_dispatch.claim(self.ctx({"item_id": "I-103", "worker": "L5队"}))

    def test_exact_authorized_delivery_dependency_is_consumed(self):
        self.submit()
        api_ledger.set_depends(self.ctx({"item_id": "I-103", "depends_on": ["I-101"]}))
        entry = {"item_id": "I-101", "requires": "delivered", "delivery_version": "version-1",
                 "evidence_ptr": "fixture/delivery.json", "approved_by": "Beth",
                 "authorization_source": "isolated authorization fixture"}
        api_ledger.set_delivery_dependencies(self.ctx({"item_id": "I-103", "delivery_dependencies": [entry]}))
        api_dispatch.claim(self.ctx({"item_id": "I-103", "worker": "L5队"}))
        self.assertEqual(repo.get_item("I-103")["status"], "in_progress")

    def test_delivery_version_mismatch_does_not_release(self):
        self.submit()
        api_ledger.set_depends(self.ctx({"item_id": "I-103", "depends_on": ["I-101"]}))
        api_ledger.set_delivery_dependencies(self.ctx({"item_id": "I-103", "delivery_dependencies": [{
            "item_id": "I-101", "requires": "delivered", "delivery_version": "wrong",
            "evidence_ptr": "fixture/delivery.json", "approved_by": "Beth", "authorization_source": "fixture"}]}))
        with self.assertRaises(ApiError):api_dispatch.claim(self.ctx({"item_id": "I-103", "worker": "L5队"}))

    def test_reviewer_mismatch_is_rejected(self):
        with self.assertRaises(ApiError):self.submit(reviewer="someone_else")

    def test_handoff_session_compare_and_swap(self):
        req = {"item_id": "I-101", "old_session_id": "sess_old", "session_id": "sess_new"}
        api_dispatch.rebind_session(self.ctx(req));api_dispatch.rebind_session(self.ctx(req))
        self.assertEqual(repo.get_item("I-101")["claim_session_id"], "sess_new")
        with self.assertRaises(ApiError):
            api_dispatch.rebind_session(self.ctx({**req, "session_id": "sess_other"}))

    def test_full_parameter_mirror_survives_nested_values(self):
        params = {"p": {"value": 1, "unit": "m", "status": "current", "graph": {"source": [1, 2]}}}
        with db.transaction() as conn:
            repo.replace_params(conn, params)
            repo.replace_params_archive(conn, [{"param": "p", "value": 0}])
        self.assertEqual(repo.snapshot()["params"], params)
        self.assertEqual(len(repo.snapshot()["params_archive"]), 1)

    def test_review_return_preserves_version_and_allows_reclaim(self):
        self.submit()
        api_ledger.return_for_rework(self.ctx({"item_id":"I-101","reviewer":"beth","reason":"isolated review failed",
            "owner_note":"隔离返工验收的机主说明：保存第一版交付和证据位置，指定审核人提出已查明的返工原因，事项退回施工后原认领释放；重新认领和提交第二版时原版本仍须保留，不把退回操作当成生产验收通过。"}))
        item=repo.get_item("I-101")
        self.assertEqual(item["status"],"open")
        self.assertTrue(item["pending_review"]["revoked"])
        api_dispatch.claim(self.ctx({"item_id":"I-101","worker":"L3队"}))
        self.submit(run_id="run-v2",delivery_version="version-2")
        self.assertEqual(repo.get_item("I-101")["pending_review"]["history"][0]["delivery_version"],"version-1")

    def test_revoke_marks_existing_result_and_holds_downstream_until_review(self):
        self.submit()
        binding={"item_id":"I-101","requires":"delivered","delivery_version":"version-1",
             "evidence_ptr":"fixture/delivery.json","approved_by":"Beth","authorization_source":"isolated fixture"}
        api_ledger.set_delivery_dependencies(self.ctx({"item_id":"I-103","delivery_dependencies":[binding]}))
        api_dispatch.claim(self.ctx({"item_id":"I-103","worker":"L5队"}))
        self.submit(item_id="I-103",run_id="consumer",delivery_version="consumer-v1",evidence_ptr="fixture/consumer.json")
        note="隔离交付撤回验收的机主说明：审核人明确撤回已交付版本，保留依赖方已产生的结果及出处，停止继续使用撤回交付的新派发，原事项修复并提交第二版后更新明确版本绑定，再独立办理影响复核。"
        api_ledger.return_for_rework(self.ctx({"item_id":"I-101","reviewer":"beth","reason":"withdrawal fixture","owner_note":note}))
        consumer=repo.get_item("I-103")
        self.assertEqual(consumer["pending_review"]["evidence_ptr"],"fixture/consumer.json")
        self.assertTrue(consumer["delivery_impact"][0]["review_required"])
        with self.assertRaises(ApiError):
            api_ledger.review_delivery_impact(self.ctx({"item_id":"I-103","reviewer":"beth","owner_note":note,"evidence_ptr":"fixture/review"}))
        api_dispatch.claim(self.ctx({"item_id":"I-101","worker":"L3队"}))
        self.submit(run_id="second",delivery_version="version-2")
        api_ledger.set_delivery_dependencies(self.ctx({"item_id":"I-103","delivery_dependencies":[{**binding,"delivery_version":"version-2"}]}))
        api_ledger.review_delivery_impact(self.ctx({"item_id":"I-103","reviewer":"beth","owner_note":note,"evidence_ptr":"fixture/review"}))
        self.assertFalse(repo.get_item("I-103")["delivery_impact"][0]["review_required"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
