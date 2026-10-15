# -*- coding: utf-8 -*-
"""
i100_retune.py — I-100 新构型控制参数重整定(TPE,统一优化器)

事项 I-100:偏航组合通道系数与俯仰环增益按新尾翼构型重标。
整定工具限用 tools/optimizers.py(机主要求)。

参数域(全部经环境键覆盖,零改码):
  YAW_DAMP     偏航角速率阻尼(旧 2.5,新舵效下相位裕度失配)
  PITCH_DAMP   俯仰角速率阻尼(旧 0.3)
  KI_A         俯仰误差积分(旧 0.15)
  MODAL_CROSS_K 模态交叉阻尼(旧 0=关;滚转-俯仰反相耦合阻尼入口)

损失(科目一口径在环,越小越好):
  (35 − 存活秒) + 0.5×|roll−10度|中位 + 0.5×|wz−0.44|均值
  + 5×elev 饱和占比 + 5×wz 峰值超限罚
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd

from tools.optimizers import TPE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISAAC_PY = "g:/Playground/isaac_sim_5.1.0/python.bat"

DOM = {
    "YAW_DAMP": (1.0, 6.0),
    "PITCH_DAMP": (0.10, 1.00),
    "KI_A": (0.05, 0.30),
    "MODAL_CROSS_K": (0.0, 0.5),
}


def run_case(pt, tag):
    # I-111 统一在环启动入口：锁四项与背靠背逐字节复验在入口内焊死，
    # 本脚本不再直接起仿真。
    from modules.sim_entry import launch_sim
    extra = {
        "FLAPPER_MAX_S": "35",
        "FLAPPER_BANK_TEST": "1", "FLAPPER_BANK_DEG": "10",
        "FLAPPER_BANK_T": "5", "FLAPPER_BANK_SLEW": "0.05",
        "FLAPPER_WING_MIX": "1",
        "FLAPPER_YAW_DAMP": "%.4f" % pt["YAW_DAMP"],
        "FLAPPER_PITCH_DAMP": "%.4f" % pt["PITCH_DAMP"],
        "FLAPPER_KI_A": "%.4f" % pt["KI_A"],
        "FLAPPER_MODAL_CROSS_K": "%.4f" % pt["MODAL_CROSS_K"],
    }
    verdict = launch_sim(
        extra, "I100tune_%s" % tag,
        "I-100重整定候选项%s %s" % (tag, pt), 35,
        os.path.join(ROOT, "logs"), timeout_s=420)
    if not verdict["ok"]:
        print("[拒] 统一入口拒绝或复验失败：%s" % verdict["reason"])
        return 99.0, 0.0, 99.0, 99.0, 1.0
    f = verdict["csv"]
    df = pd.read_csv(f)
    t = df["t"].values
    z = df["z"].values
    idx = np.argmax(z < 2.5) if (z < 2.5).any() else -1
    t_surv = float(t[idx]) if idx >= 0 else float(t[-1])
    d = df[(t >= 8.0)]
    roll_err = float(np.abs(np.degrees(d["roll"].median()) - 10.0))
    wz_dev = float(np.abs(d["wz"].mean() - 0.44)) if len(d) else 9.9
    elev_sat = float(np.mean(np.abs(d["elev"]) >= 0.298)) if len(d) else 1.0
    wz_peak_pen = max(0.0, float(np.abs(d["wz"]).max()) - 1.5) if len(d) else 9.9
    loss = (35.0 - min(t_surv, 35.0)) + 0.5 * roll_err + 0.5 * wz_dev \
        + 5.0 * elev_sat + wz_peak_pen
    return loss, t_surv, roll_err, wz_dev, elev_sat


def main():
    tpe = TPE(DOM, seed=100)
    history = []
    for rnd in range(3):
        batch = tpe.ask_batch(6 if rnd else 5)
        pts, ys = [], []
        for k, pt in enumerate(batch):
            tag = "r%d_%d" % (rnd, k)
            loss, ts, re_, wd, es = run_case(pt, tag)
            print("轮%d 点%d %s 损失%.2f 存活%.1fs 滚偏差%.1f wz偏差%.2f elev饱和%.2f"
                  % (rnd, k, {q: round(v, 3) for q, v in pt.items()},
                     loss, ts, re_, wd, es), flush=True)
            pts.append(pt)
            ys.append(loss)
            history.append((pt, loss, ts))
        tpe.tell(pts, ys)
    best = min(history, key=lambda h: h[1])
    print()
    print("最优参数:", {q: round(v, 4) for q, v in best[0].items()})
    print("损失 %.2f 存活 %.1fs" % (best[1], best[2]))


if __name__ == "__main__":
    main()
