# -*- coding: utf-8 -*-
"""
run_v2_baseline.py — V2 基线全流程：采集 → BC 训练 → 闭环评估 → 图
====================================================================
V2 契约（2026-09-20）：台架真值参数 + 完整域随机化（质量/惯量/升力系数/
执行器滞后/分通道传感噪声/风扰 ±1.5）+ 7 维观测（含拍内相位 sinφ/cosφ）。
输出：rl/data/v2_demos.npz, v2_bc_policy.pt, figs/v2_bc_loss.png,
      figs/v2_closed_loop.png, data/v2_eval_table.md
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl.hover_env_v2 import FlapperHoverEnvV2, DT_CTRL, Z_REF
from rl.expert_v2 import HoverExpertV2

RL_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(RL_DIR, "data")
FIGS = os.path.join(RL_DIR, "figs")


def collect(n_episodes=60, seed0=200):
    obs_all, act_all = [], []
    kept = dropped = 0
    for k in range(n_episodes):
        env = FlapperHoverEnvV2(randomize=True, obs_noise=True)
        obs, _ = env.reset(seed=seed0 + k)
        ex = HoverExpertV2()
        ep_o, ep_a = [], []
        alive = True
        for _ in range(500):
            a = ex.act(obs)
            ep_o.append(obs.copy())
            ep_a.append(a.copy())
            obs, _, term, trunc, _ = env.step(a)
            if term:
                alive = False
                break
            if trunc:
                break
        if alive and len(ep_o) >= 500:
            kept += 1
            obs_all.extend(ep_o[::2])
            act_all.extend(ep_a[::2])
        else:
            dropped += 1
    obs_all = np.asarray(obs_all, dtype=np.float32)
    act_all = np.asarray(act_all, dtype=np.float32)
    np.savez_compressed(os.path.join(DATA, "v2_demos.npz"),
                        obs=obs_all, act=act_all)
    print(f"[V2 collect] 保留 {kept}/{n_episodes}（弃 {dropped}），"
          f"样本 {len(obs_all)}")
    return obs_all, act_all


def train_bc(obs, act, epochs=30, batch=512, lr=1e-3):
    import torch
    import torch.nn as nn
    from rl.train_bc import MlpPolicy
    x = torch.as_tensor(obs)
    y = torch.as_tensor(act)
    n_val = max(1, int(len(x) * 0.1))
    perm = torch.randperm(len(x))
    va, tr = perm[:n_val], perm[n_val:]
    pol = MlpPolicy(obs_dim=7, act_dim=2)
    opt = torch.optim.Adam(pol.parameters(), lr=lr)
    fn = nn.MSELoss()
    hist = {"train": [], "val": []}
    for ep in range(epochs):
        for i in range(0, len(tr), batch):
            idx = tr[i:i + batch]
            loss = fn(pol(x[idx]), y[idx])
            opt.zero_grad(); loss.backward(); opt.step()
        with torch.no_grad():
            v = fn(pol(x[va]), y[va]).item()
        hist["train"].append(loss.item()); hist["val"].append(v)
        if ep % 5 == 0 or ep == epochs - 1:
            print(f"[V2 BC] epoch {ep:3d} train={loss.item():.5f} val={v:.5f}")
    torch.save(pol.state_dict(), os.path.join(DATA, "v2_bc_policy.pt"))
    np.savez(os.path.join(DATA, "v2_bc_loss.npz"),
             train=np.array(hist["train"]), val=np.array(hist["val"]))
    return pol


def evaluate(pol, n=16, seed0=600):
    from rl.expert_v2 import HoverExpertV2
    res = {}
    for name, fn in (("专家V2(力矩分配PD)", None), ("BC-V2", pol)):
        stats = []
        for s in range(seed0, seed0 + n):
            env = FlapperHoverEnvV2(randomize=True, obs_noise=True)
            obs, _ = env.reset(seed=s)
            ex = HoverExpertV2() if fn is None else None
            ret, steps = 0.0, 0
            traj = []
            for _ in range(500):
                a = ex.act(obs) if ex else fn.act_deterministic(obs)
                obs, r, term, trunc, _ = env.step(a)
                ret += r; steps += 1
                if s == seed0:
                    traj.append([env.t, env.state[0], env.state[2]])
                if term or trunc:
                    break
            stats.append({"alive": steps >= 500, "rps": ret / steps,
                          "z_err": abs(env.state[0] - Z_REF),
                          "traj": traj})
        res[name] = {
            "survival": float(np.mean([x["alive"] for x in stats])),
            "rps": float(np.mean([x["rps"] for x in stats])),
            "z_err": float(np.mean([x["z_err"] for x in stats if x["alive"]]
                                   or [float("nan")])),
            "traj": np.array(stats[0]["traj"]),
        }
        r = res[name]
        print(f"[V2 eval] {name:<18} 存活 {r['survival']*100:.0f}%  "
              f"步回报 {r['rps']:+.3f}  稳态|z-1.5| {r['z_err']:.3f} m")
    return res


def plot_all(res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                       "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    os.makedirs(FIGS, exist_ok=True)

    d = np.load(os.path.join(DATA, "v2_bc_loss.npz"))
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    ax.plot(d["train"], label="训练集", lw=1.6)
    ax.plot(d["val"], label="验证集", lw=1.6)
    ax.set_xlabel("epoch"); ax.set_ylabel("MSE")
    ax.set_title("V2 行为克隆训练曲线（完整域随机化契约）")
    ax.legend(); ax.grid(alpha=0.3); fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "v2_bc_loss.png"), dpi=140,
                bbox_inches="tight")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0))
    for (name, r), c in zip(res.items(), ("tab:blue", "tab:red")):
        tj = r["traj"]
        axes[0].plot(tj[:, 0], tj[:, 1], label=name, lw=1.5, color=c)
        axes[1].plot(tj[:, 0], tj[:, 2], label=name, lw=1.5, color=c)
    axes[0].axhline(Z_REF, ls="--", c="gray", lw=0.8)
    axes[0].set_title("高度通道 z(t)"); axes[0].set_xlabel("t (s)")
    axes[0].set_ylabel("z (m)"); axes[0].legend()
    axes[1].axhline(0.0, ls="--", c="gray", lw=0.8)
    axes[1].set_title("滚转通道 roll(t)"); axes[1].set_xlabel("t (s)")
    axes[1].set_ylabel("roll (rad)"); axes[1].legend()
    fig.suptitle("V2 闭环验证：专家（B_dA(f) 力矩分配）vs 行为克隆"
                 "（质量/惯量/升力/执行器/噪声/风扰全随机化）")
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "v2_closed_loop.png"), dpi=140,
                bbox_inches="tight")
    print("[fig] v2_bc_loss.png / v2_closed_loop.png")

    lines = ["| 策略 | 存活率 | 平均步回报 | 稳态 |z-1.5| |",
             "|---|---|---|---|"]
    for name, r in res.items():
        lines.append(f"| {name} | {r['survival']*100:.0f}% | "
                     f"{r['rps']:+.3f} | {r['z_err']:.3f} m |")
    with open(os.path.join(DATA, "v2_eval_table.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    t0 = time.time()
    obs, act = collect()
    pol = train_bc(obs, act)
    res = evaluate(pol)
    plot_all(res)
    print(f"[V2] 全流程 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
