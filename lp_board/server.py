# -*- coding: utf-8 -*-
"""server.py — LP 数字化看板服务。

纪律：
  1. 只监听本机回环地址 127.0.0.1，固定端口 8800，不对外暴露。
  2. 单一事实源：一切数据实时读取 items_registry 与 params_registry 的文件，
     本服务不复制账本数据，页面每个数字都能指回账本字段。
  3. 看板不绕过工具写账：任何状态变更调用 ledger.py、dispatch.py、
     intake.py、events.py 的函数完成，保留原子锁与证据强制。
  4. 零外部服务依赖：仅 Python 标准库，无云端、无数据库、无消息队列。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_BOARD_DIR = os.path.dirname(os.path.abspath(__file__))
_REGISTRY_DIR = os.path.join(os.path.dirname(_BOARD_DIR), "items_registry")
_PARAMS_DIR = os.path.join(os.path.dirname(_BOARD_DIR), "params_registry")
_ROOT = os.path.dirname(_BOARD_DIR)
sys.path.insert(0, _REGISTRY_DIR)
sys.path.insert(0, _PARAMS_DIR)

import ledger          # noqa: E402
import dispatch        # noqa: E402
import events          # noqa: E402
import intake          # noqa: E402
import registry        # noqa: E402

HOST = "127.0.0.1"
PORT = 8800
INDEX_PATH = os.path.join(_BOARD_DIR, "static", "index.html")
STATE_PATH = os.path.join(_BOARD_DIR, "state.json")
POLL_INTERVAL_S = 30          # 后台线程周期：租约清理与提交抓取
GIT_SCAN_LIMIT = 800          # 历史提交回溯上限


def _log(msg):
    line = "[%s] %s" % (datetime.datetime.now().isoformat(timespec="seconds"), msg)
    print(line, flush=True)


# ---------------------------------------------------------------------------
# 数据组装：全部只读，实时取自账本文件。
# ---------------------------------------------------------------------------

def _lease_left_seconds(item):
    """租约剩余时间，粒度取整到分钟：与页面显示粒度一致，避免轮询指纹抖动。"""
    lease = item.get("lease_until")
    if not lease:
        return None
    try:
        left = (datetime.datetime.fromisoformat(lease) - datetime.datetime.now()).total_seconds()
    except ValueError:
        return None
    return max(0, int(left // 60) * 60)


def _blocked_by(item, items_by_id):
    """阻塞关系：结构化 blockers 字段优先，备注文本正则兜底。"""
    refs = list(item.get("blockers") or [])
    if item.get("status") == "blocked" and not refs:
        refs = re.findall(r"阻塞于\s*(I-\d{3})", item.get("note", "") or "")
    out = []
    for ref in refs:
        target = items_by_id.get(ref)
        out.append({"id": ref, "title": target["title"] if target else "未找到",
                    "status": target["status"] if target else "未知"})
    return out


def _criterion_progress(item):
    """进度口径：已验证判据子项数除以子项总数。未登记子项返回 None，禁止假进度。"""
    crit = item.get("criteria")
    if not crit:
        return None
    verified = len([c for c in crit if c.get("verified")])
    return {"verified": verified, "total": len(crit)}


def _inbox_view():
    """读待审队列文件并转换为页面渲染形状（兼容 intake 重构后的新结构）。"""
    try:
        queue = intake._load(intake.QUEUE_PATH, {"queue": []})["queue"]
    except AttributeError:
        queue = []
    out = []
    for q in queue:
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


def build_state():
    """组装页面全量状态。任何字段都可指回账本文件的字段。"""
    obj = json.load(open(ledger.ITEMS_PATH, encoding="utf-8"))
    lanes_conf = json.load(open(dispatch.LANES_PATH, encoding="utf-8"))
    items_by_id = {i["id"]: i for i in obj["items"]}
    now = datetime.datetime.now().isoformat(timespec="seconds")

    items_view = []
    for item in obj["items"]:
        entry = dict(item)
        entry["lease_left_s"] = _lease_left_seconds(item)
        entry["blocked_by"] = _blocked_by(item, items_by_id)
        entry["progress"] = _criterion_progress(item)
        entry["replies"] = item.get("replies", [])
        items_view.append(entry)

    open_items = [i for i in items_view if i["status"] != "closed"]
    owner_items = [i for i in open_items if "机主" in (i.get("owner") or "")]

    lanes_view = []
    for lane_id, lane in lanes_conf["lanes"].items():
        lanes_view.append({
            "id": lane_id,
            "name": lane["name"],
            "mission": lane["mission"],
            "items": [i for i in open_items if i.get("lane") == lane_id],
        })
    unassigned = [i for i in open_items if not i.get("lane")]

    try:
        params_check = registry.check()
        params_count = len(registry._load(registry.PARAMS_PATH)["params"])
        archive_count = len(registry._load(registry.ARCHIVE_PATH)["archive"])
    except Exception as exc:  # 参数登记库异常不拖垮看板，账本照常展示。
        params_check = {"suspect": {}, "review": {}, "error": str(exc)}
        params_count = archive_count = -1
    inbox_view = _inbox_view()

    board_meta = {"generated_at": now, "host": HOST, "port": PORT,
                  "root": _ROOT, "poll_interval_s": POLL_INTERVAL_S}
    if os.path.exists(STATE_PATH):
        try:
            board_meta["git_cursor"] = json.load(open(STATE_PATH, encoding="utf-8")).get("last_hash", "")
        except Exception:
            board_meta["git_cursor"] = ""

    return {
        "meta": board_meta,
        "rules": obj.get("rules", {}),
        "summary": {
            "total": len(obj["items"]),
            "open": len([i for i in open_items if i["status"] == "open"]),
            "blocked": len([i for i in open_items if i["status"] == "blocked"]),
            "in_progress": len([i for i in open_items if i["status"] == "in_progress"]),
            "closed": len([i for i in obj["items"] if i["status"] == "closed"]),
            "owner_pending": len(owner_items),
            "inbox_pending": len([q for q in inbox_view if not q.get("decision")]),
            "audit_bad": ledger.audit(),
        },
        "lanes": lanes_view,
        "unassigned": unassigned,
        "owner_items": owner_items,
        "closed_items": [i for i in items_view if i["status"] == "closed"],
        "inbox": inbox_view,
        "reports": obj.get("reports", []),
        "params": {"count": params_count, "archive": archive_count, "check": params_check},
        "events": events.all_events(),
    }


# ---------------------------------------------------------------------------
# 后台线程：过期租约清理与 git 提交抓取（FR-5）。
# ---------------------------------------------------------------------------

def _load_cursor():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, encoding="utf-8")).get("last_hash", "")
        except Exception:
            return ""
    return ""


def _save_cursor(hash_value):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"last_hash": hash_value}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def _git_log():
    """读取仓库提交记录，返回从新到旧的提交列表。"""
    try:
        proc = subprocess.run(
            ["git", "-C", _ROOT, "log", "-n", str(GIT_SCAN_LIMIT),
             "--format=%H%x00%s%x00%b%x1e"],
            capture_output=True, encoding="utf-8", timeout=30)
    except Exception as exc:
        _log("git 读取失败：%s" % exc)
        return []
    if proc.returncode != 0:
        _log("git 返回非零：%s" % proc.stderr[:200])
        return []
    commits = []
    for record in proc.stdout.split("\x1e"):
        parts = [p.strip() for p in record.strip("\n").split("\x00")]
        if len(parts) >= 2 and parts[0]:
            commits.append({"hash": parts[0], "subject": parts[1],
                            "body": parts[2] if len(parts) > 2 else ""})
    return commits


def _attach_commit_evidence(commits):
    """把提交信息里引用的事项编号自动挂到对应事项的证据链上。"""
    cursor = _load_cursor()
    items_obj = json.load(open(ledger.ITEMS_PATH, encoding="utf-8"))
    by_id = {i["id"]: i for i in items_obj["items"]}
    attached = 0
    for commit in commits:                       # 从新到旧。
        if commit["hash"] == cursor:             # 已处理过的历史边界。
            break
        refs = sorted(set(re.findall(r"I-\d{3}", commit["subject"] + " " + commit["body"])))
        for ref in refs:
            item = by_id.get(ref)
            if item is None:
                continue
            short = commit["hash"][:7]
            if any(short in ev for ev in item.get("evidence", [])):
                continue                          # 该提交已挂载过，跳过。
            ev_text = "提交 %s %s" % (short, commit["subject"][:60])
            try:
                ledger.append_evidence(ref, ev_text)
                events.append("git_evidence", item_id=ref, actor="board-git",
                              detail=ev_text)
                attached += 1
            except ValueError:
                pass                              # 重复证据被账本拒绝，属正常。
    if commits:
        _save_cursor(commits[0]["hash"])
    return attached


def background_worker():
    """周期任务：过期租约清理加提交证据抓取。"""
    first = True
    while True:
        try:
            expired = dispatch.sweep()
            for item_id in expired:
                events.append("sweep", item_id=item_id, actor="board-sweep",
                              detail="租约到期自动退回 open")
            attached = _attach_commit_evidence(_git_log())
            if expired or attached or first:
                _log("后台周期完成：租约退回 %s 条，新挂证据 %s 条" % (len(expired), attached))
        except Exception as exc:
            _log("后台线程异常：%r" % exc)
        first = False
        time.sleep(POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
# HTTP 服务：路由与写操作入口。写操作全部调用账本函数。
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "LPBoard/1.0"

    def log_message(self, fmt, *args):
        pass                                       # 静默访问日志，降低资源占用。

    # -- 请求头校验：本机回环且 Host 合法，阻断浏览器跨站提交。 --
    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in ("%s:%d" % (HOST, PORT), "localhost:%d" % PORT, "[%s]:%d" % (HOST, PORT))

    def _send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            self._send_json({"error": "Host 头非法"}, 403)
            return
        if self.path in ("/", "/index.html"):
            try:
                body = open(INDEX_PATH, "rb").read()
            except OSError:
                self._send_json({"error": "页面文件缺失"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/state":
            try:
                self._send_json(build_state())
            except Exception as exc:
                self._send_json({"error": "状态组装失败：%r" % exc}, 500)
            return
        if self.path == "/api/ping":
            self._send_json({"ok": True, "ts": datetime.datetime.now().isoformat(timespec="seconds")})
            return
        if self.path == "/api/lab/params":
            # I-130：参数实验室只读载荷，实时读本地参数登记库文件。
            try:
                import lab_payload
                _params = json.load(open(os.path.join(_PARAMS_DIR, "params.json"),
                                         encoding="utf-8"))["params"]
                _archive = json.load(open(os.path.join(_PARAMS_DIR, "archive.json"),
                                          encoding="utf-8"))["archive"]
                self._send_json({"ok": True, "result": lab_payload.build_payload(_params, _archive)})
            except Exception as exc:
                self._send_json({"error": "实验室载荷组装失败：%r" % exc}, 500)
            return
        self._send_json({"error": "未知路径 %s" % self.path}, 404)

    def do_POST(self):
        if not self._host_ok():
            self._send_json({"error": "Host 头非法"}, 403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1 << 20:
            self._send_json({"error": "请求体为空或超限"}, 400)
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send_json({"error": "请求体不是合法 JSON"}, 400)
            return
        route = self.path
        try:
            result = self._dispatch_post(route, data)
            self._send_json({"ok": True, "result": result})
        except Exception as exc:
            self._send_json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 400)

    def _dispatch_post(self, route, data):
        if route == "/api/owner_reply":
            reply = ledger.owner_reply(data["item_id"], data["text"],
                                       actor=data.get("actor") or "机主")
            return {"reply": reply}

        if route == "/api/report":
            entry = ledger.add_report({
                "model": data.get("model", ""),
                "model_version": data.get("model_version", ""),
                "python": data.get("python", ""),
                "os": data.get("os", ""),
                "cwd": data.get("cwd", ""),
                "item_id": data.get("item_id", ""),
                "log_pointer": data.get("log_pointer", ""),
            })
            return {"report": entry}

        if route == "/api/intake":
            entry = intake.submit(
                title=data.get("title", ""),
                owner=data.get("owner", "待指派"),
                close_criterion=data.get("close_criterion", ""),
                criteria=data.get("criteria", []),
                note=data.get("note", ""),
                submitted_by=data.get("submitted_by", ""))
            return {"entry": entry}

        if route == "/api/intake/decide":
            entry = intake.decide(
                entry_id=data["entry_id"],
                action=data["action"],
                by=data.get("by", ""),
                lane=data.get("lane", ""),
                merge_into=data.get("merge_into", ""),
                reason=data.get("reason", ""))
            return {"entry": entry}

        if route == "/api/claim":
            dispatch.claim(data["item_id"], data["worker"],
                           lease_hours=int(data.get("lease_hours", 4)))
            events.append("claim", item_id=data["item_id"], actor=data["worker"],
                          detail="认领，租约 %s 小时" % data.get("lease_hours", 4))
            return {"claimed": data["item_id"]}

        if route == "/api/release":
            dispatch.release(data["item_id"], data["worker"])
            events.append("release", item_id=data["item_id"], actor=data["worker"],
                          detail="主动释放认领")
            return {"released": data["item_id"]}

        if route == "/api/close":
            evidence = [e for e in data.get("evidence", []) if e and e.strip()]
            ledger.close(data["item_id"], evidence, closer=data.get("worker", ""),
                         conclusion=data.get("conclusion", ""),
                         criterion_check=data.get("criterion_check"))
            events.append("close", item_id=data["item_id"], actor=data.get("worker", ""),
                          detail="打勾关闭（%s），证据 %d 条"
                                 % (data.get("conclusion", "未填结论"), len(evidence)))
            return {"closed": data["item_id"]}

        if route == "/api/evidence":
            ledger.append_evidence(data["item_id"], data["evidence"].strip())
            events.append("evidence", item_id=data["item_id"],
                          actor=data.get("actor", ""), detail=data["evidence"][:120])
            return {"appended": data["item_id"]}

        if route == "/api/criterion":
            crit = ledger.set_criterion(data["item_id"], int(data["index"]),
                                        bool(data["verified"]),
                                        evidence=data.get("evidence", ""),
                                        actor=data.get("actor", ""))
            return {"criterion": crit}

        raise ValueError("未知路由 %s" % route)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=background_worker, daemon=True)
    worker.start()
    _log("LP 看板已启动：http://%s:%d （数据源 %s）" % (HOST, PORT, _REGISTRY_DIR))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("收到中断，看板退出。账本文件不受影响。")


if __name__ == "__main__":
    main()
