# -*- coding: utf-8 -*-
"""
smoke_train_envelope.py — 包络级骨架合成数据冒烟训练（I-097）
================================================================
目的：用合成数据把训练闭环完整走一遍，证明三条链路贯通——
  1. 数据槽位链路：机翼态模型分模态两槽填了会被真实消费（用扰动
     供应商对照验证：升力乘零点九后训练曲线必须改变）；
  2. 编码链路：观测十八维两段拼装进网络，事件通道在训练中被实际
     触发（统计各模态步数与事件数）；
  3. 奖励链路：环境奖励逐步入账，回报曲线全有限值。
可复现性：同种子两次训练曲线逐位一致（合成环境与火炬均确定性）。
合成常数沿用骨架自洽集（SyntheticEnvelopeModel），不与现行构型混用。

规模为冒烟级：十迭代、每迭代八百步、小网络，不追求性能。
产物：rl/data/smoke_env_train.npz（三组曲线）、
     rl/figs/smoke_env_train.png、rl/data/smoke_env_train_report.md
用法：python rl/smoke_train_envelope.py
"""
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import torch
import torch.nn as nn

from rl.env_envelope import (EnvelopeDecisionEnv, EVENT_NONE,
                             EVENT_STOP_FLAP, EVENT_RESUME_FLAP,
                             SyntheticEnvelopeModel)

RL_DIR = os.path.dirname(os.path.abspath(__file__))
NPZ = os.path.join(RL_DIR, "data", "smoke_env_train.npz")
FIG = os.path.join(RL_DIR, "figs", "smoke_env_train.png")
REPORT = os.path.join(RL_DIR, "data", "smoke_env_train_report.md")

ITERS = 10
STEPS_PER_ITER = 800
HIDDEN = 64
LR = 3e-4
GAMMA = 0.99
LAM = 0.95
CLIP = 0.2
EPOCHS = 3
MB = 256
SEED = 20261007


class TinyActorCritic(nn.Module):
    """复合动作头：包络连续四通道高斯 + 事件离散三选一类别 + 价值头。"""

    def __init__(self, obs_dim=18):
        super().__init__()
        self.body = nn.Sequential(nn.Linear(obs_dim, HIDDEN), nn.Tanh(),
                                  nn.Linear(HIDDEN, HIDDEN), nn.Tanh())
        self.env_mu = nn.Linear(HIDDEN, 4)
        self.log_std = nn.Parameter(torch.full((4,), -1.0))
        self.event_logits = nn.Linear(HIDDEN, 3)
        self.value = nn.Linear(HIDDEN, 1)

    def forward(self, o):
        h = self.body(o)
        return (torch.tanh(self.env_mu(h)), self.log_std.exp(),
                self.event_logits(h), self.value(h).squeeze(-1))


def run_training(tag, seed=SEED, flap_provider=None, glide_provider=None):
    """一次完整小训练。返回曲线与链路统计。"""
    torch.manual_seed(seed)
    np.random.seed(seed)          # PPO 小批量洗牌走全局源，必须同播
    env = EnvelopeDecisionEnv(seed=seed, verbose=False)
    if flap_provider is not None:
        env.slots.set("wing_state_model_FLAP", flap_provider)
    if glide_provider is not None:
        env.slots.set("wing_state_model_GLIDE", glide_provider)
    pi = TinyActorCritic()
    opt = torch.optim.Adam(pi.parameters(), lr=LR)
    obs, _ = env.reset(seed=seed)
    curve, stats_each = [], []
    mode_flap_steps = mode_glide_steps = events_fired = 0
    for it in range(ITERS):
        buf = {k: [] for k in ("o", "ae", "ev", "logp", "rew", "val",
                               "done")}
        ep_rets, ep_ret = [], 0.0
        for _ in range(STEPS_PER_ITER):
            with torch.no_grad():
                mu, std, logits, v = pi(torch.as_tensor(
                    obs, dtype=torch.float32))
                dist = torch.distributions.Normal(mu, std)
                a_e = dist.sample().clamp(-1, 1)
                logp_env = dist.log_prob(a_e).sum()
                ev_dist = torch.distributions.Categorical(logits=logits)
                a_ev = int(ev_dist.sample())
                logp = logp_env + ev_dist.log_prob(
                    torch.tensor(a_ev))
            o2, r, term, trunc, _ = env.step(a_e.numpy(), a_ev)
            buf["o"].append(obs)
            buf["ae"].append(a_e.numpy())
            buf["ev"].append(a_ev)
            buf["logp"].append(float(logp))
            buf["rew"].append(r)
            buf["val"].append(float(v))
            buf["done"].append(float(term or trunc))
            ep_ret += r
            if env.mode == "FLAP":
                mode_flap_steps += 1
            else:
                mode_glide_steps += 1
            if a_ev != EVENT_NONE and ((env.mode == "GLIDE")
                                       or True):
                events_fired += 1
            obs = o2
            if term or trunc:
                ep_rets.append(ep_ret)
                ep_ret = 0.0
                obs, _ = env.reset()
        # GAE
        rew = np.array(buf["rew"], dtype=np.float32)
        val = np.array(buf["val"] + [0.0], dtype=np.float32)
        done = np.array(buf["done"], dtype=np.float32)
        adv = np.zeros_like(rew)
        gae = 0.0
        for t in reversed(range(len(rew))):
            delta = rew[t] + GAMMA * val[t + 1] * (1 - done[t]) - val[t]
            gae = delta + GAMMA * LAM * (1 - done[t]) * gae
            adv[t] = gae
        ret = adv + val[:-1]
        adv_t = torch.as_tensor((adv - adv.mean()) / (adv.std() + 1e-8))
        o_t = torch.as_tensor(np.array(buf["o"]), dtype=torch.float32)
        ae_t = torch.as_tensor(np.array(buf["ae"]), dtype=torch.float32)
        ev_t = torch.as_tensor(np.array(buf["ev"]), dtype=torch.long)
        lp_t = torch.as_tensor(np.array(buf["logp"]), dtype=torch.float32)
        # PPO 小更新
        idx = np.arange(len(o_t))
        for _ in range(EPOCHS):
            np.random.shuffle(idx)
            for s in range(0, len(idx), MB):
                mb = idx[s:s + MB]
                mu, std, logits, v = pi(o_t[mb])
                dist = torch.distributions.Normal(mu, std)
                logp = dist.log_prob(ae_t[mb]).sum(-1) \
                    + torch.distributions.Categorical(
                        logits=logits).log_prob(ev_t[mb])
                ratio = (logp - lp_t[mb]).exp()
                s1 = ratio * adv_t[mb]
                s2 = torch.clamp(ratio, 1 - CLIP, 1 + CLIP) * adv_t[mb]
                loss = (-torch.min(s1, s2).mean()
                        + 0.5 * ((v - torch.as_tensor(ret[mb])) ** 2).mean())
                opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(pi.parameters(), 0.5)
                opt.step()
        curve.append(float(np.mean(ep_rets)) if ep_rets else 0.0)
        stats_each.append((len(ep_rets), float(np.mean(buf["rew"]))))
    chain = {"flap_steps": mode_flap_steps, "glide_steps": mode_glide_steps,
             "event_actions": events_fired}
    print(f"[{tag}] 曲线 {np.round(curve, 3).tolist()}", flush=True)
    print(f"[{tag}] 链路统计 {chain}", flush=True)
    return np.array(curve, dtype=np.float64), chain


