# -*- coding: utf-8 -*-
"""
hss_i137_dose.py — 波形族通道定量处方，L4 线事项 I-137。

用冻结的自由度表（rl/预登记_训练环境重锚与准周期输入输出_20261005.md
第 3.1/3.2 节：包络连续通道含拍频、翼差动、占空比、幅值分数；波形四参
数现行值 MEAN 一点九八一零五度）与谐波限制（幅值分数相对登记幅值的乘
数），对三个新通道定量：

一、中置偏置：拍动面偏置的差动分量（左翼减右翼）经拍平均产生滚转力
矩，逐度标定 dMx/d(MEAN_L−MEAN_R)；对称分量给升力与俯仰矩的连带读
数。

二、振幅分数：相对登记幅值的乘数扫描，给拍平均升力代价曲线与对翻覆
增长率的影响（经配平重解与线性化）。

三、占空比：拍内作动时间占比的升力换算（准定常近似：有效作动分数内
力照常、其余相位零作动，均值近似乘占空比，声明近似口径）。

四、高度预算：转弯期间允许掉高度的换算——升力亏额对下沉率与高度损
失的解析换算（升力缺口除质量乘重力加速度方向投影），按判据文件高度
带口径给每秒掉高与可用转弯时长。

五、十度组合剂量：以 I-102 十度配平为基（翻覆增长率每秒正三点一八
六），叠加三通道剂量重解配平与线性化，判能否压进闭环可接管区。接管
区定义预登记：开环主模态实部不高于正一点六每秒（I-108 组合杠杆把三
点一九压到一点七六交闭环窄缝的同量级基准，五度基准次模态本稳处在其
下）。

产出写 ltv/i137_results 时间戳目录。全程零仿真器调用，纯离线解析。
"""
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _ROOT)

import hss_i102_stab as s1  # noqa: E402

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
BANK = 10.0
TAKEOVER_RE = 1.6
MEAN_DIFF_SWEEP = [1.0, 2.0, 4.0, 8.0]      # 度，左减右
MEAN_SYM_SWEEP = [2.0, 5.0, 10.0]           # 度，对称
AMP_FRAC_SWEEP = [0.95, 0.9, 0.8, 0.7, 0.6, 0.5]
DUTY_SWEEP = [0.9, 0.8, 0.7, 0.6, 0.5]
G = 9.81
MASS = s1.MASS


def wrench_wave(v_w, quat, w, freq, tail_cmd, mean_diff_deg=0.0,
                mean_sym_deg=0.0, amp_frac=1.0, duty=1.0):
    """拍平均 wrench，波形通道可参数化。

    中置差动：左翼加正差值右翼减正差值（度）。占空比：准定常近似，作
    动分数内相位照常出力、其余相位力矩置零（翼位冻结在中位不出力，
    模型层等价于均值乘占空比再补作动段内的相位加权）。
    """
    model = s1.FlapperAeroModel()
    tail = s1.TailFuselageAero()
    model.set_wing_command(frequency=freq)
    model.wing_left.amplitude = s1.A0_AMP * amp_frac
    model.wing_right.amplitude = s1.A0_AMP * amp_frac
    base_mean = np.radians(19.8105)
    model.wing_left.mean = base_mean + np.radians(mean_sym_deg
                                                  + mean_diff_deg / 2)
    model.wing_right.mean = base_mean + np.radians(mean_sym_deg
                                                   - mean_diff_deg / 2)
    n = s1.N_PHI
    Fs = np.zeros(3)
    Ts = np.zeros(3)
    for i in range(n):
        ph = i / (n * max(freq, 0.5))
        gate = 1.0 if (i / n) < duty else 0.0
        if gate == 0.0:
            continue
        F, T = model.compute_forces(np.zeros(3), quat, v_w, w, ph)
        Fs += F * gate
        Ts += T * gate
    Fm, Tm = Fs / n, Ts / n
    tn = float(np.linalg.norm(Tm))
    if tn > 2.0:
        Tm = Tm * (2.0 / tn)
    if tail_cmd is not None:
        tail.set_command(elevator=tail_cmd[0], aileron=tail_cmd[1],
                         rudder=tail_cmd[2])
    v_slip = 0.472
    Ft, Tt = tail.compute(quat, v_w, v_slip=v_slip)
    return Fm + Ft, Tm + Tt


