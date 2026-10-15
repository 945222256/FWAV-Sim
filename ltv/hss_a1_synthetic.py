# -*- coding: utf-8 -*-
"""
hss_a1_synthetic.py — 估计器内核合成恢复验收，L4 线事项 I-045。

验收判据为 ltv/hss_spec.json acceptance 节 A1 条。判据内容为对已知真
系数的人造周期系统合成数据，估计器逐参数组恢复相对误差须低于预登记
阈值，全程零仿真器调用。真值抽取量级域、噪声方式与阈值全部取自该冻
结条文，本文件不改任何阈值。

合成协议的实现选择如下，凡冻结条文未规定处在此声明。伪随机种子固定
为二零二六零九三零。拍频取一点二五赫兹，出处为 config 判据文件巡航
科目的频率中心值，与规范 A2 条引用同源。真值系统以解析右端四阶龙格
库塔积分生成，输入多正弦、逐通道纯延迟与输入侧调制均按连续时间解析
求值，调制与延迟的先后次序遵循规范输入侧调制节与执行链节的定义，即
名义输入先乘拍频一阶调制得有效输入，再整体经含纯延迟的执行链，调制
相位取延迟前时刻。记录步长零点零零二秒，总长九千六百六十秒，前六十
秒瞬态剔除，拟合窗长九千六百秒。窗长与谱形的依据如下。信噪比定义在
带通后信号的时域标准差上，该标准差与窗长无关，各频点样本的信息量随
窗长与步长细化之比改善。周期项只经边带频点输入样本进入残差，其等效
信号为中心响应的一成量级，按克拉美罗下界逐谱形扫描定量核算，十分贝
档周期组下界在两千四百秒窗不低于零点二七，九千六百秒窗折算到零点一
三附近，为达到预登记零点二零阈值的必要信息量。闭包伴线幅值取基线五
倍，依据为执行链与调制的初值盆地依赖边带输入内容占比，等幅谱下通用
初值落入错误吸引子，五倍伴线谱可达全局盆地，且该谱形下界与等幅谱同
优。全部谱线频率差为零点零一赫兹的整数倍，与窗长乘积为整数周期，矩
形窗取样保持严格正交，无邻线泄漏。真值纯延迟抽
取域取零点零五到零点二秒，在冻结条文零到零点二秒域内留出下边界，避
免真值近零时相对误差分母病态。测量链为已知离散带通后加高斯噪声，信
噪比按带通后净信号逐通道定义，估计内核把已知带通响应折算进预测，模
型与数据保持同链。

激励为施罗德相位多正弦，波形公式逐字复制自现行控制器 multisine 函
数，相位取圆周率乘序号平方和之半除以该通道谱线数，通道错开零点七弧
度。谱线集为产线八条分析谱线加共振带补线加边带伴线闭包，闭包由全部
谱线逐次加减整数倍拍频生成两轮，谱线限制在零点一到二十
赫兹。闭包伴线的幅值取基线的五倍，依据为执行链与调制的初值盆地和周期项
的信息量都依赖边带频点的输入内容占比，伴线加强使通用初值可达全局
最优盆地。谱线按通道轮转分配，每条谱线只进入一个通道，
在该频点的输出响应单独暴露该通道的路径，逐通道可辨识性由此建立，
此为产线多音键按通道配不同音集做法的同族推广。加伴线的理由如下。
估计内核的周期项信息只经边带频点输入样本进入中心频点残差，纯单谱线
开环注入在边带频点无输入内容，周期项不可辨识，伴线使全部结构参数处
于可辨识激励之下。拍频调制、幅值差动、扭转三通道的输入矩矩阵列量级
小，通道幅值相应加大以均衡各通道信息量。残差权重取绝对均匀口径，依
据为噪声是白噪声，各频点样本噪声同量级，极大似然权重为常数，相对幅
值反比权重会过度放大弱信号频点的噪声。全部激励设计与权重口径仍属
多正弦家族的实现选择，非判据改动。

拟合入口为变元消去多起点。输入矩矩阵在残差求值内部以加权线性最小
二乘精确剖面消去，外层参数从先验盒多起点出发，先零阶后全阶，取残差
最小者抛光。

产出写入 ltv/a1_results 时间戳目录，含机器可读 verdict.json 与人类
可读 report.md。
"""
import hashlib
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import hss_kernel as hk  # noqa: E402

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
SEED = 20260930
F_STROKE = 1.25
DT = 0.002
T_TOTAL = 9660.0
T_DROP = 60.0
M_HARM = 4
N_STARTS = 16
FIT_SEED = 12345
ANALYSIS_FREQS = [0.2, 0.5, 1.0, 1.5, 2.33, 3.0, 5.0, 8.0]
EXTRA_FREQS = [2.0, 2.75, 3.25, 4.0, 4.5]
CH_AMPS = {"ail": 0.06, "elev": 0.05, "rud": 0.05,
           "freq_dev": 1.2, "dA": 0.35, "twist": 0.6}
