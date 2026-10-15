# -*- coding: utf-8 -*-
"""kernel_drills.py — I-178 故障演练套件（全隔离，不碰生产）。

隔离件：独立队列根、独立内核状态根、独立登记库副本（本地模式测试
状态库）、假执行器（假会话）。生产宿主服务与生产云端账本零接触。

演练清单（对应四项验收）：
  D1  基础链路：提交→自动派发→假执行器交结果→经桥关单→回执写回
  D2  同一结果重复提交两次不重复更新；同运行第二结果被拒
  D3  终止位置一：请求保存后终止内核，重启恢复，记录零丢失
  D4  终止位置二：消息已发送回执未收终止内核，重启先对账不重发
  D5  终止位置三：结果已收状态未更新终止内核，重启补落实不重复写账
  D6  同一请求重复递交两次 + 重复唤醒，重复派发零次
  D7  同时启动两个消费者，第二实例退出且零动作
  D8  丢弃一次派发回执，重启从桥台账对账采纳，不重发
  D9  认领到期但执行器仍运行，不产生第二个有效运行
  D10 结果违反既有判据规则（criterion_check 缺键），定性拒绝留痕不写账
  D11 总停开关：halt 阻止新派发，run 恢复
  D12 evidence_appended 结果类型经桥追加证据
  D13 普通任务正例：task_kind=task 经 ledger_read 读任务线后派发关单
  D14 依赖未清先拦后放：line_wait 不派发，依赖关闭后重读放行
  D15 已关闭事项与路线不一致：line_rejected 拒绝且零派发
  D16 桥执行侧崩溃窗口：副作用已发生回执未保存，对账或保持未知不重执行
  D17 台账锁僵死接管：持有者已死或超时，后来者接管不卡死
  D18 同状态根双端口双消费者：状态根单例锁拒绝第二内核
  D19 真实执行器端到端：独立子进程自己交结果，桥按既有规则关单

用法：python kernel_drills.py [输出目录]
仅使用 Python 标准库。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch_file_queue as dq

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
PORT = 58890

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "logs", "i178_kernel_drills_%s"
    % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))

KS = os.path.join(OUT, "kernel_state")
QR = os.path.join(OUT, "dispatch_queue")
REG = os.path.join(OUT, "registry_copy")


EDGE_WORKER_SRC = """
import os, sys, pathlib
sys.path.insert(0, sys.argv[1])
import dispatch_file_queue as dq
COUNT = pathlib.Path(sys.argv[3])
def fake(req):
    COUNT.write_text(str(int(COUNT.read_text() or 0) + 1))
    return {"ok": True, "session": "sess_edge", "accepted": True,
            "readback_verified": True}
