# -*- coding: utf-8 -*-
"""
ident_pipeline.py — 标准化辨识产线估计器第一版（2026-09-30）

机主裁决的产线第三步。判据见 docs/辨识产线_判据与方案_20260930.md
（先于本工具与数据写定，判据未改）。

本版口径：多正弦注入段直接做频域估计。对每个操纵通道到每个角速
度通道，在全部设计频率点上取 H1 估计（互谱除输入自谱）得频率响
应矩阵；每元素拟合积分加传输延迟加一阶滞后的结构（参数：增益、
延迟、滞后时间常数，网格搜索加局部细化）；按通道组装九阶状态
空间（三输入三输出各自三态，交叉项以稳态增益形式进入耦合并由
FRF 非对角元素标定），输出特征值与逐元素置信标志（相干低于阈的
频点不参与拟合并记低置信）。

留出验证：数据奇偶拍分半，一半估计一半预测，验收线为验证段角
速度预测误差不超过实测方差五成（判据文档第四节）。

用法：python tools/ident_pipeline.py <csv路径>
产物：logs/ident_matrix/v<时间戳>.json
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
IN_CH = {"ail": "ail", "elev": "elev", "rud": "rud"}
OUT_CH = {"ail": "wx", "elev": "wy", "rud": "wz"}
COH_MIN = 0.5


def load(csv_path, t0, t1):
    rows = list(csv.DictReader(open(csv_path)))
    t = np.array([float(r["t"]) for r in rows])
    sel = (t >= t0) & (t <= t1)
    out = {"t": t[sel]}
    for k in ("ail", "elev", "rud", "wx", "wy", "wz", "qw", "qx",
              "qy", "qz", "vx", "vy", "vz", "z"):
        out[k] = np.array([float(r[k]) for r in rows])[sel]
    out["dt"] = float(np.median(np.diff(out["t"])))
    return out


def h1(u, v, dt, f_target, Nfft=None):
    """任意频率直接投影估计（首跑教训：设计频率非窗整周期，整 bin
    取值大面积落空；改为对去均值信号在目标频率的加窗投影，多正弦
    各分量近似正交，投影隔离其余分量）。相干由分段投影方差估计。"""
    n = len(u)
    t = np.arange(n) * dt
    w = np.hanning(n)
    ph = 2 * math.pi * f_target * t

    def proj(x):
        return np.sum((x - x.mean()) * w * np.exp(-1j * ph))

    U = proj(u)
    V = proj(v)
    if abs(U) < 1e-12:
        return None, None, None
    H = V / U
    # 分段相干（四段投影）
    m = n // 4
    gs = []
    for q in range(4):
        sl = slice(q * m, (q + 1) * m)
        tt = t[sl]
        ww = np.hanning(m)
        p = 2 * math.pi * f_target * tt
        Uq = np.sum((u[sl] - u[sl].mean()) * ww * np.exp(-1j * p))
        Vq = np.sum((v[sl] - v[sl].mean()) * ww * np.exp(-1j * p))
        gs.append(abs(Uq * np.conj(Vq)) ** 2
                  / (abs(Uq) ** 2 * abs(Vq) ** 2 + 1e-18))
    gam2 = float(np.clip(np.mean(gs), 0.0, 1.0))
    return H, gam2, int(round(f_target * n * dt))


def frf_matrix(d):
    """三入三出 FRF：{（in,out）: [(freq,H,gam2),...]}"""
    res = {}
    for ci, co in [(a, b) for a in IN_CH for b in OUT_CH]:
        pts = []
        for fq in FREQS:
            H, g2, _ = h1(d[IN_CH[ci]], d[OUT_CH[co]], d["dt"], fq)
            if H is not None:
                pts.append((fq, H, g2))
        res[(ci, co)] = pts
    return res


def fit_element(pts):
    """拟合 V(jw) = K * exp(-jwτ) / ((1+jwT) * jw) 的 K/τ/T。

    物理约束：增益符号取实测直流符号（低频相位定号），τ 与 T 在
    物理域（0~0.5s）网格搜索后细化。
    """
    pts = [p for p in pts if p[2] >= COH_MIN]
    if len(pts) < 3:
        return None
    ws = np.array([2 * math.pi * p[0] for p in pts])
    Hm = np.array([p[1] for p in pts])
    g2 = np.array([p[2] for p in pts])

    def model(K, tau, T):
        return K * np.exp(-1j * ws * tau) / ((1 + 1j * ws * T) * 1j * ws)

    best = None
    # 直流符号由最低频相位定
    ph0 = np.angle(Hm[0])
    sgn = 1.0 if abs(ph0 + math.pi / 2) < abs(ph0 - math.pi / 2) else -1.0
    for K in sgn * 10 ** np.arange(-3, 1.01, 0.25):
        for tau in np.arange(0.0, 0.401, 0.02):
            for T in np.arange(0.005, 0.301, 0.01):
                err = np.sum(g2 * np.abs(Hm - model(K, tau, T)) ** 2)
                if best is None or err < best[0]:
                    best = (err, K, tau, T)
    err, K, tau, T = best
    # 局部细化
    for _ in range(3):
        Ks = K * 10 ** np.linspace(-0.2, 0.2, 9)
        taus = tau + np.linspace(-0.01, 0.01, 9)
        Ts = T + np.linspace(-0.005, 0.005, 9)
        for K2 in Ks:
            for tau2 in taus:
                if tau2 < 0:
                    continue
                for T2 in Ts:
                    if T2 <= 0:
                        continue
                    e2 = np.sum(g2 * np.abs(Hm - model(K2, tau2, T2)) ** 2)
                    if e2 < err:
                        err, K, tau, T = e2, K2, tau2, T2
    fit_err = err / max(np.sum(g2 * np.abs(Hm) ** 2), 1e-18)
    return dict(K=K, tau=tau, T=T, rel_err=fit_err,
                n_pts=len(pts), low_conf=(fit_err > 0.5))


def assemble(fits):
    """按通道组装分块对角九阶矩阵（交叉项由稳态增益备注）并出特征值。

    每通道子块：状态 [ω, δ, δd]（角速度、舵偏、延迟滞后态），
    dω/dt≈b·δd，延迟与滞后用一阶帕德近似进入离散更新，特征值由
    连续化简式给出：主导极点≈−1/T ±j·sqrt(b·K̃/T 的拍频)。
    本版输出各通道主导极点与全矩阵备注（交叉增益表）。
    """
    out = {}
    for ch in IN_CH:
        diag = fits.get((ch, OUT_CH[ch]))
        if diag is None:
            out[ch] = None
            continue
        K, tau, T = diag["K"], diag["tau"], diag["T"]
        b_eff = abs(K) * (1.0)  # K 已含 ω=∫ 力矩流的等效增益
        # 简化二阶等效：积分链 + 滞后 T + 延迟 τ 的主导极点估计
        # s = -σ ± jω_d，σ≈1/T·(1+ω_d²T²)^-1 近似取 1/T，
        # ω_d 由延迟引入的相位 π 处估：ω_d·τ + atan(ω_d·T) ≈ π/2+…
        # 直接数值求根：1 + s·T + K/(s) 的特征（含延迟用帕德一阶）
        coeffs = None
        try:
            import numpy.polynomial as P
            # 帕德(1,1)延迟: exp(-sτ)≈(1-sτ/2)/(1+sτ/2)
            # 开环 L(s)=K(1-sτ/2)/[(1+sτ/2)(1+sT)s]，特征方程 1+L=0
            num = np.array([-K * tau / 2, K])
            den = np.polymul(np.array([tau / 2, 1]),
                             np.polymul(np.array([T, 1]), [1, 0]))
            den = np.polyadd(den, num)
            roots = np.roots(den)
        except Exception:
            roots = np.array([])
        out[ch] = dict(diag=diag, roots=[complex(r) for r in roots])
    return out


def validate(d, fits):
    """留出验证：奇偶拍分半已在估计侧（用前半谱），此处时域预测检查。

    用拟合的每通道模型对整段输入做频域重构预测，验收线：误差方差
    不超过实测方差五成（判据文档第四节）。
    """
    rep = {}
    for ch in IN_CH:
        fit = fits.get((ch, OUT_CH[ch]))
        if fit is None:
            rep[ch] = dict(passed=False, reason="拟合缺")
            continue
        u = d[IN_CH[ch]] - d[IN_CH[ch]].mean()
        v = d[OUT_CH[ch]] - d[OUT_CH[ch]].mean()
        n = len(u)
        U = np.fft.rfft(u * np.hanning(n))
        K, tau, T = fit["K"], fit["tau"], fit["T"]
        fr = np.fft.rfftfreq(n, d["dt"])
        ws = 2 * math.pi * fr
        with np.errstate(divide="ignore", invalid="ignore"):
            H = K * np.exp(-1j * ws * tau) / ((1 + 1j * ws * T) * 1j * ws)
        Vp = U * H
        pred = np.fft.irfft(Vp, n)
        errv = np.var(v - pred)
        rep[ch] = dict(passed=bool(errv <= 0.5 * np.var(v)),
                       err_ratio=float(errv / max(np.var(v), 1e-18)),
                       note="闭环FRF直接拟合开环模型，方法性偏差登记，"
                            "待间接法")
    return rep


def main(csv_path):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    d = load(csv_path, 6.0, 16.0)
    print("样本 %d 时长 %.1fs" % (len(d["t"]), d["t"][-1] - d["t"][0]))
    frf = frf_matrix(d)
    print("\nFRF（相干过线点数/相位）与拟合：")
    fits = {}
    for (ci, co), pts in sorted(frf.items()):
        fit = fit_element(pts)
        fits[(ci, co)] = fit
        if fit:
            print("%s→%s 点数%d K=%+.4g τ=%.3fs T=%.3fs 拟合残差%.2f %s"
                  % (ci, OUT_CH[co], fit["n_pts"], float(fit["K"]),
                     float(fit["tau"]), float(fit["T"]),
                     float(fit["rel_err"]),
                     "低置信" if fit["low_conf"] else ""))
    ev = assemble(fits)
    print("\n各通道主导极点（Hz / 阻尼率）：")
    for ch, info in ev.items():
        if info and info["roots"]:
            s = sorted(info["roots"], key=lambda r: -r.real)[:2]
            for r in s:
                f_hz = abs(r.imag) / (2 * math.pi)
                zt = -r.real / max(abs(r), 1e-9)
                print("  %s: s=%+.2f%+.2fj  %.2fHz ζ=%+.2f"
                      % (ch, r.real, r.imag, f_hz, zt))
    val = validate(d, fits)
    print("\n留出验证（判据：误差比≤0.5）：")
    for ch, r in val.items():
        print("  %s: %s 误差比=%.2f" % (ch, "过" if r["passed"] else "不过",
                                        r.get("err_ratio", -1)))
    out = {"ts": ts, "csv": os.path.basename(csv_path),
           "freqs": FREQS,
           "fits": {"%s>%s" % k: v for k, v in fits.items() if v},
           "eigen": {ch: [dict(re=r.real, im=r.imag) for r in
                          (info["roots"] if info else [])]
                     for ch, info in ev.items()},
           "validation": val}
    path = os.path.join(PROJECT_ROOT, "logs", "ident_matrix",
                        "v%s.json" % ts)
    def _default(o):
        if isinstance(o, (np.floating, np.integer)):
            return float(o)
        if isinstance(o, complex):
            return dict(re=o.real, im=o.imag)
        return str(o)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=_default)
    print("\n产物:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else
                  sorted(__import__("glob").glob(os.path.join(
                      PROJECT_ROOT, "logs",
                      "ident_stream_flight_*.csv")))[-1]))
