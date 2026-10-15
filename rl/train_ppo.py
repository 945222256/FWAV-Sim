# -*- coding: utf-8 -*-
"""
train_ppo.py — 从 BC 策略热启动的 PPO 微调（纯 torch 紧凑实现）
================================================================
叙事线：辨识给随机化中心 → BC 蒸馏专家冷启动 → PPO 在随机化动力学上微调。
不依赖 stable-baselines3（用户环境没装），实现保持最小可读。
输出 rl/data/ppo_policy.pt + rl/data/ppo_curve.npz
"""
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl.hover_env import FlapperHoverEnv
from rl.train_bc import MlpPolicy, CKPT_BC

RL_DIR = os.path.dirname(os.path.abspath(__file__))
CKPT_PPO = os.path.join(RL_DIR, "data", "ppo_policy.pt")

GAMMA = 0.995
LAM = 0.95
CLIP = 0.2
LR = 1e-4   # 微调阶段降学习率（3e-4 实测把策略震出 BC 的好盆地）
ENT_COEF = 0.001
ROLLOUT = 4096
EPOCHS = 6
MINIBATCH = 512
ITERS = 36
# 分段续跑支持（本机会静默回收后台 python 进程，改为前台分段跑）：
# 每个分段跑 CHUNK_ITERS 轮就存盘退出，下次调用从断点继续。
CKPT_FULL = os.path.join(RL_DIR, "data", "ppo_ckpt.pt")
CHUNK_ITERS = int(os.environ.get("PPO_CHUNK", "12"))


class ValueNet(nn.Module):
    def __init__(self, obs_dim=5, hidden=128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(obs_dim, hidden), nn.Tanh(),
            nn.Linear(hidden, hidden), nn.Tanh(),
            nn.Linear(hidden, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


def main():
    torch.manual_seed(0)
    env = FlapperHoverEnv(randomize=True, obs_noise=0.002)
    policy = MlpPolicy()
    policy.load_state_dict(torch.load(CKPT_BC, weights_only=True))
    # BC 热启动微调必须收缩探索噪声：tanh 动作空间 [-1,1] 上 std=0.61 的
    # 探索会立刻摧毁 BC 行为（实测前 5 个 iter 回合均回报 -50，存活 <1s）
    policy.log_std.data.fill_(-2.0)
    value = ValueNet()
    opt = torch.optim.Adam(list(policy.parameters()) + list(value.parameters()),
                           lr=LR)
    it_start, curve = 0, []
    if os.path.exists(CKPT_FULL):
        ck = torch.load(CKPT_FULL, weights_only=False)
        policy.load_state_dict(ck["policy"])
        value.load_state_dict(ck["value"])
        opt.load_state_dict(ck["opt"])
        it_start, curve = ck["iter"], ck["curve"]
        print(f"[PPO] 从断点 iter={it_start} 续跑")

    obs, _ = env.reset(seed=1000 + it_start)
    t0 = time.time()
    for it in range(it_start, min(it_start + CHUNK_ITERS, ITERS)):
        # ---------- rollout ----------
        buf = {k: [] for k in ("obs", "act", "logp", "rew", "val", "done")}
        ep_ret, ep_len, eps = 0.0, 0, 0
        policy.eval()
        value.eval()
        with torch.no_grad():
            for _ in range(ROLLOUT):
                o = torch.as_tensor(obs, dtype=torch.float32)
                mu = policy(o)
                std = policy.log_std.exp()
                dist = torch.distributions.Normal(mu, std)
                a = dist.sample()
                logp = dist.log_prob(a).sum()
                a_np = a.numpy().clip(-1.0, 1.0)
                nobs, r, term, trunc, _ = env.step(a_np)
                buf["obs"].append(o.numpy())
                buf["act"].append(a_np)
                buf["logp"].append(logp.item())
                buf["rew"].append(r)
                buf["val"].append(value(o).item())
                buf["done"].append(float(term or trunc))
                ep_ret += r
                ep_len += 1
                if term or trunc:
                    eps += 1
                    obs, _ = env.reset()
                else:
                    obs = nobs
        # ---------- GAE ----------
        rew = np.array(buf["rew"], dtype=np.float32)
        val = np.array(buf["val"] + [value(
            torch.as_tensor(obs, dtype=torch.float32)).item()], dtype=np.float32)
        done = np.array(buf["done"], dtype=np.float32)
        adv = np.zeros_like(rew)
        gae = 0.0
        for t in reversed(range(len(rew))):
            delta = rew[t] + GAMMA * val[t + 1] * (1 - done[t]) - val[t]
            gae = delta + GAMMA * LAM * (1 - done[t]) * gae
            adv[t] = gae
        ret_t = torch.as_tensor(adv + val[:-1])
        adv_t = torch.as_tensor(adv)
        adv_t = (adv_t - adv_t.mean()) / (adv_t.std() + 1e-8)
        obs_t = torch.as_tensor(np.array(buf["obs"]), dtype=torch.float32)
        act_t = torch.as_tensor(np.array(buf["act"]), dtype=torch.float32)
        logp_t = torch.as_tensor(np.array(buf["logp"]), dtype=torch.float32)

        # ---------- update ----------
        policy.train()
        value.train()
        idx = np.arange(len(obs_t))
        for _ in range(EPOCHS):
            np.random.shuffle(idx)
            for s in range(0, len(idx), MINIBATCH):
                mb = idx[s:s + MINIBATCH]
                mu = policy(obs_t[mb])
                std = policy.log_std.exp()
                dist = torch.distributions.Normal(mu, std)
                logp = dist.log_prob(act_t[mb]).sum(-1)
                ratio = (logp - logp_t[mb]).exp()
                s1 = ratio * adv_t[mb]
                s2 = torch.clamp(ratio, 1 - CLIP, 1 + CLIP) * adv_t[mb]
                pg = -torch.min(s1, s2).mean()
                ent = dist.entropy().sum(-1).mean()
                v = value(obs_t[mb])
                vf = ((v - ret_t[mb]) ** 2).mean()
                loss = pg - ENT_COEF * ent + 0.5 * vf
                opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(
                    list(policy.parameters()) + list(value.parameters()), 0.5)
                opt.step()
        mean_ret = ep_ret / max(1, eps)
        curve.append(mean_ret)
        print(f"[PPO] iter {it:3d}  回合均回报 {mean_ret:+8.1f}  "
              f"回合数 {eps}  用时 {time.time()-t0:.0f}s", flush=True)
        torch.save({"policy": policy.state_dict(), "value": value.state_dict(),
                    "opt": opt.state_dict(), "iter": it + 1, "curve": curve},
                   CKPT_FULL)

    torch.save(policy.state_dict(), CKPT_PPO)
    np.savez(os.path.join(RL_DIR, "data", "ppo_curve.npz"), ret=np.array(curve))
    done_all = (it_start + CHUNK_ITERS) >= ITERS
    print(f"[PPO] 本段完成，断点 iter={it_start + CHUNK_ITERS}"
          + ("（全部完成）" if done_all else "（需继续下一段）"))


if __name__ == "__main__":
    main()
