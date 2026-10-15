# -*- coding: utf-8 -*-
"""
ident_pipeline_v2.py — 估计器第二版：间接法（2026-09-30）

第一版教训（v1 留档）：闭环激励数据直接拟合开环模型，测得的是闭
环灵敏度函数。本版按间接法反解：注入 p 叠加在舵指令上，闭环
T = G/(1+G·C)，故 G = T/(1−T·C)。注入信号由 multisine 确定函数离
线重建（幅值、频率表、施罗德相位与控制器实现一致），控制器传函
C 按当跑生效配置组装（内环速率反馈+速率滤波，当跑无超前网络与
执行低通，交叉项暂按对角近似并标注混叠）。

判据沿用 docs/辨识产线_判据与方案_20260930.md（未改）。

用法：python tools/ident_pipeline_v2.py <csv路径> [幅值]
产物：logs/ident_matrix/v2_<时间戳>.json
"""
import os
import sys
import csv
import json
import math
import datetime

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FREQS = [0.2, 0.5, 1.0, 1.5, 2.33, 3.0, 5.0, 8.0]
CH = {"ail": "wx", "elev": "wy", "rud": "wz"}
CH_IDX = {"ail": 0, "elev": 1, "rud": 2}
AMP_DEFAULT = 0.04
# 当跑控制器（R92 配置）：KP_R=0.25, ROLL_DAMP=0.45, PITCH_DAMP=0.3,
# YAW_DAMP=2.5, RATE_FILT_TAU=0.03, 舵效调度 b=v_eff²·k（k 用侧滑档案
# 标定），v_eff≈4.05
KP_R, RATE_TAU = 0.25, 0.03
DAMP = {"ail": 0.45, "elev": 0.3, "rud": 2.5}
K_TAIL = {"ail": 0.00483, "elev": -0.0145, "rud": -0.0145}
V_EFF = 4.05


