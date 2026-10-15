# -*- coding: utf-8 -*-
"""kimi_linearize_trim.py — 配平点闭环线性化与模态分析（第十一班，离散映射版）。

方法：把闭环系统（控制器全部内部状态 + 物理状态）视为离散映射
x_{k+1} = F(x_k)，步长 1/120 s，在巡航配平不动点附近用有限差分求
雅可比矩阵，再求特征值。特征值模大于 1 为不稳定，模小于 1 为收敛；
振荡频率 = 特征值辐角 / 步长。

增广状态（11 维）：
  [z, vx, vz, pitch, wy, freq_last, ei, pitch_i, pd_last, pv_filt_z, w_filt_y]
"""
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config.sim_config as cfg
from modules.aero_solver import FlapperAeroModel, TailFuselageAero
from modules.flight_controller import FlightController

cfg.WIND_ENABLED = False
A0 = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
IYY = 7.4e-3
MASS = cfg.MASS
G = 9.81
DT = 1.0 / 120.0
model = FlapperAeroModel()
tail = TailFuselageAero()

FCC = cfg.FlightControllerConfig


def wrench(vx, vz, pitch, wy, freq, n=24):
    model.set_wing_command(frequency=freq)
    model.wing_left.amplitude = A0
    model.wing_right.amplitude = A0
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    quat = np.array([cp, 0.0, sp, 0.0])
    v_lin = np.array([vx, 0.0, vz])
    v_ang = np.array([0.0, wy, 0.0])
    Fs = np.zeros(3)
    Ts = np.zeros(3)
    for i in range(n):
        F, T = model.compute_forces(np.zeros(3), quat, v_lin, v_ang,
                                    i / (n * max(freq, 0.5)))
        Fs += F
        Ts += T
    Fm, Tm = Fs / n, Ts / n
    tn = float(np.linalg.norm(Tm))
    if tn > 2.0:
        Tm *= 2.0 / tn
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * freq
    Ft, Tt = tail.compute(quat, v_lin, v_slip=v_slip)
    return Fm + Ft, Tm + Tt


USE_VZ_CHANNEL = {"on": False, "K_EB_V": 0.0, "K_VZ_P": 0.0}


def step_map(s):
    """一步闭环映射：s → s_next（控制器状态写回增广向量）。"""
    z, vx, vz, pitch, wy, freq_last, ei, pitch_i, pd_last, pvz, wfy = s
    ctl = FlightController()
    ctl._freq_last = freq_last
    ctl._ei = ei
    ctl._pitch_i = pitch_i
    ctl._pd_last = pd_last
    ctl._pv_filt = np.array([vx, 0.0, pvz])
    ctl._w_filt = np.array([0.0, wfy, 0.0])
    ctl._t_last = -DT          # 令内部 dt = DT
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    pose = [0.0, 0.0, z, cp, 0.0, sp, 0.0]
    vel = [vx, 0.0, vz, 0.0, wy, 0.0]
    out = ctl.update(pose, vel, t_sim=0.0)
    # 舵面指令必须接入被控对象（第五班修复线性化遗漏：此前舵偏恒零，
    # 俯仰路径对模型不可见，极点对所有增益不变是模型假象）
    tail.set_command(elevator=out["elevator"], aileron=out["aileron"],
                     rudder=out["rudder"])
    F, T = wrench(vx, vz, pitch, wy, out["freq"])
    vx2 = vx + (F[0] / MASS) * DT
    vz2 = vz + (F[2] / MASS - G) * DT
    wy2 = float(np.clip(wy + (T[1] / IYY) * DT, -3.0, 3.0))
    pitch2 = pitch + wy2 * DT
    z2 = z + vz2 * DT
    return np.array([z2, vx2, vz2, pitch2, wy2, out["freq"],
                     ctl._ei, ctl._pitch_i, ctl._pd_last,
                     ctl._pv_filt[2], ctl._w_filt[1]])


def find_fixed_point(s0, n=6000):
    s = s0.copy()
    for _ in range(n):
        s = step_map(s)
    return s


def jacobian(f, x0, eps=1e-6):
    n = len(x0)
    J = np.zeros((n, n))
    f0 = f(x0)
    for j in range(n):
        dx = np.zeros(n)
        dx[j] = eps
        J[:, j] = (f(x0 + dx) - f0) / eps
    return J


def analyze(tag, **gain_overrides):
    saved = {}
    for k, v in gain_overrides.items():
        saved[k] = getattr(FCC, k)
        setattr(FCC, k, v)
    # 在试验架实测收敛点（z≈3.77、vx≈3.96、pitch≈−0.04、f≈1.15，
    # 见 harness PASS 输出）直接线性化，不做长程漂移（长漂会进入锁高吸引子）
    s = np.array([3.77, 3.96, 0.0, -0.04, 0.0, 1.15, 0.0, 0.0, -0.04, 0.0, 0.0])
    J = jacobian(step_map, s)
    w, _V = np.linalg.eig(J)
    for k, v in saved.items():
        setattr(FCC, k, v)
    print(f"\n== {tag} ==  不动点 z={s[0]:.3f} vx={s[1]:.3f} pitch={s[3]:.3f} f={s[5]:.2f}")
    rows = []
    for lam in w:
        mod = abs(lam)
        frq = abs(np.angle(lam)) / DT
        rows.append((mod, lam, frq))
    rows.sort(key=lambda r: -r[0])
    for mod, lam, frq in rows:
        mark = "不稳定!" if mod > 1.0 else ("弱阻尼" if mod > 0.97 else "")
        print(f"  |λ|={mod:7.4f}  λ={lam.real:+8.4f}{lam.imag:+8.4f}j  "
              f"振荡频率 {frq:6.2f} rad/s {mark}")
    return w


def main():
    # 结构更换后的垂直速度通道极点配置扫描
    for kebv, kzp, kzi in ((0.05, 0.10, 0.02), (0.05, 0.15, 0.02),
                           (0.10, 0.10, 0.02), (0.10, 0.15, 0.03),
                           (0.10, 0.20, 0.03), (0.15, 0.15, 0.03),
                           (0.10, 0.15, 0.06), (0.20, 0.15, 0.03)):
        analyze(f"e_B→vz: K_EB_V={kebv} K_VZ_P={kzp} K_VZ_I={kzi}",
                TECS_EB_TO_VZ=True, TECS_EB_V_GAIN=kebv,
                TECS_VZ_P=kzp, TECS_VZ_I=kzi)


if __name__ == "__main__":
    main()
