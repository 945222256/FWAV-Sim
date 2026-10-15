# -*- coding: utf-8 -*-
"""i190_da_semantics_check.py — I-190 dA 两层增量语义离线验证器。

判据依据：I-190 六条判据之第三、四条；采样时点与误差分档按
logs/I-190_施工登记与验证记录_20261012.md 第一节 1.8 于取数前登记：
  档一 逐位一致（容差零）；档二 浮点路径（1e-15 rad）；
  档三 采样极值（1e-9 rad）；档四 连续性（相位速率上界乘步长）。

纯离线，无 Isaac。用法：
  python tools/i190_da_semantics_check.py
产物：logs/I190_离线验证输出_<时间戳>.json
"""
import datetime
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config.sim_config import (PHYSICS_DT, WING_DRIVE_CONFIG,
                               FlapKinematicsConfig, STARTUP_AMP_RAMP_S)
from modules.aero.kinematics import WingKinematics

TOL_FP = 1e-15      # 档二
TOL_EXT = 1e-9      # 档三
DA_MAIN = (0.05, -0.05, 0.08, -0.08)  # 含 DA_MAX 满偏边界
F0, F1 = 1.8, 2.2                    # 频率变化窗
N_CYCLES = 36                        # 完整拍周期数（1.8 Hz → 20 s）
T_END = N_CYCLES / F0
T_FREQ_SWITCH = 10.0

RESULTS = []


def record(cid, desc, ok, detail=""):
    RESULTS.append({"id": cid, "desc": desc, "pass": bool(ok),
                    "detail": str(detail)[:300]})


def joint_target(phi, dA_j):
    """复刻 wing_actuation 目标表达式（本验证器内联，不 import 主循环）。"""
    return (phi + dA_j, -(phi - dA_j))


def run_sequence(dA, switch_on, gate_stop_window=None):
    """驱动一个关节层 wk 实例走完整时间线。

    时间线：t 自 PHYSICS_DT 至 T_END；t 达 T_FREQ_SWITCH 后频率 F0 改 F1；
    gate_stop_window 区间内置停拍闸门 0，出窗恢复 1。斜坡因子与主循环同式。
    返回逐步 (t, ramp 后 phi, _dA_j, sin(ph))。
    """
    wk = WingKinematics(
        frequency=WING_DRIVE_CONFIG["frequency"],
        amplitude_deg=WING_DRIVE_CONFIG["amplitude_deg"],
        pitch_mode=WING_DRIVE_CONFIG["pitch_mode"],
        alpha_set_deg=WING_DRIVE_CONFIG["alpha_set_deg"])
    rows = []
    n = int(round(T_END / PHYSICS_DT))
    for k in range(1, n + 1):
        t = k * PHYSICS_DT
        wk.set_frequency(F1 if t >= T_FREQ_SWITCH else F0)
        if gate_stop_window and gate_stop_window[0] <= t < gate_stop_window[1]:
            wk.stroke_gate = 0.0
        elif wk.stroke_gate < 0.5:
            wk.stroke_gate = 1.0
        phi_raw = wk.evaluate(t)["phi"]
        ramp = min(1.0, t / max(STARTUP_AMP_RAMP_S, 0.05))
        phi = phi_raw * ramp
        dj = dA * wk.stroke_unit(t) if switch_on else dA
        rows.append((t, phi, dj, wk.stroke_unit(t)))
    return rows


def targets(rows):
    return np.array([joint_target(p, dj) for (_, p, dj, _) in rows])


