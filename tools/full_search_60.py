# -*- coding: utf-8 -*-
"""full_search_60.py — 第六十班：俯仰建立攻坚（2026-09-26）。

基础=第五十班考卷（tools/full_search_50.py，本班零改动、import 复用其
标定 calibrate_full/判据 judge_all①~⑦/极点种子/采样工具）。上环两跑
（第十八跑/第十八b 跑）同指纹败于俯仰建立：释放后俯仰钉在 +0.1176/
+0.1261 rad 对 θ*=0.2645 rad（带内 0%）、升降舵负向满偏、缓沉触地
（docs/第五十班_全域重搜.md §七~§八）。本班三改动：

  ① 新增判据⑧：闭环俯仰建立——本班闭环仿真里俯仰中位达标
     |θ_med−θ*|≤0.05 rad（带宽与历史判据④同口径 ±0.05 rad；④裁带内
     占比 ≥80%，⑧裁中位偏移，专堵"低均衡振荡占比虚高/均值性偏置"）。
  ② 释放瞬态治理项入搜索维度：初条件跌落量 drop 为可搜索参数
     0~0.30 rad（上沿依据=第十七跑实测 0.257 rad 加记录裕量；0 为完全
     治理=弹射配平直入理想态）。组级产物为跌落能力 D_cap=该组八判据
     全过的最大 drop（网格 0.30/0.257/0.22/0.18/0.14/0.10/0.06/0；
     0.257=第十七跑未治理实测=上环现实档）。联合初条件含角速度
     q(0)=−QK·drop（实测锚：第五十班两跑首样本 wy=−0.274/−0.333 对
     首样本内已消耗跌落 0.1375 rad → QK=2.2 1/s，两跑互证）。
  ③ 俯仰通道控制器域放开（第五十班 §五骑界记档的开放性兑现）：KP_A
     1.60→6.40、KI_A 0.30→0.60（沿四十三班/五十班上界倍增先例 ×2；
     r2 最优组骑 3.20/0.30 两界）。

闭环对象补强三项（全部第五十班上环两跑实测标定，复现优先）：
  S1 考卷侧俯仰矩亏损（深度斜坡主口径）：在实测卡死均衡态（跑一
     θ=0.129/vz=−0.82/elev=−0.196；跑二 θ=0.125/vz=−0.82/elev=−0.183；
     另两窗加宽）真实净俯仰矩≈0（IYY·ẇy≈0、wy 振荡均值 0），而考卷
     对象在同状态给 +0.085~+0.095 抬头（M 曲线恢复项+DMY_DVZ·vz
     耦合信贷+舵矩）——差值即考卷侧亏损幅值 Σ=−(0.085~−0.095)，四
     态均值 −0.091、散布 ±6%。作用域=低于配平的俯仰偏移带（实测全
     部位于 θ*−θ≈0.135~0.156 下探态，配平上方无数据不假设），斜坡
     Σ·min((θ*−θ)/0.14, 1) 在实测卡死深度 0.14 rad 处满幅、配平处
     归零（少主张原则；恒值口径 EST_MODE=const 留作敏感性对照，其
     静态权限账为全域不可行=恒值假设过强又一证）。旁证：在环刚体账
     残差 IYY·ẇy−TMy 全窗中位 −0.0532/−0.0536（跑一/二）=在环存在
     而 TMy 未含的净低头矩；两控制器族互证=对象侧非控制器侧。
  S2 舵效动态折损 ELEV_DYN=0.82：实测尾舵当量 b=My_tail/(−elev) 于
     |elev|>0.2 子集=0.1783/0.2293=0.778（跑一）、0.1955/0.2287=0.855
     （跑二）对调度 0.0145·veff²；取均值 0.82。仅折损对象侧（控制器
     侧仍按名义调度求逆，与真控制器一致）。
  S3 联合初条件（见②）。
  锚定测试（stage 0，先于搜索）：第五十班最优组在补强对象+drop=0.257
  下必须复现实测失效指纹（俯仰钉低均衡/带内近零/缓沉），复现不过则
  大字记档并中止——对象不合格不许开搜。

考卷八条（①~⑦逐字沿五十班，⑧本班新增）：
  ① 静态力矩需求 |M(θ*)+MY_DC(f0)| ≤ 0.7×0.0541 = 0.037870 N·m
  ② 动态升力中位 ≥0.95W
  ③ 闭环 60 s 高度 3±1 m（t>5 s 带内 ≥95%、z_min≥1.5、存活）
  ④ 俯仰跟踪 ±0.05 rad 带内 ≥80%
  ⑤ 舵 RMS ≤70% 满偏
  ⑥ 拍频带内（钉住口径）
  ⑦ 油门频响为正（DFX_DF>0）
  ⑧ 闭环俯仰建立 |θ_med−θ*|≤0.05 rad（t>5 s 窗中位）

搜索：22 组可行解（logs/full_search_50_r2.json，同一波形工作点×两族
控制器）参数 ±20% 邻域加密 LHS + 全域补采（判据⑥有效窗加密、SPLIT
偏⑦正侧），控制器整定在放开口径域（极点种子+LHS+22 组控制器邻域
+Nelder-Mead，代价在 drop=0.257 最坏档），终选按 DROP_GRID 全判据。

用法： python tools/full_search_60.py [--quick] [--workers N]
        [--n-nbhd N] [--n-global N] [--top-k N]
        [--out logs/full_search_60.json]
产物： logs/full_search_60.json + 控制台摘要 +（可行时）
        logs/_run60_best/_fb1/_fb2.json 三档上环参数。
纪律： tools/full_search_50.py 零改动（import 仅读）；不写 config/modules；
无 git 操作；不启动 Isaac。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
import time

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── 五十班考卷按文件路径装载（tools/ 无 __init__.py；模块零改动） ──
_SPEC = importlib.util.spec_from_file_location(
    "full_search_50", os.path.join(PROJECT_ROOT, "tools", "full_search_50.py"))
fs50 = importlib.util.module_from_spec(_SPEC)
sys.modules["full_search_50"] = fs50
_SPEC.loader.exec_module(fs50)

# ══════════════════ 本班补强常量（实测来源见头注） ══════════════════
EST_DEFICIT = -0.091          # N·m S1 考卷侧低头矩幅值（实测卡死态对账，
                              # 四态均值：跑一/二 × 窗 1.5~2.6/1.2~2.0 =
                              # −0.0950/−0.0935/−0.0847/−0.0871，散布 ±6%）
DIP_FULL = 0.14               # rad 深度斜坡满幅点=实测卡死深度（两跑两窗
                              # θ*−θ=0.1355/0.1395/0.1405/0.1556，取 0.14）
ELEV_DYN = 0.82               # S2 对象侧舵效折损（0.778/0.855 均值）
QK_RELEASE = 2.2              # 1/s S3 联合初条件角速度系数（首样本锚）
DROP_HI = 0.30                # rad 释放瞬态跌落上沿（0.257+裕量）
DROP_REAL = 0.257             # rad 第十七跑未治理实测档（最坏判决档）
DROP_GRID = (0.30, 0.257, 0.22, 0.18, 0.14, 0.10, 0.06, 0.0)
NBHD_FRAC = 0.20              # 22 组邻域半宽（用户令 ±20%）
C8_BAND = 0.05                # rad 判据⑧带宽（与判据④同口径）

# 俯仰通道域放开（③：KP_A/KI_A 上界倍增先例；其余轴沿五十班不动）
CTRL_HI60 = dict(fs50.CTRL_HI)
CTRL_HI60["KP_A"] = 6.40
CTRL_HI60["KI_A"] = 0.60
CTRL_KEYS = fs50.CTRL_KEYS
TAU_GRID = fs50.TAU_GRID
WAVE_KEYS = fs50.WAVE_KEYS

R50_JSON = os.path.join(PROJECT_ROOT, "logs", "full_search_50_r2.json")


# ══════════════════ 闭环（五十班对象+S1/S2/S3 补强） ══════════════════
def closed_loop_60(obj, ctrl, drop=DROP_REAL, t_sim=fs50.T_SIM,
                   dt=fs50.DT_CL, want_series=False, ov=None):
    """六十班闭环=fs50.closed_loop 逐字移植+S1/S2/S3 三补强。

    ov 键：沿五十班（MY_DC/B_ELEV_FIXED/GUARD_VZ/DIST_SCALE/NO_DC）+
    本班（EST_DEFICIT/ELEV_DYN/QK/NO_S1/NO_S2/NO_S3——补强消融开关，
    锚定对账用）。返回 metrics 增列 th_med（t>5 s 窗俯仰有符号中位）
    与 th_med_off（|th_med−θ*|）。"""
    ov = ov or {}
    w = obj["wave"]
    f0, V0, th_star = w["freq"], w["V"], obj["theta_star"]
    my_dc = ov.get("MY_DC", None)
    if my_dc is None:
        my_dc = fs50.my_dc_at(f0)
    b_fixed = ov.get("B_ELEV_FIXED")
    guard_vz = ov.get("GUARD_VZ", fs50.GUARD_VZ)
    dist_s = ov.get("DIST_SCALE", 1.0)
    no_dc = ov.get("NO_DC", False)
    est = 0.0 if ov.get("NO_S1") else ov.get("EST_DEFICIT", EST_DEFICIT)
    est_mode = ov.get("EST_MODE", "ramp")     # ramp=深度斜坡（主口径）/
                                              # const=恒值（敏感性对照）
    dip_full = float(ov.get("DIP_FULL", DIP_FULL))
    edyn = 1.0 if ov.get("NO_S2") else ov.get("ELEV_DYN", ELEV_DYN)
    qk = 0.0 if ov.get("NO_S3") else ov.get("QK", QK_RELEASE)
    kp_a, pdamp, ki_a = ctrl["KP_A"], ctrl["PITCH_DAMP"], ctrl["KI_A"]
    gate, tau, aff = ctrl["GATE"], ctrl["TAU"], ctrl["AFF"]
    v_free, v_lock = V0, V0 - 0.5
    lines = [(kk, a * dist_s, p) for kk, a, p in obj["beat"]]
    th_g = np.radians(obj["curve"]["theta_deg"])
    m_c = np.asarray(obj["curve"]["M_tot"], dtype=float)
    fz_c = np.asarray(obj["curve"]["Fz"], dtype=float)
    fx_c = np.asarray(obj["curve"]["Fx"], dtype=float)
    dc_wave = 0.0 if no_dc else obj["My_res"] + my_dc
    freq = fs50.F_PIN                     # 钉住频率口径沿五十班
    th = th_star - drop                   # S3：联合初条件（位置+角速度）
    q = -qk * drop
    vx, vz, z = V0, 0.0, 3.0
    ei, pitch_i = 0.0, 0.0
    pd_last, qf, vxf = th_star, 0.0, V0
    psi = [0.0] * len(lines)
    n_steps = int(round(t_sim / dt))
    crash_t = None
    b_out = min(1.0, dt / fs50.OUTER_FILT_TAU)
    oob = 0
    t_first_band = None
    fzw_l, z_l, th_l, th_s, el_l, f_l, vzw_l, t_l = [], [], [], [], [], [], [], []
    for i in range(n_steps):
        t = (i + 1) * dt
        vxf += b_out * (vx - vxf)
        for j, (kk, _a, _p) in enumerate(lines):
            psi[j] += 2.0 * math.pi * kk * freq * dt
        vxd = V0 - fs50.TECS_ZV_K * (z - fs50.Z_REF)
        vxd = min(max(vxd, max(fs50.V_CMD_MIN, 0.5 * V0)), 1.5 * V0)
        e_pot = fs50.G_ACC * (fs50.Z_REF - z)
        e_kin = 0.5 * (vxd * vxd - vxf * vxf)
        e_B = min(max(e_pot - e_kin, -60.0), 60.0)
        pitch_tgt = th_star + fs50.TECS_KP_E * e_B - fs50.TECS_KD_V * vz
        pitch_tgt = min(max(pitch_tgt, -fs50.TILT_MAX), fs50.TILT_MAX)
        if pitch_tgt < th_star:
            fade = min(max((vxf - v_lock) / max(v_free - v_lock, 1e-6), 0.0), 1.0)
            pitch_tgt = max(pitch_tgt,
                            th_star - (fade * fs50.TILT_MAX
                                       + (1 - fade) * fs50.V_DIVE_ALLOW))
        d = min(max(pitch_tgt - pd_last, -fs50.PITCH_DES_SLEW * dt),
                fs50.PITCH_DES_SLEW * dt)
        pd_last += d
        perr = (pd_last - th + math.pi) % (2.0 * math.pi) - math.pi
        if abs(perr) < gate:
            pitch_i = min(max(pitch_i + perr * dt, -fs50.KI_A_MAX),
                          fs50.KI_A_MAX)
        wyd = kp_a * perr + ki_a * pitch_i
        a_in = min(1.0, dt / max(tau, 1e-3))
        qf += a_in * (q - qf)
        my_dem = fs50.KP_R * (wyd - pdamp * qf)
        v_slip = fs50.SLIP_PER_HZ * freq
        veff = min(fs50.VEFF_CAP, math.sqrt(max(vx * vx, 0.0)
                                            + v_slip * v_slip))
        b_sched = fs50.K_TAIL_ELEV * veff * veff
        b_obj = b_fixed if b_fixed else b_sched * edyn     # S2：仅对象侧折损
        sb, cb = math.sin(th), math.cos(th)
        vbx = cb * vxf + sb * vz + v_slip
        vbz = sb * vxf + cb * vz
        alpha_hat = math.atan2(vbz, max(vbx, 0.1))
        elev = -my_dem / max(b_sched, 1e-6) - aff * alpha_hat
        elev = min(max(elev, -fs50.DELTA_MAX), fs50.DELTA_MAX)
        th_q = th if th_g[0] <= th <= th_g[-1] else (
            th_g[0] if th < th_g[0] else th_g[-1])
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
        if est_mode == "const":
            est_t = est
        else:                                  # S1 主口径：深度斜坡
            est_t = est * min(max((th_star - th_q) / dip_full, 0.0), 1.0)
        m_net = (float(np.interp(th_q, th_g, m_c)) + dc_wave + m_beat
                 - fs50.C_Q * q + obj["DMY_DVZ"] * vz - b_obj * elev
                 + est_t)                       # S1：建立段亏损（斜坡/恒值）
        vz += fz / fs50.MASS * dt - fs50.G_ACC * dt
        vx += fx / fs50.MASS * dt
        q += m_net / fs50.IYY * dt
        th += q * dt
        z += vz * dt
        if t > 1.0:                      # 飞行窗（早坠退化回退窗，上环提取器口径）
            fzw_l.append(fz / fs50.WEIGHT)
            z_l.append(z)
            th_l.append(abs(th - th_star))
            th_s.append(th)
            el_l.append(elev)
            f_l.append(freq)
            vzw_l.append(vz)
            t_l.append(t)
        if t_first_band is None and t > 0.5 and abs(th - th_star) <= C8_BAND:
            t_first_band = t
        if (t > 1.0 and z < fs50.Z_CRASH) or abs(th) > 1.5 or z > 20.0 \
                or abs(vx) > 10.0 or not (th == th and z == z):
            crash_t = t
            break
    # 判据窗：t>5 s 样本 >30 用之，否则回退飞行窗 t>1 s（上环提取器口径）
    t_arr = np.asarray(t_l)
    sel5 = t_arr > 5.0
    sel = sel5 if int(sel5.sum()) > 30 else (t_arr > 1.0)
    fzw_l = [v for v, s in zip(fzw_l, sel) if s]
    z_l = [v for v, s in zip(z_l, sel) if s]
    th_l = [v for v, s in zip(th_l, sel) if s]
    th_s = [v for v, s in zip(th_s, sel) if s]
    el_l = [v for v, s in zip(el_l, sel) if s]
    f_l = [v for v, s in zip(f_l, sel) if s]
    vzw_l = [v for v, s in zip(vzw_l, sel) if s]
    fzw_arr = np.asarray(fzw_l) if fzw_l else np.array([0.0])
    el_arr = np.asarray(el_l) if el_l else np.array([0.0])
    f_arr = np.asarray(f_l) if f_l else np.array([fs50.F_PIN])
    z_arr = np.asarray(z_l) if z_l else np.array([0.0])
    th_arr_s = np.asarray(th_l) if th_l else np.array([9.9])
    th_med = float(np.median(th_s)) if th_s else 9.9
    z_frac_w = float(np.mean((z_arr >= 2.0) & (z_arr <= 4.0))) if len(z_l) else 0.0
    m = {
        "crash_t": crash_t, "t_end": float((i + 1) * dt),
        "th0": float(th_star - drop), "release_drop": float(drop),
        "q0": float(-qk * drop),
        "t_first_band": t_first_band,
        "win": "t>5s" if int(sel5.sum()) > 30 else "flight(t>1s)",
        "z_frac": z_frac_w,
        "th_frac": float(np.mean(th_arr_s <= C8_BAND)) if len(th_l) else 0.0,
        "th_med": th_med, "th_med_off": abs(th_med - th_star),
        "fzw_med": float(np.median(fzw_arr)),
        "fzw_min": float(fzw_arr.min()),
        "elev_rms_frac": float(np.sqrt((el_arr ** 2).mean()) / fs50.DELTA_MAX),
        "elev_sat_frac": float(np.mean(np.abs(el_arr) >= 0.295)) if len(el_arr) else 0.0,
        "z_min": float(z_arr.min()) if len(z_l) else 0.0,
        "th_rms": float(np.sqrt((th_arr_s ** 2).mean())) if len(th_l) else 9.9,
        "vz_med": float(np.median(vzw_l)) if vzw_l else 0.0,
        "f_med": float(np.median(f_arr)),
        "f_dev": abs(float(np.median(f_arr)) - f0),
        "f_rail_hi_frac": float(np.mean(f_arr >= 3.45)) if len(f_arr) else 1.0,
        "f_rail_lo_frac": float(np.mean(f_arr <= min(1.0, f0) + 0.05)) if len(f_arr) else 1.0,
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


def judge_60(obj, m):
    """判据①~⑦=fs50.judge_all 逐字复用；⑧=|θ_med−θ*|≤0.05 rad。"""
    jd = fs50.judge_all(obj, m)
    c8 = bool(m["th_med_off"] <= C8_BAND)
    jd["c8_pitch_est"] = c8
    jd["all8"] = bool(jd["all7"] and c8)
    return jd


def cost_60(m, f0, th_star):
    """排序代价=fs50.cost 口径+判据⑧违例项（控制器整定与排序用）。"""
    viol = (3.0 * max(0.0, fs50.FZW_GATE - m["fzw_med"]) / 0.05
            + 3.0 * max(0.0, fs50.ZBAND_OCC_GATE - m["z_frac"]) / 0.05
            + 3.0 * max(0.0, fs50.TH_OCC_GATE - m["th_frac"]) / 0.20
            + 3.0 * max(0.0, m["th_med_off"] - C8_BAND) / 0.10
            + 2.0 * max(0.0, m["elev_rms_frac"] - fs50.ELEV_RMS_GATE) / 0.30
            + 3.0 * max(0.0, m["f_dev"] - max(0.20, 0.15 * f0)) / 0.30)
    tie = (m["th_rms"] / 0.05 + m["th_med_off"] / 0.10
           + m["elev_rms_frac"] / fs50.ELEV_RMS_GATE
           + abs(m["fzw_med"] - 1.0) / 0.10 + abs(m["vz_med"]) / 1.0)
    return 100.0 * viol + 0.3 * (fs50.T_SIM - m["t_end"]) + 0.1 * tie


# ══════════════════ 并行 worker（spawn 顶层） ══════════════════
def _cal_worker(w):
    return fs50.calibrate_full(w)


def _true_worker(w):
    return fs50._true_worker(w)


def _cl60_worker(args):
    obj, ctrl, drop, th_star, f0 = args
    return cost_60(closed_loop_60(obj, ctrl, drop=drop), f0, th_star)


def _nm60_one(args):
    from scipy.optimize import minimize
    obj, seed, drop = args
    th_star, f0 = obj["theta_star"], obj["wave"]["freq"]

    def unpack(x):
        p = {k: float(v) for k, v in zip(CTRL_KEYS, x)}
        p["TAU"] = seed["TAU"]
        return p

    bounds = [(fs50.CTRL_LO[k], CTRL_HI60[k]) for k in CTRL_KEYS]
    x0 = np.array([min(max(seed[k], fs50.CTRL_LO[k]), CTRL_HI60[k])
                   for k in CTRL_KEYS])
    r = minimize(lambda x: cost_60(closed_loop_60(obj, unpack(x), drop=drop),
                                   f0, th_star),
                 x0, method="Nelder-Mead", bounds=bounds,
                 options={"maxiter": 260, "xatol": 1e-3, "fatol": 1e-4})
    p = unpack(r.x)
    p["_J"] = float(r.fun)
    return p


# ══════════════════ 采样工具 ══════════════════
def _lhs(n, lo, hi, rng):
    return fs50.lhs(n, lo, hi, rng)


def _nbhd_box(center, frac):
    lo, hi = {}, {}
    for k in WAVE_KEYS:
        span = frac * abs(center[k]) if center[k] != 0.0 else frac
        lo[k] = max(fs50.WAVE_LO[k], center[k] - span)
        hi[k] = min(fs50.WAVE_HI[k], center[k] + span)
    return lo, hi


def _ctrl_nbhd(center, frac, n, rng):
    """控制器点 ±frac 邻域 LHS（TAU 走网格轮转）。"""
    out = []
    for i in range(n):
        c = {}
        for k in CTRL_KEYS:
            span = frac * abs(center[k]) if center[k] != 0.0 else frac
            c[k] = min(CTRL_HI60[k],
                       max(fs50.CTRL_LO[k],
                           center[k] + rng.uniform(-span, span)))
        c["TAU"] = TAU_GRID[i % len(TAU_GRID)]
        out.append(c)
    return out


def load_50_feasible(path=R50_JSON):
    d = json.load(open(path, encoding="utf-8"))
    fin = [f for f in d["finalists"] if f["judge"]["all7"]]
    return d, fin


# ══════════════════ stage 0：锚定复现（先于搜索） ══════════════════
def anchor_repro(ctx, fin):
    """五十班最优组在补强对象+drop=0.257 下复现实测失效指纹。

    实测指纹（docs/第五十班 §七表）：θ 带内 0%、实际中位 +0.1176/+0.1261、
    舵 RMS 90%/82.5%、存活 7.56/6.68 s。复现判据（记档级）：补强对象下
    th_med_off>0.05（⑧不过）且 th_frac<0.5（未建立），且对照消融
    （NO_S1/S2/S3 全关）下七判据全过（=考卷侧原口径自证）。"""
    best = json.load(open(os.path.join(PROJECT_ROOT, "logs",
                                       "full_search_50_r2.json"),
                          encoding="utf-8"))["best_set_all7"]
    w = {k: best["wave"][k] for k in WAVE_KEYS}
    ctrl = {k: best["ctrl"][k] for k in CTRL_KEYS}
    ctrl["TAU"] = best["ctrl"]["TAU"]
    obj = fs50.calibrate_full(w)
    if "pruned" in obj:
        raise SystemExit(f"[anchor] 五十班最优组标定被剪：{obj['pruned']}")
    m60 = closed_loop_60(obj, ctrl, drop=DROP_REAL)
    jd60 = judge_60(obj, m60)
    m_abl = closed_loop_60(obj, ctrl, drop=DROP_REAL,
                           ov={"NO_S1": True, "NO_S2": True, "NO_S3": True})
    jd_abl = judge_60(obj, m_abl)
    live_txt = "60s" if m60["crash_t"] is None else f"{m60['crash_t']:.2f}s"
    print("[锚定] 五十班最优组 @ 补强对象 drop=0.257："
          f"θ_med {m60['th_med']:+.4f}（实测在环 +0.1176/+0.1261）"
          f" 带内 {m60['th_frac']*100:.1f}%（实测 0%）"
          f" ⑧{'过' if jd60['c8_pitch_est'] else '不过'}"
          f" 存活 {live_txt}"
          f"（实测 7.56/6.68 s）舵RMS {m60['elev_rms_frac']*100:.0f}%"
          f"（实测 90/82.5%）", flush=True)
    print("[锚定·消融] S1/S2/S3 全关（=五十班考卷口径）："
          f"七判据 {'全过' if jd_abl['all7'] else '未全过'}"
          f"（考卷侧承诺 22 组全过）θ_med_off "
          f"{m_abl['th_med_off']:.4f}", flush=True)
    ok = (not jd60["c8_pitch_est"]) and m60["th_frac"] < 0.5 \
        and (m60["crash_t"] is not None or m60["z_frac"] < 0.5)
    return {"wave": w, "ctrl": ctrl, "obj": obj, "m60": m60,
            "judge60": jd60, "m_abl": m_abl, "judge_abl": jd_abl,
            "repro_ok": bool(ok)}


# ══════════════════ 寻优主体 ══════════════════
def run(args):
    import multiprocessing as mp
    quick = bool(args.quick)
    workers = args.workers or max(4, min(28, (os.cpu_count() or 8) - 4))
    n_nbhd = 48 if quick else int(args.n_nbhd)
    n_global = 160 if quick else int(args.n_global)
    top_k = 6 if quick else int(args.top_k)
    n_ctrl_lhs = 96 if quick else 384
    out_path = args.out or os.path.join(PROJECT_ROOT, "logs",
                                        "full_search_60.json")
    rng = np.random.default_rng(20260926)
    ctx = mp.get_context("spawn")
    log, t00 = [], time.time()

    d50, fin50 = load_50_feasible()
    feas_waves, feas_ctrls = [], []
    for f in fin50:
        wv = {k: f["wave"][k] for k in WAVE_KEYS}
        if not any(all(abs(wv[k] - e[k]) < 1e-6 for k in WAVE_KEYS)
                   for e in feas_waves):
            feas_waves.append(wv)
        feas_ctrls.append({k: f["ctrl"][k] for k in CTRL_KEYS}
                          | {"TAU": f["ctrl"]["TAU"]})
    print(f"[0] 五十班 r2 可行 {len(fin50)} 组={len(feas_waves)} 个波形工作点"
          f"×{len(feas_ctrls)} 控制器；邻域半宽 ±{NBHD_FRAC*100:.0f}%",
          flush=True)

    anchor = anchor_repro(ctx, fin50)
    if not anchor["repro_ok"]:
        print("═══ 锚定复现失败：补强对象未能复现实测失效指纹，中止开搜 ═══",
              flush=True)
        out = {"ts": time.strftime("%Y%m%d_%H%M%S"), "aborted": "anchor_repro",
               "anchor": {k: anchor[k] for k in
                          ("m60", "judge60", "m_abl", "judge_abl")}}
        with open(out_path, "w", encoding="utf-8") as fp:
            json.dump(out, fp, ensure_ascii=False, indent=1)
        return out

    # ── 阶段 A：22 组波形邻域 ±20% 加密 LHS + 轴向种子 ──
    cands = []
    for wv in feas_waves:
        lo, hi = _nbhd_box(wv, NBHD_FRAC)
        cands += _lhs(n_nbhd, lo, hi, rng)
        cands.append(dict(wv))                       # 中心点自身
        for k in WAVE_KEYS:                          # 轴向 ±5/±10%
            for s in (0.95, 0.90, 1.05, 1.10):
                w2 = dict(wv)
                w2[k] = min(fs50.WAVE_HI[k],
                            max(fs50.WAVE_LO[k], wv[k] * s
                                if wv[k] > 0 else wv[k] + s - 1.0))
                cands.append(w2)
    # ── 阶段 B：全域补采（全域 LHS + 判据⑥有效窗 + SPLIT 偏⑦正侧 +
    #    权限轴加密：高速档 V4.5~5.5——依据=补强对象静态权限账 b_obj·δmax
    #    随 veff² 增长，判据⑧⑧导向的采样密度非域收窄） ──
    cands += _lhs(n_global // 4, fs50.WAVE_LO, fs50.WAVE_HI, rng)
    win_lo = {**fs50.WAVE_LO, "freq": fs50.C6_WIN[0]}
    win_hi = {**fs50.WAVE_HI, "freq": fs50.C6_WIN[1]}
    cands += _lhs(n_global // 4, win_lo, win_hi, rng)
    cands += _lhs(n_global // 4,
                  {**win_lo, "split": 0.05}, {**win_hi, "split": 0.50}, rng)
    cands += _lhs(n_global - 3 * (n_global // 4),
                  {**win_lo, "split": 0.05, "V": 4.5},
                  {**win_hi, "split": 0.50, "V": 5.5}, rng)
    # ── 阶段 B2：权限-升力双约束角加密（五十班总体资产点导向；判据⑧
    #    静态账=高速大舵效权限对冲亏损，升力须同时可配平——五十班 2646
    #    幸存行中 V≥4.2 且 fzw_static∈[0.9,1.1] 且 ①⑥⑦ 全过的资产点
    #    逐点±20%邻域+角带 LHS 加密。采样密度非域收窄） ──
    corner = [r for r in d50.get("stageA", {}).get("rows", [])
              if "K_TH" in r and r["demand_static"] <= fs50.DEMAND_GATE
              and abs(fs50.F_PIN - r["wave"]["freq"])
              <= max(0.20, 0.15 * r["wave"]["freq"])
              and r.get("DFX_DF", -1) > 0
              and 0.90 <= r.get("fzw_static", 0.0) <= 1.10
              and r["wave"]["V"] >= 4.2]
    n_corner = 0
    for r in corner[:6]:
        wv = {k: float(r["wave"][k]) for k in WAVE_KEYS}
        cands.append(wv)
        for k in WAVE_KEYS:                      # 轴向 ±5/±10/±15%
            for s in (0.85, 0.90, 0.95, 1.05, 1.10, 1.15):
                w2 = dict(wv)
                v2 = wv[k] * s if wv[k] > 0 else wv[k] + (s - 1.0)
                w2[k] = min(fs50.WAVE_HI[k], max(fs50.WAVE_LO[k], v2))
                cands.append(w2)
        lo_c, hi_c = _nbhd_box(wv, NBHD_FRAC)
        cands += _lhs(40, lo_c, hi_c, rng)
        n_corner += 1
    cands += _lhs(240,
                  {**win_lo, "V": 4.2, "amplitude_deg": 10.0,
                   "alpha_set": 15.0, "mean_deg": 18.0},
                  {**win_hi, "V": 5.5, "amplitude_deg": 22.0,
                   "alpha_set": 32.0, "mean_deg": 30.0, "split": 0.50}, rng)
    print(f"[B2] 权限-升力角加密：五十班资产点 {n_corner} 个"
          f"（V≥4.2 且 fzw∈[0.9,1.1] 且 ①⑥⑦ 过）+角带 LHS 240", flush=True)
    seen, uniq = set(), []
    for w2 in cands:
        key_t = tuple(round(w2[k], 5) for k in WAVE_KEYS)
        if key_t not in seen:
            seen.add(key_t)
            uniq.append(w2)
    t0 = time.time()
    with ctx.Pool(workers) as pool:
        cal_rows = pool.map(_cal_worker, uniq, chunksize=2)
    n_pr = sum(1 for r in cal_rows if "pruned" in r)
    live = [r for r in cal_rows if "pruned" not in r]
    log.append({"stage": "AB_calibrate", "n": len(uniq), "pruned": n_pr,
                "sec": round(time.time() - t0, 1)})
    print(f"[AB] 邻域+全域标定 {len(uniq)} 组 {time.time()-t0:.0f}s："
          f"剪枝 {n_pr}，幸存 {len(live)}", flush=True)

    # ── 终选（多样性；判据①⑦⑥三过优先） ──
    def _pref(r):
        ok_c1 = r["demand_static"] <= fs50.DEMAND_GATE \
            and r.get("fzw_dyn_med") is not None
        ok_c7 = r["DFX_DF"] > 0.0
        in_win = abs(fs50.F_PIN - r["wave"]["freq"]) \
            <= max(0.20, 0.15 * r["wave"]["freq"])
        ok_c6 = in_win
        return 0 if (ok_c1 and ok_c7 and ok_c6) else (
            1 if (ok_c1 and ok_c7) else (2 if (ok_c1 or ok_c7) else 3))
    live.sort(key=lambda r: (_pref(r),
                             abs(r["vz_star"]) + 0.3 * abs(r["FX0"]),
                             -(r["fzw_dyn_med"]
                               if r.get("fzw_dyn_med") is not None else -9.9)))
    chosen = []
    for r in live:
        if all(fs50.norm_dist_wave(r["wave"], c["wave"]) > 0.12
               for c in chosen):
            chosen.append(r)
        if len(chosen) >= top_k * 2:
            break
    chosen = chosen[:top_k * 2]
    # 五十班可行波形点保送入池（若仍在幸存行中）
    for wv in feas_waves:
        hit = next((r for r in live
                    if all(abs(r["wave"][k] - wv[k]) < 5e-4
                           for k in WAVE_KEYS)), None)
        if hit is not None and not any(
                id(hit) == id(c) for c in chosen):
            chosen.append(hit)

    # ── A3：真模型 K=8 释放复核 ──
    n0 = len(chosen)
    t0 = time.time()
    if chosen:
        with ctx.Pool(min(workers, len(chosen))) as pool:
            trows = pool.map(_true_worker, [r["wave"] for r in chosen],
                             chunksize=1)
        keep = [r for r, tr in zip(chosen, trows)
                if tr.get("fzw_true_med") is not None
                and tr["fzw_true_med"] >= fs50.FZW_GATE
                and not tr.get("grounded")]
        if keep:
            chosen = keep
    log.append({"stage": "A3_true_release", "n": n0,
                "kept": len(chosen), "sec": round(time.time() - t0, 1)})
    print(f"[A3] 真模型释放复核幸存 {len(chosen)}/{n0}", flush=True)

    # ── 阶段 C：控制器整定（放开口径域，drop=0.257 最坏档代价） ──
    fs50.CTRL_HI["KP_A"] = CTRL_HI60["KP_A"]     # 极点种子域同步放开（复用）
    fs50.CTRL_HI["KI_A"] = CTRL_HI60["KI_A"]
    ctrl_pool = _lhs(n_ctrl_lhs, fs50.CTRL_LO, CTRL_HI60, rng)
    for i, c in enumerate(ctrl_pool):
        c["TAU"] = TAU_GRID[i % len(TAU_GRID)]
    ctrl_pool += [dict(c) for c in feas_ctrls]        # 22 组可行控制器
    for c in (fs50.RUN9_CTRL, fs50.RUN36_CTRL, fs50.REC41_CTRL):
        ctrl_pool.append(dict(c))
    for c0 in feas_ctrls[:2]:                          # 两族中心 ±20% 邻域
        ctrl_pool += _ctrl_nbhd(c0, NBHD_FRAC, 40, rng)
    jobs, scored = [], {}
    for r in chosen:
        seeds_b = fs50.pole_seeds(r)
        scored[id(r)] = []
        for c in ctrl_pool + seeds_b:
            jobs.append((r, c))
    t0 = time.time()
    with ctx.Pool(workers) as pool:
        Js = pool.map(_cl60_worker,
                      [(r, c, DROP_REAL, r["theta_star"], r["wave"]["freq"])
                       for r, c in jobs], chunksize=8) if jobs else []
    for (r, c), J in zip(jobs, Js):
        cc = dict(c)
        cc["_J"] = J
        scored[id(r)].append(cc)
    for r in chosen:
        scored[id(r)].sort(key=lambda c: c["_J"])
    log.append({"stage": "C_ctrl_grid", "n": len(jobs),
                "sec": round(time.time() - t0, 1)})
    print(f"[C] 控制器网格 {len(jobs)} 组 {time.time()-t0:.0f}s"
          f"（代价@drop=0.257）", flush=True)

    nm_jobs = []
    for r in chosen:
        seen_s = []
        for c in scored[id(r)]:
            if all(fs50.norm_dist_ctrl(c, s) > 0.08 for s in seen_s):
                seen_s.append(c)
            if len(seen_s) >= 2:
                break
        for s in seen_s:
            nm_jobs.append((r, s))
    t0 = time.time()
    if nm_jobs:
        with ctx.Pool(min(workers, len(nm_jobs))) as pool:
            refined = pool.map(_nm60_one,
                               [(r, s, DROP_REAL) for r, s in nm_jobs],
                               chunksize=1)
        for (r, _s), p in zip(nm_jobs, refined):
            scored[id(r)].append(p)
        for r in chosen:
            scored[id(r)].sort(key=lambda c: c["_J"])
    log.append({"stage": "C2_nelder_mead", "n": len(nm_jobs),
                "sec": round(time.time() - t0, 1)})
    print(f"[C2] NM 细化 {len(nm_jobs)} 组 {time.time()-t0:.0f}s", flush=True)

    # ── 阶段 D：DROP_GRID 全判据 + D_cap ──
    finals = []
    for r in chosen:
        for c in scored[id(r)][:8]:
            rows_d = []
            for d in DROP_GRID:
                m = closed_loop_60(r, c, drop=d,
                                   want_series=(len(finals) < 6
                                                and d == DROP_REAL))
                jd = judge_60(r, m)
                rows_d.append({"drop": d,
                               "all8": jd["all8"], "judge": jd,
                               "metrics": fs50._round_metrics(m)})
            d_cap = max([rr["drop"] for rr in rows_d if rr["all8"]],
                        default=None)
            finals.append({
                "wave": {k: round(r["wave"][k], 4) for k in WAVE_KEYS},
                "ctrl": {k: round(c[k], 4) for k in CTRL_KEYS}
                        | {"TAU": c["TAU"]},
                "obj_digest": fs50._digest(r),
                "demand_static": round(r["demand_static"], 5),
                "d_cap": d_cap, "drop_rows": rows_d,
                "true_release": r.get("true_release")})
    def _m_real(f):
        return next(rr["metrics"] for rr in f["drop_rows"]
                    if rr["drop"] == DROP_REAL)

    feas8 = [f for f in finals if f["d_cap"] is not None]
    feas8_real = [f for f in feas8 if f["d_cap"] >= DROP_REAL]
    feas8.sort(key=lambda f: (-f["d_cap"], _m_real(f)["th_med_off"],
                              _m_real(f)["elev_rms_frac"]))
    feas8_real.sort(key=lambda f: (_m_real(f)["th_med_off"],
                                   _m_real(f)["elev_rms_frac"]))
    n_d = {d: sum(1 for f in finals
                  if any(rr["drop"] == d and rr["all8"]
                         for rr in f["drop_rows"])) for d in DROP_GRID}
    print(f"[D] {len(chosen)} 终选×控制器 → {len(finals)} 组：八判据全过 "
          f"{len(feas8)}（其中 drop≥0.257 无治理档 {len(feas8_real)}）；"
          f"逐档过数 {json.dumps({str(k): v for k, v in n_d.items()})}",
          flush=True)

    # 判据⑧近失格表（①~⑦过而⑧不过者，drop=0.257 档）
    near8 = []
    for f in finals:
        rr = next(x for x in f["drop_rows"] if x["drop"] == DROP_REAL)
        if rr["judge"]["all7"] and not rr["all8"]:
            near8.append({"wave": f["wave"], "ctrl": f["ctrl"],
                          "th_med": rr["metrics"]["th_med"],
                          "th_med_off": rr["metrics"]["th_med_off"],
                          "th_frac": rr["metrics"]["th_frac"],
                          "elev_sat_frac": rr["metrics"]["elev_sat_frac"]})
    near8.sort(key=lambda x: x["th_med_off"])
    near8 = near8[:5]

    # 逐档最好成绩（八判据诊断：各 drop 档全体的最小⑧偏移与①~⑦过数）
    best_by_drop = {}
    for d in DROP_GRID:
        rows = [(next(x for x in f["drop_rows"] if x["drop"] == d), f)
                for f in finals]
        n7 = sum(1 for rr, _f in rows if rr["judge"]["all7"])
        rows.sort(key=lambda rf: rf[0]["metrics"]["th_med_off"])
        bb = []
        for rr, f in rows[:3]:
            bb.append({"th_med_off": round(rr["metrics"]["th_med_off"], 4),
                       "crash_t": rr["metrics"]["crash_t"],
                       "elev_rms_frac": round(rr["metrics"]["elev_rms_frac"], 3),
                       "wave_V": f["wave"]["V"],
                       "K_TH": f["obj_digest"]["K_TH"]})
        best_by_drop[str(d)] = {"n_all7": n7, "top3_by_off": bb}

    # 域界骑界审计（放开的俯仰通道两轴）
    edge = {"KP_A_hi": sum(1 for f in feas8
                           if f["ctrl"]["KP_A"] >= CTRL_HI60["KP_A"] * 0.98),
            "KI_A_hi": sum(1 for f in feas8
                           if f["ctrl"]["KI_A"] >= CTRL_HI60["KI_A"] * 0.98)}

    out = {
        "ts": time.strftime("%Y%m%d_%H%M%S"),
        "tool": "tools/full_search_60.py",
        "quick": quick, "workers": workers, "search_log": log,
        "caliber_60": {
            "c8_pitch_est": (f"|θ_med−θ*|≤0.05 rad（t>5 s 窗有符号中位，"
                             "带宽与判据④同口径）"),
            "S1_est_deficit": {
                "value": EST_DEFICIT, "mode": "ramp", "dip_full_rad": DIP_FULL,
                "source": ("实测卡死均衡态对账四态均值：跑一 θ=0.129/"
                           "vz=−0.82/elev=−0.196 与跑二 θ=0.125/vz=−0.82/"
                           "elev=−0.183（窗 1.5~2.6 s）及两窗加宽（1.2~2.0 s"
                           "），真实净矩≈0 对考卷对象同状态 +0.085~+0.095 → "
                           "幅值 Σ=−0.091±0.005；作用域=下探带斜坡（实测全部"
                           "位于 θ*−θ=0.135~0.156，配平上方无数据不假设，"
                           "0.14 rad 处满幅）；旁证=在环残差 IYY·ẇy−TMy 中位 "
                           "−0.0532/−0.0536（logs/ident_stream_flight_"
                           "0926_083229/083543.csv，t=1.2~6.0 s）")},
            "S2_elev_dyn": {
                "value": ELEV_DYN,
                "source": ("尾舵当量 My/(−elev) 于 |elev|>0.2：0.778/0.855 "
                           "对调度 0.0145·veff²（同两 CSV）")},
            "S3_qk_release": {
                "value": QK_RELEASE,
                "source": ("首样本 wy −0.274/−0.333 对首样本内跌落 0.1375 "
                           "rad → q(0)=−2.2·drop（第十七跑 0.257 上限档）"
                           )},
            "drop_axis": {"lo": 0.0, "hi": DROP_HI, "real": DROP_REAL,
                          "grid": list(DROP_GRID),
                          "note": ("释放瞬态治理项：初条件跌落量可搜索 "
                                   "0~0.3 rad，依据=第十七跑实测 0.257 为"
                                   "上限；组级 D_cap=八判据全过最大档")},
            "ctrl_open": {"KP_A": [fs50.CTRL_LO["KP_A"], CTRL_HI60["KP_A"]],
                          "KI_A": [fs50.CTRL_LO["KI_A"], CTRL_HI60["KI_A"]],
                          "note": "五十班 §五骑界开放性兑现（上界倍增先例）"},
        },
        "anchor_repro": {"m60": fs50._round_metrics(anchor["m60"]),
                         "judge60": anchor["judge60"],
                         "m_ablation": fs50._round_metrics(anchor["m_abl"]),
                         "judge_ablation": anchor["judge_abl"],
                         "repro_ok": anchor["repro_ok"]},
        "funnel": {"n_cal": len(uniq), "n_pruned": n_pr, "n_live": len(live),
                   "n_chosen": len(chosen), "n_finals": len(finals),
                   "n_feas8": len(feas8), "n_feas8_drop_real": len(feas8_real),
                   "pass_count_by_drop": {str(k): v
                                          for k, v in n_d.items()}},
        "finalists": finals,
        "best_set": feas8[0] if feas8 else None,
        "best_set_drop_real": feas8_real[0] if feas8_real else None,
        "alternates": feas8[1:4],
        "near8_table": near8,
        "best_by_drop": best_by_drop,
        "edge_audit": edge,
        "total_sec": round(time.time() - t00, 1),
    }
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print(f"[done] {out['total_sec']}s → {out_path}", flush=True)

    if feas8:
        def _tier(f):
            rr = next(x for x in f["drop_rows"] if x["drop"] == DROP_REAL)
            mm = rr["metrics"]
            return {"wave": f["wave"], "ctrl": f["ctrl"],
                    "d_cap": f["d_cap"], "metrics@real": mm,
                    "judge@real": rr["judge"]}
        print("\n═══ 最优组（八判据全过）═══")
        b = feas8[0]
        print("波形+工作点:", json.dumps(b["wave"], ensure_ascii=False))
        print("控制器:", json.dumps(b["ctrl"], ensure_ascii=False))
        print(f"D_cap={b['d_cap']}（0.257=无治理档）需求 "
              f"{b['demand_static']:.5f}")
        for rr in b["drop_rows"]:
            mm = rr["metrics"]
            alive_txt = ("60s" if mm["crash_t"] is None
                         else f"{mm['crash_t']:.1f}s")
            print(f"  drop={rr['drop']:<5} all8={'过' if rr['all8'] else '否'}"
                  f" θ_med {mm['th_med']:+.4f} 带内 {mm['th_frac']*100:.0f}%"
                  f" z带 {mm['z_frac']*100:.0f}% 舵RMS "
                  f"{mm['elev_rms_frac']*100:.0f}% 存活 {alive_txt}")
        # 上环三档参数（最优+两个不重复回退）
        tiers, used = [], set()
        for f in feas8:
            key = tuple(round(f["ctrl"][k], 4) for k in CTRL_KEYS) + (f["ctrl"]["TAU"],)
            if key in used:
                continue
            used.add(key)
            tiers.append(f)
            if len(tiers) >= 3:
                break
        for i, f in enumerate(tiers):
            th_star = f["obj_digest"]["theta_star"]
            tier = {"alpha_set": f["wave"]["alpha_set"],
                    "mean": f["wave"]["mean_deg"], "split": f["wave"]["split"],
                    "lag": f["wave"]["pitch_lag_s"], "amp": f["wave"]["amplitude_deg"],
                    "f0": f["wave"]["freq"], "V0": f["wave"]["V"],
                    "th_star_rad": round(th_star, 4),
                    "kp": f["ctrl"]["KP_A"], "pd": f["ctrl"]["PITCH_DAMP"],
                    "ki": f["ctrl"]["KI_A"], "gate": f["ctrl"]["GATE"],
                    "tau": f["ctrl"]["TAU"], "aff": f["ctrl"]["AFF"],
                    "akvf": f["ctrl"]["AKV_F"],
                    "d_cap": f["d_cap"],
                    "drop_grid_note": "60班 D_cap=八判据全过最大跌落档"}
            pth = os.path.join(PROJECT_ROOT, "logs",
                               "_run60_best.json" if i == 0
                               else f"_run60_fb{i}.json")
            with open(pth, "w", encoding="utf-8") as fp:
                json.dump(tier, fp, ensure_ascii=False, indent=1)
            print(f"上环档 {i+1} → {pth}（D_cap={f['d_cap']}）")
    else:
        print("\n═══ 八判据全过组：空 ═══（⑧近失格表与逐档过数见 JSON）")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--n-nbhd", type=int, default=720)
    ap.add_argument("--n-global", type=int, default=1200)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--out", default=None)
    args_main = ap.parse_args()
    run(args_main)
