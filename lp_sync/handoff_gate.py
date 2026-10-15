# -*- coding: utf-8 -*-
"""handoff_gate.py — 控制台上下文自动切换守门（I-149，方案草案第八节）。

一期为确定性规则，不依赖 Eko。挂在采集代理五秒周期上，逐周期评估
活跃会话；触发六步顺序写死：
  1. 只读会话库提取该会话摘要与在办事项状态，写交接卡到 logs/handoff/；
  2. 经宿主服务 dispatch_new 建新会话（简报含交接卡路径与事项号）；
  3. 回读核验新会话激活；未激活则中止，老会话不动，记失败事件；
  4. 老会话入归档清单（采集代理随后上行归档态），I-208 修正为
     session/stop 定向停用该会话驱动回合（不可用则记待处理原因，
     不做全局子进程终止）；会话本体与转录不动；
  5. 写台账事件（触发占比、新老会话号、交接卡路径、执行者）；
  6. 云端状态随下一轮上行推进，观察窗播切换动画。
防护五条：单写者（切换后老会话即归档不再驱动）；每线每日上限三次
超限停机待命并告警；机主总开关在云端配置（auto、confirm、off）；
失败回退不留半态（新会话未核验即中止，老会话不动）；触发线冻结
七成，改动须指挥部决定留痕。

冷却与限额状态落在 lp_sync/handoff_state.json（簿记元数据，可审计）。
仅使用 Python 标准库。
"""
import datetime
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sysreg = os.path.join(ROOT, "items_registry")
if sysreg not in sys.path:
    sys.path.insert(0, sysreg)
_lpsync = os.path.dirname(os.path.abspath(__file__))
if _lpsync not in sys.path:
    sys.path.insert(0, _lpsync)

GATE_PCT = 70                    # 触发线（D-045 体系方案第八节冻结）
COOLDOWN_S = 600                 # 十分钟冷却（机主批准值）
DAILY_LIMIT = 3                  # 每线每日上限（机主批准值）
SESSION_DB = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite")
STATE_PATH = os.path.join(_lpsync, "handoff_state.json")
CARD_DIR = os.path.join(ROOT, "logs", "handoff")


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"last_switch": {}, "daily": {}}


def _save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def _item_status(item_id):
    """在办状态只读本地总账（采集代理同机，账本是权威镜像之一）。"""
    try:
        items = json.load(open(os.path.join(sysreg, "items.json"),
                               encoding="utf-8"))["items"]
        return next((i["status"] for i in items if i["id"] == item_id), None)
    except (OSError, ValueError, KeyError):
        return None


def _current_in_progress_for_lane(lane, session_id=None):
    """I-208/I-217：按云端权威核实该会话的在办事项。

    多工位同线在办（判据六）时不再要求全线唯一：先按认领会话精确匹配
    本会话认领的事项；无认领会话信息时回退全线唯一判定。采集行携带的
    item_id 是历史派发映射，过期时按认领修正。返回 (item_id 或 None,
    可核查原因)。"""
    try:
        import cloud_client
        snap = cloud_client.request("GET", "/api/agent/snapshot")
        items = snap.get("items", [])
        cand = [i for i in items
                if i.get("lane") == lane and i.get("status") == "in_progress"]

        def claimed_sid_of(item):
            sid = item.get("claim_session_id")
            if not sid:
                import re
                events = [e for e in snap.get("events", [])
                          if e.get("item_id") == item["id"] and e.get("action") == "claim"]
                if events:
                    match = re.search(r"会话\s+([^，\s]+)", str(events[-1].get("detail", "")))
                    sid = match.group(1) if match else None
            return sid
        if session_id:
            mine = [i for i in cand if claimed_sid_of(i) == session_id]
            if len(mine) == 1:
                return mine[0]["id"], "按认领会话定向唯一在办 %s" % mine[0]["id"]
            if len(mine) > 1:
                return None, ("同会话认领 %d 个事项，无法唯一定向（%s）"
                              % (len(mine), [i["id"] for i in mine]))
            if cand:
                return None, ("路线 %s 有 %d 个进行中事项但无一由本会话认领，"
                              "守门不切换其他工位的事项"
                              % (lane, len(cand)))
        if len(cand) == 1:
            return cand[0]["id"], "核实唯一在办 %s" % cand[0]["id"]
        if not cand:
            return None, "路线 %s 云端无进行中事项" % lane
        return None, ("路线 %s 有 %d 个进行中事项，无法唯一定向（%s）"
                      % (lane, len(cand), [i["id"] for i in cand]))
    except Exception as e:
        return None, "云端核实失败：%r" % e


