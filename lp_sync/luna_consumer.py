# -*- coding: utf-8 -*-
"""luna_consumer.py — I-198 编排通知消费者（轮询、领取、确认、路由）。

给轮询编排器结果的岗位（现任 Luna）使用：零网络纯文件操作，从内核
通知目录轮询待审核事件，按 reviewer 从现行通讯录解析收件地址，产出
投递记录；地址为空或失效时事件保留待处理并写明原因，不猜发给其他人。

目录协议（通知根 <state_root>/notifications/）：
  pending/   待处理事件（<event_id>.json；<event_id>.reason.json 为最近
             一次不可投递原因）
  taken/     已领取（<event_id>.json + .meta.json：消费者、领取时间、
             租约到期；租约过期自动回到 pending，可恢复）
  delivered/ 已投递（<event_id>.delivery.json：收件地址、投递时间、
             通知内容摘要）
  acked/     已确认（<event_id>.ack.json：确认人与时间）

防重复：delivered/ 已存在同号投递记录即跳过；重复轮询同一事件不会产
生第二份投递。未知状态（taken 但租约未过期且无投递记录）不重领。
用法：
  python luna_consumer.py --state-root lp_sync/kernel_state [--once]
  python luna_consumer.py ... --send   # 经文件桥向 zcode 会话地址真实投递
仅使用 Python 标准库。
"""
import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
CONTACTS = os.path.join(_ROOT, "organization", "contacts.json")
# 投递包输出根（隔离测试可注入临时目录；生产默认 lp_sync/notification_outbox）。
NOTIFICATION_OUTBOX = os.path.join(_HERE, "notification_outbox")
LEASE_S = 300.0


def _ts():
    return datetime.datetime.now().isoformat(timespec="milliseconds")


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resolve_address(reviewer):
    """按稳定人员标识从现行通讯录解析当前核实地址。

    返回 (地址字典 或 None, 原因)。地址保留 platform、host_id、
    thread_id 与 verified_at；人员不存在、地址为空或未经核实都返回
    原因。"""
    contacts = _load(CONTACTS) or {}
    member = (contacts.get("members") or {}).get(reviewer)
    if member is None:
        return None, "reviewer %r 不在现行通讯录" % reviewer
    contact = member.get("contact") or {}
    if not contact.get("thread_id"):
        return None, "reviewer %r 当前地址为空（未知地址不猜发）" % reviewer
    return {"platform": contact.get("platform"),
            "host_id": contact.get("host_id"),
            "thread_id": contact["thread_id"],
            "verified_at": contact.get("verified_at")}, None


def recover_expired(nroot, now=None):
    """租约过期的领取回到待处理（可恢复语义）。"""
    now = now or datetime.datetime.now()
    taken = os.path.join(nroot, "taken")
    back = 0
    for name in list(os.listdir(taken)) if os.path.isdir(taken) else []:
        if not name.endswith(".meta.json"):
            continue
        meta = _load(os.path.join(taken, name))
        if not meta:
            continue
        try:
            lease = datetime.datetime.fromisoformat(meta.get("lease_until", ""))
        except ValueError:
            lease = None
        if lease and lease < now:
            ev = name[:-len(".meta.json")]
            for suffix in (".json", ".meta.json"):
                src = os.path.join(taken, ev + suffix)
                if os.path.exists(src):
                    os.replace(src, os.path.join(nroot, "pending", ev + suffix))
            back += 1
    return back


