# -*- coding: utf-8 -*-
"""
tasks_i076.py — 有余量任务族：高度阶跃跟踪与突风扰动恢复（I-076）
====================================================================
任务定义与指标口径的冻结出处：
rl/预登记_有余量任务族与条件扩展实验_20261005.md 第一节。
环境与内环沿用 I-071 配对评估同款（hover_env_v2 现行契约 + 冻结条件
流匹配逆映射 + K 等于八前代物理拒采）。外环增益全部沿用
closed_loop_cfm 的常量，唯一改动是高度指令从常数改为时间表。
"""
import contextlib
import math
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import rl.closed_loop_cfm as clc
import rl.hover_env_v2 as hev
from rl.hover_env_v2 import FlapperHoverEnvV2, DT_CTRL

TASK_STEP = "step"     # 任务甲：高度阶跃跟踪
TASK_GUST = "gust"     # 任务乙：突风扰动恢复

# 任务甲高度指令时间表（起始秒，指令米）
ZREF_SCHEDULE = [(0.0, 1.5), (3.0, 2.1), (6.0, 1.2), (9.0, 1.5)]
STEP_EPISODE_S = 12.0
GUST_EPISODE_S = 10.0
# 任务乙突风窗口（起始秒，结束秒，符号）；幅度由标定程序确定后传入
GUST_WINDOWS = [(2.0, 3.0, +1.0), (5.0, 6.0, -1.0)]
GUST_AMP = None        # 标定后由 set_gust_amp 固定；None 时跑任务乙报错

# 配对协议的火炬随机种子基数（按任务区分，保证两任务噪声序列不同且可复现）
SEED_BASE = {TASK_STEP: 20000, TASK_GUST: 30000}


def set_gust_amp(amp):
    global GUST_AMP
    GUST_AMP = float(amp)


def zref_of(t):
    z = ZREF_SCHEDULE[0][1]
    for t0, v in ZREF_SCHEDULE:
        if t >= t0:
            z = v
    return z


def gust_offset(t):
    if GUST_AMP is None:
        raise RuntimeError("突风幅度未标定，先跑标定程序再跑任务乙")
    off = 0.0
    for t0, t1, sgn in GUST_WINDOWS:
        if t0 <= t < t1:
            off = sgn * GUST_AMP
    return off


@contextlib.contextmanager
def episode_span(task):
    """任务甲回合临时把环境截断改十二秒，回合后恢复十秒。"""
    if task == TASK_STEP:
        old = hev.EPISODE_S
        hev.EPISODE_S = STEP_EPISODE_S
        try:
            yield int(round(STEP_EPISODE_S / DT_CTRL))
        finally:
            hev.EPISODE_S = old
    else:
        yield int(round(GUST_EPISODE_S / DT_CTRL))


class TaskOuterLoop:
    """经典外环的任务版：逻辑逐行沿用 closed_loop_cfm.outer_loop，
    差异仅两点：高度指令改为时间表函数；积分器、应急、升力比状态
    收进实例（不再用模块全局，便于多回合并发与重置）。"""

    def __init__(self):
        self.ez_int = 0.0
        self.emerg = False
        self.scale_hat = 1.0

    def step(self, obs, t):
        z, vz, roll, wx = [float(x) for x in obs[:4]]
        z_ref = zref_of(t)
        ez = z_ref - z
        if not self.emerg and z < 1.0 and vz < -0.8:
            self.emerg = True
        if self.emerg and z > 1.35 and vz > 0.2:
            self.emerg = False
        if self.emerg:
            return np.array([clc.FZ_CLIP[1], 0.0, 0.0])
        az = clc.KP_Z * ez - clc.KD_Z * vz + clc.KI_Z * self.ez_int
        az_c = float(np.clip(az, *clc.AZ_CLIP))
        if az == az_c or np.sign(ez) != np.sign(az - az_c):
            self.ez_int = float(np.clip(self.ez_int + ez * DT_CTRL,
                                        -clc.AI_CLIP, clc.AI_CLIP))
        Fz = float(np.clip(clc.M_NOMINAL * (9.81 + az_c)
                           / max(self.scale_hat, 0.5), *clc.FZ_CLIP))
        Mx = float(np.clip(-(clc.KP_R * roll + clc.KD_R * wx),
                           -clc.MX_CLIP, clc.MX_CLIP))
        return np.array([Fz, Mx, 0.0])

    def mit_update(self, W_hat_prev, vz_prev, vz_now):
        """在线升力比校正，与 closed_loop_cfm.run_episode 同公式。"""
        if W_hat_prev is not None and W_hat_prev[0] > 1.0:
            az_meas = (vz_now - vz_prev) / DT_CTRL
            s_inst = ((az_meas + 9.81) * clc.M_NOMINAL) / W_hat_prev[0]
            if 0.2 < s_inst < 2.5:
                self.scale_hat += (DT_CTRL / 0.5) * (s_inst - self.scale_hat)
                self.scale_hat = float(np.clip(self.scale_hat, 0.5, 1.5))


