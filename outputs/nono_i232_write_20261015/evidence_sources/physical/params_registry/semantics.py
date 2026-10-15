"""I-232 parameter semantics contract and pure, read-only compatibility helpers.

No registry, output artifact, network, or simulator is read by this module.
``merge_semantics`` returns a new object; it is not a persistence endpoint.
Code evidence uses path/symbol/line and sha256 (or commit); paper evidence uses
kind=paper, reference/version/claim and at least one page/table/equation locator.
"""
from copy import deepcopy
import json
import re

METADATA_SCHEMA = "param-semantics-v1"
SCHEMA_VERSION = 1
USAGE_TAGS = {"current", "historical", "reserved", "undetermined"}
CONSUMER_STATES = {"connected", "conditional", "offline_only", "not_found", "unknown"}
REVIEW_STATES = {"unreviewed", "verified", "declaration_only", "pending", "disputed", "stale"}
DEFAULT_PATHS = {"active", "conditional", "disabled", "not_applicable", "unknown"}
REASON_CODES = {"no_consumer_evidence", "definition_missing", "source_conflict",
                "evidence_version_changed", "sample_scope_unknown", "dynamic_read_not_excluded"}
FACETS = {
    "domain": {"vehicle_physics", "aerodynamics", "control", "rhythm", "identification_search", "simulation_runtime"},
    "component": {"whole_vehicle", "fuselage", "left_wing", "right_wing", "wing_connector", "tail", "propulsion_slipstream", "sensing_estimation"},
    "channel": {"longitudinal", "lateral", "yaw", "altitude", "speed", "rhythm", "not_applicable"},
    "role": {"physical_property", "geometry", "state_reference", "gain", "state_limit", "output_limit", "mixing", "model_coefficient", "search_range", "step_size", "termination", "runtime_setting"},
    "method": set(),
    "stage": {"position_loop", "attitude_loop", "rate_loop", "control_allocation", "wing_actuation", "aerodynamics", "identification", "search"},
}
_TEMPLATE = {
    "schema_version": 1, "display_name": None, "definition": None,
    "usage_tag": "undetermined", "consumer_state": "unknown",
    "review_state": {slot: "unreviewed" for slot in ("name", "definition", "usage")},
    "facets": {axis: [] for axis in FACETS},
    "applicability": {"sample_scope": "unknown", "sample_ids": [], "modes": [],
                      "conditions": [], "default_path": "unknown", "runtime_evidence_refs": []},
    "alias_of": None, "superseded_by": [], "source_refs": [], "consumer_refs": [],
    "sort_order": None,
    "review": {"reviewed_by": None, "reviewed_at": None, "reason_codes": [],
               "evidence_refs": [], "search_scope": None, "next_owner": None, "limitations": []},
}


def empty_semantics():
    """A fresh read fallback, deliberately unreviewed and without inferred use."""
    return deepcopy(_TEMPLATE)


def _defaults(value, template):
    out = deepcopy(value)
    for key, default in template.items():
        if key not in out:
            out[key] = deepcopy(default)
        elif isinstance(default, dict) and isinstance(out[key], dict):
            out[key] = _defaults(out[key], default)
    return out


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _require(condition, field):
    if not condition:
        raise ValueError("invalid parameter semantics: " + field)


def _enum(value, choices, field):
    _require(isinstance(value, str) and value in choices, field)


def _strings(value, field):
    _require(isinstance(value, list) and all(_text(x) for x in value), field)
    _require(len(value) == len(set(value)), field + ": duplicates")


def _refs(value, field, consumer=False):
    _require(isinstance(value, list), field)
    for ref in value:
        _require(isinstance(ref, dict), field)
        if ref.get("kind") == "paper":
            locators = [(key, ref[key]) for key in ("page", "table", "equation") if key in ref]
            _require(not consumer and all(_text(ref.get(k)) for k in ("reference", "version", "claim"))
                     and bool(locators) and all(
                         _text(item) or (key == "page" and type(item) is int and item > 0)
                         for key, item in locators), field + ": paper locator")
            continue
        line = ref.get("line", ref.get("line_start"))
        _require(_text(ref.get("path")) and _text(ref.get("symbol"))
                 and type(line) is int and line > 0, field + ": code locator")
        version = ref.get("sha256", ref.get("blob_sha256"))
        _require((isinstance(version, str) and re.fullmatch(r"[0-9a-fA-F]{64}", version))
                 or _text(ref.get("commit")), field + ": evidence version")
        if consumer:
            _enum(ref.get("kind", "code"), {"code", "static_code", "runtime", "offline", "experiment"},
                  field + ": declaration/bridge is not a consumer")