def poll_once(nroot, consumer="luna", send=False):
    """一轮：恢复过期领取 → 领取 pending → 解析地址 → 投递或留原因。"""
    config = _load(os.path.join(nroot, "batch_config.json")) or {}
    if config.get("enabled"):
        import reviewer_batches
        result = reviewer_batches.poll(nroot, resolve_address, NOTIFICATION_OUTBOX,
                 actor=consumer, send=send, window_s=max(0, min(300, float(config.get("window_s", 10)))),
                 max_events=max(1, min(100, int(config.get("max_events", 25)))))
        result["legacy_reconciled"] = reconcile(nroot, send=False)
        return result
    for sub in ("pending", "taken", "delivered", "acked"):
        os.makedirs(os.path.join(nroot, sub), exist_ok=True)
    recover_expired(nroot)
    results = {"recovered": 0, "claimed": 0, "delivered": 0,
               "held": 0, "skipped": 0}
    now = datetime.datetime.now()
    pending = os.path.join(nroot, "pending")
    for name in sorted(os.listdir(pending)):
        if not name.endswith(".json") or name.endswith((".meta.json",
                                                        ".reason.json")):
            continue
        ev = name[:-5]
        # 防重复：已投递或已确认的同号事件跳过。
        if os.path.exists(os.path.join(nroot, "delivered", ev + ".delivery.json")) \
                or os.path.exists(os.path.join(nroot, "acked", ev + ".ack.json")):
            results["skipped"] += 1
            continue
        event = _load(os.path.join(pending, name))
        if not event:
            continue
        # 领取（原子改名）。
        os.replace(os.path.join(pending, name),
                   os.path.join(nroot, "taken", name))
        _atomic_write(os.path.join(nroot, "taken", ev + ".meta.json"),
                      {"consumer": consumer, "taken_at": _ts(),
                       "lease_until": (now + datetime.timedelta(
                           seconds=LEASE_S)).isoformat(timespec="seconds")})
        results["claimed"] += 1
        # 解析地址。
        reviewer = event.get("reviewer")
        address = None
        if not reviewer:
            reason = "事件缺 reviewer（旧链路事件），保留待处理"
        else:
            address, reason = resolve_address(reviewer)
        if address is None:
            # 保留待处理并写明原因；不猜发其他人。
            os.replace(os.path.join(nroot, "taken", name),
                       os.path.join(pending, name))
            meta = os.path.join(nroot, "taken", ev + ".meta.json")
            if os.path.exists(meta):
                os.remove(meta)
            _atomic_write(os.path.join(pending, ev + ".reason.json"),
                          {"reason": reason, "at": _ts()})
            results["held"] += 1
            continue
        # 投递状态机（I-198 修正）：prepared/submitted/pending_receipt
        # 与 delivered 分离，只有有效通道回执才落定送达；无回执不猜造、
        # 不盲重发（未知结果先对账）。
        os.makedirs(os.path.join(nroot, "sending"), exist_ok=True)
        dpath = os.path.join(nroot, "sending", ev + ".delivery.json")
        address_rec = {"platform": address.get("platform"),
                       "host_id": address.get("host_id"),
                       "thread_id": address.get("thread_id"),
                       "verified_at": address.get("verified_at")}
        if send and address.get("platform") == "codex":
            # codex：真实发送须由持有 codex 工具的环境执行（先例见
            # Anni通知方法回读.json）。产出一次一用待发送包，状态
            # prepared；发送方执行后回填 receipt，本消费者对账后落定。
            odir = os.path.join(NOTIFICATION_OUTBOX, str(reviewer))
            os.makedirs(odir, exist_ok=True)
            pkg_path = os.path.join(odir, ev + ".json")
            if not os.path.exists(pkg_path):
                _atomic_write(pkg_path, {
                    "event_id": ev, "to": address_rec,
                    "method": "mcp__codex_app__send_message_to_thread",
                    "message": ("【编排审核通知】事件 %s；事项 %s；运行 %s；"
                                "结果类型 %s；审核人 %s；需要的动作：%s；"
                                "证据位置：%s" %
                                (event.get("event_id"), event.get("item_id"),
                                 event.get("run_id"), event.get("category"),
                                 reviewer, event.get("action_needed"),
                                 event.get("evidence_ptr"))),
                    "prepared_at": _ts(), "prepared_by": consumer,
                    "receipt": None})
            _atomic_write(dpath, {"event_id": ev, "reviewer": reviewer,
                                  "address": address_rec, "state": "prepared",
                                  "package": (os.path.relpath(pkg_path, _ROOT)
                                              if os.path.splitdrive(pkg_path)[0]
                                              == os.path.splitdrive(_ROOT)[0]
                                              else pkg_path),
                                  "notification": {k: event.get(k) for k in
                                                   ("item_id", "run_id",
                                                    "category",
                                                    "action_needed",
                                                    "evidence_ptr")},
                                  "updated_at": _ts()})
            results["prepared"] = results.get("prepared", 0) + 1
        elif send and address.get("platform") == "zcode":
            sys.path.insert(0, _HERE)
            import dispatch_file_queue as dq
            rid = "ntf-%s" % ev
            bstate, _binfo = _bridge_lookup(dq, rid)
            if bstate == "absent":
                dq.write_request({"schema_version": 1, "request_id": rid,
                                  "op": "dispatch_resume",
                                  "sid": address_rec["thread_id"],
                                  "kind": "task",
                                  "message": ("【编排审核通知】事件 %s；事项 %s；"
                                              "运行 %s；结果类型 %s；审核人 %s；"
                                              "需要的动作：%s；证据位置：%s" %
                                              (event.get("event_id"),
                                               event.get("item_id"),
                                               event.get("run_id"),
                                               event.get("category"),
                                               reviewer,
                                               event.get("action_needed"),
                                               event.get("evidence_ptr")))})
            # 已在途或已完成：不重发，交给本轮统一对账。
            _atomic_write(dpath, {"event_id": ev, "reviewer": reviewer,
                                  "address": address_rec, "state": "submitted",
                                  "bridge_request": rid,
                                  "notification": {k: event.get(k) for k in
                                                   ("item_id", "run_id",
                                                    "category",
                                                    "action_needed",
                                                    "evidence_ptr")},
                                  "updated_at": _ts()})
            results["submitted"] = results.get("submitted", 0) + 1
        else:
            # 未开 --send：只登记待投递状态，不落 delivered。
            _atomic_write(dpath, {"event_id": ev, "reviewer": reviewer,
                                  "address": address_rec, "state": "prepared",
                                  "notification": {k: event.get(k) for k in
                                                   ("item_id", "run_id",
                                                    "category",
                                                    "action_needed",
                                                    "evidence_ptr")},
                                  "updated_at": _ts()})
            results["prepared"] = results.get("prepared", 0) + 1
        src = os.path.join(nroot, "taken", name)
        if os.path.exists(src):
            os.remove(src)
        meta = os.path.join(nroot, "taken", ev + ".meta.json")
        if os.path.exists(meta):
            os.remove(meta)
    # 本轮统一对账在途投递（含历史遗留）。
    results["reconciled"] = reconcile(nroot, send=send)
    return results


