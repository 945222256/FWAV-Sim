# -*- coding: utf-8 -*-
"""webui.py — 页面层：登录页、看板页、静态文件与看板状态接口。

未登录访问根路径时给登录页；登录会话有效时给看板页。
看板状态接口 /api/state 的数据形状与本地 LP 看板保持一致，
前端页面因此可以双端复用。仅使用 Python 标准库。
"""
import datetime
import json
import os
import re
import time

import auth
import db
import repo
import router
import settings
from httpd import ApiError

STATIC_DIR = os.path.join(settings.BASE_DIR, "static")
APP_DIR = os.path.join(settings.BASE_DIR, "app")
_MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".png": "image/png", ".ico": "image/x-icon",
         ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml",
         ".json": "application/json"}
_STATIC_FILES = ("manifest.webmanifest", "sw.js", "icon-192.png", "icon-512.png")


def _serve_file(name):
    path = os.path.normpath(os.path.join(STATIC_DIR, name))
    if not path.startswith(STATIC_DIR) or not os.path.isfile(path):
        raise ApiError("文件不存在", 404)
    with open(path, "rb") as f:
        body = f.read()
    ext = os.path.splitext(path)[1]
    return {"_raw_bytes": body, "_mime": _MIME.get(ext, "application/octet-stream")}


def _register_static_routes():
    for name in _STATIC_FILES:
        def _handler(ctx, _name=name):
            return _serve_file(_name)
        router.route("GET", "/" + name)(_handler)


_register_static_routes()


@router.route("GET", "/app")
def app_index(ctx):
    """新版全景作战地图入口：React 构建产物。

    I-075 修复：静态包不再设登录墙——壳内 WebView 兜底与未登录浏览器
    拿到的是真页面，React 应用自行处理 /api 401 并跳转登录页。
    （2026-10-05 黑屏根因之二：登录墙把未会话的壳挡在 106 字节
    meta-refresh 小页上，渲染不出任何内容。）
    """
    path = os.path.join(APP_DIR, "index.html")
    if not os.path.isfile(path):
        raise ApiError("新版前端未部署：缺少 app/index.html", 404)
    with open(path, "rb") as f:
        return {"_raw_bytes": f.read(), "_mime": "text/html; charset=utf-8"}


@router.route("GET", "/app/")
def app_index_slash(ctx):
    return app_index(ctx)


def _redirect_login():
    return {"_raw_html": "<!doctype html><meta charset=utf-8><meta http-equiv=refresh content='0;url=/login'>"}


@router.route("GET", "/app/assets/")
def app_assets_root(ctx):
    raise ApiError("目录不可列出", 403)


def _register_asset_routes():
    """构建产物静态资源：按目录扫描注册，缺失文件 404。"""
    assets_dir = os.path.join(APP_DIR, "assets")
    if not os.path.isdir(assets_dir):
        return
    for fn in os.listdir(assets_dir):
        def _handler(ctx, _fn=fn):
            path = os.path.join(assets_dir, _fn)
            if not os.path.isfile(path):
                raise ApiError("文件不存在", 404)
            with open(path, "rb") as f:
                body = f.read()
            ext = os.path.splitext(path)[1]
            return {"_raw_bytes": body, "_mime": _MIME.get(ext, "application/octet-stream")}
        router.route("GET", "/app/assets/" + fn)(_handler)


_register_asset_routes()


@router.route("GET", "/")
def index(ctx):
    if auth.session_user(ctx):
        return _serve_file("index.html")
    return _serve_file("login.html")


@router.route("GET", "/login")
def login_page(ctx):
    return _serve_file("login.html")


@router.route("GET", "/test")
def test_page(ctx):
    """壳路由实测页（I-064 判据一工具）：五个按钮发射契约五路由，
    与豆包语音指令走同一条应用内路由，公开无需登录。"""
    return _serve_file("test.html")


@router.route("GET", "/static/")
def static_root(ctx):
    raise ApiError("目录不可列出", 403)


