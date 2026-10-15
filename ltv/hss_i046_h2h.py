# -*- coding: utf-8 -*-
"""
hss_i046_h2h.py — 真实归档数据对拍，L4 线事项 I-046 先行范围执行。

按 ltv/hss_spec.json acceptance 节 A3 条执行对拍。线性冠军版取本内核
谐波阶数为零的特例，同场定义为同一批归档数据、同一分半协议、同一基
线扣除、同一带通、同一净口径计算、同一优化器与同一起点规则，唯一结
构差异为谐波阶数。留出验证与净口径算法逐条复用现行线性估计器
tools/ident_fd_estimator.py 的冻结实现：奇偶拍分半，逐拍前向预测一拍
时域，条件化基线扣除，零点一五到二十赫兹带通，拍相位三十六分箱锁相
扣除的净口径，双半劣者口径。全程零新增在环运行。

用法： python ltv/hss_i046_h2h.py [--batches I006rep9,I006rep11,I006rep55]
产出： ltv/h2h_results 时间戳目录，含机器可读 verdict.json 与胜负对照
表 report.md。
"""
import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
import hss_kernel as hk  # noqa: E402
import hss_a1_synthetic as a1  # noqa: E402

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
ANALYSIS_FREQS = [0.2, 0.5, 1.0, 1.5, 2.33, 3.0, 5.0, 8.0]
WINDOW = (10.5, 30.0)
N_IN = 6
K_LIST = (0, 1, 2)
N_STARTS = 4
FIT_SEED = 20261002
WIN_THRESHOLD = 0.80
CHANNELS = ["wx", "wy", "wz"]


def load_window(csv_path):
    """读归档窗数据，窗选与列名沿用现行线性估计器同款口径。"""
    df = pd.read_csv(csv_path)
    m = (df["t"] >= WINDOW[0]) & (df["t"] <= WINDOW[1]) & (df["z"] > 2.5)
    d = df[m]
    t = d["t"].values
    fd = d["freq"].values - float(np.median(d["freq"].values))
    src = {"ail": d["ail"].values, "elev": d["elev"].values,
           "rud": d["rud"].values, "freq_dev": fd, "dA": d["dA"].values,
           "twist": 0.5 * (d["twistLa"].values + d["twistRa"].values)}
    cols = a1.IN_COLS[:N_IN]
    u = np.stack([src[c] for c in cols], axis=1)
    w = np.stack([d[c].values for c in CHANNELS], axis=1)
    fl = d["freqL"].values if "freqL" in d.columns else None
    f_stroke = float(np.median(d["freq"].values))
    if fl is not None and np.isfinite(fl).sum() > len(t) * 0.5:
        fs_i = np.nan_to_num(fl, nan=float(np.nanmedian(fl)))
        ph = np.concatenate([[0.0], np.cumsum(
            0.5 * (fs_i[1:] + fs_i[:-1]) * np.diff(t))])
        pha = ph % 1.0
        stroke_idx = np.floor(ph).astype(int)
    else:
        pha = ((t - t[0]) * f_stroke) % 1.0
        stroke_idx = np.floor((t - t[0]) * f_stroke).astype(int)
    return t, u, w, pha, stroke_idx, f_stroke, d["veff"].values, fd, \
        src["dA"]


def bandpass(x, dt, lo=0.15, hi=20.0):
    """一阶高通减一阶低通的带通，与现行线性估计器同款递推。"""
    def onepole(v, fc, kind):
        aa = dt / (dt + 1.0 / (2 * np.pi * fc))
        y = np.zeros_like(v)
        acc = v[0]
        for i in range(len(v)):
            acc += aa * (v[i] - acc)
            y[i] = acc
        return y if kind == "lp" else v - y
    return onepole(onepole(x, hi, "lp"), lo, "hp")


def stroke_baseline_cond(pha, x, feats, half_mask, bins=72):
    """拍相位分箱条件化基线，复用现行线性估计器同款实现。"""
    idx = np.minimum((pha * bins).astype(int), bins - 1)
    out = np.zeros_like(x)
    g_mean = x[half_mask].mean(axis=0)
    for b in range(bins):
        mm = idx == b
        sel = half_mask & mm
        if sel.sum() > 8:
            coef, *_ = np.linalg.lstsq(feats[sel], x[sel], rcond=None)
            out[mm] = feats[mm] @ coef
        else:
            out[mm] = g_mean
    return out


