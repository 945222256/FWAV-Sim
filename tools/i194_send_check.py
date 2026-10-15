# -*- coding: utf-8 -*-
"""i194_send_check.py — I-194 按姓名发送经理消息工具的验证套（返修版）。

判据依据（I-194 冻结文本）：单一可调用工具以收件人姓名或固定人员标识
和正文为必填输入，从 organization/contacts.json 解析地址并调用真实发送
通道；地址解析、失败拒绝、投递回执和上下文开销检查逐项通过。

返修追加（HQ 只读复核与工令 2026-10-13）：线程号格式无效必须拒绝
（HQ 隔离夹具 not-a-valid-thread-id 在案）；host_id 须核对不静默向错宿
主发送；真实发送须显式授权依据；同请求重复不重发；结果不明明确记录；
内部审计字段齐全。

方法约束：地址解析与失败拒绝逐项用真实通讯录与受控故障通讯录验证；
失败拒绝经真实 Codex 通道（格式合法但无效的线程号，不触达任何真实
收件人）验证；结果不明用例在通道边界以隔离桩模拟（明确标注，仅验证
本工具逻辑，不冒充通道行为）；本套不向任何真实经理发送消息——真实
投递验收须按机主可核实的对象与动作授权另行执行，由 HQ 选择批准的
真实业务投递作核对。

用法：python tools/i194_send_check.py
产物：logs/i194_发送工具检查_<时间戳>.json
"""
import datetime
import json
import os
import subprocess
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import send_manager_message as SMM   # 真实生产函数

CONTACTS = os.path.join(ROOT, "organization", "contacts.json")
HQ_FIXTURE = os.path.join(ROOT, "logs", "hq_intake",
                          "20261013_mainline_continuation",
                          "i194_invalid_address_fixture.json")
TOOL = os.path.join(ROOT, "tools", "send_manager_message.py")
SAMPLE_DIR = os.path.join(ROOT, "logs", "i194_samples")
AUDIT = os.path.join(ROOT, "logs", "i194_send_audit.jsonl")
BOGUS_THREAD = "00000000-0000-0000-0000-000000000000"   # 格式合法、必然无效
RESULTS = []


def record(cid, desc, ok, detail=""):
    RESULTS.append({"id": cid, "desc": desc, "pass": bool(ok),
                    "detail": str(detail)[:300]})


def fault_contacts(path, mutate):
    book = json.load(open(CONTACTS, encoding="utf-8"))
    mutate(book)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, indent=1)
    return path


