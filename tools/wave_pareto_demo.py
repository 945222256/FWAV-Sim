# -*- coding: utf-8 -*-
"""
wave_pareto_demo.py — 多目标波形设计试跑（I-080 交付件之一）
================================================================
用统一优化器库的 NSGA-II 在波形参数包线上做双目标设计：
  目标一（最小化）：升力命中误差 |Fz − FZ_TARGET|（FZ_TARGET 取
    悬停邻域代表值 9.5 牛，出处 rl/waveform_inverse.py 数据集口径）；
  目标二（最小化）：滚转力矩幅值 |Mx|（滚转通道纯度）。
评估核：rl/waveform_inverse.forward（S&D 准定常前向物理，已台架辨识）。
两目标存在真实互斥：差动与不对称参数能换升力但引入滚转矩。
产物：logs/wave_pareto_front.json（前沿点与目标值）+
     logs/wave_pareto_front.png（前沿散点图）
用法：python tools/wave_pareto_demo.py
"""
import json
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.optimizers import NSGA2
from rl.waveform_inverse import RANGES, THETA_NAMES, forward

FZ_TARGET = 9.5
POP = 40
GENS = 30
SEED = 2026
OUT_JSON = os.path.join(PROJECT_ROOT, "logs", "wave_pareto_front.json")
OUT_PNG = os.path.join(PROJECT_ROOT, "logs", "wave_pareto_front.png")


def objectives(p):
    W = forward([p[k] for k in THETA_NAMES])
    return np.array([abs(W[0] - FZ_TARGET), abs(W[1])])


def main():
    dom = {k: RANGES[k] for k in THETA_NAMES}
    opt = NSGA2(dom, seed=SEED, pop=POP)
    for g in range(GENS):
        c = opt.ask()
        opt.tell([objectives(p) for p in c])
        if g % 10 == 0 or g == GENS - 1:
            f1 = [float(o[0]) for _, o in opt.pareto_front()]
            print(f"[pareto] 代 {g}  前沿 {len(f1)} 点  "
                  f"升力命中误差最小 {min(f1):.3f} N", flush=True)
    front = opt.pareto_front()
    pts = [{"theta": {k: float(p[k]) for k in THETA_NAMES},
            "obj_lift_err_N": float(o[0]),
            "obj_roll_moment_Nm": float(o[1])} for p, o in front]
    pts.sort(key=lambda d: d["obj_lift_err_N"])
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"task": "I-080 多目标波形设计试跑",
                   "objectives": ["|Fz-9.5| (N) 最小化",
                                  "|Mx| (N·m) 最小化"],
                   "evaluator": "rl/waveform_inverse.forward（S&D 准定常）",
                   "pop": POP, "gens": GENS, "seed": SEED,
                   "front": pts}, f, ensure_ascii=False, indent=1)
    print(f"[pareto] 前沿 {len(pts)} 点 → {OUT_JSON}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                       "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    ax.scatter([d["obj_lift_err_N"] for d in pts],
               [d["obj_roll_moment_Nm"] for d in pts], s=28, c="#e76f51")
    ax.set_xlabel("升力命中误差 |Fz − 9.5 牛|（N，最小化）")
    ax.set_ylabel("滚转力矩幅值 |Mx|（N·m，最小化）")
    ax.set_title("多目标波形设计帕累托前沿（NSGA-II，统一优化器库试跑）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=140, bbox_inches="tight")
    print(f"[pareto] 图 → {OUT_PNG}")


if __name__ == "__main__":
    main()
