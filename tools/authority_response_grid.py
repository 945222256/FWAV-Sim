# -*- coding: utf-8 -*-
"""
authority_response_grid.py — 权限响应函数网格测量（2026-09-28）

整改纪律第一条的首次执行：密度通道与幅值通道的滚转权限作为
（俯仰 θ × 侧滑 β）的二元函数测量，φ 取 0° 与 +10° 两档分文件。
供 authority_fit_figs.py 拟合与绘图。

网格：θ ∈ {−13, −10, −6.6, −4, −2, 0}°，β ∈ {−45, −30, −20, −10,
−5, 0, 5, 10, 15}°。工况：V=3.95 m/s、f=1.80 Hz、vz=0、ω=0。
每格点三测：基线、右翼停拍（密度通道）、幅值 ±0.10 rad（幅值通道）。

用法： python tools/authority_response_grid.py --phi 0
      python tools/authority_response_grid.py --phi 10
产物： logs/authority_response_grid_phi{0,10}.json
"""
import os
import sys
import json
import math
import datetime
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
from config import sim_config as cfg
from modules.aero_solver import euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

V0, F0 = 3.95, 1.80
N_PER_BEAT, N_BEATS = 288, 4
DA_TEST = 0.10
TH_GRID = [-13.0, -10.0, -6.6, -4.0, -2.0, 0.0]
BE_GRID = [-45, -30, -20, -10, -5, 0, 5, 10, 15]

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(cfg.FlapKinematicsConfig.ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)
    w.amplitude = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
AMP0 = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
TIMEBASE = [600000.0]


def mean_Tx(quat, vel):
    model.set_wing_command(frequency=F0)
    T = N_BEATS / F0
    n = N_PER_BEAT * N_BEATS
    ts = [TIMEBASE[0] + i * T / n for i in range(n)]
    TIMEBASE[0] += T
    Tq = np.zeros(3)
    for t in ts:
        model.compute_forces(np.zeros(3), quat, vel, np.zeros(3), t)
        Tq += model.current_T_body
    return Tq / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phi", type=float, required=True)
    args = ap.parse_args()
    phi = math.radians(args.phi)
    rows = []
    for th in TH_GRID:
        for be in BE_GRID:
            beta = math.radians(be)
            vel = np.array([V0 * math.cos(beta), V0 * math.sin(beta), 0.0])
            quat = euler_to_quat(phi, math.radians(th), 0.0)
            Tb = mean_Tx(quat, vel)
            model.wing_right.stroke_gate = 0.0
            Td = mean_Tx(quat, vel)
            model.wing_right.stroke_gate = 1.0
            model.wing_left.amplitude = AMP0 + DA_TEST
            model.wing_right.amplitude = AMP0 - DA_TEST
            Ta = mean_Tx(quat, vel)
            model.wing_left.amplitude = AMP0
            model.wing_right.amplitude = AMP0
            rows.append({"theta": th, "beta": be, "phi": args.phi,
                         "density_auth": float(Td[0] - Tb[0]),
                         "dA_auth": float(Ta[0] - Tb[0])})
            print(f"[grid] φ={args.phi:+.0f} θ={th:+6.1f} β={be:+4d}："
                  f"密度 {rows[-1]['density_auth']:+.5f}  "
                  f"幅值 {rows[-1]['dA_auth']:+.5f}", flush=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "V": V0, "f": F0, "dA_test": DA_TEST,
           "phi": args.phi, "rows": rows}
    path = os.path.join(cfg.LOG_DIR,
                        f"authority_response_grid_phi{int(args.phi)}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print(f"[grid] 完成 → {path}", flush=True)


if __name__ == "__main__":
    main()
