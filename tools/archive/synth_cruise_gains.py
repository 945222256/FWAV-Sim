# -*- coding: utf-8 -*-
"""
synth_cruise_gains.py — 任务二：巡航段（新工作点）俯仰与横航向增益整定分析
========================================================================
只读分析工具（不修改 config/ 与 modules/ 下任何文件）。

输入（全部只读）：
  - logs/ident_stream_flight_0924_202954.csv：第四跑（5c 发射窗口方案）数据流；
  - logs/scan_rot_damping_op2.json：新工作点 3×3 旋转阻尼矩阵
    （docs/旋转阻尼专项辨识_新工作点复测.md 的原始数据）；
  - config/sim_config.py：现行增益（只读导入，不写回）。

新工作点：V=3.5 m/s、f=1.25 Hz、α_set=35°、MEAN=+20°、SPLIT=0.75、
PITCH_LAG_S=−0.04 s、幅值 30°、配平俯仰 +21.45°（0.374 rad）。

分析节：
  [1] 第四跑数据流分段（爬升段/飞行段/自由段）与基础统计
  [2] 等效舵效辨识（三口径互证：静态回归/力矩级/角加速度级；Iyy 等效值）
  [3] 俯仰通道设计模型与极点配置（ζ=0.7，闭环带宽 ≤1.5 rad/s）
  [4] 与现行增益对比（含现行增益在辨识对象上的极点与饱和量化）
  [5] 升降舵均方根权限预算（轨迹反演 + 线性发射阶跃仿真，15 s 视界）
  [6] 横航向 2×2 耦合矩阵 MIMO 整定建议（对角占优检验/解耦前置补偿）
  [7] 判据核对（判据先于数据：闭环极点实部均负；RMS ≤ 满偏 70%）

输出：logs/synth_cruise_gains.json + 控制台摘要。

符号约定（与代码链一致）：
  - 正 elev → 负俯仰力矩（K_B_TAIL_ELEV=−0.0145、LEVER_ARM=−0.45），
    即正舵偏驱动俯仰角下降；
  - PD 律取正增益形式 δ = KP·(θ − θ_des) + KD·q（θ 为实际俯仰、q=wy）。
"""
import csv
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config.sim_config as cfg  # noqa: E402  只读导入现行增益

FCC = cfg.FlightControllerConfig

# ── 输入路径 ──
RUN_CSV = os.path.join(ROOT, "logs", "ident_stream_flight_0924_202954.csv")
DAMP_JSON = os.path.join(ROOT, "logs", "scan_rot_damping_op2.json")
OUT_JSON = os.path.join(ROOT, "logs", "synth_cruise_gains.json")

# ── 设计常数（判据先于数据，先于一切计算声明）──
D_PITCH_PHYS = 0.0018      # N·m·s/rad 新工作点物理俯仰阻尼斜率（op2 辨识，
                           # 符号为正=非恢复；按任务指令取该值建设计模型）
ZETA = 0.70                # 目标闭环阻尼比（欠阻尼适中）
WBW_MAX = 1.5              # rad/s 闭环带宽上限（舵权限现实的低带宽求稳）
WN = 1.4                   # rad/s 自然频率取值（对应带宽 1.414，留 5.7% 裕度）
DELTA_MAX = FCC.DELTA_MAX  # 0.30 rad 升降舵限幅
RMS_FRAC_MAX = 0.70        # 判据：15 s 发射爬升段 RMS ≤ 满偏 70%
T_LAUNCH = 15.0            # s 判据视界
MASS = cfg.MASS
G = 9.81

# 窗口边界（出处：docs/三维模型在环确认_第五步升格.md）
T_RELEASE = 0.93           # s 释放（elev 自 0.93 s 起 bang-bang）
T_CLIMB_END = 3.25         # s 下沉段起点
T_GRAZE_Z = 0.25           # m 掠地判高（4.61 s 掠地 z 最低 0.201 m）
T_CRASH_Z = 0.20           # m 终坠判高（统一口径 z<0.2）