def task_reward(task, state, action, t):
    """任务奖励：高度项跟当期指令，其余惩罚项沿用环境奖励形状。"""
    z, vz, roll, wx = state
    r = (1.0
         - 2.0 * (z - zref_of(t)) ** 2
         - 0.2 * vz * vz
         - 4.0 * roll * roll
         - 0.05 * wx * wx
         - 0.02 * float(action @ action))
    return r


def run_task_episode(net, norm, task, seed, record_traj=True):
    """配对协议跑一回合。返回统计与轨迹。

    两臂（冻结/微调）在相同步序上消费相同噪声序列：回合前重置
    火炬随机种子（按任务用不同基数）。
    """
    import torch
    from rl.finetune_cfm import deploy_inverse, fast_forward

    torch.manual_seed(SEED_BASE[task] + seed)
    outer = TaskOuterLoop()
    with episode_span(task) as n_steps:
        env = FlapperHoverEnvV2(randomize=True, obs_noise=True,
                                action_channels=("freq", "dA", "split",
                                                 "lag", "mean"))
        obs, _ = env.reset(seed=seed)
        base_wind = env.wind_x
        ret, n = 0.0, 0
        traj = []
        vz_prev = float(obs[1])
        W_hat_prev = None
        for _ in range(n_steps):
            env.wind_x = base_wind + (gust_offset(env.t)
                                      if task == TASK_GUST else 0.0)
            W_star = outer.step(obs, env.t)
            th, _ = deploy_inverse(net, norm, W_star)
            W_hat_prev = fast_forward(th)
            a = clc.theta_to_action(th)
            obs, r_env, term, trunc, _ = env.step(a)
            outer.mit_update(W_hat_prev, vz_prev, float(obs[1]))
            vz_prev = float(obs[1])
            r = task_reward(task, env.state, a, env.t)
            ret += r
            n += 1
            if record_traj:
                traj.append([env.t, env.state[0], env.state[2],
                             zref_of(env.t)])
            if term or trunc:
                break
        alive = (not term) and n >= n_steps
        # 用真值状态算指标（指标是评价输出，不是控制器输入）
        st = {"alive": alive, "rps": ret / max(n, 1), "n": n,
              "final_z": env.state[0]}
        if record_traj:
            traj = np.array(traj)
            st["metrics"] = task_metrics(task, traj)
            st["traj"] = traj
        return st


