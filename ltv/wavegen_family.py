# -*- coding: utf-8 -*-
"""
wavegen_family.py — 相位域波形发生器参考实现（I-139 预登记配套）
================================================================
本文件是 I-139 预登记文书 ltv/wavegen_prereg_20261009.md 的可执行形式，
纯数值实现，零 Isaac 依赖。数学结构以预登记文书为准，本文件逐条对应。

总架构：发生器 = 相位核（恒周期调频）复合 输出映射（形状函数）。

五个自由度 p = (mu, A, f, s, psi)：
  mu  中置偏置：拍动行程中心相对机体的安装偏角（现行 MEAN 与 BIAS 之和）
  A   振幅：拍动角单侧峰值（现行 AMPLITUDE）
  f   频率：拍频赫兹（现行 FREQUENCY），双翼共用量
  s   上下行程不对称：正半行程占周期时间比（现行 SPLIT），s 小于一半
      表示正半行程快
  psi 相位漂移：翼面俯仰相对拍动的相位偏移（现行 PITCH_LAG_S 的相位域
      形式，psi = 2*pi*f*PITCH_LAG_S 在定频标定点换算），判据文中
      “中置漂移”与本自由度同指

相位核（恒周期调频）：
  du/dt = f / H(u)，u = theta/(2*pi) 为周期分数
  H(u) = 1 + b1*sin(2*pi*u) + b3*sin(6*pi*u)
  归一化条件 mean(H) = 1 保证任意系数下周期恒等于 1/f
  占空比 s = 1/2 + (b1 + b3/3)/pi
  正值性条件 min_u H(u) >= margin 是可达域边界（margin 取 0.1）

Hopf 基底（二维状态实现）：
  dx = kappa*(A^2 - r^2)*x - (2*pi*f/H)*y
  dy = kappa*(A^2 - r^2)*y + (2*pi*f/H)*x
  r = sqrt(x^2+y^2)，相位角 theta = atan2(y, x)
  径向收缩率 2*kappa*A^2，从任意正初值单调收敛无过冲
  相位动力学与半径无关，幅值瞬态期间周期精确性仍然成立
  状态空间为去掉原点的平面，原点为排斥平衡，初值规则 r0 > 0

输出映射（在环上的形式）：
  phi(t)   = mu + A*sin(theta(t))
  alpha(t) = a_amp*cos(theta(t) - psi) + a_bias
  俯仰幅值 a_amp 与偏置 a_bias 为通道常数，不属于五自由度

禁止事项（结构性，代码内不存在的分支即本条的证据）：
  无停拍闸门、无逐拍二值调制、无双翼差频。I-125 的停拍路线不在本族内。
"""
from __future__ import annotations

import numpy as np

# ---- 冻结常数（预登记文书第五节） ----
H_MARGIN = 0.10          # 正值性安全边际，min_u H >= 0.10
B3_RATIO = 0.2 / 1.035   # 扩展区固定比值 b3/b1（可达域顶点方向）
C1_LIMIT = 1.0 - H_MARGIN  # 纯一阶区上限 |c| <= 0.9
# 固定比值下的可达上限 |c|max（margin 0.1 网格扫描值，见预登记文书）
C_EXT_MAX = 1.1017


def duty_c(s: float) -> float:
    """占空比到调频组合量的映射：c = pi*(s - 1/2)。"""
    return np.pi * (float(s) - 0.5)


def c_to_b(c: float):
    """调频组合量 c = b1 + b3/3 到系数的冻结规则。

    纯一阶区 |c| <= C1_LIMIT：b3 = 0，b1 = c。
    扩展区 C1_LIMIT < |c| <= C_EXT_MAX：固定比值方向 (1, B3_RATIO)，
    b1 = c/(1 + B3_RATIO/3)，b3 = B3_RATIO*b1。
    超出 C_EXT_MAX 抛错（可达域边界，见预登记文书第五节）。
    """
    c = float(c)
    if abs(c) <= C1_LIMIT:
        return c, 0.0
    if abs(c) > C_EXT_MAX + 1e-12:
        raise ValueError(
            "占空比超出恒周期光滑族可达域：|c|=%.4f 上限 %.4f（margin=%.2f）"
            % (abs(c), C_EXT_MAX, H_MARGIN))
    b1 = c / (1.0 + B3_RATIO / 3.0)
    return b1, B3_RATIO * b1


