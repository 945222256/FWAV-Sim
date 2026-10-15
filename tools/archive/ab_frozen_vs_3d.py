# -*- coding: utf-8 -*-
"""
ab_frozen_vs_3d.py — 冻结几何版 vs 真三维版 对拍三钩子（第十二班）

钩子一 悬停一致性：对称拍动、零来流下两版周期平均竖直力/前向力对照
       （回归门：对称工况新耦合理应抵消，量级应同阶）。
钩子二 差动偏航对拍：同差动状态两版翼面力矩对照——差值即冻结几何
       吞掉的"拍动直接出偏航/滚转"通道量值，回答 B_dA 塌缩的伪影占比。
钩子三 侧滑敏感性：侧向来流逐档，两版侧力与偏航力矩对照——回答
       "攻角对侧滑失明"丢失量。
用法： isaac python.bat tools/ab_frozen_vs_3d.py
产物：  logs/ab_frozen_vs_3d.json + docs/AB_冻结对拍三维.md
"""
import os
import sys
import json
import math
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
from config import sim_config as cfg
from modules.aero_solver import FlapperAeroModel, euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

T0 = [20000.0]


def cm(model, V, f, pd, vy=0.0, dA=0.0, n=48):
    model.set_wing_command(frequency=f)
    A0 = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
    model.wing_left.amplitude = A0 + dA
    model.wing_right.amplitude = A0 - dA
    quat = euler_to_quat(0.0, math.radians(pd), 0.0)
    vel = np.array([V, vy, 0.0])
    om = np.zeros(3)
    T = 1.0 / f
    F = np.zeros(3)
    Tq = np.zeros(3)
    for i in range(n):
        Fw, Tw = model.compute_forces(np.zeros(3), quat, vel, om,
                                      T0[0] + i * T / n)
        F += Fw
        Tq += Tw
    F /= n
    Tq /= n
    T0[0] += T
    return F, Tq


def fresh(kind):
    m = SaneDickinson3DModel() if kind == "3d" else FlapperAeroModel()
    for w in (m.wing_left, m.wing_right):
        w.alpha_set = math.radians(cfg.FlapKinematicsConfig.ALPHA_SET)
        w.split = cfg.FlapKinematicsConfig.SPLIT
        w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
        w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)
    return m


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    V, F0, P0 = 4.5, 1.55, -0.05
    out = {"ts": ts, "trim": [V, F0, P0]}

    # ── 钩子一：悬停一致性（对称、零来流）──
    hover = {}
    for kind in ("frozen", "3d"):
        m = fresh(kind)
        F, T = cm(m, 0.0, 1.55, 0.0)
        hover[kind] = dict(Fx=float(F[0]), Fy=float(F[1]), Fz=float(F[2]),
                           Tx=float(T[0]), Ty=float(T[1]), Tz=float(T[2]))
    out["hover"] = hover

    # ── 钩子二：差动对拍（翼面 B_dA 与偏航）──
    da = {}
    for kind in ("frozen", "3d"):
        m = fresh(kind)
        rows = []
        for f in (1.55, 2.0, 2.5, 3.0):
            _, Tp = cm(m, V, f, P0, dA=+0.10)
            _, Tm = cm(m, V, f, P0, dA=-0.10)
            rows.append(dict(f=f,
                             B_roll=(Tp[0] - Tm[0]) / 0.2,
                             B_yaw=(Tp[2] - Tm[2]) / 0.2))
        da[kind] = rows
    out["differential"] = da

    # ── 钩子三：侧滑敏感性 ──
    side = {}
    for kind in ("frozen", "3d"):
        m = fresh(kind)
        rows = []
        for vy in (-2.0, -1.0, 1.0, 2.0):
            F, T = cm(m, V, F0, P0, vy=vy)
            rows.append(dict(vy=vy, Fy=float(F[1]), Tz=float(T[2]),
                             Tx=float(T[0])))
        side[kind] = rows
    out["sideslip"] = side

    with open(os.path.join(cfg.LOG_DIR, "ab_frozen_vs_3d.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    md = [f"# 冻结几何 vs 真三维 对拍报告（{ts}）", "",
          "配置：配平点 V=4.5/f=1.55/p=−0.05，波形 40°/0.25/−0.02/0°；"
          "三维版含翼根偏置 −0.07 m 与扫掠几何；原始数据 `logs/ab_frozen_vs_3d.json`", "",
          "## 钩子一：悬停一致性（对称拍动、零来流；回归门）", "",
          "| 版本 | Fz (N) | Fx (N) | Fy (N) | Ty (N·m) | Tz (N·m) |",
          "|---|---|---|---|---|---|"]
    for k, v in hover.items():
        md.append(f"| {k} | {v['Fz']:+.3f} | {v['Fx']:+.3f} | {v['Fy']:+.3f} |"
                  f" {v['Ty']:+.4f} | {v['Tz']:+.4f} |")
    md += ["", "## 钩子二：差动对拍（拍动直接出滚转/偏航的通道量值）", "",
           "| f (Hz) | 冻结 B_roll | 三维 B_roll | 冻结 B_yaw | 三维 B_yaw |",
           "|---|---|---|---|---|"]
    for rf, r3 in zip(da["frozen"], da["3d"]):
        md.append(f"| {rf['f']:.2f} | {rf['B_roll']:+.4f} | {r3['B_roll']:+.4f} |"
                  f" {rf['B_yaw']:+.4f} | {r3['B_yaw']:+.4f} |")
    md += ["", "## 钩子三：侧滑敏感性（vy=±1/±2 m/s）", "",
           "| vy | 冻结 Fy | 三维 Fy | 冻结 Tz | 三维 Tz |", "|---|---|---|---|---|"]
    for rf, r3 in zip(side["frozen"], side["3d"]):
        md.append(f"| {rf['vy']:+.1f} | {rf['Fy']:+.3f} | {r3['Fy']:+.3f} |"
                  f" {rf['Tz']:+.4f} | {r3['Tz']:+.4f} |")
    md += ["", "判读要点：钩子一 两版竖直力同阶为回归门通过；钩子二 三维与冻结的"
           "差 = 冻结几何吞掉的耦合力矩（B_dA 塌缩的伪影占比按此定量）；"
           "钩子三 侧滑引起的偏航力矩差 = 攻角对侧滑失明的丢失量。", ""]
    with open(os.path.join(PROJECT_ROOT, "docs", "AB_冻结对拍三维.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(md))
    print("[AB] hover:", {k: round(v['Fz'], 3) for k, v in hover.items()})
    print("[AB] B_roll@1.55 frozen/3d:",
          f"{da['frozen'][0]['B_roll']:+.4f} / {da['3d'][0]['B_roll']:+.4f}")
    print("[AB] B_yaw@1.55  frozen/3d:",
          f"{da['frozen'][0]['B_yaw']:+.4f} / {da['3d'][0]['B_yaw']:+.4f}")


if __name__ == "__main__":
    main()
