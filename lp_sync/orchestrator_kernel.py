# -*- coding: utf-8 -*-
"""orchestrator_kernel.py — I-178 常驻编排内核首版。

形态：独立常驻 Python 进程。每个唤醒轮次（有限 tick）做一遍有界工作：
收件箱收取（经理任务提交与执行器结果）→ 未知派发结果对账 → 就绪任务
派发 → 结果落实（经文件桥代执行账本写）→ 回执写回。正常轮询不调用
模型；内核自身不联网，全部联网动作经宿主服务的文件桥通道执行
（I-175 派发队列与 I-177 账本写桥，共用 lp_sync/dispatch_queue/）。

职责边界（立项准备冻结）：云端账本是任务状态唯一权威；内核本地状态
只保存请求、运行阶段、回执与恢复记录，不另造能独立关闭事项的任务
权威。pending、reserved、session_created、delivered、uncertain 等阶段
属于编排执行记录，不扩展事项总账的 open、in_progress、closed、blocked。

防重复机制：
  1. 派发与账本写请求都用确定性请求号（kd-<任务号>-a<次数>、
     kl-<结果号>-<动作>），重启后同号重交由桥幂等跳过或按台账对账；
  2. 派发前先对账：查 done 结果文件，其次查桥台账 completed 事件，
     再次查在途文件，全无才允许新建，杜绝未知结果直接重发；
  3. 内核任务号与结果号双幂等；同一事项同一时间只允许一个在办内核
     任务与一个有效运行（认领到期也不产生第二个有效运行）；
  4. 单实例锁：绑定 127.0.0.1:58779，第二个实例起不来即退出。

总停开关：state 根下 control.json 的 mode 为 halt 时停止新派发，
在途对账与结果落实继续。

崩溃恢复（验收三的三个终止位置）：
  请求保存后：状态先于副作用持久化，重启后任务仍在 ready 派出；
  消息已发送回执未收：phase=submitted 对账桥台账或结果文件后采纳，
  不重发；
  结果已收状态未更新：结果记录先于回执归档持久化，重启后续交同号
  账本写请求，桥幂等保证不重复写账。

用法：
  python orchestrator_kernel.py --state-root lp_sync/kernel_state
  python orchestrator_kernel.py ... --once          （单轮，调试用）
  python orchestrator_kernel.py --status            （读状态摘要）
  python orchestrator_kernel.py --halt | --run      （拨总停开关）

故障演练挂钩：环境变量 KERNEL_CRASH_POINT 取 after_ingest、
after_dispatch_submit、after_result_ingest 之一，在对应阶段转移后
硬退出（os._exit 99），供演练在精确位置终止内核。
仅使用 Python 标准库。
"""
import datetime
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import dispatch_file_queue as dq
import lane_workers as lw

DEFAULT_STATE_ROOT = os.path.join(_HERE, "kernel_state")
DEFAULT_QUEUE_ROOT = dq.QUEUE_ROOT
KERNEL_PORT = 58779
WAKE_INTERVAL_S = 5.0
MAX_DISPATCH_ATTEMPTS = 2
DISPATCH_RETRY_DELAY_S = 20
HEARTBEAT_EVERY_TICKS = 12          # 约一分钟一条唤醒心跳入台账

_ITEM_RE = re.compile(r"^I-\d{3,4}$")

OUTCOMES = ("closed", "blocked", "evidence_appended", "pending_review")


def _now():
    return datetime.datetime.now()


def _ts():
    return _now().isoformat(timespec="milliseconds")


