# -*- coding: utf-8 -*-
"""httpd.py — HTTPS 服务层：线程化服务器与请求上下文。

职责单一：终结 TLS、解析请求、构造上下文、把路由结果写回。
业务逻辑全部在 api 层与 webui 层，本文件不含任何账本知识。
仅使用 Python 标准库。
"""
import json
import ssl
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import router


class Ctx:
    """一次请求的上下文：请求信息在左，响应组装在右。"""

    def __init__(self, handler):
        self._h = handler
        self.method = handler.command
        parsed = urlparse(handler.path)
        self.path = parsed.path
        self.query = parsed.query
        self.headers = handler.headers
        self.body = {}
        length = int(handler.headers.get("Content-Length") or 0)
        if 0 < length <= (1 << 20):
            try:
                self.body = json.loads(handler.rfile.read(length).decode("utf-8"))
            except Exception:
                self.body = {}
        self.status = 200
        self._cookies_out = []

    # ---- 请求侧 ----
    def cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def bearer_token(self):
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def actor_ip(self):
        return self.headers.get("X-Real-IP") or self._h.client_address[0]

    # ---- 响应侧 ----
    def set_cookie(self, name, value, max_age, http_only=True, same_site="Strict"):
        parts = ["%s=%s" % (name, value), "Path=/", "Max-Age=%d" % max_age,
                 "SameSite=%s" % same_site]
        if http_only:
            parts.append("HttpOnly")
        self._cookies_out.append("; ".join(parts))

    def drop_cookie(self, name):
        self._cookies_out.append("%s=; Path=/; Max-Age=0" % name)

    def send(self, payload, status=200):
        self.status = status
        self._payload = payload


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "LPBoardCloud/2.0"

    def log_message(self, fmt, *args):
        pass                                  # 访问日志静默，事件流承担审计。

    def _run(self):
        ctx = Ctx(self)
        try:
            result = router.dispatch(self.command, ctx.path, ctx)
            if result is not None:
                ctx._payload = result
            payload = ctx._payload
        except KeyError as exc:
            payload, ctx.status = {"ok": False, "error": str(exc)}, 404
        except ApiError as exc:
            payload, ctx.status = {"ok": False, "error": exc.message}, exc.status
        except Exception as exc:              # 未预期异常：五百分且留现场可查。
            payload = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
            ctx.status = 500
        ctype = "application/json; charset=utf-8"
        if isinstance(payload, dict) and payload.get("_raw_bytes") is not None:
            ctype = payload.get("_mime", "application/octet-stream")
            body = payload["_raw_bytes"]
            # I-202 黑屏修复：静态字节响应支持二零六部分内容与附加头
            # （视频 Range 请求），状态码与头由静态 handler 决定。
            if isinstance(payload.get("_status"), int):
                ctx.status = payload["_status"]
            extra = payload.get("_headers_extra") or {}
        elif isinstance(payload, dict) and payload.get("_raw_html"):
            ctype = "text/html; charset=utf-8"
            body = payload["_raw_html"].encode("utf-8")
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(ctx.status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra.items() if isinstance(payload, dict) and payload.get("_raw_bytes") is not None else []):
            self.send_header(k, str(v))
        for c in ctx._cookies_out:
            self.send_header("Set-Cookie", c)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        ctx = Ctx(self)
        if ctx.path == "/api/stream":
            import webui                  # 推送流由页面层接管响应，延迟导入避免环。
            return webui.serve_stream(self, ctx)
        self._run()

    def do_POST(self):
        self._run()


class ApiError(Exception):
    """业务拒绝：携带状态码与给调用方看的中文原因。"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def serve(host, port, cert_file, key_file):
    """终结 TLS 并开始服务。"""
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    print("[%s] 云端看板已启动：https://%s:%d" %
          (datetime.datetime.now().isoformat(timespec="seconds"), host, port), flush=True)
    httpd.serve_forever()
