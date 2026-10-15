# -*- coding: utf-8 -*-
"""
baselines.py — 论文 Table II 对照基线

plain_hough : "unmodified Hough circle detection"——论文原话：与改进法
              相同的降采样/滤波/边缘预处理、相同参数（param1=30,
              param2=55 固定），无评估器、无滑窗、无自适应。取 OpenCV
              累加器最优圆（circles[0]）。

yolo11      : 可选。论文用 200 张自标注图单类训练、CPU 无 GPU（NUC11
              ~10fps）。本组件不内置模型；若环境装有 ultralytics 且给
              --weights 则在 run_eval 中调用，否则跳过并标注 N/A。
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from perception.core.detector import (GateDetection, PreprocessConfig,
                                      AdaptiveHoughDetector)


class PlainHoughDetector:
    """朴素 Hough 基线：同预处理、param2 固定、逐帧、取最优圆。"""

    def __init__(self, cfg: Optional[PreprocessConfig] = None):
        # 复用 AdaptiveHoughDetector 的预处理，但不使用其状态机
        self._ref = AdaptiveHoughDetector(cfg or PreprocessConfig())
        self.cfg = self._ref.cfg

    def process(self, bgr: np.ndarray) -> GateDetection:
        import time
        t0 = time.perf_counter()
        gray, _, scale = self._ref._preprocess(bgr)
        cfg = self.cfg
        try:
            circles = cv2.HoughCircles(
                gray, cv2.HOUGH_GRADIENT, dp=1,
                minDist=cfg.min_dist,
                param1=cfg.param1, param2=cfg.param2_0,   # 固定 55
                minRadius=cfg.min_radius, maxRadius=cfg.max_radius)
        except cv2.error:
            circles = None
        if circles is None or len(circles) == 0:
            return GateDetection(valid=False,
                                 latency_ms=(time.perf_counter() - t0) * 1e3)
        u, v, r = circles[0][0]
        return GateDetection(u=float(u) * scale, v=float(v) * scale,
                             r=float(r) * scale, fit=float("nan"),
                             valid=True,
                             latency_ms=(time.perf_counter() - t0) * 1e3)
