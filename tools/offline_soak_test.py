# -*- coding: utf-8 -*-
"""offline_soak_test.py — 第三期断网验收实测：断网一小时写操作重放核对。

口径对应《云端托管_需求与约束_20260930.md》第三期验收判据：
断网一小时期间的写操作在恢复后完整重放，无丢失无重复。
断网用环境变量 LP_CLOUD_HOST 指向不可达地址模拟，只影响本进程。
运行前置：先停本地 sync_agent，避免代理把积压提前重放导致实测失真。
仅使用 Python 标准库。
"""
import datetime
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "items_registry"))

import cloud_client  # noqa: E402
import ledger        # noqa: E402

HOURS = 1
BATCH_EVERY_S = 10 * 60          # 每十分钟一批写操作。
BATCHES = (HOURS * 3600) // BATCH_EVERY_S
ITEM = "I-031"
REPORT = os.path.join(ROOT, "logs", "offline_soak_report.txt")


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().isoformat(timespec="seconds"), msg)
    print(line, flush=True)


def main():
    os.environ["LP_CLOUD_HOST"] = "192.0.2.1"     # TEST-NET 不可达地址，模拟断网。
    before = _count_evidence()
    log("断网实测开始。云端现存该事项证据 %d 条。" % before)

    for i in range(1, BATCHES + 1):
        time.sleep(BATCH_EVERY_S)
        ledger.append_evidence(ITEM, "断网一小时实测二跑第 %d 批写入（共 %d 批），写于云端不可达期间"
                               % (i, BATCHES))
        log("第 %d 批已入断网队列，积压 %d 条" % (i, cloud_client.outbox_size()))

    os.environ.pop("LP_CLOUD_HOST")
    log("网络恢复，开始重放。")
    remaining, rejected = cloud_client.replay()
    log("重放完成：剩余 %d 条，被拒 %d 条" % (remaining, rejected))

    after = _count_evidence()
    added = after - before
    events = _count_events()
    ok = (remaining == 0 and rejected == 0 and added == BATCHES and events == BATCHES)
    summary = ("断网一小时实测：%d 批写入，重放后云端证据新增 %d 条、事件新增 %d 条，"
               "无丢失无重复判定=%s" % (BATCHES, added, events, "通过" if ok else "未通过"))
    log(summary)
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), summary))


def _count_evidence():
    snap = _snapshot()
    item = next(i for i in snap["items"] if i["id"] == ITEM)
    return len(item["evidence"])


def _count_events():
    return len([e for e in _snapshot()["events"]
                if e["item_id"] == ITEM and "断网一小时实测二跑" in (e.get("detail") or "")])


def _snapshot():
    """连真云拉快照。临时摘除断网模拟变量并在结束后恢复，
    保证主流程的写操作始终处于模拟断网环境。"""
    saved = os.environ.get("LP_CLOUD_HOST")
    if saved:
        os.environ.pop("LP_CLOUD_HOST", None)
    try:
        return cloud_client.request("GET", "/api/agent/snapshot")
    finally:
        if saved:
            os.environ["LP_CLOUD_HOST"] = saved


if __name__ == "__main__":
    main()
