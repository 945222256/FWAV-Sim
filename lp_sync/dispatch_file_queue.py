# -*- coding: utf-8 -*-
"""dispatch_file_queue.py — I-175 派发请求文件队列。

形态：宿主服务内一个轮询线程，监视 dispatch_queue/incoming/ 目录。
指挥部把派发请求写成约定格式的 JSON 文件（原子写入：先写临时名再
改名），队列在两轮队列心跳内认领并自动派发，派发含服务侧回读核验；
全程不经 Kimi 侧命令确认，也不依赖 tools/dispatch_session.mjs。

目录布局（队列根 lp_sync/dispatch_queue/）：
  incoming/   指挥部投放请求（文件名 = request_id + .json）
  processing/ 已认领执行中（认领动作 = 原子改名，天然单消费者）
  done/       派发完成（含 .result.json 结果与耗时记录）
  retry/      失败待重试（含 .meta.json 重试账：次数、下次时间、错误）
  failed/     重试耗尽（含 .result.json 最终失败记录）
  invalid/    无法解析或字段不全的文件（留痕不删除）
  ledger.json 队列台账：只追加的事件流，收发认派重试成败全留痕

请求文件约定格式（I-175 冻结）：
  {
    "schema_version": 1,
    "request_id": "req-20261012-0001",   // 必填，全局唯一，幂等键
    "op": "dispatch_new",                // dispatch_new 或 dispatch_resume
    "title": "标题",                      // dispatch_new 可选，缺省 指挥部派活
    "message": "简报正文",                 // 必填非空
    "lane": "L3",                        // dispatch_new 可选，队名
    "item_id": "I-xxx",                  // 可选，事项号
    "kind": "test",                      // 可选，task 或 test，缺省 task
    "sid": "sess_...",                   // dispatch_resume 必填
    "verify": true,                      // 可选，缺省 true（回读核验）
    "max_attempts": 3,                   // 可选，缺省 3
    "retry_delay_s": 15                  // 可选，缺省 15 秒
  }

I-177 外部智能体文件桥（账本写请求，与派发队列共用同一通道）：
  {
    "schema_version": 1,
    "request_id": "br-20261012-0001",
    "op": "ledger_write",
    "agent_id": "codex-sandbox-01",      // 必填，外援自报身份，入台账
    "ledger": {
      "action": "claim",                 // claim|release|append_evidence|close|
                                         // block|set_criterion|owner_reply
      "args": {"item_id": "I-xxx", "worker": "L3队",
               "session_id": "sess_..."}
    },
    "max_attempts": 3,
    "retry_delay_s": 15
  }
  宿主读到后代执行云端账本写入（复用 items_registry 的校验与通道），
  结果回写 done/<request_id>.result.json；外援零网络权限，只用文件。
  断网时操作进入本地断网队列由同步代理重放，结果标 deferred；
  云端明确拒绝（校验不过、状态不符）为定性失败不重试；其余异常可重试。

幂等：request_id 已有 completed 台账记录时，重复递交只记
duplicate_skipped 事件并归档 done/，不再次派发（I-178 防重复的基座）。

恢复：服务重启时 processing/ 残留文件移入 retry/ 立即待重试，事件
recovered_after_restart 留痕；派发结果未知的严格对账属 I-178 范围，
本件如实记录 recovered 标记。

心跳口径：队列心跳 = POLL_INTERVAL_S（2 秒一轮）；"两轮心跳内派出"
指从文件落盘到认领并开始派发不超过两轮（4 秒量级），回读核验由派发
操作自带，其完成时间取决于会话侧，结果与耗时一并入台账。

仅使用 Python 标准库。
"""
import datetime
import json
import os
import re
import shutil
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 登记库目录：生产取真实 items_registry；故障演练用 LP_ITEMS_REGISTRY
# 指向隔离副本（测试状态库），配合 FLAPPER_LEDGER_MODE=local 全隔离。
_ITEMS_REGISTRY = os.environ.get("LP_ITEMS_REGISTRY") or \
    os.path.join(_ROOT, "items_registry")

QUEUE_ROOT = os.environ.get("LP_DISPATCH_QUEUE_ROOT", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "dispatch_queue"))
POLL_INTERVAL_S = 2.0        # 队列心跳：一轮 2 秒
STABLE_GRACE_S = 1.0         # 文件静默期：mtime 距今不足视为仍在写入
INVALID_AFTER_SCANS = 5      # 连续解析失败达到该轮数移入 invalid/
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_S = 15

_DIRS = ("incoming", "processing", "done", "retry", "failed", "invalid",
         "uncertain")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{4,64}$")

# I-177 文件桥支持的账本写动作 → 必填参数清单（深层校验由登记库函数执行）。
LEDGER_ACTIONS = {
    "claim": ("item_id", "worker"),
    "release": ("item_id", "worker"),
    "append_evidence": ("item_id", "evidence"),
    "close": ("item_id", "evidence", "conclusion", "criterion_check", "owner_note"),
    "block": ("item_id", "reason", "blocker_type", "needs", "evidence", "owner_note"),
    "set_criterion": ("item_id", "index", "verified"),
    "owner_reply": ("item_id", "text"),
    "submit_pending_review": ("item_id", "run_id", "delivery_version", "evidence_ptr", "reviewer"),
}

# I-178 返修：账本只读动作白名单（无副作用，可安全重读）。
READ_ACTIONS = {
    "get_item_line": ("item_id",),
}

LEDGER_LOCK_STALE_S = 30.0      # 台账锁持有者心跳超过该时长视为僵死

_ledger_lock = threading.Lock()


def _now():
    return datetime.datetime.now()


def _ts():
    return _now().isoformat(timespec="milliseconds")


def ensure_layout(root=QUEUE_ROOT):
    for d in _DIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    return root


def ledger_path(root=QUEUE_ROOT):
    return os.path.join(root, "ledger.json")


def _pid_alive(pid):
    from host_service import _lock_holder_alive
    return _lock_holder_alive(pid) is not False


def _registry_dir():
    """登记库目录：调用时读环境（演练主进程内联恢复也能指向隔离副本）。"""
    return os.environ.get("LP_ITEMS_REGISTRY") or _ITEMS_REGISTRY


def _registry_item(item_id):
    """读登记库条目（生产走云端快照，演练走 LP_ITEMS_REGISTRY 本地副本）。"""
    if item_id is None:
        return None
    reg = _registry_dir()
    if reg not in sys.path:
        sys.path.insert(0, reg)
    import cloud_client
    if cloud_client.enabled():
        snap = cloud_client.request("GET", "/api/agent/snapshot")
        items = snap.get("items") if isinstance(snap, dict) else []
        return next((i for i in items if i.get("id") == item_id), None)
    import ledger as reg_ledger
    for it in reg_ledger._load().get("items", []):
        if it.get("id") == item_id:
            return it
    return None