def H_profile(u: np.ndarray, b1: float, b3: float) -> np.ndarray:
    """慢度剖面 H(u)，周期一，均值一。"""
    u = np.asarray(u, dtype=float)
    return 1.0 + b1 * np.sin(2.0 * np.pi * u) + b3 * np.sin(6.0 * np.pi * u)


def min_H(b1: float, b3: float, n: int = 20001) -> float:
    """慢度剖面在周期上的最小值（正值性边界检查）。"""
    return float(H_profile(np.linspace(0.0, 1.0, n), b1, b3).min())


def phase_rate(u, f: float, b1: float, b3: float):
    """相位核右端：du/dt = f/H(u)。"""
    return float(f) / H_profile(u, b1, b3)


def duty_from_b(b1: float, b3: float) -> float:
    """系数到占空比的解析式（与数值积分互验用）。"""
    return 0.5 + (b1 + b3 / 3.0) / np.pi


def time_map(u_grid: np.ndarray, f: float, b1: float, b3: float) -> np.ndarray:
    """相位分数到时间的闭式积分 t(u) = (1/f)*∫H du。

    H 的原函数闭式：F(u) = u - (b1/(2*pi))*(cos(2*pi*u) - 1)
                           - (b3/(6*pi))*(cos(6*pi*u) - 1)。
    周期精确性：t(1) - t(0) = 1/f 精确成立。
    """
    u = np.asarray(u_grid, dtype=float)
    F = u - (b1 / (2.0 * np.pi)) * (np.cos(2.0 * np.pi * u) - 1.0) \
        - (b3 / (6.0 * np.pi)) * (np.cos(6.0 * np.pi * u) - 1.0)
    return F / float(f)


def integrate_phase(T: float, f: float, b1: float, b3: float,
                    n_steps: int = 200000) -> float:
    """相位核定步长四阶龙格库塔积分，返回 u(T)。用于周期精确性验收。"""
    h = float(T) / n_steps
    u = 0.0

    def rhs(x):
        return f / (1.0 + b1 * np.sin(2.0 * np.pi * x) + b3 * np.sin(6.0 * np.pi * x))

    for _ in range(n_steps):
        k1 = rhs(u)
        k2 = rhs(u + 0.5 * h * k1)
        k3 = rhs(u + 0.5 * h * k2)
        k4 = rhs(u + h * k3)
        u = u + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
    return u


def hopf_step(state, dt: float, A: float, f: float, b1: float, b3: float,
              kappa: float):
    """Hopf 基底单步四阶龙格库塔。state = (x, y)。"""
    def rhs(z):
        x, y = z
        r2 = x * x + y * y
        th = np.arctan2(y, x)
        w = 2.0 * np.pi * f / H_profile(th / (2.0 * np.pi), b1, b3)
        rad = kappa * (A * A - r2)
        return np.array([rad * x - w * y, rad * y + w * x])

    z = np.asarray(state, dtype=float)
    k1 = rhs(z)
    k2 = rhs(z + 0.5 * dt * k1)
    k3 = rhs(z + 0.5 * dt * k2)
    k4 = rhs(z + dt * k3)
    return z + dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0


def hopf_simulate(A: float, f: float, b1: float, b3: float, kappa: float,
                  r0: float, T: float, n_steps: int = 20000):
    """Hopf 基底整周期仿真。初值 (x,y) = (r0, 0) 即相位零点。

    返回 (轨迹数组, 半径数组, 相位数组)。"""
    z = np.array([float(r0), 0.0])
    dt = float(T) / n_steps
    traj = np.empty((n_steps + 1, 2))
    rr = np.empty(n_steps + 1)
    th = np.empty(n_steps + 1)
    traj[0] = z
    rr[0] = np.hypot(z[0], z[1])
    th[0] = np.arctan2(z[1], z[0])
    for i in range(n_steps):
        z = hopf_step(z, dt, A, f, b1, b3, kappa)
        traj[i + 1] = z
        rr[i + 1] = np.hypot(z[0], z[1])
        th[i + 1] = np.arctan2(z[1], z[0])
    return traj, rr, th


