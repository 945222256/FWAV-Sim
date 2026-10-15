# -*- coding: utf-8 -*-
"""
i094_roll_authority_probe.py — 滚转权限真值直接测量(满偏阶跃,钉固工况)

事项 I-094:对滚转通道施加满偏阶跃,直接测现行构型最大可用力矩,
与 I-082 账两口径对照,给出单一缺口倍数。

测量口径:
  钉固姿态与来流,直接调 TailFuselageAero.compute(与 I-024 探针同款,
  构造级可信,零 Isaac)。差动矩模型式 T_x=q·AREA·CN_SLOPE·ail·PANEL_Y
  在模型内线性无钳制;可信边界按有效攻角准则标注——差动舵偏等价于
  左右尾面攻角各偏离 ±ail,平板线性段只在偏离不超失速角(20 度=
  0.349 rad)内可信,超出即模型说谎段,不当真值。俯仰通道另有模型内
  真钳位(clip),一并测出作对照。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from config import sim_config as cfg
from modules.aero.tail_fuselage import TailFuselageAero


def quat_level():
    return [1.0, 0.0, 0.0, 0.0]


def roll_moment(v_air, f_flap, ail):
    """给定来流、拍频、差动舵偏,返回尾翼滚转矩(钉固水平姿态)。"""
    tb = TailFuselageAero()
    v = np.array([v_air, 0.0, 0.0])
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f_flap
    tb.set_command(elevator=0.0, aileron=ail, rudder=0.0)
    F, T = tb.compute(quat_level(), v, v_slip=v_slip)
    return float(T[0])


def pitch_moment(v_air, f_flap, elev):
    tb = TailFuselageAero()
    v = np.array([v_air, 0.0, 0.0])
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f_flap
    tb.set_command(elevator=elev, aileron=0.0, rudder=0.0)
    F, T = tb.compute(quat_level(), v, v_slip=v_slip)
    return float(T[1])


def main():
    print("== I-094 滚转权限真值直接测量(满偏阶跃,钉固工况) ==")
    print()
    DELTA = cfg.FlightControllerConfig.DELTA_MAX
    print("构型参数: 满偏 %.3f rad, 失速角 %.1f 度=%.4f rad, 舵面积 %.4f m²,"
          "法向力斜率 %.1f, 半展 %.2f m, 滑流 %.1f×%.3f×f m/s"
          % (DELTA, cfg.TailAeroConfig.STALL_ANGLE,
             np.radians(cfg.TailAeroConfig.STALL_ANGLE),
             cfg.TailAeroConfig.AREA, cfg.TailAeroConfig.CN_SLOPE,
             cfg.TailAeroConfig.PANEL_Y, cfg.SLIP_K, cfg.SLIP_V_PER_HZ))
    print()
    print("一、飞行包络角点满偏读数(差动 +%.3f rad):" % DELTA)
    print("  来流(m/s)  拍频(Hz)  合流(m/s)  滚转矩(N·m)")
    pts = []
    for v_air in (3.0, 3.95, 5.0):
        for f in (1.8, 2.0, 2.2):
            vs = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f
            v_eff = np.hypot(v_air + max(vs, 0.0), 0.0)
            Tx = roll_moment(v_air, f, DELTA)
            pts.append(Tx)
            print("  %.2f      %.1f      %.3f     %+.5f"
                  % (v_air, f, v_air + vs, Tx))
    print("  包络内读数范围: %.5f 至 %.5f N·m" % (min(pts), max(pts)))
    print()
    print("二、差动全程扫描(定来流 3.95 m/s 拍频 2.0 Hz):")
    print("  舵偏(rad)   滚转矩(N·m)   线性比(矩/舵偏)")
    a_stall = np.radians(cfg.TailAeroConfig.STALL_ANGLE)
    ref = roll_moment(3.95, 2.0, 0.05) / 0.05
    for ail in (0.05, 0.15, 0.285, 0.349, 0.5, 0.8, 1.2):
        Tx = roll_moment(3.95, 2.0, ail)
        mark = ""
        if ail > a_stall:
            mark = "  <- 超失速角,模型说谎段(不当真值)"
        elif ail > DELTA:
            mark = "  <- 超舵机满偏,构型外"
        print("  %.3f      %+.5f      %.4f%s"
              % (ail, Tx, Tx / ail, mark))
    print()
    print("三、可信边界:")
    print("  模型内差动通道无钳位(全程线性);按有效攻角准则,可信上界="
          "失速角 %.4f rad;现行满偏 %.3f rad 在可信段内(占失速角的 %.0f%%)"
          % (a_stall, DELTA, 100 * DELTA / a_stall))
    print("  俯仰通道模型内真钳位对照: 升降舵 0.2/0.349/0.5 rad 的俯仰矩:")
    for ev in (0.2, 0.349, 0.5):
        print("    elev=%.3f → My=%+.5f N·m" % (ev, pitch_moment(3.95, 2.0, ev)))
    print("  (0.349 之后矩不再增长=钳位生效;差动通道无此钳位,"
          "满偏读数落在模型可信段)")
    print()
    tx_typ = roll_moment(3.95, 2.0, DELTA)
    print("四、真值读数与 I-082 两口径对照:")
    print("  真值(包络中心:3.95 m/s,2.0 Hz,水平姿态): %+.5f N·m" % tx_typ)
    print("  口径A(矩阵舵效 0.0790×满偏): 0.0225 N·m, 真值/口径A=%.2f"
          % (tx_typ / 0.0225))
    print("  口径B(实测斜率 0.1666×满偏): 0.0475 N·m, 真值/口径B=%.2f"
          % (tx_typ / 0.0475))
    print()
    print("五、单一缺口倍数(十度需求对真值,需求出处 I-082 文件181222):")
    for tag, need in (("稳态保持", 0.0241), ("峰值95分位", 0.0594)):
        print("  %s: 需求 %.4f / 真值 %.5f = %.2f 倍"
              % (tag, need, abs(tx_typ), need / abs(tx_typ)))


if __name__ == "__main__":
    main()