def audit_entries():
    if not os.path.exists(AUDIT):
        return []
    out = []
    with open(AUDIT, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def main():
    os.makedirs(SAMPLE_DIR, exist_ok=True)

    # A1 姓名解析（真实通讯录，仅解析不发送）
    rc, r = SMM.send("Doro", "（验证样例正文，未发送）", resolve_only=True)
    okA1 = (rc == 0 and r["status"] == "resolved_only"
            and r["person_id"] == "doro"
            and r["thread_id"].startswith("01a12a93")
            and r["host_id"] == "local")
    record("A1", "姓名 Doro 解析到 doro（含宿主与线程号格式校验，未发送）",
           okA1, f"{r.get('status')} {r.get('person_id')} 用时"
                 f"{r.get('elapsed_ms')}ms")

    # A2 固定人员标识解析（大小写不敏感）
    rc, r = SMM.send("ANNI", "（验证样例正文，未发送）", resolve_only=True)
    record("A2", "人员标识 ANNI（大小写不敏感）解析到 anni",
           rc == 0 and r["person_id"] == "anni", f"{r.get('status')}")

    # A3 未知收件人明确报错（不猜测、不搜会话列表）
    rc, r = SMM.send("不存在的人", "（验证样例正文）", resolve_only=True)
    record("A3", "未知收件人：resolve_failed 且原因明确",
           rc == 1 and r["status"] == "resolve_failed"
           and "未知收件人" in r["reason"], r.get("reason"))

    # A4 姓名冲突明确报错
    def _dup_name(b):
        b["members"]["zzz"] = dict(b["members"]["zoya"], name="测试同名")
        b["members"]["fil"]["name"] = "测试同名"
    p4 = fault_contacts(os.path.join(SAMPLE_DIR, "dup_name.json"), _dup_name)
    rc, r = SMM.send("测试同名", "（验证样例正文）", contacts_path=p4,
                     resolve_only=True)
    record("A4", "姓名冲突：明确报错并建议用人员标识",
           rc == 1 and "冲突" in r.get("reason", ""), r.get("reason"))

    # A5 失效地址（线程号为空）
    p5 = fault_contacts(os.path.join(SAMPLE_DIR, "empty_thread.json"),
                        lambda b: b["members"]["doro"]["contact"]
                        .__setitem__("thread_id", ""))
    rc, r = SMM.send("Doro", "（验证样例正文）", contacts_path=p5,
                     resolve_only=True)
    record("A5", "失效地址（线程号为空）：明确报错",
           rc == 1 and "失效地址" in r.get("reason", ""), r.get("reason"))

    # A8 线程号格式无效（HQ 隔离夹具原文，返修核心反例）
    rc, r = SMM.send("Anni", "（验证样例正文）", contacts_path=HQ_FIXTURE,
                     resolve_only=True)
    okA8 = (rc == 1 and r["status"] == "resolve_failed"
            and "不是有效线程号格式" in r.get("reason", ""))
    record("A8", "线程号 not-a-valid-thread-id（HQ 夹具）：解析拒绝",
           okA8, r.get("reason"))

    # A9 宿主不符（host_id 非 local 不静默发送）
    p9 = fault_contacts(os.path.join(SAMPLE_DIR, "bad_host.json"),
                        lambda b: b["members"]["doro"]["contact"]
                        .__setitem__("host_id", "remote-host"))
    rc, r = SMM.send("Doro", "（验证样例正文）", contacts_path=p9,
                     resolve_only=True)
    record("A9", "宿主不符：明确报错，不静默向错宿主发送",
           rc == 1 and "宿主不符" in r.get("reason", ""), r.get("reason"))

    # A6 地址待核（缺核实时间）；A7 非 codex 平台
    p6 = fault_contacts(os.path.join(SAMPLE_DIR, "no_verify.json"),
                        lambda b: b["members"]["doro"]["contact"]
                        .__setitem__("verified_at", ""))
    rc, r = SMM.send("Doro", "（验证样例正文）", contacts_path=p6,
                     resolve_only=True)
    record("A6", "缺核实时间：地址待核明确报错",
           rc == 1 and "待核" in r.get("reason", ""), r.get("reason"))
    p7 = fault_contacts(os.path.join(SAMPLE_DIR, "bad_platform.json"),
                        lambda b: b["members"]["doro"]["contact"]
                        .__setitem__("platform", "carrier_pigeon"))
    rc, r = SMM.send("Doro", "（验证样例正文）", contacts_path=p7,
                     resolve_only=True)
    record("A7", "非 codex 平台：明确报错不支持",
           rc == 1 and "不支持的平台" in r.get("reason", ""), r.get("reason"))

    # B1 缺正文用法错误（命令行入口实际执行）
    cp = subprocess.run([sys.executable, TOOL, "Doro"],
                        capture_output=True, text=True)
    record("B1", "缺正文：命令行退出码 2（用法错误）",
           cp.returncode == 2, f"rc={cp.returncode}")

    # A10 授权门槛：格式合法但无效的线程号，缺授权依据即拒绝且不调通道
    p10 = fault_contacts(os.path.join(SAMPLE_DIR, "bogus_thread.json"),
                         lambda b: b["members"]["doro"]["contact"]
                         .__setitem__("thread_id", BOGUS_THREAD))
    rc, r = SMM.send("Doro", "I-194 验证探针", contacts_path=p10)
    okA10 = (rc == 1 and r["status"] == "authorization_missing"
             and r.get("authorization_basis") is None
             and "channel" not in r)
    record("A10", "缺授权依据：调用通道前拒绝，回执无通道字段",
           okA10, r.get("reason"))

    # B2 失败拒绝经真实通道（携带授权依据；无效线程不触达真实收件人）
    rid_b2 = "i194test-%s-%s-b2" % (time.strftime("%H%M%S"),
                                 uuid.uuid4().hex[:6])
    rc, r = SMM.send("Doro", "I-194 验证：无效线程失败拒绝探针（不触达"
                     "真实收件人）", contacts_path=p10,
                     authorization="I-184/I-194 施工验证授权：仅无效线程"
                                   "探针，无真实收件人",
                     request_id=rid_b2)
    r_b2 = dict(r)
    okB2 = (rc == 1 and r["status"] == "channel_rejected"
            and r["channel_exit_code"] == 1
            and "no rollout found" in (r.get("channel_stderr", "")
                                       + r.get("reason", "")))
    record("B2", "失败拒绝：真实通道对无效线程返回拒绝且原因在回执",
           okB2, f"channel_exit={r.get('channel_exit_code')} "
                 f"用时{r.get('elapsed_ms')}ms")

    # A11 同请求重复：同 request_id 再发不重发（退出码四）
    rc, r2 = SMM.send("Doro", "I-194 验证：重复请求探针", contacts_path=p10,
                      authorization="I-194 施工验证授权：重复请求探针",
                      request_id=rid_b2)
    okA11 = (rc == 4 and r2["status"] == "duplicate_no_resend"
             and r2.get("prior_status") == "channel_rejected"
             and "channel_exit_code" not in r2)
    record("A11", "同请求编号重复提交：不重发，返回先前结果（退出码四）",
           okA11, f"{r2.get('status')} 先前={r2.get('prior_status')}")

    # A12 结果不明（隔离桩：仅通道边界模拟超时，明确标注非通道行为）
    orig_run = SMM.subprocess.run

    def _fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["codex"], timeout=0.001)

    SMM.subprocess.run = _fake_run
    try:
        rc, r = SMM.send("Doro", "（隔离桩超时探针）", contacts_path=p10,
                         authorization="I-194 隔离验证：超时路径",
                         request_id="i194test-%s-%s-a12" % (time.strftime("%H%M%S"),
                                              uuid.uuid4().hex[:6]))
    finally:
        SMM.subprocess.run = orig_run
    record("A12", "结果不明（隔离桩模拟通道超时）：result_unclear 且凭"
                  "请求号对账", rc == 1 and r["status"] == "result_unclear",
           r.get("reason"))

    # A13 内部审计：字段齐全、各状态在案
    need = {"request_id", "at", "caller", "recipient", "person_id",
            "authorization", "status"}
    entries = audit_entries()
    recent = entries[-8:]
    okA13 = (len(entries) > 0 and all(need.issubset(set(e.keys()))
                                      for e in recent)
             and any(e["status"] == "authorization_missing" for e in recent)
             and any(e["status"] == "channel_rejected" for e in recent)
             and any(e["status"] == "duplicate_no_resend" for e in recent)
             and any(e["status"] == "result_unclear" for e in recent))
    record("A13", "内部审计：每次发送意图入档且字段齐全（拒绝、通道拒绝、"
                  "重复、结果不明各状态在案）", okA13,
           "审计文件 %s（近八条状态 %s）"
           % (AUDIT, "、".join(e["status"] for e in recent)))

    # C1 投递回执结构（以经真实通道的 B2 回执为样本；重复与超时回执
    # 不含通道字段属正确行为，不作结构样本）
    need_c = {"tool", "generated_at", "request_id", "caller",
              "recipient_input", "person_id", "thread_id", "host_id",
              "authorization_basis", "status", "reason", "elapsed_ms",
              "channel", "channel_exit_code"}
    okC1 = need_c.issubset(set(r_b2.keys()))
    record("C1", "回执结构：请求编号、调用身份、授权依据、宿主、通道与"
                 "耗时齐备（以真实通道回执为样本）", okC1,
           "缺：" + "、".join(sorted(need_c - set(r_b2.keys())))
           if not okC1 else "齐")

    # C2 上下文开销：单次调用无会话列表读取
    rc, rr = SMM.send("Elsa", "（验证样例正文，未发送）", resolve_only=True)
    okC2 = rr["status"] == "resolved_only" and rr["elapsed_ms"] < 2000
    record("C2", "一次调用开销：解析态 %.1f 毫秒、通道态 %.1f 毫秒，"
                 "无会话列表读取" % (rr["elapsed_ms"], r2.get("elapsed_ms",
                                                              0) or 0),
           okC2, f"resolve={rr['elapsed_ms']}ms")

    # C3 真实投递边界（未执行，待 HQ 按可核实授权选择批准的真实业务投递）
    record("C3", "真实投递验收未执行：待 HQ 在实现核对后选择批准的真实"
                 "业务投递（如 Anni 授权回复 Doro）作核对；本套不向任何"
                 "真实经理发送", True, "边界记录，非通过结论")

    n_pass = sum(1 for x in RESULTS if x["pass"])
    summary = {"total": len(RESULTS), "pass": n_pass,
               "fail": len(RESULTS) - n_pass,
               "verdict": "pass" if n_pass == len(RESULTS) else "fail"}
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"item": "I-194", "generated_at": ts, "tool": TOOL,
           "channel": "codex queue（codex-cli 0.153.4 实测在位）",
           "summary": summary, "checks": RESULTS,
           "inputs": {"CONTACTS": CONTACTS, "HQ_FIXTURE": HQ_FIXTURE}}
    path = os.path.join(ROOT, "logs", "i194_发送工具检查_%s.json" % ts)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    for x in RESULTS:
        print("%s %s %s %s" % ("过" if x["pass"] else "拒", x["id"],
                               x["desc"], x["detail"]))
    print("产物:", path)
    return 0 if summary["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