def _bridge_lookup(dq, request_id):
    """查桥上一次投递请求的状态（对账用，不重发）。"""
    qroot = dq.QUEUE_ROOT
    done = os.path.join(qroot, "done", request_id + ".result.json")
    if os.path.exists(done):
        try:
            return "completed", json.load(open(done, encoding="utf-8"))
        except (OSError, ValueError):
            return "completed", None
    for e in dq.ledger_events(qroot, request_id=request_id):
        if e.get("event") == "completed":
            return "completed", e
        if e.get("event") == "failed_final":
            return "failed_final", e
    for sub in ("incoming", "processing", "retry", "uncertain"):
        if os.path.exists(os.path.join(qroot, sub, request_id + ".json")):
            return "in_flight", {"where": sub}
    return "absent", None


def reconcile(nroot, send=False):
    """对账在途投递：有有效通道回执才落定 delivered，否则保持待回执。

    返回本轮落定送达数。zcode：桥 completed 且回读核验为真即有效；
    failed_final 或在途保持 pending_receipt 并写原因，不重发。codex：
    发送方在投递包回填 receipt（真实工具应答，threadId 匹配）即有效。"""
    sending = os.path.join(nroot, "sending")
    delivered_dir = os.path.join(nroot, "delivered")
    os.makedirs(delivered_dir, exist_ok=True)
    count = 0
    if not os.path.isdir(sending):
        return 0
    for name in list(os.listdir(sending)):
        if not name.endswith(".delivery.json"):
            continue
        path = os.path.join(sending, name)
        rec = _load(path)
        if not rec:
            continue
        if rec.get("batch_id"):
            continue
        ev = rec.get("event_id", name[:-len(".delivery.json")])
        platform = (rec.get("address") or {}).get("platform")
        if platform == "codex":
            # 升级路径：无 send 轮产出的 prepared 记录无 package；带 send
            # 的轮次在此补建一次一用发送包（事件幂等：包已存在不重建）。
            if send and not rec.get("package"):
                reviewer_id = rec.get("reviewer")
                odir = os.path.join(NOTIFICATION_OUTBOX,
                                    str(reviewer_id))
                os.makedirs(odir, exist_ok=True)
                pkg_new = os.path.join(odir, ev + ".json")
                if not os.path.exists(pkg_new):
                    note = rec.get("notification") or {}
                    _atomic_write(pkg_new, {
                        "event_id": ev, "to": rec.get("address"),
                        "method": "mcp__codex_app__send_message_to_thread",
                        "message": ("【编排审核通知】事件 %s；事项 %s；运行 %s；"
                                    "结果类型 %s；审核人 %s；需要的动作：%s；"
                                    "证据位置：%s" %
                                    (ev, note.get("item_id"),
                                     note.get("run_id"),
                                     note.get("category"), reviewer_id,
                                     note.get("action_needed"),
                                     note.get("evidence_ptr"))),
                        "prepared_at": _ts(), "prepared_by": "luna-upgrade",
                        "receipt": None})
                rec["package"] = (os.path.relpath(pkg_new, _ROOT)
                                  if os.path.splitdrive(pkg_new)[0] ==
                                  os.path.splitdrive(_ROOT)[0]
                                  else pkg_new)
                _atomic_write(path, rec)
            pkg_path = rec.get("package")
            if not pkg_path:
                full = None
            elif os.path.isabs(pkg_path):
                full = pkg_path
            else:
                full = os.path.join(_ROOT, pkg_path)
            receipt = (_load(full) or {}).get("receipt") if full else None
            if receipt and isinstance(receipt, dict) and receipt.get(
                    "threadId") == (rec.get("address") or {}).get("thread_id"):
                rec["state"] = "delivered"
                rec["receipt"] = receipt
                rec["delivered_at"] = _ts()
                _atomic_write(os.path.join(delivered_dir, name), rec)
                os.remove(path)
                count += 1
            else:
                rec["state"] = "pending_receipt" if pkg_path else rec["state"]
                rec["pending_reason"] = ("等待发送方执行一次真实发送并回填 "
                                         "receipt（包：%s）" % pkg_path)
                _atomic_write(path, rec)
        elif platform == "zcode":
            sys.path.insert(0, _HERE)
            import dispatch_file_queue as dq
            rid = rec.get("bridge_request") or ("ntf-%s" % ev)
            state, info = _bridge_lookup(dq, rid)
            if state == "completed":
                result = (info or {}).get("result") if isinstance(info, dict) else None
                detail = (info or {}).get("detail") if isinstance(info, dict) else None
                verified = (result or {}).get("readback_verified") \
                    if isinstance(result, dict) else (detail or {}).get(
                        "readback_verified") if isinstance(detail, dict) else None
                if verified:
                    rec["state"] = "delivered"
                    rec["receipt"] = {"via": "file_bridge_dispatch_resume",
                                      "request_id": rid,
                                      "readback_verified": True,
                                      "at": _ts()}
                    rec["delivered_at"] = _ts()
                    _atomic_write(os.path.join(delivered_dir, name), rec)
                    os.remove(path)
                    count += 1
                else:
                    rec["state"] = "pending_receipt"
                    rec["pending_reason"] = "桥已完成但回读核验未确认为真"
                    _atomic_write(path, rec)
            elif state == "in_flight":
                rec["state"] = "pending_receipt"
                _atomic_write(path, rec)
            elif state == "failed_final":
                rec["state"] = "pending_receipt"
                rec["pending_reason"] = "桥定性失败，待人工处置，不盲重发"
                _atomic_write(path, rec)
            # absent：状态机不应出现（submitted 必有请求），保持原状待查。
    return count



