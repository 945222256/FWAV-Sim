# -*- coding: utf-8 -*-
"""evidence_audit.py — 证据链机器校验（I-115）。

关闭事项时对证据清单做三要素机器复核：
  提交要素  形如"提交 xxxx..."或裸哈希的证据：哈希在版本库中真实
            存在，且其触及路径不超出具事项路线的领地；
  文件要素  形如路径的证据：文件在仓库中存在，指纹（SHA-256、
            字节数）写入指纹清单；
  运行要素  形如运行登记路径（logs/ 下 json）的证据：登记含锁字段
            （schema_version、item、generated_at、run_completed 或
            同义完成标记）与复验结论字段。

执行策略三档（记录/警告/拒绝），档位存档位配置，切换只许指挥部经
账本事件操作。无痛三原则：影子档先行（结果只入影子档不阻断关闭）、
只增不改（本模块不修改任何既有字段）、旧事项不回溯（只对启用后的
新关闭做复核）。

外部验收器接口（契约冻结文本见 docs/I-115_外部验收器接口契约_20261006.md）：
  audit_evidence(item, lane, evidence_list) -> 审计报告字典；
  L5 可在本模块注册外部校验器（register_validator），关闭通道统一
  调用 run_external_validators 汇总其结论。
仅使用 Python 标准库。
"""
import hashlib
import json
import os
import re
import subprocess
import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_DIR)
POLICY_PATH = os.path.join(_DIR, "audit_policy.json")
SHADOW_PATH = os.path.join(_DIR, "audit_shadow.json")
FINGERPRINTS_PATH = os.path.join(_DIR, "evidence_fingerprints.json")

LEVELS = ("记录", "警告", "拒绝")

_COMMIT_RE = re.compile(r"提交\s*([0-9a-f]{7,40})\b")
_RUN_MARKERS = ("run_completed", "verdict", "handoff_note", "criteria_file")


