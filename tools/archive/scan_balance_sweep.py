# -*- coding: utf-8 -*-
"""
scan_balance_sweep.py — 扫掠俯仰耦合力矩的物理配平扫描与来源消融
（转弯路线图第二步，2026-09-24）

问题：三维模型巡航配平点存在扫掠俯仰耦合力矩 My≈+0.38 N·m
（第十三班发现；升降舵线性配平需 0.41 rad，超出 ±0.30 rad 舵偏
限幅）。用户方向裁决：走物理配平（重心平移或尾翼安装角偏置），
不放宽升降舵限幅；附带消融实验把该力矩分解为翼根安装偏置
（URDF 真值 −0.07 m）与扫掠几何（叶素位置随拍动角扫掠）两个来源。

方法：
  A. 重心平移路径：参考点沿机体 x 平移 δ 时 My_new = My + δ·Fz_body
     （气动合力不变、力臂平移；重力绕重心恒为零矩），解析求 δ*，
     用逐叶素力矩重算复核。报告所需平移量与方向、工程可行性判断
     （工程可接受重心调整按 ≤0.05 m 量级对照）。
  B. 尾翼安装角路径：安装角偏置在数学上等价于升降舵常值偏置
     （TailFuselageAero.elevator 直接叠加进尾面攻角），扫描 δ 求
     全机 My=0；双变量配平：外层 δ 二分对 My、内层 θ 配平 Fx=0。
     报告所需安装角、剩余动态舵权限（限幅 0.30−|δ*|）、安装后
     配平俯仰与竖直残差的变化。
  C. 消融：组1 完整三维；组2 翼根偏置归零（运行时重绑定
     modules.aero.sane_dickinson_3d.ROOT_OFFSET_3D，测后还原）。
     翼力不依赖力臂（速度场由 φ̇·r 生成，与位置无关），力矩对力臂
     线性：My_full = My_sweep + (offset×F_wing)_y，解析交叉验证。

测量纪律（第十六班三条）：时间轴单调推进 / 显式设频 /
粗扫 n=72、终值 n=288。

用法： python tools/scan_balance_sweep.py（纯 NumPy，无需 Isaac Sim）
产物： logs/scan_balance_sweep.json
      + docs/扫掠俯仰耦合力矩_物理配平与消融.md
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
from modules.aero import sane_dickinson_3d as sd3d
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G = 9.81
W = cfg.MASS * G
T0 = [90000.0]
N_COARSE, N_FINE = 72, 288

OP_V, OP_F, OP_ALPHA_SET = 4.0, 1.75, 35.0
DELTA_MAX = 0.30                  # 升降舵限幅 rad
CG_PRACTICAL = 0.05               # 工程可接受重心调整量级 m（对照用）
ROOT_OFFSET_TRUE = tuple(float(x) for x in sd3d.ROOT_OFFSET_3D)

model = SaneDickinson3DModel()
tail = TailFuselageAero()

for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(OP_ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)


def eval_state(V, f, pd_deg, elev=0.0, n=N_FINE):
    """钉固状态整周期平均，翼/尾分列返回（纪律一/二：单调时基+显式设频）。
    返回 dict：F_wing_world, T_wing_world, F_tail_world, T_tail_world,
    F_total, T_total, Fz_body。"""
    model.set_wing_command(frequency=f)
    tail.set_command(elevator=elev)
    quat = euler_to_quat(0.0, math.radians(pd_deg), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    R_bw = quat_to_rotation_matrix(quat)
    T = 1.0 / f
    Fw_sum = np.zeros(3)
    Tw_sum = np.zeros(3)
    for i in range(n):
        Fw, Tw = model.compute_forces(np.zeros(3), quat, vel, om,
                                      T0[0] + i * T / n)
        Fw_sum += Fw
        Tw_sum += Tw
    T0[0] += T
    Fw_sum /= n
    Tw_sum /= n
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f
    Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
    F_tot = Fw_sum + Ft
    Fz_body = float((R_bw.T @ F_tot)[2])
    return dict(F_wing=Fw_sum, T_wing=Tw_sum, F_tail=Ft, T_tail=Tt,
                F_total=F_tot, T_total=Tw_sum + Tt, Fz_body=Fz_body)


def solve_pitch(V, f, elev=0.0):
    """θ 域求 Fx=0（双阱稳健：粗扫变号+二分，粗 n=72、精修+终值 n=288）。"""
    def fx(th, n):
        return float(eval_state(V, f, th, elev, n)["F_total"][0])

    grid = np.arange(-20.0, 25.001, 1.5)
    vals = [(float(th), fx(th, N_COARSE)) for th in grid]
    brackets = [(a[0], b[0]) for a, b in zip(vals[:-1], vals[1:])
                if a[1] == 0.0 or a[1] * b[1] < 0]
    if not brackets:
        th0 = min(vals, key=lambda r: abs(r[1]))[0]
        a, b = th0 - 1.0, th0 + 1.0
    else:
        # 多根（双阱）取竖直残差最小者——与扫描工具族同口径；
        # 2026-09-24 修正：初版取 brackets[0] 可能落进 θ≈−2.3° 错误阱
        # （竖直残差约 5%），巡航配平根在 +7° 邻域
        best = None
        for a0, b0 in brackets:
            a, b = a0, b0
            fa = fx(a, N_COARSE)
            for _ in range(14):
                mid = 0.5 * (a + b)
                fm = fx(mid, N_COARSE)
                if fa * fm <= 0:
                    b = mid
                else:
                    a, fa = mid, fm
            th_c = 0.5 * (a + b)
            fz_c = float(eval_state(V, f, th_c, elev, N_COARSE)["F_total"][2])
            score = abs(fz_c - W)
            if best is None or score < best[0]:
                best = (score, th_c, a, b)
        _, th_c, a, b = best
    fa = fx(a, N_FINE)
    fb = fx(b, N_FINE)
    if fa * fb < 0:
        for _ in range(8):
            mid = 0.5 * (a + b)
            fm = fx(mid, N_FINE)
            if fa * fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
    return 0.5 * (a + b)


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "model": "sane_dickinson_3d",
           "op": {"V": OP_V, "f": OP_F, "alpha_set": OP_ALPHA_SET},
           "root_offset_true": list(ROOT_OFFSET_TRUE),
           "delta_max": DELTA_MAX, "cg_practical": CG_PRACTICAL}

    # ── 基线：巡航配平点与全机 My（完整几何）──
    th0 = solve_pitch(OP_V, OP_F)
    base = eval_state(OP_V, OP_F, th0)
    My = float(base["T_total"][1])
    Fz_b = base["Fz_body"]
    print(f"[bal] 配平点 θ={th0:+.2f}° Fx={float(base['F_total'][0]):+.4f} "
          f"Fz={float(base['F_total'][2]):.3f} 全机My={My:+.4f} "
          f"翼My={float(base['T_wing'][1]):+.4f} "
          f"尾My={float(base['T_tail'][1]):+.4f}", flush=True)
    out["baseline"] = dict(pitch=th0, fx=float(base["F_total"][0]),
                           fz=float(base["F_total"][2]), my_total=My,
                           my_wing=float(base["T_wing"][1]),
                           my_tail=float(base["T_tail"][1]),
                           fz_body=Fz_b)

    # ── 路径 A：重心平移（解析+复核）──
    # My_new = My + δ·Fz_body（参考点沿机体 +x 前移 δ）
    d_cg = -My / Fz_b if abs(Fz_b) > 1e-6 else float("nan")
    my_check = My + d_cg * Fz_b
    dir_txt = "前移" if d_cg > 0 else "后移"
    cg_feasible = abs(d_cg) <= CG_PRACTICAL
    cg_share = CG_PRACTICAL * abs(Fz_b)   # ±0.05m 能配掉的力矩
    print(f"[bal] 路径A 重心: 需{dir_txt} {abs(d_cg)*100:.1f} cm "
          f"(解析复核 My→{my_check:+.2e})；工程可行={cg_feasible}；"
          f"±{CG_PRACTICAL*100:.0f}cm 可配 {cg_share:.3f} N·m", flush=True)
    out["path_cg"] = dict(delta_m=d_cg, direction=dir_txt,
                          verify_my=my_check, feasible=cg_feasible,
                          moment_per_5cm=cg_share)

    # ── 路径 B：尾翼安装角（elevator 常值偏置等价）──
    # dMy/dδ 数值斜率 → 二分 δ* → 双变量复核
    def my_of(elev):
        th = solve_pitch(OP_V, OP_F, elev)
        st = eval_state(OP_V, OP_F, th, elev)
        return float(st["T_total"][1]), th, st

    d_probe = 0.10
    my_p, _, _ = my_of(+d_probe)
    my_m, _, _ = my_of(-d_probe)
    k_my = (my_p - my_m) / (2.0 * d_probe)
    d_star = -My / k_my if abs(k_my) > 1e-9 else float("nan")
    # 二分精修（k 线性时一步到位，仍走完整二分保证口径）
    a, b = d_star - 0.05, d_star + 0.05
    fa, _, _ = my_of(a)
    fb, _, _ = my_of(b)
    th_b, st_b = float("nan"), None
    if fa * fb < 0:
        for _ in range(10):
            mid = 0.5 * (a + b)
            fm, th_m, st_m = my_of(mid)
            if fa * fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
        d_star = 0.5 * (a + b)
        my_b, th_b, st_b = my_of(d_star)
    else:
        my_b, th_b, st_b = my_of(d_star)
    elev_left = DELTA_MAX - abs(d_star)
    fz_b2 = float(st_b["F_total"][2])
    vert2 = abs(fz_b2 - W) / W
    b_feasible = abs(d_star) <= DELTA_MAX
    print(f"[bal] 路径B 尾翼安装角: 斜率dMy/dδ={k_my:+.4f} "
          f"需δ*={math.degrees(d_star):+.2f}° ({d_star:+.4f} rad) "
          f"My→{my_b:+.4f}；剩余舵权限={elev_left:+.3f} rad；"
          f"θ {th0:+.2f}°→{th_b:+.2f}° 残差{vert2:.1%}", flush=True)
    out["path_tail"] = dict(
        k_my_per_rad=k_my, delta_rad=d_star, delta_deg=math.degrees(d_star),
        my_after=my_b, pitch_after=th_b, vert_resid_after=vert2,
        elevator_margin=elev_left, feasible=b_feasible)

    # ── 消融：翼根偏置归零（重绑定→测→还原）──
    sd3d.ROOT_OFFSET_3D = np.zeros(3)
    try:
        th_s = solve_pitch(OP_V, OP_F)
        abl = eval_state(OP_V, OP_F, th_s)
    finally:
        sd3d.ROOT_OFFSET_3D = np.asarray(ROOT_OFFSET_TRUE)
    my_full, my_sweep = My, float(abl["T_total"][1])
    # 解析交叉：My_full − My_sweep ≈ (offset×F_wing)_y = −off_x·Fz_wing
    off = np.asarray(ROOT_OFFSET_TRUE)
    my_off_part = float(np.cross(off, base["F_wing"])[1])
    cross_err = abs((my_full - my_sweep) - my_off_part)
    share_off = my_off_part / my_full if abs(my_full) > 1e-9 else float("nan")
    print(f"[bal] 消融: My完整={my_full:+.4f} 归零偏置后={my_sweep:+.4f} "
          f"差={my_full-my_sweep:+.4f} 解析偏置项={my_off_part:+.4f} "
          f"闭合误差={cross_err:.2e}；偏置来源占比={share_off:.0%}",
          flush=True)
    out["ablation"] = dict(
        my_full=my_full, my_offset_zero=my_sweep,
        my_offset_part_analytic=my_off_part, cross_check_err=cross_err,
        offset_share=share_off,
        pitch_offset_zero=th_s,
        fz_offset_zero=float(abl["F_total"][2]))

    with open(os.path.join(cfg.LOG_DIR, "scan_balance_sweep.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    # ── 报告 ──
    md = [f"# 扫掠俯仰耦合力矩·物理配平与消融（{ts}）", "",
          f"- 模型：sane_dickinson_3d；工况：V={OP_V} m/s、f={OP_F} Hz、"
          f"α_set={OP_ALPHA_SET}°；测量纪律三条遵守（粗 n={N_COARSE}、"
          f"终值 n={N_FINE}）",
          f"- 原始数据：`logs/scan_balance_sweep.json`", "",
          "## 基线（完整几何，升降舵零位）", "",
          f"- 配平俯仰 θ={th0:+.2f}°（Fx={float(base['F_total'][0]):+.4f} N）",
          f"- 全机 Fz={float(base['F_total'][2]):.3f} N"
          f"（竖直残差 {abs(float(base['F_total'][2]) - W) / W:.1%}）；"
          f"待配力矩 My={My:+.4f} N·m"
          f"（翼 {float(base['T_wing'][1]):+.4f} ＋ 尾"
          f" {float(base['T_tail'][1]):+.4f}）", "",
          "## 路径 A：重心平移", "",
          f"- 需重心{dir_txt} {abs(d_cg) * 100:.1f} cm"
          f"（δ={d_cg:+.4f} m）；解析复核 My→{my_check:+.2e} N·m。",
          f"- 对照工程可接受调整量 {CG_PRACTICAL * 100:.0f} cm："
          + ("可行。" if cg_feasible
             else f"**不可行**（超出一个数量级）；±{CG_PRACTICAL * 100:.0f} cm"
                  f" 只能配掉 {cg_share:.3f} N·m"
                  f"（占 {cg_share / abs(My):.0%}）。"), "",
          "## 路径 B：尾翼安装角偏置", "",
          f"- 数值斜率 dMy/dδ={k_my:+.4f} N·m/rad；所需安装角 "
          f"δ*={math.degrees(d_star):+.2f}°（{d_star:+.4f} rad）。",
          f"- 配平后：My→{my_b:+.4f} N·m，配平俯仰 "
          f"{th0:+.2f}°→{th_b:+.2f}°，竖直残差 {vert2:.1%}。",
          f"- 剩余动态舵权限 {elev_left:+.3f} rad"
          + ("（安装角吃掉限幅的比例 "
             f"{abs(d_star) / DELTA_MAX:.0%}）。" if b_feasible
             else "——**超限幅，路径 B 也不可行**。"), "",
          "## 消融：翼根偏置 vs 扫掠几何", "",
          f"- 完整几何：My={my_full:+.4f} N·m；翼根偏置归零："
          f"My={my_sweep:+.4f} N·m（θ 重配平 {th_s:+.2f}°）。",
          f"- 解析交叉验证：差值 {my_full - my_sweep:+.4f} vs 解析偏置项"
          f" {my_off_part:+.4f}（闭合误差 {cross_err:.2e} N·m）。",
          f"- 来源分解：翼根安装偏置贡献约 {share_off:.0%}，"
          f"扫掠几何贡献约 {1 - share_off:.0%}"
          f"（交叉项并入偏置口径，见解析式）。", "",
          "## 判读与遗留", "",
          "- 方向裁决对照：用户裁定物理配平优先；本表给出两条路径的"
          "量化可行性，供第十七班后在两条路径中定案。",
          "- 安装角偏置等价于升降舵常值偏置的口径声明："
          "TailFuselageAero 中 elevator 直接叠加进尾面攻角，与结构安装"
          "角在数学上不可区分；工程差异（铰链力矩、伺服功率）不在本"
          "模型内。",
          "- 遗留：消融只分离常量偏置力臂与扫掠力臂（力对力臂线性），"
          "未分离攻角三维化/法向方向三维化的贡献（其为力的变化，"
          "影响力臂分解的分母 F_wing）。", ""]
    doc_path = os.path.join(
        PROJECT_ROOT, "docs", "扫掠俯仰耦合力矩_物理配平与消融.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[bal] 完成 → docs/扫掠俯仰耦合力矩_物理配平与消融.md",
          flush=True)


if __name__ == "__main__":
    main()
