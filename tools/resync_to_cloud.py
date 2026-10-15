# -*- coding: utf-8 -*-
"""resync_to_cloud.py — 切换对账：以本地最新 JSON 替换云端事项域。

只在本地文件账本切换为缓存模式前显式运行一次。
事项全量替换，事件按时间戳与动作去重追加，不丢并行会话写入。
仅使用 Python 标准库。
"""
import http.client
import json
import os
import ssl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = json.load(open(os.path.join(ROOT, "lp_sync", "cloud_secret.json"), encoding="utf-8"))
_CTX = ssl._create_unverified_context()


def _request(method, path, body=None, cookie=None):
    conn = http.client.HTTPSConnection(SECRET["host"], timeout=30, context=_CTX)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    set_cookie = resp.getheader("Set-Cookie")
    conn.close()
    return resp.status, json.loads(raw) if raw else {}, set_cookie


def main():
    status, data, cookie = _request("POST", "/api/auth/login", {
        "username": SECRET["board_user"], "password": SECRET["board_password"]})
    if status != 200 or not data.get("ok"):
        raise SystemExit("登录失败：%s" % data.get("error"))
    session = cookie.split(";")[0]

    items = json.load(open(os.path.join(ROOT, "items_registry", "items.json"), encoding="utf-8"))
    events = json.load(open(os.path.join(ROOT, "items_registry", "events.json"), encoding="utf-8"))
    snapshot = {"items": items["items"],
                "reports": items.get("reports", []),
                "events": events["events"]}
    status, data, _ = _request("POST", "/api/admin/reimport", {"snapshot": snapshot},
                               cookie=session)
    print("对账结果：", json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
