# -*- coding: utf-8 -*-
"""
feasibility_map.py — 多模态可行域边界扫描框架（第三十七班）
================================================================
方法论（用户 2026-09-25 指令，替代/升级单点寻优）：
  模态 = 三元组（工况、判据函数、输入）。四模态并行网格扫描，
  判据布尔归类 + 边界提取（可行域最大内切球 = 鲁棒半径），
  最后求四模态交集。变量 = 丙组九控制参数 + 波形五参数容差方向
  （波形本身固定为甲组签核值，只扫其 ±5%/±10% 容差的可吸收度）。

四模态：
  M1 定高定速巡航：判据 = 60 s 高度 3±1 m、俯仰达 0.374±0.05、
     拍频 1.25±0.15、升降舵 RMS≤满偏 70%；
  M2 压坡度转弯：坡度指令 10°/15° 两档，判据 = 转弯率跟踪协调值
     ±15%、高度 3±1 m 保持；
  M3 控制下降：下降率 −0.5~−1.0 m/s 带内跟踪；
  M4 起飞：输入 = 发射速度 3.5~6.5 m/s × 发射俯仰 0~0.7 rad，
     判据 = 3 s 内不坠且爬升率为正（预期空域，量化最近边界距离）。

被控对象 = 离线降阶闭环模型，原料全部出自在档辨识（见 PROVENANCE）：
  俯仰共振子 1.15 Hz / ζ 0.07（第九跑回归，docs/三维模型在环确认_
  第五步升格.md §第九跑一.1）、∂Fz/∂f=−0.0457、∂Fx/∂f=−0.4495、
  ∂Fz/∂V=+1.244（tools/scan_dfzdf_op2.py，docs 同上 §第七跑一）、
  舵效 0.49 N·m/rad、舵偏限幅 0.30 rad（docs/全参数决断总表.md 甲组
  签核值）、拍频扰动矩谱（第八跑数据流谱投影，本文件 PROVENANCE）。
  配平点力值与 ∂/∂θ、∂/∂vz、波形容差敏感度 = 本班离线探针
  （sane_dickinson_3d 静态整周期平均，n=288）。

控制律 = modules/flight_controller.py 的降阶复刻（第九跑口径，
  单段路径 + TECS 巡航分支），九参数为自由变量，其余结构常数取
  第九跑在环值。符号约定：正俯仰力矩 = 抬头；elev<0 = 抬头舵。

鲁棒半径度量：参数按各自半范围 R_j 归一化到 u_j∈[−1,1]，内切球
  取无穷范数度量的切球（radius = min_j margin_j），逐轴边界由符号
  变化线性插值提取。

用法： python tools/feasibility_map.py [--smoke]
产物： logs/feasibility_map.json（+ 控制台摘要）
纪律： 只读现有文件；不修改任何配置；不启动 Isaac。
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

import multiprocessing as mp

import numpy as np

# ══════════════════════════════════════════════════════════════════
# 一、对象原料（全部在档，出处见 PROVENANCE）
# ══════════════════════════════════════════════════════════════════
G_ACC = 9.81
MASS = 0.215                      # kg（config/sim_config.py MASS）
WEIGHT = MASS * G_ACC             # 2.10915 N

# 俯仰共振子（1.15 Hz / ζ 0.07）+ Iyy（第九跑辨识 0.00516067 kg·m²）
IYY = 0.00516067                  # kg·m²
WN_P = 2.0 * math.pi * 1.15       # rad/s
ZETA_P = 0.07
K_TH = IYY * WN_P ** 2            # 0.26966 N·m/rad 俯仰刚度（回归 −0.268）
C_Q = 2.0 * ZETA_P * WN_P * IYY   # 0.005216 N·m·s/rad 俯仰阻尼（回归 −0.0051）

# 升降舵（用户指令原料：舵效 0.49 N·m/rad、限幅 0.30 rad）
B_ELEV_PLANT = 0.49               # N·m/rad 被控对象侧舵效（决断总表甲组）
DELTA_MAX = 0.30                  # rad 舵偏限幅（第九跑口径）

# 尾舵调度（config K_B_TAIL_*，第九跑口径；控制器侧，非对象侧）
K_TAIL_ELEV = 0.0145              # N·m/(rad·(m/s)²)（取幅值；符号进公式）
K_TAIL_AIL = 0.00483
K_TAIL_RUD = 0.0145

# 纵向力图（配平点 V=3.5、f=1.25、θ=21.45°、vz=0 的周期平均）
PITCH_TRIM = 0.374                # rad 配平俯仰
FREQ_TRIM = 1.25                  # Hz 配平拍频
V_TRIM = 3.5                      # m/s 配平速度
FZ0 = 2.08116                     # N（Fz/W=0.9867，"配平承诺~0.99"）
FX0 = -0.00505                    # N（Fx=0 配平）
DFZ_DF = -0.0457                  # N/Hz（用户原料）
DFX_DF = -0.4495                  # N/Hz（用户原料）
DFZ_DV = +1.244                   # N/(m/s)（用户原料）
DFZ_DTH = -0.4866                 # N/rad（本班探针）
DFX_DTH = +1.8213                 # N/rad（本班探针）
DFX_DV = +0.1189                  # N/(m/s)（本班探针）
DFZ_DVZ = -0.2026                 # N/(m/s)（本班探针：下沉增升力）
DFX_DVZ = -0.1616                 # N/(m/s)（本班探针）
MY_DC = -0.0399                   # N·m 机翼+驱动矩直流偏置（第八跑谱）
DMY_DVZ = -0.0711                 # N·m/(m/s)（本班探针）

# 拍频扰动矩谱（第八跑飞行窗谱线投影，幅值 N·m / 相位 deg）
DIST_LINES = ((0.62, 0.01930, 84.0),
              (1.18, 0.01240, -16.0),
              (2.36, 0.00333, -26.5))

# 横侧向（logs/scan_rot_damping_op2.json matrix_05 + 惯量估计）
IXX = 0.0050                      # kg·m²（synth_cruise_gains 归一化注记）
IZZ = 0.0080                      # kg·m²
D_LAT = ((-0.0023916, -0.0233272),
         (+0.0036580, -0.0076074))   # 行 roll,yaw × 列 p,r N·m·s/rad
TAU_PSI = 1.5                     # s 机体系跟踪航迹转向率的一阶滞后
                                   # （横航向荷兰滚类模态 σ≈−0.62~−1.0 1/s，
                                   #   synth_cruise_gains mimo.normalized_1_per_s）
# 翼差动滚转通道（config K_DA/K_DA_R/DA_MAX/B_DA_*，结构常数）
K_DA, K_DA_R, DA_MAX = 0.04, 0.008, 0.08
B_DA_SLOPE, B_DA_REF = 0.0169, 0.0406

# 波形五参数 ±5%/±10% 容差敏感度（本班探针，dFz/dFx N、dMy N·m）
WAVE_NOM = {"alpha_set": 35.0, "mean_deg": 20.0, "split": 0.75,
            "pitch_lag_s": -0.04, "amplitude_deg": 30.0}
WAVE_TOL = {
    "alpha_set":   {"+5%": (0.01123, 0.02757, 0.00646), "-5%": (-0.00970, -0.02639, -0.00643),
                    "+10%": (0.02323, 0.05621, 0.01293), "-10%": (-0.01681, -0.05149, -0.01279)},
    "mean_deg":    {"+5%": (0.00860, 0.01089, -0.00456), "-5%": (-0.00895, -0.01069, 0.00451),
                    "+10%": (0.01687, 0.02198, -0.00916), "-10%": (-0.01819, -0.02118, 0.00896)},
    "split":       {"+5%": (0.00656, -0.04682, -0.01044), "-5%": (-0.00281, 0.03394, 0.00904),
                    "+10%": (0.01593, -0.11531, -0.02314), "-10%": (-0.01209, 0.05846, 0.01718)},
    "pitch_lag_s": {"+5%": (-0.00525, -0.00166, -0.00184), "-5%": (0.00520, 0.00164, 0.00181),
                    "+10%": (-0.01018, -0.00324, -0.00370), "-10%": (0.01043, 0.00327, 0.00358)},
    "amplitude_deg": {"+5%": (0.00941, -0.00336, 0.00160), "-5%": (-0.00933, 0.00372, -0.00168),
                      "+10%": (0.01883, -0.00637, 0.00313), "-10%": (-0.01851, 0.00782, -0.00344)},
}

# 控制器结构常数（第九跑在环口径，非九参数部分固定）
KP_R = 0.25                       # 角速度内环
KI_A_MAX = 2.5                    # rad·s 积分状态限幅
TILT_MAX = 0.55                   # rad 期望姿态限幅
FREQ_AP_MIN, FREQ_MAX_AP = 1.0, 3.5
V_CMD_MIN = 3.0
FREQ_SLEW = 2.5                   # Hz/s
PITCH_DES_SLEW = 1.2              # rad/s
OUTER_FILT_TAU = 0.08             # s 外环速度 EWMA
TECS_ZV_K = 0.30                  # (m/s)/m 高度→速度基准
TECS_KP_E = 0.004                 # rad/(J/kg)
TECS_KD_V = 0.02                  # rad/(m/s)
V_PITCH_FREE, V_PITCH_LOCK, V_DIVE_ALLOW = 3.5, 3.0, 0.08
YAW_DAMP = 1.0
SLIP_PER_HZ = 0.472               # m/s/Hz（×SLIP_K=1.0）
VEFF_CAP = 8.0

# 丙组九参数：名称、第九跑现值、扫描半范围 R（7 点网格含端点）
NINE = [
    ("ALPHA_FF_GAIN", 0.0,   0.30),
    ("TECS_KF_P",    -0.03,  0.06),
    ("TECS_KF_I",    -0.010, 0.020),
    ("TECS_KV_F",    -1.0,   2.0),
    ("KP_A",          0.40,  0.45),
    ("PITCH_DAMP",    0.30,  0.375),
    ("KI_A",          0.033, 0.067),
    ("INTEG_GATE",    0.25,  0.25),
    ("RATE_FILT_TAU", 0.08,  0.04),
]
NOMINAL = {k: v for k, v, _ in NINE}
RANGE = {k: r for k, _, r in NINE}
AXIS_VALUES = {}
for _k, _v, _r in NINE:
    AXIS_VALUES[_k] = [_v + _r * s for s in (-1.0, -2/3, -1/3, 0.0, 1/3, 2/3, 1.0)]

DT = 0.004                        # s 积分步长（250 Hz，扰动谱最高 2.36 Hz 的 42 倍）
T_SIM = 60.0                      # s 主仿真视界（M4 为 3 s）
Z_CRASH = 0.2                     # m 坠机判定（判据一口径）

PROVENANCE = {
    "pitch_resonator": "1.15 Hz / ζ0.07：第九跑回归（docs/三维模型在环确认_第五步升格.md §第九跑一.1，净刚度 −0.268 N·m/rad、净阻尼 −0.0051 N·m·s/rad 与本常量 K_TH/C_Q 一致到 1%）",
    "IYY": "logs/synth_cruise_gains.json ident.iyy_and_damping（力矩级/角加速度级互证 0.0051607）",
    "dfz_df_dfz_dv_dfx_df": "tools/scan_dfzdf_op2.py（docs/三维模型在环确认_第五步升格.md §第七跑一，n=288 静态周期平均）",
    "B_elev_delta": "docs/全参数决断总表.md 甲组（尾翼舵效 0.49 N·m/rad、限幅 0.30 rad，已签核）",
    "dist_lines": "第八跑数据流 logs/ident_stream_flight_0925_165356.csv 飞行窗（1.0~6.158 s）谱线投影（第三十七班提取）",
    "trim_and_theta_vz_slopes": "第三十七班离线探针：sane_dickinson_3d 于 (V=3.5, f=1.25, θ=21.45°) 静态整周期平均（n=288，时间基单调/显式设频纪律）",
    "wave_tol": "同上探针，五参数各 ±5%/±10%（相对名义值），记录 (ΔFz, ΔFx, ΔMy)",
    "lateral": "logs/scan_rot_damping_op2.json matrix_05（行 roll,yaw × 列 p,r）+ 惯量估计 Ixx≈5.0e-3 / Izz≈8.0e-3",
    "controller_replica": "modules/flight_controller.py 第九跑单段路径 + TECS 巡航分支的结构复刻；结构常数取第九跑在环值",
    "note_b_elev": "飞行窗回归 dMy/delev≈−0.13 N·m/rad（synth_cruise_gains.json）与甲组签核舵效 0.49 存在口径差；本框架按用户指令采用 0.49，敏感性对照见 JSON sensitivity_b_elev",
}


# ══════════════════════════════════════════════════════════════════
# 二、降阶闭环仿真核
# ══════════════════════════════════════════════════════════════════
def simulate(p, mode, input_spec=None, t_sim=T_SIM, collect=True):
    """一次闭环仿真。p=九参数 dict；mode∈{cruise,turn,descend,launch}；
    input_spec：turn=(phi_cmd_deg,)；launch=(v0,theta0)；其余 None。
    返回 (verdict, metrics, series)。series 仅 collect=True 时保留。"""
    kp_a, pdamp, ki_a = p["KP_A"], p["PITCH_DAMP"], p["KI_A"]
    gate, tau = p["INTEG_GATE"], p["RATE_FILT_TAU"]
    kfp, kfi, kvf, aff = p["TECS_KF_P"], p["TECS_KF_I"], p["TECS_KV_F"], p["ALPHA_FF_GAIN"]
    d_fz = d_fx = d_my = 0.0
    tol_key = (input_spec or {}).get("wave_tol") if isinstance(input_spec, dict) else None
    if tol_key:
        name, sgn = tol_key
        d_fz, d_fx, d_my = WAVE_TOL[name][sgn]

    # ── 初始状态 ──
    if mode == "launch":
        v0, th0 = input_spec["v0"], input_spec["theta0"]
        z, vz, vx, th, q = 3.0, 0.0, v0, th0, 0.0
        th_cmd_launch = th0
    else:
        z, vz, vx, th, q = 3.0, 0.0, V_TRIM, PITCH_TRIM, 0.0
        th_cmd_launch = None
    phi, pq, rr = 0.0, 0.0, 0.0                    # 横侧向（M2 用）
    phi_cmd = 0.0
    phi_cmd_settle_t = 5.0
    # ── 控制器状态 ──
    vxf = vx
    freq = FREQ_TRIM
    ei = 0.0
    pitch_i = 0.0
    pd_last = th
    qf = 0.0
    wxf = 0.0
    z_ref = 3.0
    # ── 记录 ──
    n_steps = int(round(t_sim / DT))
    ser = {"t": [], "z": [], "vz": [], "vx": [], "th": [], "freq": [],
           "elev": [], "phi": [], "r": []} if collect else None
    crashed_at = None
    rms_elev_sq, rms_n = 0.0, 0
    z_min = z
    # 判据统计窗
    in_band_z = in_band_f = in_trim_p = 0
    n_win = 0
    vz_band_n = 0
    turn_ok_n = 0
    turn_n = 0
    vz_late = []
    vz_late_sum, vz_late_n = 0.0, 0
    turn_ratio = []

    for i in range(n_steps):
        t = i * DT
        # ── 模态工况调度 ──
        if mode == "launch":
            freq = FREQ_TRIM                        # FSM：拍频锁 1.25
            pitch_tgt = th_cmd_launch               # 俯仰保持锁定律
        else:
            if mode == "descend":
                # 下降率指令：t=10 s 起 3 s 内线性升至 −0.75 m/s（判据带
                # −0.5~−1.0 的中值），z_ref 降至 0.8 m 后保持。稳态下降窗约
                # 2 s（受巡航高度 3 m 与地面余量限制，记入报告局限）
                if t >= 10.0:
                    rate = -0.75 * min(1.0, (t - 10.0) / 3.0)
                    z_ref = max(0.8, z_ref + rate * DT)
            vxd = V_TRIM - TECS_ZV_K * (z - z_ref)
            vxd = min(max(vxd, max(V_CMD_MIN, 0.5 * V_TRIM)), 1.5 * V_TRIM)
            e_pot = G_ACC * (z_ref - z)
            e_kin = 0.5 * (vxd * vxd - vxf * vxf)
            e_E = e_pot + e_kin
            e_B = min(max(e_pot - e_kin, -60.0), 60.0)
            if FREQ_AP_MIN * 1.01 < freq < FREQ_MAX_AP * 0.99:
                ei = min(max(ei + e_E * DT, -60.0), 60.0)
            freq_new = FREQ_TRIM + kfp * e_E + kfi * ei + kvf * (vxd - vxf)
            freq_new = min(max(freq_new, FREQ_AP_MIN), FREQ_MAX_AP)
            freq_new = min(max(freq_new, freq - FREQ_SLEW * DT),
                           freq + FREQ_SLEW * DT)
            freq = freq_new
            pitch_tgt = PITCH_TRIM + TECS_KP_E * e_B - TECS_KD_V * vz
            pitch_tgt = min(max(pitch_tgt, -TILT_MAX), TILT_MAX)
            if pitch_tgt < PITCH_TRIM:
                fade = min(max((vxf - V_PITCH_LOCK) / (V_PITCH_FREE - V_PITCH_LOCK), 0.0), 1.0)
                pitch_tgt = max(pitch_tgt, PITCH_TRIM - (fade * TILT_MAX + (1.0 - fade) * V_DIVE_ALLOW))
        if mode == "turn" and t >= phi_cmd_settle_t:
            phi_cmd = math.radians(input_spec["phi_cmd_deg"])

        # ── 指令斜坡 + 姿态外环 ──
        dp = min(max(pitch_tgt - pd_last, -PITCH_DES_SLEW * DT), PITCH_DES_SLEW * DT)
        pd_last += dp
        perr = pd_last - th
        if abs(perr) < gate:
            pitch_i = min(max(pitch_i + perr * DT, -KI_A_MAX), KI_A_MAX)
        wyd = kp_a * perr + ki_a * pitch_i
        qf += min(1.0, DT / max(tau, 1e-3)) * (q - qf)
        my_dem = KP_R * (wyd - pdamp * qf)

        # ── 尾舵调度 + 攻角前馈 ──
        v_slip = SLIP_PER_HZ * freq
        veff = min(VEFF_CAP, math.sqrt(max(vx * vx, 0.0) + v_slip * v_slip))
        b_sched = K_TAIL_ELEV * veff * veff
        sb, cb = math.sin(th), math.cos(th)
        vbx = cb * vxf + sb * vz + v_slip
        vbz = sb * vxf + cb * vz
        alpha_hat = math.atan2(vbz, max(vbx, 0.1))
        elev = -my_dem / max(b_sched, 1e-6) - aff * alpha_hat
        elev = min(max(elev, -DELTA_MAX), DELTA_MAX)

        # ── 横侧向（M2）──
        if mode == "turn":
            wxd = kp_a * (phi_cmd - phi)
            wxf += min(1.0, DT / max(tau, 1e-3)) * (pq - wxf)
            mx = KP_R * (wxd - wxf)                 # 尾翼差动力矩需求
            b_ail = K_TAIL_AIL * veff * veff
            ail = min(max(mx / max(b_ail, 1e-6), -DELTA_MAX), DELTA_MAX)
            b_da = B_DA_SLOPE * freq * freq
            da = ((K_DA * (phi_cmd - phi) - K_DA_R * wxf) * B_DA_REF
                  / max(b_da, 1e-6))
            da = min(max(da, -DA_MAX), DA_MAX)
        # ── 对象 ──
        dth = th - PITCH_TRIM
        fz = (FZ0 + DFZ_DV * (vx - V_TRIM) + DFZ_DF * (freq - FREQ_TRIM)
              + DFZ_DTH * dth + DFZ_DVZ * vz + d_fz - WEIGHT)
        fx = (FX0 + DFX_DV * (vx - V_TRIM) + DFX_DF * (freq - FREQ_TRIM)
              + DFX_DTH * dth + DFX_DVZ * vz + d_fx)
        if mode == "turn":
            fz *= math.cos(phi)
            fx *= math.cos(phi)
        m_total = (-K_TH * dth - C_Q * q + MY_DC + DMY_DVZ * vz + d_my
                   - B_ELEV_PLANT * elev)
        for _f, _a, _ph in DIST_LINES:
            m_total += _a * math.sin(2.0 * math.pi * _f * t + math.radians(_ph))
        # 状态积分（显式欧拉）
        vz += fz / MASS * DT
        vx += fx / MASS * DT
        q += m_total / IYY * DT
        th += q * DT
        z += vz * DT
        if mode == "turn":
            m_roll = (D_LAT[0][0] * pq + D_LAT[0][1] * rr
                      + b_ail * ail + b_da * da)
            pq += m_roll / IXX * DT
            # 机体系偏航率向航迹转向率一阶收敛（协调转弯运动学闭合：
            # 坡度提供的横向升力分量 = 向心力 → ψ̇=g·tanφ/vx；横航向
            # 荷兰滚类模态（σ≈−0.62~−1.0 1/s，含方向舵阻尼贡献）并入
            # TAU_PSI。无侧滑刚度辨识数据，不建模偏航舵与转弯率的对抗
            # （不确定度注记见本班 docs 报告）
            psi_track = G_ACC * math.tan(phi) / max(vx, 1.0)
            rr += (psi_track - rr) / TAU_PSI * DT
            phi += pq * DT
            if abs(phi) > 1.2:
                crashed_at = crashed_at or t
        vxf += min(1.0, DT / OUTER_FILT_TAU) * (vx - vxf)

        # ── 记录与判据统计 ──
        z_min = min(z_min, z)
        rms_elev_sq += elev * elev
        rms_n += 1
        if t > 5.0:
            if 2.0 <= z <= 4.0:
                in_band_z += 1
            if abs(freq - 1.25) <= 0.15:
                in_band_f += 1
            if abs(th - PITCH_TRIM) <= 0.05:
                in_trim_p += 1
            n_win += 1
        if mode == "descend" and 13.5 < t <= 15.4:
            if -1.0 <= vz <= -0.5:
                vz_band_n += 1
            vz_late.append(vz)
        if mode == "turn" and t > phi_cmd_settle_t + 5.0:
            psi_coord_cmd = G_ACC * math.tan(phi_cmd) / V_TRIM
            turn_n += 1
            if abs(rr - psi_coord_cmd) <= 0.15 * abs(psi_coord_cmd):
                turn_ok_n += 1
            turn_ratio.append(rr / psi_coord_cmd if psi_coord_cmd else 0.0)
        if collect:
            ser["t"].append(t); ser["z"].append(z); ser["vz"].append(vz)
            ser["vx"].append(vx); ser["th"].append(th); ser["freq"].append(freq)
            ser["elev"].append(elev); ser["phi"].append(phi); ser["r"].append(rr)
        if mode == "launch" and t >= t_sim - 1.0:
            vz_late_sum += vz
            vz_late_n += 1
        if z < Z_CRASH:
            crashed_at = t
            break

    elev_rms = math.sqrt(rms_elev_sq / max(rms_n, 1))
    m = {
        "crashed_at": crashed_at,
        "z_min": z_min,
        "z_end": z,
        "elev_rms": elev_rms,
        "elev_rms_frac": elev_rms / DELTA_MAX,
        "in_band_z_frac": in_band_z / max(n_win, 1),
        "in_band_f_frac": in_band_f / max(n_win, 1),
        "in_trim_p_frac": in_trim_p / max(n_win, 1),
        "freq_med": sorted(ser["freq"])[len(ser["freq"]) // 2] if collect and ser["freq"] else None,
        "th_med_late": None,
    }
    if mode == "descend":
        m["vz_band_frac"] = vz_band_n / max(len(vz_late), 1)
        m["vz_late_med"] = sorted(vz_late)[len(vz_late) // 2] if vz_late else None
    if mode == "turn":
        m["turn_ok_frac"] = turn_ok_n / max(turn_n, 1)
        m["psi_coord_cmd"] = G_ACC * math.tan(phi_cmd) / V_TRIM
        m["turn_ratio_med"] = (sorted(turn_ratio)[len(turn_ratio) // 2]
                               if turn_ratio else None)
    if mode == "launch":
        if vz_late_n > 0:
            m["vz_late_mean"] = vz_late_sum / vz_late_n
        else:
            m["vz_late_mean"] = None
        m["z_min3"] = z_min
    return m, ser


# ══════════════════════════════════════════════════════════════════
# 三B、M4 起飞模态：真三维模型在线积分（线性图在发射大偏离态不可信）
# ══════════════════════════════════════════════════════════════════
_TRUE = {"model": None, "tail": None, "T0": 500000.0}


def _true_model():
    """惰性实例化 sane_dickinson_3d + 尾翼（与 tools/scan_waveform_retrim.py
    同源口径：显式实例化、不读 AERO_MODEL 开关、不改 config）。"""
    if _TRUE["model"] is None:
        from modules.aero.sane_dickinson_3d import SaneDickinson3DModel
        from modules.aero_solver import TailFuselageAero, euler_to_quat
        _TRUE["model"] = SaneDickinson3DModel()
        _TRUE["tail"] = TailFuselageAero()
        _TRUE["euler_to_quat"] = euler_to_quat
        for w in (_TRUE["model"].wing_left, _TRUE["model"].wing_right):
            w.alpha_set = math.radians(WAVE_NOM["alpha_set"])
            w.mean = math.radians(WAVE_NOM["mean_deg"])
            w.split = WAVE_NOM["split"]
            w.pitch_lag_s = WAVE_NOM["pitch_lag_s"]
            w.amplitude = math.radians(WAVE_NOM["amplitude_deg"])
    return _TRUE["model"], _TRUE["tail"]


def set_launch_waveform(name, value):
    """M4 波形容差：直接改真模型波形参数（本模态容差为精确口径）。"""
    m, _ = _true_model()
    for w in (m.wing_left, m.wing_right):
        if name == "alpha_set":
            w.alpha_set = math.radians(value)
        elif name == "mean_deg":
            w.mean = math.radians(value)
        elif name == "split":
            w.split = value
        elif name == "pitch_lag_s":
            w.pitch_lag_s = value
        elif name == "amplitude_deg":
            w.amplitude = math.radians(value)


def simulate_launch(p, input_spec, t_sim=3.0, collect=True):
    """M4 起飞闭环：平动力与俯仰力矩取真三维模型逐时间步求值
    （含尾翼滑流、爬升守卫、非定常状态；同 scan_launch_window 口径），
    姿态环/尾舵调度为九参数复刻，升降舵力矩=−0.49·elev。"""
    m3d, tail = _true_model()
    euler_to_quat = _TRUE["euler_to_quat"]
    kp_a, pdamp, ki_a = p["KP_A"], p["PITCH_DAMP"], p["KI_A"]
    gate, tau = p["INTEG_GATE"], p["RATE_FILT_TAU"]
    aff = p["ALPHA_FF_GAIN"]
    v0, th0 = input_spec["v0"], input_spec["theta0"]
    z, vz, vx, th, q = 3.0, 0.0, v0, th0, 0.0
    freq = FREQ_TRIM
    pitch_i, pd_last, qf, vxf = 0.0, th0, 0.0, v0
    ser = {"t": [], "z": [], "vz": [], "vx": [], "th": [], "freq": [],
           "elev": [], "phi": [], "r": []} if collect else None
    crashed_at = None
    diverged = False
    att_lost = None
    vz_peak = 0.0
    z_min = z
    vz_late_sum, vz_late_n = 0.0, 0
    n_steps = int(round(t_sim / DT))
    m3d.set_wing_command(frequency=freq)
    for i in range(n_steps):
        t = i * DT
        quat = euler_to_quat(0.0, th, 0.0)
        vel = np.array([vx, 0.0, vz])
        om = np.array([0.0, q, 0.0])
        F, T = m3d.compute_forces(np.zeros(3), quat, vel, om, _TRUE["T0"])
        _TRUE["T0"] += DT
        v_slip = SLIP_PER_HZ * freq
        tail.set_command(elevator=0.0)
        Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
        Fx = float(F[0] + Ft[0])
        Fz = float(F[2] + Ft[2])
        My = float(T[1] + Tt[1])
        # ── 姿态环复刻（发射锁定律：拍频锁 1.25、俯仰保持 θ0）──
        perr = pd_last - th
        if abs(perr) < gate:
            pitch_i = min(max(pitch_i + perr * DT, -KI_A_MAX), KI_A_MAX)
        wyd = kp_a * perr + ki_a * pitch_i
        qf += min(1.0, DT / max(tau, 1e-3)) * (q - qf)
        my_dem = KP_R * (wyd - pdamp * qf)
        veff = min(VEFF_CAP, math.sqrt(max(vx * vx, 0.0) + v_slip * v_slip))
        b_sched = K_TAIL_ELEV * veff * veff
        sb, cb = math.sin(th), math.cos(th)
        vbx = cb * vxf + sb * vz + v_slip
        vbz = sb * vxf + cb * vz
        alpha_hat = math.atan2(vbz, max(vbx, 0.1))
        elev = -my_dem / max(b_sched, 1e-6) - aff * alpha_hat
        elev = min(max(elev, -DELTA_MAX), DELTA_MAX)
        m_total = My - B_ELEV_PLANT * elev
        for _f, _a, _ph in DIST_LINES:
            m_total += _a * math.sin(2.0 * math.pi * _f * t + math.radians(_ph))
        vz += (Fz - WEIGHT) / MASS * DT
        vx += Fx / MASS * DT
        q += m_total / IYY * DT
        th += q * DT
        z += vz * DT
        vxf += min(1.0, DT / OUTER_FILT_TAU) * (vx - vxf)
        z_min = min(z_min, z)
        if collect:
            ser["t"].append(t); ser["z"].append(z); ser["vz"].append(vz)
            ser["vx"].append(vx); ser["th"].append(th); ser["freq"].append(freq)
            ser["elev"].append(elev); ser["phi"].append(0.0); ser["r"].append(0.0)
        if t >= t_sim - 1.0:
            vz_late_sum += vz
            vz_late_n += 1
        vz_peak = max(vz_peak, vz)
        if abs(th) > 1.5 and att_lost is None:
            att_lost = t
        if z < Z_CRASH:
            crashed_at = t
            break
        if abs(vx) > 20.0 or abs(vz) > 10.0 or abs(z) > 60.0:
            # 数值发散门（同 scan_launch_window 口径：发散格判窗外）
            crashed_at = t
            diverged = True
            break
    m = {
        "crashed_at": crashed_at, "z_min": z_min, "z_end": z,
        "vz_late_mean": (vz_late_sum / vz_late_n) if vz_late_n else None,
        "z_min3": z_min, "vx_end": vx, "th_end": th,
        "vz_end": vz, "vz_peak": vz_peak, "attitude_lost_at": att_lost,
        "diverged": diverged,
    }
    return m, ser


# ══════════════════════════════════════════════════════════════════
# 四、判据函数（先于数据，布尔归类）
# ══════════════════════════════════════════════════════════════════
def judge_cruise(m):
    return (m["crashed_at"] is None
            and m["in_band_z_frac"] >= 0.95 and m["z_min"] >= 1.5
            and m["in_trim_p_frac"] >= 0.80
            and m["in_band_f_frac"] >= 0.95
            and m["elev_rms_frac"] <= 0.70)


def judge_turn(m):
    return (m["crashed_at"] is None
            and m["turn_ok_frac"] >= 0.90
            and m["in_band_z_frac"] >= 0.95 and m["z_min"] >= 1.5
            and m["in_band_f_frac"] >= 0.95
            and m["elev_rms_frac"] <= 0.70)


def judge_descend(m):
    """M3 判据 = 稳态下降窗（t∈(13.5,15.4] s，指令 −0.75 m/s）内实际下降率
    处于 −0.5~−1.0 m/s 带内 ≥80%（+全局舵偏预算与生存约束）。"""
    return (m["crashed_at"] is None
            and m["vz_band_frac"] >= 0.80
            and m["elev_rms_frac"] <= 0.70
            and m["z_min"] >= 0.5)


def judge_launch(m):
    """M4 判据 = 3 s 不坠且爬升率为正。附加两道防气球门（有在环先例
    scan_launch_window 的 v_end≥3.2 与数值发散门）：末速前进 ≥2 m/s、
    姿态未失（|θ|≤1.5）、数值未发散。"""
    vz_mean = m.get("vz_late_mean")
    return (m["crashed_at"] is None and not m.get("diverged", False)
            and m["z_min3"] >= 0.2
            and m.get("attitude_lost_at") is None
            and m.get("vx_end", 0.0) >= 2.0
            and m.get("vz_end", -99.0) > 0.0
            and vz_mean is not None and vz_mean > 0.0)


MODES = {
    "M1_cruise":  dict(sim_mode="cruise", judge=judge_cruise, desc="定高定速巡航 60s"),
    "M2_turn":    dict(sim_mode="turn", judge=judge_turn, desc="压坡度转弯 10°/15°"),
    "M3_descend": dict(sim_mode="descend", judge=judge_descend, desc="控制下降 −0.75m/s"),
    "M4_launch":  dict(sim_mode="launch", judge=judge_launch, desc="起飞 3s 不坠且爬升率正"),
}


# ══════════════════════════════════════════════════════════════════
# 四、扫描与边界提取
# ══════════════════════════════════════════════════════════════════
def _sgn_frac(sgn):
    return {"+5%": 0.05, "-5%": -0.05, "+10%": 0.10, "-10%": -0.10}[sgn]


def run_one(p, mode_key, input_spec=None, t_sim=T_SIM):
    mode = MODES[mode_key]["sim_mode"]
    if mode == "launch":
        m, _ = simulate_launch(p, input_spec or {}, t_sim, collect=False)
        return MODES[mode_key]["judge"](m), m
    if mode == "turn":
        # 两档坡度都过才算该参数点可行
        ms, oks = [], []
        for phi_deg in (10.0, 15.0):
            spec = dict(input_spec or {}); spec["phi_cmd_deg"] = phi_deg
            m, _ = simulate(p, "turn", spec, t_sim, collect=False)
            ms.append(m); oks.append(MODES[mode_key]["judge"](m))
        m = dict(ms[0]); m["phi10_ok"] = oks[0]; m["phi15_ok"] = oks[1]
        m["turn_ratio_med"] = ms[1].get("turn_ratio_med")   # 取 15° 档
        return all(oks), m
    m, _ = simulate(p, mode, input_spec, t_sim, collect=False)
    return MODES[mode_key]["judge"](m), m


def scan_mode(mode_key, smoke=False):
    """逐模态扫描：九参数逐轴 7 点 + 两片 2 维切片 + 波形容差 20 点。
    M4 另加发射输入网格 7×8 与最优输入处的九参数逐轴复核。"""
    points = []          # (point_id, params, input_spec)

    def pid(gen, *key):
        return gen + "|" + "|".join(str(k) for k in key)

    for name, _, _ in NINE:                                # 轴向 9×7（含名义点）
        for v in AXIS_VALUES[name]:
            pp = dict(NOMINAL); pp[name] = v
            points.append((pid("axis", name, round(v, 6)), pp, None))
    if not smoke:
        for a in AXIS_VALUES["KP_A"]:                       # 切片 1：KP_A×PITCH_DAMP
            for b in AXIS_VALUES["PITCH_DAMP"]:
                pp = dict(NOMINAL); pp["KP_A"] = a; pp["PITCH_DAMP"] = b
                points.append((pid("slice1", round(a, 4), round(b, 4)), pp, None))
        for a in AXIS_VALUES["TECS_KF_P"]:                  # 切片 2：TECS_KF_P×TECS_KV_F
            for b in AXIS_VALUES["TECS_KV_F"]:
                pp = dict(NOMINAL); pp["TECS_KF_P"] = a; pp["TECS_KV_F"] = b
                points.append((pid("slice2", round(a, 4), round(b, 4)), pp, None))
    for wname in WAVE_NOM:                                  # 波形容差 5×4
        for sgn in ("+5%", "-5%", "+10%", "-10%"):
            points.append((pid("wavetol", wname, sgn), dict(NOMINAL),
                           {"wave_tol": (wname, sgn)}))

    results = {}
    if MODES[mode_key]["sim_mode"] == "launch":
        t_sim = 3.0
        v_grid = [3.5 + 0.5 * i for i in range(7)]          # 3.5~6.5
        th_grid = [0.1 * i for i in range(8)]               # 0~0.7
        best = None
        for v0 in v_grid:
            for th0 in th_grid:
                spec = {"v0": v0, "theta0": th0}
                ok, m = run_one(NOMINAL, mode_key, spec, t_sim)
                results[pid("input", v0, round(th0, 2))] = dict(ok=ok, metrics=m,
                                                                params=dict(NOMINAL), spec=spec)
                _vz = m.get("vz_late_mean")
                key = (1 if not m.get("diverged") else 0,
                       (t_sim if m["crashed_at"] is None else -m["crashed_at"]),
                       (_vz if _vz is not None else -99.0))
                if best is None or key > best[0]:
                    best = (key, (v0, th0))
        v_best, th_best = best[1]
        for name, _, _ in NINE:                              # 最优输入处九参数逐轴
            for v in AXIS_VALUES[name]:
                pp = dict(NOMINAL); pp[name] = v
                spec = {"v0": v_best, "theta0": th_best}
                ok, m = run_one(pp, mode_key, spec, t_sim)
                results[pid("axis@best", name, round(v, 6))] = dict(ok=ok, metrics=m, params=pp, spec=spec)
        for wname in WAVE_NOM:
            for sgn in ("+5%", "-5%", "+10%", "-10%"):
                # 真模型波形参数直接施加容差（M4 精确口径），跑完还原
                set_launch_waveform(wname, WAVE_NOM[wname] * (1.0 + _sgn_frac(sgn)))
                spec = {"v0": v_best, "theta0": th_best, "wave_tol": (wname, sgn)}
                ok, m = run_one(NOMINAL, mode_key, spec, t_sim)
                results[pid("wavetol", wname, sgn)] = dict(ok=ok, metrics=m,
                                                           params=dict(NOMINAL), spec=spec)
                set_launch_waveform(wname, WAVE_NOM[wname])
    else:
        t_sim = T_SIM
        for pidx, pp, spec in points:
            if smoke and ("slice" in pidx):
                continue
            ok, m = run_one(pp, mode_key, spec, t_sim)
            results[pidx] = dict(ok=ok, metrics=m, params=pp, spec=spec)
        # ── 第二轮：过最优可行点的轴向补测（内切球半径表的中心）──
        if not smoke:
            best_pid, best_key = None, None
            for _pid, r in results.items():
                if not r["ok"] or _pid.startswith(("wavetol|", "slice")):
                    continue
                m = r["metrics"]
                key = (round(m.get("z_min", 0.0), 3),
                       -round(m.get("elev_rms_frac", 9.0), 3))
                if best_key is None or key > best_key:
                    best_pid, best_key = _pid, key
            if best_pid is not None:
                p_star = dict(results[best_pid]["params"])
                for name, _, _ in NINE:
                    for s in (-1.0, -2/3, -1/3, 0.0, 1/3, 2/3, 1.0):
                        v = p_star[name] + RANGE[name] * s
                        pp = dict(p_star); pp[name] = v
                        ok, m = run_one(pp, mode_key, None, t_sim)
                        results[pid("axis2", name, round(v, 6))] = dict(
                            ok=ok, metrics=m, params=pp, spec=None)
    return results


def axis_intervals(results, prefix="axis|"):
    """轴向边界提取：每参数沿轴的可行/不可行归类 + 不可行侧首点（保守边界）。"""
    out = {}
    for name, nom, rng in NINE:
        pts = []
        for pid, r in results.items():
            if pid.startswith(prefix + name + "|"):
                v = float(pid.split("|")[2])
                pts.append((v, r["ok"]))
        pts.sort()
        bnds = []
        for (v0, o0), (v1, o1) in zip(pts[:-1], pts[1:]):
            if o0 != o1:
                bnds.append(v0 if o0 else v1)   # 保守：取不可行侧
        out[name] = dict(nominal=nom, half_range=rng,
                         points=[(v, o) for v, o in pts], boundaries=bnds)
    return out


def axis_margins(intervals):
    """逐参数归一化边界距离（名义点到最近不可行样本，除以半范围；
    该方向无不可行样本记 1.0）。返回 {name: margin} 或全 None 标记。"""
    margins = {}
    for name, d in intervals.items():
        pts = d["points"]
        if not any(o for _, o in pts):
            margins[name] = None
            continue
        nom, rng = d["nominal"], d["half_range"]
        infeas_b = [v for v, o in pts if not o and v < nom]
        infeas_a = [v for v, o in pts if not o and v > nom]
        d_lo = (nom - max(infeas_b)) / rng if infeas_b else 1.0
        d_hi = (min(infeas_a) - nom) / rng if infeas_a else 1.0
        margins[name] = round(max(0.0, min(d_lo, d_hi)), 3)
    return margins


def radius_from_axis2(results):
    """以第二轮轴向补测（过最优可行点 p*）为据的内切球半径：
    逐参数取 p* 到最近不可行样本的归一化距离，半径 = 其最小值。
    p* = axis2 各轴 s=0 样本的共同参数组（出现 9 次的众数）。
    返回 (radius, per_axis, p_star)。无 axis2 数据返回 (None, None, None)。"""
    ax2 = {pid: r for pid, r in results.items() if pid.startswith("axis2|")}
    if not ax2:
        return None, None, None
    from collections import Counter
    cnt = Counter(tuple(round(r["params"][k], 9) for k in NOMINAL)
                  for r in ax2.values())
    p_star_t, _ = cnt.most_common(1)[0]
    p_star = dict(zip(NOMINAL, p_star_t))
    margins = {}
    for name, _, rng in NINE:
        pts = sorted((float(pid.split("|")[2]), r["ok"])
                     for pid, r in ax2.items() if pid.split("|")[1] == name)
        infeas = [v for v, o in pts if not o]
        d = min([abs(p_star[name] - v) for v in infeas], default=1.0) / rng
        margins[name] = round(min(d, 1.0), 3)
    vals = list(margins.values())
    return (min(vals) if vals else None), margins, p_star


def summarize_mode(mode_key, results):
    is_launch = MODES[mode_key]["sim_mode"] == "launch"
    prefix = "axis@best|" if is_launch else "axis|"
    intervals = axis_intervals(results, prefix)
    n_ok = sum(1 for r in results.values() if r["ok"])
    launch_stats = None
    if is_launch:
        # 名义参数组的可行性 = 发射输入网格内存在可行输入格
        nom_pid = None
        nom_ok = any(r["ok"] for pid, r in results.items() if pid.startswith("input|"))
        nom_metrics = None
        _best_key = None
        n_in = n_div = n_att = n_ground = n_alive = 0
        for pid, r in results.items():
            if not pid.startswith("input|"):
                continue
            mm = r["metrics"]
            n_in += 1
            if mm.get("diverged"):
                n_div += 1
            if mm.get("attitude_lost_at") is not None:
                n_att += 1
            if mm["crashed_at"] is not None and not mm.get("diverged"):
                n_ground += 1
            if mm["crashed_at"] is None and not mm.get("diverged"):
                n_alive += 1
            _vz = mm.get("vz_late_mean")
            key = (1 if not mm.get("diverged") else 0,
                   (3.0 if mm["crashed_at"] is None else -mm["crashed_at"]),
                   (_vz if _vz is not None else -99.0))
            if _best_key is None or key > _best_key:
                _best_key = key
                nom_metrics = mm; nom_pid = pid
        launch_stats = dict(n_inputs=n_in, n_diverged=n_div,
                            n_attitude_lost=n_att, n_ground_crash=n_ground,
                            n_alive_at_3s=n_alive)
    else:
        nom_pid = None
        for pid, r in results.items():
            if pid.startswith("axis|"):
                nm = pid.split("|")[1]
                if abs(r["params"][nm] - NOMINAL[nm]) < 1e-12 and \
                        all(abs(r["params"][k] - NOMINAL[k]) < 1e-12 for k in NOMINAL):
                    nom_pid = pid; break
        nom_ok = results[nom_pid]["ok"] if nom_pid else None
        nom_metrics = results[nom_pid]["metrics"] if nom_pid else None
    radius, margins2, p_star = radius_from_axis2(results)
    # 最优可行点（判据余量粗排：z_min 高、elev_rms 低者优先）
    best_pid, best_key = None, None
    for pid, r in results.items():
        if not r["ok"]:
            continue
        m = r["metrics"]
        key = (round(m.get("z_min", 0.0), 3), -round(m.get("elev_rms_frac", 9.0), 3))
        if best_key is None or key > best_key:
            best_pid, best_key = pid, key
    # 名义点到最近可行样本的归一化 ∞ 距离（域外时"离域多远"；
    # 波形容差点不计——容差不是参数移动）
    dom_dist = None
    if not nom_ok:
        dom_dist = min((max(abs(r["params"][k] - NOMINAL[k]) / RANGE[k]
                            for k in NOMINAL)
                        for _pid, r in results.items()
                        if r["ok"] and not _pid.startswith("wavetol|")),
                       default=None)
        dom_dist = round(dom_dist, 3) if dom_dist is not None else None
    # 空域模态的最近边界：M2=最接近 1 的转弯率比；M4=最优输入格指标
    nearest = None
    if MODES[mode_key]["sim_mode"] == "turn" and n_ok == 0:
        cand = [(abs(1.0 - r["metrics"]["turn_ratio_med"]), pid,
                 r["metrics"]["turn_ratio_med"])
                for pid, r in results.items()
                if r["metrics"].get("turn_ratio_med") is not None]
        if cand:
            cand.sort()
            nearest = dict(pid=cand[0][1], turn_ratio_med=round(cand[0][2], 3),
                           band=[0.85, 1.15],
                           gap_to_band=round(max(0.0, 0.85 - cand[0][2]), 3))
    if is_launch and n_ok == 0:
        nearest = dict(best_input_metrics=nom_metrics)
    return dict(
        mode=mode_key, desc=MODES[mode_key]["desc"],
        n_points=len(results), n_feasible=n_ok,
        nominal_feasible=nom_ok,
        nominal_metrics=nom_metrics if nom_pid else None,
        nominal_to_domain_dist_norm=dom_dist,
        nearest_boundary=nearest,
        launch_input_stats=launch_stats if is_launch else None,
        best_feasible_pid=best_pid,
        best_feasible_params=results[best_pid]["params"] if best_pid else None,
        best_feasible_spec=str(results[best_pid]["spec"]) if best_pid else None,
        robust_radius_norm=radius,
        robust_radius_center=p_star,
        robust_radius_per_axis=margins2,
        axis_intervals={k: dict(nominal=d["nominal"], half_range=d["half_range"],
                                boundaries=d["boundaries"],
                                points=[[v, int(o)] for v, o in d["points"]])
                        for k, d in intervals.items()},
        wave_absorb={pid.split("|")[1] + " " + pid.split("|")[2]:
                     bool(r["ok"]) for pid, r in results.items()
                     if pid.startswith("wavetol|")},
    )


def _worker(arg):
    mode_key, smoke = arg
    res = scan_mode(mode_key, smoke)
    return mode_key, res, summarize_mode(mode_key, res)


# ══════════════════════════════════════════════════════════════════
# 五、交集与敏感性
# ══════════════════════════════════════════════════════════════════
def intersection(raw_results, summaries):
    """共同采样点上的模态交集（M1~M3 同网格；M4 空则全局空）。"""
    m4_empty = summaries["M4_launch"]["n_feasible"] == 0
    common = None
    for mk in ("M1_cruise", "M2_turn", "M3_descend"):
        keys = {pid for pid, r in raw_results[mk].items()
                if not pid.startswith(("input|", "axis@best"))}
        common = keys if common is None else (common & keys)
    subsets = {}
    for combo in (("M1_cruise", "M3_descend"),
                  ("M1_cruise", "M2_turn"),
                  ("M2_turn", "M3_descend"),
                  ("M1_cruise", "M2_turn", "M3_descend")):
        inter_c = [pid for pid in sorted(common or ())
                   if all(raw_results[mk][pid]["ok"] for mk in combo)]
        subsets["+".join(k.split("_")[0] for k in combo)] = inter_c
    inter = subsets["M1+M2+M3"]
    # 交集参数组：取最深非空子集的全可行点中判据余量最优者
    inter_params = None
    inter_subset = None
    for combo in (("M1+M2+M3", "M1+M3", "M1+M2", "M2+M3")):
        if subsets[combo]:
            inter_subset = combo

            def _key(pid):
                m = raw_results["M1_cruise"][pid]["metrics"] \
                    if pid in raw_results["M1_cruise"] \
                    else raw_results["M3_descend"][pid]["metrics"]
                return (round(m.get("z_min", 0.0), 3),
                        -round(m.get("elev_rms_frac", 9.0), 3))
            best = max(subsets[combo], key=_key)
            src = ("M1_cruise" if best in raw_results["M1_cruise"]
                   else "M3_descend")
            inter_params = raw_results[src][best]["params"]
            break
    return dict(
        all_four_nonempty=(not m4_empty) and len(inter) > 0,
        subset_intersections=subsets,
        intersection_subset=inter_subset,
        intersection_params=inter_params,
        intersection_empty_reason=(
            "M4_launch 可行域为空（起飞模态全输入网格不可行，且 M2 为空）"
            if m4_empty else
            ("M1~M3 共同采样点无全可行者（M2 转弯率带不可达）" if not inter else None)),
    )


def sensitivity_b_elev():
    """舵效口径敏感性：名义点九参数下，B_ELEV_PLANT 取 0.49 与飞行回归 0.13
    两档对四模态名义判定的对照。"""
    global B_ELEV_PLANT
    out = {}
    for val in (0.49, 0.13):
        B_ELEV_PLANT = val
        row = {}
        for mk, md in MODES.items():
            if md["sim_mode"] == "launch":
                best = None
                for v0 in (3.5, 5.0, 6.5):
                    for th0 in (0.0, 0.35, 0.7):
                        o, m = run_one(NOMINAL, mk, {"v0": v0, "theta0": th0}, 3.0)
                        _vz = m.get("vz_late_mean")
                        k = (_vz if _vz is not None else -99.0, -m["z_min3"])
                        if best is None or k > best[0]:
                            best = (k, (v0, th0), o, m)
                row[mk] = dict(any_feasible=bool(best[2]), best_input=best[1],
                               vz_late_mean=best[3].get("vz_late_mean"),
                               z_min3=best[3]["z_min3"])
            elif md["sim_mode"] == "turn":
                oks = []
                for phi_deg in (10.0, 15.0):
                    o, m = run_one(NOMINAL, mk, {"phi_cmd_deg": phi_deg}, 30.0)
                    oks.append((o, m["crashed_at"], round(m["elev_rms_frac"], 3)))
                row[mk] = oks
            else:
                o, m = run_one(NOMINAL, mk, None, 30.0)
                row[mk] = dict(ok=o, crashed_at=m["crashed_at"],
                               z_min=round(m["z_min"], 3),
                               elev_rms_frac=round(m["elev_rms_frac"], 3))
        out["B=%s" % val] = row
    B_ELEV_PLANT = 0.49
    return out


# ══════════════════════════════════════════════════════════════════
# 六、主入口
# ══════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="快速自检（无切片）")
    args = ap.parse_args()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[fmap] 多模态可行域扫描 dt=%.3f s T=%.0f s（M4=3 s）四模态并行" % (DT, T_SIM), flush=True)

    tasks = [(mk, args.smoke) for mk in MODES]
    if args.smoke:
        raw = {}
        for t in tasks:
            mk, res, summ = _worker(t)
            raw[mk] = res
            print("[fmap] %s: %d 点，可行 %d，名义 %s" %
                  (mk, summ["n_points"], summ["n_feasible"], summ["nominal_feasible"]), flush=True)
    else:
        with mp.Pool(4) as pool:
            got = pool.map(_worker, tasks)
        raw = {mk: res for mk, res, _ in got}
        for mk, _, summ in got:
            print("[fmap] %s: %d 点，可行 %d，名义可行=%s 半径=%s" %
                  (mk, summ["n_points"], summ["n_feasible"], summ["nominal_feasible"],
                   summ["robust_radius_norm"]), flush=True)

    summaries = {mk: summarize_mode(mk, raw[mk]) for mk in MODES}
    inter = intersection(raw, summaries)
    sens = sensitivity_b_elev() if not args.smoke else None

    # 鲁棒半径表（逐参数逐模态；可行模态=以最优可行点为中心的轴向
    # 归一化边界距离，空域模态=名义到域的归一化 ∞ 距离）
    radius_table = {}
    for mk, summ in summaries.items():
        if summ["robust_radius_per_axis"]:
            radius_table[mk] = dict(center=summ["robust_radius_center"],
                                    per_axis=summ["robust_radius_per_axis"],
                                    radius=summ["robust_radius_norm"])
        else:
            radius_table[mk] = dict(center=None, per_axis=None, radius=None,
                                    nominal_to_domain_dist_norm=summ["nominal_to_domain_dist_norm"])

    out = dict(
        ts=ts, dt=DT, t_sim=T_SIM, provenance=PROVENANCE,
        object=dict(IYY=IYY, wn_pitch_hz=1.15, zeta_pitch=ZETA_P,
                    K_TH=K_TH, C_Q=C_Q, B_ELEV=0.49,
                    DELTA_MAX=DELTA_MAX, FZ0=FZ0, FX0=FX0,
                    DFZ_DF=DFZ_DF, DFX_DF=DFX_DF, DFZ_DV=DFZ_DV,
                    DFZ_DTH=DFZ_DTH, DFX_DTH=DFX_DTH, DFZ_DVZ=DFZ_DVZ,
                    MY_DC=MY_DC, DIST_LINES=DIST_LINES, MASS=MASS),
        nine_params=[dict(name=n, nominal=v, half_range=r, grid=AXIS_VALUES[n]) for n, v, r in NINE],
        wave_nominal=WAVE_NOM, wave_tol=WAVE_TOL,
        modes=summaries,
        intersection=inter,
        robust_radius_table=radius_table,
        sensitivity_b_elev=sens,
    )
    jpath = os.path.join(PROJECT_ROOT, "logs", "feasibility_map.json")
    with open(jpath, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print("[fmap] 完成 → %s" % jpath, flush=True)
    n13 = len(inter["subset_intersections"].get("M1+M3", []))
    print("[fmap] 交集：全四模态非空=%s；子集 M1+M3=%d 点、M1+M2+M3=%d 点；原因：%s" %
          (inter["all_four_nonempty"], n13,
           len(inter["subset_intersections"].get("M1+M2+M3", [])),
           inter["intersection_empty_reason"]), flush=True)


if __name__ == "__main__":
    main()
