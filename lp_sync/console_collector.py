# -*- coding: utf-8 -*-
"""console_collector.py — 编排控制台采集代理（I-143，方案草案第二节采集层）。

职责：以五秒周期只读会话库（C:\\Users\\Anni\\.zcode\\cli\\db\\db.sqlite），
按方案第三节数据字典口径计算本工作区活跃会话的指标，经既有代理令牌
上行云端 /api/console/ingest。只读不写会话库；写动作只走宿主服务指令口
（干预属第二期 I-147）。

口径（方案第三节冻结，逐格可溯；I-227 修订状态真值口径）：
  会话上下文占用百分比 = 该会话最近一次已完成模型请求的输入令牌数
                         除以 console.context.token_cap（参数登记库在册，
                         机主 2026-10-09 确认一百万）；
  会话运行状态真值 = 四源对账（I-227）：宿主会话归属、内核运行记录、
                     模型请求、工具活动；工具在途不判空闲，长工具
                     超过九十秒仍在干活；已停止须有停止确认；来源
                     缺失与采集失联判未知并附原因。判定见
                     session_truth.py；
  会话对应队伍与事项 = dispatch_registry.json 三元组（宿主服务派发
                       登记，历史由 backfill 脚本回填）与工位注册表、
                       运行记录合并；
  并行数量 = 活跃会话数，上限 console.parallel.session_cap（在册）。

窗口常量：活跃窗口三十分钟口径不变（最近一次模型请求或会话更新在
此窗口内才按窗口口径纳入；窗口外会话仅在途工具或活跃运行在册时
纳入，依据随行携带；历史会话不进快照表，归档抽屉数据属三期切换
事件）。

失败语义：快照是瞬时状态，最新一遍覆盖旧值，断网不入断网队列，
下一周期重推；连续失败在时间线留痕。
出处指针：每行快照携带四字段指针，指向会话库最新 completed 行。
仅使用 Python 标准库。
"""
import datetime
import json
import os
import sqlite3
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_sysreg = os.path.join(ROOT, "items_registry")
if _sysreg not in sys.path:
    sys.path.insert(0, _sysreg)
_lp_sync = os.path.join(ROOT, "lp_sync")
if _lp_sync not in sys.path:
    sys.path.insert(0, _lp_sync)

import cloud_client  # noqa: E402
import session_truth  # noqa: E402  I-227 状态对账四源模块

SESSION_DB = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite")
REGISTRY_PATH = os.path.join(ROOT, "lp_sync", "dispatch_registry.json")
TIMELINE_PATH = os.path.join(ROOT, "logs", "console_collector_timeline.jsonl")
WORKSPACES = ("G:\\Playground", "G:\\Playground\\isaac_flapper_sim")
ACTIVE_WINDOW_S = 1800                     # 活跃窗口三十分钟（口径不变）
CYCLE_S = 5                                # 五秒周期（I-143 判据一口径）


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _param_value(key, default):
    """参数登记库只读：控制台上限两键在册（机主 2026-10-09 批准）。

    须经 params_registry 模块读，不抄写数值；登记库不可用时用默认值
    兜底并在摘录里声明，禁止静默造数。"""
    try:
        import registry
        return registry.get(key)
    except Exception:
        return default


