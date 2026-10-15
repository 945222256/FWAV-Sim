# -*- coding: utf-8 -*-
"""
collect_demos.py — 专家演示数据采集（行为克隆数据集）
====================================================
只保留满存活（10s）的回合：演示数据质量控制本身是工程要点
（坏演示 = 发散轨迹，BC 会学到发散策略）。
输出 rl/data/demos.npz: obs (N,5), act (N,2)
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl.hover_env import FlapperHoverEnv, DT_CTRL
from rl.expert import HoverExpert

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "demos.npz")


def collect(n_episodes=60, seed0=100):
    obs_all, act_all = [], []
    kept = dropped = 0
    for k in range(n_episodes):
        env = FlapperHoverEnv(randomize=True, obs_noise=0.002)
        obs, _ = env.reset(seed=seed0 + k)
        ex = HoverExpert(kp_r=0.10, kd_r=0.02)
        ep_obs, ep_act = [], []
        alive = True
        for _ in range(500):
            a = ex.act(obs)
            ep_obs.append(obs.copy())
            ep_act.append(a.copy())
            obs, _, term, trunc, _ = env.step(a)
            if term:
                alive = False
                break
            if trunc:
                break
        if alive and len(ep_obs) >= 500:
            kept += 1
            obs_all.extend(ep_obs[::2])    # 25Hz 子采样，降样本相关
            act_all.extend(ep_act[::2])
        else:
            dropped += 1
    obs_all = np.asarray(obs_all, dtype=np.float32)
    act_all = np.asarray(act_all, dtype=np.float32)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    np.savez_compressed(OUT, obs=obs_all, act=act_all)
    print(f"[collect] 保留 {kept}/{n_episodes} 回合（丢弃发散 {dropped}），"
          f"样本 {len(obs_all)} → {OUT}")


if __name__ == "__main__":
    collect()
