# -*- coding: utf-8 -*-
"""
scan_thrust_freq.py — 推力-频率特性全曲线扫描（第四十九班）
================================================================
依据：第四十七班（∂Fx/∂f≈−0.45 N/Hz 参与极限环机理账）与第四十八班
（下一班候选裁定）的讨论；数字源头=第三十一班视角A（V=3.5/θ=21.45°
钉固、f=1.0~2.0）拟合 ∂Fx/∂f=−0.4495 N/Hz、Fx 过零 f≈1.22 Hz
（"1.25 过零疑点"，docs/第三十一班_十八项签核处置.md §一）。

问题：拍频通道推力符号反号（升频减推力）是波形属性（旧波形 A 正常、
新波形 B 反号）还是模型属性（A 也反号）？

扫描设计：
  波形 A = 旧波形 α_set=40°/MEAN=0°/SPLIT=0.25/LAG=−0.02 s/幅值=35°
           （锚频 1.55 Hz=config 旧工作频率 FREQUENCY，历史配对
           CRUISE_VX=4.5；本班两速度 3.5/4.5 均用 1.55 锚）
  波形 B = 新波形 α_set=35°/MEAN=+20°/SPLIT=0.75/LAG=−0.04 s/幅值=30°
           （锚频 1.25 Hz=甲组签核工作频率，feasibility_map.FREQ_TRIM）
  频率 0.8~3.5 Hz 步 0.1（28 点，覆盖 FREQ_AP_MIN~FREQ_MAX_AP 全域）
  速度两档 3.5 / 4.5 m/s；n=288/周期（终值分辨率纪律）。

双视角（沿第三十一班 scan_dfzdf_op2 双口径传统）：
  视角A 固定配平俯仰（主口径，通道实感）：每 (波形,速度) 在锚频解
    Fx=0 配平俯仰 θ*，钉固 θ* 扫全程 f——频率通道在姿态慢变假设下
    实际感受的 Fx(f) 曲线（−0.4495 与 1.22 过零即此口径）。
  视角B 逐点重配平（任务字面口径）：每个 f 先解 Fx=0 配平俯仰再
    周期平均——配平轨迹 θ(f)、Fz(f) 与可配平频段。

分量分账口径（差分分解，叠加原理）：
  叶素平动力对气动系数严格线性：dF_normal ∝ |C_N(α_eff)|、
  dF_tangent ∝ C_T(α_eff)，二者在 canonical（modules/aero/
  sane_dickinson_3d.py _wing_forces_canonical）中已分列求和后叠加；
  旋转力（enable_rotational 开关）、附加质量力（enable_added_mass
  开关）为独立加性项，不依赖两系数。故同一时刻五次求值：
    全参 F_full = N+T+R+A；切向置零（ct_fit_amp=ct_fit_offset=0）
    → F_noT = N+R+A；法向置零（cn_fit_amp=0）→ F_noN = T+R+A；
    旋转关（enable_rotational=False）→ F_noR = N+T+A；
    附加质量关（enable_added_mass=False）→ F_noA = N+T+R
  得 F_n=F_full−F_noN（法向贡献）、F_t=F_full−F_noT（切向贡献）、
  F_rot=F_full−F_noR（旋转力）、F_add=F_full−F_noA（附加质量力），
  代数上 F_n+F_t+F_rot+F_add≡F_full（机翼世界系合力），每点做闭合校验。
  尾翼力（含滑流）单列 F_tail，不参与四分账。
  选差分而非子类转录：差分对父实现零转录，不存在漂移风险；
  右翼经镜像变换 diag(1,−1,1) 回机体系，x 分量不受镜像影响，
  分解在整机（双翼求和后）层面做，镜像自动正确。
  附带消融（机理定位用，非主扫描）：波形 SPLIT 改 0.5/对调
  （其余四参不变）后仅做 V=3.5 视角A 定θ*扫描，定位 Fx_add 符号
  与分裂比不对称方向的关系。

判据（先于数据）：
  ① Fx(f) 单调性与过零点位置（两波形各报，视角A口径）；
  ② ∂Fx/∂f 符号区间（视角A，分段差分）；
  ③ 法向/切向分量随 f 增长速率对照——检验"法向饱和、切向平方涨"
    假说（切向 ∝ f² 与线性两模型拟合对照，法向同理）；
  ④ 斯特劳哈尔数 St=f·(2·半展·sin(幅值+|MEAN|))/V：每波形 St 工作区
    （Fx>0 推力正段与锚点的 St）对照真机 7.5 Hz@6 m/s 的 St；
  ⑤ 判决：频率通道失效=波形属性（A 正常 B 反号）或模型属性
    （A 也反号）——按两波形锚点邻域 ∂Fx/∂f 符号（两速度一致才判）。

测量纪律（第十六班三条）：时间轴全局单调推进（本工具独立时基，
只增不减）/ 显式设频（set_wing_command）/ 粗扫 n=72、终值 n=288。
静态口径（角速度恒为零）：ω×r 耦合项 ω=0 时逐位为零（第二十班）。

用法： python tools/scan_thrust_freq.py
产物： logs/scan_thrust_freq.json（+ 控制台摘要）
纪律： 只读现有文件；不改任何 config；不启动 Isaac；不执行 git 操作。
"""
import os
import sys
import json
import math
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
from config import sim_config as cfg
from modules.aero_solver import TailFuselageAero, euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G = 9.81
W = cfg.MASS * G                      # 2.109 N
N_COARSE, N_FINE = 72, 288
F_AXIS = [round(0.8 + 0.1 * i, 1) for i in range(28)]    # 0.8~3.5，28 点
V_LIST = [3.5, 4.5]
HALF_SPAN = float(cfg.WingGeometryConfig.SPAN)           # 0.32 m 半展
FX_RESOL = 0.02                        # n=288 下 Fx 矩形求积分辨（第十六班实测 ±0.02 N）

