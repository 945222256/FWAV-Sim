# -*- coding: utf-8 -*-
"""
hss_i172_safe.py — 合成上反角系数安全范围计算，I-172 第一件前置。

侧滑反馈方案：尾翼差动指令注入正比于侧滑角速度与侧滑角组合的扶正项，
实现为速率通道增益（对照 I-102 理想内环 k_wx 路径）与角度通道增益
（经典上反角的 Cl_beta 路径）。安全范围三约束：

一、权限约束：增益乘典型信号幅值不得超过尾翼差动权限（满偏零点三弧
度折算力矩零点零四六牛米每弧度差动角乘 AIL_AUTH），给侧滑角反馈的
每弧度度数上限。

二、模态效应约束：增益在 I-102 八态线性化上叠加（速率项进力矩方程
滚转行，角度项经运动学），要求不把俯仰耦合模态推不稳（I-108 实测
k_wy 路径强烈恶化禁用的教训），给符号与量级双约束。

三、闭环带宽约束：反馈不得与既有滚转速率环（ROLL_DAMP 链）相位冲突，
侧滑通道频率远低于滚转速率环穿越频率为安全侧。

产出写 ltv/i172_results 时间戳目录。全程零仿真器调用。
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
DELTA_MAX = 0.30
B_DA = 0.046          # N·m/rad 差动舵效（三维实测巡航姿态）
AIL_AUTH = 1.0
BETA_TYP_DEG = 10.0   # 十度协调转弯的侧滑角量级（I-084/I-102 配平读数）
WX_TYP = 1.0          # 滚转速率信号典型幅值 rad/s（I-100 死法观测）


def open_loop_A(x_trim, wz, roll_deg=BANK, k_beta=0.0, k_wx=0.0, eps=1e-5):
    """在 I-102 八态线性化上叠加合成上反角两项。

    k_beta：侧滑角反馈，滚转力矩增量为 -k_beta×beta（beta=侧滑角弧
    度，由侧滑速度 vy 与前向速度 u 之比近似），经运动学 beta≈vy/u。
    k_wx：侧滑速率路径对照（即 I-102 的理想差动内环 k_wx，力矩增量
    -k_wx×wx）。
    """
    u0, vy0, wb0, p0, e0, a0, r0 = x_trim
    f0 = 1.8
    roll0 = math.radians(roll_deg)

    def deriv(s):
        u, vy, wb, roll, pitch, wx, wy, wzz = s
        quat = s1.quat_from_rpy(roll, pitch, 0.0)
        R = s1.rot_body_from_world(quat)
        v_b = np.array([u, vy, wb])
        w_v = np.array([wx, wy, wzz])
        cmd = [e0, a0, r0]
        if k_wx != 0.0:
            cmd[1] = cmd[1] + k_wx * wx
        F, T = s1.WRENCH(R.T @ v_b, quat, w_v, f0, tail_cmd=tuple(cmd))
        F_b = R @ F
        g_b = R @ np.array([0.0, 0.0, -s1.G])
        vdot = F_b / s1.MASS + g_b - np.cross(w_v, v_b)
        T = T.copy()
        if k_beta != 0.0:
            beta = vy / max(u, 1.0)
            # 合成上反角：尾差动注入与侧滑角同号的扶正矩（经舵效正映
            # 射，与真实上反角 Cl_beta 同号路径）
            cmd_eff = a0 + k_beta * beta
            F5, T5 = s1.WRENCH(R.T @ v_b, quat, w_v, f0,
                               tail_cmd=(e0, cmd_eff, r0))
            T = T5.copy()
        wIw = np.cross(w_v, s1.I_DIAG * w_v)
        wdot = (T - wIw) / s1.I_DIAG
        return np.array([vdot[0], vdot[1], vdot[2], wx, wy,
                         wdot[0], wdot[1], wdot[2]])

    s0v = np.array([u0, vy0, wb0, roll0, p0, 0.0, 0.0, wz])
    A = np.zeros((8, 8))
    for j in range(8):
        sp, sm = s0v.copy(), s0v.copy()
        sp[j] += eps
        sm[j] -= eps
        A[:, j] = (deriv(sp) - deriv(sm)) / (2 * eps)
    return A


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    x, wz = s1.plant_trim(BANK)
    u0 = x[0]
    A0 = open_loop_A(x, wz)
    w0 = sorted(np.linalg.eigvals(A0), key=lambda v: -v.real)
    re_base = float(w0[0].real)
    print("十度配平基准：主模态 Re=%+.4f 次 %+.4f" % (
        w0[0].real, w0[1].real))
    beta_rad = math.radians(BETA_TYP_DEG)
    # 约束一：权限。k_beta（N·m/rad of beta）乘典型侧滑角 ≤ 满偏力矩
    mx_full = B_DA * DELTA_MAX * AIL_AUTH
    k_beta_auth_max = mx_full / beta_rad
    k_beta_frac_safe = 0.5 * k_beta_auth_max   # 半权限安全线
    print("权限约束：满偏力矩 %.4f N·m，侧滑 %.0f 度下 k_beta 上限 "
          "%.4f N·m/rad，半权限安全线 %.4f" % (
              mx_full, BETA_TYP_DEG, k_beta_auth_max, k_beta_frac_safe))
    # 速率通道（k_wx 为 N·m/(rad/s)）：典型速率下权限上限
    k_wx_auth_max = mx_full / WX_TYP
    print("速率通道：典型速率 %.1f rad/s 下 k_wx 上限 %.4f N·m/(rad/s)"
          % (WX_TYP, k_wx_auth_max))
    # 约束二：模态效应扫描（符号与量级）
    rows = []
    for tag, kw in (("k_beta 正馈 0.05", ("beta", 0.05)),
                    ("k_beta 正馈 0.10", ("beta", 0.10)),
                    ("k_beta 正馈 0.20", ("beta", 0.20)),
                    ("k_beta 正馈 0.40", ("beta", 0.40)),
                    ("k_beta 正馈 0.60", ("beta", 0.60)),
                    ("k_beta 反号 0.10", ("beta", -0.10)),
                    ("k_wx 负馈 0.10", ("wx", -0.10)),
                    ("k_wx 负馈 0.20", ("wx", -0.20)),
                    ("k_wx 负馈 0.40", ("wx", -0.40)),
                    ("k_wx 负馈 0.60", ("wx", -0.60)),
                    ("组合 0.20+负0.10", ("both", 0.20))):
        mode, val = kw
        kb = val if mode in ("beta", "both") else 0.0
        kwv = -val if mode == "wx" else (-0.10 if mode == "both" else 0.0)
        kbv = kb if mode != "wx" else 0.0
        if mode == "both":
            kbv, kwv = 0.20, -0.10
        A = open_loop_A(x, wz, k_beta=kbv, k_wx=kwv)
        w = sorted(np.linalg.eigvals(A), key=lambda v: -v.real)
        rows.append({"tag": tag, "k_beta": kbv, "k_wx": kwv,
                     "re_main": float(w[0].real),
                     "re_second": float(w[1].real),
                     "re_third": float(w[2].real),
                     "n_unstable": int(sum(1 for v in w if v.real > 0))})
        print("%-18s → 主 %+.4f 次 %+.4f 三 %+.4f 不稳 %d" % (
            tag, w[0].real, w[1].real, w[2].real,
            sum(1 for v in w if v.real > 0)))
    out = {"item": "I-172", "stage": "safe_range", "spec_sha256": spec_sha,
           "re_base": re_base,
           "authority": {"mx_full_nm": mx_full,
                         "k_beta_auth_max": k_beta_auth_max,
                         "k_beta_frac_safe": k_beta_frac_safe,
                         "k_wx_auth_max": k_wx_auth_max},
           "sweep": rows, "new_sim_runs": 0}
    out_dir = os.path.join(_HERE, "i172_results",
                           time.strftime("i172safe_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print("产出目录：%s" % out_dir)
    return out


if __name__ == "__main__":
    main()
