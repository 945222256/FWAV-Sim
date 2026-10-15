# -*- coding: utf-8 -*-
"""
quantum_remeasure_current.py — 单拍操纵量子·按现行生效波形重测
（不定频率完全差动线·阶段一前置，2026-09-27）

判据来源：docs/不定频率完全差动_判据与方案.md 判据甲一、甲二
（先于数据入库，运行后不改）。

重测动因：旧表 logs/flap_quantum_grid.json（2026-09-24）的测量波形
为攻角设定 35.0°，根源是旧工具 measure_flap_quantum_grid.py 第 45 行
硬编码 ALPHA_SET=35.0；现行生效波形（提交 39cfb9a）为 α=32.3169°、
mean=19.8105°、split=0.176、lag=−0.0674s、amp=17.73°、f=1.80Hz。
本工具全部波形参数读 config，禁止硬编码。

测量纪律（三条，沿用第十六班定案，第五条扩展）：
  1. 时间轴全局单调推进（相位是积分状态，不重置不回跳）。
  2. 显式设频：每次求值前显式 set_wing_command(frequency=...)。
  3. 分辨率分级：配平粗扫 n=72，终值与量子 n=288。

网格设计（自定、可调）：速度 3.5/3.95/4.5 m/s，拍频 1.25/1.55/1.80/
2.00/2.20 Hz。上界 2.20 Hz 为失效区边界（归因测量候选二）；工作点
3.95 m/s × 1.80 Hz 为第六十八班升格生效值。

用法：
  python tools/quantum_remeasure_current.py            # 全网格
  python tools/quantum_remeasure_current.py --point 3.95 1.80   # 单点冒烟
产物：logs/flap_quantum_grid_current.json +
      docs/单拍量子_重测_现行波形.md
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
from modules.aero_solver import TailFuselageAero, euler_to_quat, \
    quat_to_rotation_matrix
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G = 9.81
W = cfg.MASS * G
VERT_TOL = 0.02          # 判据甲二沿用旧表容差
IXX_APPROX = 2.4e-3      # 与旧表同口径的近似滚转惯量
V_GRID = [3.5, 3.95, 4.5]
F_GRID = [1.25, 1.55, 1.80, 2.00, 2.20]
N_COARSE, N_FINE = 72, 288

# 全部波形参数读配置（纪律：禁止硬编码，旧表漂移教训）
# 注：config 的 ALPHA_SET 单位为度（WingKinematics 构造器内自行 radians
# 换算）；直写 wing.alpha_set 属性时必须先换算弧度。
ALPHA_SET = float(cfg.FlapKinematicsConfig.ALPHA_SET)

model = SaneDickinson3DModel()
tail = TailFuselageAero()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(cfg.FlapKinematicsConfig.ALPHA_SET)
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


def density_linearity_check(V, f, pd_deg, beats=4, n=N_FINE):
    """密度缩放线性断言：4 拍窗口内停 1 拍与停 2 拍的冲量差之比应为 2。

    依据：同频定常态下每拍波形逐位相同，密度 ρ 的冲量应严格线性于
    停拍数。若线性成立，密度通道量子 = 单拍量子 × 停拍数，判据甲三的
    密度全额（ρ: 0 对 1）即为单拍量子本身，无需逐密度重测。
    容差 1%（自定、可调）。
    """
    model.set_wing_command(frequency=f)
    quat = euler_to_quat(0.0, math.radians(pd_deg), 0.0)
    v_body = quat_to_rotation_matrix(quat).T @ np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    T = 1.0 / f
    dt = T / n
    w, rc = model.wing_left, +model._r_centers.copy()

    def window_impulse(pause_beats):
        """4 拍窗口的总冲量：前 pause_beats 拍打钉中立位，其余拍正常。"""
        totF = np.zeros(3)
        totT = np.zeros(3)
        for b in range(beats):
            for t in phase_window(n, f):
                ws = w.evaluate(t, phase_offset_rad=0.0)
                if b < pause_beats:
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
                totF += Fw * dt
                totT += Tw * dt
        return totF, totT

    baseF, baseT = window_impulse(0)
    _, tot1 = window_impulse(1)
    _, tot2 = window_impulse(2)
    # 停 k 拍的量子 = 全拍窗口冲量 − 停 k 拍窗口冲量；线性则 q2 = 2·q1
    s1 = float(baseT[0] - tot1[0])
    s2 = float(baseT[0] - tot2[0])
    ratio = (s2 / s1) if abs(s1) > 1e-12 else float("nan")
    return {"stop1_dJ_Tx": s1, "stop2_dJ_Tx": s2,
            "ratio": float(ratio), "pass": abs(ratio - 2.0) <= 0.02}


def run_point(V, f):
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
        row["domega_roll_dps"] = math.degrees(q["L"]["dJ_T"][0] / IXX_APPROX)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--point", nargs=2, type=float, metavar=("V", "F"),
                    help="只跑单个格点（冒烟用）")
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "model": "sane_dickinson_3d",
           "waveform_source": "config.FlapKinematicsConfig（禁止硬编码）",
           "alpha_set": ALPHA_SET,
           "mean": cfg.FlapKinematicsConfig.MEAN,
           "split": cfg.FlapKinematicsConfig.SPLIT,
           "pitch_lag_s": cfg.FlapKinematicsConfig.PITCH_LAG_S,
           "amplitude": cfg.FlapKinematicsConfig.AMPLITUDE,
           "vert_tol": VERT_TOL, "n_coarse": N_COARSE, "n_fine": N_FINE,
           "V_grid": V_GRID, "F_GRID": F_GRID}
    print(f"[remeasure] 波形读自配置：α={ALPHA_SET:.4f}° "
          f"mean={cfg.FlapKinematicsConfig.MEAN:.4f}° "
          f"split={cfg.FlapKinematicsConfig.SPLIT:.4f} "
          f"lag={cfg.FlapKinematicsConfig.PITCH_LAG_S:.4f}s "
          f"amp={cfg.FlapKinematicsConfig.AMPLITUDE:.2f}°", flush=True)

    # ── 判据甲一：相位收敛检查（巡航工作点 3.95 m/s × 1.80 Hz）──
    th0, F0, _ = solve_trim_pitch(3.95, 1.80)
    conv = {}
    for n in (96, 192, 288, 576):
        q = wing_quantum(3.95, 1.80, th0, n=n)
        conv[str(n)] = q["L"]["dJ_T"][0]
    rel = {k: abs(v - conv["576"]) / max(abs(conv["576"]), 1e-12)
           for k, v in conv.items()}
    out["phase_convergence"] = {"trim_pitch": th0,
                                "dJ_Tx_L_by_n": conv, "rel_vs_576": rel,
                                "criterion_A1": "rel(288)<=1%",
                                "A1_pass": rel["288"] <= 0.01}
    print(f"[remeasure] 判据甲一：θ={th0:+.2f}°，288 对 576 差 "
          f"{rel['288']:.2%}（判据线 1%，"
          f"{'通过' if rel['288'] <= 0.01 else '不通过'}）", flush=True)

    # ── 密度线性断言（巡航工作点）──
    dens = density_linearity_check(3.95, 1.80, th0)
    out["density_linearity"] = dens
    print(f"[remeasure] 密度线性断言：停1拍 {dens['stop1_dJ_Tx']:+.5f} "
          f"对 停2拍 {dens['stop2_dJ_Tx']:+.5f}，比值 {dens['ratio']:.4f}"
          f"（应=2，{'通过' if dens['pass'] else '不通过'}）", flush=True)

    # ── 包线网格 ──
    grid = [(args.point[0], args.point[1])] if args.point else \
        [(V, f) for V in V_GRID for f in F_GRID]
    rows_out = []
    for V, f in grid:
        row = run_point(V, f)
        rows_out.append(row)
        flag = "可配平" if row["trimmable"] else "不可配平"
        extra = (f" ΔJ_Tx={row['dJ_Tx_L']:+.5f}"
                 f"（{row['domega_roll_dps']:+.1f}°/s）"
                 if row["trimmable"] else "")
        print(f"[remeasure] V={V:.2f} f={f:.2f} θ={row['pitch']:+.1f}° "
              f"残差={row['vert_resid']*100:.1f}% {flag}{extra}", flush=True)
    out["rows"] = rows_out

    # ── 判据甲二：可行域存在性 ──
    n_ok = sum(1 for r in rows_out if r["trimmable"])
    out["criterion_A2"] = {"trimmable_count": n_ok,
                           "pass": (n_ok >= 1) if not args.point else None}
    print(f"[remeasure] 判据甲二：可配平格点 {n_ok}/{len(rows_out)}",
          flush=True)

    with open(os.path.join(cfg.LOG_DIR, "flap_quantum_grid_current.json"),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    md = [f"# 单拍操纵量子·按现行生效波形重测（{ts}）", "",
          "- 判据来源：《不定频率完全差动_判据与方案.md》判据甲一、甲二"
          "（先于数据入库，运行后不改）",
          f"- 波形读自 config.FlapKinematicsConfig：α={ALPHA_SET:.4f}°、"
          f"mean={cfg.FlapKinematicsConfig.MEAN:.4f}°、"
          f"split={cfg.FlapKinematicsConfig.SPLIT:.4f}、"
          f"lag={cfg.FlapKinematicsConfig.PITCH_LAG_S:.4f}s、"
          f"amp={cfg.FlapKinematicsConfig.AMPLITUDE:.2f}°",
          "- 模型：sane_dickinson_3d；测量纪律三条沿用第十六班定案",
          "- 原始数据：`logs/flap_quantum_grid_current.json`", "",
          "## 判据甲一：相位收敛（巡航工作点 3.95 m/s × 1.80 Hz）", "",
          f"- 配平复核：θ={th0:+.2f}°，Fz={float(F0[2]):.3f} N",
          f"- 判定：288 对 576 差 {rel['288']:.2%}，判据线 1%，"
          f"{'通过' if rel['288'] <= 0.01 else '不通过'}", "",
          "| 相位点数 | ΔJ_Tx 左翼 (N·m·s) | 相对 576 点差 |",
          "|---|---|---|"]
    for k in ("96", "192", "288", "576"):
        rel_s = "—" if k == "576" else f"{rel[k]:.2%}"
        md.append(f"| {k} | {conv[k]:+.5f} | {rel_s} |")
    md += ["", "## 密度缩放线性断言（判据甲三的密度通道前置）", "",
           f"- 停 1 拍量子 {dens['stop1_dJ_Tx']:+.5f} N·m·s，停 2 拍 "
           f"{dens['stop2_dJ_Tx']:+.5f} N·m·s，比值 {dens['ratio']:.4f}"
           f"（应=2，容差 1%，{'通过' if dens['pass'] else '不通过'}）",
           "- 含义：线性成立则密度通道量子 = 单拍量子 × 停拍数，"
           "密度全额（0 对 1）即单拍量子本身，无需逐密度重测", "",
           "## 包线网格结果（左翼量子；右翼镜像）", "",
           "| V (m/s) | f (Hz) | 配平俯仰 | 竖直残差 | ΔJ_Tx (N·m·s) |"
           " ΔJ_Fz (N·s) | 折算滚转 (°/s) | 判定 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows_out:
        if r["trimmable"]:
            md.append(f"| {r['V']:.2f} | {r['f']:.2f} |"
                      f" {r['pitch']:+.1f}° | {r['vert_resid']*100:.1f}% |"
                      f" {r['dJ_Tx_L']:+.5f} | {r['dJ_Fz_L']:+.4f} |"
                      f" {r['domega_roll_dps']:+.1f} | 可配平 |")
        else:
            md.append(f"| {r['V']:.2f} | {r['f']:.2f} |"
                      f" {r['pitch']:+.1f}° | {r['vert_resid']*100:.1f}% |"
                      f" — | — | — | 不可配平 |")
    md += ["",
           f"## 判据甲二：可行域存在性——可配平格点 {n_ok}/"
           f"{len(rows_out)}（判据线 ≥1）", "",
           "## 与旧表（2026-09-24，α=35.0° 口径）对照登记", "",
           "- 旧表唯一可配平格点：V=4.0、f=1.75，ΔJ_Tx=−0.02238，"
           "折算 −534.4°/s；本表为现行波形口径，两表数字差异属预期，"
           "引用旧表权限数字须带口径注记。", ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", "单拍量子_重测_现行波形.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[remeasure] 完成 → docs/单拍量子_重测_现行波形.md", flush=True)


if __name__ == "__main__":
    main()