def _load_triples():
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _archived_rows():
    """归档清单（I-149 切换的老会话），随每轮上行保持云端归档态。"""
    st_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "handoff_state.json")
    try:
        st = json.load(open(st_path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for a in st.get("archived", []):
        sid = a.get("session_id") or ""
        out.append({
            "session_id": sid,
            "lane": (_load_triples().get(sid) or {}).get("lane"),
            "item_id": (_load_triples().get(sid) or {}).get("item_id"),
            "status": "已归档",
            "context_pct": None,
            "last_activity_at": a.get("archived_at"),
            "archived": True,
            "archive_reason": a.get("archive_reason"),
            "source": {"kind": "文件", "locus": "lp_sync/handoff_state.json#" + sid[:13],
                       "excerpt": str(a.get("archive_reason"))[:200],
                       "sampled_at": _now_iso()},
        })
    return out


def collect_once():
    """采集一遍，返回 ingest 包体（不发送）。供验收脚本直读对账。

    I-227：状态真值改由 session_truth 四源对账判定（宿主会话归属、
    运行记录、模型请求、工具活动）。长工具在途不判空闲；已停止须有
    停止确认；来源缺失与采集失联判未知并附原因。每行携带会话号、
    工位号、事项号、运行号、最近工具活动、采样时刻、来源与缺失
    原因（工位号、运行号与最近工具活动随 source 结构上行，云端
    console_sessions.source 列整包保存）。旧九十秒与三十分钟窗口
    口径不变：窗口外会话仅在途工具或活跃运行在册时纳入。"""
    cap = int(_param_value("console.context.token_cap", 1000000))
    body, meta = session_truth.collect_body(token_cap=cap)
    body["parallel_limit_default"] = int(
        _param_value("console.parallel.session_cap", 15))
    archived = _archived_rows()
    archived_ids = {a.get("session_id") for a in archived}
    body["sessions"] = [r for r in body["sessions"]
                        if r.get("session_id") not in archived_ids] + archived
    body["_truth_meta"] = meta          # 仅本地验收与时间线用，云端忽略
    return body


def push_once():
    """采集并上行一遍。返回 (包体, 云端应答)。供周期线程与验收共用。
    _truth_meta 为本地对账元数据，上行前剥离，云端包不携带。"""
    body = collect_once()
    body.pop("_truth_meta", None)
    resp = cloud_client.request("POST", "/api/console/ingest", body)
    return body, resp


def _timeline(record):
    """时间线留痕（判据一无断档的查验证据）。只增不改。"""
    try:
        with open(TIMELINE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── I-147 干预执行通道：动作任务取走 → 宿主服务执行 → 结果回填 ────
HOST_PORT = int(os.environ.get("LP_HOST_PORT", "58771"))


def _host_call(op, **kw):
    """本机宿主服务指令口调用（只本机回环）。"""
    import socket
    s = socket.create_connection(("127.0.0.1", HOST_PORT), timeout=180)
    s.sendall((json.dumps({"op": op, **kw}) + "\n").encode())
    buf = ""
    while "\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk.decode("utf-8", "replace")
    s.close()
    return json.loads(buf.strip().split("\n")[0])


def _execute_action(task):
    """执行一条动作任务。返回 (ok, 摘录)。"""
    payload = json.loads(task.get("locus") or "{}")
    op = payload.get("op")
    sid = task.get("session_id") or ""
    if op == "inject":
        message = payload.get("message") or "（指挥部注入，无正文）"
        r = _host_call("dispatch_resume", sid=sid, message=message, verify=True)
        if r.get("error"):
            return False, "注入失败：%s" % r["error"]
        ok = bool(r.get("readback_verified"))
        return ok, ("注入经宿主服务恢复后发送，回读核验=%s，accepted=%s"
                    % (r.get("readback_verified"), r.get("accepted")))
    if op == "stop":
        # 停止经宿主服务终止驱动回路（I-126 定论：杀驱动进程=有效通道；
        # 看护线程随后自愈拉起协议子进程，不影响账本与登记）。
        r = _host_call("kill_child")
        ok = bool(r.get("killed"))
        return ok, ("停止经宿主服务终止驱动回路，killed=%s，自愈拉起由看护线程负责"
                    % r.get("killed"))
    return False, "未知动作：%s" % op


def _readonly_lookup(task):
    """I-148 只读下查执行器：三类只读查询（全程不写会话库）。

    transcript：读会话库转录文本截最近片段；session_row：读会话行
    标题与时间；file：读本机文件（白名单限仓库与 lp_sync 目录）。
    返回 (成功?, 摘录)。"""
    kind = task.get("query_kind")
    locus = (task.get("locus") or "").strip()
    sid = task.get("session_id")
    if not os.path.exists(SESSION_DB):
        return False, "会话库不存在"
    con = sqlite3.connect("file:%s?mode=ro" % SESSION_DB.replace(chr(92), "/"),
                          uri=True, timeout=8.0)
    try:
        if kind == "transcript":
            like = "%" + sid + "%"
            rows = con.execute(
                "SELECT p.data FROM part p WHERE p.session_id LIKE ? "
                "ORDER BY p.id DESC LIMIT 8", (like,)).fetchall()
            texts = []
            for (d,) in rows:
                try:
                    j = json.loads(d)
                    if j.get("type") == "text" and j.get("text"):
                        texts.append(str(j["text"])[:120])
                except Exception:
                    continue
            if not texts:
                return False, "未找到该会话可读转录"
            sep = " | ".join(texts)
            return True, "转录末段（倒序）：" + sep
        if kind == "session_row":
            row = con.execute(
                "SELECT title, directory FROM session WHERE id LIKE ?",
                ("%" + (sid or "") + "%",)).fetchone()
            if not row:
                return False, "未找到该会话行"
            return True, "会话行：%s（目录 %s）" % (row[0], row[1])
        if kind == "file":
            # 相对路径按仓库根解析（ROOT=lp_sync 上级），代理工作目录无关
            real = os.path.realpath(locus if os.path.isabs(locus)
                                    else os.path.join(ROOT, locus))
            roots = [os.path.realpath(ROOT),
                     os.path.realpath(os.path.join(ROOT, "lp_sync"))]
            if not any(real.startswith(r) for r in roots):
                return False, "路径越界：只许仓库与 lp_sync 目录"
            if not os.path.isfile(real):
                return False, "文件不存在：" + locus
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(400)
            sep = chr(10)
            return True, "文件头部：" + head.replace(sep, " <br> ")[:360]
        return False, "未知只读查询类别：%s" % kind
    finally:
        con.close()


def _run_action_tasks(log):
    """取走待办任务并分类处理（I-148 扩展）：action 类经宿主服务执行；
    只读三类（transcript、session_row、file）本机只读查询回摘录；
    未知类别照单回执 failed 不静默。全部经 tasks/result 回填。"""
    try:
        r = cloud_client.request("GET", "/api/console/tasks?claim=1")
    except Exception as exc:
        log("下查任务拉取失败：%r" % exc)
        return
    for task in r if isinstance(r, list) else []:
        kind = task.get("query_kind")
        try:
            if kind == "action":
                ok, excerpt = _execute_action(task)
            elif kind in ("transcript", "session_row", "file"):
                ok, excerpt = _readonly_lookup(task)
            else:
                ok, excerpt = False, "未知任务类别：%s" % kind
        except Exception as exc:
            ok, excerpt = False, repr(exc)[:300]
        try:
            cloud_client.request("POST", "/api/console/tasks/result", {
                "task_id": task.get("task_id"), "ok": ok, "excerpt": excerpt})
        except Exception as exc:
            log("任务结果回填失败：%r" % exc)
        log("下查任务 #%s（%s）：%s" % (task.get("task_id"), kind, excerpt[:80]))




def _cycle(log):
    body = None
    try:
        body = collect_once()
        meta = body.pop("_truth_meta", None) or {}
        resp = cloud_client.request("POST", "/api/console/ingest", body)
        _timeline({"ts": _now_iso(), "ingested": resp.get("ingested"),
                   "sessions": len(body["sessions"]), "error": None,
                   "source_errors": meta.get("source_errors") or None,
                   "collection_error": meta.get("collection_error")})
    except Exception as exc:
        _timeline({"ts": _now_iso(), "ingested": None, "sessions": None,
                   "error": repr(exc)[:200]})
        log("采集上行失败：%r" % exc)
    _run_action_tasks(log)
    # I-149：守门评估（总开关在云端配置，off 或不可达时零动作）
    try:
        import handoff_gate
        gate_r = handoff_gate.evaluate(body, log) if body is not None else {}
        if gate_r.get("actions"):
            _timeline({"ts": _now_iso(), "gate": gate_r["actions"], "error": None})
    except Exception as exc:
        log("守门评估异常：%r" % exc)


def start_collector_thread(log):
    """同步代理进程内启动采集线程（方案第二节：扩展 sync_agent 为采集代理）。"""
    def runner():
        log("控制台采集线程启动（周期 %d 秒，窗口 %d 秒）" % (CYCLE_S, ACTIVE_WINDOW_S))
        while True:
            _cycle(log)
            time.sleep(CYCLE_S)
    t = threading.Thread(target=runner, daemon=True, name="console-collector")
    t.start()
    return t
