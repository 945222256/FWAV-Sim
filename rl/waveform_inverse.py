# -*- coding: utf-8 -*-
"""
waveform_inverse.py — 内环逆映射演示：期望力矩 → 扑动波形参数
================================================================
生产口径的分层架构（2026-09-19，应面试团队"内环映射"方向）：
  外环（经典、可解释）：TECS/串级PID 输出期望扳手 W* = [Fz, Mx, My]
  内环（学习）：逆映射网络 W* → θ = [f, dA, split, pitch_lag, mean]
  前向物理：modules/aero_solver.FlapperAeroModel（S&D 准定常，已台架辨识）

为什么内环需要学习：θ→W 的前向映射强非线性（split/lag 改变周期内波形）、
多值（f 与幅值都能升力）、随飞行状态变化——解析反解不存在；
逆映射网络给出初始解，RL 在真机上做残差微调（Real2Sim2Real 闭环）。

用法：C:\\Python313\\python.exe rl\\waveform_inverse.py
输出：rl/data/wave_inverse.npz / rl/figs/wave_inverse_*.png
"""
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.aero_solver import FlapperAeroModel

RL_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(RL_DIR, "data")
FIGS = os.path.join(RL_DIR, "figs")

A0 = math.radians(35.0)
# θ 的包线（全部在台架辨识覆盖过的物理范围内）
RANGES = {"f": (1.5, 3.5), "dA": (-0.25, 0.25), "split": (0.45, 0.65),
          "lag": (-0.06, 0.06), "mean_deg": (-8.0, 8.0)}
THETA_NAMES = ["f", "dA", "split", "lag", "mean_deg"]
W_NAMES = ["Fz", "Mx", "My"]


def forward(theta):
    """θ=[f,dA,split,lag,mean_deg] → 周期平均扳手 [Fz, Mx, My]（悬停静气）。"""
    f, dA, split, lag, mean_deg = theta
    m = FlapperAeroModel({"span": 1.15, "root_chord": 0.35,
                          "tip_chord": 0.25, "n_strips": 14})
    # 几何同步冻结（2026-09-20）：数据集生成于 2.3m/700g Phoenix 口径，
    # 主线已切 SparrowHawk 0.6m，前代复核必须与数据集口径一致
    m.set_wing_command(frequency=f)
    m.wing_left.amplitude = A0 + dA
    m.wing_right.amplitude = A0 - dA
    for w in (m.wing_left, m.wing_right):
        w.split = split
        w.pitch_lag_s = lag
        w.mean = math.radians(mean_deg)
        # α_set 冻结在数据集生成口径 15°（2026-09-20：主线已切巡航
        # 配平 α=40°，与缓存数据集 wave_inverse.npz 解耦）
        w.alpha_set = math.radians(15.0)
        w.pitch_mode = "aero"
    # 相位状态复位（2026-09-20）：主线已改积分相位态，前向数据集按
    # 绝对时间无状态求值口径生成，每次 forward 复位保持一致
    for _w in (m.wing_left, m.wing_right):
        _w._t_prev = None
        _w._tau_state = 0.0
    n = 48
    Fs, Ts = [], []
    for i in range(n):
        F, T = m.compute_forces(np.zeros(3), np.array([1.0, 0, 0, 0]),
                                np.zeros(3), np.zeros(3), i / (n * f))
        Fs.append(F)
        Ts.append(T)
    return np.array([np.mean(Fs, axis=0)[2],
                     np.mean(Ts, axis=0)[0],
                     np.mean(Ts, axis=0)[1]])


def gen_dataset(n=12000, seed=42):
    rng = np.random.default_rng(seed)
    lo = np.array([RANGES[k][0] for k in THETA_NAMES])
    hi = np.array([RANGES[k][1] for k in THETA_NAMES])
    TH = rng.uniform(lo, hi, size=(n, len(THETA_NAMES)))
    W = np.zeros((n, len(W_NAMES)))
    t0 = time.time()
    for i in range(n):
        W[i] = forward(TH[i])
        if i % 2000 == 0:
            print(f"[data] {i}/{n} ({time.time()-t0:.0f}s)", flush=True)
    np.savez_compressed(os.path.join(DATA, "wave_inverse.npz"), TH=TH, W=W)
    print(f"[data] {n} 样本完成，{time.time()-t0:.0f}s")
    return TH, W


def train_inverse(TH, W, epochs=60, batch=512, lr=1e-3):
    import torch
    import torch.nn as nn
    # 输入/输出都标准化，θ 归一化到 [-1,1]
    w_mu, w_sd = W.mean(0), W.std(0) + 1e-9
    lo = np.array([RANGES[k][0] for k in THETA_NAMES])
    hi = np.array([RANGES[k][1] for k in THETA_NAMES])
    t_mu, t_sd = (hi + lo) / 2, (hi - lo) / 2

    x = torch.as_tensor((W - w_mu) / w_sd, dtype=torch.float32)
    y = torch.as_tensor((TH - t_mu) / t_sd, dtype=torch.float32)
    n_val = 2000
    x_va, y_va, TH_va = x[:n_val], y[:n_val], TH[:n_val]
    x_tr, y_tr = x[n_val:], y[n_val:]

    net = nn.Sequential(
        nn.Linear(3, 256), nn.Tanh(),
        nn.Linear(256, 256), nn.Tanh(),
        nn.Linear(256, 5), nn.Tanh())
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    fn = nn.MSELoss()
    for ep in range(epochs):
        perm = torch.randperm(len(x_tr))
        for i in range(0, len(x_tr), batch):
            idx = perm[i:i + batch]
            loss = fn(net(x_tr[idx]), y_tr[idx])
            opt.zero_grad(); loss.backward(); opt.step()
        if ep % 10 == 0 or ep == epochs - 1:
            with torch.no_grad():
                v = fn(net(x_va), y_va).item()
            print(f"[inverse] epoch {ep:3d} train={loss.item():.5f} val={v:.5f}")
    net._w_mu, net._w_sd = w_mu, w_sd
    net._t_mu, net._t_sd = t_mu, t_sd
    return net, TH_va, x_va


