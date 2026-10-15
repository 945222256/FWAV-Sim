"""Explicit delivery dependencies; legacy dependencies retain closed semantics."""


def unmet_dependencies(item, items):
    by_id = {v["id"]: v for v in items}
    delivery = {v["item_id"]: v for v in item.get("delivery_dependencies") or []}
    unmet = []
    ids = list(dict.fromkeys(list(item.get("depends_on") or []) + list(delivery)))
    for upstream_id in ids:
        upstream = by_id.get(upstream_id)
        impacted = any(v.get("review_required") for v in (upstream or {}).get("delivery_impact") or [])
        if upstream and upstream.get("status") == "closed" and not impacted:
            continue
        requirement = delivery.get(upstream_id)
        submitted = (upstream or {}).get("pending_review") or {}
        if requirement and requirement.get("requires") == "delivered" \
                and (upstream or {}).get("status") == "pending_review" \
                and not impacted \
                and requirement.get("authorization_source") and requirement.get("approved_by") \
                and requirement.get("delivery_version") == submitted.get("delivery_version") \
                and requirement.get("evidence_ptr") == submitted.get("evidence_ptr") \
                and submitted.get("evidence_ptr"):
            continue
        unmet.append(upstream_id)
    return unmet


def validate_delivery_dependencies(entries):
    if not isinstance(entries, list):
        raise ValueError("delivery_dependencies must be a list")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("item_id") or entry["item_id"] in seen:
            raise ValueError("each dependency must identify a unique item")
        seen.add(entry["item_id"])
        if entry.get("requires") not in ("accepted", "delivered"):
            raise ValueError("requires must be accepted or delivered")
        if entry["requires"] == "delivered" and not all(entry.get(k) for k in
                    ("delivery_version", "evidence_ptr", "approved_by", "authorization_source")):
            raise ValueError("delivery release needs an exact version, evidence and authorization source")

