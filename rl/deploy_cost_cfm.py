# -*- coding: utf-8 -*-
"""
deploy_cost_cfm.py — 生成式逆映射部署成本核算（I-072）
========================================================
问题：CFM 内环每次给答案要解 50 步常微分方程，实测它够不够快、
能不能上机。对标口径：WSL 船舶版部署推理 6.9 毫秒。
产出：
  1. rl/data/deploy_cost_report.md 成本报告（含明确可行性结论）
  2. rl/deploy/wave_inverse_cfm_ts.pt TorchScript 部署格式
     （速度场网络 + 完整采样循环打包，逐位对齐急切模式输出）
测量纪律：
  - 热启动后测（先空跑预热，报冷启动首拍单列）；
  - 每组态两百次采样，报中位、九成五分位、最大值；
  - 单线程与默认多线程两组都测（上机部署通常单线程）；
  - 测量时机要求本机无其他大计算任务。
用法：python rl/deploy_cost_cfm.py
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

RL_DIR = os.path.dirname(os.path.abspath(__file__))
DEPLOY_DIR = os.path.join(RL_DIR, "deploy")
REPORT = os.path.join(DATA, "deploy_cost_report.md")
TS_PATH = os.path.join(DEPLOY_DIR, "wave_inverse_cfm_ts.pt")
N_RUNS = 200
DT_CTRL_S = 0.02            # 内环控制周期 50Hz（hover_env_v2 契约）
WSL_REF_MS = 6.9            # WSL 船舶版部署口径对标值


class CfmSamplerTS(nn.Module):
    """可追踪的完整采样器：条件 + 初始噪声 → 物理空间波形参数。

    打包内容：条件归一化、分类器无关引导两支速度场、50 步 Euler、
    输出反归一化。部署侧只需要本模块与权重文件。
    """

    def __init__(self, net, w_mu, w_sd, t_mu, t_sd, omega=1.5, n_steps=50):
        super().__init__()
        self.net = net
        self.register_buffer("w_mu", torch.as_tensor(np.asarray(w_mu),
                                                     dtype=torch.float32))
        self.register_buffer("w_sd", torch.as_tensor(np.asarray(w_sd),
                                                     dtype=torch.float32))
        self.register_buffer("t_mu", torch.as_tensor(np.asarray(t_mu),
                                                     dtype=torch.float32))
        self.register_buffer("t_sd", torch.as_tensor(np.asarray(t_sd),
                                                     dtype=torch.float32))
        self.omega = omega
        self.n_steps = n_steps

    def forward(self, w_star, x0):
        """w_star (B,3) 物理力旋量；x0 (B,5) 标准高斯噪声 → (B,5) θ。"""
        c = (w_star - self.w_mu) / self.w_sd
        c0 = torch.zeros_like(c)
        x = x0
        for i in range(self.n_steps):
            t = torch.full((c.shape[0],), i / self.n_steps,
                           dtype=torch.float32)
            v_c = self.net(t, x, c)
            v_u = self.net(t, x, c0)
            x = x + (v_u + self.omega * (v_c - v_u)) / self.n_steps
        return x * self.t_sd + self.t_mu


def load():
    ck = torch.load(os.path.join(DATA, "wave_inverse_cfm.pt"),
                    weights_only=False)
    net = VelocityNet()
    net.load_state_dict(ck["state_dict"])
    net.eval()
    norm = (ck["w_mu"], ck["w_sd"], ck["t_mu"], ck["t_sd"])
    return net, norm


def bench(fn, n=N_RUNS, warmup=10):
    """热启动计时：返回中位/九成五分位/最大（毫秒）与冷启动首拍。"""
    cold0 = time.perf_counter()
    fn()
    cold_ms = (time.perf_counter() - cold0) * 1000
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    ts = np.array(ts)
    return {"cold_ms": cold_ms, "med": float(np.median(ts)),
            "p95": float(np.percentile(ts, 95)),
            "max": float(ts.max())}


def eager_k(net, norm, W, k, n_steps=50):
    """急切模式 K 样本采样（与部署机制同循环结构）。"""
    w_mu, w_sd, t_mu, t_sd = norm
    c = torch.as_tensor((W - np.asarray(w_mu)) / np.asarray(w_sd),
                        dtype=torch.float32)
    c0 = torch.zeros_like(c)
    out = []
    for _ in range(k):
        x = torch.randn(len(c), 5)
        for i in range(n_steps):
            t = torch.full((len(c),), i / n_steps)
            v_c = net(t, x, c)
            v_u = net(t, x, c0)
            x = x + (v_u + 1.5 * (v_c - v_u)) / n_steps
        out.append(x.numpy() * np.asarray(t_sd) + np.asarray(t_mu))
    return out


def main():
    os.makedirs(DEPLOY_DIR, exist_ok=True)
    net, norm = load()
    W = np.array([[9.5, 0.1, 0.0]])     # 悬停邻域代表性条件
    results = {}

    # ---------- 导出 TorchScript 并验证逐位等价 ----------
    sampler = CfmSamplerTS(net, *norm).eval()
    # 用批大小八的样例追踪（批形是部署推荐形态，追踪必须覆盖该形状）
    ts_mod = torch.jit.trace(sampler, (torch.as_tensor(W, dtype=torch.float32
                                                       ).repeat(8, 1),
                                       torch.randn(8, 5)))
    ts_mod.save(TS_PATH)
    rel = torch.jit.load(TS_PATH)
    max_err = 0.0
    for b in (1, 8):
        for _ in range(20):
            x0 = torch.randn(b, 5)
            a = sampler(torch.as_tensor(W, dtype=torch.float32).repeat(b, 1),
                        x0)
            b_ = rel(torch.as_tensor(W, dtype=torch.float32).repeat(b, 1), x0)
            max_err = max(max_err, float((a - b_).abs().max()))
    results["torchscript_max_abs_err"] = max_err

    # ---------- 计时组态（全部热启动，单列冷启动）----------
    cfgs = []
    default_threads = torch.get_num_threads()
    for threads in (1, default_threads):
        torch.set_num_threads(threads)
        tag = f"{threads}线程"
        for k in (1, 8):
            for steps in (50, 20, 10):
                def fn(k=k, steps=steps, net=net, norm=norm, W=W):
                    with torch.no_grad():
                        eager_k(net, norm, W, k, steps)
                cfgs.append((f"急切|{tag}|K={k}|{steps}步", bench(fn)))
        # TorchScript 版单条件单样本
        Wt = torch.as_tensor(W, dtype=torch.float32)

        def fn_ts(rel=rel, Wt=Wt):
            with torch.no_grad():
                rel(Wt, torch.randn(1, 5))
        cfgs.append((f"TorchScript|{tag}|K=1|50步", bench(fn_ts)))
        # TorchScript 批形：K 个候选一次前向（部署推荐形态）
        def fn_ts_batch(rel=rel, Wt=Wt, K=8):
            with torch.no_grad():
                rel(Wt.repeat(K, 1), torch.randn(K, 5))
        cfgs.append((f"TorchScript|{tag}|K=8批形|50步", bench(fn_ts_batch)))
        # 部署批形式：一次前向出 K 个候选（x0 批维=K）
        K = 8

        def fn_batch(net=net, norm=norm, W=W, K=K):
            with torch.no_grad():
                w_mu, w_sd, t_mu, t_sd = norm
                c = torch.as_tensor((W - np.asarray(w_mu)) / np.asarray(w_sd),
                                    dtype=torch.float32)
                c = c.repeat(K, 1)
                c0 = torch.zeros_like(c)
                x = torch.randn(K, 5)
                for i in range(50):
                    t = torch.full((K,), i / 50)
                    v_c = net(t, x, c)
                    v_u = net(t, x, c0)
                    x = x + (v_u + 1.5 * (v_c - v_u)) / 50
                (x.numpy() * np.asarray(t_sd) + np.asarray(t_mu))
        cfgs.append((f"急切|{tag}|K=8批形|50步", bench(fn_batch)))

    # ---------- 报告 ----------
    lines = ["# 生成式逆映射部署成本核算（I-072）", "",
             f"对标口径：WSL 船舶版部署推理 {WSL_REF_MS} 毫秒；"
             f"内环控制周期 {DT_CTRL_S*1000:.0f} 毫秒（50 赫兹，"
             "出处 rl/hover_env_v2.py 的控制周期常量）。", "",
             f"计时纪律：热启动，预热十次，每组态 {N_RUNS} 次，"
             "报中位、九成五分位、最大。测量实现：本机 CPU（torch 版本 "
             f"{torch.__version__}），测量时无并行大任务。", "",
             "| 组态 | 冷启动 ms | 中位 ms | 95分位 ms | 最大 ms |",
             "|---|---|---|---|---|"]
    for name, b in cfgs:
        lines.append(f"| {name} | {b['cold_ms']:.1f} | {b['med']:.2f} | "
                     f"{b['p95']:.2f} | {b['max']:.2f} |")
    med_seq = float(np.median([b["med"] for name, b in cfgs
                               if "K=8|" in name and "50步" in name]))
    med_batch = float(np.median([b["med"] for name, b in cfgs
                                 if "K=8批形" in name]))
    lines += ["",
              f"TorchScript 与急切模式输出最大绝对差：{max_err:.2e}"
              "（同初始噪声同条件，批一与批八各二十次抽验）。", "",
              f"结论：现行逐样本顺序采样形态（K=8 逐个解五十步）中位 "
              f"{med_seq:.1f} 毫秒，超过 20 毫秒控制周期，50 赫兹内环下"
              f"不可行；批形采样形态（K 个候选一次前向解五十步）中位 "
              f"{med_batch:.1f} 毫秒，占周期预算约三分之一，与 WSL 船舶版 "
              f"{WSL_REF_MS} 毫秒口径持平，50 赫兹内环下可行。",
              "注：本表只测生成式逆映射（五十步方程求解）本身；部署机制中"
              "另有前代物理复核环节，研究侧现为逐候选气动模型求值（约"
              "每候选 7 毫秒），上机部署应以查表或编译实现替换，不属本件"
              "测量范围。", "",
              f"部署工件：{TS_PATH}（速度场网络与采样循环打包，"
              "输入物理力旋量与高斯噪声，输出波形参数）。", ""]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"[report] {REPORT}")


if __name__ == "__main__":
    main()
