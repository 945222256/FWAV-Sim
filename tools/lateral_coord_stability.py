# -*- coding: utf-8 -*-
"""协调盘旋横侧 MIMO 稳定性分析（2026-09-29 转弯线 R20 前置）。

背景：R14~R19 五轮在环实录的统一形态——协调段（t=5.5~9.5s）建立后
在 9.5~10.5s 起荷兰滚型耦合振荡爆发。67 班 latsim 框架（零激励平飞）
不含侧滑-滚转梯度 L_β 与垂尾恢复 MZ_β 两项，其"rd=0.30 全档稳定"
结论在盘旋工况失效。本脚本在协调盘旋配平点线性化横侧五态
（β、wx、wz、φ、χ），用实测梯度（docs/侧滑工况横侧通道效能.md 与
URDF 惯量 ixx=0.012/izz=0.018）算闭环特征值，扫参数域选稳定点。

模型（小扰动，坐标约定与在环判读一致）：
  β = yaw − χ（机头-轨迹差，机头超前转弯方向为正）
  β̇ = wz − χ̇
  χ̇ = (L·sinφ0·φ̃/φ0·… 简化为 a⊥/V：a⊥=(Y_β·β + W·tanφ0·φ̃)/m …
  按配平线性化：χ̇_lin = (Y_β·β + (W/cos²φ0)·φ̃·…) 近似 (Y_β·β + W·φ̃)/m
  Mx = L_β·β + B_AIL·ail + MX_WX·wx + MX_WZ·wz   （L_β=+0.0368 不稳方向）
  Mz = MZ_β·β + B_RUD·rud + MZ_WX·wx + MZ_WZ·wz  （MZ_β=−0.272 恢复）
  φ̇ = wx
  wẋ = Mx/IXX，wż = Mz/IZZ
控制律（转弯线现行）：
  mx = KP_R·(KP_A·(φ_des−φ) − ROLL_DAMP·wx)
  ail = mx/B_AIL_eff
  wzd = FF·tan(φ_des)·g/V（常值） + KB·(−β)   （β=yaw−χ，误差=χ−yaw=−β）
  mz = KP_R·(wzd − YAW_DAMP·wz)
  rud = mz/B_RUD
参数默认取 config 当前生效值；扫 KB×YAW_DAMP×ROLL_DAMP 网格输出
最劣特征值实部与荷兰滚模态阻尼比。
"""
import math
import sys

import numpy as np

# ── 物理参数（实测出处见模块 docstring）──
MASS, G = 0.215, 9.81
# IXX/IZZ 改读登记库现行整机合成值（I-002 几何法新纪量 2026-09-30）；
# 气动梯度为力矩导数不随惯量纪量变化，仍用旧实测值。旧分析用的
# 0.012/0.018 为 base_link 旧声明对角，已退役入 params_registry/archive。
import os as _os
sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "params_registry"))
import registry as _registry
_COMP = _registry.get("vehicle.inertia.composite.body_frame")
IXX, IZZ = float(_COMP[0]), float(_COMP[2])
V = 3.95                          # m/s 巡航速度
PHI0 = math.radians(10.0)         # 盘旋坡度配平
L_BETA = 0.0368                   # N·m/rad 侧滑-滚转梯度（不稳方向）
MZ_BETA = -0.272                  # N·m/rad 垂尾偏航恢复
Y_BETA = 0.515                    # N/rad 侧滑侧力恢复
MX_WX, MX_WZ = -0.0024, -0.0233   # 滚转阻尼与耦合（67 班矩阵）
MZ_WX, MZ_WZ = 0.0037, -0.0076    # 偏航耦合与阻尼
B_AIL = 0.00483 * V * V           # 尾翼差动效能
B_RUD = -0.0145 * V * V           # 方向舵效能（负号）
KP_A, KP_R = 2.6, 0.25            # 姿态外环/角速度内环（config 现值）


