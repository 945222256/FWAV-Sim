# -*- coding: utf-8 -*-
"""host_service.py — I-126 判据四：常驻后台派活宿主服务（样机级）。

形态：单进程常驻，内管一个长活的协议子进程（app-server），本机
127.0.0.1:58771 提供逐行 JSON 指令口：
  {"op":"status"}                          → 服务与子进程状态
  {"op":"dispatch_new","title","message"}  → 经长活子进程新建会话直送
  {"op":"dispatch_resume","sid","message"} → 经长活子进程先恢复后发送
  {"op":"index_register","sid","title"}    → I-131 任务索引登记（幂等）
  {"op":"queue_status"}                    → I-175 派发请求文件队列状态
  {"op":"queue_submit",...请求字段}         → I-175 程序化投递请求入队
  {"op":"kill_child"}                      → 自愈测试钩子：杀子进程
I-175 派发请求文件队列（dispatch_file_queue.py）：常驻轮询线程监视
lp_sync/dispatch_queue/incoming/，指挥部写入约定格式请求文件后，两轮
队列心跳内自动认领派发并回读核验，失败进重试队列留痕，全程不经
Kimi 侧命令确认。格式与台账约定见 dispatch_file_queue.py 头注。
派发回读命中后自动做任务索引登记（I-131 丙案，防护六条见
op_index_register 注释；调查报告 docs/HQ专项_任务索引登记风险调查_20261008.md）。
自愈：看护线程三秒一轮，子进程死亡即拉起并计数。
开机自启：install_host_service_startup.bat 装入启动文件夹。
派活工具 tools/dispatch_session.mjs 优先走本服务，服务不在时回退
按需拉起。

I-157 派发纪律（D-047）：一队一常驻会话，后续任务注入老会话；
新会话仅限两种情形——交接切换（常驻会话上下文满触发切换）与新
路线首发（该队尚无常驻会话）；测试与自检派发不登记任务索引也不进
派发登记册。驻留映射存 DISPATCH_ROSTER_PATH（队名→会话号）。
仅使用 Python 标准库。
"""
import datetime
import json
import os
import socket
import subprocess
import sys
import threading
import time
import re
import uuid
import hashlib
from execution_store import ExecutionStore
import lane_workers as lw

# I-208 根因修复：以脚本方式启动时（python/pythonw host_service.py），
# 本模块在 sys.modules 中名为 __main__；文件桥（dispatch_file_queue）
# 的派发路径惰性执行 import host_service 会从磁盘再加载一份副本，
# 形成双模块实例——两份全局态（子进程池、锁）互不相通，且副本固定
# 为首次惰性导入时刻的源码快照。这里把本实例注册为 host_service，
# 使后续 import 拿到同一运行实例（Jane 15:21 实测队列三路 NameError
# 而同宿主直连正常，即此双实例机理）。
if __name__ in ("__main__", "host_service") and "host_service" not in sys.modules:
    sys.modules["host_service"] = sys.modules[__name__]

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZC = os.path.join(HOME, "AppData", "Local", "Programs", "ZCode",
                  "resources", "glm", "zcode.cjs")
HOST, PORT = "127.0.0.1", int(os.environ.get("LP_HOST_PORT", "58771"))
STATUS_PATH = os.path.join(ROOT, "logs", os.environ.get(
    "LP_HOST_STATUS", "i126_t4_service_status.json"))
# I-157 驻留注册表：队名 → 常驻会话号（一队一驻，交接切换时换绑）
DISPATCH_ROSTER_PATH = os.environ.get("LP_HOST_ROSTER_PATH", os.path.join(ROOT, "lp_sync", "dispatch_roster.json"))
RUNTIME_ROOT = os.environ.get("LP_HOST_STATE_ROOT", os.path.join(ROOT, "lp_sync", "runtime"))
EXECUTION_STORE = ExecutionStore(os.path.join(RUNTIME_ROOT, "execution.json"))
_operation_lock_guard = threading.Lock()
_operation_locks = {}
_roster_lock = threading.RLock()
_status_lock = threading.RLock()


def operation_lock(key):
    with _operation_lock_guard:
        return _operation_locks.setdefault(key, threading.RLock())
WS = {"workspace": {"workspaceKey": "G:\\Playground", "workspacePath": "G:\\Playground"}}

_state = {"started_at": None, "restarts": 0, "child_ok": False,
          "ops_total": 0, "last_op": None, "last_error": None}


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().isoformat(timespec="seconds"), msg)
    print(line, flush=True)


def save_status():
    with _status_lock:
        os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATUS_PATH)


# I-208 缺陷二：turn 取消/终止事件 → 持久恢复记录
CANCEL_DIR = os.environ.get("LP_TURN_RECOVERY_ROOT", os.path.join(ROOT, "lp_sync", "turn_recovery"))


def _capture_turn_event(msg, context=None):
    """从协议事件流识别 turn 取消/终止，持久保存已核实关联与断点。

    识别信号：事件携带 error 且含 cancel/stop 字样、或 method 含
    turn.failed / turn.cancelled。记录包含 sessionId（能取到时）、
    原始事件摘要与本机时间；对账状态先记 unknown（不猜测完成）。
    恢复请求由 kernel 的 reconcile 在下一轮 tick 处理（原 session
    自动续一次有效运行；对账不明则出 reviewer 通知）。"""
    try:
        method = str(msg.get("method") or "")
        params = msg.get("params") or {}
        event_type = params.get("type") or method
        payload = params.get("payload") or {}
        text = json.dumps(msg, ensure_ascii=False)
        is_cancel = (("cancel" in event_type.lower() or
                      "turn.failed" in event_type) or
                     (msg.get("error") and
                      any(w in str(msg.get("error", "")).lower()
                          for w in ("cancel", "stopping"))))
        if not is_cancel:
            return
        context = context or {}
        sid = params.get("sessionId") or params.get("session_id") or context.get("session_id")
        if not sid:
            m = re.search(r"sess_[0-9a-f-]{36}", text)
            sid = m.group(0) if m else None
        os.makedirs(CANCEL_DIR, exist_ok=True)
        owned = EXECUTION_STORE.session(sid) if sid else {}
        if (owned or {}).get("phase") in ("stopping", "stopped", "retired"):
            return
        rec = {**(owned or {}), **context, "captured_at": datetime.datetime.now().isoformat(
                   timespec="milliseconds"),
               "session_id": sid,
               "method": event_type[:80],
               "detail_head": text[:300],
               "reconcile": "unknown"}
        error = payload.get("error") or msg.get("error") or {}
        if isinstance(error, dict):
            summary = str(error.get("message") or error.get("underlyingErrorMessage") or "")[:800]
            attribution = error.get("attribution") or {}
            rec["error_summary"] = summary
            if "Select a model before continuing" in summary or "Reasoning level is required" in summary:
                rec["failure_category"] = "configuration"
            if attribution.get("reason") == "rate_limited" or "Usage limit" in summary or "1308" in summary:
                rec["failure_category"] = "rate_limited"
                match = re.search(r"reset at\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", summary)
                rec["retry_at"] = match.group(1).replace(" ", "T") if match else (
                      datetime.datetime.now() + datetime.timedelta(seconds=60)).isoformat(timespec="seconds")
        safe_ts = rec["captured_at"].replace(":", "").replace("-", "")[:17]
        name = "tr-%s-%s-%s.json" % (sid[:18] if sid else "no_sid", safe_ts, uuid.uuid4().hex[:12])
        tmp = os.path.join(CANCEL_DIR, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(CANCEL_DIR, name))
        if owned:
            EXECUTION_STORE.bind(sid, owned["executor_key"], phase="interrupted",
                                  failure_category=rec.get("failure_category"), retry_at=rec.get("retry_at"))
        log("turn 取消事件捕获：%s" % name)
    except Exception as e:
        _state["last_error"] = repr(e)