def multisine(ch, t):
    n = len(FREQS)
    ci = CH_IDX[ch]
    s = 0.0
    for i, fq in enumerate(FREQS):
        ph = math.pi * (i * (i + 1) // 2) / n + ci * 0.7
        s += math.sin(2 * math.pi * fq * t + ph)
    return s / (0.5 * n)


def load(csv_path, t0, t1):
    rows = list(csv.DictReader(open(csv_path)))
    t = np.array([float(r["t"]) for r in rows])
    sel = (t >= t0) & (t <= t1)
    d = {"t": t[sel]}
    for k in ("ail", "elev", "rud", "wx", "wy", "wz"):
        d[k] = np.array([float(r[k]) for r in rows])[sel]
    d["dt"] = float(np.median(np.diff(d["t"])))
    return d


def proj(x, t, f):
    w = np.hanning(len(x))
    return np.sum((x - x.mean()) * w * np.exp(-1j * 2 * math.pi * f * t))


def ctrl_tf(ch, s):
    """控制器 wx→舵偏 传函（符号：负反馈，e=−y）。"""
    b = K_TAIL[ch] * V_EFF ** 2
    return -KP_R * DAMP[ch] / (1.0 + RATE_TAU * s) / b


def main(csv_path, amp=AMP_DEFAULT):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    d = load(csv_path, 6.0, 16.0)
    t = d["t"]
    print("样本 %d 时长 %.1fs 注入幅值 %.3f"
          % (len(t), t[-1] - t[0], amp))
    results = {}
    print("\n间接法 FRF（对象开环）与拟合：")
    for ch, yk in CH.items():
        p = np.array([amp * multisine(ch, tt) for tt in t])
        y = d[yk]
        pts = []
        for fq in FREQS:
            P = proj(p, t, fq)
            Y = proj(y, t, fq)
            if abs(P) < 1e-12:
                continue
            T = Y / P
            s = 1j * 2 * math.pi * fq
            C = ctrl_tf(ch, s)
            G = T / (1.0 - T * C)
            pts.append((fq, G, T, C))
        # 拟合 G(jw)=K·e^{−jwτ}/((1+jwT)·jw)
        ws = np.array([2 * math.pi * p0[0] for p0 in pts])
        Gm = np.array([p0[1] for p0 in pts])
        ph0 = np.angle(Gm[0])
        sgn = 1.0 if abs(ph0 + math.pi / 2) < abs(ph0 - math.pi / 2) else -1.0

        def model(K, tau, T_):
            return K * np.exp(-1j * ws * tau) / ((1 + 1j * ws * T_) * 1j * ws)

        best = None
        for K in sgn * 10 ** np.arange(-3, 1.01, 0.25):
            for tau in np.arange(0.0, 0.401, 0.02):
                for Tl in np.arange(0.005, 0.301, 0.01):
                    err = np.sum(np.abs(Gm - model(K, tau, Tl)) ** 2)
                    if best is None or err < best[0]:
                        best = (err, K, tau, Tl)
        err, K, tau, Tl = best
        for _ in range(3):
            for K2 in K * 10 ** np.linspace(-0.2, 0.2, 9):
                for tau2 in tau + np.linspace(-0.01, 0.01, 9):
                    if tau2 < 0:
                        continue
                    for T2 in Tl + np.linspace(-0.005, 0.005, 9):
                        if T2 <= 0:
                            continue
                        e2 = np.sum(np.abs(Gm - model(K2, tau2, T2)) ** 2)
                        if e2 < err:
                            err, K, tau, Tl = e2, K2, tau2, T2
        rel = float(err / max(np.sum(np.abs(Gm) ** 2), 1e-18))
        # 特征根：1+L=0, L=K(1-sτ/2)/[(1+sτ/2)(1+sTl)s]
        num = np.array([-K * tau / 2, K])
        den = np.polymul(np.array([tau / 2, 1]),
                         np.polymul(np.array([Tl, 1]), [1, 0]))
        den = np.polyadd(den, num)
        roots = np.roots(den)
        print("%s→%s K=%+.4g τ=%.3fs T=%.3fs 残差%.2f %s"
              % (ch, yk, float(K), float(tau), float(Tl), rel,
                 "低置信" if rel > 0.5 else ""))
        for r in sorted(roots, key=lambda q: -q.real):
            print("   根 s=%+.2f%+.2fj  %.2fHz ζ=%+.2f"
                  % (r.real, r.imag, abs(r.imag) / 2 / math.pi,
                     -r.real / max(abs(r), 1e-9)))
        results[ch] = dict(K=float(K), tau=float(tau), T=float(Tl),
                           rel_err=rel,
                           frf=[[float(fq), float(abs(G)), float(np.angle(G))]
                                for fq, G, _, _ in pts],
                           roots=[[float(r.real), float(r.imag)]
                                  for r in roots])
    # 留出验证（时域重构）
    print("\n留出验证：")
    val = {}
    for ch, yk in CH.items():
        p = np.array([amp * multisine(ch, tt) for tt in t])
        y = d[yk] - d[yk].mean()
        r = results[ch]
        fr = np.fft.rfftfreq(len(p), d["dt"])
        ws2 = 2 * math.pi * fr
        P2 = np.fft.rfft(p * np.hanning(len(p)))
        with np.errstate(divide="ignore", invalid="ignore"):
            G2 = (r["K"] * np.exp(-1j * ws2 * r["tau"])
                  / ((1 + 1j * ws2 * r["T"]) * 1j * ws2))
            C2 = ctrl_tf(ch, 1j * ws2)
            T2 = G2 / (1 + G2 * C2)
        pred = np.fft.irfft(P2 * T2, len(p))
        ratio = float(np.var(y - pred) / max(np.var(y), 1e-18))
        val[ch] = dict(err_ratio=ratio, passed=bool(ratio <= 0.5))
        print("  %s: %s 误差比=%.2f" % (ch, "过" if ratio <= 0.5 else
                                        "不过", ratio))
    path = os.path.join(PROJECT_ROOT, "logs", "ident_matrix",
                        "v2_%s.json" % ts)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(ts=ts, csv=os.path.basename(csv_path), method="indirect",
                       results=results, validation=val),
                  f, ensure_ascii=False, indent=1)
    print("\n产物:", path)
    return 0


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else sorted(
        __import__("glob").glob(os.path.join(
            PROJECT_ROOT, "logs", "ident_stream_flight_*.csv")))[-1]
    amp = float(sys.argv[2]) if len(sys.argv) > 2 else AMP_DEFAULT
    sys.exit(main(a, amp))
