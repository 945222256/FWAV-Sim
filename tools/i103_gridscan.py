# -*- coding: utf-8 -*-
"""
i103_gridscan.py — I-103 孤峰邻域密集网格扫描(存活时间全地图)

预登记见 tools/I103_网格预登记_20261007.md。33 点网格,每点一次
35 秒在环跑(坡度十度科目一口径),产物为存活时间表与稳定边界线。
达标(满窗)采样的 CSV 旁建立"★★"前缀归档副本,有意义(超孤峰
21.8 秒或边界特征)建立"★"前缀副本。
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISAAC_PY = "g:/Playground/isaac_sim_5.1.0/python.bat"

KPA_GRID = [round(1.50 + 0.04 * i, 2) for i in range(11)]
TRIM_GRID = [-0.08, -0.10, -0.12]
PEAK_SURV = 21.8


def run_case(kpa, trim, tag):
    # I-111 统一在环启动入口：锁四项与背靠背逐字节复验在入口内焊死，
    # 本脚本不再直接起仿真。
    from modules.sim_entry import launch_sim
    extra = {
        "FLAPPER_MAX_S": "35",
        "FLAPPER_BANK_TEST": "1", "FLAPPER_BANK_DEG": "10",
        "FLAPPER_BANK_T": "5", "FLAPPER_BANK_SLEW": "0.05",
        "FLAPPER_WING_MIX": "1",
        "FLAPPER_KP_A": "%.4f" % kpa,
        "FLAPPER_PITCH_TRIM": "%.4f" % trim,
    }
    verdict = launch_sim(
        extra, "I103_%s" % tag,
        "I-103网格扫描 %s 角度外环%.2f 配平%.3f" % (tag, kpa, trim), 35,
        os.path.join(ROOT, "logs"), timeout_s=420)
    if not verdict["ok"]:
        print("[拒] 统一入口拒绝或复验失败：%s" % verdict["reason"])
        return 0.0, float("nan"), "-"
    f = os.path.basename(verdict["csv"])
    df = pd.read_csv(verdict["csv"])
    t = df["t"].values
    z = df["z"].values
    idx = np.argmax(z < 2.5) if (z < 2.5).any() else -1
    surv = float(t[idx]) if idx >= 0 else float(t[-1])
    d = df[(t >= 8.0)]
    roll_med = float(np.degrees(d["roll"].median())) if len(d) else float("nan")
    # 星标纪律
    stars = 0
    if surv >= 34.5:
        stars = 2
    elif surv > PEAK_SURV:
        stars = 1
    if stars:
        star = "★★" if stars == 2 else "★"
        shutil.copy2(os.path.join(ROOT, "logs", f),
                     os.path.join(ROOT, "logs", star + f))
    return surv, roll_med, f


def main():
    rows = []
    for trim in TRIM_GRID:
        for kpa in KPA_GRID:
            tag = "r05_k%.2f_t%.3f" % (kpa, trim)
            surv, roll_med, f = run_case(kpa, trim, tag)
            mark = "★★" if surv >= 34.5 else ("★" if surv > PEAK_SURV else "")
            print("%s 外环%.2f 配平%+.3f 存活%6.1fs 滚转中位%+7.1f度 %s"
                  % (tag, kpa, trim, surv, roll_med, mark), flush=True)
            rows.append((kpa, trim, surv, roll_med, f, mark))
    # 汇总表
    out = os.path.join(ROOT, "logs", "I103_网格扫描汇总表_R05_20261007.csv")
    pd.DataFrame(rows, columns=["KP_A", "PITCH_TRIM", "存活秒",
                                "滚转中位度", "数据流", "星标"]).to_csv(out, index=False,
                                                                         encoding="utf-8-sig")
    print("汇总表 →", out)
    best = max(rows, key=lambda r: r[2])
    print("最长存活: 外环%.2f 配平%+.3f %.1f 秒" % (best[0], best[1], best[2]))


if __name__ == "__main__":
    main()
