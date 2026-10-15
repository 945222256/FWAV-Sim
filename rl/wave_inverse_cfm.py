# -*- coding: utf-8 -*-
"""
wave_inverse_cfm.py — 内环逆映射的生成式版本（复用 WSL OT-CFM 配方）
====================================================================
复用来源：~/xtnav_hpma/src/frenet_flow_data/tools/train_cfm_v19b_t_grid.py
（USV 规划线验证过的 OT-CFM 生产配方）。配方要件逐条对应：
  OT 小批量配对（Sinkhorn 精神，batch 内 Hungarian 精确指派）
  线性插值路径 x_t=(1-t)x0+t·x1（rectified flow 家族，直线路径）
  CFG 条件丢弃 10%，推理 v = v_uncond + ω·(v_cond − v_uncond)
  Euler ODE 采样（50 步，与部署版一致）
  部署指标选优 + 拒采（K 样本前代物理复核取最优）
问题形状差异（如实记录）：本任务输出 θ 是 5 维参数向量，无时域网格结构；
时域网格 CFM 留给"逐相位波形序列生成"的后续版本（重点思考2方向）。

数据：rl/data/wave_inverse.npz（θ→W 前向物理数据集，2026-09-19）
输出：rl/data/wave_inverse_cfm.pt + rl/figs/wave_inverse_cfm.png
"""
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl.waveform_inverse import DATA, FIGS, RANGES, THETA_NAMES, forward

import torch
import torch.nn as nn

CKPT = os.path.join(DATA, "wave_inverse_cfm.pt")
N_ODE_STEPS = 50          # 与 WSL 部署版同款 50 步 Euler
CFG_DROP = 0.10           # 10% 条件丢弃（训无条件分支）
DEVICE = "cpu"   # torch 2.6.0+cu124 不支持本机 GPU 架构（CUDA kernel 缺失），
                 # 本任务网络小，CPU 足够


class VelocityNet(nn.Module):
    """v(t, x, c)：时间标量 + 当前点 + 条件（标准化扳手）→ 速度场。"""

    def __init__(self, x_dim=5, c_dim=3, hidden=256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(x_dim + c_dim + 1, hidden), nn.SiLU(),
            nn.Linear(hidden, hidden), nn.SiLU(),
            nn.Linear(hidden, hidden), nn.SiLU(),
            nn.Linear(hidden, x_dim))

    def forward(self, t, x, c):
        return self.net(torch.cat([x, c, t[:, None]], dim=-1))


def ot_pair(x0, x1):
    """minibatch 精确 OT（Hungarian），代价 = 欧氏距离²（Sinkhorn 的硬版）。"""
    from scipy.optimize import linear_sum_assignment
    cost = torch.cdist(x1.float(), x0.float()).pow(2).cpu().numpy()
    r, c = linear_sum_assignment(cost)
    return x0[torch.as_tensor(c)]     # 与 x1[r==自然序] 对齐的 x0


def train(epochs=60, batch=512, lr=1e-3):
    d = np.load(os.path.join(DATA, "wave_inverse.npz"))
    TH, W = d["TH"], d["W"]
    lo = np.array([RANGES[k][0] for k in THETA_NAMES])
    hi = np.array([RANGES[k][1] for k in THETA_NAMES])
    t_mu, t_sd = (hi + lo) / 2, (hi - lo) / 2
    w_mu, w_sd = W.mean(0), W.std(0) + 1e-9

    X = torch.as_tensor((TH - t_mu) / t_sd, dtype=torch.float32)   # θ 归一 [-1,1]
    C = torch.as_tensor((W - w_mu) / w_sd, dtype=torch.float32)
    X, C = X.to(DEVICE), C.to(DEVICE)

    net = VelocityNet().to(DEVICE)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    n = len(X)
    t0 = time.time()
    for ep in range(epochs):
        perm = torch.randperm(n, device=DEVICE)
        ep_loss, nb = 0.0, 0
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            x1, c = X[idx], C[idx]
            # CFG：10% 样本条件置零（训无条件分支）
            cmask = (torch.rand(len(x1), device=DEVICE) > CFG_DROP).float()
            c_in = c * cmask[:, None]
            x0 = torch.randn_like(x1)
            x0p = ot_pair(x0, x1).to(DEVICE)          # OT 配对
            t = torch.rand(len(x1), device=DEVICE)
            x_t = (1 - t[:, None]) * x0p + t[:, None] * x1
            u = x1 - x0p                               # 直线路径目标速度
            loss = nn.functional.mse_loss(net(t, x_t, c_in), u)
            opt.zero_grad(); loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 0.5)
            opt.step()
            ep_loss += loss.item(); nb += 1
        if ep % 10 == 0 or ep == epochs - 1:
            print(f"[CFM] epoch {ep:3d} loss={ep_loss/nb:.5f} "
                  f"({time.time()-t0:.0f}s)", flush=True)
    torch.save({"state_dict": net.state_dict(), "w_mu": w_mu, "w_sd": w_sd,
                "t_mu": t_mu, "t_sd": t_sd}, CKPT)
    print(f"[CFM] 模型 → {CKPT}")
    return net, (w_mu, w_sd, t_mu, t_sd)


