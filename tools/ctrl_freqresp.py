# -*- coding: utf-8 -*-
"""
ctrl_freqresp.py — 控制器频响数值化（2026-09-30 守门轮三十七）

轮三十六定案：解析式控制器传函无法三通道同优，控制器代码本身即
传函定义。本工具直接实例化 FlightController，喂恒定配平位姿与速
度、在目标轴叠加角速度正弦，让全部滤波与积分状态真实演化，取稳
态段投影得 C(jω)（角速度→舵偏）。采集跑当跑配置（无超前网络、
无执行低通、欧拉误差路径）与控制器构造一致。

用法：python tools/ctrl_freqresp.py
产物：logs/ident_matrix/ctrl_freqresp.json
"""
import os
import sys
import json
import math
import datetime

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from modules.flight_controller import FlightController
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

FREQS = [float(x) for x in "0.2,0.4,0.7,1.0,1.4,1.8,2.1,2.33,2.6,3.0,3.5,4.5,6.0,8.0,12.0,18.0".split(",")]
AXIS = {"ail": 3, "elev": 4, "rud": 5}     # vel 索引: wx,wy,wz
from config import sim_config as _cfg
_REF = _cfg.FlightControllerConfig
X_REF, Y_REF, Z_REF = _REF.X_REF, _REF.Y_REF, _REF.Z_REF
OUT = {"ail": "aileron", "elev": "elevator", "rud": "rudder"}
W_AMP = 0.1
DT = 1.0 / 120.0
V_TRIM = 3.95
PITCH_TRIM = math.radians(-2.4)


def quat_of(roll, pitch, yaw):
    """ZYX（yaw,pitch,roll）→四元数标准式。轮三十九解谜：旧式末行
    与次行系数错（sp 项 cy/cr 混写），纯俯仰合成三维同值小量、模
    长超一，被 _euler 解出假滚转假偏航直流，舵被顶满正弦不可见。"""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return [cy * cp * cr + sy * sp * sr,
            cy * cp * sr - sy * sp * cr,
            cy * sp * cr + sy * cp * sr,
            sy * cp * cr - cy * sp * sr]


ANG_IDX = {"ail": 0, "elev": 1}      # 欧拉分量: roll,pitch


def find_trim(ctrl):
    """自洽配平迭代（轮四十：合成配平与控制器稳态pitch_des错配,
    误差直流加积分顶满限幅,正弦不可见=elev零值根因）。"""
    trim = PITCH_TRIM
    for _ in range(8):
        pose = [X_REF, Y_REF, Z_REF] + quat_of(0.0, trim, 0.0)
        c = FlightController()
        out = None
        for i in range(int(12.0 / DT)):
            out = c.update(pose, [V_TRIM, 0.0, 0.0, 0.0, 0.0, 0.0],
                           t_sim=i * DT)
        trim -= 0.3 * (trim - out["pitch_des"])
        if abs(trim - out["pitch_des"]) < 0.002:
            break
    return trim


def measure(ch, fq, ctrl, trim):
    """单频率点：运动学一致注入（轮三十八教训：恒角速度+固姿态使
    姿态误差饱和顶满限幅，正弦被削为零）。姿态角 θ=A·sin(ωt)，
    角速度=其导数 A·ω·cos(ωt)，姿态误差不过饱和；输出投影到
    角速度输入得 C(jω)（含姿态环+速率环的真实整环）。"""
    a_ang = math.radians(9.0)          # 姿态正弦幅值（轮四十二：对准
                                      # 采集跑实际摆幅统计主量级，工作点
                                      # 含增益调度非线性时 C 与小扰动域不同）
    n_warm = int(20.0 / DT)
    per = 1.0 / fq
    n_steady = int(max(6.0 * per, 4.0) / DT)
    in_sig = []
    out_sig = []
    for i in range(n_warm + n_steady):
        t = i * DT
        om = 2 * math.pi * fq
        ang = a_ang * math.sin(om * t)
        w_dot = a_ang * om * math.cos(om * t)
        r, p = (ang, trim) if ch == "ail" else (0.0, trim + ang)
        pose = [X_REF, Y_REF, Z_REF] + quat_of(r, p, 0.0)
        vel = [V_TRIM, 0.0, 0.0, 0.0, 0.0, 0.0]
        vel[AXIS[ch]] = w_dot
        out = ctrl.update(pose, vel, t_sim=t)
        if i >= n_warm:
            in_sig.append(w_dot)
            out_sig.append(out[OUT[ch]])
    tt = np.arange(len(in_sig)) * DT
    Pj = np.sum((np.array(in_sig)) * np.exp(-1j * 2 * math.pi * fq * tt))
    Oj = np.sum((np.array(out_sig) - np.mean(out_sig))
                * np.exp(-1j * 2 * math.pi * fq * tt))
    return Oj / Pj if abs(Pj) > 1e-12 else None


def main():
    from modules.flight_controller import FlightController
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    resp = {}
    for ch in AXIS:
        ctrl = FlightController()
        trim = find_trim(FlightController())
        print("%s 自洽配平 %.2f°" % (ch, math.degrees(trim)))
        resp[ch] = {}
        for fq in FREQS:
            H = measure(ch, fq, ctrl, trim)
            if H is not None:
                resp[ch]["%.3f" % fq] = [float(abs(H)),
                                         float(np.angle(H))]
        print("%s: %s" % (ch, {k: "%.3f∠%+.0f°"
                               % (v[0], math.degrees(v[1]))
                               for k, v in resp[ch].items()}))
    path = os.path.join(PROJECT_ROOT, "logs", "ident_matrix",
                        "ctrl_freqresp.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(ts=ts, resp=resp, w_amp=W_AMP, dt=DT), f,
                  ensure_ascii=False, indent=1)
    print("产物:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
