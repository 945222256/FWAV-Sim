# -*- coding: utf-8 -*-
"""
probe_freq_response_decomp.py — 频率响应分解探针（2026-09-27）

动因：阶段一报告的两条结论受到质疑——其一，频率差动的滚转产出为何
小于偏航产出；其二，每翼秒均升力为何在 1.1~2.5 Hz 近似常数（是否
因为分裂比与攻角程序被锁定）。本探针只做解释性测量，不改动任何
判据。

测量设计：
  其一，在巡航配平姿态（V=3.95、θ=−13.03°）下测左翼每拍力与力矩
  全矢量随频率的函数，换算为秒均速率 q·f，分离升力项（Fz·f）、
  推力项（Fx·f）与两力矩项。偏航力矩来源核对：Tz ≈ −r_y·Fx
  （推力差经展向力臂）；滚转力矩来源核对：Tx ≈ +r_y·Fz − r_z·Fx。
  其二，在四个机制点（速度 3.95/2.5 m/s × 幅值 17.73°/35°）比较
  升力速率对频率的斜率，检验"频率灵敏度取决于拍动速度占来流速度
  份额"的解释。V=2.5 为未配平姿态，只作机制对照并登记。
  参考量：拍动切向速度量级 = 幅值×相位速率×频率×均径；分裂比
  0.176 时下扑相位速率 π/0.176≈17.85，均径取 0.16 m。

本探针全部波形参数除幅值外读配置，测量纪律三条沿用。
产物：logs/probe_freq_response_decomp.json + 控制台输出。
"""
import os
import sys
import json
import math
import importlib.util

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np

spec = importlib.util.spec_from_file_location(
    "dfs", os.path.join(PROJECT_ROOT, "tools", "quantum_diff_freq_scan.py"))
dfs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dfs)
cfg = dfs.cfg
model = dfs.model

F_SWEEP = [1.3, 1.55, 1.8, 2.05, 2.3]
MECH_POINTS = [(3.95, -13.03, 17.73), (3.95, -13.03, 35.0),
               (2.50, -13.03, 17.73), (2.50, -13.03, 35.0)]
R_MEAN = 0.16   # 均径估计（自定，只用于速度份额估算）


def wing_rates(V, th, f, amp_deg):
    """左翼在自身整数拍窗口的秒均力与力矩（q·f）。"""
    for w in (model.wing_left, model.wing_right):
        w.amplitude = math.radians(amp_deg)
    F, T = dfs.per_wing_beat_impulse("L", V, th, f)
    return F * f, T * f


def main():
    out = {"ts": dfs.datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
           "f_sweep": F_SWEEP, "mech_points": MECH_POINTS,
           "r_mean": R_MEAN, "rows": []}
    print("[probe] 频率响应分解（左翼，q·f 秒均口径）", flush=True)
    for V, th, amp in MECH_POINTS:
        rows = []
        for f in F_SWEEP:
            Fr, Tr = wing_rates(V, th, f, amp)
            rows.append({"f": f, "Fx": float(Fr[0]), "Fz": float(Fr[2]),
                         "Tx": float(Tr[0]), "Tz": float(Tr[2])})
            print(f"[probe] V={V:.2f} amp={amp:.2f}° f={f:.2f}："
                  f"Fx={Fr[0]:+.3f} N  Fz={Fr[2]:+.3f} N  "
                  f"Tx={Tr[0]:+.4f} N·m  Tz={Tr[2]:+.4f} N·m", flush=True)
        fs = np.array([r["f"] for r in rows])
        slope = {}
        for k in ("Fx", "Fz", "Tx", "Tz"):
            ys = np.array([r[k] for r in rows])
            slope[k] = float(np.polyfit(fs, ys, 1)[0])
        # 拍动切向速度份额估算（下扑峰值，split=0.176）
        v_flap = math.radians(amp) * (math.pi / cfg.FlapKinematicsConfig.SPLIT) \
            * 1.8 * R_MEAN
        rec = {"V": V, "theta": th, "amp": amp, "rows": rows,
               "slope_per_hz": slope,
               "v_flap_peak_at_1p8hz": v_flap,
               "v_flap_share": v_flap / V}
        out["rows"].append(rec)
        print(f"[probe] V={V:.2f} amp={amp:.2f}° 斜率(每Hz)："
              f"Fx {slope['Fx']:+.3f} N  Fz {slope['Fz']:+.3f} N  "
              f"Tx {slope['Tx']:+.4f}  Tz {slope['Tz']:+.4f} N·m  "
              f"拍动速度份额≈{v_flap/V:.0%}", flush=True)
    with open(os.path.join(cfg.LOG_DIR, "probe_freq_response_decomp.json"),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print("[probe] 完成 → logs/probe_freq_response_decomp.json", flush=True)


if __name__ == "__main__":
    main()
