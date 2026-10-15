"""I-232 metadata endpoint against isolated SQLite transactions, never live data."""
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class MetadataApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "outputs")
        self.database = str(Path(self.temp.name) / "isolated.sqlite")
        self.local = threading.local()
        self.conn().executescript("CREATE TABLE config(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE events(detail TEXT);")
        self.initial = {"a": {"value": "3", "unit": "Hz", "status": "current", "extra": "legacy bytes",
                              "extension": {"unknown": [None, False, 1]}}, "b": {"value": "4", "unit": "m"}}
        self.put("params", self.initial)
        self.put("archive", [{"param": "a", "value": "old", "unknown": True}])
        db, repo, auth, router, httpd = (ModuleType(n) for n in ("db", "repo", "auth", "router", "httpd"))
        db.transaction = self.transaction
        repo.get_config = self.get
        repo.set_config = lambda conn, key, value: self.put(key, value)
        repo.all_params = lambda: self.get("params")
        repo.replace_params = lambda conn, value: self.put("params", value)
        repo.append_event = lambda conn, kind, **kw: conn.execute("INSERT INTO events VALUES (?)", (kind,))
        auth.caller = lambda ctx: ("isolated-test", [])
        router.route = lambda *a: (lambda fn: fn)
        httpd.ApiError = ValueError
        modules = {"db": db, "repo": repo, "auth": auth, "router": router, "httpd": httpd}
        spec = importlib.util.spec_from_file_location("isolated_i232_api", ROOT / "cloud_board/server/api_param_semantics.py")
        self.api = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.api)

    def tearDown(self):
        self.conn().close()
        self.temp.cleanup()

    def conn(self):
        if not hasattr(self.local, "connection"):
            self.local.connection = sqlite3.connect(self.database, isolation_level=None, timeout=10)
        return self.local.connection

    def get(self, key):
        row = self.conn().execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        self.conn().execute("INSERT OR REPLACE INTO config VALUES (?,?)", (key, json.dumps(value)))

    @contextmanager
    def transaction(self):
        conn = self.conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def body(self, migration="test-1", name="已核名称"):
        return {"migration_id": migration, "expected_digest": self.api.digest(self.get("params")),
                "patches": {"a": {"schema_version": 1, "display_name": name,
                    "usage_tag": "undetermined", "review_state": {"name": "verified"},
                    "review": {"reason_codes": ["no_consumer_evidence"]}}}}

    def call(self, body):
        return self.api.patch_semantics(SimpleNamespace(body=body))["result"]

    def test_core_extensions_archive_preserved(self):
        archive = deepcopy(self.get("archive"))
        result = self.call(self.body())
        records = self.get("params")
        del records["a"]["semantics"]
        self.assertEqual(records, self.initial)
        self.assertEqual(self.get("archive"), archive)
        self.assertEqual(result["changed_keys"], 1)

    def test_stale_revision_rejected(self):
        body = self.body()
        revised = deepcopy(self.initial)
        revised["b"]["value"] = "changed by another writer"
        self.put("params", revised)
        with self.assertRaisesRegex(ValueError, "版本已变化"):
            self.call(body)
        self.assertEqual(self.get("params"), revised)

    def test_replay_and_migration_identity(self):
        body = self.body()
        first = self.call(body)
        second = self.call(body)
        self.assertTrue(second["replay"])
        self.assertEqual(first["after_digest"], second["after_digest"])
        body["patches"]["a"]["display_name"] = "different"
        with self.assertRaisesRegex(ValueError, "不同补丁"):
            self.call(body)
        self.assertEqual(self.conn().execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_core_patch_and_relation_cycle_rejected_atomically(self):
        body = self.body()
        body["patches"]["a"]["value"] = 999
        with self.assertRaises(ValueError):
            self.call(body)
        body = self.body()
        body["patches"]["a"]["alias_of"] = "b"
        body["patches"]["b"] = {"alias_of": "a", "usage_tag": "historical"}
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.call(body)
        self.assertEqual(self.get("params"), self.initial)

    def test_concurrent_same_revision_has_one_winner(self):
        first, second = self.body("one", "first"), self.body("two", "second")
        outcomes = []
        barrier = threading.Barrier(2)
        def worker(body):
            barrier.wait()
            try:
                self.call(body)
                outcomes.append("written")
            except ValueError:
                outcomes.append("stale")
            finally:
                self.conn().close()
        threads = [threading.Thread(target=worker, args=(body,)) for body in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["written", "stale"])


if __name__ == "__main__":
    unittest.main()