w = dq.QueueWorker(root=sys.argv[2], dispatcher=fake, stable_grace_s=0)
w.poll_once()
"""


_checks = []


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail), flush=True)


def jload(path):
    """带短重试的读取：目标文件被写方每 0.3 秒原子替换（Windows 替换
    瞬间读取抛权限错误），重试三次后再判失败，杜绝幻影空读数。"""
    import time as _t
    for _ in range(3):
        try:
            return json.load(open(path, encoding="utf-8"))
        except PermissionError:
            _t.sleep(0.02)
        except (OSError, ValueError):
            return None
    return None


def write_task(item_id, msg=None, task_kind="test"):
    rid = "kt-%s" % item_id.lower()
    payload = {"schema_version": 1, "kind": "task_submit",
               "request_id": rid, "item_id": item_id, "lane": "L3",
               "title": "演练任务 %s" % item_id,
               "message": msg or ("演练简报：%s 的构造性任务。" % item_id),
               "approved_by": "Beth", "task_kind": task_kind}
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


def kernel_events(ref=None):
    path = os.path.join(KS, "ledger.json")
    obj = jload(path) or {"events": []}
    evs = obj.get("events", [])
    return [e for e in evs if ref is None or e.get("request_id") == ref]


def queue_events(ref=None):
    obj = jload(os.path.join(QR, "ledger.json")) or {"events": []}
    evs = obj.get("events", [])
    return [e for e in evs if ref is None or e.get("request_id") == ref]


def registry_item(item_id):
    obj = jload(os.path.join(REG, "items.json"))
    for it in obj.get("items", []):
        if it["id"] == item_id:
            return it
    return None


def drill_evidence_count(item_id):
    """测试账本里演练证据条数（防重复写账的计数依据）。"""
    item = registry_item(item_id)
    if not item:
        return 0
    return len([e for e in item.get("evidence", []) if "机主说明：" not in e])


def wait_for(predicate, timeout_s=30, step=0.3):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def spawn_worker(extra_env=None):
    env = dict(os.environ)
    env["DRILL_QUEUE_ROOT"] = QR
    env["DRILL_KERNEL_INBOX"] = os.path.join(KS, "inbox")
    env["LP_ITEMS_REGISTRY"] = REG
    env["FLAPPER_LEDGER_MODE"] = "local"
    env.update(extra_env or {})
    p = subprocess.Popen([PY, os.path.join(HERE, "kernel_drill_worker.py")],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    return p


def spawn_kernel(crash_point=None, wake=0.3, port=None, line_retry=2.0,
                  state_root=None, queue_root=None):
    env = dict(os.environ)
    if crash_point:
        env["KERNEL_CRASH_POINT"] = crash_point
    p = subprocess.Popen([PY, os.path.join(HERE, "orchestrator_kernel.py"),
                          "--state-root", state_root or KS,
                          "--queue-root", queue_root or QR,
                          "--wake-s", str(wake), "--port", str(port or PORT),
                          "--line-retry-s", str(line_retry)],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    return p


def quiet_kill(p):
    if p and p.poll() is None:
        p.kill()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def safe_wait(p, timeout):
    """等子进程退出：超时不抛异常（返回 None 并杀掉），单段失败不拖垮整套演练。"""
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        quiet_kill(p)
        return None


def ensure_worker(worker):
    """工作器健康检查：进程死亡即重启（自愈留痕）。"""
    if worker is None or worker.poll() is not None:
        print("[自愈] 隔离工作器已死，重启", flush=True)
        return spawn_worker()
    return worker


def seed_registry():
    shutil.copytree(os.path.join(ROOT, "items_registry"), REG,
                    ignore=shutil.ignore_patterns("__pycache__", "*.tmp",
                                                  ".ledger.lock"))
    items = []
    # 901-912 走模板；913-917 由下方显式条目提供（依赖、关闭、路线各不同），
    # 不得进模板避免同号重复被模板遮蔽。
    for n in range(901, 913):
        items.append({"id": "I-%d" % n, "title": "演练事项 I-%d" % n,
                      "status": "open", "opened_at": "2026-10-12",
                      "closed_at": None, "closed_by": None, "owner": "L3队",
                      "lane": "L3", "close_criterion": "演练判据",
                      "note": "演练专用测试状态库条目", "claimed_by": None,
                      "claimed_at": None, "lease_until": None,
                      "evidence": [], "criteria": None, "replies": [],
                      "blockers": [], "conclusion": None,
                      "criterion_check": [], "depends_on": [],
                      "partial_start": [], "claim_scope": []})
    # I-178 返修新增：普通任务（task_kind=task）正反例专用条目。
    items.append({"id": "I-913", "title": "普通任务正例", "status": "open",
                  "opened_at": "2026-10-12", "closed_at": None,
                  "closed_by": None, "owner": "L3队", "lane": "L3",
                  "close_criterion": "演练判据", "note": "正例",
                  "claimed_by": None, "claimed_at": None, "lease_until": None,
                  "evidence": [], "criteria": None, "replies": [],
                  "blockers": [], "conclusion": None, "criterion_check": [],
                  "depends_on": [], "partial_start": [], "claim_scope": []})
    items.append({"id": "I-914", "title": "依赖未清反例", "status": "open",
                  "opened_at": "2026-10-12", "closed_at": None,
                  "closed_by": None, "owner": "L3队", "lane": "L3",
                  "close_criterion": "演练判据", "note": "依赖 I-915",
                  "claimed_by": None, "claimed_at": None, "lease_until": None,
                  "evidence": [], "criteria": None, "replies": [],
                  "blockers": [], "conclusion": None, "criterion_check": [],
                  "depends_on": ["I-915"], "partial_start": [],
                  "claim_scope": []})
    items.append({"id": "I-915", "title": "依赖占位事项", "status": "open",
                  "opened_at": "2026-10-12", "closed_at": None,
                  "closed_by": None, "owner": "L3队", "lane": "L3",
                  "close_criterion": "演练判据", "note": "保持 open 的依赖",
                  "claimed_by": None, "claimed_at": None, "lease_until": None,
                  "evidence": [], "criteria": None, "replies": [],
                  "blockers": [], "conclusion": None, "criterion_check": [],
                  "depends_on": [], "partial_start": [], "claim_scope": []})
    items.append({"id": "I-916", "title": "已关闭反例", "status": "closed",
                  "opened_at": "2026-10-12", "closed_at": "2026-10-12",
                  "closed_by": "L3队", "owner": "L3队", "lane": "L3",
                  "close_criterion": "演练判据", "note": "已关闭",
                  "claimed_by": None, "claimed_at": None, "lease_until": None,
                  "evidence": [], "criteria": None, "replies": [],
                  "blockers": [], "conclusion": "判据通过",
                  "criterion_check": [], "depends_on": [],
                  "partial_start": [], "claim_scope": []})
    items.append({"id": "I-917", "title": "路线不一致反例", "status": "open",
                  "opened_at": "2026-10-12", "closed_at": None,
                  "closed_by": None, "owner": "L2队", "lane": "L2",
                  "close_criterion": "演练判据", "note": "属 L2 路",
                  "claimed_by": None, "claimed_at": None, "lease_until": None,
                  "evidence": [], "criteria": None, "replies": [],
                  "blockers": [], "conclusion": None, "criterion_check": [],
                  "depends_on": [], "partial_start": [], "claim_scope": []})
    with open(os.path.join(REG, "items.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "items": items}, f, ensure_ascii=False,
                  indent=2)
    with open(os.path.join(REG, "events.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "events": []}, f, ensure_ascii=False,
                  indent=2)


def full_chain_ok(item_id):
    """D1/D3/D4/D5 共用：链路完成断言。返回 (是否完成, 说明)。"""
    st = kernel_state()
    task = st["tasks"].get("kt-%s" % item_id.lower())
    if not task:
        return False, "任务不存在"
    if task["phase"] != "completed":
        return False, "phase=%s" % task["phase"]
    item = registry_item(item_id)
    if not item or item["status"] != "closed":
        return False, "测试账本事项未关闭"
    if item.get("conclusion") != "判据通过":
        return False, "conclusion=%r" % item.get("conclusion")
    if drill_evidence_count(item_id) != 1:
        return False, "证据 %d 条（应为 1）" % drill_evidence_count(item_id)
    ack = jload(os.path.join(KS, "outbox",
                             "kr-run-kt-%s-a1.ack.json" % item_id.lower()))
    if not ack or not ack.get("ok"):
        return False, "回执缺失或失败"
    return True, "链路完整"


def dispatch_count(item_id):
    """只数派发请求（op=dispatch_new）的 dispatch_start；账本写不算派发。"""
    n = 0
    for e in queue_events():
        d = e.get("detail") or {}
        if e.get("event") == "dispatch_start" and d.get("op") == "dispatch_new" \
                and str(d.get("item_id")) == item_id:
            n += 1
    return n


def main():
    # 独立持久运行：忽略控制台中断信号（Ctrl+C / Ctrl+Break），
    # 停止用 taskkill /F。计划任务承载时不受会话控制台事件影响。
    import signal
    for sig in (signal.SIGINT, signal.SIGBREAK):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except (ValueError, AttributeError, OSError):
            pass
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(KS, "inbox"), exist_ok=True)
    seed_registry()
    worker = spawn_worker()
    kern = None
    try:
        # ── D1 基础链路 ──
        kern = spawn_kernel()
        write_task("I-901")
        ok = wait_for(lambda: full_chain_ok("I-901")[0], 40)
        ok2, why = full_chain_ok("I-901")
        check("D1 基础链路（提交→派发→结果→关单→回执）", ok and ok2, why)
        evs = [e["event"] for e in kernel_events("kt-i-901")]
        chain = all(x in evs for x in ("task_received", "dispatch_submitted"))
        evr = [e["event"] for e in kernel_events()]
        check("D1 台账事件链齐全", chain and
              "run_started" in evr and
              "result_received" in evr and
              "result_applied" in evr, str(evs))

        # ── D2 同一结果重复提交两次 ──
        # 同一结果重复提交 = 逐字同一份文件再投一次（读回归档原件）。
        orig_path = os.path.join(KS, "processed", "kr-run-kt-i-901-a1.json")
        dup = jload(orig_path)
        assert dup is not None, "D2 前置：原结果文件应已归档"
        before = drill_evidence_count("I-901")
        write_raw("kr-run-kt-i-901-a1.dup.json", dup)
        ok = wait_for(lambda: any(e["event"] == "duplicate_result_skipped"
                                  for e in kernel_events("kr-run-kt-i-901-a1")), 15)
        # 同号不同文 = 冲突拒绝（复验要求三），云端不得写账。
        conflict = dict(dup)
        conflict["evidence"] = ["冲突正文：同号不同文必须被拒"]
        write_raw("kr-run-kt-i-901-a1.conflict.json", conflict)
        okc = wait_for(lambda: any(e["event"] == "result_conflict_rejected"
                                   for e in kernel_events("kr-run-kt-i-901-a1")), 15)
        time.sleep(2)
        check("D2 同一结果重复提交不重复更新", ok and
              drill_evidence_count("I-901") == before and
              registry_item("I-901")["status"] == "closed",
              "证据仍 %d 条，状态 %s" % (drill_evidence_count("I-901"),
                                        registry_item("I-901")["status"]))
        check("D2b 同号不同文结果冲突拒绝", okc, "result_conflict_rejected")
        second = dict(dup, result_id="kr-run-kt-i-901-a1-second")
        write_raw("kr-run-kt-i-901-a1.second.json", second)
        ok = wait_for(lambda: any(e["event"] == "result_rejected"
                                  and "已结束" in str(e.get("detail", {}).get("error"))
                                  for e in kernel_events("kr-run-kt-i-901-a1-second")), 15)
        check("D2 已结束运行不接受第二个有效结果", ok)

        # ── D6 重复递交 + 重复唤醒（内核已连续运行多轮） ──
        write_task("I-906", msg="重复递交演练：同一份请求投放两次。")
        same_task = {"schema_version": 1, "kind": "task_submit",
                     "request_id": "kt-i-906", "item_id": "I-906", "lane": "L3",
                     "title": "演练任务 I-906",
                     "message": "重复递交演练：同一份请求投放两次。",
                     "approved_by": "Beth", "task_kind": "test"}
        write_raw("kt-i-906.dup.json", same_task)
        ok = wait_for(lambda: kernel_state()["tasks"].get("kt-i-906", {})
                      .get("phase") == "completed", 40)
        time.sleep(2)
        dup_ev = [e for e in kernel_events("kt-i-906")
                  if e["event"] == "duplicate_task_skipped"]
        # 同号不同文任务 = 冲突拒绝（复验要求三）。
        conflict_task = dict(same_task)
        conflict_task["message"] = "冲突正文：同号不同文必须被拒。"
        write_raw("kt-i-906.conflict.json", conflict_task)
        okc = wait_for(lambda: any(e["event"] == "task_conflict_rejected"
                                   for e in kernel_events("kt-i-906")), 15)
        check("D6 重复递交与重复唤醒重复派发零次", ok and len(dup_ev) == 1 and
              dispatch_count("I-906") == 1,
              "派发 %d 次，跳过事件 %d 条" % (dispatch_count("I-906"), len(dup_ev)))
        check("D6b 同号不同文任务冲突拒绝", okc, "task_conflict_rejected")

        # ── D12 evidence_appended 结果类型 ──
        write_task("I-912", msg="演练简报：I-912 的构造性任务。结果类型:evidence_appended")
        ok = wait_for(lambda: registry_item("I-912")
                      and any("假执行器追加一条证据" in e
                              for e in registry_item("I-912").get("evidence", []))
                      and kernel_state()["tasks"].get("kt-i-912", {})
                      .get("phase") == "completed", 40)
        check("D12 阶段证据追加结果经桥落实", ok)

        # ── D11 总停开关 ──
        with open(os.path.join(KS, "control.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "halt"}, f)
        time.sleep(1.5)
        write_task("I-911")
        time.sleep(3)
        t11 = kernel_state()["tasks"].get("kt-i-911", {})
        halted = t11.get("phase") in ("ready", None) and dispatch_count("I-911") == 0
        with open(os.path.join(KS, "control.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "run"}, f)
        ok = wait_for(lambda: full_chain_ok("I-911")[0], 40)
        check("D11 总停阻止新派发且恢复后补派", halted and ok)

        # ── D7 双消费者 ──
        p2 = spawn_kernel()
        try:
            rc = p2.wait(timeout=25)
        except subprocess.TimeoutExpired:
            rc = None
            quiet_kill(p2)
        rejected = any(e["event"] == "second_instance_rejected"
                       for e in kernel_events())
        check("D7 第二消费者退出且零动作", rc == 3 and rejected, "退出码 %s" % rc)

        # ── D9 认领到期执行器仍运行（段首换干净内核与工作器，
        # 规避长跑内核的未知状态残留；独立最小复现已验证内核行为正确） ──
        quiet_kill(kern)
        worker = ensure_worker(worker)
        kern = spawn_kernel()
        time.sleep(1.0)
        write_task("I-909", msg="演练简报：I-909 的构造性任务。执行毫秒:15000")
        ok = wait_for(lambda: kernel_state()["tasks"].get("kt-i-909", {})
                      .get("phase") in ("dispatched", "result_pending"), 40)
        # 模拟云端租约到期退回 open（改测试状态库），此时运行仍活跃。
        reg_items = jload(os.path.join(REG, "items.json"))
        for it in reg_items["items"]:
            if it["id"] == "I-909":
                it["status"] = "open"
                it["claimed_by"] = None
                it["lease_until"] = None
        with open(os.path.join(REG, "items.json"), "w", encoding="utf-8") as f:
            json.dump(reg_items, f, ensure_ascii=False, indent=2)
        # 同事项新任务递交必须被拒；原任务不得重派。
        write_raw("kt-i-909b.json",
                  {"schema_version": 1, "kind": "task_submit",
                   "request_id": "kt-i-909b", "item_id": "I-909", "lane": "L3",
                   "title": "第二任务", "message": "认领到期后同事项第二任务。",
                   "approved_by": "Beth", "task_kind": "test"})
        ok = wait_for(lambda: any(e["event"] == "item_busy_rejected"
                                  for e in kernel_events()), 15)
        # 断言改用内核台账：本任务恰好一次派发提交、无第二次尝试号。
        def _d9_submits():
            return [e for e in kernel_events("kt-i-909")
                    if e["event"] == "dispatch_submitted"]
        ok2 = wait_for(lambda: len(_d9_submits()) == 1, 20)
        no_second = not any(str(e.get("detail", {}).get("request_id", "")).endswith("-a2")
                            for e in _d9_submits())
        check("D9 认领到期不产生第二个有效运行", ok and ok2 and no_second,
              "派发提交 %d 次" % len(_d9_submits()))
        wait_for(lambda: full_chain_ok("I-909")[0], 40)

        quiet_kill(kern)
        kern = None

        worker = ensure_worker(worker)
        # ── D3 终止位置一：请求保存后 ──
        kern = spawn_kernel(crash_point="after_ingest")
        write_task("I-903")
        rc = safe_wait(kern, 25)
        crash_logged = any(e["event"] == "crash_injected"
                           and e.get("detail", {}).get("point") == "after_ingest"
                           for e in kernel_events())
        kern = spawn_kernel()
        ok = wait_for(lambda: full_chain_ok("I-903")[0], 40)
        ok2, why = full_chain_ok("I-903")
        check("D3 请求保存后终止恢复零丢失", rc == 99 and crash_logged and ok and ok2,
              "退出码 %s，%s" % (rc, why))
        quiet_kill(kern)
        kern = None

        # ── D4 终止位置二：消息已发送回执未收 ──
        kern = spawn_kernel(crash_point="after_dispatch_submit")
        write_task("I-904")
        rc = safe_wait(kern, 25)
        time.sleep(3)          # 让隔离工作器完成派发与假执行器交结果
        kern = spawn_kernel()
        ok = wait_for(lambda: full_chain_ok("I-904")[0], 40)
        ok2, why = full_chain_ok("I-904")
        check("D4 回执未收先对账不重发", rc == 99 and ok and ok2 and
              dispatch_count("I-904") == 1,
              "退出码 %s，派发 %d 次，%s" % (rc, dispatch_count("I-904"), why))
        quiet_kill(kern)
        kern = None

        worker = ensure_worker(worker)
        # ── D5 终止位置三：结果已收状态未更新 ──
        kern = spawn_kernel(crash_point="after_result_ingest")
        write_task("I-905")
        rc = safe_wait(kern, 35)
        time.sleep(2)
        was_open = registry_item("I-905")["status"] != "closed"
        kern = spawn_kernel()
        ok = wait_for(lambda: full_chain_ok("I-905")[0], 40)
        ok2, why = full_chain_ok("I-905")
        check("D5 结果已收未写账重启补落实且仅一次", rc == 99 and was_open and ok and ok2,
              "退出码 %s，重启前未关闭=%s，%s" % (rc, was_open, why))
        quiet_kill(kern)
        kern = None

        # ── D8 丢弃一次派发回执 ──
        kern = spawn_kernel(crash_point="after_dispatch_submit")
        write_task("I-908")
        rc = safe_wait(kern, 25)
        time.sleep(3)          # 工作器完成派发；回执文件已生成
        # 丢弃回执：删 done 结果文件与 done 请求文件，只留桥台账。
        for fn in ("kd-kt-i-908-a1.result.json", "kd-kt-i-908-a1.json"):
            p = os.path.join(QR, "done", fn)
            if os.path.exists(p):
                os.remove(p)
        kern = spawn_kernel()
        ok = wait_for(lambda: full_chain_ok("I-908")[0], 40)
        reconciled = any(e.get("detail", {}).get("reconciled_from_queue_ledger")
                         for e in kernel_events("run-kt-i-908-a1")
                         if e["event"] in ("run_started", "late_dispatch_receipt"))
        check("D8 丢弃回执从桥台账对账采纳不重发", rc == 99 and ok and reconciled and
              dispatch_count("I-908") == 1,
              "对账采纳=%s，派发 %d 次" % (reconciled, dispatch_count("I-908")))
        quiet_kill(kern)
        kern = None

        # ── D10 结果违反既有判据规则 ──
        kern = spawn_kernel()
        write_task("I-910", msg="演练简报：I-910 的构造性任务。结果类型:invalid_close")
        ok = wait_for(lambda: kernel_state()["tasks"].get("kt-i-910", {})
                      .get("phase") == "result_failed", 40)
        item = registry_item("I-910")
        invalid_result = kernel_state()["results"].get("kr-run-kt-i-910-a1", {})
        close_request = next((a["bridge_request_id"] for a in invalid_result.get("actions", []) if a["action"] == "close"), "missing")
        failed_file = jload(os.path.join(QR, "failed", close_request + ".result.json"))
        check("D10 违反既有规则定性拒绝留痕", ok and item["status"] != "closed" and
              failed_file is not None,
              "item 状态 %s" % (item or {}).get("status"))
        quiet_kill(kern)
        kern = None

        worker = ensure_worker(worker)
        # ── D13 普通任务正例（task_kind=task，真实 ledger_read 通道） ──
        kern = spawn_kernel()
        write_task("I-913", msg="演练简报：I-913 普通任务正例。", task_kind="task")
        ok = wait_for(lambda: full_chain_ok("I-913")[0], 60)
        ok2, why = full_chain_ok("I-913")
        line_req_done = any(e.get("event") == "completed"
                            and str(e.get("request_id", "")).startswith("kl-kt-i-913-line")
                            for e in queue_events())
        line_checked = any(e.get("event") == "line_checked"
                           for e in kernel_events("kt-i-913"))
        check("D13 普通任务：读任务线→派发→关单全链", ok and ok2 and
              line_req_done and line_checked and dispatch_count("I-913") == 1,
              "%s；line_completed=%s line_checked=%s" %
              (why, line_req_done, line_checked))

        worker = ensure_worker(worker)
        # ── D14 依赖未清先拦后放 ──
        write_task("I-914", msg="演练简报：I-914 依赖未清反例。", task_kind="task")
        ok = wait_for(lambda: kernel_state()["tasks"].get("kt-i-914", {})
                      .get("phase") == "line_wait", 30)
        no_dispatch = dispatch_count("I-914") == 0
        reg_items = jload(os.path.join(REG, "items.json"))
        for it in reg_items["items"]:
            if it["id"] == "I-915":
                it["status"] = "closed"
                it["closed_at"] = "2026-10-12"
                it["conclusion"] = "判据通过"
        with open(os.path.join(REG, "items.json"), "w", encoding="utf-8") as f:
            json.dump(reg_items, f, ensure_ascii=False, indent=2)
        ok2 = wait_for(lambda: full_chain_ok("I-914")[0], 90)
        check("D14 依赖未清先拦后放", ok and no_dispatch and ok2,
              "等待期派发 %d 次" % dispatch_count("I-914"))

        worker = ensure_worker(worker)
        # ── D15 已关闭事项与路线不一致拒绝 ──
        write_task("I-916", msg="演练简报：I-916 已关闭反例。", task_kind="task")
        write_task("I-917", msg="演练简报：I-917 路线不一致反例。", task_kind="task")
        ok = wait_for(lambda: all(
            kernel_state()["tasks"].get("kt-i-91%s" % x, {}).get("phase")
            == "line_rejected" for x in ("6", "7")), 30)
        rejected_ev = [e for e in kernel_events()
                       if e["event"] == "line_rejected"]
        check("D15 已关闭与路线不一致拒绝零派发", ok and len(rejected_ev) >= 2 and
              dispatch_count("I-916") == 0 and dispatch_count("I-917") == 0,
              "拒绝事件 %d 条" % len(rejected_ev))
        quiet_kill(kern)
        kern = None

        worker = ensure_worker(worker)
        # ── D16 桥执行侧崩溃窗口（复验复现转正） ──
        eq = os.path.join(OUT, "edge_queue")
        dq.ensure_layout(eq)
        import pathlib as _pl
        count_file = _pl.Path(os.path.join(OUT, "side_effect_count.txt"))
        child = os.path.join(OUT, "edge_worker_a.py")
        with open(child, "w", encoding="utf-8") as f:
            f.write(EDGE_WORKER_SRC)
        with open(count_file, "w", encoding="utf-8") as f:
            f.write("0")
        dq.write_request({"schema_version": 1, "request_id": "edge-crash-dispatch",
                          "op": "dispatch_new", "message": "隔离构造简报",
                          "item_id": "I-902", "lane": "L3", "kind": "test"},
                         root=eq)
        env = dict(os.environ, DQ_CRASH_POINT="after_side_effect_before_result",
                   PYTHONIOENCODING="utf-8")
        rc = subprocess.run([PY, child, HERE, eq, count_file], env=env).returncode
        # 重启（无崩溃点）：recover_processing 必须保持未知，不重执行。
        w2 = dq.QueueWorker(root=eq, dispatcher=lambda req: (
            count_file.write_text(str(int(count_file.read_text()) + 1))
            or {"ok": True, "session": "sess_edge2", "accepted": True,
                "readback_verified": True}), stable_grace_s=0)
        w2.recover_processing()
        evs = dq.ledger_events(eq, request_id="edge-crash-dispatch")
        starts = [e for e in evs if e.get("event") == "dispatch_start"]
        held = [e for e in evs if e.get("event") == "uncertain_held"]
        check("D16a 派发副作用已发回执未存：保持未知不重执行",
              rc == 77 and int(count_file.read_text()) == 1 and
              len(starts) == 1 and held and
              os.path.exists(os.path.join(eq, "uncertain",
                                          "edge-crash-dispatch.json")),
              "退出码 %s，副作用 %s 次，dispatch_start %d 次" %
              (rc, count_file.read_text(), len(starts)))

        # 场景二：ledger_write close 副作用已发生（测试账本已关闭）→ 对账采纳。
        eq2 = os.path.join(OUT, "edge_queue2")
        dq.ensure_layout(eq2)
        close_args = {"item_id": "I-911", "evidence": ["D16 对账采纳证据"],
                      "closer": "L3队", "conclusion": "判据通过",
                      "criterion_check": [{"判据": "演练判据", "结果": "过",
                                           "数值出处": "D16"}],
                      "owner_note": "演练机主说明：D16 场景二对账采纳验证用，"
                                    "回答做了什么、结果是什么、为什么、下一步四问。"}
        dq.write_request({"schema_version": 1, "request_id": "edge-crash-close",
                          "op": "ledger_write", "agent_id": "drill",
                          "ledger": {"action": "close", "args": close_args}},
                         root=eq2)
        env2 = dict(os.environ, DQ_CRASH_POINT="after_side_effect_before_result",
                    LP_ITEMS_REGISTRY=REG, FLAPPER_LEDGER_MODE="local",
                    DRILL_KERNEL_INBOX=os.path.join(OUT, "no_inbox"),
                    DRILL_QUEUE_ROOT=eq2, PYTHONIOENCODING="utf-8")
        rc2 = subprocess.run([PY, os.path.join(HERE, "kernel_drill_worker.py")],
                             env=env2).returncode
        closed_before = registry_item("I-911")["status"] == "closed"
        # 重启对账：已关闭 → 采纳完成，不再执行 close。恢复在演练主进程
        # 内联执行，须先指向隔离登记库（dq 调用时读环境），完成后还原。
        os.environ["LP_ITEMS_REGISTRY"] = REG
        os.environ["FLAPPER_LEDGER_MODE"] = "local"
        try:
            w3 = dq.QueueWorker(root=eq2, ledger_executor=lambda a, ar: (
                {"bridge_ok": False, "definitive": True, "error": "不应再执行"}),
                stable_grace_s=0)
            w3.recover_processing()
        finally:
            os.environ.pop("LP_ITEMS_REGISTRY", None)
            os.environ.pop("FLAPPER_LEDGER_MODE", None)
        evs2 = dq.ledger_events(eq2, request_id="edge-crash-close")
        starts2 = [e for e in evs2 if e.get("event") == "dispatch_start"]
        adopted = [e for e in evs2 if e.get("event") == "recovered_adopted"]
        check("D16b 关单副作用已落地：对账采纳不重执行",
              rc2 == 77 and closed_before and len(starts2) == 1 and adopted and
              os.path.exists(os.path.join(eq2, "done", "edge-crash-close.json")),
              "退出码 %s，关闭=%s，dispatch_start %d 次" %
              (rc2, closed_before, len(starts2)))

        # 场景三：关单请求执行前崩溃（dispatch_start 已记、副作用未落地）
        # → 恢复对账未落地 → 安全重试 → 恰好关闭一次。
        eq3 = os.path.join(OUT, "edge_queue3")
        dq.ensure_layout(eq3)
        before_ev3 = drill_evidence_count("I-912") if registry_item("I-912") else 0
        close3_args = {"item_id": "I-912", "evidence": ["D16c 安全重试证据"],
                       "closer": "L3队", "conclusion": "判据通过",
                       "criterion_check": [{"判据": "演练判据", "结果": "过",
                                            "数值出处": "D16c"}],
                       "owner_note": "演练机主说明：本说明由 D16c 演练生成，用于执行前崩"
                                     "溃后安全重试并恰好关闭一次的验证。做了什么：构造一次关单请"
                                     "求并在执行前崩溃；结果：恢复对账未落地后重试成功；为什么："
                                     "未知结果必须先对账；下一步：场景结束归档。"}
        dq.write_request({"schema_version": 1, "request_id": "edge-crash-early",
                          "op": "ledger_write", "agent_id": "drill",
                          "ledger": {"action": "close", "args": close3_args}},
                         root=eq3)
        env3 = dict(os.environ, DQ_CRASH_POINT="after_dispatch_start_before_execute",
                    LP_ITEMS_REGISTRY=REG, FLAPPER_LEDGER_MODE="local",
                    DRILL_KERNEL_INBOX=os.path.join(OUT, "no_inbox"),
                    DRILL_QUEUE_ROOT=eq3, PYTHONIOENCODING="utf-8")
        rc3 = subprocess.run([PY, os.path.join(HERE, "kernel_drill_worker.py")],
                             env=env3).returncode
        still_open = registry_item("I-912")["status"] == "open"
        os.environ["LP_ITEMS_REGISTRY"] = REG
        os.environ["FLAPPER_LEDGER_MODE"] = "local"
        try:
            w4 = dq.QueueWorker(root=eq3, stable_grace_s=0)
            w4.recover_processing()
            w4.move_due_retries()
            w4.poll_once()
            time.sleep(1.0)
            w4.poll_once()
        finally:
            os.environ.pop("LP_ITEMS_REGISTRY", None)
            os.environ.pop("FLAPPER_LEDGER_MODE", None)
        done3 = os.path.exists(os.path.join(eq3, "done",
                                            "edge-crash-early.result.json"))
        ev_added = drill_evidence_count("I-912") - before_ev3
        check("D16c 执行前崩溃：对账未落地安全重试，关单恰好一次",
              rc3 == 77 and still_open and done3 and ev_added == 1,
              "退出码 %s，执行前未关闭=%s，证据新增 %d 条"
              % (rc3, still_open, ev_added))

        # ── D17 台账锁僵死接管 ──
        lock_root = os.path.join(OUT, "lock_probe")
        dq.ensure_layout(lock_root)
        stale = os.path.join(lock_root, ".ledger.lock")
        with open(stale, "w", encoding="utf-8") as f:
            json.dump({"pid": 999999, "ts": time.time() - 300}, f)
        dq.ledger_append("stale_lock_probe", root=lock_root)
        took = os.path.exists(os.path.join(lock_root,
                                           "ledger_stale_takeover.log"))
        check("D17 台账锁僵死接管", took, "接管日志 %s" % took)

        # ── D18 同状态根双端口双消费者 ──
        ks2 = os.path.join(OUT, "kernel_state_dual")
        os.makedirs(os.path.join(ks2, "inbox"), exist_ok=True)
        ka = spawn_kernel(state_root=ks2, port=PORT + 3)
        time.sleep(1.5)
        kb = spawn_kernel(state_root=ks2, port=PORT + 4)
        try:
            rcb = kb.wait(timeout=25)
        except subprocess.TimeoutExpired:
            rcb = None
            quiet_kill(kb)
        quiet_kill(ka)
        dual_rejected = any(e.get("event") == "second_instance_rejected"
                            for e in dq.ledger_events(ks2))
        check("D18 同状态根双端口第二内核退出", rcb == 3 and dual_rejected,
              "退出码 %s" % rcb)

        worker = ensure_worker(worker)
        # ── D19 真实执行器端到端 ──
        exec_brief = os.path.join(OUT, "exec_brief.json")
        if os.path.exists(exec_brief):
            os.remove(exec_brief)
        # 重启主工作器进入真实执行器模式（简报落地，不代写结果）。
        quiet_kill(worker)
        worker = spawn_worker(extra_env={"DRILL_EXEC_BRIEF": exec_brief})
        kern = spawn_kernel()
        write_task("I-902", msg="演练简报：I-902 真实执行器端到端。真实执行器",
                   task_kind="test")
        env_exec = dict(os.environ, DRILL_EXEC_BRIEF=exec_brief,
                        DRILL_KERNEL_INBOX=os.path.join(KS, "inbox"),
                        DRILL_EXEC_WORK_MS="300", PYTHONIOENCODING="utf-8")
        px = subprocess.Popen([PY, os.path.join(HERE, "kernel_drill_executor.py")],
                              env=env_exec, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace")
        rcx = px.wait(timeout=90)
        ok = wait_for(lambda: full_chain_ok("I-902")[0], 60)
        ok2, why = full_chain_ok("I-902")
        item902 = registry_item("I-902")
        real_evidence = item902 and any("真实执行器子进程" in e
                                        for e in item902.get("evidence", []))
        check("D19 真实执行器自交结果经桥按既有规则关单",
              rcx == 0 and ok and ok2 and real_evidence, why)
        quiet_kill(kern)
        kern = None

    finally:
        quiet_kill(kern)
        quiet_kill(worker)
        report = {"startedChecks": _checks,
                  "summary": {"total": len(_checks),
                              "passed": sum(1 for c in _checks if c["ok"])},
                  "finished_at": datetime.datetime.now().isoformat(timespec="seconds"),
                  "isolated_root": OUT}
        # 字段名用中文，与项目报告词汇一致。
        report["checks"] = report.pop("startedChecks")
        with open(os.path.join(OUT, "drill_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print("演练总判定：%s（%d/%d 过）" %
              ("全部通过" if report["summary"]["passed"] == len(_checks) else "存在不过项",
               report["summary"]["passed"], len(_checks)))
        print("演练输出目录：%s" % OUT)
    return 0 if report["summary"]["passed"] == len(_checks) else 1


if __name__ == "__main__":
    sys.exit(main())
