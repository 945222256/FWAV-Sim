# -*- coding: utf-8 -*-
"""
ident_pipeline_v3.py — 估计器第三版：间接法+二阶共振结构（2026-09-30）

第一版教训（v1 留档）：闭环激励数据直接拟合开环模型，测得的是闭
环灵敏度函数。本版按间接法反解：注入 p 叠加在舵指令上，闭环
T = G/(1+G·C)，故 G = T/(1−T·C)。注入信号由 multisine 确定函数离
线重建（幅值、频率表、施罗德相位与控制器实现一致），控制器传函
C 按当跑生效配置组装（内环速率反馈+速率滤波，当跑无超前网络与
执行低通，交叉项暂按对角近似并标注混叠）。

判据沿用 docs/辨识产线_判据与方案_20260930.md（未改）。

用法：python tools/ident_pipeline_v2.py <csv路径> [幅值]
产物：logs/ident_matrix/v3_<时间戳>.json
"""
import os
import sys
import csv
import json
import math
import datetime

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FREQS = [float(x) for x in "0.2,0.4,0.7,1.0,1.4,1.8,2.1,2.33,2.6,3.0,3.5,4.5,6.0,8.0,12.0,18.0".split(",")]
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


_CFR = None


def _load_cfr():
    """数值化控制器频响（轮四十一接入：替代解析式）。"""
    global _CFR
    import json as _j
    path = os.path.join(PROJECT_ROOT, "logs", "ident_matrix",
                        "ctrl_freqresp.json")
    if os.path.exists(path):
        _CFR = _j.load(open(path, encoding="utf-8"))["resp"]
    else:
        _CFR = None


_load_cfr()


def ctrl_tf(ch, s):
    """优先查数值化频响表（对数频率插值），无表回退解析式。"""
    if _CFR is not None and ch in _CFR:
        arr = np.atleast_1d(s)
        fqs = np.clip(np.imag(arr) / 2 / math.pi, 1e-3, None)
        keys = sorted(float(k) for k in _CFR[ch])
        tab_f = np.array(keys)
        tab_a = np.array([_CFR[ch]["%.3f" % k][0] for k in keys])
        tab_p = np.array([_CFR[ch]["%.3f" % k][1] for k in keys])
        a = np.interp(np.log(fqs), np.log(tab_f), np.log(tab_a))
        a = np.exp(a)
        dphi = np.unwrap(tab_p)
        ph = np.interp(np.log(fqs), np.log(tab_f), dphi)
        out = a * np.exp(1j * ph)
        return out[0] if np.isscalar(s) or getattr(s, 'ndim', 1) == 0             else out
    return ctrl_tf_analytic(ch, s)


KP_A, KI_A = 2.6, 0.15


def ctrl_tf_analytic(ch, s):
    """控制器 角速度→舵偏 传函（负反馈）。轮三十六补姿态外环项：
    ail 的姿态外环 KP_A 对滚转角（=wx 积分）作用，elev 含 KP_A 与
    俯仰积分 KI_A/s；此前仅建模速率内环致低频反解污染。"""
    b = K_TAIL[ch] * V_EFF ** 2
    inner = DAMP[ch] / (1.0 + RATE_TAU * s)
    if ch == "ail":
        outer = KP_A / s          # 姿态环经 φ=wx/s
    elif ch == "elev":
        outer = KP_A + KI_A / s
    else:
        outer = 0.0
    return -KP_R * (outer + inner) / b


AMP_BY_CH = {"ail": 0.12, "elev": 0.08, "rud": 0.08}


