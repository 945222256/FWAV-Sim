# -*- coding: utf-8 -*-
"""
policy.py + train_bc.py — 策略网络定义与行为克隆训练
====================================================
MLP 5→128→128→2，tanh 输出 ∈[-1,1]²（归一化动作空间）。
BC 冷启动 + PPO 微调是主流 pipeline（本仓库的面试叙事主线）。
"""
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RL_DIR = os.path.dirname(os.path.abspath(__file__))
CKPT_BC = os.path.join(RL_DIR, "data", "bc_policy.pt")


class MlpPolicy(nn.Module):
    def __init__(self, obs_dim=5, act_dim=2, hidden=128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(obs_dim, hidden), nn.Tanh(),
            nn.Linear(hidden, hidden), nn.Tanh(),
            nn.Linear(hidden, act_dim), nn.Tanh(),
        )
        self.log_std = nn.Parameter(torch.full((act_dim,), -0.5))

    def forward(self, obs):
        return self.net(obs)

    def act_deterministic(self, obs_np):
        with torch.no_grad():
            o = torch.as_tensor(obs_np, dtype=torch.float32).unsqueeze(0)
            return self.forward(o).squeeze(0).numpy()


def train_bc(epochs=30, batch=512, lr=1e-3):
    d = np.load(os.path.join(RL_DIR, "data", "demos.npz"))
    obs = torch.as_tensor(d["obs"])
    act = torch.as_tensor(d["act"])
    n = len(obs)
    n_val = max(1, int(n * 0.1))
    perm = torch.randperm(n)
    va, tr = perm[:n_val], perm[n_val:]
    pol = MlpPolicy()
    opt = torch.optim.Adam(pol.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    hist = {"train": [], "val": []}
    t0 = time.time()
    for ep in range(epochs):
        pol.train()
        ep_loss, nb = 0.0, 0
        for i in range(0, len(tr), batch):
            idx = tr[i:i + batch]
            loss = loss_fn(pol(obs[idx]), act[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
            ep_loss += loss.item()
            nb += 1
        pol.eval()
        with torch.no_grad():
            vloss = loss_fn(pol(obs[va]), act[va]).item()
        hist["train"].append(ep_loss / nb)
        hist["val"].append(vloss)
        if ep % 5 == 0 or ep == epochs - 1:
            print(f"[BC] epoch {ep:3d} train={ep_loss/nb:.5f} val={vloss:.5f}")
    torch.save(pol.state_dict(), CKPT_BC)
    np.savez(os.path.join(RL_DIR, "data", "bc_loss.npz"),
             train=np.array(hist["train"]), val=np.array(hist["val"]))
    print(f"[BC] {time.time()-t0:.0f}s，模型 → {CKPT_BC}")
    return pol


if __name__ == "__main__":
    train_bc()
