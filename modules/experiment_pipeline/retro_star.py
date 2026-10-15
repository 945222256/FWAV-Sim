# -*- coding: utf-8 -*-
"""retro_star.py 既有重点样本补标，事项 I-104。

名单生成规则（HQ 未另给名单时按判据记录自动生成）：
  两颗星：基准判据记录中判定为通过且带数据文件的全部采样，逐份从
  logs/bench_scores 的分数档机器提取；
  一颗星：登记在册事项的关键对照采样，逐份列出并附理由与出处。

补标方式为复制加星标名，不重命名原文件，既有证据路径零影响。半角星
号为系统禁用字符，本工具只产出全角星，并对产物名做禁用字符自检。

用法：PYTHONUTF8=1 python -m modules.experiment_pipeline.retro_star
产物：logs/experiments/star_marks_时间戳.md 与同名 jsonl 清单。
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from modules.experiment_pipeline.runner import (  # noqa: E402
    STAR_IMPORTANT, STAR_PASS)

LOGS_DIR = os.path.join(_REPO_ROOT, "logs")
SCORES_GLOB = os.path.join(LOGS_DIR, "bench_scores", "*_scores.json")

# 一颗星名单：登记在册事项的关键对照采样，理由与出处随行。
IMPORTANCE_LIST = [
    ("logs/ident_stream_flight_1003_222149.csv",
     "I-038 波形桥通道账 A 跑基线", "I-038"),
    ("logs/ident_stream_flight_1003_222258.csv",
     "I-038 B 跑现树幅值覆盖", "I-038"),
    ("logs/ident_stream_flight_1003_222400.csv",
     "I-038 C 跑旧树幅值覆盖", "I-038"),
    ("logs/ident_stream_flight_1003_222947.csv",
     "I-038 D 跑旧树默认对照", "I-038"),
    ("logs/ident_stream_flight_1003_223043.csv",
     "I-038 E 旧树分裂比覆盖", "I-038"),
    ("logs/ident_stream_flight_1003_223141.csv",
     "I-038 E 新树分裂比覆盖，通道账六跑定案组", "I-038"),
    ("logs/ident_stream_flight_1004_174045.csv",
     "I-067 坡度链路修复验收采集", "I-067"),
    ("logs/ident_stream_flight_1006_114846.csv",
     "I-095 尾翼补强回归新配置", "I-095"),
    ("logs/I095_BASE_stream.csv",
     "I-095 尾翼补强回归变更前基线", "I-095"),
    ("logs/ident_stream_flight_1006_080644.csv",
     "I-083 多坡度配置族首批样本二度", "I-083"),
    ("logs/ident_stream_flight_1006_080943.csv",
     "I-083 多坡度配置族首批样本三度", "I-083"),
    ("logs/ident_stream_flight_1006_081238.csv",
     "I-083 多坡度配置族首批样本五度", "I-083"),
]


def _star_copy(src_rel, stars, reason, source):
    """复制原文件为带全角星前缀名，返回登记行；缺文件如实登记。"""
    src = os.path.join(_REPO_ROOT, src_rel)
    prefix = STAR_PASS if stars >= 2 else STAR_IMPORTANT
    entry = {"original": src_rel, "stars": stars,
             "reason": reason, "source": source, "copy": None,
             "status": ""}
    if "*" in prefix:
        entry["status"] = "内部错误：星前缀含半角星"
        return entry
    if not os.path.isfile(src):
        entry["status"] = "原文件缺失，跳过"
        return entry
    dst = os.path.join(os.path.dirname(src), prefix + os.path.basename(src))
    try:
        shutil.copy2(src, dst)
    except OSError as exc:
        entry["status"] = "复制失败：%s" % exc
        return entry
    meta = src + ".meta.json"
    if os.path.isfile(meta):
        shutil.copy2(meta, os.path.join(
            os.path.dirname(src), prefix + os.path.basename(meta)))
    entry["copy"] = os.path.relpath(dst, _REPO_ROOT).replace(os.sep, "/")
    entry["status"] = "已补标"
    return entry


def main():
    rows = []
    seen = set()
    for sf in sorted(glob.glob(SCORES_GLOB)):
        try:
            with open(sf, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        for subj in data.get("subjects", []):
            if subj.get("verdict") != "pass" or not subj.get("data_file"):
                continue
            rel = os.path.relpath(subj["data_file"], _REPO_ROOT).replace(
                os.sep, "/")
            if rel in seen:
                continue
            seen.add(rel)
            rows.append(_star_copy(rel, 2,
                                   "基准判据记录判定通过，出处 %s"
                                   % os.path.basename(sf),
                                   "bench_scores"))
    for rel, reason, source in IMPORTANCE_LIST:
        if rel in seen:
            continue
        seen.add(rel)
        rows.append(_star_copy(rel, 1, reason, source))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    md = ["# 既有重点样本补标清单（事项 I-104，自动生成）", ""]
    md.append("规则：判据通过两颗全角星，重要意义一颗；补标为复制加星"
              "标名，原文件不重命名；半角星为系统禁用字符未使用。")
    md.append("")
    md.append("| 星 | 原文件 | 补标副本 | 理由 | 出处 | 状态 |")
    md.append("|---|---|---|---|---|---|")
    for r in rows:
        md.append("| %s | %s | %s | %s | %s | %s |" % (
            "★" * r["stars"], r["original"], r["copy"] or "-",
            r["reason"], r["source"], r["status"]))
    md_path = os.path.join(LOGS_DIR, "experiments",
                           "star_marks_%s.md" % stamp)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    jsonl_path = md_path[:-3] + ".jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    ok = sum(1 for r in rows if r["status"] == "已补标")
    print("补标完成 %d/%d，清单 %s" % (ok, len(rows), md_path))
    for r in rows:
        if r["status"] != "已补标":
            print("  未成：%s %s" % (r["original"], r["status"]))
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
