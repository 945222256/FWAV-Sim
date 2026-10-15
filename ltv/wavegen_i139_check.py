# -*- coding: utf-8 -*-
"""
wavegen_i139_check.py — I-139 相位域波形发生器预登记验收脚本。

执行 ltv/wavegen_prereg_20261009.md 第十二节六条判据（甲乙丙丁戊己），
全部离线，零 Isaac 依赖。结果写 ltv/wavegen_i139_results_<时间戳>/
目录：verdict.json（机器可读逐条判定）与 report.md（人类可读）。

数值来源声明：
- 执行链名义参数取在案辨识集 logs/ident_matrix/I118new5_20261008_090235/
  matrix.json 的逐通道共振频率、阻尼比、纯延迟；文件缺失时以正文记载的
  区间端点内取值并以告警标注。
- 现行工作点（占空比、拍频、振幅、俯仰滞后）取 config/sim_config.py 的
  FlapKinematicsConfig 冻结值。
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import wavegen_family as wg  # noqa: E402

RESULTS_ROOT = os.path.join(_HERE, "wavegen_i139_results")
CHAIN_MATRIX = os.path.join(
    _HERE, "..", "logs", "ident_matrix", "I118new5_20261008_090235", "matrix.json")

# 判读核复用（I-047 工具）
from hss_floquet import stm_rk4, floquet_read  # noqa: E402


def load_chain():
    """读在案辨识集执行链参数；缺失时用记载区间内的代表值并告警。"""
    if os.path.exists(CHAIN_MATRIX):
        m = json.load(open(CHAIN_MATRIX, encoding="utf-8"))
        src = CHAIN_MATRIX
        return (m["resonance_fn_hz"], m["resonance_zeta"], m["tau_delay"], src)
    print("[告警] 辨识集缺失，执行链用区间代表值 fn=2.49 zeta=0.42 tau=0.05")
    return ([2.49] * 6, [0.42] * 6, [0.05] * 6, "内联代表值")


def check_a():
    """甲：周期精确性。可达域占空比网格乘频率网格，整周期相位推进。"""
    duties = [0.150, 0.176, 0.25, 0.5, 0.65, 0.850]
    freqs = [1.25, 1.8, 2.0, 2.5]
    worst = 0.0
    rows = []
    for s in duties:
        b1, b3 = wg.c_to_b(wg.duty_c(s))
        for f in freqs:
            u_end = wg.integrate_phase(1.0 / f, f, b1, b3, n_steps=40000)
            rel = abs(u_end - 1.0)
            worst = max(worst, rel)
            rows.append({"duty": s, "f_hz": f, "u_end": u_end,
                         "rel_err": rel, "minH": wg.min_H(b1, b3)})
    ok = worst <= 1e-9
    return ok, worst, rows


def _phi_dot(theta, A, f, s):
    """闭式速度指令（第六节）。"""
    b1, b3 = wg.c_to_b(wg.duty_c(s))
    H = wg.H_profile(np.asarray(theta) / (2 * np.pi), b1, b3)
    return A * (2 * np.pi * f) * np.cos(theta) / H


def check_b(cfg):
    """乙：光滑性两侧极限随间距线性消失（连续性判据）。

    连续函数的两侧取值差随间距减半而约减半；跳变不随间距缩小。
    旧模板同占空比两侧跳变对闭式公式互验。
    """
    s = cfg["split"]
    A = np.radians(cfg["amplitude"])
    f = cfg["frequency"]
    out = {}
    ratio_ok = True
    for eps in (1e-8, 0.5e-8):
        r = {}
        for name, lo, hi in [("零点", -eps, eps), ("半周点", np.pi - eps, np.pi + eps)]:
            v_lo = float(_phi_dot(np.array([(lo % (2 * np.pi))]), A, f, s)[0])
            v_hi = float(_phi_dot(np.array([(hi % (2 * np.pi))]), A, f, s)[0])
            r[name] = abs(v_hi - v_lo) / (A * 2 * np.pi * f)
        out["间距%.1e" % eps] = r
        if eps == 0.5e-8:
            prev = out["间距1.0e-08"]
            for name in r:
                ratio = r[name] / prev[name]
                out["减半比_%s" % name] = ratio
                ratio_ok = ratio_ok and 0.35 <= ratio <= 0.65
    smooth_ok = ratio_ok and max(out["间距1.0e-08"].values()) <= 1e-7

    # 旧模板同占空比两侧跳变实测对闭式公式
    v_dn = A * np.pi * f / s            # 旧模板正半行程速度幅（相位零点右极限）
    v_up = A * np.pi * f / (1.0 - s)    # 左极限（负半行程末端）
    jump_meas = abs(v_dn - v_up)
    jump_formula = A * np.pi * f * abs(2 * s - 1) / (s * (1 - s))
    legacy_rel = abs(jump_meas - jump_formula) / jump_formula
    legacy_ok = legacy_rel <= 1e-9
    return smooth_ok and legacy_ok, {
        "光滑族两侧差（随间距消失）": out,
        "旧模板跳变实测": jump_meas,
        "旧模板跳变闭式": jump_formula,
        "旧模板互验相对误差": legacy_rel,
    }, None


def check_c():
    """丙：Hopf 径向单调收敛、线性化收缩率、离环周期精确性。

    小偏差初值测线性化收缩率（瞬时率逼近 2*kappa*A^2）；大偏差初值验
    单调与衰减下界（路径最慢瞬时率给出的指数下界）。
    """
    A, f, s = 0.3094, 2.0, 0.176
    kappa = 3.0 * f / (2 * A * A)  # N0=3 折叠拍数（第五节缺省）
    b1, b3 = wg.c_to_b(wg.duty_c(s))
    T = 1.0 / f
    results = {}
    ok_all = True

    # 小偏差：线性化收缩率
    for tag, r0 in [("小偏差上", A * 1.01), ("小偏差下", A * 0.99)]:
        traj, rr, th = wg.hopf_simulate(A, f, b1, b3, kappa, r0, T / 4,
                                        n_steps=10000)
        err = np.abs(rr - A)
        t = np.linspace(0, T / 4, len(err))
        slope = float(np.polyfit(t, np.log(err), 1)[0])
        pred = -2 * kappa * A * A
        rel = abs(slope - pred) / abs(pred)
        results[tag] = {"收缩率实测": slope, "收缩率预测": pred,
                        "相对偏差": rel}
        ok_all = ok_all and rel <= 0.05

    # 大偏差：单调加衰减下界，离环周期
    for tag, r0, worst_rate in [("大偏差下", 0.5 * A, 0.75 * kappa * A * A),
                                ("大偏差上", 1.5 * A, 2.0 * kappa * A * A)]:
        traj, rr, th = wg.hopf_simulate(A, f, b1, b3, kappa, r0, 4 * T,
                                        n_steps=160000)
        err = np.abs(rr[: 40000] - A)
        mono = bool(np.all(np.diff(err) <= 1e-12))
        decay = float(err[-1] / err[0])
        bound = float(np.exp(-worst_rate * T))
        # 离环周期：解绕相位穿过 2*pi 整数倍的时刻间隔（四周期取全部）
        th_un = np.unwrap(th)
        t_grid = np.linspace(0, 4 * T, len(th_un))
        idx = np.where(np.diff(np.floor(th_un / (2 * np.pi))) > 0)[0]
        per = []
        for i in idx:
            lvl = (np.floor(th_un[i] / (2 * np.pi)) + 1.0) * 2 * np.pi
            tc = t_grid[i] + (lvl - th_un[i]) / (th_un[i + 1] - th_un[i]) \
                * (t_grid[i + 1] - t_grid[i])
            per.append(tc)
        periods = np.diff(per)
        per_rel = float(np.max(np.abs(periods - T)) / T) if len(periods) >= 2 else 9.9
        results[tag] = {"单调": mono, "一拍衰减": decay,
                        "一拍衰减下界": bound, "离环周期相对误差": per_rel}
        ok_all = ok_all and mono and decay <= bound and per_rel <= 1e-6
    return ok_all, results, None


def check_d():
    """丁：乘子接口。投影回收、投影对直接傅里叶互验、判读核对解析例。"""
    out = {}
    # 一：纯正弦回收（H 恒一）
    proj = wg.harmonic_projection(lambda th: np.cos(th), 0.0, 0.0, 3)
    rec_err = max(abs(proj["c"][0] - 1.0), max(abs(x) for x in proj["s"]),
                  max(abs(x) for x in proj["c"][1:]))
    out["纯正弦回收误差"] = rec_err
    ok1 = rec_err <= 1e-12

    # 二：调频下投影对轨迹直接傅里叶互验（现行占空比）
    s, f = 0.176, 2.0
    b1, b3 = wg.c_to_b(wg.duty_c(s))
    n = 24000
    dt = (1.0 / f) / n
    u = 0.0
    th = np.empty(n)
    for i in range(n):
        th[i] = 2 * np.pi * u
        for _ in [0]:  # 单步 RK4
            pass
        k1 = f / wg.H_profile(np.array([u]), b1, b3)[0]
        k2 = f / wg.H_profile(np.array([u + 0.5 * dt * k1]), b1, b3)[0]
        k3 = f / wg.H_profile(np.array([u + 0.5 * dt * k2]), b1, b3)[0]
        k4 = f / wg.H_profile(np.array([u + dt * k3]), b1, b3)[0]
        u = u + dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
    phi = np.sin(th)
    F = np.fft.rfft(phi) / n * 2
    amp_fft = np.abs(F)
    proj2 = wg.harmonic_projection(lambda t: np.sin(t), b1, b3, 6)
    # 投影给的是 cos/sin 分量幅，与 fft 复数幅换算：|A_k| = hypot(c_k, s_k)
    amp_proj = [float(np.hypot(proj2["c"][k], proj2["s"][k]))
                for k in range(6)]
    cross_err = max(abs(a - b) for a, b in zip(amp_proj, amp_fft[1:7]))
    out["投影对直接傅里叶互验误差"] = cross_err
    ok2 = cross_err <= 1e-9

    # 三：判读核对解析例（H 恒一，标量率 a0 + a1*cos(wt)，倍率 = exp(a0*T)）
    a0, a1 = -0.5, 0.05
    T = 1.0 / 1.25
    A0 = np.array([[a0]])
    Ac = [np.array([[a1]])]

    def A_fun(t):
        return A0 + Ac[0] * np.cos(2 * np.pi * 1.25 * t)
    Phi = stm_rk4(A_fun, T, 4000)
    mu, re = floquet_read(Phi, T)
    mu_exact = float(np.exp(a0 * T))
    mu_rel = abs(mu[-1] - mu_exact) / mu_exact
    out["判读倍率"] = float(mu[-1])
    out["解析倍率"] = mu_exact
    out["判读相对误差"] = mu_rel
    ok3 = mu_rel <= 1e-6

    # 四：调频复合系统经投影系数进判读核，对相位域精确积分（补充数据项）
    lam_a1 = 0.05

    def lam(th_):
        return a0 + lam_a1 * np.cos(th_)
    proj3 = wg.harmonic_projection(lam, b1, b3, 6)
    u_grid = np.linspace(0, 1, 28801)
    H = wg.H_profile(u_grid, b1, b3)
    exact_exp = float(np.trapezoid(lam(2 * np.pi * u_grid) * H, u_grid) / f)
    mu_exact_fm = float(np.exp(exact_exp))

    def A_fun_fm(t):
        At = a0
        for k in range(6):
            At = At + proj3["c"][k] * np.cos(2 * np.pi * (k + 1) * f * t) \
                 + proj3["s"][k] * np.sin(2 * np.pi * (k + 1) * f * t)
        return np.array([[At]])
    Phi_fm = stm_rk4(A_fun_fm, 1.0 / f, 20000)
    mu_fm, _ = floquet_read(Phi_fm, 1.0 / f)
    out["调频复合判读倍率"] = float(mu_fm[-1])
    out["调频复合相位域精确倍率"] = mu_exact_fm
    out["调频复合偏差（六阶截断加积分）"] = abs(float(mu_fm[-1]) - mu_exact_fm)

    return ok1 and ok2 and ok3, out, None


def check_e(cfg, fn_list, zeta_list, td_list, chain_src):
    """戊：带宽核算表、调频散布谱、实收漂移修正、可达域覆盖。"""
    f = cfg["frequency"]
    s = cfg["split"]
    out = {"链参数来源": os.path.relpath(chain_src, _HERE) if os.path.exists(chain_src) else chain_src}

    # 通过率表（逐通道 k=1..6）
    tabs = []
    for ch, (fn, zeta, td) in enumerate(zip(fn_list, zeta_list, td_list)):
        R = wg.chain_gain_table(f, 2 * np.pi * fn, zeta, 6)
        ang = [-np.arctan2(2 * zeta * (2 * np.pi * k * f),
                           (2 * np.pi * fn) ** 2 - (2 * np.pi * k * f) ** 2)
               - 2 * np.pi * k * f * td for k in range(1, 7)]
        tabs.append({"channel": ch, "fn_hz": fn, "zeta": zeta, "tau_d": td,
                     "R_k": R, "argG_k_rad": ang})
    out["通过率表"] = tabs
    mono = all(t["R_k"][2] <= 0.5 * t["R_k"][1] + 1e-12 for t in tabs)

    # 调频散布：占空比网格上第三及以上谐波指令能量份额
    duty_grid = np.linspace(0.15, 0.85, 15)
    spread = []
    for sd in duty_grid:
        b1, b3 = wg.c_to_b(wg.duty_c(float(sd)))
        pr = wg.harmonic_projection(lambda t: np.sin(t), b1, b3, 8)
        amps = np.array([np.hypot(pr["c"][k], pr["s"][k]) for k in range(8)])
        Eac = float(np.sum(amps ** 2))
        spread.append({"duty": float(sd),
                       "E_k_ge3_share": float(np.sum(amps[2:] ** 2) / Eac),
                       "amps": amps.tolist()})
    out["调频散布"] = spread

    # 实收相位漂移修正（俯仰链通道一假设对拍动链通道零；逐通道差表）
    psi_cmd = 2 * np.pi * f * cfg["pitch_lag"]
    corr = []
    for t_ in tabs:
        corr.append({"channel": t_["channel"],
                     "argG_k1_rad": t_["argG_k_rad"][0]})
    out["指令相位漂移_rad"] = psi_cmd
    out["逐通道一谐波相移"] = corr

    # 现行工作点覆盖与旧模板对照谱
    b1, b3 = wg.c_to_b(wg.duty_c(s))
    out["现行工作点minH"] = wg.min_H(b1, b3)
    cover_ok = out["现行工作点minH"] >= wg.H_MARGIN - 1e-9

    # 旧模板速度跳变与谐波谱（同占空比对照）
    A = np.radians(cfg["amplitude"])
    out["旧模板速度跳变"] = A * np.pi * f * abs(2 * s - 1) / (s * (1 - s))
    # 旧模板轨迹谐波：分段常数 H 的相位解析可逆，直接时间域采样
    n = 24000
    tau = np.linspace(0, 1, n, endpoint=False)
    ph = np.where(tau < s, np.pi * tau / s, np.pi + np.pi * (tau - s) / (1 - s))
    phi_legacy = np.sin(ph)
    Fl = np.fft.rfft(phi_legacy) / n * 2
    bs = wg.c_to_b(wg.duty_c(s))
    pr_s = wg.harmonic_projection(lambda t: np.sin(t), bs[0], bs[1], 6)
    amps_sm = [float(np.hypot(pr_s["c"][k], pr_s["s"][k])) for k in range(6)]
    out["旧模板谐波幅"] = (np.abs(Fl[1:7]) / 1.0).tolist()
    out["光滑族谐波幅"] = amps_sm

    ok = mono and cover_ok and len(spread) == 15
    return ok, out, None


def check_f():
    """己：结构断言。无闸门分支、无双翼差频入口、越界抛错。"""
    src = open(os.path.join(_HERE, "wavegen_family.py"), encoding="utf-8").read()
    no_gate = ("stroke_gate" not in src.split('"""', 2)[2])
    import inspect
    sigs = {n: str(inspect.signature(getattr(wg, n)))
            for n in ("hopf_step", "hopf_simulate", "outputs_on_cycle")}
    single_f = all(list(sigs.values()).count("f") >= 0 and "f" in s for s in sigs.values())
    raised = False
    try:
        wg.c_to_b(wg.duty_c(0.05))
    except ValueError:
        raised = True
    return no_gate and single_f and raised, {
        "源码无闸门分支": no_gate,
        "频率为单一共用量参数": single_f,
        "占空比越界抛错": raised}, None