class ProtocolChild:
    """长活协议子进程：请求-应答 + 服务端反向请求自动应答。"""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.buf = ""
        self.waiters = {}
        self.next_id = 1
        self.contexts = {}
        self.executor_key = None
        self.exit_recorded = False
        self.loaded_sessions = set()
        self.model_bound_sessions = set()
        self.spawn()

    def spawn(self):
        os.makedirs(RUNTIME_ROOT, exist_ok=True)
        errf = open(os.path.join(RUNTIME_ROOT, "child_stderr.log"), "ab")
        # I-208：子进程无窗口（CREATE_NO_WINDOW），stdout 重定向文件，
        # 不依赖任何控制台；生产宿主经 pythonw 无控制台运行时子进程
        # 也不再弹黑窗。
        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            ["node", ZC, "app-server"], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=errf, env=dict(os.environ),
            creationflags=creationflags)
        self.buf = ""
        threading.Thread(target=self._pump, daemon=True).start()
        time.sleep(2.5)

    def _pump(self):
        p = self.proc
        try:
            for chunk in iter(lambda: p.stdout.readline(), b""):
                self.buf += chunk.decode("utf-8", "replace")
                while "\n" in self.buf:
                    line, self.buf = self.buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("id") and msg.get("method") \
                            and str(msg["id"]).startswith("server-"):
                        result = ({"nativeSearchEnhancementsEnabled": False}
                                  if msg["method"] == "session/requestRuntimePreferences" else {})
                        try:
                            with self.lock:
                                p.stdin.write((json.dumps({"id": msg["id"], "result": result}) + "\n").encode())
                                p.stdin.flush()
                        except Exception:
                            pass
                        continue
                    context = self.contexts.pop(msg.get("id"), None) or {}
                    w = self.waiters.pop(msg.get("id"), None)
                    if w:
                        w(msg)
                    # I-208 缺陷二：协议事件消费处收取 turn 终止/取消，
                    # 写持久恢复记录（不依赖看护线程重建子进程）。
                    _capture_turn_event(msg, context)
                    event = msg.get("params") or {}
                    if msg.get("method") == "session/event" and event.get("type") == "turn.completed":
                        sid = event.get("sessionId")
                        owned = EXECUTION_STORE.session(sid) if sid else None
                        if owned and owned.get("executor_key") == self.executor_key:
                            EXECUTION_STORE.bind(sid, self.executor_key, phase="idle")
                    if msg.get("method") == "session/event" and event.get("type") == "turn.failed":
                        sid = event.get("sessionId")
                        owned = EXECUTION_STORE.session(sid) if sid else None
                        if owned and owned.get("phase") == "stopping":
                            EXECUTION_STORE.bind(sid, self.executor_key, phase="stopped")
        except Exception:
            pass
        finally:
            self.capture_exit()

    def capture_exit(self):
        if self.exit_recorded or not self.executor_key or self.proc.poll() is None:
            return
        self.exit_recorded = True
        for sid, owned in EXECUTION_STORE.sessions().items():
            if owned.get("executor_key") == self.executor_key and owned.get("phase") == "active" \
                    and owned.get("executor_pid") == self.proc.pid:
                _capture_turn_event({"method": "turn.cancelled", "params": {"sessionId": sid},
                                     "error": "executor process exited"},
                                    {"executor_key": self.executor_key,
                                     "executor_pid": self.proc.pid,
                                     "exit_code": self.proc.poll(), "event_kind": "child_exit"})

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def call(self, method, params, timeout=120):
        with self.lock:
            if not self.alive():
                raise RuntimeError("子进程已死")
            mid = "svc-%d" % self.next_id
            self.next_id += 1
            box = {}
            def done(m):
                box["m"] = m
            self.waiters[mid] = done
            self.contexts[mid] = {"session_id": params.get("sessionId"),
                                  "executor_key": self.executor_key,
                                  "executor_pid": self.proc.pid, "rpc_method": method}
            self.proc.stdin.write((json.dumps(
                {"id": mid, "method": method, "params": params}) + "\n").encode())
            self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline and "m" not in box:
            time.sleep(0.2)
        self.waiters.pop(mid, None)
        self.contexts.pop(mid, None)
        if "m" not in box:
            raise RuntimeError("调用超时 %s" % method)
        return box["m"]

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass


CHILD = None

# I-175 派发请求文件队列：指挥部写文件即自动派活的轮询线程。
import dispatch_file_queue

QUEUE_WORKER = dispatch_file_queue.QueueWorker()


def queue_thread():
    """I-175 队列线程：与看护线程同级常驻，两轮心跳内认领新请求。"""
    try:
        QUEUE_WORKER.run()
    except Exception as e:
        log("派发队列线程异常退出：%r" % e)


_child_lock = threading.Lock()
# I-208 判据二：每条 L 独立执行器（按执行键池化；默认键空串走共享池
# 保持向后兼容，隔离测试与每线模式按路线指定独立键）。
_CHILDREN = {}