def closed_loop_A(kb=1.2, yaw_damp=2.5, roll_damp=0.45, phi_des=PHI0):
    """四态 [β, wx, wz, φ] 闭环系统矩阵。

    χ 已并入 β 动力学（β=yaw−χ 的微分消去积分态）；控制律含 β 反馈
    （KB）与 φ 反馈（滚转环），执行器动力学压成代数（尾翼响应快于
    横侧模态）。R20 初版五态含 χ 时产生零特征值（与 β 冗余）。
    """
    W = MASS * G
    # 状态导数装配（线性项前的系数）
    # β̇ = wz − χ̇ ; χ̇ = (Y_BETA·β + W·φ̃)/(m·V)  (φ̃=φ−φ0 线性化侧力)
    # wẋ = (L_BETA·β + MX_WX·wx + MX_WZ·wz + B_AIL·ail)/IXX
    # wż = (MZ_BETA·β + MZ_WX·wx + MZ_WZ·wz + B_RUD·rud)/IZZ
    # φ̇ = wx ; χ̇ 见上
    # 控制量代换：
    #   ail = KP_R·(KP_A·(φ_des−φ) − ROLL_DAMP·wx)/B_AIL
    #   rud = KP_R·(wzd0 + KB·(−β) − YAW_DAMP·wz)/B_RUD  （wzd0 常值不入 A）
    A = np.zeros((4, 4))
    chi_dot_beta = Y_BETA / (MASS * V)
    chi_dot_phi = W / (MASS * V)
    # β 行
    A[0, 0] = -chi_dot_beta
    A[0, 2] = 1.0
    A[0, 3] = -chi_dot_phi
    # wx 行：ail 代换后 φ 与 wx 系数
    k_ail = KP_R / B_AIL
    A[1, 0] = L_BETA / IXX
    A[1, 1] = (MX_WX - k_ail * roll_damp) / IXX
    A[1, 2] = MX_WZ / IXX
    A[1, 3] = (-k_ail * KP_A) / IXX
    # wz 行：rud=mz/B_RUD 且力矩 B_RUD·rud=mz（B_RUD 消去，负号不
    # 双重计入——R20 初版曾在此把 +57 1/s 的代数假不稳算出来）
    # mz = KP_R·(FF0 − KB·β − YAW_DAMP·wz)，FF0 常值不入 A
    A[2, 0] = (MZ_BETA - KP_R * kb) / IZZ
    A[2, 1] = MZ_WX / IZZ
    A[2, 2] = (MZ_WZ - KP_R * yaw_damp) / IZZ
    A[2, 3] = 0.0
    # φ 行
    A[3, 1] = 1.0
    return A


def analyze(kb, yaw_damp, roll_damp):
    A = closed_loop_A(kb, yaw_damp, roll_damp)
    ev = np.linalg.eigvals(A)
    lam = max(ev.real)
    # 荷兰滚模态=频率最高的振荡对
    osc = [e for e in ev if abs(e.imag) > 1e-6]
    dr_ratio = None
    if osc:
        dr = max(osc, key=lambda e: abs(e.imag))
        dr_ratio = -dr.real / abs(dr)
    return lam, dr_ratio


def main():
    print("参数默认点（kb=1.2 yaw=2.5 roll=0.45）：")
    lam, dr = analyze(1.2, 2.5, 0.45)
    print(f"  最劣特征值实部 = {lam:+.4f} 1/s（>0 不稳）"
          f"  荷兰滚阻尼比 = {dr if dr is None else round(dr, 3)}")
    print()
    print("KB × YAW_DAMP 网格（ROLL_DAMP=0.45），表值=最劣实部：")
    kbs = [0.0, 0.6, 1.2, 2.0, 3.0]
    yaws = [1.0, 2.5, 4.0, 6.0, 8.0]
    hdr = "KB\\YD " + "".join(f"{y:>8.1f}" for y in yaws)
    print(hdr)
    for kb in kbs:
        row = f"{kb:>5.1f}"
        for yd in yaws:
            lam, _ = analyze(kb, yd, 0.45)
            row += f"{lam:>+8.3f}"
        print(row)
    print()
    print("ROLL_DAMP × YAW_DAMP 网格（KB=1.2），表值=最劣实部：")
    rds = [0.2, 0.45, 0.8, 1.2]
    print("RD\\YD " + "".join(f"{y:>8.1f}" for y in yaws))
    for rd in rds:
        row = f"{rd:>5.2f}"
        for yd in yaws:
            lam, _ = analyze(1.2, yd, rd)
            row += f"{lam:>+8.3f}"
        print(row)
    print()
    print("搜索全稳定点（最劣实部<0 且荷兰滚阻尼比最大）：")
    best = None
    for kb in np.arange(0.0, 5.01, 0.25):
        for yd in np.arange(0.5, 10.01, 0.5):
            for rd in np.arange(0.1, 1.61, 0.1):
                lam, dr = analyze(kb, yd, rd)
                if lam < 0 and dr is not None:
                    score = dr
                    if best is None or score > best[0]:
                        best = (score, kb, yd, rd, lam)
    if best:
        print(f"  最优：阻尼比={best[0]:.3f} KB={best[1]:.2f} "
              f"YAW_DAMP={best[2]:.1f} ROLL_DAMP={best[3]:.2f} "
              f"最劣实部={best[4]:+.4f}")
    else:
        print("  网格内无全稳定点——模型内需要结构变更（侧力面/前馈解耦）")


if __name__ == "__main__":
    sys.exit(main())
