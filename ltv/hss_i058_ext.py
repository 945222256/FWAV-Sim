# -*- coding: utf-8 -*-
"""I-058 外部可比性腿：三输入协议（对齐 I-055 现行值协议）重跑 K 阶梯。
临时驱动，结论并入双轨报告后与主腿产出一起提交。"""
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
import hss_i046_h2h as h2h  # noqa: E402

h2h.N_IN = 3
BATCHES = {"rep45": [10.5, 30.0], "hop9": [10.5, 110.0]}
out = {}
for tag, win in BATCHES.items():
    h2h.WINDOW = tuple(win)
    t0 = time.time()
    b = h2h.run_batch(tag, os.path.join(_ROOT, "logs",
                                        "_i006_%s.csv" % tag))
    out[tag] = {K: {"net_this": v["net_ratio_this_half"],
                    "net_swap": v["net_ratio_swap_half"],
                    "net_worst": v["net_worst"]}
                for K, v in b["results"].items()}
    print("[%s 3in] 滚转劣者 K=0 %.3f K=1 %.3f K=2 %.3f 用时 %.0fs" % (
        tag, out[tag]["K=0"]["net_worst"][0],
        out[tag]["K=1"]["net_worst"][0],
        out[tag]["K=2"]["net_worst"][0], time.time() - t0), flush=True)
with open(os.path.join(_HERE, "_i058_ext_leg.json"), "w",
          encoding="utf-8") as fp:
    json.dump(out, fp, ensure_ascii=False, indent=2)
print("done")
