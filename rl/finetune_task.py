# -*- coding: utf-8 -*-
"""
finetune_task.py — 有余量任务族上的生成式微调（I-076）
========================================================
沿用 I-071 第四次尝试的全部稳定件（评论员预热、价值中心化、速度场
行为锚定、同噪声轨迹锚定系数二十、策略学习率五e负六、每八次评论员
更新一次策略更新），差异只有两处：
1. 回合来自任务族（高度阶跃跟踪与突风扰动恢复，tasks_i076 模块），
   每迭代两类任务各采三回合；
2. 奖励换成任务奖励（高度项跟当期指令，其余项沿用环境奖励形状），
   评论员学的价值是任务回报的价值。
条件通道、部署机制、外环增益与 I-071 完全一致，保证配对评估的两臂
唯一差异仍是逆映射网络权重。
输出：rl/data/ft_task_ckpt.pt + rl/data/ft_task_policy_it*.pt + 训练日志
用法：python rl/finetune_task.py   （突风幅度须先标定并写入幅度文件）
"""
import os
import sys
import time

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import torch
import torch.nn as nn

from rl.waveform_inverse import DATA
from rl.wave_inverse_cfm import VelocityNet
from rl.finetune_cfm import (flow_sample, deploy_inverse, fast_forward,
                             Critic, anchor_loss, load_anchor_dataset)
import rl.tasks_i076 as tk

CKPT = os.path.join(DATA, "ft_task_ckpt.pt")
AMP_PATH = os.path.join(DATA, "gust_amp_selected.txt")
LOG = os.path.join(DATA, "ft_task_train.log")

GAMMA = 0.99
TAU = 0.005
LR_CRITIC = 1e-3
LR_ACTOR = 5e-6
LAMBDA_ANCHOR = 1.0
BETA_TRAJ = 20.0
CRITIC_WARMUP = 300
EPISODES_PER_TASK = 3
ITERS_TOTAL = 5
UPDATES_PER_ITER = 400
ACTOR_EVERY = 8
BATCH = 256
X0_DRAWS = 2
BUFFER_CAP = 60000
TRAIN_SEED_BASE_A = 1200      # 任务甲训练种子
TRAIN_SEED_BASE_B = 1300      # 任务乙训练种子
# 健康探针种子（选取依据：配对协议预检（2026-10-05）中冻结臂全部
# 存活且步回报为正——阶跃 1401 为正 0.519、1405 为正 0.316，突风
# 1403 为正 0.340、1409 为正 0.248；探针域与评估种子 1000~1015、
# 1100~1115 及训练种子 1200/1300 起均不相交）
PROBE = {tk.TASK_STEP: (1401, 1405), tk.TASK_GUST: (1403, 1409)}


def load_amp():
    with open(AMP_PATH, encoding="utf-8") as f:
        return float(f.read().strip())


def collect_episode(net, norm, task, seed):
    """部署机制跑任务回合，返回转移列表（条件归一化与 I-071 同口径）。"""
    torch.manual_seed(tk.SEED_BASE[task] + seed + 50000)   # 训练采集种子域
    outer = tk.TaskOuterLoop()
    with tk.episode_span(task) as n_steps:
        env = tk.FlapperHoverEnvV2(randomize=True, obs_noise=True,
                                   action_channels=("freq", "dA", "split",
                                                    "lag", "mean"))
        obs, _ = env.reset(seed=seed)
        base_wind = env.wind_x
        trans = []
        vz_prev = float(obs[1])
        W_hat_prev = None
        for _ in range(n_steps):
            env.wind_x = base_wind + (tk.gust_offset(env.t)
                                      if task == tk.TASK_GUST else 0.0)
            W_star = outer.step(obs, env.t)
            th, _ = deploy_inverse(net, norm, W_star)
            W_hat_prev = fast_forward(th)
            a = tk.clc.theta_to_action(th)
            obs, r_env, term, trunc, _ = env.step(a)
            outer.mit_update(W_hat_prev, vz_prev, float(obs[1]))
            vz_prev = float(obs[1])
            r = tk.task_reward(task, env.state, a, env.t)
            c_n = ((W_star - np.asarray(norm[0])) / np.asarray(norm[1]))
            a_n = np.clip((th - np.asarray(norm[2])) / np.asarray(norm[3]),
                          -1.0, 1.0)
            trans.append((c_n.astype(np.float32), a_n.astype(np.float32),
                          float(r), float(term or trunc)))
            if term or trunc:
                break
        n = len(trans)
        return trans, {"alive": n >= n_steps,
                       "rps": sum(t[2] for t in trans) / max(n, 1)}