def validate_semantics(value, *, param_id=None, records=None, vocabulary=None):
    """Validate a proposed v1 write. Unknown existing extension fields survive.

    Referenced keys require ``records`` so relation checks cannot silently skip
    existence/cycle validation. The vocabulary override must be an approved
    per-axis ID set; no ID is inferred from a parameter's spelling.
    """
    _require(isinstance(value, dict), "object required")
    _require(type(value.get("schema_version")) is int and value["schema_version"] == SCHEMA_VERSION,
             "unsupported schema_version")
    v = _defaults(value, _TEMPLATE)
    for field in ("display_name", "definition", "alias_of"):
        _require(v[field] is None or _text(v[field]), field)
    _enum(v["usage_tag"], USAGE_TAGS, "usage_tag")
    _enum(v["consumer_state"], CONSUMER_STATES, "consumer_state")
    _require(isinstance(v["review_state"], dict), "review_state")
    for slot in ("name", "definition", "usage"):
        _enum(v["review_state"].get(slot), REVIEW_STATES, "review_state." + slot)
    for slot, field in (("name", "display_name"), ("definition", "definition")):
        if v["review_state"][slot] == "verified":
            _require(_text(v[field]), field + ": verified text missing")
    _require(isinstance(v["facets"], dict), "facets")
    vocab = FACETS if vocabulary is None else vocabulary
    for axis, ids in v["facets"].items():
        _require(axis in vocab, "facets." + axis)
        _strings(ids, "facets." + axis)
        _require(set(ids) <= set(vocab[axis]), "facets." + axis + ": unknown ID")
    app = v["applicability"]
    _require(isinstance(app, dict), "applicability")
    _enum(app["sample_scope"], {"unknown", "common", "listed"}, "sample_scope")
    _strings(app["sample_ids"], "sample_ids")
    _require(bool(app["sample_ids"]) == (app["sample_scope"] == "listed"), "sample_scope/sample_ids")
    _strings(app["modes"], "modes")
    _strings(app["conditions"], "conditions")
    _enum(app["default_path"], DEFAULT_PATHS, "default_path")
    _require(isinstance(app["runtime_evidence_refs"], list)
             and all(isinstance(r, dict) and _text(r.get("run_id")) for r in app["runtime_evidence_refs"]),
             "runtime_evidence_refs: run_id required")
    if v["consumer_state"] == "conditional" or app["default_path"] == "conditional":
        _require(bool(app["conditions"]), "conditional: conditions required")
    review = v["review"]
    _require(isinstance(review, dict), "review")
    for field in ("reviewed_by", "reviewed_at", "next_owner"):
        _require(review[field] is None or _text(review[field]), "review." + field)
    _strings(review["reason_codes"], "reason_codes")
    _require(set(review["reason_codes"]) <= REASON_CODES, "reason_codes: unknown ID")
    _strings(review["limitations"], "limitations")
    for field in ("source_refs", "consumer_refs"):
        _refs(v[field], field, consumer=field == "consumer_refs")
    _refs(review["evidence_refs"], "review.evidence_refs")
    if v["usage_tag"] == "current":
        _require(bool(v["consumer_refs"]) and v["consumer_state"] in {"connected", "conditional", "offline_only"},
                 "current: consumer evidence required")
    elif v["usage_tag"] == "reserved":
        _require(bool(v["source_refs"]) and _text(review["next_owner"]), "reserved: design source and responsible owner required")
    elif v["usage_tag"] == "historical":
        _require(bool(v["source_refs"]) or bool(v["alias_of"]) or bool(v["superseded_by"]),
                 "historical: source or replacement evidence required")
    elif v["usage_tag"] == "undetermined":
        _require(bool(review["reason_codes"]), "undetermined: reason required")
    if v["consumer_state"] == "not_found":
        scope = review["search_scope"]
        _require(isinstance(scope, dict) and _text(scope.get("version"))
                 and isinstance(scope.get("paths"), list) and bool(scope["paths"])
                 and all(_text(p) for p in scope["paths"]), "not_found: search paths and version required")
    _require(v["sort_order"] is None or (type(v["sort_order"]) is int and v["sort_order"] >= 0), "sort_order")
    _strings(v["superseded_by"], "superseded_by")
    targets = ([v["alias_of"]] if v["alias_of"] else []) + v["superseded_by"]
    if targets:
        _require(_text(param_id) and isinstance(records, dict), "relations: records and param_id required")
    if records is not None and param_id is not None:
        graph = {}
        for key, rec in records.items():
            # Use the same nearest-explicit compatibility rule as readback,
            # without recursively invoking validation while building a graph.
            sem = v if key == param_id else _nearest_semantics(rec)[0]
            sem = sem if isinstance(sem, dict) else {}
            successors = sem.get("superseded_by", [])
            edges = ([sem["alias_of"]] if sem.get("alias_of") else [])
            _require(isinstance(successors, list), "relations: invalid successors")
            graph[key] = edges + successors
        graph.setdefault(param_id, targets)
        seen, active = set(), set()
        def visit(key):
            _require(key in graph, "relations: missing target " + str(key))
            _require(key not in active, "relations: cycle")
            if key in seen:
                return
            active.add(key)
            for target in graph[key]:
                _require(isinstance(target, str), "relations: invalid target")
                visit(target)
            active.remove(key)
            seen.add(key)
        visit(param_id)