def ensure_child(exec_key=""):
    """拉起协议子进程（I-178 返修加互斥；I-208 每执行键独立实例）。

    看护线程与套接字处理线程可能同时发现子进程死亡并并发拉起，
    互斥加二次检查保证同一执行键任何时刻只有一个子进程实例。"""
    if not exec_key:
        raise ValueError("必须指定执行器归属；生产不再使用共享执行器")
    with _child_lock:
        ch = _CHILDREN.get(exec_key)
        if ch is None or not ch.alive():
            ch = ProtocolChild()
            ch.executor_key = exec_key
            _CHILDREN[exec_key] = ch
            _state["restarts"] += 1
            _state["child_ok"] = True
            log("独立执行器拉起（键 %s，第 %d 次）" % (exec_key,
                                                       _state["restarts"]))
            save_status()
        return ch


def watcher():
    while True:
        time.sleep(3)
        try:
            # I-208：看护每线执行器池（死亡即清键重拉）。
            for key, ch in list(_CHILDREN.items()):
                if not ch.alive():
                    ch.capture_exit()
                    log("看护：执行器 %s 死亡，自愈拉起" % key)
                    with _child_lock:
                        if _CHILDREN.get(key) is ch:
                            _CHILDREN.pop(key, None)
                    ensure_child(key)
        except Exception as e:
            _state["last_error"] = repr(e)


def sid_of(r):
    import re
    if r.get("result", {}).get("sessionId"):
        return r["result"]["sessionId"]
    m = re.search(r"sess_[0-9a-f-]{36}", json.dumps(r))
    return m.group(0) if m else None


def _exec_key_for_sid(sid):
    """I-208/I-217：按已核实驻留映射把 sid 解析到固定执行键。

    顺序：执行库已绑定键 → 工位注册表（I-217：工位会话属工位键，即
    独立执行器）→ 线 roster（单工位兼容）；都不在则拒绝，不另建执行
    器接管未知会话。"""
    owned = EXECUTION_STORE.session(sid)
    if owned:
        return owned["executor_key"]
    worker_id = lw.worker_by_session(sid)
    if worker_id:
        EXECUTION_STORE.bind(sid, worker_id, phase="idle")
        return worker_id
    roster = roster_load()
    for lane, bound_sid in roster.items():
        if bound_sid == sid and lane:
            EXECUTION_STORE.bind(sid, lane, lane=lane, phase="idle")
            return lane
    raise ValueError("会话归属未知，禁止另建执行器接管：%s" % sid)


def _readback_hit(sid, marker, rounds=36, gap=5, exec_key=None):
    """服务侧回读核验：派发后确认对方真的收到并执行。

    exec_key 由首发方显式传入（同 session 全程固定键，Beth 15:48
    复核缺陷一）；缺省才按 roster 反查。"""
    rk = exec_key if exec_key is not None else _exec_key_for_sid(sid)
    ch = ensure_child(rk)
    if sid not in ch.loaded_sessions:
        resumed = ch.call("session/resume", {"sessionId": sid})
        if resumed.get("error"):
            return False
        ch.loaded_sessions.add(sid)
    for _ in range(rounds):
        time.sleep(gap)
        try:
            rb = ch.call("session/messages", {"sessionId": sid}, timeout=40)
            if marker in json.dumps(rb.get("result", {}).get("messages", []), ensure_ascii=False):
                return True
        except Exception:
            ch = ensure_child(rk)
    return False


REGISTRY_PATH = os.environ.get("LP_HOST_REGISTRY_PATH", os.path.join(ROOT, "lp_sync", "dispatch_registry.json"))


def roster_load():
    if not os.path.exists(DISPATCH_ROSTER_PATH):
        return {}
    with open(DISPATCH_ROSTER_PATH, encoding="utf-8") as f:
        return json.load(f)


def roster_save(roster):
    tmp = DISPATCH_ROSTER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(roster, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DISPATCH_ROSTER_PATH)


def roster_resolve(lane, kind="task"):
    """I-157 三通道判定：返回 (会话号或 None, 通道名)。

    task 常规任务：该队有常驻会话则复用（通道 resume），无则首发开
    新会话并入驻（通道 new）；test 测试派发：一律走 test 通道，开
    临时会话且不入驻、不登记索引、不进派发登记册。"""
    roster = roster_load()
    if kind == "test":
        return None, "test"
    sid = roster.get(lane or "")
    return (sid, "resume") if sid else (None, "new")


def roster_bind(lane, sid):
    with _roster_lock:
        roster = roster_load()
        roster[lane or ""] = sid
        roster_save(roster)


def registry_record(sid, title, item_id=None, lane=None, origin="dispatch"):
    """I-143 派发三元组登记：会话号、路线、事项号落 dispatch_registry.json。

    采集代理以上行会话快照的队伍与事项映射取自这里；历史派发由
    backfill 脚本按 logs/dispatch_session/ 回填并标 origin=backfill。"""
    try:
        reg = json.load(open(REGISTRY_PATH, encoding="utf-8")) \
            if os.path.exists(REGISTRY_PATH) else {}
        reg[sid] = {"item_id": item_id, "lane": lane,
                    "title_head": (title or "")[:40],
                    "origin": origin,
                    "dispatched_at": datetime.datetime.now().isoformat(timespec="seconds")}
        tmp = REGISTRY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, REGISTRY_PATH)
    except Exception as e:
        log("派发登记失败 %s：%r" % (sid[:18], e))


