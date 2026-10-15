# -*- coding: utf-8 -*-
"""
verify_i064.py — I-064 本地可验判据执行体。

判据二：应用展示数据与 /api/state 一致——对指定事项逐字段比对
        /api/state 与 /api/item-detail 两接口（前端展示源即这两接口）。
判据三：壳代码静态检索不含任何写接口调用。
判据四：契约入库且被壳与文档引用——检查契约文件存在、五路由齐全、
        AndroidManifest 与前端 deeplink.ts 的路由与契约一致。

用法：
  python tools/verify_i064.py item I-055     判据二（需可访问云端）
  python tools/verify_i064.py readonly       判据三
  python tools/verify_i064.py contract       判据四
仅使用 Python 标准库。
"""
import json
import os
import re
import ssl
import http.client
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL = os.path.join(ROOT, "android_shell")
CONTRACT = os.path.join(SHELL, "deeplink_contract.json")
SECRET = os.path.join(ROOT, "lp_sync", "cloud_secret.json")
TOKEN = os.path.join(ROOT, "lp_sync", "agent_token")

WRITE_PATTERNS = [
    (r"\bPOST\b", "HTTP POST 字样"),
    (r"\bPUT\b", "HTTP PUT 字样"),
    (r"\bDELETE\b", "HTTP DELETE 字样"),
    (r"HttpURLConnection", "Java 原生 HTTP 客户端"),
    (r"OkHttp", "OkHttp 客户端"),
    (r"openConnection|openOutputStream", "连接写出调用"),
    (r"/api/dispatch|/api/intake|/api/agent/register|/api/admin",
     "云端看板写接口路径"),
]


def cmd_item(item_id):
    sec = json.load(open(SECRET, encoding="utf-8"))
    tok = open(TOKEN, encoding="utf-8").read().strip()
    ctx = ssl._create_unverified_context()
    conn = http.client.HTTPSConnection(sec["host"], timeout=20, context=ctx)
    H = {"Authorization": "Bearer " + tok}
    conn.request("GET", "/api/state", headers=H)
    state = json.load(conn.getresponse())
    conn.request("GET", "/api/item-detail?id=" + item_id, headers=H)
    detail = json.load(conn.getresponse())

    def hunt(node):
        if isinstance(node, dict):
            if node.get("id") == item_id:
                return node
            for v in node.values():
                hit = hunt(v)
                if hit:
                    return hit
        elif isinstance(node, list):
            for v in node:
                hit = hunt(v)
                if hit:
                    return hit
        return None

    in_state = hunt(state)
    in_detail = (detail.get("result") or {}).get("item", {})
    if not in_state or not in_detail:
        print("[拒] %s 在两接口中未同时存在（state=%s detail=%s）"
              % (item_id, bool(in_state), bool(in_detail)))
        return 1
    bad = 0
    for k in ("title", "status", "claimed_by", "owner", "lane",
              "close_criterion"):
        a, b = in_state.get(k), in_detail.get(k)
        ok = a == b
        bad += 0 if ok else 1
        print("[%s] %-16s state=%r detail=%r" % ("过" if ok else "拒", k, a, b))
    print("[过] 判据二通过：%s 两接口 %d 字段一致" % (item_id, 6 - bad)
          if bad == 0 else "[拒] 判据二不通过：%d 字段不一致" % bad)
    return 0 if bad == 0 else 1


def cmd_readonly():
    bad = []
    n_files = 0
    for dirpath, _dirs, files in os.walk(SHELL):
        for fn in files:
            p = os.path.join(dirpath, fn)
            if fn.endswith((".json", ".md", ".template")):
                continue  # 声明类文件不是代码；契约本身含路由名不属代码
            n_files += 1
            text = open(p, encoding="utf-8", errors="replace").read()
            for pat, label in WRITE_PATTERNS:
                for m in re.finditer(pat, text):
                    bad.append((os.path.relpath(p, ROOT), label,
                                text[max(0, m.start() - 20):m.end() + 20]))
    print("静态检索范围：%d 个代码文件（gradle/java/xml）" % n_files)
    if bad:
        for p, label, ctx in bad:
            print("[拒] %s：%s …%s…" % (p, label, ctx.replace("\n", " ")))
        return 1
    print("[过] 判据三通过：壳代码零写接口命中")
    return 0


def cmd_contract():
    contract = json.load(open(CONTRACT, encoding="utf-8"))
    routes = {r["name"]: r for r in contract["routes"]}
    need = {"home", "item", "lanes", "graph", "line"}
    bad = 0
    if set(routes) != need:
        print("[拒] 契约路由集 %s != 五路由 %s" % (sorted(routes), sorted(need)))
        bad += 1
    # 前端实现与契约一致：deeplink.ts 路由名与契约逐条对照
    ts = open(os.path.join(ROOT, "board_ui", "src", "deeplink.ts"),
              encoding="utf-8").read()
    for name in need:
        if 'route: "%s"' % name not in ts:
            print("[拒] 前端 deeplink.ts 缺路由 %s" % name)
            bad += 1
    # 壳与契约一致：MainActivity 的 scheme 与基路径、清单的 scheme/host
    java = open(os.path.join(SHELL, "app", "src", "main", "java",
                             "com", "lp", "board", "MainActivity.java"),
                encoding="utf-8").read()
    if contract["custom_scheme"]["scheme"] not in java:
        print("[拒] MainActivity 未引用契约 scheme")
        bad += 1
    if contract["host"]["app_base"] not in java:
        print("[拒] MainActivity 未引用契约应用基路径")
        bad += 1
    mf = open(os.path.join(SHELL, "app", "src", "main",
                           "AndroidManifest.xml"), encoding="utf-8").read()
    if 'android:scheme="%s"' % contract["custom_scheme"]["scheme"] not in mf:
        print("[拒] 清单未声明契约 scheme")
        bad += 1
    # 文档引用：README 引契约文件
    readme = open(os.path.join(SHELL, "README.md"), encoding="utf-8").read()
    if "deeplink_contract.json" not in readme:
        print("[拒] 壳 README 未引用契约文件")
        bad += 1
    # item 路由样例编号在账本真实存在（契约样例可追溯账本）
    import datetime
    items = json.load(open(os.path.join(ROOT, "items_registry",
                                        "items.json"),
                           encoding="utf-8"))["items"]
    ids = {it["id"] for it in items}
    for r in contract["routes"]:
        for m in re.findall(r"I-[0-9]{3}", json.dumps(r)):
            if m not in ids:
                print("[拒] 契约样例 %s 不在总账" % m)
                bad += 1
    if bad == 0:
        print("[过] 判据四通过：契约五路由齐全，壳、前端、文档三处一致，"
              "样例编号均在总账")
        return 0
    print("[拒] 判据四不通过：%d 处" % bad)
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法: verify_i064.py item I-xxx | readonly | contract")
    cmd = sys.argv[1]
    if cmd == "item":
        sys.exit(cmd_item(sys.argv[2]))
    if cmd == "readonly":
        sys.exit(cmd_readonly())
    if cmd == "contract":
        sys.exit(cmd_contract())
    raise SystemExit("未知子命令：" + cmd)
