# -*- coding: utf-8 -*-
"""
detector.py — 自适应 Hough 圆门检测器（SparrowHawk TII 2026, §V.A Algorithm 1 复刻）
=================================================================================

论文给定参数（正文明确写出的全部保留原值）：
    预处理        降采样 → 灰度 → 高斯 → 形态学 → Canny
    Hough 圆      param1 = 30, param2 初值 = 55
    评估器 E      候选圆画在零底图上与 ROI 二值掩码按位与，重叠像素按候选圆
                  周长归一化 → 匹配分；fitDegree = 0.8, minFitDegree = 0.5,
                  保留前 maxFitNum = 3
    自适应机制    param2_update()：累加器阈值按检出半径成比例动态调整
    滑窗          每 period = 3 帧评估一帧，结果存 capacity = 7 的缓冲，
                  超限丢弃最旧；无有效候选时重置 param2；其余情况沿用上一
                  帧结果保证时序稳定

论文未给定、由本实现选定（均可从 PreprocessConfig 覆盖）：
    param2 比例律  Hough 累加器投票数近似 ∝ 圆周长 ∝ r，故取
                  param2 = clip(param2_0 · r / r_ref, p2_min, p2_max)，
                  r_ref = 工作分辨率高度 / 6。远小圆自动放宽阈值、近大圆
                  自动收紧（论文 Fig.8 干净动态集里朴素 Hough 恰是在"门的
                  像素尺寸变大后"失效的，此律即针对该失效模式）
    ROI 掩码      取 Canny 边缘图再膨胀 2px（容忍圆定位误差的容差带）
    输出稳定化    被接受的逐帧检测进 buffer，对外输出 = buffer 中位数
                  （论文只说滑窗"stabilize the estimates"，未给聚合方式）

依赖：numpy + opencv（Isaac Sim 自带 Python 4.11 / 系统 Python 4.13 均可）。
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


# ============================================================
# 数据结构
# ============================================================

@dataclass
class GateDetection:
    """一次门检测结果（全分辨率像素坐标）。

    u, v   : 圆心像素坐标
    r      : 圆半径（像素）
    fit    : 评估器匹配分 ∈ [0,1]（buffer 中位数输出时取最优帧的 fit）
    valid  : 本帧是否给出了有效检测（滑窗沿用帧也视为 valid=True，
             因为它承载的是最近一次有效估计 —— 与论文"沿用上一帧"一致）
    latency_ms : 本帧算法耗时（含沿用帧的近零耗时，统计 FPS 时有意义）
    """

    u: float = 0.0
    v: float = 0.0
    r: float = 0.0
    fit: float = 0.0
    valid: bool = False
    latency_ms: float = 0.0

    def normalized_errors(self, cx: float, cy: float,
                          w: int, h: int) -> Tuple[float, float, float]:
        """论文式(18)：归一化像素误差 e_u, e_v 与归一化距离误差 e_d。"""
        e_u = (self.u - cx) / w
        e_v = (self.v - cy) / h
        e_d = 1.0 - 2.0 * self.r / np.sqrt(float(w * h))
        return e_u, e_v, e_d


@dataclass
class PreprocessConfig:
    """预处理与检测全部可调参数（默认 = 论文值 + 本实现选定值）。"""

    # —— 论文原值 ——
    param1: float = 30.0            # Canny 高阈值（Hough 内部用）
    param2_0: float = 55.0          # 累加器阈值初值
    fit_degree: float = 0.8         # 接受为最终结果的匹配分阈值
    min_fit_degree: float = 0.5     # 候选保留下限
    max_fit_num: int = 3            # 每帧保留候选数上限
    period: int = 3                 # 每 N 帧评估一帧
    capacity: int = 7               # 滑窗缓冲容量

    # —— 本实现选定（论文未给） ——
    downsample: int = 2             # 工作分辨率 = 全分辨率 / 此值
    min_radius: int = 5             # 工作分辨率下最小半径（px）
    max_radius: int = 130           # 工作分辨率下最大半径（px）
    min_dist: int = 24              # Hough 圆心最小间距（px）
    gauss_ksize: int = 5            # 高斯核
    morph_ksize: int = 3            # 形态学核（开运算去噪点）
    roi_dilate: int = 2             # ROI 掩码膨胀像素（匹配容差带）
    circle_thickness: int = 1       # 评估器画候选圆的线宽
    p2_min: float = 18.0            # param2 动态下限
    p2_max: float = 110.0           # param2 动态上限
    r_ref: Optional[float] = None   # param2 比例律参考半径；None = h_work/6
    # 散度门（时序一致性硬门）：实采杂乱集中柱边+地平线可拼出 fit≈1.0
    # 的假圆，纯 fit 排序会被劫持。候选与滑窗中位估计的圆心距超过
    # gate_dist_sigma·r̄ 一律拒收（不进缓冲，保持上一帧输出），连续
    # reject_flush 帧被拒则冲刷缓冲+重置 param2 重新捕获。
    gate_dist_sigma: float = 1.5
    reject_flush: int = 5


# ============================================================
# 检测器
# ============================================================

class AdaptiveHoughDetector:
    """SparrowHawk 自适应 Hough 门检测器（时序有状态，逐帧喂 BGR 图）。"""

    def __init__(self, cfg: Optional[PreprocessConfig] = None):
        self.cfg = cfg or PreprocessConfig()
        self._param2 = self.cfg.param2_0
        self._buffer: deque = deque(maxlen=self.cfg.capacity)
        self._frame_idx = 0
        self._last: Optional[GateDetection] = None
        self._r_ref_cache: Optional[float] = self.cfg.r_ref
        self._work_shape: Optional[Tuple[int, int]] = None
        self._reject_streak = 0
        # 调试/可视化信息（最近一次 process 的中间量）
        self.debug: Dict[str, np.ndarray] = {}

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------
    def process(self, bgr: np.ndarray) -> GateDetection:
        """喂一帧 BGR 图，返回检测。内部按 period 跳帧评估。"""
        t0 = time.perf_counter()
        self._frame_idx += 1

        if self._last is not None and self._frame_idx % self.cfg.period != 0:
            # 非评估帧：沿用上一帧结果（论文：保证时序稳定 + 降算力）
            out = GateDetection(self._last.u, self._last.v, self._last.r,
                                self._last.fit, valid=True,
                                latency_ms=(time.perf_counter() - t0) * 1e3)
            return out

        gray_work, roi_mask, scale = self._preprocess(bgr)

        circles = self._hough_circles(gray_work)
        scored = self._evaluate(circles, roi_mask)

        if scored:
            best = self._temporally_rerank(scored)
            if self._buffer and not self._within_gate(best[0]):
                # 散度门拦截：候选离稳定跟踪估计太远 → 视为杂波，拒收
                self._reject_streak += 1
                if self._reject_streak >= self.cfg.reject_flush:
                    self._buffer.clear()
                    self._param2 = self.cfg.param2_0
                    self._reject_streak = 0
                out = self._hold(t0)
                self.debug["roi"] = roi_mask
                self.debug["gray"] = gray_work
                return out
            self._reject_streak = 0
            if best_fit(best) >= self.cfg.fit_degree:
                det = GateDetection(u=best[0][0] * scale, v=best[0][1] * scale,
                                    r=best[0][2] * scale, fit=best_fit(best),
                                    valid=True,
                                    latency_ms=(time.perf_counter() - t0) * 1e3)
                self._buffer.append((det.u, det.v, det.r))
                self._last = det
                # param2 随半径调整（比例律见模块 docstring）
                self._update_param2(det.r / scale)   # 用工作分辨率半径
                out = self._median_output(det, t0)
            else:
                # 有候选但不够好：沿用上一帧，不动 param2（论文"其余情况"分支）
                out = self._hold(t0)
        else:
            # 完全无候选：重置 param2（论文明确：重新初始化）
            self._param2 = self.cfg.param2_0
            out = self._hold(t0)

        self.debug["roi"] = roi_mask
        self.debug["gray"] = gray_work
        self.debug["candidates"] = circles if circles is not None else np.empty((0, 3))
        return out

    # ------------------------------------------------------------
    # 预处理：降采样 → 灰度 → 高斯 → 形态学（开） → Canny(+膨胀=ROI)
    # ------------------------------------------------------------
    def _preprocess(self, bgr: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        cfg = self.cfg
        h, w = bgr.shape[:2]
        scale = float(cfg.downsample)
        work = cv2.resize(bgr, (int(w / scale), int(h / scale)),
                          interpolation=cv2.INTER_AREA) if cfg.downsample > 1 else bgr
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (cfg.gauss_ksize, cfg.gauss_ksize), 0)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT,
                                           (cfg.morph_ksize, cfg.morph_ksize))
        gray = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)
        self._work_shape = gray.shape                # r_ref 用真实工作分辨率
        # Canny：高阈值 = param1（与 Hough 内部一致），低 = param1/2（OpenCV 惯例）
        edges = cv2.Canny(gray, cfg.param1, max(1.0, cfg.param1 / 2.0))
        if cfg.roi_dilate > 0:
            k = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (2 * cfg.roi_dilate + 1, 2 * cfg.roi_dilate + 1))
            roi = cv2.dilate(edges, k)
        else:
            roi = edges
        return gray, roi, scale

    # ------------------------------------------------------------
    # Hough 圆（在平滑灰度图上跑，param1 控制其内部 Canny）
    # ------------------------------------------------------------
    def _hough_circles(self, gray: np.ndarray) -> Optional[np.ndarray]:
        cfg = self.cfg
        try:
            circles = cv2.HoughCircles(
                gray, cv2.HOUGH_GRADIENT, dp=1,
                minDist=cfg.min_dist,
                param1=cfg.param1, param2=self._param2,
                minRadius=cfg.min_radius, maxRadius=cfg.max_radius,
            )
        except cv2.error:
            return None
        if circles is None or len(circles) == 0:
            return None
        return circles[0]          # shape (N, 3)

    # ------------------------------------------------------------
    # 评估器 E：候选圆 vs ROI 掩码，重叠像素 / 完整圆周像素 → 匹配分
    # ------------------------------------------------------------
    def _evaluate(self, circles: Optional[np.ndarray],
                  roi: np.ndarray) -> List[Tuple[Tuple[float, float, float], float]]:
        """返回 [( (u,v,r), score ), ...]，按分数降序，已过滤 min_fit_degree
        与 max_fit_num。

        归一化分母 = 完整圆周的离散像素数（在大画布上画满圆计数），
        而非图像内可见部分——否则中心贴近图像边缘的候选只画出部分弧，
        分母变小、得分虚高，杂乱场景下会锁定边缘杂物（合成自测实证）。
        """
        if circles is None:
            return []
        cfg = self.cfg
        out: List[Tuple[Tuple[float, float, float], float]] = []
        for u, v, r in circles:
            u_i, v_i, r_i = int(round(u)), int(round(v)), int(round(r))
            if r_i <= 0:
                continue
            # 完整圆周像素数（与图像无关，仅半径决定）
            full_px = self._circle_px_count(r_i)
            if full_px == 0:
                continue
            score = self._overlap_score(u_i, v_i, r_i, roi, full_px)
            if score >= cfg.min_fit_degree:
                out.append(((float(u), float(v), float(r)), float(score)))
        out.sort(key=lambda s: s[1], reverse=True)
        return out[:cfg.max_fit_num]

    def _circle_px_count(self, r_i: int, thickness: Optional[int] = None) -> int:
        """画满圆的离散像素数（大画布上计数，与图像内容无关）。"""
        th = thickness if thickness is not None else self.cfg.circle_thickness
        pad = r_i + th + 2
        full = np.zeros((2 * pad + 1, 2 * pad + 1), dtype=np.uint8)
        cv2.circle(full, (pad, pad), r_i, 255, thickness=th)
        return int(np.count_nonzero(full))

    def _overlap_score(self, u_i: int, v_i: int, r_i: int,
                       roi: np.ndarray, full_px: int) -> float:
        """候选圆画到图像内与 ROI 的重叠率（分母=完整圆周，见 _evaluate 注释）。"""
        h, w = roi.shape
        canvas = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(canvas, (u_i, v_i), r_i, 255,
                   thickness=self.cfg.circle_thickness)
        overlap = int(np.count_nonzero((canvas > 0) & (roi > 0)))
        return overlap / full_px

    # ------------------------------------------------------------
    # 时序一致性重排 + 散度门
    # ------------------------------------------------------------
    def _temporally_rerank(
            self, scored: List[Tuple[Tuple[float, float, float], float]]
    ) -> Tuple[Tuple[float, float, float], float]:
        """按 fit × 距离衰减重排候选；历史不足时退回纯 fit 最优。

        （论文滑窗"stabilize the estimates"的具体化。）
        """
        if len(self._buffer) < 2:
            return scored[0]
        med = np.median(np.asarray(self._buffer, dtype=float), axis=0)
        sigma = max(med[2], 10.0)
        best, best_key = None, -1.0
        for cand, fit in scored:
            d2 = (cand[0] - med[0]) ** 2 + (cand[1] - med[1]) ** 2
            key = fit * math.exp(-d2 / (2.0 * sigma * sigma))
            if key > best_key:
                best, best_key = (cand, fit), key
        return best

    def _within_gate(self, cand: Tuple[float, float, float]) -> bool:
        """候选与滑窗中位估计的圆心距 ≤ gate_dist_sigma·r̄（散度门）。"""
        med = np.median(np.asarray(self._buffer, dtype=float), axis=0)
        d = math.hypot(cand[0] - med[0], cand[1] - med[1])
        return d <= self.cfg.gate_dist_sigma * max(med[2], 10.0)

    # ------------------------------------------------------------
    # param2_update：比例律 + 限幅
    # ------------------------------------------------------------
    def _update_param2(self, r_work: float) -> None:
        cfg = self.cfg
        if self._r_ref_cache is None:
            h_work = float(self._work_shape[0]) if self._work_shape else 240.0
            self._r_ref_cache = h_work / 6.0
        r_ref = max(1.0, self._r_ref_cache)
        self._param2 = float(np.clip(cfg.param2_0 * r_work / r_ref,
                                     cfg.p2_min, cfg.p2_max))

    # ------------------------------------------------------------
    # 输出聚合与沿用
    # ------------------------------------------------------------
    def _median_output(self, frame_det: GateDetection, t0: float) -> GateDetection:
        """滑窗中位数输出（时序稳定化），fit 沿用本帧最优值。"""
        if not self._buffer:
            frame_det.latency_ms = (time.perf_counter() - t0) * 1e3
            return frame_det
        arr = np.asarray(self._buffer, dtype=float)
        med = np.median(arr, axis=0)
        return GateDetection(u=float(med[0]), v=float(med[1]), r=float(med[2]),
                             fit=frame_det.fit, valid=True,
                             latency_ms=(time.perf_counter() - t0) * 1e3)

    def _hold(self, t0: float) -> GateDetection:
        """沿用上一帧结果；历史无检测则返回无效帧。"""
        if self._last is not None:
            return GateDetection(self._last.u, self._last.v, self._last.r,
                                 self._last.fit, valid=True,
                                 latency_ms=(time.perf_counter() - t0) * 1e3)
        return GateDetection(valid=False,
                             latency_ms=(time.perf_counter() - t0) * 1e3)

    # ------------------------------------------------------------
    @property
    def param2(self) -> float:
        return self._param2

    def reset(self) -> None:
        self._param2 = self.cfg.param2_0
        self._buffer.clear()
        self._frame_idx = 0
        self._last = None


def best_fit(item: Tuple[Tuple[float, float, float], float]) -> float:
    return item[1]