def _send_once(request_id, sid, message, verify, channel, key, resume=False,
               item_id=None, run_id=None):
    record = EXECUTION_STORE.request(request_id)
    marker = "【派发请求号】" + request_id
    if record["phase"] != "prepared":
        result = record.get("result") or {"session": sid, "accepted": True,
                  "readback_verified": False, "uncertain": True}
        if record["phase"] in ("sending", "uncertain") and verify:
            hit = _readback_hit(sid, marker, rounds=3, gap=1, exec_key=key)
            if hit:
                result = {**result, "accepted": True, "readback_verified": True,
                          "uncertain": False}
                EXECUTION_STORE.update(request_id, phase="verified", result=result)
        return {**result, "request_id": request_id, "reconciled": True}
    child = ensure_child(key)
    prior_owned = EXECUTION_STORE.session(sid)
    EXECUTION_STORE.bind(sid, key, item_id=item_id, run_id=run_id,
                         request_id=request_id, executor_pid=child.proc.pid, phase="active")
    if resume and sid not in child.loaded_sessions:
        res = child.call("session/resume", {"sessionId": sid})
        if res.get("error"):
            EXECUTION_STORE.restore_session(sid, prior_owned)
            return {"session": sid, "accepted": False, "safe_to_retry": True,
                    "error": json.dumps(res["error"], ensure_ascii=False)[:300]}
        child.loaded_sessions.add(sid)
    if sid not in getattr(child, "model_bound_sessions", set()):
        _bind_session_model(child, sid)
    EXECUTION_STORE.update(request_id, phase="sending", session_id=sid,
                           executor_key=key)
    subscription = child.call("session/subscribe", {"sessionId": sid,
                              "deliveryKind": "desktop-continuous"})
    if subscription.get("error"):
        EXECUTION_STORE.update(request_id, phase="prepared")
        EXECUTION_STORE.restore_session(sid, prior_owned)
        return {"session": sid, "accepted": False, "safe_to_retry": True,
                "error": subscription["error"]}
    try:
        sent = child.call("session/send", {"sessionId": sid,
                         "content": message + "\n\n" + marker, "inputId": request_id})
    except Exception as exc:
        result = {"session": sid, "channel": channel, "accepted": None,
                  "readback_verified": False, "uncertain": True,
                  "request_id": request_id, "error": repr(exc)}
        EXECUTION_STORE.update(request_id, phase="uncertain", result=result)
        return result
    accepted = bool(sent.get("result", {}).get("accepted"))
    hit = _readback_hit(sid, marker, rounds=12, gap=1, exec_key=key) if verify and accepted else None
    result = {"session": sid, "channel": channel, "accepted": accepted,
              "readback_verified": hit, "request_id": request_id,
              "uncertain": accepted and verify and not hit}
    if sent.get("error"):
        if sent["error"].get("code") == -32010:
            EXECUTION_STORE.restore_session(sid, prior_owned)
            EXECUTION_STORE.bind(sid, key, executor_pid=child.proc.pid, phase="active")
            result.update(uncertain=True, busy_wait=True, error=sent["error"],
                          prior_request_id=(prior_owned or {}).get("request_id"),
                          prior_run_id=(prior_owned or {}).get("run_id"))
            EXECUTION_STORE.update(request_id, phase="busy_wait", result=result)
            return result
        result["error"] = sent["error"]
        result["safe_to_retry"] = True
        EXECUTION_STORE.update(request_id, phase="prepared", result=result)
        EXECUTION_STORE.restore_session(sid, prior_owned)
        return result
    EXECUTION_STORE.update(request_id, phase="verified" if hit else (
                           "accepted" if accepted and not verify else "uncertain"), result=result)
    return result


def create_persistent_session(child, title, request_id):
    # Empty sessions are not persisted by the installed SDK until their first
    # history mutation. Use its supported shared-context import, containing
    # only the supervisor's actual initialization record, before any task send.
    markdown = "# 编排器会话初始化\n\n创建请求：%s\n此时尚未执行任何任务。" % request_id
    digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    context = {"source": "sharedContext", "title": title or "编排器运行会话", "markdown": markdown,
               "provenance": {"shareId": "orchestrator-" + request_id,
                              "contextId": "initialization-" + request_id,
                              "projectionSha256": digest,
                              "artifactSetSha256": hashlib.sha256(b"[]").hexdigest(),
                              "formatterVersion": 1, "markdownSha256": digest,
                              "installedArtifacts": []}}
    model = _inherited_model(lane=(child.executor_key or "").removeprefix("test-"))
    params = {**WS, "persistence": "immediate", "titleGenerationEnabled": False, "importedHistory": context}
    if model:
        params["model"] = model
    created = child.call("session/create", params)
    sid = sid_of(created)
    if sid and model:
        _bind_session_model(child, sid, model)
    return created


def _inherited_model(sid=None, lane=None):
    """Use the session's persisted explicit choice, then the verified operator default."""
    if not sid and lane:
        sid = roster_load().get(lane)
    if sid:
        import sqlite3
        from contextlib import closing
        path = os.path.join(HOME, ".zcode", "cli", "db", "db.sqlite")
        if os.path.exists(path):
            with closing(sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)) as con:
                rows = con.execute("SELECT data FROM message WHERE session_id=? ORDER BY time_created DESC LIMIT 50", (sid,)).fetchall()
                for row in rows:
                    model = json.loads(row[0]).get("modelSelection")
                    if model and model.get("providerId") and model.get("modelId"):
                        if model.get("options", {}).get("reasoningLevel"):
                            return model
                        sparse = model
                        break
    path = os.environ.get("LP_EXECUTOR_DEFAULTS", os.path.join(os.path.dirname(__file__), "executor_defaults.json"))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as stream:
            model = json.load(stream).get("model")
        if model and model.get("providerId") and model.get("modelId"):
            if 'sparse' in locals() and any(sparse.get(k) != model.get(k) for k in ("providerId", "modelId")):
                raise RuntimeError("persisted model lacks reasoning level; verified default belongs to another model")
            return model
    return None


def _bind_session_model(child, sid, model=None):
    model = model or _inherited_model(sid=sid)
    if model:
        response = child.call("session/setModel", {"sessionId": sid, "model": model,
                              "persistAsWorkspaceLastUsed": False})
        if response.get("error"):
            raise RuntimeError("cannot inherit configured session model: " + str(response["error"]))
        if not hasattr(child, "model_bound_sessions"):
            child.model_bound_sessions = set()
        child.model_bound_sessions.add(sid)