@torch.no_grad()
def sample(net, w_star, norm, k=8, omega=1.5, n_steps=N_ODE_STEPS):
    """CFG Euler ODE 采样 k 个 θ 候选（归一化空间→物理空间）。"""
    w_mu, w_sd, t_mu, t_sd = norm
    c = torch.as_tensor((np.atleast_2d(w_star) - w_mu) / w_sd,
                        dtype=torch.float32, device=DEVICE)
    c0 = torch.zeros_like(c)
    xs = []
    for _ in range(k):
        x = torch.randn(len(c), 5, device=DEVICE)
        for i in range(n_steps):
            t = torch.full((len(c),), i / n_steps, device=DEVICE)
            v_c = net(t, x, c)
            v_u = net(t, x, c0)
            x = x + (v_u + omega * (v_c - v_u)) / n_steps
        xs.append(x.cpu().numpy() * t_sd + t_mu)
    return xs                                   # k × (B,5) 物理空间 θ


def validate(net, norm, n_eval=150, k=8):
    """与判别式/线性同一复核协议：反解 θ 前代物理，比期望 vs 实际。
    CFM 版按 K 样本拒采取最优（部署版同款机制）。"""
    rng = np.random.default_rng(11)
    d = np.load(os.path.join(DATA, "wave_inverse.npz"))
    TH, W = d["TH"], d["W"]
    idx = rng.choice(len(TH), size=n_eval, replace=False)
    W_star = W[idx]

    import torch.nn as nn_
    ck = torch.load(os.path.join(DATA, "wave_inverse_policy.pt"),
                    weights_only=False)
    net_nn = nn_.Sequential(nn_.Linear(3, 256), nn_.Tanh(),
                            nn_.Linear(256, 256), nn_.Tanh(),
                            nn_.Linear(256, 5), nn_.Tanh())
    net_nn.load_state_dict(ck["state_dict"])
    lo = np.array([RANGES[k_][0] for k_ in THETA_NAMES])
    hi = np.array([RANGES[k_][1] for k_ in THETA_NAMES])

    def nn_inv(w):
        with torch.no_grad():
            x = torch.as_tensor((w - norm[0]) / norm[1], dtype=torch.float32)
            th = net_nn(x).numpy() * norm[3] + norm[2]
        return np.clip(th, lo, hi)

    err = {"线性伪逆": [], "判别式网络": [], "CFM生成式(K=8拒采)": []}
    TRIM = np.array([2.5, 0.0, 0.56, -0.02, 0.0])
    from rl.wave_inverse_baseline import jacobian_at
    J, W0 = jacobian_at(TRIM)
    Jpinv = np.linalg.pinv(J)

    for ws in W_star:
        # 线性伪逆
        th = np.clip(TRIM + Jpinv @ (ws - W0), lo, hi)
        err["线性伪逆"].append(forward(th) - ws)
        # 判别式
        err["判别式网络"].append(forward(nn_inv(ws)) - ws)
        # CFM K 样本拒采
        best = None
        for th in sample(net, ws, norm, k=k):
            e = forward(np.clip(th[0], lo, hi)) - ws
            if best is None or np.abs(e[0]) < np.abs(best[0]):
                best = e
        err["CFM生成式(K=8拒采)"].append(best)

    print("\n== 同一复核协议（物理前代）：平均绝对误差 ==")
    summary = {}
    for name, es in err.items():
        es = np.array(es)
        line = {}
        for j, wn in enumerate(["Fz", "Mx", "My"]):
            rng_j = np.percentile(W_star[:, j], 97.5) - \
                np.percentile(W_star[:, j], 2.5)
            mae = np.abs(es[:, j]).mean()
            line[wn] = mae / rng_j * 100 if rng_j > 1e-9 else float("nan")
        summary[name] = line
        print(f"  {name:<16} Fz {line['Fz']:5.1f}%  Mx {line['Mx']:5.1f}%")
    return summary, W_star, err


def plot(summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                       "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    names = list(summary.keys())
    x = np.arange(2)
    width = 0.25
    fig, ax = plt.subplots(figsize=(8.2, 4.2))
    colors = ["#8d99ae", "#2a9d8f", "#e76f51"]
    for i, (name, c) in enumerate(zip(names, colors)):
        vals = [summary[name]["Fz"], summary[name]["Mx"]]
        ax.bar(x + (i - 1) * width, vals, width, label=name, color=c)
        for xi, v in zip(x + (i - 1) * width, vals):
            ax.text(xi, v + 0.3, f"{v:.1f}%", ha="center", fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels(["Fz 通道（升力）", "Mx 通道（滚转力矩）"])
    ax.set_ylabel("平均相对误差 (%)")
    ax.set_title("内环逆映射三方法对比（同一物理复核协议，CFM 为 K=8 拒采）")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = os.path.join(FIGS, "wave_inverse_cfm.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print("[fig]", out)


def main():
    net, norm = train()
    summary, _, _ = validate(net, norm)
    plot(summary)


if __name__ == "__main__":
    main()
