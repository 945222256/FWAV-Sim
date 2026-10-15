# -*- coding: utf-8 -*-
"""
scan_launch_window.py — 新工作点发射窗口离线分析（第二十五班）

问题：第五步两次在环失败的新根因=发射能量赤字（发射 3.5 m/s 加
21.45° 仰角，扑翼推力零富余，前向速度两秒衰减 52%、升力崩至体重
28%）。本工具对发射速度×发射俯仰做二维开环扫描，画出"能量能撑
住"的安全窗口，替代第三轮试凑（分析优先纪律）。

口径：姿态锁定积分——机体俯仰角锁在发射值（去掉姿态动力学不确定
性，纯测能量收支），速度与位置逐步积分；力=主线三维模型（已恢复
ω×r）逐时间步求值（含尾翼滑流、爬升守卫、线性阻尼），复刻在环
力环境、不含控制器。初始条件与在环发射一致：高度 3 米、速度沿
世界水平轴、机体俯仰=发射俯仰（初始攻角=发射俯仰）。

判据先于数据（2026-09-24 定）：
  通过线：3 秒末前向速度不低于 3.2 m/s（配平速度 3.5 的 91%，
  即能量基本守住）且高度不低于 1.5 m（掉高不超过 1.5 米）。
  窗口输出：全网格的 3 秒末速度、末高、最低高度表。

用法： python tools/scan_launch_window.py
产物： logs/scan_launch_window.json + docs/发射窗口分析.md
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
from modules.aero_solver import euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G_ACC = 9.81
MASS = cfg.MASS
DT = 0.002
T_SIM = 3.0
Z0 = 3.0
F_OP = 1.25

V_GRID = [3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5]
# 同族三解（第二十六班选项一）：幅值 30/35/40 配平俯仰
# +21.45/+21.72/+21.86°（docs/波形参数重搜索_扩域.md 前三名表）
AMP_DEG = 35.0
PITCH_TRIM_DEG = 21.72
for _a in sys.argv[1:]:
    if _a.startswith("--amp"):
        AMP_DEG = float(_a.split("=")[1])
if AMP_DEG == 30.0:
    PITCH_TRIM_DEG = 21.45
elif AMP_DEG == 40.0:
    PITCH_TRIM_DEG = 21.86
TH_GRID = [0.0, 5.0, 10.0, 15.0, PITCH_TRIM_DEG, 25.0]

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(35.0)
    w.split = 0.75
    w.pitch_lag_s = -0.04
    w.mean = math.radians(20.0)
    w.amplitude = math.radians(AMP_DEG)
model.set_wing_command(frequency=F_OP)

T0 = [220000.0]


def run_cell(v0, th_deg):
    """姿态锁定 3 秒积分。返回末速/末高/最低高/最大掉速。"""
    quat = euler_to_quat(0.0, math.radians(th_deg), 0.0)
    vel = np.array([v0, 0.0, 0.0])
    pos = np.array([0.0, 0.0, Z0])
    n = int(round(T_SIM / DT))
    z_min = Z0
    for i in range(n):
        t = T0[0]
        F, _ = model.compute_forces(pos, quat, vel, np.zeros(3), t)
        F = F + np.array([0.0, 0.0, -MASS * G_ACC])
        vel = vel + F / MASS * DT
        pos = pos + vel * DT
        T0[0] += DT
        z_min = min(z_min, pos[2])
    return dict(v_end=float(vel[0]), z_end=float(pos[2]),
                z_min=float(z_min))


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = []
    print(f"[lw] 网格 {len(V_GRID)}×{len(TH_GRID)}，姿态锁定 "
          f"{T_SIM}s，dt={DT*1000:g}ms", flush=True)
    for v0 in V_GRID:
        for th in TH_GRID:
            r = run_cell(v0, th)
            diverged = abs(r["v_end"]) > 20.0 or abs(r["z_end"]) > 60.0
            ok = (not diverged) and (r["v_end"] >= 3.2) \
                and (r["z_end"] >= 1.5)
            r.update(v0=v0, th=th, ok=ok)
            rows.append(r)
            print(f"[lw] v0={v0:.1f} θ0={th:5.2f}° → 末速="
                  f"{r['v_end']:+.2f} 末高={r['z_end']:.2f} "
                  f"最低={r['z_min']:.2f} "
                  f"{'窗口内' if ok else ('数值发散' if diverged else '窗外')}", flush=True)
    n_ok = sum(r["ok"] for r in rows)
    print(f"[lw] 窗口内 {n_ok}/{len(rows)} 格", flush=True)

    out = dict(ts=ts, dt=DT, t_sim=T_SIM, z0=Z0, f=F_OP,
               criteria=dict(v_end_min=3.2, z_end_min=1.5),
               rows=rows, n_ok=n_ok)
    with open(os.path.join(cfg.LOG_DIR, "scan_launch_window_amp%d.json" % int(AMP_DEG)),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    md = [f"# 发射窗口分析·新工作点能量收支（{ts}）", "",
          f"- 口径：姿态锁定开环积分 {T_SIM} s（机体俯仰锁发射值，"
          f"纯能量收支），力=三维模型逐时间步（含滑流/守卫/阻尼，"
          f"已恢复 ω×r），初始高度 3 m、速度水平、攻角=发射俯仰",
          f"- 判据先于数据：3 秒末前向速度≥3.2 m/s 且末高度≥1.5 m",
          f"- 原始数据：`logs/scan_launch_window.json`", "",
          "| 发射速度 | 发射俯仰 | 3秒末速度 | 末高度 | 最低高度 |"
          " 判定 |", "|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['v0']:.1f} m/s | {r['th']:.2f}° |"
                  f" {r['v_end']:+.2f} | {r['z_end']:.2f} |"
                  f" {r['z_min']:.2f} |"
                  f" {'窗口内' if r['ok'] else '窗外'} |")
    md += ["", f"- 窗口统计：{n_ok}/{len(rows)} 格在窗内。", ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", "发射窗口分析_幅值%d.md" % int(AMP_DEG))
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[lw] 完成 → docs/发射窗口分析_幅值%d.md" % int(AMP_DEG), flush=True)


if __name__ == "__main__":
    main()
