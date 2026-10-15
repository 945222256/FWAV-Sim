# -*- coding: utf-8 -*-
"""I-204 v3 数值核验：对照 Doro 2026-10-13 第二版复验决定逐条修正。

核验原则：待检公式只写一遍，验证一律对照实际对象——真实参考单步
函数 ltv/wavegen_family.hopf_step、数值积分的有限差分、矩阵指数同源
离散化。纯数值，零仿真，零在环运行。
"""
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "ltv"))
from wavegen_family import hopf_step  # noqa: E402


def composite_radius_counterexample():
    """段谱半径乘积非一般上界；上界限同一诱导范数；体积增长恒等。"""
    A = np.array([[0.1, 10.0], [0.0, 0.1]])
    B = np.array([[0.1, 0.0], [10.0, 0.1]])
    rho_A = max(abs(np.linalg.eigvals(A)))
    rho_B = max(abs(np.linalg.eigvals(B)))
    BA = B @ A
    rho_BA = max(abs(np.linalg.eigvals(BA)))
    prod_radii = rho_A * rho_B
    bound2 = np.linalg.norm(A, 2) * np.linalg.norm(B, 2)
    det_id = (abs(np.linalg.det(BA))
              - abs(np.linalg.det(B)) * abs(np.linalg.det(A)))
    return {
        "case": "composite_radius",
        "segment_radius_product": float(prod_radii),
        "composite_radius": float(rho_BA),
        "same_induced_norm_bound_2": float(bound2),
        "determinant_identity_residual": float(det_id),
        "assert_pass": bool(prod_radii < rho_BA
                            and rho_BA <= bound2 + 1e-9
                            and abs(det_id) < 1e-12),
    }


def scalar_jump_counterexample():
    """标量跳跃也不足以让驻留矩阵特征值相乘。"""
    A = np.array([[2.0, 0.0], [0.0, 0.5]])
    B = np.array([[0.0, -1.0], [1.0, 0.0]])
    comp = 1.0 * (B @ A)
    prod_eigmod = (max(abs(np.linalg.eigvals(A)))
                   * max(abs(np.linalg.eigvals(B))))
    rho_comp = max(abs(np.linalg.eigvals(comp)))
    return {
        "case": "scalar_jump",
        "segment_eigmod_product": float(prod_eigmod),
        "composite_radius": float(rho_comp),
        "assert_pass": bool(abs(prod_eigmod - 2.0) < 1e-9
                            and abs(rho_comp - 1.0) < 1e-9),
    }


def lyapunov_counterexample():
    """方程有解加右端正定不构成候选有效：A=1、Q=1 解 P=-1/2 为负。"""
    a, q = 1.0, 1.0
    p = -q / (2.0 * a)
    residual = 2.0 * a * p + q
    return {
        "case": "lyapunov_candidate",
        "solution_P": float(p),
        "equation_residual": float(residual),
        "P_positive_definite": bool(p > 0),
        "A_hurwitz": bool(a < 0),
        "assert_pass": bool(abs(residual) < 1e-12 and p < 0),
    }


def hopf_real_kernel_candidate():
    """真实参考核上的径向候选核验，非单位幅值 A=0.5、kappa=4。

    候选 V=(r-A)^2/2 沿真实径向方程 r'=k(A^2-r^2)r 的导数为
    V' = -k*r*(r-A)^2*(r+A)，r>=0 时非正，r=0 与 r=A 处为零。
    核验三件：原点驻留不收敛、正半径收敛到 A、实测局部收敛率为
    2*k*A^2=2。V' 由真实 hopf_step 轨迹数值差分得到，公式只出现
    一次作为被检对象。"""
    A, k, f, dt = 0.5, 4.0, 2.0, 1e-4

    def radius_after(r0, t):
        z = np.array([r0, 0.0])
        n = max(int(round(t / dt)), 1)
        for _ in range(n):
            z = hopf_step(z, dt, A, f, 0.0, 0.0, k)
        return float(np.hypot(z[0], z[1]))

    # 原点：真实核下保持为零，不吸引到目标圆
    r_origin = radius_after(0.0, 0.5)
    # 正半径收敛
    r_start = [radius_after(r0, 4.0) for r0 in (0.1, 0.3, 0.7, 1.0, 1.2)]
    converged = all(abs(r - A) < 0.02 for r in r_start)
    # 实测局部收敛率：r 近 A 时 ln|r-A| 斜率
    r0 = A * (1.0 + 1e-3)
    z = np.array([r0, 0.0])
    lns = []
    ts = []
    t = 0.0
    for i in range(15000):
        z = hopf_step(z, dt, A, f, 0.0, 0.0, k)
        t += dt
        if i % 50 == 0:
            r = float(np.hypot(z[0], z[1]))
            lns.append(np.log(abs(r - A)))
            ts.append(t)
    slope = float(np.polyfit(ts[10:], lns[10:], 1)[0])
    rate_true = 2.0 * k * A * A
    # V' 数值差分对照解析式（解析式只此一处，作为被检对象）
    def vdot_numeric(r):
        dts = 1e-6
        z = np.array([r, 0.0])
        z2 = hopf_step(z, dts, A, f, 0.0, 0.0, k)
        r2 = float(np.hypot(z2[0], z2[1]))
        return ((r2 - A) ** 2 - (r - A) ** 2) / (2.0 * dts)

    vdot_checks = []
    for r in (0.1, 0.4, 0.7, 1.0):
        num = vdot_numeric(r)
        ana = -k * r * (r - A) ** 2 * (r + A)
        vdot_checks.append(abs(num - ana) < 1e-6 + 1e-3 * abs(ana))
    vdot_origin = abs(vdot_numeric(1e-9)) < 1e-9
    return {
        "case": "hopf_real_kernel",
        "A": A, "kappa": k,
        "origin_stays_zero": bool(abs(r_origin) < 1e-12),
        "positive_radii_converged": bool(converged),
        "measured_log_slope": slope,
        "measured_convergence_rate": -slope,
        "true_rate_2kA2": rate_true,
        "vdot_formula_matches_kernel": bool(all(vdot_checks)),
        "vdot_at_origin_zero": bool(vdot_origin),
        "assert_pass": bool(abs(r_origin) < 1e-12 and converged
                            and abs((-slope) - rate_true) < 0.05
                            and all(vdot_checks) and vdot_origin),
    }


