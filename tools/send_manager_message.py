# -*- coding: utf-8 -*-
"""send_manager_message.py — 按姓名发送经理消息（事项 I-194 返修版，L2 队）。

单一可调用工具：以收件人姓名或固定人员标识和正文为必填输入，每次调用
重新读取公共通讯录 organization/contacts.json（Elsa 维护，事项 I-179
交付）解析当前地址，调用真实发送通道 Codex CLI 的 queue 子命令投递，
向调用者返回简短结果与投递回执。

真实通道：`codex queue --thread <thread_id> --message <正文>`（本机
codex-cli 0.153.4 实测在位；经应用服务线程存储投递）。本工具不含任何
模拟、伪造或替身发送路径；默认执行即真实调用。未知地址不猜测、不做
会话列表搜索。

地址校验（I-194 返修，判据三）：platform 须为 codex；host_id 须为本通道
支持的宿主（当前仅 local——本机 Codex 应用；跨宿主地址明确报错，绝不
静默向错宿主发送）；thread_id 须为标准 UUID 格式，格式无效即失效地址
明确报错（HQ 隔离夹具 not-a-valid-thread-id 在案）。

请求身份与审计（I-194 返修）：每次发送意图生成或使用请求编号
request_id，记录调用身份（系统用户与主机）；真实发送必须携带显式
--authorization 授权依据文本——回执里的纪律说明不是授权证据，缺依据
在调用通道前拒绝；每次发送意图（含拒绝与失败）追加写入内部审计
logs/i194_send_audit.jsonl。同一 request_id 重复提交不重发，返回先前
结果（退出码四）；通道超时等结果不明状态按 result_unclear 记录，凭
request_id 对账后以新请求号重试。

纪律（organization/manager_operations.md）：跨会话发送消息须有机主对
对象和动作的明确授权；持有本工具不等于获得发送权。真实投递验收须按
可核实的对象与动作授权执行，本工具不自行追加收件人。

用法：
  python tools/send_manager_message.py <姓名或人员标识> <正文> --authorization <依据>
      [--request-id <编号>] [--json out.json] [--contacts PATH]
  python tools/send_manager_message.py <姓名或人员标识> <正文> --resolve-only
退出码：零为通道受理；一为解析、授权、投递失败或结果不明；二为用法
错误；四为同请求重复未重发。
"""
import datetime
import argparse
import getpass
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONTACTS = os.path.join(ROOT, "organization", "contacts.json")
AUDIT_PATH = os.path.join(ROOT, "logs", "i194_send_audit.jsonl")
TOOL_NAME = "send_manager_message"
SUPPORTED_HOSTS = ("local",)   # 本通道为本机 Codex 应用；跨宿主明确拒绝
_THREAD_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                        r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _caller_identity():
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    return "%s@%s" % (user, platform.node())