def op_dispatch_new(title, message, verify=True, item_id=None, lane=None,
                    kind="task", request_id=None, run_id=None, force_new=False,
                    handoff_from=None, worker_id=None):
    if kind != "test" and not lane:
        raise ValueError("生产派发必须指定路线")
    request_id = request_id or "direct-" + uuid.uuid4().hex
    # I-217：带 worker_id 的派发走工位通道——执行键即工位号（独立执行
    # 器），会话驻留在工位注册表；无 worker_id 时保持原单工位行为。
    key = worker_id or (lane if kind != "test" else "test-" + (lane or "adhoc"))
    with operation_lock(key):
        prior, fresh = EXECUTION_STORE.begin(request_id, {"op": "dispatch_new",
                    "title": title, "message": message, "lane": lane,
                    "kind": kind, "item_id": item_id, "run_id": run_id,
                    "force_new": force_new, "handoff_from": handoff_from,
                    "worker_id": worker_id})
        sid = prior.get("session_id")
        channel = prior.get("channel")
        if not sid:
            if worker_id:
                # 工位会话：首发前查工位注册表复用；force_new（交接）必须
                # 新开会话，不复用旧驻留。无 worker_id 保持原三通道判定。
                if not force_new:
                    sid = (lw.worker_lookup(worker_id) or {}).get("session_id")
                channel = "resume" if sid else "new"
            elif force_new:
                sid, channel = None, "new"
            else:
                sid, channel = roster_resolve(lane, kind)
            if sid:
                key = _exec_key_for_sid(sid)
            else:
                child = ensure_child(key)
                created = create_persistent_session(child, (item_id + " " if item_id else "") + title, request_id)
                sid = sid_of(created)
                if not sid:
                    raise RuntimeError("session/create did not return a session")
                child.loaded_sessions.add(sid)
            if not EXECUTION_STORE.session(sid):
                EXECUTION_STORE.bind(sid, key, lane=lane, item_id=item_id,
                                     run_id=run_id, phase="idle", worker_id=worker_id)
            EXECUTION_STORE.update(request_id, session_id=sid, executor_key=key, channel=channel)
            if worker_id:
                lw.worker_bind(worker_id, lane, session_id=sid, phase="active",
                               item_id=item_id, run_id=run_id)
            elif kind != "test" and not force_new:
                roster_bind(lane, sid)
        else:
            key = prior["executor_key"]
        content = title + "\n\n" + message
        if item_id and kind != "test":
            content += "\n\n【会话归属】session_id=" + sid + "；事项=" + item_id
        if worker_id:
            content += "\n【执行工位】" + worker_id + "（独立会话与执行器，勿跨工位改共享文件）"
        if force_new and item_id and handoff_from:
            _rebind_claim(item_id, handoff_from, sid)
        if kind != "test" and item_id and not force_new and EXECUTION_STORE.request(request_id)["phase"] == "prepared":
            _claim_for_execution(item_id, lane, sid, worker_id=worker_id)
            content += "\n内核已按本路线认领人登记认领，核对后执行，不重复认领。"
        result = _send_once(request_id, sid, content, verify, channel, key,
                            resume=channel == "resume", item_id=item_id, run_id=run_id)
        if kind != "test":
            registry_record(sid, title, item_id, lane)
        if result.get("readback_verified") and kind != "test":
            try:
                result["index_register"] = op_index_register(sid, title)
            except Exception as exc:
                result["index_register"] = {"registered": False, "reason": repr(exc)}
        return result


def _claim_for_execution(item_id, lane, sid, worker_id=None):
    import dispatch_file_queue as dq
    registry = dq._registry_dir()
    if registry not in sys.path:
        sys.path.insert(0, registry)
    import dispatch as registry_dispatch
    item = dq._registry_item(item_id)
    if item is None:
        raise ValueError("正式事项不存在")
    worker = lane + "队"
    if item.get("status") == "open":
        import cloud_client
        if cloud_client.enabled():
            return cloud_client.request("POST", "/api/dispatch/claim",
                      {"item_id": item_id, "worker": worker, "session_id": sid,
                       "lease_hours": 4, "worker_id": worker_id})
        return registry_dispatch.claim(item_id, worker, session_id=sid)
    if item.get("status") == "in_progress" and item.get("claimed_by") == worker:
        if item.get("claim_session_id") not in (None, sid):
            raise ValueError("事项认领会话不一致")
        return {"reconciled": True}
    raise ValueError("事项当前不允许执行：%s" % item.get("status"))


def _rebind_claim(item_id, old_sid, new_sid):
    import dispatch_file_queue as dq
    registry = dq._registry_dir()
    if registry not in sys.path:
        sys.path.insert(0, registry)
    import cloud_client
    if cloud_client.enabled():
        return cloud_client.request("POST", "/api/dispatch/rebind_session",
               {"item_id": item_id, "old_session_id": old_sid, "session_id": new_sid})
    import dispatch as registry_dispatch
    with registry_dispatch._lock():
        obj = registry_dispatch._load(registry_dispatch.ITEMS_PATH)
        item = next(i for i in obj["items"] if i["id"] == item_id)
        if item.get("claim_session_id") == new_sid:
            return
        if item.get("status") != "in_progress" or item.get("claim_session_id") != old_sid:
            raise ValueError("原认领会话不一致，保留交接待核验")
        item["claim_session_id"] = new_sid
        registry_dispatch._save(registry_dispatch.ITEMS_PATH, obj)


def op_dispatch_resume(sid, message, verify=True, request_id=None, item_id=None, run_id=None):
    key = _exec_key_for_sid(sid)
    if EXECUTION_STORE.session(sid).get("phase") == "retired":
        raise ValueError("原会话已交接，禁止重新执行；请读取其 successor")
    request_id = request_id or "direct-" + uuid.uuid4().hex
    with operation_lock(key):
        EXECUTION_STORE.begin(request_id, {"op": "dispatch_resume", "sid": sid,
                              "message": message, "item_id": item_id, "run_id": run_id})
        EXECUTION_STORE.update(request_id, session_id=sid, executor_key=key, channel="resume")
        return _send_once(request_id, sid, message, verify, "resume", key,
                          resume=True, item_id=item_id, run_id=run_id)


def op_reconcile(request_id):
    rec = EXECUTION_STORE.request(request_id)
    if not rec or not rec.get("session_id"):
        return {"ok": False, "uncertain": True, "error": "无持久派发关联，保持待核验"}
    sid, key = rec["session_id"], rec["executor_key"]
    with operation_lock(key):
        result = rec.get("result") or {"session": sid, "accepted": None}
        if rec.get("phase") == "verified":
            return result
        if rec.get("phase") == "busy_wait":
            prior = EXECUTION_STORE.request(result.get("prior_request_id")) or {}
            same_run = (result.get("prior_run_id") and result["prior_run_id"] == rec.get("payload", {}).get("run_id"))
            if same_run and prior.get("session_id") == sid and _readback_hit(
                    sid, "【派发请求号】" + prior["request_id"], rounds=1, gap=0, exec_key=key):
                result.update(accepted=True, readback_verified=True, uncertain=False,
                              reconciled_existing_run=True, existing_request_id=prior["request_id"])
                EXECUTION_STORE.update(request_id,phase="verified",result=result)
            elif (EXECUTION_STORE.session(sid) or {}).get("phase") == "idle":
                # The SDK explicitly rejected this input as busy. A recorded
                # turn.completed makes retry of that unaccepted input safe.
                EXECUTION_STORE.update(request_id,phase="prepared",busy_released_at=datetime.datetime.now().isoformat())
                return {**result,"ready_to_retry":True,"definitive_rejection":True}
            return result
        hit = _readback_hit(sid, "【派发请求号】" + request_id, rounds=1, gap=0, exec_key=key)
        if hit:
            result = {**result, "session": sid, "accepted": True,
                      "readback_verified": True, "uncertain": False, "reconciled": True}
            EXECUTION_STORE.update(request_id, phase="verified", result=result)
        return result


