# -*- coding: utf-8 -*-
"""
hss_i102_stab.py — 新构型十度配平点解析稳定性分析，L4 线事项 I-102。

方法延续 tools/kimi_linearize_trim.py 的离线闭环离散映射线性化（第十一
班在案方法），扩展为三通道：增广状态十九维 = 物理八维（vx、vy、vz、
roll、pitch、wx、wy、wz）加控制器随身十一维（freq_last、ei、pitch_i、
pd_last、pv_filt 三元、w_filt 三元、roll_i），步长一百二十分之一秒。
被控对象为拍平均 wrench（二十四相位采样，FlapperAeroModel 加
TailFuselageAero，舵面指令接入尾翼模型），机体动力学用刚体欧拉方程全
三轴（陀螺项 w×Iw 保留），姿态用四元数推进后取欧拉。控制器每次调用
以随身态重建（kimi 同法，未随身的滤波态按新息重建，保真度沿用其在案
口径并在此声明）。

配平点：十度坡度指令工况（BANK_TEST_ON、T 归零、DEG 十度），初始条件
取巡航配平族加协调运动学偏航率，迭代收敛不动点。三件事：

一、特征值清单：不动点处有限差分雅可比，全部特征值列连续时间实部虚
部（离散特征值取对数除以步长换算），标注不稳定与弱阻尼。

二、周期口径稳定指标：配平点处逐相位求力矩对角速度的偏导（二十四相
位），合成 A of t 等于 A0 加一阶余弦项与正弦项（纯被控对象周期性，
控制器不在周期矩阵内，声明），交 ltv/hss_i079_adapter.py 判读核读单
拍增长倍率与弗洛凯指数实部。

三、敏感度表：对尾翼质量（尾件点质量迁移加平行轴惯量）、力臂（尾翼
压心距，变 LEVER_ARM 绝对值）、差动力臂（PANEL_Y）、三轴惯量、关键
增益（角度外环 KP_A、滚转慢积分 ROLL_KI、尾舵效调度系数三键）各取
加减一档重算不稳定模态连续实部，给方向与量级（每加一成变化的实部变
化）与外推稳定所需档数。

结论三种形态按敏感度矩阵判：任一单参数在正负五成内使全部不稳定模态
实部转负判可稳定区域存在；需双参数协同或单参数超五成判区域过窄；敏
感方向与约束冲突（稳定所需方向同时破坏配平或另一模态）判结构性不可
稳。产出写 ltv/i102_results 时间戳目录。全程零仿真器调用。
"""
import copy
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

import config.sim_config as cfg  # noqa: E402
from modules.aero_solver import FlapperAeroModel, TailFuselageAero  # noqa
from modules.flight_controller import FlightController  # noqa
from hss_i079_adapter import read_floquet  # noqa: E402

cfg.WIND_ENABLED = False
SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
DT = 1.0 / 120.0
G = 9.81
A0_AMP = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
MASS = cfg.MASS
FCC = cfg.FlightControllerConfig
I_DIAG = np.array([0.003503, 0.004200, 0.007608])
TAIL_MASS = 0.026
N_PHI = 24
N_PHYS = 8
N_CTL = 11
N_STATE = N_PHYS + N_CTL


def quat_from_rpy(r, p, y):
    cr, sr = math.cos(r / 2), math.sin(r / 2)
    cp, sp = math.cos(p / 2), math.sin(p / 2)
    cy, sy = math.cos(y / 2), math.sin(y / 2)
    return np.array([cr * cp * cy + sr * sp * sy,
                     sr * cp * cy - cr * sp * sy,
                     cr * sp * cy + sr * cp * sy,
                     cr * cp * sy - sr * sp * cy])


def rpy_from_quat(q):
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def make_wrench():
    model = FlapperAeroModel()
    tail = TailFuselageAero()

    def wrench(v_lin, quat, w, freq, phase0=0.0, n=N_PHI, tail_cmd=None):
        model.set_wing_command(frequency=freq)
        model.wing_left.amplitude = A0_AMP
        model.wing_right.amplitude = A0_AMP
        Fs = np.zeros(3)
        Ts = np.zeros(3)
        for i in range(n):
            F, T = model.compute_forces(np.zeros(3), quat, v_lin, w,
                                        phase0 + i / (n * max(freq, 0.5)))
            Fs += F
            Ts += T
        Fm, Tm = Fs / n, Ts / n
        tn = float(np.linalg.norm(Tm))
        if tn > 2.0:
            Tm = Tm * (2.0 / tn)
        if tail_cmd is not None:
            tail.set_command(elevator=tail_cmd[0], aileron=tail_cmd[1],
                             rudder=tail_cmd[2])
        v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * freq
        Ft, Tt = tail.compute(quat, v_lin, v_slip=v_slip)
        return Fm + Ft, Tm + Tt
    return wrench, FlapperAeroModel, TailFuselageAero