def main():
    torch.manual_seed(0)
    amp = load_amp()
    tk.set_gust_amp(amp)
    print(f"[FT-T] 突风幅度 {amp} m/s（标定文件）", flush=True)
    ck = torch.load(os.path.join(DATA, "wave_inverse_cfm.pt"),
                    weights_only=False)
    net = VelocityNet()
    net.load_state_dict(ck["state_dict"])
    net0 = VelocityNet()
    net0.load_state_dict(ck["state_dict"])
    for p in net0.parameters():
        p.requires_grad_(False)
    norm = (ck["w_mu"], ck["w_sd"], ck["t_mu"], ck["t_sd"])
    critic = Critic()
    critic_target = Critic()
    critic_target.load_state_dict(critic.state_dict())
    net_target = VelocityNet()
    net_target.load_state_dict(net.state_dict())
    for p in critic_target.parameters():
        p.requires_grad_(False)
    for p in net_target.parameters():
        p.requires_grad_(False)
    opt_critic = torch.optim.Adam(critic.parameters(), lr=LR_CRITIC)
    opt_actor = torch.optim.Adam(net.parameters(), lr=LR_ACTOR)

    it_start = 0
    buf = {"c": [], "a": [], "r": [], "d": []}
    X_ds, C_ds = load_anchor_dataset()

    if os.path.exists(CKPT):
        s = torch.load(CKPT, weights_only=False)
        net.load_state_dict(s["net"])
        critic.load_state_dict(s["critic"])
        critic_target.load_state_dict(s["critic_target"])
        net_target.load_state_dict(s["net_target"])
        opt_critic.load_state_dict(s["opt_critic"])
        opt_actor.load_state_dict(s["opt_actor"])
        it_start = s["iter"]
        buf = s["buffer"]
        print(f"[FT-T] 从断点 iter={it_start} 续训", flush=True)

    logf = open(LOG, "a", encoding="utf-8")
    t0 = time.time()
    for it in range(it_start, ITERS_TOTAL):
        ep_stats = {tk.TASK_STEP: [], tk.TASK_GUST: []}
        for task, sbase in ((tk.TASK_STEP, TRAIN_SEED_BASE_A),
                            (tk.TASK_GUST, TRAIN_SEED_BASE_B)):
            for e in range(EPISODES_PER_TASK):
                seed = sbase + (it * EPISODES_PER_TASK + e) % 60
                trans, st = collect_episode(net, norm, task, seed)
                buf["c"].extend([t_[0] for t_ in trans])
                buf["a"].extend([t_[1] for t_ in trans])
                buf["r"].extend([t_[2] for t_ in trans])
                buf["d"].extend([t_[3] for t_ in trans])
                ep_stats[task].append(st)
        for key in buf:
            buf[key] = buf[key][-BUFFER_CAP:]
        line = (f"[FT-T] iter {it}  采集 阶跃存活 "
                f"{np.mean([s['alive'] for s in ep_stats[tk.TASK_STEP]]):.2f}"
                f"/回报 {np.mean([s['rps'] for s in ep_stats[tk.TASK_STEP]]):+.3f}"
                f"  突风存活 "
                f"{np.mean([s['alive'] for s in ep_stats[tk.TASK_GUST]]):.2f}"
                f"/回报 {np.mean([s['rps'] for s in ep_stats[tk.TASK_GUST]]):+.3f}"
                f"  缓冲 {len(buf['r'])}  用时 {time.time()-t0:.0f}s")
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

        cs = torch.as_tensor(np.array(buf["c"]), dtype=torch.float32)
        a_s = torch.as_tensor(np.array(buf["a"]), dtype=torch.float32)
        rs = torch.as_tensor(np.array(buf["r"]), dtype=torch.float32)
        ds = torch.as_tensor(np.array(buf["d"]), dtype=torch.float32)
        n = len(rs)
        q_trace, anc_trace = [], []
        for u in range(UPDATES_PER_ITER):
            idx = torch.randint(0, n, (BATCH,))
            c_b, a_b = cs[idx], a_s[idx]
            r_b, d_b = rs[idx], ds[idx]
            with torch.no_grad():
                x0 = torch.randn(len(c_b), 5)
                a_next = torch.clamp(flow_sample(net_target, c_b, x0),
                                     -1.0, 1.0)
                y = r_b + GAMMA * (1 - d_b) * critic_target(c_b, a_next)
            q = critic(c_b, a_b)
            loss_q = nn.functional.smooth_l1_loss(q, y)
            opt_critic.zero_grad()
            loss_q.backward()
            nn.utils.clip_grad_norm_(critic.parameters(), 0.5)
            opt_critic.step()
            q_trace.append(loss_q.item())

            if u % ACTOR_EVERY == 0 and (it > 0 or u >= CRITIC_WARMUP):
                idx = torch.randint(0, n, (BATCH,))
                c_b, a_b = cs[idx], a_s[idx]
                with torch.no_grad():
                    q_base = critic(c_b, a_b).mean()
                x0 = torch.randn(len(c_b) // X0_DRAWS, 5).repeat(X0_DRAWS, 1)
                a_raw = flow_sample(net, c_b, x0)
                a_pred = torch.clamp(a_raw, -1.0, 1.0)
                with torch.no_grad():
                    a_ref = torch.clamp(flow_sample(net0, c_b, x0),
                                        -1.0, 1.0)
                loss_actor = (
                    -(critic(c_b, a_pred).mean() - q_base)
                    + LAMBDA_ANCHOR * anchor_loss(net, X_ds, C_ds)
                    + BETA_TRAJ * nn.functional.mse_loss(a_raw, a_ref))
                opt_actor.zero_grad()
                loss_actor.backward()
                nn.utils.clip_grad_norm_(net.parameters(), 0.5)
                opt_actor.step()
                anc_trace.append(loss_actor.item())

            with torch.no_grad():
                for p, pt in zip(critic.parameters(),
                                 critic_target.parameters()):
                    pt.mul_(1 - TAU).add_(TAU * p)
                for p, pt in zip(net.parameters(), net_target.parameters()):
                    pt.mul_(1 - TAU).add_(TAU * p)

        # 健康探针（配对协议，冻结臂预检存活的种子）
        probe_rps = []
        for task, seeds in PROBE.items():
            for s in seeds:
                st = tk.run_task_episode(net, norm, task, s,
                                         record_traj=False)
                probe_rps.append(st["rps"])
        line = (f"[FT-T] iter {it}  更新 {UPDATES_PER_ITER} 步  "
                f"评论员损失 {np.mean(q_trace):.4f}  "
                f"策略+锚定损失 {np.mean(anc_trace):.4f}  "
                f"探针步回报均值 {np.mean(probe_rps):+.3f}")
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()
        torch.save({"net": net.state_dict(), "critic": critic.state_dict(),
                    "critic_target": critic_target.state_dict(),
                    "net_target": net_target.state_dict(),
                    "opt_critic": opt_critic.state_dict(),
                    "opt_actor": opt_actor.state_dict(), "iter": it + 1,
                    "buffer": buf}, CKPT)
        torch.save(net.state_dict(),
                   os.path.join(DATA, f"ft_task_policy_it{it}.pt"))
    logf.close()
    print("[FT-T] 全部迭代完成")


if __name__ == "__main__":
    main()
