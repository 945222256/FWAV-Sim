# -*- coding: utf-8 -*-
"""
eval_task_i076.py — I-076 配对评估：冻结臂对微调臂，两任务十六种子
====================================================================
协议出处：预登记第一节（同噪声序列两臂，唯一差异是逆映射网络权重）。
输出：rl/data/task_eval/{arm}/{task}/seed_*.npz
     rl/data/task_eval_table.md + rl/figs/task_eval_compare.png
用法：EVAL_ARMS=frozen,ft python rl/eval_task_i076.py
"""
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import torch

from rl.waveform_inverse import DATA, FIGS
from rl.wave_inverse_cfm import VelocityNet
from rl.tasks_i076 import (run_task_episode, summarize, TASK_STEP,
                           TASK_GUST, set_gust_amp)

OUT = os.path.join(DATA, "task_eval")
AMP_PATH = os.path.join(DATA, "gust_amp_selected.txt")
SEEDS = {TASK_STEP: range(1000, 1016), TASK_GUST: range(1100, 1116)}


def load_net(path):
    ck = torch.load(path, weights_only=False)
    if "state_dict" not in ck:
        src = torch.load(os.path.join(DATA, "wave_inverse_cfm.pt"),
                         weights_only=False)
        ck = {"state_dict": ck, "w_mu": src["w_mu"], "w_sd": src["w_sd"],
              "t_mu": src["t_mu"], "t_sd": src["t_sd"]}
    net = VelocityNet()
    net.load_state_dict(ck["state_dict"])
    net.eval()
    return net, (ck["w_mu"], ck["w_sd"], ck["t_mu"], ck["t_sd"])


def main():
    with open(AMP_PATH, encoding="utf-8") as f:
        set_gust_amp(float(f.read().strip()))
    arms_sel = os.environ.get("EVAL_ARMS", "frozen,ft").split(",")
    arms = {}
    if "frozen" in arms_sel:
        arms["frozen"] = load_net(os.path.join(DATA,
                                               "wave_inverse_cfm.pt"))
    ft_path = os.path.join(DATA, "ft_task_policy.pt")
    if "ft" in arms_sel:
        arms["ft"] = load_net(ft_path)
    results = {}
    for arm, (net, norm) in arms.items():
        results[arm] = {}
        for task in (TASK_STEP, TASK_GUST):
            stats = []
            od = os.path.join(OUT, arm, task)
            os.makedirs(od, exist_ok=True)
            for seed in SEEDS[task]:
                st = run_task_episode(net, norm, task, seed)
                np.savez_compressed(os.path.join(od, f"seed_{seed}.npz"),
                                    alive=st["alive"], rps=st["rps"],
                                    n=st["n"],
                                    traj=st.get("traj", np.zeros((0, 4))))
                stats.append(st)
                print(f"[{arm}|{task}] seed={seed} 存活 {st['alive']} "
                      f"步回报 {st['rps']:+.3f}", flush=True)
            results[arm][task] = summarize(task, stats)
            print(f"[{arm}|{task}] 合计 {results[arm][task]}", flush=True)

    if len(results) == 2:
        lines = ["# 有余量任务族配对评估（I-076，同噪声序列两臂）", ""]
        for task, tname in ((TASK_STEP, "高度阶跃跟踪"), (TASK_GUST,
                                                          "突风扰动恢复")):
            f_, t_ = results["frozen"][task], results["ft"][task]
            lines += [f"## {tname}", "",
                      "| 臂 | 存活 | 步回报均值 | 指标均值 | 未收口数 |",
                      "|---|---|---|---|---|"]
            key = ("seg_err_mean" if task == TASK_STEP
                   else "recovery_mean")
            kname = ("段跟踪误差 m" if task == TASK_STEP
                     else "恢复时间 s")
            unk = ("settle_unresolved_total" if task == TASK_STEP
                   else "recovery_unresolved_total")
            lines.append(f"| 冻结 | {f_['alive_n']}/{f_['n']} | "
                         f"{f_['rps']:+.3f} | {kname} {f_[key]:.3f} | "
                         f"{f_.get(unk, '-')} |")
            lines.append(f"| 微调 | {t_['alive_n']}/{t_['n']} | "
                         f"{t_['rps']:+.3f} | {kname} {t_[key]:.3f} | "
                         f"{t_.get(unk, '-')} |")
            if task == TASK_GUST:
                lines.append(f"最大高度跌落：冻结 {f_['max_drop']:.3f} m，"
                             f"微调 {t_['max_drop']:.3f} m")
            lines.append("")
        with open(os.path.join(DATA, "task_eval_table.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("\n".join(lines))

        # 图：每任务一格，两臂 z(t) 叠加（展示任一臂死亡的第一个种子）
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                           "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
        for ax, task in zip(axes, (TASK_STEP, TASK_GUST)):
            seeds = list(SEEDS[task])
            show = None
            for s_ in seeds:
                fa = np.load(os.path.join(OUT, "frozen", task,
                                          f"seed_{s_}.npz"))
                ta = np.load(os.path.join(OUT, "ft", task,
                                          f"seed_{s_}.npz"))
                if not (bool(fa["alive"]) and bool(ta["alive"])):
                    show = s_
                    break
            if show is None:
                show = seeds[0]
            for arm, c in (("frozen", "#2a9d8f"), ("ft", "#e76f51")):
                d = np.load(os.path.join(OUT, arm, task,
                                         f"seed_{show}.npz"))
                ax.plot(d["traj"][:, 0], d["traj"][:, 1], color=c,
                        label=f"{arm} 存活={bool(d['alive'])}", lw=1.4)
            d = np.load(os.path.join(OUT, "frozen", task,
                                     f"seed_{show}.npz"))
            ax.plot(d["traj"][:, 0], d["traj"][:, 3], "k--", lw=0.8,
                    label="高度指令")
            ax.set_title(f"{'高度阶跃' if task == TASK_STEP else '突风恢复'}"
                         f" 种子 {show}")
            ax.set_xlabel("t (s)")
            ax.set_ylabel("z (m)")
            ax.legend(fontsize=8)
        fig.suptitle("有余量任务族两臂配对对比（同噪声序列）")
        fig.tight_layout()
        out = os.path.join(FIGS, "task_eval_compare.png")
        fig.savefig(out, dpi=140, bbox_inches="tight")
        print("[fig]", out)


if __name__ == "__main__":
    main()