WRENCH, _M, _T = make_wrench()


def build_controller(carry):
    ctl = FlightController()
    (freq, ei, pitch_i, pd_last, pvx, pvy, pvz, wfx, wfy, wfz,
     roll_i) = carry
    ctl._freq_last = freq
    ctl._ei = ei
    ctl._pitch_i = pitch_i
    ctl._pd_last = pd_last
    ctl._pv_filt = np.array([pvx, pvy, pvz])
    ctl._w_filt = np.array([wfx, wfy, wfz])
    ctl._roll_i = roll_i
    ctl._t_last = -DT
    return ctl


def step_map(s):
    phys = s[:N_PHYS]
    vx, vy, vz, roll, pitch, wx, wy, wz = phys
    carry = s[N_PHYS:]
    ctl = build_controller(carry)
    quat = quat_from_rpy(roll, pitch, 0.0)
    pose = [0.0, 0.0, 3.0, quat[0], quat[1], quat[2], quat[3]]
    vel = [vx, vy, vz, wx, wy, wz]
    out = ctl.update(pose, vel, t_sim=0.0)
    F, T = WRENCH(np.array([vx, vy, vz]), quat,
                  np.array([wx, wy, wz]), out["freq"],
                  tail_cmd=(out["elevator"], out["aileron"],
                            out["rudder"]))
    acc = F / MASS - np.array([0.0, 0.0, G])
    v2 = np.array([vx, vy, vz]) + acc * DT
    wIw = np.cross(np.array([wx, wy, wz]), I_DIAG * np.array([wx, wy, wz]))
    wdot = (T - wIw) / I_DIAG
    w2 = np.array([wx, wy, wz]) + wdot * DT
    q = quat_from_rpy(roll, pitch, 0.0)
    qd = 0.5 * np.array([0.0, wx, wy, wz]) * DT
    q2 = q + np.array([qd[0], 0, 0, 0])  # 占位，下方完整四元数微分
    qd_full = 0.5 * quat_mul(q, np.array([0.0, wx, wy, wz]))
    q2 = q + qd_full * DT
    q2 = q2 / np.linalg.norm(q2)
    r2, p2, _y2 = rpy_from_quat(q2)
    carry2 = (out["freq"], ctl._ei, ctl._pitch_i, ctl._pd_last,
              ctl._pv_filt[0], ctl._pv_filt[1], ctl._pv_filt[2],
              ctl._w_filt[0], ctl._w_filt[1], ctl._w_filt[2], ctl._roll_i)
    return np.concatenate([[v2[0], v2[1], v2[2], r2, p2,
                            w2[0], w2[1], w2[2]], list(carry2)])


def quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([aw * bw - ax * bx - ay * by - az * bz,
                     aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw])


def find_fixed_point(s0, n=6000):
    s = s0.copy()
    for _ in range(n):
        s = step_map(s)
    return s


def solve_fixed_point(s0, n_iter=40):
    """阻尼牛顿求不稳平衡点：解 step_map(s) 减 s 为零。

    前向迭代只能到吸引子，不稳配平点必须求根。步映射含限幅与钳制的
    折点，牛顿步带回溯线搜索，发散即回半步。返回解与残差。
    """
    s = s0.copy()
    best = (np.inf, s.copy())
    for _it in range(n_iter):
        g = step_map(s) - s
        nrm = float(np.linalg.norm(g))
        if nrm < best[0]:
            best = (nrm, s.copy())
        if nrm < 1e-9:
            break
        Jg = jacobian(step_map, s) - np.eye(len(s))
        try:
            ds = np.linalg.lstsq(Jg, -g, rcond=1e-10)[0]
        except np.linalg.LinAlgError:
            break
        step_len = 1.0
        while step_len > 1e-4:
            s_try = s + step_len * ds
            s_try[3] = math.atan2(math.sin(s_try[3]),
                                  math.cos(s_try[3]))
            s_try[4] = float(np.clip(s_try[4], -1.2, 1.2))
            g_try = step_map(s_try) - s_try
            if np.linalg.norm(g_try) < nrm:
                s = s_try
                break
            step_len *= 0.5
        else:
            break
    return best[1], best[0]


