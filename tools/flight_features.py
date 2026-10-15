#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
flight_features.py — 飞行记录波形特征提取器（F1~F7 标准化实现）
================================================================
用法:
  python tools/flight_features.py logs/ident_stream_flight_XXXX.csv [--windows]

输出:
  1. 控制台特征判定表（OK / WARN / ALARM / NODATA）
  2. <csv同名>.features.json    机器可读全量结果
  3. --windows 时另出 <csv同名>.features_windows.csv  逐秒时间线

门控规则（2026-09-20 教训固化，最高优先级）:
  一切特征只在空中段计算: z > AIR_Z_MIN 且 pin==0（pin 列存在时）。
  触地后数据单独归档为 post_touchdown 统计，绝不与空中段混合。
  采样率、混叠风险随结果一并报告。

特征与判据（与特征表一一对应）:
  F1 拍频比      目标/实际过零半周期中位数测频 ÷ 命令频率; |比值-1|>0.10 → ALARM
  F2 均值/纹波   按拍周期切段 mean 与 peak-peak; pp>max(|mean|,1.0) → WARN
  F3 阻尼斜率 ★  每 0.5s 取 (v̄,F̄) 对, 滑窗最小二乘 dF̄/dv̄;
                 任一窗 slope>0 且该窗 |v̄|>2 m/s → ALARM
  F4 钳位占空比  |Fw|>3mg(20.6N) 样本比例, 1s 滑窗; duty>5% → ALARM
  F5 频谱归属    Welch 谱分带功率 (<0.5Hz 低频带 / 1×f / 2×f);
                 低频带功率 > 2×f 峰功率 → WARN(某环自激候选原因)
  F6 指令滞后    pitch_des↔pitch FFT 互相关滞后; |滞后|>0.4×主振荡周期 → WARN
  F7 关节跟踪差  RMS(flapLt-flapLa) 1s 滑窗; RMS>0.3 rad → ALARM;
                 振幅比 act_std/tgt_std ∉ [0.7,1.3] → WARN
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

# ── 项目路径（读 MASS 算钳位阈值；读不到用缺省）──
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    from config.sim_config import MASS
    F_CLAMP = 3.0 * MASS * 9.81
except Exception:
    F_CLAMP = 20.6

# ── 判据常量（与特征表同步）──
AIR_Z_MIN = 0.3        # m  空中判定高度
TOUCH_Z = 0.15         # m  触地判定高度
F3_SEG = 0.5           # s  (v̄,F̄) 分段长
F3_VMIN = 2.0          # m/s 红警要求的速度下限
F3_WIN_PAIRS = 8       # 滑窗内最小点对数
F1_TOL = 0.10          # 拍频比偏离容限
F2_MEAN_FLOOR = 1.0    # m/s 纹波比判定的均值下限
F4_DUTY_ALARM = 0.05   # 钳位占空比阈值
F7_RMS_ALARM = 0.30    # rad
F7_AMP_LO, F7_AMP_HI = 0.70, 1.30
SLIDE_S = 1.0          # s  F4/F7 滑窗长

V_CHANNELS = ("vx", "vy", "vz", "wx", "wy", "wz")
F5_CHANNELS = ("vx", "vz", "pitch")
OK, WARN, ALARM, NODATA = "OK", "WARN", "ALARM", "NODATA"


# ============================================================
# 门控: 空中段 / 触地 / 分段
# ============================================================

def gate_flight(df: pd.DataFrame):
    """返回 (air_mask, air_segments, touchdown_t, fs)。

    air_segments: [(t0, t1), ...] 连续空中段列表。
    touchdown_t: 首个 z<TOUCH_Z 且此前存在空中样本的时刻; 无则 None。
    """
    t = df["t"].values
    z = df["z"].values
    pin = (df["pin"].values == 1) if "pin" in df.columns \
        else np.zeros(len(df), dtype=bool)
    air = (z > AIR_Z_MIN) & (~pin)
    segs, i, n = [], 0, len(t)
    while i < n:
        if air[i]:
            j = i
            while j + 1 < n and air[j + 1]:
                j += 1
            if t[j] - t[i] > 0.2:               # 段长>0.2s 才算
                segs.append((float(t[i]), float(t[j])))
            i = j + 1
        else:
            i += 1
    touch = None
    if segs:
        after = np.where((z < TOUCH_Z) & (t > segs[0][1] - 1e-6))[0]
        if len(after):
            touch = float(t[after[0]])
    dt = np.median(np.diff(t))
    return air, segs, touch, float(1.0 / max(dt, 1e-9))


