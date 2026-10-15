# -*- coding: utf-8 -*-
"""lane_workers.py — I-217 业务线多工位核心：配置、工位注册表、冲突准入。

模型（判据一）：
  业务线（lane）与执行工位（worker）分离。每线并发上限可配置为一或
  二（上限二，防误配），缺省一；L3 试点二。工位标识固定为
  <lane>-w<N>，重启不变。每工位一个独立会话、一个独立执行器（宿主
  侧按工位键池化，见 host_service.ensure_child）。

持久件：
  工位注册表 lane_workers_roster.json（环境变量 LP_LANE_WORKERS_ROSTER
  可重定向，隔离测试用）：worker_id → {lane, session_id, item_id,
  run_id, phase, executor_pid, updated_at}。这是工位与运行的持久映射
  （判据三：重启前后回读一致），只增改不删行，工位空闲时 phase=idle
  并保留最后会话与履历入口（观察窗兼容要求）。

准入（判据四）：
  并行任务声明可写范围 writable_paths 与独占资源 exclusive_resources。
  两个同线任务同时活跃的必要条件：双方声明完整且互不重叠。可写路径
  前缀重叠或独占资源同名即拒绝并行准入，保留等待原因；缺声明的任务
  只能独占该线（不能并行），不是被拒之门外。

仅使用 Python 标准库。本模块不联网、不起进程；被内核与宿主共同引用。
"""
import json
import os

MAX_CONCURRENCY = 2                   # 上限二（机主批准范围：先双工位）

DEFAULT_ROSTER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "lane_workers_roster.json")


def roster_path():
    return os.environ.get("LP_LANE_WORKERS_ROSTER", DEFAULT_ROSTER_PATH)


# ---------- 每线并发配置 ----------

def load_lane_config(root):
    """读每线并发上限配置（内核状态根下 lane_workers.json）。

    形态 {"default": 1, "lanes": {"L3": 2}}；缺省文件返回全线上限一。上限截到
    [1, MAX_CONCURRENCY]，非法值回退一。"""
    path = os.path.join(root, "lane_workers.json")
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    default = _clamp(cfg.get("default", 1))
    lanes = {}
    for lane, v in (cfg.get("lanes") or {}).items():
        lanes[lane] = _clamp(v)
    return {"default": default, "lanes": lanes}


def _clamp(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_CONCURRENCY, n))


def concurrency_limit(config, lane):
    if not lane:
        return config["default"]
    return config["lanes"].get(lane, config["default"])


def worker_ids(lane, limit):
    """固定工位标识：<lane>-w1 … <lane>-wN，重启不变。"""
    return ["%s-w%d" % (lane, i) for i in range(1, limit + 1)]


# ---------- 工位注册表（持久） ----------

