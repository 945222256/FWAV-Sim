# -*- coding: utf-8 -*-
"""backfill_dispatch_registry.py — I-143：按 logs/dispatch_session/ 回填派发三元组。

历史派发留痕一个 JSON 一条，含会话号与简报头；路线与事项号从简报
文本按在册编号与路线名提取，提取不到的留空并标 origin=backfill。
只读留痕目录，只写 dispatch_registry.json（不覆盖已登记键）。"""
import datetime
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"G:\Playground\isaac_flapper_sim"
LOG_DIR = os.path.join(ROOT, "logs", "dispatch_session")
REG_PATH = os.path.join(ROOT, "lp_sync", "dispatch_registry.json")

ITEM_RE = re.compile(r"I-\d{3}")
LANE_RE = re.compile(r"(L\d{1,2})队|（(L\d{1,2})）|\b(L\d{1,2})\b")

reg = json.load(open(REG_PATH, encoding="utf-8")) if os.path.exists(REG_PATH) else {}
# 路线反查表：事项总账里事项号 → 路线（比留痕文本提取可靠，兜底用）。
try:
    items = json.load(open(os.path.join(ROOT, "items_registry", "items.json"),
                           encoding="utf-8"))["items"]
    item_lane = {i["id"]: i.get("lane") for i in items if i.get("lane")}
except (OSError, ValueError, KeyError):
    item_lane = {}

added, skipped = 0, 0
for fn in sorted(os.listdir(LOG_DIR)):
    if not fn.endswith(".json") or "selftest" in fn:
        continue
    try:
        rec = json.load(open(os.path.join(LOG_DIR, fn), encoding="utf-8"))
    except (OSError, ValueError):
        continue
    sid = rec.get("sessionId")
    if not sid or sid in reg:
        skipped += 1
        continue
    text = (rec.get("contentHead") or "")
    items_found = ITEM_RE.findall(text)
    lane = None
    m = re.search(r"(L\d{1,2})\s*队", text)
    if m:
        lane = m.group(1)
    item_id = items_found[0] if items_found else None
    if lane is None and item_id:
        lane = item_lane.get(item_id)          # 事项路线反查兜底
    reg[sid] = {
        "item_id": item_id,
        "lane": lane,
        "title_head": text[:40],
        "origin": "backfill",
        "dispatched_at": rec.get("at"),
        "backfill_from": "logs/dispatch_session/" + fn,
    }
    added += 1

tmp = REG_PATH + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(reg, f, ensure_ascii=False, indent=1)
os.replace(tmp, REG_PATH)
print("回填完成：新增 %d 条，跳过 %d 条（已登记或自测），总账 %d 条"
      % (added, skipped, len(reg)))
for sid, v in list(reg.items())[:6]:
    print(" ", sid[:22], "| lane", v.get("lane"), "| item", v.get("item_id"), "|", v.get("origin"))
