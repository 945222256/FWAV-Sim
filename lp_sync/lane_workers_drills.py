# -*- coding: utf-8 -*-
"""lane_workers_drills.py — I-217 业务线双工位并行的全隔离验证套件。

对应八项判据的样例（判据八由本套件的隔离性自身与输出清单满足）：
  LW1 业务线与工位分离、上限配置、单工位兼容
  LW2 同线两任务真实重叠执行 + 第三项排队 + 自动接续 + 数量不超限
  LW3 重复入队/双消费者/认领到期/未知回执去重 + 工位映射重启一致
  LW4 可写范围与独占资源准入四样例
  LW5 单工位故障隔离（只杀工位一执行器）+ 生产 kill_child 拒绝
  LW6 守门按会话定向（两项同线在办、旧会话保留、重复交接抑制）
  LW7 接口契约逐字段回读 + 通知关联与零重复零丢失

隔离件：独立队列根、内核状态根、登记库副本、工位注册表、端口。
不触碰生产宿主、生产内核、生产队列与生产云端。仅标准库。

用法：python lane_workers_drills.py [输出目录]
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch_file_queue as dq
import lane_workers as lw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
PORT = 58891

OUT = os.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "logs", "i217_lane_workers_drills_%s"
    % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
KS = os.path.join(OUT, "kernel_state")
QR = os.path.join(OUT, "dispatch_queue")
REG = os.path.join(OUT, "registry_copy")
ROSTER = os.path.join(OUT, "lane_workers_roster.json")
DISPATCH_LOG = os.path.join(OUT, "dispatch_log.json")
EXEC_DIR = os.path.join(OUT, "executors")

_checks = []


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail), flush=True)
    return bool(ok)


def jload(path):
    for _ in range(3):
        try:
            return json.load(open(path, encoding="utf-8"))
        except PermissionError:
            time.sleep(0.02)
        except (OSError, ValueError):
            return None
    return None


def wait_for(predicate, timeout_s=40, step=0.3):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def write_task(item_id, msg, task_kind="task", paths=None, resources=None,
               request_id=None, lane="L3"):
    rid = request_id or ("kt-%s" % item_id.lower())
    payload = {"schema_version": 1, "kind": "task_submit",
               "request_id": rid, "item_id": item_id, "lane": lane,
               "title": "I-217 演练任务 %s" % item_id,
               "message": msg, "approved_by": "Beth",
               "task_kind": task_kind}
    if paths:
        payload["writable_paths"] = paths
    if resources:
        payload["exclusive_resources"] = resources
    dst = os.path.join(KS, "inbox", rid + ".json")
    tmp = dst + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)
    return rid


def write_raw(filename, payload):
    dst = os.path.join(KS, "inbox", filename)
    tmp = dst + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)


def kernel_state():
    return jload(os.path.join(KS, "state.json")) or {"tasks": {}, "runs": {},
                                                     "results": {}}


def task_of(st, rid):
    return st.get("tasks", {}).get(rid) or {}


def dispatch_entries(item_id=None):
    entries = jload(DISPATCH_LOG) or []
    if item_id:
        entries = [e for e in entries if e.get("item_id") == item_id]
    return entries


RUN_PORT = 25000 + (os.getpid() % 10000)      # 每次运行唯一端口，防遗留进程占用


def spawn_worker():
    env = dict(os.environ)
    env.update({"DRILL_QUEUE_ROOT": QR, "DRILL_KERNEL_INBOX":
                os.path.join(KS, "inbox"), "DRILL_DISPATCH_LOG": DISPATCH_LOG,
                "DRILL_EXEC_DIR": EXEC_DIR, "LP_LANE_WORKERS_ROSTER": ROSTER,
                "LP_ITEMS_REGISTRY": REG, "FLAPPER_LEDGER_MODE": "local"})
    logf = open(os.path.join(OUT, "drill_worker.log"), "a", encoding="utf-8")
    p = subprocess.Popen([PY, os.path.join(HERE, "lane_workers_drill_worker.py")],
                         env=env, stdout=logf, stderr=subprocess.STDOUT)
    return p


def spawn_kernel(port=None, wake=1.0):
    env = dict(os.environ)
    env.update({"LP_LANE_WORKERS_ROSTER": ROSTER, "LP_ITEMS_REGISTRY": REG,
                "FLAPPER_LEDGER_MODE": "local"})
    p = subprocess.Popen([PY, os.path.join(HERE, "orchestrator_kernel.py"),
                          "--state-root", KS, "--queue-root", QR,
                          "--wake-s", str(wake), "--port", str(port or RUN_PORT),
                          "--line-retry-s", "2"],
                         env=env, stdout=open(os.path.join(OUT, "kernel.log"), "a",
                                              encoding="utf-8"),
                         stderr=subprocess.STDOUT)
    return p


def stop_executors():
    """写停止文件让执行器占位进程自行退出，超时强杀。"""
    for name in os.listdir(EXEC_DIR):
        if name.endswith(".stop"):
            continue
        open(os.path.join(EXEC_DIR, name + ".stop"), "w").close()
    deadline = time.time() + 5
    while time.time() < deadline:
        if not [f for f in os.listdir(EXEC_DIR) if f.endswith(".heartbeat")]:
            return
        time.sleep(0.5)


def cleanup(kernel, worker):
    quiet_kill(kernel)
    quiet_kill(worker)
    stop_executors()


def quiet_kill(p):
    if p and p.poll() is None:
        p.kill()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def seed_registry():
    shutil.copytree(os.path.join(ROOT, "items_registry"), REG,
                    ignore=shutil.ignore_patterns("__pycache__", "*.tmp",
                                                  ".ledger.lock"))
    obj = jload(os.path.join(REG, "items.json"))
    # 只留演练条目：生产真实事项（含 I-217 本身）会以在办状态挡住
    # 任务线读取（lane_active），全量带入会让演练任务永远排队。
    obj["items"] = [i for i in obj.get("items", [])
                    if 921 <= int(str(i.get("id", "I-0"))[2:] or 0) <= 942]
    for n in range(921, 941):
        obj["items"].append({"id": "I-%d" % n, "title": "I-217 演练事项 I-%d" % n,
                             "status": "open", "opened_at": "2026-10-14",
                             "closed_at": None, "closed_by": None,
                             "owner": "L3队", "lane": "L3",
                             "close_criterion": "I-217 演练判据",
                             "note": "I-217 演练专用测试状态库条目",
                             "claimed_by": None, "claimed_at": None,
                             "lease_until": None, "evidence": [],
                             "criteria": None, "replies": [], "blockers": [],
                             "conclusion": None, "criterion_check": [],
                             "depends_on": [], "reviewer": "beth"})
    for n in (943, 944, 945):
        obj["items"].append({"id": "I-%d" % n, "title": "I-217 任务线并行 %d" % n,
                             "status": "open", "opened_at": "2026-10-14",
                             "closed_at": None, "closed_by": None,
                             "owner": "L3队", "lane": "L3",
                             "close_criterion": "I-217 演练判据",
                             "note": "I-217 补修 LW8 专用",
                             "claimed_by": None, "claimed_at": None,
                             "lease_until": None, "evidence": [],
                             "criteria": None, "replies": [], "blockers": [],
                             "conclusion": None, "criterion_check": [],
                             "depends_on": [], "reviewer": "beth"})
    for n in (941, 942):
        obj["items"].append({"id": "I-%d" % n, "title": "I-217 单工位兼容 I-%d" % n,
                             "status": "open", "opened_at": "2026-10-14",
                             "closed_at": None, "closed_by": None,
                             "owner": "L9队", "lane": "L9",
                             "close_criterion": "I-217 演练判据",
                             "note": "I-217 演练专用（L9 默认上限一）",
                             "claimed_by": None, "claimed_at": None,
                             "lease_until": None, "evidence": [],
                             "criteria": None, "replies": [], "blockers": [],
                             "conclusion": None, "criterion_check": [],
                             "depends_on": [], "reviewer": "beth"})
    with open(os.path.join(REG, "items.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def setup():
    os.environ["LP_LANE_WORKERS_ROSTER"] = ROSTER
    os.makedirs(KS, exist_ok=True)
    for sub in ("inbox", "processed", "invalid", "outbox"):
        os.makedirs(os.path.join(KS, sub), exist_ok=True)
    os.makedirs(EXEC_DIR, exist_ok=True)
    seed_registry()
    # L3 试点二，其余线缺省一（判据一）
    with open(os.path.join(KS, "lane_workers.json"), "w", encoding="utf-8") as f:
        json.dump({"default": 1, "lanes": {"L3": 2}}, f, ensure_ascii=False)


def main():
    os.makedirs(OUT, exist_ok=True)
    setup()
    results = {"started_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "out_dir": OUT, "checks": _checks}
    results["run_port"] = RUN_PORT

    worker = spawn_worker()
    kernel = spawn_kernel()
    try:
        run_all(results, kernel, worker)
    finally:
        cleanup(kernel, worker)
        results["finished_at"] = datetime.datetime.now().isoformat(
            timespec="seconds")
        results["summary"] = {"total": len(_checks),
                              "passed": sum(1 for c in _checks if c["ok"])}
        dst = os.path.join(OUT, "drill_results.json")
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print("\n==== I-217 演练汇总：%d/%d 通过，结果 %s ===="
              % (results["summary"]["passed"], results["summary"]["total"], dst),
              flush=True)
    return 0 if results["summary"]["passed"] == results["summary"]["total"] else 1


def run_all(results, kernel, worker):
    # 内核健康检查：启动事件与心跳必须出现，否则后续全部样例无意义。
    ok_boot = wait_for(lambda: any(
        e.get("event") == "kernel_started"
        for e in (jload(os.path.join(KS, "ledger.json")) or
                  {"events": []})["events"]), timeout_s=15)
    check("LW0 内核与工作器健康启动", ok_boot, "port=%d" % RUN_PORT)
    if not ok_boot:
        return

    def ensure_worker():
        """工作器自愈：看门狗自退出或死亡即重启（磁盘状态可恢复）。"""
        nonlocal worker
        if worker is None or worker.poll() is not None:
            print("[自愈] I-217 演练工作器已死（exit=%s），重启"
                  % (worker.poll() if worker else None), flush=True)
            worker = spawn_worker()
        return worker

    # ---------------- LW1 业务线与工位分离、配置、单工位兼容 ----------------
    cfg = lw.load_lane_config(KS)
    check("LW1a 并发上限配置：L3=2、缺省=1",
          lw.concurrency_limit(cfg, "L3") == 2 and cfg["default"] == 1,
          "L3=%s default=%s" % (lw.concurrency_limit(cfg, "L3"), cfg["default"]))
    check("LW1b 工位标识固定",
          lw.worker_ids("L3", 2) == ["L3-w1", "L3-w2"],
          str(lw.worker_ids("L3", 2)))
    # 单工位兼容：L9（缺省上限一）两个无声明任务必须串行。
    r1 = write_task("I-941", "L9 串行样例一。执行毫秒:2500；结果类型:closed",
                    lane="L9")
    r2 = write_task("I-942", "L9 串行样例二。执行毫秒:800；结果类型:closed",
                    lane="L9")
    ok = wait_for(lambda: task_of(kernel_state(), r2).get("phase") == "completed",
                  timeout_s=60)
    st = kernel_state()
    t1, t2 = task_of(st, r1), task_of(st, r2)
    l9 = (jload(os.path.join(KS, "lane_queues.json")) or {}).get("L9") or {}
    serial_l9 = None
    e1 = [e for e in dispatch_entries("I-941") if e["op"] == "dispatch_new"]
    e2 = [e for e in dispatch_entries("I-942") if e["op"] == "dispatch_new"]
    if e1 and e2:
        # 串行判据：任务二的派发时刻不早于任务一派发后其执行时长（2.5 秒）
        serial_l9 = e2[0]["ts"] >= e1[0]["ts"] + 2.0
    check("LW1c 单工位串行（请求、队列、结果样例）",
          ok and t1.get("phase") == "completed" and serial_l9 is True
          and l9.get("concurrency_limit") == 1,
          "t1=%s t2=%s 先后=%s" % (t1.get("phase"), t2.get("phase"),
                                   "串行" if serial_l9 is True else "未知"))

    ensure_worker()
    # ---------------- LW2 双工位真实重叠 + 排队 + 自动接续 ----------------
    wa = os.path.join(OUT, "scope_a")
    wb = os.path.join(OUT, "scope_b")
    os.makedirs(wa, exist_ok=True)
    os.makedirs(wb, exist_ok=True)
    ra = write_task("I-921", "并行任务 A。执行毫秒:5000；结果类型:evidence_appended",
                    paths=[wa])
    rb = write_task("I-922", "并行任务 B。执行毫秒:5000；结果类型:evidence_appended",
                    paths=[wb])
    rc = write_task("I-923", "排队任务 C。执行毫秒:1500；结果类型:evidence_appended",
                    paths=[os.path.join(OUT, "scope_c")])
    # 等两任务并行在办、C 排队
    ok_ab = wait_for(lambda: (task_of(kernel_state(), ra).get("phase") in
                              ("dispatched", "result_pending") and
                              task_of(kernel_state(), rb).get("phase") in
                              ("dispatched", "result_pending")), timeout_s=45)
    st = kernel_state()
    ta, tb, tc = task_of(st, ra), task_of(st, rb), task_of(st, rc)
    overlap_record = None
    da = [e for e in dispatch_entries("I-921") if e["op"] == "dispatch_new"]
    db = [e for e in dispatch_entries("I-922") if e["op"] == "dispatch_new"]
    if da and db:
        overlap_record = {"a": da[0], "b": db[0],
                          "a_run": da[0]["run_id"], "b_run": db[0]["run_id"],
                          "a_worker": da[0]["worker_id"],
                          "b_worker": db[0]["worker_id"],
                          "a_session": da[0]["session"], "b_session": db[0]["session"],
                          "a_executor_pid": da[0].get("executor_pid"),
                          "b_executor_pid": db[0].get("executor_pid"),
                          "a_at": da[0]["iso"], "b_at": db[0]["iso"]}
    check("LW2a 两任务同时活跃于不同工位",
          ok_ab and ta.get("worker_id") != tb.get("worker_id")
          and {ta.get("worker_id"), tb.get("worker_id")} == {"L3-w1", "L3-w2"},
          "A=%s B=%s" % (ta.get("worker_id"), tb.get("worker_id")))
    check("LW2b 会话/工位/进程/运行号四不同",
          bool(overlap_record) and overlap_record["a_session"] != overlap_record["b_session"]
          and overlap_record["a_executor_pid"] != overlap_record["b_executor_pid"]
          and overlap_record["a_run"] != overlap_record["b_run"],
          json.dumps(overlap_record, ensure_ascii=False) if overlap_record else "无派发记录")
    check("LW2c 第三项排队中",
          task_of(kernel_state(), rc).get("phase") in ("ready", "line_wait")
          and not dispatch_entries("I-923"),
          "C phase=%s 派发数=%d" % (task_of(kernel_state(), rc).get("phase"),
                                     len(dispatch_entries("I-923"))))
    ok_c = wait_for(lambda: task_of(kernel_state(), rc).get("phase") == "completed",
                    timeout_s=90)
    c_entries = dispatch_entries("I-923")
    check("LW2d 完成后自动接续（无需经理消息）",
          ok_c and len([e for e in c_entries if e["op"] == "dispatch_new"]) == 1,
          "C 派发=%d" % len([e for e in c_entries if e["op"] == "dispatch_new"]))
    st = kernel_state()
    phases = [t.get("phase") for t in st["tasks"].values()]
    l3q = (jload(os.path.join(KS, "lane_queues.json")) or {}).get("L3") or {}
    check("LW2e 每线与全局在办不超限",
          l3q.get("conflict") in (None, []) and len(l3q.get("workers") or []) <= 2,
          "L3 conflict=%s workers=%s" % (l3q.get("conflict"),
                                         len(l3q.get("workers") or [])))
    results["overlap_record"] = overlap_record

    # ---------------- LW4 冲突准入四样例（在 LW2 之上继续） ----------------
    shared = os.path.join(OUT, "shared")
    rd = write_task("I-924", "冲突样例 D（与 E 重叠）。执行毫秒:4000；结果类型:evidence_appended",
                    paths=[shared])
    wait_for(lambda: task_of(kernel_state(), rd).get("phase") in
             ("dispatched", "result_pending"), timeout_s=30)
    re_ = write_task("I-925", "冲突样例 E（与 D 重叠）。执行毫秒:500；结果类型:evidence_appended",
                     paths=[os.path.join(shared, "sub")])
    rf = write_task("I-926", "无冲突样例 F。执行毫秒:500；结果类型:evidence_appended",
                    paths=[os.path.join(OUT, "scope_f")])
    rg = write_task("I-927", "缺声明样例 G。执行毫秒:500；结果类型:evidence_appended")
    wait_for(lambda: task_of(kernel_state(), rf).get("phase") == "completed",
             timeout_s=45)
    st = kernel_state()
    te, tf, tg = task_of(st, re_), task_of(st, rf), task_of(st, rg)
    # 区间断言：D（shared）与 E（shared/sub 重叠声明）的运行区间不得
    # 相交——E 被阻塞排队或错峰运行都合法，同时运行才是违规。
    led_evs = (jload(os.path.join(KS, "ledger.json")) or {"events": []})["events"]

    def run_interval(item_id):
        starts = [e["ts"] for e in led_evs if e.get("event") == "run_started"
                  and (e.get("detail") or {}).get("item_id") == item_id]
        ends = [e["ts"] for e in led_evs if e.get("event") == "result_received"
                and (e.get("detail") or {}).get("item_id") == item_id]
        return (min(starts), max(ends)) if starts and ends else None

    def ts_key(iso):
        return iso.replace("-", "").replace(":", "").replace(".", "")
    d_iv, e_iv = run_interval("I-924"), run_interval("I-925")
    no_concurrent = True
    if d_iv and e_iv:
        no_concurrent = not (ts_key(d_iv[0]) < ts_key(e_iv[1])
                             and ts_key(e_iv[0]) < ts_key(d_iv[1]))
    check("LW4a 可写范围重叠：两任务运行区间不相交",
          no_concurrent,
          "D=%s E=%s 相交=%s" % (d_iv, e_iv, not no_concurrent))
    check("LW4b 互不冲突任务继续",
          tf.get("phase") == "completed",
          "F phase=%s" % tf.get("phase"))
    check("LW4c 缺声明不能并行（排队保留原因）",
          tg.get("phase") in ("ready",) and "缺" in (tg.get("last_error") or ""),
          "G phase=%s reason=%s" % (tg.get("phase"), tg.get("last_error")))
    # 隔离目录样例：D 完成后 G 仍需等待（缺声明）；再提两个隔离目录任务并行
    wait_for(lambda: task_of(kernel_state(), rd).get("phase") == "completed",
             timeout_s=45)
    rh = write_task("I-928", "隔离样例 H。执行毫秒:2500；结果类型:evidence_appended",
                    paths=[os.path.join(OUT, "iso_h")])
    ri = write_task("I-929", "隔离样例 I。执行毫秒:2500；结果类型:evidence_appended",
                    paths=[os.path.join(OUT, "iso_i")])
    ok_iso = wait_for(lambda: (task_of(kernel_state(), rh).get("phase") in
                               ("dispatched", "result_pending") and
                               task_of(kernel_state(), ri).get("phase") in
                               ("dispatched", "result_pending")), timeout_s=45)
    st = kernel_state()
    th, ti = task_of(st, rh), task_of(st, ri)
    check("LW4d 隔离目录两任务并行",
          ok_iso and th.get("worker_id") != ti.get("worker_id"),
          "H=%s(%s) I=%s(%s)" % (th.get("worker_id"), th.get("phase"),
                                 ti.get("worker_id"), ti.get("phase")))
    wait_for(lambda: task_of(kernel_state(), ri).get("phase") == "completed",
             timeout_s=60)

    ensure_worker()
    # ---------------- LW3 去重样例与映射重启一致 ----------------
    # 重复入队同事项
    write_task("I-930", "重复入队样例。执行毫秒:3000；结果类型:evidence_appended",
               paths=[os.path.join(OUT, "dup")], request_id="kt-i930-first")
    wait_for(lambda: task_of(kernel_state(), "kt-i930-first").get("phase") in
             ("dispatched", "result_pending"), timeout_s=30)
    write_task("I-930", "重复入队样例（同事项第二任务）。执行毫秒:500",
               paths=[os.path.join(OUT, "dup")], request_id="kt-i930-second")
    time.sleep(2.5)
    ev_dup = [e for e in (jload(os.path.join(KS, "ledger.json")) or
                          {"events": []})["events"]
              if e.get("event") == "item_busy_rejected"]
    check("LW3a 同事项重复入队被拒",
          len(ev_dup) >= 1 and not dispatch_entries("I-930") or
          len([e for e in dispatch_entries("I-930")
               if e["request_id"] == "kd-kt-i930-first-a1"]) == 1,
          "item_busy_rejected=%d" % len(ev_dup))
    # 双消费者：第二内核实例拒绝（状态根锁在确认持有者存活时等待至
    # 超时上限约 10 秒后退出，等待须覆盖该窗口）
    second = spawn_kernel(port=RUN_PORT + 1)
    code = None
    try:
        code = second.wait(timeout=15)
    except subprocess.TimeoutExpired:
        quiet_kill(second)
    check("LW3b 第二内核实例退出", code == 3, "exit=%s" % code)
    # 认领到期：强制把活跃事项租约置过期，验证不产生第二个有效运行
    st = kernel_state()
    active_item = task_of(st, "kt-i930-first").get("item_id")
    reg_path = os.path.join(REG, "items.json")
    reg = jload(reg_path)
    for it in reg["items"]:
        if it["id"] == active_item:
            it["lease_until"] = "2026-10-14T00:00:00"
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    time.sleep(2)
    runs_930 = [r for r in kernel_state().get("runs", {}).values()
                if r.get("item_id") == "I-930"]
    check("LW3c 认领到期不产生第二个有效运行",
          len(runs_930) == 1, "I-930 运行数=%d" % len(runs_930))
    wait_for(lambda: task_of(kernel_state(), "kt-i930-first").get("phase") ==
             "completed", timeout_s=45)
    # 未知回执：派发已执行、内核未采纳回执时重启，同号对账不重发
    rj = write_task("I-931", "未知回执样例。执行毫秒:15000；结果类型:evidence_appended",
                    paths=[os.path.join(OUT, "unc")])
    wait_for(lambda: len([e for e in dispatch_entries("I-931")
                          if e["op"] == "dispatch_new"]) >= 1, timeout_s=30)
    before_count = len([e for e in dispatch_entries("I-931")
                        if e["op"] == "dispatch_new"])
    # 未知回执样例：重启前后同号派发数不变
    roster_before = lw.roster_load()
    quiet_kill(kernel)
    time.sleep(1.0)
    kernel = spawn_kernel()
    ok_j = wait_for(lambda: task_of(kernel_state(), rj).get("phase") ==
                    "completed", timeout_s=60)
    after_count = len([e for e in dispatch_entries("I-931")
                       if e["op"] == "dispatch_new"])
    check("LW3d 未知回执重启后不重发",
          ok_j and after_count == before_count,
          "派发数 前=%d 后=%d" % (before_count, after_count))
    roster_after = lw.roster_load()
    consistent = True
    for wid, rec in roster_before.get("workers", {}).items():
        other = roster_after.get("workers", {}).get(wid)
        if rec.get("phase") == "active" and not other:
            consistent = False
        if rec.get("run_id") and other and other.get("session_id") != rec.get("session_id"):
            consistent = False
    check("LW3e 工位与运行映射重启前后回读一致",
          consistent and bool(roster_after.get("workers")),
          "工位数 前=%d 后=%d" % (len(roster_before.get("workers", {})),
                                  len(roster_after.get("workers", {}))))

    ensure_worker()
    # ---------------- LW5 单工位故障隔离 ----------------
    rk = write_task("I-932", "隔离故障样例 K（工位一）。执行毫秒:60000；结果类型:evidence_appended",
                    paths=[os.path.join(OUT, "fault_k")])
    rl = write_task("I-933", "隔离故障样例 L（工位二）。执行毫秒:6000；结果类型:evidence_appended",
                    paths=[os.path.join(OUT, "fault_l")])
    ok_kl = wait_for(lambda: (task_of(kernel_state(), rk).get("phase") in
                              ("dispatched", "result_pending") and
                              task_of(kernel_state(), rl).get("phase") in
                              ("dispatched", "result_pending")), timeout_s=45)
    st = kernel_state()
    tk, tl = task_of(st, rk), task_of(st, rl)
    k_worker = tk.get("worker_id")
    l_worker = tl.get("worker_id")
    l_session = tl.get("session_id")
    entries_k = [e for e in dispatch_entries("I-932") if e["op"] == "dispatch_new"]
    entries_l = [e for e in dispatch_entries("I-933") if e["op"] == "dispatch_new"]
    exec_pid_k = entries_k[0].get("executor_pid") if entries_k else None
    exec_pid_l = entries_l[0].get("executor_pid") if entries_l else None
    # 只终止工位一的执行器（真实子进程）
    killed = False
    if exec_pid_k:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(exec_pid_k)],
                       capture_output=True)
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_uint32)]
        k32.GetExitCodeProcess.restype = ctypes.c_int
        k32.CloseHandle.argtypes = [ctypes.c_void_p]

        def _alive(pid):
            h = k32.OpenProcess(0x1000, 0, pid)
            if not h:
                return False
            try:
                code = ctypes.c_uint32()
                if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return True
                return code.value == 259
            finally:
                k32.CloseHandle(ctypes.c_void_p(h))
        deadline_k = time.time() + 3.0
        while time.time() < deadline_k:
            if not _alive(exec_pid_k):
                killed = True
                break
            time.sleep(0.2)
    check("LW5a 只终止自建工位一执行器", killed, "pid=%s" % exec_pid_k)
    ok_l = wait_for(lambda: task_of(kernel_state(), rl).get("phase") ==
                    "completed", timeout_s=60)
    st = kernel_state()
    check("LW5b 工位二原会话与连续工作保持",
          ok_l and task_of(st, rl).get("session_id") == l_session
          and task_of(st, rl).get("worker_id") == l_worker,
          "L 会话保持=%s 工位=%s" % (task_of(st, rl).get("session_id") == l_session,
                                      task_of(st, rl).get("worker_id")))
    # 工位一断点恢复：写 turn_recovery 记录（同会话续一次有效运行）
    k_session = tk.get("session_id")
    k_run = tk.get("run_id")
    tr_dir = os.path.join(os.path.dirname(KS), "turn_recovery")
    os.makedirs(tr_dir, exist_ok=True)
    tr_name = "tr-%s-faultsample.json" % (k_session or "no")[:18]
    with open(os.path.join(tr_dir, tr_name), "w", encoding="utf-8") as f:
        json.dump({"captured_at": datetime.datetime.now().isoformat(
            timespec="milliseconds"), "session_id": k_session,
            "run_id": k_run, "method": "drill.fault_isolation",
            "reconcile": "unknown"}, f, ensure_ascii=False, indent=1)
    ok_resume = wait_for(lambda: any(
        e["op"] == "dispatch_resume" and e.get("session") == k_session
        for e in dispatch_entries()), timeout_s=70)
    check("LW5c 工位一按断点恢复原会话", ok_resume,
          "dispatch_resume 至 %s" % (k_session or "?"))
    wait_for(lambda: task_of(kernel_state(), rk).get("phase") == "completed",
             timeout_s=45)
    # 生产入口拒绝无授权全局终止（隔离宿主标志缺失时 kill_child 拒绝）
    probe = subprocess.run(
        [PY, "-c",
         "import os,sys,json; sys.path.insert(0, r'%s'); "
         "os.environ['LP_HOST_STATUS']=r'%s'; os.environ['LP_HOST_STATE_ROOT']=r'%s'; "
         "os.environ.pop('LP_HOST_ISOLATED',None); "
         "os.environ['LP_DISPATCH_QUEUE_ROOT']=r'%s'; "
         "import host_service as h; "
         "print(json.dumps(h.handle('{\"op\":\"kill_child\"}'), ensure_ascii=False))"
         % (HERE, os.path.join(OUT, "probe_status.json"),
            os.path.join(OUT, "probe_runtime"), QR)],
        capture_output=True, text=True, encoding="utf-8")
    refused = "禁用" in (probe.stdout or "")
    check("LW5d 生产入口拒绝无授权全局终止", refused,
          (probe.stdout or probe.stderr or "").strip()[:160])

    # ---------------- LW6 守门定向样例（独立子进程，桩云端） ----------------
    gate_src = os.path.join(OUT, "gate_probe.py")
    with open(gate_src, "w", encoding="utf-8") as f:
        f.write(GATE_PROBE_SRC
                .replace("@OUT@", OUT.replace("\\", "\\"))
                .replace("@ROOT@", ROOT.replace("\\", "\\")))
    probe = subprocess.run([PY, gate_src], capture_output=True, text=True,
                           encoding="utf-8")
    try:
        gate_res = json.loads((probe.stdout or "").strip().splitlines()[-1])
    except Exception:
        gate_res = {"error": (probe.stdout or probe.stderr or "")[:300]}
    check("LW6a 两项同线在办按认领会话定向",
          gate_res.get("two_active_match") is True,
          json.dumps(gate_res, ensure_ascii=False)[:220])
    check("LW6b 旧会话保留（未认领其他工位事项不切换）",
          gate_res.get("old_session_skip") is True,
          str(gate_res.get("old_session_reason"))[:160])
    check("LW6c 重复交接抑制", gate_res.get("duplicate_suppressed") is True,
          str(gate_res.get("duplicate_reason"))[:160])
    check("LW6d 不凭历史派发事项切错会话",
          gate_res.get("stale_item_corrected") is True,
          str(gate_res.get("stale_correction"))[:160])

    ensure_worker()
    # ---------------- LW8 任务线在办并行准入（补修一） ----------------
    def set_item_state(item_id, **fields):
        reg_path_lw8 = os.path.join(REG, "items.json")
        reg8 = jload(reg_path_lw8)
        for it in reg8["items"]:
            if it["id"] == item_id:
                it.update(fields)
        with open(reg_path_lw8, "w", encoding="utf-8") as f:
            json.dump(reg8, f, ensure_ascii=False, indent=1)

    ensure_worker()
    # 归属可核在办（认领会话 + 工位）：第二项必须过线进入并行执行
    set_item_state("I-943", status="in_progress", claimed_by="L3队",
                   claim_session_id="sess_lw8_a", claim_worker_id="L3-w1")
    r8a = write_task("I-944", "LW8 归属可核并行样例。执行毫秒:2000；结果类型:evidence_appended",
                     paths=[os.path.join(OUT, "lw8a")])
    ok8a = wait_for(lambda: task_of(kernel_state(), r8a).get("phase") in
                    ("dispatched", "result_pending"), timeout_s=40)
    st8 = kernel_state()
    parallel_events = [e for e in (jload(os.path.join(KS, "ledger.json")) or
                                   {"events": []})["events"]
                       if e.get("event") == "line_parallel_admitted"
                       and e.get("request_id") == r8a]
    check("LW8a 在办归属可核时第二项过线并行",
          ok8a and bool(parallel_events),
          "phase=%s line_parallel_admitted=%d"
          % (task_of(st8, r8a).get("phase"), len(parallel_events)))
    wait_for(lambda: task_of(kernel_state(), r8a).get("phase") == "completed",
             timeout_s=45)
    # 归属未知在办：保守等待（不凭未知归属放行）。先复位 I-943 为
    # open（样例事项自身必须可派），I-945 为归属未知在办。
    set_item_state("I-943", status="open", claimed_by=None,
                   claim_session_id=None, claim_worker_id=None)
    set_item_state("I-945", status="in_progress", claimed_by="L3队")
    r8c = write_task("I-943", "LW8 归属未知保守样例。执行毫秒:500",
                     paths=[os.path.join(OUT, "lw8c")])
    time.sleep(4)
    t8c = task_of(kernel_state(), r8c)
    check("LW8b 在办归属未知时保守等待",
          t8c.get("phase") in ("ready", "line_wait")
          and "归属未核" in (t8c.get("last_error") or ""),
          "phase=%s reason=%s" % (t8c.get("phase"), t8c.get("last_error")))
    set_item_state("I-945", status="open", claimed_by=None)

    # ---------------- LW7 契约回读与通知关联 ----------------
    queues_snap = {"sampled_at": datetime.datetime.now().isoformat(
        timespec="seconds"),
        "queues": jload(os.path.join(KS, "lane_queues.json")) or {}}
    import lane_workers_contract_check as lwc
    c_ok, c_issues, c_fields = lwc.check_snapshot(queues_snap)
    check("LW7a 接口与契约逐字段回读一致", c_ok and c_fields >= 20,
          "核对字段 %d 项，问题=%s" % (c_fields, c_issues or "无"))
    ndir = os.path.join(KS, "notifications")
    ev_files = []
    for sub in ("pending", "taken", "sending", "delivered", "acked"):
        d = os.path.join(ndir, sub)
        if os.path.isdir(d):
            ev_files += [f for f in os.listdir(d) if f.endswith(".json")]
    ledger_events = [e for e in (jload(os.path.join(KS, "ledger.json")) or
                                 {"events": []})["events"]
                     if e.get("event") == "review_notification"]
    bad_assoc = [f for f in ev_files
                 if not f.startswith("ev-")]
    dup_events = len(ledger_events) - len(set(
        e.get("request_id") for e in ledger_events))
    check("LW7b 结果与审核通知按事项运行关联且零重复零丢失",
          dup_events == 0 and len(ev_files) >= len(ledger_events)
          and not bad_assoc,
          "通知文件=%d 台账事件=%d 重复=%d" % (len(ev_files), len(ledger_events),
                                              dup_events))



GATE_PROBE_SRC = r'''
# -*- coding: utf-8 -*-
"""I-217 LW6 守门定向样例：桩云端 + 隔离状态，验证按会话定向交接。"""
import json
import os
import sys

OUT = r"@OUT@"
ROOT = r"@ROOT@"
for p in (os.path.join(ROOT, "lp_sync"), os.path.join(ROOT, "items_registry")):
    if p not in sys.path:
        sys.path.insert(0, p)

snap = {"items": [
    {"id": "I-701", "lane": "L3", "status": "in_progress",
     "claim_session_id": "sess_gate_A"},
    {"id": "I-702", "lane": "L3", "status": "in_progress",
     "claim_session_id": "sess_gate_B"}],
    "events": []}


class FakeCloud:
    def request(self, method, path, body=None):
        if path == "/api/agent/snapshot":
            return snap
        if path == "/api/console/config":
            return {"handoff_mode": "auto", "gate_pct": 70}
        return {}


fake = sys.modules.get("cloud_client") or type(sys)("cloud_client")
fake.request = FakeCloud().request
fake.enabled = lambda: False
sys.modules["cloud_client"] = fake

import handoff_gate
handoff_gate.STATE_PATH = os.path.join(OUT, "gate_state.json")
handoff_gate.CARD_DIR = os.path.join(OUT, "gate_cards")
handoff_gate.COOLDOWN_S = 0

res = {}
i, why = handoff_gate._current_in_progress_for_lane("L3", "sess_gate_A")
res["two_active_match"] = (i == "I-701")
i2, why2 = handoff_gate._current_in_progress_for_lane("L3", "sess_gate_B")
res["two_active_match"] = res["two_active_match"] and (i2 == "I-702")
i3, why3 = handoff_gate._current_in_progress_for_lane("L3", "sess_gate_old")
res["old_session_skip"] = (i3 is None and "不切换其他工位" in why3)
res["old_session_reason"] = why3

# 历史派发映射过期：采集行 item 旧值，云端认领已换新
snap["items"] = [
    {"id": "I-703", "lane": "L3", "status": "in_progress",
     "claim_session_id": "sess_gate_A"}]
i4, why4 = handoff_gate._current_in_progress_for_lane("L3", "sess_gate_A")
res["stale_item_corrected"] = (i4 == "I-703")
res["stale_correction"] = why4

# 重复交接抑制：同会话已有 pending 计划/已归档不再触发
st = {"last_switch": {}, "daily": {},
      "archived": [{"session_id": "sess_gate_B",
                    "successor": "sess_gate_B2",
                    "archived_at": "2026-10-14T00:00:00",
                    "card": "x", "archive_reason": "演练"}],
      "pending_handoffs": {}}
handoff_gate._save_state(st)
actions = handoff_gate.evaluate({"sessions": [
    {"session_id": "sess_gate_B", "lane": "L3", "context_pct": 85,
     "item_id": "I-702"}]}, lambda *a, **k: None)
res["duplicate_suppressed"] = not any(
    a.get("switched") for a in actions.get("actions", []))
res["duplicate_reason"] = "archived 会话被跳过，无第二次切换"
print(json.dumps(res, ensure_ascii=False))
'''

if __name__ == "__main__":
    sys.exit(main())