def task_metrics(task, traj):
    """从真值轨迹算冻结口径的指标。traj 列：t、z、roll、z_ref。"""
    t, z, roll, zref = traj[:, 0], traj[:, 1], traj[:, 2], traj[:, 3]
    m = {}
    if task == TASK_STEP:
        # 各指令段最后一点五秒的平均跟踪误差
        errs, settles = [], []
        seg_bounds = [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0), (9.0, 12.0)]
        for (t0, t1) in seg_bounds:
            sel = (t >= t0 + 1.5) & (t < t1)
            if sel.any():
                errs.append(float(np.mean(np.abs(z[sel] - zref[sel]))))
        m["seg_errs"] = errs
        m["seg_err_mean"] = float(np.mean(errs)) if errs else float("nan")
        # 阶跃调节时间：进入新指令正负零点一五米带且保持到段末
        for t_step, t_end in ((3.0, 6.0), (6.0, 9.0), (9.0, 12.0)):
            zr = zref_of(t_step + 0.1)
            sel = (t >= t_step) & (t < t_end)
            ts, zs = t[sel], z[sel]
            st_ = None
            for i in range(len(ts)):
                if np.all(np.abs(zs[i:] - zr) <= 0.15):
                    st_ = float(ts[i] - t_step)
                    break
            settles.append(st_)
        ok = [s for s in settles if s is not None]
        m["settles"] = settles
        m["settle_mean"] = float(np.mean(ok)) if ok else float("nan")
        m["settle_unresolved"] = len(settles) - len(ok)
    else:
        # 突风恢复：突风结束后回带（高度正负零点一五米且滚转正负
        # 零点一弧度）并保持到下一事件边界或回合末
        recs, drops = [], []
        for (t0, t1, _sgn), t_hold in zip(GUST_WINDOWS, (5.0, 12.0)):
            sel = (t >= t1) & (t < t_hold)
            ts, zs, rs = t[sel], z[sel], roll[sel]
            rec = None
            for i in range(len(ts)):
                if (np.all(np.abs(zs[i:] - 1.5) <= 0.15)
                        and np.all(np.abs(rs[i:]) <= 0.1)):
                    rec = float(ts[i] - t1)
                    break
            recs.append(rec)
            w = (t >= t0) & (t < t1)
            pre = (t < t0)
            z_start = z[pre][-1] if pre.any() else 1.5
            drops.append(float(min(0.0, np.min(z[w]) - z_start))
                         if w.any() else 0.0)
        ok = [r_ for r_ in recs if r_ is not None]
        m["recoveries"] = recs
        m["recovery_mean"] = float(np.mean(ok)) if ok else float("nan")
        m["recovery_unresolved"] = len(recs) - len(ok)
        m["max_drop"] = float(min(drops))       # 负值，越负跌得越深
    return m


def summarize(task, stats):
    alives = [s["alive"] for s in stats]
    out = {"alive_n": int(np.sum(alives)), "n": len(alives),
           "rps": float(np.mean([s["rps"] for s in stats]))}
    ms = [s["metrics"] for s in stats if s["alive"]]
    key = "seg_err_mean" if task == TASK_STEP else "recovery_mean"
    vals = [m[key] for m in ms if not math.isnan(m.get(key, float("nan")))]
    out[key] = float(np.mean(vals)) if vals else float("nan")
    if task == TASK_STEP:
        out["settle_unresolved_total"] = int(sum(
            m["settle_unresolved"] for m in ms))
    else:
        out["max_drop"] = float(np.mean([m["max_drop"] for m in ms]))
        out["recovery_unresolved_total"] = int(sum(
            m["recovery_unresolved"] for m in ms))
    return out


if __name__ == "__main__":
    # 冒烟：冻结网络两任务各一回合
    import torch
    from rl.wave_inverse_cfm import VelocityNet
    ck = torch.load(os.path.join(PROJECT_ROOT, "rl", "data",
                                 "wave_inverse_cfm.pt"), weights_only=False)
    net = VelocityNet()
    net.load_state_dict(ck["state_dict"])
    net.eval()
    norm = (ck["w_mu"], ck["w_sd"], ck["t_mu"], ck["t_sd"])
    set_gust_amp(1.5)
    for task, seed in ((TASK_STEP, 1000), (TASK_GUST, 1100)):
        st = run_task_episode(net, norm, task, seed)
        print(task, "alive=", st["alive"], "rps=%+.3f" % st["rps"],
              st["metrics"])