# ============================================================
# F1 拍频比
# ============================================================

def _zc_freq(t: np.ndarray, x: np.ndarray) -> float:
    """上升过零半周期中位数测频; 过零不足 4 次返回 nan。"""
    ix = np.where((x[:-1] < 0) & (x[1:] >= 0))[0] + 1
    if len(ix) < 5:
        return float("nan")
    d = np.diff(t[ix])
    d = d[(d > 0.03) & (d < 2.0)]
    if len(d) < 4:
        return float("nan")
    return float(1.0 / (2.0 * np.median(d)))


def f1_flap_freq(t, tgt, act, freq_cmd):
    """返回 dict: 目标/实际测频、两比值、混叠风险标志。"""
    ft = _zc_freq(t, tgt)
    fa = _zc_freq(t, act)
    fc = float(np.nanmedian(freq_cmd))
    rt = ft / fc if np.isfinite(ft) and fc > 0 else float("nan")
    ra = fa / fc if np.isfinite(fa) and fc > 0 else float("nan")
    return {"f_target": ft, "f_actual": fa, "f_cmd": fc,
            "ratio_target": rt, "ratio_actual": ra}


# ============================================================
# F2 均值/纹波分离（按拍周期切段）
# ============================================================

def f2_mean_ripple(t, channels: dict, period: float):
    """channels: {名: 数组}。返回 {名: {mean, pp, ratio}}。"""
    out = {}
    edges = np.arange(t[0], t[-1], max(period, 1e-3))
    for name, x in channels.items():
        means, pps = [], []
        for a, b in zip(edges[:-1], edges[1:]):
            m = (t >= a) & (t < b)
            if m.sum() >= 3:
                seg = x[m]
                means.append(float(seg.mean()))
                pps.append(float(seg.max() - seg.min()))
        if not means:
            out[name] = {"mean": float("nan"), "pp": float("nan"),
                         "flag": NODATA}
            continue
        mean_r = float(np.mean(means))
        pp_r = float(np.mean(pps))
        scale = max(abs(mean_r), F2_MEAN_FLOOR)
        out[name] = {"mean": mean_r, "pp": pp_r,
                     "flag": WARN if pp_r > scale else OK}
    return out


# ============================================================
# F3 阻尼斜率 ★
# ============================================================

def f3_pairs(t, v, F, seg=F3_SEG):
    """按固定段长取 (v̄, F̄) 点对。"""
    edges = np.arange(t[0], t[-1], seg)
    pv, pf = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (t >= a) & (t < b)
        if m.sum() >= 3:
            pv.append(float(v[m].mean()))
            pf.append(float(F[m].mean()))
    return np.asarray(pv), np.asarray(pf)


def f3_damping(t, v, F):
    """返回 {slope, n_pairs, alarms:[窗口描述]}。滑窗=连续 F3_WIN_PAIRS 对。"""
    pv, pf = f3_pairs(t, v, F)
    if len(pv) < F3_WIN_PAIRS or np.ptp(pv) < 0.5:
        return {"slope": float("nan"), "n_pairs": int(len(pv)),
                "flag": NODATA, "alarms": []}
    alarms = []
    n = len(pv)
    worst = -np.inf
    for k in range(n - F3_WIN_PAIRS + 1):
        wv, wf = pv[k:k + F3_WIN_PAIRS], pf[k:k + F3_WIN_PAIRS]
        if np.ptp(wv) < 0.3:
            continue
        sl = float(np.polyfit(wv, wf, 1)[0])
        worst = max(worst, sl)
        if sl > 0 and np.mean(np.abs(wv)) > F3_VMIN:
            alarms.append({"pair_index": int(k),
                           "slope": round(sl, 3),
                           "v_mean_abs": round(float(np.mean(np.abs(wv))), 2)})
    gl = float(np.polyfit(pv, pf, 1)[0])
    return {"slope": gl, "worst_window_slope": worst if np.isfinite(worst)
            else float("nan"),
            "n_pairs": int(n), "flag": ALARM if alarms else OK,
            "alarms": alarms}