WAVES = {
    "A": dict(alpha_set=40.0, mean_deg=0.0, split=0.25,
              pitch_lag_s=-0.02, amplitude_deg=35.0, f_ref=1.55),
    "B": dict(alpha_set=35.0, mean_deg=20.0, split=0.75,
              pitch_lag_s=-0.04, amplitude_deg=30.0, f_ref=1.25),
}

model = SaneDickinson3DModel()
tail = TailFuselageAero()
TB = [120000.0]                        # 本工具全局单调时基（与其它工具时基无关，同模型相位状态独立）


def set_waveform(w):
    for wing in (model.wing_left, model.wing_right):
        wing.alpha_set = math.radians(w["alpha_set"])
        wing.mean = math.radians(w["mean_deg"])
        wing.split = float(w["split"])
        wing.pitch_lag_s = float(w["pitch_lag_s"])
        wing.amplitude = math.radians(w["amplitude_deg"])


def _compute5(quat, vel, om, t, decomp):
    """同一时刻一次（或差分分解五次）机翼力求值。
    置零系数法：_C_N 用 self.cn_fit_amp、_C_T 用 self.ct_fit_amp 与
    self.ct_fit_offset（属性即时读取）；旋转/附加质量走 enable 开关；
    置零后对应分量严格为零，五次求值同一时刻相位状态不推进。"""
    F_full, T_full = model.compute_forces(np.zeros(3), quat, vel, om, t)
    if not decomp:
        return F_full, T_full, None, None, None, None
    cn0, ct0, cto0 = model.cn_fit_amp, model.ct_fit_amp, model.ct_fit_offset
    er0, ea0 = model.enable_rotational, model.enable_added_mass
    model.cn_fit_amp = 0.0                     # 法向置零 → F_noN = T+R+A
    F_noN, _ = model.compute_forces(np.zeros(3), quat, vel, om, t)
    model.cn_fit_amp = cn0
    model.ct_fit_amp = 0.0
    model.ct_fit_offset = 0.0                  # 切向置零 → F_noT = N+R+A
    F_noT, _ = model.compute_forces(np.zeros(3), quat, vel, om, t)
    model.ct_fit_amp, model.ct_fit_offset = ct0, cto0
    model.enable_rotational = False            # 旋转关 → F_noR = N+T+A
    F_noR, _ = model.compute_forces(np.zeros(3), quat, vel, om, t)
    model.enable_rotational = er0
    model.enable_added_mass = False            # 附加质量关 → F_noA = N+T+R
    F_noA, _ = model.compute_forces(np.zeros(3), quat, vel, om, t)
    model.enable_added_mass = ea0
    F_n = F_full - F_noN                        # 法向贡献（世界系）
    F_t = F_full - F_noT                        # 切向贡献（世界系）
    F_rot = F_full - F_noR                      # 旋转力（世界系）
    F_add = F_full - F_noA                      # 附加质量力（世界系）
    return F_full, T_full, F_n, F_t, F_rot, F_add