def serve_stream(handler, ctx):
    """服务器推送事件流：事件序号推进即推给页面，页面实时刷新。

    约束：数据接口仍由页面轮询拉取，本流只做变更通知，占用一线程。
    每二十秒发一次注释行心跳防中间层断连，连接一小时后主动结束，
    页面的 EventSource 会自动重连。
    """
    try:
        auth.caller(ctx)
    except ApiError as exc:
        body = json.dumps({"ok": False, "error": exc.message}, ensure_ascii=False).encode("utf-8")
        handler.send_response(exc.status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()
    last_seq = db.server_seq()
    deadline = time.time() + 3600
    ticks = 0
    while time.time() < deadline:
        time.sleep(2)
        try:
            seq = db.server_seq()
            if seq != last_seq:
                last_seq = seq
                handler.wfile.write(("data: %s\n\n" %
                                     json.dumps({"server_seq": seq})).encode("utf-8"))
            elif ticks % 10 == 0:
                handler.wfile.write(b": ping\n\n")
            handler.wfile.flush()
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            break                                   # 页面关闭，线程退出。
        ticks += 1


def _queue_mirror_view(queue):
    """把本地待审队列镜像转换为页面渲染形状（intake 重构后的新结构）。"""
    out = []
    for q in queue or []:
        decision = None
        if q.get("review") == "approved":
            decision = {"action": "approve", "at": q.get("approved_at", ""),
                        "lane": "", "result_item": q.get("approved_as", "")}
        elif q.get("review") == "rejected":
            decision = {"action": "reject", "at": q.get("rejected_at", ""),
                        "reason": q.get("reject_reason", "")}
        lint_view = None
        lint = q.get("lint_report")
        if lint:
            rules = []
            for name, res in lint.get("checks", []):
                res_text = str(res)
                verdict = "fail" if res_text.startswith("拒") else (
                    "warn" if "建议" in res_text else "pass")
                rules.append({"rule": name, "verdict": verdict, "reason": res_text})
            lint_view = {"checked_at": q.get("submitted_at", ""),
                         "suggested_lane": lint.get("suggested_lane", "未定"),
                         "duplicate_of": lint.get("merge_candidates", []),
                         "rules": rules}
        out.append({
            "id": q.get("qid", "?"), "title": q.get("title", ""),
            "owner": q.get("submitted_by", ""), "close_criterion": q.get("close_criterion", ""),
            "criteria": [], "note": q.get("note", ""),
            "submitted_by": q.get("submitted_by", ""),
            "submitted_at": q.get("submitted_at", ""),
            "lint": lint_view, "decision": decision, "overrides": [],
        })
    return out


@router.route("GET", "/api/state")
def state(ctx):
    user = auth.session_user(ctx)
    if not user:
        try:
            auth.token_identity(ctx)
        except ApiError:
            raise ApiError("未登录或令牌无效", 401)
    items = repo.all_items()
    by_id = {i["id"]: i for i in items}
    # I-092：读权威键 lanes（与心跳上行同键；旧镜像键 lanes_conf 退役）
    lanes = repo.get_config("lanes", {}) or {}

    items_view = []
    for item in items:
        entry = dict(item)
        entry["lease_left_s"] = _lease_left(item)
        entry["blocked_by"] = [
            {"id": b, "title": by_id[b]["title"] if b in by_id else "未找到",
             "status": by_id[b]["status"] if b in by_id else "未知"}
            for b in item.get("blockers", [])]
        entry["progress"] = _progress(item)
        items_view.append(entry)

    open_items = [i for i in items_view if i["status"] != "closed"]
    lanes_view = [{"id": lid, "name": conf.get("name", lid),
                   "mission": conf.get("mission", ""),
                   "items": [i for i in open_items if i.get("lane") == lid]}
                  for lid, conf in lanes.items()]
    params = repo.all_params()
    suspect = {k: v for k, v in params.items() if "suspect" in (v.get("status") or "")}
    review = {k: v for k, v in params.items()
              if (v.get("status") or "") in ("superseded", "reference")}
    import json
    audit_bad = [i["id"] for i in items
                 if i["status"] == "closed" and (not i["evidence"] or not i["closed_at"])]
    queue_mirror = _queue_mirror_view(repo.get_config("review_queue_mirror", []))
    inbox_pending = len([e for e in queue_mirror if not e.get("decision")])
    return {
        "meta": {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                 "server_seq": repo.snapshot()["server_seq"], "viewer": user or "agent"},
        "summary": {
            "total": len(items),
            "open": len([i for i in open_items if i["status"] == "open"]),
            "blocked": len([i for i in open_items if i["status"] == "blocked"]),
            "in_progress": len([i for i in open_items if i["status"] == "in_progress"]),
            "closed": len([i for i in items if i["status"] == "closed"]),
            "owner_pending": len([i for i in open_items if "机主" in (i.get("owner") or "")]),
            "inbox_pending": inbox_pending,
            "audit_bad": audit_bad,
        },
        "lanes": lanes_view,
        "unassigned": [i for i in open_items if not i.get("lane")],
        "owner_items": [i for i in open_items if "机主" in (i.get("owner") or "")],
        "closed_items": [i for i in items_view if i["status"] == "closed"],
        "inbox": queue_mirror,
        "reports": repo.all_reports(),
        "params": {"count": len(params), "archive": 0,
                   "check": {"suspect": suspect, "review": review}},
        "events": repo.all_events(),
    }


def _lease_left(item):
    lease = item.get("lease_until")
    if not lease:
        return None
    try:
        left = (datetime.datetime.fromisoformat(lease)
                - datetime.datetime.now()).total_seconds()
    except ValueError:
        return None
    return max(0, int(left // 60) * 60)


def _progress(item):
    crit = item.get("criteria")
    if not crit:
        return None
    return {"verified": len([c for c in crit if c.get("verified")]), "total": len(crit)}