def load_policy():
    if os.path.exists(POLICY_PATH):
        try:
            return json.load(open(POLICY_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {"default_level": "记录", "per_lane": {}, "updated_by": None,
            "updated_at": None}


def level_for(lane, policy=None):
    policy = policy or load_policy()
    if lane in policy.get("per_lane", {}):
        return policy["per_lane"][lane]
    return policy.get("default_level", "记录")


def _git_commit_exists(h):
    try:
        subprocess.check_output(["git", "cat-file", "-e", h], cwd=ROOT,
                                stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _git_commit_paths(h):
    try:
        out = subprocess.check_output(
            ["git", "-c", "core.quotepath=off", "show", "--name-only",
             "--format=", h], cwd=ROOT).decode("utf-8", "replace")
        return [l.strip().replace("\\", "/") for l in out.splitlines() if l.strip()]
    except Exception:
        return None


def _lane_paths(lanes, lane):
    l = lanes.get(lane) or {}
    return l.get("exclusive_paths", [])


def _file_fingerprint(path):
    fp = os.path.join(ROOT, path)
    if not os.path.isfile(fp):
        return None
    h = hashlib.sha256(open(fp, "rb").read()).hexdigest()
    return {"sha256": h, "bytes": os.path.getsize(fp)}


def _looks_like_path(s):
    return bool(re.match(r"^[\w\u4e00-\u9fff./\\-]+\.(json|md|txt|csv|py|log|js|html)$",
                         s.strip())) and ("/" in s or "\\" in s)


_PATH_SUFFIX = (".json", ".md", ".txt", ".csv", ".py", ".log", ".js", ".html")


def _extract_path(ref):
    """I-115 补强：主流证据形态是"路径 加 说明文字"，从中提取路径记号。
    整串为纯路径时直接可用；混排形态按空白与标点切分取首枚带斜杠且
    后缀合法的记号。返回路径字符串或 None。"""
    s = ref.strip()
    if _looks_like_path(s):
        return s
    for tok in re.split(r"[\s，,;；：（）()]+", s):
        tok = tok.strip().strip("\"'")
        if "/" in tok and tok.lower().endswith(_PATH_SUFFIX):
            return tok
    return None


def _audit_file(ref, path, item_lane=None, lanes=None):
    """文件要素复核（I-115）：存在性、指纹入清单、路径同路。
    logs 下 json 若为运行登记（含完成或结论标记）则按运行要素复核
    锁字段与复验结论（I-115 三要素之运行）。"""
    fp = _file_fingerprint(path)
    out = {"ref": ref[:80], "kind": "文件", "path": path,
           "exists": fp is not None, "fingerprint": fp}
    if fp and path.startswith("logs/") and path.endswith(".json"):
        try:
            data = json.load(open(os.path.join(ROOT, path), encoding="utf-8"))
            if isinstance(data, dict) and any(k in data for k in _RUN_MARKERS):
                out["kind"] = "运行"
                out["lock_fields"] = "schema_version" in data and "generated_at" in data
                out["verdict_field"] = ("verdict" in data or any(
                    "verdict" in s or "结论" in s for s in
                    (data.get("subjects") or []) if isinstance(s, dict)))
                return out["kind"], out
        except Exception:
            pass
    if fp:
        lpaths = _lane_paths(lanes or {}, item_lane)
        out["path_in_lane"] = (not lpaths) or \
            any(path.startswith(x) for x in lpaths) or \
            any(path.startswith(s) for s in ("tools/", "logs/"))
        entry = {"checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
                 "path": path, **fp}
        store = []
        if os.path.exists(FINGERPRINTS_PATH):
            try:
                store = json.load(open(FINGERPRINTS_PATH, encoding="utf-8"))
            except Exception:
                store = []
        store.append(entry)
        json.dump(store, open(FINGERPRINTS_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        out["fingerprint_recorded"] = True
    else:
        out["path_in_lane"] = None
    return "文件", out


def audit_one(ref, item_lane, lanes):
    """复核单条证据引用，返回 (要素类型, 结果字典)。"""
    m = _COMMIT_RE.search(ref)
    if m:
        h = m.group(1)
        exists = _git_commit_exists(h)
        out = {"ref": ref[:80], "kind": "提交", "hash": h, "exists": exists}
        if exists:
            paths = _git_commit_paths(h)
            out["touched_paths"] = len(paths or [])
            lpaths = _lane_paths(lanes, item_lane)
            if paths is not None and lpaths and item_lane != "HQ":
                out["paths_in_lane"] = all(
                    any(p.startswith(x) for x in lpaths)
                    or any(p.startswith(s) for s in ("tools/", "logs/"))
                    or _TOOL_DATA_RE.match(p)
                    for p in paths)
            else:
                out["paths_in_lane"] = None   # 无领地/共享区可比，不判
        return "提交", out
    if _looks_like_path(ref):
        path = ref.strip().split()[0].split("：")[0]
        return _audit_file(ref, path, item_lane, lanes)
    extracted = _extract_path(ref)
    if extracted and _looks_like_path(extracted):
        return _audit_file(ref, extracted, item_lane, lanes)
    # 运行登记：logs 下 json 且含完成或结论标记
    for seg in re.split(r"[，,;；\s]+", ref):
        seg = seg.strip()
        if seg.endswith(".json") and seg.startswith("logs/"):
            fp = os.path.join(ROOT, seg)
            if os.path.isfile(fp):
                try:
                    data = json.load(open(fp, encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(data, dict) and any(k in data for k in _RUN_MARKERS):
                    has_lock = "schema_version" in data and "generated_at" in data
                    has_verdict = ("verdict" in data or any(
                        "verdict" in s or "结论" in s for s in
                        (data.get("subjects") or []) if isinstance(s, dict)))
                    return "运行", {"ref": ref[:80], "kind": "运行",
                                   "path": seg, "exists": True,
                                   "lock_fields": has_lock,
                                   "verdict_field": has_verdict}
    return "未识别", {"ref": ref[:80], "kind": "未识别", "note": "证据未含可机器复核要素"}


_TOOL_DATA_RE = re.compile(
    r"^(items_registry/(?!lanes\.json)[^/]+\.json"
    r"|params_registry/(params|archive)\.json)$")


def audit_evidence(item_id, item_lane, evidence_list, lanes):
    """外部验收器接口（I-115 契约冻结）：对证据清单整体复核。

    返回审计报告字典：
      item, lane, level, refs: [要素类型与结果], counts, audited_at。
    本函数只读不写（除指纹清单追加），阻断行为由调用方按档位执行。
    """
    policy = load_policy()
    level = level_for(item_lane, policy)
    refs = []
    counts = {}
    for ref in evidence_list or []:
        kind, out = audit_one(str(ref), item_lane, lanes)
        out["element"] = kind
        refs.append(out)
        counts[kind] = counts.get(kind, 0) + 1
    problems = [r for r in refs
                if (r.get("element") in ("提交", "文件", "运行")
                    and (r.get("exists") is False
                         or r.get("lock_fields") is False
                         or r.get("verdict_field") is False
                         or r.get("path_in_lane") is False))]
    report = {
        "item": item_id, "lane": item_lane, "level": level,
        "refs": refs, "counts": counts, "problems": len(problems),
        "audited_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "external": run_external_validators(item_id, item_lane, evidence_list),
    }
    return report


_external_validators = []


def register_validator(fn):
    """I-116 挂接点：外部验收器注册。fn(item_id, lane, evidence) -> dict。"""
    _external_validators.append(fn)


def run_external_validators(item_id, item_lane, evidence_list):
    out = []
    for fn in _external_validators:
        try:
            out.append({"validator": getattr(fn, "__name__", str(fn)),
                        "result": fn(item_id, item_lane, evidence_list)})
        except Exception as e:
            out.append({"validator": getattr(fn, "__name__", str(fn)),
                        "error": repr(e)})
    return out


def shadow_record(report):
    """影子档：只追加、永不阻断（无痛原则一、二）。"""
    store = []
    if os.path.exists(SHADOW_PATH):
        try:
            store = json.load(open(SHADOW_PATH, encoding="utf-8"))
        except Exception:
            store = []
    store.append(report)
    json.dump(store, open(SHADOW_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
