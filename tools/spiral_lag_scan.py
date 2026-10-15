# -*- coding: utf-8 -*-
"""
spiral_lag_scan.py — 带执行链滞后与反馈滤波的盘旋螺旋稳定扫描（I-008 G6 前置）

背景：G2~G5 四轮实测（提交 98fc820）——新惯量盘旋入环后滚转持续偏置加深、
偏航率超跑协调值、反偏滚转矩随坡度增长（斜率与 L_β 同量级），全崩于
8~9s；四态准定常模型（lateral_coord_stability.py）无滞后全域稳定，解释
不了。本工具把两条被四态压缩成代数的动态显式建态：
  其一，角速度反馈一阶滤波（RATE_FILT_TAU，wx/wz 各一态）；
  其二，方向舵执行链一阶滞后+传输延迟合并为力矩域一阶态（τ_r 默认取
  G3 窗辨识值 τ_act 0.15 + τ_delay 0.17 的等效 0.22s；界内值，饱和窗
  数据偏粗，可 --tau-rud 覆盖）。
控制律按现行结构接入：m_z 指令=KP_R·(−KB·β−YAW_DAMP·wz_f)，滚转
ail 指令走 wx_f 阻尼与 KP_A 姿态态。扫描 KB×YAW_DAMP×KP_R 给最劣
特征值实部表，产出 G6 协调增益处方。

用法：python tools/spiral_lag_scan.py [--tau-rud 0.22] [--kp-r 0.088]
"""
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "params_registry"))
sys.path.insert(0, ROOT)
import registry  # noqa: E402
from config import sim_config as cfg  # noqa: E402

MASS, G = 0.215, 9.81
_COMP = registry.get("vehicle.inertia.composite.body_frame")
IXX, IZZ = float(_COMP[0]), float(_COMP[2])
V = 3.95
L_BETA, MZ_BETA, Y_BETA = 0.0368, -0.272, 0.515
MX_WX, MX_WZ = -0.0024, -0.0233
MZ_WX, MZ_WZ = 0.0037, -0.0076
B_AIL = 0.00483 * V * V
W = MASS * G
TAU_F = cfg.FlightControllerConfig.RATE_FILT_TAU
KP_A = cfg.FlightControllerConfig.KP_A


def closed_loop(kb, yaw_damp, roll_damp, kp_r, tau_rud):
    """七态 [β,wx,wz,φ,wx_f,wz_f,m_z] 闭环矩阵（偏差线性化）。"""
    A = np.zeros((7, 7))
    chi_b = Y_BETA / (MASS * V)
    chi_p = W / (MASS * V)
    # β̇ = wz − χ̇ ; χ̇=(Y_β·β + W·φ)/(mV)
    A[0] = [-chi_b, 0, 1.0, -chi_p, 0, 0, 0]
    # wẋ：ail 力矩=KP_R·(−KP_A·φ − ROLL_DAMP·wx_f)（B_AIL 消去）
    A[1, 0] = L_BETA / IXX
    A[1, 1] = MX_WX / IXX
    A[1, 2] = MX_WZ / IXX
    A[1, 3] = -kp_r * KP_A / IXX
    A[1, 4] = -kp_r * roll_damp / IXX
    # wż：方向舵力矩走执行态 m_z
    A[2, 0] = MZ_BETA / IZZ
    A[2, 1] = MZ_WX / IZZ
    A[2, 2] = MZ_WZ / IZZ
    A[2, 6] = 1.0 / IZZ
    # φ̇ = wx
    A[3, 1] = 1.0
    # 滤波态
    A[4, 1] = 1.0 / TAU_F
    A[4, 4] = -1.0 / TAU_F
    A[5, 2] = 1.0 / TAU_F
    A[5, 5] = -1.0 / TAU_F
    # m_z 指令 = KP_R·(−KB·β − YAW_DAMP·wz_f)，经一阶执行态
    A[6, 0] = -kp_r * kb / tau_rud
    A[6, 5] = -kp_r * yaw_damp / tau_rud
    A[6, 6] = -1.0 / tau_rud
    return A


def worst(A):
    return float(max(np.linalg.eigvals(A).real))


def main():
    tau_rud = 0.22
    kp_r = 0.088
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--tau-rud":
            tau_rud = float(args[i + 1]); i += 2
        elif args[i] == "--kp-r":
            kp_r = float(args[i + 1]); i += 2
        else:
            i += 1
    roll_damp = 0.45
    print("七态模型：τ_f=%.3fs τ_rud=%.2fs KP_R=%.3f KP_A=%.2f RD=%.2f"
          % (TAU_F, tau_rud, kp_r, KP_A, roll_damp))
    print("I=(%.3e, %.3e)（登记库现行）\n" % (IXX, IZZ))
    print("KB × YAW_DAMP 网格，表值=最劣特征值实部（>0 螺旋不稳）：")
    kbs = [0.0, 1.0, 1.62, 3.0, 5.0, 8.0, 12.0]
    yds = [0.5, 1.0, 1.53, 3.0, 5.0, 8.0]
    print("KB\\YD " + "".join("%9.2f" % y for y in yds))
    for kb in kbs:
        row = "%5.2f" % kb
        for yd in yds:
            row += "%+9.3f" % worst(closed_loop(kb, yd, roll_damp,
                                                kp_r, tau_rud))
        print(row)
    print()
    # 最稳点
    best = None
    for kb in np.arange(0.0, 12.01, 0.25):
        for yd in np.arange(0.25, 10.01, 0.25):
            w = worst(closed_loop(kb, yd, roll_damp, kp_r, tau_rud))
            if best is None or w < best[0]:
                best = (w, kb, yd)
    print("粗扫最稳点：最劣实部 %+.3f @ KB=%.2f YAW_DAMP=%.2f" % best)
    # 滞后敏感性：τ_rud 0 / 0.1 / 0.22 / 0.35 于 G3 组合
    print("\nτ_rud 敏感性（KB=1.62 YAW_DAMP=1.53）：")
    for tr in (0.001, 0.10, 0.22, 0.35, 0.50):
        print("  τ_rud=%.2f → 最劣实部 %+.3f"
              % (tr, worst(closed_loop(1.62, 1.53, roll_damp, kp_r, tr))))


if __name__ == "__main__":
    main()
