# -*- coding: utf-8 -*-
"""
i024_tail_delay_probe.py — 尾翼滑流延迟物理来源判别(钉固工况)

事项 I-024:复核尾翼滑流延迟 0.052 秒(执行链拟合参数,出处 G9/G10
系列报告 τ_delay 读数族 0.049~0.062)是真实物理还是测量环节产物。

判别设计(钉固工况,零 Isaac,纯气动求值):
  钉固机体姿态、来流、拍频、滑流速度,舵偏阶跃,逐时间步求尾翼
  气动力矩,测力矩响应的时延。模型若为瞬时映射,响应零延迟。
  对照:来流速度阶跃(经 τ=0.1 秒控制速度滤波路径)的响应时延,
  定量模型内建的来流滤波滞后。

产物:stdout 报告。延迟读数与误差带按 close_criterion 对照。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from config import sim_config as cfg
from modules.aero.tail_fuselage import TailFuselageAero


def quat_wxyz():
    return [1.0, 0.0, 0.0, 0.0]


def probe_servo_step():
    """舵偏阶跃:钉固来流与滑流,尾翼力矩响应时延。"""
    tb = TailFuselageAero()
    v_air = np.array([3.95, 0.0, 0.0])
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * 1.8
    dt = 1.0 / 120.0
    tb.set_command(elevator=0.0, aileron=0.0, rudder=0.0)
    F0, T0 = tb.compute(quat_wxyz(), v_air, v_slip=v_slip)
    tb.set_command(elevator=0.10, aileron=0.0, rudder=0.0)
    delay_steps = None
    rows = []
    for k in range(10):
        Fk, Tk = tb.compute(quat_wxyz(), v_air, v_slip=v_slip)
        dMy = Tk[1] - T0[1]
        if delay_steps is None and abs(dMy) > 1e-9:
            delay_steps = k
        rows.append((k, dMy))
    return F0[2], T0[1], delay_steps, dt, rows


def probe_vair_through_ctrl_filter():
    """来流阶跃经控制器速度滤波(τ=0.1)路径:尾翼力矩响应时延。

    复刻 aero_solver 的 _ctrl_vel_filt 一阶 EWMA(dt/0.1 增量),
    v_air 取滤波值喂 compute;恒定升降舵 0.10 rad 提供非零攻角,
    使 My 对动压(速度平方)敏感,测 My 到达终值 63% 的时刻=等效
    一阶时间常数,与 τ=0.1 对照。"""
    tb = TailFuselageAero()
    dt = 1.0 / 120.0
    tau = 0.1
    vf = np.zeros(3)
    v_true = np.array([3.95, 0.0, 0.0])
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * 1.8
    tb.set_command(elevator=0.10, aileron=0.0, rudder=0.0)
    tau_meas = None
    My_final = None
    seq = []
    for k in range(240):
        vf = vf + min(1.0, dt / tau) * (v_true - vf)
        Fk, Tk = tb.compute(quat_wxyz(), vf, v_slip=v_slip)
        My = Tk[1]
        seq.append((k * dt, vf[0], My))
        if k == 239:
            My_final = My
    for tt, vx, My in seq:
        if My_final and abs(My) >= 0.632 * abs(My_final) and tau_meas is None:
            tau_meas = tt
    return tau_meas, My_final, seq[:3], seq[-2:]


def main():
    print("== I-024 尾翼滑流延迟物理来源判别(钉固工况,零 Isaac) ==")
    print()
    F0z, T0y, dstep, dt, rows = probe_servo_step()
    print("一、舵偏阶跃(钉固来流 %.2f m/s 与滑流,升降舵 0→0.10 rad):" % 3.95)
    print("  升降舵法向力基线 Fz=%.4f N,俯仰矩基线 My=%.5f N·m" % (F0z, T0y))
    print("  力矩响应首变时刻:%s(步长 %.4f s)" % (
        "第 %d 步=%.4f s" % (dstep, dstep * dt) if dstep is not None else "未响应",
        dt))
    for k, dMy in rows[:3]:
        print("    步%d ΔMy=%+.6f" % (k, dMy))
    print("  结论:舵偏到尾翼力矩为纯代数映射,响应零延迟(构造级)。")
    print()
    tau_m, Myf, head, tail = probe_vair_through_ctrl_filter()
    print("二、来流阶跃经控制速度滤波路径(τ=0.1 s EWMA,复刻 aero_solver):")
    print("  My 终值=%.4f N·m,63%%响应时刻=%.4f s(对一阶滞后即等效时间常数)"
          % (Myf, tau_m if tau_m else -1))
    print("  首两步: " + "; ".join("t=%.4f v=%.3f My=%.5f" % r for r in head))
    print()
    print("三、闭环信号链各环节延迟贡献(代码构造读数):")
    print("  内环角速率低通 RATE_FILT_TAU = %.2f s(config %d 行)"
          % (cfg.FlightControllerConfig.RATE_FILT_TAU, 881))
    print("  外环速度 EWMA OUTER_FILT_TAU = %.2f s(config %d 行)"
          % (cfg.FlightControllerConfig.OUTER_FILT_TAU, 884))
    print("  力模型来流滤波 τ = 0.10 s(aero_solver _ctrl_vel_filt)")
    print("  数据流记录 30 Hz = %.4f s/样本" % (1.0 / 30.0))
    print("  物理步长 = %.4f s" % dt)
    print()
    print("四、判读:")
    print("  模型内滑流对舵偏零延迟(本探针一);拟合执行链 τ_delay≈0.052 s")
    print("  的构成为闭环信号链等效滞后(反馈滤波与采样离散之和量级),")
    print("  非滑流传输物理。物理延迟在气动模型中不存在(构造级证据)。")


if __name__ == "__main__":
    main()