# ============================================================
# F4 钳位占空比（1s 滑窗, 向量化）
# ============================================================

def _slide_mean(x: np.ndarray, fs: float, win_s: float) -> np.ndarray:
    w = max(int(round(win_s * fs)), 1)
    c = np.concatenate([[0.0], np.cumsum(x)])
    out = (c[w:] - c[:-w]) / w if len(x) >= w else np.array([x.mean()])
    return out


def f4_clamp_duty(Fw: np.ndarray, fs: float):
    hit = (np.linalg.norm(Fw, axis=1) > F_CLAMP).astype(float)
    duty = _slide_mean(hit, fs, SLIDE_S)
    return {"duty_mean": float(hit.mean()), "duty_peak": float(duty.max()),
            "flag": ALARM if duty.max() > F4_DUTY_ALARM else OK}


# ============================================================
# F5 频谱归属（Welch; scipy 缺失时 numpy 兜底）
# ============================================================

def _welch(x: np.ndarray, fs: float):
    x = x - x.mean()
    nper = max(int(fs * 2) // 1, 64)              # ~2s 窗
    nper = min(nper, len(x))
    if nper < 16:
        return np.array([0.0]), np.array([0.0])
    try:
        from scipy.signal import welch
        return welch(x, fs=fs, nperseg=nper)
    except ImportError:
        step = max(nper // 2, 1)
        win = np.hanning(nper)
        acc, cnt = None, 0
        for s in range(0, len(x) - nper + 1, step):
            seg = x[s:s + nper] * win
            p = (np.abs(np.fft.rfft(seg)) ** 2) / (fs * (win ** 2).sum())
            acc = p if acc is None else acc + p
            cnt += 1
        f = np.fft.rfftfreq(nper, 1.0 / fs)
        return f, acc / max(cnt, 1)


def f5_spectrum(t, channels: dict, f_meas: float, fs: float):
    out = {}
    for name, x in channels.items():
        f, p = _welch(x, fs)
        if len(f) < 4:
            out[name] = {"flag": NODATA}
            continue
        band = lambda lo, hi: float(p[(f >= lo) & (f < hi)].sum())
        f0 = f_meas if np.isfinite(f_meas) else \
            (f[np.argmax(p)] if np.isfinite(f_meas) else 1.9)
        low = band(0.05, 0.5)
        b1 = band(max(f0 - 0.25, 0.05), f0 + 0.25)
        b2 = band(f0 * 2 - 0.5, f0 * 2 + 0.5)
        pk = float(f[np.argmax(p)])
        out[name] = {"dominant_hz": round(pk, 3),
                     "pow_low_lt0p5": round(low, 4),
                     "pow_1f": round(b1, 4), "pow_2f": round(b2, 4),
                     "flag": WARN if low > max(b1, b2, 1e-12) else OK}
    return out


# ============================================================
# F6 指令滞后角（FFT 互相关）
# ============================================================

def f6_lag(cmd, state, fs):
    a = cmd - cmd.mean()
    b = state - state.mean()
    if a.std() < 1e-9 or b.std() < 1e-9:
        return {"lag_s": float("nan"), "flag": NODATA}
    n = len(a)
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    cc = np.fft.irfft(np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft)))
    cc = np.concatenate([cc[-(n // 2):], cc[:n // 2 + 1]])
    k = int(np.argmax(np.abs(cc))) - (n // 2)
    lag = k / fs                                 # 正=状态滞后于指令
    # 主振荡周期: 状态去均值后上升过零间隔中位数×2
    ix = np.where((b[:-1] < 0) & (b[1:] >= 0))[0] + 1
    d = np.diff(np.arange(len(b))[ix]) / fs
    T = float(2 * np.median(d)) if len(d) >= 4 else float("nan")
    ratio = abs(lag) / T if np.isfinite(T) and T > 0 else float("nan")
    return {"lag_s": round(float(lag), 4),
            "state_period_s": round(T, 3) if np.isfinite(T) else None,
            "lag_over_period": round(ratio, 3) if np.isfinite(ratio) else None,
            "flag": NODATA if not np.isfinite(ratio)
            else (WARN if ratio > 0.4 else OK)}


# ============================================================
# F7 关节跟踪差
# ============================================================

def f7_tracking(t, tgt, act, fs):
    err = tgt - act
    rms = np.sqrt(_slide_mean(err ** 2, fs, SLIDE_S))
    amp = act.std() / tgt.std() if tgt.std() > 1e-9 else float("nan")
    flag = OK
    if np.isfinite(rms.max()) and rms.max() > F7_RMS_ALARM:
        flag = ALARM
    elif np.isfinite(amp) and not (F7_AMP_LO <= amp <= F7_AMP_HI):
        flag = WARN
    return {"rms_max": float(rms.max()) if len(rms) else float("nan"),
            "amp_ratio": float(amp), "flag": flag}


# ============================================================
# 主流程
# ============================================================

def analyze(path: str, write_windows: bool = False) -> dict:
    df = pd.read_csv(path)
    # 姿态口径修正（第六十三班，本体级）：ident_stream CSV 的 "pitch"/"roll"
    # 列实为四元数分量 qy/qx（main_sim 落盘行为，第六十二班 §一.1 勘误），
    # 一切姿态判读禁止直读。此处由四元数列（qw、qx="roll"列、qy="pitch"列、
    # qz）欧拉换算后整列覆盖，换算式与在环控制器 _euler 同源；F5/F6 等
    # 姿态通道自此读到的即为欧拉角。追加列 "qx"（重复 pose[5]）与 "qy"
    # （恒 0）系落盘配对缺陷，不作为数据源。
    # 2026-09-29 转弯线落盘修复适配：main_sim 装配已修正（roll/pitch
    # 列写欧拉角、qx/qy 列写真分量）。以 qy 列是否恒零探测格式：旧档
    # qy 列恒零走重建；新档 qy 列非零（真分量有动）则直读欧拉列。
    if {"qw", "roll", "pitch", "qz"} <= set(df.columns):
        _old_fmt = True
        if "qy" in df.columns:
            _old_fmt = not bool(np.any(np.abs(df["qy"].values) > 1e-9))
        if _old_fmt:
            _qw = df["qw"].values; _qx = df["roll"].values
            _qy = df["pitch"].values; _qz = df["qz"].values
            df["pitch"] = np.arcsin(np.clip(2.0 * (_qw * _qy - _qz * _qx),
                                            -1.0, 1.0))
            df["roll"] = np.arctan2(2.0 * (_qw * _qx + _qy * _qz),
                                    1.0 - 2.0 * (_qx ** 2 + _qy ** 2))
    else:
        raise SystemExit(
            "[flight_features] 缺四元数源列（qw/roll/pitch/qz）："
            f"欧拉口径无法换算（第六十三班姿态判读口径）: {path}")
    need = {"t", "z", "freq", "Fx", "Fy", "Fz"}
    miss = need - set(df.columns)
    if miss:
        raise SystemExit(f"[flight_features] 缺列 {miss}, 无法分析: {path}")
    air, segs, touch, fs = gate_flight(df)
    if not segs:
        raise SystemExit("[flight_features] 无空中段(全部为躺地数据或状态锁定期), 无可分析数据")
    d = df[air]
    t = d["t"].values

    res = {"file": os.path.basename(path), "fs_hz": round(fs, 1),
           "n_air": int(air.sum()),
           "air_segments_s": [(round(a, 2), round(b, 2)) for a, b in segs],
           "air_duration_s": round(float(t[-1] - t[0]), 2),
           "touchdown_t": round(touch, 2) if touch is not None else None,
           "f_clamp_n": round(F_CLAMP, 2)}

    # F1（拍频, 兼作 F2/F5 的周期基准）
    f1 = f1_flap_freq(t, d["flapLt"].values, d["flapLa"].values,
                      d["freq"].values) if "flapLt" in d.columns else {}
    if f1:
        r = f1["ratio_target"]
        f1["flag"] = NODATA if not np.isfinite(r) else \
            (ALARM if abs(r - 1.0) > F1_TOL else OK)
        f1["alias_risk"] = bool(np.isfinite(f1["f_target"])
                                and f1["f_target"] > 0.4 * fs)
    res["F1_flap_freq"] = f1

    period = 1.0 / f1["f_target"] if f1 and np.isfinite(f1.get("f_target",
                                                                np.nan)) \
        else 1.0 / max(np.nanmedian(d["freq"].values), 0.5)

    # F2
    have2 = [c for c in V_CHANNELS if c in d.columns]
    res["F2_mean_ripple"] = f2_mean_ripple(
        t, {c: d[c].values for c in have2}, period)

    # F3 ★
    f3 = {}
    for ax in "xyz":
        f3[ax] = f3_damping(t, d[f"v{ax}"].values, d[f"F{ax}"].values)
    res["F3_damping"] = f3

    # F4
    fwcols = [c for c in ("Fwx", "Fwy", "Fwz") if c in d.columns]
    if len(fwcols) == 3:
        res["F4_clamp_duty"] = f4_clamp_duty(d[fwcols].values, fs)
    else:
        res["F4_clamp_duty"] = {"flag": NODATA, "why": "缺 Fwx/Fwy/Fwz 列"}

    # F5
    have5 = [c for c in F5_CHANNELS if c in d.columns]
    res["F5_spectrum"] = f5_spectrum(
        t, {c: d[c].values for c in have5},
        f1.get("f_target", np.nan), fs)

    # F6
    if "pitch_des" in d.columns and "pitch" in d.columns:
        res["F6_cmd_lag"] = f6_lag(d["pitch_des"].values, d["pitch"].values,
                                   fs)
    else:
        res["F6_cmd_lag"] = {"flag": NODATA}

    # F7
    if "flapLt" in d.columns and "flapLa" in d.columns:
        res["F7_tracking_L"] = f7_tracking(t, d["flapLt"].values,
                                           d["flapLa"].values, fs)
        res["F7_tracking_R"] = f7_tracking(t, d["flapRt"].values,
                                           d["flapRa"].values, fs)

    # 触地后归档（单独统计, 不进特征）
    if touch is not None:
        post = df[(df["t"].values >= touch)]
        res["post_touchdown"] = {
            "n": int(len(post)),
            "duration_s": round(float(post["t"].values[-1] - touch), 2),
            "note": "触地躺平段已隔离, 不参与任何特征"}

    # 逐窗时间线
    if write_windows:
        rows = []
        t0 = t[0]
        for lo in np.arange(t0, t[-1], 1.0):
            m = (t >= lo) & (t < lo + 1.0)
            if m.sum() < 5:
                continue
            tt = t[m]
            row = {"t": round(lo, 2)}
            for ax in "xyz":
                pv, pf = f3_pairs(tt, d[f"v{ax}"].values[m],
                                  d[f"F{ax}"].values[m], seg=0.25)
                row[f"F3_{ax}"] = round(float(np.polyfit(pv, pf, 1)[0]), 3) \
                    if len(pv) >= 3 and np.ptp(pv) > 0.2 else np.nan
            if len(fwcols) == 3:
                fw = d[fwcols].values[m]
                row["F4_duty"] = round(float(np.mean(
                    np.linalg.norm(fw, axis=1) > F_CLAMP)), 3)
            if "flapLt" in d.columns:
                e = d["flapLt"].values[m] - d["flapLa"].values[m]
                row["F7_rms"] = round(float(np.sqrt(np.mean(e ** 2))), 3)
            rows.append(row)
        wpath = path.replace(".csv", ".features_windows.csv")
        pd.DataFrame(rows).to_csv(wpath, index=False)
        res["windows_csv"] = os.path.basename(wpath)

    # JSON 落盘
    jpath = path.replace(".csv", ".features.json")

    def _default(o):
        if isinstance(o, (np.floating, np.integer)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(type(o))

    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=_default)
    res["json"] = os.path.basename(jpath)
    return res


def _print_report(r: dict):
    print("=" * 72)
    touch = r["touchdown_t"]
    print(f"飞行特征报告: {r['file']}  采样率 {r['fs_hz']}Hz  "
          f"空中 {r['air_duration_s']}s/{r['n_air']}样本  "
          f"触地 t={touch}s" if touch is not None else
          f"飞行特征报告: {r['file']}  采样率 {r['fs_hz']}Hz  "
          f"空中 {r['air_duration_s']}s/{r['n_air']}样本  全程未触地")
    print(f"空中段: {r['air_segments_s']}  钳位阈值 {r['f_clamp_n']}N")
    print("=" * 72)

    def line(tag, name, st, detail):
        print(f"[{st:6s}] {tag} {name}: {detail}")

    f1 = r.get("F1_flap_freq", {})
    if f1:
        line(f1.get("flag", NODATA), "F1", "拍频比",
             f"目标 {f1.get('f_target', float('nan')):.2f}Hz / 实际 "
             f"{f1.get('f_actual', float('nan')):.2f}Hz / 命令 "
             f"{f1.get('f_cmd', float('nan')):.2f}Hz -> 比值 "
             f"{f1.get('ratio_target', float('nan')):.2f}"
             + ("  [混叠风险]" if f1.get("alias_risk") else ""))
    for name, v in r.get("F2_mean_ripple", {}).items():
        if v.get("flag") != NODATA:
            line(v["flag"], "F2", name,
                 f"均值 {v['mean']:+.2f} 纹波pp {v['pp']:.2f}")
    for ax, v in r.get("F3_damping", {}).items():
        if v.get("flag") == NODATA:
            line(NODATA, "F3★", f"{ax}轴 dF/dv",
                 f"点对不足({v.get('n_pairs', 0)}段)")
        else:
            ww = v.get("worst_window_slope")
            ww = ww if ww is not None and np.isfinite(ww) else float("nan")
            line(v["flag"], "F3★", f"{ax}轴 dF/dv",
                 f"全局 {v['slope']:+.2f} 最差窗 {ww:+.2f} N·s/m  "
                 f"红警窗 {len(v.get('alarms', []))}")
    v = r.get("F4_clamp_duty", {})
    if v.get("flag") != NODATA:
        line(v["flag"], "F4", "钳位占空比",
             f"均值 {v['duty_mean']*100:.0f}% 峰值窗 {v['duty_peak']*100:.0f}%")
    for name, v in r.get("F5_spectrum", {}).items():
        if v.get("flag") != NODATA:
            line(v["flag"], "F5", name,
                 f"主峰 {v['dominant_hz']}Hz  低频带 {v['pow_low_lt0p5']}"
                 f" 1f {v['pow_1f']} 2f {v['pow_2f']}")
    v = r.get("F6_cmd_lag", {})
    if v.get("flag") != NODATA:
        line(v["flag"], "F6", "pitch 指令滞后",
             f"滞后 {v['lag_s']}s / 周期 {v.get('state_period_s')}s "
             f"= {v.get('lag_over_period')}")
    for side in ("L", "R"):
        v = r.get(f"F7_tracking_{side}", {})
        if v:
            line(v["flag"], "F7", f"{side}翼跟踪",
                 f"RMS {v['rms_max']:.3f}rad 振幅比 {v['amp_ratio']:.2f}")
    if "post_touchdown" in r:
        p = r["post_touchdown"]
        print(f"[隔离  ] 触地后 {p['duration_s']}s/{p['n']}样本 "
              f"已单独归档, 不参与特征")
    print(f"JSON: {r.get('json')}")


def main():
    ap = argparse.ArgumentParser(description="飞行记录 F1~F7 特征提取")
    ap.add_argument("csv", help="ident_stream_flight_*.csv 路径")
    ap.add_argument("--windows", action="store_true",
                    help="另出逐秒时间线 CSV")
    a = ap.parse_args()
    r = analyze(a.csv, write_windows=a.windows)
    _print_report(r)


if __name__ == "__main__":
    main()