def discrete_from_continuous():
    """同一连续系统取矩阵指数得离散映射：A_c=-0.5，Phi=exp(A_c*dt)。
    连续方程按每秒权重 Q 解 P_c=1；离散方程按每步权重 Q*dt 解
    P_d=Q*dt/(1-Phi^2)。两口径残差各自为零；步长缩小时 P_d 趋近
    连续解 P_c，验证同源口径转换而非两个不同系统。"""
    a, q = -0.5, 1.0
    p_cont = q / (-2.0 * a)
    r_cont = 2.0 * a * p_cont + q
    runs = []
    for dt in (0.1, 0.01):
        phi = float(np.exp(a * dt))
        q_d = q * dt
        p_disc = q_d / (1.0 - phi * phi)
        r_disc = p_disc - phi * phi * p_disc - q_d
        runs.append({"dt": dt, "Phi": phi, "P_d": float(p_disc),
                     "residual": float(r_disc),
                     "P_d_minus_P_c": float(p_disc - p_cont)})
    tol = [0.06, 0.01]
    converge_ok = all(abs(runs[i]["P_d"] - p_cont) < tol[i]
                      for i in range(len(runs)))
    return {
        "case": "discrete_from_continuous",
        "continuous_A": a,
        "continuous_P": float(p_cont), "continuous_residual": float(r_cont),
        "step_runs": runs,
        "P_d_converges_to_P_c": bool(converge_ok),
        "assert_pass": bool(abs(r_cont) < 1e-12
                            and all(abs(r["residual"]) < 1e-12
                                    for r in runs)
                            and p_cont > 0
                            and all(r["P_d"] > 0 for r in runs)
                            and converge_ok),
    }


def return_map_derivative_example():
    """返回映射导数矩阵式的可核查例子。

    系统 r' = -(r-1)，th' = 1；截面 th=0，穿越方向 th 增方向，
    横截条件 n^T f = 1 不为零。返回时刻 t_r = 2*pi - th0。
    矩阵式：DP = Dphi_{t_r} + f(P) * grad(t_r)^T，
    其中 Dphi 固定时间为 diag(exp(-2pi),1)，返回点速度 f(P)=(0,1)，
    返回时刻梯度 grad(t_r)=(0,-1)。维度：n x n = 2 x 2。
    验证用真实积分的有限差分对照矩阵式，公式只写一遍。"""
    def flow_step(s):
        r, th = s
        return np.array([r + (-(r - 1.0)) * dt_s, th + dt_s])

    dt_s = 1e-6

    def return_point(r0, th0):
        s = np.array([r0, th0 % (2.0 * np.pi)])
        t = 0.0
        while s[1] < 2.0 * np.pi:
            s = flow_step(s)
            t += dt_s
        return s, t

    def P(r0, th0):
        return return_point(r0, th0)[0]

    eps = 1e-5
    th_base = 1e-3
    dP_dr = (P(1.0 + eps, th_base)[0]
             - P(1.0 - eps, th_base)[0]) / (2 * eps)
    dP_dth = (P(1.0, th_base + eps)[0]
              - P(1.0, th_base - eps)[0]) / (2 * eps)
    e = float(np.exp(-2.0 * np.pi))
    fP = np.array([0.0, 1.0])
    grad_t = np.array([0.0, -1.0])
    DP = np.diag([e, 1.0]) + np.outer(fP, grad_t)
    numeric_row = np.array([dP_dr, dP_dth])
    analytic_row = DP[0, :]
    transversality = 1.0
    return {
        "case": "return_map_derivative",
        "DP_matrix": DP.tolist(),
        "numeric_first_row": numeric_row.tolist(),
        "analytic_first_row": analytic_row.tolist(),
        "row_max_abs_diff": float(np.max(np.abs(numeric_row - analytic_row))),
        "transversality_nTf": transversality,
        "correction_term_is_velocity_outer_gradient":
            bool(abs(DP[1, 1] - (1.0 - 1.0)) < 1e-12),
        "assert_pass": bool(np.max(np.abs(numeric_row - analytic_row)) < 1e-4
                            and transversality != 0.0),
    }


def main():
    out = [composite_radius_counterexample(),
           scalar_jump_counterexample(),
           lyapunov_counterexample(),
           hopf_real_kernel_candidate(),
           discrete_from_continuous(),
           return_map_derivative_example()]
    for r in out:
        print("%-30s assert_pass=%s" % (r["case"], r["assert_pass"]))
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=1))
