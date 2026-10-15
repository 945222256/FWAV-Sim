"""I-210 opt-in observation sidecar; does not change FW-IID binary layout.

``valid`` describes JSON archival integrity, never scientific qualification.
The caller owns clocks, payload meanings and observation-error declarations.
Explicit null is preserved; no missing physical value is replaced with zero.
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import os
import threading
import uuid
import weakref
from collections.abc import Mapping

SCHEMA = "i210.actual_events/1"
_LOCK = threading.RLock()
_WRITERS = weakref.WeakKeyDictionary()


def _enabled():
    return os.environ.get("FLAPPER_I210_EVIDENCE") == "1"


def _result(status, recorded=False, valid=False, seq=None, issues=None, **extra):
    return {"status": status, "recorded": recorded, "valid": valid,
            "seq": seq, "issues": issues or [], **extra}


def _safe(value, path, issues, active=None):
    """JSON-safe archival conversion; no object repr or arbitrary method calls."""
    if active is None:
        active = set()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        issues.append({"path": path, "reason": "nonfinite", "value": str(value)})
        return None
    # Import numpy only on an enabled observation, never on default-off entry.
    import numpy as np
    if isinstance(value, np.generic):
        converted = value.item()
        if isinstance(converted, np.generic):
            issues.append({"path": path, "reason": "unsupported_numpy_precision_or_type", "dtype": str(value.dtype)})
            return None
        return _safe(converted, path, issues, active)
    if isinstance(value, np.ndarray):
        if id(value) in active:
            issues.append({"path": path, "reason": "cycle"})
            return None
        active.add(id(value))
        try:
            return _safe(value.tolist(), path, issues, active)
        finally:
            active.remove(id(value))
    if isinstance(value, (Mapping, list, tuple)):
        if id(value) in active:
            issues.append({"path": path, "reason": "cycle"})
            return None
        active.add(id(value))
        try:
            if isinstance(value, Mapping):
                result = {}
                for key, child in value.items():
                    if not isinstance(key, str):
                        issues.append({"path": path, "reason": "nonstring_mapping_key", "type": type(key).__name__})
                        continue
                    result[key] = _safe(child, path + "." + key, issues, active)
                return result
            return [_safe(child, path + "[%s]" % index, issues, active)
                    for index, child in enumerate(value)]
        finally:
            active.remove(id(value))
    issues.append({"path": path, "reason": "unsupported_type", "type": type(value).__name__})
    return None


def _ledger_type(ledger):
    if ledger is None:
        return False
    from modules.fw_iid_ledger import LedgerRecorder
    return isinstance(ledger, LedgerRecorder)


def _native_clock_snapshot():
    """Read native counters at the observation site; never advance simulation.

    These sequential queries are not an atomic solver snapshot. Keep all
    registered simulations, with no inferred PhysX owner or scene selection.
    Missing data is null with a query error, never a synthetic zero.
    """
    errors = []
    result = {
        "schema": "i210.native_clock/1", "status": "unknown",
        "sample_stage": "inside_observe_before_event_write",
        "atomic": False, "physical_time_qualified": False,
        "scene_scope": "not_queried_no_scene_disambiguation",
        "read_order": ["manager.time_s", "manager.step_count",
                       "physics_core.simulation_ids", "per_simulation.id_name_active_count_timestamp"],
        "manager": {"source": "isaacsim.core.simulation_manager.SimulationManager",
                    "time_s": None, "step_count": None},
        "physics_core": {"source": "omni.physics.core.get_physics_simulation_interface",
                         "enumeration_source": "omni.physics.core.get_physics_interface",
                         "selection": "none_all_registered_reported",
                         "simulation_count": None, "simulations": []},
        "query_errors": errors,
    }

    def error(path, reason, exc=None):
        entry = {"path": path, "reason": reason}
        if exc is not None:
            entry.update({"type": type(exc).__name__, "message": str(exc)})
        errors.append(entry)

    def read(path, fn, kind):
        try:
            value = fn()
            if kind == "count":
                valid = isinstance(value, numbers.Integral) and not isinstance(value, bool) and value >= 0
            elif kind == "time":
                valid = isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
            elif kind == "name":
                valid = isinstance(value, str) and bool(value.strip())
            else:
                valid = isinstance(value, bool)
            if not valid:
                error(path, "invalid_native_" + kind)
                return None
            return int(value) if kind == "count" else float(value) if kind == "time" else value
        except Exception as exc:
            error(path, "query_failed", exc)
            return None

    try:
        from isaacsim.core.simulation_manager import SimulationManager
        result["manager"]["time_s"] = read(
            "manager.time_s", lambda: SimulationManager.get_simulation_time(), "time")
        result["manager"]["step_count"] = read(
            "manager.step_count", lambda: SimulationManager.get_num_physics_steps(), "count")
    except Exception as exc:
        error("manager", "import_failed", exc)
    try:
        import omni.physics.core as physics_core
        interface = physics_core.get_physics_interface()
        simulation_interface = physics_core.get_physics_simulation_interface()
        ids = interface.get_simulation_ids()
        if not isinstance(ids, (list, tuple)):
            error("physics_core.simulation_ids", "invalid_simulation_ids_container")
        else:
            result["physics_core"]["simulation_count"] = len(ids)
            if len(ids) != 1:
                error("physics_core.simulation_ids", "no_registered_simulation" if not ids else "multiple_registered_simulations")
            seen = set()
            for index, simulation_id in enumerate(ids):
                path = "physics_core.simulations[%s]" % index
                item = {"enumeration_index": index, "id": None, "name": None,
                        "active": None, "step_count": None, "timestamp": None,
                        "timestamp_unit": "native_integer_step_token_not_seconds"}
                result["physics_core"]["simulations"].append(item)
                # SimulationId.id is the installed SDK's public numeric ID.
                item["id"] = read(path + ".id", lambda: simulation_id.id, "count")
                if item["id"] is not None:
                    if item["id"] in seen:
                        error(path + ".id", "duplicate_simulation_id")
                    seen.add(item["id"])
                item["name"] = read(path + ".name", lambda: interface.get_simulation_name(simulation_id), "name")
                item["active"] = read(path + ".active", lambda: interface.is_simulation_active(simulation_id), "bool")
                item["step_count"] = read(path + ".step_count", lambda: simulation_interface.get_simulation_step_count(simulation_id), "count")
                item["timestamp"] = read(path + ".timestamp", lambda: simulation_interface.get_simulation_timestamp(simulation_id), "count")
                if item["active"] is False:
                    error(path + ".active", "simulation_inactive")
    except Exception as exc:
        error("physics_core", "import_or_enumeration_failed", exc)
    result["status"] = "observed" if not errors else "unknown"
    return result


class _Writer:
    def __init__(self, ledger):
        self.lock = threading.RLock()
        self.path = os.path.abspath(ledger.path) + ".i210.jsonl"
        self.bin_path = os.path.abspath(ledger.path)
        self.binding = uuid.uuid4().hex  # Recorder binding only, never a run ID.
        self.joint_names = list(ledger.joint_names)
        self.phys_dt = ledger.phys_dt
        self.event_count = 0
        self.issue_count = 0
        self.io_error_count = 0
        self.invalid_event_count = 0
        self.warning_count = 0
        self.closed = False
        self.final_close_result = None
        self.fatal = None
        self.fh = None
        self.prefix = hashlib.sha256()
        self.header_issues = []
        meta = _safe(ledger.meta, "ledger.meta", self.header_issues)
        self.execution_identity = {"source": "process_environment",
                                   "captured_at": "first_observation",
                                   **{key: os.environ.get(key) for key in
                                      ("I186_ARCHIVE_DIR", "I186_EXPERIMENT_ID", "I186_RUN_ID")}}
        self.identity_issues = []
        meta_run = meta.get("run_id") if isinstance(meta, dict) else None
        env_run = self.execution_identity["I186_RUN_ID"]
        has_meta_run = isinstance(meta_run, str) and bool(meta_run.strip())
        has_env_run = isinstance(env_run, str) and bool(env_run.strip())
        self.identity_missing = []
        if not isinstance(meta, dict) or not isinstance(meta.get("tag"), str) or not meta["tag"].strip():
            self.identity_missing.append("tag")
        if not has_meta_run and not has_env_run:
            self.identity_missing.append("run_id")
        if meta_run is not None and not has_meta_run:
            self.identity_issues.append({"path": "ledger.meta.run_id", "reason": "invalid_identity_type_or_empty"})
        if has_meta_run and has_env_run and meta_run != env_run:
            self.identity_issues.append({"path": "run_id", "reason": "conflicting_run_id",
                                         "meta_run_id": meta_run, "environment_run_id": env_run})
        self.header = {"schema": SCHEMA, "record_type": "header", "seq": 0,
                       "binding_id": self.binding, "bin_path": self.bin_path,
                       "joint_names": _safe(self.joint_names, "ledger.joint_names", self.header_issues),
                       "phys_dt": _safe(self.phys_dt, "ledger.phys_dt", self.header_issues),
                       "meta": meta, "identity_missing": self.identity_missing,
                       "execution_identity": self.execution_identity,
                       "identity_issues": self.identity_issues,
                       "ledger_header_sha256": hashlib.sha256(ledger._header_json).hexdigest(),
                       "serialization_issues": self.header_issues,
                       "validity_scope": "serialization_integrity_only; not scientific or branch qualification"}
        try:
            self.fh = open(self.path, "xb", buffering=0)
            self._write(self.header)
        except Exception as exc:
            self._failure("sidecar_open_or_header", exc)
            if self.fh is not None:
                try:
                    self.fh.close()
                except Exception:
                    pass

    def _warn(self, text):
        if self.warning_count < 3:
            self.warning_count += 1
            try:
                print("[I210-EVIDENCE] " + text, flush=True)
            except Exception:
                pass

    def _failure(self, where, exc):
        self.io_error_count += 1
        self.fatal = {"path": where, "reason": "io_or_observer_error", "type": type(exc).__name__, "message": str(exc)}
        self._warn(where + ": " + type(exc).__name__ + ": " + str(exc))

    def _write(self, row):
        raw = (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        # Exclusive create prevents a second process writer; this process lock
        # and a single unbuffered write keep each full line indivisible here.
        count = self.fh.write(raw)
        if count != len(raw):
            raise OSError("partial JSONL write: %s/%s" % (count, len(raw)))
        self.prefix.update(raw)

    def observe(self, ledger, kind, clock, payload):
        with self.lock:
            if self.closed:
                return _result("closed", issues=[{"reason": "observation_after_sidecar_close"}])
            if self.fatal:
                return _result("error", issues=[self.fatal], identity_missing=self.identity_missing)
            try:
                issues = []
                if kind not in ("drive", "aero_apply"):
                    issues.append({"path": "kind", "reason": "unsupported_kind"})
                if not isinstance(clock, Mapping):
                    issues.append({"path": "clock", "reason": "explicit_clock_object_required"})
                if not isinstance(payload, Mapping):
                    issues.append({"path": "payload", "reason": "payload_object_required"})
                if os.path.abspath(ledger.path) != self.bin_path:
                    issues.append({"path": "ledger.path", "reason": "recorder_binding_changed"})
                if list(ledger.joint_names) != self.joint_names or ledger.phys_dt != self.phys_dt:
                    issues.append({"path": "ledger", "reason": "joint_names_or_phys_dt_binding_changed"})
                if ledger._closed or ledger._fh.closed:
                    issues.append({"path": "ledger", "reason": "observation_after_binary_close"})
                row = {"schema": SCHEMA, "record_type": "event", "seq": self.event_count + 1,
                       "binding_id": self.binding, "kind": _safe(kind, "kind", issues),
                       "clock": _safe(clock, "clock", issues),
                       "payload": _safe(payload, "payload", issues)}
                if kind in ("drive", "aero_apply"):
                    row["native_clock"] = _native_clock_snapshot()
                row["serialization_issues"] = issues
                row["valid"] = not issues and not self.header_issues
                self._write(row)
                self.event_count += 1
                self.issue_count += len(issues)
                self.invalid_event_count += not row["valid"]
                return _result("recorded" if row["valid"] else "recorded_invalid", True,
                               row["valid"], row["seq"], issues,
                               identity_missing=self.identity_missing, identity_issues=self.identity_issues)
            except Exception as exc:
                self._failure("observe", exc)
                return _result("error", issues=[self.fatal], identity_missing=self.identity_missing)

    def close(self, ledger):
        with self.lock:
            if self.closed:
                return dict(self.final_close_result) if self.final_close_result is not None else _result(
                    "closed_invalid", issues=[{"reason": "final_close_result_missing"}])
            self.closed = True
            if self.fatal:
                if self.fh is not None:
                    try:
                        self.fh.close()
                    except Exception:
                        pass
                self.final_close_result = _result("error", issues=[self.fatal], identity_missing=self.identity_missing,
                                                  identity_issues=self.identity_issues)
                return dict(self.final_close_result)
            issues = []
            digest = None
            size = None
            try:
                binary_closed = bool(ledger._closed and ledger._fh.closed)
                if os.path.abspath(ledger.path) != self.bin_path:
                    issues.append({"path": "ledger.path", "reason": "recorder_binding_changed"})
                if not binary_closed:
                    issues.append({"path": "ledger", "reason": "binary_not_closed"})
                else:
                    h = hashlib.sha256()
                    size = 0
                    with open(self.bin_path, "rb") as f:
                        while True:
                            block = f.read(1024 * 1024)
                            if not block:
                                break
                            h.update(block)
                            size += len(block)
                    digest = h.hexdigest()
                tail = {"schema": SCHEMA, "record_type": "tail", "seq": self.event_count + 1,
                        "binding_id": self.binding, "bin_path": self.bin_path,
                        "bin_sha256": digest, "bin_size_bytes": size,
                        "binary_closed": binary_closed, "event_count": self.event_count,
                        "invalid_event_count": self.invalid_event_count,
                        "serialization_issue_count": self.issue_count + len(self.header_issues),
                        "io_error_count": self.io_error_count, "identity_missing": self.identity_missing,
                        "execution_identity": self.execution_identity, "identity_issues": self.identity_issues,
                        "prefix_sha256": self.prefix.hexdigest(), "close_issues": issues,
                        "valid": not issues and not self.header_issues and not self.invalid_event_count}
                self._write(tail)
                self.fh.flush()
                os.fsync(self.fh.fileno())
                self.fh.close()
                self.final_close_result = _result("closed" if tail["valid"] else "closed_invalid", True,
                                                  tail["valid"], tail["seq"], issues,
                                                  identity_missing=self.identity_missing,
                                                  identity_issues=self.identity_issues,
                                                  event_count=self.event_count,
                                                  invalid_event_count=self.invalid_event_count,
                                                  serialization_issue_count=tail["serialization_issue_count"],
                                                  io_error_count=self.io_error_count)
                return dict(self.final_close_result)
            except Exception as exc:
                self._failure("close", exc)
                try:
                    self.fh.close()
                except Exception:
                    pass
                self.final_close_result = _result("error", issues=[self.fatal], identity_missing=self.identity_missing,
                                                  identity_issues=self.identity_issues)
                return dict(self.final_close_result)


def observe(ledger, kind, clock, payload):
    """Archive an actual caller observation; never raise into physical code."""
    if not _enabled():
        return _result("disabled")
    try:
        if not _ledger_type(ledger):
            return _result("unavailable", issues=[{"reason": "existing_LedgerRecorder_required"}])
        with _LOCK:
            writer = _WRITERS.get(ledger)
            if writer is None:
                writer = _Writer(ledger)
                _WRITERS[ledger] = writer
        return writer.observe(ledger, kind, clock, payload)
    except Exception as exc:
        return _result("error", issues=[{"reason": "observer_error", "type": type(exc).__name__, "message": str(exc)}])


def close(ledger):
    """Called only after FW binary close; does not open an unused sidecar."""
    if not _enabled():
        return _result("disabled")
    try:
        if not _ledger_type(ledger):
            return _result("unavailable", issues=[{"reason": "existing_LedgerRecorder_required"}])
        with _LOCK:
            writer = _WRITERS.get(ledger)
        if writer is None:
            return _result("unavailable", issues=[{"reason": "no_observations_no_sidecar"}])
        return writer.close(ledger)
    except Exception as exc:
        return _result("error", issues=[{"reason": "observer_close_error", "type": type(exc).__name__, "message": str(exc)}])
