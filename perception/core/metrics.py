# -*- coding: utf-8 -*-
"""
metrics.py — 论文 §VI.B 评测指标（Table II 口径）

CPE_t = ||Ĉ_t − C_GT||_2                         （圆心瞬时误差，像素）
CPE RMSE = sqrt(mean(CPE_t²))                    （序列抖动/精度）
附加：有效率（valid 帧占比）、半径偏差、检测帧率、单帧耗时。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from .detector import GateDetection


def cpe(pred: GateDetection, gt_u: float, gt_v: float) -> float:
    """圆心位置误差（像素）。无效检测返回 NaN（不计入 RMSE）。"""
    if not pred.valid:
        return float("nan")
    return float(np.hypot(pred.u - gt_u, pred.v - gt_v))


def evaluate_sequence(preds: Sequence[GateDetection],
                      gts: Sequence[Dict],
                      img_w: int, img_h: int) -> Dict[str, float]:
    """一个序列的完整指标。

    preds / gts 等长逐帧对齐；gts 每项含 u, v, r（像素）。
    无效帧不计入 CPE（论文的对照实验里检测器每帧都有输出，
    这里对"无输出"单独统计 valid_rate，信息更诚实）。
    """
    assert len(preds) == len(gts)
    cpes: List[float] = []
    r_errs: List[float] = []
    n_valid = 0
    for p, g in zip(preds, gts):
        if p.valid:
            n_valid += 1
            cpes.append(cpe(p, g["u"], g["v"]))
            r_errs.append(abs(p.r - g["r"]))
    out: Dict[str, float] = {
        "frames": float(len(preds)),
        "valid_rate": n_valid / max(1, len(preds)),
        "latency_ms_mean": float(np.mean([p.latency_ms for p in preds])) if preds else 0.0,
        "latency_ms_p95": float(np.percentile([p.latency_ms for p in preds], 95)) if preds else 0.0,
        "cpe_rmse_px": float(np.sqrt(np.mean(np.square(cpes)))) if cpes else float("nan"),
        "cpe_mean_px": float(np.mean(cpes)) if cpes else float("nan"),
        "radius_err_mean_px": float(np.mean(r_errs)) if r_errs else float("nan"),
    }
    diag = float(np.hypot(img_w, img_h))
    if cpes and not np.isnan(out["cpe_rmse_px"]):
        out["cpe_rmse_norm"] = out["cpe_rmse_px"] / diag
    else:
        out["cpe_rmse_norm"] = float("nan")
    return out


def format_table(rows: Dict[str, Dict[str, float]]) -> str:
    """把 {方法名: 指标dict} 排成 Markdown 表（Table II 风格）。"""
    cols = ["cpe_rmse_px", "cpe_mean_px", "radius_err_mean_px",
            "valid_rate", "latency_ms_mean", "latency_ms_p95"]
    header = "| method | " + " | ".join(cols) + " |"
    sep = "|---" * (len(cols) + 1) + "|"
    lines = [header, sep]
    for name, m in rows.items():
        vals = []
        for c in cols:
            v = m.get(c, float("nan"))
            vals.append("N/A" if (isinstance(v, float) and np.isnan(v))
                        else (f"{v:.3f}" if v < 10 else f"{v:.1f}"))
        lines.append(f"| {name} | " + " | ".join(vals) + " |")
    return "\n".join(lines)
