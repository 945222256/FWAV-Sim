# -*- coding: utf-8 -*-
"""executor.py — I-116 判据脚本执行器与注册器（本体）。

规则（冻结口径）：
  脚本以独立进程运行；退出码零=通过；非零=不通过；超时与异常=不通过。
  超时默认三百秒（可用 --timeout 调整）。执行结果含 ran_at、exit_code、
  output_tail、script、pass，附在关闭请求的 check_result 字段提交。

用法：
  python tools/checks/executor.py run <脚本路径> [--timeout N]
  python tools/checks/executor.py register <事项编号> <tools/checks/xx.py>
  python tools/checks/executor.py verify <事项编号>     （按注册脚本运行并落证据）
  python tools/checks/executor.py close <事项编号> --closer X --conclusion 判据通过
        --evidence 证据1 --evidence 2 [--criterion-check 判据对照.json]
        [--owner-note "..."]                                  （走判据闸关闭）
  python tools/checks/executor.py list

仅使用 Python 标准库。
"""
import datetime
import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHECKS_DIR = os.path.join(ROOT, "tools", "checks")
LOGS = os.path.join(ROOT, "logs", "checks")
SECRET = os.path.join(ROOT, "lp_sync", "cloud_secret.json")
DEFAULT_TIMEOUT = 300
_SERVER = None


def _server():
    """按需拉起协议无关的云端 HTTP 通道（复用 cloud_client 的令牌路线）。"""
    global _SERVER
    if _SERVER is None:
        sys.path.insert(0, os.path.join(ROOT, "items_registry"))
        import cloud_client
        _SERVER = cloud_client
    return _SERVER


def run_script(script_path, timeout=DEFAULT_TIMEOUT):
    """执行单个判据脚本，返回结果字典（规则：退出码零=通过）。"""
    path = os.path.normpath(os.path.join(ROOT, script_path)) \
        if not os.path.isabs(script_path) else script_path
    if not os.path.isfile(path):
        return {"pass": False, "exit_code": -1, "ran_at": _now(),
                "script": script_path, "output_tail": "脚本文件不存在：%s" % path,
                "duration_s": 0}
    started = datetime.datetime.now()
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           cwd=ROOT, timeout=timeout)
        exit_code, tail = r.returncode, (r.stdout + r.stderr)[-800:]
    except subprocess.TimeoutExpired:
        return {"pass": False, "exit_code": -2, "ran_at": _now(),
                "script": script_path, "output_tail": "超时（>%ds）" % timeout,
                "duration_s": (datetime.datetime.now() - started).seconds}
    except Exception as exc:
        return {"pass": False, "exit_code": -3, "ran_at": _now(),
                "script": script_path, "output_tail": "执行异常：%r" % exc,
                "duration_s": (datetime.datetime.now() - started).seconds}
    return {"pass": exit_code == 0, "exit_code": exit_code, "ran_at": _now(),
            "script": script_path.replace("\\", "/"),
            "output_tail": tail.strip()[-500:],
            "duration_s": (datetime.datetime.now() - started).seconds}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _snapshot_item(item_id):
    cc = _server()
    snap = cc.request("GET", "/api/agent/snapshot")
    for it in snap["items"]:
        if it["id"] == item_id:
            return it
    raise SystemExit("事项不存在：%s" % item_id)


def verify(item_id, timeout):
    """按事项注册的判据脚本运行，证据落 logs/checks/。"""
    item = _snapshot_item(item_id)
    script = item.get("check_script")
    if not script:
        raise SystemExit("事项 %s 未注册判据脚本" % item_id)
    result = run_script(script, timeout)
    result["item_id"] = item_id
    os.makedirs(LOGS, exist_ok=True)
    out = os.path.join(LOGS, "%s_%s.json" % (item_id, result["ran_at"].replace(":", "")))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("判据脚本 %s 执行：%s（退出码 %s，证据 %s）"
          % (script, "过" if result["pass"] else "不过", result["exit_code"], out))
    return result


def register(item_id, script):
    cc = _server()
    r = cc.request("POST", "/api/ledger/set_check_script",
                   {"item_id": item_id, "script": script,
                    "author": "L5队-接口与应用线"})
    print("注册成功：%s → %s" % (item_id, r["check_script"]))
    return r


