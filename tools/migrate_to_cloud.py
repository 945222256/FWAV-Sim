# -*- coding: utf-8 -*-
"""migrate_to_cloud.py — 本地 JSON 账本一次性迁移到云端（在本地工地机运行）。

动作三件：用机主账号登录云端、组装本地五份 JSON 为快照、
调用云端迁移接口全量导入。云端账本非空时云端会拒绝，防二次导入。
仅使用 Python 标准库。
"""
import http.client
import json
import os
import ssl
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = json.load(open(os.path.join(ROOT, "lp_sync", "cloud_secret.json"), encoding="utf-8"))

_CTX = ssl._create_unverified_context()      # 自签证书：只校验传输加密，不校验签发者。


def _request(method, path, body=None, cookie=None):
    conn = http.client.HTTPSConnection(SECRET["host"], timeout=20, context=_CTX)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    set_cookie = resp.getheader("Set-Cookie")
    conn.close()
    data = json.loads(raw) if raw else {}
    return resp.status, data, set_cookie


def build_snapshot():
    items = json.load(open(os.path.join(ROOT, "items_registry", "items.json"), encoding="utf-8"))
    inbox = json.load(open(os.path.join(ROOT, "items_registry", "inbox.json"), encoding="utf-8"))
    events = json.load(open(os.path.join(ROOT, "items_registry", "events.json"), encoding="utf-8"))
    params = json.load(open(os.path.join(ROOT, "params_registry", "params.json"), encoding="utf-8"))
    lanes = json.load(open(os.path.join(ROOT, "items_registry", "lanes.json"), encoding="utf-8"))
    return {
        "items": items["items"],
        "inbox": inbox["queue"],
        "reports": items.get("reports", []),
        "events": events["events"],
        "params": params["params"],
        "lanes": lanes["lanes"],
    }


def main():
    status, data, cookie = _request("POST", "/api/auth/login", {
        "username": SECRET["board_user"], "password": SECRET["board_password"]})
    if status != 200 or not data.get("ok"):
        raise SystemExit("登录失败：%s" % data.get("error"))
    session = cookie.split(";")[0]
    print("云端登录成功")

    snapshot = build_snapshot()
    status, data, _ = _request("POST", "/api/admin/import", {"snapshot": snapshot},
                               cookie=session)
    print("导入结果：", json.dumps(data, ensure_ascii=False))
    if not data.get("ok"):
        sys.exit(1)

    status, data, _ = _request("GET", "/api/state", cookie=session)
    s = data.get("summary", {})
    print("云端现状：事项 %s，在办 %s，已关闭 %s，待审 %s，事件 %d 条，参数 %d 个"
          % (s.get("total"), s.get("open"), s.get("closed"), s.get("inbox_pending"),
             len(data.get("events", [])), data.get("params", {}).get("count")))


if __name__ == "__main__":
    main()
