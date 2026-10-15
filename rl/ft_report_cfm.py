# -*- coding: utf-8 -*-
"""
ft_report_cfm.py — I-071 结果报告生成器
========================================
读 rl/data/cl_cfm_ft/{frozen,ft}/seed_*.npz 配对结果，产出：
  1. rl/data/cl_cfm_ft_table.md 逐种子配对表 + 合计
  2. rl/figs/closed_loop_cfm_ft.png 两臂 z(t) 对比（死亡种子优先展示）
用法：python rl/ft_report_cfm.py
"""
import glob
import os
import sys

import numpy as np

RL_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(RL_DIR, "data", "cl_cfm_ft")
TABLE = os.path.join(RL_DIR, "data", "cl_cfm_ft_table.md")
FIG = os.path.join(RL_DIR, "figs", "closed_loop_cfm_ft.png")


def load_arm(arm):
    out = {}
    for f in sorted(glob.glob(os.path.join(OUT_DIR, arm, "seed_*.npz"))):
        d = np.load(f)
        seed = int(os.path.basename(f)[5:-4])
        out[seed] = {"alive": bool(d["alive"]), "rps": float(d["rps"]),
                     "z_err": float(d["z_err"]), "traj": d["traj"]}
    return out


def main():
    frozen = load_arm("frozen")
    ft = load_arm("ft")
    seeds = sorted(set(frozen) & set(ft))
    lines = ["# 生成式载体微调配对评估（I-071，同噪声序列两臂对比）", "",
             "| 种子 | 冻结存活 | 微调存活 | 冻结稳态误差 m | 微调稳态误差 m | "
             "冻结步回报 | 微调步回报 |",
             "|---|---|---|---|---|---|---|"]
    fl, tl, fz, tz, fr, tr_ = [], [], [], [], [], []
    for s in seeds:
        a, b = frozen[s], ft[s]
        lines.append(f"| {s} | {a['alive']} | {b['alive']} | "
                     f"{a['z_err']:.3f} | {b['z_err']:.3f} | "
                     f"{a['rps']:+.3f} | {b['rps']:+.3f} |")
        fl.append(a["alive"]); tl.append(b["alive"])
        if a["alive"]: fz.append(a["z_err"])
        if b["alive"]: tz.append(b["z_err"])
        fr.append(a["rps"]); tr_.append(b["rps"])
    n_f, n_t = int(np.sum(fl)), int(np.sum(tl))
    zf = float(np.mean(fz)) if fz else float("nan")
    zt = float(np.mean(tz)) if tz else float("nan")
    lines += ["", f"冻结臂：存活 {n_f}/{len(seeds)}，"
              f"存活种子稳态误差均值 {zf:.4f} m，步回报均值 {np.mean(fr):+.3f}",
              f"微调臂：存活 {n_t}/{len(seeds)}，"
              f"存活种子稳态误差均值 {zt:.4f} m，步回报均值 {np.mean(tr_):+.3f}",
              "", f"配对翻转：冻结死微调活 "
              f"{[s for s in seeds if not fl[seeds.index(s)] and tl[seeds.index(s)]]}；"
              f"冻结活微调死 "
              f"{[s for s in seeds if fl[seeds.index(s)] and not tl[seeds.index(s)]]}"]
    txt = "\n".join(lines)
    with open(TABLE, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)

    # 图：挑展示种子 = 任一臂死亡的种子（最多四格）+ 一格双方都活的最稳种子
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                       "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    dead = [s for s in seeds if not (frozen[s]["alive"] and ft[s]["alive"])]
    show = dead[:4]
    if len(show) < 4:
        alive_stable = sorted([s for s in seeds
                               if frozen[s]["alive"] and ft[s]["alive"]],
                              key=lambda s: frozen[s]["z_err"])
        show += alive_stable[:4 - len(show)]
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.4), sharex=True)
    for ax, s in zip(axes.ravel(), show):
        ax.plot(frozen[s]["traj"][:, 0], frozen[s]["traj"][:, 1],
                label=f"冻结 (存活={frozen[s]['alive']})", lw=1.4)
        ax.plot(ft[s]["traj"][:, 0], ft[s]["traj"][:, 1],
                label=f"微调 (存活={ft[s]['alive']})", lw=1.4)
        ax.axhline(1.5, ls="--", c="gray", lw=0.8)
        ax.set_title(f"种子 {s}")
        ax.set_ylabel("z (m)")
        ax.legend(fontsize=8)
    for ax in axes[1]:
        ax.set_xlabel("t (s)")
    fig.suptitle("生成式内环微调前后配对闭环对比（同环境随机化与同采样噪声序列）")
    fig.tight_layout()
    fig.savefig(FIG, dpi=140, bbox_inches="tight")
    print(f"[fig] {FIG}")
    print(f"[table] {TABLE}")


if __name__ == "__main__":
    main()