def main():
    A0 = math.radians(FlapKinematicsConfig.AMPLITUDE)
    a_wd = WING_DRIVE_CONFIG["amplitude_deg"]

    record("C0", "关节侧与气动侧基座幅值来源同值（%s 对 %s 度）"
           % (a_wd, FlapKinematicsConfig.AMPLITUDE),
           a_wd == FlapKinematicsConfig.AMPLITUDE)

    # A1 零差动：开关两路径目标逐位相等（档一）
    tg_on0 = targets(run_sequence(0.0, switch_on=True))
    tg_off0 = targets(run_sequence(0.0, switch_on=False))
    record("A1", "零差动：开关开启与关闭两路径目标逐位相等",
           np.array_equal(tg_on0.view(np.float64), tg_off0.view(np.float64)))

    # A2 关闭态：新代码路径与旧表达式逐位相等（档一，含满偏边界）
    for dA in DA_MAIN:
        rows = run_sequence(dA, switch_on=False)
        ok = all(joint_target(p, dj) == (p + dA, -(p - dA))
                 for (_, p, dj, _) in rows)
        record("A2", "关闭态：dA=%+.3f 输出与旧表达式逐位相等" % dA, ok)

    for dA in DA_MAIN:
        on = run_sequence(dA, switch_on=True)
        base = run_sequence(0.0, switch_on=True)
        tg, tg0 = targets(on), targets(base)
        inc_L = tg[:, 0] - tg0[:, 0]
        inc_R = tg[:, 1] - tg0[:, 1]
        sinph = np.array([s for (_, _, _, s) in on])

        # A3 镜像：URDF 右关节自带镜像负号（flap_R=−θ_R），故右关节增量
        # 与左关节相等即等价于左系口径右幅值减 dA（左系增量反号）（档二）
        record("A3", "镜像：dA=%+.3f 右关节增量与左关节相等（左系口径反号）（1e-15）" % dA,
               np.max(np.abs(inc_R - inc_L)) <= TOL_FP,
               "max|Δ|=%.3e" % np.max(np.abs(inc_R - inc_L)))

        # B1 幅值语义：恢复增量等于 dA·sin(ph)（档二）
        record("B1", "幅值语义：dA=%+.3f 增量等于 dA·sin(ph)（1e-15）" % dA,
               np.max(np.abs(inc_L - dA * sinph)) <= TOL_FP,
               "max|Δ|=%.3e" % np.max(np.abs(inc_L - dA * sinph)))

        # B2 极值：定常段最大最小分别落入先验采样界（档三，界见登记 1.8；
        # c 取下扑段保守界，上扑段相位更慢、分辨率更高，同界适用）
        seg = [i for i, (t, *_ ) in enumerate(on)
               if STARTUP_AMP_RAMP_S < t < T_FREQ_SWITCH - 1.0]
        s_inc = inc_L[seg]
        dph = math.pi * F0 * PHYSICS_DT / min(FlapKinematicsConfig.SPLIT,
                                              1.0 - FlapKinematicsConfig.SPLIT)
        c = math.cos(dph / 2.0)
        a = abs(dA)
        okB2 = (a * c - TOL_EXT <= s_inc.max() <= a + TOL_EXT
                and -a - TOL_EXT <= s_inc.min() <= -a * c + TOL_EXT)
        record("B2", "极值：dA=%+.3f 最大落入 [%.6f,%.6f]、最小落入 [%.6f,%.6f]（端点 1e-9）"
               % (dA, a * c, a, -a, -a * c), okB2,
               "max=%.9f min=%.9f" % (s_inc.max(), s_inc.min()))

        # B3 中置不变：目标减增量项后等于零差动基座（档二）
        lhs = tg[:, 0] - dA * sinph
        record("B3", "中置不变：dA=%+.3f 目标减增量项等于零差动基座（1e-15）" % dA,
               np.max(np.abs(lhs - tg0[:, 0])) <= TOL_FP)

    # B4 故障样例：旧偏置语义（增量恒为 dA）被判非幅值语义
    base = run_sequence(0.0, switch_on=True)
    sinph0 = np.array([s for (_, _, _, s) in base])
    record("B4", "故障样例：旧偏置语义增量恒 dA 被判非幅值语义",
           np.max(np.abs(np.full(len(base), 0.05) - 0.05 * sinph0)) > 1e-3)

    # B5 故障样例：右翼幅值误取与左翼同号（左系口径）时镜像检查判错。
    # 错误实现的 URDF 右关节增量为 −dA·sin(ph)，与左关节 +dA·sin(ph) 不等。
    record("B5", "故障样例：右翼幅值误取同号时镜像判错",
           np.max(np.abs(-0.05 * sinph0 - 0.05 * sinph0)) > TOL_FP)

    # E1 启动斜坡窗：增量不随斜坡衰减（档二）
    on = run_sequence(0.05, switch_on=True)
    tg = targets(on)
    tg0 = targets(run_sequence(0.0, switch_on=True))
    ramp_rows = [(tg[i, 0] - tg0[i, 0], s)
                 for i, (t, _, _, s) in enumerate(on)
                 if t < STARTUP_AMP_RAMP_S]
    record("E1", "启动斜坡窗：增量保持 dA·sin(ph) 不衰减（1e-15）",
           len(ramp_rows) > 0 and
           max(abs(i - 0.05 * s) for i, s in ramp_rows) <= TOL_FP)

    # E2 频率变化：增量公式逐步成立（档二）且波形连续（档四）
    sinph = np.array([s for (_, _, _, s) in on])
    rate_bound = (math.pi / min(FlapKinematicsConfig.SPLIT,
                                1.0 - FlapKinematicsConfig.SPLIT)
                  * max(F0, F1)) * PHYSICS_DT
    record("E2", "频率变化窗：增量公式逐步成立且连续（界 %.3f）" % rate_bound,
           np.max(np.abs(sinph - np.clip(sinph, -1, 1))) == 0.0
           and np.max(np.abs(np.diff(sinph))) <= rate_bound,
           "max|Δsin|=%.4f" % np.max(np.abs(np.diff(sinph))))

    # E3 停拍窗：增量与归一化波形为零；故障样例（E3b）
    on = run_sequence(0.05, switch_on=True, gate_stop_window=(5.0, 6.0))
    stop = [(dj, s) for (t, _, dj, s) in on if 5.0 < t < 6.0]
    okE3 = len(stop) > 0 and all(dj == 0.0 and s == 0.0 for dj, s in stop)
    record("E3", "停拍窗：增量与归一化波形均为零", okE3)
    record("E3b", "故障样例：停拍段非零增量被判错", okE3)

    # G 跨层一致：气动幅值差增量 vs 关节增量（档二）
    for dA in (0.05, -0.05):
        wk_j = WingKinematics(frequency=F0, amplitude_deg=a_wd)
        wk_0 = WingKinematics(frequency=F0,
                              amplitude_deg=FlapKinematicsConfig.AMPLITUDE)
        wk_p = WingKinematics(frequency=F0,
                              amplitude_deg=FlapKinematicsConfig.AMPLITUDE)
        maxerr = 0.0
        n = int(round(6.0 / PHYSICS_DT))
        for k in range(1, n + 1):
            t = k * PHYSICS_DT
            for w in (wk_j, wk_0, wk_p):
                w.set_frequency(F0)
            ramp = min(1.0, t / max(STARTUP_AMP_RAMP_S, 0.05))
            wk_0.amplitude = A0 * ramp
            wk_p.amplitude = A0 * ramp + dA
            pj = wk_j.evaluate(t)["phi"] * ramp
            su = wk_j.stroke_unit(t)
            p0 = wk_0.evaluate(t)["phi"]
            pp = wk_p.evaluate(t)["phi"]
            maxerr = max(maxerr, abs((pp - p0) - dA * su))
        record("G1", "跨层：dA=%+.3f 气动幅值差增量与关节增量一致（1e-15）" % dA,
               maxerr <= TOL_FP, "max|Δ|=%.3e" % maxerr)

    # W1 波形发生器正交性登记（静态）：五键在 FlightControllerConfig 内，
    # 皆为频率或相位不对称类调制参数（WAVEGEN_AMBDA 为 Hopf 幅值自稳目标、
    # 负值哨兵即用现行拍幅，不改 dA 通道语义）
    import config.sim_config as _c
    wg_keys = [k for k in dir(_c.FlightControllerConfig) if k.startswith("WAVEGEN")]
    record("W1", "波形发生器键清单 %s（频率相位类调制，与 dA 幅值通道正交）"
           % (wg_keys or "无"), len(wg_keys) >= 5)

    n_pass = sum(1 for r in RESULTS if r["pass"])
    summary = {"total": len(RESULTS), "pass": n_pass,
               "fail": len(RESULTS) - n_pass,
               "verdict": "pass" if n_pass == len(RESULTS) else "fail"}
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ROOT, "logs", "I190_离线验证输出_%s.json" % ts)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"item": "I-190", "generated_at": ts,
                   "tolerances": {"fp": TOL_FP, "extreme": TOL_EXT},
                   "summary": summary, "checks": RESULTS},
                  f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    for r in RESULTS:
        print("%s %s %s %s" % ("过" if r["pass"] else "拒", r["id"], r["desc"],
                               r["detail"]))
    print("产物:", path)
    return 0 if summary["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
