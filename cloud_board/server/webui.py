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
         ".json": "application/json",
         # I-202 黑屏修复：视频与三维资产 MIME 显式登记（octet-stream
         # 会被部分浏览器与移动端 WebView 拒绝渲染）。
         ".mp4": "video/mp4", ".webm": "video/webm", ".webp": "image/webp",
         ".glb": "model/gltf-binary"}
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


def _serve_media_with_range(path, mime, ctx):
    """I-202 黑屏修复：媒体静态响应支持 HTTP Range（视频 seek 与移动端
    渐进播放需要二零六部分内容），并显式声明 Accept-Ranges。"""
    import re as _re
    with open(path, "rb") as f:
        data = f.read()
    rng = ctx.headers.get("Range") or ""
    m = _re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
    total = len(data)
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else total - 1
        else:
            start = max(0, total - int(m.group(2)))
            end = total - 1
        start, end = max(0, start), min(total - 1, end)
        if start > end:
            raise ApiError("区间无效", 416)
        return {"_raw_bytes": data[start:end + 1], "_mime": mime, "_status": 206,
                "_headers_extra": {"Content-Range": "bytes %d-%d/%d" % (start, end, total),
                                   "Accept-Ranges": "bytes"}}
    return {"_raw_bytes": data, "_mime": mime,
            "_headers_extra": {"Accept-Ranges": "bytes"}}


def _register_lab_routes():
    """I-130：参数实验室固定渲染资产（转台序列帧与资产清单）。

    目录 app/lab/ 由资产导出工序产出；页面只加载这里的导出产物，
    不请求 usd 或 urdf 源文件（四稿四之一）。
    顺带注册 app 根下的 PWA 图标与清单（此前 404，壳内安装信息缺失）。"""
    mime = dict(_MIME)
    mime.update({".webp": "image/webp", ".glb": "model/gltf-binary"})
    lab_dir = os.path.join(APP_DIR, "lab")
    if os.path.isdir(lab_dir):
        # I-202：递归注册子目录（eaglex700 五段帧序列在
        # lab/eaglex700/<段>/frame_NN.webp；I-130 时仅顶层转台帧）。
        for _base, _dirs, _files in os.walk(lab_dir):
            for fn in _files:
                rel = os.path.relpath(os.path.join(_base, fn), lab_dir).replace("\\", "/")
                def _handler(ctx, _rel=rel):
                    path = os.path.join(lab_dir, *_rel.split("/"))
                    if not os.path.isfile(path):
                        raise ApiError("文件不存在", 404)
                    ext = os.path.splitext(path)[1]
                    m = mime.get(ext, "application/octet-stream")
                    if ext in (".mp4", ".webm"):
                        return _serve_media_with_range(path, m, ctx)
                    with open(path, "rb") as f:
                        return {"_raw_bytes": f.read(), "_mime": m}
                router.route("GET", "/app/lab/" + rel)(_handler)
    for fn in ("manifest.webmanifest", "icon-192.png", "icon-512.png", "icons.svg"):
        path = os.path.join(APP_DIR, fn)
        if not os.path.isfile(path):
            continue

        def _root_handler(ctx, _path=path, _fn=fn):
            with open(_path, "rb") as f:
                body = f.read()
            ext = os.path.splitext(_path)[1]
            return {"_raw_bytes": body, "_mime": _MIME.get(ext, "application/octet-stream")}
        router.route("GET", "/app/" + fn)(_root_handler)


_register_lab_routes()


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
