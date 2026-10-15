# -*- coding: utf-8 -*-
"""verify_deploy_interfaces.py — 部署接口清单自检（I-164 判据三）。

原理：入口文件 server_main.py 的模块挂载清单必须覆盖页面所需接口
模块全集。本自检在部署后（或部署前本地）运行：
  1. 静态核对：解析入口源码的 import api_* 清单，与页面模块全集
     比对，缺一个即报警退出非零；
  2. 在线核对（可选 --live 主机）：对全部已登记 GET 接口逐一探测，
     非 404 即视为在线（401/403/400 都是鉴权或参数拦截，模块已挂载）。

用法：
  python tools/verify_deploy_interfaces.py            静态核对（本地仓库）
  python tools/verify_deploy_interfaces.py --live 主机名  静态+在线核对
仅使用 Python 标准库。
"""
import json
import os
import re
import ssl
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOUD = os.path.join(ROOT, "cloud_board")
ENTRY = os.path.join(CLOUD, "server_main.py")

# 页面所需接口模块全集：模块名 → 依赖的页面/功能
REQUIRED = {
    "api_auth": "登录与会话（全部页面）",
    "api_agent": "代理心跳与快照（采集代理）",
    "api_ledger": "总账与事件（旧版看板与作战地图）",
    "api_dispatch": "派发认领（泳道板）",
    "api_intake": "待审收纳（拍板区）",
    "api_map": "作战地图与事项详情（新版页面）",
    "api_console": "编排控制台三页（I-142）",
    "api_lab": "参数实验室（I-130/I-164）",
}


def static_check(entry_path=ENTRY):
    src = open(entry_path, encoding="utf-8").read()
    mounted = set(re.findall(r"^import (api_\w+)", src, re.M))
    missing = sorted(set(REQUIRED) - mounted)
    extra = sorted(mounted - set(REQUIRED))
    bad = 0
    for mod in sorted(REQUIRED):
        if mod in mounted:
            print("[过] %-14s 已挂载（%s）" % (mod, REQUIRED[mod]))
        else:
            print("[拒] %-14s 缺失！（%s——对应页面将不可用）"
                  % (mod, REQUIRED[mod]))
            bad += 1
    for mod in extra:
        print("[警] %-14s 已挂载但不在必需清单（新模块请更新本自检）" % mod)
    print("[过] 静态核对通过：%d/%d 模块挂载" % (len(mounted & set(REQUIRED)),
                                             len(REQUIRED))
          if bad == 0 else "[拒] 静态核对：%d 个模块缺失" % bad)
    return 1 if bad else 0


def live_check(host):
    """在线核对：对必需模块各取一个代表接口探测。404 即模块未挂载；
    401/403/400/200 均为已挂载（鉴权与参数拦截是模块在位的证据）。"""
    sec_path = os.path.join(ROOT, "lp_sync", "cloud_secret.json")
    ctx = ssl._create_unverified_context()
    import http.client as _hc
    probes = {
        "api_auth": ("/api/auth/whoami", "GET"),
        "api_agent": ("/api/agent/snapshot", "GET"),
        "api_ledger": ("/api/events", "GET"),
        "api_dispatch": ("/api/dispatch/sweep", "POST"),
        "api_intake": ("/api/intake/decide", "POST"),
        "api_map": ("/api/map", "GET"),
        "api_console": ("/api/console/state", "GET"),
        "api_lab": ("/api/lab/params", "GET"),
    }
    bad = 0
    for mod, path in sorted(probes.items()):
        try:
            p, method = path
            conn = _hc.HTTPSConnection(host, timeout=15, context=ctx)
            conn.request(method, p, body="{}" if method == "POST" else None,
                         headers={"Content-Type": "application/json"}
                         if method == "POST" else {})
            code = conn.getresponse().status
            conn.close()
        except Exception as e:
            print("[拒] %-14s 探测异常：%r" % (mod, e))
            bad += 1
            continue
        if code == 404:
            print("[拒] %-14s %s → 404（模块未挂载！）" % (mod, p))
            bad += 1
        else:
            print("[过] %-14s %s → %d" % (mod, p, code))
    if bad == 0:
        print("[过] 在线核对通过：全部必需模块接口在线")
        return 0
    print("[拒] 在线核对：%d 个模块不在线" % bad)
    return 1


if __name__ == "__main__":
    rc = static_check()
    if "--live" in sys.argv:
        host = sys.argv[sys.argv.index("--live") + 1]
        rc = max(rc, live_check(host))
    sys.exit(rc)
