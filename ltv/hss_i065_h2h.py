# -*- coding: utf-8 -*-
"""
hss_i065_h2h.py — 翼面响应子模型与现行静态列模型的真实数据对拍，L4 线
事项 I-065 第二阶段。

两臂定义：八通道模型为六现行通道（ail、elev、rud、freq_dev、dA、twist）
加左右翼指令偏差列（flapLt、flapRt 减窗内中位）。静态臂即现行静态列模
态：翼指令列以链旁路进入（内核 chain_bypass 掩码，G 等于一，无延迟无共
振），对应 I-055 静态列结构族；动态臂即本事项子模型：翼指令列各带逐翼
二阶共振链（舵机有限刚度响应动态）。两臂唯一差异为翼列是否携带响应动
态，其余全部同场：同一批归档数据、同一奇偶拍分半、同一条件化基线、同
一带通、同一净口径与双半劣者口径、同一优化器与起点数、同为零阶结构。

判读规则预登记（先于数据，沿用 I-058 同款带）：滚转通道净口径双半劣者
比值 动态臂比静态臂 不超零点八零判改善，零点八零至一点二零判证伪，超
一点二零判有害。两臂在 rep45 与 hop9 两参照批（窗口取归档登记值，即
I-055 现行值出处批次）上执行，两种结局均按完成通道交付。全程零新增在
环运行。

时域留出仿真的翼链口径与频域一致：旁路通道不经链直接进入，链通道按二
阶共振积分，与频域 G 逐点对应。

产出写入 ltv/i065_results 时间戳目录。
"""
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
import hss_i046_h2h as h2h  # noqa: E402

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
FREQS = h2h.ANALYSIS_FREQS
N_IN = 8
K = 0
N_STARTS = 3
FIT_SEED = 20261003
WIN_THRESHOLD = 0.80
FALSIFY_BAND = (0.80, 1.20)
BATCHES = {"rep45": [10.5, 30.0], "hop9": [10.5, 110.0]}
CHANNELS = ["wx", "wy", "wz"]


def load_window8(csv_path, window):
    df = pd.read_csv(csv_path)
    m = (df["t"] >= window[0]) & (df["t"] <= window[1]) & (df["z"] > 2.5)
    d = df[m]
    t = d["t"].values
    fd = d["freq"].values - float(np.median(d["freq"].values))
    flap_lc = d["flapLt"].values - float(np.median(d["flapLt"].values))
    flap_rc = d["flapRt"].values - float(np.median(d["flapRt"].values))
    src = {"ail": d["ail"].values, "elev": d["elev"].values,
           "rud": d["rud"].values, "freq_dev": fd, "dA": d["dA"].values,
           "twist": 0.5 * (d["twistLa"].values + d["twistRa"].values),
           "flapLc": flap_lc, "flapRc": flap_rc}
    cols = ["ail", "elev", "rud", "freq_dev", "dA", "twist",
            "flapLc", "flapRc"]
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


def sim_full8(u, pha, stroke_idx, p, dt, harm, harm_coef, lag_max,
              dev_w, byp):
    """逐拍前向时域仿真，旁路通道不经链。与频域 G 口径逐点对应。"""
    n = len(u)
    byp = byp if byp is not None else [False] * N_IN
    w_ = dev_w[0].copy()
    xr_ = np.zeros(N_IN)
    xv_ = np.zeros(N_IN)
    out_ = np.zeros((n, 3))
    nsub = 4
    h = dt / nsub
    lag = np.rint(p["td"] / dt).astype(int)
    lag = np.where(np.asarray(byp, bool), 0, lag)
    raw = np.zeros((n + int(lag_max) + 1, N_IN))
    raw[lag_max:lag_max + n] = u
    for i in range(int(lag_max)):
        raw[i] = u[0]
    for i in range(n):
        if i > 0 and stroke_idx[i] != stroke_idx[i - 1]:
            w_ = dev_w[i].copy()
        d_in = np.array([raw[lag_max + i - lag[q], q]
                         for q in range(N_IN)])
        for q in range(3):
            d_in[q] *= 1.0 + p["m"][q] * np.sin(2 * np.pi * pha[i]
                                                + p["psi"][q])
        for _s in range(nsub):
            for q in range(N_IN):
                if not byp[q]:
                    xv_[q] = xv_[q] + (-2 * p["zt"][q] * p["wn"][q] * xv_[q]
                                       + p["wn"][q] ** 2
                                       * (d_in[q] - xr_[q])) * h
            xr_ = xr_ + xv_ * h
        forcing = harm[i] @ harm_coef if harm_coef is not None else 0.0
        w_ = w_ + (p["A0"] @ w_ + p["B"] @ xr_ + forcing) * dt
        out_[i] = w_
    return out_


