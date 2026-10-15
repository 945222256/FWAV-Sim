#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
vmomentum_audit.py — 垂直动量账残差归因（任务二，只读数据）
================================================================
账本定义（世界系 z 向）:
  r(t) = m·a_z(t) − [Fz_applied(t) − m·g]
  a_z 由 vz 中心差分（逐样本 t）；Fz_applied 取记录列 Fz（钳位后总施加力）。
  m 由悬停 Fz 中位数自动判别口径（<4N → 215g 平台，否则 700g 存档）。

门控: z>0.3m 且 pin==0 的连续空中段（逐段差分，不跨段）。

方法:
  A 冲量式对账（对差分噪声免疫的判别器）:
    段内 ∫(Fz−mg)dt 与 m·Δvz 对账 —— 若差分式残差 ~1N 而冲量式闭合,
    则残差属"估算伪影"而非真实未登记力。
  B 平滑窗宽敏感性: r 的 0.1/0.25/0.5/1/2s 滑窗均值。
  C 飞行阶段分段: 按爬升(vz>0.3)/下降(vz<−0.3)/平飞(|vz|≤0.3)分段重算。
  D 带阻滤波: 对 vz 与 Fz 各做拍频 f1 与 2f1 的 FFT 带阻后重算账。
  E 翼面垂直惯性力估算（真实力候选 H2）:
    z_i = y_i·sin(q_i)  →  F_wing,z = Σ m_i·y_i·(cos q·q̈ − sin q·q̇²)
    q 取记录关节角（flapLa/flapRa），质量/半径取 MASS_TABLE+URDF 连杆 CG。
    与残差均值直接对量。
用法:
  python tools/vmomentum_audit.py <csv> [--deep]