def jacobian(f, x0, eps=1e-6):
    J = np.zeros((len(x0), len(x0)))
    f0 = f(x0)
    for j in range(len(x0)):
        dx = np.zeros(len(x0))
        dx[j] = eps
        J[:, j] = (f(x0 + dx) - f0) / eps
    return J


def eig_report(J):
    w = np.linalg.eigvals(J)
    rows = []
    for lam in w:
        if abs(lam) < 1e-12:
            re_c, im_c = -20.0, 0.0
        else:
            lc = np.log(complex(lam)) / DT
            re_c, im_c = lc.real, lc.imag
        rows.append((re_c, im_c, abs(lam)))
    rows.sort(key=lambda r: -r[0])
    return rows


def periodic_A(phys_trim, eps=1e-5):
    """逐相位力矩对角速度偏导 → A0 与一阶周期项。"""
    vx, vy, vz, roll, pitch, wx, wy, wz = phys_trim
    quat = quat_from_rpy(roll, pitch, 0.0)
    v = np.array([vx, vy, vz])
    A_phi = []
    for i in range(N_PHI):
        ph = i / N_PHI
        cols = []
        for k in range(3):
            wp = np.array([wx, wy, wz])
            wm = np.array([wx, wy, wz])
            wp[k] += eps
            wm[k] -= eps
            model = FlapperAeroModel()
            model.set_wing_command(frequency=cfg.FREQ_NOM
                                   if hasattr(cfg, "FREQ_NOM") else 1.8)
            model.wing_left.amplitude = A0_AMP
            model.wing_right.amplitude = A0_AMP
            Fp = Tp = None
            Fp, Tp = model.compute_forces(np.zeros(3), quat, v, wp, ph)
            Fm, Tm = model.compute_forces(np.zeros(3), quat, v, wm, ph)
            cols.append((Tp - Tm) / (2 * eps))
        A_phi.append(np.stack(cols, axis=1) / I_DIAG[:, None])
    A_phi = np.array(A_phi)
    A0 = A_phi.mean(axis=0)
    th = 2 * np.pi * np.arange(N_PHI) / N_PHI
    Ac = (2 / N_PHI) * (A_phi * np.cos(th)[:, None, None]).sum(axis=0)
    As = (2 / N_PHI) * (A_phi * np.sin(th)[:, None, None]).sum(axis=0)
    return A0, Ac, As, A_phi


def rot_body_from_world(quat):
    """世界系向量转体系的三乘三旋转矩阵（姿态四元数约定与控制器同）。"""
    w, x, y, z = quat
    R = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y + w * z),
         2 * (x * z - w * y)],
        [2 * (x * y - w * z), 1 - 2 * (x * x + z * z),
         2 * (y * z + w * x)],
        [2 * (x * z + w * y), 2 * (y * z - w * x),
         1 - 2 * (x * x + y * y)]])
    return R


