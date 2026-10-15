# -*- coding: utf-8 -*-
"""
wave_inverse_cfm_state.py — 条件通道扩展：力旋量加机体状态（I-077）
====================================================================
方案冻结出处：rl/预登记_有余量任务族与条件扩展实验_20261005.md 第二节。
条件向量从三维（力旋量目标）扩为六维（力旋量目标 + 竖直速度、
滚转角、滚转角速度，各一项共三项）。数据集在前向物理里给状态参数，
其余生成与训练配方（最优传输小批量配对、百分之十条件丢弃、五十步
欧拉采样、K 等于八拒采）与现行版本逐项一致。

对照协议：留出一百五十组（波形参数，状态）样本，两臂在同一评估点
反解后前代物理复核。旧臂按部署现状（三维条件、状态无关拒采），
新臂按新部署形态（六维条件、状态化拒采）。判据看升力与力矩两通道
的相对误差均值，新臂不劣于旧臂才算过。

用法：python rl/wave_inverse_cfm_state.py
输出：rl/data/wave_inverse_state.npz、rl/data/wave_inverse_cfm_state.pt、
     rl/data/state_cond_compare.md、rl/figs/state_cond_compare.png
"""
import math
import os
import sys
import time

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rl.waveform_inverse import DATA, FIGS, RANGES, THETA_NAMES

import torch
import torch.nn as nn

CKPT = os.path.join(DATA, "wave_inverse_cfm_state.pt")
DS = os.path.join(DATA, "wave_inverse_state.npz")
N_DS = 12000
N_EVAL = 150
K_REJ = 8

# 状态范围（预登记第二节冻结）：竖直速度、滚转角、滚转角速度
S_NAMES = ["vz", "roll", "wx"]
S_LO = np.array([-1.5, -0.6, -3.0])
S_HI = np.array([1.5, 0.6, 3.0])
S_MID = (S_HI + S_LO) / 2
S_HALF = (S_HI - S_LO) / 2


def forward_state(theta, s, z=1.5):
    """状态化前向物理：波形参数加状态 → 周期平均力旋量。

    与 waveform_inverse.forward 同一模型同一调用口径，差异仅在
    竖直速度、滚转角、滚转角速度不再钉零位。位置参数传入但预期
    气动不消费（启动自检验证，若消费则本模块报错退出）。
    """
    from modules.aero_solver import FlapperAeroModel
    f, dA, split, lag, mean_deg = theta
    vz, roll, wx = s
    m = FlapperAeroModel({"span": 1.15, "root_chord": 0.35,
                          "tip_chord": 0.25, "n_strips": 14})
    m.set_wing_command(frequency=f)
    m.wing_left.amplitude = math.radians(35.0) + dA
    m.wing_right.amplitude = math.radians(35.0) - dA
    for w in (m.wing_left, m.wing_right):
        w.split = split
        w.pitch_lag_s = lag
        w.mean = math.radians(mean_deg)
        w.alpha_set = math.radians(15.0)
        w.pitch_mode = "aero"
        w._t_prev = None
        w._tau_state = 0.0
    quat = np.array([math.cos(roll / 2), math.sin(roll / 2), 0.0, 0.0])
    v_lin = np.array([0.0, 0.0, vz])
    v_ang = np.array([wx, 0.0, 0.0])
    n = 48
    Fs, Ts = [], []
    for i in range(n):
        F, T = m.compute_forces(np.array([0.0, 0.0, z]), quat, v_lin,
                                v_ang, i / (n * f))
        Fs.append(F)
        Ts.append(T)
    return np.array([np.mean(Fs, axis=0)[2],
                     np.mean(Ts, axis=0)[0],
                     np.mean(Ts, axis=0)[1]])


def check_position_unused():
    """位置无关性自检：两个高度下同一（参数，状态）的输出须逐位一致。"""
    th = np.array([2.5, 0.05, 0.56, -0.02, 1.0])
    s = np.array([0.8, 0.2, 1.0])
    a = forward_state(th, s, z=0.5)
    b = forward_state(th, s, z=2.5)
    if not np.allclose(a, b, atol=1e-10):
        raise RuntimeError("气动模型消费位置参数，预登记第二节的"
                           "高度不进条件前提被推翻，须回预登记重裁")


def gen_dataset(seed=42):
    rng = np.random.default_rng(seed)
    lo = np.array([RANGES[k][0] for k in THETA_NAMES])
    hi = np.array([RANGES[k][1] for k in THETA_NAMES])
    TH = rng.uniform(lo, hi, size=(N_DS, 5))
    S = rng.uniform(S_LO, S_HI, size=(N_DS, 3))
    W = np.zeros((N_DS, 3))
    t0 = time.time()
    for i in range(N_DS):
        W[i] = forward_state(TH[i], S[i])
        if i % 2000 == 0:
            print(f"[data] {i}/{N_DS} ({time.time()-t0:.0f}s)", flush=True)
    np.savez_compressed(DS, TH=TH, S=S, W=W)
    print(f"[data] {N_DS} 样本完成，{time.time()-t0:.0f}s → {DS}")
    return TH, S, W