"""
from __future__ import annotations
import argparse, os, sys
import numpy as np
import pandas as pd

G = 9.81
# URDF 连杆 CG 半径（y 向，含关节链偏移；运动学常数，源 fwav URDF 关节原点）
# 质量自参数登记库读取（I-002 施工 2026-09-30，禁抄写数字）
import os as _os
sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))), "params_registry"))
import registry as _registry
WING_PARTS = [  # (名称, 质量kg, |y_CG|m)
    ("flapconn", _registry.get("vehicle.mass.link.flapconn_L"), 0.07),
    ("innerwing", _registry.get("vehicle.mass.link.innerwing_L"), 0.15),
    ("outerwing", _registry.get("vehicle.mass.link.outerwing_L"), 0.32),
]


def load_segments(path):
    df = pd.read_csv(path)
    t = df["t"].values
    z = df["z"].values
    pin = (df["pin"].values == 1) if "pin" in df.columns \
        else np.zeros(len(df), bool)
    air = (z > 0.3) & (~pin)
    segs, i, n = [], 0, len(t)
    while i < n:
        if air[i]:
            j = i
            while j + 1 < n and air[j + 1]:
                j += 1
            if j - i > 60:
                segs.append((i, j))
            i = j + 1
        else:
            i += 1
    return df, segs


def detect_mass(df):
    fz = df["Fz"].values
    med = float(np.median(fz))
    return (0.215, med) if med < 4.0 else (0.700, med)


def central_diff(t, x):
    """逐样本中心差分（内部点），返回 (t_inner, dxdt)。"""
    return (t[1:-1], (x[2:] - x[:-2]) / (t[2:] - t[:-2]))


def smooth(x, fs, win_s):
    w = max(int(round(win_s * fs)), 1)
    if len(x) < w:
        return x.copy()
    c = np.concatenate([[0.0], np.cumsum(x)])
    return (c[w:] - c[:-w]) / w


def fft_notch(x, fs, freqs, halfwidth):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / fs)
    mask = np.ones_like(f, dtype=bool)
    for fc in freqs:
        mask &= np.abs(f - fc) > halfwidth
    X[~mask] = 0.0
    return np.fft.irfft(X, n=len(x))


def flap_freq(t, q, fs):
    ix = np.where((q[:-1] < 0) & (q[1:] >= 0))[0] + 1
    if len(ix) < 5:
        return float("nan")
    d = np.diff(t[ix])
    d = d[(d > 0.04) & (d < 2.0)]
    return 1.0 / (2.0 * np.median(d)) if len(d) >= 4 else float("nan")


def wing_inertia_force(t, qL, qR):
    """双翼垂直惯性力（整机牛顿第三定律内力，对 base_link 动量账表现为
    未登记项）。z_i = y_i·sin(q)；返回 (t_inner, F_wz)。"""
    ti, dqL = central_diff(t, qL)
    _, dqR = central_diff(t, qR)
    _, ddqL = central_diff(ti, dqL)
    ti2, ddqR = central_diff(ti, dqR)
    dqL2, dqR2 = dqL[1:-1], dqR[1:-1]
    qL2, qR2 = qL[2:-2], qR[2:-2]
    F = np.zeros(len(ti2))
    for _, m, y in WING_PARTS:
        # 左翼 y>0 关节角 qL；右翼 y<0 关节角 qR（同上反）
        FL = m * y * (np.cos(qL2) * ddqL - np.sin(qL2) * dqL2 ** 2)
        FR = m * (-y) * (np.cos(qR2) * ddqR - np.sin(qR2) * dqR2 ** 2)
        F += FL + FR
    return ti2, F


def audit(path, deep=False):
    df, segs = load_segments(path)
    if not segs:
        raise SystemExit("无空中段")
    m, fz_med = detect_mass(df)
    print(f"文件: {os.path.basename(path)}")
    print(f"质量口径 m={m}kg（悬停Fz中位 {fz_med:+.2f}N）  空中段数={len(segs)}")
    # 取最长段为主段
    i0, i1 = max(segs, key=lambda s: s[1] - s[0])
    d = df.iloc[i0:i1 + 1]
    t = d["t"].values
    vz, Fz = d["vz"].values, d["Fz"].values
    fs = 1.0 / float(np.median(np.diff(t)))
    mg = m * G
    print(f"主段: t=[{t[0]:.1f},{t[-1]:.1f}]s {t[-1]-t[0]:.1f}s  fs={fs:.1f}Hz "
          f"n={len(t)}")

    # ── 基线差分账 ──
    ti, az = central_diff(t, vz)
    Fi = Fz[1:-1]
    r = m * az - (Fi - mg)
    print(f"\n[基线差分账] 残差均值={np.nanmean(r):+.3f}N 中位={np.nanmedian(r):+.3f}N "
          f"std={np.nanstd(r):.2f}N")

    # ── A 冲量式对账（伪影判别器）──
    dt = np.diff(t)
    impulse = float(np.sum((Fz[:-1] - mg) * dt))
    dmoment = m * float(vz[-1] - vz[0])
    print(f"[A 冲量式] ∫(Fz−mg)dt={impulse:+.3f} N·s   m·Δvz={dmoment:+.3f} N·s   "
          f"缺口={impulse - dmoment:+.3f} N·s "
          f"(折合均值 {impulse - dmoment if t[-1]==t[0] else (impulse-dmoment)/(t[-1]-t[0]):+.3f} N)")

    # ── B 平滑窗宽敏感性 ──
    print("\n[B 平滑窗宽敏感性] r 的滑窗均值（坍缩=伪影信号）:")
    for w in (0.1, 0.25, 0.5, 1.0, 2.0):
        rs = smooth(r, fs, w)
        print(f"   窗宽 {w:4.2f}s: 残差均值 {np.nanmean(rs):+.3f}N  std {np.nanstd(rs):.3f}N")

    # ── C 飞行阶段分段 ──
    vzs = smooth(vz, fs, 0.5)
    ti_s = t[: len(vzs)]
    phases = [("爬升(vz>+0.3)", vzs > 0.3), ("平飞(|vz|≤0.3)", np.abs(vzs) <= 0.3),
              ("下降(vz<−0.3)", vzs < -0.3)]
    # 差分 r 对齐到 ti；用 vz 平滑版相位掩码插值
    mask_ti = np.interp(ti, ti_s, vzs.astype(float))
    print("\n[C 飞行阶段分段] 差分账残差:")
    for name, msk in phases:
        mm = (mask_ti > 0.3) if "爬升" in name else \
             (np.abs(mask_ti) <= 0.3) if "平飞" in name else (mask_ti < -0.3)
        if mm.sum() > 30:
            print(f"   {name:14s}: n={mm.sum():5d}  残差均值 {np.nanmean(r[mm]):+.3f}N")

    # ── D 带阻滤波 ──
    f1 = flap_freq(t, d["flapLt"].values, fs)
    if np.isfinite(f1):
        vz_n = fft_notch(vz - vz.mean(), fs, [f1, 2 * f1, 3 * f1], 0.6) + vz.mean()
        Fz_n = fft_notch(Fz - Fz.mean(), fs, [f1, 2 * f1, 3 * f1], 0.6) + Fz.mean()
        tn, az_n = central_diff(t, vz_n)
        r_n = m * az_n - (Fz_n[1:-1] - mg)
        print(f"\n[D 带阻滤波] 拍频 f1={f1:.2f}Hz, 阻带 f1/2f1/3f1±0.6Hz:")
        print(f"   滤波后残差均值={np.nanmean(r_n):+.3f}N  std={np.nanstd(r_n):.3f}N")

    # ── E 翼面惯性力（真实力候选）──
    if "flapLa" in d.columns and "flapRa" in d.columns:
        tw, Fw = wing_inertia_force(t, d["flapLa"].values, d["flapRa"].values)
        # 平滑后取均值（二阶差分噪声大）
        Fw_s = smooth(Fw, fs, 0.5)
        print(f"\n[E 翼面垂直惯性力估算] (URDF CG 半径 + MASS_TABLE)")
        print(f"   未平滑: 均值={np.nanmean(Fw):+.3f}N  0.5s平滑后均值={np.nanmean(Fw_s):+.3f}N")
        # 与残差的相关（对齐插值）
        r_at_w = np.interp(tw, ti, r)
        if len(Fw_s) > 50 and np.nanstd(Fw_s) > 1e-6:
            cc = np.corrcoef(r_at_w[:len(Fw_s)], Fw_s)[0, 1]
            print(f"   残差与翼面惯性力的相关系数={cc:+.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    a = ap.parse_args()
    audit(a.csv)


if __name__ == "__main__":
    main()