def stop_session(sid):
    key = _exec_key_for_sid(sid)
    with operation_lock(key):
        child = ensure_child(key)
        owned = EXECUTION_STORE.session(sid)
        if sid not in child.loaded_sessions:
            response = child.call("session/resume", {"sessionId": sid})
            if response.get("error"):
                return {"ok": False, "error": response["error"]}
            child.loaded_sessions.add(sid)
        EXECUTION_STORE.bind(sid, key, phase="stopping")
        response = child.call("session/stop", {"sessionId": sid})
        if response.get("error"):
            EXECUTION_STORE.restore_session(sid, owned)
            return {"ok": False, "error": response["error"]}
        if owned.get("phase") != "active" or owned.get("executor_pid") != child.proc.pid:
            EXECUTION_STORE.bind(sid, key, phase="stopped")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if EXECUTION_STORE.session(sid).get("phase") == "stopped":
                return {"ok": True, "stopped": sid, "executor_key": key}
            time.sleep(.1)
        return {"ok": False, "pending_reason": "等待原会话停止回执；不启动接力执行"}


def op_handoff(lane, old_sid, title, message, item_id, request_id):
    key = _exec_key_for_sid(old_sid)
    # I-217：工位会话的交接换绑工位注册表；线会话保持原 roster 逻辑。
    # 其他工位与线内其他会话不受影响（判据六）。
    worker_id = lw.worker_by_session(old_sid)
    with operation_lock(key):
        prior, _ = EXECUTION_STORE.begin(request_id, {"op": "handoff", "lane": lane,
                   "old_sid": old_sid, "message": message, "item_id": item_id,
                   "worker_id": worker_id})
        if prior.get("phase") == "completed":
            _persist_handoff_event(request_id, prior["result"])
            return prior["result"]
        if worker_id:
            current = (lw.worker_lookup(worker_id) or {}).get("session_id")
        else:
            current = roster_load().get(lane)
        child_request = EXECUTION_STORE.request(request_id + "-new") or {}
        child_result = child_request.get("result") or {}
        if (current != old_sid and current == child_result.get("session") and
                child_result.get("readback_verified")):
            owned = EXECUTION_STORE.session(old_sid)
            EXECUTION_STORE.bind(old_sid, key, phase="retired", successor=current)
            result = {"ok": True, "switched": True, "old": old_sid, "new": current,
                      "lane": lane, "item_id": item_id, "run_id": owned.get("run_id"),
                      "worker_id": worker_id}
            EXECUTION_STORE.update(request_id, phase="completed", result=result)
            _persist_handoff_event(request_id, result)
            return result
        if current != old_sid:
            return {"ok": False, "switched": False, "reason": "驻留归属已改变，交接不覆盖其他操作"}
        if EXECUTION_STORE.session(old_sid).get("phase") != "stopped":
            stopped = stop_session(old_sid)
            if not stopped.get("ok"):
                EXECUTION_STORE.update(request_id, phase="waiting_old_stop", stop_result=stopped)
                return {"ok": False, "switched": False,
                        "reason": stopped.get("pending_reason") or str(stopped.get("error"))}
        owned = EXECUTION_STORE.session(old_sid)
        new = op_dispatch_new(title, message, item_id=item_id, lane=lane,
                              request_id=request_id + "-new", run_id=owned.get("run_id"),
                              force_new=True, handoff_from=old_sid, worker_id=worker_id)
        if not new.get("readback_verified"):
            EXECUTION_STORE.update(request_id, phase="waiting_new_receipt", new_result=new)
            return {"ok": False, "switched": False, "reason": "新会话回执待对账，保持执行槽占用",
                    "new": new.get("session")}
        if worker_id:
            lw.worker_bind(worker_id, lane, session_id=new["session"], phase="active")
        else:
            with _roster_lock:
                if roster_load().get(lane) != old_sid:
                    return {"ok": False, "switched": False, "reason": "驻留换绑冲突，待对账"}
                roster_bind(lane, new["session"])
        EXECUTION_STORE.bind(old_sid, key, phase="retired", successor=new["session"])
        result = {"ok": True, "switched": True, "old": old_sid, "new": new["session"],
                  "lane": lane, "item_id": item_id, "run_id": owned.get("run_id"),
                  "worker_id": worker_id}
        EXECUTION_STORE.update(request_id, phase="completed", result=result)
        _persist_handoff_event(request_id, result)
        return result


def _persist_handoff_event(request_id, result):
    directory = os.path.join(os.path.dirname(CANCEL_DIR), "handoff_events")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, request_id + ".json")
    tmp = path + "." + uuid.uuid4().hex + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ── I-131 任务索引登记（丙案承载，调查报告第三节防护六条） ────────
INDEX_DB = os.path.join(HOME, ".zcode", "v2", "tasks-index.sqlite")
# 写入器依赖的列集；客户端升级加列不碍事，缺列即形态闸拒绝。
_INDEX_COLUMNS = ("workspace_key", "workspace_path", "task_id", "title",
                  "task_status", "provider", "mode", "created_at", "updated_at",
                  "last_unread_at", "pinned", "archived", "deleted",
                  "title_overridden", "searchable_text", "meta_json")
_META_KEYS = ("taskId", "traceId", "title", "workspacePath", "createdAt",
              "updatedAt", "mode", "provider", "status")