def roster_load():
    try:
        return json.load(open(roster_path(), encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "workers": {}}


def roster_save(obj):
    path = roster_path()
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def worker_bind(worker_id, lane, clear=(), **fields):
    """登记或更新工位（合并写）。字段：session_id、item_id、run_id、
    phase、executor_pid 等。None 值默认跳过（保留履历）；列入 clear 的
    键显式清空（工位转空闲时清运行归属、保留会话履历）。"""
    import datetime
    obj = roster_load()
    rec = obj.setdefault("workers", {}).setdefault(worker_id, {"lane": lane})
    rec["lane"] = lane or rec.get("lane")
    rec.update({k: v for k, v in fields.items() if v is not None})
    for k in clear:
        rec.pop(k, None)
    rec["updated_at"] = datetime.datetime.now().isoformat(timespec="milliseconds")
    roster_save(obj)
    return rec


def worker_lookup(worker_id):
    return roster_load().get("workers", {}).get(worker_id)


def worker_by_session(sid):
    """会话号反查工位（宿主执行键解析与守门定向用）。"""
    if not sid:
        return None
    for wid, rec in roster_load().get("workers", {}).items():
        if rec.get("session_id") == sid:
            return wid
    return None


def worker_by_run(run_id):
    if not run_id:
        return None
    for wid, rec in roster_load().get("workers", {}).items():
        if rec.get("run_id") == run_id:
            return wid
    return None


def lane_workers_view(lane):
    """某线全部工位的注册表视图（观察窗接口用）。"""
    out = []
    for wid, rec in sorted(roster_load().get("workers", {}).items()):
        if rec.get("lane") == lane:
            out.append({"worker_id": wid, **rec})
    return out


# ---------- 可写范围与独占资源准入（判据四） ----------

def normalize_path(p):
    """路径归一：绝对化、正斜杠、Windows 大小写不敏感。"""
    p = str(p).strip().replace("\\", "/")
    if not p:
        return ""
    if not os.path.isabs(p) and not p.startswith("./"):
        p = "./" + p
    p = os.path.abspath(p).replace("\\", "/")
    return p.rstrip("/").lower()


def paths_overlap(a, b):
    """两条声明路径是否重叠：一方是另一方的前缀目录，或同一路径。"""
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def declaration_of(task):
    """从任务（内核 task 记录）取声明，缺省空。"""
    decl = {"writable_paths": [normalize_path(p) for p in
                               (task.get("writable_paths") or [])],
            "exclusive_resources": [str(r) for r in
                                    (task.get("exclusive_resources") or [])]}
    return decl


def has_declaration(task):
    return bool(task.get("writable_paths") or task.get("exclusive_resources"))


def check_parallel_admission(candidate, active_tasks):
    """判据四准入判定：candidate（任务记录）与同线已活跃任务集。

    返回 (ok, reason)。ok=True 才允许并行进入第二及以上工位。
    规则：
      一、candidate 无声明：不允许并行（reason 说明缺声明），该线
          空闲时仍可单独执行（由调用方按槽位判断）；
      二、任一活跃任务无声明：同理不允许并行；
      三、可写路径与任一活跃任务重叠：拒绝，reason 列出重叠路径；
      四、独占资源同名：拒绝，reason 列出冲突资源；
      五、互不冲突：放行。"""
    if not has_declaration(candidate):
        return False, "缺可写范围与独占资源声明，不能并行准入；仅可独占本线执行"
    cand = declaration_of(candidate)
    for other in active_tasks:
        label = other.get("task_req_id") or other.get("item_id") or "?"
        if not has_declaration(other):
            return False, "活跃任务 %s 缺声明，保守拒绝并行（不能同时改共享文件）" % label
        oth = declaration_of(other)
        for cp in cand["writable_paths"]:
            for op in oth["writable_paths"]:
                if paths_overlap(cp, op):
                    return False, ("可写范围与活跃任务 %s 重叠：%s ∩ %s"
                                   % (label, cp, op))
        for res in cand["exclusive_resources"]:
            if res in oth["exclusive_resources"]:
                return False, ("独占资源与活跃任务 %s 冲突：%s"
                               % (label, res))
    return True, "声明互不重叠，允许并行"


# ---------- 槽位视图（内核派发用） ----------

ACTIVE_PHASES = ("submitted", "dispatched", "result_pending", "uncertain",
                 "retry_wait")

# 持槽相位：任务从分配工位到终态前一直占位（含 line_pending/line_wait/
# ready 等待线检查的中间态），与旧单执行槽的占位口径一致——否则任务线
# 检查期间工位会被重复分配。
TERMINAL_PHASES = ("completed", "dispatch_failed", "result_failed",
                   "line_rejected", "retired")


def lane_slots(tasks_by_req, lane, config):
    """计算某线工位占用：返回 {worker_id: task_req_id}（持工位号且未到
    终态的任务）与空闲工位号列表。tasks_by_req: req_id → task 记录。"""
    limit = concurrency_limit(config, lane)
    ids = worker_ids(lane, limit)
    busy = {}
    for req, t in tasks_by_req.items():
        if t.get("lane") != lane:
            continue
        if t.get("worker_id") in ids and t.get("phase") not in TERMINAL_PHASES:
            busy[t["worker_id"]] = req
    free = [w for w in ids if w not in busy]
    return busy, free