def outputs_on_cycle(theta: np.ndarray, p, a_amp: float, a_bias: float):
    """在环输出映射及其对时间导数（解析式）。

    p = (mu, A, f, s)；导数按链式法则闭式给出，用于光滑性与指令链。
    """
    mu, A, f, s = p
    b1, b3 = c_to_b(duty_c(s))
    u = theta / (2.0 * np.pi)
    H = H_profile(u, b1, b3)
    thdot = 2.0 * np.pi * f / H
    phi = mu + A * np.sin(theta)
    phi_dot = A * thdot * np.cos(theta)
    dHdu = 2.0 * np.pi * b1 * np.cos(2.0 * np.pi * u) \
        + 6.0 * np.pi * b3 * np.cos(6.0 * np.pi * u)
    dHdth = dHdu / (2.0 * np.pi)
    th_ddot = -2.0 * np.pi * f * dHdth * thdot / H ** 2
    phi_ddot = A * (th_ddot * np.cos(theta) - thdot ** 2 * np.sin(theta))
    return {"phi": phi, "phi_dot": phi_dot, "phi_ddot": phi_ddot,
            "alpha": a_amp * np.cos(theta) + a_bias,
            "alpha_dot": -a_amp * thdot * np.sin(theta)}


def harmonic_projection(Qfun, b1: float, b3: float, kmax: int,
                        n_quad: int = 288) -> dict:
    """乘子接口的谐波投影算子（预登记文书第八节）。

    对相位周期量 Q(theta)（周期 2*pi）给出时间傅里叶系数：
      c_k = 2*∫_0^1 Q(2*pi*u) * cos(2*pi*k*F(u)) * H(u) du
      s_k = 2*∫_0^1 Q(2*pi*u) * sin(2*pi*k*F(u)) * H(u) du
    其中 F(u) = ∫_0^u H du' 为相位分数到周期分数的时间映射，用 H 原函数
    闭式（见 time_map）保证精度。终值精度按项目测量纪律取 n = 288
    （粗扫 72）。返回 {"c": [...], "s": [...]}，下标 k=1..kmax。
    """
    u = np.linspace(0.0, 1.0, n_quad + 1)
    H = H_profile(u, b1, b3)
    F = u - (b1 / (2.0 * np.pi)) * (np.cos(2.0 * np.pi * u) - 1.0) \
        - (b3 / (6.0 * np.pi)) * (np.cos(6.0 * np.pi * u) - 1.0)
    Q = Qfun(2.0 * np.pi * u[:-1])
    Hw = H[:-1]
    out_c, out_s = [], []
    for k in range(1, kmax + 1):
        ang = 2.0 * np.pi * k * F[:-1]
        out_c.append(2.0 * float(np.sum(Q * np.cos(ang) * Hw) / n_quad))
        out_s.append(2.0 * float(np.sum(Q * np.sin(ang) * Hw) / n_quad))
    return {"c": out_c, "s": out_s}


# ---- 执行链（冻结结构：二阶共振串联纯延迟，频域只看幅值） ----

def chain_gain(wn: float, zeta: float, w: float) -> float:
    """舵机链幅频响应 |G(jw)|，延迟项幅值恒一不影响。"""
    return wn ** 2 / np.sqrt((wn ** 2 - w ** 2) ** 2 + (2.0 * zeta * wn * w) ** 2)


def chain_gain_table(f: float, wn: float, zeta: float, kmax: int = 6):
    """第 k 拍频谐波幅值通过率表 R_k = |G(j*2*pi*k*f)|。"""
    return [float(chain_gain(wn, zeta, 2.0 * np.pi * k * f))
            for k in range(1, kmax + 1)]


def delivered_harmonics(amp_k: list, R_k: list):
    """指令谐波幅值经链后的送达谐波幅值。"""
    return [a * r for a, r in zip(amp_k, R_k)]
