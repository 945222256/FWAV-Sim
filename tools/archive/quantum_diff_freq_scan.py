# -*- coding: utf-8 -*-
"""
quantum_diff_freq_scan.py — 异频与频率差动量子扫描（修正版）
（不定频率完全差动线·阶段一主体，2026-09-27）

判据来源：docs/不定频率完全差动_判据与方案.md 判据甲三、甲四
（先于数据入库，运行后不改）。

测量对象：logs/flap_quantum_grid_current.json 中全部可配平格点
（现行生效波形口径，由 quantum_remeasure_current.py 产出）。

修正记档（先查代码与逻辑纪律）：初版窗口取 4 个基准拍，异频时两翼
在窗口内拍数非整数，截断残差与被测量同量级（初版 Δf=0.2 档反号即
此所致）。本版改为两条独立路径互核：
  路径一（解析合成，无截断）：每翼在自身频率的整数拍窗口内测每拍
  冲量 q(f)，差动滚转力矩均值 = q_L(f_L)·f_L + q_R(f_R)·f_R，减
  去基准（两翼同 f0）后按基准拍长折算每拍值。依据：本模型双翼气动
  不直接耦合（滑流只进尾翼，尾翼对称不产滚转），叠加成立。
  路径二（直接窗口，交叉核验）：窗口取两翼公共整数拍 N（由
  lcm_beats 搜索），两翼同窗直接结算。路径一与路径二相对差超过
  5%（自定、可调）时该行标记不一致并登记。
单翼频率扫描同样按自身整数拍开窗（编号 43/44 的偏导函数原料）。

判据甲三：巡航工作点（3.95 m/s × 1.80 Hz）Δf=0.5 档每拍滚转角速度
增量 ≥ 172°/s（发散每拍增速 86°/s × 裕度 2，倍数自定可调）。
判据甲四：一阶预测 sign(ΔJ_Tx) = sign(f_L − f_R)（依据首日方向标定：
左翼多产出对应正滚转）。允许结论为预测不成立，须逐点登记。

用法： python tools/quantum_diff_freq_scan.py
产物： logs/quantum_diff_freq_scan.json + docs/异频量子_频率差动扫描.md
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
from modules.aero_solver import euler_to_quat, quat_to_rotation_matrix
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G = 9.81
IXX_APPROX = 2.4e-3          # 与量子表同口径的近似滚转惯量
N_PER_BEAT = 288             # 纪律五：每拍 288 点
N_BEATS = 4                  # 单翼窗口拍数（自定、可调）
DF_GRID = [0.1, 0.2, 0.5]    # 频率差考核档（判据乙三沿用）
F_FAILSAFE = 2.2             # 失效区边界（约束一，归因测量候选二）
DIVERGENCE_DPS = 86.0        # 横向发散每拍增速实测（第七十一班）
MARGIN = 2.0                 # 权限裕度倍数（判据甲三，自定、可调）
XCHECK_TOL = 0.05            # 两路径一致性容差（自定、可调）

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(cfg.FlapKinematicsConfig.ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)
    w.amplitude = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)

TIMEBASE = [200000.0]   # 全局单调推进时基（与重测工具区段隔离）


def window_times_fc(fc, n_beats):
    """取 n_beats 个 fc 拍的采样时刻，每拍 288 点，推进时基。"""
    T = n_beats / fc
    n = N_PER_BEAT * n_beats
    ts = [TIMEBASE[0] + i * T / n for i in range(n)]
    TIMEBASE[0] += T
    return ts, T / n


def _wing_geom(side):
    if side == "L":
        return model.wing_left, +model._r_centers.copy(), 0.0
    return model.wing_right, -model._r_centers.copy(), model.phase_offset_lr


def per_wing_beat_impulse(side, V, pd_deg, f_wing, n_beats=N_BEATS):
    """单翼在自身整数拍窗口内的每拍力与力矩冲量（无截断伪差）。"""
    w, rc, po = _wing_geom(side)
    w.set_frequency(f_wing)
    quat = euler_to_quat(0.0, math.radians(pd_deg), 0.0)
    v_body = quat_to_rotation_matrix(quat).T @ np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    ts, dt = window_times_fc(f_wing, n_beats)
    F = np.zeros(3)
    Tq = np.zeros(3)
    for t in ts:
        ws = w.evaluate(t, phase_offset_rad=po)
        Fw, Tw = model._wing_forces(rc, model._chords, ws, v_body, om)
        F += Fw * dt
        Tq += Tw * dt
    return F / n_beats, Tq / n_beats


def both_wings_impulse(V, pd_deg, fL, fR, f0, n_beats_f0):
    """直接窗口测量：窗口为 f0 的 n_beats_f0 拍（两翼拍数须均整数）。"""
    model.wing_left.set_frequency(fL)
    model.wing_right.set_frequency(fR)
    quat = euler_to_quat(0.0, math.radians(pd_deg), 0.0)
    v_body = quat_to_rotation_matrix(quat).T @ np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    ts, dt = window_times_fc(f0, n_beats_f0)
    wL, rcL, poL = _wing_geom("L")
    wR, rcR, poR = _wing_geom("R")
    F = np.zeros(3)
    Tq = np.zeros(3)
    for t in ts:
        wsL = wL.evaluate(t, phase_offset_rad=poL)
        wsR = wR.evaluate(t, phase_offset_rad=poR)
        FwL, TwL = model._wing_forces(rcL, model._chords, wsL, v_body, om)
        FwR, TwR = model._wing_forces(rcR, model._chords, wsR, v_body, om)
        F += (FwL + FwR) * dt
        Tq += (TwL + TwR) * dt
    return F, Tq


def lcm_beats(f0, df, cap=400):
    """最小公共拍数 N：N·(f0±df)/f0 均为整数（容差 1e-9）。"""
    for N in range(1, cap + 1):
        bL = N * (f0 + df) / f0
        bR = N * (f0 - df) / f0
        if abs(bL - round(bL)) < 1e-9 and abs(bR - round(bR)) < 1e-9:
            return N
    return None


def diff_channel(V, th, f0, df):
    """频率差动通道：解析合成与直接窗口两路径互核。

    返回 dict：每基准拍折算的 ΔJ_Tx / ΔJ_Tz / ΔJ_Fz（两路径）、
    一致性标志、公共拍数。
    """
    fL, fR = f0 + df, f0 - df
    # 路径一：解析合成（无截断）
    FL_, TL_ = per_wing_beat_impulse("L", V, th, fL)
    FR_, TR_ = per_wing_beat_impulse("R", V, th, fR)
    FL0, TL0 = per_wing_beat_impulse("L", V, th, f0)
    FR0, TR0 = per_wing_beat_impulse("R", V, th, f0)
    dF_rate = (FL_ * fL + FR_ * fR) - (FL0 * f0 + FR0 * f0)
    dT_rate = (TL_ * fL + TR_ * fR) - (TL0 * f0 + TR0 * f0)
    aF = dF_rate / f0
    aT = dT_rate / f0
    # 路径二：直接窗口（公共整数拍）
    N = lcm_beats(f0, df)
    dF_d = dT_d = None
    agree = None
    if N is not None:
        Fd, Td = both_wings_impulse(V, th, fL, fR, f0, N)
        Fb, Tb = both_wings_impulse(V, th, f0, f0, f0, N)
        dF_d = (Fd - Fb) / N
        dT_d = (Td - Tb) / N
        denom = max(abs(aT[0]), 1e-4)
        agree = bool(abs(dT_d[0] - aT[0]) / denom <= XCHECK_TOL)
    return {"df": df, "fL": fL, "fR": fR,
            "dJ_Tx": float(aT[0]), "dJ_Tz": float(aT[2]),
            "dJ_Fz": float(aF[2]),
            "dJ_Tx_direct": None if dT_d is None else float(dT_d[0]),
            "dJ_Tz_direct": None if dT_d is None else float(dT_d[2]),
            "dJ_Fz_direct": None if dF_d is None else float(dF_d[2]),
            "lcm_beats": N, "paths_agree": agree,
            "domega_roll_dps": math.degrees(aT[0] / IXX_APPROX),
            "beyond_failsafe": (fL > F_FAILSAFE) or (fR > F_FAILSAFE),
            "A4_pred_ok": bool(aT[0] > 0.0)}


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    src = os.path.join(cfg.LOG_DIR, "flap_quantum_grid_current.json")
    with open(src, encoding="utf-8") as fp:
        grid = json.load(fp)
    points = [(r["V"], r["f"], r["pitch"]) for r in grid["rows"]
              if r["trimmable"]]
    print(f"[dfs] 可配平格点 {len(points)} 个："
          + "、".join(f"({V:.2f},{f0:.2f})" for V, f0, _ in points),
          flush=True)

    out = {"ts": ts, "source_grid": "flap_quantum_grid_current.json",
           "n_per_beat": N_PER_BEAT, "n_beats": N_BEATS,
           "df_grid": DF_GRID, "divergence_dps": DIVERGENCE_DPS,
           "margin": MARGIN, "xcheck_tol": XCHECK_TOL,
           "method": "路径一解析合成（每翼自身整数拍）+路径二直接窗口"
                     "（公共整数拍）互核",
           "points": []}

    for V, f0, th in points:
        rec = {"V": V, "f0": f0, "pitch": th,
               "diff_rows": [], "sweep_rows": []}
        for df in DF_GRID:
            r = diff_channel(V, th, f0, df)
            rec["diff_rows"].append(r)
            dir_s = ("直测缺公共拍" if r["dJ_Tx_direct"] is None else
                     f"直测{r['dJ_Tx_direct']:+.5f}"
                     f"（{'一致' if r['paths_agree'] else '不一致'}）")
            print(f"[dfs] ({V:.2f},{f0:.2f}) Δf={df:.1f}：解析 "
                  f"ΔJ_Tx={r['dJ_Tx']:+.5f}（{r['domega_roll_dps']:+.1f}"
                  f"°/s/拍）{dir_s} ΔJ_Tz={r['dJ_Tz']:+.5f} "
                  f"ΔJ_Fz={r['dJ_Fz']:+.4f}"
                  f"{' [失效区外推]' if r['beyond_failsafe'] else ''}"
                  f" 方向{'符' if r['A4_pred_ok'] else '不符'}预测",
                  flush=True)
        # 单翼频率扫描（编号 43/44 偏导原料，自身整数拍开窗）
        fLo = max(1.0, f0 - 0.5)
        fHi = f0 + 0.5
        fSweep = [round(fLo + 0.1 * i, 2)
                  for i in range(int(round((fHi - fLo) / 0.1)) + 1)]
        for fL in fSweep:
            FLb, TLb = per_wing_beat_impulse("L", V, th, fL)
            rec["sweep_rows"].append(
                {"fL": fL, "dJ_Tx_L_per_beat": float(TLb[0]),
                 "dJ_Fz_L_per_beat": float(FLb[2]),
                 "lift_rate": float(FLb[2] * fL),
                 "beyond_failsafe": fL > F_FAILSAFE})
        out["points"].append(rec)
        sweep_str = " ".join(f"{r['fL']:.1f}:{r['lift_rate']:+.3f}"
                             for r in rec["sweep_rows"])
        print(f"[dfs] ({V:.2f},{f0:.2f}) 左翼秒均升力 q·f 随频率："
              + sweep_str, flush=True)

    # ── 判据甲三：巡航工作点权限覆盖考核 ──
    cruise = next((p for p in out["points"]
                   if abs(p["V"] - 3.95) < 1e-6 and abs(p["f0"] - 1.80) < 1e-6),
                  None)
    a3 = {"density_channel_dps": None, "freq_channel_dps": None,
          "threshold_dps": DIVERGENCE_DPS * MARGIN, "pass": None}
    if cruise is not None:
        qrow = next(r for r in grid["rows"]
                    if abs(r["V"] - 3.95) < 1e-6 and abs(r["f"] - 1.80) < 1e-6)
        a3["density_channel_dps"] = qrow["domega_roll_dps"]
        row05 = next(r for r in cruise["diff_rows"] if abs(r["df"] - 0.5) < 1e-9)
        a3["freq_channel_dps"] = row05["domega_roll_dps"]
        a3["density_pass"] = abs(a3["density_channel_dps"]) >= a3["threshold_dps"]
        a3["freq_pass"] = abs(a3["freq_channel_dps"]) >= a3["threshold_dps"]
        a3["pass"] = a3["density_pass"] and a3["freq_pass"]
    out["criterion_A3"] = a3
    print(f"[dfs] 判据甲三：密度全额 {a3['density_channel_dps']:+.1f}°/s/拍，"
          f"频率差全额 {a3['freq_channel_dps']:+.1f}°/s/拍，"
          f"判据线 {a3['threshold_dps']:.0f}°/s/拍，"
          f"{'通过' if a3['pass'] else '不通过'}", flush=True)

    with open(os.path.join(cfg.LOG_DIR, "quantum_diff_freq_scan.json"),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    # ── 文档 ──
    md = [f"# 异频量子·频率差动扫描（{ts}，修正版）", "",
          "- 判据来源：《不定频率完全差动_判据与方案.md》判据甲三、甲四"
          "（先于数据入库，运行后不改）",
          "- 输入：`logs/flap_quantum_grid_current.json` 的可配平格点"
          "（现行生效波形口径）",
          "- 原始数据：`logs/quantum_diff_freq_scan.json`",
          "- 方法：路径一解析合成（每翼自身整数拍开窗，无截断）与"
          "路径二直接窗口（公共整数拍）互核，容差 5%；初版固定 4 基准拍"
          "窗口的截断伪差已排除（先查代码纪律执行记录）", ""]
    for p in out["points"]:
        md += [f"## 格点 V={p['V']:.2f} m/s、f0={p['f0']:.2f} Hz"
               f"（配平俯仰 {p['pitch']:+.1f}°）", "",
               "### 频率差动通道（左 f0+Δf 对 右 f0−Δf）", "",
               "| Δf (Hz) | 左频 | 右频 | ΔJ_Tx 解析 (N·m·s/拍) | ΔJ_Tx 直测 |"
               " 折算滚转 (°/s/拍) | ΔJ_Tz | ΔJ_Fz | 两路径 | 方向性 |",
               "|---|---|---|---|---|---|---|---|---|"]
        for r in p["diff_rows"]:
            direct = "—" if r["dJ_Tx_direct"] is None else \
                f"{r['dJ_Tx_direct']:+.5f}"
            agree = "—" if r["paths_agree"] is None else \
                ("一致" if r["paths_agree"] else "不一致")
            flag = "符预测" if r["A4_pred_ok"] else "不符预测"
            if r["beyond_failsafe"]:
                flag += "·外推域"
            md.append(f"| {r['df']:.1f} | {r['fL']:.2f} | {r['fR']:.2f} |"
                      f" {r['dJ_Tx']:+.5f} | {direct} |"
                      f" {r['domega_roll_dps']:+.1f} |"
                      f" {r['dJ_Tz']:+.5f} | {r['dJ_Fz']:+.4f} |"
                      f" {agree} | {flag} |")
        md += ["", "### 左翼每拍冲量与秒均升力随自身频率（右翼固定 f0）", "",
               "| 左翼频率 (Hz) | 每拍 ΔJ_Tx (N·m·s) | 每拍 ΔJ_Fz (N·s) |"
               " 秒均升力 q·f (N) | 注 |",
               "|---|---|---|---|---|"]
        for r in p["sweep_rows"]:
            note = "失效区外推" if r["beyond_failsafe"] else ""
            md.append(f"| {r['fL']:.2f} | {r['dJ_Tx_L_per_beat']:+.5f} |"
                      f" {r['dJ_Fz_L_per_beat']:+.4f} |"
                      f" {r['lift_rate']:+.3f} | {note} |")
        md.append("")
    md += ["## 判据甲三：权限覆盖考核（巡航工作点 3.95 m/s × 1.80 Hz）", "",
           f"- 密度差动全额：{a3['density_channel_dps']:+.1f}°/s/拍"
           f"（= 单拍量子本身，密度线性断言已证）——"
           f"{'过线' if a3.get('density_pass') else '不过线'}",
           f"- 频率差动全额（Δf=0.5 Hz）：{a3['freq_channel_dps']:+.1f}"
           f"°/s/拍——{'过线' if a3.get('freq_pass') else '不过线'}",
           f"- 判据线：发散每拍增速 {DIVERGENCE_DPS:.0f}°/s × 裕度倍数 "
           f"{MARGIN:.0f}（自定、可调）= {a3['threshold_dps']:.0f}°/s/拍",
           f"- 判定：{'通过' if a3['pass'] else '不通过'}", ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", "异频量子_频率差动扫描.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[dfs] 完成 → docs/异频量子_频率差动扫描.md", flush=True)


if __name__ == "__main__":
    main()
