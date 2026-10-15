# -*- coding: utf-8 -*-
"""
dashboard.py — Isaac 实时状态看板服务（纯 stdlib，无第三方依赖）
================================================================
GET /       → dashboard.html
GET /state  → 最新快照 JSON（main_sim 以 30Hz 调 update()）

浏览器打开 http://localhost:<port> 即可，随时开随时关，不影响仿真。
"""
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("dashboard")


class _Handler(BaseHTTPRequestHandler):
    html_path = None
    snapshot = {}

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            try:
                with open(self.html_path, "rb") as f:
                    body = f.read()
                self._send(200, body, "text/html; charset=utf-8")
            except OSError as e:
                self._send(404, str(e).encode(), "text/plain")
        elif self.path == "/state":
            body = json.dumps(self.snapshot or {}).encode()
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # 静默访问日志


class DashboardServer:
    def __init__(self, port: int, html_path: str = None):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.port = port
        self.html_path = html_path or os.path.join(root, "dashboard.html")
        self._lock = threading.Lock()
        self._snap = {}
        self._httpd = None

    def update(self, snapshot: dict):
        with self._lock:
            self._snap = snapshot

    def start(self):
        h = _Handler
        h.html_path = self.html_path

        def get_snap():
            with self._lock:
                return self._snap
        h.snapshot = property(lambda self: get_snap())

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), h)
        threading.Thread(target=self._httpd.serve_forever,
                         daemon=True, name="dashboard").start()
        logger.info("看板已启动: http://localhost:%d", self.port)