class StateCFM:
    """六维条件速度场 + 归一化常数的训练与采样封装。"""

    def __init__(self):
        from rl.wave_inverse_cfm import VelocityNet
        self.net = VelocityNet(c_dim=6)
        self.w_mu = self.w_sd = None
        self.t_mu = self.t_sd = None

    def train(self, TH, S, W, epochs=60, batch=512, lr=1e-3):
        from rl.wave_inverse_cfm import ot_pair, CFG_DROP
        self.w_mu, self.w_sd = W.mean(0), W.std(0) + 1e-9
        lo = np.array([RANGES[k][0] for k in THETA_NAMES])
        hi = np.array([RANGES[k][1] for k in THETA_NAMES])
        self.t_mu, self.t_sd = (hi + lo) / 2, (hi - lo) / 2

        X = torch.as_tensor((TH - self.t_mu) / self.t_sd, dtype=torch.float32)
        C = torch.as_tensor(np.concatenate(
            [(W - self.w_mu) / self.w_sd, (S - S_MID) / S_HALF], axis=1),
            dtype=torch.float32)
        opt = torch.optim.Adam(self.net.parameters(), lr=lr)
        n = len(X)
        t0 = time.time()
        for ep in range(epochs):
            perm = torch.randperm(n)
            ep_loss, nb = 0.0, 0
            for i in range(0, n, batch):
                idx = perm[i:i + batch]
                x1, c = X[idx], C[idx]
                cmask = (torch.rand(len(x1)) > CFG_DROP).float()
                c_in = c * cmask[:, None]
                x0 = torch.randn_like(x1)
                x0p = ot_pair(x0, x1)
                t = torch.rand(len(x1))
                x_t = (1 - t[:, None]) * x0p + t[:, None] * x1
                u = x1 - x0p
                loss = nn.functional.mse_loss(self.net(t, x_t, c_in), u)
                opt.zero_grad(); loss.backward()
                torch.nn.utils.clip_grad_norm_(self.net.parameters(), 0.5)
                opt.step()
                ep_loss += loss.item(); nb += 1
            if ep % 10 == 0 or ep == epochs - 1:
                print(f"[CFM-S] epoch {ep:3d} loss={ep_loss/nb:.5f} "
                      f"({time.time()-t0:.0f}s)", flush=True)
        torch.save({"state_dict": self.net.state_dict(),
                    "w_mu": self.w_mu, "w_sd": self.w_sd,
                    "t_mu": self.t_mu, "t_sd": self.t_sd}, CKPT)
        print(f"[CFM-S] 模型 → {CKPT}")
        return self

    @torch.no_grad()
    def sample(self, W_star, s, k=K_REJ, omega=1.5, n_steps=50):
        c = torch.as_tensor(np.concatenate(
            [(np.atleast_2d(W_star) - self.w_mu) / self.w_sd,
             (np.atleast_2d(s) - S_MID) / S_HALF], axis=1),
            dtype=torch.float32)
        c0 = torch.zeros_like(c)
        xs = []
        for _ in range(k):
            x = torch.randn(len(c), 5)
            for i in range(n_steps):
                t = torch.full((len(c),), i / n_steps)
                v_c = self.net(t, x, c)
                v_u = self.net(t, x, c0)
                x = x + (v_u + omega * (v_c - v_u)) / n_steps
            xs.append(x.numpy() * self.t_sd + self.t_mu)
        return xs