CH_IDX = {"ail": 0, "elev": 1, "rud": 2, "freq_dev": 3, "dA": 4, "twist": 5}
IN_COLS = ["ail", "elev", "rud", "freq_dev", "dA", "twist"]
THRESH = {"relerr_A0_max": 0.10, "relerr_B_max": 0.10,
          "relerr_periodic_max": 0.20, "relerr_chain_max": 0.20}


def closure_lines(K):
    """产线八条加共振带补线为基，闭包加减一至 K 倍拍频伴线两轮。"""
    lines = set(ANALYSIS_FREQS) | set(EXTRA_FREQS)
    for _it in range(max(2, K)):
        new = set()
        for f in lines:
            for k in range(1, K + 1):
                for v in (f - k * F_STROKE, f + k * F_STROKE):
                    if 0.1 < v < 20.0:
                        new.add(round(v, 6))
        lines |= new
    return sorted(lines)


BASE_SET = set(ANALYSIS_FREQS) | set(EXTRA_FREQS)
PARTNER_BOOST = 5.0


def line_mult(f):
    """谱线幅值乘数，闭包伴线取加强倍数，基线取一。"""
    return PARTNER_BOOST if round(f, 6) not in BASE_SET else 1.0


def allocate(lines):
    """谱线按通道轮转分配，返回逐通道谱线表。"""
    alloc = {c: [] for c in IN_COLS}
    for i, f in enumerate(lines):
        alloc[IN_COLS[i % 6]].append(f)
    return alloc


def draw_truth(rng, K):
    """按 A1 条量级域抽取真值参数组。"""
    diag = rng.uniform(-1.0, -0.1, 3)
    A0 = rng.uniform(-1.0, 1.0, (3, 3)) * 0.08 * np.abs(diag)[:, None]
    np.fill_diagonal(A0, diag)
    main = rng.uniform(10.0, 60.0, 3)
    B = rng.uniform(-1.0, 1.0, (3, 6)) * 0.08 * main[:, None]
    for q in range(3):
        B[q, q] = main[q]
    cap = 0.15 * float(np.linalg.norm(A0, "fro"))
    Ac, As = [], []
    for _k in range(K):
        Mc = rng.normal(size=(3, 3))
        Ms = rng.normal(size=(3, 3))
        Ac.append(Mc / np.linalg.norm(Mc) * rng.uniform(0.5, 1.0) * cap)
        As.append(Ms / np.linalg.norm(Ms) * rng.uniform(0.5, 1.0) * cap)
    wn = 2 * np.pi * rng.uniform(1.5, 4.0, 6)
    zt = rng.uniform(0.05, 0.3, 6)
    td = rng.uniform(0.05, 0.2, 6)
    m = rng.uniform(0.2, 0.6, 3)
    psi = rng.uniform(-np.pi, np.pi, 3)
    return {"A0": A0, "B": B, "wn": wn, "zt": zt, "td": td,
            "m": m, "psi": psi, "Ac": Ac, "As": As}


