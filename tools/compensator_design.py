# -*- coding: utf-8 -*-
"""
compensator_design.py — 按矩阵元素定量计算补偿器参数（I-008 前置，I-006 配套）

依据 docs/辨识产线_判据与方案_20260930.md 第七节的处置条款：读矩阵特征值，
若模态阻尼为负则按矩阵元素定量设计补偿器（含滑流延迟状态的相位整形），
补偿后特征值实部转正为中间验收线。本工具把暂定矩阵（跳变三窗组，校准检验
满分版本）的执行链参数转成各通道在模态频率处的相位缺口与超前网络处方，
并给出补偿前闭环根基线读数（单通道增广：三态机体+二阶执行链+延迟帕德一阶）。

用法：python tools/compensator_design.py logs/ident_matrix/I006hop_20260930_153942/matrix.json
输出：各通道相位账、建议参数、补偿前闭环根（只打印不落盘，部署属裁决后动作）。
"""
import json
import math
import sys

import numpy as np


def phase_account(fn_hz, zeta, tau_d, f_mode):
    """一通道在模态频率处的执行链相位账（度，滞后为负）。"""
    w = 2 * np.pi * f_mode
    wn = 2 * np.pi * fn_hz
    re = wn ** 2 - w ** 2
    im = 2 * zeta * wn * w
    ph2 = -np.degrees(np.arctan2(im, re))
    phd = -np.degrees(w * tau_d)
    return ph2, phd, ph2 + phd


def lead_needed(deficit_deg, per_stage=29.0, max_stages=6):
    n = int(math.ceil(deficit_deg / per_stage))
    return min(n, max_stages) if n > 0 else 0


def closed_loop_roots(A, Bcol, fn, zt, tau_d, gain):
    """补偿前单通道闭环根：状态 [w(3); x; v; p](二阶执行链 x/v +
    一阶帕德延迟 p)。反馈 u = -gain·wx（当前生效内环量级），
    延迟以 (1-τs/2)/(1+τs/2) 近似进入 u_path。"""
    wn = 2 * np.pi * fn
    a1, a0 = 2 * zt * wn, wn ** 2
    tau = max(tau_d, 1e-3)
    n = 6
    M = np.zeros((n, n))
    M[0:3, 0:3] = A
    M[0:3, 3] = Bcol            # x 进入机体
    M[3, 4] = 1.0               # x' = v
    M[4, 3] = -a0               # v' = -a0·x -a1·v + u_delayed
    M[4, 4] = -a1
    # u = -gain·wx 经帕德延迟: u ≈ -gain·( wx_p ), wx_p 一阶态:
    # wx_p' = (2/τ)(wx·(-1)-... 简化: 延迟态 p 满足 p'=(2/τ)(-gain·wx - p)
    M[4, 5] = 1.0               # 延迟态输出进 v
    M[5, 0] = (2.0 / tau) * (-gain)
    M[5, 5] = -2.0 / tau
    return np.linalg.eigvals(M)


def main(path):
    d = json.load(open(path, encoding="utf-8"))
    f_mode = 2.33
    chs = ["ail", "elev", "rud"]
    gains = {"ail": 0.088 * 0.45, "elev": 0.088 * 0.474, "rud": 0.088 * 0.9}
    A = np.array(d["A"])
    B = np.array(d["B"])
    print("== 矩阵补偿器处方（模态频率 %.2fHz）==" % f_mode)
    print("通道 | fn | ζ | 延迟 | 二阶相位 | 延迟相位 | 合计 | 需超前 | 级数")
    for k, ch in enumerate(chs):
        fn = d["resonance_fn_hz"][k]
        zt = d["resonance_zeta"][k]
        td = d["tau_delay"][k]
        ph2, phd, tot = phase_account(fn, zt, td, f_mode)
        nst = lead_needed(-tot)
        print("%s | %.2f | %.2f | %.3f | %.0f | %.0f | %.0f | %.0f | %d"
              % (ch, fn, zt, td, ph2, phd, tot, -tot, nst))
    eig = np.linalg.eigvals(A)
    print("\n== 对象侧特征值 ==")
    for lam in eig:
        wn = abs(lam)
        z = -lam.real / wn if wn > 1e-9 else float("nan")
        print("λ = %+.3f %+.3fj  f=%.2fHz  ζ=%.3f"
              % (lam.real, lam.imag, wn / (2 * np.pi), z))
    print("\n== 补偿前闭环根基线（三态+执行链+延迟，当前内环量级）==")
    worst = -1e9
    for k, ch in enumerate(chs):
        fn = max(d["resonance_fn_hz"][k], 0.5)
        zt = max(d["resonance_zeta"][k], 0.05)
        td = d["tau_delay"][k]
        roots = closed_loop_roots(A, B[:, k], fn, zt, td, gains[ch])
        mx = max(roots.real)
        worst = max(worst, mx)
        ring = [r for r in roots
                if 1.5 < abs(r.imag) / (2 * np.pi) < 4.5]
        desc = "; ".join("%.2fHz ζ=%.2f" % (abs(r) / (2 * np.pi),
                                            -r.real / abs(r)) for r in ring)
        print("%s: 最劣实部 %+.3f  模态带根: %s" % (ch, mx, desc or "无"))
    print("\n全通道最劣闭环实部 %+.3f（判据第七节中间验收线=补偿后转正）"
          % worst)
    print("注: 部署属裁决后动作;处方由矩阵元素直算,禁盲扫。")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "logs/ident_matrix/I006hop_20260930_153942/matrix.json")