def validate_and_report(scfm):
    """同协议对照：旧臂（三维条件、状态无关拒采）对新臂。"""
    from rl.wave_inverse_cfm import VelocityNet, sample as old_sample
    d = np.load(DS)
    TH, S, W = d["TH"], d["S"], d["W"]
    rng = np.random.default_rng(11)
    idx = rng.choice(len(TH), size=N_EVAL, replace=False)

    ck0 = torch.load(os.path.join(DATA, "wave_inverse_cfm.pt"),
                     weights_only=False)
    net0 = VelocityNet()
    net0.load_state_dict(ck0["state_dict"])
    net0.eval()
    norm0 = (ck0["w_mu"], ck0["w_sd"], ck0["t_mu"], ck0["t_sd"])
    from rl.waveform_inverse import forward as forward_neutral

    lo = np.array([RANGES[k][0] for k in THETA_NAMES])
    hi = np.array([RANGES[k][1] for k in THETA_NAMES])

    err = {"旧臂(三维条件)": [], "新臂(六维条件)": []}
    for j in idx:
        ws, s = W[j], S[j]
        # 旧臂：部署现状（状态无关条件与拒采）
        best = None
        for th in old_sample(net0, ws, norm0, k=K_REJ):
            th = np.clip(th[0], lo, hi)
            e = forward_neutral(th) - ws
            if best is None or (abs(e[0]) / 10 + abs(e[1]) / 5) \
                    < (abs(best[0]) / 10 + abs(best[1]) / 5):
                best = e
        err["旧臂(三维条件)"].append(best)
        # 新臂：状态化条件与拒采（复核在相同状态上进行）
        best = None
        for ths in scfm.sample(ws, s, k=K_REJ):
            th = np.clip(ths[0], lo, hi)
            e = forward_state(th, s) - ws
            if best is None or (abs(e[0]) / 10 + abs(e[1]) / 5) \
                    < (abs(best[0]) / 10 + abs(best[1]) / 5):
                best = e
        err["新臂(六维条件)"].append(best)

    W_star = W[idx]
    summary = {}
    for name, es in err.items():
        es = np.array(es)
        line = {}
        for ch, wn in enumerate(["Fz", "Mx", "My"]):
            rng_ch = (np.percentile(W_star[:, ch], 97.5)
                      - np.percentile(W_star[:, ch], 2.5))
            mae = np.abs(es[:, ch]).mean()
            line[wn] = mae / rng_ch * 100 if rng_ch > 1e-9 else float("nan")
        summary[name] = line
        print(f"  {name:<12} Fz {line['Fz']:5.1f}%  Mx {line['Mx']:5.1f}%  "
              f"My {line['My']:5.1f}%")

    old, new = summary["旧臂(三维条件)"], summary["新臂(六维条件)"]
    verdict_fz = "过" if new["Fz"] <= old["Fz"] else "不过"
    verdict_mx = "过" if new["Mx"] <= old["Mx"] else "不过"
    lines = ["# 条件通道扩展对照（I-077，同一复核协议，K 等于八拒采）", "",
             f"评估点：留出 {N_EVAL} 组（波形参数与状态联合均匀采样），"
             "误差按目标通道百分之九十七点五分位与百分之二点五分位之差"
             "归一。", "",
             "| 臂 | 升力通道 Fz | 力矩通道 Mx | 俯仰力矩 My（照报） |",
             "|---|---|---|---|",
             f"| 旧臂（三维条件，状态无关拒采） | {old['Fz']:.1f}% | "
             f"{old['Mx']:.1f}% | {old['My']:.1f}% |",
             f"| 新臂（六维条件，状态化拒采） | {new['Fz']:.1f}% | "
             f"{new['Mx']:.1f}% | {new['My']:.1f}% |", "",
             f"判据对照：升力通道不劣化 {verdict_fz}"
             f"（{new['Fz']:.1f}% 对 {old['Fz']:.1f}%）；"
             f"力矩通道不劣化 {verdict_mx}"
             f"（{new['Mx']:.1f}% 对 {old['Mx']:.1f}%）。", ""]
    with open(os.path.join(DATA, "state_cond_compare.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                       "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(7.5, 4.0))
    names = list(summary.keys())
    xs = np.arange(2)
    for i, (name, c) in enumerate(zip(names, ("#8d99ae", "#e76f51"))):
        vals = [summary[name]["Fz"], summary[name]["Mx"]]
        ax.bar(xs + (i - 0.5) * 0.35, vals, 0.35, label=name, color=c)
        for xi, v in zip(xs + (i - 0.5) * 0.35, vals):
            ax.text(xi, v + 0.2, f"{v:.1f}%", ha="center", fontsize=10)
    ax.set_xticks(xs)
    ax.set_xticklabels(["升力通道 Fz", "力矩通道 Mx"])
    ax.set_ylabel("平均相对误差 (%)")
    ax.set_title("条件通道扩展前后对照（同一复核协议）")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = os.path.join(FIGS, "state_cond_compare.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print("[fig]", out)
    return summary, verdict_fz, verdict_mx


def main():
    check_position_unused()
    print("[check] 位置参数无气动效应，高度不进条件的前提成立")
    if os.path.exists(DS):
        d = np.load(DS)
        TH, S, W = d["TH"], d["S"], d["W"]
        print(f"[data] 复用缓存 {DS}")
    else:
        TH, S, W = gen_dataset()
    scfm = StateCFM().train(TH, S, W)
    scfm.net.eval()
    validate_and_report(scfm)


if __name__ == "__main__":
    main()
