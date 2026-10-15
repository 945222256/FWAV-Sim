# -*- coding: utf-8 -*-
"""i233_pending_work_test.py — I-233 待领工作区契约隔离检查。

覆盖判据一至六的隔离样例：单项两次重试、未分配、需求与任务、历史
类别缺失、依赖撤回、资源冲突、无预留策略、采样失联、认领与运行
冲突、双工位之一释放并接续；事件重复两次、重启与断线恢复去重与
漏段核对；独立云端实例上的真实上行与只读回读。全部夹具与状态目录
重定向至 logs/i233_pending_work_20261014/fixture，独立端口 9463；
不重启、不停止任何生产服务，不新造生产测试事项。仅标准库。
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LP_SYNC = os.path.join(ROOT, "lp_sync")
OUT_DIR = os.path.join(ROOT, "logs", "i233_pending_work_20261014")
FIXTURE = os.path.join(OUT_DIR, "fixture")
RESULTS_PATH = os.path.join(OUT_DIR, "isolation_check_results.json")
CLOUD_PORT = 9463
NOW = time.time()


def iso_ago(seconds):
    return datetime.datetime.fromtimestamp(NOW - seconds).isoformat(
        timespec="milliseconds")


CHECKS = []


def check(cid, desc, ok, evidence=None):
    CHECKS.append({"id": cid, "desc": desc, "pass": bool(ok),
                   "evidence": evidence})
    print(("PASS " if ok else "FAIL ") + cid + " " + desc)


def jd(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def build_fixture():
    if os.path.isdir(FIXTURE):
        shutil.rmtree(FIXTURE)
    for sub in ("state", "events_store"):
        os.makedirs(os.path.join(FIXTURE, sub))
    items = [
        # S1 单事项两次重试：两项内核请求指向同一事项
        {"id": "I-901", "title": "两次重试任务", "kind": "task",
         "status": "open", "lane": "L9", "reviewer": "beth"},
        # S2 未分配路线
        {"id": "I-902", "title": "未分配任务", "kind": "task",
         "status": "open", "lane": None},
        # S3 需求排除
        {"id": "I-903", "title": "需求条目", "kind": "requirement",
         "status": "open", "lane": "L9"},
        # S4 历史类别缺失（在办范围内）
        {"id": "I-904", "title": "类别缺失", "kind": None,
         "status": "open", "lane": "L9"},
        # S5 依赖未清与交付撤回
        {"id": "I-905", "title": "依赖未清", "kind": "task",
         "status": "open", "lane": "L9", "depends_on": ["I-906"]},
        {"id": "I-906", "title": "未完成依赖", "kind": "task",
         "status": "in_progress", "lane": "L8",
         "claimed_by": "L8队", "claim_session_id": "sess_dep_holder"},
        {"id": "I-907", "title": "交付撤回影响", "kind": "task",
         "status": "open", "lane": "L9", "delivery_impact": [
             {"review_required": True, "from": "I-050"}]},
        # S6 资源冲突（内核准入结果搬运）
        {"id": "I-908", "title": "资源冲突", "kind": "task",
         "status": "open", "lane": "L9"},
        # S9 认领与运行矛盾
        {"id": "I-909", "title": "认领运行矛盾", "kind": "task",
         "status": "in_progress", "lane": "L7", "claimed_by": "L7队",
         "claim_session_id": "sess_claim_a"},
        # S10 双工位释放与接续
        {"id": "I-910", "title": "接续任务", "kind": "task",
         "status": "in_progress", "lane": "L9", "claimed_by": "L9队",
         "claim_session_id": "sess_w1_successor"},
        {"id": "I-911", "title": "另一工位在办", "kind": "task",
         "status": "in_progress", "lane": "L9", "claimed_by": "L9队",
         "claim_session_id": "sess_w2_running"},
        # 排除项
        {"id": "I-912", "title": "待审核", "kind": "task",
         "status": "pending_review", "lane": "L9"},
        {"id": "I-913", "title": "已完成", "kind": "task",
         "status": "closed", "lane": "L9"},
        {"id": "I-914", "title": "测试通道", "kind": "test",
         "status": "open", "lane": "L9"},
        {"id": "I-915", "title": "一致认领排除", "kind": "task",
         "status": "in_progress", "lane": "L6", "claimed_by": "L6队",
         "claim_session_id": "sess_w2_running"},
    ]
    tasks = {
        "kt-i901-a": {"task_req_id": "kt-i901-a", "item_id": "I-901",
                      "lane": "L9", "phase": "ready",
                      "created_at": iso_ago(600), "dispatch_attempts": 1,
                      "last_error": None, "worker_id": None},
        "kt-i901-b": {"task_req_id": "kt-i901-b", "item_id": "I-901",
                      "lane": "L9", "phase": "ready",
                      "created_at": iso_ago(300), "dispatch_attempts": 1,
                      "last_error": None, "worker_id": None},
        "kt-i908": {"task_req_id": "kt-i908", "item_id": "I-908",
                    "lane": "L9", "phase": "ready",
                    "created_at": iso_ago(500), "dispatch_attempts": 0,
                    "last_error": "可写范围与活跃任务 kt-i911 重叠："
                                  "logs/x ∩ logs/x",
                    "worker_id": None},
        "kt-i910": {"task_req_id": "kt-i910", "item_id": "I-910",
                    "lane": "L9", "phase": "dispatched",
                    "created_at": iso_ago(400), "dispatch_attempts": 1,
                    "dispatched_at": iso_ago(100),
                    "session_id": "sess_w1_successor",
                    "run_id": "run-i910-a1", "worker_id": "L9-w1",
                    "last_error": None},
        "kt-i911": {"task_req_id": "kt-i911", "item_id": "I-911",
                    "lane": "L9", "phase": "dispatched",
                    "created_at": iso_ago(900), "dispatch_attempts": 1,
                    "dispatched_at": iso_ago(700),
                    "session_id": "sess_w2_running",
                    "run_id": "run-i911-a1", "worker_id": "L9-w2",
                    "last_error": None},
    }
    runs = {
        "run-i909-a1": {"run_id": "run-i909-a1", "task_req_id": "kt-i909",
                        "item_id": "I-909", "session_id": "sess_run_b",
                        "started_at": iso_ago(200), "status": "active",
                        "worker_id": "L7-w1"},
        "run-i910-a1": {"run_id": "run-i910-a1", "task_req_id": "kt-i910",
                        "item_id": "I-910", "session_id": "sess_w1_successor",
                        "started_at": iso_ago(95), "status": "active",
                        "worker_id": "L9-w1",
                        "dispatch_request_id": "kd-kt-i910-a1"},
        "run-i911-a1": {"run_id": "run-i911-a1", "task_req_id": "kt-i911",
                        "item_id": "I-911", "session_id": "sess_w2_running",
                        "started_at": iso_ago(690), "status": "active",
                        "worker_id": "L9-w2"},
    }
    ledger_events = [
        {"ts": iso_ago(120), "event": "lane_slot_released",
         "request_id": "kt-i910-old", "detail": {"item_id": "I-910",
                                                 "lane": "L9",
                                                 "worker_id": "L9-w1"}},
        # 同一释放事实在账本中重复记录两次（去重样例）
        {"ts": iso_ago(120), "event": "lane_slot_released",
         "request_id": "kt-i910-old", "detail": {"item_id": "I-910",
                                                 "lane": "L9",
                                                 "worker_id": "L9-w1"}},
        {"ts": iso_ago(110), "event": "dispatch_submitted",
         "request_id": "kd-kt-i910-a1",
         "detail": {"item_id": "I-910", "lane": "L9", "run_id": "run-i910-a1"}},
        {"ts": iso_ago(97), "event": "run_started",
         "request_id": "kd-kt-i910-a1",
         "detail": {"item_id": "I-910", "run_id": "run-i910-a1"}},
        {"ts": iso_ago(60), "event": "line_wait", "request_id": "kt-i908",
         "detail": {"item_id": "I-908"}},
        {"ts": iso_ago(30), "event": "turn_recovery_resubmit",
         "request_id": "kc-x", "detail": {"item_id": "I-911",
                                          "run_id": "run-i911-a1"}},
    ]
    jd(os.path.join(FIXTURE, "items_file.json"),
       {"items": items})
    jd(os.path.join(FIXTURE, "state", "state.json"),
       {"tasks": tasks, "runs": runs, "generation": 7,
        "last_tick_at": iso_ago(2)})
    jd(os.path.join(FIXTURE, "state", "lane_workers.json"),
       {"default": 1, "lanes": {"L9": 2, "L7": 1}})
    jd(os.path.join(FIXTURE, "roster.json"),
       {"workers": {"L9-w1": {"lane": "L9",
                               "session_id": "sess_w1_successor",
                               "phase": "active", "item_id": "I-910"},
                    "L9-w2": {"lane": "L9",
                               "session_id": "sess_w2_running",
                               "phase": "active", "item_id": "I-911"}}})
    jd(os.path.join(FIXTURE, "state", "ledger.json"),
       {"events": ledger_events})


def set_env(items_file=None):
    os.environ.update({
        "PW_ITEMS_SOURCE": "file",
        "PW_ITEMS_FILE": items_file or os.path.join(FIXTURE, "items_file.json"),
        "LP_KERNEL_STATE_ROOT": os.path.join(FIXTURE, "state"),
        "LP_LANE_WORKERS_ROSTER": os.path.join(FIXTURE, "roster.json"),
        "PW_EVENTS_DIR": os.path.join(FIXTURE, "events_store"),
    })
    if LP_SYNC not in sys.path:
        sys.path.insert(0, LP_SYNC)


def snapshot_checks():
    import pending_work as pw
    snap = pw.build_snapshot()
    s = snap["summary"]
    by_item = {e["item_id"]: e for e in snap["items"]}

    check("S1-two-retries-one-item", "单事项两次请求只计一项且请求层保留",
          s["total_pending"] == len(snap["items"])
          and sum(1 for e in snap["items"] if e["item_id"] == "I-901") == 1
          and len(by_item["I-901"]["enqueue"]["request_ids"]) == 2,
          {"i901": by_item.get("I-901", {}).get("enqueue")})
    check("S2-unassigned", "未分配路线进独立入口不伪装业务线",
          s["unassigned"]["count"] == 1
          and s["unassigned"]["items"] == ["I-902"]
          and "L9" in s["by_lane"] and None not in s["by_lane"],
          {"unassigned": s["unassigned"]})
    check("S3-requirement-excluded", "需求排除计数且任务计入",
          s["exclusions"]["requirement"]["count"] == 1
          and "I-903" not in by_item,
          {"exclusion": s["exclusions"]["requirement"]})
    check("S4-kind-missing", "在办历史类别缺失列待核对不批改",
          any(n["item_id"] == "I-904" and n["code"] == "KIND_UNKNOWN"
              for n in snap["needs_verification"])
          and "I-904" not in by_item,
          {"nv": [n for n in snap["needs_verification"]
                  if n["item_id"] == "I-904"]})
    e905 = by_item.get("I-905")
    e907 = by_item.get("I-907")
    check("S5-dep-and-withdrawn", "依赖未清与交付撤回分别给码与恢复条件",
          e905 and e905["primary_reason"]["code"] == "DEP_NOT_CLEARED"
          and e905["category"] == "waiting_conditions"
          and e905["related"]["dependencies"][0]["status"] == "in_progress"
          and e907 and e907["primary_reason"]["code"] ==
          "DEP_WITHDRAWN_UNREVIEWED",
          {"i905": e905 and e905["primary_reason"],
           "i907": e907 and e907["primary_reason"]})
    e908 = by_item.get("I-908")
    check("S6-resource-conflict", "资源冲突采用内核准入结果搬运",
          e908 and e908["primary_reason"]["code"] == "RESOURCE_WRITABLE_OVERLAP"
          and e908["primary_reason"]["provenance"] ==
          "kernel_admission_last_error",
          {"i908": e908 and e908["primary_reason"]})
    check("S7-no-reservation", "无预留策略明确未设置且 reserved 为 null",
          snap["capacity"]["reservation_policy"]["status"] == "not_set"
          and snap["capacity"]["global"]["reserved"] is None
          and snap["capacity"]["by_lane"]["L9"]["effective_limit"] == 2
          and snap["capacity"]["by_lane"]["L9"]["occupied"] == 2,
          {"global": snap["capacity"]["global"],
           "L9": snap["capacity"]["by_lane"]["L9"]})
    check("S9-claim-run-conflict", "认领与运行矛盾列待核对并从已认领调整",
          any(n["item_id"] == "I-909"
              and n["code"] == "CLAIM_RUN_SESSION_MISMATCH"
              for n in snap["needs_verification"]),
          {"nv": [n for n in snap["needs_verification"]
                  if n["item_id"] == "I-909"]})
    check("SC-summary-consistency", "汇总明细各类计数逐项一致",
          s["total_pending"] == len(snap["items"])
          and sum(s["by_category"].values()) == s["total_pending"]
          and sum(v["count"] for v in s["by_lane"].values()) +
          s["unassigned"]["count"] == s["total_pending"],
          {"summary": s["by_category"]})
    # 失联样例：事项文件不可读
    os.environ["PW_ITEMS_FILE"] = os.path.join(FIXTURE, "missing.json")
    import importlib
    importlib.reload(pw)
    snap2 = pw.build_snapshot()
    check("S8-sample-loss", "来源失联如实降级不假零",
          snap2["completeness"]["status"] == "partial"
          and any(r["severity"] == "degraded"
                  for r in snap2["completeness"]["reasons"]),
          {"completeness": snap2["completeness"]})
    os.environ["PW_ITEMS_FILE"] = os.path.join(FIXTURE, "items_file.json")
    importlib.reload(pw)
    return snap


def events_checks():
    import pending_work_events as pwe
    result = pwe.rebuild(persist=True)
    events = result["events"]
    ids = [e["event_id"] for e in events]
    by_type = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e)
    check("E1-duplicate-fact-dedup", "账本重复记录的同一事实只计一次",
          len(ids) == len(set(ids))
          and len([e for e in by_type.get("slot_released", [])
                   if e["identity"]["item_id"] == "I-910"]) == 1,
          {"total": len(events),
           "slot_released_i910": len(
               [e for e in by_type.get("slot_released", [])
                if e["identity"]["item_id"] == "I-910"])})
    # 双工位接续链：释放→选择→认领→运行，另一工位不受影响
    chain = [e for e in events if e["identity"]["item_id"] == "I-910"]
    chain.sort(key=lambda e: e.get("occurred_at") or "")
    types = [e["type"] for e in chain]
    check("E2-continuation-chain",
          "释放→选择→认领确认→实际运行链完整且身份一致",
          types == ["slot_released", "dispatch_selected", "claim_confirmed",
                    "run_started"]
          and chain[3]["identity"]["session_id"] == "sess_w1_successor"
          and chain[3]["identity"]["worker_id"] == "L9-w1"
          and chain[3]["identity"]["run_id"] == "run-i910-a1"
          and all(e.get("causes") or i == 0 for i, e in enumerate(chain)),
          {"types": types, "run": chain[3]["identity"] if len(chain) > 3
           else None})
    w2 = [e for e in events if e["identity"]["item_id"] == "I-911"]
    check("E3-other-worker-intact", "另一工位事件独立不受接续影响",
          any(e["type"] == "run_started"
              and e["identity"]["worker_id"] == "L9-w2" for e in w2),
          {"w2_types": [e["type"] for e in w2]})
    check("E4-no-fake-run-started", "无实际运行记录不生成开工事件",
          all(e["identity"].get("run_id")
              for e in by_type.get("run_started", []))
          and not any(e["identity"].get("item_id") == "I-901"
                      and e["type"] == "run_started" for e in events),
          {"run_started_items": sorted(
              {e["identity"].get("item_id")
               for e in by_type.get("run_started", [])})})
    # 重启重建：游标稳定、计数不变
    meta_before = dict(result["meta"])
    result2 = pwe.rebuild(persist=True)
    check("E5-restart-idempotent", "重启重建计数与标识稳定不重复",
          result2["meta"]["event_count"] == meta_before["event_count"]
          and [e["event_id"] for e in result2["events"]] == ids,
          {"before": meta_before["event_count"],
           "after": result2["meta"]["event_count"]})
    # 断线重连：after_cursor 增量
    mid = ids and result2["events"][len(ids) // 2]["cursor"]
    inc = pwe.read_events(after_cursor=mid)
    check("E6-reconnect-incremental", "重连增量只取游标之后事件",
          all(e["cursor"] > mid for e in inc["events"])
          and inc["next_cursor"] >= mid,
          {"after": mid, "returned": len(inc["events"])})
    # 漏段核对：伪造高水位后账本变短
    meta_path = pwe.META_PATH
    meta = json.load(open(meta_path, encoding="utf-8"))
    meta["seen_high_water"] = 999
    jd(meta_path, meta)
    result3 = pwe.rebuild(persist=True)
    check("E7-gap-detection", "来源高水位回退时报告漏段",
          bool(result3["gaps"]), {"gaps": result3["gaps"]})
    return events


def cloud_checks(snapshot):
    """隔离云端实例：真实上行与只读回读、事件服务端去重。"""
    stack = os.path.join(FIXTURE, "cloud")
    os.makedirs(stack, exist_ok=True)
    import hashlib
    import secrets
    with open(os.path.join(stack, "secret.json"), "w", encoding="utf-8") as f:
        json.dump({"session_key": secrets.token_urlsafe(48)}, f)
    import subprocess as sp
    openssl = r"C:\Program Files\Git\mingw64\bin\openssl.EXE"
    if not os.path.exists(openssl):
        import shutil as sh
        openssl = sh.which("openssl")
    sp.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", os.path.join(stack, "key.pem"),
            "-out", os.path.join(stack, "cert.pem"), "-days", "7",
            "-subj", "/CN=lp-pw-iso"], check=True, capture_output=True)
    env = dict(os.environ)
    env.update({"LP_CLOUD_HTTP_HOST": "127.0.0.1",
                "LP_CLOUD_HTTP_PORT": str(CLOUD_PORT),
                "LP_CLOUD_DB_PATH": os.path.join(stack, "ledger.db"),
                "LP_CLOUD_SECRET_PATH": os.path.join(stack, "secret.json"),
                "LP_CLOUD_CERT_FILE": os.path.join(stack, "cert.pem"),
                "LP_CLOUD_KEY_FILE": os.path.join(stack, "key.pem"),
                "LP_CLOUD_HOST": "127.0.0.1",
                "LP_CLOUD_PORT": str(CLOUD_PORT),
                "PYTHONUTF8": "1"})
    # 播种令牌（真实 agent_token 哈希入隔离库）
    seed = sp.run([sys.executable, "-c", r'''
import sys, os, json, hashlib, datetime
sys.path.insert(0, r"%s")
import db as cdb
cdb.init_schema()
token = open(r"%s", encoding="utf-8").read().strip()
with cdb.transaction() as conn:
    conn.execute("INSERT INTO tokens (name, secret_hash, lanes, revoked, created_at)"
                 " VALUES (?,?,?,?,?)",
                 ("i233-probe", hashlib.sha256(token.encode()).hexdigest(),
                  json.dumps(["L1","L3","L9"]), 0,
                  datetime.date.today().isoformat()))
print("seeded")
''' % (os.path.join(ROOT, "cloud_board"),
       os.path.join(ROOT, "lp_sync", "agent_token"))],
                  env=env, capture_output=True, text=True)
    if "seeded" not in (seed.stdout or ""):
        check("C0-cloud-seed", "隔离云播种", False,
              {"stderr": seed.stderr[-200:]})
        return
    proc = sp.Popen([sys.executable,
                     os.path.join(ROOT, "cloud_board", "server_main.py")],
                    env=env, cwd=os.path.join(ROOT, "cloud_board"),
                    stdout=open(os.path.join(stack, "server.log"), "a",
                                encoding="utf-8"),
                    stderr=subprocess.STDOUT,
                    creationflags=0x08000000)
    base = "https://127.0.0.1:%d" % CLOUD_PORT
    import ssl
    ctx = ssl._create_unverified_context()
    try:
        def call(method, path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + path, data=data, method=method,
                                         headers={"Content-Type":
                                                  "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as exc:
                return {"http_error": exc.code,
                        "body": exc.read().decode()[:200]}
        ready = False
        import socket as _sk
        for _ in range(60):
            try:
                with _sk.create_connection(("127.0.0.1", CLOUD_PORT),
                                            timeout=1):
                    ready = True
                    break
            except OSError:
                time.sleep(0.5)
        if ready:
            time.sleep(2.0)   # 应用层初始化余量
        for _ in range(20):
            # 经真实 cloud_client 通道探测（携带令牌）
            probe = sp.run([sys.executable, "-c",
                           "import cloud_client;"
                           "print(cloud_client.request('GET',"
                           "'/api/console/pending_work'))"],
                           env=env, capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "items_registry"))
            if probe.returncode == 0 and "ok" in (probe.stdout or ""):
                ready = True
                break
            time.sleep(0.5)
        check("C1-cloud-up", "隔离云端就绪（独立端口与库）", ready, {})
        push_cmd = r'''
import sys, json, time
sys.path.insert(0, r"%s")
import pending_work, pending_work_events
for attempt in range(3):
    try:
        snap = pending_work.push_once()
        ev, resp = pending_work_events.push_once()
        print(json.dumps({"snapshot_id": snap[0]["snapshot_id"],
                          "ev_count": len(ev["events"]),
                          "ingest": resp}, ensure_ascii=False))
        break
    except Exception as exc:
        if attempt == 2:
            print(json.dumps({"error": repr(exc)[:300]}))
        time.sleep(2)
''' % LP_SYNC
        push = sp.run([sys.executable, "-c", push_cmd], env=env,
                      capture_output=True, text=True, cwd=LP_SYNC)
        try:
            pushed = json.loads((push.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            pushed = {"error": (push.stderr or push.stdout or "")[-300:]}
        check("C2-push-snapshot-events", "快照与事件经真实通道上行",
              "snapshot_id" in pushed and pushed.get("ev_count"),
              {"pushed": pushed, "stderr": push.stderr[-200:]})
        got = sp.run([sys.executable, "-c",
                      "import cloud_client,json;"
                      "r=cloud_client.request('GET',"
                      "'/api/console/pending_work');"
                      "print(json.dumps({'sid': r.get('snapshot_id'),"
                      "'total': r['summary']['total_pending']},"
                      "ensure_ascii=False))"],
                     env=env, capture_output=True, text=True,
                     cwd=os.path.join(ROOT, "items_registry"))
        try:
            readback = json.loads((got.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            readback = {}
        check("C3-readback-same-snapshot", "只读回读与上行同一快照身份",
              readback.get("sid") == pushed.get("snapshot_id")
              and readback.get("total") ==
              snapshot["summary"]["total_pending"],
              {"readback": readback})
        push2 = sp.run([sys.executable, "-c", r'''
import sys, json
sys.path.insert(0, r"%s")
import pending_work_events as pwe
payload, resp = pwe.push_once()
print(json.dumps(resp, ensure_ascii=False))
''' % LP_SYNC], env=env, capture_output=True, text=True, cwd=LP_SYNC)
        try:
            resp2 = json.loads((push2.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            resp2 = {}
        added = None
        if isinstance(resp2, dict):
            added = resp2.get("added", (resp2.get("result") or {}).get("added"))
        check("C4-server-dedup", "重复上行事件服务端零新增",
              added == 0, {"second_push": resp2})
        cursored = sp.run([sys.executable, "-c",
                           "import cloud_client,json;"
                           "r=cloud_client.request('GET',"
                           "'/api/console/pending_work/events?after_cursor=2');"
                           "print(json.dumps({'n': len(r['events']),"
                           "'next': r.get('next_cursor'),"
                           "'unique': r.get('stored_unique')},"
                           "ensure_ascii=False))"],
                          env=env, capture_output=True, text=True,
                          cwd=os.path.join(ROOT, "items_registry"))
        try:
            cur = json.loads((cursored.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            cur = {}
        check("C5-cursor-read", "after_cursor 增量读取且存储去重",
              cur.get("n", 99) <= (pushed.get("ev_count") or 99)
              and cur.get("unique") is True, {"cursor_read": cur})
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return


def main():
    build_fixture()
    set_env()
    before = {f: os.stat(os.path.join(FIXTURE, "state", f)).st_mtime
              for f in ("state.json", "lane_workers.json", "ledger.json")}
    snap = snapshot_checks()
    events_checks()
    cloud_checks(snap)
    after = {f: os.stat(os.path.join(FIXTURE, "state", f)).st_mtime
             for f in ("state.json", "lane_workers.json", "ledger.json")}
    check("R1-read-only-sources", "快照与事件构建不写内核状态来源",
          before == after, {"before": before, "after": after})
    passed = sum(1 for c in CHECKS if c["pass"])
    result = {"item_id": "I-233",
              "run_id": "run-beth-i233-pending-work-backend-20261014-a1",
              "fixture": FIXTURE, "passed": passed, "total": len(CHECKS),
              "all_pass": passed == len(CHECKS), "checks": CHECKS}
    jd(RESULTS_PATH, result)
    print("结果写入 %s：%d/%d" % (RESULTS_PATH, passed, len(CHECKS)))
    sys.exit(0 if result["all_pass"] else 1)


if __name__ == "__main__":
    main()