def merge_semantics(current, patch, **validation_context):
    """Pure, validated partial merge; no file writes or registry mutation."""
    _require(isinstance(current, dict) and isinstance(patch, dict), "merge objects required")
    _require(type(current.get("schema_version", 1)) is int
             and current.get("schema_version", 1) == SCHEMA_VERSION, "unsupported schema_version")
    def merge(base, changes, template):
        _require(isinstance(base, dict), "invalid existing object")
        out = deepcopy(base)
        for key, item in changes.items():
            _require(key in template, "patch field not allowed: " + key)
            if isinstance(template[key], dict) and isinstance(item, dict):
                out[key] = merge(out.get(key, {}), item, template[key])
            else:
                out[key] = deepcopy(item)
        return out
    result = merge(_defaults(current, _TEMPLATE), patch, _TEMPLATE)
    validate_semantics(result, **validation_context)
    return result


def _nearest_semantics(record, *, max_depth=8, max_extra_chars=1000000):
    """Extract only: no defaults, contract validation, or relation traversal.

    The nearest explicit semantics object wins, including null or future data.
    Older conflicts are diagnostic only. Legacy mirrors are never re-encoded.
    """
    selected, found, diagnostics = None, False, []
    node = record
    for depth in range(max_depth + 1):
        if not isinstance(node, dict):
            break
        if "semantics" in node:
            if not found:
                selected, found = deepcopy(node["semantics"]), True
            elif node["semantics"] != selected:
                diagnostics.append("legacy_semantics_conflict")
        extra = node.get("extra")
        if extra is None or extra == "":
            break
        if depth == max_depth:
            diagnostics.append("extra_depth_exceeded")
            break
        if not isinstance(extra, str) or len(extra) > max_extra_chars:
            diagnostics.append("invalid_or_oversized_extra")
            break
        try:
            node = json.loads(extra)
        except (ValueError, RecursionError):
            diagnostics.append("invalid_extra_json")
            break
        if not isinstance(node, dict):
            diagnostics.append("invalid_extra_object")
            break
    return selected, found, diagnostics


def read_semantics(record, *, max_depth=8, max_extra_chars=1000000):
    """Return (semantics, diagnostics), without promoting legacy core fields."""
    selected, found, diagnostics = _nearest_semantics(
        record, max_depth=max_depth, max_extra_chars=max_extra_chars)
    if not found:
        diagnostics.append("semantics_missing")
        selected = empty_semantics()
    elif not isinstance(selected, dict):
        diagnostics.append("invalid_semantics")
        diagnostics.append("invalid_semantics_object")
    elif type(selected.get("schema_version")) is not int or selected["schema_version"] != SCHEMA_VERSION:
        diagnostics.append("unsupported_schema_version")
    else:
        candidate = _defaults(selected, _TEMPLATE)
        try:
            validate_semantics(candidate)
        except (ValueError, TypeError, KeyError) as exc:
            # A single-record read cannot prove target existence or cycles.
            if str(exc).endswith("relations: records and param_id required"):
                diagnostics.append("relations_require_registry_validation")
                selected = candidate
            else:
                diagnostics.append("invalid_semantics")
                diagnostics.append(str(exc))
        else:
            selected = candidate
    return selected, list(dict.fromkeys(diagnostics))


def project_text(key, value, slot, diagnostics=None):
    """Only separately verified v1 text becomes a display assertion."""
    if diagnostics is None:
        _, diagnostics = read_semantics({"semantics": value})
    field = "display_name" if slot == "name" else "definition"
    label = "名称" if slot == "name" else "定义"
    reason = "尚未核对"
    if "invalid_semantics" in diagnostics:
        reason = "语义契约无效"
    elif not isinstance(value, dict):
        reason = "语义记录无效"
    elif type(value.get("schema_version")) is not int or value.get("schema_version") != SCHEMA_VERSION:
        reason = "语义版本暂不支持"
    else:
        state = value.get("review_state")
        state = state.get(slot) if isinstance(state, dict) else None
        if state == "verified" and _text(value.get(field)):
            return value[field]
        reason = {"stale": "证据版本已变化", "disputed": "存在争议", "declaration_only": "仅核对声明",
                  "pending": "证据待补", "verified": "已核文本缺失"}.get(state, reason)
    return "%s待核对（%s） · %s" % (label, reason, key)
