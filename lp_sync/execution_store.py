"""Persistent execution ownership and request reconciliation for I-208.

This is execution bookkeeping. The cloud ledger remains the item authority.
Only the singleton host writes this store; its queue and socket threads share
the same lock and module instance. No lock is held across a protocol call.
"""
import copy
import datetime
import hashlib
import json
import os
import threading
import uuid


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


class ExecutionStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as stream:
                return json.load(stream)
        except FileNotFoundError:
            return {"schema_version": 1, "sessions": {}, "requests": {}}

    def _save(self, state):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + "." + uuid.uuid4().hex + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def session(self, sid):
        with self.lock:
            return copy.deepcopy(self._load()["sessions"].get(sid))

    def sessions(self):
        with self.lock:
            return copy.deepcopy(self._load()["sessions"])

    def requests(self):
        with self.lock:
            return copy.deepcopy(self._load()["requests"])

    def bind(self, sid, executor_key, **fields):
        with self.lock:
            state = self._load()
            prior = state["sessions"].get(sid, {})
            if prior.get("executor_key") not in (None, executor_key):
                raise ValueError("session already belongs to executor %s" %
                                 prior["executor_key"])
            prior.update({k: v for k, v in fields.items() if v is not None})
            prior.update(session_id=sid, executor_key=executor_key, updated_at=now())
            state["sessions"][sid] = prior
            self._save(state)
            return copy.deepcopy(prior)

    def begin(self, request_id, payload):
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                             sort_keys=True).encode("utf-8")).hexdigest()
        with self.lock:
            state = self._load()
            prior = state["requests"].get(request_id)
            if prior:
                if prior["payload_hash"] != digest:
                    raise ValueError("request_id reused with a different payload")
                return copy.deepcopy(prior), False
            rec = {"request_id": request_id, "payload_hash": digest,
                   "payload": payload, "phase": "prepared", "created_at": now()}
            state["requests"][request_id] = rec
            self._save(state)
            return copy.deepcopy(rec), True

    def restore_session(self, sid, prior):
        with self.lock:
            state = self._load()
            state["sessions"][sid] = copy.deepcopy(prior)
            self._save(state)

    def request(self, request_id):
        with self.lock:
            return copy.deepcopy(self._load()["requests"].get(request_id))

    def update(self, request_id, **fields):
        with self.lock:
            state = self._load()
            rec = state["requests"][request_id]
            rec.update(fields)
            rec["updated_at"] = now()
            self._save(state)
            return copy.deepcopy(rec)

