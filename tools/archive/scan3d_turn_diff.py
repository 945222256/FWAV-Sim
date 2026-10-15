# -*- coding: utf-8 -*-
"""
scan3d_turn_diff.py — 尾舵＋机翼差动联合权限的盘旋扫描（第十四班）

问题：第十三班修正版裁决"十五度以上滚转权限不足"基于尾舵单通道
（持续能力 0.10 N·m/rad×0.30 rad=0.03 N·m）。本工具打开机翼差动
通道（左翼拍幅 A0+dA、右翼 A0−dA），求每个坡度下把残余滚转力矩
归零所需的 dA，并重新裁决可达坡度边界。

口径与边界：
  - 工况沿用第十三班修正版：V=4.0 m/s、α_set=35°、协调盘旋角速度
    经线性阻尼通道生效（阻尼力矩剥离单列、不进裁决）。
  - dA 已验证域 ±0.10 rad（对照测试_冻结几何与真三维 的考核工况），
    外推域 ±0.175 rad（±10°）；超出已验证域的解在表中显式标注。
  - 竖直平衡达标判据 |Fz−W|/W≤2%；拍频网格 1.0~3.5 Hz、步长 0.5。
  - 轻量化：对称配平只求解一次，差动效能与复验在该姿态上单点求值；
    差动引起的俯仰平衡漂移为二阶小量，列入遗留。

用法： python tools/scan3d_turn_diff.py（纯 NumPy，无需 Isaac Sim）
产物： logs/scan3d_turn_diff_hr.json + docs/盘旋扫描_尾舵加翼差动_高分辨率.md

第十七班·高分辨率复跑+差动效能多点化（转弯路线图第四步）：
  - 求积分辨率分级（第十六班测量纪律）：粗扫/二分 n=72、终值 n=288，
    根精修同 scan3d_trim_turn.py；判据与口径零改动。
  - 差动效能 B_dA 从 ±0.10 单点考核改为多点中心差分
    （d=0.025…0.175 rad），给出线性范围：效能偏离 ±0.10 参考值
    ≤10% 的最大 d——外推域 ±0.175（±10°）的可用性由实测线性度
    支撑，20° 坡度结论从外推升格为实测外延。
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
from modules.aero_solver import TailFuselageAero, euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G = 9.81
W = cfg.MASS * G
M = cfg.MASS
T0 = [80000.0]

OP_V, OP_ALPHA_SET = 4.0, 35.0
ROLL_CAP = 0.10 * 0.30      # 尾舵持续能力 0.03 N·m
VERT_TOL = 0.02
F_LO, F_HI, F_STEP = 1.0, 3.5, 0.25
DA_VALID = 0.10             # rad，已验证域
DA_MAX = 0.175              # rad，外推域上限（±10°）
ROT_DAMP = float(getattr(cfg.AeroModelConfig, "WING_ROT_DAMP", 0.1))
A0 = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
# 第十七班：分辨率分级 + 差动效能多点考核
N_COARSE = 72
N_FINE = 288
DA_PROBE = [0.025, 0.05, 0.075, 0.10, 0.125, 0.15, 0.175]  # rad 中心差分点
LIN_TOL = 0.10   # 线性范围判据：偏离 B(0.10) ≤10% 的最大 d

model = SaneDickinson3DModel()
tail = TailFuselageAero()

for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(OP_ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)


def cm(V, f, pd_deg, bank_deg=0.0, om_body=(0.0, 0.0, 0.0), da=0.0,
       n=N_COARSE):
    """钉固状态整周期平均（含尾翼滑流、机翼差动 da）。
    返回 (F_world, T_aero, T_damp)，T_aero 已剥离阻尼力矩。"""
    model.set_wing_command(frequency=f)
    model.wing_left.amplitude = A0 + da
    model.wing_right.amplitude = A0 - da
    quat = euler_to_quat(math.radians(bank_deg), math.radians(pd_deg), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.asarray(om_body, dtype=np.float64)
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
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f
    Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
    T0[0] += T
    t_damp = -ROT_DAMP * om
    return F + Ft, Tq + Tt - t_damp, t_damp


def solve_pitch(V, f, bank=0.0, om=(0.0, 0.0, 0.0), da=0.0,
                lo=-20.0, hi=25.0, fine=False):
    """双阱稳健版：粗扫变号区间＋二分细化，多根取竖直残差最小者。
    fine=True（第十七班）：粗根 ±1° 用 N_FINE 二分 8 次精修+终值。"""
    grid = np.arange(lo, hi + 0.001, 1.5)
    rows = []
    for th in grid:
        F, _, _ = cm(V, f, float(th), bank, om, da)
        rows.append((float(th), float(F[0])))
    brackets = [(a[0], b[0]) for a, b in zip(rows[:-1], rows[1:])
                if a[1] == 0.0 or a[1] * b[1] < 0]
    if not brackets:
        th = min(rows, key=lambda r: abs(r[1]))[0]
        if not fine:
            F, T, Td = cm(V, f, th, bank, om, da)
            return th, F, T, Td
    else:
        best = None
        for a, b in brackets:
            fa = cm(V, f, a, bank, om, da)[0][0]
            for _ in range(14):
                mid = 0.5 * (a + b)
                fm = cm(V, f, mid, bank, om, da)[0][0]
                if fa * fm <= 0:
                    b = mid
                else:
                    a, fa = mid, fm
            th = 0.5 * (a + b)
            F, T, Td = cm(V, f, th, bank, om, da)
            score = abs(float(F[2]) - W)
            if best is None or score < best[0]:
                best = (score, th, F, T, Td)
        _, th, F, T, Td = best
        if not fine:
            return th, F, T, Td
    a, b = th - 1.0, th + 1.0
    fa = cm(V, f, a, bank, om, da, n=N_FINE)[0][0]
    fb = cm(V, f, b, bank, om, da, n=N_FINE)[0][0]
    if fa * fb < 0:
        for _ in range(8):
            mid = 0.5 * (a + b)
            fm = cm(V, f, mid, bank, om, da, n=N_FINE)[0][0]
            if fa * fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
        th = 0.5 * (a + b)
    F, T, Td = cm(V, f, th, bank, om, da, n=N_FINE)
    return th, F, T, Td


def turn_rates(w, bank_deg, pitch_deg):
    phi = math.radians(bank_deg)
    th = math.radians(pitch_deg)
    return np.array([-w * math.sin(th),
                     w * math.sin(phi) * math.cos(th),
                     w * math.cos(phi) * math.cos(th)])


def solve_turn_da(V, f, bank):
    """对称配平一次 → 多点测差动效能 → 线性求 dA* → 施加复验。
    第十七班：终值 n=288；B_dA 多点中心差分并测线性范围。"""
    th, F, T, Td = solve_pitch(V, f, bank, fine=True)
    fy = float(F[1])
    R = M * V ** 2 / max(abs(fy), 0.01)
    w = V / R
    om = turn_rates(w, bank, th)
    tx_sym = float(T[0])

    # 差动效能多点考核：B(d)=中心差分，参考值 B(0.10)=已验证域口径
    b_curve = []
    b_ref = None
    lin_max = 0.0
    for d in DA_PROBE:
        Fp, Tp, _ = cm(V, f, th, bank, om_body=om, da=+d, n=N_FINE)
        Fm, Tm, _ = cm(V, f, th, bank, om_body=om, da=-d, n=N_FINE)
        bd = (float(Tp[0]) - float(Tm[0])) / (2.0 * d)
        b_curve.append(dict(d=d, b_da=bd,
                            rel_dev=(bd - b_curve[0]["b_da"])
                            / abs(b_curve[0]["b_da"]) if b_curve else 0.0))
        if abs(d - DA_VALID) < 1e-9:
            b_ref = bd
    # 线性范围：相对 B(0.10) 偏离 ≤LIN_TOL 的最大 d
    b_ref = b_ref if b_ref is not None else b_curve[0]["b_da"]
    for item in b_curve:
        rel = abs((item["b_da"] - b_ref) / b_ref) if abs(b_ref) > 1e-9 else 0
        item["rel_dev_vs_ref"] = rel
        if rel <= LIN_TOL:
            lin_max = max(lin_max, item["d"])
    b_da = b_ref

    tx_after_tail = tx_sym - math.copysign(min(abs(tx_sym), ROLL_CAP),
                                           tx_sym)
    da_star, da_note = 0.0, "尾舵足够"
    if abs(tx_after_tail) > 1e-9 and abs(b_da) > 1e-6:
        da_need = -tx_after_tail / b_da
        if abs(da_need) <= DA_MAX:
            da_star = da_need
            da_note = "已验证域内" if abs(da_star) <= DA_VALID else "外推域"
        else:
            da_star = math.copysign(DA_MAX, da_need)
            da_note = "差动打满仍不足"

    # 施加 dA* 复验（N_FINE）：竖直平衡、残余滚转、侧向力与半径更新
    F2, T2, Td2 = cm(V, f, th, bank, om_body=om, da=da_star, n=N_FINE)
    fy2 = float(F2[1])
    R2 = M * V ** 2 / max(abs(fy2), 0.01)
    vert_resid = abs(float(F2[2]) - W) / W
    tx_resid = float(T2[0]) - math.copysign(
        min(abs(float(T2[0])), ROLL_CAP), float(T2[0]))
    lin_note = ("已验证域内" if lin_max <= DA_VALID + 1e-9 else
                f"线性至±{math.degrees(lin_max):.1f}°"
                + ("（覆盖外推域）" if lin_max >= DA_MAX - 1e-9 else
                   "（未覆盖外推域）"))
    return dict(th=th, F=F2, T=T2, R=R2, w=V / R2, vert_resid=vert_resid,
                tx_sym=tx_sym, b_da=b_da, da_star=da_star,
                da_note=da_note, tx_resid=tx_resid,
                b_curve=b_curve, lin_max=lin_max, lin_note=lin_note)


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "W": W, "model": "sane_dickinson_3d",
           "op": {"V": OP_V, "alpha_set": OP_ALPHA_SET},
           "roll_cap_tail": ROLL_CAP, "da_valid": DA_VALID,
           "da_max": DA_MAX, "vert_tol": VERT_TOL}

    print(f"[diff-turn] W={W:.3f} N，V={OP_V}，α_set={OP_ALPHA_SET}°，"
          f"尾舵额度 {ROLL_CAP:.3f} N·m，dA 域 ±{DA_VALID}/{DA_MAX} rad",
          flush=True)
    turn_rows = []
    for bank in range(5, 36, 5):
        best_row = None
        for f in np.arange(F_LO, F_HI + 0.001, F_STEP):
            r = solve_turn_da(OP_V, float(f), float(bank))
            key = (r["vert_resid"] > VERT_TOL, r["vert_resid"],
                   abs(r["tx_sym"]))
            if best_row is None or key < best_row["key"]:
                best_row = r
                best_row["key"] = key
                best_row["f"] = float(f)
                best_row["bank"] = float(bank)
        row = best_row
        row.pop("key")
        f_edge = row["f"] <= F_LO + 1e-9 or row["f"] >= F_HI - 1e-9
        if row["vert_resid"] > VERT_TOL:
            verdict = "升力不足（未配平）"
        elif abs(row["tx_sym"]) <= ROLL_CAP:
            verdict = "可行（尾舵足够）"
        elif row["da_note"] == "差动打满仍不足":
            verdict = "权限不足（尾舵＋差动打满）"
        elif f_edge:
            verdict = "拍频贴界（降级参考）"
        else:
            verdict = f"可行（尾舵＋差动·{row['da_note']}）"
        row["verdict"] = verdict
        turn_rows.append(row)
        print(f"[diff-turn] 坡度{bank}°: f={row['f']:.2f} "
              f"θ={row['th']:+.2f}° Fz={float(row['F'][2]):.2f} "
              f"残差={row['vert_resid']*100:.1f}% "
              f"R={row['R']:.1f}m ω={row['w']:.3f}rad/s "
              f"Tx_sym={row['tx_sym']:+.4f} B_dA={row['b_da']:+.4f} "
              f"线性±{math.degrees(row['lin_max']):.1f}° "
              f"dA*={math.degrees(row['da_star']):+.1f}°({row['da_note']}) "
              f"→ {verdict}", flush=True)
    out["turn"] = [
        {k: (list(map(float, v)) if isinstance(v, np.ndarray) else v)
         for k, v in row.items()}
        for row in turn_rows]

    with open(os.path.join(cfg.LOG_DIR, "scan3d_turn_diff_hr.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    md = [f"# 盘旋扫描·尾舵加机翼差动·高分辨率（{ts}）", "",
          f"- 模型：sane_dickinson_3d（镜像包装终版）；体重 W={W:.3f} N",
          f"- 工况：V={OP_V} m/s、α_set={OP_ALPHA_SET}°；协调盘旋角速度"
          f"经线性阻尼通道生效（阻尼 {ROT_DAMP} N·m·s/rad 剥离单列）",
          f"- 求积分辨率：粗扫/二分 n={N_COARSE}、终值 n={N_FINE}"
          f"（第十六班测量纪律；判据与口径与第十四班零改动）",
          f"- 尾舵持续能力 0.10 N·m/rad×0.30 rad={ROLL_CAP:.3f} N·m；"
          f"差动已验证域 ±{DA_VALID} rad、外推域 ±{DA_MAX} rad",
          f"- 差动效能 B_dA 多点中心差分（d="
          f"{'/'.join(f'{x}' for x in DA_PROBE)} rad），线性范围判据："
          f"偏离 B(±0.10) ≤{LIN_TOL:.0%} 的最大 d",
          f"- 原始数据：`logs/scan3d_turn_diff_hr.json`", "",
          "| 坡度 | 拍频 | 俯仰 | Fz (N) | 竖直残差 | 半径 (m) |"
          " 对称滚转需求 (N·m) | 差动效能 (N·m/rad) | 线性范围 |"
          " 所需 dA | 裁决 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in turn_rows:
        md.append(f"| {r['bank']:.0f}° | {r['f']:.2f} | {r['th']:+.2f}° |"
                  f" {float(r['F'][2]):.2f} | {r['vert_resid']*100:.1f}% |"
                  f" {r['R']:.1f} | {r['tx_sym']:+.4f} |"
                  f" {r['b_da']:+.4f} |"
                  f" ±{math.degrees(r['lin_max']):.1f}°（{r['lin_note']}） |"
                  f" {math.degrees(r['da_star']):+.1f}°"
                  f"（{r['da_note']}） | {r['verdict']} |")
    # 巡航邻域差动效能曲线单列（外推域可用性的直接证据）
    md += ["", "## 差动效能多点曲线（选行工况逐点中心差分）", ""]
    for r in turn_rows:
        md += [f"### 坡度 {r['bank']:.0f}°（f={r['f']:.2f} Hz）", "",
               "| d (rad) | d (°) | B_dA (N·m/rad) | 相对 B(0.10) 偏离 |",
               "|---|---|---|---|"]
        for it in r["b_curve"]:
            md.append(f"| {it['d']:.3f} | {math.degrees(it['d']):+.2f} |"
                      f" {it['b_da']:+.4f} |"
                      f" {it['rel_dev_vs_ref']*100:+.1f}% |")
        md.append("")
    md += ["", "## 判读与遗留", "",
           "- 选行规则：先竖直达标（残差≤2%），再对称滚转需求最小；"
           "尾舵额度零点零三牛米优先承担，差动补剩余。",
           "- 所需 dA 的域标注以多点线性范围实测为准：线性范围覆盖"
           " ±0.175 rad 时外推域结论升格为实测外延；未覆盖时该行"
           "结论仍属外推。",
           "- 轻量化口径：差动效能在对称配平姿态上单点姿态测量，差动"
           "引起的俯仰平衡漂移为二阶小量未回代，列入遗留。",
           "- 差动引入的偏航力矩见 JSON 的 T 列，其处置（方向舵协调）"
           "列为下一班议题。",
           "- 遗留同修正版：逐叶素 ω×r 耦合按 2026-09-19 裁决移除；"
           "阻尼持续盘旋适用性待裁决；尾翼模型不接收角速度输入。", ""]
    doc_path = os.path.join(
        PROJECT_ROOT, "docs", "盘旋扫描_尾舵加翼差动_高分辨率.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[diff-turn] 完成 → docs/盘旋扫描_尾舵加翼差动_高分辨率.md",
          flush=True)


if __name__ == "__main__":
    main()
