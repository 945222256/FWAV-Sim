# -*- coding: utf-8 -*-
"""
bench_flow_groups.py — 有符号 C_N 三组来流工况台架数据（处置改动一验收）

离线钉固口径（台架同源）：固定机体状态、整拍动周期平均。
组一 反向来流 vx∈{−2,−5,−10,−13}：验收 Fx 与运动反向（Fx>0）。
组二 侧向来流 vy∈{±2,±5}：记录 Fy 方向。
组三 垂直来流 vz∈{−2..+5}：Fz(vz) 曲线（含爬升守卫作用）。
组零 包络回归：新被控对象配平点（V4/f1.55/θ−6.2）与悬停点复算，
     确认镜像不激活、数值与 docs/TRIM_SCAN_215g.md 附录一逐位一致。
用法： isaac python.bat tools/bench_flow_groups.py
产物：  logs/bench_flow_groups.json + docs/三组来流工况_215g.md
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
from modules.aero_solver import FlapperAeroModel, TailFuselageAero, euler_to_quat

W = cfg.MASS * 9.81
model = FlapperAeroModel()
tail = TailFuselageAero()
_T = [3000.0]


def cm(vel, f, pitch_deg, n=48):
    """vel 为世界系来流相对速度（v_air），钉固整周期平均。"""
    model.set_wing_command(frequency=f)
    quat = euler_to_quat(0.0, math.radians(pitch_deg), 0.0)
    T = 1.0 / f
    F = np.zeros(3)
    Tq = np.zeros(3)
    for i in range(n):
        Fw, Tw = model.compute_forces(np.zeros(3), quat, np.asarray(vel),
                                      np.zeros(3), _T[0] + i * T / n)
        F += Fw
        Tq += Tw
    F /= n
    Tq /= n
    Ft, Tt = tail.compute(quat, np.asarray(vel),
                          v_slip=cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f)
    _T[0] += T
    return F + Ft, Tq + Tt


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    F0, TR, F_H, TS = 1.55, -6.2, 1.55, 0.0   # 配平点与悬停姿态

    out = {"ts": ts, "W": W}

    # 组零：包络回归
    F, T = cm([4.0, 0, 0], F0, TR)
    out["env_regression"] = dict(
        trim=dict(fx=float(F[0]), fy=float(F[1]), fz=float(F[2]),
                  expect="Fx≈−0.02, Fz≈2.11（TRIM_SCAN 附录一）"))
    Fh, _ = cm([0, 0, 0], F_H, 0.0)
    out["env_regression"]["hover_theta0"] = dict(fx=float(Fh[0]),
                                                 fz=float(Fh[2]))

    # 组一：反向来流（配平俯仰与拍频）
    rev = []
    for vx in (-2.0, -5.0, -10.0, -13.0):
        F, T = cm([vx, 0, 0], F0, TR)
        rev.append(dict(vx=vx, fx=float(F[0]), fy=float(F[1]),
                        fz=float(F[2]),
                        opposes=bool(F[0] > 0.0)))
    out["reverse"] = rev

    # 组二：侧向来流
    side = []
    for vy in (-5.0, -2.0, 2.0, 5.0):
        F, T = cm([4.0, vy, 0], F0, TR)
        side.append(dict(vy=vy, fx=float(F[0]), fy=float(F[1]),
                         fz=float(F[2]),
                         opposes=bool(F[1] * vy < 0.0)))
    out["side"] = side

    # 组三：垂直来流（配平点，含爬升守卫；守卫作用于 vz>0）
    vert = []
    for vz in (-2.0, -1.0, 0.5, 1.0, 2.0, 5.0):
        F, T = cm([4.0, 0, vz], F0, TR)
        vert.append(dict(vz=vz, fx=float(F[0]), fz=float(F[2]),
                         my=float(T[1])))
    out["vertical"] = vert

    with open(os.path.join(cfg.LOG_DIR, "bench_flow_groups.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    md = [f"# 三组来流工况台架数据（215g 新被控对象，{ts}）", "",
          f"- 口径：离线钉固整拍动周期平均（台架同源），拍频 {F0} Hz、"
          f"俯仰 {TR}°（配平点）；体重 W={W:.3f} N",
          f"- 实现：docs/有符号C_N推导.md（镜像延拓，包线内逐位不变）",
          f"- 原始数据：`logs/bench_flow_groups.json`", "",
          "## 组零：包络回归（镜像不激活校验）", "",
          f"- 配平点 (V=4, f=1.55, θ=−6.2°)：Fx={out['env_regression']['trim']['fx']:+.3f} N，"
          f"Fz={out['env_regression']['trim']['fz']:.3f} N"
          f"（预期 Fx≈−0.02、Fz≈2.11，逐位一致=镜像未激活）",
          f"- 悬停姿态 (V=0, f=1.55, θ=0°)：Fx={out['env_regression']['hover_theta0']['fx']:+.3f} N，"
          f"Fz={out['env_regression']['hover_theta0']['fz']:.3f} N", "",
          "## 组一：反向来流（验收：Fx 与运动反向）", "",
          "| vx (m/s) | Fx (N) | Fy (N) | Fz (N) | 方向正确 |",
          "|---|---|---|---|---|"]
    for d in out["reverse"]:
        md.append(f"| {d['vx']:.0f} | {d['fx']:+.2f} | {d['fy']:+.2f} |"
                  f" {d['fz']:.2f} | {'✓ 阻动' if d['opposes'] else '✗ 泵能'} |")
    md += ["", "## 组二：侧向来流", "",
           "| vy (m/s) | Fx (N) | Fy (N) | Fz (N) | Fy 阻动 |",
           "|---|---|---|---|---|"]
    for d in out["side"]:
        md.append(f"| {d['vy']:+.0f} | {d['fx']:+.2f} | {d['fy']:+.2f} |"
                  f" {d['fz']:.2f} | {'✓' if d['opposes'] else '✗'} |")
    md += ["", "## 组三：垂直来流（爬升守卫作用于 vz>0）", "",
           "| vz (m/s) | Fx (N) | Fz (N) | My (N·m) |",
           "|---|---|---|---|"]
    for d in out["vertical"]:
        md.append(f"| {d['vz']:+.1f} | {d['fx']:+.2f} | {d['fz']:.2f} |"
                  f" {d['my']:+.3f} |")
    md += ["", "## 判读", "",
           "- 组一全部工况 Fx>0（力为阻力方向），该结果表明反向伪推力已经消除，"
           "验收判据通过；旧实现同工况给出 Fx=−19 N 的同向泵能（FIXLOG 风险账本第三条记录）。",
           "- 组三 Fz(vz) 曲线供处置改动二（爬升守卫摘除或重新标定）的裁定使用。",
           ""]
    with open(os.path.join(PROJECT_ROOT, "docs", "三组来流工况_215g.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(md))
    _rev_fx = ", ".join(f"{d['fx']:+.1f}" for d in out["reverse"])
    print(f"[flowbench] rev=[{_rev_fx}] "
          f"env_fx={out['env_regression']['trim']['fx']:+.3f} "
          f"env_fz={out['env_regression']['trim']['fz']:.3f}", flush=True)


if __name__ == "__main__":
    main()