def ack(nroot, event_id, actor):
    """确认通知（人读后的确认记录；已投递是投递事实，确认是阅读事实）。"""
    delivered = _load(os.path.join(nroot, "delivered", event_id + ".delivery.json"))
    if delivered and delivered.get("batch_id"):
        import reviewer_batches
        return reviewer_batches.ack(nroot, event_id, actor)
    if not delivered or not delivered.get("receipt") or delivered.get("reviewer") != actor:
        raise ValueError("只允许指定审核人确认已有送达回执的事件")
    _atomic_write(os.path.join(nroot, "acked", event_id + ".ack.json"),
                  {"event_id": event_id, "acked_by": actor, "acked_at": _ts()})
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-root", default=os.path.join(_HERE, "kernel_state"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--ack", help="确认指定事件号")
    ap.add_argument("--actor", default="luna")
    args = ap.parse_args()
    nroot = os.path.join(args.state_root, "notifications")
    if args.ack:
        ack(nroot, args.ack, args.actor)
        print("已确认：%s" % args.ack)
        return 0
    if args.once:
        print(json.dumps(poll_once(nroot, args.actor, args.send),
                         ensure_ascii=False))
        return 0
    import time
    while True:
        print(json.dumps(poll_once(nroot, args.actor, args.send),
                         ensure_ascii=False), flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