def _gate_reason_file():
    """守门跳过/失败原因的持久可查通道（不只黄色徽标）。"""
    return os.path.join(_lpsync, "handoff_gate_reasons.json")


def _record_gate_reason(session_id, kind, detail):
    """追加式守门原因台账（采集代理周期调用方与人工都可查）。"""
    path = _gate_reason_file()
    try:
        obj = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        obj = {"schema_version": 1, "events": []}
    obj["events"].append({"ts": _now_iso(), "session": session_id,
                          "kind": kind, "detail": detail[:300]})
    obj["events"] = obj["events"][-500:]            # 有界保留
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _session_digest(session_id, max_turns=8):
    """只读提取会话最近文本轮（交接卡素材）。"""
    if not os.path.exists(SESSION_DB):
        return []
    con = sqlite3.connect("file:" + SESSION_DB.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select p.data from part p join message m on m.id=p.message_id "
            "where p.session_id=? order by m.time_created desc limit 40",
            (session_id,)).fetchall()
    finally:
        con.close()
    texts = []
    for (data,) in rows:
        try:
            j = json.loads(data)
        except ValueError:
            continue
        if j.get("type") == "text" and j.get("text"):
            texts.append((j["text"] or "").strip())
        if len(texts) >= max_turns:
            break
    return list(reversed(texts))