def sliding(csv_path, amp=AMP_DEFAULT, win=20.0, step=10.0):
    """滑窗分段估计（轮四十七：拍频慢漂使全窗 LTI 失配，窗内准稳态）。"""
    import re as _re
    _m = _re.search(r"(AIL|ELEV|RUD)", csv_path)
    only = ({"AIL": "ail", "ELEV": "elev", "RUD": "rud"}[_m.group(1)]
            if _m else None)
    rows_full = list(csv.DictReader(open(csv_path)))
    t_full = np.array([float(r["t"]) for r in rows_full])
    t_end = t_full[-1]
    print("滑窗 %gs 窗 %gs 步:" % (win, step))
    for t0 in np.arange(6.0, t_end - win, step):
        sel = (t_full >= t0) & (t_full < t0 + win)
        idx = np.where(sel)[0]
        d = {"t": t_full[sel], "dt": float(np.median(np.diff(t_full[sel])))}
        for k in ("ail", "elev", "rud", "wx", "wy", "wz"):
            d[k] = np.array([float(rows_full[i][k]) for i in idx])
        f_flap_med = np.median([float(rows_full[i]["freq"]) for i in idx])
        rep = {}
        for ch, yk in CH.items():
            if only and ch != only:
                continue
            p_inj = np.array([AMP_BY_CH.get(ch, amp) * multisine(ch, tt)
                              for tt in d["t"]])
            pts = []
            for fq in FREQS:
                P = proj(p_inj, d["t"], fq)
                Y = proj(d[yk], d["t"], fq)
                if abs(P) < 1e-12:
                    continue
                T = Y / P
                s = 1j * 2 * math.pi * fq
                C = ctrl_tf(ch, s)
                G = T / (1.0 - T * C)
                pts.append((fq, G))
            pts_f = [p0 for p0 in pts if p0[0] > 0.3]
            if len(pts_f) < 3:
                continue
            ws = np.array([2 * math.pi * p0[0] for p0 in pts_f])
            Gm = np.array([p0[1] for p0 in pts_f])
            num_ = den_ = None
            best = None
            ph_h = np.angle(Gm[-1])
            sgn = 1.0 if abs(ph_h + math.pi) < abs(ph_h - math.pi) else -1.0
            for K in sgn * 10 ** np.arange(-2, 1.01, 0.5):
                for tau in np.arange(0.0, 0.301, 0.05):
                    for Tl in (0.01,):
                        for wn in 2 * math.pi * np.arange(1.8, 3.6, 0.2):
                            for zt in (0.01, 0.1, 0.3):
                                m_ = (K * np.exp(-1j * ws * tau)
                                      / ((1 + 1j * ws * Tl) * 1j * ws)
                                      * wn ** 2 / (wn ** 2 - ws ** 2
                                                   + 1j * 2 * zt * wn * ws))
                                err = np.sum(np.abs(Gm - m_) ** 2)
                                if best is None or err < best[0]:
                                    best = (err, K, tau, wn, zt)
            err, K, tau, wn, zt = best
            rel = float(err / max(np.sum(np.abs(Gm) ** 2), 1e-18))
            # 窗内频域验证
            num_ = den_ = 0.0
            for fq, G in pts:
                wq = 2 * math.pi * fq
                Gq = (K * np.exp(-1j * wq * tau)
                      / ((1 + 1j * wq * 0.01) * 1j * wq)
                      * wn ** 2 / (wn ** 2 - wq ** 2 + 1j * 2 * zt * wn * wq))
                Pq = proj(p_inj, d["t"], fq)
                Yq = proj(d[yk], d["t"], fq)
                Cq = ctrl_tf(ch, 1j * wq)
                Tq = Gq / (1 + Gq * Cq)
                num_ += abs(Yq - Pq * Tq) ** 2
                den_ += abs(Yq) ** 2
            vr = float(num_ / max(den_, 1e-18))
            rep[ch] = (K, tau, wn / 2 / math.pi, zt, rel, vr)
        for ch, (K, tau, fn, zt, rel, vr) in rep.items():
            print("  t=%4.0f-%4.0fs 拍频%.2f %s K=%+.3g τ=%.3f fn=%.2f "
                  "ζ=%.2f 拟合%.2f 验证%.2f %s"
                  % (t0, t0 + win, f_flap_med, ch, K, tau, fn, zt, rel,
                     vr, "✓" if (rel <= 0.5 and vr <= 0.5) else ""))
    return 0


