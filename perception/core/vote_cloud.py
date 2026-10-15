# -*- coding: utf-8 -*-
"""
vote_cloud.py — Hough 投票的点云化候选生成器（实验性）
======================================================

把"抽象圆约束"显式改写为参数空间点云的密度模态检测：

    1. 边缘点（Canny）沿梯度方向 ±，对每个离散半径 r 向 (u,v,r) 三维
       参数空间投票 —— 每张票是一个参数空间点；
    2. 三维直方图（bin=2px）+ 高斯核平滑 ≈ 投票点云的核密度；
    3. 局部极大 = 聚类模态 = 候选圆；
    4. 模态显著性 = 峰值密度 / (2πr̂ · k₀)（k₀=核对单位冲激的峰值响应，
       消除平滑稀释）—— 直读为"圆周被支持的比例"∈[0,1]，对半径归一，
       天然替代论文 param2∝r 的自适应阈值。

与 cv2.HoughCircles（硬网格累加器 + param2 计数阈值）的差异：
    无 dp 网格量化、显著性有几何量纲（圆周支持率）、模态提取即聚类。

实现踩坑（v1 全灭的两处根因，勿重蹈）：
    • r 维平滑必须 reshape 成 (V·U, R) 用 (1,ksz) 核——展平成 (-1,1)
      会在 (v,u) 行间跨界模糊，把不同像素的半径维混掉；
    • 显著性分母必须含高斯核稀释因子 k₀（σ=1.5bin 时 ≈0.019），否则
      完整圆的支持率天花板只有 ~k₀ 量级，任何合理阈值都全灭。

用途：perception/eval/vote_recall_eval.py 在实采数据集上与本组件累加器
版做候选级召回/假阳性与端到端对比。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

from .detector import AdaptiveHoughDetector, PreprocessConfig


class VoteCloudGenerator:
    """参数空间投票点云 → 候选圆模态（工作分辨率坐标）。"""

    def __init__(self,
                 bin_px: int = 2,
                 r_step: int = 2,
                 sigma_bins: float = 1.5,
                 top_k: int = 8):
        self.bin_px = bin_px
        self.r_step = r_step
        self.sigma = sigma_bins
        self.top_k = top_k
        self._k0: Optional[float] = None   # 核冲激峰值（平滑稀释因子）

    # ------------------------------------------------------------
    def _kernel_impulse_peak(self) -> float:
        """单位冲激经同样三维平滑后的峰值（缓存；与数组尺寸无关）。"""
        if self._k0 is None:
            a = np.zeros((32, 32, 16), dtype=np.float32)
            a[16, 16, 8] = 1.0
            b = self._smooth(a)
            self._k0 = float(b.max())
        return self._k0

    def _smooth(self, hist: np.ndarray) -> np.ndarray:
        """uv 逐切片高斯 + r 维高斯（(V·U,R) 重排后 (1,ksz) 核）。"""
        ksz = int(2 * round(1.5 * self.sigma) + 1)
        v, u, r = hist.shape
        for k in range(r):
            hist[:, :, k] = cv2.GaussianBlur(hist[:, :, k], (ksz, ksz), self.sigma)
        hist = cv2.GaussianBlur(hist.reshape(v * u, r), (1, ksz), self.sigma)
        return hist.reshape(v, u, r)

    # ------------------------------------------------------------
    def candidates(self, gray: np.ndarray, min_r: int, max_r: int
                   ) -> Tuple[np.ndarray, np.ndarray]:
        """返回 (candidates (N,3)[u,v,r], significances (N,))，按显著性降序。"""
        h, w = gray.shape
        edges = cv2.Canny(gray, 30.0, 15.0)
        ys, xs = np.nonzero(edges)
        if len(xs) == 0:
            return np.empty((0, 3)), np.empty((0,))
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)[ys, xs]
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)[ys, xs]
        norm = np.hypot(gx, gy)
        ok = norm > 1e-3
        xs, ys = xs[ok], ys[ok]
        gx, gy = gx[ok] / norm[ok], gy[ok] / norm[ok]
        if len(xs) == 0:
            return np.empty((0, 3)), np.empty((0,))

        radii = np.arange(max(min_r, 3), max_r + 1, self.r_step)
        nb_u, nb_v = w // self.bin_px, h // self.bin_px
        b = self.bin_px

        # 双向投票（暗环对亮背景与反之的边缘梯度方向相反，符号不可知）
        iu_all, iv_all, k_all = [], [], []
        for k, r in enumerate(radii):
            for sgn in (-1.0, 1.0):
                iu = np.floor((xs + sgn * r * gx) / b).astype(np.int32)
                iv = np.floor((ys + sgn * r * gy) / b).astype(np.int32)
                m = (iu >= 0) & (iu < nb_u) & (iv >= 0) & (iv < nb_v)
                iu_all.append(iu[m])
                iv_all.append(iv[m])
                k_all.append(np.full(int(m.sum()), k, dtype=np.int32))
        if not iu_all:
            return np.empty((0, 3)), np.empty((0,))
        hist = np.zeros((nb_v, nb_u, len(radii)), dtype=np.float32)
        np.add.at(hist, (np.concatenate(iv_all), np.concatenate(iu_all),
                         np.concatenate(k_all)), 1.0)
        hist = self._smooth(hist)
        k0 = self._kernel_impulse_peak()

        # 局部极大（uv 切片内对膨胀图比较 + r 维与相邻切片比较）
        out: List[Tuple[float, float, float, float]] = []
        r_half = 1
        max_all = float(hist.max())
        for k, r in enumerate(radii):
            sl = hist[:, :, k]
            if float(sl.max()) < 0.2 * max_all:
                continue
            dil = cv2.dilate(sl, np.ones((3, 3), np.float32))
            pys, pxs = np.nonzero((sl >= dil) & (sl > 1e-6))
            for py, px in zip(pys, pxs):
                d = float(sl[py, px])
                if k > 0 and d <= hist[py, px, k - 1]:
                    continue
                if k < len(radii) - 1 and d < hist[py, px, k + 1]:
                    continue
                out.append(((px + 0.5) * b, (py + 0.5) * b, float(r), d))
        if not out:
            return np.empty((0, 3)), np.empty((0,))

        k0 = max(k0, 1e-9)
        scored = [(u, v, r, d / (2.0 * np.pi * max(r, 3.0) * k0))
                  for u, v, r, d in out]
        scored.sort(key=lambda m: -m[3])
        arr = np.array([(u, v, r) for u, v, r, _ in scored[:self.top_k]])
        sig = np.array([s for *_, s in scored[:self.top_k]])
        return arr, sig


class VoteCloudHoughDetector(AdaptiveHoughDetector):
    """端到端对比用：候选源换成投票点云模态，其余（评估器/滑窗/散度门）
    与累加器版完全一致——隔离"候选生成方式"这一个变量。"""

    def __init__(self, cfg: Optional[PreprocessConfig] = None,
                 sig_threshold: float = 0.30,
                 generator: Optional[VoteCloudGenerator] = None):
        super().__init__(cfg)
        self.gen = generator or VoteCloudGenerator()
        self.sig_threshold = sig_threshold

    def _hough_circles(self, gray: np.ndarray) -> Optional[np.ndarray]:
        cfg = self.cfg
        cands, sig = self.gen.candidates(gray, cfg.min_radius, cfg.max_radius)
        keep = sig >= self.sig_threshold
        if not np.any(keep):
            return None
        return cands[keep]
