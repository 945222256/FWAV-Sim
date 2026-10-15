"""I-232 local contract tests. Persistent tests; no live registry/cloud writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from params_registry import registry, semantics
from lp_board import lab_payload

ROOT = Path(__file__).resolve().parents[1]


def evidence(**extra):
    return {"path": "modules/flight_controller.py", "symbol": "FlightController.update",
            "line": 422, "sha256": "0" * 64, **extra}


def unreviewed():
    value = semantics.empty_semantics()
    value["review"]["reason_codes"] = ["no_consumer_evidence"]
    return value


def record(**extra):
    return {"key": "ctrl.gain.fixture", "value": "1", "unit": "rad", "status": "current",
            "source": "isolated fixture", "since": "2026-01-01", "note": None, **extra}


def payload(params, archive=None):
    # These tests exercise parameter contracts, not unrelated visual assets or
    # formula walls that require the entire real registry in small fixtures.
    with patch.object(lab_payload, "FORMULAS", []), patch.object(lab_payload, "build_eaglex700", return_value={}):
        return lab_payload.build_payload(params, archive or [])


class ContractTests(unittest.TestCase):
    def test_paper_locators_reject_containers_and_invalid_scalar_types(self):
        paper = {"kind": "paper", "reference": "fixture paper", "version": "v1", "claim": "fixture claim"}
        for locator in ({"page": 1}, {"page": "12-14"}, {"table": "Table I"}, {"equation": "(3)"}):
            with self.subTest(valid=locator):
                result = semantics.merge_semantics(unreviewed(), {"source_refs": [{**paper, **locator}]})
                self.assertEqual(result["source_refs"], [{**paper, **locator}])
        invalid = [{}, {"page": 0}, {"page": -1}, {"page": True}, {"page": False}, {"page": 1.0}]
        for key in ("page", "table", "equation"):
            invalid.extend({key: item} for item in ([], {}, None, "", " "))
        invalid.extend(({"table": 1}, {"equation": 1}, {"page": 1, "table": []}))
        for locator in invalid:
            with self.subTest(invalid=locator), self.assertRaisesRegex(ValueError, "paper locator"):
                semantics.merge_semantics(unreviewed(), {"source_refs": [{**paper, **locator}]})

    def test_illegal_contracts_and_evidence_gates(self):
        cases = [
            {"schema_version": 2}, {"schema_version": True}, {"usage_tag": "unused"},
            {"usage_tag": []}, {"consumer_state": "enabled"},
            {"review_state": {"name": "accepted"}}, {"review_state": {"definition": "verified"}},
            {"sort_order": True}, {"sort_order": -1}, {"facets": {"role": ["invented"]}},
            {"facets": {"channel": ["yaw", "yaw"]}},
            {"usage_tag": "current"}, {"usage_tag": "reserved"}, {"usage_tag": "historical"},
            {"consumer_state": "conditional"}, {"consumer_state": "not_found"},
            {"applicability": {"sample_scope": "common", "sample_ids": ["215g"]}},
            {"applicability": {"sample_scope": "listed"}},
            {"applicability": {"default_path": "conditional"}},
            {"consumer_refs": [{"path": "config/sim_config.py"}]},
            {"consumer_refs": [evidence(kind="declaration")]},
            {"consumer_refs": [evidence(kind=[])]},
            {"source_refs": [{"path": "config/sim_config.py", "symbol": "VALUE", "line": 1}]},
            {"review": {"reason_codes": ["invented"]}},
            {"review": {"reason_codes": []}},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                semantics.merge_semantics(unreviewed(), changes)

    def test_supported_but_default_disabled_and_offline_use_are_distinct(self):
        for state in ("connected", "conditional", "offline_only"):
            value = semantics.merge_semantics(unreviewed(), {
                "usage_tag": "current", "consumer_state": state,
                "consumer_refs": [evidence()],
                "applicability": {"default_path": "disabled", "conditions": ["FEATURE_ON is true"]},
            })
            self.assertEqual(value["usage_tag"], "current")
            self.assertEqual(value["applicability"]["default_path"], "disabled")
            self.assertEqual(value["consumer_state"], state)

    def test_not_found_has_versioned_scope_and_is_not_reserved(self):
        value = semantics.merge_semantics(unreviewed(), {
            "consumer_state": "not_found",
            "review": {"search_scope": {"paths": ["modules/"], "version": "fixture-v1"}},
        })
        self.assertEqual(value["usage_tag"], "undetermined")
        self.assertEqual(value["consumer_refs"], [])
        with self.assertRaises(ValueError):
            semantics.merge_semantics(value, {"usage_tag": "reserved", "source_refs": [evidence()]})
        value = semantics.merge_semantics(value, {"usage_tag": "reserved", "source_refs": [evidence()],
                                                   "review": {"next_owner": "fixture-owner"}})
        self.assertEqual(value["usage_tag"], "reserved")

    def test_pure_merge_preserves_extensions_and_refuses_unknown_patch(self):
        value = unreviewed()
        value["vendor_extension"] = {"null": None, "array": [0, "", False]}
        value["review"]["vendor_note"] = {"keep": True}
        before = deepcopy(value)
        result = semantics.merge_semantics(value, {"display_name": "测试名称"})
        self.assertEqual(value, before)
        self.assertEqual(result["vendor_extension"], before["vendor_extension"])
        self.assertEqual(result["review"]["vendor_note"], before["review"]["vendor_note"])
        result["vendor_extension"]["array"].append(1)
        self.assertEqual(value, before)
        for changes in ({"value": "changed"}, {"invented": True}, {"review": {"invented": True}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                semantics.merge_semantics(value, changes)

    def test_alias_targets_cycles_and_equal_values(self):
        records = {"a": record(semantics=unreviewed()), "b": record(semantics=unreviewed())}
        self.assertIsNone(semantics.merge_semantics(unreviewed(), {})["alias_of"])
        for target in ("a", "missing"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                semantics.merge_semantics(unreviewed(), {"alias_of": target}, param_id="a", records=records)
        records["b"]["semantics"]["superseded_by"] = ["a"]
        with self.assertRaises(ValueError):
            semantics.merge_semantics(unreviewed(), {"alias_of": "b"}, param_id="a", records=records)
        with self.assertRaises(ValueError):
            semantics.merge_semantics(unreviewed(), {"alias_of": "b"})

    def test_relation_cycles_inside_nested_extra_are_rejected(self):
        target = unreviewed()
        target["alias_of"] = "a"
        for depth in (1, 2):
            nested = {"semantics": target}
            for _ in range(depth):
                nested = {"extra": json.dumps(nested)}
            records = {"a": record(semantics=unreviewed()), "b": record(**nested)}
            before = deepcopy(records)
            with self.subTest(depth=depth), self.assertRaisesRegex(ValueError, "cycle"):
                semantics.merge_semantics(unreviewed(), {"alias_of": "b"}, param_id="a", records=records)
            self.assertEqual(records, before)
        # A nearer explicit semantic record remains authoritative, even when
        # an older mirror contains a conflicting alias back to the candidate.
        records["b"]["semantics"] = unreviewed()
        merged = semantics.merge_semantics(unreviewed(), {"alias_of": "b"}, param_id="a", records=records)
        self.assertEqual(merged["alias_of"], "b")


class ReadAndProjectionTests(unittest.TestCase):
    def test_invalid_v1_is_preserved_but_not_projected_as_verified(self):
        base = unreviewed()
        base.update(display_name="不得断言的名称", definition="不得断言的定义")
        base["review_state"].update(name="verified", definition="verified", usage="verified")
        cases = [
            {"usage_tag": "reserved"},
            {"usage_tag": "reserved", "source_refs": [evidence()]},
            {"usage_tag": "reserved", "review": {**base["review"], "next_owner": "fixture-owner"}},
            {"usage_tag": "current", "consumer_state": "connected", "consumer_refs": [{}]},
        ]
        for changes in cases:
            invalid = {**deepcopy(base), **changes}
            original = deepcopy(invalid)
            with self.subTest(changes=changes):
                row = payload({"ctrl.gain.fixture": record(semantics=invalid)})["params"][0]
                self.assertEqual(row["semantics"], original)
                self.assertEqual(invalid, original)
                self.assertIn("invalid_semantics", row["semantics_diagnostics"])
                self.assertIn("语义契约无效", semantics.project_text("ctrl.gain.fixture", invalid, "name"))
                for text in (row["short_name"], row["explanation"], row["teaching"]["definition"]):
                    self.assertIn("语义契约无效", text)
                    self.assertIn("ctrl.gain.fixture", text)
                    self.assertNotIn("不得断言", text)

    def test_informational_diagnostics_do_not_demote_verified_text(self):
        value = unreviewed()
        value.update(display_name="已核名称", definition="已核定义", alias_of="other.key")
        value["review_state"].update(name="verified", definition="verified")
        older = deepcopy(value)
        older["display_name"] = "旧名称"
        row = payload({"ctrl.gain.fixture": record(semantics=value, extra=json.dumps({"semantics": older}))})["params"][0]
        self.assertIn("legacy_semantics_conflict", row["semantics_diagnostics"])
        self.assertIn("relations_require_registry_validation", row["semantics_diagnostics"])
        self.assertNotIn("invalid_semantics", row["semantics_diagnostics"])
        self.assertEqual(row["short_name"], "已核名称")
        self.assertEqual(row["teaching"]["definition"], "已核定义")

    def test_relation_read_preserves_data_without_claiming_registry_validation(self):
        value = unreviewed()
        value.update(usage_tag="historical", alias_of="other.key")
        selected, diagnostics = semantics.read_semantics(record(semantics=value))
        self.assertEqual(selected, value)
        self.assertIn("relations_require_registry_validation", diagnostics)
        self.assertFalse(any("invalid parameter semantics" in d for d in diagnostics))

    def test_top_semantics_wins_but_legacy_core_rule_stays(self):
        top = unreviewed()
        top.update(display_name="规范名称", definition="规范定义")
        top["review_state"].update(name="verified", definition="verified")
        older = deepcopy(top)
        older["display_name"] = "旧镜像名称"
        raw = record(semantics=top, extra=json.dumps({"value": "legacy-projection", "semantics": older}))
        before = deepcopy(raw)
        normalized = lab_payload._normalize(raw)
        self.assertEqual(normalized["value"], "legacy-projection")
        self.assertEqual(normalized["semantics"]["display_name"], "规范名称")
        self.assertIn("legacy_semantics_conflict", normalized["semantics_diagnostics"])
        self.assertEqual(raw, before)

    def test_nested_semantics_does_not_promote_graph_or_sample(self):
        sem = unreviewed()
        sem["vendor_extension"] = [None, 0, ""]
        raw = record(extra=json.dumps({"extra": json.dumps({"semantics": sem, "graph": {"status": "缺失"}, "sample": "700g"})}))
        normalized = lab_payload._normalize(raw)
        self.assertEqual(normalized["semantics"], sem)
        self.assertNotIn("graph", normalized)
        self.assertNotIn("sample", normalized)
        self.assertEqual(normalized["extra"], raw["extra"])

    def test_broken_deep_and_oversized_mirrors_are_diagnostic(self):
        for extra, code in (("{broken", "invalid_extra_json"), ("[]", "invalid_extra_object")):
            sem, diagnostics = semantics.read_semantics(record(extra=extra))
            self.assertIn(code, diagnostics)
            self.assertEqual(sem["usage_tag"], "undetermined")
        deep = {"semantics": unreviewed()}
        for _ in range(10):
            deep = {"extra": json.dumps(deep)}
        sem, diagnostics = semantics.read_semantics(deep, max_depth=2)
        self.assertIn("extra_depth_exceeded", diagnostics)
        self.assertIn("semantics_missing", diagnostics)
        self.assertEqual(sem["consumer_state"], "unknown")
        self.assertIn("invalid_or_oversized_extra", semantics.read_semantics(record(extra=" " * 10), max_extra_chars=5)[1])

    def test_future_version_is_preserved_read_only(self):
        future = {"schema_version": 2, "display_name": "未来名称", "review_state": {"name": "verified"}, "new": {"keep": [None]}}
        result = payload({"ctrl.gain.fixture": record(semantics=future)})
        row = result["params"][0]
        self.assertEqual(row["semantics"], future)
        self.assertIn("unsupported_schema_version", row["semantics_diagnostics"])
        self.assertIn("语义版本暂不支持", row["short_name"])
        row["semantics"]["new"]["keep"].append(1)
        self.assertEqual(future["new"]["keep"], [None])
        with self.assertRaises(ValueError):
            semantics.merge_semantics(future, {"display_name": "覆盖"})

    def test_name_and_definition_are_projected_by_independent_review_slots(self):
        value = unreviewed()
        value.update(display_name="已核名称", definition="尚未确认的定义")
        value["review_state"].update(name="verified", definition="disputed")
        rows = payload({"ctrl.gain.fixture": record(semantics=value)})
        self.assertEqual(rows["schema"], "lab-payload-v2")
        self.assertEqual(rows["metadata_schema"], "param-semantics-v1")
        row = rows["params"][0]
        self.assertEqual(row["short_name"], "已核名称")
        self.assertIn("存在争议", row["teaching"]["definition"])
        self.assertIn("ctrl.gain.fixture", row["explanation"])
        value["review_state"].update(name="stale", definition="verified")
        row = payload({"ctrl.gain.fixture": record(semantics=value)})["params"][0]
        self.assertIn("证据版本已变化", row["short_name"])
        self.assertEqual(row["teaching"]["definition"], value["definition"])
        row = payload({"ctrl.gain.fixture": record()})["params"][0]
        self.assertIn("尚未核对", row["short_name"])
        self.assertIn("ctrl.gain.fixture", row["short_name"])


class RetirementTests(unittest.TestCase):
    def test_retire_preserves_full_extensions_archive_and_new_payload_value(self):
        mirrors = [None, "{broken", "[]", json.dumps({"custom": [1]}),
                   json.dumps({"value": "old", "unit": "rad", "status": "superseded", "source": "old", "since": "old-date",
                               "custom": {"keep": True}, "extra": json.dumps({"value": "deeper-old", "sample": "700g"})})]
        for mirror in mirrors:
            with self.subTest(mirror=mirror), tempfile.TemporaryDirectory(prefix="i232-semantics-", dir=ROOT / "tests") as tmp:
                root = Path(tmp)
                params_path, archive_path = root / "params.json", root / "archive.json"
                old = record(value="old", since=None, sample="215g", semantics=unreviewed(),
                             graph={"downstream": ["child"]}, vendor={"keep": [None, "", 0]})
                if mirror is not None:
                    old["extra"] = mirror
                old_copy = deepcopy(old)
                records = {"ctrl.gain.fixture": old, "child": record(graph={"status": "已完成"})}
                params_path.write_text(json.dumps({"params": records}), encoding="utf-8")
                archive_path.write_text(json.dumps({"archive": [{"param": "earlier", "value": None}]}), encoding="utf-8")
                with patch.multiple(registry, PARAMS_PATH=str(params_path), ARCHIVE_PATH=str(archive_path), _DIR=tmp):
                    registry.retire("ctrl.gain.fixture", {"new": [0, None]}, "new-source", "fixture retirement")
                    current = registry.get_record("ctrl.gain.fixture")
                    archived = registry.history("ctrl.gain.fixture")[0]
                    all_current = json.loads(params_path.read_text(encoding="utf-8"))["params"]
                self.assertEqual(archived["record_snapshot"], old_copy)
                self.assertEqual(archived["value"], "old")
                self.assertEqual(current["vendor"], old_copy["vendor"])
                self.assertEqual(current["sample"], "215g")
                self.assertEqual(current["semantics"], old_copy["semantics"])
                self.assertEqual(all_current["child"]["graph"]["status"], "待重算")
                self.assertTrue((root / "graph_events.jsonl").exists())
                row = next(p for p in payload(all_current)["params"] if p["key"] == "ctrl.gain.fixture")
                self.assertEqual(row["value"], {"new": [0, None]})
                self.assertEqual(row["source"], "new-source")
                self.assertEqual(row["lifecycle"], "current")
                if mirror in ("{broken", "[]", json.dumps({"custom": [1]})):
                    self.assertEqual(current["extra"], mirror)
                if mirror and mirror.startswith('{"value"'):
                    refreshed = json.loads(current["extra"])
                    self.assertEqual(refreshed["custom"], {"keep": True})
                    self.assertEqual(refreshed["extra"], json.loads(mirror)["extra"])


class RealPayloadTests(unittest.TestCase):
    def test_real_registry_core_and_archive_are_unchanged(self):
        paths = [ROOT / "params_registry/params.json", ROOT / "params_registry/archive.json"]
        before = {p: p.read_bytes() for p in paths}
        records = json.loads(before[paths[0]])["params"]
        archive = json.loads(before[paths[1]])["archive"]
        records_copy, archive_copy = deepcopy(records), deepcopy(archive)
        with patch.object(lab_payload, "build_eaglex700", return_value={}):
            result = lab_payload.build_payload(records, archive)
        actual = {r["key"]: r for r in result["params"]}
        self.assertEqual(set(actual), set(records))
        for key, rec in records.items():
            # Freeze pre-I-232 I-130 compatibility behavior, independent of the
            # new semantics reader: exactly one mirror level for core fields.
            legacy = deepcopy(rec)
            try:
                extra = json.loads(rec.get("extra", ""))
            except (ValueError, TypeError):
                extra = None
            if isinstance(extra, dict):
                legacy.update({k: v for k, v in extra.items() if k != "extra"})
            for field in ("value", "unit", "source", "since", "note", "graph"):
                self.assertEqual(actual[key][field], legacy.get(field), (key, field))
            self.assertEqual(actual[key]["lifecycle"], legacy.get("status"), key)
            self.assertEqual(actual[key]["sample"], legacy.get("sample", "215g"), key)
            self.assertIn("semantics", actual[key])
        self.assertEqual(records, records_copy)
        self.assertEqual(archive, archive_copy)
        self.assertEqual(result["stats"]["total"], len(records))
        for path in paths:
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), hashlib.sha256(before[path]).digest())


if __name__ == "__main__":
    unittest.main()