def sim_full(u, pha, stroke_idx, p, dt, harm, harm_coef, lag_max):
    """逐拍前向时域仿真，K 阶周期状态矩阵，复用现行线性估计器孪生稿
    的半隐式执行链与逐拍重置口径。"""
    n = len(u)
    n_in = u.shape[1]
    K = len(p["Ac"])
    w_ = np.zeros(3)
    xr_ = np.zeros(n_in)
    xv_ = np.zeros(n_in)
    out_ = np.zeros((n, 3))
    nsub = 4
    h = dt / nsub
    lag = np.rint(p["td"] / dt).astype(int)
    raw = np.zeros((n + int(lag_max) + 1, n_in))
    raw[lag_max:lag_max + n] = u
    for i in range(int(lag_max)):
        raw[i] = u[0]
    for i in range(n):
        if i > 0 and stroke_idx[i] != stroke_idx[i - 1]:
            w_ = _dev_w[i].copy()
        d_in = np.array([raw[lag_max + i - lag[q], q] for q in range(n_in)])
        for q in range(3):
            d_in[q] *= 1.0 + p["m"][q] * np.sin(2 * np.pi * pha[i]
                                                + p["psi"][q])
        for _s in range(nsub):
            xv_ = xv_ + (-2 * p["zt"] * p["wn"] * xv_
                         + p["wn"] ** 2 * (d_in - xr_)) * h
            xr_ = xr_ + xv_ * h
        forcing = harm[i] @ harm_coef if harm_coef is not None else 0.0
        At = p["A0"].copy()
        for k in range(K):
            At = At + p["Ac"][k] * np.cos(2 * np.pi * (k + 1) * pha[i]) \
                   + p["As"][k] * np.sin(2 * np.pi * (k + 1) * pha[i])
        w_ = w_ + (At @ w_ + p["B"] @ xr_ + forcing) * dt
        out_[i] = w_
    return out_


def net_metric(sim_dev, meas_dev, pha, mask, bins=36):
    """净口径：验证段误差与实测各扣拍相位分箱锁相分量后的均方比。"""
    pha_v = pha[mask]
    idx_v = np.minimum((pha_v * bins).astype(int), bins - 1)
    out = []
    for q in range(3):
        def lock(x):
            prof = np.zeros(bins)
            for b in range(bins):
                sel = idx_v == b
                prof[b] = x[sel].mean() if sel.sum() > 0 else 0.0
            return prof[idx_v]
        e_net = (sim_dev[:, q] - lock(sim_dev[:, q])) \
            - (meas_dev[:, q] - lock(meas_dev[:, q]))
        v_net = meas_dev[:, q] - lock(meas_dev[:, q])
        out.append(float(np.mean(e_net ** 2) / np.var(v_net))
                   if np.var(v_net) > 0 else None)
    return out


_dev_w = None