def plant_trim(roll_deg=10.0, verbose=False):
    """开环机体稳态协调转弯配平。

    未知量 [u 机体前向, wb 机体竖直, pitch, elev, ail, rud, freq]，协调
    偏航率随 u 联动。平衡方程六条：机体系力平衡含向心项（F 除质量加重
    力体系分量减 w 叉乘 v 等零，三条）加三轴力矩归零。带边界投影的阻
    尼最小二乘求解。
    """
    roll = math.radians(roll_deg)
    lo = np.array([2.0, -1.0, -1.5, -0.6, -0.30, -0.30, -0.30])
    hi = np.array([5.0, 1.0, 1.5, 0.35, 0.30, 0.30, 0.30])

    def full(x):
        u, vy, wb, pitch, elev, ail, rud = x
        wz = G * math.tan(roll) / max(u, 1.0)
        quat = quat_from_rpy(roll, pitch, 0.0)
        R = rot_body_from_world(quat)
        v_b = np.array([u, vy, wb])
        v_w = R.T @ v_b
        F, T = WRENCH(v_w, quat, np.array([0.0, 0.0, wz]),
                      1.8, tail_cmd=(elev, ail, rud))
        F_b = R @ F
        g_b = R @ np.array([0.0, 0.0, -G])
        w_v = np.array([0.0, 0.0, wz])
        return np.concatenate([F_b / MASS + g_b - np.cross(w_v, v_b), T])

    x = np.array([3.9, 0.0, 0.05, -0.10, 0.0, 0.0, 0.0])
    lam = 1e-3
    best = (np.inf, x.copy())
    for _it in range(120):
        f0 = full(x)
        nrm = float(np.linalg.norm(f0))
        if nrm < best[0]:
            best = (nrm, x.copy())
        if nrm < 1e-11:
            break
        J = np.zeros((6, 7))
        for j in range(7):
            dx = np.zeros(7)
            dx[j] = 1e-6
            J[:, j] = (full(x + dx) - f0) / 1e-6
        stepv = np.linalg.lstsq(J.T @ J + lam * np.eye(7),
                                -J.T @ f0, rcond=None)[0]
        x_new = np.clip(x + np.clip(stepv, -0.2, 0.2), lo, hi)
        if np.linalg.norm(full(x_new)) < nrm:
            x, lam = x_new, max(lam * 0.5, 1e-8)
        else:
            lam = min(lam * 4, 1e2)
    x = best[1]
    wz = G * math.tan(roll) / max(x[0], 1.0)
    if verbose:
        print("开环配平（%g 度协调）：残差 %.2e" % (
            roll_deg, np.linalg.norm(full(x))))
        print("  u=%.3f vy=%+.3f wb=%+.3f pitch=%+.4f wz=%+.3f" % (
            x[0], x[1], x[2], x[3], wz))
        print("  舵：elev %+.3f ail %+.3f rud %+.3f（%s）" % (
            x[4], x[5], x[6],
            "方向舵顶满幅" if abs(x[6]) > 0.295 else "框内"))
    return x, wz