def _payload_hash(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _bounded_request_id(raw):
    if dq._REQUEST_ID_RE.match(raw):
        return raw
    return raw[:24] + "-" + hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------- I-205 业务线持久有序队列 ----------

def load_lane_queues(root):
    """读取按路线的持久有序队列。

    形态：{lane: {"order": [task_req_id...], "current": id 或 None,
                 "waiting": {id: 原因}}}。文件缺失返回空。"""
    path = os.path.join(root, "lane_queues.json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_lane_queues(root, queues):
    path = os.path.join(root, "lane_queues.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(queues, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def queue_enqueue(queues, lane, task_req_id):
    """入队并去重：重复入队两次有效记录仍为一条。"""
    q = queues.setdefault(lane or "", {"order": [], "current": None,
                                       "waiting": {}})
    if task_req_id not in q["order"] and task_req_id != q.get("current"):
        q["order"].append(task_req_id)
    return q


def queue_view(queues, lane):
    """队列视图：顺序、当前执行、等待数、各等待原因。"""
    q = queues.get(lane or "") or {"order": [], "current": None,
                                   "waiting": {}}
    return {"order": list(q["order"]), "current": q.get("current"),
            "waiting_count": len([v for v in q["order"] if v != q.get("current")]),
            "waiting": dict(q.get("waiting") or {})}


class Kernel:
    def __init__(self, state_root=DEFAULT_STATE_ROOT, queue_root=DEFAULT_QUEUE_ROOT,
                 wake_interval_s=WAKE_INTERVAL_S, port=KERNEL_PORT,
                 line_retry_s=60.0):
        self.state_root = state_root
        self.queue_root = queue_root
        self.wake_interval_s = wake_interval_s
        self.port = port
        self.line_retry_s = line_retry_s
        self.state_path = os.path.join(state_root, "state.json")
        self.inbox = os.path.join(state_root, "inbox")
        self.processed = os.path.join(state_root, "processed")
        self.invalid = os.path.join(state_root, "invalid")
        self.outbox = os.path.join(state_root, "outbox")
        self.sock = None
        self.tick_count = 0
        self._singleton_fd = None
        for d in (self.inbox, self.processed, self.invalid, self.outbox):
            os.makedirs(d, exist_ok=True)
        if not os.path.exists(self.state_path):
            self._save_state({"schema_version": 1, "generation": 0,
                              "control_mode": "run", "tasks": {}, "runs": {},
                              "results": {}, "started_at": _ts(),
                              "last_tick_at": None})

    # ---------- 状态与台账 ----------

    def _load_state(self):
        return json.load(open(self.state_path, encoding="utf-8"))

    def _save_state(self, st):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_path)

    def log(self, event, ref="", detail=None):
        dq.ledger_append(event, request_id=ref, detail=detail,
                         root=self.state_root)

    def ledger_tail(self, limit=50):
        return dq.ledger_events(self.state_root, limit=limit)

    # ---------- 单实例 ----------

    def bind_single_instance(self):
        """双重单实例锁（I-178 返修）：
        其一，状态根目录排他锁文件——不同端口、同一状态根的第二内核
        也必须退出，堵双消费者绕过端口锁的路；
        其二，端口绑定——默认端口的快速互斥。两者任一失败即退出。"""
        lock_path = os.path.join(self.state_root, ".kernel.lock")
        deadline = time.time() + 10
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pid": os.getpid(),
                                         "ts": time.time()}).encode())
                self._singleton_fd = fd
                break
            except FileExistsError:
                try:
                    info = json.load(open(lock_path, encoding="utf-8"))
                    holder = int(info.get("pid") or 0)
                    held = float(info.get("ts") or 0)
                except (OSError, ValueError, TypeError):
                    holder, held = 0, 0

                from host_service import _lock_holder_alive
                # 僵死判定：持有进程已死，或心跳超时。存活持有者每轮刷新
                # 时间戳；被硬杀的持有者停止刷新，后来者最多等一个阈值
                # 即可接管（三倍唤醒周期，下限六秒），不再固定等三十秒。
                stale_after = max(6.0, 3.0 * self.wake_interval_s)
                try:
                    lock_age = time.time() - os.path.getmtime(lock_path)
                except OSError:
                    lock_age = None
                heartbeat_stale = (held and time.time() - held > stale_after) \
                    or (lock_age is not None and lock_age > stale_after)
                if holder and _lock_holder_alive(holder) is False:
                    try:
                        os.remove(lock_path)      # 僵死接管
                        continue
                    except OSError:
                        pass
                if time.time() > deadline:
                    return False
                time.sleep(0.1)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Windows 上 SO_REUSEADDR 允许重复绑定同端口，单实例锁必须
            # 不设该选项，让第二个消费者的 bind 直接失败。
            s.bind(("127.0.0.1", self.port))
            s.listen(1)
            self.sock = s
        except OSError:
            self._release_state_singleton()
            return False
        return True

    def _release_state_singleton(self):
        lock_path = os.path.join(self.state_root, ".kernel.lock")
        if self._singleton_fd is not None:
            try:
                os.close(self._singleton_fd)
            except OSError:
                pass
            self._singleton_fd = None
        try:
            os.remove(lock_path)
        except OSError:
            pass

    def _refresh_singleton_heartbeat(self):
        """每轮刷新单例锁时间戳：长跑持有者不得被后来者误判僵死。"""
        lock_path = os.path.join(self.state_root, ".kernel.lock")
        try:
            with open(lock_path, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "ts": time.time()}, f)
        except OSError:
            pass

    # ---------- 桥查询与投递 ----------

    def bridge_lookup(self, request_id):
        """对账：返回 (state, info)。state 取 completed、failed_final、
        in_flight、uncertain、absent 之一；completed 时 info 含结果字典。"""
        done = os.path.join(self.queue_root, "done", request_id + ".result.json")
        if os.path.exists(done):
            return "completed", json.load(open(done, encoding="utf-8"))
        failed = os.path.join(self.queue_root, "failed", request_id + ".result.json")
        if os.path.exists(failed):
            return "failed_final", json.load(open(failed, encoding="utf-8"))
        # 回执文件被丢弃时，桥台账是对账权威。
        for e in dq.ledger_events(self.queue_root, request_id=request_id):
            if e.get("event") == "completed":
                info = {"reconciled_from": "queue_ledger",
                        "session": e.get("detail", {}).get("session")}
                return "completed", info
            if e.get("event") == "failed_final":
                return "failed_final", {"reconciled_from": "queue_ledger",
                                        "detail": e.get("detail")}
        # I-178 返修：桥侧保持未知的请求（执行已开始无法对账）不可当作
        # 不存在而重交。
        if os.path.exists(os.path.join(self.queue_root, "uncertain",
                                       request_id + ".json")):
            receipt = os.path.join(self.queue_root, "uncertain", request_id + ".result.json")
            try:
                with open(receipt,encoding="utf-8") as stream:
                    return "uncertain", json.load(stream)
            except (OSError,ValueError):
                return "uncertain", {"where": "uncertain"}
        for sub in ("incoming", "processing", "retry"):
            if os.path.exists(os.path.join(self.queue_root, sub, request_id + ".json")):
                return "in_flight", {"where": sub}
        return "absent", None

    def bridge_submit(self, request):
        """向桥投递请求文件（原子写入）。已存在同号在途文件视为已递交。"""
        _, error = dq.validate_request(request)
        if error:
            raise ValueError("文件桥拒绝请求：" + error)
        try:
            dq.write_request(request, root=self.queue_root)
            return "submitted"
        except ValueError:
            return "already_present"

    # ---------- 收件箱 ----------

    def ingest_inbox(self, st):
        names = sorted(os.listdir(self.inbox))
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.inbox, name)
            try:
                obj = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError) as e:
                dst = os.path.join(self.invalid, name)
                os.replace(path, dst)
                self.log("invalid_inbox_file", ref=name, detail={"error": str(e)})
                continue
            kind = obj.get("kind")
            if kind == "task_submit":
                self._ingest_task(st, obj, name, path)
            elif kind == "result_submit":
                self._ingest_result(st, obj, name, path)
            else:
                dst = os.path.join(self.invalid, name)
                os.replace(path, dst)
                self.log("invalid_inbox_file", ref=name,
                         detail={"error": "kind 必须为 task_submit 或 result_submit"})

    def _ingest_task(self, st, obj, name, path):
        err = self._validate_task(obj)
        rid = str(obj.get("request_id") or name[:-5])
        if err:
            os.replace(path, os.path.join(self.invalid, name))
            self.log("task_rejected", ref=rid, detail={"error": err})
            return
        if rid in st["tasks"]:
            stored = st["tasks"][rid]
            if stored.get("payload_hash") == _payload_hash(obj):
                os.replace(path, os.path.join(self.processed, name))
                self.log("duplicate_task_skipped", ref=rid,
                         detail={"reason": "任务号已存在且正文一致"})
                return
            # I-178 返修：同号不同文是内容冲突，显式拒绝并留痕，不得
            # 静默当作重复跳过。
            os.replace(path, os.path.join(self.invalid, name))
            self.log("task_conflict_rejected", ref=rid,
                     detail={"reason": "任务号已存在且正文不一致（载荷哈希冲突）",
                             "stored_hash": stored.get("payload_hash"),
                             "incoming_hash": _payload_hash(obj)})
            return
        busy = [t for t in st["tasks"].values()
                if t["item_id"] == obj["item_id"] and t["phase"] not in
                ("completed", "dispatch_failed", "result_failed", "retired")]
        if busy:
            os.replace(path, os.path.join(self.invalid, name))
            self.log("item_busy_rejected", ref=rid,
                     detail={"item_id": obj["item_id"],
                             "existing_task": busy[0]["task_req_id"],
                             "reason": "同一事项已有在办内核任务"})
            return
        task = {"task_req_id": rid, "item_id": obj["item_id"],
                "lane": obj.get("lane"), "title": obj.get("title") or "内核派发任务",
                "message": obj["message"], "approved_by": obj["approved_by"],
                "payload_hash": _payload_hash(obj),
                "kind": obj.get("task_kind", "task"),
                "created_at": _ts(), "phase": "ready",
                "dispatch_attempts": 0,
                "max_dispatch_attempts": int(obj.get("max_dispatch_attempts",
                                                     MAX_DISPATCH_ATTEMPTS)),
                "dispatch_request_id": None, "run_id": None, "session_id": None,
                "worker_id": None,
                "writable_paths": obj.get("writable_paths") or [],
                "exclusive_resources": obj.get("exclusive_resources") or [],
                "last_error": None}
        task["reviewer"] = obj.get("reviewer")
        st["tasks"][rid] = task
        queues = load_lane_queues(self.state_root)
        queue_enqueue(queues, task.get("lane"), rid)
        save_lane_queues(self.state_root, queues)
        self._save_state(st)                    # 先持久化再归档（崩溃零丢失）
        os.replace(path, os.path.join(self.processed, name))
        self.log("task_received", ref=rid,
                 detail={"item_id": task["item_id"], "lane": task["lane"],
                         "approved_by": task["approved_by"], "kind": task["kind"]})
        self._maybe_crash("after_ingest")

    def _validate_task(self, obj):
        if obj.get("schema_version") != 1:
            return "schema_version 必须为 1"
        rid = obj.get("request_id")
        if not rid or not dq._REQUEST_ID_RE.match(str(rid)):
            return "request_id 缺失或非法"
        if not _ITEM_RE.match(str(obj.get("item_id") or "")):
            return "item_id 必须为 I-xxx 形态"
        if not isinstance(obj.get("message"), str) or not obj["message"].strip():
            return "message 必须为非空字符串"
        if not obj.get("approved_by"):
            return "approved_by 必填（批准人）"
        # 收件箱顶层 kind 是提交类型（task_submit/result_submit）；
        # 派发通道类型用 task_kind 字段（task 或 test）。两者同名会让
        # 全部任务提交被拒。首版内核不读云端，批准由 approved_by 声明。
        if obj.get("task_kind", "task") not in ("task", "test"):
            return "task_kind 只允许 task 或 test"
        # I-217 并行声明：可写范围与独占资源（可选；缺省不能并行准入）
        for key in ("writable_paths", "exclusive_resources"):
            val = obj.get(key)
            if val is None:
                continue
            if (not isinstance(val, list) or
                    not all(isinstance(x, str) and x.strip() for x in val)):
                return "%s 必须是非空字符串列表" % key
        return ""

    # ---------- 对账（先于一切新动作） ----------

    def reconcile_turn_recovery(self, st):
        """I-208 缺陷二：处理宿主捕获的 turn 取消记录。

        对账三态：一、事项已 closed（结果已入账）→ 记已完成不重做；
        二、运行在内核 state 仍 active 且 session 可续 → 把对应任务
        退回 submitted（下一轮按同号请求对账后自动续原 session，一次
        有效运行）；三、无关联或状态不明 → 记 pending_decision 并产
        reviewer 通知，不猜测完成。"""
        import glob as _glob
        tr_dir = os.path.join(os.path.dirname(self.state_root),
                              "turn_recovery")
        if not os.path.isdir(tr_dir):
            return
        for path in _glob.glob(os.path.join(tr_dir, "tr-*.json")):
            try:
                rec = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("reconcile") not in ("unknown", "retry_wait"):
                continue
            if rec.get("reconcile") == "retry_wait" and rec.get("retry_at", "") > _ts():
                continue
            sid = rec.get("session_id")
            # Match the persisted run first, including ended runs. A late
            # cancellation must never reopen work with a received result.
            run = st.get("runs", {}).get(rec.get("run_id"))
            if run is None:
                candidates = [r for r in st.get("runs", {}).values()
                              if r.get("session_id") == sid]
                active = [r for r in candidates if r.get("status") == "active"]
                run = active[0] if len(active) == 1 else (
                      candidates[0] if len(candidates) == 1 else None)
            task = st["tasks"].get(run["task_req_id"]) if run else None
            if run and run.get("session_id") and run["session_id"] != sid:
                rec["reconcile"] = "superseded_session"
                rec["reason"] = "run has continued in a successor session; ignore late old-session cancellation"
                self._rewrite_tr(path, rec)
                continue
            if run and not run.get("session_id") and sid:
                run["session_id"] = sid
                task["session_id"] = sid
            if task and (run.get("status") == "ended" or
                         task.get("phase") in ("completed", "result_pending", "result_failed")):
                rec["reconcile"] = "already_done"
                rec["reason"] = "result already received; no continuation"
                self._rewrite_tr(path, rec)
                continue
            if task is None:
                rec["reconcile"] = "pending_decision"
                rec["reason"] = "无关联运行或状态不明，待经理决定"
                self.log("turn_recovery_pending", ref=os.path.basename(path),
                         detail={"session": sid})
                self._notify_review(st, None, "turn_cancelled",
                                    "lp_sync/turn_recovery/%s"
                                    % os.path.basename(path),
                                    "决定被取消回合的处置（续原会话或放弃）")
                self._rewrite_tr(path, rec)
                continue
            if rec.get("failure_category") == "rate_limited" and rec.get("retry_at", "") > _ts():
                task["phase"] = "retry_wait"
                task["retry_at"] = rec["retry_at"]
                task["last_error"] = rec.get("error_summary") or "执行模型限流，等待重试时间"
                rec["reconcile"] = "retry_wait"
                self._save_state(st)
                self._rewrite_tr(path, rec)
                self._notify_review(st, task, "execution_wait", path,
                                    "执行模型限流，等待 " + rec["retry_at"] + " 后自动接续")
                continue
            if task["phase"] in ("dispatched", "submitted", "uncertain", "retry_wait"):
                task["recovery_attempts"] = task.get("recovery_attempts", 0) + 1
                if task["recovery_attempts"] > 3:
                    task["phase"] = "uncertain"
                    task["last_error"] = "连续恢复未成功，保留断点和执行槽等待核验"
                    rec["reconcile"] = "pending_decision"
                    self._save_state(st)
                    self._rewrite_tr(path, rec)
                    self._notify_review(st, task, "execution_failed", path, task["last_error"])
                    continue
                request_id = "kc-" + hashlib.sha256((run["run_id"] + os.path.basename(path)).encode()).hexdigest()[:40]
                task["phase"] = "submitted"
                task["dispatch_request_id"] = request_id
                task["continuation_of"] = run["run_id"]
                task["turn_cancelled_at"] = rec.get("captured_at")
                self._save_state(st)
                request = {"schema_version": 1, "request_id": request_id,
                           "op": "dispatch_resume", "sid": sid,
                           "lane": task.get("lane"), "item_id": task["item_id"],
                           "run_id": run["run_id"], "verify": True,
                           "message": "执行器中断后的接续。先读取已有交付、结果文件和最后断点；只补未完成步骤，已完成不重做。"
                                      "本次继续原运行，不新认领事项。\n" + task["message"] +
                                      "\n【内核运行号】" + run["run_id"] +
                                      "\n结果仍写入 " + self.inbox}
                self.bridge_submit(request)
                rec["reconcile"] = "continuation_submitted"
                rec["continuation_request_id"] = request_id
                rec["reason"] = "durable continuation submitted for original session"
                self.log("turn_recovery_resubmit",
                         ref=task["task_req_id"],
                         detail={"session": sid})
            self._rewrite_tr(path, rec)

    def _rewrite_tr(self, path, rec):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    def reconcile(self, st):
        """重启或每轮先对账未知结果：submitted 任务与未落实结果动作。"""
        for task in st["tasks"].values():
            if task.get("dispatch_request_id") and task["phase"] in ("result_pending", "completed") and not task.get("dispatch_receipt_recorded"):
                state, info = self.bridge_lookup(task["dispatch_request_id"])
                if state == "completed":
                    self._adopt_dispatch(st,task,info)
            if task["phase"] == "submitted":
                state, info = self.bridge_lookup(task["dispatch_request_id"])
                if state == "completed":
                    self._adopt_dispatch(st, task, info)
                elif state == "failed_final":
                    task["phase"] = "dispatch_failed"
                    task["last_error"] = str(
                        (info or {}).get("final_error") or (info or {}).get("error") or
                        (info or {}).get("detail"))[:200]
                    self._save_state(st)
                    self.log("dispatch_failed_final", ref=task["task_req_id"],
                             detail={"request_id": task["dispatch_request_id"],
                                     "error": task["last_error"]})
                # in_flight / absent：交给 dispatch_ready 处理（absent 时重交同号）。
        for res in st["results"].values():
            for act in res["actions"]:
                if act["state"] != "pending":
                    continue
                state, info = self.bridge_lookup(act["bridge_request_id"])
                if state == "completed":
                    if ((info or {}).get("result") or {}).get("deferred"):
                        act["error"] = "写入仅在断网缓存排队，尚无正式回执"
                        continue
                    act["state"] = "done"
                    self.log("result_action_done", ref=res["result_id"],
                             detail={"action": act["action"],
                                     "reconciled": bool((info or {}).get("reconciled_from"))})
                elif state == "failed_final":
                    act["state"] = "definitive_failed"
                    act["error"] = str((info or {}).get("final_error") or
                                       (info or {}).get("error"))[:200]
                    self.log("result_action_definitive_failed", ref=res["result_id"],
                             detail={"action": act["action"], "error": act["error"]})
                elif state == "absent":
                    # 从未送达：本轮由 process_pending_results 重交同号。
                    pass
        self._finalize_results(st)

    def _adopt_dispatch(self, st, task, info):
        result = (info or {}).get("result") or {}
        session = result.get("session") or (info or {}).get("session")
        run_id = self._run_id_for(task)
        if (st["runs"].get(run_id) or {}).get("status") == "ended":
            if session:
                task["session_id"] = session
                st["runs"][run_id]["session_id"] = session
            task["dispatch_receipt_recorded"] = True
            self._save_state(st)
            self.log("late_dispatch_receipt",ref=run_id,detail={"session":session,
                     "reconciled_from_queue_ledger":bool((info or {}).get("reconciled_from"))})
            return  # A late dispatch receipt cannot reopen a consumed result.
        task["phase"] = "dispatched"
        task["last_error"] = None
        task["session_id"] = session
        task["run_id"] = run_id
        task["dispatched_at"] = _ts()
        task["dispatch_receipt_recorded"] = True
        st["runs"][run_id] = {"run_id": run_id, "task_req_id": task["task_req_id"],
                              "item_id": task["item_id"],
                              "dispatch_request_id": task["dispatch_request_id"],
                              "session_id": session, "started_at": _ts(),
                              "status": "active",
                              "worker_id": task.get("worker_id"),
                              "reconciled": bool((info or {}).get("reconciled_from"))}
        self._save_state(st)
        if task.get("worker_id"):
            lw.worker_bind(task["worker_id"], task.get("lane"),
                           session_id=session, item_id=task["item_id"],
                           run_id=run_id, phase="active")
        self.log("run_started", ref=run_id,
                 detail={"task": task["task_req_id"], "item_id": task["item_id"],
                         "session": session, "worker_id": task.get("worker_id"),
                         "reconciled_from_queue_ledger":
                             bool((info or {}).get("reconciled_from"))})

    def _run_id_for(self, task):
        return task.get("run_id") or "run-%s-a%d" % (task["task_req_id"], task["dispatch_attempts"])

    def _rebuild_worker_registry(self, st):
        """I-217 判据三：重启后按 state 的活跃运行重建工位注册表，
        工位与运行的持久映射重启前后回读一致。"""
        rebuilt = 0
        for run in st.get("runs", {}).values():
            if run.get("status") != "active" or not run.get("worker_id"):
                continue
            task = st["tasks"].get(run.get("task_req_id")) or {}
            lw.worker_bind(run["worker_id"], task.get("lane"),
                           session_id=run.get("session_id"),
                           item_id=run.get("item_id"), run_id=run["run_id"],
                           phase="active")
            rebuilt += 1
        if rebuilt:
            self.log("worker_registry_rebuilt", detail={"workers": rebuilt})

    # ---------- 派发 ----------

    def dispatch_ready(self, st):
        if self._control_mode() == "halt":
            return
        for task in list(st["tasks"].values()):
            if task["phase"] == "line_pending":
                # 任务线读取结果对账（重启与每轮同路径）。
                state, info = self.bridge_lookup(task["line_request_id"])
                if state == "completed":
                    self._adopt_line(st, task, info)
                elif state == "failed_final":
                    self._line_retry_or_fail(st, task, str(
                        (info or {}).get("final_error") or
                        (info or {}).get("error"))[:200])
                continue
            if task["phase"] == "line_wait":
                # 依赖未清或事项非 open：到点重读任务线。
                if task.get("line_retry_at", "") <= _ts():
                    task["phase"] = "ready"
                    task.pop("line_checked", None)
                    self._save_state(st)
                continue
            if task["phase"] not in ("ready", "submitted"):
                continue
            if task["phase"] == "ready" and not task.get("line_checked"):
                # 判据一：派发前读取任务线（云端账本为唯一权威）。
                # test 通道任务不进生产账本，跳过任务线核验。
                if task.get("kind") == "test":
                    task["line_checked"] = True
                    self._save_state(st)
                else:
                    self._submit_line_read(st, task)
                    continue
            if task["phase"] == "ready":
                # I-217 工位槽：每线并发上限可配置（缺省一、上限二），
                # 并行准入按可写范围与独占资源声明判定（判据四）。
                # limit=1 且无并行时行为与 I-205 单执行槽逐位一致。
                config = lw.load_lane_config(self.state_root)
                limit = lw.concurrency_limit(config, task.get("lane"))
                busy, free = lw.lane_slots(st["tasks"], task.get("lane"),
                                           config)
                wid = task.get("worker_id")
                if wid and wid in busy and busy[wid] != task["task_req_id"]:
                    wid = None                     # 槽位被异常占用，重分配
                if not wid:
                    if not free:
                        task["last_error"] = ("本线 %d 个工位均在执行，排队等待"
                                              % limit)
                        self._save_state(st)
                        continue
                    if busy:
                        actives = [st["tasks"][r] for r in busy.values()]
                        ok, reason = lw.check_parallel_admission(task, actives)
                        if not ok:
                            task["last_error"] = reason
                            self._save_state(st)
                            continue
                    wid = free[0]
                    task["worker_id"] = wid
                    self._save_state(st)
                queues_now = load_lane_queues(self.state_root)
                q_now = queues_now.setdefault(
                    task.get("lane") or "",
                    {"order": [], "current": None, "waiting": {}})
                q_now.setdefault("worker_slots", {})[wid] = task["task_req_id"]
                q_now["concurrency_limit"] = limit
                # 兼容字段 current：最早派发的活跃工位任务（单工位时即
                # 唯一执行；多工位时仅代表该工位，见接口契约）。
                active_order = [rid for rid in q_now.get("order", [])
                                if rid in busy.values() or rid == task["task_req_id"]]
                q_now["current"] = active_order[0] if active_order \
                    else task["task_req_id"]
                save_lane_queues(self.state_root, queues_now)
                if task.get("kind") != "test" and task.get("line_admission") != task["task_req_id"]:
                    task["line_admission"] = task["task_req_id"]
                    task.pop("line_checked",None)
                    self._submit_line_read(st,task)
                    continue
                # 派发前最终准入复核：持槽集合自首次判定以来可能变化
                # （他任务当时尚在首次任务线检查、未持槽），冲突即释放
                # 槽位回等待，不允许带着冲突进入并行执行。
                config2 = lw.load_lane_config(self.state_root)
                busy2, _free2 = lw.lane_slots(st["tasks"], task.get("lane"),
                                              config2)
                busy2.pop(task.get("worker_id"), None)
                if busy2:
                    others = [st["tasks"][r] for r in busy2.values()]
                    ok2, reason2 = lw.check_parallel_admission(task, others)
                    if not ok2:
                        queues_rel = load_lane_queues(self.state_root)
                        q_rel = queues_rel.get(task.get("lane"))
                        if q_rel and task.get("worker_id"):
                            q_rel.setdefault("worker_slots", {}).pop(
                                task["worker_id"], None)
                            save_lane_queues(self.state_root, queues_rel)
                        task["worker_id"] = None
                        task["last_error"] = reason2
                        self._save_state(st)
                        continue
                task["dispatch_attempts"] += 1
                if task["dispatch_attempts"] > task["max_dispatch_attempts"]:
                    task["phase"] = "dispatch_failed"
                    self._release_lane_slot(st, task, "dispatch_failed")
                    task["last_error"] = "派发次数耗尽"
                    self._save_state(st)
                    self.log("dispatch_failed_final", ref=task["task_req_id"],
                             detail={"error": "派发次数耗尽"})
                    continue
                task["dispatch_request_id"] = _bounded_request_id("kd-%s-a%d" % (
                    task["task_req_id"], task["dispatch_attempts"]))
                task["phase"] = "submitted"
                self._save_state(st)                # 阶段先持久化（不可逆动作前）
                self._submit_dispatch(st, task)
                self._maybe_crash("after_dispatch_submit")
            else:  # submitted：对账后 absent 才重交同号（不新建）。
                state, dispatch_info = self.bridge_lookup(task["dispatch_request_id"])
                if state == "absent":
                    self._submit_dispatch(st, task)
                elif state == "uncertain":
                    if (((dispatch_info or {}).get("result") or {}).get("busy_wait")):
                        task["last_error"] = "同一会话上一回合尚未结束，等待完成后自动接续"
                        task["uncertain_ticks"] = 0
                        self._save_state(st)
                        continue
                    # I-178 返修：桥侧保持未知（执行已开始无法对账），
                    # 不得重交；连续多轮仍未知则转待决，等经理处置。
                    task["uncertain_ticks"] = task.get("uncertain_ticks", 0) + 1
                    if task["uncertain_ticks"] >= 6:
                        task["phase"] = "uncertain"
                        task["last_error"] = ("派发执行结果未知且无法对账，"
                                              "保持 uncertain 等经理决定，不重发")
                        self._save_state(st)
                        self.log("task_uncertain", ref=task["task_req_id"],
                                 detail={"request_id": task["dispatch_request_id"]})
                        self._notify_review(st, task, "uncertain",
                                            "lp_sync/kernel_state/state.json#%s"
                                            % task["task_req_id"],
                                            "决定未知派发结果的处置（查证或放弃）")
                    else:
                        self._save_state(st)

    def _submit_line_read(self, st, task):
        """经桥读取任务线（只读动作，确定性请求号，失败可换号重读）。"""
        task["line_attempts"] = task.get("line_attempts", 0) + 1
        task["line_request_id"] = _bounded_request_id("kl-%s-line-a%d" % (task["task_req_id"],
                                                      task["line_attempts"]))
        task["phase"] = "line_pending"
        self._save_state(st)
        req = {"schema_version": 1, "request_id": task["line_request_id"],
               "op": "ledger_read", "agent_id": "orchestrator-kernel",
               "ledger": {"action": "get_item_line",
                          "args": {"item_id": task["item_id"]}}}
        outcome = self.bridge_submit(req)
        self.log("line_read_submitted", ref=task["task_req_id"],
                 detail={"request_id": task["line_request_id"],
                         "attempt": task["line_attempts"],
                         "outcome": outcome})

    def _line_retry_or_fail(self, st, task, error):
        task["line_attempts"] = task.get("line_attempts", 1)
        if task["line_attempts"] - task.get("line_retry_budget_start", 0) >= 5:
            # A transport outage is not a rejected task. Keep its identity and
            # retry fresh read-only requests after a bounded backoff; never
            # dispatch from cached task-line state while the cloud is missing.
            if "CloudUnreachable" in error:
                cycles = task.get("line_network_failures", 0) + 1
                delay = min(300, max(15, self.line_retry_s) * 2 ** min(cycles - 1, 4))
                task.update(phase="line_wait", line_network_failures=cycles,
                            line_retry_budget_start=task["line_attempts"],
                            line_retry_at=(_now() + datetime.timedelta(seconds=delay)).isoformat(timespec="milliseconds"),
                            last_error="云端任务线暂不可达，%s秒后自动重读：%s" % (delay, error))
                task.pop("line_checked", None)
                self._save_state(st)
                self.log("line_network_wait", ref=task["task_req_id"],
                         detail={"error": error, "retry_after_s": delay})
                return
            task["phase"] = "line_rejected"
            task["last_error"] = "任务线读取多次失败：%s" % error
            self._save_state(st)
            self.log("line_rejected", ref=task["task_req_id"],
                     detail={"error": task["last_error"]})
            return
        task["phase"] = "ready"
        task.pop("line_checked", None)
        self._save_state(st)

    def _adopt_line(self, st, task, info):
        """采纳任务线读取结果：可派发才放行，依赖未清或状态不符则等待。"""
        result = (info or {}).get("result") or {}
        line = result.get("ledger_result") or {}
        item = line.get("item")
        open_deps = line.get("open_deps") or []
        if item is None:
            task["phase"] = "line_rejected"
            task["last_error"] = "事项 %s 不存在于任务线" % task["item_id"]
            self._save_state(st)
            self.log("line_rejected", ref=task["task_req_id"],
                     detail={"error": task["last_error"]})
            return
        status = item.get("status")
        if status == "in_progress" and item.get("claimed_by") == (task.get("lane") or "") + "队":
            from host_service import EXECUTION_STORE
            matching = [record for record in EXECUTION_STORE.requests().values()
                        if record.get("session_id") == item.get("claim_session_id") and record.get("session_id")
                        and (record.get("payload") or {}).get("item_id") == task["item_id"]
                        and (record.get("payload") or {}).get("run_id") == task.get("run_id") and task.get("run_id")]
            if matching:
                task["session_id"] = item["claim_session_id"]
                if task.get("run_id") in st["runs"]:
                    st["runs"][task["run_id"]]["session_id"] = item["claim_session_id"]
                status = "open"  # Continue this exact execution's already-owned claim.
        if any(v.get("review_required") for v in item.get("delivery_impact") or []) and not item.get("partial_start"):
            task["phase"] = "line_wait"
            task["last_error"] = "上游交付撤回影响尚未复核，等待依赖重新满足及审核"
            task["line_retry_at"] = (_now() + datetime.timedelta(seconds=self.line_retry_s)).isoformat(timespec="milliseconds")
            self._save_state(st)
            return
        if status == "closed":
            task["phase"] = "line_rejected"
            task["last_error"] = "事项 %s 已关闭，不可派发" % task["item_id"]
            self._save_state(st)
            self.log("line_rejected", ref=task["task_req_id"],
                     detail={"error": task["last_error"]})
            return
        if status != "open":
            # in_progress / blocked：到点重读，不盲目派发。
            task["phase"] = "line_wait"
            task["line_retry_at"] = (_now() + datetime.timedelta(
                seconds=self.line_retry_s)).isoformat(timespec="milliseconds")
            task["last_error"] = "事项状态 %s，等待重读任务线" % status
            self._save_state(st)
            self.log("line_wait", ref=task["task_req_id"],
                     detail={"status": status})
            return
        if line.get("lane_active"):
            # I-217 补修：lane_active 不再一律当串行门禁。按已分配工位、
            # 该线有效容量与活跃运行归属判定：容量未满且每个在办项归属
            # 可核（认领会话或工位号在案）时放行进入工位槽与冲突准入；
            # 容量满或归属未知仍保守等待。冲突、租约与去重由后续槽位
            # 准入与云端认领校验把守，本处不绕过。
            active_ids = line["lane_active"]
            details = line.get("lane_active_details") or []
            limit = lw.concurrency_limit(
                lw.load_lane_config(self.state_root), task.get("lane"))
            ownership_known = bool(details) and all(
                d.get("claim_session_id") or d.get("claim_worker_id")
                for d in details)
            if len(active_ids) >= limit or not ownership_known:
                task["phase"] = "line_wait"
                task["line_retry_at"] = (_now() + datetime.timedelta(
                    seconds=self.line_retry_s)).isoformat(timespec="milliseconds")
                task["last_error"] = (
                    "本线 %d 个工位均在执行：%s" % (limit, active_ids)
                    if len(active_ids) >= limit else
                    "本线在办归属未核（认领会话/工位缺失）：%s" % active_ids)
                self._save_state(st)
                self.log("line_wait", ref=task["task_req_id"],
                         detail={"lane_active": active_ids,
                                 "ownership_known": ownership_known,
                                 "limit": limit})
                return
            # 容量未满且归属可核：放行（并行冲突在工位槽准入复核把守）。
            self.log("line_parallel_admitted", ref=task["task_req_id"],
                     detail={"lane_active": active_ids, "limit": limit})
        partial_scope = item.get("partial_start") or []
        if partial_scope and (not isinstance(partial_scope, list) or
                              not all(isinstance(v, str) and v.strip() for v in partial_scope)):
            task["phase"] = "line_rejected"
            task["last_error"] = "先行范围格式无效，必须为非空文字列表"
            self._save_state(st)
            return
        if open_deps and not partial_scope:
            task["phase"] = "line_wait"
            task["line_retry_at"] = (_now() + datetime.timedelta(
                seconds=self.line_retry_s)).isoformat(timespec="milliseconds")
            task["last_error"] = "依赖未清：%s" % open_deps
            self._save_state(st)
            self.log("line_wait", ref=task["task_req_id"],
                     detail={"open_deps": open_deps})
            return
        item_lane = item.get("lane")
        if item_lane and task.get("lane") and item_lane != task["lane"]:
            task["phase"] = "line_rejected"
            task["last_error"] = "路线不一致：事项属 %s，任务指定 %s" % (
                item_lane, task["lane"])
            self._save_state(st)
            self.log("line_rejected", ref=task["task_req_id"],
                     detail={"error": task["last_error"]})
            return
        task["partial_start_scope"] = partial_scope if open_deps else []
        task["admitted_open_dependencies"] = list(open_deps)
        task["line_checked"] = True
        task["last_error"] = None
        task["reviewer"] = item.get("reviewer")
        task["phase"] = "ready"
        self._save_state(st)
        self.log("line_checked", ref=task["task_req_id"],
                 detail={"item_id": task["item_id"], "status": status,
                         "open_deps": open_deps})

    def _submit_dispatch(self, st, task):
        run_id = self._run_id_for(task)
        task["run_id"] = run_id
        st["runs"].setdefault(run_id, {"run_id": run_id, "task_req_id": task["task_req_id"],
                           "item_id": task["item_id"], "status": "starting", "session_id": None,
                           "worker_id": task.get("worker_id")})
        self._save_state(st)
        message = (task["message"] +
                   "\n\n【内核运行号】" + run_id +
                   "。执行器收工时把结果文件写到 " + self.inbox +
                   "，kind=result_submit，字段格式见 lp_sync/kernel_README.md。" +
                   "本消息由常驻编排内核自动派发（批准人 " + task["approved_by"] + "）。")
        if task.get("worker_id"):
            message += "\n【执行工位】" + task["worker_id"]
        if task.get("partial_start_scope"):
            message += ("\n【本次仅限已登记的先行施工】\n" +
                        "\n".join(task["partial_start_scope"]) +
                        "\n当前未清依赖：" + str(task.get("admitted_open_dependencies") or []) +
                        "。本段优先于历史简报中的整项交付要求；本次只提交阶段证据，" +
                        "result_submit outcome=evidence_appended，不得提交整项pending_review、" +
                        "closed或勾选整项判据；未放行部分保持原依赖。")
        req = {"schema_version": 1,
               "request_id": task["dispatch_request_id"],
               "op": "dispatch_new",
               "title": task["title"],
               "message": message,
               "lane": task["lane"],
               "item_id": task["item_id"],
               "run_id": run_id,
               "worker_id": task.get("worker_id"),
               "kind": task.get("kind", "task")}
        outcome = self.bridge_submit(req)
        self.log("dispatch_submitted", ref=task["task_req_id"],
                 detail={"request_id": task["dispatch_request_id"],
                         "attempt": task["dispatch_attempts"],
                         "worker_id": task.get("worker_id"),
                         "outcome": outcome})

    def poll_dispatch_results(self, st):
        for task in st["tasks"].values():
            if task["phase"] != "submitted":
                continue
            state, info = self.bridge_lookup(task["dispatch_request_id"])
            if state == "completed":
                self._adopt_dispatch(st, task, info)
            elif state == "failed_final":
                task["phase"] = "ready" if task["dispatch_attempts"] < \
                    task["max_dispatch_attempts"] else "dispatch_failed"
                if task["phase"] == "ready":
                    task["dispatch_request_id"] = None
                task["last_error"] = str((info or {}).get("final_error"))[:200]
                self._save_state(st)
                self.log("dispatch_attempt_failed", ref=task["task_req_id"],
                         detail={"attempt": task["dispatch_attempts"],
                                     "error": task["last_error"],
                                     "will_retry": task["phase"] == "ready"})

    # ---------- 结果收取与落实 ----------

    def _ingest_result(self, st, obj, name, path):
        rid = str(obj.get("result_id") or name[:-5])
        # 幂等先于校验：已处理过的结果号重交按内容比对分流（一致=重复
        # 跳过，不一致=冲突拒绝），再校验新结果。
        if rid in st["results"]:
            rec = st["results"][rid]
            if rec.get("payload_hash") == _payload_hash(obj):
                os.replace(path, os.path.join(self.processed, name))
                rec["duplicate_count"] = rec.get("duplicate_count", 0) + 1
                self._save_state(st)
                self.log("duplicate_result_skipped", ref=rid,
                         detail={"reason": "结果号已存在且正文一致，不重复更新"})
                self._write_ack(rid, {"ok": True, "duplicate": True})
                return
            os.replace(path, os.path.join(self.invalid, name))
            self.log("result_conflict_rejected", ref=rid,
                     detail={"reason": "结果号已存在且正文不一致（载荷哈希冲突）",
                             "stored_hash": rec.get("payload_hash"),
                             "incoming_hash": _payload_hash(obj)})
            self._write_ack(rid, {"ok": False,
                                  "error": "结果号已存在且正文不一致，冲突拒绝"})
            return
        err = self._validate_result(st, obj)
        if err:
            os.replace(path, os.path.join(self.invalid, name))
            self.log("result_rejected", ref=rid, detail={"error": err})
            self._write_ack(rid, {"ok": False, "error": err})
            return
        run = st["runs"][obj["run_id"]]
        if not run.get("session_id"):
            from host_service import EXECUTION_STORE
            sessions = {record["session_id"] for record in EXECUTION_STORE.requests().values()
                        if record.get("session_id") and (record.get("payload") or {}).get("item_id") == obj["item_id"]
                        and (record.get("payload") or {}).get("run_id") == obj["run_id"]}
            if len(sessions) == 1:
                run["session_id"] = next(iter(sessions))
        res = {"result_id": rid, "run_id": obj["run_id"],
               "item_id": obj["item_id"], "outcome": obj["outcome"],
               "payload_hash": _payload_hash(obj),
               "received_at": _ts(), "applied_at": None,
               "evidence_ptr": obj.get("evidence_ptr") or self.state_path + "#results/" + rid,
               "actions": self._plan_actions(obj, self._task_of_run(st, obj["run_id"]))}
        st["results"][rid] = res
        run["status"] = "ended"
        task = self._task_of_run(st, obj["run_id"])
        if task:
            task["phase"] = "result_pending"
            task["last_error"] = None
            task["session_id"] = run.get("session_id") or task.get("session_id")
        self._save_state(st)                # 结果先持久化再归档（崩溃零丢失）
        os.replace(path, os.path.join(self.processed, name))
        self.log("result_received", ref=rid,
                 detail={"run_id": obj["run_id"], "item_id": obj["item_id"],
                         "outcome": obj["outcome"]})
        self._maybe_crash("after_result_ingest")

    def _validate_result(self, st, obj):
        if obj.get("schema_version") != 1:
            return "schema_version 必须为 1"
        rid = obj.get("result_id")
        if not rid or not dq._REQUEST_ID_RE.match(str(rid)):
            return "result_id 缺失或非法"
        run = st["runs"].get(obj.get("run_id") or "")
        if run is None:
            return "run_id 不属于任何已知运行"
        if run["status"] not in ("active", "starting"):
            return "运行 %s 已结束，不接受第二个有效结果" % run["run_id"]
        if obj.get("item_id") != run["item_id"]:
            return "result 的 item_id 与运行不关联"
        if obj.get("outcome") not in OUTCOMES:
            return "outcome 只允许 %s" % (OUTCOMES,)
        if obj["outcome"] == "closed":
            for k in ("evidence", "conclusion", "criterion_check", "owner_note"):
                if not obj.get(k):
                    return "closed 结果缺 %s" % k
            if obj["conclusion"] not in ("判据通过", "判据不通过", "证据不足无法判定"):
                return "conclusion 措辞不合法"
        if obj["outcome"] == "blocked":
            for k in ("reason", "blocker_type", "needs", "evidence", "owner_note"):
                if not obj.get(k):
                    return "blocked 结果缺 %s" % k
        if obj["outcome"] == "evidence_appended" and not obj.get("evidence"):
            return "evidence_appended 结果缺 evidence"
        if obj["outcome"] == "pending_review":
            for key in ("delivery_version", "evidence_ptr", "reviewer"):
                if not obj.get(key):
                    return "pending_review 结果缺 " + key
            for key in ("delivery_version", "evidence_ptr", "reviewer"):
                if not isinstance(obj.get(key),str):
                    return "pending_review 的 %s 必须是字符串；多个证据请提交一个清单文件位置" % key
            if "unmet" in obj and not isinstance(obj["unmet"],list):
                return "pending_review 的 unmet 必须是列表"
        return ""

    def _plan_actions(self, obj, task=None):
        """把结果映射为账本写动作清单（经 I-177 桥执行）。"""
        acts = []
        if obj["outcome"] == "closed":
            acts.append({"action": "close",
                         "bridge_request_id": "kl-%s-close" % obj["result_id"],
                         "args": {"item_id": obj["item_id"],
                                  "evidence": obj["evidence"],
                                  "closer": obj.get("closer") or "L3队",
                                  "conclusion": obj["conclusion"],
                                  "criterion_check": obj["criterion_check"],
                                  "owner_note": obj["owner_note"]},
                         "state": "pending"})
        elif obj["outcome"] == "blocked":
            acts.append({"action": "block",
                         "bridge_request_id": "kl-%s-block" % obj["result_id"],
                         "args": {"item_id": obj["item_id"],
                                  "reason": obj["reason"],
                                  "blocker_type": obj["blocker_type"],
                                  "needs": obj["needs"],
                                  "evidence": obj["evidence"],
                                  "reporter": obj.get("reporter") or "L3队",
                                  "owner_note": obj["owner_note"]},
                         "state": "pending"})
        elif obj["outcome"] == "pending_review":
            acts.append({"action": "submit_pending_review",
                         "bridge_request_id": "kl-%s-review" % obj["result_id"],
                         "args": {k: obj[k] for k in ("item_id", "run_id", "delivery_version", "evidence_ptr", "reviewer")},
                         "state": "pending"})
        else:
            ev = obj["evidence"]
            acts.append({"action": "append_evidence",
                         "bridge_request_id": "kl-%s-ev" % obj["result_id"],
                         "args": {"item_id": obj["item_id"],
                                  "evidence": ev if isinstance(ev, str) else ev[0]},
                         "state": "pending"})
            # An approved partial run has ended, while its parent item still
            # awaits dependencies or review. Release its cloud execution claim
            # after preserving the stage evidence; otherwise an idle stage
            # continues to occupy the entire lane in cloud admission checks.
            if (task and task.get("kind", "task") == "task"
                    and task.get("partial_start_scope")
                    and task.get("run_id") == obj["run_id"]):
                acts.append({"action": "release",
                             "bridge_request_id": "kl-%s-stage-release" % obj["result_id"],
                             "args": {"item_id": obj["item_id"],
                                      "worker": task["lane"] + "队"},
                             "state": "pending"})
        # A result name can recur after rework; the logical run disambiguates
        # the globally persisted cloud operation identity.
        run_suffix = hashlib.sha256(obj["run_id"].encode()).hexdigest()[:16]
        for act in acts:
            act["bridge_request_id"] = _bounded_request_id(act["bridge_request_id"] + "-" + run_suffix)
        if obj["outcome"] == "pending_review":
            for key in ("unmet", "owner_note"):
                if key in obj:
                    acts[0]["args"][key] = obj[key]
        return acts

    def _task_of_run(self, st, run_id):
        run = st["runs"].get(run_id)
        if not run:
            return None
        return st["tasks"].get(run["task_req_id"])

    def process_pending_results(self, st):
        for res in st["results"].values():
            if res["applied_at"]:
                continue
            for index, act in enumerate(res["actions"]):
                # Later effects depend on earlier durable effects. In
                # particular, do not release a partial claim before its
                # evidence append has a confirmed cloud receipt.
                if any(previous["state"] != "done"
                       for previous in res["actions"][:index]):
                    break
                if act["state"] == "pending":
                    original_id=act["bridge_request_id"]
                    bounded=_bounded_request_id(original_id)
                    if bounded!=original_id:
                        old_state,_=self.bridge_lookup(original_id)
                        if old_state!="absent":
                            act["error"]="旧超长请求仍有执行记录，保留待对账"
                            continue
                        act["legacy_bridge_request_id"]=original_id
                        act["bridge_request_id"]=bounded
                        self._save_state(st)
                    state, _ = self.bridge_lookup(act["bridge_request_id"])
                    if state in ("completed", "failed_final", "in_flight"):
                        continue
                    req = {"schema_version": 1,
                           "request_id": act["bridge_request_id"],
                           "op": "ledger_write",
                           "agent_id": "orchestrator-kernel",
                           "ledger": {"action": act["action"], "args": act["args"]}}
                    outcome = self.bridge_submit(req)
                    self.log("result_action_submitted", ref=res["result_id"],
                             detail={"action": act["action"],
                                     "request_id": act["bridge_request_id"],
                                     "outcome": outcome})
        # 下一轮 reconcile 会把 completed/done 落定；本轮先查一次结果文件。
        for res in st["results"].values():
            if res["applied_at"]:
                continue
            for act in res["actions"]:
                if act["state"] != "pending":
                    continue
                state, info = self.bridge_lookup(act["bridge_request_id"])
                if state == "completed":
                    if ((info or {}).get("result") or {}).get("deferred"):
                        act["error"] = "写入仅在断网缓存排队，尚无正式回执"
                        continue
                    act["state"] = "done"
                elif state == "failed_final":
                    act["state"] = "definitive_failed"
                    act["error"] = str((info or {}).get("final_error") or
                                       (info or {}).get("error"))[:200]
                    self.log("result_action_definitive_failed", ref=res["result_id"],
                             detail={"action": act["action"], "error": act["error"]})
            self._save_state(st)
        self._finalize_results(st)

    def _release_lane_slot(self, st, task, terminal_reason=""):
        """I-205/I-217：任务终态释放其占用的工位槽并留接续记录。
        工位注册表保留最后会话与事项（履历入口），仅置 idle。"""
        if not task:
            return
        queues = load_lane_queues(self.state_root)
        q = queues.get(task.get("lane"))
        if q:
            wid = task.get("worker_id")
            slots = q.setdefault("worker_slots", {})
            if wid and slots.get(wid) == task["task_req_id"]:
                slots.pop(wid, None)
            if q.get("current") == task["task_req_id"] \
                    or task["task_req_id"] in q.get("order", []):
                remaining_active = [rid for rid in q.get("order", [])
                                    if rid in slots.values()]
                q["current"] = remaining_active[0] if remaining_active else None
                q["order"] = [x for x in q.get("order", [])
                              if x != task["task_req_id"]]
                q.setdefault("waiting", {}).pop(task["task_req_id"], None)
                save_lane_queues(self.state_root, queues)
                for next_id in q["order"]:
                    next_task = st["tasks"].get(next_id)
                    if next_task and next_task.get("phase") == "line_wait":
                        next_task["line_retry_at"] = _ts()
        if task.get("worker_id"):
            lw.worker_bind(task["worker_id"], task.get("lane"), phase="idle",
                           clear=("run_id", "item_id"))
        self.log("lane_slot_released",
                 detail={"lane": task.get("lane"),
                         "task": task["task_req_id"],
                         "worker_id": task.get("worker_id"),
                         "reason": terminal_reason,
                         "next_count": len((q or {}).get("order", []))})

    def _finalize_results(self, st):
        for res in st["results"].values():
            if res["applied_at"] and not res.get("failed"):
                evidence = res.get("evidence_ptr") or next((a["args"].get("evidence_ptr") for a in res["actions"] if a["args"].get("evidence_ptr")),None) or self.state_path + "#results/" + res["result_id"]
                self._notify_review(st, self._task_of_run(st, res["run_id"]), res.get("outcome"),
                                    evidence, "审核执行结果并决定事项走向",
                                    result_id=res["result_id"], run_id=res["run_id"])
            if res["applied_at"] or not res["actions"]:
                continue
            if all(a["state"] == "done" for a in res["actions"]):
                res["applied_at"] = _ts()
                task = self._task_of_run(st, res["run_id"])
                if task and task["phase"] == "result_pending":
                    task["phase"] = "completed"
                    self._release_lane_slot(st, task, "result_applied")
                self._save_state(st)
                self.log("result_applied", ref=res["result_id"],
                         detail={"outcome": res["outcome"],
                                 "item_id": res["item_id"]})
                self._notify_review(st, self._task_of_run(st, res["run_id"]),
                                    res.get("outcome"),
                                    res.get("evidence_ptr") or self.state_path + "#results/" + res["result_id"],
                                    "审核执行结果并决定事项走向",
                                    result_id=res["result_id"], run_id=res["run_id"])
                self._write_ack(res["result_id"],
                                {"ok": True, "applied_at": res["applied_at"],
                                 "actions": [a["action"] for a in res["actions"]]})
            elif any(a["state"] == "definitive_failed" for a in res["actions"]):
                res["applied_at"] = _ts()
                res["failed"] = True
                task = self._task_of_run(st, res["run_id"])
                if task and task["phase"] == "result_pending":
                    task["phase"] = "result_failed"
                    self._release_lane_slot(st, task, "result_failed")
                self._save_state(st)
                self.log("result_failed", ref=res["result_id"],
                         detail={"errors": [a.get("error") for a in res["actions"]]})
                self._notify_review(st,task,"execution_failed",
                                    self.state_path+"#results/"+res["result_id"],
                                    "结果未被正式账本接收；核对失败原因后修正提交，不能宣称事项已完成",
                                    result_id=res["result_id"],run_id=res["run_id"])
                self._write_ack(res["result_id"],
                                {"ok": False, "errors": [a.get("error")
                                                         for a in res["actions"]]})

    def _notify_review(self, st, task, category, evidence_ptr, action,
                        result_id=None, run_id=None):
        """Compose review notification (I-198 criterion three): unique event number, item, run, reviewer,
        result type, evidence location, and the action needed; only one entry per event number (idempotent).
        When reviewer is missing, return to pending with a reason recorded, do not guess."""
        reviewer = (task or {}).get("reviewer")
        event_id = "ev-%s" % (result_id
                              or (task or {}).get("task_req_id", "unknown"))
        ndir = os.path.join(self.state_root, "notifications", "pending")
        os.makedirs(ndir, exist_ok=True)
        path = os.path.join(ndir, event_id + ".json")
        nroot = os.path.dirname(ndir)
        if os.path.exists(path) or any(os.path.exists(os.path.join(nroot, folder, event_id + suffix))
                    for folder,suffix in (("taken",".json"),("sending",".delivery.json"),
                                          ("delivered",".delivery.json"),("acked",".ack.json"))):
            return False
        rec = {"event_id": event_id,
               "item_id": (task or {}).get("item_id"),
               "run_id": run_id or (task or {}).get("run_id"),
               "reviewer": reviewer,
               "category": category,
               "evidence_ptr": evidence_ptr,
               "action_needed": action,
               "created_at": _ts()}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        self.log("review_notification", ref=event_id,
                 detail={"item_id": rec["item_id"], "run_id": rec["run_id"],
                         "reviewer": reviewer, "category": category})
        return True

    def _write_ack(self, result_id, payload):
        payload = dict(payload)
        payload["result_id"] = result_id
        payload["written_at"] = _ts()
        dst = os.path.join(self.outbox, result_id + ".ack.json")
        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, dst)

    # ---------- 控制 ----------

    def _control_mode(self):
        try:
            return json.load(open(os.path.join(self.state_root, "control.json"),
                                  encoding="utf-8")).get("mode", "run")
        except (OSError, ValueError):
            return "run"

    def _maybe_crash(self, point):
        if os.environ.get("KERNEL_CRASH_POINT") == point:
            self.log("crash_injected", detail={"point": point})
            sys.stdout.flush()
            os._exit(99)

    # ---------- 主循环 ----------

    def refresh_queue_views(self, st):
        queues = load_lane_queues(self.state_root)
        for lane_number in range(1, 11):
            queues.setdefault("L" + str(lane_number), {"order": [], "current": None, "waiting": {}})
        terminal = {"completed", "dispatch_failed", "result_failed", "line_rejected", "retired"}
        for task in st["tasks"].values():
            if task.get("phase") not in terminal:
                queue_enqueue(queues, task.get("lane"), task["task_req_id"])
        config = lw.load_lane_config(self.state_root)
        for lane, queue in queues.items():
            queue["order"] = [rid for rid in queue.get("order", [])
                              if rid in st["tasks"] and st["tasks"][rid].get("phase") not in terminal]
            if queue.get("current") not in queue["order"]:
                queue["current"] = None
            limit = lw.concurrency_limit(config, lane)
            queue["concurrency_limit"] = limit
            active = [rid for rid in queue["order"] if st["tasks"][rid].get("phase") in
                       lw.ACTIVE_PHASES]
            # I-217：活跃数超出该线有效上限才是冲突；多工位并行是合法态。
            queue["conflict"] = active if len(active) > limit else []
            if active and not queue.get("current"):
                queue["current"] = active[0]
            # 工位视图（判据七）：每工位的事项、会话、运行与状态。
            # workers 为接口数组；worker_slots 为内部槽位映射（派发用）。
            workers_map = {}
            for rid in active:
                t = st["tasks"][rid]
                if t.get("worker_id"):
                    workers_map[t["worker_id"]] = rid
            queue["worker_slots"] = workers_map
            queue["workers"] = [{"worker_id": wid,
                                 "item_id": st["tasks"][rid]["item_id"],
                                 "session_id": st["tasks"][rid].get("session_id"),
                                 "run_id": st["tasks"][rid].get("run_id"),
                                 "phase": st["tasks"][rid]["phase"],
                                 "request_id": rid}
                                for wid, rid in sorted(workers_map.items())]
            queue["active_count"] = len(active)
            queue["waiting"] = {rid: st["tasks"][rid].get("last_error") or "等待本线工位空闲"
                                 for rid in queue["order"] if rid not in active}
            queue["waiting_count"] = len(queue["waiting"])
            queue["queued_items"] = [{"request_id": rid, "item_id": st["tasks"][rid]["item_id"],
                         "title": st["tasks"][rid].get("title"), "phase": st["tasks"][rid]["phase"],
                         "reason": queue["waiting"].get(rid)} for rid in queue["order"]]
            current = st["tasks"].get(queue.get("current")) or {}
            queue["current_item_id"] = current.get("item_id")
        save_lane_queues(self.state_root, queues)

    def reconcile_handoffs(self, st):
        directory = os.path.join(os.path.dirname(self.state_root), "handoff_events")
        if not os.path.isdir(directory):
            return
        for name in os.listdir(directory):
            if not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            with open(path, encoding="utf-8") as stream:
                event = json.load(stream)
            if event.get("kernel_reconciled"):
                continue
            run = st["runs"].get(event.get("run_id"))
            if run and run.get("session_id") == event.get("old"):
                run["session_id"] = event["new"]
                task = st["tasks"].get(run["task_req_id"])
                if task:
                    task["session_id"] = event["new"]
                self._save_state(st)
                self.log("handoff_reconciled", ref=run["run_id"], detail=event)
            event["kernel_reconciled"] = _ts()
            with open(path + ".tmp", "w", encoding="utf-8") as stream:
                json.dump(event, stream, ensure_ascii=False, indent=1)
            os.replace(path + ".tmp", path)

    def tick(self):
        st = self._load_state()
        self.tick_count += 1
        st["last_tick_at"] = _ts()
        self._refresh_singleton_heartbeat()
        self.ingest_inbox(st)
        self.refresh_queue_views(st)
        self.reconcile_handoffs(st)
        self.reconcile_turn_recovery(st)
        self.reconcile(st)
        self.dispatch_ready(st)
        self.poll_dispatch_results(st)
        self.process_pending_results(st)
        self.refresh_queue_views(st)
        self._save_state(st)
        if self.tick_count % HEARTBEAT_EVERY_TICKS == 1:
            self.log("wake", detail={"tick": self.tick_count,
                                     "mode": self._control_mode(),
                                     "tasks": len(st["tasks"]),
                                     "active_runs": sum(1 for r in st["runs"].values()
                                                        if r["status"] == "active")})

    def run(self):
        if not self.bind_single_instance():
            print("[内核] 单实例锁绑定失败：127.0.0.1:%d 已被占用，本实例退出" % self.port)
            self.log("second_instance_rejected", detail={"port": self.port})
            return 3
        st = self._load_state()
        st["generation"] = st.get("generation", 0) + 1
        self._rebuild_worker_registry(st)
        self._save_state(st)
        self.log("kernel_started",
                 detail={"generation": st["generation"], "pid": os.getpid(),
                         "state_root": self.state_root,
                         "queue_root": self.queue_root})
        while True:
            try:
                self.tick()
            except Exception as e:
                self.log("tick_error", detail={"error": repr(e)})
            time.sleep(self.wake_interval_s)

    def run_once(self):
        if not self.bind_single_instance():
            print("[内核] 单实例锁绑定失败，--once 拒绝运行")
            return 3
        self.log("kernel_started", detail={"mode": "once", "pid": os.getpid()})
        self.tick()
        return 0


