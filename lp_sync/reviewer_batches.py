"""I-206 bounded reviewer batches with durable attempts and per-event receipts.

Codex packets are prepared for an authorized sender; this module never claims
that creating a packet sent it. ZCode uses the existing durable dispatch bridge.
"""
import datetime
import hashlib
import json
import os
import time


def write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return None


def poll(nroot, resolve_address, outbox, actor="luna", send=False,
         window_s=10, max_events=25, clock=time.time):
    window_s=max(0,min(300,float(window_s)))
    max_events=max(1,min(100,int(max_events)))
    for folder in ("pending", "sending", "delivered", "acked", "batches"):
        os.makedirs(os.path.join(nroot, folder), exist_ok=True)
    # Shared across GUI and background consumers; live owners are never stolen.
    from host_service import _lock_holder_alive
    lock_path = os.path.join(nroot, ".batch.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        owner = read(lock_path) or {}
        if owner.get("pid") and _lock_holder_alive(owner["pid"]) is False:
            os.remove(lock_path)
        return {"busy": True}
    os.write(fd, json.dumps({"pid": os.getpid()}).encode())
    try:
        result = {"batches_prepared": 0, "events_prepared": 0, "delivered": 0, "held": 0}
        existing = {}
        for name in os.listdir(os.path.join(nroot, "batches")):
            if name.endswith(".json"):
                batch = read(os.path.join(nroot, "batches", name))
                if batch:
                    existing.update({eid: batch["batch_id"] for eid in batch["event_ids"]})
        grouped = {}
        for name in sorted(os.listdir(os.path.join(nroot, "pending"))):
            if not name.endswith(".json") or name.endswith((".reason.json", ".meta.json")):
                continue
            event = read(os.path.join(nroot, "pending", name))
            if not event:
                continue
            eid = event.get("event_id")
            if not eid or eid in existing or any(os.path.exists(os.path.join(nroot, sub, eid + suffix))
                    for sub, suffix in (("sending", ".delivery.json"), ("delivered", ".delivery.json"), ("acked", ".ack.json"))):
                continue
            address, reason = resolve_address(event.get("reviewer"))
            if address is None or not address.get("verified_at"):
                write(os.path.join(nroot, "pending", eid + ".reason.json"),
                      {"reason": reason or "收件地址尚未核实", "at": clock()})
                result["held"] += 1
                continue
            grouped.setdefault(event["reviewer"], []).append((event, address))
        for reviewer, rows in grouped.items():
            rows.sort(key=lambda row: row[0].get("created_at") or "")
            urgent_rows = [row for row in rows if row[0].get("category") in ("blocked", "execution_failed", "uncertain")]
            if urgent_rows:
                rows = urgent_rows[:1]
            first = rows[0][0]
            try:
                age = clock() - datetime.datetime.fromisoformat(first["created_at"].replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError):
                age = clock() - os.path.getmtime(os.path.join(nroot, "pending", first["event_id"] + ".json"))
            urgent = first.get("category") in ("blocked", "execution_failed", "uncertain")
            if not urgent and age < window_s and len(rows) < max_events:
                continue
            rows = rows[:1 if urgent else max_events]
            events, address = [r[0] for r in rows], rows[0][1]
            ids = [e["event_id"] for e in events]
            bid = "batch-" + hashlib.sha256((reviewer + "|" + "|".join(ids)).encode()).hexdigest()[:32]
            message = "【编排集合通知】批次 %s；审核人 %s；共 %d 个事件。逐项处理，送达与审核分别记录。\n" % (bid, reviewer, len(events))
            for event in events:
                message += "事件 %s｜事项 %s｜运行 %s｜类型 %s｜证据 %s｜请求 %s\n" % tuple(
                        event.get(k) for k in ("event_id", "item_id", "run_id", "category", "evidence_ptr", "action_needed"))
            package = os.path.join(outbox, reviewer, bid + ".json")
            batch = {"batch_id": bid, "reviewer": reviewer, "event_ids": ids,
                     "events": events, "address": address, "state": "prepared",
                     "created_at": clock(), "prepared_by": actor, "package": package}
            batch["message"] = message
            batch["send_attempt"] = None
            write(os.path.join(nroot, "batches", bid + ".json"), batch)
            write(package, {"batch_id": bid, "event_ids": ids, "to": address,
                            "message": message, "prepared_by": actor, "receipt": None,
                            "batch_record": os.path.join(nroot, "batches", bid + ".json"),
                            "send_attempt": None,
                            "method": "mcp__codex_app__send_message_to_thread" if address.get("platform") == "codex" else "file_bridge"})
            result["batches_prepared"] += 1;result["events_prepared"] += len(events)
        # Replay incomplete preparation without changing membership or send identity.
        for name in sorted(os.listdir(os.path.join(nroot, "batches"))):
            if not name.endswith(".json"):
                continue
            path = os.path.join(nroot, "batches", name)
            batch = read(path)
            if batch.get("state") == "delivered":
                _project_delivered_events(nroot, batch, clock)
                continue
            package = read(batch["package"])
            if not package and batch.get("state") == "prepared" and not batch.get("send_attempt"):
                package = {"batch_id": batch["batch_id"], "event_ids": batch["event_ids"],
                           "to": batch["address"], "message": batch["message"],
                           "receipt": None, "send_attempt": None, "batch_record": path}
                write(batch["package"], package)
            if not package:
                batch["pending_reason"] = "发送包缺失，保留批次等待核验，不重组或重发"
                write(path, batch);continue
            address, reason = resolve_address(batch["reviewer"])
            if address != batch["address"]:
                batch["pending_reason"] = reason or "当前地址与准备批次不一致，保留待核验"
                write(path, batch);continue
            for event in batch["events"]:
                eid = event["event_id"]
                record = {"event_id": eid, "batch_id": batch["batch_id"], "reviewer": batch["reviewer"],
                          "state": "pending_receipt" if batch.get("send_attempt") else "prepared", "address": address, "notification": event,
                          "package": batch["package"]}
                write(os.path.join(nroot, "sending", eid + ".delivery.json"), record)
                pending = os.path.join(nroot, "pending", eid + ".json")
                if os.path.exists(pending):os.remove(pending)
            receipt = package.get("receipt")
            if send and address.get("platform") == "zcode":
                if not _authorized(package, package.get("authorization")):
                    batch["pending_reason"]="缺少机主明确的本批次发送授权"
                    write(path,batch)
                    result["held"]+=1
                    continue
                import dispatch_file_queue as dq
                rid = "ntf-" + batch["batch_id"]
                done = read(os.path.join(dq.QUEUE_ROOT, "done", rid + ".result.json"))
                if done and (done.get("result") or {}).get("readback_verified"):
                    receipt = {"threadId": address["thread_id"], "request_id": rid,
                               "readback_verified": True, "via": "file_bridge"}
                elif not package.get("send_attempt"):
                    package["send_attempt"] = {"request_id": rid, "at": clock()}
                    batch["send_attempt"] = package["send_attempt"]
                    batch["state"] = "submitted"
                    write(path, batch)
                    write(batch["package"], package)
                    dq.write_request({"schema_version": 1, "request_id": rid, "op": "dispatch_resume",
                                      "sid": address["thread_id"], "message": package["message"]})
            if receipt and isinstance(receipt, dict) and receipt.get("threadId") == address["thread_id"]:
                batch["state"] = "delivered";batch["receipt"] = receipt;batch["delivered_at"] = clock()
                write(path, batch)
                result["delivered"] += _project_delivered_events(nroot, batch, clock)
        return result
    finally:
        os.close(fd)
        os.remove(lock_path)


def _project_delivered_events(nroot, batch, clock):
    """Replay receipt projections after a crash; never perform another send."""
    receipt = batch.get("receipt") or {}
    if receipt.get("threadId") != batch["address"]["thread_id"]:
        raise ValueError("批次送达回执地址不符")
    count = 0
    for event in batch["events"]:
        eid = event["event_id"]
        if read(os.path.join(nroot, "acked", eid + ".ack.json")):
            continue
        delivered = os.path.join(nroot, "delivered", eid + ".delivery.json")
        if not read(delivered):
            record = {"event_id": eid, "batch_id": batch["batch_id"], "reviewer": batch["reviewer"],
                      "address": batch["address"], "notification": event, "package": batch["package"],
                      "state": "delivered", "receipt": receipt,
                      "delivered_at": batch.get("delivered_at") or clock()}
            write(delivered, record)
            count += 1
        sending = os.path.join(nroot, "sending", eid + ".delivery.json")
        if os.path.exists(sending):
            os.remove(sending)
    return count


def ack(nroot, event_id, actor):
    delivery = read(os.path.join(nroot, "delivered", event_id + ".delivery.json"))
    if not delivery or not delivery.get("receipt"):
        raise ValueError("事件尚无送达回执，不能确认")
    if delivery.get("reviewer") != actor:
        raise ValueError("只有指定审核人可确认该事件")
    write(os.path.join(nroot, "acked", event_id + ".ack.json"),
          {"event_id": event_id, "batch_id": delivery.get("batch_id"),
           "acked_by": actor, "acked_at": datetime.datetime.now().isoformat()})
    return True


def _authorized(package, authorization):
    return isinstance(authorization,dict) and authorization.get("owner_approved") is True and bool(authorization.get("source")) and authorization.get("recipient")==package["to"]["thread_id"] and authorization.get("batch_id")==package["batch_id"]


def prepare_send(package_path, actor, authorization=None, resolve_address=None):
    """Call immediately before one authorized real tool send; unknown attempts hold."""
    package = read(package_path)
    if not package or package.get("receipt") or package.get("send_attempt"):
        raise ValueError("包已送达、已尝试或不可读；先对账，不重发")
    if not _authorized(package,authorization):
        raise ValueError("缺少机主明确的本批次发送授权")
    if resolve_address is None:
        from luna_consumer import resolve_address
    address, reason=resolve_address(read(package["batch_record"])["reviewer"])
    if address!=package["to"] or not address.get("verified_at"):
        raise ValueError(reason or "当前通讯录地址与原包不一致，保留待核验")
    batch = read(package["batch_record"])
    if batch.get("send_attempt"):
        raise ValueError("批次已有发送尝试，先对账")
    attempt = {"sender": actor, "at": datetime.datetime.now().isoformat(),
               "thread_id": package["to"]["thread_id"]}
    batch["send_attempt"] = attempt
    batch["state"] = "pending_receipt"
    write(package["batch_record"], batch)
    package["send_attempt"] = attempt
    package["authorization"] = authorization
    write(package_path, package)
    return package


def record_receipt(package_path, receipt):
    package = read(package_path)
    if not package or not package.get("send_attempt"):
        raise ValueError("没有已登记的发送尝试")
    if not isinstance(receipt, dict) or receipt.get("threadId") != package["to"]["thread_id"]:
        raise ValueError("回执与发送目标不一致")
    package["receipt"] = receipt
    write(package_path, package)