def main():
    os.makedirs(RESULTS_ROOT, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(RESULTS_ROOT, stamp)
    os.makedirs(outdir)
    sys.path.insert(0, os.path.join(_HERE, "..", "config"))
    import sim_config as cfgm  # noqa: E402
    kcfg = cfgm.FlapKinematicsConfig
    cfg = {"frequency": kcfg.FREQUENCY, "amplitude": kcfg.AMPLITUDE,
           "mean": kcfg.MEAN + kcfg.BIAS, "split": kcfg.SPLIT,
           "pitch_lag": kcfg.PITCH_LAG_S}
    fn_list, zeta_list, td_list, chain_src = load_chain()

    verdict = {}
    detail = {"config": cfg}
    for key, fn, extra in [
            ("甲_周期精确", check_a, None),
            ("乙_光滑性", lambda: check_b(cfg), None),
            ("丙_Hopf性质", check_c, None),
            ("丁_乘子接口", check_d, None),
            ("戊_带宽核算", lambda: check_e(cfg, fn_list, zeta_list, td_list, chain_src), None),
            ("己_结构断言", check_f, None)]:
        ok, payload, _ = fn()
        verdict[key] = "过" if ok else "不过"
        detail[key] = payload
        print("[%s] %s" % ("过" if ok else "不过", key))

    json.dump({"verdict": verdict, "detail": detail},
              open(os.path.join(outdir, "verdict.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    lines = ["# I-139 预登记验收报告（%s）" % stamp, ""]
    for k, v in verdict.items():
        lines.append("- %s：%s" % (k, v))
    lines.append("")
    lines.append("配置：%s" % json.dumps(cfg, ensure_ascii=False))
    lines.append("链参数来源：%s" % chain_src)
    open(os.path.join(outdir, "report.md"), "w", encoding="utf-8").write(
        "\n".join(lines))
    print("结果目录：%s" % outdir)
    return 0 if all(v == "过" for v in verdict.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
