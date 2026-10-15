# -*- coding: utf-8 -*-
"""
pool_ident_csv.py — 多跑拼接器（盘旋配平点辨识统计量加倍，I-006）

背景：盘旋平台存活时长有限（协调段约三秒），单跑每半仅约三拍，
留出验证统计量不足。判据的奇偶拍分半只要求拍样本量，不要求时间
连续；多次同配方采集的协调段拼接后按拼接时间轴重新计算拍序，
统计量按跑数线性增加。

用法：python tools/pool_ident_csv.py out.csv in1.csv in2.csv [--t0 10.5 --t1 13.4]
拼接规则：每份输入按窗口裁剪后，时间轴平移到前一份末尾加一拍周期，
全部列原样保留。元数据侧车不生成（拟合时显式传窗口与参数）。
"""
import sys

import pandas as pd
import numpy as np


def main():
    args = sys.argv[1:]
    t0 = 10.5
    t1 = 13.4
    pos = []
    i = 0
    while i < len(args):
        if args[i] == "--t0":
            t0 = float(args[i + 1]); i += 2
        elif args[i] == "--t1":
            t1 = float(args[i + 1]); i += 2
        else:
            pos.append(args[i]); i += 1
    out, ins = pos[0], pos[1:]
    parts = []
    t_off = 0.0
    for k, f in enumerate(ins):
        d = pd.read_csv(f)
        d = d[(d.t >= t0) & (d.t <= t1) & (d.z > 1.0)].copy()
        if k > 0:
            fs = float(np.nanmedian(d.freqL.values))
            t_off = parts[-1].t.max() + 1.0 / max(fs, 0.5)
        d.t = d.t + t_off
        parts.append(d)
    pooled = pd.concat(parts, ignore_index=True)
    pooled.to_csv(out, index=False)
    print("拼接 %d 份 共 %d 行 时长 %.1fs → %s"
          % (len(parts), len(pooled), pooled.t.max() - pooled.t.min(), out))


if __name__ == "__main__":
    main()
