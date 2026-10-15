# -*- coding: utf-8 -*-
"""第五十六班辅助核查（只读）：A=第五十班最优波形过框架标定；B=50 诊断波形全程
my(θ) 曲线最小值扫描（判据①通过需要 my*(θ*)≈−0.0186·f²，即小负值）。"""
import os, sys, json, math
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for p in (PROJECT_ROOT, TOOLS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)
import unified_search_framework as uf

print("== A: class-50 optimum wave through framework calibrator ==")
w50 = dict(alpha_set=30.5749, mean=22.0012, split=0.1039, lag=-0.0674,
           amp=11.4407, freq=1.2383, speed=3.8121)
cal = uf.calibrate_waveform(dict(w50))
for k, v in cal.items():
    print("  %s = %.6f" % (k, v))
print("  need/cap = %.3f  dfx>0: %s" % (cal["need"] / cal["cap"],
                                        cal["dfx_df"] > 0))
print("  MY_DC_COEF*f^2 = %.6f  (pass needs my_star ~ %.6f)"
      % (-0.0186 * w50["freq"] ** 2, 0.0186 * w50["freq"] ** 2))

print("== B: min my(theta) sweep over 50 diag waves ==")
d = json.load(open(os.path.join(PROJECT_ROOT, "logs", "unified_big_diag.json"),
                  encoding="utf-8"))
waves = [r["wave"] for r in d["records"]]
ths = [-10.0, -5.0, 0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0]
mins = []
for wv in waves:
    vals = []
    for th in ths:
        vals.append(uf.my_curve_point(dict(wv), th))
    mins.append(min(vals))
mins = np.array(mins)
print("  min my over grid: min=%.4f med=%.4f max=%.4f  negative count=%d/%d"
      % (mins.min(), np.median(mins), mins.max(), int((mins < 0).sum()),
         len(mins)))
need_floor = 0.0186 * np.array([w["freq"] for w in waves]) ** 2
print("  structural need floor |my - (-0.0186 f^2)| at my=0: %.4f..%.4f"
      % (need_floor.min(), need_floor.max()))
caps = np.array([r["cal"]["cap"] for r in d["records"]])
print("  caps: %.4f..%.4f" % (caps.min(), caps.max()))
print("  waves where need floor < cap (my would have to be negative): %d/%d"
      % (int((need_floor < caps).sum()), len(caps)))
