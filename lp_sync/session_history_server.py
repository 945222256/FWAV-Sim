# -*- coding: utf-8 -*-
"""session_history_server.py — I-227 会话历史只读查询服务。

独立进程运行，只响应 GET 请求；端口经环境变量
LP_SESSION_HISTORY_PORT 指定，缺省 58791，只绑定本机回环地址。
本服务不随生产启动项自动运行；生产启用由维护方按交付清单步骤
显式启动。查询只读：不恢复会话、不执行会话、不写任何文件。

路由：
  GET /healthz                        存活检查
  GET /api/session_history/sessions   列表筛选与稳定分页
  GET /api/session_history/sessions/<会话号>   单会话详情
  GET /api/session_history/stats      索引统计与来源计数
仅使用 Python 标准库。
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import session_history

PORT = int(os.environ.get("LP_SESSION_HISTORY_PORT", "58791"))
HOST = "127.0.0.1"


def _query_filters(qs):
    get = lambda k: (qs.get(k) or [None])[0]  # noqa: E731
    return {"lane": get("lane"), "worker_id": get("worker_id"),
            "item_id": get("item_id"), "retention": get("retention"),
            "date_from": get("date_from"), "date_to": get("date_to")}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "session-history-readonly/1"

    def log_message(self, fmt, *args):
        pass                            # 只读服务不刷日志

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        path = parsed.path.rstrip("/")
        try:
            if path == "/healthz":
                self._send(200, {"ok": True, "service": "session_history",
                                 "read_only": True})
            elif path == "/api/session_history/sessions":
                page = int((qs.get("page") or ["1"])[0] or 1)
                page_size = int((qs.get("page_size") or
                                 [session_history.PAGE_SIZE_DEFAULT])[0]
                                or session_history.PAGE_SIZE_DEFAULT)
                result = session_history.list_sessions(
                    _query_filters(qs), page=page, page_size=page_size)
                self._send(200, {"ok": True, "result": result})
            elif path.startswith("/api/session_history/sessions/"):
                sid = path[len("/api/session_history/sessions/"):]
                detail = session_history.get_session(sid)
                self._send(200 if detail.get("ok") else 404,
                           {"ok": bool(detail.get("ok")), "result": detail})
            elif path == "/api/session_history/stats":
                self._send(200, {"ok": True,
                                 "result": session_history.stats()})
            else:
                self._send(404, {"ok": False, "reason": "未知路径"})
        except Exception as exc:      # 查询失败返回明确原因，不静默
            self._send(500, {"ok": False, "reason": repr(exc)[:200]})

    def do_POST(self):
        self._send(405, {"ok": False,
                         "reason": "本服务只读，只接受 GET 请求"})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("session_history 只读查询服务监听 %s:%d" % (HOST, PORT), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