def close(item_id, args):
    """verify 后走判据闸关闭（仅适用于挂了脚本的事项）。

    I-115 契约挂接（联调收尾）：关闭前经注册点挂上外部验收器，并按
    关闭通道的同入口同参数跑一遍证据审计与影子档（external 数组即含
    验收器独立复跑结论）；通道代码一字未动，阻断仍由云侧判据闸承担
    （外部验收器按契约无阻断权）。
    """
    result = verify(item_id, args.get("timeout", DEFAULT_TIMEOUT))
    if not result["pass"]:
        raise SystemExit("判据脚本未通过，关闭被闸拒绝（退出码 %s）" % result["exit_code"])
    criterion_check = None
    if args.get("criterion_check"):
        with open(args["criterion_check"], encoding="utf-8") as f:
            criterion_check = json.load(f)
    evidence = args.get("evidence") or []
    owner_note = args.get("owner_note") or ""
    closer = args.get("closer") or "L5队-接口与应用线"
    # 通道同款约定：机主说明进证据首条。
    if owner_note:
        evidence = ["机主说明：" + owner_note.strip()] + list(evidence)

    # 契约挂接：注册外部验收器 + 通道同入口同参数的审计与影子档。
    import validators
    validators.attach()
    try:
        import evidence_audit
        cc = _server()
        lanes = cc.request("GET", "/api/agent/snapshot").get("lanes") or {}
        try:
            lane = cc.request("GET", "/api/item-detail?id=" + item_id)["result"]["item"].get("lane")
        except Exception:
            lane = None
        rep = evidence_audit.audit_evidence(item_id, lane or "未派发", evidence, lanes)
        rep["closure_context"] = {"closer": closer,
                                  "conclusion": args.get("conclusion") or "判据通过",
                                  "via": "executor-close(契约挂接)"}
        evidence_audit.shadow_record(rep)
        print("影子档已记：external 环节数=%d" % len(rep.get("external") or []))
    except Exception as exc:
        print("影子档记录异常（不阻断关闭，契约单点降级）：%r" % exc)

    payload = {
        "item_id": item_id,
        "evidence": evidence,
        "closer": closer,
        "conclusion": args.get("conclusion") or "判据通过",
        "criterion_check": criterion_check,
        "check_result": result,
    }
    cc = _server()
    out = cc.request("POST", "/api/ledger/close", payload)
    print("关闭成功：%s" % json.dumps(out, ensure_ascii=False))
    return out


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    cmd = args[0]
    if cmd == "run" and len(args) >= 2:
        timeout = int(args[args.index("--timeout") + 1]) if "--timeout" in args else DEFAULT_TIMEOUT
        result = run_script(args[1], timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["pass"] else 1)
    if cmd == "register" and len(args) >= 3:
        register(args[1], args[2])
        return
    if cmd == "verify" and len(args) >= 2:
        timeout = int(args[args.index("--timeout") + 1]) if "--timeout" in args else DEFAULT_TIMEOUT
        result = verify(args[1], timeout)
        sys.exit(0 if result["pass"] else 1)
    if cmd == "close" and len(args) >= 2:
        rest = {"timeout": DEFAULT_TIMEOUT}
        i = 1
        while i < len(args):
            if args[i] == "--timeout":
                rest["timeout"] = int(args[i + 1]); i += 2
            elif args[i].startswith("--"):
                key = args[i][2:].replace("-", "_")
                vals = []
                j = i + 1
                while j < len(args) and not args[j].startswith("--"):
                    vals.append(args[j]); j += 1
                rest[key] = vals if key == "evidence" else (vals[0] if vals else "")
                i = j
            else:
                i += 1
        close(args[1], rest)
        return
    if cmd == "list":
        cc = _server()
        snap = cc.request("GET", "/api/agent/snapshot")
        for it in snap["items"]:
            if it.get("check_script"):
                cr = it.get("check_result")
                print("%s %s → %s（%s）" % (it["id"], it["title"][:36],
                      it["check_script"],
                      ("上次执行：%s" % ("过" if cr and cr.get("pass") else "不过/未执行"))))
        return
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
