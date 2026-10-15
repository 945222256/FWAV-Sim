# -*- coding: utf-8 -*-
"""goals.py — 目标库：与任务清单分离的最高层。

权限模型：
  任何人可以 propose（提议，状态 draft）。
  只有携带机主授权记录的 authorize 才能把目标置为 active。
  授权记录两档强度：
    会话档：记录授权出处（哪次会话、什么时间），弱强度，可审计可回滚；
    签名档：机主私钥在仓库外签名，公钥验证，强强度，配置方法见本模块末尾。
  授权他人：机主签发授权证书（grant），含范围与有效期，可吊销（revoke）。
仅使用 Python 标准库。
"""
import datetime
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
GOALS_PATH = os.path.join(_DIR, "goals.json")
GRANTS_PATH = os.path.join(_DIR, "grants.json")


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def propose(text, proposed_by, note=""):
    """任何人可提议。提议只是草案，不生效。"""
    obj = _load(GOALS_PATH, {"schema_version": 1, "goals": []})
    nums = [int(g["id"].split("-")[1]) for g in obj["goals"]] or [0]
    gid = "G-%03d" % (max(nums) + 1)
    obj["goals"].append({
        "id": gid, "text": text, "status": "draft",
        "proposed_by": proposed_by,
        "proposed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "note": note, "history": [],
    })
    _save(GOALS_PATH, obj)
    return gid


def authorize(goal_id, auth_ref, authorized_by="机主", signature=None):
    """置为生效。必须提供授权出处；签名档为可选升级。"""
    obj = _load(GOALS_PATH, {"goals": []})
    for g in obj["goals"]:
        if g["id"] == goal_id:
            g["history"].append({"from": g["status"], "to": "active",
                                 "at": datetime.datetime.now().isoformat(timespec="seconds"),
                                 "auth_ref": auth_ref})
            g["status"] = "active"
            g["authorized_by"] = authorized_by
            g["authorized_at"] = datetime.date.today().isoformat()
            g["auth_ref"] = auth_ref
            g["signature"] = signature
            _save(GOALS_PATH, obj)
            return
    raise KeyError("目标不存在：%s" % goal_id)


def amend(goal_id, new_text, auth_ref):
    """修改生效目标。旧文本入历史，永不删除。"""
    obj = _load(GOALS_PATH, {"goals": []})
    for g in obj["goals"]:
        if g["id"] == goal_id:
            if g["status"] != "active":
                raise ValueError("非生效目标请走 propose 与 authorize 流程")
            if not auth_ref:
                raise ValueError("修改生效目标必须携带授权出处，无出处拒绝")
            g["history"].append({"text": g["text"], "retired_at":
                                 datetime.datetime.now().isoformat(timespec="seconds"),
                                 "auth_ref_of_change": auth_ref})
            g["text"] = new_text
            g["auth_ref"] = auth_ref
            _save(GOALS_PATH, obj)
            return
    raise KeyError("目标不存在：%s" % goal_id)


def grant(scope, grantee, expires, auth_ref):
    """机主授权他人修改特定范围目标。范围、对象、有效期、出处四必填。"""
    grants = _load(GRANTS_PATH, {"schema_version": 1, "grants": []})
    grants["grants"].append({
        "scope": scope, "grantee": grantee, "expires": expires,
        "auth_ref": auth_ref, "status": "active",
        "granted_at": datetime.datetime.now().isoformat(timespec="seconds"),
    })
    _save(GRANTS_PATH, grants)


def revoke(scope, grantee, auth_ref):
    """吊销授权，留痕。"""
    grants = _load(GRANTS_PATH, {"grants": []})
    for g in grants["grants"]:
        if g["scope"] == scope and g["grantee"] == grantee and g["status"] == "active":
            g["status"] = "revoked"
            g["revoked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            g["revoke_ref"] = auth_ref
    _save(GRANTS_PATH, grants)


def audit():
    """审计：生效目标缺授权出处即报警；授权证书过期即列出。"""
    obj = _load(GOALS_PATH, {"goals": []})
    alerts = [g["id"] for g in obj["goals"]
              if g["status"] == "active" and not g.get("auth_ref")]
    today = datetime.date.today().isoformat()
    grants = _load(GRANTS_PATH, {"grants": []})
    expired = [g for g in grants["grants"]
               if g["status"] == "active" and g["expires"] < today]
    return {"无出处目标": alerts, "过期证书": expired}


if __name__ == "__main__":
    obj = _load(GOALS_PATH, {"goals": []})
    for g in obj["goals"]:
        print("[%s] %s（%s）" % (g["id"], g["status"], g["text"][:40]))
    print("审计：%s" % audit())

# 签名档配置方法（需要时执行）：
# 1. 机主在仓库外生成密钥：ssh-keygen -t ed25519 -f C:/Users/Anni/.flapper_goal_key
# 2. 公钥 .flapper_goal_key.pub 复制进 items_registry/allowed_signers
# 3. 变更目标后签名：ssh-keygen -Y sign -f 私钥 -n goals goals.json
# 4. 任何人验证：ssh-keygen -Y verify -f allowed_signers -I 机主 -n goals -s 签名文件 < goals.json
