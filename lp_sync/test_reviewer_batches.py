"""Batch state tests; no real messaging, queue or contact records."""
import datetime
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import reviewer_batches as rb


class ReviewerBatches(unittest.TestCase):
    def setUp(self):
        base = pathlib.Path(__file__).resolve().parents[1] / "logs/batch_consumer_scratch"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.nroot = os.path.join(self.root, "notifications")
        self.outbox = os.path.join(self.root, "outbox")
        self.now = 1700000000.0
        self.address = {"platform": "codex", "host_id": "local", "thread_id": "fixture-reviewer",
                        "verified_at": "2026-10-13T00:00:00+08:00"}

    def event(self, eid, item, age=30, category="pending_review"):
        rb.write(os.path.join(self.nroot, "pending", eid + ".json"), {
            "event_id": eid, "item_id": item, "run_id": "run-" + eid,
            "reviewer": "beth", "category": category, "evidence_ptr": "fixture/" + eid,
            "action_needed": "review", "created_at": datetime.datetime.fromtimestamp(self.now-age).isoformat()})

    def poll(self):
        return rb.poll(self.nroot, lambda _: (self.address, None), self.outbox, clock=lambda: self.now)

    def package(self):
        names = list(pathlib.Path(self.outbox).glob("*/*.json"))
        self.assertEqual(len(names), 1)
        return str(names[0])

    def prepare(self, path):
        package=rb.read(path)
        grant={"owner_approved":True,"source":"explicit isolated authorization fixture",
               "recipient":self.address["thread_id"],"batch_id":package["batch_id"]}
        return rb.prepare_send(path,"fixture-sender",grant,lambda _:(self.address,None))

    def test_two_items_one_batch_no_fake_delivery_and_repeat_safe(self):
        self.event("event-a", "I-101");self.event("event-b", "I-102")
        self.poll();self.poll()
        package = rb.read(self.package())
        self.assertEqual(len(package["event_ids"]), 2)
        self.assertIsNone(package["receipt"])
        self.assertEqual(os.listdir(os.path.join(self.nroot, "delivered")), [])

    def test_real_receipt_shape_and_individual_ack(self):
        self.event("event-a", "I-101");self.event("event-b", "I-102")
        self.poll();package = self.package()
        self.prepare(package)
        # Explicit synthetic fixture response, not a production delivery claim.
        rb.record_receipt(package, {"threadId": "fixture-reviewer", "fixture": True})
        self.poll();self.poll()
        self.assertEqual(len(os.listdir(os.path.join(self.nroot, "delivered"))), 2)
        rb.ack(self.nroot, "event-a", "beth")
        self.assertEqual(os.listdir(os.path.join(self.nroot, "acked")), ["event-a.ack.json"])
        with self.assertRaises(ValueError):rb.ack(self.nroot, "event-b", "luna")

    def test_missing_receipt_and_unknown_send_attempt_never_retransmit(self):
        self.event("event-a", "I-101");self.poll();package = self.package()
        self.prepare(package);self.poll()
        with self.assertRaises(ValueError):self.prepare(package)
        with self.assertRaises(ValueError):rb.ack(self.nroot, "event-a", "beth")
        self.assertIsNone(rb.read(package)["receipt"])

    def test_window_is_bounded_and_urgent_event_is_immediate(self):
        self.event("normal", "I-101", age=1);self.poll()
        self.assertEqual(os.listdir(os.path.join(self.nroot, "batches")), [])
        self.event("urgent", "I-102", age=0, category="blocked");self.poll()
        self.assertEqual(rb.read(self.package())["event_ids"], ["urgent"])
        self.now += 11;self.poll()
        self.assertEqual(len(list(pathlib.Path(self.outbox).glob("*/*.json"))), 2)

    def test_preparation_crash_recovers_same_batch(self):
        self.event("event-a", "I-101")
        real = rb.write
        def crash(path, value):
            if path.startswith(self.outbox):raise RuntimeError("fixture crash before package")
            return real(path, value)
        with patch.object(rb, "write", side_effect=crash):
            with self.assertRaises(RuntimeError):self.poll()
        self.poll();self.poll()
        self.assertEqual(len(rb.read(self.package())["event_ids"]), 1)

    def test_three_items_and_other_reviewer_are_separate(self):
        for eid,item in (("one","I-101"),("two","I-102"),("three","I-103"),("other","I-104")):
            self.event(eid,item)
        path=os.path.join(self.nroot,"pending/other.json");event=rb.read(path);event["reviewer"]="other";rb.write(path,event)
        self.poll();self.poll()
        batches=[rb.read(str(p)) for p in pathlib.Path(self.nroot,"batches").glob('*.json')]
        self.assertEqual(sorted(len(b["event_ids"]) for b in batches),[1,3])
        self.assertEqual(len({eid for b in batches for eid in b["event_ids"]}),4)

    def test_unverified_and_changed_address_preserve_original_packet(self):
        self.event("one","I-101")
        self.address["verified_at"]=None;self.poll()
        self.assertTrue(pathlib.Path(self.nroot,"pending/one.json").exists())
        self.address["verified_at"]="2026-10-13T00:00:00+08:00";self.poll();path=self.package();original=rb.read(path)
        self.address=dict(self.address,thread_id="new-fixture-reviewer");self.poll()
        self.assertEqual(rb.read(path)["to"],original["to"])
        with self.assertRaises(ValueError):self.prepare(path)
        self.assertIsNone(rb.read(path)["send_attempt"])

    def test_missing_authorization_cannot_send(self):
        self.event("one","I-101");self.poll();path=self.package()
        with self.assertRaises(ValueError):rb.prepare_send(path,"fixture-sender")
        self.assertIsNone(rb.read(path)["send_attempt"])
        self.assertIsNone(rb.read(path)["receipt"])

    def test_full_batch_triggers_before_window_and_bounds_are_finite(self):
        for number in range(3):self.event("event-"+str(number),"I-10"+str(number),age=0)
        rb.poll(self.nroot,lambda _:(self.address,None),self.outbox,window_s=10000,max_events=3,clock=lambda:self.now)
        self.assertEqual(len(rb.read(self.package())["event_ids"]),3)

    def test_delivery_projection_crash_recovers_without_second_send(self):
        self.event("one","I-101");self.event("two","I-102");self.poll();path=self.package()
        self.prepare(path);rb.record_receipt(path,{"threadId":self.address["thread_id"],"fixture":True})
        actual=rb.write
        def fail(path,value):
            if path.endswith('two.delivery.json') and 'delivered' in path:raise RuntimeError('controlled receipt projection interruption')
            return actual(path,value)
        with patch.object(rb,'write',side_effect=fail):
            with self.assertRaises(RuntimeError):self.poll()
        self.poll();self.poll()
        self.assertEqual(len(list(pathlib.Path(self.nroot,'delivered').glob('*.json'))),2)
        self.assertIsNotNone(rb.read(path)["send_attempt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