def run_batch(tag, csv_path):
    t, u, w, pha, stroke_idx, f_stroke, veff, fd, dA = load_window(
        csv_path)
    dt = float(np.median(np.diff(t)))
    even = (stroke_idx % 2) == 0
    est_mask = even
    val_mask = ~est_mask
    U, W, Uhar = hk.build_samples(u, w, t, ANALYSIS_FREQS, f_stroke,
                                  a1.M_HARM, mask=est_mask)
    results = {}
    for K in K_LIST:
        S = {"U": U, "W": W, "Uhar": Uhar, "freqs": ANALYSIS_FREQS,
             "ws": 2 * np.pi * f_stroke, "n_in": N_IN, "K": K,
             "M_harm": a1.M_HARM}
        th, r, J, cov, ci, si = hk.fit_hss(S, n_starts=N_STARTS,
                                           seed=FIT_SEED)
        p = hk.unpack_theta(th, N_IN, K)
        feats = np.stack([np.ones(len(t)), veff - float(np.median(veff)),
                          fd, dA], axis=1)
        base = stroke_baseline_cond(pha, w, feats, est_mask)
        dev_w = w - base
        harm = np.stack([f(2 * np.pi * k * pha) for k in (1, 2, 3)
                         for f in (np.sin, np.cos)], axis=1)
        lag_max = int(np.rint(p["td"].max() / dt))
        global _dev_w
        _dev_w = dev_w
        meas_bp = bandpass(dev_w, dt)
        sim0 = bandpass(sim_full(u, pha, stroke_idx, p, dt, harm, None,
                                 lag_max), dt)
        resid_full = meas_bp - sim0
        coef, *_ = np.linalg.lstsq(harm[est_mask], resid_full[est_mask],
                                   rcond=None)
        sim1 = bandpass(sim_full(u, pha, stroke_idx, p, dt, harm, coef,
                                 lag_max), dt)
        sim2 = bandpass(sim_full(u, pha, stroke_idx, p, dt, harm, None,
                                 lag_max), dt)
        coef2, *_ = np.linalg.lstsq(harm[val_mask], resid_full[val_mask],
                                    rcond=None)
        sim3 = bandpass(sim_full(u, pha, stroke_idx, p, dt, harm, coef2,
                                 lag_max), dt)
        hold = []
        hold_swap = []
        for q in range(3):
            mse = float(np.mean((sim1[val_mask][:, q]
                                 - meas_bp[val_mask][:, q]) ** 2))
            var = float(np.var(meas_bp[val_mask][:, q]))
            hold.append(mse / var if var > 0 else None)
            mse2 = float(np.mean((sim3[est_mask][:, q]
                                  - meas_bp[est_mask][:, q]) ** 2))
            var2 = float(np.var(meas_bp[est_mask][:, q]))
            hold_swap.append(mse2 / var2 if var2 > 0 else None)
        net_a = net_metric(sim1[val_mask], meas_bp[val_mask], pha, val_mask)
        net_b = net_metric(sim3[est_mask], meas_bp[est_mask], pha, est_mask)
        results["K=%d" % K] = {
            "holdout_ratio_this_half": hold,
            "holdout_ratio_swap_half": hold_swap,
            "net_ratio_this_half": net_a,
            "net_ratio_swap_half": net_b,
            "net_worst": [max(a or 9e9, b or 9e9)
                          for a, b in zip(net_a, net_b)],
            "theta_norm": float(np.linalg.norm(r)),
        }
        print("[%s] K=%d 净口径本半 %s 换半 %s" % (
            tag, K, np.round(net_a, 3).tolist(),
            np.round(net_b, 3).tolist()), flush=True)
    lin = results["K=0"]
    table = {}
    for K in K_LIST[1:]:
        rk = results["K=%d" % K]
        r_this = [rk["net_ratio_this_half"][1]
                  / lin["net_ratio_this_half"][1]]
        r_swap = [rk["net_ratio_swap_half"][1]
                  / lin["net_ratio_swap_half"][1]]
        table["K=%d" % K] = {
            "R_this_half": r_this[0], "R_swap_half": r_swap[0],
            "win": max(r_this[0], r_swap[0]) <= WIN_THRESHOLD,
        }
    return {"tag": tag, "csv": csv_path, "f_stroke_hz": f_stroke,
            "results": results, "wy_verdict": table}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=str,
                    default="I006rep9,I006rep11,I006rep55")
    args = ap.parse_args()
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    batches = []
    for tag in args.batches.split(","):
        stem = tag.lower()
        if stem.startswith("i006"):
            stem = stem[4:]
        csv = os.path.join(_ROOT, "logs", "_i006_%s.csv" % stem)
        if not os.path.exists(csv):
            csv = os.path.join(_ROOT, "logs", "_i006_%s.csv" % tag)
        t0 = time.time()
        batches.append(run_batch(tag, csv))
        print("[%s] 批耗时 %.0fs" % (tag, time.time() - t0), flush=True)
    verdict = {
        "item": "I-046",
        "criterion": "A3_head_to_head",
        "spec_sha256": spec_sha,
        "win_threshold": WIN_THRESHOLD,
        "channel": "wy 俯仰",
        "batches": batches,
        "new_sim_runs": 0,
    }
    out_dir = os.path.join(_HERE, "h2h_results",
                           time.strftime("h2h_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(verdict, fp, ensure_ascii=False, indent=2)
    rep = ["# I-046 对拍胜负对照表", "",
           "判据出处 ltv/hss_spec.json acceptance 节 A3 条，规范指纹 %s。"
           % spec_sha[:16],
           "胜出条件为俯仰通道净口径比值双半劣者不超零点八零。", "",
           "| 工况 | 谐波阶数 | 本半比值 | 换半比值 | 劣者 | 判定 |",
           "|---|---|---|---|---|---|"]
    for b in batches:
        for K, v in b["wy_verdict"].items():
            rep.append("| %s | %s | %.3f | %.3f | %.3f | %s |" % (
                b["tag"], K.replace("K=", ""), v["R_this_half"],
                v["R_swap_half"], max(v["R_this_half"], v["R_swap_half"]),
                "胜出" if v["win"] else "未胜出"))
    rep += ["", "## 三通道全指标数值表", ""]
    for b in batches:
        rep.append("### 工况 %s（拍频 %.3f 赫兹）" % (b["tag"],
                                                   b["f_stroke_hz"]))
        rep.append("| 阶数 | 指标 | wx | wy | wz | |---|---|---|---|---|")
        for K, v in b["results"].items():
            rep.append("| %s | 净口径本半 | %s |" % (
                K, " | ".join("%.3f" % x for x
                              in v["net_ratio_this_half"])))
            rep.append("| %s | 净口径换半 | %s |" % (
                K, " | ".join("%.3f" % x for x
                              in v["net_ratio_swap_half"])))
            rep.append("| %s | 净口径劣者 | %s |" % (
                K, " | ".join("%.3f" % x for x in v["net_worst"])))
    rep += ["", "全程零新增在环运行。机器可读判 verdict.json 同目录。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return verdict


if __name__ == "__main__":
    main()