def _read_item_line(item_id):
    """账本只读：任务线摘要（状态、责任线、未清依赖）。"""
    item = _registry_item(item_id)
    reg = _registry_dir()
    if reg not in sys.path:
        sys.path.insert(0, reg)
    import cloud_client
    if cloud_client.enabled():
        snap = cloud_client.request("GET", "/api/agent/snapshot")
        items = snap.get("items") if isinstance(snap, dict) else []
    else:
        import ledger as reg_ledger
        items = reg_ledger._load().get("items", [])
    from dependency_policy import unmet_dependencies
    summary = None
    if item is not None:
        summary = {k: item.get(k) for k in ("id", "status", "lane", "title",
                                            "claimed_by", "lease_until", "claim_session_id",
                                            "claim_worker_id",
                                            "reviewer", "delivery_impact", "partial_start",
                                            "pending_review", "delivery_dependencies")}
    open_deps = unmet_dependencies(item or {}, items)
    active = [i for i in items if i.get("lane") == (item or {}).get("lane")
              and i.get("status") == "in_progress" and i["id"] != item_id]
    lane_active = [i["id"] for i in active]
    # I-217 补修：在办归属明细（认领会话与工位），供内核按容量与固定
    # 工位判定多工位并行准入；未知归属保持保守。
    lane_active_details = [{"id": i["id"],
                            "claim_session_id": i.get("claim_session_id"),
                            "claim_worker_id": i.get("claim_worker_id")}
                           for i in active]
    return {"item": summary, "open_deps": open_deps, "lane_active": lane_active,
            "lane_active_details": lane_active_details}


def _archive_released_windows_lock(lock_path):
    """Recover only a Windows lock whose ownership descriptor is closed.

    Our os.open descriptors deny deletion/rename while held. A successful
    rename therefore proves this file is no longer held; a live owner PID
    alone cannot distinguish a writer from failed cleanup after fd close.
    Retain the old file for audit. Never use this rule on POSIX, where open
    files can be renamed. The caller's grace period protects normal cleanup.
    """
    if os.name != "nt":
        return False
    archived = lock_path + ".released." + str(time.time_ns()) + ".json"
    try:
        os.rename(lock_path, archived)
    except OSError:
        return False
    try:
        with open(os.path.join(os.path.dirname(lock_path),
                               "ledger_released_lock_recovery.log"),
                  "a", encoding="utf-8") as stream:
            stream.write("%s released descriptor; retained %s\n" %
                         (_ts(), os.path.basename(archived)))
    except OSError:
        pass
    return True


def _release_ledger_lock(lock_path, fd):
    identity = os.fstat(fd)
    os.close(fd)
    for _ in range(20):
        try:
            current = os.stat(lock_path)
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                return  # A recovered/replaced lock belongs to another writer.
            os.remove(lock_path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.025)
    # An external reader can transiently prevent removal after fd close.
    # Windows acquirers recover this released file, without stealing held fd.


def _acquire_ledger_lock(lock_path):
    """排他锁文件（I-178 返修：带持有者心跳与僵死接管）。

    锁文件内容写 pid 与时间戳：持有进程崩溃未解锁时，后来者按
    心跳超时或进程已死判定僵死并接管，杜绝台账写入永久卡死。
    Windows 语义：被存活进程打开的文件不可删除，接管删除失败即
    说明持有者实际存活，必须回到等待而非死循环；任何路径都必须
    经过截止时间检查。"""
    deadline = time.time() + 15
    while True:
        if time.time() > deadline:
            raise RuntimeError("队列台账被其他进程占用超过十五秒")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(),
                                     "ts": time.time()}).encode())
            return fd
        except FileExistsError:
            holder_pid, held_since = 0, 0.0
            try:
                info = json.load(open(lock_path, encoding="utf-8"))
                holder_pid = int(info.get("pid") or 0)
                held_since = float(info.get("ts") or 0)
            except (OSError, ValueError, TypeError):
                pass
            if not held_since:
                # 内容读不到（持有者写入竞态）：按文件年龄判，不做
                # 「读不到即僵死」的激进判定。
                try:
                    held_since = os.path.getmtime(lock_path)
                except OSError:
                    held_since = time.time()
            stale = bool(holder_pid and not _pid_alive(holder_pid))
            if stale:
                try:
                    os.remove(lock_path)
                except OSError:
                    # 删不掉 = 持有者活着握着句柄：判僵死有误，回到等待。
                    time.sleep(0.05)
                    continue
                try:
                    with open(os.path.join(os.path.dirname(lock_path),
                                           "ledger_stale_takeover.log"),
                              "a", encoding="utf-8") as f:
                        f.write("%s 台账锁僵死接管（持有者 pid=%s）\n"
                                % (_ts(), holder_pid))
                except OSError:
                    pass
                continue
            if held_since and time.time() - held_since >= 2 \
                    and _archive_released_windows_lock(lock_path):
                continue
            time.sleep(0.05)


def ledger_append(event, request_id="", file="", detail=None, root=QUEUE_ROOT,
                  attempt=None):
    """队列台账追加：只追加永不改写，原子落盘。

    跨进程互斥：内核进程投递与队列工作器执行会并发追加同一台账，
    用排他创建的锁文件串行化；持有者崩溃后按僵死规则接管。
    I-217 实测加固：读与写都带 PermissionError 短重试——内核高频
    只读回读（bridge_lookup）与本侧替换在 Windows 上偶发占用冲突，
    瞬时冲突重试即过，否则会连环炸空整轮轮询。"""
    ensure_layout(root)
    lock_path = os.path.join(root, ".ledger.lock")
    fd = _acquire_ledger_lock(lock_path)
    try:
        path = ledger_path(root)
        obj = None
        for read_attempt in range(3):
            try:
                with open(path, encoding="utf-8") as stream:
                    obj = json.load(stream)
                break
            except PermissionError as exc:
                if read_attempt == 2:
                    raise RuntimeError("队列台账读取被占用，保留原历史并等待重试") from exc
                time.sleep(0.02)
            except FileNotFoundError:
                obj = None
                break
            except (OSError, ValueError) as exc:
                raise RuntimeError("队列台账不可读取或解析，保留原文件而不重建空历史") from exc
        if obj is None:
            obj = {"schema_version": 1, "events": []}
        rec = {"ts": _ts(), "event": event, "request_id": request_id,
               "file": file}
        if attempt is not None:
            rec["attempt"] = attempt
        if detail is not None:
            rec["detail"] = detail
        obj["events"].append(rec)
        tmp = path + ".tmp"
        _write_json_retry(tmp, obj)
        _atomic_move(tmp, path)
        return rec
    finally:
        _release_ledger_lock(lock_path, fd)


def ledger_events(root=QUEUE_ROOT, request_id=None, limit=None):
    path = ledger_path(root)
    try:
        evts = json.load(open(path, encoding="utf-8")).get("events", [])
    except (OSError, ValueError):
        evts = []
    if request_id is not None:
        evts = [e for e in evts if e.get("request_id") == request_id]
    return evts[-limit:] if limit else evts


