# -*- coding: utf-8 -*-
"""joint_wave_control_opt.py — 第四十一班：十四参数波控联立寻优（2026-09-25）。

纠正此前寻优边界错误：波形五参数不再锁死，与拍频、速度（工作点）及
控制器七参数联合寻优。变量共十四个：
  波形五  α_set 20~45°、MEAN −10~+25°、SPLIT 0.5~0.9、LAG −0.06~0 s、
          幅值 20~40°（modules/aero/sane_dickinson_3d.py 翼运动学五参数）
  工作点二 拍频 0.8~2.5 Hz、速度 2.5~4.5 m/s
  控制器七 KP_A、PITCH_DAMP、KI_A、积分门 GATE、RATE_FILT_TAU、
          攻角前馈 ALPHA_FF_GAIN、TECS_KV_F（幅值搜、符号负输出；
          KF_P/KF_I 按第三十六班最优结构置零固定，不在自由变量内）

════════════════════════════════════════════════════════════════════
考卷（闭环判据，先于数据）——60 s 闭环仿真（含拍频扰动谱+三探针耦合）：
  ① Fz/W 闭环中位 ≥ 0.95（动态周期平均口径，见下）
  ② 高度 3±1 m：t>5 s 带内占比 ≥95% 且 z_min ≥ 1.5 m、60 s 存活
  ③ 俯仰跟踪 ±0.05 rad（对该波形自身配平根 θ*）：带内占比 ≥80%
  ④ 升降舵 RMS ≤ 70% 满偏（0.30 rad → ≤0.21 rad）

关键修正（本班核心口径裁决，依据=第九跑数据流直接对账）：
  Fz/W 判据用"含拍频反作用与守卫的动态周期平均"，不用钉固开环。
  对账证据（logs/ident_stream_flight_0925_174753.csv 飞行窗 1.0<t≤5.575，
  n=412）：CSV 的 Fz 列（rate_scale 后的每触发施加值）中位 1.0254 N
  =0.4862W；Frz 列（钳位前原值=同步周期平均总力，含爬升守卫与尾翼）
  中位 2.0505 N=0.9722W；两列之比恒为 0.500（逐样本中位，n=412）——
  modules/aero_solver.py 的多触发补偿（F_apply=F_world/fpms，fpms≈2.0）
  把 Fz 列除以 2。故"十六跑 0.478~0.491"的墙值一半是簿记因子；第九跑
  实测口径的动态周期平均 Fz/W=0.972。判据① 采用 Frz 同源口径（守卫+
  尾翼+拍频反作用在内的周期平均原值）；0.5 施加簿记不进判据——该口径
  下任何波形的天花板为 0.5，判据退化为恒假，已证伪为判据口径而非
  波形能力。

两层方法：
  内层（每波形标定，并行）——真三维模型（sane_dickinson_3d+尾翼，
    feasibility_map._true_model 同源）钉固状态整周期平均（n=36 粗扫/
    72 斜率/144 终值，时基单调、显式设频纪律）：
    (a) 俯仰根 θ*：My(θ)=0 粗扫变号 + 二分；
    (b) 逐波形斜率：∂Fz/∂f、∂Fz/∂V、∂Fz/∂θ、∂Fz/∂vz（0/−0.5 单侧
        负向差分，避开爬升守卫污染）与 Fx 同套、∂My/∂θ 刚度——
        三探针耦合结构的逐波形重提；
    (c) 拍频扰动谱：根态整周期 My 序列 DFT 1f~4f 幅相（波形自身）
        + 第八跑 0.525f 次谐波线（0.0193 N·m、84°）；
    (d) 动态标定：锚定映射开环释放 12 拍（垂速/俯仰/高度自由、速度
        钉固），末 6 拍逐拍周期平均 Fz/W 取中位 → 动态 Fz/W，<0.95
        剪枝；终选候选用 event_series_6dof 式真模型纵向短积分复核
        （每步真力、守卫生效、拍频反作用在环）。
  外层（升力达标波形整定控制器）——降阶闭环 60 s（映射=内层锚定的
    Fz/Fx/My 线性图 + 扰动谱 + 守卫；控制律=flight_controller 单段
    巡航路径复刻，结构常数第九跑在环值）：逐候选 LHS 随机网格 →
    Nelder-Mead 细化 → 判据评估；输出全套最优组、备选组、可行域与
    14 轴鲁棒半径（波形轴扰动含重标定）。

用法： python tools/joint_wave_control_opt.py [--quick] [--workers N]
产物： logs/joint_opt.json + 控制台摘要。
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
B_ELEV = 0.49                         # N·m/rad 对象侧舵效（决断总表甲组签核）
DELTA_MAX = 0.30                      # rad 舵偏限幅（甲组）
MY_DC = -0.0399                       # N·m 机翼+驱动矩直流（第八跑谱投影，37班探针B）
SUBHARM_K, SUBHARM_A, SUBHARM_PHI = 0.525, 0.01930, math.radians(84.0)
                                      # 第八跑 0.62Hz 线对锁频 1.18Hz 之比，随拍频缩放
GUARD_VZ = 2.5                        # m/s 爬升守卫半衰减点（aero_solver 生效值）
K_TAIL_ELEV = 0.0145                  # 控制器侧尾舵调度（config 第九跑口径）
SLIP_PER_HZ = 0.472                   # m/s/Hz 滑流（run-9 CSV vslip=0.59@f=1.25 互证）
VEFF_CAP = 8.0
# 三探针名义值（第三十七班探针 A + 第三十一班斜率；锚定核对用）
PROBE_NOM = {"FZ0": 2.08116, "FX0": -0.00505, "DFZ_DTH": -0.4866,
             "DFX_DTH": 1.8213, "DFZ_DVZ": -0.2026, "DFX_DVZ": -0.1616,
             "DMY_DVZ": -0.0711, "DFZ_DF": -0.0457, "DFX_DF": -0.4495,
             "DFZ_DV": 1.244, "DFX_DV": 0.1189}

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

# ══════════════════ 十四变量搜索域（任务指定） ══════════════════
WAVE_KEYS = ("alpha_set", "mean_deg", "split", "pitch_lag_s",
             "amplitude_deg", "freq", "V")
WAVE_LO = {"alpha_set": 20.0, "mean_deg": -10.0, "split": 0.5,
           "pitch_lag_s": -0.06, "amplitude_deg": 20.0, "freq": 0.8, "V": 2.5}
WAVE_HI = {"alpha_set": 45.0, "mean_deg": 25.0, "split": 0.9,
           "pitch_lag_s": 0.0, "amplitude_deg": 40.0, "freq": 2.5, "V": 4.5}
TAU_GRID = (0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
CTRL_KEYS = ("KP_A", "PITCH_DAMP", "KI_A", "GATE", "AFF", "AKV_F")
CTRL_LO = {"KP_A": 0.10, "PITCH_DAMP": 0.05, "KI_A": 0.005, "GATE": 0.05,
           "AFF": -0.10, "AKV_F": 0.10}
CTRL_HI = {"KP_A": 0.90, "PITCH_DAMP": 1.20, "KI_A": 0.20, "GATE": 0.80,
           "AFF": 0.45, "AKV_F": 3.00}

# 判据阈值（先于数据）
FZW_GATE = 0.95
ZBAND_OCC_GATE = 0.95
ZMIN_GATE = 1.5
TH_OCC_GATE = 0.80
ELEV_RMS_GATE = 0.70

NOMINAL_WAVE = {"alpha_set": 35.0, "mean_deg": 20.0, "split": 0.75,
                "pitch_lag_s": -0.04, "amplitude_deg": 30.0,
                "freq": 1.25, "V": 3.5}     # 甲组签核值（feasibility_map WAVE_NOM）
RUN9_CTRL = {"KP_A": 0.40, "PITCH_DAMP": 0.30, "KI_A": 0.033, "GATE": 0.25,
             "TAU": 0.08, "AFF": 0.0, "AKV_F": 1.00}   # 第九跑现值
RUN36_CTRL = {"KP_A": 0.20, "PITCH_DAMP": 1.20, "KI_A": 0.20, "GATE": 0.80,
              "TAU": 0.02, "AFF": 0.0, "AKV_F": 3.00}  # 三十六班最优组对照
SEED_CTRL = {"KP_A": 0.40, "PITCH_DAMP": 0.60, "KI_A": 0.10, "GATE": 0.50,
             "TAU": 0.04, "AFF": 0.30, "AKV_F": 2.00}  # 37班 M1 域中心邻域


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


def cycle_eval(V, f, pitch_deg, vz=0.0, n=72, want_series=False):
    """钉固状态整周期平均（scan_waveform_retrim.eval_state 同口径：时基
    单调推进、显式设频、升降舵零位、滑流=SLIP_PER_HZ·f、世界系力/力矩）。"""
    m, tail = _model_pair()
    m.set_wing_command(frequency=f)
    tail.set_command(elevator=0.0)
    quat = _TRUE["e2q"](0.0, math.radians(pitch_deg), 0.0)
    vel = np.array([V, 0.0, vz])
    om = np.zeros(3)
    T = 1.0 / f
    t0 = _TRUE["T0"]
    Fz = Fx = My = 0.0
    my_s = [] if want_series else None
    for i in range(n):
        Fw, Tw = m.compute_forces(np.zeros(3), quat, vel, om, t0 + i * T / n)
        Fz += float(Fw[2]); Fx += float(Fw[0]); My += float(Tw[1])
        if want_series:
            my_s.append(float(Tw[1]))
    _TRUE["T0"] = t0 + T                     # 测量纪律一：时基单调
    v_slip = SLIP_PER_HZ * f
    Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
    out = {"Fz": Fz / n + float(Ft[2]), "Fx": Fx / n + float(Ft[0]),
           "My": My / n + float(Tt[1])}
    if want_series:
        out["My_series"] = my_s
    return out


# ══════════════════ 内层 A：俯仰根 + 逐波形斜率 + 扰动谱 ══════════════════
# 拍频扰动谱名义线（实测）：次谐波 0.525f=第八跑 0.62Hz/1.18Hz 线
# （0.0193 N·m、84°）；1f~4f=第九跑 (My−TMy) 锁频谐波 LS 拟合
# （0.01196/0.00871/0.00367/0.00208 N·m、相位 2.097/1.270/1.019/0.993 rad，
# optimize_gains BEAT_A/BEAT_PHI 同源）。逐波形缩放：根态整周期 My
# 序列 DFT 对名义波形 DFT 之比（在环施加力为同步周期平均，拍内纹波
# 大部分被平均掉、残差与纹波幅值同源，故取相对缩放，锚定实测值；
# 缩放比限幅 [0.25,4]）。
DIST_NOM = [(0.525, 0.01930, math.radians(84.0)),
            (1.0, 0.01196, 2.097), (2.0, 0.00871, 1.270),
            (3.0, 0.00367, 1.019), (4.0, 0.00208, 0.993)]
_NOM_BEAT = {}


def _nom_beat_dft():
    """名义波形根态 My 序列 DFT（每进程惰性缓存，作缩放基准）。"""
    if not _NOM_BEAT:
        obj0 = {"wave": dict(NOMINAL_WAVE)}
        obj0.update(calibrate_raw(obj0["wave"]))
        _NOM_BEAT.update(amp=obj0["beat_dft"], root=obj0)
    return _NOM_BEAT["amp"]


def calibrate_raw(w):
    """根解 + 全部偏置态求值（斜率与 DFT 原料）。所有参与差分的求值
    一律 n=144（相同相位网格）——aero 模式 α=|cos| 波形有折点，不同 n
    的相位网格不对齐会引入 ~0.03 N 量级伪斜率（本班实测教训）。"""
    _set_waveform(w)
    V, f = w["V"], w["freq"]

    def my_at(pitch_deg, n):
        return cycle_eval(V, f, pitch_deg, 0.0, n)["My"]

    grid = np.arange(-15.0, 40.01, 2.5)
    vals = [my_at(th, 36) for th in grid]
    brackets = []
    for i in range(len(grid) - 1):
        if vals[i] == 0.0 or vals[i] * vals[i + 1] < 0:
            brackets.append((grid[i], grid[i + 1]))
    if not brackets:
        th_min = float(grid[int(np.argmin(np.abs(vals)))])
        if abs(my_at(th_min, 72)) > 0.03:
            return {"pruned": "no_pitch_root",
                    "My_min_abs": abs(my_at(th_min, 72))}
        brackets = [(th_min - 1.0, th_min + 1.0)]
    best = None
    for a0, b0 in brackets:
        a, b = float(a0), float(b0)
        fa = my_at(a, 36)
        for _ in range(8):
            mid = 0.5 * (a + b)
            fm = my_at(mid, 36)
            if fa * fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
        th_c = 0.5 * (a + b)
        fz_c = cycle_eval(V, f, th_c, 0.0, 144)["Fz"]
        if best is None or abs(fz_c - WEIGHT) < best[0]:
            best = (abs(fz_c - WEIGHT), th_c, a, b)
    _, _, a, b = best
    fa = my_at(a, 144)
    for _ in range(6):
        mid = 0.5 * (a + b)
        fm = my_at(mid, 144)
        if fa * fm <= 0:
            b = mid
        else:
            a, fa = mid, fm
    th_star_deg = 0.5 * (a + b)

    root = cycle_eval(V, f, th_star_deg, 0.0, 144, want_series=True)
    # 偏置态（全部 n=144）：f±span（宽跨度避开 Fz(f) 丘形细结构）、
    # V+0.5、θ+2°、vz=−0.5（单侧负向，避开爬升守卫污染）
    span = max(0.10, 0.08 * f)
    st_fp = cycle_eval(V, f + span, th_star_deg, 0.0, 144)
    st_fm = cycle_eval(V, max(0.3, f - span), th_star_deg, 0.0, 144)
    st_vp = cycle_eval(V + 0.5, f, th_star_deg, 0.0, 144)
    st_th = cycle_eval(V, f, th_star_deg + 2.0, 0.0, 144)
    st_vz = cycle_eval(V, f, th_star_deg, -0.5, 144)
    dfz_df = (st_fp["Fz"] - st_fm["Fz"]) / (f + span - max(0.3, f - span))
    dfx_df = (st_fp["Fx"] - st_fm["Fx"]) / (f + span - max(0.3, f - span))
    out = {
        "wave": dict(w),
        "theta_star_deg": float(th_star_deg),
        "theta_star": float(math.radians(th_star_deg)),
        "FZ0": float(root["Fz"]), "FX0": float(root["Fx"]),
        "My_res": float(root["My"]),
        "DFZ_DF": float(np.clip(dfz_df, -0.20, 0.20)),
        "DFX_DF": float(np.clip(dfx_df, -1.50, 1.50)),
        "DFZ_DV": (st_vp["Fz"] - root["Fz"]) / 0.5,
        "DFX_DV": (st_vp["Fx"] - root["Fx"]) / 0.5,
        "DFZ_DTH": (st_th["Fz"] - root["Fz"]) / math.radians(2.0),
        "DFX_DTH": (st_th["Fx"] - root["Fx"]) / math.radians(2.0),
        "DFZ_DVZ": (st_vz["Fz"] - root["Fz"]) / (-0.5),
        "DFX_DVZ": (st_vz["Fx"] - root["Fx"]) / (-0.5),
        "K_TH": -(st_th["My"] - root["My"]) / math.radians(2.0),
        "DMY_DVZ": (st_vz["My"] - root["My"]) / (-0.5),
    }
    ser = np.asarray(root["My_series"])
    n_s = len(ser)
    ks = np.arange(n_s)
    harm = []
    for k in (1, 2, 3, 4):
        ak = float(2.0 / n_s * np.sum(ser * np.sin(2 * np.pi * k * ks / n_s)))
        bk = float(2.0 / n_s * np.sum(ser * np.cos(2 * np.pi * k * ks / n_s)))
        harm.append((float(np.hypot(ak, bk)), float(math.atan2(bk, ak))))
    out["beat_dft"] = harm
    return out


def calibrate(w):
    """标定 + 扰动谱缩放（锚定实测线），返回锚定映射 obj。"""
    obj = calibrate_raw(w)
    if "pruned" in obj:
        return obj
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
    obj["fzw_static"] = obj["FZ0"] / WEIGHT
    # 开环俯仰–垂向耦合环路增益诊断（>1 即开环静态发散，俯仰须闭环镇定）
    obj["pitch_heave_loop_gain"] = float(
        abs(obj["DMY_DVZ"] / max(obj["K_TH"], 1e-6))
        * abs(obj["DFZ_DTH"] / min(obj["DFZ_DVZ"], -1e-6)))
    return obj


def _prune_static(obj):
    """静态剪枝（执行机构/姿态可达性，scan_waveform_retrim 判据四同源）。"""
    w = obj["wave"]
    if abs(w["mean_deg"]) + w["amplitude_deg"] > 50.0:
        return "actuator_phi_peak_gt_50deg"
    if not (-0.10 <= obj["theta_star"] <= 0.52):
        return "theta_star_out_of_tilt_reach"
    if not (0.02 <= obj["K_TH"] <= 3.0):
        return "pitch_stiffness_degenerate"
    return None


def _beat_lines(obj):
    """obj['beat'] 已是 [(k, A, phi)] 缩放线（calibrate 产出），直通。"""
    return obj["beat"]


# ══════════════════ 内层 B：动态标定（映射释放 + 真模型释放） ══════════════════
def dyn_release(obj, n_strokes=12, dt=0.001):
    """锚定映射垂向释放（俯仰钉固 θ*、速度钉固 V0——开环自由俯仰在
    静态斜率口径下静态发散：环路增益 (∂My/∂vz/K_TH)·(∂Fz/∂θ/|∂Fz/∂vz|)
    ≈1.1>1，俯仰必须由闭环提供刚度，与在环一致）。垂速/高度自由、
    爬升守卫生效：末 6 拍逐拍周期平均 Fz/W 中位 + 定常下沉率 vz*。"""
    w = obj["wave"]
    f0 = w["freq"]
    T = 1.0 / f0
    n_steps = int(round(n_strokes * T / dt))
    vz, z = 0.0, 3.0
    th_star = obj["theta_star"]
    stroke_fz = []
    cur_sum, cur_n, stroke_idx = 0.0, 0, 1
    for i in range(n_steps):
        t = (i + 1) * dt
        fz = obj["FZ0"] + obj["DFZ_DVZ"] * vz
        if vz > 0.0:
            fz *= 1.0 / (1.0 + (vz / GUARD_VZ) ** 2)
        vz += (fz - WEIGHT) / MASS * dt
        z += vz * dt
        cur_sum += fz; cur_n += 1
        if t >= stroke_idx * T - 0.5 * dt:
            stroke_fz.append(cur_sum / max(cur_n, 1) / WEIGHT)
            cur_sum, cur_n = 0.0, 0
            stroke_idx += 1
    tail_strokes = stroke_fz[-6:]
    return {"fzw_dyn_med": float(np.median(tail_strokes)) if tail_strokes else None,
            "fzw_dyn_series": [round(v, 4) for v in tail_strokes],
            "vz_star": float(vz), "z_end": float(z),
            "grounded": bool(z < 0.2)}


def true_release(obj, n_strokes=10, dt=0.004):
    """event_series_6dof 式真模型纵向短积分（终选复核），力口径=在环
    aero_solver 的 K=8 同步周期平均（每步在当前态对整周期 8 相位求值
    取平均，相位状态快照恢复——第九跑 Frz 列同源）；俯仰钉固 θ*、速度
    钉固 V0（与 dyn_release 同一隔离口径）。逐拍周期平均 Fz/W。"""
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
        Fz_s = Fx_s = My_s = 0.0
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


def _cal_worker(w):
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
    obj.update(dyn_release(obj))
    if obj["fzw_dyn_med"] is None or obj["fzw_dyn_med"] < FZW_GATE:
        obj["pruned"] = f"dyn_fzw_lt_{FZW_GATE}"
    return obj


def _true_worker(w):
    try:
        obj = calibrate(w)
        if "pruned" in obj:
            return {"error": obj["pruned"]}
        return true_release(obj)
    except Exception as exc:                    # noqa: BLE001
        return {"error": f"{type(exc).__name__}:{exc}"}


# ══════════════════ 外层：降阶闭环 60 s ══════════════════
def closed_loop(obj, ctrl, t_sim=T_SIM, dt=DT_CL, want_series=False, ov=None):
    """60 s 闭环（含拍频扰动谱+三探针耦合+守卫）。ov=对象覆写（稳健变体）：
    MY_DC / B_ELEV / GUARD_VZ / DIST_SCALE。"""
    ov = ov or {}
    my_dc = ov.get("MY_DC", MY_DC)
    b_elev = ov.get("B_ELEV", B_ELEV)
    guard_vz = ov.get("GUARD_VZ", GUARD_VZ)
    dist_s = ov.get("DIST_SCALE", 1.0)
    w = obj["wave"]
    f0, V0, th_star = w["freq"], w["V"], obj["theta_star"]
    kp_a, pdamp, ki_a = ctrl["KP_A"], ctrl["PITCH_DAMP"], ctrl["KI_A"]
    gate, tau, aff = ctrl["GATE"], ctrl["TAU"], ctrl["AFF"]
    kvf = -ctrl["AKV_F"]
    f_lo = min(1.0, f0)                  # f0<1.0 时按第十五跑建议下探 AP 下沿
    v_free, v_lock = V0, V0 - 0.5        # 低速下压衰减域随工作点平移
    lines = [(kk, a * dist_s, p) for kk, a, p in _beat_lines(obj)]
    th, q = th_star, 0.0
    vx, vz, z = V0, 0.0, 3.0
    freq, ei, pitch_i = f0, 0.0, 0.0
    pd_last, qf, vxf = th_star, 0.0, V0
    psi = [0.0] * len(lines)
    n_steps = int(round(t_sim / dt))
    crash_t = None
    b_out = min(1.0, dt / OUTER_FILT_TAU)
    n_win = z_in = th_in = 0
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
        # ── 对象：三探针耦合映射 + 扰动谱 + 守卫 ──
        dth = th - th_star
        fz = (obj["FZ0"] + obj["DFZ_DV"] * (vx - V0)
              + obj["DFZ_DF"] * (freq - f0) + obj["DFZ_DTH"] * dth
              + obj["DFZ_DVZ"] * vz)
        if vz > 0.0:
            fz *= 1.0 / (1.0 + (vz / guard_vz) ** 2)
        fx = (obj["FX0"] + obj["DFX_DV"] * (vx - V0)
              + obj["DFX_DF"] * (freq - f0) + obj["DFX_DTH"] * dth
              + obj["DFX_DVZ"] * vz)
        m_beat = sum(a * math.sin(psi[j] + p)
                     for j, (_kk, a, p) in enumerate(lines))
        m_net = (obj["My_res"] + my_dc + m_beat - obj["K_TH"] * dth
                 - C_Q * q + obj["DMY_DVZ"] * vz - b_elev * elev)
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
            if abs(dth) <= 0.05:
                th_in += 1
            fzw_l.append(fz / WEIGHT)
            z_l.append(z); th_l.append(abs(dth)); el_l.append(elev)
            f_l.append(freq); vzw_l.append(vz)
        if (t > 1.0 and z < Z_CRASH) or abs(th) > 1.5 or z > 20.0 \
                or abs(vx) > 10.0 or not (th == th and z == z):
            crash_t = t
            break
    fzw_arr = np.asarray(fzw_l) if fzw_l else np.array([0.0])
    el_arr = np.asarray(el_l) if el_l else np.array([0.0])
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
        "f_med": float(np.median(f_l)) if f_l else f0,
        "z_end": float(z),
    }
    if want_series and len(z_l) >= 4:
        keep = max(1, len(z_l) // 300)
        m["series"] = {"fzw": [round(v, 4) for v in fzw_l[::keep]],
                       "z": [round(v, 3) for v in z_l[::keep]],
                       "th_err": [round(v, 4) for v in th_l[::keep]],
                       "el": [round(v, 4) for v in el_l[::keep]]}
    return m


def judge(m):
    return (m["crash_t"] is None
            and m["z_frac"] >= ZBAND_OCC_GATE and m["z_min"] >= ZMIN_GATE
            and m["th_frac"] >= TH_OCC_GATE
            and m["elev_rms_frac"] <= ELEV_RMS_GATE
            and m["fzw_med"] >= FZW_GATE)


def cost(m):
    """排序代价：超带重罚 + 存活罚 + 平级 tie-breaker（可行组按 tie 排序）。"""
    viol = (3.0 * max(0.0, FZW_GATE - m["fzw_med"]) / 0.05
            + 3.0 * max(0.0, ZBAND_OCC_GATE - m["z_frac"]) / 0.05
            + 3.0 * max(0.0, TH_OCC_GATE - m["th_frac"]) / 0.20
            + 2.0 * max(0.0, m["elev_rms_frac"] - ELEV_RMS_GATE) / 0.30)
    tie = (m["th_rms"] / 0.05 + m["elev_rms_frac"] / ELEV_RMS_GATE
           + abs(m["fzw_med"] - 1.0) / 0.10 + abs(m["vz_med"]) / 1.0)
    return 100.0 * viol + 0.3 * (T_SIM - m["t_end"]) + 0.1 * tie


def _cl_worker(args):
    obj, ctrl = args
    return cost(closed_loop(obj, ctrl))


# ══════════════════ 寻优主体 ══════════════════
def _nm_one(args):
    from scipy.optimize import minimize
    obj, seed = args

    def unpack(x):
        p = {k: float(v) for k, v in zip(CTRL_KEYS, x)}
        p["TAU"] = seed["TAU"]
        return p

    bounds = [(CTRL_LO[k], CTRL_HI[k]) for k in CTRL_KEYS]
    x0 = np.array([seed[k] for k in CTRL_KEYS])
    r = minimize(lambda x: cost(closed_loop(obj, unpack(x))), x0,
                 method="Nelder-Mead", bounds=bounds,
                 options={"maxiter": 260, "xatol": 1e-3, "fatol": 1e-4})
    p = unpack(r.x)
    p["_J"] = float(r.fun)
    return p


def norm_dist_wave(a, b):
    return sum(abs(a[k] - b[k]) / max(WAVE_HI[k] - WAVE_LO[k], 1e-9)
               for k in WAVE_KEYS)


def norm_dist_ctrl(a, b):
    d = abs(TAU_GRID.index(a["TAU"]) - TAU_GRID.index(b["TAU"])) / (len(TAU_GRID) - 1)
    return d + sum(abs(a[k] - b[k]) / max(CTRL_HI[k] - CTRL_LO[k], 1e-9)
                   for k in CTRL_KEYS)


def lhs(n, lo, hi, rng):
    keys = list(lo)
    u = np.zeros((n, len(keys)))
    for j in range(len(keys)):
        cuts = (np.arange(n) + rng.random(n)) / n
        rng.shuffle(cuts)
        u[:, j] = cuts
    return [{k: lo[k] + u[i, jj] * (hi[k] - lo[k])
             for jj, k in enumerate(keys)} for i in range(n)]


def _digest(r):
    if "pruned" in r:
        return {"wave": {k: round(r["wave"][k], 4) for k in WAVE_KEYS},
                "pruned": r["pruned"]}
    return {"wave": {k: round(r["wave"][k], 4) for k in WAVE_KEYS},
            "theta_star_deg": round(r["theta_star_deg"], 2),
            "FZ0": round(r["FZ0"], 4), "fzw_static": round(r["fzw_static"], 4),
            "FX0": round(r["FX0"], 4), "K_TH": round(r["K_TH"], 4),
            "My_res": round(r["My_res"], 4),
            "DFZ_DF": round(r["DFZ_DF"], 4), "DFZ_DV": round(r["DFZ_DV"], 4),
            "DFZ_DTH": round(r["DFZ_DTH"], 4),
            "DFZ_DVZ": round(r["DFZ_DVZ"], 4),
            "beat_1f_2f_3f": [round(r["beat"][1][1], 5),
                              round(r["beat"][2][1], 5),
                              round(r["beat"][3][1], 5)],
            "beat_dft_1f_2f": [round(r["beat_dft"][0][0], 5),
                               round(r["beat_dft"][1][0], 5)],
            "pitch_heave_loop_gain": round(r["pitch_heave_loop_gain"], 3),
            "fzw_dyn_med": round(r["fzw_dyn_med"], 4),
            "vz_star": round(r["vz_star"], 4)}


def _round_metrics(m):
    return {k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in m.items() if k != "series"}


def _radius_worker(args):
    """半径轴探针：波形轴=扰动波形重标定+最优控制器；控制器轴=最优波形
    重标定+扰动控制器。逐点独立重标定（进程内 ~1 s）。"""
    wave_try, ctrl_try, best_wave, best_ctrl = args
    try:
        if wave_try is not None:
            obj = calibrate(wave_try)
            if "pruned" in obj:
                return {"feasible": False, "why": "recal_pruned"}
            pr = _prune_static(obj)
            if pr:
                return {"feasible": False, "why": pr}
            ctrl = best_ctrl
        else:
            obj = calibrate(best_wave)
            ctrl = ctrl_try
        m = closed_loop(obj, ctrl)
        return {"feasible": judge(m), "fzw_med": round(m["fzw_med"], 4),
                "z_frac": round(m["z_frac"], 4), "th_frac": round(m["th_frac"], 4),
                "elev_rms_frac": round(m["elev_rms_frac"], 4),
                "crash_t": m["crash_t"]}
    except Exception as exc:                    # noqa: BLE001
        return {"feasible": False, "why": f"{type(exc).__name__}"}


def run(args):
    import multiprocessing as mp
    quick = bool(args.quick)
    workers = args.workers or max(4, min(28, (os.cpu_count() or 8) - 4))
    n_stage_a = 240 if quick else int(args.n_stage_a)
    top_k = 6 if quick else 20
    n_ctrl = 96 if quick else 384
    rng = np.random.default_rng(20260925)
    log = []
    t00 = time.time()
    ctx = mp.get_context("spawn")

    # ── 阶段 A：波形×工作点 LHS + 种子 → 内层标定（并行） ──
    cands = lhs(n_stage_a, WAVE_LO, WAVE_HI, rng)
    seeds = [dict(NOMINAL_WAVE),
             {"alpha_set": 35.0, "mean_deg": 20.0, "split": 0.75,
              "pitch_lag_s": -0.04, "amplitude_deg": 35.0, "freq": 1.25, "V": 3.5},
             {"alpha_set": 35.0, "mean_deg": 20.0, "split": 0.75,
              "pitch_lag_s": -0.04, "amplitude_deg": 40.0, "freq": 1.25, "V": 3.5},
             {"alpha_set": 35.0, "mean_deg": 20.0, "split": 0.75,
              "pitch_lag_s": -0.04, "amplitude_deg": 35.0, "freq": 1.5, "V": 3.5},
             {"alpha_set": 30.0, "mean_deg": 15.0, "split": 0.7,
              "pitch_lag_s": -0.03, "amplitude_deg": 35.0, "freq": 1.6, "V": 4.0}]
    for s in seeds:
        if all(norm_dist_wave(s, c) > 0.05 for c in cands):
            cands.append(s)
    t0 = time.time()
    with ctx.Pool(workers) as pool:
        cal_rows = pool.map(_cal_worker, cands, chunksize=4)
    log.append({"stage": "A_calibrate", "n": len(cands), "workers": workers,
                "sec": round(time.time() - t0, 1)})
    n_pruned = sum(1 for r in cal_rows if "pruned" in r)
    feasible_wave = [r for r in cal_rows if "pruned" not in r]
    nom_row = next((r for r in cal_rows
                    if all(abs(r["wave"][k] - NOMINAL_WAVE[k]) < 1e-6
                           for k in WAVE_KEYS)), None)
    print(f"[A] 标定 {len(cands)} 组 {time.time()-t0:.0f}s：剪枝 {n_pruned}，"
          f"动态 Fz/W≥{FZW_GATE} 幸存 {len(feasible_wave)}", flush=True)
    if nom_row and "pruned" not in nom_row:
        print(f"[A·锚定] 名义点 θ*={nom_row['theta_star_deg']:.2f}° "
              f"FZ0={nom_row['FZ0']:.4f} N ({nom_row['fzw_static']:.4f}W) "
              f"∂Fz/∂θ={nom_row['DFZ_DTH']:.4f} ∂Fz/∂vz={nom_row['DFZ_DVZ']:.4f} "
              f"∂Fz/∂V={nom_row['DFZ_DV']:.4f} 扰动线1f/2f="
              f"{nom_row['beat'][1][1]:.5f}/{nom_row['beat'][2][1]:.5f}"
              f"（37班探针 A：21.45°/2.0812/−0.4866/−0.2026/斜率1.244；"
              f"九跑实测 0.01196/0.00871）", flush=True)

    # ── 排序与终选（|vz*| 主序 + Fx 余项，参数空间多样性） ──
    feasible_wave.sort(key=lambda r: (abs(r["vz_star"]) + 0.3 * abs(r["FX0"]),
                                      -r["fzw_dyn_med"]))
    chosen = []
    for r in feasible_wave:
        if all(norm_dist_wave(r["wave"], c["wave"]) > 0.25 for c in chosen):
            chosen.append(r)
        if len(chosen) >= top_k:
            break

    # ── 阶段 A2：终选真模型短积分复核 ──
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
        chosen = keep
    log.append({"stage": "A2_true_release", "n": n_before,
                "dropped": n_before - len(chosen),
                "sec": round(time.time() - t0, 1)})
    print(f"[A2] 真模型复核幸存 {len(chosen)}/{n_before}", flush=True)

    # ── 阶段 B：逐候选控制器整定（LHS+τ 展开 + 三种子 → 并行评分） ──
    ctrl_pool = lhs(n_ctrl, CTRL_LO, CTRL_HI, rng)
    for i, c in enumerate(ctrl_pool):
        c["TAU"] = TAU_GRID[i % len(TAU_GRID)]
    ctrl_pool += [dict(RUN9_CTRL), dict(RUN36_CTRL), dict(SEED_CTRL)]
    for ti in range(len(TAU_GRID)):
        for c in ctrl_pool[:max(8, n_ctrl // 4)]:
            cc = dict(c); cc["TAU"] = TAU_GRID[ti]
            ctrl_pool.append(cc)
    jobs = [(r, c) for r in chosen for c in ctrl_pool]
    t0 = time.time()
    with ctx.Pool(workers) as pool:
        Js = pool.map(_cl_worker, jobs, chunksize=8) if jobs else []
    scored = {}
    for (r, c), J in zip(jobs, Js):
        cc = dict(c); cc["_J"] = J
        scored.setdefault(id(r), []).append(cc)
    for r in chosen:
        scored[id(r)].sort(key=lambda c: c["_J"])
    log.append({"stage": "B_ctrl_grid", "n": len(jobs),
                "sec": round(time.time() - t0, 1)})
    print(f"[B] 控制器网格 {len(jobs)} 组 {time.time()-t0:.0f}s", flush=True)

    # ── 阶段 B2：每 finalist 前二种子 NM 细化 ──
    nm_jobs = []
    for r in chosen:
        seen = []
        for c in scored[id(r)]:
            if all(norm_dist_ctrl(c, s) > 0.08 for s in seen):
                seen.append(c)
            if len(seen) >= 2:
                break
        for s in seen:
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

    # ── 阶段 C：判据全量评估（每 finalist 前六控制器） ──
    finals = []
    for r in chosen:
        for c in scored[id(r)][:6]:
            m = closed_loop(r, c, want_series=True)
            finals.append({
                "wave": {k: round(r["wave"][k], 4) for k in WAVE_KEYS},
                "ctrl": {k: round(c[k], 4) for k in CTRL_KEYS}
                        | {"TAU": c["TAU"],
                           "TECS_KV_F_signed": round(-c["AKV_F"], 3)},
                "obj_digest": _digest(r),
                "metrics": _round_metrics(m), "feasible": judge(m),
                "true_release": r.get("true_release"),
                "fzw_dyn_med": r.get("fzw_dyn_med"), "vz_star": r.get("vz_star")})
    feas_finals = [f for f in finals if f["feasible"]]
    feas_finals.sort(key=lambda f: (3.0 * (1.0 - f["metrics"]["z_frac"])
                                    + f["metrics"]["th_rms"]
                                    + 5.0 * abs(f["metrics"]["fzw_med"] - 1.0)
                                    + f["metrics"]["elev_rms_frac"]
                                    + 0.5 * abs(f["metrics"]["z_end"] - 3.0)))
    print(f"[C] {len(chosen)} 终选 × 前 6 控制器 → {len(finals)} 组，"
          f"四判据全过 {len(feas_finals)} 组", flush=True)

    # ── V1 稳健复核（MY_DC=0：真模型根态 My 直流 +0.014 口径 vs 在环
    #    实测 −0.0399 口径两档并存；最优组须在两档下均可行，否则降级） ──
    v1_rows = []
    if feas_finals:
        for f in feas_finals[:6]:
            r = next(r for r in chosen
                     if all(abs(r["wave"][k] - f["wave"][k]) < 5e-4
                            for k in WAVE_KEYS))
            c = {k: f["ctrl"][k] for k in CTRL_KEYS}
            c["TAU"] = f["ctrl"]["TAU"]; c["AKV_F"] = -f["ctrl"]["TECS_KV_F_signed"]
            m1 = closed_loop(r, c, ov={"MY_DC": 0.0})
            f["v1_MyDC0"] = {"feasible": judge(m1),
                             "z_frac": round(m1["z_frac"], 4),
                             "th_frac": round(m1["th_frac"], 4),
                             "fzw_med": round(m1["fzw_med"], 4),
                             "z_end": round(m1["z_end"], 3)}
            v1_rows.append(f)
        both = [f for f in v1_rows if f["v1_MyDC0"]["feasible"]]
        if both:
            feas_finals.remove(both[0])
            feas_finals.insert(0, both[0])
            print(f"[C·V1] 双口径（MY_DC=−0.0399 与 0）均可行组 {len(both)}/6，"
                  f"首选双口径组", flush=True)
        else:
            print("[C·V1] 无双口径组——最优组仅在 MY_DC=−0.0399 口径可行"
                  "（AFF 前馈与力矩直流耦合，见报告）", flush=True)

    # ── 基线对照（名义波形 × 第九跑/三十六班控制器） ──
    baselines = {}
    if nom_row and "pruned" not in nom_row:
        for tag, ctl in (("run9_gains", RUN9_CTRL), ("run36_gains", RUN36_CTRL)):
            m = closed_loop(nom_row, ctl)
            baselines[tag] = {"ctrl": ctl, "metrics": _round_metrics(m),
                              "feasible": judge(m)}

    out = {"ts": time.strftime("%Y%m%d_%H%M%S"),
           "tool": "tools/joint_wave_control_opt.py",
           "quick": quick, "workers": workers, "search_log": log,
           "caliber_note": (
               "Fz/W 判据=动态周期平均原值（第九跑 Frz 列同源，含爬升守卫与"
               "拍频反作用）；CSV Fz 列为其 0.5×每触发施加簿记（aero_solver "
               "多触发补偿 fpms≈2；第九跑对账：Frz 中位 2.0505 N=0.9722W、"
               "Fz 中位 1.0254 N=0.4862W、比值恒 0.500、n=412）"),
           "criteria": {"fzw_med_gate": FZW_GATE, "z_band": [2.0, 4.0],
                        "z_occ_gate": ZBAND_OCC_GATE, "z_min_gate": ZMIN_GATE,
                        "th_band_rad": 0.05, "th_occ_gate": TH_OCC_GATE,
                        "elev_rms_frac_gate": ELEV_RMS_GATE, "t_sim": T_SIM,
                        "window": "t>5 s"},
           "nominal_anchor": _digest(nom_row) if nom_row else None,
           "probe_nom_reference": PROBE_NOM,
           "baselines": baselines,
           "stageA": {"n": len(cands), "n_pruned": n_pruned,
                      "n_dyn_feasible": len(feasible_wave),
                      "prune_reasons": _counter(
                          [r.get("pruned") for r in cal_rows if "pruned" in r]),
                      "rows": [_digest(r) for r in cal_rows]},
           "finalists": finals,
           "n_feasible_sets": len(feas_finals),
           "best_set": feas_finals[0] if feas_finals else None,
           "alternates": feas_finals[1:4]}

    # ── 阶段 D：鲁棒半径（最优组 14 轴，波形轴扰动重标定）+ 稳健变体 ──
    if feas_finals:
        best = feas_finals[0]
        best_wave = {k: best["wave"][k] for k in WAVE_KEYS}
        best_ctrl = {k: best["ctrl"][k] for k in CTRL_KEYS}
        best_ctrl["TAU"] = best["ctrl"]["TAU"]
        best_obj = next(r for r in chosen
                        if all(abs(r["wave"][k] - best_wave[k]) < 5e-4
                               for k in WAVE_KEYS))
        probes = []                            # (标签, 扰动值, wave_try, ctrl_try)
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
                probes.append((f"tol|{k}|x{sgn}", round(w2[k], 5), w2, None))
        t0 = time.time()
        with ctx.Pool(workers) as pool:
            rad_rows = pool.map(_radius_worker,
                                [(lbl_val[2], lbl_val[3], best_wave, best_ctrl)
                                 for lbl_val in probes], chunksize=1)
        margins, tol_absorb, axis_rows = {}, {}, []
        for (lbl, val, _w2, _c2), rr in zip(probes, rad_rows):
            rr = dict(rr); rr["label"] = lbl; rr["value"] = val
            axis_rows.append(rr)
            if lbl.startswith("tol|"):
                tol_absorb[lbl] = bool(rr.get("feasible"))
                continue
            key = lbl.split("|")[1]
            margins.setdefault(key, []).append((bool(rr.get("feasible")), val))
        radius_tbl = {}
        for key, pts in margins.items():
            hr = 0.5 * ((WAVE_HI[key] - WAVE_LO[key]) if key in WAVE_KEYS
                        else (CTRL_HI[key] - CTRL_LO[key]))
            center = best_wave.get(key, best_ctrl.get(key))
            infeas_d = [abs(center - v) / hr for ok, v in pts if not ok]
            radius_tbl[key] = round(min(infeas_d), 3) if infeas_d else 1.0
        out["robustness"] = {
            "center": {"wave": best_wave,
                       "ctrl": {k: best_ctrl[k] for k in CTRL_KEYS}
                               | {"TAU": best_ctrl["TAU"]}},
            "per_axis_radius_norm": radius_tbl,
            "inscribed_radius_norm": (min(radius_tbl.values())
                                      if radius_tbl else None),
            "wave_tol_absorb": tol_absorb,
            "axis_rows": axis_rows,
            "sec": round(time.time() - t0, 1)}
        # 稳健变体（对象口径扰动；最优组不动）
        variants = {"V1_MyDC_0": {"MY_DC": 0.0},
                    "V2_MyDC_x1p5": {"MY_DC": MY_DC * 1.5},
                    "V3_B_elev_0p40": {"B_ELEV": 0.40},
                    "V4_guard_2p0": {"GUARD_VZ": 2.0},
                    "V5_beat_x1p5": {"DIST_SCALE": 1.5}}
        vr = {}
        for tag, ov in variants.items():
            m = closed_loop(best_obj, best_ctrl, ov=ov)
            vr[tag] = {"feasible": judge(m), "fzw_med": round(m["fzw_med"], 4),
                       "z_frac": round(m["z_frac"], 4),
                       "th_frac": round(m["th_frac"], 4),
                       "elev_rms_frac": round(m["elev_rms_frac"], 4),
                       "crash_t": m["crash_t"]}
        out["robustness"]["variants"] = vr
    out["total_sec"] = round(time.time() - t00, 1)
    jpath = os.path.join(PROJECT_ROOT, "logs", "joint_opt.json")
    with open(jpath, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print(f"[done] {out['total_sec']}s → {jpath}", flush=True)
    if feas_finals:
        b = feas_finals[0]
        mm = b["metrics"]
        print("\n═══ 最优组（四判据全过）═══")
        print("波形+工作点:", json.dumps(b["wave"], ensure_ascii=False))
        print("控制器:", json.dumps(b["ctrl"], ensure_ascii=False))
        print(f"Fz/W 中位 {mm['fzw_med']:.4f} | z 带内 {mm['z_frac']*100:.1f}% "
              f"(z_min {mm['z_min']:.2f}) | θ 带内 {mm['th_frac']*100:.1f}% "
              f"| 舵 RMS {mm['elev_rms_frac']*100:.1f}% | 存活 "
              f"{'60 s' if mm['crash_t'] is None else mm['crash_t']}")
        if out.get("robustness"):
            print(f"内切球半径（归一化）: {out['robustness']['inscribed_radius_norm']}")
    else:
        print("\n═══ 四判据全过组：空 ═══（最近边界见 JSON finalists）")
    return out


def _counter(items):
    out_c = {}
    for it in items:
        key = str(it).split(":")[0]
        out_c[key] = out_c.get(key, 0) + 1
    return out_c


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--n-stage-a", type=float, default=2400)
    args = ap.parse_args()
    run(args)
