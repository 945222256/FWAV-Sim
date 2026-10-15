# -*- coding: utf-8 -*-
"""
eval_finetune_cfm.py — I-071 配对评估：冻结 CFM vs 微调 CFM
=============================================================
协议：与 closed_loop_cfm.py 的 16 种子基准同一环境契约、同一外环、
同一 K=8 拒采。配对设计：每个种子回合开始前重置 torch 随机种子，
两边（冻结网络 / 微调网络）在相同步序上消费完全相同的初始噪声序列，
环境随机化也用同一种子重放——两臂唯一差异是速度场权重。
输出：rl/data/cl_cfm_ft/{frozen,ft}/seed_*.npz + rl/data/cl_cfm_ft_table.md
     + rl/figs/closed_loop_cfm_ft.png
用法：python rl/eval_finetune_cfm.py [seeds_start seeds_end]
"""
import os
import sys
import time

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import torch

from rl.hover_env_v2 import FlapperHoverEnvV2
from rl.waveform_inverse import DATA, FIGS
from rl.wave_inverse_cfm import VelocityNet
from rl.finetune_cfm import deploy_inverse
import rl.closed_loop_cfm as clc

OUT = os.path.join(DATA, "cl_cfm_ft")


def load_net(path):
    ck = torch.load(path, weights_only=False)
    if "state_dict" not in ck:          # 纯状态字典（逐迭代快照）
        sd, norm_src = ck, torch.load(
            os.path.join(DATA, "wave_inverse_cfm.pt"), weights_only=False)
        ck = {"state_dict": sd, "w_mu": norm_src["w_mu"],
              "w_sd": norm_src["w_sd"], "t_mu": norm_src["t_mu"],
              "t_sd": norm_src["t_sd"]}
    net = VelocityNet()
    net.load_state_dict(ck["state_dict"])
    net.eval()
    return net, (ck["w_mu"], ck["w_sd"], ck["t_mu"], ck["t_sd"])


def run_episode(net, norm, seed):
    """与 closed_loop_cfm.run_episode 同构；回合前重置 torch 种子配对。"""
    torch.manual_seed(10000 + seed)
    clc._ez_int = 0.0
    clc._emerg = False
    clc._scale_hat = 1.0
    env = FlapperHoverEnvV2(randomize=True, obs_noise=True,
                            action_channels=("freq", "dA", "split", "lag",
                                             "mean"))
    obs, _ = env.reset(seed=seed)
    ret, n = 0.0, 0
    traj, infer_ms = [], []
    vz_prev = float(obs[1])
    from rl.finetune_cfm import fast_forward
    W_hat_prev = None
    for _ in range(500):
        W_star = clc.outer_loop(obs)
        th, ms = deploy_inverse(net, norm, W_star, k=8)
        infer_ms.append(ms)
        W_hat_prev = fast_forward(th)
        obs, r, term, trunc, _ = env.step(clc.theta_to_action(th))
        if W_hat_prev is not None and W_hat_prev[0] > 1.0:
            az_meas = (float(obs[1]) - vz_prev) / clc.DT_CTRL
            s_inst = ((az_meas + 9.81) * clc.M_NOMINAL) / W_hat_prev[0]
            if 0.2 < s_inst < 2.5:
                clc._scale_hat += (clc.DT_CTRL / 0.5) * (s_inst - clc._scale_hat)
                clc._scale_hat = float(np.clip(clc._scale_hat, 0.5, 1.5))
        vz_prev = float(obs[1])
        ret += r
        n += 1
        traj.append([env.t, env.state[0], env.state[2], W_star[0]])
        if term or trunc:
            break
    return {"seed": seed, "alive": n >= 500, "rps": ret / n,
            "z_err": abs(env.state[0] - clc.Z_REF),
            "traj": np.array(traj),
            "infer_ms_mean": float(np.mean(infer_ms)),
            "infer_ms_max": float(np.max(infer_ms))}


def main():
    s0 = int(sys.argv[1]) if len(sys.argv) > 1 else 700
    s1 = int(sys.argv[2]) if len(sys.argv) > 2 else 716
    # EVAL_ARMS=frozen,ft 指定只评哪些臂（默认两臂都评）。冻结臂协议
    # 确定（回合前重置 torch 种子），历史结果可直接复用不必重跑
    arms_sel = os.environ.get("EVAL_ARMS", "frozen,ft").split(",")
    ft_path = os.path.join(DATA, "ft_cfm_policy.pt")
    arms = {}
    if "frozen" in arms_sel:
        arms["frozen"] = (load_net(os.path.join(DATA, "wave_inverse_cfm.pt")))
    if os.path.exists(ft_path):
        arms["ft"] = (load_net(ft_path))
    else:
        print("[eval] 未找到微调权重，只评冻结臂")
    for arm, (net, norm) in arms.items():
        od = os.path.join(OUT, arm)
        os.makedirs(od, exist_ok=True)
        alive, zerrs, rpss = [], [], []
        for seed in range(s0, s1):
            r = run_episode(net, norm, seed)
            np.savez_compressed(os.path.join(od, f"seed_{seed}.npz"),
                                traj=r["traj"], alive=r["alive"],
                                rps=r["rps"], z_err=r["z_err"],
                                infer_ms_mean=r["infer_ms_mean"],
                                infer_ms_max=r["infer_ms_max"])
            alive.append(r["alive"])
            zerrs.append(r["z_err"] if r["alive"] else float("nan"))
            rpss.append(r["rps"])
            print(f"[{arm}] seed={seed} 存活 {r['alive']} "
                  f"步回报 {r['rps']:+.3f} 稳态误差 {r['z_err']:.3f}m "
                  f"推理 {r['infer_ms_mean']:.1f}ms/步", flush=True)
        n_al = int(np.nansum(alive))
        print(f"[{arm}] 合计 存活 {n_al}/{len(alive)}  "
              f"存活种子稳态误差均值 {np.nanmean(zerrs):.4f}m  "
              f"步回报均值 {np.mean(rpss):+.3f}", flush=True)


if __name__ == "__main__":
    main()