def write_request(request, root=QUEUE_ROOT):
    """把请求按约定格式原子写入 incoming/。文件名 = request_id.json。"""
    ensure_layout(root)
    rid = request.get("request_id")
    if not rid or not _REQUEST_ID_RE.match(str(rid)):
        raise ValueError("request_id 非法：%r（4-64 位字母数字点横下划线）" % rid)
    dst = os.path.join(root, "incoming", rid + ".json")
    if os.path.exists(dst):
        raise ValueError("incoming/ 已有同名请求：%s" % rid)
    tmp = dst + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(request, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)
    ledger_append("received", request_id=rid, file=rid + ".json",
                  detail={"op": request.get("op"), "lane": request.get("lane"),
                          "item_id": request.get("item_id"),
                          "kind": request.get("kind", "task")}, root=root)
    return dst


def validate_request(req):
    """字段校验：返回 (规范化请求, 错误消息)。错误消息非空即拒。"""
    if not isinstance(req, dict):
        return None, "请求不是 JSON 对象"
    if req.get("schema_version") != 1:
        return None, "schema_version 必须为 1"
    rid = req.get("request_id")
    if not rid or not _REQUEST_ID_RE.match(str(rid)):
        return None, "request_id 缺失或非法"
    op = req.get("op")
    if op not in ("dispatch_new", "dispatch_resume", "ledger_write",
                  "ledger_read"):
        return None, "op 只允许 dispatch_new、dispatch_resume、ledger_write 或 ledger_read"
    msg = req.get("message")
    if op not in ("ledger_write", "ledger_read") and \
            (not isinstance(msg, str) or not msg.strip()):
        return None, "message 必须为非空字符串"
    if op == "dispatch_resume":
        sid = req.get("sid")
        if not isinstance(sid, str) or not sid.startswith("sess_"):
            return None, "dispatch_resume 必须带 sess_ 开头的 sid"
    if op in ("ledger_write", "ledger_read"):
        led = req.get("ledger")
        if not isinstance(led, dict):
            return None, "%s 必须带 ledger 对象（action 与 args）" % op
        action = led.get("action")
        actions = LEDGER_ACTIONS if op == "ledger_write" else READ_ACTIONS
        if action not in actions:
            return None, "不支持的%s动作：%r（可用 %s）" % (
                "账本写" if op == "ledger_write" else "账本读", action,
                sorted(actions))
        args = led.get("args")
        if not isinstance(args, dict):
            return None, "ledger.args 必须为对象"
        missing = [k for k in actions[action] if args.get(k) in (None, "", [])]
        if missing:
            return None, "账本动作 %s 缺必填参数：%s" % (action, missing)
        if not req.get("agent_id"):
            return None, "%s 必填 agent_id（发起方自报身份，入台账）" % op
    kind = req.get("kind", "task")
    if kind not in ("task", "test"):
        return None, "kind 只允许 task 或 test"
    # I-217：工位路由与并行声明字段（可选；缺省走原单工位通道）。
    wid = req.get("worker_id")
    if wid is not None:
        if not isinstance(wid, str) or not re.match(
                r"^(L[0-9]{1,2}|HQ)-w[0-9]+$", wid):
            return None, "worker_id 必须形如 L3-w1（线号-工位号）"
        if op == "dispatch_new" and kind != "test" \
                and wid.rsplit("-w", 1)[0] != req.get("lane"):
            return None, "worker_id 的线号与 lane 不一致"
    for key in ("writable_paths", "exclusive_resources"):
        val = req.get(key)
        if val is not None and (not isinstance(val, list) or
                                not all(isinstance(x, str) and x.strip()
                                        for x in val)):
            return None, "%s 必须是非空字符串列表" % key
    out = dict(req)
    out.setdefault("title", "指挥部派活")
    out["kind"] = kind
    out["verify"] = bool(req.get("verify", True))
    out["max_attempts"] = int(req.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
    out["retry_delay_s"] = int(req.get("retry_delay_s", DEFAULT_RETRY_DELAY_S))
    return out, ""


def _atomic_move(src, dst):
    """原子改名，带 PermissionError 短重试。

    I-217 实测：内核每 tick 高频回读 done/ 与台账文件，与本侧的
    原子替换在 Windows 上偶发占用冲突（错误 13）。瞬时冲突重试即过；
    连续三次失败才向上抛（保持原失败语义）。"""
    for _ in range(3):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(0.02)
    os.replace(src, dst)


def _write_json_retry(path, obj):
    """结果 JSON 落盘，带 PermissionError 短重试（同 _atomic_move 语义）。"""
    for _ in range(3):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            return
        except PermissionError:
            time.sleep(0.02)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _meta_path(root, sub, name):
    return os.path.join(root, sub, name.replace(".json", ".meta.json"))


def _result_path(root, sub, name):
    return os.path.join(root, sub, name.replace(".json", ".result.json"))


class QueueWorker:
    """队列工作线程主体。dispatcher 注入派发函数，测试可替换为假件。

    dispatcher(request) → 结果字典，含 readback_verified 等字段；
    抛异常视为本次派发失败。成功判据 = readback_verified 为真。"""

    def __init__(self, root=QUEUE_ROOT, dispatcher=None, ledger_executor=None,
                 ledger_read_executor=None, poll_interval_s=None, clock=None,
                 stable_grace_s=None, resume_reconciler=None, dispatch_reconciler=None):
        self.root = ensure_layout(root)
        self.dispatcher = dispatcher
        self.ledger_executor = ledger_executor
        self.ledger_read_executor = ledger_read_executor
        self.resume_reconciler = resume_reconciler
        self.dispatch_reconciler = dispatch_reconciler
        self.poll_interval_s = poll_interval_s or POLL_INTERVAL_S
        self.clock = clock or (lambda: time.time())
        self.stable_grace_s = STABLE_GRACE_S if stable_grace_s is None else stable_grace_s
        self._bad_scans = {}
        self._stop = threading.Event()

    # ---------- 台账与状态 ----------

    def status(self):
        counts = {d: len([f for f in os.listdir(os.path.join(self.root, d))
                          if f.endswith(".json") and not f.endswith((".meta.json", ".result.json"))])
                  for d in _DIRS if d != "processing"}
        counts["processing"] = len([f for f in os.listdir(os.path.join(self.root, "processing"))
                                    if f.endswith(".json") and not f.endswith(".result.json")])
        return {"ok": True, "root": self.root,
                "poll_interval_s": self.poll_interval_s,
                "dirs": counts,
                "ledger_recent": ledger_events(self.root, limit=20)}

    def completed_request_ids(self):
        done = set()
        for e in ledger_events(self.root):
            if e.get("event") == "completed":
                done.add(e.get("request_id"))
        return done

    # ---------- 恢复与重试到期 ----------

    def recover_processing(self):
        """服务重启后处置 processing/ 残留（I-178 返修：未知结果不盲重试）。

        判定顺序：
        1. 台账已有 dispatch_result（成功或失败）→ 结果已知，直接采纳：
           按结果重建 done/ 或 failed/ 归档，不重执行；
        2. 台账只有 dispatch_start → 执行已开始，副作用可能已发生：
           - ledger_write 按动作对账（读登记库核对副作用是否落地），
             落地即采纳完成，未落地才转重试；
           - dispatch_new 会话号未知无法对账 → 保持 uncertain 归档，
             不重执行，交人工或上游处置；
           - dispatch_resume 可注入回读核对（reconciler）；
        3. 台账两者皆无 → 从未开始执行，转重试安全。"""
        proc_dir = os.path.join(self.root, "processing")
        for name in list(os.listdir(proc_dir)):
            if not name.endswith(".json") or name.endswith(".result.json"):
                continue
            rid = name[:-5]
            src = os.path.join(proc_dir, name)
            try:
                req = json.load(open(src, encoding="utf-8"))
            except (OSError, ValueError):
                req = {}
            evs = ledger_events(self.root, request_id=rid)
            has_result = any(e.get("event") == "dispatch_result" for e in evs)
            has_start = any(e.get("event") == "dispatch_start" for e in evs)
            if has_result:
                self._adopt_known_result(src, name, rid, evs)
            elif has_start:
                self._reconcile_started(src, name, rid, req, evs)
            else:
                self._safe_retry(src, name, rid,
                                 note="重启时未见 dispatch_start，执行未开始，转重试")

    def _adopt_known_result(self, src, name, rid, evs):
        """台账已有执行结果：重建归档，不重执行。"""
        result_evt = [e for e in evs if e.get("event") == "dispatch_result"][-1]
        ok = bool((result_evt.get("detail") or {}).get("ok"))
        sub = "done" if ok else "failed"
        result_record = {"request_id": rid, "attempt": result_evt.get("attempt"),
                         "claimed_at": result_evt.get("ts"),
                         "finished_at": result_evt.get("ts"),
                         "ok": ok,
                         "recovered_from": "queue_ledger",
                         "result": result_evt.get("detail") or {}}
        with open(_result_path(self.root, sub, name), "w", encoding="utf-8") as f:
            json.dump(result_record, f, ensure_ascii=False, indent=1)
        _atomic_move(src, os.path.join(self.root, sub, name))
        ledger_append("recovered_adopted", request_id=rid, file=name,
                      detail={"from": "processing", "to": sub,
                              "basis": "dispatch_result 在台账"}, root=self.root)

    def _reconcile_started(self, src, name, rid, req, evs):
        op = req.get("op")
        if op == "ledger_write":
            action = (req.get("ledger") or {}).get("action")
            args = (req.get("ledger") or {}).get("args") or {}
            reg = _registry_dir()
            if reg not in sys.path:
                sys.path.insert(0, reg)
            import cloud_client
            if cloud_client.enabled():
                try:
                    payload = self._cloud_ledger_payload(action, args, rid)
                    receipt = cloud_client.request("POST", "/api/agent/operation_receipt",
                              {"operation_id": rid, "action": action, "payload": payload})
                except Exception as exc:
                    _atomic_move(src, os.path.join(self.root, "uncertain", name))
                    ledger_append("uncertain_held", request_id=rid, file=name,
                                  detail={"error": repr(exc), "reason": "原写入回执不可核实"}, root=self.root)
                    return
                if receipt.get("recorded"):
                    record = {"request_id": rid, "ok": True, "recovered_from": "cloud_operation_receipt",
                              "result": {"bridge_ok": True, "deferred": False,
                                         "ledger_result": receipt["operation_result"], "reconciled": True}}
                    with open(_result_path(self.root, "done", name), "w", encoding="utf-8") as stream:
                        json.dump(record, stream, ensure_ascii=False, indent=1)
                    _atomic_move(src, os.path.join(self.root, "done", name))
                    ledger_append("recovered_adopted", request_id=rid, file=name,
                                  detail={"basis": "云端同一事务的原请求回执"}, root=self.root)
                else:
                    self._safe_retry(src, name, rid, note="原请求未入账；按同号幂等写入重试")
                return
            item_id = args.get("item_id")
            item = _registry_item(item_id) if item_id else None
            landed = False
            if action == "close" and item and item.get("status") == "closed":
                landed = True
            elif action == "block" and item and item.get("status") == "blocked":
                landed = True
            elif action == "claim" and item and \
                    item.get("claimed_by") == args.get("worker"):
                landed = True
            elif action == "release" and item and \
                    item.get("claimed_by") != args.get("worker"):
                landed = True
            elif action == "append_evidence" and item and \
                    args.get("evidence") in (item.get("evidence") or []):
                landed = True
            elif action == "get_item_line":
                landed = False                     # 只读无副作用
            if landed:
                result_record = {"request_id": rid,
                                 "attempt": (evs[-1] or {}).get("attempt"),
                                 "ok": True, "recovered_from": "side_effect_check",
                                 "result": {"bridge_ok": True, "deferred": False,
                                            "ledger_result": None,
                                            "reconciled": True}}
                with open(_result_path(self.root, "done", name), "w",
                          encoding="utf-8") as f:
                    json.dump(result_record, f, ensure_ascii=False, indent=1)
                _atomic_move(src, os.path.join(self.root, "done", name))
                ledger_append("recovered_adopted", request_id=rid, file=name,
                              detail={"from": "processing", "to": "done",
                                      "basis": "副作用对账已落地（%s）" % action},
                              root=self.root)
                return
            self._safe_retry(src, name, rid,
                             note="副作用对账未落地（%s），转重试" % action)
            return
        if op == "dispatch_resume" and self.resume_reconciler:
            try:
                hit = self.resume_reconciler(req.get("sid"), req.get("message", ""))
            except Exception:
                hit = None
            if hit:
                result_record = {"request_id": rid, "ok": True,
                                 "recovered_from": "readback_reconciler",
                                 "result": {"accepted": True,
                                            "readback_verified": True,
                                            "session": req.get("sid"),
                                            "reconciled": True}}
                with open(_result_path(self.root, "done", name), "w",
                          encoding="utf-8") as f:
                    json.dump(result_record, f, ensure_ascii=False, indent=1)
                _atomic_move(src, os.path.join(self.root, "done", name))
                ledger_append("recovered_adopted", request_id=rid, file=name,
                              detail={"from": "processing", "to": "done",
                                      "basis": "会话回读命中标志"}, root=self.root)
                return
        # dispatch_new（会话未知）与对账不出的 dispatch_resume：保持未知，
        # 不重执行。
        _atomic_move(src, os.path.join(self.root, "uncertain", name))
        ledger_append("uncertain_held", request_id=rid, file=name,
                      detail={"from": "processing", "to": "uncertain",
                              "op": op,
                              "note": "执行已开始但结果无法对账，保持未知不重执行"},
                      root=self.root)

    def _safe_retry(self, src, name, rid, note):
        retry_dir = os.path.join(self.root, "retry")
        dst = os.path.join(retry_dir, name)
        if os.path.exists(dst):
            os.remove(src)
            return
        _atomic_move(src, dst)
        meta = {"attempts": 0, "next_retry_at": _ts(),
                "last_error": note, "recovered_after_restart": True}
        with open(_meta_path(self.root, "retry", name), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        ledger_append("recovered_after_restart", request_id=rid, file=name,
                      detail={"from": "processing", "to": "retry", "note": note},
                      root=self.root)

    def move_due_retries(self):
        """到期重试回送 incoming/，事件 retry_due 留痕。"""
        retry_dir = os.path.join(self.root, "retry")
        now = _now()
        for name in list(os.listdir(retry_dir)):
            if not name.endswith(".json") or name.endswith(".meta.json"):
                continue
            mp = _meta_path(self.root, "retry", name)
            try:
                meta = json.load(open(mp, encoding="utf-8"))
            except (OSError, ValueError):
                meta = {"attempts": 0, "next_retry_at": _ts()}
            due = meta.get("next_retry_at", "")
            try:
                due_dt = datetime.datetime.fromisoformat(due)
            except ValueError:
                due_dt = now
            if due_dt > now:
                continue
            _atomic_move(os.path.join(retry_dir, name),
                         os.path.join(self.root, "incoming", name))
            try:
                os.remove(mp)
            except OSError:
                pass
            ledger_append("retry_due", request_id=name[:-5], file=name,
                          detail={"attempts": meta.get("attempts", 0)},
                          root=self.root)

    # ---------- 主循环 ----------

    def poll_once(self):
        # I-208：maintenance halt 停新派发（在途对账与结果回写继续）。
        ctrl_path = os.path.join(self.root, "control.json")
        try:
            mode = json.load(open(ctrl_path, encoding="utf-8")).get("mode")
        except (OSError, ValueError):
            mode = None
        self.reconcile_uncertain()
        self.move_due_retries()
        inc = os.path.join(self.root, "incoming")
        try:
            names = sorted(os.listdir(inc), key=lambda n: os.path.getmtime(os.path.join(inc, n)))
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".json") or name.endswith(".meta.json"):
                continue
            path = os.path.join(inc, name)
            if mode == "halt":
                try:
                    queued = json.load(open(path, encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if queued.get("op") not in ("ledger_read", "ledger_write"):
                    continue
            if self.clock() - os.path.getmtime(path) < self.stable_grace_s:
                continue
            if not self._handle_incoming(name, path):
                continue

    def reconcile_uncertain(self):
        """Accepted/unknown sends are queried using their original request id.

        The host's durable request record prevents a second session/send.
        Legacy uncertain requests without that record remain held for review.
        """
        for name in os.listdir(os.path.join(self.root, "uncertain")):
            if not name.endswith(".json") or name.endswith((".meta.json", ".result.json")):
                continue
            path = os.path.join(self.root, "uncertain", name)
            try:
                req = json.load(open(path, encoding="utf-8"))
                if self.dispatcher is None:
                    import host_service
                    result = host_service.op_reconcile(req["request_id"])
                elif self.dispatch_reconciler is not None:
                    result = self.dispatch_reconciler(req)
                else:
                    continue
                if result.get("ready_to_retry") and result.get("definitive_rejection"):
                    _atomic_move(path,os.path.join(self.root,"incoming",name))
                    ledger_append("busy_input_ready",request_id=req["request_id"],file=name,
                                  detail={"basis":"SDK rejected input; recorded prior turn completed"},root=self.root)
                    continue
                if not result.get("readback_verified"):
                    continue
                rec = {"request_id": req["request_id"], "ok": True,
                       "recovered_from": "durable_request_readback", "result": result}
                with open(_result_path(self.root, "done", name), "w", encoding="utf-8") as stream:
                    json.dump(rec, stream, ensure_ascii=False, indent=1)
                _atomic_move(path, os.path.join(self.root, "done", name))
                ledger_append("completed", request_id=req["request_id"], file=name,
                              detail={"session": result.get("session"), "reconciled": True}, root=self.root)
            except Exception:
                continue

    def _handle_incoming(self, name, path):
        rid = name[:-5]
        try:
            raw = open(path, encoding="utf-8").read()
            req, err = validate_request(json.loads(raw))
        except (OSError, ValueError) as e:
            req, err = None, "解析失败：%s" % e
        if err:
            n = self._bad_scans.get(name, 0) + 1
            self._bad_scans[name] = n
            if n >= INVALID_AFTER_SCANS:
                _atomic_move(path, os.path.join(self.root, "invalid", name))
                ledger_append("invalid", request_id=rid, file=name,
                              detail={"reason": err, "scans": n}, root=self.root)
                self._bad_scans.pop(name, None)
            return False
        self._bad_scans.pop(name, None)
        # 首见即记 received：人工投放与程序化投递统一在台账留下接收
        # 事件（程序化路径 write_request 已记的这里不重复记）。
        if not any(e.get("event") == "received" for e in
                   ledger_events(self.root, request_id=rid)):
            ledger_append("received", request_id=rid, file=name,
                          detail={"op": req["op"], "kind": req.get("kind", "task"),
                                  "source": "file"}, root=self.root)
        # 幂等：已完成的 request_id 不重复派发。
        if rid in self.completed_request_ids():
            _atomic_move(path, os.path.join(self.root, "done", name))
            ledger_append("duplicate_skipped", request_id=rid, file=name,
                          detail={"reason": "request_id 已有 completed 记录，不重复派发"},
                          root=self.root)
            return True
        proc = os.path.join(self.root, "processing", name)
        try:
            _atomic_move(path, proc)
        except OSError:
            return False        # 已被并发认领（单线程下防御性保留）
        mtime = os.path.getmtime(proc)
        claimed_ts = _now()
        ledger_append("claimed", request_id=rid, file=name,
                      detail={"detection_latency_s": round(claimed_ts.timestamp()
                                                           - mtime, 3)},
                      root=self.root)
        self._execute(name, rid, req, claimed_ts)
        return True

    def _execute(self, name, rid, req, claimed_ts):
        attempt = 1
        mp = _meta_path(self.root, "processing", name)
        # 重试回来的文件可能自带 attempt 计数（meta 由 move_due_retries 清理，
        # 计数从台账取，避免 meta 与台账双权威）。
        attempt = 1 + sum(1 for e in ledger_events(self.root, request_id=rid)
                          if e.get("event") == "retry_scheduled")
        is_ledger = req["op"] in ("ledger_write", "ledger_read")
        ledger_append("dispatch_start", request_id=rid, file=name, attempt=attempt,
                      detail={"op": req["op"],
                              "action": req.get("ledger", {}).get("action"),
                              "agent_id": req.get("agent_id"),
                              "lane": req.get("lane"),
                              "item_id": req.get("item_id")
                                        or req.get("ledger", {}).get("args", {}).get("item_id"),
                              "kind": req.get("kind", "task")},
                      root=self.root)
        # 演练崩溃点一：已记 dispatch_start、尚未执行（重启后安全重试）。
        if os.environ.get("DQ_CRASH_POINT") == "after_dispatch_start_before_execute":
            sys.stdout.flush()
            os._exit(77)
        try:
            result = self._dispatch(req)
        except Exception as e:
            result = {"ok": False, "error": repr(e)}
        # 演练崩溃点二：副作用已发生、dispatch_result 未入台账（重启后
        # 必须对账或保持未知，禁止直接重执行）。
        if os.environ.get("DQ_CRASH_POINT") == "after_side_effect_before_result":
            sys.stdout.flush()
            os._exit(77)
        done_ts = _now()
        if is_ledger:
            ok = bool(result.get("bridge_ok"))
            definitive_fail = (not ok) and bool(result.get("definitive"))
        else:
            ok = bool(result.get("readback_verified"))
            definitive_fail = False
        ledger_append("dispatch_result", request_id=rid, file=name, attempt=attempt,
                      detail={"ok": ok, "session": result.get("session"),
                              "channel": result.get("channel"),
                              "accepted": result.get("accepted"),
                              "readback_verified": result.get("readback_verified"),
                              "deferred": result.get("deferred"),
                              "error": result.get("error"),
                              "duration_s": round((done_ts - claimed_ts).total_seconds(), 3)},
                      root=self.root)
        result_record = {"request_id": rid, "attempt": attempt,
                         "claimed_at": claimed_ts.isoformat(timespec="milliseconds"),
                         "finished_at": done_ts.isoformat(timespec="milliseconds"),
                         "dispatch_duration_s": round((done_ts - claimed_ts).total_seconds(), 3),
                         "ok": ok, "result": result}
        if not is_ledger and (result.get("uncertain") or
                              (result.get("accepted") and not ok)):
            _write_json_retry(_result_path(self.root, "uncertain", name),
                              result_record)
            _atomic_move(os.path.join(self.root, "processing", name),
                         os.path.join(self.root, "uncertain", name))
            ledger_append("uncertain", request_id=rid, file=name,
                          detail={"session": result.get("session"),
                                  "reason": "send accepted or unknown; reconcile without resending"}, root=self.root)
            if os.path.exists(mp):
                os.remove(mp)
            return
        if ok:
            rp = _result_path(self.root, "processing", name)
            _write_json_retry(rp, result_record)
            _atomic_move(os.path.join(self.root, "processing", name),
                         os.path.join(self.root, "done", name))
            _atomic_move(rp, _result_path(self.root, "done", name))
            ledger_append("completed", request_id=rid, file=name, attempt=attempt,
                          detail={"session": result.get("session"),
                                  "channel": result.get("channel")},
                          root=self.root)
        else:
            max_att = int(req.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
            # 定性失败（云端明确拒绝或本地校验拒绝）不重试：重试不会改变结局。
            if attempt < max_att and not definitive_fail:
                delay = int(req.get("retry_delay_s", DEFAULT_RETRY_DELAY_S))
                meta = {"attempts": attempt,
                        "next_retry_at": (_now() + datetime.timedelta(seconds=delay)).isoformat(timespec="seconds"),
                        "last_error": str(result.get("error") or "回读核验未通过")[:300]}
                retry_file = os.path.join(self.root, "retry", name)
                _atomic_move(os.path.join(self.root, "processing", name), retry_file)
                with open(_meta_path(self.root, "retry", name), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=1)
                ledger_append("retry_scheduled", request_id=rid, file=name,
                              attempt=attempt,
                              detail={"next_retry_at": meta["next_retry_at"],
                                      "last_error": meta["last_error"],
                                      "max_attempts": max_att},
                              root=self.root)
            else:
                rp = _result_path(self.root, "failed", name)
                result_record["final_error"] = str(result.get("error") or "回读核验未通过")[:300]
                result_record["definitive"] = definitive_fail
                with open(rp, "w", encoding="utf-8") as f:
                    json.dump(result_record, f, ensure_ascii=False, indent=1)
                _atomic_move(os.path.join(self.root, "processing", name),
                             os.path.join(self.root, "failed", name))
                ledger_append("failed_final", request_id=rid, file=name, attempt=attempt,
                              detail={"attempts": attempt, "max_attempts": max_att,
                                      "definitive": definitive_fail,
                                      "last_error": result_record["final_error"]},
                              root=self.root)
        try:
            if os.path.exists(mp):
                os.remove(mp)
        except OSError:
            pass

    def _dispatch(self, req):
        if req["op"] == "ledger_write":
            return self._ledger_write(req)
        if req["op"] == "ledger_read":
            return self._ledger_read(req)
        if self.dispatcher is not None:
            return self.dispatcher(req)
        import host_service
        if req["op"] == "dispatch_resume":
            return host_service.op_dispatch_resume(req["sid"], req["message"],
                                                   verify=req.get("verify", True), request_id=req["request_id"],
                                                   item_id=req.get("item_id"), run_id=req.get("run_id"))
        return host_service.op_dispatch_new(req["title"], req["message"],
                                            verify=req.get("verify", True),
                                            item_id=req.get("item_id"),
                                            lane=req.get("lane"),
                                            kind=req.get("kind", "task"), request_id=req["request_id"],
                                            run_id=req.get("run_id"),
                                            worker_id=req.get("worker_id"))

    # ---------- I-178 账本读代执行 ----------

    def _ledger_read(self, req):
        """代执行账本只读（无副作用，可安全重读）。"""
        if self.ledger_read_executor is not None:
            return self.ledger_read_executor(req["ledger"]["action"],
                                             dict(req["ledger"].get("args", {})))
        try:
            line = _read_item_line(req["ledger"]["args"]["item_id"])
        except RuntimeError as e:
            if "云端不可达" in str(e):
                return {"bridge_ok": False, "retryable": True, "error": str(e)}
            return {"bridge_ok": False, "definitive": True, "error": str(e)}
        except Exception as e:
            return {"bridge_ok": False, "retryable": True, "error": repr(e)}
        return {"bridge_ok": True, "deferred": False, "ledger_result": line}

    # ---------- I-177 账本写代执行 ----------

    def _ledger_write(self, req):
        """代外援执行账本写。结果三态：bridge_ok 真（含 deferred 断网
        转发件箱）、bridge_ok 假带 definitive（定性失败不重试）、
        bridge_ok 假可重试（异常与未知）。"""
        led = req["ledger"]
        action, args = led["action"], dict(led.get("args", {}))
        if self.ledger_executor is not None:
            return self.ledger_executor(action, args)
        if _ITEMS_REGISTRY not in sys.path:
            sys.path.insert(0, _ITEMS_REGISTRY)
        import cloud_client
        before = cloud_client.outbox_size()
        try:
            result = self._call_ledger(action, args, req.get("request_id"))
        except RuntimeError as e:
            # 需要云端分配结果的调用断网时直接抛错且不入队：可安全重试。
            if "云端不可达" in str(e):
                return {"bridge_ok": False, "retryable": True, "error": str(e)}
            return {"bridge_ok": False, "definitive": True, "error": str(e)}
        except (ValueError, KeyError) as e:
            # 登记库客户端校验拒绝或事项不存在：定性失败。
            return {"bridge_ok": False, "definitive": True, "error": repr(e)}
        except Exception as e:
            return {"bridge_ok": False, "retryable": True, "error": repr(e)}
        after = cloud_client.outbox_size()
        if after > before:
            return {"bridge_ok": True, "deferred": True,
                    "ledger_result": None,
                    "note": "云端不可达，操作已入本地断网队列，由同步代理恢复后按序重放"}
        return {"bridge_ok": True, "deferred": False, "ledger_result": result}

    @staticmethod
    def _cloud_ledger_payload(action, args, request_id):
        payload = dict(args)
        payload["operation_id"] = request_id
        if action in ("close", "block"):
            import ledger
            ledger._check_owner_note(payload.get("owner_note", ""))
            evidence = payload.get("evidence") or []
            if isinstance(evidence, str):
                evidence = [evidence]
            payload["evidence"] = ["机主说明：" + payload["owner_note"].strip()] + list(evidence)
        return payload

    def _call_ledger(self, action, a, request_id=None):
        import cloud_client
        if cloud_client.enabled():
            paths = {"claim": "/api/dispatch/claim", "release": "/api/dispatch/release",
                     "append_evidence": "/api/ledger/append_evidence", "close": "/api/ledger/close",
                     "block": "/api/ledger/block", "submit_pending_review": "/api/ledger/submit_pending_review",
                     "set_criterion": "/api/ledger/set_criterion", "owner_reply": "/api/ledger/owner_reply"}
            return cloud_client.request("POST", paths[action], self._cloud_ledger_payload(action, a, request_id))
        if action == "submit_pending_review":
            import cloud_client
            if cloud_client.enabled():
                return cloud_client.request("POST", "/api/ledger/submit_pending_review", a)
            reg = _registry_dir()
            if reg not in sys.path:
                sys.path.insert(0, reg)
            import ledger
            return ledger.submit_pending_review(**a)
        import dispatch as reg_dispatch
        import ledger as reg_ledger
        if action == "claim":
            return reg_dispatch.claim(a["item_id"], a["worker"],
                                      lease_hours=int(a.get("lease_hours", 4)),
                                      session_id=a.get("session_id"))
        if action == "release":
            return reg_dispatch.release(a["item_id"], a["worker"])
        if action == "append_evidence":
            return reg_ledger.append_evidence(a["item_id"], a["evidence"])
        if action == "close":
            return reg_ledger.close(a["item_id"], a["evidence"],
                                    closer=a.get("closer") or a.get("worker", ""),
                                    conclusion=a["conclusion"],
                                    criterion_check=a["criterion_check"],
                                    owner_note=a["owner_note"])
        if action == "block":
            return reg_ledger.block(a["item_id"], a["reason"], a["blocker_type"],
                                    a["needs"], a["evidence"],
                                    reporter=a.get("reporter") or a.get("worker", ""),
                                    owner_note=a["owner_note"])
        if action == "set_criterion":
            return reg_ledger.set_criterion(a["item_id"], int(a["index"]),
                                            bool(a["verified"]),
                                            evidence=a.get("evidence", ""),
                                            actor=a.get("actor", ""))
        if action == "owner_reply":
            return reg_ledger.owner_reply(a["item_id"], a["text"],
                                          actor=a.get("actor", "机主"))
        raise ValueError("不支持的账本动作：%s" % action)

    def run(self):
        """常驻轮询：每 POLL_INTERVAL_S 一轮（队列心跳）。

        防死纪律：单轮异常（含台账写失败）只留痕不退出；台账也写
        不进时静默计数并继续轮询，工作线程不因单轮异常整体死亡。"""
        self.recover_processing()
        silent_errors = 0
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:
                try:
                    ledger_append("poll_error", detail={"error": repr(e)},
                                  root=self.root)
                except Exception:
                    silent_errors += 1
            self._stop.wait(self.poll_interval_s)

    def stop(self):
        self._stop.set()


# ---------------- 自检（假派发器，隔离队列，不碰真实会话） ----------------

def _selftest():
    import tempfile
    root = ensure_layout(os.path.join(tempfile.mkdtemp(prefix="dq_selftest_")))
    calls = []

    def flaky_dispatcher(req):
        calls.append(req["request_id"])
        if req["request_id"] == "req-fail-once":
            if calls.count("req-fail-once") == 1:
                raise RuntimeError("构造的第一次失败")
            return {"ok": True, "session": "sess_fake", "channel": "test",
                    "accepted": True, "readback_verified": True}
        if req["request_id"] == "req-always-fail":
            raise RuntimeError("构造的持续失败")
        return {"ok": True, "session": "sess_fake", "channel": "test",
                "accepted": True, "readback_verified": True}

    w = QueueWorker(root, dispatcher=flaky_dispatcher, poll_interval_s=0.05,
                    stable_grace_s=0)
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print("[%s] %s %s" % ("过" if ok else "拒", name, detail))

    # 场景一：成功路径全事件链。
    write_request({"schema_version": 1, "request_id": "req-ok-0001",
                   "op": "dispatch_new", "message": "测试简报", "kind": "test"},
                  root=root)
    w.poll_once()
    evs = [e["event"] for e in ledger_events(root, request_id="req-ok-0001")]
    check("成功路径事件链", evs[:2] == ["received", "claimed"] and
          "dispatch_start" in evs and "dispatch_result" in evs and "completed" in evs,
          str(evs))
    check("成功归档 done", os.path.exists(os.path.join(root, "done", "req-ok-0001.json"))
          and os.path.exists(os.path.join(root, "done", "req-ok-0001.result.json")))
    lat = [e for e in ledger_events(root, request_id="req-ok-0001")
           if e["event"] == "claimed"][0]["detail"]["detection_latency_s"]
    check("认领时延记录", isinstance(lat, (int, float)), str(lat))

    # 场景二：重复递交不重复派发。
    write_request({"schema_version": 1, "request_id": "req-ok-0001",
                   "op": "dispatch_new", "message": "测试简报", "kind": "test"},
                  root=root)
    w.poll_once()
    dup = [e for e in ledger_events(root, request_id="req-ok-0001")
           if e["event"] == "duplicate_skipped"]
    check("重复递交幂等跳过", len(dup) == 1 and calls.count("req-ok-0001") == 1,
          "派发调用 %d 次" % calls.count("req-ok-0001"))

    # 场景三：失败一次后重试成功。
    write_request({"schema_version": 1, "request_id": "req-fail-once",
                   "op": "dispatch_new", "message": "重试简报", "kind": "test",
                   "retry_delay_s": 0}, root=root)
    w.poll_once()
    check("首次失败进 retry", os.path.exists(os.path.join(root, "retry", "req-fail-once.json"))
          and os.path.exists(os.path.join(root, "retry", "req-fail-once.meta.json")))
    # next_retry_at 已到期（delay 0），下一轮应回送并成功。
    w.poll_once()
    evs3 = [e["event"] for e in ledger_events(root, request_id="req-fail-once")]
    check("重试后成功", "retry_due" in evs3 and "completed" in evs3
          and calls.count("req-fail-once") == 2, str(evs3))

    # 场景四：重试耗尽进 failed。
    write_request({"schema_version": 1, "request_id": "req-always-fail",
                   "op": "dispatch_new", "message": "耗尽简报", "kind": "test",
                   "max_attempts": 2, "retry_delay_s": 0}, root=root)
    w.poll_once()
    w.poll_once()
    w.poll_once()
    evs4 = [e["event"] for e in ledger_events(root, request_id="req-always-fail")]
    check("重试耗尽进 failed", "failed_final" in evs4
          and calls.count("req-always-fail") == 2
          and os.path.exists(os.path.join(root, "failed", "req-always-fail.json"))
          and os.path.exists(os.path.join(root, "failed", "req-always-fail.result.json")),
          str(evs4))

    # 场景五：坏文件进 invalid 留痕。
    bad = os.path.join(root, "incoming", "req-badxxx.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not json")
    for _ in range(INVALID_AFTER_SCANS):
        w.poll_once()
    check("坏文件进 invalid", os.path.exists(os.path.join(root, "invalid", "req-badxxx.json")))

    # 场景六：重启恢复 processing 残留。
    leftover = os.path.join(root, "processing", "req-crash001.json")
    with open(leftover, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "request_id": "req-crash001",
                   "op": "dispatch_new", "message": "中断简报", "kind": "test"},
                  f, ensure_ascii=False)
    w2 = QueueWorker(root, dispatcher=flaky_dispatcher, poll_interval_s=0.05,
                     stable_grace_s=0)
    w2.recover_processing()
    w2.poll_once()
    evs6 = [e["event"] for e in ledger_events(root, request_id="req-crash001")]
    check("重启恢复残留", "recovered_after_restart" in evs6 and "completed" in evs6,
          str(evs6))

    # 场景七：字段校验拒绝缺 message 的请求。
    _, err = validate_request({"schema_version": 1, "request_id": "req-nomsg",
                               "op": "dispatch_new"})
    check("缺 message 被拒", err == "message 必须为非空字符串", err)

    # 场景八：人工投放（不经 write_request）也有 received 事件。
    manual = os.path.join(root, "incoming", "req-manual01.json")
    with open(manual, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "request_id": "req-manual01",
                   "op": "dispatch_new", "message": "人工投放简报", "kind": "test"},
                  f, ensure_ascii=False)
    w.poll_once()
    evs8 = [e["event"] for e in ledger_events(root, request_id="req-manual01")]
    check("人工投放首见记 received", evs8[:2] == ["received", "claimed"],
          str(evs8))

    # 场景九至十二：I-177 文件桥账本写四形态（假执行器隔离验证）。
    ledger_calls = []

    def fake_ledger(action, args):
        ledger_calls.append("%s:%s" % (action, args.get("item_id")))
        iid = args.get("item_id")
        if iid == "I-DEFINITE":
            return {"bridge_ok": False, "definitive": True, "error": "构造的定性拒绝"}
        if iid == "I-RETRYBR":
            if ledger_calls.count("claim:I-RETRYBR") == 1:
                return {"bridge_ok": False, "retryable": True, "error": "构造的可重试失败"}
            return {"bridge_ok": True, "deferred": False,
                    "ledger_result": {"claimed": "I-RETRYBR"}}
        if iid == "I-DEFERBR":
            return {"bridge_ok": True, "deferred": True, "ledger_result": None,
                    "note": "测试断网转入发件箱"}
        return {"bridge_ok": True, "deferred": False, "ledger_result": {"claimed": iid}}

    wb = QueueWorker(root, ledger_executor=fake_ledger, poll_interval_s=0.05,
                     stable_grace_s=0)

    def bridge_req(rid, iid, action="claim"):
        return {"schema_version": 1, "request_id": rid, "op": "ledger_write",
                "agent_id": "selftest-agent",
                "ledger": {"action": action, "args": {"item_id": iid, "worker": "L3队"}},
                "retry_delay_s": 0}

    write_request(bridge_req("br-ok-0001", "I-OKBR"), root=root)
    wb.poll_once()
    res9 = json.load(open(os.path.join(root, "done", "br-ok-0001.result.json"),
                          encoding="utf-8"))
    check("账本写成功回写结果", res9["ok"] and
          res9["result"]["bridge_ok"] and res9["result"]["ledger_result"] == {"claimed": "I-OKBR"})

    write_request(bridge_req("br-def0001", "I-DEFINITE"), root=root)
    wb.poll_once()
    evs10 = [e["event"] for e in ledger_events(root, request_id="br-def0001")]
    check("定性失败不重试", evs10[-1] == "failed_final" and
          ledger_calls.count("claim:I-DEFINITE") == 1 and
          os.path.exists(os.path.join(root, "failed", "br-def0001.result.json")),
          str(evs10))

    write_request(bridge_req("br-retry01", "I-RETRYBR"), root=root)
    wb.poll_once()
    wb.poll_once()
    evs11 = [e["event"] for e in ledger_events(root, request_id="br-retry01")]
    check("账本写失败可重试后成功", "completed" in evs11 and
          ledger_calls.count("claim:I-RETRYBR") == 2, str(evs11))

    write_request(bridge_req("br-defer01", "I-DEFERBR"), root=root)
    wb.poll_once()
    res12 = json.load(open(os.path.join(root, "done", "br-defer01.result.json"),
                           encoding="utf-8"))
    check("断网转入发件箱记 deferred", res12["ok"] and res12["result"]["deferred"]
          and ledger_calls.count("claim:I-DEFERBR") == 1)

    bad_br = {"schema_version": 1, "request_id": "br-noagent",
              "op": "ledger_write",
              "ledger": {"action": "claim", "args": {"item_id": "I-x", "worker": "L3队"}}}
    rejected13, err13 = validate_request(bad_br)
    check("缺 agent_id 被拒", rejected13 is None and "agent_id" in err13, err13)

    bad_count = sum(1 for _, ok, _ in checks if not ok)
    print("自检总判定：%s（%d/%d 过）" % ("全部通过" if bad_count == 0 else "存在不过项",
                                        len(checks) - bad_count, len(checks)))
    print("隔离队列根：%s" % root)
    return 0 if bad_count == 0 else 1


def _status_cli():
    w = QueueWorker()
    print(json.dumps(w.status(), ensure_ascii=False, indent=1))


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    if "--status" in argv:
        _status_cli()
        return 0
    if "--submit" in argv:
        i = argv.index("--submit")
        src = argv[i + 1] if len(argv) > i + 1 else None
        if not src:
            print("用法：--submit <请求JSON文件路径>")
            return 2
        req = json.load(open(src, encoding="utf-8"))
        norm, err = validate_request(req)
        if err:
            print("[拒] %s" % err)
            return 1
        path = write_request(norm)
        print("[过] 请求已投放入队：%s" % path)
        return 0
    print("用法：python dispatch_file_queue.py --selftest | --status | --submit <文件>")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
