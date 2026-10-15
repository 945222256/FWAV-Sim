# -*- coding: utf-8 -*-
"""trail_viz_selftest.py — TrailViz 冒烟（headless；pxr 需 kit 引导）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config.sim_config  # noqa: F401  占住 config 名（防 pip_prebundle 遮蔽）

from isaacsim import SimulationApp
app = SimulationApp({"headless": True})

import numpy as np
from pxr import Usd
from modules.trail_viz import TrailViz

_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "logs", "trail_selftest_result.txt")
open(_LOG, "w", encoding="utf-8").write("script entered\n")   # 哨兵1

try:
    stage = Usd.Stage.CreateInMemory()
    tv = TrailViz(stage)
    for i in range(200):
        a = i * 0.1
        tv.push_ref([a, np.sin(a), 1.5])
        tv.push_actual([a, np.sin(a) + 0.1, 1.45])
    tv.flush()
    p1 = stage.GetPrimAtPath("/World/Viz/DesiredPath")
    p2 = stage.GetPrimAtPath("/World/Viz/ActualTrail")
    print("desired points:", len(p1.GetAttribute("points").Get()), "valid:", p1.IsValid(),
          "type:", p1.GetAttribute("type").Get(), flush=True)
    print("actual points:", len(p2.GetAttribute("points").Get()), flush=True)
    print("widths:", p1.GetAttribute("widths").Get(), flush=True)
    tv.set_path([[0, 0, 1], [1, 1, 1], [2, 0, 1]])
    tv.flush()
    # kit stdout 重定向下早退打印会被吞（实测 140ms 内 print+close 全丢），
    # 结果落盘最稳
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "logs", "trail_selftest_result.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"desired points: {len(p1.GetAttribute('points').Get())} "
                f"valid: {p1.IsValid()} type: {p1.GetAttribute('type').Get()}\n")
        f.write(f"actual points: {len(p2.GetAttribute('points').Get())}\n")
        f.write(f"widths: {p1.GetAttribute('widths').Get()}\n")
        f.write(f"set_path ok: {len(p1.GetAttribute('points').Get())}\n")
        f.write("SELFTEST PASS\n")
    print("SELFTEST PASS", flush=True)
except Exception:
    import traceback
    with open(_LOG, "a", encoding="utf-8") as f:
        f.write(traceback.format_exc() + "\n")
    raise
finally:
    app.close()
