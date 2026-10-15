# -*- coding: utf-8 -*-
"""
scan3d_trim_turn.py — 三维模型配平重扫＋压坡度盘旋扫描（第十三班修正版）

相对第十三班初版的三处修正（评审意见落地）：
  1. 工况口径统一：波形攻角设定切到扩展配平搜索的高速高频近优解
     （α_set=35°，V=4.0 m/s、f=1.75 Hz 邻域），盘旋扫描不再沿用
     默认波形（α_set=40°）的旧工况——初版两部分口径混用已记档。
  2. 协调盘旋角速度：定常盘旋的机体系角速度（p=−ω·sinθ、
     q=ω·sinφ·cosθ、r=ω·cosφ·cosθ）经 compute_forces 角速度入口
     生效，盘旋半径与角速度迭代收敛。边界声明：逐叶素 ω×r 耦合按
     2026-09-19 裁决保持移除（|C_N| 饱和在 |ω|>3 rad/s 后反号自激，
     见 aero_solver.py 基类 _wing_forces 注释）；本工具只激活已登记
     的线性旋转阻尼通道。阻尼力矩从滚转需求中剥离单列——其标定
     工况是小信号姿态阻尼，持续盘旋角速度下的适用性列为待裁决项。
  3. 达标判据：竖直平衡残差 |Fz−W|/W ≤ 2% 才允许裁决"可行"；
     滚转力矩需求对照尾翼差动持续能力（0.10 N·m/rad×0.30 rad
     =0.03 N·m）；拍频贴执行域边界（1.0/3.5 Hz）的行降级为参考。
  4. 求解器稳健化：探针证实 |Fx|(θ) 为双阱地形（θ≈−3.4° 与
     θ≈+9° 两个过零点，probe_theta_branch.py），初版三分搜索
     收敛到错误阱；本版改为粗扫全部变号区间＋二分细化，多根时
     取竖直残差最小者。

用法： python tools/scan3d_trim_turn.py（纯 NumPy，无需 Isaac Sim）
产物： logs/scan3d_trim_turn_hr.json + docs/三维盘旋扫描_高分辨率复跑.md

第十七班·高分辨率复跑（转弯路线图第一步）：
  5. 求积分辨率分级（第十六班测量纪律之三落地）：粗扫与二分
     n=N_COARSE=72，选行终值与根精修 n=N_FINE=288。原因：Fx 是
     推力/阻力近对消的小量，n=36 矩形求积误差约 ±0.02 N、与配平
     残差同量级，修正版表俯仰根的数度级不确定性即由此产生。
     本班只动分辨率一个变量，判据与口径零改动（单变量纪律）；
     输出用 _hr 新文件，修正版原数据保留供逐行对照（见报告第三节）。
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
T0 = [60000.0]

# 修正一：工况取自扩展配平搜索的高速高频近优解
OP_V, OP_F, OP_ALPHA_SET = 4.0, 1.75, 35.0
# 修正三：裁决常数
ROLL_AUTHORITY = 0.10      # 尾翼差动操纵效能 N·m/rad（飞行包线口径）
DELTA_MAX = 0.30           # 舵偏限幅 rad
ROLL_CAP = ROLL_AUTHORITY * DELTA_MAX   # 持续能力 0.03 N·m
VERT_TOL = 0.02            # 竖直平衡达标残差
F_LO, F_HI = 1.0, 3.5      # 自驾拍频执行域
# 线性旋转阻尼系数（与 compute_forces 内生效值同源读取）
ROT_DAMP = float(getattr(cfg.AeroModelConfig, "WING_ROT_DAMP", 0.6))
# 第十七班：求积分辨率分级（第十六班测量纪律三条之三）
N_COARSE = 72    # 粗扫与二分
N_FINE = 288     # 选行终值与根精修

model = SaneDickinson3DModel()
tail = TailFuselageAero()


def set_kinematics(alpha_set=None, split=None, lag=None, mean=None):
    for w in (model.wing_left, model.wing_right):
        if alpha_set is not None:
            w.alpha_set = math.radians(alpha_set)
        if split is not None:
            w.split = split
        if lag is not None:
            w.pitch_lag_s = lag
        if mean is not None:
            w.mean = math.radians(mean)


# 修正一落地：攻角设定 35°，其余波形通道保持配置现行值
set_kinematics(OP_ALPHA_SET,
               cfg.FlapKinematicsConfig.SPLIT,
               cfg.FlapKinematicsConfig.PITCH_LAG_S,
               cfg.FlapKinematicsConfig.MEAN)


def cm(V, f, pd_deg, bank_deg=0.0, om_body=(0.0, 0.0, 0.0), n=N_COARSE):
    """钉固状态整周期平均（含尾翼滑流）。om_body 为机体系角速度，
    仅经 compute_forces 的线性旋转阻尼通道生效（逐叶素 ω×r 耦合
    按 2026-09-19 裁决保持移除）。返回 (F_world, T_aero, T_damp)，
    其中 T_aero 已剥离阻尼力矩，T_damp 单列。"""
    model.set_wing_command(frequency=f)
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


def solve_pitch(V, f, bank=0.0, om=(0.0, 0.0, 0.0), lo=-20.0, hi=25.0,
                fine=False):
    """θ 域求 Fx=0 的根。|Fx|(θ) 已证实为双阱地形（probe_theta_branch），
    故不用三分搜索：粗扫全部变号区间＋二分细化，多根时取竖直残差
    |Fz−W| 最小者（配平语义是力的联合平衡，不是单一通道归零）。
    fine=True（第十七班）：粗根邻域 ±1° 用 N_FINE 二分 8 次精修根
    位置、终值 N_FINE——消低分辨率求积噪声引起的根位置跳动。"""
    grid = np.arange(lo, hi + 0.001, 1.0)
    rows = []
    for th in grid:
        F, _, _ = cm(V, f, float(th), bank, om)
        rows.append((float(th), float(F[0])))
    brackets = [(a[0], b[0]) for a, b in zip(rows[:-1], rows[1:])
                if a[1] == 0.0 or a[1] * b[1] < 0]
    if not brackets:
        th = min(rows, key=lambda r: abs(r[1]))[0]
        if not fine:
            F, T, Td = cm(V, f, th, bank, om)
            return th, F, T, Td
    else:
        best = None
        for a, b in brackets:
            fa = cm(V, f, a, bank, om)[0][0]
            for _ in range(14):
                mid = 0.5 * (a + b)
                fm = cm(V, f, mid, bank, om)[0][0]
                if fa * fm <= 0:
                    b = mid
                else:
                    a, fa = mid, fm
            th = 0.5 * (a + b)
            F, T, Td = cm(V, f, th, bank, om)
            score = abs(float(F[2]) - W)
            if best is None or score < best[0]:
                best = (score, th, F, T, Td)
        _, th, F, T, Td = best
        if not fine:
            return th, F, T, Td
    # fine 精修：粗根 ±1° 内 N_FINE 二分 8 次 + N_FINE 终值
    a, b = th - 1.0, th + 1.0
    fa = cm(V, f, a, bank, om, n=N_FINE)[0][0]
    fb = cm(V, f, b, bank, om, n=N_FINE)[0][0]
    if fa * fb < 0:
        for _ in range(8):
            mid = 0.5 * (a + b)
            fm = cm(V, f, mid, bank, om, n=N_FINE)[0][0]
            if fa * fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
        th = 0.5 * (a + b)
    F, T, Td = cm(V, f, th, bank, om, n=N_FINE)
    return th, F, T, Td


def turn_rates(w, bank_deg, pitch_deg):
    """定常协调盘旋的机体系角速度：地垂系转弯率 w 按姿态分解。
    φ̇=θ̇=0 → p=−w·sinθ，q=w·sinφ·cosθ，r=w·cosφ·cosθ。"""
    phi = math.radians(bank_deg)
    th = math.radians(pitch_deg)
    return np.array([-w * math.sin(th),
                     w * math.sin(phi) * math.cos(th),
                     w * math.cos(phi) * math.cos(th)])


def solve_turn(V, f, bank, iters=6):
    """盘旋半径 R 与转弯率 ω 迭代收敛。当前模型里力不依赖 om（om 只
    经线性阻尼影响力矩），迭代一次即收敛；保留迭代结构以便逐叶素
    ω×r 耦合若未来恢复时本工具口径不变。"""
    om = np.zeros(3)
    th, R, w = 0.0, float("nan"), 0.0
    for _ in range(iters):
        th, F, T, Td = solve_pitch(V, f, bank, om=om)
        fy = float(F[1])
        R = M * V ** 2 / max(abs(fy), 0.01)
        w_new = V / R
        om_new = turn_rates(w_new, bank, th)
        if abs(w_new - w) <= 0.01 * max(abs(w_new), 1e-6):
            om = om_new
            w = w_new
            break
        om = om_new
        w = w_new
    # 第十七班：收敛后高分辨率终值（根精修 + N_FINE 周期平均）
    th, F, T, Td = solve_pitch(V, f, bank, om=om, fine=True)
    return th, F, T, Td, R, w, om


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "W": W, "model": "sane_dickinson_3d",
           "op": {"V": OP_V, "f": OP_F, "alpha_set": OP_ALPHA_SET},
           "roll_cap": ROLL_CAP, "vert_tol": VERT_TOL, "rot_damp": ROT_DAMP}

    # ── 第一步：新工况邻域配平复核（统一口径）──
    print(f"[3D-trim] W={W:.3f} N，α_set={OP_ALPHA_SET}°，"
          f"邻域复核 (V, f, θ)...", flush=True)
    trim_rows = []
    for V in np.arange(OP_V - 0.5, OP_V + 0.51, 0.25):
        for f in np.arange(OP_F - 0.5, OP_F + 0.51, 0.25):
            th, F, T, _ = solve_pitch(float(V), float(f))
            J = math.hypot(F[0] / W, (F[2] - W) / W)
            trim_rows.append(dict(V=float(V), f=float(f), pitch=th,
                                  fx=float(F[0]), fz=float(F[2]),
                                  my=float(T[1]), J=J))
    trim_rows.sort(key=lambda d: d["J"])
    best = trim_rows[0]
    # 第十七班：最优行高分辨率复核（根精修 + N_FINE 终值）
    th_f, F_f, T_f, _ = solve_pitch(float(best["V"]), float(best["f"]),
                                    fine=True)
    best = dict(V=float(best["V"]), f=float(best["f"]), pitch=th_f,
                fx=float(F_f[0]), fz=float(F_f[2]), my=float(T_f[1]),
                J=math.hypot(F_f[0] / W, (F_f[2] - W) / W))
    print(f"[3D-trim] 最优(hr): V={best['V']:.1f} f={best['f']:.2f} "
          f"θ={best['pitch']:+.2f}° Fx={best['fx']:+.3f} Fz={best['fz']:.3f} "
          f"My={best['my']:+.4f} J={best['J']:.4f}", flush=True)
    out["trim_best"] = best
    out["trim_top"] = trim_rows[:10]

    V0 = best["V"]

    # ── 第二步：压坡度盘旋扫描（协调角速度+达标判据）──
    print(f"[3D-turn] V={V0} m/s，坡度 5°~35° 网格...", flush=True)
    turn_rows = []
    for bank in range(5, 36, 5):
        bank_f = float(bank)
        best_row = None
        for f in np.arange(F_LO, F_HI + 0.001, 0.25):
            th, F, T, Td, R, w, om = solve_turn(V0, float(f), bank_f)
            vert_resid = abs(float(F[2]) - W) / W
            row = dict(bank=bank_f, f=float(f), pitch=th,
                       fx=float(F[0]), fy=float(F[1]), fz=float(F[2]),
                       vert_resid=vert_resid,
                       roll_moment=float(T[0]), pitch_moment=float(T[1]),
                       yaw_moment=float(T[2]),
                       damp_moment=[float(x) for x in Td],
                       turn_radius=R, turn_rate=w,
                       om_body=[float(x) for x in om])
            # 选行规则：先比竖直达标，再比竖直残差小，再比滚转需求小
            key = (vert_resid > VERT_TOL, vert_resid, abs(row["roll_moment"]))
            if best_row is None or key < best_row["key"]:
                best_row = row
                best_row["key"] = key
        row = best_row
        f_edge = row["f"] <= F_LO + 1e-9 or row["f"] >= F_HI - 1e-9
        if row["vert_resid"] > VERT_TOL:
            verdict = "升力不足（未配平）"
        elif abs(row["roll_moment"]) > ROLL_CAP:
            verdict = "滚转权限不足"
        elif f_edge:
            verdict = "拍频贴界（降级参考）"
        else:
            verdict = "可行"
        row["verdict"] = verdict
        row.pop("key")
        turn_rows.append(row)
        print(f"[3D-turn] 坡度{bank}°: f={row['f']:.2f} "
              f"θ={row['pitch']:+.1f}° Fz={row['fz']:.2f} "
              f"残差={row['vert_resid']*100:.1f}% Fy={row['fy']:+.3f} "
              f"R={row['turn_radius']:.1f}m ω={row['turn_rate']:.3f}rad/s "
              f"Tx={row['roll_moment']:+.4f} 阻尼Tx={row['damp_moment'][0]:+.4f} "
              f"→ {verdict}", flush=True)
    out["turn"] = turn_rows

    with open(os.path.join(cfg.LOG_DIR, "scan3d_trim_turn_hr.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    # ── 与修正版旧表逐行对照（第十七班新增，判据先于数据）──
    old_path = os.path.join(cfg.LOG_DIR, "scan3d_trim_turn_v2.json")
    cmp_rows = []
    if os.path.exists(old_path):
        with open(old_path, encoding="utf-8") as fp:
            old = json.load(fp)
        old_by_bank = {r["bank"]: r for r in old.get("turn", [])}
        for r in turn_rows:
            o = old_by_bank.get(r["bank"])
            if o is None:
                continue
            cmp_rows.append(dict(
                bank=r["bank"], pitch_old=o["pitch"], pitch_new=r["pitch"],
                dpitch=r["pitch"] - o["pitch"],
                f_old=o["f"], f_new=r["f"],
                R_old=o["turn_radius"], R_new=r["turn_radius"],
                tx_old=o["roll_moment"], tx_new=r["roll_moment"],
                verdict_old=o["verdict"], verdict_new=r["verdict"]))
    out["compare_v2"] = cmp_rows
    with open(os.path.join(cfg.LOG_DIR, "scan3d_trim_turn_hr.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    # ── 报告 ──
    md = [f"# 三维盘旋扫描·高分辨率复跑（{ts}）", "",
          f"- 模型：sane_dickinson_3d（镜像包装终版）；体重 W={W:.3f} N",
          f"- 工况：α_set={OP_ALPHA_SET}°（扩展配平搜索近优解波形），"
          f"其余波形通道保持配置现行值",
          f"- 判据与口径与修正版完全一致：竖直残差 |Fz−W|/W≤"
          f"{VERT_TOL:.0%}、滚转需求≤{ROLL_CAP:.3f} N·m、拍频不贴界；"
          f"唯一变量=求积分辨率（粗扫 n={N_COARSE}、终值 n={N_FINE}，"
          f"修正版为 n=36）",
          f"- 滚转需求对照：尾翼差动持续能力 {ROLL_AUTHORITY:.2f} N·m/rad"
          f"×{DELTA_MAX:.2f} rad = {ROLL_CAP:.3f} N·m；阻尼力矩已剥离单列",
          f"- 原始数据：`logs/scan3d_trim_turn_hr.json`"
          f"（含与修正版的逐行对照段 compare_v2）", "",
          "## 第一步：新工况邻域配平复核（高分辨率终值）", "",
          f"- 最优配平点：V={best['V']:.2f} m/s，f={best['f']:.2f} Hz，"
          f"θ={best['pitch']:+.2f}°",
          f"- Fx={best['fx']:+.3f} N，Fz={best['fz']:.3f} N，"
          f"My={best['my']:+.4f} N·m，J={best['J']:.4f}", "",
          "## 第二步：压坡度盘旋（高分辨率裁决）", "",
          "| 坡度 | 拍频 | 俯仰 | Fz (N) | 竖直残差 | Fy (N) | 半径 (m) |"
          " 角速度 (rad/s) | 滚转需求 (N·m) | 阻尼滚转 (N·m) | 裁决 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in turn_rows:
        md.append(f"| {r['bank']:.0f}° | {r['f']:.2f} | {r['pitch']:+.2f}° |"
                  f" {r['fz']:.2f} | {r['vert_resid']*100:.1f}% |"
                  f" {r['fy']:+.3f} | {r['turn_radius']:.1f} |"
                  f" {r['turn_rate']:.3f} | {r['roll_moment']:+.4f} |"
                  f" {r['damp_moment'][0]:+.4f} | {r['verdict']} |")
    if cmp_rows:
        md += ["", "## 第三步：与修正版（n=36）逐行对照", "",
               "| 坡度 | 俯仰(旧) | 俯仰(新) | 移动量 | 拍频(旧→新) |"
               " 半径(旧→新) | 滚转需求(旧→新) | 裁决(旧→新) |",
               "|---|---|---|---|---|---|---|---|"]
        for c in cmp_rows:
            md.append(
                f"| {c['bank']:.0f}° | {c['pitch_old']:+.1f}° |"
                f" {c['pitch_new']:+.2f}° | {c['dpitch']:+.2f}° |"
                f" {c['f_old']:.2f}→{c['f_new']:.2f} |"
                f" {c['R_old']:.1f}→{c['R_new']:.1f} |"
                f" {c['tx_old']:+.4f}→{c['tx_new']:+.4f} |"
                f" {c['verdict_old']}→{c['verdict_new']} |")
        dp = [abs(c["dpitch"]) for c in cmp_rows]
        flips = [c for c in cmp_rows if c["verdict_old"] != c["verdict_new"]]
        md += ["",
               f"- 俯仰根移动量：最大 {max(dp):.2f}°、平均 "
               f"{sum(dp)/len(dp):.2f}°（n=36→n=288 的根位置修正）。",
               f"- 裁决翻转：{len(flips)} 行。"
               + ("；".join(f"{c['bank']:.0f}°:{c['verdict_old']}→"
                            f"{c['verdict_new']}" for c in flips)
                  if flips else "全部行维持修正版裁决。"),
               "- 竖直力/半径/滚转需求列的变差量级见 compare_v2"
               "（预期约 0.1% 量级，与第十六班分辨率稳健性结论对照）。"]
    md += ["", "## 判读与遗留", "",
           "- 判读标准先于数据：竖直残差≤2% 且滚转需求≤0.03 N·m 且拍频"
           "不贴界，三者同时满足才裁决'可行'。",
           "- 遗留一：逐叶素 ω×r 耦合保持移除（2026-09-19 裁决），外翼增速"
           "引起的过弯滚转力矩不在需求列内；其量级估计为转弯率×半展长"
           "空速差阶次，列入真机辨识待办。",
           f"- 遗留二：线性旋转阻尼（现行 {ROT_DAMP} N·m·s/rad，取值依据"
           "数值稳定性、属处置清单遗留未决项）按小信号姿态阻尼引入，"
           "持续盘旋角速度（本扫描最大约 1.6 rad/s）下是否适用为待裁决项，"
           "故阻尼力矩单列、不进裁决。",
           "- 遗留三：尾翼模型不接收角速度输入，盘旋俯仰率对尾翼等效攻角"
           "的增量未计入。",
           "- 与修正版的对照：本版只把求积分辨率从 n=36 提到 72/288 分级，"
           "俯仰根位置以本表为准；裁决翻转为零时，修正版结论升格为"
           "分辨率稳健。", ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", "三维盘旋扫描_高分辨率复跑.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[3D] 完成 → docs/三维盘旋扫描_高分辨率复跑.md", flush=True)


if __name__ == "__main__":
    main()