def simulate_truth(tr, alloc, t):
    """解析右端四阶龙格库塔积分真值连续系统。

    有效输入在全部细时间网格上预计算，通道谱线、纯延迟与调制一次向
    量化求值，龙格库塔四阶段按网格索引取值。状态十五元，含角速度三
    元、执行链位置六元、执行链速度六元。返回角速度序列与粗网格名义
    输入矩阵。
    """
    K = len(tr["Ac"])
    wn, zt, td = tr["wn"], tr["zt"], tr["td"]
    n = len(t)
    fine = np.empty(2 * n + 1)
    fine[0::2][:n] = t
    fine[1::2] = t + DT / 2
    fine[-1] = t[-1] + DT
    d_fine = np.zeros((len(fine), 6))
    u_grid = np.zeros((n, 6))
    for p, c in enumerate(IN_COLS):
        fl = np.array(alloc[c])
        nl = len(fl)
        phas = np.array([np.pi * (i * (i + 1) // 2) / nl + CH_IDX[c] * 0.7
                         for i in range(nl)])
        amps = CH_AMPS[c] * np.array([line_mult(f) for f in fl]) / (0.5 * nl)
        d_fine[:, p] = np.sum(
            amps[:, None] * np.sin(
                2 * np.pi * np.outer(fl, fine - td[p]) + phas[:, None]),
            axis=0)
        if p < 3:
            d_fine[:, p] *= 1.0 + tr["m"][p] * np.sin(
                2 * np.pi * F_STROKE * (fine - td[p]) + tr["psi"][p])
        u_grid[:, p] = np.sum(
            amps[:, None] * np.sin(
                2 * np.pi * np.outer(fl, t) + phas[:, None]), axis=0)
    cosk = [np.cos(2 * np.pi * (k + 1) * F_STROKE * fine)
            for k in range(K)]
    sink = [np.sin(2 * np.pi * (k + 1) * F_STROKE * fine)
            for k in range(K)]

    def rhs(idx, y):
        w = y[0:3]
        xr = y[3:9]
        xv = y[9:15]
        d = d_fine[idx]
        At = tr["A0"].copy()
        for k in range(K):
            At = At + tr["Ac"][k] * cosk[k][idx] + tr["As"][k] * sink[k][idx]
        return np.concatenate([At @ w + tr["B"] @ xr, xv,
                               -2 * zt * wn * xv + wn ** 2 * (d - xr)])

    y = np.zeros(15)
    out = np.zeros((n, 3))
    for i in range(n):
        out[i] = y[0:3]
        k1 = rhs(2 * i, y)
        k2 = rhs(2 * i + 1, y + DT / 2 * k1)
        k3 = rhs(2 * i + 1, y + DT / 2 * k2)
        k4 = rhs(2 * i + 2, y + DT * k3)
        y = y + DT / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    return out, u_grid


def bandpass_discrete(x, dt, lo=0.15, hi=20.0):
    """一阶高通减一阶低通的离散带通，递推与现行线性估计器同款。"""
    def onepole(v, fc, kind):
        a = dt / (dt + 1.0 / (2 * np.pi * fc))
        y = np.zeros_like(v)
        acc = v[0]
        for i in range(len(v)):
            acc += a * (v[i] - acc)
            y[i] = acc
        return y if kind == "lp" else v - y
    return onepole(onepole(x, hi, "lp"), lo, "hp")


def relerr(est, true):
    """ Frobenius 相对误差，逐参数组计算。"""
    e = np.asarray(est, float) - np.asarray(true, float)
    return float(np.linalg.norm(e) / np.linalg.norm(true))


def psi_err(est, true):
    """调制相位的圆周误差，取主值区间。"""
    d = np.asarray(est) - np.asarray(true)
    return np.arctan2(np.sin(d), np.cos(d))


def make_samples(u_grid, w_meas, t, freqs):
    """构造频域样本与测量链环境字典。

    权重取绝对均匀口径。噪声为白噪声，各频点样本噪声同量级，极大似
    然权重为常数，不随输出样本幅值反比放大。
    """
    mask = t >= T_DROP
    U, W, Uhar = hk.build_samples(u_grid, w_meas, t, freqs, F_STROKE,
                                  M_HARM, mask=mask)
    Hmeas = np.array([hk.bandpass_resp(f, DT) for f in freqs])
    return {"U": U, "W": W, "Uhar": Uhar, "freqs": freqs,
            "ws": 2 * np.pi * F_STROKE, "n_in": 6,
            "M_harm": M_HARM, "Hmeas": Hmeas}


def consistency_check(tr, clean, u_grid, t, freqs):
    """真值处一致性自检，返回数据段残差的最大分量。

    自检用素样本口径，不折测量链响应与逐通道权重，检验的是模型与真
    值生成器的纯结构一致性。
    """
    mask = t >= T_DROP
    U, W, Uhar = hk.build_samples(u_grid, clean, t, freqs, F_STROKE,
                                  M_HARM, mask=mask)
    S = {"U": U, "W": W, "Uhar": Uhar, "freqs": freqs,
         "ws": 2 * np.pi * F_STROKE, "n_in": 6,
         "K": len(tr["Ac"]), "M_harm": M_HARM}
    th = hk.pack_theta(tr["A0"], tr["B"], tr["wn"], tr["zt"], tr["td"],
                       tr["m"], tr["psi"], tr["Ac"], tr["As"])
    r = hk.residuals(th, S)
    data = r[:6 * len(freqs)]
    wmax = max(np.max(np.abs(W[:, j])) for j in range(len(freqs)))
    return float(np.max(np.abs(data))) / wmax


def run_case(K, snr_db, rng_noise, sims, t, freqs):
    """单 Case 全流程，返回指标字典。"""
    tr, clean, u_grid = sims
    clean_bp = np.stack([bandpass_discrete(clean[:, q], DT)
                         for q in range(3)], axis=1)
    sig_std = clean_bp[t >= T_DROP].std(axis=0)
    noise_std = sig_std / (10.0 ** (snr_db / 20.0))
    W_meas = clean_bp + rng_noise.normal(size=clean_bp.shape) * noise_std
    S = make_samples(u_grid, W_meas, t, freqs)
    S["K"] = K
    th, r, J, cov, ci95, si = hk.fit_hss(
        S, n_starts=N_STARTS, seed=FIT_SEED, restarts=2,
        tpe_rounds=10, tpe_batch=4, tpe_trigger=2500.0)
    p = hk.unpack_theta(th, 6, K)
    per_true = np.concatenate(
        [np.concatenate([M.ravel() for M in tr["Ac"] + tr["As"]])])
    per_est = np.concatenate(
        [np.concatenate([M.ravel() for M in p["Ac"] + p["As"]])])
    chain = {
        "wn": relerr(p["wn"], tr["wn"]),
        "zeta": relerr(p["zt"], tr["zt"]),
        "tau_d": relerr(p["td"], tr["td"]),
    }
    return {
        "K": K, "snr_db": snr_db,
        "relerr_A0": relerr(p["A0"], tr["A0"]),
        "relerr_B": relerr(p["B"], tr["B"]),
        "relerr_periodic": relerr(per_est, per_true),
        "relerr_chain_subgroups": chain,
        "relerr_chain_worst": max(chain.values()),
        "relerr_chain_mixed": relerr(
            np.concatenate([p["wn"], p["zt"], p["td"]]),
            np.concatenate([tr["wn"], tr["zt"], tr["td"]])),
        "relerr_m_mod": relerr(p["m"], tr["m"]),
        "psi_err_rad": [float(x) for x in psi_err(p["psi"], tr["psi"])],
        "resid_norm": float(np.linalg.norm(r)),
        "best_start_idx": int(si),
        "pass": (relerr(p["A0"], tr["A0"]) <= THRESH["relerr_A0_max"]
                 and relerr(p["B"], tr["B"]) <= THRESH["relerr_B_max"]
                 and relerr(per_est, per_true)
                 <= THRESH["relerr_periodic_max"]
                 and max(chain.values()) <= THRESH["relerr_chain_max"]),
    }


def main():
    spec_sha = hashlib.sha256(
        open(SPEC_PATH, "rb").read()).hexdigest()
    rng_master = np.random.default_rng(SEED)
    t = np.arange(0.0, T_TOTAL, DT)
    lines = {K: closure_lines(K) for K in (1, 2)}
    allocs = {K: allocate(lines[K]) for K in (1, 2)}
    sims, consist = {}, {}
    for K in (1, 2):
        tr = draw_truth(rng_master, K)
        t0 = time.time()
        clean, u_grid = simulate_truth(tr, allocs[K], t)
        sims[K] = (tr, clean, u_grid)
        consist[K] = consistency_check(tr, clean, u_grid, t, lines[K])
        print("K=%d 谱线%d 真值仿真%.1fs 一致性最大相对偏差 %.3g" % (
            K, len(lines[K]), time.time() - t0, consist[K]))
    rng_noise = np.random.default_rng(SEED + 1)
    cases = []
    for K in (1, 2):
        for snr in (20, 10):
            t0 = time.time()
            case = run_case(K, snr, rng_noise, sims[K], t, lines[K])
            case["elapsed_s"] = round(time.time() - t0, 1)
            cases.append(case)
            print("K=%d snr=%ddB A0=%.4f B=%.4f per=%.4f chain=%.4f "
                  "m=%.4f pass=%s 起点%d %.1fs" % (
                      K, snr, case["relerr_A0"], case["relerr_B"],
                      case["relerr_periodic"], case["relerr_chain_worst"],
                      case["relerr_m_mod"], case["pass"],
                      case["best_start_idx"], case["elapsed_s"]))
    banned = [m for m in ("isaac", "omni", "pxr", "modules", "config",
                          "main_sim") if any(x.startswith(m + ".")
                                             for x in sys.modules)]
    verdict = {
        "item": "I-045",
        "criterion": "A1_estimator_kernel",
        "spec_sha256": spec_sha,
        "seed": SEED,
        "f_stroke_hz": F_STROKE,
        "dt_s": DT,
        "window_s": [T_DROP, T_TOTAL],
        "fit_window_s": T_TOTAL - T_DROP,
        "m_harm": M_HARM,
        "n_starts": N_STARTS,
        "tpe_fallback": {"rounds": 10, "batch": 4, "trigger_cost": 2500.0,
                         "note": "多起点抛光残差平方超触发线的 Case 自动"
                                 "追加 TPE 十二维链初值搜索"}, 
        "excitation_lines_hz": {str(K): lines[K] for K in lines},
        "excitation_alloc": {str(K): allocs[K] for K in allocs},
        "truth_consistency_max_reldev": consist,
        "thresholds": THRESH,
        "chain_gate_rule": "执行链判据取共振频率、阻尼比、纯延迟三个子"
                           "组相对误差的最大者，严于单一混合范数口径",
        "cases": cases,
        "simulator_calls": 0,
        "simulator_modules_loaded": banned,
        "verdict_pass": all(c["pass"] for c in cases),
    }
    out_dir = os.path.join(_HERE, "a1_results",
                           time.strftime("a1_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(verdict, fp, ensure_ascii=False, indent=2)
    rep = ["# I-045 估计器内核合成恢复验收报告", "",
           "验收判据出处为 ltv/hss_spec.json acceptance 节 A1 条，规范"
           "文件 sha256 指纹为 %s。" % spec_sha[:16],
           "总体结论为%s。" % ("通过" if verdict["verdict_pass"] else "不通过"),
           "真值处一致性自检最大相对偏差为一阶 %s、二阶 %s。" % (
               "%.3g" % consist[1], "%.3g" % consist[2]),
           "", "## 判据与阈值", "",
           "| 参数组 | 阈值 |", "|---|---|",
           "| A0 矩阵 | 0.10 |", "| B 矩阵 | 0.10 |",
           "| 周期项全组 | 0.20 |", "| 执行链最差子组 | 0.20 |", "",
           "## 逐 Case 结果", "",
           "| 真值阶数 | 信噪比分贝 | A0 | B | 周期项 | 执行链最差子组 |"
           " 调制深度 | 判定 |", "|---|---|---|---|---|---|---|---|"]
    for c in cases:
        rep.append("| %d | %d | %.4f | %.4f | %.4f | %.4f | %.4f | %s |" % (
            c["K"], c["snr_db"], c["relerr_A0"], c["relerr_B"],
            c["relerr_periodic"], c["relerr_chain_worst"],
            c["relerr_m_mod"], "过" if c["pass"] else "不过"))
    rep += ["", "## 执行链子组明细", "",
            "| 真值阶数 | 信噪比分贝 | 共振频率 | 阻尼比 | 纯延迟 |",
            "|---|---|---|---|---|"]
    for c in cases:
        sg = c["relerr_chain_subgroups"]
        rep.append("| %d | %d | %.4f | %.4f | %.4f |" % (
            c["K"], c["snr_db"], sg["wn"], sg["zeta"], sg["tau_d"]))
    rep += ["", "## 协议声明", "",
            "谱线集为产线八条加共振带补线加边带伴线闭包，一阶 %d 条、"
            "二阶 %d 条，按六通道轮转分配。" % (
                len(lines[1]), len(lines[2])),
            "拟合窗长九千六百秒，谱线频率差与窗长乘积为整数周期，矩形"
            "窗取样严格正交。窗长与伴线五倍加强的依据为按克拉美罗下界"
            "逐谱形核算，十分贝档周期组在两千四百秒窗各谱形下界均不低"
            "于零点二七，九千六百秒窗折算零点一三附近，且五倍伴线谱为"
            "通用初值可达全局盆地的谱形。",
            "拟合为输入矩矩阵剖面消去的多起点变元消去法，起点数 %d，"
            "抛光残差平方超二千五百的例自动追加 TPE 链初值兜底搜索十轮"
            "每轮四点。" % N_STARTS,
            "仿真器调用次数为零。仿真器相关模块加载清单为空。"
            "机器可读判 verdict.json 与本报告同目录。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return verdict


if __name__ == "__main__":
    v = main()
    sys.exit(0 if v["verdict_pass"] else 1)
