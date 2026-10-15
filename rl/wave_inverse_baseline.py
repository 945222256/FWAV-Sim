# -*- coding: utf-8 -*-
"""
wave_inverse_baseline.py — 逆映射：神经网络 vs 经典线性伪逆 基线对比
=====================================================================
经典基线 = 工作点雅可比伪逆（台架辨识线性化 + 控制分配标准做法）：
  θ_lin = θ0 + pinv(J)·(W* − W(θ0))，J = ∂W/∂θ|θ0（有限差分）
神经网络 = rl/data/wave_inverse_policy.pt（waveform_inverse.py 训练）
同一物理复核协议：反解 θ 回代前向模型，比较期望扳手 vs 实际扳手。

输出：rl/figs/wave_inverse_vs_linear.png + 控制台误差表
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl.waveform_inverse import (DATA, FIGS, RANGES, THETA_NAMES, W_NAMES,
                                 forward)

TRIM = np.array([2.5, 0.0, 0.56, -0.02, 0.0])   # 工作点（悬停配平标称）


def jacobian_at(theta0, eps_frac=0.02):
    W0 = forward(theta0)
    J = np.zeros((len(W_NAMES), len(THETA_NAMES)))
    for j, name in enumerate(THETA_NAMES):
        lo, hi = RANGES[name]
        h = eps_frac * (hi - lo)
        tp = theta0.copy(); tp[j] += h
        tm = theta0.copy(); tm[j] -= h
        J[:, j] = (forward(tp) - forward(tm)) / (2 * h)
    return J, W0


def clip_theta(th):
    lo = np.array([RANGES[k][0] for k in THETA_NAMES])
    hi = np.array([RANGES[k][1] for k in THETA_NAMES])
    return np.clip(th, lo, hi)


def main():
    d = np.load(os.path.join(DATA, "wave_inverse.npz"))
    TH, W = d["TH"], d["W"]
    rng = np.random.default_rng(7)
    idx = rng.choice(len(TH), size=400, replace=False)
    W_star = W[idx]

    J, W0 = jacobian_at(TRIM)
    print("工作点雅可比 J = ∂W/∂θ：")
    hdr = "        " + "".join(f"{n:>10}" for n in THETA_NAMES)
    print(hdr)
    for i, wn in enumerate(W_NAMES):
        print(f"  {wn:<5}" + "".join(f"{J[i,j]:>10.2f}" for j in range(len(THETA_NAMES))))

    # 神经网络
    import torch
    ck = torch.load(os.path.join(DATA, "wave_inverse_policy.pt"),
                    weights_only=False)
    import torch.nn as nn
    net = nn.Sequential(nn.Linear(3, 256), nn.Tanh(),
                        nn.Linear(256, 256), nn.Tanh(),
                        nn.Linear(256, 5), nn.Tanh())
    net.load_state_dict(ck["state_dict"])
    w_mu, w_sd, t_mu, t_sd = ck["w_mu"], ck["w_sd"], ck["t_mu"], ck["t_sd"]

    def nn_inverse(w):
        with torch.no_grad():
            x = torch.as_tensor((w - w_mu) / w_sd, dtype=torch.float32)
            th = net(x).numpy() * t_sd + t_mu
        return clip_theta(th)

    Jpinv = np.linalg.pinv(J)

    def lin_inverse(w):
        return clip_theta(TRIM + (Jpinv @ (w - W0)).flatten())

    # 同一复核协议
    err = {"线性伪逆": np.zeros((len(W_star), 3)), "神经网络": np.zeros((len(W_star), 3))}
    for i, ws in enumerate(W_star):
        for name, fn in (("线性伪逆", lin_inverse), ("神经网络", nn_inverse)):
            th = fn(ws)
            err[name][i] = forward(th) - ws

    print("\n== 物理复核：|期望−实际| 平均绝对误差（及通道量程相对误差）==")
    table = {}
    for j, wn in enumerate(W_NAMES):
        rng_j = np.percentile(W_star[:, j], 97.5) - np.percentile(W_star[:, j], 2.5)
        row = {}
        for name in err:
            mae = np.abs(err[name][:, j]).mean()
            row[name] = (mae, mae / rng_j * 100 if rng_j > 1e-9 else float("nan"))
        table[wn] = row
        print(f"  {wn}: 线性伪逆 {row['线性伪逆'][0]:.3f} ({row['线性伪逆'][1]:.1f}%)   "
              f"神经网络 {row['神经网络'][0]:.3f} ({row['神经网络'][1]:.1f}%)")

    # 图：两方法误差对比（Fz / Mx 两通道散点误差带 + 汇总柱图）
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2))
    for j, wn in enumerate(W_NAMES[:2]):
        for name, mk, c in (("线性伪逆", "x", "tab:blue"),
                            ("神经网络", "o", "tab:red")):
            axes[j].scatter(W_star[:, j], err[name][:, j], s=8, alpha=0.4,
                            marker=mk, label=name, c=c)
        axes[j].axhline(0, c="gray", lw=0.8, ls="--")
        axes[j].set_title(f"{wn} 通道：反解残差 vs 期望值")
        axes[j].set_xlabel(f"期望 {wn}")
        axes[j].set_ylabel("实际 − 期望")
        axes[j].legend()
    # 汇总柱图
    labels = [f"{wn}" for wn in W_NAMES[:2]]
    x = np.arange(len(labels))
    width = 0.35
    lin_rel = [table[wn]["线性伪逆"][1] for wn in W_NAMES[:2]]
    nn_rel = [table[wn]["神经网络"][1] for wn in W_NAMES[:2]]
    axes[2].bar(x - width / 2, lin_rel, width, label="线性伪逆", color="tab:blue")
    axes[2].bar(x + width / 2, nn_rel, width, label="神经网络", color="tab:red")
    for xi, v in zip(x - width / 2, lin_rel):
        axes[2].text(xi, v + 0.2, f"{v:.1f}%", ha="center", fontsize=10)
    for xi, v in zip(x + width / 2, nn_rel):
        axes[2].text(xi, v + 0.2, f"{v:.1f}%", ha="center", fontsize=10)
    axes[2].set_xticks(x)
    axes[2].set_xticklabels(labels)
    axes[2].set_ylabel("平均相对误差 (%)")
    axes[2].set_title("同一物理复核协议下的对比")
    axes[2].legend()
    fig.suptitle("内环逆映射：神经网络 vs 经典线性伪逆（期望扳手→波形参数，前代物理复核）")
    fig.tight_layout()
    out = os.path.join(FIGS, "wave_inverse_vs_linear.png")
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print("[fig]", out)


if __name__ == "__main__":
    main()
