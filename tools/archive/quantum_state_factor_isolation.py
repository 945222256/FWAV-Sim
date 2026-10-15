# -*- coding: utf-8 -*-
"""
quantum_state_factor_isolation.py — 在环权限塌缩的因子隔离（2026-09-28）

任务来源：《量子异步抽验_口径调和.md》路径三与《权限包线_侧滑坡度扫描》
之间的矛盾：包线表在坡度 −10°、侧滑 −55° 格点给出密度通道权限
+0.088 牛米（方向正确且强），而科目一日志 t=8s 状态的回放钉固测量
只给出 +0.0041 牛米。两处坡度与侧滑相近，差异只能来自回放状态里
包线表没有固定的变量：俯仰角、角速度、竖直速度、拍频。本工具在
t=8s 状态上逐一清零这些因子，定位是哪一个吃掉了权限。

判定口径（先于数据写定于本文件头）：若某因子清零后权限恢复到
包线值的一半以上（+0.044 牛米，自定），判该因子为主因；若全部
单因子清零都恢复不到一半，判为多因子联合或包线表口径差异。

用法： python tools/quantum_state_factor_isolation.py
产物： logs/quantum_state_factor_isolation.json + 控制台表
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
import pandas as pd
from config import sim_config as cfg
from modules.aero_solver import euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

N_PER_BEAT = 288
N_BEATS = 4
CSV = os.path.join(cfg.LOG_DIR, "ident_stream_flight_0927_213932.csv")
T_QUERY = 8.0
THETA_TRIM = -13.03

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(cfg.FlapKinematicsConfig.ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)
    w.amplitude = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)

TIMEBASE = [500000.0]


def stop_right_authority(quat, vel, om, f):
    """钉固状态下停右翼的滚转权限（停拍态−全拍态的 Tx 均值差）。"""
    model.set_wing_command(frequency=f)
    out = {}
    for gate in (1.0, 0.0):
        model.wing_right.stroke_gate = gate
        Tq = np.zeros(3)
        n = 0
        T = N_BEATS / f
        n_s = N_PER_BEAT * N_BEATS
        ts = [TIMEBASE[0] + i * T / n_s for i in range(n_s)]
        TIMEBASE[0] += T
        for t in ts:
            model.compute_forces(np.zeros(3), quat, vel, om, t)
            Tq += model.current_T_body
            n += 1
        out[gate] = Tq / n
    model.wing_right.stroke_gate = 1.0
    return float(out[0.0][0] - out[1.0][0])


def quat_with_pitch(quat, pitch_deg):
    """保留滚转与偏航，替换俯仰角。"""
    w, x, y, z = quat
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return euler_to_quat(roll, math.radians(pitch_deg), yaw)


def main():
    df = pd.read_csv(CSV)
    i = int(np.argmin(np.abs(df["t"].values - T_QUERY)))
    r = df.iloc[i]
    quat0 = np.array([r["qw"], r["qx"], r["qy"], r["qz"]])
    vel0 = np.array([r["vx"], r["vy"], r["vz"]])
    om0 = np.array([r["wx"], r["wy"], r["wz"]])
    f0 = float(r["freq"])
    print(f"[iso] t={float(r['t']):.2f}s 状态：vel={np.round(vel0,2)} "
          f"om={np.round(om0,2)} f={f0:.2f}", flush=True)

    cases = [
        ("原始状态（回放基线）", quat0, vel0, om0, f0),
        ("角速度清零", quat0, vel0, np.zeros(3), f0),
        ("竖直速度清零", quat0, vel0 * np.array([1, 1, 0]), om0, f0),
        ("俯仰换回配平 −13.03°", quat_with_pitch(quat0, THETA_TRIM),
         vel0, om0, f0),
        ("拍频换回 1.80", quat0, vel0, om0, 1.80),
        ("全部清理（应对齐包线值）", quat_with_pitch(quat0, THETA_TRIM),
         vel0 * np.array([1, 1, 0]), np.zeros(3), 1.80),
    ]
    rows = []
    for name, q, v, o, ff in cases:
        a = stop_right_authority(q, v, o, ff)
        rows.append({"case": name, "authority_Tx": a})
        print(f"[iso] {name}：停右翼权限 {a:+.5f} N·m", flush=True)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(cfg.LOG_DIR,
                           "quantum_state_factor_isolation.json"),
              "w", encoding="utf-8") as fp:
        json.dump({"ts": ts, "t_query": T_QUERY,
                   "state": {"vel": vel0.tolist(), "om": om0.tolist(),
                             "freq": f0},
                   "half_envelope_ref": 0.044, "rows": rows},
                  fp, ensure_ascii=False, indent=1)
    print("[iso] 完成 → logs/quantum_state_factor_isolation.json",
          flush=True)


if __name__ == "__main__":
    main()
