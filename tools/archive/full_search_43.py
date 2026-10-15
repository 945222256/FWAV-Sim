# -*- coding: utf-8 -*-
"""full_search_43.py — 第四十三班：全参数完整重搜（2026-09-25）。

用户令：十五变量全部放开（无预设边界以外的限制），考卷全部实测口径、
零外推。历次失真（开环考卷、线性力矩外推、导数高估五倍、积分门口径
错配、缺静态力矩权限约束）全部根除。

════════════════════════════════════════════════════════════════════
十五变量搜索域（用户令逐字给出；控制器七参数用户令未给数值界，取
第四十一班域向外扩，不用任何未测假设收窄）：
  波形五  α_set 15~50°、MEAN −20~+30°、SPLIT 0.4~0.95、
          LAG −0.08~+0.02 s、幅值 15~45°
  工作点二 拍频 0.7~3.4 Hz、速度 2.0~5.5 m/s
  控制器七 KP_A、PITCH_DAMP、KI_A、积分门 GATE、RATE_FILT_TAU、
          攻角前馈 ALPHA_FF_GAIN、TECS_KV_F（符号负输出；
          KF_P/KF_I 按三十六班最优结构置零固定，非自由变量）

════════════════════════════════════════════════════════════════════
考卷（先于数据，六条硬判据）：
  ① 静态力矩需求 |M(θ*)| ≤ 舵权限×70% = 0.7×0.0541 = 0.037870 N·m
     （舵权限=第十五跑实测当量 0.0541 N·m = 0.0145×3.526²×0.30；
       M=该波形真三维模型全程俯仰力矩曲线在配平俯仰 θ* 处的取值，
       含第八跑实测机翼+驱动矩直流 MY_DC=−0.0399 N·m——第十六跑
       实测口径：名义点持配平俯仰需求 0.0865 对权限 0.0541，其中
       直流分量即 MY_DC；θ*=全程曲线 M(θ)=0 的配平根）
  ② 动态升力中位 ≥0.95W（含拍频反作用与爬升守卫的动态周期平均，
     Frz 同源口径——第四十一班口径裁决，第九跑数据流对账）
  ③ 闭环 60 s 高度 3±1 m（t>5 s 带内占比 ≥95%、z_min≥1.5、存活）
  ④ 俯仰跟踪 ±0.05 rad（带内占比 ≥80%，对配平根 θ*）
  ⑤ 舵 RMS ≤70% 满偏（±0.30 rad → ≤0.21 rad）
  ⑥ 拍频带内（中位频偏 ≤max(0.20 Hz, 0.15·f0)；不贴保护带：
     f≥3.45 Hz 或 ≤f_lo+0.05 的样本占比各 ≤5%）

════════════════════════════════════════════════════════════════════
零外推的对象内核（本班核心修正）：
  1. 每个候选波形用真三维模型（sane_dickinson_3d+尾翼，
     feasibility_map._true_model 同源）逐点标定完整俯仰力矩曲线
     M(θ)：θ 全程 −10~+40° 扫 24 点（n=144 整周期平均，升降舵
     零位），翼/尾分量分开记账；同网格测 Fz(θ)/Fx(θ) 曲线。
  2. 闭环仿真力矩=全程曲线查表（线性插值、端点钳位、出程计数），
     斜率（刚度 K_TH、∂Fz/∂θ）用同网格曲线差分——不用根邻域
     线性化外推（第十六跑证伪点：根邻域刚度 0.0657 线性图给
     θ 带内 100%，真实在环均衡点偏 0.25 rad 出外推区）。
  3. 对象侧舵效用 veff 调度当量 b=0.0145·veff²（第十五/十六跑
     实测回归斜率 0.0145×3.526²=0.180 N·m/rad；不用 0.49 假设——
     该值对应 veff≈5.8，把静态直流的舵偏当量低计 2.7 倍）。

方法：内层波形标定（并行 LHS+种子）、外层控制器整定（极点配置
种子+LHS 网格+Nelder-Mead 细化）、序贯加密可行域边界（近失格点
局部再采样）+对角切片（可行点连线与角点连线，堵窄域漏检）；
输出可行域+最大内切球+鲁棒半径表，非单点。

用法： python tools/full_search_43.py [--quick] [--workers N]
        [--n-stage-a N] [--out logs/full_search_43.json]
产物： logs/full_search_43.json + 控制台摘要。
纪律： 只读现有文件（import 仅读）；不写 config/modules；无 git 操作。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ══════════════════ 对象常量（出处见各注释） ══════════════════
G_ACC = 9.81
MASS = 0.215                          # kg（215 g 口径，config/sim_config.py MASS）
WEIGHT = MASS * G_ACC                 # 2.10915 N
IYY = 0.00516067                      # kg·m²（logs/synth_cruise_gains.json ident）
C_Q = 0.005216                        # N·m·s/rad 俯仰阻尼（第九跑回归 ζ0.07@1.15Hz）
DELTA_MAX = 0.30                      # rad 舵偏限幅（甲组）
MY_DC = -0.0399                       # N·m 机翼+驱动矩直流（第八跑谱投影，37班探针B）
NOM_MYDC_DYN = 0.014                  # N·m 名义波形真模型根态动态直流（41班复核档）
ELEV_AUTH_NOM = 0.0541                # N·m 舵权限（第十五/十六跑实测当量 0.0145×3.526²×0.30）
ELEV_AUTH_FRAC = 0.70                 # 判据①权限比例
DEMAND_GATE = ELEV_AUTH_FRAC * ELEV_AUTH_NOM     # 0.037870 N·m
SUBHARM_K, SUBHARM_A, SUBHARM_PHI = 0.525, 0.01930, math.radians(84.0)
                                      # 第八跑 0.62Hz 线对锁频 1.18Hz 之比，随拍频缩放
GUARD_VZ = 2.5                        # m/s 爬升守卫半衰减点（aero_solver 生效值）
K_TAIL_ELEV = 0.0145                  # 尾舵当量调度（|K_B_TAIL_ELEV|，config 第九跑口径）
SLIP_PER_HZ = 0.472                   # m/s/Hz 滑流（run-9 CSV vslip=0.59@f=1.25 互证）
VEFF_CAP = 8.0
# 三探针名义值（第三十七班探针 A + 第三十一班斜率；锚定核对用）
PROBE_NOM = {"FZ0": 2.08116, "FX0": -0.00505, "DFZ_DTH": -0.4866,
             "DFX_DTH": 1.8213, "DFZ_DVZ": -0.2026, "DFX_DVZ": -0.1616,
             "DMY_DVZ": -0.0711, "DFZ_DF": -0.0457, "DFX_DF": -0.4495,
             "DFZ_DV": 1.244, "DFX_DV": 0.1189}
A41_ANCHOR = {"theta_star_deg": 22.02, "FZ0": 2.0828, "K_TH": 0.0657,
              "DFZ_DV": 1.2438, "DFZ_DVZ": -0.2067, "DFZ_DTH_2deg": -0.645,
              "beat_1f": 0.01196, "beat_2f": 0.00871}

# 控制律结构常量（flight_controller.py 单段巡航路径，第九跑在环值）
KP_R = 0.25
KI_A_MAX = 2.5
TILT_MAX = 0.55                       # rad
FREQ_SLEW = 2.5                       # Hz/s
FREQ_AP_MAX = 3.5                     # Hz
PITCH_DES_SLEW = 1.2                  # rad/s
OUTER_FILT_TAU = 0.08                 # s
TECS_ZV_K = 0.30
TECS_KP_E, TECS_KD_V = 0.004, 0.02
V_CMD_MIN = 3.0                       # m/s（真控制器固定下沿，低 V0 候选如实受此约束）
KF_P_FIXED, KF_I_FIXED = 0.0, 0.0     # 第三十六班最优结构（e_E 通道置零）
Z_REF = 3.0                           # m
V_DIVE_ALLOW = 0.08                   # rad

DT_CL = 0.004                         # s 闭环积分步长（feasibility_map 口径）
T_SIM = 60.0
Z_CRASH = 0.2
PHI_DRIVE_LIMIT = math.radians(80.2)  # 驱动限位 ±1.4 rad（scan_waveform_retrim 注记
                                      # 真值；41 班 |MEAN|+幅值≤50° 为保守假设，本班弃用）

# ══════════════════ 十五变量搜索域 ══════════════════
WAVE_KEYS = ("alpha_set", "mean_deg", "split", "pitch_lag_s",
             "amplitude_deg", "freq", "V")
WAVE_LO = {"alpha_set": 15.0, "mean_deg": -20.0, "split": 0.4,
           "pitch_lag_s": -0.08, "amplitude_deg": 15.0, "freq": 0.7, "V": 2.0}
WAVE_HI = {"alpha_set": 50.0, "mean_deg": 30.0, "split": 0.95,
           "pitch_lag_s": 0.02, "amplitude_deg": 45.0, "freq": 3.4, "V": 5.5}
TAU_GRID = (0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10)
CTRL_KEYS = ("KP_A", "PITCH_DAMP", "KI_A", "GATE", "AFF", "AKV_F")
# 控制器域：用户令未给数值界——取 41 班域向外扩（不收窄）。首跑最优组
# 骑在 PITCH_DAMP=2.40 上界且该轴半径 0（首跑实测），二跑再扩至 4.80。
CTRL_LO = {"KP_A": 0.05, "PITCH_DAMP": 0.05, "KI_A": 0.002, "GATE": 0.02,
           "AFF": -0.10, "AKV_F": 0.05}
CTRL_HI = {"KP_A": 1.60, "PITCH_DAMP": 4.80, "KI_A": 0.30, "GATE": 1.20,
           "AFF": 0.80, "AKV_F": 4.00}

# 判据阈值（先于数据）
FZW_GATE = 0.95
ZBAND_OCC_GATE = 0.95
ZMIN_GATE = 1.5
TH_OCC_GATE = 0.80
ELEV_RMS_GATE = 0.70
FREQ_RAIL_FRAC = 0.05

NOMINAL_WAVE = {"alpha_set": 35.0, "mean_deg": 20.0, "split": 0.75,
                "pitch_lag_s": -0.04, "amplitude_deg": 30.0,
                "freq": 1.25, "V": 3.5}     # 甲组签核值（41 班 WAVE_NOM）
BEST41_WAVE = {"alpha_set": 42.957, "mean_deg": 11.617, "split": 0.7618,
               "pitch_lag_s": -0.0533, "amplitude_deg": 24.030,
               "freq": 2.2373, "V": 4.0680}   # 41 班正式最优组
RUN9_CTRL = {"KP_A": 0.40, "PITCH_DAMP": 0.30, "KI_A": 0.033, "GATE": 0.25,
             "TAU": 0.08, "AFF": 0.0, "AKV_F": 1.00}   # 第九跑现值
RUN36_CTRL = {"KP_A": 0.20, "PITCH_DAMP": 1.20, "KI_A": 0.20, "GATE": 0.80,
              "TAU": 0.02, "AFF": 0.0, "AKV_F": 3.00}  # 三十六班最优组
REC41_CTRL = {"KP_A": 0.406, "PITCH_DAMP": 1.156, "KI_A": 0.189, "GATE": 0.174,
              "TAU": 0.03, "AFF": 0.232, "AKV_F": 1.840}  # 41 班推荐组

# 曲线网格（用户令：θ 全程 −10~+40° 扫 24 点）
CURVE_DEG = np.linspace(-10.0, 40.0, 24)
NC = 144                              # 曲线/斜率相位网格（41 班教训：同 n 同网格）
N_K8 = 8                              # 在环 K=8 同步周期平均口径（对账用）


# ══════════════════ 真三维模型（每进程惰性单例） ══════════════════
_TRUE = {"model": None, "tail": None, "e2q": None, "T0": 900000.0}


def _model_pair():
    if _TRUE["model"] is None:
        from modules.aero.sane_dickinson_3d import SaneDickinson3DModel
        from modules.aero_solver import TailFuselageAero, euler_to_quat
        _TRUE["model"] = SaneDickinson3DModel()
        _TRUE["tail"] = TailFuselageAero()
        _TRUE["e2q"] = euler_to_quat
    return _TRUE["model"], _TRUE["tail"]


def _set_waveform(w):
    m, _ = _model_pair()
    for wing in (m.wing_left, m.wing_right):
        wing.alpha_set = math.radians(w["alpha_set"])
        wing.mean = math.radians(w["mean_deg"])
        wing.split = w["split"]
        wing.pitch_lag_s = w["pitch_lag_s"]
        wing.amplitude = math.radians(w["amplitude_deg"])


def cycle_eval(V, f, pitch_deg, vz=0.0, n=NC, want_series=False):
    """钉固状态整周期平均（scan_waveform_retrim.eval_state 同口径：时基
    单调推进、显式设频、升降舵零位、滑流=SLIP_PER_HZ·f、世界系力/力矩）。
    翼/尾力矩分开记账（判据①口径：M(θ*) 的翼分量、尾分量并列）。"""
    m, tail = _model_pair()
    m.set_wing_command(frequency=f)
    tail.set_command(elevator=0.0)
    quat = _TRUE["e2q"](0.0, math.radians(pitch_deg), 0.0)
    vel = np.array([V, 0.0, vz])
    om = np.zeros(3)
    T = 1.0 / f
    t0 = _TRUE["T0"]
    Fz = Fx = My = My_w = 0.0
    my_s = [] if want_series else None
    for i in range(n):
        Fw, Tw = m.compute_forces(np.zeros(3), quat, vel, om, t0 + i * T / n)
        Fz += float(Fw[2]); Fx += float(Fw[0]); My += float(Tw[1])
        My_w += float(Tw[1])
        if want_series:
            my_s.append(float(Tw[1]))
    _TRUE["T0"] = t0 + T                     # 测量纪律一：时基单调
    v_slip = SLIP_PER_HZ * f
    Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
    out = {"Fz": Fz / n + float(Ft[2]), "Fx": Fx / n + float(Ft[0]),
           "My": My / n + float(Tt[1]), "My_wing": My_w / n,
           "My_tail": float(Tt[1])}
    if want_series:
        out["My_series"] = my_s
    return out


# ══════════════════ 内层 A：全程曲线标定（24 点×翼/尾分量+K8 口径） ══════════════════
# 拍频扰动谱名义线（实测）：次谐波 0.525f=第八跑 0.62Hz/1.18Hz 线
# （0.0193 N·m、84°）；1f~4f=第九跑 (My−TMy) 锁频谐波 LS 拟合
# （0.01196/0.00871/0.00367/0.00208 N·m、相位 2.097/1.270/1.019/0.993 rad）。
# 逐波形缩放：根态整周期 My 序列 DFT 对名义波形 DFT 之比（限幅 [0.25,4]）。
DIST_NOM = [(0.525, 0.01930, math.radians(84.0)),
            (1.0, 0.01196, 2.097), (2.0, 0.00871, 1.270),
            (3.0, 0.00367, 1.019), (4.0, 0.00208, 0.993)]
_NOM_BEAT = {}


def _nom_beat_dft():
    """名义波形根态 My 序列 DFT（每进程惰性缓存，作缩放基准）。"""
    if not _NOM_BEAT:
        obj0 = calibrate(dict(NOMINAL_WAVE))
        if "pruned" in obj0:
            _NOM_BEAT.update(amp=None, root=obj0)
        else:
            _NOM_BEAT.update(amp=obj0["beat_dft"], root=obj0)
    return _NOM_BEAT["amp"]


def _interp(x, xs, ys):
    return float(np.interp(x, xs, ys))       # 端点钳位（不外推）


def calibrate(w):
    """全程曲线标定：24 点 M(θ)/Fz(θ)/Fx(θ)（n=144，翼/尾分量分开）+
    K=8 在环口径 M(θ) 曲线 + 配平根 θ*（曲线变号+二分）+ 同网格斜率
    （f/V/vz 探针 + 曲线差分 θ 向）+ 根态 DFT。返回含判据①需求。"""
    _set_waveform(w)
    V, f = w["V"], w["freq"]
    n_c = len(CURVE_DEG)
    M_tot = np.empty(n_c); M_wd = np.empty(n_c)
    Fz_c = np.empty(n_c); Fx_c = np.empty(n_c)
    M_k8 = np.empty(n_c)
    for i, th in enumerate(CURVE_DEG):
        r = cycle_eval(V, f, float(th), 0.0, NC)
        M_tot[i] = r["My"]; M_wd[i] = r["My_wing"]
        Fz_c[i] = r["Fz"]; Fx_c[i] = r["Fx"]
        r8 = cycle_eval(V, f, float(th), 0.0, N_K8)
        M_k8[i] = r8["My"]

    sgn = np.sign(M_tot)
    brackets = []
    for i in range(n_c - 1):
        if M_tot[i] == 0.0 or sgn[i] * sgn[i + 1] < 0:
            brackets.append((float(CURVE_DEG[i]), float(CURVE_DEG[i + 1])))
    quasi_root = not brackets
    v_slip0 = SLIP_PER_HZ * f
    veff0 = min(VEFF_CAP, math.sqrt(V * V + v_slip0 * v_slip0))
    b_trim_max = K_TAIL_ELEV * veff0 * veff0 * DELTA_MAX   # 满偏配平权限（veff 当量）
    if quasi_root:
        # 准根族：曲线在全程内不变号但存在近零极小（曲线正/负偏置）——
        # 升降舵偏置可配平（判据①按需求裁决）。物理剪枝=满偏也配不平。
        j = int(np.argmin(np.abs(M_tot)))
        my_min = float(M_tot[j])
        if abs(my_min + MY_DC) > b_trim_max:
            return {"wave": dict(w), "pruned": "untrimmable_full_elevator",
                    "My_min_abs": abs(my_min)}
        th_star_deg = float(CURVE_DEG[j])       # 网格分辨率 2.17°（记档）
    else:
        th_star_deg = None
    if not quasi_root:
        # 多变号：取根处 Fz 最接近 W 的变号区间（41 班口径）
        best = None
        for a0, b0 in brackets:
            thm = 0.5 * (a0 + b0)
            fz0 = _interp(thm, CURVE_DEG, Fz_c)
            if best is None or abs(fz0 - WEIGHT) < best[0]:
                best = (abs(fz0 - WEIGHT), a0, b0)
        _, a, b = best
        fa = _interp(a, CURVE_DEG, M_tot)
        for _ in range(7):                    # 曲线引导二分（同 n 同网格）
            mid = 0.5 * (a + b)
            fm = cycle_eval(V, f, mid, 0.0, NC)["My"]
            if fa * fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
        th_star_deg = 0.5 * (a + b)

    root = cycle_eval(V, f, th_star_deg, 0.0, NC, want_series=True)
    # 偏置态（全部 n=144，同相位网格）：f±宽跨度、V+0.5、vz=−0.5 单侧负向
    span = max(0.10, 0.08 * f)
    st_fp = cycle_eval(V, f + span, th_star_deg, 0.0, NC)
    st_fm = cycle_eval(V, max(0.3, f - span), th_star_deg, 0.0, NC)
    st_vp = cycle_eval(V + 0.5, f, th_star_deg, 0.0, NC)
    st_vz = cycle_eval(V, f, th_star_deg, -0.5, NC)
    dfz_df = (st_fp["Fz"] - st_fm["Fz"]) / (f + span - max(0.3, f - span))
    dfx_df = (st_fp["Fx"] - st_fm["Fx"]) / (f + span - max(0.3, f - span))
    # θ 向斜率：同网格曲线在根邻域的中央差分（跨根两侧最近格点）
    i0 = int(np.searchsorted(CURVE_DEG, th_star_deg))
    i0 = min(max(i0, 1), n_c - 1)
    dth = math.radians(CURVE_DEG[i0] - CURVE_DEG[i0 - 1])
    k_th = -(M_tot[i0] - M_tot[i0 - 1]) / dth
    dfz_dth = (Fz_c[i0] - Fz_c[i0 - 1]) / dth
    dfx_dth = (Fx_c[i0] - Fx_c[i0 - 1]) / dth
    # 判据①三口径需求（先于数据记档的口径表，见模块头注①）
    my_res = float(root["My"])
    demand = abs(my_res + MY_DC)              # 主口径：全程曲线在配平根+实测直流
    demand_wing = abs(float(root["My_wing"]) + MY_DC)   # 第十六跑字面口径（翼+驱动）
    demand_k8 = abs(_interp(th_star_deg, CURVE_DEG, M_k8) + MY_DC)  # K=8 在环口径
    auth_veff = K_TAIL_ELEV * veff0 * veff0 * DELTA_MAX   # 该工作点 veff 当量权限
    out = {
        "wave": dict(w), "V": V, "f": f,
        "quasi_root": bool(quasi_root),
        "theta_star_deg": float(th_star_deg),
        "theta_star": float(math.radians(th_star_deg)),
        "FZ0": float(root["Fz"]), "FX0": float(root["Fx"]),
        "My_res": my_res, "My_wing_res": float(root["My_wing"]),
        "My_tail_res": float(root["My_tail"]),
        "K_TH": float(k_th), "DFZ_DTH": float(dfz_dth),
        "DFX_DTH": float(dfx_dth),
        "DFZ_DF": float(np.clip(dfz_df, -0.20, 0.20)),
        "DFX_DF": float(np.clip(dfx_df, -1.50, 1.50)),
        "DFZ_DV": (st_vp["Fz"] - root["Fz"]) / 0.5,
        "DFX_DV": (st_vp["Fx"] - root["Fx"]) / 0.5,
        "DFZ_DVZ": (st_vz["Fz"] - root["Fz"]) / (-0.5),
        "DFX_DVZ": (st_vz["Fx"] - root["Fx"]) / (-0.5),
        "DMY_DVZ": (st_vz["My"] - root["My"]) / (-0.5),
        "demand_static": float(demand),
        "demand_wing": float(demand_wing),
        "demand_k8": float(demand_k8),
        "elev_auth_veff": float(auth_veff),
        "demand_over_auth_veff": float(demand / max(0.7 * auth_veff, 1e-9)),
        "veff0": float(veff0),
        "curve": {"theta_deg": [round(float(x), 3) for x in CURVE_DEG],
                  "M_tot": [round(float(x), 5) for x in M_tot],
                  "M_wing": [round(float(x), 5) for x in M_wd],
                  "M_k8": [round(float(x), 5) for x in M_k8],
                  "Fz": [round(float(x), 5) for x in Fz_c],
                  "Fx": [round(float(x), 5) for x in Fx_c]},
    }
    ser = np.asarray(root["My_series"])
    n_s = len(ser)
    ks = np.arange(n_s)
    harm = []
    for k in (1, 2, 3, 4):
        ak = float(2.0 / n_s * np.sum(ser * np.sin(2 * math.pi * k * ks / n_s)))
        bk = float(2.0 / n_s * np.sum(ser * np.cos(2 * math.pi * k * ks / n_s)))
        harm.append((float(np.hypot(ak, bk)), float(math.atan2(bk, ak))))
    out["beat_dft"] = harm
    out["fzw_static"] = out["FZ0"] / WEIGHT
    out["pitch_heave_loop_gain"] = float(
        abs(out["DMY_DVZ"] / max(out["K_TH"], 1e-6))
        * abs(out["DFZ_DTH"] / min(out["DFZ_DVZ"], -1e-6)))
    return out


def attach_beat(obj):
    """扰动谱缩放（锚定实测线），返回带 obj['beat'] 的锚定映射。"""
    try:
        nom = _nom_beat_dft()
    except Exception:                           # noqa: BLE001
        nom = None
    lines = [DIST_NOM[0]]
    for i, k in enumerate((1, 2, 3, 4)):
        _kk, a_nom, phi_nom = DIST_NOM[i + 1]
        if nom:
            ratio = obj["beat_dft"][i][0] / max(nom[i][0], 1e-6)
            ratio = min(4.0, max(0.25, ratio))
        else:
            ratio = 1.0
        lines.append((float(k), a_nom * ratio, phi_nom))
    obj["beat"] = lines
    return obj


def _prune_static(obj):
    """静态剪枝（实测硬界，非假设收窄）：驱动限位 ±1.4 rad、姿态可达、
    刚度窗。41 班 |MEAN|+幅值≤50° 为保守假设，本班弃用；41 班
    K_TH∈[0.02,3.0] 与 |M_min|>0.03 剪枝同样属未测收窄，本班改为：
    准根族准入（判据①裁决），刚度窗放宽到闭环可镇定域
    （|K_TH|≤KP_R·KP_A_max≈0.40 的负侧留裕 +8 正上限）。"""
    w = obj["wave"]
    peak_phi = abs(w["mean_deg"]) + w["amplitude_deg"]
    if peak_phi > math.degrees(PHI_DRIVE_LIMIT):
        return "actuator_phi_peak_gt_drivelimit"
    if not (-0.10 <= obj["theta_star"] <= 0.52):
        return "theta_star_out_of_tilt_reach"
    if not (-0.45 <= obj["K_TH"] <= 8.0):
        return "pitch_stiffness_degenerate"
    return None


# ══════════════════ 内层 B：动态标定（映射释放 + 真模型 K=8 释放） ══════════════════
def dyn_release(obj, n_strokes=12, dt=0.001):
    """锚定映射垂向释放（俯仰钉固 θ*、速度钉固 V0——开环自由俯仰在
    静态斜率口径下环路增益普遍>1 静态发散，41 班发现，俯仰须闭环镇定）。
    垂速/高度自由、爬升守卫生效：末 6 拍逐拍周期平均 Fz/W 中位+定常 vz*。"""
    w = obj["wave"]
    f0 = w["freq"]
    T = 1.0 / f0
    n_steps = int(round(n_strokes * T / dt))
    vz, z = 0.0, 3.0
    stroke_fz = []
    diverged = False
    cur_sum, cur_n, stroke_idx = 0.0, 0, 1
    for i in range(n_steps):
        t = (i + 1) * dt
        fz = obj["FZ0"] + obj["DFZ_DVZ"] * vz
        if vz > 0.0:
            fz *= 1.0 / (1.0 + (vz / GUARD_VZ) ** 2)
        vz += (fz - WEIGHT) / MASS * dt
        z += vz * dt
        if abs(vz) > 25.0:                   # 正 ∂Fz/∂vz 指数发散（负垂向阻尼）
            diverged = True
            break
        cur_sum += fz; cur_n += 1
        if t >= stroke_idx * T - 0.5 * dt:
            stroke_fz.append(cur_sum / max(cur_n, 1) / WEIGHT)
            cur_sum, cur_n = 0.0, 0
            stroke_idx += 1
    tail_strokes = stroke_fz[-6:]
    if diverged or not tail_strokes:
        return {"fzw_dyn_med": None, "fzw_dyn_diverged": True,
                "vz_star": float(vz), "z_end": float(z),
                "grounded": bool(z < 0.2)}
    return {"fzw_dyn_med": float(np.median(tail_strokes)) if tail_strokes else None,
            "fzw_dyn_series": [round(v, 4) for v in tail_strokes],
            "vz_star": float(vz), "z_end": float(z),
            "grounded": bool(z < 0.2)}


def true_release(obj, n_strokes=10, dt=0.004):
    """event_series_6dof 式真模型纵向短积分（终选复核），力口径=在环
    aero_solver 的 K=8 同步周期平均（第九跑 Frz 列同源）；俯仰钉固 θ*、
    速度钉固 V0（与 dyn_release 同一隔离口径）。逐拍周期平均 Fz/W。"""
    m3d, tail = _model_pair()
    w = obj["wave"]
    f0, V0 = w["freq"], w["V"]
    _set_waveform(w)
    m3d.set_wing_command(frequency=f0)          # 测量纪律二：显式设频（一次）
    T = 1.0 / f0
    n_steps = int(round(n_strokes * T / dt))
    th, vz, z = obj["theta_star"], 0.0, 3.0
    quat = _TRUE["e2q"](0.0, th, 0.0)
    v_slip = SLIP_PER_HZ * f0
    t = _TRUE["T0"]
    _TRUE["T0"] += n_strokes * T + T
    wl, wr = m3d.wing_left, m3d.wing_right
    stroke_fz, stroke_my = [], []
    cur_fz, cur_my, cur_n, stroke_idx = 0.0, 0.0, 0, 1
    for i in range(n_steps):
        vel = np.array([V0, 0.0, vz])
        om = np.zeros(3)
        snap = ((wl._tau_state, wl._t_prev), (wr._tau_state, wr._t_prev))
        Fz_s = My_s = 0.0
        for k8 in range(8):
            F, Tq = m3d.compute_forces(np.zeros(3), quat, vel, om,
                                       t + k8 * T / 8.0)
            Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
            Fz_s += float(F[2] + Ft[2])
            My_s += float(Tq[1] + Tt[1])
        (wl._tau_state, wl._t_prev), (wr._tau_state, wr._t_prev) = snap
        Fz, My = Fz_s / 8.0, My_s / 8.0
        vz += (Fz - WEIGHT) / MASS * dt
        z += vz * dt
        t += dt
        cur_fz += Fz; cur_my += My; cur_n += 1
        if (i + 1) * dt >= stroke_idx * T - 0.5 * dt:
            stroke_fz.append(cur_fz / cur_n / WEIGHT)
            stroke_my.append(cur_my / cur_n)
            cur_fz, cur_my, cur_n = 0.0, 0.0, 0
            stroke_idx += 1
    tail6 = stroke_fz[-6:]
    return {"fzw_true_med": float(np.median(tail6)) if tail6 else None,
            "fzw_true_series": [round(v, 4) for v in tail6],
            "vz_star_true": float(vz),
            "My_dc_dyn": float(np.median(stroke_my[-6:])) if stroke_my else None,
            "z_end": float(z), "grounded": bool(z < 0.2)}


def calibrate_full(w):
    """标定+扰动谱+剪枝+动态释放（判据②内层剪枝），标定失败的行保留
    剪枝原因与已得字段（判据级诊断用）。"""
    try:
        obj = calibrate(w)
    except Exception as exc:                    # noqa: BLE001
        return {"wave": dict(w), "pruned": f"calibrate_error:{type(exc).__name__}"}
    obj.setdefault("wave", dict(w))
    if "pruned" in obj:
        return obj
    pr = _prune_static(obj)
    if pr:
        obj["pruned"] = pr
        return obj
    attach_beat(obj)
    obj.update(dyn_release(obj))
    return obj


# ══════════════════ 外层：全程曲线查表闭环 60 s（零线性外推） ══════════════════
def closed_loop(obj, ctrl, t_sim=T_SIM, dt=DT_CL, want_series=False, ov=None):
    """60 s 闭环。对象=内层标定的 Fz(θ)/Fx(θ)/M(θ) 全程曲线查表（端点
    钳位、出程计数）+ f/V/vz 同网格斜率 + 扰动谱 + 守卫 + 实测直流
    dc_wave=My_res+MY_DC + veff 调度舵效（对象侧=控制器侧调度，无 0.49
    高估）。ov=对象覆写：B_ELEV_FIXED（固定舵效档）/ MY_DC / GUARD_VZ /
    DIST_SCALE / NO_DC。"""
    ov = ov or {}
    my_dc = ov.get("MY_DC", MY_DC)
    b_fixed = ov.get("B_ELEV_FIXED")            # None=veff 调度（本班主口径）
    guard_vz = ov.get("GUARD_VZ", GUARD_VZ)
    dist_s = ov.get("DIST_SCALE", 1.0)
    no_dc = ov.get("NO_DC", False)
    w = obj["wave"]
    f0, V0, th_star = w["freq"], w["V"], obj["theta_star"]
    kp_a, pdamp, ki_a = ctrl["KP_A"], ctrl["PITCH_DAMP"], ctrl["KI_A"]
    gate, tau, aff = ctrl["GATE"], ctrl["TAU"], ctrl["AFF"]
    kvf = -ctrl["AKV_F"]
    f_lo = min(1.0, f0)                  # f0<1.0 时按第十五跑建议下探 AP 下沿
    v_free, v_lock = V0, V0 - 0.5        # 低速下压衰减域随工作点平移
    lines = [(kk, a * dist_s, p) for kk, a, p in obj["beat"]]
    th_g = np.radians(obj["curve"]["theta_deg"])
    m_c = np.asarray(obj["curve"]["M_tot"], dtype=float)
    fz_c = np.asarray(obj["curve"]["Fz"], dtype=float)
    fx_c = np.asarray(obj["curve"]["Fx"], dtype=float)
    dc_wave = 0.0 if no_dc else obj["My_res"] + my_dc
    th, q = th_star, 0.0
    vx, vz, z = V0, 0.0, 3.0
    freq, ei, pitch_i = f0, 0.0, 0.0
    pd_last, qf, vxf = th_star, 0.0, V0
    psi = [0.0] * len(lines)
    n_steps = int(round(t_sim / dt))
    crash_t = None
    b_out = min(1.0, dt / OUTER_FILT_TAU)
    n_win = z_in = th_in = oob = 0
    fzw_l, z_l, th_l, el_l, f_l, vzw_l = [], [], [], [], [], []
    for i in range(n_steps):
        t = (i + 1) * dt
        vxf += b_out * (vx - vxf)
        for j, (kk, _a, _p) in enumerate(lines):
            psi[j] += 2.0 * math.pi * kk * freq * dt
        # ── TECS 巡航分支 ──
        vxd = V0 - TECS_ZV_K * (z - Z_REF)
        vxd = min(max(vxd, max(V_CMD_MIN, 0.5 * V0)), 1.5 * V0)
        e_pot = G_ACC * (Z_REF - z)
        e_kin = 0.5 * (vxd * vxd - vxf * vxf)
        e_E = e_pot + e_kin
        e_B = min(max(e_pot - e_kin, -60.0), 60.0)
        if f_lo * 1.01 < freq < FREQ_AP_MAX * 0.99:
            ei = min(max(ei + e_E * dt, -60.0), 60.0)
        f_raw = f0 + KF_P_FIXED * e_E + KF_I_FIXED * ei + kvf * (vxd - vxf)
        freq = min(max(f_raw, f_lo), FREQ_AP_MAX)
        freq = min(max(freq, freq - FREQ_SLEW * dt), freq + FREQ_SLEW * dt)
        # ── 俯仰指令链 ──
        pitch_tgt = th_star + TECS_KP_E * e_B - TECS_KD_V * vz
        pitch_tgt = min(max(pitch_tgt, -TILT_MAX), TILT_MAX)
        if pitch_tgt < th_star:
            fade = min(max((vxf - v_lock) / max(v_free - v_lock, 1e-6), 0.0), 1.0)
            pitch_tgt = max(pitch_tgt,
                            th_star - (fade * TILT_MAX + (1 - fade) * V_DIVE_ALLOW))
        d = min(max(pitch_tgt - pd_last, -PITCH_DES_SLEW * dt),
                PITCH_DES_SLEW * dt)
        pd_last += d
        # ── 姿态外环 + 条件积分 + 内环 ──
        perr = (pd_last - th + math.pi) % (2.0 * math.pi) - math.pi
        if abs(perr) < gate:
            pitch_i = min(max(pitch_i + perr * dt, -KI_A_MAX), KI_A_MAX)
        wyd = kp_a * perr + ki_a * pitch_i
        a_in = min(1.0, dt / max(tau, 1e-3))
        qf += a_in * (q - qf)
        my_dem = KP_R * (wyd - pdamp * qf)
        # ── 尾舵调度（veff 当量）+ 攻角前馈 ──
        v_slip = SLIP_PER_HZ * freq
        veff = min(VEFF_CAP, math.sqrt(max(vx * vx, 0.0) + v_slip * v_slip))
        b_sched = K_TAIL_ELEV * veff * veff
        b_obj = b_fixed if b_fixed else b_sched
        sb, cb = math.sin(th), math.cos(th)
        vbx = cb * vxf + sb * vz + v_slip
        vbz = sb * vxf + cb * vz
        alpha_hat = math.atan2(vbz, max(vbx, 0.1))
        elev = -my_dem / max(b_sched, 1e-6) - aff * alpha_hat
        elev = min(max(elev, -DELTA_MAX), DELTA_MAX)
        # ── 对象：全程曲线查表 + 斜率 + 扰动谱 + 守卫（零线性外推） ──
        th_q = th if th_g[0] <= th <= th_g[-1] else (th_g[0] if th < th_g[0] else th_g[-1])
        if th_q != th:
            oob += 1
        fz = (float(np.interp(th_q, th_g, fz_c))
              + obj["DFZ_DV"] * (vx - V0)
              + obj["DFZ_DF"] * (freq - f0)
              + obj["DFZ_DVZ"] * vz)
        if vz > 0.0:
            fz *= 1.0 / (1.0 + (vz / guard_vz) ** 2)
        fx = (float(np.interp(th_q, th_g, fx_c))
              + obj["DFX_DV"] * (vx - V0)
              + obj["DFX_DF"] * (freq - f0)
              + obj["DFX_DVZ"] * vz)
        m_beat = sum(a * math.sin(psi[j] + p)
                     for j, (_kk, a, p) in enumerate(lines))
        m_net = (float(np.interp(th_q, th_g, m_c)) + dc_wave + m_beat
                 - C_Q * q + obj["DMY_DVZ"] * vz - b_obj * elev)
        # ── 积分 ──
        vz += fz / MASS * dt - G_ACC * dt
        vx += fx / MASS * dt
        q += m_net / IYY * dt
        th += q * dt
        z += vz * dt
        # ── 判据统计（t>5 s 窗）──
        if t > 5.0:
            n_win += 1
            if 2.0 <= z <= 4.0:
                z_in += 1
            if abs(th - th_star) <= 0.05:
                th_in += 1
            fzw_l.append(fz / WEIGHT)
            z_l.append(z); th_l.append(abs(th - th_star)); el_l.append(elev)
            f_l.append(freq); vzw_l.append(vz)
        if (t > 1.0 and z < Z_CRASH) or abs(th) > 1.5 or z > 20.0 \
                or abs(vx) > 10.0 or not (th == th and z == z):
            crash_t = t
            break
    fzw_arr = np.asarray(fzw_l) if fzw_l else np.array([0.0])
    el_arr = np.asarray(el_l) if el_l else np.array([0.0])
    f_arr = np.asarray(f_l) if f_l else np.array([f0])
    f_tol = max(0.20, 0.15 * f0)
    f_med = float(np.median(f_arr))
    rail_hi = float(np.mean(f_arr >= 3.45)) if len(f_arr) else 1.0
    rail_lo = float(np.mean(f_arr <= f_lo + 0.05)) if len(f_arr) else 1.0
    m = {
        "crash_t": crash_t, "t_end": float((i + 1) * dt),
        "z_frac": z_in / max(n_win, 1),
        "th_frac": th_in / max(n_win, 1),
        "fzw_med": float(np.median(fzw_arr)),
        "fzw_min": float(fzw_arr.min()),
        "elev_rms_frac": float(np.sqrt((el_arr ** 2).mean()) / DELTA_MAX),
        "elev_sat_frac": float(np.mean(np.abs(el_arr) >= 0.295)) if len(el_arr) else 0.0,
        "z_min": float(min(z_l)) if z_l else 0.0,
        "th_rms": float(np.sqrt((np.asarray(th_l) ** 2).mean())) if th_l else 9.9,
        "vz_med": float(np.median(vzw_l)) if vzw_l else 0.0,
        "f_med": f_med,
        "f_dev": abs(f_med - f0),
        "f_rail_hi_frac": rail_hi, "f_rail_lo_frac": rail_lo,
        "theta_oob_frac": oob / max(n_steps, 1),
        "z_end": float(z),
    }
    if want_series and len(z_l) >= 4:
        keep = max(1, len(z_l) // 300)
        m["series"] = {"fzw": [round(v, 4) for v in fzw_l[::keep]],
                       "z": [round(v, 3) for v in z_l[::keep]],
                       "th_err": [round(v, 4) for v in th_l[::keep]],
                       "el": [round(v, 4) for v in el_l[::keep]]}
    return m


def judge_c2(m):   return m["fzw_med"] >= FZW_GATE
def judge_c3(m):   return (m["crash_t"] is None and m["z_frac"] >= ZBAND_OCC_GATE
                           and m["z_min"] >= ZMIN_GATE)
def judge_c4(m):   return m["th_frac"] >= TH_OCC_GATE
def judge_c5(m):   return m["elev_rms_frac"] <= ELEV_RMS_GATE
def judge_c6(m, f0):
    return (m["f_dev"] <= max(0.20, 0.15 * f0)
            and m["f_rail_hi_frac"] <= FREQ_RAIL_FRAC
            and m["f_rail_lo_frac"] <= FREQ_RAIL_FRAC)


def judge_all(obj, m):
    c1 = obj["demand_static"] <= DEMAND_GATE
    c2, c3, c4, c5 = judge_c2(m), judge_c3(m), judge_c4(m), judge_c5(m)
    c6 = judge_c6(m, obj["wave"]["freq"])
    return {"c1_static_moment": bool(c1), "c2_fzw": bool(c2),
            "c3_zband": bool(c3), "c4_track": bool(c4),
            "c5_elev": bool(c5), "c6_freq": bool(c6),
            "all6": bool(c1 and c2 and c3 and c4 and c5 and c6),
            "c2to6": bool(c2 and c3 and c4 and c5 and c6)}


def cost(m, f0):
    """排序代价（控制器整定用）：判据②~⑥违例归一和+存活罚+平级项
    （判据①为波形级静态量，不进控制器代价）。"""
    viol = (3.0 * max(0.0, FZW_GATE - m["fzw_med"]) / 0.05
            + 3.0 * max(0.0, ZBAND_OCC_GATE - m["z_frac"]) / 0.05
            + 3.0 * max(0.0, TH_OCC_GATE - m["th_frac"]) / 0.20
            + 2.0 * max(0.0, m["elev_rms_frac"] - ELEV_RMS_GATE) / 0.30
            + 3.0 * max(0.0, m["f_dev"] - max(0.20, 0.15 * f0)) / 0.30)
    tie = (m["th_rms"] / 0.05 + m["elev_rms_frac"] / ELEV_RMS_GATE
           + abs(m["fzw_med"] - 1.0) / 0.10 + abs(m["vz_med"]) / 1.0)
    return 100.0 * viol + 0.3 * (T_SIM - m["t_end"]) + 0.1 * tie


def _cl_worker(args):
    obj, ctrl = args
    return cost(closed_loop(obj, ctrl), obj["wave"]["freq"])


# ══════════════════ 极点配置种子（外层整定结构先验） ══════════════════
def pole_seeds(obj):
    """极点配置（对象=全程曲线在根处的同网格刚度 K_TH、实测 C_Q；舵效
    对象侧=控制器侧 veff 调度，环路增益与 veff 无关，G=KP_R）：
      q̇=−(K_TH+KP_R·kp_a)/IYY·(θ−θ*)−(C_Q+KP_R·pd)/IYY·q
    → ωn²=(K_TH+KP_R·kp_a)/IYY、2ζωn=(C_Q+KP_R·pd)/IYY。
    ωn 目标带跨拍频基波；ζ 三档含过阻尼（第十六跑指纹：大 pd 过阻尼
    慢极点）。攻角前馈按直流静态舵偏需求分档。"""
    k_th = obj["K_TH"]
    w = obj["wave"]
    th_s = obj["theta_star"]
    v_slip0 = SLIP_PER_HZ * w["freq"]
    veff0 = min(VEFF_CAP, math.sqrt(w["V"] ** 2 + v_slip0 ** 2))
    b0 = K_TAIL_ELEV * veff0 * veff0
    alpha_trim = math.atan2(math.sin(th_s) * w["V"],
                            max(math.cos(th_s) * w["V"] + v_slip0, 0.1))
    dc = obj["My_res"] + MY_DC
    aff0 = -dc / max(alpha_trim * b0, 1e-6)     # 直流静态舵偏需求的前馈当量
    seeds = []
    for f_wn in (0.8, 1.2, 1.6, 2.0, 2.4):
        wn = 2.0 * math.pi * f_wn
        for zeta in (0.5, 0.8, 1.1):
            kp = (wn * wn * IYY - k_th) / KP_R
            pd = (2.0 * zeta * wn * IYY - C_Q) / KP_R
            kp = min(max(kp, CTRL_LO["KP_A"]), CTRL_HI["KP_A"])
            pd = min(max(pd, CTRL_LO["PITCH_DAMP"]), CTRL_HI["PITCH_DAMP"])
            for aff in (0.0, min(0.5 * aff0, 0.8), min(1.0 * aff0, 0.8)):
                for akv in (0.6, 1.5, 3.0):
                    seeds.append({"KP_A": round(kp, 4),
                                  "PITCH_DAMP": round(pd, 4),
                                  "KI_A": round(min(max(kp / 8.0,
                                                        CTRL_LO["KI_A"]),
                                                   CTRL_HI["KI_A"]), 4),
                                  "GATE": 0.40, "TAU": 0.03,
                                  "AFF": round(min(max(aff, CTRL_LO["AFF"]),
                                                   CTRL_HI["AFF"]), 4),
                                  "AKV_F": akv})
    for g in (0.10, 1.00):                      # 积分门两档（第十六跑口径教训）
        for c in list(seeds[:9]):
            cc = dict(c); cc["GATE"] = g
            seeds.append(cc)
    return seeds


# ══════════════════ 采样工具 ══════════════════
def lhs(n, lo, hi, rng):
    keys = list(lo)
    u = np.zeros((n, len(keys)))
    for j in range(len(keys)):
        cuts = (np.arange(n) + rng.random(n)) / n
        rng.shuffle(cuts)
        u[:, j] = cuts
    return [{k: lo[k] + u[i, jj] * (hi[k] - lo[k])
             for jj, k in enumerate(keys)} for i in range(n)]


def norm_dist_wave(a, b):
    return sum(abs(a[k] - b[k]) / max(WAVE_HI[k] - WAVE_LO[k], 1e-9)
               for k in WAVE_KEYS)


def norm_dist_ctrl(a, b):
    ia = TAU_GRID.index(a["TAU"]) if a["TAU"] in TAU_GRID else 0
    ib = TAU_GRID.index(b["TAU"]) if b["TAU"] in TAU_GRID else 0
    d = abs(ia - ib) / (len(TAU_GRID) - 1)
    return d + sum(abs(a[k] - b[k]) / max(CTRL_HI[k] - CTRL_LO[k], 1e-9)
                   for k in CTRL_KEYS)


def _nm_one(args):
    from scipy.optimize import minimize
    obj, seed = args

    def unpack(x):
        p = {k: float(v) for k, v in zip(CTRL_KEYS, x)}
        p["TAU"] = seed["TAU"]
        return p

    bounds = [(CTRL_LO[k], CTRL_HI[k]) for k in CTRL_KEYS]
    x0 = np.array([seed[k] for k in CTRL_KEYS])
    r = minimize(lambda x: cost(closed_loop(obj, unpack(x)), obj["wave"]["freq"]),
                 x0, method="Nelder-Mead", bounds=bounds,
                 options={"maxiter": 260, "xatol": 1e-3, "fatol": 1e-4})
    p = unpack(r.x)
    p["_J"] = float(r.fun)
    return p


def _cal_worker(w):
    return calibrate_full(w)


def _true_worker(w):
    try:
        obj = calibrate(w)
        if "pruned" in obj:
            return {"error": obj["pruned"]}
        attach_beat(obj)
        return true_release(obj)
    except Exception as exc:                    # noqa: BLE001
        return {"error": f"{type(exc).__name__}:{exc}"}


def _radius_worker(args):
    """半径轴探针：波形轴=扰动波形重标定+最优控制器；控制器轴=最优波形
    +扰动控制器。判据级输出（含判据①重标定需求）。"""
    wave_try, ctrl_try, best_wave, best_ctrl = args
    try:
        if wave_try is not None:
            obj = calibrate_full(wave_try)
            if "pruned" in obj:
                return {"feasible": False, "why": "recal_pruned",
                        "pruned": obj["pruned"]}
            ctrl = best_ctrl
        else:
            obj = calibrate_full(best_wave)
            if "pruned" in obj:
                return {"feasible": False, "why": "recal_pruned"}
            ctrl = ctrl_try
        m = closed_loop(obj, ctrl)
        jd = judge_all(obj, m)
        return {"feasible": jd["all6"], "feasible_c2to6": jd["c2to6"],
                "demand_static": round(obj["demand_static"], 5),
                "fzw_med": round(m["fzw_med"], 4),
                "z_frac": round(m["z_frac"], 4), "th_frac": round(m["th_frac"], 4),
                "elev_rms_frac": round(m["elev_rms_frac"], 4),
                "f_dev": round(m["f_dev"], 4), "crash_t": m["crash_t"]}
    except Exception as exc:                    # noqa: BLE001
        return {"feasible": False, "why": f"{type(exc).__name__}"}


# ══════════════════ 摘要工具 ══════════════════
def _digest(r):
    if "pruned" in r:
        out_d = {"wave": {k: round(r["wave"][k], 4) for k in WAVE_KEYS},
                 "pruned": r["pruned"]}
        if "My_min_abs" in r:
            out_d["My_min_abs"] = round(r["My_min_abs"], 5)
        if "demand_static" in r:
            out_d["demand_static"] = round(r["demand_static"], 5)
        return out_d
    return {"wave": {k: round(r["wave"][k], 4) for k in WAVE_KEYS},
            "quasi_root": r.get("quasi_root", False),
            "theta_star_deg": round(r["theta_star_deg"], 2),
            "FZ0": round(r["FZ0"], 4), "fzw_static": round(r["fzw_static"], 4),
            "FX0": round(r["FX0"], 4), "K_TH": round(r["K_TH"], 4),
            "My_res": round(r["My_res"], 5),
            "My_wing_res": round(r["My_wing_res"], 4),
            "demand_static": round(r["demand_static"], 5),
            "demand_wing": round(r["demand_wing"], 5),
            "demand_k8": round(r["demand_k8"], 5),
            "demand_over_auth_veff": round(r["demand_over_auth_veff"], 4),
            "DFZ_DF": round(r["DFZ_DF"], 4), "DFZ_DV": round(r["DFZ_DV"], 4),
            "DFZ_DTH": round(r["DFZ_DTH"], 4),
            "DFZ_DVZ": round(r["DFZ_DVZ"], 4),
            "pitch_heave_loop_gain": round(r["pitch_heave_loop_gain"], 3),
            "fzw_dyn_med": (round(r["fzw_dyn_med"], 4)
                            if r.get("fzw_dyn_med") is not None else None),
            "vz_star": round(r["vz_star"], 4)}


def _round_metrics(m):
    return {k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in m.items() if k != "series"}


def _counter(items):
    out_c = {}
    for it in items:
        key = str(it).split(":")[0]
        out_c[key] = out_c.get(key, 0) + 1
    return out_c


def _top5(rows, key, gate, higher_ok=True, extra=None):
    """判据级最接近可行前五（值+差距）。rows 已含所需字段。"""
    tab = []
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        gap = (gate - v) if higher_ok else (v - gate)
        item = {"value": round(float(v), 5), "gap_to_gate": round(float(gap), 5)}
        item.update(extra(r) if extra else {})
        tab.append((gap, item))
    tab.sort(key=lambda x: x[0])
    return [it for _g, it in tab[:5]]


# ══════════════════ 寻优主体 ══════════════════
def run(args):
    import multiprocessing as mp
    quick = bool(args.quick)
    workers = args.workers or max(4, min(28, (os.cpu_count() or 8) - 4))
    n_stage_a = 240 if quick else int(args.n_stage_a)
    top_k = 6 if quick else 16
    n_ctrl_lhs = 96 if quick else 384
    out_path = args.out or os.path.join(PROJECT_ROOT, "logs", "full_search_43.json")
    rng = np.random.default_rng(20260925)
    log = []
    t00 = time.time()
    ctx = mp.get_context("spawn")

    # ── 阶段 A：波形×工作点 LHS + 种子 → 全程曲线标定（并行） ──
    cands = lhs(n_stage_a, WAVE_LO, WAVE_HI, rng)
    seeds = [dict(NOMINAL_WAVE), dict(BEST41_WAVE),
             {"alpha_set": 35.0, "mean_deg": 20.0, "split": 0.75,
              "pitch_lag_s": -0.04, "amplitude_deg": 35.0, "freq": 1.25, "V": 3.5},
             {"alpha_set": 35.0, "mean_deg": 20.0, "split": 0.75,
              "pitch_lag_s": -0.04, "amplitude_deg": 30.0, "freq": 1.55, "V": 4.5},
             {"alpha_set": 42.0, "mean_deg": 11.0, "split": 0.76,
              "pitch_lag_s": -0.05, "amplitude_deg": 24.0, "freq": 2.24, "V": 4.07},
             {"alpha_set": 25.0, "mean_deg": 5.0, "split": 0.6,
              "pitch_lag_s": -0.02, "amplitude_deg": 25.0, "freq": 2.8, "V": 5.0},
             {"alpha_set": 45.0, "mean_deg": 25.0, "split": 0.9,
              "pitch_lag_s": -0.06, "amplitude_deg": 40.0, "freq": 1.0, "V": 3.0},
             {"alpha_set": 20.0, "mean_deg": -15.0, "split": 0.45,
              "pitch_lag_s": 0.0, "amplitude_deg": 20.0, "freq": 3.2, "V": 2.5},
             {"alpha_set": 30.0, "mean_deg": 0.0, "split": 0.5,
              "pitch_lag_s": 0.0, "amplitude_deg": 15.0, "freq": 3.4, "V": 5.5},
             {"alpha_set": 50.0, "mean_deg": 30.0, "split": 0.4,
              "pitch_lag_s": 0.02, "amplitude_deg": 15.0, "freq": 0.7, "V": 2.0}]
    for s in seeds:
        if all(norm_dist_wave(s, c) > 0.05 for c in cands):
            cands.append(s)
    t0 = time.time()
    with ctx.Pool(workers) as pool:
        cal_rows = pool.map(_cal_worker, cands, chunksize=2)
    log.append({"stage": "A_curve_calibrate", "n": len(cands),
                "workers": workers, "sec": round(time.time() - t0, 1)})
    n_pruned_a = sum(1 for r in cal_rows if "pruned" in r)
    live = [r for r in cal_rows if "pruned" not in r]
    nom_row = next((r for r in cal_rows
                    if all(abs(r["wave"][k] - NOMINAL_WAVE[k]) < 1e-6
                           for k in WAVE_KEYS)), None)
    print(f"[A] 全程曲线标定 {len(cands)} 组 {time.time()-t0:.0f}s："
          f"剪枝 {n_pruned_a}，幸存 {len(live)}", flush=True)
    if nom_row and "pruned" not in nom_row:
        print(f"[A·锚定] 名义点 θ*={nom_row['theta_star_deg']:.2f}° "
              f"(41班 22.02) FZ0={nom_row['FZ0']:.4f} N "
              f"({nom_row['fzw_static']:.4f}W, 41班 2.0828/0.9875) "
              f"K_TH={nom_row['K_TH']:.4f} (41班 0.0657) "
              f"∂Fz/∂V={nom_row['DFZ_DV']:.4f} (1.2438) "
              f"∂Fz/∂vz={nom_row['DFZ_DVZ']:.4f} (−0.2067) "
              f"∂Fz/∂θ={nom_row['DFZ_DTH']:.4f} (n=288 段值 −0.49) "
              f"判据①需求={nom_row['demand_static']:.5f} "
              f"(门槛 {DEMAND_GATE:.5f})", flush=True)

    # ── 阶段 A2：序贯加密（近失格局部再采样 + 对角切片，堵窄域漏检） ──
    t0 = time.time()
    near = [r for r in live if (
        (r["fzw_dyn_med"] is not None and 0.90 <= r["fzw_dyn_med"] < FZW_GATE
         and abs(r["vz_star"]) <= 1.2)
        or (DEMAND_GATE < r["demand_static"] <= DEMAND_GATE + 0.012))]   # 判据①近失格
    near.sort(key=lambda r: -min(r["fzw_dyn_med"] if r["fzw_dyn_med"] else 0.0,
                                 1.0 / (1.0 + r["demand_static"])))
    near = near[:80]
    ref_cands = []
    for r in near:
        for _ in range(8 if quick else 12):
            w2 = {}
            for k in WAVE_KEYS:
                hr = 0.12 * (WAVE_HI[k] - WAVE_LO[k])
                v = r["wave"][k] + rng.uniform(-hr, hr)
                w2[k] = min(WAVE_HI[k], max(WAVE_LO[k], v))
            ref_cands.append(w2)
    # 对角切片：幸存最优点↔名义点/盒角连线 + 幸存点两两连线
    live_sorted = sorted(live, key=lambda r: (abs(r["vz_star"])
                                              + 0.3 * abs(r["FX0"])))
    anchors_diag = [dict(NOMINAL_WAVE), dict(BEST41_WAVE)] + \
        [{k: (WAVE_LO[k] if i2 % 2 == 0 else WAVE_HI[k]) for k in WAVE_KEYS}
         for i2 in range(8)]
    for top in live_sorted[:6]:
        for an in anchors_diag:
            for s_frac in (0.2, 0.35, 0.5, 0.65, 0.8):
                w2 = {k: top["wave"][k] + s_frac * (an[k] - top["wave"][k])
                      for k in WAVE_KEYS}
                ref_cands.append(w2)
    for i1, ra in enumerate(live_sorted[:4]):
        for rb in live_sorted[i1 + 1:6]:
            for s_frac in (0.3, 0.5, 0.7):
                w2 = {k: ra["wave"][k] + s_frac * (rb["wave"][k] - ra["wave"][k])
                      for k in WAVE_KEYS}
                ref_cands.append(w2)
    seen = set()
    ref_uniq = []
    for w2 in ref_cands:
        key_t = tuple(round(w2[k], 5) for k in WAVE_KEYS)
        if key_t not in seen:
            seen.add(key_t)
            ref_uniq.append(w2)
    if ref_uniq:
        with ctx.Pool(workers) as pool:
            ref_rows = pool.map(_cal_worker, ref_uniq, chunksize=2)
        cal_rows += ref_rows
        n_pruned_r = sum(1 for r in ref_rows if "pruned" in r)
        live = [r for r in cal_rows if "pruned" not in r]
        log.append({"stage": "A2_refine_diagonals", "n": len(ref_uniq),
                    "pruned": n_pruned_r, "near_seed": len(near),
                    "sec": round(time.time() - t0, 1)})
        print(f"[A2] 边界加密+对角切片 {len(ref_uniq)} 组 "
              f"{time.time()-t0:.0f}s：剪枝 {n_pruned_r}，幸存累计 {len(live)}",
              flush=True)

    # ── 排序与终选（判据①过且动态不发散者优先入池，主序 |vz*|+Fx 余项，
    #     参数空间多样性） ──
    def _pref(r):
        ok_c1 = (r["demand_static"] <= DEMAND_GATE
                 and r.get("fzw_dyn_med") is not None)
        return 0 if ok_c1 else 1
    live.sort(key=lambda r: (_pref(r),
                             abs(r["vz_star"]) + 0.3 * abs(r["FX0"]),
                             -(r["fzw_dyn_med"] if r.get("fzw_dyn_med") is not None else -9.9)))
    chosen = []
    for r in live:
        if all(norm_dist_wave(r["wave"], c["wave"]) > 0.25 for c in chosen):
            chosen.append(r)
        if len(chosen) >= top_k * 2:
            break
    chosen = chosen[:top_k * 2]

    # ── 阶段 A3：终选真模型 K=8 释放复核（动态列口径） ──
    n_before = len(chosen)
    t0 = time.time()
    if chosen:
        with ctx.Pool(min(workers, len(chosen))) as pool:
            true_rows = pool.map(_true_worker, [r["wave"] for r in chosen],
                                 chunksize=1)
        keep = []
        for r, tr in zip(chosen, true_rows):
            r["true_release"] = tr
            med = tr.get("fzw_true_med")
            if med is not None and med >= FZW_GATE and not tr.get("grounded"):
                keep.append(r)
        dropped = n_before - len(keep)
        if keep:
            chosen = keep
        else:
            chosen = chosen[:top_k]            # 全灭时按主序保留（诊断用）
            dropped = n_before - len(chosen)
    log.append({"stage": "A3_true_release", "n": n_before, "dropped": dropped,
                "sec": round(time.time() - t0, 1)})
    print(f"[A3] 真模型 K=8 释放复核幸存 {len(chosen)}/{n_before}", flush=True)

    # ── 阶段 B：逐终选控制器整定（极点配置种子+LHS 并行评分） ──
    ctrl_pool = lhs(n_ctrl_lhs, CTRL_LO, CTRL_HI, rng)
    for i, c in enumerate(ctrl_pool):
        c["TAU"] = TAU_GRID[i % len(TAU_GRID)]
    ctrl_pool += [dict(RUN9_CTRL), dict(RUN36_CTRL), dict(REC41_CTRL)]
    jobs, scored = [], {}
    for r in chosen:
        seeds_b = pole_seeds(r)
        pool_c = ctrl_pool + seeds_b
        scored[id(r)] = []
        for c in pool_c:
            jobs.append((r, c))
    t0 = time.time()
    with ctx.Pool(workers) as pool:
        Js = pool.map(_cl_worker, jobs, chunksize=8) if jobs else []
    for (r, c), J in zip(jobs, Js):
        cc = dict(c); cc["_J"] = J
        scored[id(r)].append(cc)
    for r in chosen:
        scored[id(r)].sort(key=lambda c: c["_J"])
    log.append({"stage": "B_ctrl_grid_pole_seeds", "n": len(jobs),
                "sec": round(time.time() - t0, 1)})
    print(f"[B] 控制器网格（极点种子+LHS+对照） {len(jobs)} 组 "
          f"{time.time()-t0:.0f}s", flush=True)

    # ── 阶段 B2：每终选前二种子 Nelder-Mead 细化 ──
    nm_jobs = []
    for r in chosen:
        seen_s = []
        for c in scored[id(r)]:
            if all(norm_dist_ctrl(c, s) > 0.08 for s in seen_s):
                seen_s.append(c)
            if len(seen_s) >= 2:
                break
        for s in seen_s:
            nm_jobs.append((r, s))
    t0 = time.time()
    if nm_jobs:
        with ctx.Pool(min(workers, len(nm_jobs))) as pool:
            refined = pool.map(_nm_one, nm_jobs, chunksize=1)
        for (r, _s), p in zip(nm_jobs, refined):
            scored[id(r)].append(p)
        for r in chosen:
            scored[id(r)].sort(key=lambda c: c["_J"])
    log.append({"stage": "B2_nelder_mead", "n": len(nm_jobs),
                "sec": round(time.time() - t0, 1)})
    print(f"[B2] NM 细化 {len(nm_jobs)} 组 {time.time()-t0:.0f}s", flush=True)

    # ── 阶段 C：判据全量评估（每终选前八控制器，τ 展开） ──
    finals = []
    for r in chosen:
        cands_c = list(scored[id(r)][:6])
        for c in list(cands_c[:2]):
            for ti in TAU_GRID:
                if ti != c["TAU"]:
                    cc = dict(c); cc["TAU"] = ti
                    cands_c.append(cc)
        for c in cands_c:
            m = closed_loop(r, c, want_series=(len(finals) < 24))
            jd = judge_all(r, m)
            finals.append({
                "wave": {k: round(r["wave"][k], 4) for k in WAVE_KEYS},
                "ctrl": {k: round(c[k], 4) for k in CTRL_KEYS}
                        | {"TAU": c["TAU"],
                           "TECS_KV_F_signed": round(-c["AKV_F"], 3)},
                "obj_digest": _digest(r),
                "demand_static": round(r["demand_static"], 5),
                "metrics": _round_metrics(m), "judge": jd,
                "true_release": r.get("true_release"),
                "fzw_dyn_med": r.get("fzw_dyn_med"), "vz_star": r.get("vz_star")})
    feas_all6 = [f for f in finals if f["judge"]["all6"]]
    feas_c2to6 = [f for f in finals if f["judge"]["c2to6"]]
    rank_key = (lambda f: (3.0 * (1.0 - f["metrics"]["z_frac"])
                           + f["metrics"]["th_rms"]
                           + 5.0 * abs(f["metrics"]["fzw_med"] - 1.0)
                           + f["metrics"]["elev_rms_frac"]
                           + 0.5 * abs(f["metrics"]["z_end"] - 3.0)))
    feas_all6.sort(key=rank_key)
    feas_c2to6.sort(key=rank_key)
    print(f"[C] {len(chosen)} 终选 × 控制器 → {len(finals)} 组：六判据全过 "
          f"{len(feas_all6)}，判据②~⑥全过（条件域）{len(feas_c2to6)}", flush=True)

    # ── 判据级最接近可行前五（六张表，含差距） ──
    near_tables = {}
    near_tables["c1_static_moment"] = _top5(
        [r for r in cal_rows if "demand_static" in r],
        "demand_static", DEMAND_GATE, higher_ok=False,
        extra=lambda r: {"wave": {k: round(r["wave"][k], 3) for k in WAVE_KEYS},
                         "quasi_root": r.get("quasi_root", False),
                         "gate": round(DEMAND_GATE, 5),
                         "demand_wing": round(r.get("demand_wing", float("nan")), 5),
                         "demand_k8": round(r.get("demand_k8", float("nan")), 5)})
    near_tables["c2_fzw_dyn"] = _top5(
        [r for r in cal_rows if r.get("fzw_dyn_med") is not None],
        "fzw_dyn_med", FZW_GATE, higher_ok=True,
        extra=lambda r: {"wave": {k: round(r["wave"][k], 3) for k in WAVE_KEYS}})
    cl_evals = []
    for f in finals:
        cl_evals.append({"wave": f["wave"], "ctrl": f["ctrl"],
                         "z_frac": f["metrics"]["z_frac"],
                         "th_frac": f["metrics"]["th_frac"],
                         "elev_rms_frac": f["metrics"]["elev_rms_frac"],
                         "f_dev": f["metrics"]["f_dev"],
                         "crash_t": f["metrics"]["crash_t"]})
    near_tables["c3_zband"] = _top5(
        cl_evals, "z_frac", ZBAND_OCC_GATE, higher_ok=True,
        extra=lambda r: {"wave": r["wave"], "ctrl": r["ctrl"]})
    near_tables["c4_track"] = _top5(
        cl_evals, "th_frac", TH_OCC_GATE, higher_ok=True,
        extra=lambda r: {"wave": r["wave"], "ctrl": r["ctrl"]})
    near_tables["c5_elev"] = _top5(
        cl_evals, "elev_rms_frac", ELEV_RMS_GATE, higher_ok=False,
        extra=lambda r: {"wave": r["wave"], "ctrl": r["ctrl"]})
    near_tables["c6_freq"] = _top5(
        cl_evals, "f_dev", max(0.20, 0.15 * float(np.median(
            [f["wave"]["freq"] for f in finals])) if finals else 0.2),
        higher_ok=False, extra=lambda r: {"wave": r["wave"], "ctrl": r["ctrl"]})

    # ── 基线对照（名义波形 × 第九跑/三十六班/41推荐控制器，本班考卷） ──
    baselines = {}
    if nom_row and "pruned" not in nom_row:
        for tag, ctl in (("run9_gains", RUN9_CTRL),
                         ("run36_gains", RUN36_CTRL),
                         ("rec41_gains", REC41_CTRL)):
            m = closed_loop(nom_row, ctl)
            baselines[tag] = {"ctrl": ctl, "metrics": _round_metrics(m),
                              "judge": judge_all(nom_row, m)}

    out = {"ts": time.strftime("%Y%m%d_%H%M%S"),
           "tool": "tools/full_search_43.py",
           "quick": quick, "workers": workers, "search_log": log,
           "caliber_note": {
               "c1_static_moment": (
                   "需求=|M(θ*)+MY_DC|，M=真三维模型全程俯仰力矩曲线"
                   f"（θ −10~+40° 24 点，n=144 整周期平均，升降舵零位）在配平根 "
                   "θ*（曲线零点）处的取值；MY_DC=−0.0399 N·m 第八跑实测机翼+"
                   "驱动矩直流（第十六跑口径：名义点持配平需求 0.0865 对权限 "
                   "0.0541，直流分量即此值）；门槛=0.7×0.0541（第十五跑实测当量"
                   "权限）=0.037870 N·m。并列口径：demand_wing=|M_wing(θ*)+MY_DC|"
                   "（第十六跑字面量）、demand_k8=|M_K8(θ*)+MY_DC|（在环 K=8 同步"
                   "周期平均）、demand_over_auth_veff=需求/（0.7×该工作点 veff "
                   "当量权限 0.0145·veff²·0.30）"),
               "c2_fzw": ("动态周期平均原值口径（第九跑 Frz 列同源，含爬升守卫"
                          "与拍频反作用）；内层另有锚定映射 12 拍释放中位"
                          "（<0.95 剪枝）与终选真模型 K=8 释放复核两级"),
               "object": ("闭环对象=全程曲线查表（线性插值、端点钳位、出程"
                          "计数）+同网格斜率（f±宽跨度/V+0.5/vz−0.5 单侧）+扰动"
                          "谱（实测线锚定、逐波形 DFT 缩放）+守卫；对象侧舵效"
                          "=veff 调度 0.0145·veff²（第十五/十六跑实测回归；"
                          "0.49 档仅作变体对照）"),
               "freq_band_c6": ("中位频偏≤max(0.20 Hz,0.15·f0) 且 f≥3.45 或 "
                                "≤f_lo+0.05 样本占比各≤5%，f_lo=min(1.0,f0)")},
           "criteria": {"c1_gate_nm": round(DEMAND_GATE, 6),
                        "elev_auth_nm": ELEV_AUTH_NOM,
                        "c2_fzw_gate": FZW_GATE, "c3_z_band": [2.0, 4.0],
                        "c3_occ_gate": ZBAND_OCC_GATE, "c3_z_min_gate": ZMIN_GATE,
                        "c4_band_rad": 0.05, "c4_occ_gate": TH_OCC_GATE,
                        "c5_rms_gate": ELEV_RMS_GATE,
                        "c6_band_rule": "f_med dev<=max(0.2,0.15*f0); rails<=5%",
                        "t_sim": T_SIM, "window": "t>5 s"},
           "search_domain": {"wave_lo": WAVE_LO, "wave_hi": WAVE_HI,
                             "ctrl_lo": CTRL_LO, "ctrl_hi": CTRL_HI,
                             "tau_grid": TAU_GRID,
                             "note": ("用户令逐字给出波形五+工作点二；控制器七"
                                      "未给数值界，取 41 班域向外扩（KP_A 0.90"
                                      "→1.60、PITCH_DAMP 1.20→4.80（首跑 2.40 "
                                      "骑界后再扩）、KI_A 0.20→0.30、门 0.80"
                                      "→1.20、AFF 0.45→0.80、AKV_F 3.0→4.0、"
                                      "τ 网格 0.01~0.10）")},
           "nominal_anchor": _digest(nom_row) if nom_row else None,
           "nominal_anchor_curve": nom_row.get("curve") if nom_row else None,
           "anchor_refs": {"probe_nom_37": PROBE_NOM, "anchor_41": A41_ANCHOR},
           "baselines_nominal": baselines,
           "stageA": {"n_total": len(cal_rows), "n_pruned": n_pruned_a,
                      "prune_reasons": _counter(
                          [r.get("pruned") for r in cal_rows if "pruned" in r]),
                      "n_live": len(live),
                      "rows": [_digest(r) for r in cal_rows]},
           "finalists": finals,
           "n_feasible_all6": len(feas_all6),
           "n_feasible_c2to6": len(feas_c2to6),
           "best_set_all6": feas_all6[0] if feas_all6 else None,
           "best_set_c2to6": feas_c2to6[0] if feas_c2to6 else None,
           "alternates_c2to6": feas_c2to6[1:4],
           "near_miss_tables": near_tables}

    # ── 阶段 D：可行域+最大内切球+鲁棒半径表（首选组=全六可行，否则条件域组） ──
    primary = feas_all6[0] if feas_all6 else (feas_c2to6[0] if feas_c2to6 else None)
    if primary is not None:
        best = primary
        best_wave = {k: best["wave"][k] for k in WAVE_KEYS}
        best_ctrl = {k: best["ctrl"][k] for k in CTRL_KEYS}
        best_ctrl["TAU"] = best["ctrl"]["TAU"]
        probes = []
        for k in WAVE_KEYS:
            hr = 0.5 * (WAVE_HI[k] - WAVE_LO[k])
            for s in (-0.8, -0.5, -0.25, 0.25, 0.5, 0.8):
                v = min(WAVE_HI[k], max(WAVE_LO[k], best_wave[k] + s * hr))
                if abs(v - best_wave[k]) < 1e-9:
                    continue
                w2 = dict(best_wave); w2[k] = v
                probes.append((f"axis|{k}", round(v, 5), w2, None))
        for k in CTRL_KEYS:
            hr = 0.5 * (CTRL_HI[k] - CTRL_LO[k])
            for s in (-0.8, -0.5, -0.25, 0.25, 0.5, 0.8):
                v = min(CTRL_HI[k], max(CTRL_LO[k], best_ctrl[k] + s * hr))
                if abs(v - best_ctrl[k]) < 1e-9:
                    continue
                c2 = dict(best_ctrl); c2[k] = v
                probes.append((f"axis|{k}", round(v, 5), None, c2))
        for k in ("alpha_set", "mean_deg", "split", "pitch_lag_s",
                  "amplitude_deg"):
            for sgn in (0.95, 0.90, 1.05, 1.10):
                w2 = dict(best_wave); w2[k] = best_wave[k] * sgn
                w2[k] = min(WAVE_HI[k], max(WAVE_LO[k], w2[k]))
                probes.append((f"tol|{k}|x{sgn}", round(w2[k], 5), w2, None))
        t0 = time.time()
        with ctx.Pool(workers) as pool:
            rad_rows = pool.map(_radius_worker,
                                [(lv[2], lv[3], best_wave, best_ctrl)
                                 for lv in probes], chunksize=1)
        margins, tol_absorb, axis_rows = {}, {}, []
        for (lbl, val, _w2, _c2), rr in zip(probes, rad_rows):
            rr = dict(rr); rr["label"] = lbl; rr["value"] = val
            axis_rows.append(rr)
            if lbl.startswith("tol|"):
                tol_absorb[lbl] = bool(
                    rr.get("feasible") if feas_all6 else rr.get("feasible_c2to6"))
                continue
            key = lbl.split("|")[1]
            ok = rr.get("feasible") if feas_all6 else rr.get("feasible_c2to6")
            margins.setdefault(key, []).append((bool(ok), val))
        radius_tbl = {}
        for key, pts in margins.items():
            hr = 0.5 * ((WAVE_HI[key] - WAVE_LO[key]) if key in WAVE_KEYS
                        else (CTRL_HI[key] - CTRL_LO[key]))
            center = best_wave.get(key, best_ctrl.get(key))
            infeas_d = [abs(center - v) / hr for ok, v in pts if not ok]
            radius_tbl[key] = round(min(infeas_d), 3) if infeas_d else 1.0
        out["robustness"] = {
            "basis": "all6" if feas_all6 else "c2to6_conditional",
            "center": {"wave": best_wave,
                       "ctrl": {k: best_ctrl[k] for k in CTRL_KEYS}
                               | {"TAU": best_ctrl["TAU"]}},
            "per_axis_radius_norm": radius_tbl,
            "inscribed_radius_norm": (min(radius_tbl.values())
                                      if radius_tbl else None),
            "wave_tol_absorb": tol_absorb,
            "axis_rows": axis_rows,
            "sec": round(time.time() - t0, 1)}
        # 稳健变体（对象口径扰动；首选组不动）
        best_obj = next((r for r in chosen
                         if all(abs(r["wave"][k] - best_wave[k]) < 5e-4
                                for k in WAVE_KEYS)), None)
        if best_obj is not None:
            variants = {"V1_MyDC_0": {"MY_DC": 0.0},
                        "V2_MyDC_x1p25": {"MY_DC": MY_DC * 1.25},
                        "V3_B_elev_0p49_41ban": {"B_ELEV_FIXED": 0.49},
                        "V4_guard_2p0": {"GUARD_VZ": 2.0},
                        "V5_beat_x1p5": {"DIST_SCALE": 1.5},
                        "V6_no_dc": {"NO_DC": True}}
            vr = {}
            for tag, ov in variants.items():
                m = closed_loop(best_obj, best_ctrl, ov=ov)
                jd = judge_all(best_obj, m)
                vr[tag] = {"feasible": jd["all6"], "feasible_c2to6": jd["c2to6"],
                           "fzw_med": round(m["fzw_med"], 4),
                           "z_frac": round(m["z_frac"], 4),
                           "th_frac": round(m["th_frac"], 4),
                           "elev_rms_frac": round(m["elev_rms_frac"], 4),
                           "crash_t": m["crash_t"]}
            out["robustness"]["variants"] = vr
    out["total_sec"] = round(time.time() - t00, 1)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print(f"[done] {out['total_sec']}s → {out_path}", flush=True)

    def _print_set(tag, f):
        mm = f["metrics"]; jd = f["judge"]
        print(f"\n═══ {tag} ═══")
        print("波形+工作点:", json.dumps(f["wave"], ensure_ascii=False))
        print("控制器:", json.dumps(f["ctrl"], ensure_ascii=False))
        print(f"判据①需求 {f['demand_static']:.5f}（门槛 {DEMAND_GATE:.5f}"
              f" {'过' if jd['c1_static_moment'] else '不过'}）| "
              f"Fz/W 中位 {mm['fzw_med']:.4f} | z 带内 {mm['z_frac']*100:.1f}% "
              f"(z_min {mm['z_min']:.2f}) | θ 带内 {mm['th_frac']*100:.1f}% "
              f"| 舵 RMS {mm['elev_rms_frac']*100:.1f}% | 频偏 {mm['f_dev']:.3f} Hz "
              f"| 存活 {'60 s' if mm['crash_t'] is None else mm['crash_t']}")
    if feas_all6:
        _print_set("最优组（六判据全过）", feas_all6[0])
    elif feas_c2to6:
        print("\n═══ 六判据全过组：空 ═══")
        _print_set("条件最优组（判据②~⑥全过，判据①见诊断表）", feas_c2to6[0])
    else:
        print("\n═══ 六判据全过组：空；条件域（②~⑥）亦空 ═══"
              "（判据级最接近表见 JSON near_miss_tables）")
    if out.get("robustness"):
        print(f"内切球半径（归一化，基准 {out['robustness']['basis']}）: "
              f"{out['robustness']['inscribed_radius_norm']}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--n-stage-a", type=float, default=3200)
    ap.add_argument("--out", default=None)
    args_main = ap.parse_args()
    run(args_main)