def _cmd_status(state_root):
    st = json.load(open(os.path.join(state_root, "state.json"), encoding="utf-8"))
    summary = {"generation": st.get("generation"),
               "control_mode": json.load(open(os.path.join(state_root, "control.json"),
                                              encoding="utf-8")).get("mode")
               if os.path.exists(os.path.join(state_root, "control.json"))
               else st.get("control_mode", "run"),
               "last_tick_at": st.get("last_tick_at"),
               "tasks": {tid: {"item_id": t["item_id"], "phase": t["phase"],
                               "run_id": t.get("run_id")}
                         for tid, t in st.get("tasks", {}).items()},
               "active_runs": [r["run_id"] for r in st.get("runs", {}).values()
                               if r.get("status") == "active"],
               "results": {rid: {"outcome": r["outcome"],
                                 "applied_at": r.get("applied_at")}
                           for rid, r in st.get("results", {}).items()}}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("---- 台账最近事件 ----")
    for e in dq.ledger_events(state_root, limit=20):
        print("%s [%s] %s %s" % (e["ts"][11:23], e["event"], e.get("request_id") or "",
                                 json.dumps(e.get("detail", {}), ensure_ascii=False)[:120]))


def main(argv):
    state_root = DEFAULT_STATE_ROOT
    queue_root = DEFAULT_QUEUE_ROOT
    once = False
    op = None
    wake = WAKE_INTERVAL_S
    port = KERNEL_PORT
    line_retry = 60.0
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--state-root":
            state_root = argv[i + 1]; i += 2
        elif a == "--queue-root":
            queue_root = argv[i + 1]; i += 2
        elif a == "--wake-s":
            wake = float(argv[i + 1]); i += 2
        elif a == "--line-retry-s":
            line_retry = float(argv[i + 1]); i += 2
        elif a == "--port":
            port = int(argv[i + 1]); i += 2
        elif a == "--once":
            once = True; i += 1
        elif a in ("--status", "--halt", "--run"):
            op = a.lstrip("-"); i += 1
        else:
            i += 1
    if op == "status":
        _cmd_status(state_root)
        return 0
    if op in ("halt", "run"):
        os.makedirs(state_root, exist_ok=True)
        with open(os.path.join(state_root, "control.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": op, "changed_at": _ts()}, f, ensure_ascii=False)
        print("[内核] 总停开关拨到 %s" % op)
        return 0
    k = Kernel(state_root=state_root, queue_root=queue_root,
               wake_interval_s=wake, port=port, line_retry_s=line_retry)
    return k.run_once() if once else k.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
