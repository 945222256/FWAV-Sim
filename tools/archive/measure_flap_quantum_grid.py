# -*- coding: utf-8 -*-
"""
measure_flap_quantum_grid.py — 单拍操纵量子·包线网格版（第十六班·重写）

在第十五班单点测量基础上扩展到包线网格：速度 3.5~5.0 m/s × 拍频
1.25~3.0 Hz。逐格点先配平俯仰（Fx=0，双阱稳健求解器），再测每翼
单拍操纵量子（扑动一次 对 保持固定 的冲量差）。停扑建模：翼面固定于
中立位置、攻角保持扑动程序周期均值（α_set·2/π）、不再随相位扭转
（第十六班排障修正：初版只清零幅度，俯仰程序仍在运行，不符停扑物理）。

测量纪律（第十六班排障后确立，三条）：
  1. 时间轴全局单调推进：翼运动学的拍动相位是积分状态
     （_phase_sc，2026-09-20 相位积分化修复），任何离线测量不得
     重置或回跳时间轴；本工具所有求值共享同一推进时基。
  2. 显式设频：每次求值前显式 set_wing_command(frequency=...)，
     不依赖环境残留频率。
  3. 分辨率分级：配平粗扫 n=72，终值与量子 n=288。原因：前向力
     Fx 是推力/阻力近对消量，n=36 矩形求积误差约 ±0.02 N，与
     配平残差同量级（probe_grid_debug 实测）；竖直力与力矩冲量
     对分辨率稳健。

附带相位网格收敛检查：巡航配平点用 96/192/288/576 重测操纵量子。

用法： python tools/measure_flap_quantum_grid.py（纯 NumPy）
产物： logs/flap_quantum_grid.json + docs/单拍量子_包线网格.md
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
from modules.aero_solver import TailFuselageAero, euler_to_quat, \
    quat_to_rotation_matrix
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G = 9.81
W = cfg.MASS * G
ALPHA_SET = 35.0
VERT_TOL = 0.02
IXX_APPROX = 2.4e-3
V_GRID = [3.5, 4.0, 4.5, 5.0]
F_GRID = [1.25, 1.50, 1.75, 2.00, 2.50, 3.00]
N_COARSE, N_FINE = 72, 288

model = SaneDickinson3DModel()
tail = TailFuselageAero()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)
    w.amplitude = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)

TIMEBASE = [100000.0]   # 全局单调推进时基（纪律一）


def phase_window(n, f):
    """从全局时基取一个完整扑动周期的 n 个采样时刻，推进时基。"""
    T = 1.0 / f
    ts = [TIMEBASE[0] + i * T / n for i in range(n)]
    TIMEBASE[0] += T
    return ts


def total_cm(V, f, pd_deg, n):
    """全机（双翼＋尾翼滑流）钉固状态周期平均力（纪律二：显式设频）。"""
    model.set_wing_command(frequency=f)
    quat = euler_to_quat(0.0, math.radians(pd_deg), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    F = np.zeros(3)
    for t in phase_window(n, f):
        Fw, _ = model.compute_forces(np.zeros(3), quat, vel, om, t)
        F += Fw
    F /= n
    Ft, _ = tail.compute(quat, vel,
                         v_slip=cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f)
    return F + Ft


def solve_trim_pitch(V, f):
    """粗扫（n=72）变号区间＋二分细化，终值 n=288 复核。
    多根取竖直残差最小者。返回 (θ, F_trim, 是否找到过零根)。"""
    grid = np.arange(-20.0, 25.001, 1.5)
    rows = []
    for th in grid:
        F = total_cm(V, f, float(th), N_COARSE)
        rows.append((float(th), float(F[0])))
    brackets = [(a[0], b[0]) for a, b in zip(rows[:-1], rows[1:])
                if a[1] == 0.0 or a[1] * b[1] < 0]
    if not brackets:
        th = min(rows, key=lambda r: abs(r[1]))[0]
        return th, total_cm(V, f, th, N_FINE), False
    best = None
    for a, b in brackets:
        fa = total_cm(V, f, a, N_COARSE)[0]
        for _ in range(14):
            mid = 0.5 * (a + b)
            fm = total_cm(V, f, mid, N_COARSE)[0]
            if fa * fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
        th = 0.5 * (a + b)
        F = total_cm(V, f, th, N_FINE)
        score = abs(float(F[2]) - W)
        if best is None or score < best[0]:
            best = (score, th, F)
    _, th, F = best
    return th, F, True


def wing_quantum(V, f, pd_deg, n=N_FINE):
    """在配平姿态下测每翼单拍操纵量子（扑动一次 对 保持固定）。"""
    model.set_wing_command(frequency=f)
    quat = euler_to_quat(0.0, math.radians(pd_deg), 0.0)
    v_body = quat_to_rotation_matrix(quat).T @ np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    T = 1.0 / f
    dt = T / n
    out = {}
    for side in ("L", "R"):
        if side == "L":
            w, rc, po = model.wing_left, +model._r_centers.copy(), 0.0
        else:
            w, rc, po = model.wing_right, -model._r_centers.copy(), \
                model.phase_offset_lr
        dF = np.zeros(3)
        dT = np.zeros(3)
        for paused in (False, True):
            Fs = np.zeros((n, 3))
            Ts = np.zeros((n, 3))
            for i, t in enumerate(phase_window(n, f)):
                ws = w.evaluate(t, phase_offset_rad=po)
                if paused:
                    # 停扑建模：翼面固定于中立位置、攻角保持扑动程序的
                    # 周期均值（α_set·2/π），不再随相位扭转；相位状态
                    # 照常推进以保持时间轴连续
                    ws = dict(ws)
                    ws["phi"] = float(w.bias + w.mean)
                    ws["phi_dot"] = 0.0
                    ws["phi_ddot"] = 0.0
                    ws["alpha"] = float(w.alpha_set * 2.0 / math.pi
                                        + w.pitch_bias)
                    ws["alpha_dot"] = 0.0
                    ws["alpha_ddot"] = 0.0
                Fw, Tw = model._wing_forces(rc, model._chords, ws,
                                            v_body, om)
                Fs[i], Ts[i] = Fw, Tw
            if not paused:
                dF += np.sum(Fs, axis=0) * dt
                dT += np.sum(Ts, axis=0) * dt
            else:
                dF -= np.sum(Fs, axis=0) * dt
                dT -= np.sum(Ts, axis=0) * dt
        out[side] = {"dJ_F": dF.tolist(), "dJ_T": dT.tolist()}
    return out


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "model": "sane_dickinson_3d",
           "alpha_set": ALPHA_SET, "vert_tol": VERT_TOL,
           "n_coarse": N_COARSE, "n_fine": N_FINE,
           "V_grid": V_GRID, "F_GRID": F_GRID}

    # ── 相位网格收敛检查（巡航配平点；先按纪律一二定频定时基）──
    th0, F0, _ = solve_trim_pitch(4.0, 1.75)
    conv = {}
    for n in (96, 192, 288, 576):
        q = wing_quantum(4.0, 1.75, th0, n=n)
        conv[str(n)] = q["L"]["dJ_T"][0]
    rel = {k: abs(v - conv["576"]) / max(abs(conv["576"]), 1e-12)
           for k, v in conv.items()}
    out["phase_convergence"] = {"trim_pitch": th0,
                                "dJ_Tx_L_by_n": conv, "rel_vs_576": rel}
    print(f"[grid] 巡航配平点复核：θ={th0:+.2f}°，Fz={float(F0[2]):.3f}；"
          f"相位收敛 ΔJ_Tx(L)：96/192/288/576="
          f"{conv['96']:+.5f}/{conv['192']:+.5f}/{conv['288']:+.5f}/"
          f"{conv['576']:+.5f}（288 对 576 差 {rel['288']:.2%}）", flush=True)

    # ── 包线网格 ──
    rows_out = []
    for V in V_GRID:
        for f in F_GRID:
            th, F, rooted = solve_trim_pitch(V, f)
            resid = abs(float(F[2]) - W) / W
            ok = rooted and resid <= VERT_TOL
            row = {"V": V, "f": f, "pitch": th, "fx": float(F[0]),
                   "fz": float(F[2]), "vert_resid": resid,
                   "rooted": rooted, "trimmable": ok}
            if ok:
                q = wing_quantum(V, f, th)
                row["dJ_Tx_L"] = q["L"]["dJ_T"][0]
                row["dJ_Ty_L"] = q["L"]["dJ_T"][1]
                row["dJ_Tz_L"] = q["L"]["dJ_T"][2]
                row["dJ_Fz_L"] = q["L"]["dJ_F"][2]
                row["domega_roll_dps"] = math.degrees(
                    q["L"]["dJ_T"][0] / IXX_APPROX)
            rows_out.append(row)
            flag = "可配平" if ok else "不可配平"
            extra = (f" ΔJ_Tx={row['dJ_Tx_L']:+.5f}"
                     f"（{row['domega_roll_dps']:+.1f}°/s）"
                     if ok else "")
            print(f"[grid] V={V:.1f} f={f:.2f} θ={th:+.1f}° "
                  f"残差={resid*100:.1f}% {flag}{extra}", flush=True)
    out["rows"] = rows_out

    with open(os.path.join(cfg.LOG_DIR, "flap_quantum_grid.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    md = [f"# 单拍操纵量子·包线网格（{ts}）", "",
          f"- 模型：sane_dickinson_3d；波形：攻角设定 {ALPHA_SET}°，"
          "其余通道配置现行值；每格点先配平俯仰（Fx=0）再测量",
          f"- 测量纪律：时间轴全局单调推进、显式设频、粗扫 n={N_COARSE}"
          f"／终值 n={N_FINE}（第十六班排障后确立，见文末记档）",
          "- 原始数据：`logs/flap_quantum_grid.json`", "",
          "## 相位网格收敛检查（巡航配平点）", "",
          f"- 配平复核：θ={th0:+.2f}°，Fz={float(F0[2]):.3f} N", "",
          "| 相位点数 | ΔJ_Tx 左翼 (N·m·s) | 相对 576 点差 |",
          "|---|---|---|"]
    for k in ("96", "192", "288", "576"):
        rel_s = "—" if k == "576" else f"{rel[k]:.2%}"
        md.append(f"| {k} | {conv[k]:+.5f} | {rel_s} |")
    md += ["", "## 包线网格结果（左翼量子；右翼镜像）", "",
           "| V (m/s) | f (Hz) | 配平俯仰 | 竖直残差 | ΔJ_Tx (N·m·s) |"
           " ΔJ_Fz (N·s) | 折算滚转 (°/s) | 判定 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows_out:
        if r["trimmable"]:
            md.append(f"| {r['V']:.1f} | {r['f']:.2f} |"
                      f" {r['pitch']:+.1f}° | {r['vert_resid']*100:.1f}% |"
                      f" {r['dJ_Tx_L']:+.5f} | {r['dJ_Fz_L']:+.4f} |"
                      f" {r['domega_roll_dps']:+.1f} | 可配平 |")
        else:
            md.append(f"| {r['V']:.1f} | {r['f']:.2f} |"
                      f" {r['pitch']:+.1f}° | {r['vert_resid']*100:.1f}% |"
                      f" — | — | — | 不可配平 |")
    md += ["", "## 测量精度记档（第十六班排障结论）", "",
           "- 前向力 Fx 是推力与阻力近对消的小量：n=36 矩形求积误差"
           "约 ±0.02 N，与配平残差同量级，是此前三个工具俯仰根互异"
           "的来源；本班起粗扫 n=72、终值 n=288。",
           "- 翼运动学的拍动相位是积分状态（_phase_sc），时间轴必须"
           "单调推进：离线工具重置或回跳时间轴会污染相位状态——初版"
           "网格工具的'全网格不可配平'即由此产生。",
           "- 第十三班修正版与第十四班的竖直力／力矩类数字对分辨率"
           "稳健（Fz 变差约 0.1%），俯仰根位置存在数度级不确定性；"
           "如需引用俯仰角绝对值，建议以高分辨率复跑为准。",
           "- 遗留：网格只覆盖巡航邻域；停扑建模为翼面固定中立位置、"
           "攻角保持扑动程序周期均值（α_set·2/π）的滑翔状态；"
           "偏航折算缺偏航惯量真值未列。", ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", "单拍量子_包线网格.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[grid] 完成 → docs/单拍量子_包线网格.md", flush=True)


if __name__ == "__main__":
    main()
