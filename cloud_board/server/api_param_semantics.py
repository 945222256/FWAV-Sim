"""I-232: atomic, revision-checked metadata-only parameter migration."""
from copy import deepcopy
import hashlib
import json

import auth
import db
import repo
import router
from httpd import ApiError
from params_registry.semantics import empty_semantics, validate_semantics


def digest(value):
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def merge_fields(existing, patch, template):
    if not isinstance(existing, dict) or not isinstance(patch, dict):
        raise ApiError("语义补丁必须为对象")
    result = deepcopy(existing)
    for key, value in patch.items():
        if key not in template:
            raise ApiError("语义补丁字段未获准：" + key)
        if isinstance(template[key], dict) and isinstance(value, dict):
            result[key] = merge_fields(result.get(key, {}), value, template[key])
        else:
            result[key] = deepcopy(value)
    return result


@router.route("POST", "/api/agent/param_semantics")
def patch_semantics(ctx):
    actor, _ = auth.caller(ctx)
    body = ctx.body
    patches = body.get("patches")
    expected = body.get("expected_digest")
    migration_id = body.get("migration_id")
    if not isinstance(patches, dict) or not patches or not isinstance(expected, str) or len(expected) != 64:
        raise ApiError("须提供非空语义补丁与完整写前摘要")
    if not isinstance(migration_id, str) or not migration_id or len(migration_id) > 100:
        raise ApiError("须提供稳定迁移编号")
    request_digest = digest(patches)
    receipt_key = "param_semantics_migration:" + migration_id
    with db.transaction() as conn:
        previous = repo.get_config(receipt_key)
        if previous is not None:
            if previous.get("patch_digest") != request_digest:
                raise ApiError("迁移编号已被不同补丁使用")
            return {"ok": True, "result": dict(previous, replay=True)}
        current = repo.all_params()
        if digest(current) != expected:
            raise ApiError("参数版本已变化，拒绝覆盖；重新读取并核对差异")
        candidate = deepcopy(current)
        template = empty_semantics()
        for key, patch in patches.items():
            if key not in candidate or not isinstance(candidate[key], dict):
                raise ApiError("参数键不在当前登记库：" + str(key))
            old = candidate[key].get("semantics", {})
            if not isinstance(old, dict) or type(old.get("schema_version", 1)) is not int or old.get("schema_version", 1) != 1:
                raise ApiError("已有语义版本不支持写入：" + key)
            candidate[key]["semantics"] = merge_fields(old, patch, template)
        for key in patches:
            try:
                validate_semantics(candidate[key]["semantics"], param_id=key, records=candidate)
            except (TypeError, ValueError, KeyError) as error:
                raise ApiError("参数语义校验失败 %s：%s" % (key, error))
        # Only semantics changed; archive tables and every other field remain intact.
        after_digest = digest(candidate)
        repo.replace_params(conn, candidate)
        repo.set_config(conn, "params_digest", after_digest)
        receipt = {"migration_id": migration_id, "patch_digest": request_digest,
                   "before_digest": expected, "after_digest": after_digest,
                   "changed_keys": len(patches)}
        repo.set_config(conn, receipt_key, receipt)
        repo.append_event(conn, "param_semantics", actor=actor,
                          detail="语义迁移 %s：%d 键，核心值及归档保留" % (migration_id, len(patches)))
    return {"ok": True, "result": receipt}
