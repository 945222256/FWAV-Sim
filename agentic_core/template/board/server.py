# -*- coding: utf-8 -*-
"""server.py — 本地只读看板服务。

纪律：
  1. 只监听本机回环地址 127.0.0.1，默认端口 8899，不对外暴露。
  2. 单一事实源：一切数据实时读取 registry 目录的文件，本服务
     不复制账本数据，页面每个数字都能指回账本字段。
  3. 本服务只读不写：认领、关闭等写操作一律走 registry 命令行
     工具完成，看板页面不提供写入口。
  4. 零外部服务依赖：仅 Python 标准库。
  5. 路线、颜色等展示配置不在本文件写死，全部来自 lanes.json。
仅使用 Python 标准库。
"""
import json
import os
import sys
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_BOARD_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BOARD_DIR)
_REGISTRY_DIR = os.path.join(_ROOT, "registry")
sys.path.insert(0, _REGISTRY_DIR)

HOST = "127.0.0.1"
PORT = int(os.environ.get("BOARD_PORT", "8899"))
INDEX_PATH = os.path.join(_BOARD_DIR, "static", "index.html")


def _read_json(name, default):
    path = os.path.join(_REGISTRY_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_state():
    """组装看板全量状态：只读，实时取自账本文件。"""
    items_obj = _read_json("items.json", {"items": []})
    lanes = _read_json("lanes.json", {"lanes": {}})["lanes"]
    queue = _read_json("review_queue.json", {"queue": []})["queue"]
    events = _read_json("events.json", {"events": []})["events"][-60:]
    by_lane = {}
    for i in items_obj["items"]:
        if i["status"] == "closed":
            continue
        by_lane.setdefault(i.get("lane") or "未派发", []).append(
            {"id": i["id"], "title": i["title"], "status": i["status"],
             "claimed_by": i.get("claimed_by") or "空闲",
             "lease_until": i.get("lease_until"),
             "owner": i.get("owner", "")})
    for lane_id, lane in lanes.items():
        by_lane.setdefault(lane_id, [])
    open_count = sum(len(v) for k, v in by_lane.items() if k != "未派发")
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "lanes": [{"id": lid, "name": lanes.get(lid, {}).get("name", lid)}
                  for lid in sorted(lanes)] + (
            [{"id": "未派发", "name": "未派发"}] if by_lane.get("未派发") else []),
        "board": [{"lane": lid, "items": items}
                  for lid, items in sorted(by_lane.items())],
        "inbox": [{"qid": q.get("qid"), "title": q.get("title"),
                   "submitted_by": q.get("submitted_by"),
                   "review": q.get("review")} for q in queue
                  if q.get("review") in ("pending", "linted")],
        "events": events,
        "totals": {"open": open_count,
                   "closed": len([i for i in items_obj["items"]
                                  if i["status"] == "closed"])},
    }


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path.startswith("/api/state"):
            body = json.dumps(build_state(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ("/", "/index.html") and os.path.exists(INDEX_PATH):
            with open(INDEX_PATH, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, fmt, *args):
        pass


def main():
    print("看板服务：%s:%d（只读，写操作走 registry 命令行工具）" % (HOST, PORT))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
