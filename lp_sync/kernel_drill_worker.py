# -*- coding: utf-8 -*-
"""kernel_drill_worker.py — I-178 故障演练的隔离队列工作器。

模拟宿主服务的桥行为，跑在隔离队列根上：
  - dispatch_* 请求由假执行器承接（假会话、回读为真），执行完把结果
    文件写进内核收件箱（模拟执行器收工交结果）；
  - ledger_write 请求走真实 _call_ledger，登记库目录由环境变量
    LP_ITEMS_REGISTRY 指向隔离副本（测试状态库），本地模式执行。

环境变量：
  DRILL_QUEUE_ROOT   隔离队列根（必填）
  DRILL_KERNEL_INBOX 内核收件箱（假执行器写结果用，必填）
  DRILL_EXEC_BRIEF   真实执行器模式：简报落地文件（消息含「真实执行器」
                     标记时启用，此时不自动写结果，由独立执行器子进程
                     kernel_drill_executor.py 自行交结果）
  FAKE_EXEC_MS       假执行器耗时毫秒（缺省 800）
  FAKE_EXEC_OUTCOME  假执行器结果类型 closed|blocked|evidence_appended
                     |invalid_close（缺省 closed）
仅使用 Python 标准库。
"""
import json
import os
import re
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import dispatch_file_queue as dq

QUEUE_ROOT = os.environ["DRILL_QUEUE_ROOT"]
KERNEL_INBOX = os.environ["DRILL_KERNEL_INBOX"]
EXEC_BRIEF = os.environ.get("DRILL_EXEC_BRIEF", "")
EXEC_MS = int(os.environ.get("FAKE_EXEC_MS", "800"))
OUTCOME = os.environ.get("FAKE_EXEC_OUTCOME", "closed")

_session_seq = [0]

OWNER_NOTE = ("演练机主说明：本次为常驻编排内核故障演练的构造性收尾说明，"
              "回答做了什么、结果是什么、为什么是这个结果、下一步是什么四问，"
              "全部由演练脚本生成，用于满足关闭校验的字数与措辞要求。")


def _write_result(run_id, item_id, outcome):
    os.makedirs(KERNEL_INBOX, exist_ok=True)
    rid = "kr-%s" % run_id
    if outcome == "closed":
        payload = {"schema_version": 1, "kind": "result_submit",
                   "result_id": rid, "run_id": run_id, "item_id": item_id,
                   "outcome": "closed", "conclusion": "判据通过",
                   "evidence": ["演练证据：假执行器完成 %s 的构造性运行" % run_id],
                   "criterion_check": [{"判据": "演练判据", "结果": "过",
                                        "数值出处": "演练脚本"}],
                   "owner_note": OWNER_NOTE, "closer": "L3队"}
    elif outcome == "blocked":
        payload = {"schema_version": 1, "kind": "result_submit",
                   "result_id": rid, "run_id": run_id, "item_id": item_id,
                   "outcome": "blocked", "reason": "演练构造的阻塞原因说明",
                   "blocker_type": "工具或环境故障",
                   "needs": "演练解除条件", "evidence": ["演练阻塞证据"],
                   "owner_note": OWNER_NOTE, "reporter": "L3队"}
    elif outcome == "evidence_appended":
        payload = {"schema_version": 1, "kind": "result_submit",
                   "result_id": rid, "run_id": run_id, "item_id": item_id,
                   "outcome": "evidence_appended",
                   "evidence": "演练阶段证据：假执行器追加一条证据 %s" % run_id}
    else:  # invalid_close：过内核浅校验、撞登记库深校验（判据表缺键）。
        payload = {"schema_version": 1, "kind": "result_submit",
                   "result_id": rid, "run_id": run_id, "item_id": item_id,
                   "outcome": "closed", "conclusion": "判据通过",
                   "evidence": ["演练证据：判据表缺键结果"],
                   "criterion_check": [{"判据": "演练判据", "结果": "过"}],
                   "owner_note": OWNER_NOTE, "closer": "L3队"}
    dst = os.path.join(KERNEL_INBOX, rid + ".json")
    tmp = dst + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)


def fake_dispatcher(req):
    """假执行通道：开假会话、回读为真；异步完成并回写结果文件。

    结果类型按消息「结果类型:xxx」标记取；执行耗时按「执行毫秒:N」
    标记取；两者缺省用环境变量。消息含「真实执行器」标记时只把简报
    落地到 DRILL_EXEC_BRIEF，不代写结果，由独立执行器子进程自行交。"""
    _session_seq[0] += 1
    sid = "sess_fake%02d-0000-0000-0000-000000000000" % _session_seq[0]
    msg = req.get("message") or ""
    m_out = re.search(r"结果类型[:：]([A-Za-z_]+)", msg)
    outcome = m_out.group(1) if m_out else OUTCOME
    m_ms = re.search(r"执行毫秒[:：](\d+)", msg)
    exec_ms = int(m_ms.group(1)) if m_ms else EXEC_MS

    if EXEC_BRIEF and "真实执行器" in msg:
        tmp = EXEC_BRIEF + ".writing"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "item_id": req.get("item_id"),
                       "message": msg, "at": time.time()}, f,
                      ensure_ascii=False)
        os.replace(tmp, EXEC_BRIEF)
        return {"ok": True, "session": sid, "channel": "test",
                "accepted": True, "readback_verified": True}

    def execute():
        time.sleep(exec_ms / 1000.0)
        m = re.search(r"run-[A-Za-z0-9_.\-]+", msg)
        run_id = m.group(0) if m else "run-unknown"
        item_id = req.get("item_id") or "I-999"
        _write_result(run_id, item_id, outcome)

    threading.Thread(target=execute, daemon=True).start()
    return {"ok": True, "session": sid, "channel": "test",
            "accepted": True, "readback_verified": True}


def main():
    worker = dq.QueueWorker(root=QUEUE_ROOT, dispatcher=fake_dispatcher,
                            poll_interval_s=0.5, stable_grace_s=0.2)
    print("[演练工作器] 隔离队列 %s，假执行耗时 %d ms，结果类型 %s"
          % (QUEUE_ROOT, EXEC_MS, OUTCOME), flush=True)
    worker.run()


if __name__ == "__main__":
    main()
