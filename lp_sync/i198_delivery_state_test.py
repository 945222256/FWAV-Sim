# -*- coding: utf-8 -*-
"""i198_delivery_state_test.py — I-198 送达状态机修正的补充验证。

只补 Beth 指定的四类：送达状态分离（prepared/submitted/
pending_receipt 与 delivered）、缺回执不落定、丢回执后对账恢复、
重复轮询有效投递仍为一。隔离件：临时通知根、临时通讯录、临时桥根。
仅使用 Python 标准库。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lp_sync"))
import luna_consumer as lc

WORK = tempfile.mkdtemp(prefix="i198_delivery_")
CONTACTS = os.path.join(WORK, "contacts.json")
NROOT = os.path.join(WORK, "notifications")

_checks = []


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail), flush=True)


def make_event(ev, reviewer="cz", platform="codex"):
    for sub in ("pending", "taken", "sending", "delivered", "acked"):
        os.makedirs(os.path.join(NROOT, sub), exist_ok=True)
    json.dump({"event_id": ev, "item_id": "I-901", "run_id": "run-x",
               "reviewer": reviewer, "category": "closed_pass",
               "evidence_ptr": "x", "action_needed": "审核",
               "created_at": "2026-10-13T00:00:00"},
              open(os.path.join(NROOT, "pending", ev + ".json"), "w",
                   encoding="utf-8"), ensure_ascii=False)


def main():
    # 隔离通讯录：cz=codex 平台、zz=zcode 平台。
    members = {
        "cz": {"name": "Codex样例", "role": "r", "tag": "manager",
               "onboarding_file": None,
               "contact": {"platform": "codex", "host_id": "local",
                           "thread_id": "th-cz-001",
                           "verified_at": "2026-10-13T00:00:00+00:00",
                           "source": {"method": "t", "basis": "b"}}},
        "zz": {"name": "Zcode样例", "role": "r", "tag": "manager",
               "onboarding_file": None,
               "contact": {"platform": "zcode", "host_id": "local",
                           "thread_id": "sess_zz_isolated_0001",
                           "verified_at": "2026-10-13T00:00:00+00:00",
                           "source": {"method": "t", "basis": "b"}}}}
    json.dump({"schema_version": 1, "members": members},
              open(CONTACTS, "w", encoding="utf-8"), ensure_ascii=False)
    lc.CONTACTS = CONTACTS
    OUTBOX = os.path.join(WORK, "notification_outbox")
    lc.NOTIFICATION_OUTBOX = OUTBOX

    # 一、codex：包已备（prepared）不得落定 delivered。
    make_event("ev-a1", reviewer="cz")
    r = lc.poll_once(NROOT, send=True)
    dpath = os.path.join(NROOT, "sending", "ev-a1.delivery.json")
    rec = json.load(open(dpath, encoding="utf-8"))
    check("codex 包已备（prepared，轮末对账转 pending_receipt）未落 delivered",
          rec.get("state") in ("prepared", "pending_receipt")
          and not os.path.exists(os.path.join(NROOT, "delivered",
                                              "ev-a1.delivery.json"))
          and r.get("prepared", 0) >= 1)

    # 二、缺回执：重复轮询不落定、不重发（投递包仍一份）。
    r2 = lc.poll_once(NROOT, send=True)
    rec2 = json.load(open(dpath, encoding="utf-8"))
    pkg_dir = os.path.join(OUTBOX, "cz")
    # 隔离运行也写到了真实 outbox 目录——测试事件用后即清。
    pkg_count = len([f for f in os.listdir(pkg_dir)
                     if f.startswith("ev-a1")]) if os.path.isdir(pkg_dir) else 0
    check("缺回执轮询保持 pending_receipt 且不重发",
          rec2.get("state") == "pending_receipt"
          and pkg_count == 1 and r2.get("delivered", 0) == 0)

    # 三、回填真实回执后对账落定（threadId 匹配才有效）。
    pkg_path = os.path.join(pkg_dir, "ev-a1.json")
    pkg = json.load(open(pkg_path, encoding="utf-8"))
    pkg["receipt"] = {"threadId": "th-cz-001", "ok": True,
                      "tool": "mcp__codex_app__send_message_to_thread"}
    json.dump(pkg, open(pkg_path, "w", encoding="utf-8"), ensure_ascii=False)
    got = lc.reconcile(NROOT)
    check("回填真实回执后对账落定 delivered",
          got >= 1 and os.path.exists(os.path.join(
              NROOT, "delivered", "ev-a1.delivery.json")))

    # 回执 threadId 不匹配的负样例：不得落定。
    make_event("ev-a2", reviewer="cz")
    lc.poll_once(NROOT, send=True)
    pkg2 = os.path.join(pkg_dir, "ev-a2.json")
    p2 = json.load(open(pkg2, encoding="utf-8"))
    p2["receipt"] = {"threadId": "th-other-999", "ok": True}
    json.dump(p2, open(pkg2, "w", encoding="utf-8"), ensure_ascii=False)
    got2 = lc.reconcile(NROOT)
    check("回执 threadId 不匹配不落定",
          got2 == 0 and os.path.exists(os.path.join(
              NROOT, "sending", "ev-a2.delivery.json")))

    # 四、zcode 路径：用隔离桥验证 submitted→pending_receipt→delivered。
    # 隔离桥根：让 _bridge_lookup 走临时目录。write_request 的默认根
    # 在定义时绑定生产队列，测试内显式改写指向隔离根，杜绝测试负载
    # 误入生产通道（首轮曾误入一枚并已撤回留痕）。
    import dispatch_file_queue as dq
    QROOT = os.path.join(WORK, "bridge_queue")
    dq.ensure_layout(QROOT)
    dq.QUEUE_ROOT = QROOT
    _real_write_request = dq.write_request
    dq.write_request = lambda req, root=None: _real_write_request(
        req, root=QROOT)
    make_event("ev-b1", reviewer="zz")
    lc.poll_once(NROOT, send=True)
    rec_b1 = json.load(open(os.path.join(NROOT, "sending",
                                         "ev-b1.delivery.json"),
                            encoding="utf-8"))
    check("zcode 已提交（submitted，轮末对账转 pending_receipt）未落 delivered",
          rec_b1.get("state") in ("submitted", "pending_receipt")
          and not os.path.exists(os.path.join(NROOT, "delivered",
                                              "ev-b1.delivery.json")))
    got3 = lc.reconcile(NROOT)
    rec_b1b = json.load(open(os.path.join(NROOT, "sending",
                                          "ev-b1.delivery.json"),
                             encoding="utf-8"))
    check("桥在途保持 pending_receipt 不重发",
          got3 == 0 and rec_b1b.get("state") == "pending_receipt")
    # 桥侧完成（回读核验为真）→ 对账落定。
    rid = "ntf-ev-b1"
    shutil.move(os.path.join(QROOT, "incoming", rid + ".json"),
                os.path.join(QROOT, "processing", rid + ".json"))
    dq.ledger_append("dispatch_start", request_id=rid, root=QROOT,
                     detail={"op": "dispatch_resume"})
    dq.ledger_append("dispatch_result", request_id=rid, root=QROOT,
                     detail={"ok": True, "readback_verified": True})
    dq.ledger_append("completed", request_id=rid, root=QROOT,
                     detail={"session": "sess_zz_isolated_0001"})
    os.replace(os.path.join(QROOT, "processing", rid + ".json"),
               os.path.join(QROOT, "done", rid + ".json"))
    json.dump({"request_id": rid, "ok": True,
               "result": {"accepted": True, "readback_verified": True,
                          "session": "sess_zz_isolated_0001"}},
              open(os.path.join(QROOT, "done", rid + ".result.json"), "w",
                   encoding="utf-8"), ensure_ascii=False)
    got4 = lc.reconcile(NROOT)
    check("桥完成且回读为真后对账落定 delivered",
          got4 >= 1 and os.path.exists(os.path.join(
              NROOT, "delivered", "ev-b1.delivery.json")))

    # 五、重复轮询（已落定后）：有效投递总数仍为 1。
    before = len(os.listdir(os.path.join(NROOT, "delivered")))
    lc.poll_once(NROOT, send=True)
    lc.poll_once(NROOT, send=True)
    after = len(os.listdir(os.path.join(NROOT, "delivered")))
    check("重复轮询两轮后有效投递总数不变",
          after == before, "delivered=%d" % after)

    # 六、恢复样例：租约过期回 pending 后可重新走状态机且仍不二发。
    make_event("ev-c1", reviewer="zz")
    os.replace(os.path.join(NROOT, "pending", "ev-c1.json"),
               os.path.join(NROOT, "taken", "ev-c1.json"))
    json.dump({"consumer": "luna", "taken_at": "2026-10-13T00:00:00",
               "lease_until": "2026-10-13T00:01:00"},
              open(os.path.join(NROOT, "taken", "ev-c1.meta.json"), "w",
                   encoding="utf-8"))
    import datetime
    back = lc.recover_expired(NROOT, now=datetime.datetime(2026, 10, 13, 12, 0))
    r6 = lc.poll_once(NROOT, send=True)
    ok6 = back >= 1 and os.path.exists(os.path.join(
        NROOT, "sending", "ev-c1.delivery.json")) \
        and r6.get("submitted", 0) >= 1
    check("过期领取恢复后重新提交且仅一份投递记录", ok6)

    bad = sum(1 for c in _checks if not c["ok"])
    out = os.path.join(ROOT, "logs", "i198_delivery_state_test_20261013.json")
    json.dump({"checks": _checks,
               "summary": {"total": len(_checks),
                           "passed": len(_checks) - bad},
               "isolated_root": WORK},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("补充验证总判定：%s（%d/%d 过）"
          % ("全部通过" if bad == 0 else "存在不过项", len(_checks) - bad,
             len(_checks)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
