# -*- coding: utf-8 -*-
"""validators.py — I-116 外部验收器（按 I-115 冻结契约挂接）。

契约要点（docs/I-115_外部验收器接口契约_20261007.md）：
  签名统一为 fn(item_id, lane, evidence_list)，返回字典；
  返回内容原样进入审计报告 external 数组（键 validator 与 result）；
  外部验收器无阻断权，阻断行为由档位决定，档位在指挥部手里；
  L5 不修改关闭通道代码，只经 register_validator 注册点挂接。

本验收器职责：对挂了判据脚本的事项，在审计链里独立复跑一次脚本
（与执行器自身的 verify 相互独立，构成第二双眼睛）；未挂脚本的
事项如实返回不适用。
仅使用 Python 标准库。
"""
import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_DIR))
_REG = os.path.join(ROOT, "items_registry")

if _REG not in sys.path:
    sys.path.insert(0, _REG)


def _item_of(item_id):
    """取事项（云端优先，本地缓存兜底），失败返回空字典。"""
    try:
        import cloud_client
        r = cloud_client.request("GET", "/api/item-detail?id=" + item_id)
        return r["result"]["item"]
    except Exception:
        pass
    try:
        items = json.load(open(os.path.join(_REG, "items.json"), encoding="utf-8"))["items"]
        return next((i for i in items if i["id"] == item_id), {})
    except Exception:
        return {}


def check_script_validator(item_id, lane, evidence_list):
    """契约签名的外部验收器：独立复跑事项注册的判据脚本。"""
    item = _item_of(item_id)
    script = (item or {}).get("check_script") or ""
    if not script:
        return {"applies": False,
                "reason": "事项未挂判据脚本，外部验收器不适用（契约第3节）"}
    import executor
    result = executor.run_script(script)
    return {"applies": True, "lane": lane,
            "independent_rerun": True,
            "script": result["script"], "pass": result["pass"],
            "exit_code": result["exit_code"], "ran_at": result["ran_at"],
            "duration_s": result["duration_s"],
            "output_tail": (result.get("output_tail") or "")[-200:]}


def attach():
    """经契约注册点挂接（幂等：重复调用不重复注册）。"""
    import evidence_audit
    if not any(getattr(fn, "__name__", "") == "check_script_validator"
               for fn in evidence_audit._external_validators):
        evidence_audit.register_validator(check_script_validator)
    return len(evidence_audit._external_validators)
