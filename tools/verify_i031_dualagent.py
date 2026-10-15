# -*- coding: utf-8 -*-
"""
verify_i031_dualagent.py — I-031 第四期多机项的双代理预备验证。

目的：冻结判据"两台本地机同时在线认领不同事项互不干扰"的字面验收
需要真第二台机器；本试验在单机上以两个独立令牌、两个独立认领身份、
线程同步栅栏并发请求，先行验证服务端认领串行化与互斥逻辑，作为
第四期多机验收的预备证据（不替代字面判据）。

三段试验：
  A 异项并发认领：两代理同时认领两个不同事项，双双成功，互不干扰；
  B 同项并发竞态：两代理同时认领同一事项，恰一成功一拒绝；
  C 清理：认领成功的事项全部释放回 open，账本状态复位。

用法：python tools/verify_i031_dualagent.py <事项甲> <事项乙>
仅使用 Python 标准库。
"""
import json
import os
import ssl
import http.client
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = os.path.join(ROOT, "lp_sync", "cloud_secret.json")
TOKEN_A = os.path.join(ROOT, "lp_sync", "agent_token")
TOKEN_B = os.path.join(ROOT, "lp_sync", "agent_token_b")
WORKER_A = "i031-agent-a"
WORKER_B = "i031-agent-b"


def call(token, method, path, body=None):
    sec = json.load(open(SECRET, encoding="utf-8"))
    ctx = ssl._create_unverified_context()
    conn = http.client.HTTPSConnection(sec["host"], timeout=20, context=ctx)
    headers = {"Authorization": "Bearer " + token,
               "Content-Type": "application/json"}
    conn.request(method, path,
                 body=json.dumps(body or {}) if method == "POST" else None,
                 headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    return resp.status, data


def claim(token, worker, item_id, result, key):
    try:
        status, data = call(token, "POST", "/api/dispatch/claim",
                            {"item_id": item_id, "worker": worker})
        result[key] = (status, data)
    except Exception as e:
        result[key] = (0, {"ok": False, "error": repr(e)})


def main():
    item_a, item_b = sys.argv[1], sys.argv[2]
    tok_a = open(TOKEN_A, encoding="utf-8").read().strip()
    tok_b = open(TOKEN_B, encoding="utf-8").read().strip()
    failures = []

    # ── 试验 A：异项并发认领 ──
    res = {}
    barrier = threading.Barrier(2)
    threads = []
    for tok, worker, item, key in ((tok_a, WORKER_A, item_a, "A1"),
                                   (tok_b, WORKER_B, item_b, "B1")):
        def run(t=tok, w=worker, i=item, k=key):
            barrier.wait()
            claim(t, w, i, res, k)
        th = threading.Thread(target=run)
        threads.append(th)
        th.start()
    for th in threads:
        th.join()
    for key, item in (("A1", item_a), ("B1", item_b)):
        status, data = res[key]
        ok = status == 200 and data.get("ok") is True
        if not ok:
            failures.append("试验A %s 认领 %s 失败：%s" % (key, item, data))
        print("[试验A] %s 认领 %s：http=%s %s"
              % (key, item, status, "成功" if ok else str(data)[:60]))

    # ── 试验 B：同项并发竞态（两代理同抢事项乙）──
    res2 = {}
    barrier2 = threading.Barrier(2)
    threads = []
    # 先释放乙，让竞态干净
    call(tok_a, "POST", "/api/dispatch/release", {"item_id": item_b, "worker": WORKER_B})
    for tok, worker, key in ((tok_a, WORKER_A, "A2"), (tok_b, WORKER_B, "B2")):
        def run(t=tok, w=worker, k=key):
            barrier2.wait()
            claim(t, w, item_b, res2, k)
        th = threading.Thread(target=run)
        threads.append(th)
        th.start()
    for th in threads:
        th.join()
    wins = [k for k, (s, d) in res2.items()
            if s == 200 and d.get("ok") is True]
    if len(wins) != 1:
        failures.append("试验B 同项竞态胜者数=%d（应为恰一）" % len(wins))
    print("[试验B] 同项竞态 %s：胜者=%s（恰一=%s）"
          % (item_b, wins, len(wins) == 1))
    for key, (status, data) in sorted(res2.items()):
        print("        %s http=%s %s" % (key, status,
              "认领成功" if data.get("ok") else str(data.get("error", ""))[:40]))

    # ── 试验 C：清理释放 ──
    released = []
    for tok, worker, item in ((tok_a, WORKER_A, item_a),
                              (tok_b, WORKER_B, item_b)):
        status, data = call(tok, "POST", "/api/dispatch/release",
                            {"item_id": item, "worker": worker})
        released.append(data.get("ok") is True or "未认领" in str(data))
    print("[试验C] 释放复位：", "双双复位" if all(released) else released)

    if failures:
        for f in failures:
            print("[拒]", f)
        return 1
    print("[过] 双代理预备验证：异项并发认领互不干扰，同项竞态恰一胜出，"
          "账本状态复位。字面两机验收仍待真第二台机器。")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("用法: verify_i031_dualagent.py <事项甲> <事项乙>")
    sys.exit(main())