def run_arm(tag, csv_path, window, byp):
    t, u, w, pha, stroke_idx, f_stroke, veff, fd, dA = load_window8(
        csv_path, window)
    dt = float(np.median(np.diff(t)))
    even = (stroke_idx % 2) == 0
    est_mask = even
    val_mask = ~est_mask
    U, W, Uhar = hk.build_samples(u, w, t, FREQS, f_stroke, 2, mask=est_mask)
    Hmeas = np.array([hk.bandpass_resp(f, dt) for f in FREQS])
    S = {"U": U, "W": W, "Uhar": Uhar, "freqs": FREQS,
         "ws": 2 * np.pi * f_stroke, "n_in": N_IN, "K": K,
         "M_harm": 2, "Hmeas": Hmeas}
    if byp is not None:
        S["chain_bypass"] = byp
    th, r, J, cov, ci, si = hk.fit_hss(
        S, n_starts=N_STARTS, seed=FIT_SEED, restarts=1,
        tpe_rounds=5, tpe_batch=4, tpe_trigger=2500.0)
    p = hk.unpack_theta(th, N_IN, K)
    feats = np.stack([np.ones(len(t)), veff - float(np.median(veff)),
                      fd, dA], axis=1)
    base = h2h.stroke_baseline_cond(pha, w, feats, est_mask)
    dev_w = w - base
    harm = np.stack([f(2 * np.pi * k * pha) for k in (1, 2, 3)
                     for f in (np.sin, np.cos)], axis=1)
    byp_l = byp if byp is not None else [False] * N_IN
    chained = [q for q in range(N_IN) if not byp_l[q]]
    lag_max = (int(np.rint(max(p["td"][q] for q in chained) / dt))
               if chained else 0)
    meas_bp = h2h.bandpass(dev_w, dt)
    sim0 = h2h.bandpass(sim_full8(u, pha, stroke_idx, p, dt, harm, None,
                                  lag_max, dev_w, byp), dt)
    resid_full = meas_bp - sim0
    coef, *_ = np.linalg.lstsq(harm[est_mask], resid_full[est_mask],
                               rcond=None)
    sim1 = h2h.bandpass(sim_full8(u, pha, stroke_idx, p, dt, harm, coef,
                                  lag_max, dev_w, byp), dt)
    coef2, *_ = np.linalg.lstsq(harm[val_mask], resid_full[val_mask],
                                rcond=None)
    sim3 = h2h.bandpass(sim_full8(u, pha, stroke_idx, p, dt, harm, coef2,
                                  lag_max, dev_w, byp), dt)
    net_a = h2h.net_metric(sim1[val_mask], meas_bp[val_mask], pha, val_mask)
    net_b = h2h.net_metric(sim3[est_mask], meas_bp[est_mask], pha, est_mask)
    net_worst = [max(a or 9e9, b or 9e9) for a, b in zip(net_a, net_b)]
    wing_chain = {"wn": (p["wn"][6:8] / (2 * np.pi)).tolist(),
                  "zeta": p["zt"][6:8].tolist(),
                  "td": p["td"][6:8].tolist()}
    return {"net_this": net_a, "net_swap": net_b, "net_worst": net_worst,
            "wing_chain_est": wing_chain, "resid_norm":
                float(np.linalg.norm(r))}


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    batches = {}
    for tag, window in BATCHES.items():
        csv = os.path.join(_ROOT, "logs", "_i006_%s.csv" % tag)
        t0 = time.time()
        stat = run_arm(tag, csv, window, [False] * 6 + [True, True])
        dyn = run_arm(tag, csv, window, None)
        ratio = dyn["net_worst"][0] / stat["net_worst"][0]
        if ratio <= WIN_THRESHOLD:
            verdict = "改善"
        elif ratio <= FALSIFY_BAND[1]:
            verdict = "证伪：无可判读改善"
        else:
            verdict = "有害"
        batches[tag] = {
            "window": window, "static": stat, "dynamic": dyn,
            "ratio": ratio, "verdict": verdict,
            "elapsed_s": round(time.time() - t0, 1)}
        print("[%s] 滚转净口径劣者 静态 %.3f 动态 %.3f 比值 %.2f → %s"
              % (tag, stat["net_worst"][0], dyn["net_worst"][0], ratio,
                 verdict), flush=True)
    verdict = {
        "item": "I-065",
        "stage": "head_to_head_real",
        "spec_sha256": spec_sha,
        "thresholds": {"win": WIN_THRESHOLD, "falsify_band":
                       list(FALSIFY_BAND)},
        "channel": "wx 滚转",
        "batches": batches,
        "new_sim_runs": 0,
    }
    out_dir = os.path.join(_HERE, "i065_results",
                           time.strftime("i065h2h_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(verdict, fp, ensure_ascii=False, indent=2)
    rep = ["# I-065 翼面响应子模型对静态列模型对拍报告", "",
           "两臂唯一差异为翼指令列是否携带二阶响应链（舵机有限刚度动态），"
           "其余同场。判读规则预登记：滚转净口径双半劣者比值不超零点八零"
           "判改善，零点八零至一点二零判证伪，超一点二零判有害。", "",
           "| 工况 | 窗口 | 静态臂 | 动态臂 | 比值 | 判读 |",
           "|---|---|---|---|---|---|"]
    for tag, b in batches.items():
        rep.append("| %s | %.1f~%.1f s | %.3f | %.3f | %.2f | %s |" % (
            tag, b["window"][0], b["window"][1],
            b["static"]["net_worst"][0], b["dynamic"]["net_worst"][0],
            b["ratio"], b["verdict"]))
    rep += ["", "## 翼链估计读数（动态臂）", ""]
    for tag, b in batches.items():
        wc = b["dynamic"]["wing_chain_est"]
        rep.append("- %s：共振频率 %s 赫兹，阻尼比 %s，纯延迟 %s 秒" % (
            tag, np.round(wc["wn"], 2).tolist(),
            np.round(wc["zeta"], 3).tolist(),
            np.round(wc["td"], 3).tolist()))
    rep += ["", "## 三通道全指标数值表", ""]
    for tag, b in batches.items():
        for arm in ("static", "dynamic"):
            v = b[arm]
            rep.append("- %s %s：本半 %s；换半 %s" % (
                tag, arm, np.round(v["net_this"], 3).tolist(),
                np.round(v["net_swap"], 3).tolist()))
    rep += ["", "全程零新增在环运行。机器可读判 verdict.json 同目录。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return verdict


if __name__ == "__main__":
    main()
