# -*- coding: utf-8 -*-
"""
vote_recall_eval.py — 累加器 Hough vs 投票点云：候选级召回 + 端到端对比
======================================================================

两级对比，隔离"候选生成方式"这一个变量（评估器/滑窗/散度门不变）：

  第一级（候选级）：每帧问"GT 圆心 5px 内有没有候选"→ 召回率；
                   每帧候选数（含假阳性）随阈值的变化曲线。
      A = cv2.HoughCircles（param2 扫描）
      B = VoteCloudGenerator（显著性 sig 扫描，量纲=圆周支持率）
  第二级（端到端）：三套检测器跑完整序列 → CPE RMSE / 有效率 / 时延。
      adaptive(累加器) / adaptive(投票点云) / plain_hough

运行：python perception/eval/vote_recall_eval.py [--out data/vote_recall_report.md]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Dict, List

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from perception.core.detector import (AdaptiveHoughDetector, GateDetection,
                                      PreprocessConfig)
from perception.core.metrics import evaluate_sequence, format_table
from perception.core.vote_cloud import (VoteCloudGenerator,
                                        VoteCloudHoughDetector)
from perception.eval.baselines import PlainHoughDetector
from perception.eval.run_eval import load_dataset

DATASETS = ["gate_clean", "gate_clutter", "gate_lowlight"]
NEAR_PX = 5.0          # 工作分辨率下算"命中 GT"的圆心距离


def work_gt(rec: Dict) -> Tuple[float, float, float]:
    return rec["u"] / 2.0, rec["v"] / 2.0, rec["r"] / 2.0   # downsample=2


def stage1_candidate_recall(dataset_dir: str) -> List[str]:
    meta, frames, gts = load_dataset(dataset_dir)
    ref = AdaptiveHoughDetector(PreprocessConfig())
    gen = VoteCloudGenerator()
    p2_list = [55, 40, 30, 25, 20]
    sig_list = [0.6, 0.5, 0.4, 0.3, 0.2]
    acc = {p: dict(hit=0, n=0, cand=0, t=0.0) for p in p2_list}
    accb = {s: dict(hit=0, n=0, cand=0, t=0.0) for s in sig_list}

    for path, rec in zip(frames, gts):
        img = cv2.imread(path)
        gray, roi, _ = ref._preprocess(img)
        gu, gv, gr = work_gt(rec)
        for p2 in p2_list:
            t0 = time.perf_counter()
            c = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=24,
                                 param1=30, param2=p2, minRadius=5, maxRadius=130)
            acc[p2]["t"] += time.perf_counter() - t0
            n = 0 if c is None else len(c[0])
            acc[p2]["n"] += 1
            acc[p2]["cand"] += n
            if c is not None and n:
                d = np.hypot(c[0][:, 0] - gu, c[0][:, 1] - gv)
                acc[p2]["hit"] += int(np.min(d) <= NEAR_PX)
        t0 = time.perf_counter()
        cands, sig = gen.candidates(gray, 5, 130)
        dt = time.perf_counter() - t0
        for s in sig_list:
            m = sig >= s
            accb[s]["n"] += 1
            accb[s]["cand"] += int(m.sum())
            accb[s]["t"] += dt / len(sig_list)     # 生成一次多阈值复用
            if np.any(m):
                d = np.hypot(cands[m, 0] - gu, cands[m, 1] - gv)
                accb[s]["hit"] += int(np.min(d) <= NEAR_PX)

    lines = [f"### 第一级：候选级召回（命中=候选圆心距 GT ≤{NEAR_PX:.0f}px 工作分辨率）",
             "", "| 方法 | 阈值 | 召回率 | 平均候选数/帧 | 候选生成耗时/帧 |",
             "|---|---|---|---|---|"]
    for p2 in p2_list:
        a = acc[p2]
        lines.append(f"| 累加器 Hough | param2={p2} | {a['hit']/max(1,a['n']):.2f} | "
                     f"{a['cand']/max(1,a['n']):.1f} | {a['t']/max(1,a['n'])*1e3:.1f}ms |")
    for s in sig_list:
        a = accb[s]
        lines.append(f"| 投票点云 | sig≥{s:.1f} | {a['hit']/max(1,a['n']):.2f} | "
                     f"{a['cand']/max(1,a['n']):.1f} | {a['t']/max(1,a['n'])*1e3:.1f}ms |")
    return lines


def stage2_end_to_end(dataset_dir: str) -> List[str]:
    meta, frames, gts = load_dataset(dataset_dir)
    rows: Dict[str, Dict[str, float]] = {}
    for name, det in (
        ("adaptive_累加器", AdaptiveHoughDetector(PreprocessConfig())),
        ("adaptive_投票点云", VoteCloudHoughDetector(PreprocessConfig())),
        ("plain_hough", PlainHoughDetector(PreprocessConfig())),
    ):
        preds: List[GateDetection] = []
        t0 = time.perf_counter()
        for p in frames:
            preds.append(det.process(cv2.imread(p)))
        wall = time.perf_counter() - t0
        m = evaluate_sequence(preds, gts, 640, 480)
        m["fps_pipeline"] = len(frames) / wall
        rows[name] = m
    lines = ["", "### 第二级：端到端（同一评估器/滑窗/散度门，仅换候选源）", "",
             format_table(rows)]
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data")
    ap.add_argument("--out", default="data/vote_recall_report.md")
    args = ap.parse_args()

    report = ["# 投票点云 vs 累加器 Hough 对比报告（实采数据集）", ""]
    for ds in DATASETS:
        d = os.path.join(args.root, ds)
        if not os.path.isdir(d):
            print(f"[skip] {d} 不存在")
            continue
        print(f"[eval] {ds} ...", flush=True)
        report.append(f"\n## {ds}\n")
        report.extend(stage1_candidate_recall(d))
        report.extend(stage2_end_to_end(d))
    text = "\n".join(report) + "\n"
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"[eval] 报告 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
