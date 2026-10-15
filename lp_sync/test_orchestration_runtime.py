"""Consumer regressions; protocol stubs and independent temporary state only.

These tests do not replace the real two-process and cancellation acceptance.
"""
import json
import os
import tempfile
import types
import pathlib
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"items_registry"))

import host_service as hs
import dispatch_file_queue as dq
import orchestrator_kernel as kernel
from execution_store import ExecutionStore


class RuntimeConsumers(unittest.TestCase):
    def setUp(self):
        base = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "logs", "runtime_consumer_scratch"))
        os.makedirs(base, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="beth-runtime-consumers-", dir=base)
        if os.path.commonpath([os.path.realpath(self.tmp.name), base]) != base:
            raise RuntimeError("test temporary path escaped workspace")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.calls = []
        self.messages = {}
        calls, messages = self.calls, self.messages
        class StubChild:
            def __init__(self):
                self.proc = types.SimpleNamespace(pid=0, poll=lambda: None)
                self.loaded_sessions = set()
                self.executor_key = None
            def alive(self):
                return True
            def call(self, method, params, timeout=120):
                sid = params.get("sessionId")
                calls.append((method, sid, self.executor_key))
                if method == "session/create":
                    sid = "sess_stub_%d" % sum(c[0] == method for c in calls)
                    return {"result": {"sessionId": sid}}
                if method == "session/send":
                    messages.setdefault(sid, []).append(params["content"])
                    return {"result": {"accepted": True}}
                if method == "session/messages":
                    return {"result": {"messages": messages.get(sid, [])}}
                return {"result": {}}
        settings = dict(ProtocolChild=StubChild,
                        EXECUTION_STORE=ExecutionStore(os.path.join(self.root, "execution.json")),
                        DISPATCH_ROSTER_PATH=os.path.join(self.root, "roster.json"),
                        REGISTRY_PATH=os.path.join(self.root, "registry.json"),
                        STATUS_PATH=os.path.join(self.root, "status.json"),
                        CANCEL_DIR=os.path.join(self.root, "turn_recovery"),
                        _CHILDREN={}, _operation_locks={},
                        op_index_register=lambda *a: {"registered": False}, log=lambda *a: None,
                        _claim_for_execution=lambda *a: None, _rebind_claim=lambda *a: None)
        for key, value in settings.items():
            p = patch.object(hs, key, value);p.start();self.addCleanup(p.stop)
        actual_readback = hs._readback_hit
        p = patch.object(hs, "_readback_hit", lambda sid, marker, **kw:
                actual_readback(sid, marker, rounds=1, gap=0, exec_key=kw.get("exec_key")))
        p.start();self.addCleanup(p.stop)

    def new(self, request_id="test-request-001", verify=True):
        return hs.op_dispatch_new("test", "consumer probe", verify=verify,
                    lane="L3", item_id="I-208", request_id=request_id, run_id="run-test")

    def test_task_line_preserves_claim_identity_and_withdrawal_flags(self):
        import cloud_client
        item = {"id": "I-208", "status": "in_progress", "lane": "L3",
                "claim_session_id": "sess_exact", "claimed_by": "L3队",
                "delivery_impact": [{"review_required": True}],
                "partial_start": False, "delivery_dependencies": []}
        with patch.object(dq, "_registry_item", return_value=item), \
             patch.object(cloud_client, "enabled", return_value=True), \
             patch.object(cloud_client, "request", return_value={"items": [item]}):
            line = dq._read_item_line("I-208")
        self.assertEqual(line["item"]["claim_session_id"], "sess_exact")
        self.assertEqual(line["item"]["delivery_impact"], item["delivery_impact"])
        self.assertFalse(line["item"]["partial_start"])

    def test_same_request_replay_and_cold_reconcile_send_once(self):
        first = self.new()
        self.new()
        hs._CHILDREN.clear()
        self.new()
        self.assertEqual(sum(c[0] == "session/send" for c in self.calls), 1)
        self.assertEqual(first["session"], hs.EXECUTION_STORE.request("test-request-001")["session_id"])

    def test_request_payload_conflict_is_rejected(self):
        self.new()
        with self.assertRaises(ValueError):
            hs.op_dispatch_new("test", "different", lane="L3", item_id="I-208", request_id="test-request-001")
        self.assertEqual(sum(c[0] == "session/send" for c in self.calls), 1)

    def test_old_session_owner_survives_roster_change(self):
        first = self.new()
        hs.roster_bind("L3", "sess_new_owner")
        self.assertEqual(hs._exec_key_for_sid(first["session"]), "L3")
        self.assertEqual(list(hs._CHILDREN), ["L3"])

    def test_no_verification_single_send(self):
        first = self.new(verify=False)
        hs.op_dispatch_resume(first["session"], "single", verify=False, request_id="test-single-resume")
        self.assertEqual(sum(c[0] == "session/send" for c in self.calls), 2)

    def test_unknown_queue_does_not_retry_send(self):
        qr = os.path.join(self.root, "queue")
        req = {"schema_version": 1, "request_id": "test-unknown-queue", "op": "dispatch_new",
               "title": "test", "message": "consumer probe", "lane": "L3", "item_id": "I-208"}
        def dispatch(obj):
            return hs.op_dispatch_new(obj["title"], obj["message"], lane=obj["lane"],
                    item_id=obj["item_id"], request_id=obj["request_id"])
        worker = dq.QueueWorker(root=qr, dispatcher=dispatch, stable_grace_s=0,
                               dispatch_reconciler=lambda obj: hs.op_reconcile(obj["request_id"]))
        with patch.object(hs, "_readback_hit", return_value=False):
            dq.write_request(req, qr);worker.poll_once();worker.poll_once()
        self.assertEqual(sum(c[0] == "session/send" for c in self.calls), 1)
        self.assertTrue(os.path.isfile(os.path.join(qr, "uncertain", req["request_id"] + ".json")))
        worker.poll_once()
        self.assertEqual(sum(c[0] == "session/send" for c in self.calls), 1)
        self.assertTrue(os.path.isfile(os.path.join(qr, "done", req["request_id"] + ".result.json")))

    def test_protocol_envelope_cancel_has_run_and_executor(self):
        first = self.new()
        hs._capture_turn_event({"method": "session/event", "params": {
                              "sessionId": first["session"], "type": "turn.failed",
                              "payload": {"error": {"message": "runtime stopping"}}}})
        path = os.path.join(hs.CANCEL_DIR, os.listdir(hs.CANCEL_DIR)[0])
        with open(path, encoding="utf-8") as stream: event = json.load(stream)
        self.assertEqual(event["run_id"], "run-test")
        self.assertEqual(event["executor_key"], "L3")

    def kernel_state(self, old_phase="dispatched", run_status="active"):
        k = kernel.Kernel(state_root=os.path.join(self.root, "kernel"), queue_root=os.path.join(self.root, "bridge"))
        st = k._load_state()
        st["tasks"]["old"] = {"task_req_id": "old", "item_id": "I-208", "lane": "L3",
            "kind": "test", "message": "original", "approved_by": "Beth", "phase": old_phase,
            "dispatch_request_id": "old-dispatch", "dispatch_attempts": 1,
            "max_dispatch_attempts": 2, "run_id": "run-old", "session_id": "sess_old"}
        st["runs"]["run-old"] = {"run_id": "run-old", "task_req_id": "old", "item_id": "I-208",
                                  "session_id": "sess_old", "status": run_status}
        return k, st

    def test_cancellation_submits_actual_idempotent_continuation(self):
        k, st = self.kernel_state()
        os.makedirs(hs.CANCEL_DIR, exist_ok=True)
        p = os.path.join(hs.CANCEL_DIR, "tr-probe.json")
        with open(p, "w", encoding="utf-8") as stream:
            json.dump({"session_id": "sess_old", "run_id": "run-old", "reconcile": "unknown"}, stream)
        k.reconcile_turn_recovery(st);k.reconcile_turn_recovery(st)
        names = os.listdir(os.path.join(k.queue_root, "incoming"))
        self.assertEqual(len(names), 1)
        with open(os.path.join(k.queue_root, "incoming", names[0]), encoding="utf-8") as stream: req = json.load(stream)
        self.assertEqual((req["op"], req["sid"], req["run_id"]), ("dispatch_resume", "sess_old", "run-old"))
        self.assertNotEqual(req["request_id"], "old-dispatch")

    def test_late_cancel_does_not_reopen_completed_run(self):
        k, st = self.kernel_state("completed", "ended")
        os.makedirs(hs.CANCEL_DIR, exist_ok=True)
        with open(os.path.join(hs.CANCEL_DIR, "tr-done.json"), "w", encoding="utf-8") as stream:
            json.dump({"session_id": "sess_old", "run_id": "run-old", "reconcile": "unknown"}, stream)
        k.reconcile_turn_recovery(st)
        self.assertEqual(st["tasks"]["old"]["phase"], "completed")
        self.assertFalse(os.path.isdir(os.path.join(k.queue_root, "incoming")))

    def test_uncertain_lane_does_not_start_next_task(self):
        k, st = self.kernel_state("uncertain")
        st["tasks"]["next"] = {"task_req_id": "next", "item_id": "I-205", "lane": "L3",
            "kind": "test", "phase": "ready", "line_checked": True, "dispatch_attempts": 0,
            "max_dispatch_attempts": 2, "message": "next", "title": "next", "approved_by": "Beth"}
        kernel.save_lane_queues(k.state_root, {"L3": {"order": ["old", "next"], "current": "old", "waiting": {}}})
        k.dispatch_ready(st)
        self.assertEqual(st["tasks"]["next"]["phase"], "ready")

    def test_handoff_does_not_start_new_before_stop_receipt(self):
        first = self.new()
        with patch.object(hs, "stop_session", return_value={"ok": False, "pending_reason": "waiting"}):
            result = hs.op_handoff("L3", first["session"], "handoff", "continue", "I-208", "handoff-probe")
        self.assertFalse(result["switched"])
        self.assertEqual(hs.roster_load()["L3"], first["session"])
        self.assertEqual(sum(c[0] == "session/create" for c in self.calls), 1)

    def test_handoff_verified_then_old_owner_retired_and_replay_safe(self):
        first = self.new()
        def confirm(sid):
            hs.EXECUTION_STORE.bind(sid, "L3", phase="stopped")
            return {"ok": True, "stopped": sid}
        with patch.object(hs, "stop_session", side_effect=confirm):
            result = hs.op_handoff("L3", first["session"], "handoff", "continue", "I-208", "handoff-probe")
            repeated = hs.op_handoff("L3", first["session"], "handoff", "continue", "I-208", "handoff-probe")
        self.assertTrue(result["switched"])
        self.assertEqual(result, repeated)
        self.assertEqual(sum(c[0] == "session/send" for c in self.calls), 2)
        self.assertEqual(hs.roster_load()["L3"], result["new"])
        with self.assertRaises(ValueError):
            hs.op_dispatch_resume(first["session"], "stale", request_id="stale-resume")

    def test_generic_unknown_adapter_is_never_reinvoked(self):
        calls = []
        def unknown(req):
            calls.append(req["request_id"])
            return {"accepted": True, "readback_verified": False, "session": "stub"}
        worker = dq.QueueWorker(root=os.path.join(self.root, "queue"), dispatcher=unknown, stable_grace_s=0)
        dq.write_request({"schema_version": 1, "request_id": "unknown-adapter-probe",
                          "op": "dispatch_resume", "sid": "sess_stub_adapter", "message": "probe"}, worker.root)
        worker.poll_once();worker.poll_once();worker.poll_once()
        self.assertEqual(len(calls), 1)

    def test_rate_limit_is_durable_wait_with_no_immediate_send(self):
        import datetime
        k, st = self.kernel_state()
        os.makedirs(hs.CANCEL_DIR, exist_ok=True)
        retry = (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()
        with open(os.path.join(hs.CANCEL_DIR, "tr-rate.json"), "w", encoding="utf-8") as stream:
            json.dump({"session_id": "sess_old", "run_id": "run-old", "reconcile": "unknown",
                       "failure_category": "rate_limited", "retry_at": retry}, stream)
        k.reconcile_turn_recovery(st);k.reconcile_turn_recovery(st)
        self.assertEqual(st["tasks"]["old"]["phase"], "retry_wait")
        self.assertFalse(os.path.isdir(os.path.join(k.queue_root, "incoming")))

    def test_result_before_dispatch_receipt_is_preserved(self):
        k,st=self.kernel_state(old_phase="submitted",run_status="starting")
        obj={"schema_version":1,"kind":"result_submit","result_id":"early-result",
             "run_id":"run-old","item_id":"I-208","outcome":"evidence_appended","evidence":"isolated actual early result"}
        path=os.path.join(k.inbox,"early-result.json")
        with open(path,"w",encoding="utf-8") as stream:json.dump(obj,stream)
        k._ingest_result(st,obj,"early-result.json",path)
        self.assertEqual(st["tasks"]["old"]["phase"],"result_pending")
        self.assertEqual(st["runs"]["run-old"]["status"],"ended")
        self.assertIn("early-result",st["results"])

    def test_busy_rejection_is_held_without_blind_retry(self):
        first=self.new()
        child=hs.ensure_child("L3");original=child.call
        def busy(method,params,**kw):
            if method=="session/send":
                self.calls.append((method,params.get("sessionId"),"L3"))
                return {"error":{"code":-32010,"message":"A prompt is already running for this session"}}
            return original(method,params,**kw)
        with patch.object(child,"call",side_effect=busy):
            a=hs.op_dispatch_resume(first["session"],"continuation",request_id="busy-recovery",run_id="run-test")
            b=hs.op_dispatch_resume(first["session"],"continuation",request_id="busy-recovery",run_id="run-test")
        self.assertTrue(a["busy_wait"] and b["uncertain"])
        self.assertEqual(hs.EXECUTION_STORE.request("busy-recovery")["phase"],"busy_wait")
        self.assertEqual(sum(c[0]=="session/send" for c in self.calls),2) # initial accepted + one rejected; no third send

    def test_dispatch_retry_preserves_logical_run_identity(self):
        k,st=self.kernel_state()
        st["tasks"]["old"]["dispatch_attempts"]=9
        self.assertEqual(k._run_id_for(st["tasks"]["old"]),"run-old")

    def test_long_valid_result_can_reach_the_actual_file_bridge(self):
        k,st=self.kernel_state(old_phase="submitted",run_status="starting")
        obj={"schema_version":1,"kind":"result_submit","result_id":"r"*64,
             "run_id":"run-old","item_id":"I-208","outcome":"evidence_appended","evidence":"isolated boundary result"}
        path=os.path.join(k.inbox,obj["result_id"]+".json")
        with open(path,'w',encoding='utf-8') as stream:json.dump(obj,stream)
        k._ingest_result(st,obj,obj["result_id"]+".json",path)
        k.process_pending_results(st)
        action=st['results'][obj['result_id']]['actions'][0]
        self.assertLessEqual(len(action['bridge_request_id']),64)
        self.assertTrue(os.path.isfile(os.path.join(k.queue_root,'incoming',action['bridge_request_id']+'.json')))
        k.process_pending_results(st)
        self.assertEqual(len(os.listdir(os.path.join(k.queue_root,'incoming'))),1)

    def test_late_receipt_and_notification_replay_preserve_ended_run(self):
        k,st=self.kernel_state(old_phase="completed",run_status="ended")
        st['tasks']['old']['reviewer']='beth'
        st['results']['result-replay']={'result_id':'result-replay','run_id':'run-old','item_id':'I-208',
              'outcome':'pending_review','applied_at':'already-applied','actions':[{'args':{'evidence_ptr':'actual/evidence.json'}}]}
        k._adopt_dispatch(st,st['tasks']['old'],{'session':'sess_original','reconciled_from':'queue_ledger'})
        k._finalize_results(st);k._finalize_results(st)
        self.assertEqual(st['tasks']['old']['phase'],'completed')
        self.assertEqual(st['runs']['run-old']['status'],'ended')
        event=os.path.join(k.state_root,'notifications/pending/ev-result-replay.json')
        with open(event,encoding='utf-8') as stream:self.assertEqual(json.load(stream)['evidence_ptr'],'actual/evidence.json')

    def test_invalid_pending_review_shape_does_not_end_original_run(self):
        k,st=self.kernel_state()
        obj={'schema_version':1,'kind':'result_submit','result_id':'invalid-shape','run_id':'run-old',
             'item_id':'I-208','outcome':'pending_review','delivery_version':'v1','reviewer':'beth','evidence_ptr':['one','two']}
        path=os.path.join(k.inbox,'invalid-shape.json')
        with open(path,'w',encoding='utf-8') as stream:json.dump(obj,stream)
        k._ingest_result(st,obj,'invalid-shape.json',path)
        self.assertEqual(st['runs']['run-old']['status'],'active')
        self.assertNotIn('invalid-shape',st['results'])
        self.assertTrue(os.path.isfile(os.path.join(k.invalid,'invalid-shape.json')))


if __name__ == "__main__":
    unittest.main(verbosity=2)