# 第四跑激活过、冻结库现行值不同的人工旋转阻尼（5c 曾设 0.005，冻结库 0.1）
WING_ROT_DAMP_RUN = 0.005
WING_ROT_DAMP_FROZEN = cfg.AeroModelConfig.WING_ROT_DAMP


def ols_slope(x, y):
    """中心化最小二乘斜率（无截距项等价于带截距的斜率）。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x = x - x.mean()
    y = y - y.mean()
    return float((x * y).sum() / max((x * x).sum(), 1e-12))


def boxsm(a, k=5):
    """k 点滑动平均（压制拍频纹波后再做差分回归）。"""
    return np.convolve(np.asarray(a, dtype=float), np.ones(k) / k, mode="same")


def cplx(z):
    """复数枚举转 JSON 安全字符串。"""
    return [f"{v.real:+.6g}{v.imag:+.6g}j" for v in z]


def main():
    report = {"tool": "tools/synth_cruise_gains.py", "ts": None,
              "op_point": {"V": 3.5, "f": 1.25, "alpha_set_deg": 35.0,
                           "MEAN_deg": 20.0, "SPLIT": 0.75,
                           "PITCH_LAG_S": -0.04, "amplitude_deg": 30.0,
                           "pitch_trim_rad": 0.374}}

    # ════════════════ [1] 数据流加载与分段 ════════════════
    rows = []
    with open(RUN_CSV, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)

    def col(n):
        return np.array([float(r[n]) for r in rows])

    t = col("t"); z = col("z"); pit = col("pitch"); wy = col("wy")
    elv = col("elev"); my = col("My"); tmy = col("TMy")
    veff = col("veff"); pdes = col("pitch_des")
    wx = col("wx"); wz = col("wz"); roll = col("roll")

    t_crash = next((float(ti) for ti, zi in zip(t, z) if ti > 1.0 and zi < T_CRASH_Z), None)
    t_graze = next((float(ti) for ti, zi in zip(t, z) if ti > 1.0 and zi < T_GRAZE_Z), None)
    windows = {
        "climb": (T_RELEASE, T_CLIMB_END),
        "flight": (T_RELEASE, t_graze),
        "free": (T_RELEASE, t_crash),
    }

    def wsel(name):
        a, b = windows[name]
        i0 = int(np.argmax(t > a))
        i1 = int(np.argmax(t > b)) if b else len(t)
        return slice(i0, i1)

    win_stats = {}
    for name in windows:
        s = wsel(name)
        win_stats[name] = {
            "t0": float(t[s][0]), "t1": float(t[s][-1]), "n": int(s.stop - s.start),
            "elev_sat_frac": float(np.mean(np.abs(elv[s]) >= 0.295)),
            "wy_rms": float(np.sqrt((wy[s] ** 2).mean())),
            "wy_absmax": float(np.abs(wy[s]).max()),
            "pitch_min": float(pit[s].min()), "pitch_max": float(pit[s].max()),
            "My_peak_abs": float(np.abs(my[s]).max()),
            "veff_median": float(np.median(veff[s])),
        }
    report["run"] = {"csv": os.path.relpath(RUN_CSV, ROOT), "n_rows": len(rows),
                     "t_crash_z0p2": t_crash, "t_graze_z0p25": t_graze,
                     "windows": win_stats}

    # 爬升段俯仰振荡指纹（设计带宽选择的依据）
    s_cl = wsel("climb")
    thc = pit[s_cl] - pit[s_cl].mean()
    fs = 1.0 / float(np.median(np.diff(t[s_cl])))
    Yf = np.abs(np.fft.rfft(thc * np.hanning(len(thc))))
    fr = np.fft.rfftfreq(len(thc), 1.0 / fs)
    k_pk = int(np.argmax(Yf[1:])) + 1
    report["run"]["climb_osc"] = {"freq_hz": float(fr[k_pk]),
                                  "omega_rad_s": float(2 * np.pi * fr[k_pk]),
                                  "pitch_std_rad": float(pit[s_cl].std())}

    # ════════════════ [2] 等效舵效辨识（三口径互证）════════════════
    ident = {}
    # [2a] 任务指令口径：数据流 wy 对 elev 的静态回归斜率 d(wy)/d(elev)
    for name in windows:
        s = wsel(name)
        ident.setdefault("static_wy_elev", {})[name] = {
            "slope_1_per_s": ols_slope(elv[s], wy[s]),
            "r": float(np.corrcoef(elv[s], wy[s])[0, 1]),
        }
    # 回路污染解释：现行律 elev 内含 +KP_R·PITCH_DAMP/|b_elev|·wy 的阻尼正馈项
    fb_term = FCC.KP_R * FCC.PITCH_DAMP  # 0.50 N·m/(rad/s)
    ident["static_wy_elev"]["feedback_path_note"] = {
        "elev_on_wy_coeff_sched": fb_term / abs(FCC.K_B_TAIL_ELEV
                                                * win_stats["climb"]["veff_median"] ** 2),
        "note": ("静态回归含反馈通道（elev 由含 wy 的闭环律生成），斜率符号为正"
                 "即由此而来；仅作指令口径记录与量级互证，不作设计对象增益"),
    }

    # [2b] 力矩级：尾翼俯仰力矩 My 对 elev 的回归（物理输出对物理输入，最干净）
    for name in ("climb", "flight"):
        s = wsel(name)
        sl_direct = ols_slope(elv[s], my[s])
        k_norm = ols_slope(elv[s] * veff[s] ** 2, my[s])
        ident.setdefault("moment_level", {})[name] = {
            "dMy_delev_Nm_per_rad": sl_direct,
            "k_eff_Nm_per_rad_per_v2": k_norm,
            "k_sched": FCC.K_B_TAIL_ELEV,
            "eff_over_sched": k_norm / FCC.K_B_TAIL_ELEV,
        }

    # [2c] 角加速度级：dwy/dt = c0 + m_q·wy + b_acc·elev（联合回归）
    grad_wy = np.gradient(wy, t)
    for name in ("climb", "flight"):
        s = wsel(name)
        n = s.stop - s.start
        X = np.column_stack([np.ones(n), boxsm(wy[s]), boxsm(elv[s])])
        yv = boxsm(grad_wy[s])
        beta, _, _, _ = np.linalg.lstsq(X, yv, rcond=None)
        r2 = 1 - ((yv - X @ beta) ** 2).sum() / ((yv - yv.mean()) ** 2).sum()
        ident.setdefault("accel_level", {})[name] = {
            "m_q_1_per_s": float(beta[1]), "b_acc_1_per_s2": float(beta[2]),
            "R2": float(r2),
            "note": ("闭环相关性使 wy 与 elev 回归量共线，m_q 受污染偏正；"
                     "b_acc 与力矩级斜率换算值一致（见 Iyy 互证）"),
        }

    # [2d] Iyy 等效值 = 力矩级斜率 / 角加速度级斜率（同窗互证）
    sl_f = ident["moment_level"]["flight"]["dMy_delev_Nm_per_rad"]
    b_f = ident["accel_level"]["flight"]["b_acc_1_per_s2"]
    iyy_equiv = sl_f / b_f
    # PhysX 惯量表交叉核（docs/INERTIA_TABLE_215g.md：各连杆 iyy 之和）
    iyy_physx_sum = 5.03e-3  # 4.765e-3(base)+2×6e-5+2×3e-5+2×1.2e-5+1e-5+2×8e-6
    # 运行窗口净阻尼（力矩级直接回归，含人工阻尼 −0.005 与尾翼速率阻尼）
    s_f = wsel("flight")
    net_damp_moment = ols_slope(wy[s_f], my[s_f])
    ident["iyy_and_damping"] = {
        "iyy_equiv_kg_m2": iyy_equiv,
        "iyy_physx_link_sum": iyy_physx_sum,
        "rel_diff": abs(iyy_equiv - iyy_physx_sum) / iyy_physx_sum,
        "net_dMy_dwy_run_Nm_s_per_rad": net_damp_moment,
        "net_run_1_per_s": net_damp_moment / iyy_equiv,
        "decomposition": {
            "artificial_run_-0.005": -WING_ROT_DAMP_RUN,
            "wing_physical_op2": D_PITCH_PHYS,
            "implied_tail_rate_damping": net_damp_moment + WING_ROT_DAMP_RUN - D_PITCH_PHYS,
            "frozen_lib_artificial_-0.1": -WING_ROT_DAMP_FROZEN,
            "note": ("净值 −0.013 ≈ 人工 −0.005 + 机翼物理 +0.0018 + 尾翼速率阻尼"
                     "（ implied ≈ −0.010，量级与 k_eff·(力臂/veff)≈−0.012 一致）"),
        },
    }
    report["ident"] = ident

    # ════════════════ [3] 设计模型与极点配置 ════════════════
    k_eff = ident["moment_level"]["flight"]["k_eff_Nm_per_rad_per_v2"]
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * 1.25
    veff_design = float(np.sqrt(3.5 ** 2 + v_slip ** 2))
    b_des = k_eff * veff_design ** 2 / iyy_equiv      # 1/s²，设计合流速度下的等效舵效
    m_q_des = D_PITCH_PHYS / iyy_equiv                # 1/s，指令口径：物理非恢复阻尼
    wn = WN
    zeta = ZETA
    wbw = wn * np.sqrt(1 - 2 * zeta ** 2 + np.sqrt(4 * zeta ** 4 - 4 * zeta ** 2 + 2))
    KP = wn ** 2 / abs(b_des)                         # rad/rad
    KD = (2 * zeta * wn + m_q_des) / abs(b_des)       # rad/(rad/s)
    poles = np.roots([1.0, 2 * zeta * wn, wn ** 2])
    design = {
        "veff_design": veff_design, "v_slip": v_slip,
        "k_eff_Nm_rad_v2": k_eff, "b_des_1_per_s2": b_des,
        "m_q_design_1_per_s": m_q_des, "m_q_source": "任务指令：+0.0018 N·m·s/rad（非恢复）",
        "zeta": zeta, "wn": wn, "wbw": wbw, "wbw_margin_vs_1p5": 1.5 - wbw,
        "KP_rad_per_rad": KP, "KD_rad_per_rad_s": KD,
        "law": "delta = KP*(theta - theta_des) + KD*q （正 elev=负俯仰力矩口径）",
        "closed_loop_poles": cplx(poles),
        # 结构等价值（现行调度 K_B_TAIL_ELEV 不变前提下的 KP_A/PITCH_DAMP）
        "struct_keep_sched": {
            "KP_A_eq": wn ** 2 * iyy_equiv * abs(FCC.K_B_TAIL_ELEV) / (abs(k_eff) * FCC.KP_R),
            "PITCH_DAMP_eq": (2 * zeta * wn + m_q_des) * iyy_equiv
                             * abs(FCC.K_B_TAIL_ELEV) / (abs(k_eff) * FCC.KP_R),
            "note": "保持 K_B_TAIL_ELEV=-0.0145 调度不变时，KP_A/PITCH_DAMP 的等效取值",
        },
        "struct_fix_sched": {
            "KP_A_eq": wn ** 2 * iyy_equiv / FCC.KP_R,
            "PITCH_DAMP_eq": (2 * zeta * wn + m_q_des) * iyy_equiv / FCC.KP_R,
            "note": "若把 K_B_TAIL_ELEV 改为辨识值（契约层，须批准）后的结构常量",
        },
    }
    report["design"] = design

    # ════════════════ [4] 与现行增益对比 ════════════════
    b_sched_abs = abs(FCC.K_B_TAIL_ELEV) * veff_design ** 2
    KP_cur = FCC.KP_R * FCC.KP_A / b_sched_abs
    KD_cur = FCC.KP_R * FCC.PITCH_DAMP / b_sched_abs
    wn_cur = float(np.sqrt(abs(b_des) * KP_cur))
    zeta_cur = (abs(b_des) * KD_cur - m_q_des) / (2 * wn_cur)
    comp = {
        "at_veff_design": veff_design,
        "KP_cur": KP_cur, "KD_cur": KD_cur,
        "KP_ratio_cur_over_new": KP_cur / KP,
        "KD_ratio_cur_over_new": KD_cur / KD,
        "cur_struct": {"KP_A": FCC.KP_A, "KP_R": FCC.KP_R,
                       "PITCH_DAMP": FCC.PITCH_DAMP, "KI_A": FCC.KI_A,
                       "TILT_MAX": FCC.TILT_MAX, "PITCH_TRIM": FCC.PITCH_TRIM},
        "cur_linear_poles_on_identified_plant": {
            "wn_cur": wn_cur, "zeta_cur": zeta_cur,
            "note": "现行增益在辨识对象上的线性极点（若未饱和）",
        },
        "saturation_quant": {
            "q_sat_cur": DELTA_MAX / KD_cur,
            "e_sat_cur": DELTA_MAX / KP_cur,
            "q_sat_new": DELTA_MAX / KD,
            "e_sat_new": DELTA_MAX / KP,
            "observed_climb": {"wy_absmax": win_stats["climb"]["wy_absmax"],
                               "e_absmax": float(np.abs(pdes[s_cl] - pit[s_cl]).max())},
            "note": ("现行 KD 下 0.11 rad/s 角速率即顶满舵偏；爬升段实测 |wy| 达 "
                     f"{win_stats['climb']['wy_absmax']:.2f} rad/s，为该门限的 "
                     f"{win_stats['climb']['wy_absmax'] / (DELTA_MAX / KD_cur):.0f} 倍——"
                     "95% bang-bang 的机理量化"),
        },
        "tilt_trim_conflict": {
            "PITCH_TRIM_374_vs_TILT_MAX_035": True,
            "note": ("新工作点配平俯仰 0.374 rad 超过现行 TILT_MAX=0.35，pitch_tgt "
                     "在配平点即被削顶；5c 在环曾放宽至 0.55（已随失败还原）。"
                     "改 TILT_MAX 属契约层，须用户批准"),
        },
    }
    # KI_A 等比建议（保持与 KP_A 同比，积分时间常数不变）
    comp["KI_A_eq_scaled"] = FCC.KI_A / comp["KP_ratio_cur_over_new"]
    report["compare"] = comp

    # 鲁棒性网格：舵效比 ρ=k_real/k_nom 与 m_q_real 扫描，固定新增益
    grid = []
    for rho in (0.5, 0.67, 1.0, 1.5, 2.0):
        for mq in (-22.0, -2.58, m_q_des, 1.0):
            b_r = b_des * rho
            # 结构增益经调度后等效 A_eff=KP·(b_r/…)→直接用特征方程：
            # s² + (|b_r|·KD − m_q_real)·s + |b_r|·KP = 0
            p = np.roots([1.0, abs(b_r) * KD - mq, abs(b_r) * KP])
            grid.append({"rho": rho, "m_q_real": mq,
                         "max_real": float(max(v.real for v in p)),
                         "poles": cplx(p)})
    comp["robustness_grid"] = grid
    comp["robustness_all_stable"] = all(g["max_real"] < 0 for g in grid)
    comp["robustness_min_damping_corner"] = min(grid, key=lambda g: g["max_real"])

    # ════════════════ [5] 升降舵 RMS 权限预算 ════════════════
    budget = {"threshold_rad": RMS_FRAC_MAX * DELTA_MAX,
              "threshold_frac": RMS_FRAC_MAX}
    # [5a] 轨迹反演：第四跑记录的 (θ_des, θ, wy) 过新律（q 经 τ=0.08 EWMA，
    #      与控制器 RATE_FILT_TAU 同口径）
    tau = FCC.RATE_FILT_TAU
    for name in ("climb", "flight", "free"):
        s = wsel(name)
        n = s.stop - s.start
        q = wy[s]
        qf = np.empty(n)
        qf[0] = q[0]
        for k in range(1, n):
            a = min(1.0, (t[s][k] - t[s][k - 1]) / tau)
            qf[k] = qf[k - 1] + a * (q[k] - qf[k - 1])
        d = KP * (pit[s] - pdes[s]) + KD * qf
        rms = float(np.sqrt((d * d).mean()))
        budget.setdefault("replay", {})[name] = {
            "n": n, "rms_rad": rms, "rms_frac_full": rms / DELTA_MAX,
            "absmax_rad": float(np.abs(d).max()),
            "pass": rms <= budget["threshold_rad"],
        }
    budget["replay"]["note"] = ("记录轨迹由 bang-bang 律生成，反演值为新律需求的"
                                "偏保守上界；free 段含 4.61 s 掠地后翻滚（wy 达 "
                                "101 rad/s），不属发射爬升段，判据按 climb/flight 裁定")

    # [5b] 设计模型线性发射阶跃仿真（15 s 视界；e_θ0 取释放实测 0.14 与
    #      姿态同步失效的最坏 0.436，q0 取爬升段实测 −1.7 rad/s）
    # 状态：x1=θ−θ_des（θ_des 常值 → x1_dot=+q）、x2=q；
    # 对象：x2_dot = m_q·x2 + b_des·δ（b_des 为负，正 elev=负俯仰力矩）；
    # 控制律：δ = KP·x1 + KD·x2（KP、KD 为正，见符号约定）。
    def lin_step(e0, q0, T=T_LAUNCH, dt=0.005):
        x1, x2 = e0, q0
        peak, m2 = 0.0, 0.0
        for _ in range(int(T / dt)):
            d = KP * x1 + KD * x2
            peak = max(peak, abs(d))
            m2 += d * d * dt
            qd = m_q_des * x2 + b_des * d
            x1n = x1 + x2 * dt          # x1_dot = q
            x2 = x2 + qd * dt
            x1 = x1n
        return peak, float(np.sqrt(m2 / T))

    budget["linear_step"] = {}
    for (e0, q0) in ((0.436, 0.0), (0.140, -1.7), (0.436, -1.7)):
        pk, rm = lin_step(e0, q0)
        budget["linear_step"][f"e0={e0}_q0={q0}"] = {
            "peak_rad": pk, "peak_frac": pk / DELTA_MAX,
            "rms_rad": rm, "rms_frac": rm / DELTA_MAX,
            "pass": rm <= budget["threshold_rad"]}
    report["budget"] = budget

    # ════════════════ [6] 横航向 2×2 MIMO ════════════════
    with open(DAMP_JSON, encoding="utf-8") as fh:
        dj = json.load(fh)
    M = np.array(dj["matrix_05"])          # 3×3（行=力矩轴，列=角速度轴）
    D_lat = M[np.ix_([0, 2], [0, 2])]      # roll/yaw 2×2
    d11, d12 = float(D_lat[0, 0]), float(D_lat[0, 1])
    d21, d22 = float(D_lat[1, 0]), float(D_lat[1, 1])
    eig = np.linalg.eigvals(D_lat)
    b_ail = FCC.K_B_TAIL_AIL * veff_design ** 2
    b_rud = abs(FCC.K_B_TAIL_RUD) * veff_design ** 2
    mimo = {
        "D_lat_Nm_s_per_rad": D_lat.tolist(),
        "source": "logs/scan_rot_damping_op2.json matrix_05（行 roll,yaw × 列 p,r）",
        "eigen_moment_level": cplx(eig),
        "gershgorin": {
            "row_roll_dominant": abs(d12) <= abs(d11),
            "row_yaw_dominant": abs(d21) <= abs(d22),
            "col_p_dominant": abs(d21) <= abs(d11),
            "col_r_dominant": abs(d12) <= abs(d22),
        },
        "coupling_ratios": {"|D12|/|D11|_rollrow": abs(d12 / d11),
                            "|D21|/|D22|_yawrow": abs(d21 / d22)},
        "normalized_1_per_s": {
            "note": "除以 PhysX 锚定惯量估计 Ixx≈5.0e-3、Izz≈8.0e-3 kg·m²",
            "roll_row": [d11 / 5.0e-3, d12 / 5.0e-3],
            "yaw_row": [d21 / 8.0e-3, d22 / 8.0e-3],
            "dutch_roll_like": {"sigma_1_per_s": [eig[0].real / 5.0e-3, eig[0].real / 8.0e-3],
                                "omega_1_per_s": [abs(eig[0].imag) / 5.0e-3,
                                                  abs(eig[0].imag) / 8.0e-3],
                                "zeta": float(-eig[0].real / abs(eig[0]))},
        },
        "current_diag_feedback_Nm_s_per_rad": {
            "roll": FCC.KP_R, "yaw": FCC.KP_R * FCC.YAW_DAMP,
            "note": "mx=KP_R·(wxd−wx)、mz=KP_R·(0−YAW_DAMP·wz) 的力矩级系数",
        },
        "decoupler": {
            "cancel_D12_into_roll": {"moment_Nm_s_per_rad": -d12,
                                     "aileron_rad_per_rad_s": (-d12) / b_ail,
                                     "yaw_rate_full_coverage_rad_s": DELTA_MAX * b_ail / (-d12)},
            "cancel_D21_into_yaw": {"moment_Nm_s_per_rad": -d21,
                                    "rudder_rad_per_rad_s": (-d21) / b_rud,
                                    "roll_rate_full_coverage_rad_s": DELTA_MAX * b_rud / abs(d21)},
            "diag_augment_recommend": {
                "g_p_add_Nm_s_per_rad": 0.03, "g_r_add_Nm_s_per_rad": 0.02,
                "note": ("对角速率阻尼建议增量（力矩级量级即可）：滚转气动阻尼"
                         "非线性塌缩（op2 线性段不成立，5 rad/s 处斜率 −0.0002），"
                         "速率反馈须承担全部滚转阻尼")},
        },
        "authority_context": {
            "tail_aileron_full_Nm": b_ail * DELTA_MAX,
            "coupling_moment_per_rad_s_yaw": d12,
            "wing_diff_rated_Nm_oldop": -0.0598,
            "note": ("尾翼差动满偏力矩 0.018 N·m 小于 1 rad/s 偏航率注入的耦合矩 "
                     "0.023 N·m：单靠尾翼差动无法完全解耦，需机翼差动作第二滚转"
                     "权限（旧工作点额定 −0.0598 N·m，新点 SPLIT=0.75 待测）"),
        },
        "frozen_damping_context": {
            "note": ("冻结库 WING_ROT_DAMP=0.1 人工阻尼主导时（对角 −0.10 量级），"
                     "耦合矩阵特征值实部被压至约 −0.105 N·m·s/rad，横航向模态"
                     "人工过阻尼、耦合主要表现为滚转通道扰动而非失稳；本节建议"
                     "面向物理阻尼（人工阻尼减免后）口径"),
        },
    }
    report["mimo"] = mimo

    # ════════════════ [7] 判据核对（判据先于数据）════════════════
    crit = {
        "c1_poles_neg_real": {
            "target_poles": cplx(poles),
            "robustness_grid_all_stable": comp["robustness_all_stable"],
            "worst_corner": comp["robustness_min_damping_corner"],
            "pass": bool(max(p.real for p in poles) < 0 and comp["robustness_all_stable"]),
        },
        "c2_rms_budget": {
            "threshold_rad": budget["threshold_rad"],
            "climb": budget["replay"]["climb"],
            "flight": budget["replay"]["flight"],
            "linear_step_worst": max(budget["linear_step"].items(),
                                     key=lambda kv: kv[1]["rms_rad"]),
            "coverage_note": (f"第四跑仅提供 {windows['flight'][0]:.2f}–"
                              f"{windows['flight'][1]:.2f} s 飞行数据（{windows['flight'][1]-windows['flight'][0]:.1f} s），"
                              f"15 s 视界以该段反演+线性仿真覆盖"),
        },
    }
    crit["c2_rms_budget"]["pass"] = bool(
        crit["c2_rms_budget"]["climb"]["pass"] and
        crit["c2_rms_budget"]["flight"]["pass"] and
        all(v["pass"] for v in budget["linear_step"].values()))
    report["criteria"] = crit

    # ════════════════ 输出 ════════════════
    import datetime
    report["ts"] = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    # 控制台摘要
    print("=" * 72)
    print("[任务二] 巡航段（新工作点）俯仰/横航向增益整定分析")
    print("=" * 72)
    print(f"设计点 veff={veff_design:.3f} m/s  k_eff={k_eff:.5f} N·m/(rad·(m/s)²) "
          f"({abs(k_eff)/abs(FCC.K_B_TAIL_ELEV)*100:.0f}% 调度值)  "
          f"b_des={b_des:.1f} 1/s²  Iyy_equiv={iyy_equiv*1e3:.2f}e-3 kg·m²")
    print(f"指令口径 d(wy)/d(elev) 静态回归（爬升段）="
          f"{ident['static_wy_elev']['climb']['slope_1_per_s']:+.2f} 1/s "
          f"(r={ident['static_wy_elev']['climb']['r']:+.2f}，含反馈通道污染)")
    print(f"极点配置：ζ={zeta} ω_n={wn} → ω_bw={wbw:.3f} rad/s (≤{WBW_MAX})  "
          f"极点 {poles[0]:.3f}")
    print(f"新增益：KP={KP:.4f} rad/rad  KD={KD:.4f} rad/(rad/s)")
    print(f"现行等效：KP_cur={KP_cur:.3f}  KD_cur={KD_cur:.3f} → "
          f"现行/新 = {comp['KP_ratio_cur_over_new']:.1f}x / {comp['KD_ratio_cur_over_new']:.1f}x")
    print(f"结构常量（保调度）：KP_A≈{design['struct_keep_sched']['KP_A_eq']:.4f} "
          f"(现行 {FCC.KP_A})；PITCH_DAMP≈{design['struct_keep_sched']['PITCH_DAMP_eq']:.4f} "
          f"(现行 {FCC.PITCH_DAMP})；KI_A 等比≈{comp['KI_A_eq_scaled']:.4f}")
    print(f"饱和门限：现行 q_sat={comp['saturation_quant']['q_sat_cur']:.3f} rad/s "
          f"(实测 |wy|max={win_stats['climb']['wy_absmax']:.2f})；"
          f"新 q_sat={comp['saturation_quant']['q_sat_new']:.2f} rad/s")
    for name in ("climb", "flight"):
        b_ = budget["replay"][name]
        print(f"RMS 反演[{name}] = {b_['rms_rad']:.4f} rad "
              f"({b_['rms_frac_full']*100:.1f}% 满偏，门限 70%) → "
              f"{'PASS' if b_['pass'] else 'FAIL'}")
    for k, v in budget["linear_step"].items():
        print(f"线性阶跃[{k}] peak={v['peak_rad']:.4f} rms15s={v['rms_rad']:.4f} → "
              f"{'PASS' if v['pass'] else 'FAIL'}")
    print(f"鲁棒性格网（ρ 0.5–2.0 × m_q −22–+1）全部稳定："
          f"{comp['robustness_all_stable']}")
    print(f"判据1（极点实部均负）：{'PASS' if crit['c1_poles_neg_real']['pass'] else 'FAIL'}")
    print(f"判据2（RMS≤70%满偏）：{'PASS' if crit['c2_rms_budget']['pass'] else 'FAIL'}")
    print(f"MIMO：D_lat={D_lat.tolist()}  特征值 {cplx(eig)}")
    print(f"      Gershgorin 行占优 roll={'是' if mimo['gershgorin']['row_roll_dominant'] else '否'}"
          f"/yaw={'是' if mimo['gershgorin']['row_yaw_dominant'] else '否'}  "
          f"|D12|/|D11|={abs(d12/d11):.1f}")
    print(f"      解耦前置：r→roll 抵消需 {(-d12)/b_ail:.2f} rad 副翼/(rad/s) "
          f"(满偏覆盖 {DELTA_MAX/((-d12)/b_ail):.2f} rad/s 偏航率)；"
          f"p→yaw 抵消需 {(-d21)/b_rud:.3f} rad 方向舵/(rad/s)")
    print(f"输出：{os.path.relpath(OUT_JSON, ROOT)}")


if __name__ == "__main__":
    main()