def op_index_register(session_id, title):
    """把派发会话登记进客户端任务索引（I-131）。

    防护落点：其一，只插入不更新不删除，唯一键工作区加任务号冲突
    即视为已登记，幂等；其二，最小列集加 meta_json 九键，形态对齐
    客户端写入器；其三，忙等八秒；其四，状态一律 completed，运行态
    真值在会话库，客户端不跟踪跨进程会话（调查报告证据三）；
    其五，写前形态闸，列集缺项即拒绝，防客户端升级漂移；其六，
    登记动作只在常驻服务内承载，派发脚本不自带直写。"""
    import sqlite3
    import uuid
    if not os.path.exists(INDEX_DB):
        return {"registered": False, "reason": "索引库不存在"}
    con = sqlite3.connect(INDEX_DB, timeout=8.0)
    try:
        con.execute("PRAGMA busy_timeout=8000")
        have = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
        missing = [c for c in _INDEX_COLUMNS if c not in have]
        if missing:
            return {"registered": False, "reason": "形态闸：索引列集缺 %s" % missing}
        now_ms = int(time.time() * 1000)
        meta = {"taskId": session_id, "traceId": str(uuid.uuid4()),
                "title": title, "workspacePath": WS["workspace"]["workspacePath"],
                "createdAt": now_ms, "updatedAt": now_ms,
                "mode": "default", "provider": "glm", "status": "completed"}
        cur = con.execute(
            "INSERT OR IGNORE INTO tasks (workspace_key, workspace_path, task_id, title, "
            "task_status, provider, mode, created_at, updated_at, last_unread_at, "
            "pinned, archived, deleted, title_overridden, searchable_text, meta_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,0,0,0,0,?,?)",
            (WS["workspace"]["workspaceKey"], WS["workspace"]["workspacePath"],
             session_id, title, "completed", "glm", "default",
             now_ms, now_ms, 0, title,
             json.dumps(meta, ensure_ascii=False)))
        con.commit()
        if cur.rowcount == 1:
            # I-131 判据五素材：登记台账，形态自检脚本用它区分我方行与客户端行。
            try:
                led_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "i131_registered.json")
                led = json.load(open(led_path, encoding="utf-8")) if os.path.exists(led_path) else []
                if session_id not in led:
                    led.append(session_id)
                    json.dump(led, open(led_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            except Exception:
                pass
        return {"registered": True, "inserted": cur.rowcount == 1,
                "already_present": cur.rowcount == 0}
    finally:
        con.close()


def handle(line):
    req = json.loads(line)
    op = req.get("op")
    control = os.path.join(os.environ.get("LP_DISPATCH_QUEUE_ROOT", os.path.join(ROOT, "lp_sync", "dispatch_queue")), "control.json")
    try:
        with open(control, encoding="utf-8") as stream:
            halted = json.load(stream).get("mode") == "halt"
    except FileNotFoundError:
        halted = False
    if halted and op in ("dispatch_new", "dispatch_resume", "handoff"):
        return {"ok": False, "error": "维护中，停止新派发并保留现有断点", "safe_to_retry": True}
    _state["ops_total"] += 1
    _state["last_op"] = op
    out = None
    # 派发类操作带一层死子进程重试（子进程随机自毙的构造性防线）。
    for _attempt in (1,):
        try:
            if op == "status":
                out = {"ok": True, "child_alive": any(c.alive() for c in _CHILDREN.values()),
                       "executors": {k: {"pid": c.proc.pid, "alive": c.alive()} for k, c in _CHILDREN.items()},
                       "sessions": EXECUTION_STORE.sessions(), **_state}
            elif op == "maintenance":
                # I-208：maintenance 子命令——mode=halt 停新派发保留断点，
                # mode=run 恢复，mode=status 返回活动摘要。
                import glob as _glob
                mode = req.get("mode", "status")
                ctrl_path = control
                if mode == "status":
                    kern_ctrl = os.path.join(ROOT, "lp_sync", "kernel_state",
                                             "control.json")
                    q = None
                    try:
                        q = json.load(open(ctrl_path, encoding="utf-8"))
                    except (OSError, ValueError):
                        pass
                    ks = None
                    try:
                        ks = json.load(open(kern_ctrl, encoding="utf-8"))
                    except (OSError, ValueError):
                        pass
                    inc = os.path.join(ROOT, "lp_sync", "dispatch_queue",
                                       "incoming")
                    pend = len([f for f in os.listdir(inc)
                                if f.endswith(".json")]) if os.path.isdir(inc) else 0
                    out = {"ok": True, "queue_control": q,
                           "kernel_control": ks, "pending_requests": pend,
                           "child_alive": CHILD is not None and CHILD.alive()}
                elif mode in ("halt", "run"):
                    os.makedirs(os.path.dirname(ctrl_path), exist_ok=True)
                    json.dump({"mode": mode,
                               "changed_at": datetime.datetime.now().isoformat(
                                   timespec="seconds")},
                              open(ctrl_path, "w", encoding="utf-8"),
                              ensure_ascii=False)
                    out = {"ok": True, "mode": mode,
                           "note": "halt 停止新派发并保留断点；run 恢复"}
                else:
                    out = {"ok": False, "error": "mode 只允许 halt、run、status"}
            elif op == "queue_status":
                # I-175：派发请求文件队列状态（目录计数 + 近期台账）。
                out = QUEUE_WORKER.status()
            elif op == "queue_submit":
                # I-175：程序化投递（按约定格式原子写入 incoming/）。
                norm, err = dispatch_file_queue.validate_request(
                    {k: v for k, v in req.items() if k != "op"})
                if err:
                    out = {"ok": False, "error": err}
                else:
                    path = dispatch_file_queue.write_request(norm)
                    out = {"ok": True, "queued": path,
                           "request_id": norm["request_id"]}
            elif op == "dispatch_new":
                out = {"ok": True, **op_dispatch_new(req["title"], req["message"],
                                                    bool(req.get("verify", True)),
                                                    item_id=req.get("item_id"),
                                                    lane=req.get("lane"),
                                                    kind=req.get("kind", "task"), request_id=req.get("request_id"),
                                                    run_id=req.get("run_id"),
                                                    worker_id=req.get("worker_id"))}
            elif op == "dispatch_resume":
                out = {"ok": True, **op_dispatch_resume(req["sid"], req["message"],
                                                        bool(req.get("verify", True)), request_id=req.get("request_id"),
                                                        item_id=req.get("item_id"), run_id=req.get("run_id"))}
            elif op == "handoff":
                out = op_handoff(req["lane"], req["old_sid"], req["title"],
                                  req["message"], req["item_id"], req["request_id"])
            elif op == "request_status":
                out = op_reconcile(req["request_id"])
            elif op == "index_register":
                # I-131：独立登记口，供补登既有会话与验收实验用。
                out = {"ok": True, **op_index_register(req["sid"], req["title"])}
            elif op == "session_stop":
                # I-208：定向停用一个会话的驱动回合（协议 session/stop，
                # 只影响目标会话；不做全局子进程终止）。
                sid = req.get("sid")
                if not sid:
                    out = {"ok": False, "error": "session_stop 需要 sid"}
                    break
                out = stop_session(sid)
            elif op == "kill_child":
                # I-208 判据六：生产禁用 kill_child（自愈测试改走隔离
                # 宿主 + 独立端口与队列）。仅在显式声明隔离模式时可用。
                if os.environ.get("LP_HOST_ISOLATED") != "1":
                    out = {"ok": False,
                           "error": "kill_child 已在生产禁用（I-208）："
                                    "自愈测试请用隔离宿主（LP_HOST_ISOLATED=1"
                                    " 加独立端口）"}
                    break
                key = req.get("executor_key")
                if not key or key not in _CHILDREN:
                    out = {"ok": False, "error": "隔离演练必须指定自建执行器键"}
                    break
                child = _CHILDREN[key]
                if req.get("graceful"):
                    child.proc.stdin.close()
                    child.proc.wait(timeout=15)
                else:
                    child.kill()
                time.sleep(0.5)
                out = {"ok": True, "killed": True, "graceful":bool(req.get("graceful")), "child_alive_after": child.alive()}
            else:
                out = {"ok": False, "error": "未知 op：%s" % op}
            break
        except Exception as e:
            _state["last_error"] = repr(e)
            out = {"ok": False, "error": repr(e)}
            # 套接字请求异常自愈：此层无 sid 可解析，仅重建共享池；
            # 每线实例由 watcher 看护（I-208）。
    save_status()
    return out


def serve():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind((HOST, PORT))
    srv.listen(8)
    log("常驻宿主服务监听 %s:%d" % (HOST, PORT))
    def connection(conn):
        try:
            f = conn.makefile("rwb", buffering=0)
            line = f.readline()
            out = handle(line.decode("utf-8"))
            f.write((json.dumps(out, ensure_ascii=False) + "\n").encode())
        except Exception as e:
            try:
                conn.sendall((json.dumps({"ok": False, "error": repr(e)}) + "\n").encode())
            except Exception:
                pass
        finally:
            conn.close()
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=connection, args=(conn,), daemon=True).start()


# ---------- I-208：宿主单例锁（任何子进程创建前生效） ----------

def _lock_holder_alive(pid):
    """Windows 只读存活查询：OpenProcess 声明 64 位句柄，查询权限无
    信号。返回 True（确认存活）、False（确认不存在）或 None（权限
    拒绝等未知，不得当死亡处理）。"""
    import ctypes
    if pid <= 0:
        return False
    try:
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p          # 64 位句柄
        k32.OpenProcess.argtypes = [ctypes.c_uint32,       # 访问掩码
                                    ctypes.c_int,          # 继承
                                    ctypes.c_uint32]       # pid
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        k32.GetExitCodeProcess.restype = ctypes.c_int
        h = k32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False if k32.GetLastError() == 87 else None
        try:
            exit_code = ctypes.c_uint32()
            if not k32.GetExitCodeProcess(h, ctypes.byref(exit_code)):
                return None
            return exit_code.value == 259  # STILL_ACTIVE; a retained handle alone is not liveness.
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _host_singleton_lock():
    """宿主单例：端口绑定前先取根目录排他锁文件，第二实例在任何子进程
    创建之前退出。

    I-208 缺陷三修正：存活宿主即使超过 60 秒也不允许接管（年龄不再单
    独作为清理依据）；只有确认持有进程不存在时才清理残留锁；权限未知
    或文件异常一律不接管（保持拒绝）。返回锁句柄或 None。"""
    os.makedirs(RUNTIME_ROOT, exist_ok=True)
    lock_path = os.path.join(RUNTIME_ROOT, ".host_singleton.lock")
    deadline = time.time() + 10
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(),
                                     "ts": time.time()}).encode())
            return fd
        except FileExistsError:
            info = None
            try:
                info = json.load(open(lock_path, encoding="utf-8"))
            except (OSError, ValueError):
                pass
            holder = int((info or {}).get("pid") or 0)
            alive = _lock_holder_alive(holder) if holder else None
            if alive is False:
                # 确认不存在：清理残留后重试（唯一可接管路径）。
                try:
                    os.remove(lock_path)
                    continue
                except OSError:
                    pass
            # alive 为 True（活宿主）或 None（权限未知/文件异常）：拒绝。
            if time.time() > deadline:
                return None
            time.sleep(0.1)


