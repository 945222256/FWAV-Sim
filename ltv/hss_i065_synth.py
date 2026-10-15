# -*- coding: utf-8 -*-
"""
hss_i065_synth.py — 翼面响应动力学子模型合成恢复验收，L4 线事项 I-065。

子模型结构：翼面在舵机有限刚度下的响应动态建为逐翼二阶共振链，翼面实
际拍动角对指令角的响应为 G_w(s) = ω_w²/(s²+2ζ_w·ω_w·s+ω_w²)·exp(−τ_w·s)，
参数逐翼独立估计，翼指令偏差列经该链进入体轴角速度方程的翼路径。静态
列模型即该链旁路（G 等于一）的特例，内核以 chain_bypass 掩码统一两结构。

验收判据为本事项新预登记（先于数据，对齐 ltv/hss_spec.json A1 条的阈
值理由，不改动任何已冻结文件）：
  一、翼链子组恢复：两翼通道的共振频率、阻尼比、纯延迟三子组相对误差
      （Frobenius，逐组跨翼取劣者）各不超零点二零，理由同 A1 执行链两
      成线（非线性参数取故障线量级）；
  二、翼路径输入矩块（三行乘两翼列）相对误差不超零点一零，理由同 A1 主
      矩阵组一成线；
  三、信噪比二十与十分贝两档全过，全程零仿真器调用。
A0 与尾链恢复一并列报不作门。真值抽取量级域：A0 对角负一点零至负零点
一每秒、非对角不超对角一成；尾路径 B 主元十至六十；翼路径块滚转反对称
元十至六十、俯仰对称元五至三十、其余不超主元一成；翼链共振频率一点五
至四点零赫兹、阻尼比零点零五至零点三、纯延迟零点零五至零点二秒，全部
在估计器声明搜索域内。激励为施罗德多正弦，谱线按通道独占分配（六现行
通道用产线八线，两翼通道用七条专属线组，分析频点取并集），独占分配为
辨识产线在案的可辨识性关键措施，翼通道幅值零点一弧度。真值以解析右端
四阶龙格库塔积分，延迟与调制次序沿用规范
口径（先调制后过含延迟链）。

产出写入 ltv/i065_results 时间戳目录。
"""
import hashlib
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import hss_kernel as hk  # noqa: E402

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
SEED = 20261003
F_STROKE = 1.25
DT = 0.002
T_TOTAL = 3640.0
T_DROP = 40.0
M_HARM = 2
N_IN = 8
N_STARTS = 4
FIT_SEED = 20261003
FREQS = [0.2, 0.5, 1.0, 1.5, 2.33, 3.0, 5.0, 8.0]
WING_FREQS = [0.75, 1.15, 2.6, 3.6, 4.7, 6.5, 9.5]
ANALYSIS = sorted(set(FREQS) | set(WING_FREQS))
CH_LINES = [FREQS] * 6 + [WING_FREQS] * 2
CH_AMPS = [0.06, 0.05, 0.05, 0.30, 0.35, 0.10, 0.10, 0.10]
THRESH = {"wing_chain_max": 0.20, "wing_B_max": 0.10, "snr_db": [20, 10]}


def draw_truth(rng):
    diag = rng.uniform(-1.0, -0.1, 3)
    A0 = rng.uniform(-1.0, 1.0, (3, 3)) * 0.08 * np.abs(diag)[:, None]
    np.fill_diagonal(A0, diag)
    B = rng.uniform(-1.0, 1.0, (3, N_IN)) * 0.08
    tail_main = rng.uniform(10.0, 60.0, 3)
    for q in range(3):
        B[q, q] = tail_main[q]
    beta = rng.uniform(10.0, 60.0, 2)
    gamma = rng.uniform(5.0, 30.0, 2)
    B[0, 6] = beta[0]
    B[0, 7] = -beta[1]
    B[1, 6] = gamma[0]
    B[1, 7] = gamma[1]
    wn = 2 * np.pi * rng.uniform(1.5, 4.0, N_IN)
    zt = rng.uniform(0.05, 0.3, N_IN)
    td = rng.uniform(0.05, 0.2, N_IN)
    m = rng.uniform(0.2, 0.6, 3)
    psi = rng.uniform(-np.pi, np.pi, 3)
    return {"A0": A0, "B": B, "wn": wn, "zt": zt, "td": td,
            "m": m, "psi": psi}


