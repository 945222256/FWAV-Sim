# -*- coding: utf-8 -*-
"""
closed_loop_cfm.py — 分层架构闭环验收：经典外环 + CFM 生成式内环
====================================================================
信号流（对应 arch_serving.png）：
  状态 → 外环 PD → 期望力旋量 W*=[Fz,Mx] → CFM 逆映射采样 K=8
        → 前代物理拒采选最优 θ=[f,dA,split,lag,mean] → 环境执行

对照：rl/expert_v2.HoverExpertV2（经典外环 + 力矩分配内环，同一外环增益）
输出：rl/data/cl_cfm_results.npz + rl/figs/closed_loop_cfm.png
分段运行：python closed_loop_cfm.py <seed_start> <seed_end>  （结果逐种子落盘）
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl.hover_env_v2 import FlapperHoverEnvV2, DT_CTRL, Z_REF, DA_MAX
from rl.waveform_inverse import DATA, FIGS, RANGES, forward
from rl.wave_inverse_cfm import sample as cfm_sample

import torch

RL_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DATA, "cl_cfm")

FREQ_MID, FREQ_HALF = 2.5, 1.0
M_NOMINAL = 0.70
# 外环 PID（TECS 标准：无积分必有静差——随机化升力下 PD 静差实测 0.73m，
# 与专家 PD 同源；加小积分消除，抗饱和 ±2N）
KP_Z, KD_Z, KI_Z = 1.8, 1.8, 0.8   # 加速度空间级联，物理限幅
AZ_CLIP = (-4.0, 8.0)     # m/s² 期望法向加速度限幅（物理语义）
AI_CLIP = 1.5             # m/s² 积分限幅（防气球）
KP_R, KD_R = 0.15, 0.01
FZ_CLIP = (3.0, 22.5)      # N，数据集可达域内（f=3.5 名义 23.9N，
                          # 超出即 CFM 条件分布外输入，输出失真）
MX_CLIP = 1.5              # N·m

_ez_int = 0.0
_emerg = False
_scale_hat = 1.0      # 在线升力比估计（MIT 规则最小版）

_cfm_net = None
_cfm_norm = None


def _load_cfm():
    global _cfm_net, _cfm_norm
    if _cfm_net is None:
        from rl.wave_inverse_cfm import VelocityNet
        ck = torch.load(os.path.join(DATA, "wave_inverse_cfm.pt"),
                        weights_only=False)
        net = VelocityNet()
        net.load_state_dict(ck["state_dict"])
        net.eval()
        _cfm_net = net
        _cfm_norm = (ck["w_mu"], ck["w_sd"], ck["t_mu"], ck["t_sd"])
    return _cfm_net, _cfm_norm


def outer_loop(obs):
    """经典外环：状态 → 期望力旋量 W*（PID + 抗积分饱和）。"""
    global _ez_int
    z, vz, roll, wx = [float(x) for x in obs[:4]]
    ez = Z_REF - z
    global _scale_hat
    # 防坠应急（迟滞型，生产标准件）：切入 低高度+下降，切出 回到
    # 安全高度且上升——706 实证无迟滞时在应急/正常间 bang-bang，
    # 恰好停在终止边界上（ez=1.2 处常规需求被 0.71 抽负后刚好不升不降）
    global _emerg
    if not _emerg and z < 1.0 and vz < -0.8:
        _emerg = True
    if _emerg and z > 1.35 and vz > 0.2:
        _emerg = False
    if _emerg:
        return np.array([FZ_CLIP[1], 0.0, 0.0])
    az = KP_Z * ez - KD_Z * vz + KI_Z * _ez_int
    az_c = float(np.clip(az, *AZ_CLIP))
    # 抗饱和（条件积分）：未饱和或积分有助于脱离饱和时才累积
    if az == az_c or np.sign(ez) != np.sign(az - az_c):
        _ez_int = float(np.clip(_ez_int + ez * DT_CTRL, -AI_CLIP, AI_CLIP))
    # 在线升力比校正：±30% 升力抽负下固定增益级联已达能力边界，
    # 需求除以实测升力比 = 最小自适应（RMA 思想的经典版）
    Fz = float(np.clip(M_NOMINAL * (9.81 + az_c) / max(_scale_hat, 0.5),
                       *FZ_CLIP))
    Mx = float(np.clip(-(KP_R * roll + KD_R * wx), -MX_CLIP, MX_CLIP))
    return np.array([Fz, Mx, 0.0])


def cfm_inverse(W_star, k=8):
    """CFM 采样 K 候选 + 前代物理拒采 → θ。返回 (θ, 采样耗时ms)。"""
    net, norm = _load_cfm()
    t0 = time.perf_counter()
    cands = cfm_sample(net, W_star, norm, k=k)
    sample_ms = (time.perf_counter() - t0) * 1000
    lo = np.array([RANGES[n][0] for n in ("f", "dA", "split", "lag",
                                          "mean_deg")])
    hi = np.array([RANGES[n][1] for n in ("f", "dA", "split", "lag",
                                          "mean_deg")])
    best, best_cost = None, None
    for th in cands:
        th = np.clip(th[0], lo, hi)
        W_hat = forward(th)
        cost = abs(W_hat[0] - W_star[0]) / 10.0 + abs(W_hat[1] - W_star[1]) / 5.0
        if best is None or cost < best_cost:
            best, best_cost = th, cost
    return best, sample_ms


def theta_to_action(th):
    f, dA, split, lag, mean_deg = th
    return np.array([
        (f - FREQ_MID) / FREQ_HALF,
        dA / DA_MAX,
        (split - 0.55) / 0.10,
        lag / 0.06,
        mean_deg / 8.0], dtype=np.float32)


def run_episode(seed):
    global _ez_int, _emerg, _scale_hat
    _ez_int = 0.0
    _emerg = False
    _scale_hat = 1.0
    env = FlapperHoverEnvV2(randomize=True, obs_noise=True,
                            action_channels=("freq", "dA", "split", "lag",
                                             "mean"))
    obs, _ = env.reset(seed=seed)
    ret, n = 0.0, 0
    traj, infer_ms = [], []
    vz_prev = float(obs[1])
    W_hat_prev = None
    for _ in range(500):
        W_star = outer_loop(obs)
        th, ms = cfm_inverse(W_star, k=8)
        infer_ms.append(ms)
        # 记住本步模型预测升力，供下一步实测校正
        W_hat_prev = forward(th)
        obs, r, term, trunc, _ = env.step(theta_to_action(th))
        # MIT 规则最小版：实测法向加速度 vs 模型预测 → 升力比 EMA
        if W_hat_prev is not None and W_hat_prev[0] > 1.0:
            az_meas = (float(obs[1]) - vz_prev) / DT_CTRL
            s_inst = ((az_meas + 9.81) * M_NOMINAL) / W_hat_prev[0]
            if 0.2 < s_inst < 2.5:
                _scale_hat += (DT_CTRL / 0.5) * (s_inst - _scale_hat)
                _scale_hat = float(np.clip(_scale_hat, 0.5, 1.5))
        vz_prev = float(obs[1])
        ret += r
        n += 1
        traj.append([env.t, env.state[0], env.state[2], W_star[0]])
        if term or trunc:
            break
    return {"seed": seed, "alive": n >= 500, "rps": ret / n,
            "z_err": abs(env.state[0] - Z_REF), "traj": np.array(traj),
            "infer_ms_mean": float(np.mean(infer_ms)),
            "infer_ms_max": float(np.max(infer_ms))}


def main():
    s0 = int(sys.argv[1]) if len(sys.argv) > 1 else 700
    s1 = int(sys.argv[2]) if len(sys.argv) > 2 else s0 + 4
    os.makedirs(OUT, exist_ok=True)
    for seed in range(s0, s1):
        r = run_episode(seed)
        np.savez_compressed(os.path.join(OUT, f"seed_{seed}.npz"),
                            traj=r["traj"], alive=r["alive"], rps=r["rps"],
                            z_err=r["z_err"], infer_ms_mean=r["infer_ms_mean"],
                            infer_ms_max=r["infer_ms_max"])
        print(f"[CL-CFM] seed={seed} 存活 {r['alive']} 步回报 {r['rps']:+.3f} "
              f"稳态误差 {r['z_err']:.3f}m 推理 {r['infer_ms_mean']:.1f}ms/"
              f"步(峰 {r['infer_ms_max']:.0f})", flush=True)


if __name__ == "__main__":
    main()
