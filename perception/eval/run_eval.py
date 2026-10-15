# -*- coding: utf-8 -*-
"""
run_eval.py — 数据集评测（论文 Table II 口径复刻）
==================================================

对一个 gate_dataset.py / RideAlongCollector 采集的目录跑：
    adaptive    本组件 AdaptiveHoughDetector
    plain_hough 论文传统基线（同预处理/参数，param2 固定，无评估器/滑窗）
    yolo11      可选：--weights yolov11n.pt（需 ultralytics，CPU 即可，
                复现论文"无 GPU 加速"条件）

用法：
    python perception/eval/run_eval.py --dataset data/gate_clean
    python perception/eval/run_eval.py --dataset data/gate_lowlight \
        --yolo-weights runs/gate/best.pt

输出：终端 Markdown 表 + <dataset>/eval_report.md
"""

from __future__ import annotations

import argparse
import json
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
from perception.eval.baselines import PlainHoughDetector


def load_dataset(ds_dir: str):
    meta_p = os.path.join(ds_dir, "meta.json")
    gt_p = os.path.join(ds_dir, "gt.jsonl")
    frames_p = sorted(
        f for f in os.listdir(ds_dir) if f.startswith("frame_") and f.endswith(".png"))
    if not os.path.exists(gt_p) or not frames_p:
        raise FileNotFoundError(f"数据集不完整: {ds_dir}（需 gt.jsonl + frame_*.png）")
    with open(meta_p, "r", encoding="utf-8") as f:
        meta = json.load(f)
    gts = []
    with open(gt_p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                gts.append(json.loads(line))
    gts = [g for g in gts if g["visible"]]        # 门出画帧不计（客观不可检）
    kept = {g["frame"] for g in gts}
    frames = [os.path.join(ds_dir, f) for f in frames_p
              if int(f.split("_")[1].split(".")[0]) in kept]
    gts = [g for g in gts if os.path.exists(
        os.path.join(ds_dir, f"frame_{g['frame']:06d}.png"))]
    frames = [os.path.join(ds_dir, f"frame_{g['frame']:06d}.png") for g in gts]
    return meta, frames, gts


def run_yolo(weights: str, frames: List[str], gts: List[Dict]) -> Dict[str, float]:
    """YOLOv11 基线（可选）。检测框 → 内切圆 (u,v,r)。"""
    try:
        from ultralytics import YOLO
    except ImportError:
        print("[eval] ultralytics 未安装，跳过 YOLOv11 基线")
        return {}
    model = YOLO(weights)
    preds: List[GateDetection] = []
    t_all = 0.0
    for p in frames:
        t0 = time.perf_counter()
        res = model.predict(p, verbose=False, device="cpu",
                            conf=0.25, max_det=3)
        t_all += time.perf_counter() - t0
        # 部署口径：取最高置信度框 → 内切圆 (u,v,r)
        best = None
        best_conf = -1.0
        for r in res:
            for b in r.boxes:
                conf = float(b.conf[0])
                if conf > best_conf:
                    x1, y1, x2, y2 = b.xyxy[0].tolist()
                    best_conf = conf
                    best = ((x1 + x2) / 2.0, (y1 + y2) / 2.0,
                            min(x2 - x1, y2 - y1) / 2.0)
        det = GateDetection(valid=False)
        if best is not None:
            det = GateDetection(u=best[0], v=best[1], r=best[2], valid=True)
        det.latency_ms = t_all / max(1, len(frames)) * 1e3
        preds.append(det)
    return evaluate_sequence(preds, gts, 640, 480)


def main() -> int:
    ap = argparse.ArgumentParser("run_eval")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--yolo-weights", default="")
    args = ap.parse_args()

    meta, frames, gts = load_dataset(args.dataset)
    K = meta.get("intrinsics", {})
    W, H = int(K.get("width", 640)), int(K.get("height", 480))
    print(f"[eval] {args.dataset}: {len(frames)} 可见帧 | scenario="
          f"{meta.get('scenario')} light={meta.get('light')}")

    rows: Dict[str, Dict[str, float]] = {}

    # adaptive
    det = AdaptiveHoughDetector(PreprocessConfig())
    preds: List[GateDetection] = []
    t0 = time.perf_counter()
    for p in frames:
        img = cv2.imread(p)
        preds.append(det.process(img))
    t_wall = time.perf_counter() - t0
    m = evaluate_sequence(preds, gts, W, H)
    m["fps_pipeline"] = len(frames) / t_wall
    rows["adaptive"] = m

    # plain hough
    plain = PlainHoughDetector(PreprocessConfig())
    preds = []
    t0 = time.perf_counter()
    for p in frames:
        preds.append(plain.process(cv2.imread(p)))
    t_wall = time.perf_counter() - t0
    m = evaluate_sequence(preds, gts, W, H)
    m["fps_pipeline"] = len(frames) / t_wall
    rows["plain_hough"] = m

    # yolo11 (optional)
    if args.yolo_weights and os.path.exists(args.yolo_weights):
        m = run_yolo(args.yolo_weights, frames, gts)
        if m:
            m.setdefault("fps_pipeline", 1000.0 / max(1e-9, m.get("latency_ms_mean", 1e9)))
            rows["yolo11"] = m

    table = format_table(rows)
    pretrain = {"adaptive": "否", "plain_hough": "否",
                "yolo11": "是（200 张级标注）"} if "yolo11" in rows else \
        {"adaptive": "否", "plain_hough": "否"}
    report = [f"# 评测报告 — {os.path.basename(args.dataset)}", "",
              f"- 帧数(门可见): {len(frames)} | 工况: {meta.get('scenario')} / "
              f"{meta.get('light')}", "",
              table, "", f"免预训练: {pretrain}", "",
              f"CPE RMSE 归一化(÷对角线): "
              f"{ {k: round(v.get('cpe_rmse_norm', float('nan')), 5) for k, v in rows.items()} }"]
    text = "\n".join(str(x) for x in report)
    print("\n" + text)
    out_p = os.path.join(args.dataset, "eval_report.md")
    with open(out_p, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\n[eval] 报告 → {out_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
