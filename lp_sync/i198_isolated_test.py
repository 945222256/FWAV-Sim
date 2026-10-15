# -*- coding: utf-8 -*-
"""i198_isolated_test.py — I-198 隔离样例检查（判据一至判据四）。

隔离件：临时目录复制云端代码与登记库、临时通讯录副本、内核通知目录、
假通知事件。不碰生产云端、生产通讯录与生产通知目录。判据五（真实向
Beth 授权地址投递一次）不在本脚本范围，单独执行并留回执。
仅使用 Python 标准库。
"""
import json
import os
import shutil
import sys
import tempfile
import types
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lp_sync"))

WORK = tempfile.mkdtemp(prefix="i198_isolated_")
CODE = os.path.join(WORK, "code")
REG = os.path.join(WORK, "registry")
CONTACTS = os.path.join(WORK, "contacts.json")
NROOT = os.path.join(WORK, "notifications")

_checks = []


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail), flush=True)


def ctx(body):
    return types.SimpleNamespace(body=body, headers={}, cookie=lambda k: None)


def main():
    # ── 服务端隔离：reviewer 登记与保留（判据一、二） ──
    shutil.copytree(os.path.join(ROOT, "cloud_board"), CODE,
                    ignore=shutil.ignore_patterns("__pycache__", "data",
                                                  "server", "deploy",
                                                  "static", "app"))
    os.makedirs(os.path.join(CODE, "data"), exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "items_registry"), REG,
                    ignore=shutil.ignore_patterns("__pycache__", "*.tmp",
                                                  ".ledger.lock"))
    os.environ["LP_ITEMS_REGISTRY"] = REG
    os.environ["FLAPPER_LEDGER_MODE"] = "local"
    sys.path.insert(0, CODE)
    sys.path.insert(0, REG)
    import db
    db.settings.DATA_DIR = os.path.join(CODE, "data")
    db.settings.DB_PATH = os.path.join(CODE, "data", "ledger.db")
    db.init_schema()
    import auth
    auth.caller = lambda c: ("隔离测试", ["L3"])
    import repo
    import api_ledger          # noqa: F401
    import api_dispatch        # noqa: F401
    import api_intake          # noqa: F401

    add = api_ledger.add(ctx({"title": "隔离样例 reviewer 登记甲",
                              "owner": "L3队", "close_criterion": "隔离判据",
                              "kind": "task", "goal": "G-002",
                              "reviewer": "beth"}))
    iid = add["result"]["id"]
    it = repo.get_item(iid)
    check("登记带 reviewer 入库", it.get("reviewer") == "beth", repr(it.get("reviewer")))

    # 后续写操作全链保留（判据二）
    api_ledger.attach(ctx({"item_id": iid, "evidence": ["隔离证据"]}))
    api_ledger.set_depends(ctx({"item_id": iid, "depends_on": [],
                                "author": "隔离测试"}))
    api_dispatch.assign(ctx({"item_id": iid, "lane": "L3", "owner": "L3队"}))
    api_dispatch.claim(ctx({"item_id": iid, "worker": "L3队", "lease_hours": 1}))
    api_dispatch.release(ctx({"item_id": iid, "worker": "L3队"}))
    it = repo.get_item(iid)
    check("五种后续写后 reviewer 保留", it.get("reviewer") == "beth")
    check("负责人与执行会话字段独立（claimed 已释放而 reviewer 在）",
          it.get("claimed_by") is None and it.get("reviewer") == "beth")

    # 旧事项缺失值明确表达（判据二）
    add_old = api_ledger.add(ctx({"title": "隔离旧式事项（无 reviewer）",
                                  "owner": "L3队",
                                  "close_criterion": "隔离判据"}))
    it_old = repo.get_item(add_old["result"]["id"])
    check("旧式登记 reviewer 为空且明确", it_old.get("reviewer") is None)

    # 待审入册链路带 reviewer（判据一）
    import intake as reg_intake
    q_before = reg_intake._load(reg_intake.QUEUE_PATH, {"queue": []})
    reg_intake._save = reg_intake._save if hasattr(reg_intake, "_save") else None
    # 直接构造待审条目走 decide（绕过本地队列文件依赖）
    entry = {"id": "Q-901", "title": "隔离待审 reviewer 样例",
             "goal_context": "I-198 隔离验证", "close_criterion": "隔离判据",
             "submitted_by": "隔离测试", "submitted_at": "2026-10-12",
             "note": "", "depends_on": [], "review": "pending",
             "criteria": ["隔离判据一"],
             "overrides": [], "reviewer": "beth",
             "lint": {"verdict": "pass", "suggested_lane": "L3",
                      "suggested_depends_on": [], "rules": []}}
    with db.transaction() as conn:
        repo.put_inbox(conn, entry)
    dec = api_intake.decide(ctx({"entry_id": "Q-901", "action": "approve",
                                 "lane": "L3"}))
    new_item = repo.get_item(dec["result"]["item"])
    check("待审入册 reviewer 随事项保存", new_item.get("reviewer") == "beth")

    # ── 客户端校验（判据一）：缺失与未知 reviewer 拒绝 ──
    # 用隔离通讯录副本（消费者解析也用它）
    members = {"beth": {"name": "Beth", "role": "经理", "tag": "manager",
                        "onboarding_file": None,
                        "contact": {"platform": "zcode", "host_id": "local",
                                    "thread_id": "sess_isolated_beth",
                                    "verified_at": "2026-10-12T00:00:00+00:00",
                                    "source": {"method": "样例", "basis": "隔离"}}},
               "emptyaddr": {"name": "空址样例", "role": "样例", "tag": "manager",
                             "onboarding_file": None,
                             "contact": {"platform": "zcode", "host_id": "local",
                                         "thread_id": None,
                                         "verified_at": "2026-10-12T00:00:00+00:00",
                                         "source": {"method": "样例",
                                                    "basis": "隔离留空"}}}}
    json.dump({"schema_version": 1, "members": members},
              open(CONTACTS, "w", encoding="utf-8"), ensure_ascii=False)
    import luna_consumer as lc
    lc.CONTACTS = CONTACTS

    sys.path.insert(0, REG)
    import importlib
    # validate_reviewer 走真实通讯录路径时被替换为隔离副本路径
    reg_intake._contacts_members = lambda: set(members.keys())
    _, err_missing = reg_intake.validate_reviewer(None)
    check("缺失 reviewer 被拒", bool(err_missing), err_missing[:60])
    _, err_unknown = reg_intake.validate_reviewer("nobody")
    check("未知 reviewer 被拒", "未登记" in err_unknown, err_unknown[:60])
    ok_val, err_ok = reg_intake.validate_reviewer("beth")
    check("已登记 reviewer 通过", ok_val == "beth" and not err_ok)

    # ── 内核通知样例（判据三）与消费者（判据四） ──
    for sub in ("pending", "taken", "delivered", "acked"):
        os.makedirs(os.path.join(NROOT, sub), exist_ok=True)

    def make_event(ev, reviewer="beth", category="closed_pass"):
        json.dump({"event_id": ev, "item_id": "I-901", "run_id": "run-x-a1",
                   "reviewer": reviewer, "category": category,
                   "evidence_ptr": "ledger#x", "action_needed": "审核",
                   "created_at": "2026-10-12T00:00:00"}, 
                  open(os.path.join(NROOT, "pending", ev + ".json"), "w",
                       encoding="utf-8"), ensure_ascii=False)

    make_event("ev-r1")
    r = lc.poll_once(NROOT)
    check("正常事件领取并投递（记录含全部判据三字段）",
          r["claimed"] == 1 and r["delivered"] == 1)
    d = json.load(open(os.path.join(NROOT, "delivered", "ev-r1.delivery.json"),
                       encoding="utf-8"))
    fields_ok = all(d["notification"].get(k) is not None
                    for k in ("item_id", "run_id", "category",
                              "action_needed", "evidence_ptr")) \
        and d["reviewer"] == "beth" and d["address"]["thread_id"] == "sess_isolated_beth"
    check("通知含唯一事件号与运行号并按现行地址路由", fields_ok)

    # 重复事件与重复轮询（判据三、四）：同号事件重投不二发
    make_event("ev-r1")
    r2 = lc.poll_once(NROOT)
    check("同事件重复投递零次", r2["delivered"] == 0 and r2["skipped"] >= 1,
          json.dumps(r2))

    # 空地址：保留待处理并写原因（判据四）
    make_event("ev-r2", reviewer="emptyaddr")
    r3 = lc.poll_once(NROOT)
    reason = json.load(open(os.path.join(NROOT, "pending", "ev-r2.reason.json"),
                            encoding="utf-8"))
    check("空地址保留待处理并显示原因",
          r3["held"] == 1 and "地址为空" in reason.get("reason", ""))

    # 未知 reviewer：同样保留（注意上一轮 held 的事件仍在 pending，
    # 本轮会再次尝试并被继续保留，故用事件级断言而非轮次计数）
    make_event("ev-r3", reviewer="ghost")
    r4 = lc.poll_once(NROOT)
    reason3 = json.load(open(os.path.join(NROOT, "pending", "ev-r3.reason.json"), encoding="utf-8"))
    check("未知审核人保留待处理并显示原因",
          os.path.exists(os.path.join(NROOT, "pending", "ev-r3.json"))
          and reason3 and "不在现行通讯录" in reason3.get("reason", ""))

    # 恢复样例（判据四）：过期领取回到待处理后可再投
    make_event("ev-r4")
    lc.poll_once(NROOT)      # ev-r4 正常投递了——换一个专测恢复的事件
    make_event("ev-r5")
    # 手工构造过期 taken（模拟消费者领取后崩溃）
    os.replace(os.path.join(NROOT, "pending", "ev-r5.json"),
               os.path.join(NROOT, "taken", "ev-r5.json"))
    json.dump({"consumer": "luna", "taken_at": "2026-10-12T00:00:00",
               "lease_until": "2026-10-12T00:01:00"},
              open(os.path.join(NROOT, "taken", "ev-r5.meta.json"), "w",
                   encoding="utf-8"))
    back = lc.recover_expired(NROOT, now=datetime.datetime(2026, 10, 12, 12, 0))
    r5 = lc.poll_once(NROOT)
    check("过期领取恢复后可再投递", back >= 1 and r5["delivered"] >= 1)

    # 确认记录（判据四）
    lc.ack(NROOT, "ev-r1", "beth")
    check("确认记录可查", os.path.exists(os.path.join(NROOT, "acked",
                                                       "ev-r1.ack.json")))

    bad = sum(1 for c in _checks if not c["ok"])
    out = os.path.join(ROOT, "logs", "i198_isolated_test_20261012.json")
    json.dump({"checks": _checks,
               "summary": {"total": len(_checks),
                           "passed": len(_checks) - bad},
               "isolated_root": WORK},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("隔离样例总判定：%s（%d/%d 过）"
          % ("全部通过" if bad == 0 else "存在不过项", len(_checks) - bad,
             len(_checks)))
    print("报告：%s" % os.path.relpath(out, ROOT))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
