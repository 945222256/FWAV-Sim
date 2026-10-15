# -*- coding: utf-8 -*-
"""
finetune_cfm.py — 生成式策略载体（CFM 速度场）上的强化学习微调
================================================================
任务：I-071。验证对 CFM 内环网络做闭环强化学习微调，收益能否从负转正。
9 月 21 日的负结果（PPO 微调 BC 多层感知机，存活率 94%→75%）两个机理：
探索噪声直接摧毁模仿行为、价值网络冷启动把策略带偏。本脚本针对这两点
改用不同的算法族：

1. 不加显式探索噪声——CFM 采样本身的初始噪声 x0 就是随机策略的来源，
   微调只更新速度场，采样随机性保持；
2. 行为锚定——每步更新都保留 OT-CFM 原始目标（小批量最优传输配对 +
   直线插值路径回归），系数 lambda_anchor，等价于扩散策略微调里的
   行为正则项，防止速度场漂移破坏已学会的分布；
3. 策略梯度走可微路径——50 步 Euler 常微分方程求解器全部展开反传，
   策略损失 = -Q(c, theta)（DDPG 确定性策略梯度思想的流匹配版），
   不需要 PPO 的对数概率比（流采样的对数密度不可解析）。

闭环接口与 closed_loop_cfm.py 完全一致：经典外环（比例积分微分 + 迟滞
应急 + 最小在线升力比校正）输出期望力旋量 W*，CFM 采样 K=8 候选经
前代物理拒采取最优，theta_to_action 进 V2 环境。微调期间外环增益、
拒采代价函数、K 值全部冻结，唯一变化量是速度场权重。

训练种子池 800 起，与评估种子 700~715 不相交。
输出：rl/data/ft_cfm_ckpt.pt（断点）+ rl/data/ft_cfm_train.log
分段运行：set FT_CHUNK=<迭代数> 后重跑本脚本从断点续训。
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

from rl.hover_env_v2 import FlapperHoverEnvV2
from rl.waveform_inverse import DATA, RANGES, THETA_NAMES, forward
from rl.wave_inverse_cfm import VelocityNet, ot_pair, CFG_DROP
import rl.closed_loop_cfm as clc

RL_DIR = os.path.dirname(os.path.abspath(__file__))
CKPT = os.path.join(DATA, "ft_cfm_ckpt.pt")
LOG = os.path.join(DATA, "ft_cfm_train.log")
CKPT_PRETRAINED = os.path.join(DATA, "wave_inverse_cfm.pt")

# ---------------- 超参数（预登记，跑前冻结）----------------
K_ROLLOUT = 8            # 部署机制同款 K 样本拒采
N_ODE = 50               # 与部署一致的 50 步 Euler
OMEGA = 1.5              # 分类器无关引导权重，与部署一致
GAMMA = 0.99
TAU = 0.005
LR_CRITIC = 1e-3
LR_ACTOR = 5e-6          # 速度场微调必须小步。三次实测：1e-4 量级一步毁
                         # 盆地，1e-5 量级在百次更新内仍把存活率打到零
LAMBDA_ANCHOR = 1.0      # 速度场行为锚定系数
BETA_TRAJ = 20.0         # 同噪声轨迹锚定系数：同一初始噪声下微调采样
                         # 必须贴近冻结采样的输出。三次实测没有更重的
                         # 轨迹约束时策略在迭代间剧烈振荡
CRITIC_WARMUP = 300      # 首迭代先只训评论员（无热身时策略追半熟
                         # 评论员一步就崩，实测存活率归零）
EPISODES_PER_ITER = 6
ITERS_TOTAL = 5
UPDATES_PER_ITER = 400
ACTOR_EVERY = 8          # 每八次评论员更新做一次策略更新（约五十次/迭代）
# 探针种子选取依据（2026-10-05 预检）：配对协议下冻结网络在 905/907/909
# 全部存活（步回报 +0.650/+0.834/+0.889），探针有区分力；先前盲选的
# 900~902 冻结网络本身死两个，探针读数无归因力，已废弃
PROBE_SEEDS = (905, 907, 909)
STEPS_PER_EPISODE = 500
BATCH = 256
X0_DRAWS = 2             # 策略梯度对初始噪声取的固定抽样数
BUFFER_CAP = 60000
CHUNK = int(os.environ.get("FT_CHUNK", str(ITERS_TOTAL)))
TRAIN_SEED_START = 800
EVAL_SEEDS = range(700, 716)   # 评估种子，训练中禁止触碰


class Critic(nn.Module):
    """Q(c, theta)：条件（标准化力旋量）+ 波形参数（[-1,1] 归一）→ 动作价值。"""

    def __init__(self, c_dim=3, a_dim=5, hidden=256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(c_dim + a_dim, hidden), nn.SiLU(),
            nn.Linear(hidden, hidden), nn.SiLU(),
            nn.Linear(hidden, hidden), nn.SiLU(),
            nn.Linear(hidden, 1))

    def forward(self, c, a):
        return self.net(torch.cat([c, a], dim=-1)).squeeze(-1)


# ---------------- 前代物理的快速版（结果与 waveform_inverse.forward 逐位一致）----------------
_fast_model = None


def fast_forward(theta):
    """与 forward(theta) 数值等价：复用同一个气动模型实例，省掉每次重建。"""
    global _fast_model
    import math
    from modules.aero_solver import FlapperAeroModel
    if _fast_model is None:
        _fast_model = FlapperAeroModel({"span": 1.15, "root_chord": 0.35,
                                        "tip_chord": 0.25, "n_strips": 14})
    f, dA, split, lag, mean_deg = theta
    m = _fast_model
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


def check_fast_forward():
    """启动自检：快速前代与原版逐位一致才准用（拒采代价必须同数）。"""
    rng = np.random.default_rng(0)
    lo = np.array([RANGES[k][0] for k in THETA_NAMES])
    hi = np.array([RANGES[k][1] for k in THETA_NAMES])
    for _ in range(8):
        th = rng.uniform(lo, hi)
        a, b = forward(th), fast_forward(th)
        assert np.allclose(a, b, atol=1e-10), (a, b)


# ---------------- 可微采样器（部署机制同款公式，保留梯度）----------------
def flow_sample(net, c_n, x0, n_steps=N_ODE, omega=OMEGA):
    """c_n 归一化条件、x0 初始噪声 → 归一化空间 theta（可微）。

    公式与 wave_inverse_cfm.sample 逐行一致（CFG 两支 + Euler），
    唯一区别是不包 no_grad。
    """
    c0 = torch.zeros_like(c_n)
    x = x0
    for i in range(n_steps):
        t = torch.full((len(c_n),), i / n_steps, device=c_n.device)
        v_c = net(t, x, c_n)
        v_u = net(t, x, c0)
        x = x + (v_u + omega * (v_c - v_u)) / n_steps
    return x


def norm_cond(w_star, norm):
    w_mu, w_sd = norm[0], norm[1]
    return torch.as_tensor((np.atleast_2d(w_star) - w_mu) / w_sd,
                           dtype=torch.float32)


def denorm_theta(x_n, norm):
    t_mu, t_sd = np.asarray(norm[2]), np.asarray(norm[3])
    x = x_n.detach().numpy() if torch.is_tensor(x_n) else np.asarray(x_n)
    return x * t_sd + t_mu


# ---------------- 部署机制（与 closed_loop_cfm 相同的选择规则）----------------
def deploy_inverse(net, norm, W_star, k=K_ROLLOUT):
    """采样 K 候选 + 前代物理拒采 → theta。返回 (theta, 采样耗时 ms)。"""
    t0 = time.perf_counter()
    with torch.no_grad():
        c_n = norm_cond(W_star, norm)
        xs = []
        for _ in range(k):
            x0 = torch.randn(len(c_n), 5)
            xs.append(flow_sample(net, c_n, x0).numpy())
    ms = (time.perf_counter() - t0) * 1000
    lo = np.array([RANGES[n][0] for n in ("f", "dA", "split", "lag",
                                          "mean_deg")])
    hi = np.array([RANGES[n][1] for n in ("f", "dA", "split", "lag",
                                          "mean_deg")])
    best, best_cost = None, None
    for xn in xs:
        th = np.clip(denorm_theta(xn, norm)[0], lo, hi)
        W_hat = fast_forward(th)
        cost = abs(W_hat[0] - W_star[0]) / 10.0 + abs(W_hat[1] - W_star[1]) / 5.0
        if best is None or cost < best_cost:
            best, best_cost = th, cost
    return best, ms


def rollout_episode(net, norm, seed):
    """部署机制跑一回合，返回转移列表与回合统计。

    外环直接复用 closed_loop_cfm.outer_loop（含其全局积分器/应急/升力比
    状态，逐回合复位，与基线 run_episode 同一写法）。
    """
    clc._ez_int = 0.0
    clc._emerg = False
    clc._scale_hat = 1.0
    env = FlapperHoverEnvV2(randomize=True, obs_noise=True,
                            action_channels=("freq", "dA", "split", "lag",
                                             "mean"))
    obs, _ = env.reset(seed=seed)
    trans = []
    ret, n = 0.0, 0
    vz_prev = float(obs[1])
    W_hat_prev = None
    for _ in range(STEPS_PER_EPISODE):
        W_star = clc.outer_loop(obs)
        th, _ = deploy_inverse(net, norm, W_star)
        W_hat_prev = fast_forward(th)
        # 条件归一（进缓冲区的是归一化力旋量，与策略条件同口径）
        c_n = ((W_star - np.asarray(norm[0])) / np.asarray(norm[1]))
        a_n = np.clip((th - np.asarray(norm[2])) / np.asarray(norm[3]),
                      -1.0, 1.0)
        obs, r, term, trunc, _ = env.step(clc.theta_to_action(th))
        if W_hat_prev is not None and W_hat_prev[0] > 1.0:
            az_meas = (float(obs[1]) - vz_prev) / clc.DT_CTRL
            s_inst = ((az_meas + 9.81) * clc.M_NOMINAL) / W_hat_prev[0]
            if 0.2 < s_inst < 2.5:
                clc._scale_hat += (clc.DT_CTRL / 0.5) * (s_inst - clc._scale_hat)
                clc._scale_hat = float(np.clip(clc._scale_hat, 0.5, 1.5))
        vz_prev = float(obs[1])
        trans.append((c_n.astype(np.float32), a_n.astype(np.float32),
                      float(r), float(term or trunc)))
        ret += r
        n += 1
        if term or trunc:
            break
    return trans, {"alive": n >= STEPS_PER_EPISODE, "rps": ret / n,
                   "z_err": abs(env.state[0] - clc.Z_REF)}


# ---------------- 锚定损失（OT-CFM 原目标，防漂移）----------------
def anchor_loss(net, X_ds, C_ds, batch=512):
    """与 wave_inverse_cfm.train 同公式的流匹配损失（含 10% 条件丢弃）。"""
    idx = torch.randperm(len(X_ds))[:batch]
    x1, c = X_ds[idx], C_ds[idx]
    cmask = (torch.rand(len(x1)) > CFG_DROP).float()
    c_in = c * cmask[:, None]
    x0 = torch.randn_like(x1)
    x0p = ot_pair(x0, x1)
    t = torch.rand(len(x1))
    x_t = (1 - t[:, None]) * x0p + t[:, None] * x1
    u = x1 - x0p
    return nn.functional.mse_loss(net(t, x_t, c_in), u)


def load_anchor_dataset():
    d = np.load(os.path.join(DATA, "wave_inverse.npz"))
    TH, W = d["TH"], d["W"]
    lo = np.array([RANGES[k][0] for k in THETA_NAMES])
    hi = np.array([RANGES[k][1] for k in THETA_NAMES])
    t_mu, t_sd = (hi + lo) / 2, (hi - lo) / 2
    w_mu, w_sd = W.mean(0), W.std(0) + 1e-9
    X = torch.as_tensor((TH - t_mu) / t_sd, dtype=torch.float32)
    C = torch.as_tensor((W - w_mu) / w_sd, dtype=torch.float32)
    return X, C


# ---------------- 主循环 ----------------
def main():
    torch.manual_seed(0)
    check_fast_forward()
    ck = torch.load(CKPT_PRETRAINED, weights_only=False)
    net = VelocityNet()
    net.load_state_dict(ck["state_dict"])
    net0 = VelocityNet()                 # 冻结副本：轨迹锚定参照
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
        print(f"[FT] 从断点 iter={it_start} 续训", flush=True)

    it_end = min(it_start + CHUNK, ITERS_TOTAL)
    logf = open(LOG, "a", encoding="utf-8")
    t0 = time.time()
    for it in range(it_start, it_end):
        # ---------- 采集（部署机制，训练种子池）----------
        ep_stats = []
        for e in range(EPISODES_PER_ITER):
            seed = TRAIN_SEED_START + (it * EPISODES_PER_ITER + e) % 96
            trans, st = rollout_episode(net, norm, seed)
            buf["c"].extend([t_[0] for t_ in trans])
            buf["a"].extend([t_[1] for t_ in trans])
            buf["r"].extend([t_[2] for t_ in trans])
            buf["d"].extend([t_[3] for t_ in trans])
            ep_stats.append(st)
        for key in buf:
            buf[key] = buf[key][-BUFFER_CAP:]
        alive = np.mean([s["alive"] for s in ep_stats])
        rps = np.mean([s["rps"] for s in ep_stats])
        line = (f"[FT] iter {it}  采集 {EPISODES_PER_ITER} 回合  存活 "
                f"{alive:.2f}  步回报 {rps:+.3f}  缓冲 {len(buf['r'])}  "
                f"用时 {time.time()-t0:.0f}s")
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

        # ---------- 更新 ----------
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
            # 目标动作：目标速度场（指数滑动平均）的单次可微采样
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
                # 价值中心化：策略目标用同批重放动作的评论员价值作基线，
                # 消掉量级约 r/(1-γ) 的常值偏移，只留相对改进方向
                with torch.no_grad():
                    q_base = critic(c_b, a_b).mean()
                x0 = torch.randn(len(c_b) // X0_DRAWS, 5) \
                    .repeat(X0_DRAWS, 1)
                a_raw = flow_sample(net, c_b, x0)
                a_pred = torch.clamp(a_raw, -1.0, 1.0)
                # 同噪声轨迹锚定：冻结网络在同一 x0 下的采样输出
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
        line = (f"[FT] iter {it}  更新 {UPDATES_PER_ITER} 步  "
                f"评论员损失 {np.mean(q_trace):.4f}  "
                f"策略+锚定损失 {np.mean(anc_trace):.4f}")
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

        # ---------- 健康探针：配对协议（固定噪声）下的固定探针种子 ----------
        from rl.eval_finetune_cfm import run_episode as _paired_episode
        probe = [_paired_episode(net, norm, s) for s in PROBE_SEEDS]
        p_alive = float(np.mean([p["alive"] for p in probe]))
        p_rps = float(np.mean([p["rps"] for p in probe]))
        p_zerr = float(np.mean([p["z_err"] for p in probe if p["alive"]]
                               or [float("nan")]))
        line = (f"[FT] iter {it}  探针 存活 {p_alive:.2f}  "
                f"步回报 {p_rps:+.3f}  稳态误差 {p_zerr:.3f}")
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
                   os.path.join(DATA, f"ft_cfm_policy_it{it}.pt"))
    torch.save(net.state_dict(), os.path.join(DATA, "ft_cfm_policy.pt"))
    logf.close()
    done = it_end >= ITERS_TOTAL
    print(f"[FT] 本段完成至 iter={it_end}"
          + ("（全部完成）" if done else "（需继续下一段）"))


if __name__ == "__main__":
    main()