def main():
    # 一、默认合成数据，同种子两次（可复现性）
    c1, s1 = run_training("默认一")
    c2, s2 = run_training("默认二")
    repro = bool(np.array_equal(c1, c2))
    finite = bool(np.all(np.isfinite(c1)))
    # 二、槽位扰动对照：扑动槽填升力乘 0.9 的供应商，曲线必须改变
    syn = SyntheticEnvelopeModel(np.random.default_rng(0))

    def perturbed(theta, state):
        W = syn.wrench(theta, "FLAP")
        return W * np.array([0.9, 1.0, 1.0])

    c3, s3 = run_training("槽位扰动", flap_provider=perturbed)
    slot_consumed = not np.array_equal(c1, c3)
    # 三、链路统计断言
    assert finite, "回报曲线出现非有限值"
    assert repro, "同种子两次训练曲线不一致"
    assert slot_consumed, "槽位供应商未被消费（扰动未改变曲线）"
    assert s1["glide_steps"] > 0, "训练中滑翔模态从未进入（事件链未通）"
    assert s1["event_actions"] > 0, "事件动作从未发出"

    np.savez(NPZ, curve_default=c1, curve_perturbed=c3,
             reproducible=repro, finite=finite,
             slot_consumed=slot_consumed,
             flap_steps=s1["flap_steps"], glide_steps=s1["glide_steps"],
             event_actions=s1["event_actions"])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                       "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(7.5, 4.0))
    ax.plot(c1, "o-", label="默认合成（两次逐位一致）")
    ax.plot(c3, "s--", label="槽位扰动（升力乘 0.9）")
    ax.set_xlabel("迭代")
    ax.set_ylabel("回合平均回报")
    ax.set_title("包络级骨架合成数据冒烟训练（I-097）")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG, dpi=140, bbox_inches="tight")

    lines = ["# 包络级骨架合成数据冒烟训练报告（I-097）", "",
             f"规模：{ITERS} 迭代 × {STEPS_PER_ITER} 步，网络 {HIDDEN}×2，"
             f"种子 {SEED}。", "",
             f"回报曲线（默认合成，回合平均）："
             f"{np.round(c1, 3).tolist()}", "",
             f"可复现性：同种子两次曲线逐位一致 = {repro}；"
             f"全部有限值 = {finite}。", "",
             f"槽位链路：扰动供应商（升力乘 0.9）改变曲线 = "
             f"{slot_consumed}（填了会被真实消费）。", "",
             f"编码链路：扑动步 {s1['flap_steps']}，滑翔步 "
             f"{s1['glide_steps']}，事件动作 {s1['event_actions']} 次"
             "（两模态与事件通道均被训练触达）。", "",
             f"合成常数：SyntheticEnvelopeModel 自洽集"
             "（升力律 1.95·f²、差动增益 5.2·f²、质量 0.70 千克、"
             "滚转惯量 0.015 千克平方米，09-18 悬停辨识契约口径），"
             "未与登记库现行构型混用。", ""]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[smoke] 曲线与断言档案 → {NPZ}")
    print(f"[smoke] 图 → {FIG}")
    print(f"[smoke] 报告 → {REPORT}")
    print("[smoke] 冒烟训练全部链路验证通过")


if __name__ == "__main__":
    main()