def patch_wrench(**kw):
    """把 s1.WRENCH 临时替换为波形参数化版本。"""
    import types

    def w(v_w, quat, w, freq, tail_cmd=None, phase0=0.0, n=None,
          _kw=kw):
        return wrench_wave(v_w, quat, w, freq, tail_cmd, **_kw)
    old = s1.WRENCH
    s1.WRENCH = w
    return old


def measure(bank=BANK, **kw):
    """配平加线性化加主次模态，返回读数字典。"""
    old = patch_wrench(**kw)
    try:
        x, wz = s1.plant_trim(bank)
        A, s0 = s1.open_loop_A(x, wz, roll_deg=bank)
        w = sorted(np.linalg.eigvals(A), key=lambda v: -v.real)
        return {"trim": x.tolist(), "wz": wz,
                "re_main": float(w[0].real), "re_second": float(w[1].real),
                "re_third": float(w[2].real)}
    finally:
        s1.WRENCH = old


_TRIM_CACHE = {}

def base_trim(bank=BANK):
    if bank not in _TRIM_CACHE:
        _TRIM_CACHE[bank] = s1.plant_trim(bank)
    return _TRIM_CACHE[bank]


def lift_at(bank=BANK, **kw):
    """固定基准飞行状态的拍平均升力代价（体竖直通道力）。

    代价曲线必须固定状态评估：若随波形再配平，配平会通过俯仰与攻角
    吸收波形变化，把真实代价掩没（幅值减半升力只降百分之零点九的假
    读数即此成因）。稳定性组合档仍走再配平。
    """
    x, wz = base_trim(bank)
    u0, vy0, wb0, p0, e0, a0, r0 = x
    quat = s1.quat_from_rpy(math.radians(bank), p0, 0.0)
    R = s1.rot_body_from_world(quat)
    F, _ = wrench_wave(R.T @ np.array([u0, vy0, wb0]), quat,
                       np.array([0.0, 0.0, wz]), 1.8,
                       (e0, a0, r0), **kw)
    return float((R @ F)[2])


def roll_moment_at(bank=BANK, **kw):
    """固定基准状态的拍平均滚转力矩（中置差动剂量标定用）。"""
    x, wz = base_trim(bank)
    u0, vy0, wb0, p0, e0, a0, r0 = x
    quat = s1.quat_from_rpy(math.radians(bank), p0, 0.0)
    R = s1.rot_body_from_world(quat)
    _F, T = wrench_wave(R.T @ np.array([u0, vy0, wb0]), quat,
                        np.array([0.0, 0.0, wz]), 1.8,
                        (e0, a0, r0), **kw)
    return float((R @ T)[0])


