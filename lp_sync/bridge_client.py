# -*- coding: utf-8 -*-
"""bridge_client.py — I-177 外部智能体文件桥客户端（零网络）。

给沙箱外援（如 Codex）用的纯文件客户端：只读写本地文件，不导入
socket、http、urllib、requests 等任何网络模块，沙箱禁网时照常工作。
真正联网执行由宿主服务的派发请求文件队列完成（dispatch_file_queue）。

用法（在外援沙箱内，工作目录任意）：
  python bridge_client.py submit <请求JSON文件路径>
      把请求原子投放到 incoming/。请求格式：
      {"schema_version": 1, "request_id": "br-xxx", "op": "ledger_write",
       "agent_id": "你的身份", "ledger": {"action": "claim",
       "args": {"item_id": "I-xxx", "worker": "L3队",
                "session_id": "sess_..."}}}
  python bridge_client.py wait <request_id> [--timeout 300]
      轮询等待结果文件出现，打印结果并按结果退出码返回
      （0 成功含断网转入发件箱；1 定性失败或重试耗尽；2 超时）。
  python bridge_client.py status
      只读各目录计数，判断通道忙闲。

支持动作与必填参数：
  claim          item_id, worker（可选 session_id、lease_hours）
  release        item_id, worker
  append_evidence item_id, evidence（单条字符串）
  close          item_id, evidence, conclusion, criterion_check, owner_note
  block          item_id, reason, blocker_type, needs, evidence, owner_note
  set_criterion  item_id, index, verified
  owner_reply    item_id, text

request_id 是防重复的幂等键：同一编号重复递交只执行一次，结果可在
done/<request_id>.result.json 复查。失败请求进 retry/ 自动重试，重试
耗尽进 failed/ 并附 .result.json 错误全文；云端明确拒绝的定性失败不
重试，直接进 failed/。
"""
import datetime
import json
import os
import sys
import time

QUEUE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "dispatch_queue")
REQUEST_ID_MIN_LEN = 4


def _atomic_write(dst, obj):
    tmp = dst + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)


def submit(request):
    rid = request.get("request_id")
    if not rid or len(str(rid)) < REQUEST_ID_MIN_LEN:
        raise ValueError("request_id 缺失或过短")
    inc = os.path.join(QUEUE_ROOT, "incoming")
    os.makedirs(inc, exist_ok=True)
    dst = os.path.join(inc, rid + ".json")
    if os.path.exists(dst):
        raise ValueError("incoming/ 已有同名请求，换一个 request_id")
    _atomic_write(dst, request)
    return dst


def find_result(request_id):
    """在 done/ 与 failed/ 找结果文件。返回 (路径, 状态) 或 (None, 状态说明)。"""
    for sub in ("done", "failed"):
        p = os.path.join(QUEUE_ROOT, sub, request_id + ".result.json")
        if os.path.exists(p):
            return p, sub
    if os.path.exists(os.path.join(QUEUE_ROOT, "processing", request_id + ".json")):
        return None, "processing"
    if os.path.exists(os.path.join(QUEUE_ROOT, "retry", request_id + ".json")):
        return None, "retry"
    if os.path.exists(os.path.join(QUEUE_ROOT, "incoming", request_id + ".json")):
        return None, "queued"
    return None, "unknown"


def wait(request_id, timeout_s=300):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        p, state = find_result(request_id)
        if p:
            with open(p, encoding="utf-8") as f:
                return json.load(f), state
        time.sleep(2)
    return None, "timeout"


def cmd_submit(path):
    with open(path, encoding="utf-8") as f:
        req = json.load(f)
    dst = submit(req)
    print("[过] 请求已投放：%s（%s）" % (dst, datetime.datetime.now().isoformat(timespec="seconds")))
    print("等待结果：python bridge_client.py wait %s" % req.get("request_id"))
    return 0


def cmd_wait(rid, timeout_s):
    res, state = wait(rid, timeout_s)
    if res is None:
        print("[超时] %s 秒内未见结果（当前状态 %s）" % (timeout_s, state))
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if state == "done":
        note = "（断网转入发件箱：已入本地队列由同步代理重放）" \
            if res.get("result", {}).get("deferred") else ""
        print("[过] 宿主已执行完毕，结果如上%s" % note)
        return 0
    print("[失败] 定性失败或重试耗尽，错误见 result 字段")
    return 1


def cmd_status():
    counts = {}
    for d in ("incoming", "processing", "done", "retry", "failed", "invalid"):
        p = os.path.join(QUEUE_ROOT, d)
        counts[d] = len([f for f in os.listdir(p) if f.endswith(".json")
                         and not f.endswith((".meta.json", ".result.json"))]) \
            if os.path.isdir(p) else 0
    print(json.dumps({"queue_root": QUEUE_ROOT, "dirs": counts,
                      "checked_at": datetime.datetime.now().isoformat(timespec="seconds")},
                     ensure_ascii=False, indent=1))
    return 0


def main(argv):
    if len(argv) >= 2 and argv[0] == "submit":
        return cmd_submit(argv[1])
    if len(argv) >= 2 and argv[0] == "wait":
        timeout_s = 300
        if "--timeout" in argv:
            timeout_s = int(argv[argv.index("--timeout") + 1])
        return cmd_wait(argv[1], timeout_s)
    if argv and argv[0] == "status":
        return cmd_status()
    print("用法：python bridge_client.py submit <请求JSON文件> | wait <request_id> [--timeout 秒] | status")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