def _audit_append(entry):
    os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _audit_load():
    if not os.path.exists(AUDIT_PATH):
        return []
    out = []
    with open(AUDIT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def resolve(recipient, contacts_path):
    """从通讯录解析收件人地址并做地址有效性校验。返回成员条目与
    人员标识；失败抛 ValueError 并给明确原因（未知、冲突、平台不符、
    宿主不符、线程号格式无效、地址待核）。"""
    try:
        with open(contacts_path, "r", encoding="utf-8") as f:
            book = json.load(f)
    except Exception as exc:
        raise ValueError("通讯录读取失败：%s（%s）" % (contacts_path, exc))
    members = book.get("members") or {}
    if not isinstance(members, dict) or not members:
        raise ValueError("通讯录无成员表：%s" % contacts_path)
    key = recipient.strip()
    hit = None
    for pid, m in members.items():
        if pid.lower() == key.lower():
            hit = (pid, m)
            break
    if hit is None:
        names = [(pid, m) for pid, m in members.items()
                 if isinstance(m.get("name"), str)
                 and m["name"].strip().lower() == key.lower()]
        if len(names) > 1:
            raise ValueError("姓名冲突：通讯录中 %d 人同名 %r（%s），"
                             "请改用固定人员标识"
                             % (len(names), recipient,
                                "、".join(p for p, _ in names)))
        if names:
            hit = names[0]
    if hit is None:
        raise ValueError("未知收件人：%r 不在通讯录 %s 中（人员标识：%s）"
                         % (recipient, contacts_path,
                            "、".join(sorted(members))))
    pid, m = hit
    c = m.get("contact") or {}
    if c.get("platform") != "codex":
        raise ValueError("不支持的平台：%s 的联系平台为 %r，本工具只支持 "
                         "codex 通道" % (pid, c.get("platform")))
    host = c.get("host_id")
    if host not in SUPPORTED_HOSTS:
        raise ValueError("宿主不符：%s（%s）的 host_id 为 %r，本通道仅支持 "
                         "%s，不静默向其他宿主发送"
                         % (pid, m.get("name"), host,
                            "、".join(SUPPORTED_HOSTS)))
    tid = c.get("thread_id")
    if not isinstance(tid, str) or not tid.strip():
        raise ValueError("失效地址：%s（%s）的 thread_id 为空，须先由 Elsa "
                         "核实更新通讯录" % (pid, m.get("name")))
    if not _THREAD_RE.match(tid.strip()):
        raise ValueError("失效地址：%s（%s）的 thread_id %r 不是有效线程号"
                         "格式（须为 UUID），须先由 Elsa 核实更新通讯录"
                         % (pid, m.get("name"), tid))
    if not c.get("verified_at"):
        raise ValueError("地址待核：%s（%s）缺核实时间，须先由 Elsa 核实"
                         "更新通讯录" % (pid, m.get("name")))
    return m, pid


def send(recipient, body, contacts_path=DEFAULT_CONTACTS,
         resolve_only=False, json_out=None, authorization=None,
         request_id=None):
    """解析地址并投递。返回 (exit_code, receipt_dict)。

    真实发送必须携带 authorization（授权依据文本，可核实）；缺依据在
    调用通道前拒绝。request_id 缺省自动生成；与审计中既有编号重复时不
    重发，返回先前结果。结果不明（通道超时）按 result_unclear 记录。
    """
    t0 = time.perf_counter()
    rid = (request_id or
           "i194-%s-%s" % (datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
                           uuid.uuid4().hex[:8]))
    receipt = {
        "tool": TOOL_NAME,
        "generated_at": _now_iso(),
        "request_id": rid,
        "caller": _caller_identity(),
        "recipient_input": recipient,
        "body_length": len(body),
        "contacts": os.path.abspath(contacts_path),
        "authorization_basis": authorization if authorization else None,
        "authorization_rule": "真实发送须显式授权依据；本字段为空即拒绝"
                              "发送，回执与审计中的说明文字不构成授权证据。",
    }

    def finish(status, reason, code, extra=None):
        receipt.update({"status": status, "reason": reason,
                        "elapsed_ms": round((time.perf_counter() - t0) * 1e3,
                                            1)})
        if extra:
            receipt.update(extra)
        print("[SEND] %s：%s" % (status, reason))
        if json_out:
            with open(json_out, "w", encoding="utf-8") as f:
                json.dump(receipt, f, ensure_ascii=False, indent=1)
        return code, receipt

    try:
        m, pid = resolve(recipient, contacts_path)
    except ValueError as exc:
        if not resolve_only:
            _audit_append({"request_id": rid, "at": _now_iso(),
                           "caller": receipt["caller"],
                           "recipient": recipient, "person_id": None,
                           "authorization": authorization or None,
                           "status": "resolve_failed",
                           "reason": str(exc)})
        return finish("resolve_failed", str(exc), 1)
    tid = m["contact"]["thread_id"].strip()
    receipt.update({
        "person_id": pid,
        "person_name": m.get("name"),
        "host_id": m["contact"].get("host_id"),
        "thread_id": tid,
        "verified_at": m["contact"].get("verified_at"),
    })
    if resolve_only:
        return finish("resolved_only",
                      "仅解析地址（含格式与宿主校验），未发送、未产生投递"
                      "回执", 0)
    if not body.strip():
        _audit_append({"request_id": rid, "at": _now_iso(),
                       "caller": receipt["caller"], "recipient": recipient,
                       "person_id": pid, "authorization": authorization or None,
                       "status": "rejected_empty_body", "reason": "正文为空"})
        return finish("rejected", "正文为空", 2)
    if not authorization or not authorization.strip():
        _audit_append({"request_id": rid, "at": _now_iso(),
                       "caller": receipt["caller"], "recipient": recipient,
                       "person_id": pid, "authorization": None,
                       "status": "authorization_missing",
                       "reason": "缺显式授权依据，未调用通道"})
        return finish("authorization_missing",
                      "缺显式授权依据（--authorization），未调用通道；"
                      "回执说明不构成授权证据", 1)
    prior = [e for e in _audit_load() if e.get("request_id") == rid]
    if prior:
        p = prior[-1]
        _audit_append({"request_id": rid, "at": _now_iso(),
                       "caller": receipt["caller"], "recipient": recipient,
                       "person_id": pid,
                       "authorization": authorization,
                       "status": "duplicate_no_resend",
                       "prior_status": p.get("status"),
                       "reason": "同请求编号重复提交，未重发"})
        return finish("duplicate_no_resend",
                      "请求编号 %s 已有审计记录（先前状态 %s），未重发；"
                      "如需重试请用新请求编号"
                      % (rid, p.get("status")), 4,
                      {"prior_status": p.get("status")})
    cmd = ["codex", "queue", "--thread", tid, "--message", body]
    audit_base = {"request_id": rid, "at": _now_iso(),
                  "caller": receipt["caller"], "recipient": recipient,
                  "person_id": pid, "authorization": authorization,
                  "thread_id": tid, "channel": "codex queue"}
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        _audit_append(dict(audit_base, status="channel_missing",
                           reason="codex 不在 PATH"))
        return finish("channel_missing", "codex 可执行文件不在 PATH，"
                      "真实通道不可用", 1)
    except subprocess.TimeoutExpired:
        _audit_append(dict(audit_base, status="result_unclear",
                           reason="通道超过 120 秒未返回，投递结果不明；"
                                  "凭 request_id 对账"))
        return finish("result_unclear",
                      "通道超时，投递结果不明；凭请求编号 %s 对账后以新"
                      "请求号重试" % rid, 1)
    out = (cp.stdout or "").strip()
    err = (cp.stderr or "").strip()
    receipt.update({
        "channel": "codex queue",
        "channel_exit_code": cp.returncode,
        "channel_stdout": out[-800:],
        "channel_stderr": err[-800:],
    })
    if cp.returncode == 0:
        _audit_append(dict(audit_base, status="queued",
                           channel_exit_code=0))
        return finish("queued",
                      "通道受理（真实投递回执以目标会话收到为准；送达核对"
                      "须按授权另行执行）", 0)
    _audit_append(dict(audit_base, status="channel_rejected",
                       channel_exit_code=cp.returncode,
                       reason=(err or out)[:300]))
    return finish("channel_rejected",
                  "通道拒绝（退出码 %d）：%s" % (cp.returncode,
                                                 (err or out)[:200]), 1)


def _main(argv):
    parser = argparse.ArgumentParser(description="旧版按姓名发送入口；岗位标准联络优先使用 manager_comms MCP 工具")
    parser.add_argument("recipient")
    parser.add_argument("body")
    parser.add_argument("--contacts", default=DEFAULT_CONTACTS)
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--authorization")
    parser.add_argument("--request-id")
    args = parser.parse_args(argv)
    rc, _ = send(args.recipient, args.body, contacts_path=args.contacts,
                 resolve_only=args.resolve_only, json_out=args.json_out,
                 authorization=args.authorization, request_id=args.request_id)
    return rc


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