def rollover_re(**kw):
    return measure(**kw)


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    t0 = time.time()
    base = measure()
    lift0 = lift_at()
    print("基准：主模态 %+.4f 次模态 %+.4f 升力 %.4f N" % (
        base["re_main"], base["re_second"], lift0))

    # 一、中置偏置（固定状态剂量标定加再配平稳定性）
    mx0 = roll_moment_at()
    mean_rows = []
    for d in MEAN_DIFF_SWEEP:
        m = measure(mean_diff_deg=d)
        mx = roll_moment_at(mean_diff_deg=d)
        mean_rows.append({"mean_diff_deg": d, "re": m["re_main"],
                          "second": m["re_second"],
                          "dmx_nm": mx - mx0,
                          "dmx_per_deg": (mx - mx0) / d})
        print("中置差动 %+g 度 → 固定状态 ΔMx %+.5f N·m（每度 %+.6f），"
              "再配平主 %+.4f 次 %+.4f" % (
                  d, mx - mx0, (mx - mx0) / d, m["re_main"],
                  m["re_second"]))
    sym_rows = []
    for d in MEAN_SYM_SWEEP:
        lift = lift_at(mean_sym_deg=d)
        sym_rows.append({"mean_sym_deg": d, "lift": lift})
        print("中置对称 %+g 度 → 升力 %.4f N（%+.1f%%）" % (
            d, lift, (lift / lift0 - 1) * 100))

    # 二、振幅分数
    amp_rows = []
    for f in AMP_FRAC_SWEEP:
        lift = lift_at(amp_frac=f)
        m = measure(amp_frac=f)
        amp_rows.append({"amp_frac": f, "lift": lift,
                         "lift_frac": lift / lift0,
                         "re": m["re_main"], "second": m["re_second"]})
        print("幅值分数 %.2f → 升力 %.4f N（%.1f%%）主模态 %+.4f" % (
            f, lift, lift / lift0 * 100, m["re_main"]))

    # 三、占空比
    duty_rows = []
    for d in DUTY_SWEEP:
        lift = lift_at(duty=d)
        duty_rows.append({"duty": d, "lift": lift,
                          "lift_frac": lift / lift0})
        print("占空比 %.2f → 升力 %.4f N（%.1f%%）" % (
            d, lift, lift / lift0 * 100))

    # 四、高度预算
    alt_rows = []
    for f in (1.0, 0.9, 0.8, 0.7):
        lift = lift_at(amp_frac=f)
        deficit = max(0.0, MASS * G - lift)
        sink = deficit / MASS
        alt_rows.append({"amp_frac": f, "lift": lift,
                         "sink_m_per_s": sink,
                         "seconds_per_meter": 1.0 / sink if sink > 0
                         else None})
        print("幅值分数 %.2f → 升力亏 %.3f N 下沉 %.3f m/s 每米 %.1f s"
              % (f, deficit, sink, 1.0 / sink if sink > 0 else float(
                  "inf")))

    # 五、十度组合剂量
    combo_rows = []
    for tag, kw in (
            ("幅值0.8", dict(amp_frac=0.8)),
            ("幅值0.6", dict(amp_frac=0.6)),
            ("中置差动2度", dict(mean_diff_deg=2.0)),
            ("中置差动4度", dict(mean_diff_deg=4.0)),
            ("差动2度加幅值0.8", dict(mean_diff_deg=2.0, amp_frac=0.8)),
            ("差动4度加幅值0.8", dict(mean_diff_deg=4.0, amp_frac=0.8)),
            ("差动4度加幅值0.6", dict(mean_diff_deg=4.0, amp_frac=0.6)),
            ("差动8度加幅值0.6", dict(mean_diff_deg=8.0, amp_frac=0.6)),
    ):
        m = measure(**kw)
        lift = lift_at(**kw)
        ok = m["re_main"] <= TAKEOVER_RE
        combo_rows.append({"tag": tag, "re": m["re_main"],
                           "second": m["re_second"], "lift": lift,
                           "takeover": bool(ok)})
        print("组合 %-16s → 主 %+.4f 次 %+.4f 升力 %.3f N %s" % (
            tag, m["re_main"], m["re_second"], lift,
            "★进接管区" if ok else ""))

    out = {"item": "I-137", "spec_sha256": spec_sha,
           "takeover_re_threshold": TAKEOVER_RE,
           "base": base, "base_lift": lift0,
           "mean_diff": mean_rows, "mean_sym": sym_rows,
           "amp": amp_rows, "duty": duty_rows, "alt": alt_rows,
           "combo": combo_rows, "new_sim_runs": 0}
    out_dir = os.path.join(_HERE, "i137_results",
                           time.strftime("i137_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print("产出目录：%s 用时 %.0fs" % (out_dir, time.time() - t0))
    return out


if __name__ == "__main__":
    main()