def cycle_eval(V, f, pd_deg, n=N_FINE, decomp=False):
    """钉固状态整周期平均（三纪律：单调时基/显式设频/分辨率分级）。
    口径与 scan_waveform_retrim.eval_state 一致：升降舵零位、
    滑流 v_slip=SLIP_K·SLIP_V_PER_HZ·f、世界系力/力矩。"""
    model.set_wing_command(frequency=f)
    tail.set_command(elevator=0.0)
    quat = euler_to_quat(0.0, math.radians(pd_deg), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    T = 1.0 / f
    S_full = np.zeros(3); S_T = np.zeros(3)
    S_n = np.zeros(3); S_t = np.zeros(3)
    S_rot = np.zeros(3); S_add = np.zeros(3)
    for i in range(n):
        t = TB[0] + i * T / n
        F_full, T_full, F_n, F_t, F_rot, F_add = _compute5(quat, vel, om, t, decomp)
        S_full += F_full; S_T += T_full
        if decomp:
            S_n += F_n; S_t += F_t; S_rot += F_rot; S_add += F_add
    TB[0] += T
    F_wing = S_full / n
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f
    F_tail, T_tail = tail.compute(quat, vel, v_slip=v_slip)
    out = dict(F_total=F_wing + F_tail, T_total=S_T / n + T_tail,
               F_wing=F_wing, T_wing=S_T / n, F_tail=F_tail, T_tail=T_tail,
               pitch_deg=pd_deg)
    if decomp:
        F_n, F_t, F_rot, F_add = S_n / n, S_t / n, S_rot / n, S_add / n
        F_total = F_wing + F_tail
        out.update(F_n=F_n, F_t=F_t, F_rot=F_rot, F_add=F_add,
                   fx=float(F_total[0]), fz=float(F_total[2]),
                   fx_n=float(F_n[0]), fx_t=float(F_t[0]),
                   fx_rot=float(F_rot[0]), fx_add=float(F_add[0]),
                   fx_tail=float(F_tail[0]),
                   fz_n=float(F_n[2]), fz_t=float(F_t[2]),
                   fz_rot=float(F_rot[2]), fz_add=float(F_add[2]),
                   fx_wing=float(F_wing[0]),
                   closure=float(abs((F_n + F_t + F_rot + F_add - F_wing).max())))
    return out


def solve_pitch(V, f):
    """θ 域求 Fx=0（与 scan_waveform_retrim.solve_pitch 同法：粗扫变号
    +二分，多根取竖直残差最小者，粗 n=72、精修+终值 n=288）。
    返回 (θ, rooted)；rooted=终值 |Fx|≤FX_RESOL（Fx 是推力/阻力近对消
    小量，n=288 矩形求积误差 ±0.02 N 量级，超出即按不可闭合记档）。"""
    def fx(th, n):
        return float(cycle_eval(V, f, th, n)["F_total"][0])

    grid = np.arange(-20.0, 25.001, 1.5)
    vals = [(float(th), fx(th, N_COARSE)) for th in grid]
    brackets = [(a[0], b[0]) for a, b in zip(vals[:-1], vals[1:])
                if a[1] == 0.0 or a[1] * b[1] < 0]
    if not brackets:
        th0 = min(vals, key=lambda r: abs(r[1]))[0]
        a, b = th0 - 1.0, th0 + 1.0
    else:
        best = None
        for a0, b0 in brackets:
            a, b = a0, b0
            fa = fx(a, N_COARSE)
            for _ in range(14):
                mid = 0.5 * (a + b)
                fm = fx(mid, N_COARSE)
                if fa * fm <= 0:
                    b = mid
                else:
                    a, fa = mid, fm
            th_c = 0.5 * (a + b)
            fz_c = float(cycle_eval(V, f, th_c, N_COARSE)["F_total"][2])
            score = abs(fz_c - W)
            if best is None or score < best[0]:
                best = (score, th_c, a, b)
        _, th_c, a, b = best
    fa = fx(a, N_FINE)
    fb = fx(b, N_FINE)
    if fa * fb < 0:
        for _ in range(8):
            mid = 0.5 * (a + b)
            fm = fx(mid, N_FINE)
            if fa * fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
    th = 0.5 * (a + b)
    resid = fx(th, N_FINE)
    return th, abs(resid) <= FX_RESOL


def st_of(f, V, wave):
    """判据④公式：St=f·(2·半展·sin(幅值+|MEAN|))/V。"""
    ang = math.radians(wave["amplitude_deg"] + abs(wave["mean_deg"]))
    return f * (2.0 * HALF_SPAN * math.sin(ang)) / V


# ── 分析函数（先于数据入库，判据①~③的固定算法）──

def zero_crossings(fs, ys):
    """逐段变号线性插值过零点（含端点恰为零）。"""
    out = []
    for i in range(len(fs) - 1):
        y0, y1 = ys[i], ys[i + 1]
        if y0 == 0.0:
            out.append(float(fs[i]))
        elif y0 * y1 < 0:
            out.append(float(fs[i] + (fs[i + 1] - fs[i]) * (-y0) / (y1 - y0)))
    if ys[-1] == 0.0:
        out.append(float(fs[-1]))
    return out


def sign_intervals(fs, ys, tol=0.0):
    """∂Fx/∂f 符号区间：逐段中央差分 dF/df 取符号（容差内记 0）。"""
    segs = []
    for i in range(len(fs) - 1):
        d = (ys[i + 1] - ys[i]) / (fs[i + 1] - fs[i])
        s = "+" if d > tol else ("-" if d < -tol else "0")
        if segs and segs[-1][2] == s:
            segs[-1][1] = fs[i + 1]
        else:
            segs.append([fs[i], fs[i + 1], s])
    return segs


def local_slope(fs, ys, f0):
    """锚点邻域中央差分斜率（锚点左右各取最近网格点）。"""
    i = min(range(len(fs)), key=lambda k: abs(fs[k] - f0))
    if 0 < i < len(fs) - 1:
        return (ys[i + 1] - ys[i - 1]) / (fs[i + 1] - fs[i - 1]), fs[i - 1], fs[i + 1]
    return float("nan"), None, None


def peak_of(fs, ys, mode):
    """峰/谷位置（网格 argmax/argmin）。"""
    i = int(np.argmax(ys)) if mode == "max" else int(np.argmin(ys))
    return float(fs[i]), float(ys[i])


def quad_vs_lin_fit(fs, ys, f_lo=None, f_hi=None):
    """判据③拟合：y≈a·f²+b 与 y≈c·f+d 最小二乘对照（返回系数与 R²）。"""
    xs = np.array([f for f in fs
                   if (f_lo is None or f >= f_lo - 1e-9)
                   and (f_hi is None or f <= f_hi + 1e-9)])
    zv = np.array([y for f, y in zip(fs, ys)
                   if (f_lo is None or f >= f_lo - 1e-9)
                   and (f_hi is None or f <= f_hi + 1e-9)])
    if len(xs) < 3:
        return None
    A2 = np.vstack([xs ** 2, np.ones_like(xs)]).T
    (a, b), *_ = np.linalg.lstsq(A2, zv, rcond=None)
    r2 = 1.0 - float(np.sum((zv - (a * xs ** 2 + b)) ** 2)) / \
        max(float(np.sum((zv - zv.mean()) ** 2)), 1e-12)
    A1 = np.vstack([xs, np.ones_like(xs)]).T
    (c, d), *_ = np.linalg.lstsq(A1, zv, rcond=None)
    r1 = 1.0 - float(np.sum((zv - (c * xs + d)) ** 2)) / \
        max(float(np.sum((zv - zv.mean()) ** 2)), 1e-12)
    return dict(f_range=[float(xs[0]), float(xs[-1])], n=len(xs),
                quad=dict(a=float(a), b=float(b), r2=r2),
                lin=dict(c=float(c), d=float(d), r2=r1))


def analyze_fixed(rows, wave, V):
    """视角A曲线判据①②③④（固定算法）。"""
    fs = [r["f"] for r in rows]
    fx = [r["fx"] for r in rows]
    fxn = [abs(r["fx_n"]) for r in rows]
    fxt = [abs(r["fx_t"]) for r in rows]
    out = dict(
        zero_cross_fx=zero_crossings(fs, fx),
        peak_fx=peak_of(fs, fx, "max"), trough_fx=peak_of(fs, fx, "min"),
        slope_at_anchor=local_slope(fs, fx, wave["f_ref"]),
        slope_segments=sign_intervals(fs, fx),
        fits_fx_n=quad_vs_lin_fit(fs, fxn, 1.0, 3.5),
        fits_fx_t=quad_vs_lin_fit(fs, fxt, 1.0, 3.5),
        fits_fx_add=quad_vs_lin_fit(fs, [r["fx_add"] for r in rows], 0.8, 3.5),
        fits_fx_rot=quad_vs_lin_fit(fs, [r["fx_rot"] for r in rows], 0.8, 3.5),
        fits_fz_n=quad_vs_lin_fit(fs, [r["fz_n"] for r in rows]),
        st_at_ref=st_of(wave["f_ref"], V, wave),
        st_axis=[st_of(fs[0], V, wave), st_of(fs[-1], V, wave)],
    )
    # 判据④ St 工作区：Fx>0 推力正段（连续正段取最长者）上的 St 范围
    pos = [i for i, y in enumerate(fx) if y > 0]
    if pos:
        runs, run = [], [pos[0]]
        for i in pos[1:]:
            if i == run[-1] + 1:
                run.append(i)
            else:
                runs.append(run); run = [i]
        runs.append(run)
        best = max(runs, key=len)
        out["st_workband"] = dict(
            f_lo=float(fs[best[0]]), f_hi=float(fs[best[-1]]),
            st_lo=st_of(fs[best[0]], V, wave), st_hi=st_of(fs[best[-1]], V, wave),
            n=len(best))
    else:
        out["st_workband"] = None
    return out


def analyze_retrim(rows, wave, V):
    """视角B配平轨迹：可配平频段、θ(f) 走向、Fz=W 过零。"""
    fs = [r["f"] for r in rows]
    ok = [r for r in rows if r["rooted"]]
    out = dict(
        n_rooted=len(ok),
        rooted_band=None if not ok else [min(r["f"] for r in ok),
                                         max(r["f"] for r in ok)],
        theta_samples=[[r["f"], r["theta_deg"]] for r in rows[::7]],
        fz_cross_w=zero_crossings(fs, [r["fz"] - W for r in rows]),
        slope_theta_deg_per_hz=(None if len(ok) < 2 else
                                (ok[-1]["theta_deg"] - ok[0]["theta_deg"])
                                / (ok[-1]["f"] - ok[0]["f"])),
    )
    return out


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[stf] 推力-频率特性扫描 W=%.3f N  f=0.8~3.5 Hz 28点  V=%s  n=%d"
          % (W, V_LIST, N_FINE), flush=True)
    out = dict(ts=ts, model="sane_dickinson_3d",
               decomp="五分账差分分解（C_N/C_T 置零 + 旋转/附加质量开关，叠加原理精确）",
               static_omega_zero=True, n_fine=N_FINE, n_coarse=N_COARSE,
               fx_resol_n=FX_RESOL, weight_n=W, half_span_m=HALF_SPAN,
               waves=WAVES, V_list=V_LIST, freq_axis=F_AXIS,
               st_formula="St=f*(2*half_span*sin(amplitude+|MEAN|))/V",
               criteria_note="判据①②⑤主口径=视角A（固定配平俯仰）；③两视角同报；④全频率轴；"
                             "消融段=SPLIT 单变量机理定位（非主扫描）")
    combos = {}
    for name, w in WAVES.items():
        set_waveform(w)
        for V in V_LIST:
            key = "%s_V%.1f" % (name, V)
            # ── 锚：f_ref 处解 Fx=0 配平俯仰 ──
            th_star, rooted = solve_pitch(V, w["f_ref"])
            anchor = cycle_eval(V, w["f_ref"], th_star, N_FINE, decomp=True)
            # ── 锚邻域密扫（0.1° 步，n=288，plain）：记档 Fx(θ) 连续性
            dense = []
            th_lo, th_hi = th_star - 2.5, th_star + 2.5
            th = th_lo
            while th <= th_hi + 1e-9:
                fxv = float(cycle_eval(V, w["f_ref"], round(th, 3), N_FINE)["F_total"][0])
                dense.append([round(th, 3), fxv])
                th += 0.1
            # ── 视角A：钉固 θ* 扫 f ──
            fixed = []
            for f in F_AXIS:
                r = cycle_eval(V, f, th_star, N_FINE, decomp=True)
                fixed.append(dict(f=f, st=st_of(f, V, w),
                                  fx=r["fx"], fz=r["fz"],
                                  fx_n=r["fx_n"], fx_t=r["fx_t"],
                                  fx_rot=r["fx_rot"], fx_add=r["fx_add"],
                                  fx_tail=r["fx_tail"],
                                  fx_wing=r["fx_wing"],
                                  fz_n=r["fz_n"], fz_t=r["fz_t"],
                                  fz_rot=r["fz_rot"], fz_add=r["fz_add"],
                                  my=float(r["T_total"][1]),
                                  closure=r["closure"]))
            # ── 视角B：逐 f 重配平 ──
            retrim = []
            for f in F_AXIS:
                th_f, rooted_f = solve_pitch(V, f)
                r = cycle_eval(V, f, th_f, N_FINE, decomp=True)
                retrim.append(dict(f=f, theta_deg=th_f, rooted=rooted_f,
                                   fx=r["fx"], fz=r["fz"],
                                   fz_over_w=r["fz"] / W,
                                   fx_n=r["fx_n"], fx_t=r["fx_t"],
                                   fx_rot=r["fx_rot"], fx_add=r["fx_add"],
                                   fx_tail=r["fx_tail"],
                                   fz_n=r["fz_n"], fz_t=r["fz_t"],
                                   st=st_of(f, V, w),
                                   closure=r["closure"]))
            an_f = analyze_fixed(fixed, w, V)
            an_r = analyze_retrim(retrim, w, V)
            n_rooted = len([r for r in retrim if r["rooted"]])
            combos[key] = dict(
                wave=name, V=V, f_ref=w["f_ref"], theta_star=th_star,
                anchor=dict(fx=anchor["fx"], fz=anchor["fz"],
                            fz_over_w=anchor["fz"] / W,
                            fx_n=anchor["fx_n"], fx_t=anchor["fx_t"],
                            fx_rot=anchor["fx_rot"], fx_add=anchor["fx_add"],
                            fx_tail=anchor["fx_tail"],
                            rooted=rooted,
                            closure=anchor["closure"]),
                dense_theta_fx=dense, fixed=fixed, retrim=retrim,
                analysis_fixed=an_f, analysis_retrim=an_r)
            zs = zero_crossings([r["f"] for r in fixed], [r["fx"] for r in fixed])
            print("[stf] %s θ*=%+.2f° 锚Fx=%+.4f 锚Fz/W=%.3f rooted=%s | "
                  "视角A过零=%s 峰=%s(%+.3fN) 锚斜率=%+.3f | 视角B可配平%d/28"
                  % (key, th_star, anchor["fx"], anchor["fz"] / W, rooted,
                     (["%.3f" % z for z in zs] or ["无"]),
                     an_f["peak_fx"][0], an_f["peak_fx"][1],
                     an_f["slope_at_anchor"][0], n_rooted), flush=True)
    out["combos"] = combos

    # ── 判据⑤：判决（两波形锚点斜率符号，两速度一致才判）──
    sA = [combos["A_V%.1f" % V]["analysis_fixed"]["slope_at_anchor"][0] for V in V_LIST]
    sB = [combos["B_V%.1f" % V]["analysis_fixed"]["slope_at_anchor"][0] for V in V_LIST]
    a_neg = all(s < 0 for s in sA)
    b_neg = all(s < 0 for s in sB)
    if a_neg and b_neg:
        verdict = "模型属性（A 也反号：两波形锚点 ∂Fx/∂f 均为负）"
    elif (not a_neg) and b_neg:
        verdict = "波形属性（A 正常 B 反号）"
    elif a_neg and (not b_neg):
        verdict = "波形属性（B 正常 A 反号）"
    else:
        verdict = "两波形均正常（锚点 ∂Fx/∂f 均非负）"
    out["verdict"] = dict(text=verdict,
                          slope_A={str(V): s for V, s in zip(V_LIST, sA)},
                          slope_B={str(V): s for V, s in zip(V_LIST, sB)})

    # ── 判据④：真机 7.5 Hz@6 m/s 对照（同一半展，两波形角度口径各算）──
    out["st_real"] = {name: dict(f=7.5, V=6.0,
                                 angle_deg=w["amplitude_deg"] + abs(w["mean_deg"]),
                                 st=st_of(7.5, 6.0, w)) for name, w in WAVES.items()}

    # ── 机理定位消融（非主扫描）：SPLIT 单变量改 0.5/对调，V=3.5 视角A ──
    # 依据：主扫描分解显示 Fx 的 f² 增长项几乎全部在附加质量通道
    # （fx_add），其符号疑似由分裂比不对称方向（s 偏离 0.5 的方向）决定。
    # 消融单变量改 SPLIT：A_s05/A_s075（0.25→0.5/0.75）、B_s05/B_s025
    # （0.75→0.5/0.25），其余四参与母波形不变，锚频沿用母波形 f_ref。
    ablations = {}
    for ab_name, mother, s_new in (("A_s05", "A", 0.5), ("A_s075", "A", 0.75),
                                   ("B_s05", "B", 0.5), ("B_s025", "B", 0.25)):
        w = dict(WAVES[mother]); w["split"] = s_new
        set_waveform(w)
        V = 3.5
        th_star, rooted = solve_pitch(V, w["f_ref"])
        anchor = cycle_eval(V, w["f_ref"], th_star, N_FINE, decomp=True)
        rows = []
        for f in F_AXIS:
            r = cycle_eval(V, f, th_star, N_FINE, decomp=True)
            rows.append(dict(f=f, fx=r["fx"], fz=r["fz"],
                             fx_n=r["fx_n"], fx_t=r["fx_t"],
                             fx_rot=r["fx_rot"], fx_add=r["fx_add"],
                             fx_tail=r["fx_tail"]))
        fx = [r["fx"] for r in rows]; fs = [r["f"] for r in rows]
        add8 = [r["fx_add"] for r in rows]
        ablations[ab_name] = dict(
            wave=mother, split=s_new, V=V, f_ref=w["f_ref"],
            theta_star=th_star, anchor_fx=anchor["fx"],
            anchor_fz_over_w=anchor["fz"] / W, rooted=rooted, rows=rows,
            zero_cross_fx=zero_crossings(fs, fx),
            slope_at_anchor=local_slope(fs, fx, w["f_ref"]),
            fx_add_at=[add8[0], add8[len(add8) // 2], add8[-1]])
        print("[stf] 消融 %s（%s SPLIT→%.2f）：θ*=%+.2f 锚Fx=%+.4f 过零=%s "
              "锚斜率=%+.3f fx_add(0.8/2.15/3.5)=%+.3f/%+.3f/%+.3f"
              % (ab_name, mother, s_new, th_star, anchor["fx"],
                 ["%.3f" % z for z in zero_crossings(fs, fx)] or ["无"],
                 local_slope(fs, fx, w["f_ref"])[0],
                 add8[0], add8[len(add8) // 2], add8[-1]), flush=True)
    out["ablations"] = ablations

    jpath = os.path.join(cfg.LOG_DIR, "scan_thrust_freq.json")
    with open(jpath, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print("[stf] 判决：%s" % verdict, flush=True)
    print("[stf] 真机 St（7.5Hz@6m/s，半展%.2f）：A=%.4f（角%.0f°） B=%.4f（角%.0f°）"
          % (HALF_SPAN, out["st_real"]["A"]["st"], out["st_real"]["A"]["angle_deg"],
             out["st_real"]["B"]["st"], out["st_real"]["B"]["angle_deg"]), flush=True)
    print("[stf] JSON → %s" % jpath, flush=True)


if __name__ == "__main__":
    main()
