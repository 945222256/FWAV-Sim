# -*- coding: utf-8 -*-
"""lane_workers_contract_check.py — I-217 判据七：固定版本接口文件与
真实接口逐字段回读一致性检查。

用法：
  python lane_workers_contract_check.py <lane_queues.json 路径>   检查真实快照
  python lane_workers_contract_check.py --api <base_url>          检查 HTTP 接口

检查内容：
  一、契约文件 lane_workers_contract_v1.json 存在且 schema=lane-workers-v1；
  二、真实快照逐线逐字段与契约形状核对（类型与取值域）；
  三、一致性核对：active_count==len(workers)、waiting_count==len(waiting)、
     workers 内 phase ∈ 活跃词表、worker_id 形态 <lane>-wN；
  四、兼容字段真值核对：多工位时 current 必须是 workers 之一的
     request_id（不能指向等待任务），单工位时 current 即唯一活跃项；
  五、消费者字段核对：queued_items 每元素五键齐全。

输出 JSON 判定（pass/fail + 逐项差异），退出码 0/1。仅标准库。"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT_PATH = os.path.join(_HERE, "lane_workers_contract_v1.json")

WORKER_ID_RE = __import__("re").compile(r"^(L[0-9]{1,2}|HQ)-w[0-9]+$")
ACTIVE_PHASES = {"submitted", "dispatched", "result_pending", "uncertain",
                 "retry_wait"}


def check_snapshot(snap):
    issues = []
    ok_fields = 0
    if not isinstance(snap, dict) or "sampled_at" not in snap \
            or not isinstance(snap.get("queues"), dict):
        return False, ["快照缺 sampled_at 或 queues 对象"], 0
    ok_fields += 2
    for lane, q in snap["queues"].items():
        if not isinstance(q, dict):
            issues.append("%s：队列项不是对象" % lane)
            continue
        limit = q.get("concurrency_limit")
        if isinstance(limit, int) and 1 <= limit <= 2:
            ok_fields += 1
        else:
            issues.append("%s：concurrency_limit 非法 %r" % (lane, limit))
        workers = q.get("workers")
        if isinstance(workers, list):
            ok_fields += 1
            for w in workers:
                for k in ("worker_id", "item_id", "session_id", "run_id",
                          "phase", "request_id"):
                    if k not in w:
                        issues.append("%s：workers 元素缺 %s" % (lane, k))
                if not WORKER_ID_RE.match(str(w.get("worker_id"))):
                    issues.append("%s：worker_id 形态非法 %r"
                                  % (lane, w.get("worker_id")))
                if w.get("worker_id", "x-w9").rsplit("-w", 1)[0] != lane:
                    issues.append("%s：worker_id 线号不匹配 %r"
                                  % (lane, w.get("worker_id")))
                if w.get("phase") not in ACTIVE_PHASES:
                    issues.append("%s：workers 元素 phase 非活跃 %r"
                                  % (lane, w.get("phase")))
        else:
            issues.append("%s：workers 非数组" % lane)
        ac = q.get("active_count")
        if ac is not None and workers is not None and ac != len(workers):
            issues.append("%s：active_count=%r 与 workers 数 %d 不一致"
                          % (lane, ac, len(workers)))
        else:
            ok_fields += 1
        waiting = q.get("waiting")
        if isinstance(waiting, dict):
            ok_fields += 1
            if q.get("waiting_count") != len(waiting):
                issues.append("%s：waiting_count=%r 与 waiting 数 %d 不一致"
                              % (lane, q.get("waiting_count"), len(waiting)))
        else:
            issues.append("%s：waiting 非对象" % lane)
        cur = q.get("current")
        if cur is None:
            if workers:
                issues.append("%s：有活跃工位但 current 为空" % lane)
        else:
            worker_reqs = {w.get("request_id") for w in (workers or [])}
            if workers and cur not in worker_reqs:
                issues.append("%s：current %r 不属于任何活跃工位（兼容字段"
                              "真值违规）" % (lane, cur))
            else:
                ok_fields += 1
        for item in q.get("queued_items") or []:
            if not all(k in item for k in ("request_id", "item_id", "title",
                                           "phase", "reason")):
                issues.append("%s：queued_items 元素缺键 %r" % (lane, item))
            else:
                ok_fields += 1
        conf = q.get("conflict")
        if conf is None:
            issues.append("%s：缺 conflict 字段" % lane)
        elif conf and len(workers or []) <= (limit or 1):
            issues.append("%s：未超限却标 conflict" % lane)
        else:
            ok_fields += 1
    return not issues, issues, ok_fields


def main(argv):
    contract = json.load(open(CONTRACT_PATH, encoding="utf-8"))
    if contract.get("schema") != "lane-workers-v1":
        print(json.dumps({"pass": False,
                          "issues": ["契约文件 schema 不是 lane-workers-v1"]},
                         ensure_ascii=False))
        return 1
    if len(argv) >= 2 and argv[0] == "--api":
        import urllib.request
        url = argv[1].rstrip("/") + "/api/agent/lane_queues"
        body = json.loads(urllib.request.urlopen(url, timeout=15).read())
        snap = body.get("result") or {}
    else:
        path = argv[0] if argv else os.path.join(
            _HERE, "kernel_state", "lane_queues.json")
        queues = json.load(open(path, encoding="utf-8"))
        snap = {"sampled_at": "local-file", "queues": queues}
    ok, issues, ok_fields = check_snapshot(snap)
    out = {"pass": ok, "checked_fields": ok_fields,
           "issues": issues, "source": snap.get("sampled_at")}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
