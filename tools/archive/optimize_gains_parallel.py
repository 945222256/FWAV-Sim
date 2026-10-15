# -*- coding: utf-8 -*-
"""第三十六班：丙组九参数离线闭环联立寻优（并行网格 + 局部细化）。

依据：docs/全参数决断总表.md（第三十五班，用户已批"离线闭环并行整定、
一次出组"）。本工具只读现有文件、不写 config/modules、不做任何 git 操作。

被控对象（全部数值出处见 docs/第三十六班_九参数联立寻优.md 第一节）：
  俯仰通道  轻阻尼共振子：ω_p=2π×1.15 rad/s、ζ_p=0.07（第九跑回归：
            净刚度 −0.268 N·m/rad、净阻尼 −0.0051 N·m·s/rad、
            Iyy=0.0051607 kg·m²，logs/synth_cruise_gains.json）
  升降舵    舵效 0.49 N·m/rad（决断总表甲组签核值）、限幅 ±0.30 rad，
            正舵偏=负俯仰力矩口径（同 flight_controller/synth_cruise_gains）
  拍频扰动  第九跑飞行窗（1.0<t≤5.575 s）(My−TMy) 在拍频 1.1792 Hz
            谐波线上的最小二乘拟合：1f 0.01196 / 2f 0.00871 /
            3f 0.00367 / 4f 0.00208 N·m（相位随拟合值）；扰动瞬时频率
            跟随拍频 f(t)（扑动驱动反作用的物理），幅值恒定
  高度/速度  ∂Fz/∂f=−0.0457 N/Hz、∂Fx/∂f=−0.4495 N/Hz、
            ∂Fz/∂V=+1.244 N/(m/s)（三十一班 scan_dfzdf_op2 视角 A，
            任务原文三导数；θ→力交叉导数名义取 0，理由与稳健性
            变体见 DFX_DTH 处注释）
  质量      m=0.215 kg（215 g 口径；W=2.109 N 与 scan Fz/W≈0.989 互证）

控制律：modules/flight_controller.py 单段巡航路径逐式复刻（固定常量：
  KP_R=0.25、KI_A_MAX=2.5、FREQ_SLEW=2.5 Hz/s、TECS_ZV_K=0.30、
  TECS_KP_E=0.004、TECS_KD_V=0.02、TECS_EB_MAX=TECS_EI_MAX=60、
  TILT_MAX=0.55、V_CMD_MIN=3.0、V_PITCH_FREE/LOCK=3.5/3.0、
  V_DIVE_ALLOW=0.08、OUTER_FILT_TAU=0.08、PITCH_DES_SLEW=1.2、
  FREQ_AP_MIN/MAX=1.0/3.5、PITCH_TRIM=0.374、FREQ_HOVER=1.25）。

九寻优变量：KP_A、PITCH_DAMP、KI_A、条件积分门 GATE_PERR（实码内联
  常量 0.25）、RATE_FILT_TAU（0.02~0.08 网格）、ALPHA_FF（≡0 固定）、
  TECS_KF_P/KF_I/KV_F（符号已反，本工具搜幅值，输出带负号）。

目标函数（60 s 闭环、dt=1/120，三情景系综）：
  S0 名义（配平态直入，判据权威口径）＋0.5×S1 释放散布（俯仰 −0.05 rad、
  q −0.3 rad/s、vz −0.15 m/s）＋0.5×S2 能量散布（vx −0.2 m/s、vz −0.3 m/s）。
  S0 罚 = 100×超带罚（z 3±1 / θ 0.374±0.05 / f 1.25±0.15）＋4×RMS 平级项；
  S1/S2 罚 = 10×超带罚 + 0.1×RMS 平级项 + 存活秒数损失（60−t_end）。
  （S1/S2 为稳健性排序用：本对象高度交换模态在九参数箱内结构欠阻尼，
  罗斯条件要求 ∂f/∂vx > ∂Fz/∂V/|∂Fz/∂f| = 1.244/0.0457 ≈ 27.2 Hz/(m/s)，
  超出参数箱；瞬态情景以存活时长分序，详见报告结构发现节。）

判据（报告口径，先于数据）：
  ① z∈[2,4] m 全程；② |θ−0.374|≤0.05 rad 样本占比；③ rms(elev)≤0.21 rad；
  ④ f∈[1.10,1.40] Hz 全程。

用法：python tools/optimize_gains_parallel.py [--quick] [--workers N]
输出：logs/optimize_gains.json + 控制台摘要。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time

import numpy as np

# ════════════════════ 被控对象常量（出处见文件头） ════════════════════
IYY = 0.005160670339831472          # kg·m²（synth_cruise_gains.ident）
MASS = 0.215                        # kg（215 g 口径）
G = 9.81                            # m/s²
W = MASS * G                        # 2.109 N
F_TRIM = 1.25                       # Hz 配平拍频（FREQ_HOVER）
TH_TRIM = 0.374                     # rad 配平俯仰（PITCH_TRIM）
V_TRIM = 3.5                        # m/s 配平速度（CRUISE_VX）
Z_REF = 3.0                         # m（Z_REF/验收带中心）
# 共振子（第九跑回归）：ω_p=2π×1.15、ζ=0.07 → K_θ=Iyy·ω_p²=0.2690、
# C_θ=2ζω_p·Iyy=0.00522（回归原值 0.268 / 0.0051）
WP = 2.0 * math.pi * 1.15
ZETA_P = 0.07
K_TH = IYY * WP * WP                # 0.2690 N·m/rad
C_TH = 2.0 * ZETA_P * WP * IYY      # 0.00522 N·m·s/rad
M_RES = 0.0020                      # N·m 配平残余矩（扩域第一名，elev=0 时 My）
B_ELEV = 0.49                       # N·m/rad 升降舵效（决断总表甲组）
DELTA_MAX = 0.30                    # rad 舵偏限幅（已签核）
# 高度/速度通道力导数（三十一班 scan_dfzdf_op2 视角 A，任务原文三导数）
DFZ_DF = -0.0457                    # N/Hz
DFX_DF = -0.4495                    # N/Hz
DFZ_DV = +1.244                     # N/(m/s)
# θ→力瞬时交叉导数（视角 A−B 差分反解值 +4.154/−0.313 N/rad）不进名义模型：
# 第九跑实测（Δθ≈−0.27 rad 持续 ~4 s 而 vx 仅 ±0.5 m/s）证伪其无滞后瞬时
# 作用；拍频/扫速为慢通道再平衡关系。名义取 0，反解值的 25% 作稳健性变体
# R5（见 ROBUST_OV）。
DFX_DTH = 0.0                       # N/rad（名义 0）
DFZ_DTH = 0.0                       # N/rad（名义 0）
# 拍频扰动谐波（第九跑 (My−TMy) LS 拟合 @f=1.1792 Hz；--verify 时重提核对）
BEAT_A = (0.01196, 0.00871, 0.00367, 0.00208)     # N·m，1f~4f
BEAT_PHI = (2.097, 1.270, 1.019, 0.993)           # rad
BEAT_FIT_F0 = 1.1792                               # Hz（第九跑锁频）
RUN9_CSV = os.path.join("logs", "ident_stream_flight_0925_174753.csv")

# ════════════════════ 控制律固定常量（flight_controller.py 巡航路径） ════════════════════
KP_R = 0.25
KI_A_MAX = 2.5
FREQ_SLEW = 2.5
FREQ_AP_MIN, FREQ_AP_MAX = 1.0, 3.5
TECS_ZV_K = 0.30
TECS_KP_E, TECS_KD_V = 0.004, 0.02
TECS_EB_MAX = TECS_EI_MAX = 60.0
TILT_MAX = 0.55
V_CMD_MIN, V_PITCH_FREE, V_PITCH_LOCK = 3.0, 3.5, 3.0
V_DIVE_ALLOW = 0.08
OUTER_FILT_TAU = 0.08
PITCH_DES_SLEW = 1.2

DT = 1.0 / 120.0                    # s 控制拍（实码口径 0.0083）
T_SIM = 60.0                        # s 视界
N_STEP = int(round(T_SIM / DT))
ELEV_RMS_GATE = 0.70 * DELTA_MAX    # 0.21 rad（70% 满偏）

TAU_GRID = (0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
VAR_LO = {"KP_A": 0.10, "PITCH_DAMP": 0.05, "KI_A": 0.005,
          "GATE": 0.25, "AKF_P": 0.000, "AKF_I": 0.000, "AKV_F": 0.10}
# 箱域说明：初箱 PITCH_DAMP≤0.80/KI_A≤0.12/AKV_F≤2.00 三上界被顶满且
# 舵偏预算仅用 7%，按历史存在档（PITCH_DAMP 2.0、KI_A 0.10~0.2、
# TECS_KV_F 1.0）放宽至 1.20/0.20/3.00 后二次寻优（见报告方法论节）。
VAR_HI = {"KP_A": 1.20, "PITCH_DAMP": 1.20, "KI_A": 0.20,
          "GATE": 0.80, "AKF_P": 0.060, "AKF_I": 0.020, "AKV_F": 3.00}

# 情景系综：S0=名义配平态直入（判据权威口径，任务原文单一 60 s 仿真）；
# S1/S2=发射口径散布（第九跑释放行为量级：pitch_des 0.353~0.391、
# 释放俯仰≈0.37；散布取俯仰 −0.05 rad/q −0.3 rad/s/vz −0.15 m/s 与
# 速度 −0.2 m/s/vz −0.3 m/s），仅作稳健性排序，不进判据。
SCENARIOS = (
    {"tag": "S0", "th0": TH_TRIM, "q0": 0.0, "vx0": V_TRIM, "vz0": 0.0, "z0": Z_REF},
    {"tag": "S1", "th0": TH_TRIM - 0.05, "q0": -0.30, "vx0": V_TRIM, "vz0": -0.15, "z0": Z_REF},
    {"tag": "S2", "th0": TH_TRIM, "q0": 0.0, "vx0": V_TRIM - 0.20, "vz0": -0.30, "z0": Z_REF},
)
SCEN_WEIGHT = {"S0": 1.0, "S1": 0.5, "S2": 0.5}


def default_params():
    """现行第九跑档（config/sim_config.py 现值，符号已反的能量三增益）。"""
    return {"KP_A": 0.40, "PITCH_DAMP": 0.30, "KI_A": 0.033, "GATE": 0.25,
            "TAU": 0.08, "AKF_P": 0.030, "AKF_I": 0.010, "AKV_F": 1.00}


def vec_to_params(x, tau):
    return {"KP_A": x[0], "PITCH_DAMP": x[1], "KI_A": x[2], "GATE": x[3],
            "AKF_P": x[4], "AKF_I": x[5], "AKV_F": x[6], "TAU": tau}


def params_to_cfg(p, ov=None):
    """寻优参数 + 对象覆写（稳健性变体）→ 仿真用常量组。"""
    ov = ov or {}
    return {
        "KP_A": p["KP_A"], "PITCH_DAMP": p["PITCH_DAMP"], "KI_A": p["KI_A"],
        "GATE": p["GATE"], "TAU": p["TAU"],
        "KF_P": -p["AKF_P"], "KF_I": -p["AKF_I"], "KV_F": -p["AKV_F"],
        "b_elev": ov.get("b_elev", B_ELEV),
        "k_th": ov.get("k_th", K_TH),
        "c_th": ov.get("c_th", C_TH),
        "dist_scale": ov.get("dist_scale", 1.0),
        "dfx_dth": ov.get("dfx_dth", DFX_DTH),
    }


# ════════════════════ 离线闭环仿真 ════════════════════
def simulate(p, scen, ov=None, want_traj=True):
    """60 s 纵向离线闭环。返回指标 dict（含 2 Hz 降采样轨迹）。"""
    c = params_to_cfg(p, ov)
    kp_a, pdamp, ki_a, gate = c["KP_A"], c["PITCH_DAMP"], c["KI_A"], c["GATE"]
    tau, kf_p, kf_i, kv_f = c["TAU"], c["KF_P"], c["KF_I"], c["KV_F"]
    b_elev, k_th, c_th, dist_s, dfx_dth = (c["b_elev"], c["k_th"], c["c_th"],
                                           c["dist_scale"], c["dfx_dth"])
    a1, a2_, a3_, a4_ = (BEAT_A[0] * dist_s, BEAT_A[1] * dist_s,
                         BEAT_A[2] * dist_s, BEAT_A[3] * dist_s)
    p1, p2, p3, p4 = BEAT_PHI

    th, q = scen["th0"], scen["q0"]
    vx, vz, z = scen["vx0"], scen["vz0"], scen["z0"]
    f_prev = F_TRIM                  # 控制器 _freq_last 初值（FREQ_HOVER）
    e_i = 0.0                        # TECS 比能积分
    pitch_i = 0.0                    # 俯仰条件积分
    pd_last = TH_TRIM                # main_sim 释放块同步 LAUNCH_PITCH 口径
    vf_x, vf_z = vx, vz              # 外环速度 EWMA（OUTER_FILT_TAU）
    wf = q                           # 内环角速率低通（RATE_FILT_TAU）
    psi1 = psi2 = psi3 = psi4 = 0.0  # 扰动谐波相位（跟随时拍频）

    b_out = min(1.0, DT / OUTER_FILT_TAU)
    crash_t = None
    z_a, th_a, f_a, el_a, t_a, vx_a = [], [], [], [], [], []

    for k in range(N_STEP):
        t = (k + 1) * DT
        f = f_prev
        # ── 0b 外环速度 EWMA ──
        vf_x += b_out * (vx - vf_x)
        vf_z += b_out * (vz - vf_z)
        # ── 0 扰动（相位积分跟随时拍频 f）──
        w1 = 2.0 * math.pi * f
        psi1 += w1 * DT; psi2 += 2 * w1 * DT; psi3 += 3 * w1 * DT; psi4 += 4 * w1 * DT
        # ── 2C TECS 巡航分支 ──
        vxd = V_TRIM - TECS_ZV_K * (z - Z_REF)
        vxd = min(5.25, max(3.0, vxd))                       # clip[3.0, 5.25]
        e_pot = G * (Z_REF - z)
        e_kin = 0.5 * (vxd * vxd - vf_x * vf_x)
        e_E = e_pot + e_kin
        e_B = min(TECS_EB_MAX, max(-TECS_EB_MAX, e_pot - e_kin))
        if FREQ_AP_MIN * 1.01 < f < FREQ_AP_MAX * 0.99:
            e_i = min(TECS_EI_MAX, max(-TECS_EI_MAX, e_i + e_E * DT))
        f_raw = F_TRIM + kf_p * e_E + kf_i * e_i + kv_f * (vxd - vf_x)
        f = min(FREQ_AP_MAX, max(FREQ_AP_MIN, f_raw))
        f = min(f_prev + FREQ_SLEW * DT, max(f_prev - FREQ_SLEW * DT, f))
        f_prev = f
        # ── 俯仰指令链 ──
        pitch_tgt = TH_TRIM + TECS_KP_E * e_B - TECS_KD_V * vf_z
        pitch_tgt = min(TILT_MAX, max(-TILT_MAX, pitch_tgt))
        if pitch_tgt < TH_TRIM:                              # 低速下压衰减
            fade = min(1.0, max(0.0, (vf_x - V_PITCH_LOCK)
                                 / (V_PITCH_FREE - V_PITCH_LOCK)))
            cap = fade * TILT_MAX + (1.0 - fade) * V_DIVE_ALLOW
            pitch_tgt = max(pitch_tgt, TH_TRIM - cap)
        d = pitch_tgt - pd_last
        d = min(PITCH_DES_SLEW * DT, max(-PITCH_DES_SLEW * DT, d))
        pitch_des = pd_last + d
        pd_last = pitch_des
        # ── 3 姿态外环 + 条件积分 ──
        perr = (pitch_des - th + math.pi) % (2.0 * math.pi) - math.pi
        if abs(perr) < gate:
            pitch_i = min(KI_A_MAX, max(-KI_A_MAX, pitch_i + perr * DT))
        wyd = kp_a * perr + ki_a * pitch_i
        # ── 内环角速率低通 + 4 内环 → 舵 ──
        a_in = min(1.0, DT / max(tau, 1e-3))
        wf += a_in * (q - wf)
        my = KP_R * (wyd - pdamp * wf)
        elev = -my / b_elev                                  # 正舵偏=负俯仰矩
        if elev > DELTA_MAX: elev = DELTA_MAX
        elif elev < -DELTA_MAX: elev = -DELTA_MAX
        # ── 对象：俯仰共振子 + 力通道 ──
        m_beat = (a1 * math.sin(psi1 + p1) + a2_ * math.sin(psi2 + p2)
                  + a3_ * math.sin(psi3 + p3) + a4_ * math.sin(psi4 + p4))
        m_net = (M_RES + m_beat - k_th * (th - TH_TRIM) - c_th * q
                 - b_elev * elev)
        fx = DFX_DF * (f - F_TRIM) + dfx_dth * (th - TH_TRIM)
        fz = (DFZ_DF * (f - F_TRIM) + DFZ_DV * (vx - V_TRIM)
              + DFZ_DTH * (th - TH_TRIM))
        # ── 半隐式欧拉积分 ──
        q += m_net / IYY * DT
        th += q * DT
        vx += fx / MASS * DT
        vz += fz / MASS * DT
        z += vz * DT
        z_a.append(z); th_a.append(th); f_a.append(f); el_a.append(elev)
        t_a.append(t); vx_a.append(vx)
        # ── 坠毁/发散判据（t>1 s 后 z<0.2 为第九跑存活口径）──
        if (t > 1.0 and z < 0.2) or abs(th) > 1.5 or z > 20.0 or abs(vx) > 10.0 \
                or not (th == th and z == z):
            crash_t = t
            break
    return _metrics(scen["tag"], t_a, z_a, th_a, f_a, el_a, vx_a, crash_t,
                    want_traj)


def _metrics(tag, t_a, z_a, th_a, f_a, el_a, vx_a, crash_t, want_traj):
    z = np.asarray(z_a); th = np.asarray(th_a); f = np.asarray(f_a)
    el = np.asarray(el_a); t = np.asarray(t_a); vxa = np.asarray(vx_a)
    n = len(t)
    zd = np.abs(z - Z_REF); thd = np.abs(th - TH_TRIM); fd = np.abs(f - F_TRIM)
    w20 = t > max(0.0, t[-1] - 20.0)
    m = {
        "tag": tag, "n": int(n), "t_end": float(t[-1]), "crash_t": crash_t,
        "z_rms": float(np.sqrt((zd ** 2).mean())),
        "z_max": float(zd.max()),
        "z_inband_frac": float(np.mean(zd <= 1.0)),
        "z_exceed_rms": float(np.sqrt((np.clip(zd - 1.0, 0, None) ** 2).mean())),
        "th_rms": float(np.sqrt((thd ** 2).mean())),
        "th_max": float(thd.max()),
        "th_inband_frac": float(np.mean(thd <= 0.05)),
        "th_exceed_rms": float(np.sqrt((np.clip(thd - 0.05, 0, None) ** 2).mean())),
        "th_mean_last20": float(th[w20].mean()) if w20.any() else float("nan"),
        "el_rms": float(np.sqrt((el ** 2).mean())),
        "el_rms_frac": float(np.sqrt((el ** 2).mean()) / DELTA_MAX),
        "el_sat_frac": float(np.mean(np.abs(el) >= 0.295)),
        "f_rms_dev": float(np.sqrt((fd ** 2).mean())),
        "f_inband_frac": float(np.mean((f >= 1.10) & (f <= 1.40))),
        "f_exceed_rms": float(np.sqrt((np.clip(fd - 0.15, 0, None) ** 2).mean())),
        "f_min": float(f.min()), "f_max": float(f.max()),
        "f_med": float(np.median(f)),
        "vx_min": float(vxa.min()), "vx_mean": float(vxa.mean()),
    }
    if want_traj and n >= 4:
        keep = max(1, n // 120)
        m["traj"] = {
            "t": [round(float(v), 3) for v in t[::keep]],
            "z": [round(float(v), 4) for v in z[::keep]],
            "th": [round(float(v), 4) for v in th[::keep]],
            "f": [round(float(v), 4) for v in f[::keep]],
            "el": [round(float(v), 4) for v in el[::keep]],
        }
    return m


def scen_cost(m):
    """逐情景代价。

    S0（判据权威）：超带重罚 + 平级 tie-breaker（拍频纹波抑制质量）。
    S1/S2（瞬态稳健性）：超带罚 + 存活秒数损失（60−t_end）主导——本模型
    高度交换模态在九参数箱内结构欠阻尼（见报告结构发现节），瞬态判据
    不可全过，以存活时长与带内占比排序。
    """
    viol = (m["z_exceed_rms"] / 1.0 + m["th_exceed_rms"] / 0.05
            + max(0.0, m["el_rms"] - ELEV_RMS_GATE) / DELTA_MAX
            + m["f_exceed_rms"] / 0.15)
    tie = (m["th_rms"] / 0.05 + m["el_rms"] / ELEV_RMS_GATE
           + m["f_rms_dev"] / 0.15 + m["z_rms"] / 1.0)
    if m["tag"] == "S0":
        return 100.0 * viol + 4.0 * tie
    return 10.0 * viol + 0.1 * tie + 1.0 * (T_SIM - m["t_end"])


def evaluate(p, ov=None, want_traj=False):
    ms = [simulate(p, s, ov=ov, want_traj=want_traj) for s in SCENARIOS]
    J = sum(SCEN_WEIGHT[m["tag"]] * scen_cost(m) for m in ms)
    return J, ms


def _eval_worker(args):
    p, ov = args
    J, _ = evaluate(p, ov=ov, want_traj=False)
    return J


# ════════════════════ 扰动谱重提核对（--verify） ════════════════════
def reextract_beat():
    rows = []
    with open(RUN9_CSV, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({k: (v if k == "phase" else float(v)) for k, v in r.items()})
    fw = [r for r in rows if 1.0 < r["t"] <= 5.5750]
    t = np.array([r["t"] for r in fw])
    mw = np.array([r["My"] - r["TMy"] for r in fw])
    cols = [np.ones_like(t)]
    for k in (1, 2, 3, 4):
        w = 2 * np.pi * k * BEAT_FIT_F0
        cols += [np.sin(w * t), np.cos(w * t)]
    coef, *_ = np.linalg.lstsq(np.column_stack(cols), mw, rcond=None)
    out = {"n": len(fw), "DC": float(coef[0])}
    for k in range(1, 5):
        out[f"A{k}"] = float(np.hypot(coef[2 * k - 1], coef[2 * k]))
        out[f"phi{k}"] = float(np.arctan2(coef[2 * k], coef[2 * k - 1]))
    out["embedded"] = {"A": BEAT_A, "phi": BEAT_PHI}
    return out


# ════════════════════ 寻优主体 ════════════════════
def norm_dist(p, q, var_hi=None):
    hi = var_hi or VAR_HI
    d = 0.0
    for key in ("KP_A", "PITCH_DAMP", "KI_A", "GATE", "AKF_P", "AKF_I", "AKV_F"):
        lo, h = VAR_LO[key], hi[key]
        d += abs(p[key] - q[key]) / max(h - lo, 1e-9)
    d += abs(TAU_GRID.index(p["TAU"]) - TAU_GRID.index(q["TAU"])) / (len(TAU_GRID) - 1)
    return d


def _nm_one(args):
    """Nelder-Mead 局部细化（连续 7 变量，τ 固定为种子值）。模块级以可 pickle。"""
    from scipy.optimize import minimize
    seed, keys, bounds = args
    x0 = np.array([seed[k] for k in keys])
    r = minimize(lambda x: evaluate(vec_to_params(x, seed["TAU"]))[0],
                 x0, method="Nelder-Mead", bounds=bounds,
                 options={"maxiter": 400, "xatol": 1e-4, "fatol": 1e-5})
    out = vec_to_params(list(r.x), seed["TAU"])
    out["_J"] = float(r.fun)
    return out


def run_optimization(n_stage1=6000, n_seeds=6, workers=None, quick=False,
                     var_hi=None, box_tag=""):
    """单箱三阶段寻优（随机网格 → Nelder-Mead → 局部因子网格）。

    var_hi 覆写 VAR_HI 上界（双箱对比：保守初箱 vs 放宽箱）。
    """
    import multiprocessing as mp

    vhi = dict(VAR_HI)
    if var_hi:
        vhi.update(var_hi)
    if quick:
        n_stage1, n_seeds = 400, 2
    if workers is None:
        workers = max(1, min(24, (os.cpu_count() or 4) - 2))
    rng = np.random.default_rng(20260925)
    log = []

    # ── 阶段一：随机网格（LHS 均匀 + τ 网格均匀）──
    keys = ["KP_A", "PITCH_DAMP", "KI_A", "GATE", "AKF_P", "AKF_I", "AKV_F"]
    u = rng.random((n_stage1, len(keys) + 1))
    cands = []
    for i in range(n_stage1):
        p = {k: VAR_LO[k] + u[i, j] * (vhi[k] - VAR_LO[k])
             for j, k in enumerate(keys)}
        p["TAU"] = TAU_GRID[int(u[i, -1] * len(TAU_GRID)) % len(TAU_GRID)]
        cands.append(p)
    t0 = time.time()
    ctx = mp.get_context("spawn")
    with ctx.Pool(workers) as pool:
        Js = pool.map(_eval_worker, [(p, None) for p in cands], chunksize=16)
    for p, J in zip(cands, Js):
        p["_J"] = J
        p["_box"] = box_tag
    log.append({"stage": "1_random_grid" + box_tag, "n": n_stage1,
                "workers": workers, "sec": round(time.time() - t0, 1),
                "best_J": float(min(Js))})
    print(f"[阶段一{box_tag}] 随机网格 n={n_stage1} workers={workers} "
          f"{time.time()-t0:.0f}s best_J={min(Js):.3f}")

    pool_all = sorted(cands, key=lambda p: p["_J"])
    # ── 阶段二：top 种子 Nelder-Mead 局部细化（连续 7 变量，τ 固定）──
    seeds, t2 = [], time.time()
    for p in pool_all:
        if all(norm_dist(p, s, vhi) > 0.10 for s in seeds):
            seeds.append(dict(p))
        if len(seeds) >= n_seeds:
            break
    bounds = [(VAR_LO[k], vhi[k]) for k in keys]
    with ctx.Pool(min(workers, len(seeds))) as pool:
        refined = pool.map(_nm_one, [(s, keys, bounds) for s in seeds])
    for p in refined:
        p["_box"] = box_tag
        pool_all.append(p)
    log.append({"stage": "2_nelder_mead" + box_tag, "n_seeds": len(seeds),
                "sec": round(time.time() - t2, 1),
                "best_J": float(min(p["_J"] for p in refined))})
    print(f"[阶段二{box_tag}] NM×{len(seeds)} {time.time()-t2:.0f}s "
          f"best_J={min(p['_J'] for p in refined):.3f}")

    # ── 阶段三：围绕最优的局部因子网格（产生备选组）──
    best = min(pool_all, key=lambda p: p["_J"])
    t3 = time.time()
    grid_pts = []
    ti = TAU_GRID.index(best["TAU"])
    for dtau in (-1, 0, 1):
        tau_i = min(len(TAU_GRID) - 1, max(0, ti + dtau))
        for fk in (0.85, 1.0, 1.15):
            for fd in (0.85, 1.0, 1.15):
                for fi in (0.7, 1.0, 1.4):
                    for fv in (0.8, 1.0, 1.2):
                        p = {"KP_A": min(vhi["KP_A"], best["KP_A"] * fk),
                             "PITCH_DAMP": min(vhi["PITCH_DAMP"],
                                               best["PITCH_DAMP"] * fd),
                             "KI_A": min(vhi["KI_A"], best["KI_A"] * fi),
                             "GATE": best["GATE"],
                             "AKF_P": best["AKF_P"], "AKF_I": best["AKF_I"],
                             "AKV_F": min(vhi["AKV_F"], best["AKV_F"] * fv),
                             "TAU": TAU_GRID[tau_i]}
                        grid_pts.append(p)
    with ctx.Pool(workers) as pool:
        Js3 = pool.map(_eval_worker, [(p, None) for p in grid_pts], chunksize=8)
    for p, J in zip(grid_pts, Js3):
        p["_J"] = J
        p["_box"] = box_tag
        pool_all.append(p)
    log.append({"stage": "3_local_grid" + box_tag, "n": len(grid_pts),
                "sec": round(time.time() - t3, 1),
                "best_J": float(min(Js3))})
    print(f"[阶段三{box_tag}] 局部网格 n={len(grid_pts)} {time.time()-t3:.0f}s "
          f"best_J={min(Js3):.3f}")
    return pool_all, log


def select_groups(pool_all):
    """选组：全局最优 + 三备选（另一箱最优优先，其余按参数空间分离度）。"""
    pool_all = sorted(pool_all, key=lambda p: p["_J"])
    best = pool_all[0]
    chosen = [best]
    for p in pool_all[1:]:
        if p["_box"] != best["_box"]:
            chosen.append(p)
            break
    for p in pool_all[1:]:
        if len(chosen) == 4:
            break
        if any(p is c for c in chosen):
            continue
        if p["_J"] > 4.0 * best["_J"] + 2.0:
            break
        if all(norm_dist(p, c) > 0.12 for c in chosen):
            chosen.append(p)
    return chosen[:4]


ROBUST_OV = {
    "R1_扰动×1.5": {"dist_scale": 1.5},
    "R2_ζp=0.04": {"c_th": 2.0 * 0.04 * WP * IYY},
    "R3_舵效0.40": {"b_elev": 0.40},
    "R4_刚度×1.2": {"k_th": K_TH * 1.2},
    "R5_θ力耦合25%": {"dfx_dth": 4.154 * 0.25, "dfz_dth": -0.313 * 0.25},
}


def full_report(p):
    J, ms = evaluate(p, want_traj=True)
    rob = {}
    for tag, ov in ROBUST_OV.items():
        m0 = simulate(p, SCENARIOS[0], ov=ov, want_traj=False)
        rob[tag] = {"S0_th_inband_frac": m0["th_inband_frac"],
                    "S0_th_rms": m0["th_rms"],
                    "S0_el_rms_frac": m0["el_rms_frac"],
                    "S0_f_inband_frac": m0["f_inband_frac"],
                    "S0_z_inband_frac": m0["z_inband_frac"],
                    "S0_crash_t": m0["crash_t"]}
    return {"params": {k: p[k] for k in ("KP_A", "PITCH_DAMP", "KI_A", "GATE",
                                         "TAU", "AKF_P", "AKF_I", "AKV_F")},
            "params_signed": {"TECS_KF_P": -p["AKF_P"], "TECS_KF_I": -p["AKF_I"],
                              "TECS_KV_F": -p["AKV_F"], "ALPHA_FF_GAIN": 0.0},
            "J": J, "metrics": ms, "robustness": rob}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--verify", action="store_true",
                    help="重提第九跑扰动谱核对内嵌常数")
    args = ap.parse_args()

    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

    out = {"tool": "tools/optimize_gains_parallel.py",
           "ts": time.strftime("%Y%m%d_%H%M%S"),
           "model": {
               "IYY": IYY, "MASS": MASS, "WP_hz": 1.15, "ZETA_P": ZETA_P,
               "K_TH": K_TH, "C_TH": C_TH, "M_RES": M_RES,
               "B_ELEV": B_ELEV, "DELTA_MAX": DELTA_MAX,
               "DFZ_DF": DFZ_DF, "DFX_DF": DFX_DF, "DFZ_DV": DFZ_DV,
               "DFX_DTH": DFX_DTH, "DFZ_DTH": DFZ_DTH,
               "BEAT_A": list(BEAT_A), "BEAT_PHI": list(BEAT_PHI),
               "BEAT_FIT_F0": BEAT_FIT_F0,
               "beat_provenance": "第九跑 logs/ident_stream_flight_0925_174753.csv "
                                  "飞行窗 1.0<t≤5.575 s (My−TMy) 谐波 LS 拟合",
               "scenarios": [dict(s) for s in SCENARIOS],
               "scen_weight": SCEN_WEIGHT,
           }}
    if args.verify or os.path.exists(RUN9_CSV):
        try:
            out["beat_reextract"] = reextract_beat()
            print("[核对] 第九跑扰动谱重提：",
                  json.dumps(out["beat_reextract"], ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            print("[核对] 重提失败（继续用内嵌常数）：", e)

    # 双箱寻优：箱A=保守初箱（PITCH_DAMP≤0.80/KI_A≤0.12/AKV_F≤2.00，
    # 第九跑现值的历史演进邻域）；箱B=放宽箱（1.20/0.20/3.00，初箱三上界
    # 顶满且舵偏预算仅用 7% 后的一次箱域迭代）。
    box_a = {"PITCH_DAMP": 0.80, "KI_A": 0.12, "AKV_F": 2.00}
    pool_a, log_a = run_optimization(workers=args.workers, quick=args.quick,
                                     var_hi=box_a, box_tag="A保守箱")
    pool_b, log_b = run_optimization(workers=args.workers, quick=args.quick,
                                     box_tag="B放宽箱")
    pool_all = pool_a + pool_b
    chosen = select_groups(pool_all)
    out["search_log"] = log_a + log_b
    out["n_evaluated"] = len(pool_all)

    out["baseline_current"] = full_report(default_params())
    out["best"] = full_report(chosen[0])
    out["alternates"] = [full_report(c) for c in chosen[1:4]]

    with open("logs/optimize_gains.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    b = out["best"]
    m0 = b["metrics"][0]
    print("\n═══ 最优组（60 s 离线，S0 名义）═══")
    print(json.dumps(b["params_signed"] | {k: b["params"][k] for k in
          ("KP_A", "PITCH_DAMP", "KI_A", "GATE", "TAU")}, ensure_ascii=False))
    print(f"z 带内 {m0['z_inband_frac']*100:.1f}% | θ 带内 "
          f"{m0['th_inband_frac']*100:.1f}% (rms {m0['th_rms']:.4f}) | "
          f"elev rms {m0['el_rms_frac']*100:.1f}% 满偏 | f 带内 "
          f"{m0['f_inband_frac']*100:.1f}% [{m0['f_min']:.3f},{m0['f_max']:.3f}]")
    print(f"J={b['J']:.3f}（现行组 J={out['baseline_current']['J']:.3f}）")
    print("输出：logs/optimize_gains.json")


if __name__ == "__main__":
    main()