def write_handoff_card(session_id, item_id, pct):
    """六步之一：交接卡落盘，返回卡路径。"""
    os.makedirs(CARD_DIR, exist_ok=True)
    title = None
    criteria = None
    try:
        items = json.load(open(os.path.join(sysreg, "items.json"),
                               encoding="utf-8"))["items"]
        it = next((i for i in items if i["id"] == item_id), None)
        if it:
            title = it["title"]
            criteria = it.get("close_criterion")
    except (OSError, ValueError, KeyError):
        pass
    turns = _session_digest(session_id)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(CARD_DIR, "handoff_%s_%s.md" % (session_id[:13], stamp))
    structured = {"schema_version": 1, "session_id": session_id,
                  "item_id": item_id, "created_at": _now_iso(),
                  "goal": title, "acceptance": criteria,
                  "evidence_source": "local_cloud_mirror",
                  "recorded_evidence": (it or {}).get("evidence", []) if 'it' in locals() else [],
                  "remaining_checks": [c for c in ((it or {}).get("criteria") or [])
                                       if not c.get("verified")] if 'it' in locals() else [],
                  "last_texts": turns,
                  "next_action": "核对交付与断点，只补未完成步骤，禁止重复认领或重复执行"}
    try:
        from host_service import EXECUTION_STORE
        structured["execution"] = EXECUTION_STORE.session(session_id)
        kernel_path = os.path.join(ROOT, "lp_sync", "kernel_state", "state.json")
        with open(kernel_path, encoding="utf-8") as stream:
            state = json.load(stream)
        structured["kernel_tasks"] = [t for t in state.get("tasks", {}).values()
                                       if t.get("item_id") == item_id]
        checkpoint = os.path.join(ROOT, "lp_sync", "checkpoints", session_id + ".json")
        if os.path.isfile(checkpoint):
            with open(checkpoint, encoding="utf-8") as stream:
                structured["worker_checkpoint"] = json.load(stream)
    except (OSError, ValueError):
        structured["execution_lookup"] = "unavailable; keep recorded source limitations"
    with open(path + ".json", "w", encoding="utf-8") as stream:
        json.dump(structured, stream, ensure_ascii=False, indent=1)
    lines = [
        "# 交接卡：会话 %s" % session_id,
        "",
        "- 触发时刻：%s（上下文 %.1f%% 达到守门线 %d%%）" % (_now_iso(), pct, GATE_PCT),
        "- 在办事项：%s %s" % (item_id, title or ""),
        "- 事项判据：%s" % (criteria or "见总账"),
        "- 老会话转录保留：会话库只读可查，本卡不替代转录",
        "- 结构化交接：%s.json（含运行关联、交付证据、未验收项、断点与下一步）" % path,
        "",
        "## 最近对话摘录（老会话最后文本轮，新到旧排列）",
        "",
    ]
    for i, t in enumerate(reversed(turns), 1):
        lines.append("### 摘录 %d" % i)
        lines.append(t[:600])
        lines.append("")
    lines += ["## 接力指令", "",
              "先读本卡与在办事项判据，从最近摘录的进度继续施工，",
              "不要重新盘点，不要重复已完成步骤；完成或受阻都向指挥部留痕。", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _cloud_event(action, session_id, reason):
    import cloud_client
    try:
        cloud_client.request("POST", "/api/console/action", {
            "action": action, "session_id": session_id, "reason": reason[:400]})
    except Exception:
        pass


def _stop_session_targeted(sid):
    """I-208：对目标会话做定向停用，不做全局子进程终止。

    优先协议 session/stop（参数 sessionId，只影响该会话的驱动回合）；
    不可用或失败时返回明确待处理原因，绝不回退到 kill_child。"""
    import socket as _sock
    try:
        s = _sock.create_connection(("127.0.0.1", 58771), timeout=10)
        payload = json.dumps({"op": "session_stop", "sid": sid}) + "\n"
        s.sendall(payload.encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            buf += s.recv(65536)
        s.close()
        return json.loads(buf.decode("utf-8"))
    except Exception as e:
        return {"ok": False, "pending_reason":
                "定向停用通道不可用（%r）；老会话保持不动，等人工或下一"
                "轮再停，不做全局终止" % e}


def _pending_reason_from_response(resp):
    """把宿主响应归一为可核查的待处理原因（未知 op 等明确入库）。"""
    if resp.get("ok"):
        return resp
    err = resp.get("error") or resp.get("pending_reason") or ""
    if "未知 op" in err:
        return {"ok": False,
                "pending_reason": "宿主未实现 session_stop（%s）——生产"
                                 "版本不含该口；切换继续但老会话保持不动，"
                                 "等宿主更新后下一轮再停，不做全局终止" % err}
    return {"ok": False, "pending_reason": err or "未知失败"}


def attempt_switch(session, item_id, lane, log):
    """Persist one handoff and let the singleton host stop, create and rebind."""
    import hashlib
    sid = session["session_id"]
    st = _load_state()
    pending = st.setdefault("pending_handoffs", {})
    plan = pending.get(sid)
    if plan is None:
        card = write_handoff_card(sid, item_id, session.get("context_pct") or 0)
        plan = {"item_id": item_id, "lane": lane, "card": card,
                "request_id": "hg-" + hashlib.sha256((sid + item_id).encode()).hexdigest()[:36]}
        pending[sid] = plan
        _save_state(st)
    from console_collector import _host_call
    briefing = ("你是本路线接力会话。原会话已定向停止。在办事项 %s。"
                "先读结构化交接卡 %s，核对交付与断点，只补未完成工作。"
                % (item_id, plan["card"]))
    response = _host_call("handoff", lane=lane, old_sid=sid, item_id=item_id,
                         request_id=plan["request_id"],
                         title="自动接续：%s" % item_id, message=briefing)
    if not response.get("switched"):
        reason = response.get("reason") or response.get("error") or "交接回执待核验"
        _record_gate_reason(sid, "pending", str(reason))
        log("交接未完成：%s" % reason)
        return {"switched": False, "reason": reason}
    st = _load_state()
    archived = st.setdefault("archived", [])
    if not any(v["session_id"] == sid for v in archived):
        archived.append({"session_id": sid, "successor": response["new"],
                         "archived_at": _now_iso(), "card": plan["card"],
                         "archive_reason": "已取得定向停止和新会话回读回执"})
    now_ts = datetime.datetime.now().timestamp()
    st.setdefault("last_switch", {})[sid] = now_ts
    st["last_switch"][lane] = now_ts
    st.setdefault("pending_handoffs", {}).pop(sid, None)
    _save_state(st)
    _cloud_event("archive", sid, "自动接续已核验，新会话 %s，交接卡 %s"
                 % (response["new"], plan["card"]))
    return {"switched": True, "old": sid, "new": response["new"], "card": plan["card"]}


def evaluate(sessions, log):
    """逐周期评估入口（采集代理每轮调用）。

    sessions 为 collect_once 的包体；返回本轮动作摘要供时间线留痕。"""
    import cloud_client
    try:
        with open(os.path.join(ROOT, "lp_sync", "dispatch_queue", "control.json"), encoding="utf-8") as stream:
            if json.load(stream).get("mode") == "halt":
                return {"mode": "maintenance", "actions": []}
    except FileNotFoundError:
        pass
    # 总开关（云端配置）
    threshold = GATE_PCT
    try:
        cfg = cloud_client.request("GET", "/api/console/config")
        mode = cfg.get("handoff_mode", "auto")
        threshold = max(1, min(99, float(cfg.get("gate_pct", GATE_PCT))))
    except Exception:
        mode = None                           # 云端不可达：不做任何切换
    if mode != "auto":
        # 总开关非 auto：越线会话照常识别但不动作，留证据行（总开关
        # 实测生效的判据面）。
        for s in sessions.get("sessions", []):
            pct = s.get("context_pct")
            if pct is not None and pct >= threshold and s.get("item_id"):
                log("守门总开关为 %s：会话 %s 占用 %.1f%% 越线，不动作"
                    % (mode, s["session_id"][:13], pct))
        return {"mode": mode or "unreachable", "switched": None}

    st = _load_state()
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().timestamp()
    actions = []
    for s in sessions.get("sessions", []):
        pct = s.get("context_pct")
        stale_item = s.get("item_id")       # 采集映射的历史事项（仅参考）
        lane = s.get("lane")
        if pct is None or pct < threshold or not lane:
            continue
        # I-208 修正：以云端当前唯一在办为准，采集旧 item_id 过期时
        # 自动换新（不再因旧映射非进行中而空跳），原因可查。
        item_id, why = _current_in_progress_for_lane(lane, s["session_id"])
        if not item_id:
            log("守门跳过 %s：%s" % (s["session_id"][:13], why))
            _record_gate_reason(s["session_id"], "skip", why)
            continue
        if stale_item and stale_item != item_id:
            log("守门定向修正 %s：采集映射 %s 过期，按云端在办 %s 接力"
                % (s["session_id"][:13], stale_item, item_id))
        if any(a["session_id"] == s["session_id"] for a in st.get("archived", [])):
            continue                          # 已归档不重复触发
        if _item_status(item_id) != "in_progress":
            reason = "本地镜像 %s 非进行中（以云端为准继续）" % item_id
            log("守门注意 %s：%s" % (s["session_id"][:13], reason))
        key = today + "|" + lane
        used = st["daily"].get(key, 0)
        if used >= DAILY_LIMIT:
            log("守门停机待命：%s 本日已切换 %d 次，达到上限" % (lane, used))
            _cloud_event("note", s["session_id"],
                         "切换守门停机待命：%s 本日已达上限 %d 次" % (lane, used))
            actions.append({"sid": s["session_id"][:13], "refused": "daily_limit"})
            continue
        # I-217：冷却按会话（工位）计——一个工位交接不影响其他工位
        # 连续工作；每日上限仍按线计（保守安全边界）。
        last = st["last_switch"].get(s["session_id"]) or st["last_switch"].get(lane)
        if last and now - last < COOLDOWN_S:
            log("守门冷却跳过：%s 距上次切换 %.0f 秒" % (s["session_id"][:13], now - last))
            actions.append({"sid": s["session_id"][:13], "refused": "cooldown"})
            continue
        # 触发六步
        r = attempt_switch(s, item_id, lane, log)
        if r.get("switched"):
            st = _load_state()
            st["daily"][key] = used + 1
            _save_state(st)
        actions.append({"sid": s["session_id"][:13], "switched": r["switched"],
                        "new": r.get("new", "")[:13], "reason": r.get("reason")})
        break                                 # 单写者：一轮只切一单
    return {"mode": mode, "actions": actions}