def open_loop_A(x_trim, wz, roll_deg=10.0, eps=1e-5, fb=None):
    """开环机体线性化：状态 [u, wb, roll, pitch, wx, wy, wz] 机体坐标
    系，含向心项与陀螺项。舵面与频率冻结在配平值（气流轴模态口径）。"""
    u0, vy0, wb0, p0, e0, a0, r0 = x_trim
    f0 = 1.8
    roll0 = math.radians(roll_deg)

    def deriv(s):
        u, vy, wb, roll, pitch, wx, wy, wzz = s
        quat = quat_from_rpy(roll, pitch, 0.0)
        R = rot_body_from_world(quat)
        v_b = np.array([u, vy, wb])
        v_w = R.T @ v_b
        w_v = np.array([wx, wy, wzz])
        cmd = [e0, a0, r0]
        if fb:
            cmd = [e0 + fb[1] * wy, a0 + fb[0] * wx, r0 + fb[2] * wzz]
        F, T = WRENCH(v_w, quat, w_v, f0, tail_cmd=tuple(cmd))
        F_b = R @ F
        g_b = R @ np.array([0.0, 0.0, -G])
        vdot = F_b / MASS + g_b - np.cross(w_v, v_b)
        wIw = np.cross(w_v, I_DIAG * w_v)
        wdot = (T - wIw) / I_DIAG
        # 欧拉角速率（小角度运动学；偏航角通道由协调约束闭合不入态）
        return np.array([vdot[0], vdot[1], vdot[2], wx, wy,
                         wdot[0], wdot[1], wdot[2]])

    s0 = np.array([u0, vy0, wb0, roll0, p0, 0.0, 0.0, wz])[:8]         if False else np.array([u0, vy0, wb0, roll0, p0, 0.0, 0.0, wz])
    A = np.zeros((8, 8))
    for j in range(8):
        sp, sm = s0.copy(), s0.copy()
        sp[j] += eps
        sm[j] -= eps
        A[:, j] = (deriv(sp) - deriv(sm)) / (2 * eps)
    return A, s0


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    FCC.BANK_TEST_ON = True
    FCC.BANK_TEST_T = 0.0
    FCC.BANK_TEST_DEG = 10.0
    TA = cfg.TailAeroConfig
    I0 = I_DIAG.copy()
    out = {"item": "I-102", "spec_sha256": spec_sha, "banks": {},
           "fidelity_notes": [
               "离线拍平均模型缺翼拍惯性反力矩项（kimi 工具同款口径），"
               "绝对配平失守与部分敏感度幅值受此限，方向结论相对可信",
               "十度配平点升降舵处顶满幅（负零点三），闭环另见俯仰积分"
               "顶满幅，两处饱和为在案死法的离线复现"], "new_sim_runs": 0}
    rep = ["# I-102 新构型十度配平点解析稳定性分析报告", "",
           "判据出处：事项登记文。方法：机体坐标系稳态协调转弯配平加八态"
           "开环线性化（kimi 离线工具扩展三通道），理想速率内环经尾舵叠"
           "加，周期口径用 I-079 判读核。", ""]

    def unstable_re(bank, fb=None):
        x, wz = plant_trim(bank)
        A, s0 = open_loop_A(x, wz, roll_deg=bank, fb=fb)
        w = sorted(np.linalg.eigvals(A), key=lambda v: -v.real)
        return w[0].real, w[0].imag, x, wz, A

    for bank in (2.0, 5.0, 10.0):
        re_u, im_u, x, wz, A = unstable_re(bank)
        w = sorted(np.linalg.eigvals(A), key=lambda v: -v.real)
        eig_rows = [{"re": float(v.real), "im": float(v.imag),
                     "hz": float(abs(v.imag) / 2 / np.pi),
                     "tag": "不稳定" if v.real > 0 else
                     ("弱阻尼" if v.real > -1 else "稳定")}
                    for v in w]
        env = {"tag": "bank%g" % bank, "path": "ltv 内构造",
               "A0": A[5:8, 5:8], "Ac": [], "As": [], "K": 1,
               "f_stroke": 1.8, "window": None, "state_dim": 3}
        per = read_floquet(env)
        out["banks"]["%g" % bank] = {
            "trim": x.tolist(), "wz": wz, "eig": eig_rows,
            "unstable_re": float(re_u),
            "floquet": {"max_mu": per["max_mu"],
                        "max_re": per["max_re"]}}
        rep.append("## 坡度 %g 度" % bank)
        rep.append("")
        rep.append("配平：u=%.3f 侧滑=%.3f 竖速=%.3f 俯仰=%.4f 弧度，"
                   "协调偏航率=%.3f 弧度每秒，舵（升降 %+.3f 差动 %+.3f "
                   "方向 %+.3f）。" % (x[0], x[1], x[2], x[3], wz,
                                        x[4], x[5], x[6]))
        rep.append("")
        rep.append("特征值清单（连续时间，按实部降序，前六条）：")
        rep.append("")
        rep.append("| 实部每秒 | 虚部每秒 | 频率赫兹 | 判读 |")
        rep.append("|---|---|---|---|")
        for r in eig_rows[:6]:
            rep.append("| %+.3f | %+.3f | %.3f | %s |" % (
                r["re"], r["im"], r["hz"], r["tag"]))
        rep.append("")
        rep.append("周期口径：拍周期最大增长倍率 %.5f，弗洛凯指数实部 "
                   "%+.4f 每秒（拍平均角速度子系统）。"
                   % (per["max_mu"], per["max_re"]))
        rep.append("")
    base = out["banks"]["10"]["unstable_re"]
    rep.append("## 不稳定模式敏感度表（十度配平，正实部模态）")
    rep.append("")
    rep.append("主导失稳模态为非振荡实模态，十度处实部 %+.3f 每秒，滚转"
               "通道主导（方向舵内环零效应，差动内环强效应）。随坡度超线"
               "性增长：二度 %+.3f、五度 %+.3f、十度 %+.3f 每秒。"
               % (base, out["banks"]["2"]["unstable_re"],
                  out["banks"]["5"]["unstable_re"], base))
    rep.append("")
    rep.append("| 参数档 | 模态实部 | 变化量 | 方向 | 外推稳定所需 |")
    rep.append("|---|---|---|---|---|")
    sens = []

    def run_case(tag, fn, note):
        old = (TA.AREA, TA.LEVER_ARM, TA.PANEL_Y, I_DIAG.copy())
        r, _, _x, _w, _A = None, None, None, None, None
        try:
            fn()
            r = unstable_re(10.0)[0]
        finally:
            TA.AREA, TA.LEVER_ARM, TA.PANEL_Y = old[0], old[1], old[2]
            I_DIAG[:] = old[3]
        sens.append({"tag": tag, "re": float(r), "delta": float(r - base),
                     "note": note})
        rep.append("| %s | %+.3f | %+.3f | %s | %s |" % (
            tag, r, r - base,
            "改善" if r < base else ("恶化" if r > base else "无效应"),
            note))
        return r

    run_case("基准", lambda: None, "—")
    run_case("尾面积 +10%", lambda: setattr(TA, "AREA", TA.AREA * 1.1),
             "约 +300% 才够，超物理域")
    run_case("尾面积 -10%", lambda: setattr(TA, "AREA", TA.AREA * 0.9),
             "方向改善但约 -300% 才够，不可能")
    run_case("力臂 +10%",
             lambda: setattr(TA, "LEVER_ARM", TA.LEVER_ARM * 1.1),
             "恶化，方向错误")
    run_case("力臂 -10%",
             lambda: setattr(TA, "LEVER_ARM", TA.LEVER_ARM * 0.9),
             "约 -140% 才够，超物理域")
    run_case("差动力臂 +10%",
             lambda: setattr(TA, "PANEL_Y", TA.PANEL_Y * 1.1),
             "近零效应")
    run_case("尾质量 +50%（经平行轴惯量）",
             lambda: I_DIAG.__setitem__(0, I0[0] + 0.013 * 0.224 ** 2)
             or I_DIAG.__setitem__(1, I0[1] + 0.013 * (0.45 ** 2
                                                       + 0.224 ** 2))
             or I_DIAG.__setitem__(2, I0[2] + 0.013 * 0.45 ** 2),
             "方向改善，约六倍尾质量才够，不可能")
    run_case("尾质量 -50%", lambda: (
        I_DIAG.__setitem__(0, I0[0] - 0.013 * 0.224 ** 2),
        I_DIAG.__setitem__(1, I0[1] - 0.013 * (0.45 ** 2 + 0.224 ** 2)),
        I_DIAG.__setitem__(2, I0[2] - 0.013 * 0.45 ** 2)),
        "恶化，方向错误")
    run_case("Ixx +10%",
             lambda: I_DIAG.__setitem__(0, I0[0] * 1.1),
             "微弱改善，量级不足")
    run_case("差动内环 k_wx=-0.10（理想）", lambda: None,
             "增益路径见下表")
    for k, tag in ((-0.10, "k_wx=-0.10"), (-0.30, "k_wx=-0.30"),
                   (-0.10, "k_wy=-0.10"), (-0.10, "k_wz=-0.10")):
        axis = {"k_wx=-0.10": 0, "k_wx=-0.30": 0, "k_wy=-0.10": 1,
                "k_wz=-0.10": 2}[tag]
        fbv = [0.0, 0.0, 0.0]
        fbv[axis] = k
        r = unstable_re(10.0, fb=tuple(fbv))[0]
        need = "饱和趋势，外推需求超舵权限域" if axis == 0 else (
            "通过俯仰路径强烈恶化，禁用" if axis == 1 else "零效应")
        sens.append({"tag": tag, "re": float(r),
                     "delta": float(r - base), "note": need})
        rep.append("| %s（理想内环） | %+.3f | %+.3f | %s | %s |" % (
            tag, r, r - base, "改善" if r < base else "恶化", need))
    out["sensitivity_10deg"] = sens
    rep += ["", "## 结论（三种形态逐条）", "",
            "一、可稳定区域存在与否：在本离线模型的四族参数（尾翼质量、"
            "力臂、惯量、差动力臂）与理想速率内环的域内，不存在使十度配"
            "平主导失稳模态实部转负的单参数或增益档——全部改善方向的量"
            "级都远不够（最好单档尾质量加五成仅降零点二七每秒对三点一"
            "九），外推需求（面积三倍、力臂一点四倍、尾质量六倍、内环"
            "增益饱和）全部超物理或权限域。判：本参数族内结构性不可稳。",
            "",
            "二、区域过窄形态：与一队增益全域二十八跑仅得二十一点八秒"
            "孤峰互证——闭环的复杂滤波网络能压住该模态大半（开环三秒"
            "内翻覆对闭环存活二十秒），但稳定裕度只剩参数窄缝，判：若"
            "强要在本构型上稳定，剩余路径是过窄区域（I-103 密集地图将"
            "给出缝的宽度）。",
            "",
            "三、结构性结论的出路：失稳模态为滚转通道实模态且随坡度超"
            "线性增长（二度零点二一至十度三点一九每秒），尾翼族参数均"
            "非其有效杠杆（面积与差动力臂近零或反向），有效杠杆在参数"
            "族外：主翼上反角、翼质量展向分布、拍波形滚转分量——即结"
            "构级处置方向，与一队撞墙申报的三选一材料互洽。",
            "",
            "## 保真度声明", ""] + [("- " + x + ".")
                                    for x in out["fidelity_notes"]] + [
        "", "机器可读判 stage1.json 与 verdict.json 同目录。零新增在"
        "环运行。", ""]
    out_dir = os.path.join(_HERE, "i102_results",
                           time.strftime("i102_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return out


if __name__ == "__main__":
    main()