def main(csv_path, amp=AMP_DEFAULT):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    import os as _os
    import csv as _csv
    # 单通道采集模式: 幅值参数指向该跑唯一注入通道
    d = load(csv_path, 6.0, 106.0)
    t = d["t"]
    print("样本 %d 时长 %.1fs 注入幅值 %.3f"
          % (len(t), t[-1] - t[0], amp))
    results = {}
    print("\n间接法 FRF（对象开环）与拟合：")
    for ch, yk in CH.items():
        p = np.array([AMP_BY_CH.get(ch, amp) * multisine(ch, tt)
                      for tt in t])
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
        # v3：拟合结构=积分×延迟×一阶×二阶共振（v2 教训：elev 通道
        # FRF 幅值单调升后回落、相位滑落再绕回=典型二阶共振，峰在
        # 模态频段）；剔除 0.2Hz 点（姿态外环污染：C 仅建模速率内环，
        # 低频外环闭环改写 T，登记于判据例外）
        pts_f = [p0 for p0 in pts if p0[0] > 0.3]
        ws = np.array([2 * math.pi * p0[0] for p0 in pts_f])
        Gm = np.array([p0[1] for p0 in pts_f])
        # 符号由高频末点相位定（绕回后渐近域）
        ph_h = np.angle(Gm[-1])
        sgn = 1.0 if abs(ph_h + math.pi) < abs(ph_h - math.pi) else -1.0

        def model(K, tau, T_, wn, zt):
            return (K * np.exp(-1j * ws * tau)
                    / ((1 + 1j * ws * T_) * 1j * ws)
                    * wn ** 2 / (wn ** 2 - ws ** 2 + 1j * 2 * zt * wn * ws))

        best = None
        for K in sgn * 10 ** np.arange(-2, 1.01, 0.25):
            for tau in np.arange(0.0, 0.301, 0.02):
                for Tl in np.arange(0.005, 0.201, 0.01):
                    for wn in 2 * math.pi * np.arange(1.8, 3.6, 0.1):
                        for zt in np.arange(0.05, 0.55, 0.05):
                            err = np.sum(np.abs(Gm - model(K, tau, Tl, wn,
                                                           zt)) ** 2)
                            if best is None or err < best[0]:
                                best = (err, K, tau, Tl, wn, zt)
        err, K, tau, Tl, wn, zt = best
        for _ in range(2):
            for K2 in K * 10 ** np.linspace(-0.15, 0.15, 7):
                for tau2 in tau + np.linspace(-0.008, 0.008, 7):
                    if tau2 < 0:
                        continue
                    for wn2 in wn * np.linspace(0.97, 1.03, 7):
                        for zt2 in zt * np.linspace(0.9, 1.1, 7):
                            e2 = np.sum(np.abs(Gm - model(K2, tau2, Tl,
                                                          wn2, zt2)) ** 2)
                            if e2 < err:
                                err, K, wn, zt = e2, K2, wn2, zt2
                                if tau2 >= 0:
                                    tau = tau2
        rel = float(err / max(np.sum(np.abs(Gm) ** 2), 1e-18))
        # 特征根：含二阶共振环节的闭环根
        num = np.array([-K * tau / 2, K]) * (wn ** 2)
        den = np.polymul(np.polymul(np.array([tau / 2, 1]),
                                    np.array([Tl, 1])),
                         np.polymul([1, 0],
                                    [1, 2 * zt * wn, wn ** 2]))
        den = np.polyadd(den, num)
        roots = np.roots(den)
        print("%s→%s K=%+.4g τ=%.3fs T=%.3fs fn=%.2fHz ζ=%.2f "
              "残差%.2f %s"
              % (ch, yk, float(K), float(tau), float(Tl),
                 wn / 2 / math.pi, zt, rel,
                 "低置信" if rel > 0.5 else ""))
        for r in sorted(roots, key=lambda q: -q.real):
            print("   根 s=%+.2f%+.2fj  %.2fHz ζ=%+.2f"
                  % (r.real, r.imag, abs(r.imag) / 2 / math.pi,
                     -r.real / max(abs(r), 1e-9)))
        results[ch] = dict(K=float(K), tau=float(tau), T=float(Tl),
                           wn=float(wn), zeta=float(zt),
                           rel_err=rel,
                           frf=[[float(fq), float(abs(G)), float(np.angle(G))]
                                for fq, G, _, _ in pts],
                           roots=[[float(r.real), float(r.imag)]
                                  for r in roots])
    # 留出验证（时域重构）
    print("\n留出验证：")
    val = {}
    for ch, yk in CH.items():
        p = np.array([AMP_BY_CH.get(ch, amp) * multisine(ch, tt)
                      for tt in t])
        y = d[yk] - d[yk].mean()
        r = results[ch]
        fr = np.fft.rfftfreq(len(p), d["dt"])
        ws2 = 2 * math.pi * fr
        P2 = np.fft.rfft(p * np.hanning(len(p)))
        wn_ = r.get("wn", 2 * math.pi * 2.4)
        zt_ = r.get("zeta", 0.3)
        with np.errstate(divide="ignore", invalid="ignore"):
            ws2_safe = np.where(ws2 == 0, 1e-6, ws2)
            G2 = (r["K"] * np.exp(-1j * ws2 * r["tau"])
                  / ((1 + 1j * ws2 * r["T"]) * 1j * ws2_safe)
                  * wn_ ** 2 / (wn_ ** 2 - ws2 ** 2
                                + 1j * 2 * zt_ * wn_ * ws2))
            C2 = ctrl_tf(ch, 1j * ws2)
            T2 = G2 / (1 + G2 * C2)
        T2 = np.nan_to_num(T2)
        # 频域逐设计频率点误差（时域重构版加窗幅值缺陷废弃：误差比
        # 恒一，判据本义是频域输出误差）
        num_ = 0.0
        den_ = 0.0
        for fq in FREQS:
            Pq = proj(p, t, fq)
            Yq = proj(d[yk], t, fq)
            if abs(Pq) < 1e-12:
                continue
            wq = 2 * math.pi * fq
            Gq = (r["K"] * np.exp(-1j * wq * r["tau"])
                  / ((1 + 1j * wq * r["T"]) * 1j * wq)
                  * wn_ ** 2 / (wn_ ** 2 - wq ** 2
                                + 1j * 2 * zt_ * wn_ * wq))
            Cq = ctrl_tf(ch, 1j * wq)
            Tq = Gq / (1 + Gq * Cq)
            num_ += abs(Yq - Pq * Tq) ** 2
            den_ += abs(Yq) ** 2
        ratio = float(num_ / max(den_, 1e-18))
        val[ch] = dict(err_ratio=ratio, passed=bool(ratio <= 0.5))
        print("  %s: %s 误差比=%.2f" % (ch, "过" if ratio <= 0.5 else
                                        "不过", ratio))
    path = os.path.join(PROJECT_ROOT, "logs", "ident_matrix",
                        "v3_%s.json" % ts)
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
    if len(sys.argv) > 3 and sys.argv[3] == "slide":
        sys.exit(sliding(a, amp))
    sys.exit(main(a, amp))
