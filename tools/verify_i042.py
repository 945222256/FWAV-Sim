# -*- coding: utf-8 -*-
"""
verify_i042.py — I-042 公网访问控制审查执行体。

两项检查：
  write   对全部写路由做无凭证 POST 匿名实测，判定口径为不得出现
          ok 为真的成功响应（401、403、400 与 ok 假响应均算挡住）。
  read    对全部读路由做匿名 GET 实测，输出读范围事实供书面声明引用。

用法：python tools/verify_i042.py [write|read|all]
仅使用 Python 标准库；主机取自 lp_sync/cloud_secret.json。
"""
import json
import ssl
import http.client
import sys

ROOT = os_dir = None
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = os.path.join(ROOT, "lp_sync", "cloud_secret.json")

WRITE_ROUTES = [
    "/api/admin/import", "/api/admin/reimport",
    "/api/agent/register", "/api/agent/heartbeat",
    "/api/dispatch/claim", "/api/dispatch/release",
    "/api/dispatch/assign", "/api/dispatch/sweep",
    "/api/intake/submit", "/api/intake/decide",
    "/api/ledger/add", "/api/ledger/append_evidence", "/api/ledger/attach",
    "/api/ledger/close", "/api/ledger/block", "/api/ledger/unblock",
    "/api/ledger/owner_reply", "/api/ledger/add_report",
    "/api/ledger/set_criterion", "/api/ledger/set_depends",
    "/api/ledger/set_rationale", "/api/ledger/set_line",
    "/api/ledger/set_enables",
]
READ_ROUTES = [
    "/", "/login", "/app/",
    "/api/auth/whoami", "/api/state", "/api/map",
    "/api/item-detail?id=I-055", "/api/events", "/api/agent/snapshot",
]


def probe(method, path, body=None):
    sec = json.load(open(SECRET, encoding="utf-8"))
    ctx = ssl._create_unverified_context()
    conn = http.client.HTTPSConnection(sec["host"], timeout=15, context=ctx)
    payload = json.dumps(body or {}) if method == "POST" else None
    conn.request(method, path, body=payload,
                 headers={"Content-Type": "application/json"} if payload else {})
    resp = conn.getresponse()
    raw = resp.read()
    try:
        data = json.loads(raw)
    except Exception:
        data = None
    return resp.status, data, raw[:60]


def cmd_write():
    bad = 0
    for path in WRITE_ROUTES:
        status, data, raw = probe("POST", path)
        succeeded = isinstance(data, dict) and data.get("ok") is True
        ok = not succeeded
        # 变体二：包体自带 actor 字段（I-042 实锤过的鉴权短路形态，
        # 修复后一切写路由必须仍拒绝——身份只认凭证不认包体自述）
        if not succeeded:
            status2, data2, _ = probe(
                "POST", path, {"actor": "anon-probe-i042", "text": "x",
                               "item_id": "I-023", "index": 0,
                               "verified": True, "evidence": ""})
            succeeded2 = isinstance(data2, dict) and data2.get("ok") is True
            if succeeded2:
                print("[拒] POST %s 自带 actor 字段写穿（鉴权短路）" % path)
                bad += 1
                continue
        bad += 0 if ok else 1
        verdict = "挡住" if ok else "写成功（漏洞）"
        print("[%s] POST %-32s http=%s %s（含 actor 变体挡住）"
              % ("过" if ok else "拒", path, status, verdict))
    # 登录口单独验：错误凭证必须拒绝（它是鉴权门本体，不算写漏洞）
    status, data, _ = probe("POST", "/api/auth/login",
                            {"username": "no-such-user", "password": "x"})
    ok = not (isinstance(data, dict) and data.get("ok") is True)
    print("[%s] POST %-32s http=%s 错误凭证拒绝=%s"
          % ("过" if ok else "拒", "/api/auth/login", status, ok))
    bad += 0 if ok else 1
    if bad == 0:
        print("[过] 匿名写实测：%d 条写路由全部挡住（含自带 actor 变体），"
              "错误凭证登录被拒" % len(WRITE_ROUTES))
        return 0
    print("[拒] %d 条写路由匿名可达" % bad)
    return 1


def cmd_read():
    for path in READ_ROUTES:
        status, data, raw = probe("GET", path)
        if isinstance(data, dict) and "ok" in data:
            leak = data.get("ok") is True
            print("[读] GET %-32s http=%s %s"
                  % (path, status, "返回数据" if leak else "拒绝：" + str(data.get("error", ""))[:24]))
        else:
            print("[读] GET %-32s http=%s 非 JSON（页面或跳转，前 %d 字节：%r）"
                  % (path, status, len(raw), raw[:24]))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("write", "all"):
        rc = cmd_write()
        if mode == "write":
            sys.exit(rc)
    if mode in ("read", "all"):
        cmd_read()