def main():
    _lock_fd = _host_singleton_lock()
    if _lock_fd is None:
        print("[宿主] 单例锁获取失败：另一宿主实例正在运行，本实例退出",
              flush=True)
        return 4
    _state["started_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _state["host_pid"] = os.getpid()
    _state["source_sha256"] = {name: hashlib.sha256(open(os.path.join(
        os.path.dirname(__file__), name), "rb").read()).hexdigest()
        for name in ("host_service.py", "execution_store.py", "dispatch_file_queue.py")}
    # 启动只拉共享池执行器；每线实例在首次使用对应 lane 时按需创建。
    for sid, owned in EXECUTION_STORE.sessions().items():
        if owned.get("phase") == "active":
            pid = owned.get("executor_pid")
            deadline = time.monotonic() + 20
            while pid and _lock_holder_alive(pid) is True and time.monotonic() < deadline:
                time.sleep(.2)
            if pid and _lock_holder_alive(pid) is not False:
                raise RuntimeError("旧执行器仍存活或归属待核验，禁止并行接管")
            _capture_turn_event({"method": "turn.cancelled", "params": {"sessionId": sid},
                                 "error": "host restarted after executor exit"},
                                {"event_kind": "host_restart", "executor_pid": pid})
    for lane, sid in roster_load().items():
        if lane and sid:
            if not EXECUTION_STORE.session(sid):
                EXECUTION_STORE.bind(sid, lane, lane=lane, phase="idle")
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=queue_thread, daemon=True).start()
    serve()


if __name__ == "__main__":
    main()