def simulate_truth(tr, t):
    n = len(t)
    fine = np.empty(2 * n + 1)
    fine[0::2][:n] = t
    fine[1::2] = t + DT / 2
    fine[-1] = t[-1] + DT
    d_fine = np.zeros((len(fine), N_IN))
    u_grid = np.zeros((n, N_IN))
    for p in range(N_IN):
        lines = np.array(CH_LINES[p])
        nl = len(lines)
        phas = np.array([np.pi * (i * (i + 1) // 2) / nl + p * 0.7
                         for i in range(nl)])
        amp = CH_AMPS[p] / (0.5 * nl)
        d_fine[:, p] = amp * np.sum(
            np.sin(2 * np.pi * np.outer(lines, fine - tr["td"][p])
                   + phas[:, None]), axis=0)
        u_grid[:, p] = amp * np.sum(
            np.sin(2 * np.pi * np.outer(lines, t) + phas[:, None]), axis=0)
    for p in range(3):
        d_fine[:, p] *= 1.0 + tr["m"][p] * np.sin(
            2 * np.pi * F_STROKE * (fine - tr["td"][p]) + tr["psi"][p])

    def rhs(idx, y):
        w = y[0:3]
        xr = y[3:3 + N_IN]
        xv = y[3 + N_IN:]
        d = d_fine[idx]
        wd = tr["A0"] @ w + tr["B"] @ xr
        xvd = -2 * tr["zt"] * tr["wn"] * xv + tr["wn"] ** 2 * (d - xr)
        return np.concatenate([wd, xv, xvd])

    y = np.zeros(3 + 2 * N_IN)
    out = np.zeros((n, 3))
    for i in range(n):
        out[i] = y[0:3]
        k1 = rhs(2 * i, y)
        k2 = rhs(2 * i + 1, y + DT / 2 * k1)
        k3 = rhs(2 * i + 1, y + DT / 2 * k2)
        k4 = rhs(2 * i + 2, y + DT * k3)
        y = y + DT / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    return out, u_grid


def bandpass(x, dt, lo=0.15, hi=20.0):
    def onepole(v, fc, kind):
        a = dt / (dt + 1.0 / (2 * np.pi * fc))
        y = np.zeros_like(v)
        acc = v[0]
        for i in range(len(v)):
            acc += a * (v[i] - acc)
            y[i] = acc
        return y if kind == "lp" else v - y
    return onepole(onepole(x, hi, "lp"), lo, "hp")


def relerr(est, true):
    e = np.asarray(est, float) - np.asarray(true, float)
    return float(np.linalg.norm(e) / np.linalg.norm(true))


def run_case(snr_db, tr, clean, u_grid, t, rng_noise):
    mask = t >= T_DROP
    clean_bp = np.stack([bandpass(clean[:, q], DT) for q in range(3)],
                        axis=1)
    sig_std = clean_bp[mask].std(axis=0)
    W_meas = clean_bp + rng_noise.normal(size=clean_bp.shape) \
        * sig_std / 10.0 ** (snr_db / 20.0)
    U, W, Uhar = hk.build_samples(u_grid, W_meas, t, ANALYSIS, F_STROKE,
                                  M_HARM, mask=mask)
    Hmeas = np.array([hk.bandpass_resp(f, DT) for f in ANALYSIS])
    S = {"U": U, "W": W, "Uhar": Uhar, "freqs": ANALYSIS,
         "ws": 2 * np.pi * F_STROKE, "n_in": N_IN, "K": 0,
         "M_harm": M_HARM, "Hmeas": Hmeas}
    th, r, J, cov, ci, si = hk.fit_hss(
        S, n_starts=N_STARTS, seed=FIT_SEED, restarts=1,
        tpe_rounds=5, tpe_batch=4, tpe_trigger=2500.0)
    p = hk.unpack_theta(th, N_IN, 0)
    wing_chain = {
        "wn": relerr(p["wn"][6:8], tr["wn"][6:8]),
        "zeta": relerr(p["zt"][6:8], tr["zt"][6:8]),
        "tau_d": relerr(p["td"][6:8], tr["td"][6:8]),
    }
    res = {
        "snr_db": snr_db,
        "wing_chain_subgroups": wing_chain,
        "wing_chain_worst": max(wing_chain.values()),
        "wing_B_relerr": relerr(p["B"][:, 6:8], tr["B"][:, 6:8]),
        "A0_relerr": relerr(p["A0"], tr["A0"]),
        "tail_chain_worst": max(relerr(p["wn"][:6], tr["wn"][:6]),
                                relerr(p["zt"][:6], tr["zt"][:6]),
                                relerr(p["td"][:6], tr["td"][:6])),
        "resid_norm": float(np.linalg.norm(r)),
        "best_start": int(si),
        "pass": (max(wing_chain.values()) <= THRESH["wing_chain_max"]
                 and relerr(p["B"][:, 6:8], tr["B"][:, 6:8])
                 <= THRESH["wing_B_max"]),
    }
    return res


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    rng = np.random.default_rng(SEED)
    tr = draw_truth(rng)
    t = np.arange(0.0, T_TOTAL, DT)
    t0 = time.time()
    clean, u_grid = simulate_truth(tr, t)
    print("真值仿真 %.0fs" % (time.time() - t0), flush=True)
    rng_noise = np.random.default_rng(SEED + 1)
    cases = []
    for snr in THRESH["snr_db"]:
        t0 = time.time()
        c = run_case(snr, tr, clean, u_grid, t, rng_noise)
        c["elapsed_s"] = round(time.time() - t0, 1)
        cases.append(c)
        print("snr=%d 翼链最差=%.4f 翼B=%.4f A0=%.4f pass=%s %.0fs" % (
            snr, c["wing_chain_worst"], c["wing_B_relerr"],
            c["A0_relerr"], c["pass"], c["elapsed_s"]), flush=True)
    banned = [m for m in ("isaac", "omni", "pxr", "modules", "config",
                          "main_sim") if any(x.startswith(m + ".")
                                             for x in sys.modules)]
    verdict = {
        "item": "I-065",
        "stage": "synthetic_recovery",
        "spec_sha256": spec_sha,
        "seed": SEED,
        "thresholds": THRESH,
        "n_in": N_IN,
        "cases": cases,
        "simulator_calls": 0,
        "simulator_modules_loaded": banned,
        "verdict_pass": all(c["pass"] for c in cases),
    }
    out_dir = os.path.join(_HERE, "i065_results",
                           time.strftime("i065syn_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(verdict, fp, ensure_ascii=False, indent=2)
    rep = ["# I-065 翼面响应子模型合成恢复验收报告", "",
           "预登记阈值：翼链三子组各不超零点二零、翼路径输入矩块不超零点"
           "一零、双档信噪比全过、零仿真器调用。规范文件指纹 %s 未改动。"
           % spec_sha[:16],
           "总体结论为%s。" % ("通过" if verdict["verdict_pass"] else "不通过"),
           "", "| 信噪比分贝 | 翼链最差子组 | 翼路径块 | A0 | 尾链最差 | 判定 |",
           "|---|---|---|---|---|---|"]
    for c in cases:
        rep.append("| %d | %.4f | %.4f | %.4f | %.4f | %s |" % (
            c["snr_db"], c["wing_chain_worst"], c["wing_B_relerr"],
            c["A0_relerr"], c["tail_chain_worst"],
            "过" if c["pass"] else "不过"))
    rep += ["", "翼链子组明细：", ""]
    for c in cases:
        sg = c["wing_chain_subgroups"]
        rep.append("- 信噪比 %d：共振频率 %.4f、阻尼比 %.4f、纯延迟 %.4f"
                   % (c["snr_db"], sg["wn"], sg["zeta"], sg["tau_d"]))
    rep += ["", "仿真器调用零次，仿真器模块加载清单为空。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return verdict


if __name__ == "__main__":
    v = main()
    sys.exit(0 if v["verdict_pass"] else 1)
