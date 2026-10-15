# -*- coding: utf-8 -*-
"""
eval_closed_loop.py — 闭环评估：专家 / BC / PPO 三策略对比
==========================================================
在固定种子集上跑闭环，输出存活率、平均步回报、|z-1.5| 稳态误差，
并画 z(t)/roll(t) 对比图 → rl/figs/closed_loop_compare.png
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl.hover_env import FlapperHoverEnv, DT_CTRL, Z_REF
from rl.expert import HoverExpert

RL_DIR = os.path.dirname(os.path.abspath(__file__))
FIGS = os.path.join(RL_DIR, "figs")


def run_episode(policy_fn, seed, record=False):
    env = FlapperHoverEnv(randomize=True, obs_noise=0.002)
    obs, _ = env.reset(seed=seed)
    ret, n = 0.0, 0
    traj = []
    for _ in range(500):
        a = policy_fn(obs)
        obs, r, term, trunc, _ = env.step(a)
        ret += r
        n += 1
        if record:
            traj.append([env.t, env.state[0], env.state[2]])
        if term or trunc:
            break
    alive = n >= 500
    z_err = abs(env.state[0] - Z_REF)
    return {"alive": alive, "ret_per_step": ret / n, "z_err": z_err,
            "traj": np.array(traj) if record else None}


def eval_policy(name, policy_fn, seeds=range(500, 516), record_seed=500):
    stats = []
    for s in seeds:
        stats.append(run_episode(policy_fn, s, record=(s == record_seed)))
    surv = np.mean([x["alive"] for x in stats])
    ret = np.mean([x["ret_per_step"] for x in stats])
    zerr = np.mean([x["z_err"] for x in stats if x["alive"]] or [float("nan")])
    print(f"[eval] {name:<12} 存活率 {surv*100:5.1f}%  步回报 {ret:+.3f}  "
          f"稳态|z-1.5| {zerr:.3f} m")
    return {"name": name, "survival": surv, "ret": ret, "z_err": zerr,
            "traj": stats[0]["traj"]}


def main():
    import torch
    from rl.train_bc import MlpPolicy, CKPT_BC

    results = []

    ex = HoverExpert(kp_r=0.10, kd_r=0.02)
    results.append(eval_policy("专家(PD)", lambda o: ex.act(o)))

    pol_bc = MlpPolicy()
    pol_bc.load_state_dict(torch.load(CKPT_BC, weights_only=True))
    results.append(eval_policy("BC", pol_bc.act_deterministic))

    ppo_path = os.path.join(RL_DIR, "data", "ppo_policy.pt")
    if os.path.exists(ppo_path):
        pol_ppo = MlpPolicy()
        pol_ppo.load_state_dict(torch.load(ppo_path, weights_only=True))
        results.append(eval_policy("BC+PPO", pol_ppo.act_deterministic))

    os.makedirs(FIGS, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for res in results:
        tj = res["traj"]
        axes[0].plot(tj[:, 0], tj[:, 1], label=res["name"], lw=1.4)
        axes[1].plot(tj[:, 0], tj[:, 2], label=res["name"], lw=1.4)
    axes[0].axhline(Z_REF, ls="--", c="gray", lw=0.8)
    axes[0].set_title("高度通道 z(t)")
    axes[0].set_xlabel("t (s)")
    axes[0].set_ylabel("z (m)")
    axes[0].legend()
    axes[1].axhline(0.0, ls="--", c="gray", lw=0.8)
    axes[1].set_title("滚转通道 roll(t)")
    axes[1].set_xlabel("t (s)")
    axes[1].set_ylabel("roll (rad)")
    fig.suptitle("扑翼悬停闭环对比：专家 PD / 行为克隆 / BC+PPO（随机化动力学 + 观测噪声）")
    fig.tight_layout()
    out = os.path.join(FIGS, "closed_loop_compare.png")
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"[eval] 对比图 → {out}")

    # 汇总表（控制台 + markdown）
    lines = ["| 策略 | 存活率 | 平均步回报 | 稳态 |z-1.5| |",
             "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['name']} | {r['survival']*100:.0f}% | "
                     f"{r['ret']:+.3f} | {r['z_err']:.3f} m |")
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(RL_DIR, "data", "eval_table.md"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")


if __name__ == "__main__":
    main()
