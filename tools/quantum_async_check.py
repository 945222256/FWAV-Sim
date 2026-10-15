# -*- coding: utf-8 -*-
"""
quantum_async_check.py — 量子异步抽验：调和三条权限口径（2026-09-27 夜）

任务来源：《冲量积分线_阶段二首日.md》第四节墙级发现——单翼停拍的
滚转权限有三个互相矛盾的数字：
  口径一（孤立翼量子表）：+0.0355 N·m·s/拍，折合周期平均 0.064 N·m；
  口径二（首日离线整机标定）：右停左拍 +0.00742 N·m；
  口径三（在环实测）：δρ 饱和时翼侧滚转力矩约 +0.01 N·m。
口径一对口径二高估 8.6 倍。本工具在完全相同的巡航条件下用三条
测量路径复算，定位差异落在哪一段：

  路径一：量子表原路径——单翼 _wing_forces 直算，扑动对停拍。
  路径二：整机 compute_forces 路径——双翼全拍对右翼闸门停拍，
          钉固配平姿态、零角速度。若路径二等于路径一，则差异不在
          测量路径，而在首日标定工况或在环状态反馈；若路径二显著
          小于路径一，则差异在路径结构。
  路径三：在环状态回放——取科目一日志 t=6/8/10 秒的实测状态
          （姿态、速度、角速度、拍频）钉固，同测双翼全拍对右翼停拍，
          检验在环工况本身是否吃掉权限。

测量纪律：时间轴全局单调推进、显式设频、每拍 288 点。
模型与波形：sane_dickinson_3d，全部参数读 config（现行生效口径）。

用法： python tools/quantum_async_check.py
产物： logs/quantum_async_check.json + 控制台对照表
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
from modules.aero_solver import euler_to_quat, quat_to_rotation_matrix
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

F0 = 1.80
V0 = 3.95
THETA0 = -13.03          # 巡航配平俯仰（重测工具实测，前向力根口径）
N_PER_BEAT = 288
N_BEATS = 4
IXX_TRUE = 7.4e-3        # 215g 惯量真值（harness 口径）
IXX_APPROX = 2.4e-3      # 量子表折算用的近似惯量

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(cfg.FlapKinematicsConfig.ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)
    w.amplitude = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)

TIMEBASE = [300000.0]


def window(f0=F0, n_beats=N_BEATS):
    T = n_beats / f0
    n = N_PER_BEAT * n_beats
    ts = [TIMEBASE[0] + i * T / n for i in range(n)]
    TIMEBASE[0] += T
    return ts


def path1_quantum_isolated(V, th, f):
    """路径一：量子表原口径，左翼扑动对停拍，_wing_forces 直算。"""
    model.set_wing_command(frequency=f)
    quat = euler_to_quat(0.0, math.radians(th), 0.0)
    v_body = quat_to_rotation_matrix(quat).T @ np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    w, rc = model.wing_left, +model._r_centers.copy()
    T = 1.0 / f
    dt = T / N_PER_BEAT
    dT = np.zeros(3)
    dF = np.zeros(3)
    for paused in (False, True):
        for t in window(f, 1):
            ws = w.evaluate(t, phase_offset_rad=0.0)
            if paused:
                ws = dict(ws)
                ws["phi"] = float(w.bias + w.mean)
                ws["phi_dot"] = ws["phi_ddot"] = 0.0
                ws["alpha"] = float(w.alpha_set * 2.0 / math.pi
                                    + w.pitch_bias)
                ws["alpha_dot"] = ws["alpha_ddot"] = 0.0
            Fw, Tw = model._wing_forces(rc, model._chords, ws, v_body, om)
            sgn = -1.0 if paused else 1.0
            dF += sgn * Fw * dt
            dT += sgn * Tw * dt
    return dF, dT          # 每拍冲量（扑动−停拍）


def path2_whole_aircraft(V, th, f):
    """路径二：整机 compute_forces，双翼全拍对右翼闸门停拍。

    钉固姿态、零角速度；取机体系力矩均值差（周期平均口径）。
    """
    model.set_wing_command(frequency=f)
    quat = euler_to_quat(0.0, math.radians(th), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    out = {}
    for gate_R in (1.0, 0.0):
        model.wing_right.stroke_gate = gate_R
        Tq = np.zeros(3)
        Fq = np.zeros(3)
        n = 0
        for t in window(f, N_BEATS):
            model.compute_forces(np.zeros(3), quat, vel, om, t)
            Tq += model.current_T_body
            Fq += model.current_F_body
            n += 1
        out[gate_R] = (Fq / n, Tq / n)
    model.wing_right.stroke_gate = 1.0
    dF = out[1.0][0] - out[0.0][0]     # 全拍 − 右停
    dT = out[1.0][1] - out[0.0][1]
    return dF, dT


def path3_inloop_replay(csv_path, t_list):
    """路径三：在环状态回放钉固测量。"""
    import pandas as pd
    df = pd.read_csv(csv_path)
    rows = []
    for tq in t_list:
        i = int(np.argmin(np.abs(df["t"].values - tq)))
        r = df.iloc[i]
        quat = np.array([r["qw"], r["qx"], r["qy"], r["qz"]])
        vel = np.array([r["vx"], r["vy"], r["vz"]])
        om = np.array([r["wx"], r["wy"], r["wz"]])
        f = float(r["freq"])
        model.set_wing_command(frequency=f)
        res = {}
        for gate_R in (1.0, 0.0):
            model.wing_right.stroke_gate = gate_R
            Tq = np.zeros(3)
            Fq = np.zeros(3)
            n = 0
            for t in window(f, N_BEATS):
                model.compute_forces(np.zeros(3), quat, vel, om, t)
                Tq += model.current_T_body
                Fq += model.current_F_body
                n += 1
            res[gate_R] = (Fq / n, Tq / n)
        model.wing_right.stroke_gate = 1.0
        dT = res[1.0][1] - res[0.0][1]
        dF = res[1.0][0] - res[0.0][0]
        rows.append({"t_query": tq, "t_actual": float(r["t"]),
                     "freq": f, "dTx": float(dT[0]), "dFz": float(dF[2]),
                     "roll_deg": float(math.degrees(math.atan2(
                         2 * (quat[0] * quat[1] + quat[2] * quat[3]),
                         1 - 2 * (quat[1] ** 2 + quat[2] ** 2)))),
                     "vy": float(r["vy"]), "wx": float(r["wx"])})
    return rows


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "condition": {"V": V0, "f": F0, "theta": THETA0},
           "ixx_true": IXX_TRUE, "ixx_approx": IXX_APPROX}

    dF1, dT1 = path1_quantum_isolated(V0, THETA0, F0)
    m1 = float(dT1[0]) * F0          # 周期平均力矩 = 每拍冲量 × 拍频
    out["path1"] = {"dJ_Tx_per_beat": float(dT1[0]),
                    "mean_Tx": m1}
    print(f"[路径一·孤立翼量子表口径] 每拍 ΔJ_Tx={float(dT1[0]):+.5f}"
          f" N·m·s，折合周期平均 {m1:+.5f} N·m", flush=True)

    dF2, dT2 = path2_whole_aircraft(V0, THETA0, F0)
    m2 = float(dT2[0])
    out["path2"] = {"mean_Tx_full_minus_rightstop": m2,
                    "dFz": float(dF2[2])}
    print(f"[路径二·整机钉固口径] 全拍−右停 平均 Tx={m2:+.5f} N·m"
          f"（右翼停拍效应= {-m2:+.5f}）", flush=True)

    csv = os.path.join(cfg.LOG_DIR, "ident_stream_flight_0927_213932.csv")
    rows = path3_inloop_replay(csv, [6.0, 8.0, 10.0])
    out["path3"] = rows
    for r in rows:
        print(f"[路径三·在环回放 t={r['t_actual']:.2f}s] roll="
              f"{r['roll_deg']:+.1f}° vy={r['vy']:+.2f} f={r['freq']:.2f}"
              f"：全拍−右停 平均 Tx={r['dTx']:+.5f} N·m", flush=True)

    # 折算对照：三种惯量与口径下的每拍角速度增量
    conv = {}
    for name, m in (("路径一", m1), ("路径二", m2)):
        dJ = m / F0
        conv[name] = {"per_beat_dps_ixx_approx":
                      math.degrees(dJ / IXX_APPROX),
                      "per_beat_dps_ixx_true":
                      math.degrees(dJ / IXX_TRUE)}
        print(f"[折算] {name}：每拍角速度增量 "
              f"{conv[name]['per_beat_dps_ixx_approx']:+.0f}°/s"
              f"（惯量2.4e-3口径）对 "
              f"{conv[name]['per_beat_dps_ixx_true']:+.0f}°/s"
              f"（惯量7.4e-3真值口径）；发散增速参照 86°/s/拍", flush=True)
    out["conversion"] = conv
    out["reference"] = {"day1_whole_aircraft": +0.00742,
                        "inloop_observed": 0.01,
                        "divergence_dps": 86.0}
    with open(os.path.join(cfg.LOG_DIR, "quantum_async_check.json"),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print("[check] 完成 → logs/quantum_async_check.json", flush=True)


if __name__ == "__main__":
    main()