def validate_and_plot(net, TH_va, W_va_raw):
    import torch
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    with torch.no_grad():
        w_n = (W_va_raw - net._w_mu) / net._w_sd
        th_pred_n = net(torch.as_tensor(w_n, dtype=torch.float32)).numpy()
    TH_pred = th_pred_n * net._t_sd + net._t_mu
    # 反解出的 θ 回代前向物理，得到实际扳手 Ŵ
    W_hat = np.zeros_like(W_va_raw)
    for i in range(len(TH_pred)):
        W_hat[i] = forward(TH_pred[i])

    os.makedirs(FIGS, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.0))
    stats = {}
    for j, name in enumerate(W_NAMES):
        w, wh = W_va_raw[:, j], W_hat[:, j]
        err = wh - w
        # 相对误差按通道量程归一
        rng = np.percentile(w, 97.5) - np.percentile(w, 2.5)
        if rng < 1e-6:
            # 该通道在这组波形参数下没有操纵权限（方差≈0）——如实展示
            stats[name] = float("nan")
            axes[j].text(0.5, 0.5, f"{name}：该组波形参数下\n操纵权限≈0\n"
                         f"（期望方差 {w.std():.2e}）",
                         ha="center", va="center", transform=axes[j].transAxes,
                         fontsize=11, color="tab:red")
            axes[j].set_title(f"{name}：无操纵通道")
            continue
        rel = np.abs(err).mean() / rng
        stats[name] = rel
        axes[j].scatter(w, wh, s=3, alpha=0.35)
        lo = min(w.min(), wh.min()); hi = max(w.max(), wh.max())
        axes[j].plot([lo, hi], [lo, hi], "r--", lw=1)
        axes[j].set_title(f"{name}：期望 vs 实际\n平均相对误差 {rel*100:.1f}%")
        axes[j].set_xlabel("期望 W*"); axes[j].set_ylabel("实际 Ŵ")
    fig.suptitle("内环逆映射验证：期望扳手 → 网络反解波形参数 → 前代物理复核")
    fig.tight_layout()
    out1 = os.path.join(FIGS, "wave_inverse_scatter.png")
    fig.savefig(out1, dpi=140, bbox_inches="tight")
    print("[fig]", out1)

    # 前向映射非线性切片：Mx(dA, f) 热图（为什么需要学习的论据）
    gs = 25
    fs = np.linspace(*RANGES["f"], gs)
    das = np.linspace(*RANGES["dA"], gs)
    MX = np.zeros((gs, gs))
    FZ = np.zeros((gs, gs))
    for i, f in enumerate(fs):
        for k, da in enumerate(das):
            w = forward([f, da, 0.56, -0.02, 0.0])
            FZ[k, i], MX[k, i] = w[0], w[1]
    fig2, ax2 = plt.subplots(1, 2, figsize=(11, 4.0))
    c0 = ax2[0].contourf(fs, das, FZ, levels=20, cmap="viridis")
    ax2[0].set_title("前向映射 Fz(f, dA)\n（多值性：同一升力有无数组合）")
    ax2[0].set_xlabel("拍频 f (Hz)"); ax2[0].set_ylabel("翼差动 dA (rad)")
    fig2.colorbar(c0, ax=ax2[0], label="N")
    c1 = ax2[1].contourf(fs, das, MX, levels=20, cmap="RdBu_r")
    ax2[1].set_title("前向映射 Mx(f, dA)\n（非线性：增益随拍频漂移）")
    ax2[1].set_xlabel("拍频 f (Hz)"); ax2[1].set_ylabel("翼差动 dA (rad)")
    fig2.colorbar(c1, ax=ax2[1], label="N·m")
    fig2.tight_layout()
    out2 = os.path.join(FIGS, "wave_forward_map.png")
    fig2.savefig(out2, dpi=140, bbox_inches="tight")
    print("[fig]", out2)
    return stats


def main():
    cache = os.path.join(DATA, "wave_inverse.npz")
    if os.path.exists(cache):
        d = np.load(cache)
        TH, W = d["TH"], d["W"]
        print(f"[data] 复用缓存 {cache}（{len(TH)} 样本）")
    else:
        TH, W = gen_dataset()
    net, TH_va, _ = train_inverse(TH, W)
    import torch
    torch.save({"state_dict": net.state_dict(),
                "w_mu": net._w_mu, "w_sd": net._w_sd,
                "t_mu": net._t_mu, "t_sd": net._t_sd},
               os.path.join(DATA, "wave_inverse_policy.pt"))
    W_va_raw = np.array([forward(t) for t in TH_va])
    stats = validate_and_plot(net, TH_va, W_va_raw)
    print("[result] 平均相对误差:", {k: f"{v*100:.1f}%" for k, v in stats.items()})


if __name__ == "__main__":
    main()
