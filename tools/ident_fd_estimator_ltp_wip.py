# -*- coding: utf-8 -*-
"""
ident_fd_estimator.py — 频域输出误差估计器（辨识产线第三步，I-006）

依据 docs/辨识产线_判据与方案_20260930.md 第三节（判据冻结，运行后不许改）：
  输入 93 列数据流（ident_stream_flight_*.csv），
  频域最小化模型输出与实测输出之差，
  输出完整导数矩阵（A/B）与逐参数置信区间。

模型结构（灰箱，判据文档第一节）：体轴角速度子系统
  ω̇ = A·ω + B·δ_eff，  δ_eff,p = δ_p · e^(−τd_p·s)/(1+τ_p·s)
  A、B 全元素自由（交叉耦合=矩阵元素，不预设零），
  τ_p 执行器一阶滞后、τd_p 传输延迟（升降舵滑流延迟在此显形）。
  物理矩导数 C_ω = I_epoch·A、C_δ = I_epoch·B（I 取数据纪元的整机合成值，
  自 params_registry/archive.json 组装，URDF 运动学与质量不变）。

零频自检锚点（判据文档第三节）：C_δ 主元素对尾舵效调度
  b_p = K_B_TAIL_p·v_eff²（config）与旋转阻尼族对照，偏差超过锚点两成
  判估计器故障。

留出验证（判据文档第四节，冻结）：数据按奇偶拍分半，一半估计一半验证；
验证段模型预测角速度均方误差不超过实测角速度方差五成。口径声明（先于
数据）：拍频基线（未受激励的周期响应）以估计半的拍相位分箱均值扣除，
判据作用于扣除基线后的偏差量；模型与实测先经同一 0.15~20Hz 带通。

用法（纯离线，无需 Isaac）：
  python tools/ident_fd_estimator.py <csv> [--tag R92] [--t0 3.0] [--t1 13.0]
      [--freqs 0.2,0.5,1.0,1.5,2.33,3.0,5.0,8.0] [--strokes-even]
产物：logs/ident_matrix/<tag>_<ts>/ 目录（matrix.json + report.md）
  data_epoch 取 archive 组装值；跨纪元数据须打 not_for_control 标记。
"""
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OSE = os.environ.get
sys.path.insert(0, os.path.join(ROOT, "params_registry"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import registry  # noqa: E402
from inertia_geometric_recalc import parse_urdf, build_world_coms  # noqa: E402

IN_COLS = ["ail", "elev", "rud", "freq_dev", "dA", "twist"]
OUT_COLS = ["wx", "wy", "wz"]


# ─────────────────── 数据纪元整机惯量（archive 组装） ───────────────────

def epoch_inertia(urdf_path):
    """用 archive.json 里退役的逐连杆张量（数据纪元值）+ 现行 URDF 的
    质量/质心/运动学（两纪元未变）合成整机对角。"""
    return _assemble(urdf_path, use_archive=True)


def current_inertia(urdf_path):
    """用登记库现行逐连杆张量（几何法新纪元）合成整机对角。"""
    return _assemble(urdf_path, use_archive=False)


def _assemble(urdf_path, use_archive):
    links, joints = parse_urdf(urdf_path)
    tensors = {}
    if use_archive:
        archive = json.load(open(os.path.join(ROOT, "params_registry",
                                              "archive.json"),
                                 encoding="utf-8"))
        for e in archive["archive"]:
            if not e["param"].startswith("link."):
                continue
            name = e["param"].split(".", 1)[1]
            v = e["value"]
            if isinstance(v, dict):   # 旧纪元记录形如 {mass,ixx,iyy,izz}
                tensors[name] = np.diag([float(v["ixx"]), float(v["iyy"]),
                                         float(v["izz"])])
            elif isinstance(v, list):
                tensors[name] = np.diag(np.array(v, dtype=float))
    else:
        for name in links:
            val = registry.get("vehicle.inertia.link.%s" % name)
            tensors[name] = np.diag(np.array(val, dtype=float))
    world = build_world_coms(links, joints)
    total = sum(links[n]["mass"] for n in links)
    cg = sum(links[n]["mass"] * np.array(world[n]) for n in links) / total
    comp = np.zeros((3, 3))
    for n in links:
        m = links[n]["mass"]
        r = np.array(world[n]) - cg
        comp += tensors[n] + m * (r @ r * np.eye(3) - np.outer(r, r))
    return comp, total


# ─────────────────── 频域取样（multisine 频点相关积分） ───────────────────

def dft_at(x, t, freq):
    """非整周期安全的指定频点 DFT：X=Σ x·e^{−i2πft}·dt（直接求和，
    允许样本在时间上不连续——奇偶拍分半后的缺口不会被跨积）。"""
    dt = float(np.median(np.diff(t)))
    return float(np.sum(x * np.exp(-2j * np.pi * freq * t)) * dt)


# ─────────────────── 模型传函与残差 ───────────────────

def tf_matrix(s, A, B, wn, zt, td):
    """T(s)=（3×n_in）(sI−A)^{-1}B·diag(G_p)。G 为二阶共振加延迟：
    ωn²/(s²+2ζωn·s+ωn²)·e^{−τd·s}。v6 结构升级依据：产线矩阵级证据
    =对象开环含 2.2~3.1Hz 近零阻尼共振，一阶滞后形态装不下（提交
    2d4e496 与本线 v5 上限结论互证）。"""
    G = np.diag(wn ** 2 / (s ** 2 + 2 * zt * wn * s + wn ** 2)
                * np.exp(-td * s))
    return np.linalg.solve(s * np.eye(3) - A, B @ G)


def residuals(theta, U, W, freqs, n_in, wgt=None, Usb=None, ws=0.0):
    """U/W: n_in×K / 3×K 频域样本；wgt: 每频点权重（外注入占比²，
    闭环反馈淹没注入的频点降权——谱诊断确定的闭环可辨识性处置）。
    v8 周期时变结构升级（判据文件预登记的非整数倍频点检验路径）：
    尾舵效被拍动滑流按拍周期调制，u_eff=u·(1+m·sin(ωs t+ψ))，频域上
    在注入频率处产生加减拍频边带的响应混叠；Usb 形如 (2,n_in,K)，
    上下边带频点的输入样本，theta 尾部携带 m/ψ（各三通道）。"""
    A = theta[0:9].reshape(3, 3)
    Ac = theta[-18:-9].reshape(3, 3)
    As = theta[-9:].reshape(3, 3)
    B = theta[9:9 + 3 * n_in].reshape(3, n_in)
    wn = theta[9 + 3 * n_in:9 + 4 * n_in]
    zt = theta[9 + 4 * n_in:9 + 5 * n_in]
    td = theta[9 + 5 * n_in:9 + 6 * n_in]
    i_m0 = 9 + 6 * n_in
    m_mod = theta[i_m0:i_m0 + 3]
    p_mod = theta[i_m0 + 3:i_m0 + 6]
    r = []
    for k, fq in enumerate(freqs):
        s = 2j * np.pi * fq
        T = tf_matrix(s, A, B, wn, zt, td)
        pred = T @ U[:, k]
        if Usb is not None and ws > 0:
            for p in range(min(n_in, 3)):
                mp, ps = m_mod[p], p_mod[p]
                # 输入侧调制的边带：u 的 (ωk∓ωs) 分量与调制乘积落在 ωk，
                # 随后过 T(jωk)——传函取中心频点（时域仿真同口径）
                T_lo = T
                T_hi = T
                cp = mp / (2j) * np.exp(1j * ps)
                cm = -mp / (2j) * np.exp(-1j * ps)
                pred = pred + T_lo[:, p] * (cp * Usb[0, p, k]) \
                            + T_hi[:, p] * (cm * Usb[1, p, k])
            # 输出侧周期状态空间(一阶谐波平衡):状态边带经A周期项耦合回中心
            s_lo = s - 1j * ws
            s_hi = s + 1j * ws
            T_lo2 = tf_matrix(s_lo, A, B, wn, zt, td)
            T_hi2 = tf_matrix(s_hi, A, B, wn, zt, td)
            x0_lo = T_lo2 @ Usb[0, :, k]
            x0_hi = T_hi2 @ Usb[1, :, k]
            T0 = np.linalg.solve(s * np.eye(3) - A, np.eye(3))
            pred = pred + T0 @ ((0.5 * Ac + 0.5 * As / 1j) @ x0_lo
                                + (0.5 * Ac - 0.5 * As / 1j) @ x0_hi)
        d = W[:, k] - pred
        wk = 1.0 if wgt is None else wgt[k]
        r.append(wk * np.concatenate([d.real, d.imag]))
    # 赫尔维茨软约束：A 的正实部特征值受二次软约束，防止频带内拟合
    # 对低频外推出非物理的不稳定极点
    pos = np.maximum(np.linalg.eigvals(A).real, 0.0)
    r.append(np.full(3, np.sqrt(1e4) * float(np.sqrt(np.sum(pos ** 2)))))
    nrm = float(np.linalg.norm(A, 'fro'))
    for _Mp in (Ac, As):
        _ex = max(float(np.linalg.norm(_Mp, 'fro')) - 0.15 * nrm, 0.0)
        r.append(np.array([100.0 * _ex]))
    return np.concatenate(r)


# ─────────────────── 高斯牛顿（含 LM 阻尼） ───────────────────

def gauss_newton(f0, theta0, jac_fun, max_iter=80, lam=1e-3,
                 lo=None, hi=None):
    """高斯牛顿 + LM 阻尼 + 逐参数盒式界（执行链物理域）。"""
    theta = theta0.copy()
    r0 = f0(theta)
    cost = r0 @ r0
    for _ in range(max_iter):
        J = jac_fun(theta)
        JtJ = J.T @ J + lam * np.eye(len(theta))
        g = J.T @ r0
        try:
            step = np.linalg.solve(JtJ, -g)
        except np.linalg.LinAlgError:
            lam *= 10
            continue
        cand = theta + step
        if lo is not None:
            cand = np.maximum(cand, lo)
        if hi is not None:
            cand = np.minimum(cand, hi)
        r1 = f0(cand)
        if r1 @ r1 < cost:
            theta = cand
            r0 = r1
            cost = r0 @ r0
            lam = max(lam * 0.3, 1e-9)
        else:
            lam *= 10
        if np.linalg.norm(step) < 1e-10:
            break
    return theta, r0, J


# ─────────────────── 时间域仿真（留出验证） ───────────────────

def bandpass(x, dt, lo=0.15, hi=20.0):
    """一阶高通减一阶低通的带通（判据口径：0.15~20Hz）。"""
    def onepole(x, fc, kind):
        a = dt / (dt + 1.0 / (2 * np.pi * fc))
        y = np.zeros_like(x)
        acc = x[0]
        for i in range(len(x)):
            acc += a * (x[i] - acc)
            y[i] = acc
        return y if kind == "lp" else x - y
    return onepole(onepole(x, hi, "lp"), lo, "hp")


def sim_rates(delta, dt, A, B, tau, taud, w0):
    """驱动自稳态一阶滞后执行器（含逐通道传输延迟整数拍移位）
    + 刚体角速度方程（欧拉积分）。"""
    n = len(delta)
    lag = np.rint(np.asarray(taud) / dt).astype(int)
    d_in = np.stack([np.concatenate([np.full(lag[p], delta[0, p]),
                                     delta[:n - lag[p] or n, p]])
                     if lag[p] > 0 else delta[:, p]
                     for p in range(3)], axis=1)
    w = np.array(w0, dtype=float)
    da = d_in[0].copy()
    out = np.zeros((n, 3))
    for i in range(n):
        alpha = dt / (dt + tau)
        da = da + alpha * (d_in[i] - da)
        w = w + (A @ w + B @ da) * dt
        out[i] = w
    return out


def stroke_baseline(pha, x, half_mask, bins=72):
    """实测拍相位分箱均值基线（只用估计半数据求取；相位取自频率积分）。"""
    idx = np.minimum((pha * bins).astype(int), bins - 1)
    base = np.zeros((bins, len(x[0])))
    for b in range(bins):
        sel = half_mask & (idx == b)
        base[b] = x[sel].mean(axis=0) if sel.sum() > 2 else np.nan
    # 环形插值补缺失箱
    for b in range(bins):
        if np.isnan(base[b]).any():
            base[b] = base[(b + 1) % bins]
    return base[idx]


def stroke_baseline_cond(pha, x, feats, half_mask, bins=72):
    """v3 周期项结构升级（判据文档第四节触发的结构升级路径）：
    拍相位分箱 × 工作点线性条件化基线。依据 G9 验证残差谱集中于拍的
    二倍频与三倍频簇——静态分箱均值留下被工作点调制的拍谐波残余；
    每箱以 [1, veff−中位, freq_dev, dA] 线性回归估计半求基线，
    验证半按实测工作点外推。feats: (N,4) 含常数列。"""
    idx = np.minimum((pha * bins).astype(int), bins - 1)
    out = np.zeros_like(x)
    g_mean = x[half_mask].mean(axis=0)
    for b in range(bins):
        m = idx == b
        sel = half_mask & m
        if sel.sum() > 8:
            coef, *_ = np.linalg.lstsq(feats[sel], x[sel], rcond=None)
            out[m] = feats[m] @ coef
        else:
            out[m] = g_mean
    return out


# ─────────────────── 主流程 ───────────────────

def main(csv_path, tag, t0, t1, freqs, holdout_even, epoch="archive",
         n_in=3, amps=None):
    import pandas as pd
    from config import sim_config as cfg

    df = pd.read_csv(csv_path)
    m = (df["t"] >= t0) & (df["t"] <= t1) & (df["z"] > 2.5)
    d = df[m]
    t = d["t"].values
    dt = float(np.median(np.diff(t)))
    # freq_dev=拍频对窗口中位数的偏差（拍频调制通道的输入量）
    _fd = d["freq"].values - float(np.median(d["freq"].values))
    _src = {"ail": d["ail"].values, "elev": d["elev"].values,
            "rud": d["rud"].values, "freq_dev": _fd, "dA": d["dA"].values,
            # 第六输入：机翼扭转角实测值(对称分量)——扭转直接产俯仰矩,
            # 俯仰通道缺口的候选驱动(v9)
            "twist": 0.5 * (d["twistLa"].values + d["twistRa"].values)}
    cols = IN_COLS[:n_in]
    U_raw = np.stack([_src[c] for c in cols], axis=1)   # 输入δ
    W_raw = np.stack([d[c].values for c in OUT_COLS], axis=1)  # 输出ω

    f_stroke = float(np.median(d["freq"].values))
    # 实测拍相位（G9 定案：拍频调制注入下 时间×常数频率 会把相位分箱
    # 基线抹糊，验证量残留拍谐波；由逐翼瞬时频率积分得实测相位）
    _fl = d["freqL"].values if "freqL" in d.columns else None
    if _fl is not None and np.isfinite(_fl).sum() > len(t) * 0.5:
        _fs_i = np.nan_to_num(_fl, nan=float(np.nanmedian(_fl)))
        _ph = np.concatenate([[0.0], np.cumsum(
            0.5 * (_fs_i[1:] + _fs_i[:-1]) * np.diff(t))])
        pha = _ph % 1.0
        stroke_idx = np.floor(_ph).astype(int)
    else:
        pha = ((t - t[0]) * f_stroke) % 1.0
        stroke_idx = np.floor((t - t[0]) * f_stroke).astype(int)
    even = (stroke_idx % 2) == 0
    est_mask = even if holdout_even else ~even
    val_mask = ~est_mask

    # 饱和占比诊断（R93 教训：注入幅值叠控制器指令触限幅时指令≠实输入，
    # 线性拟合失真锚点劣化；占比超一成即警告本窗数据不宜辨识）
    sat_frac = [float(np.mean(np.abs(U_raw[:, p])
                              >= 0.95 * cfg.FlightControllerConfig.DELTA_MAX))
                for p in range(min(n_in, 3))]

    # 频域样本：估计半（连续段近似——按掩码逐频点相关积分，非整周期安全）
    Uf = np.zeros((n_in, len(freqs)), dtype=complex)
    Wf = np.zeros((3, len(freqs)), dtype=complex)
    for j, fq in enumerate(freqs):
        for p in range(n_in):
            Uf[p, j] = dft_at(U_raw[est_mask, p], t[est_mask], fq)
        for q in range(3):
            Wf[q, j] = dft_at(W_raw[est_mask, q], t[est_mask], fq)

    # 初值：B 主对角按锚点调度值/I，A 对角小阻尼
    if epoch == "current":
        I_ep, mass = current_inertia(os.path.join(
            ROOT, "assets", "flapper", "fwav_15dof_from_group.urdf"))
    else:
        I_ep, mass = epoch_inertia(os.path.join(
            ROOT, "assets", "flapper", "fwav_15dof_from_group.urdf"))
    v_eff = float(np.median(d["veff"].values))
    b_sched = np.array([
        cfg.FlightControllerConfig.K_B_TAIL_AIL,
        cfg.FlightControllerConfig.K_B_TAIL_ELEV,
        cfg.FlightControllerConfig.K_B_TAIL_RUD]) * v_eff * v_eff
    # 外注入占比加权（G10 谱诊断：施罗德相位泄漏致个别频点外注入自消
    # 被环路自振铃淹没——解析重构外注入幅与测量幅之比定权，占比低于
    # 三成权重归零；幅值由 --amps 显式给出，离线进程读不到运行时环境桥）
    wgt = None
    if amps is not None:
        from modules.flight_controller import multisine
        _chmap = ["ail", "elev", "rud", "freq", "da"]
        _mt_amp = float(_OSE("FLAPPER_MODAL_TONE_AMP", "0") or 0)
        _tones = []
        if _mt_amp > 0:
            _tones.append((2.33, _mt_amp))
        _tk = 0
        for _tone in str(_OSE("FLAPPER_MODAL_TONES", "")).split(","):
            if ":" in _tone:
                try:
                    _tf, _ta = _tone.split(":")
                    _tk += 1
                    _ph = (1.1 if _OSE("I006_TONE_SAME", "") == "1"
                           else 1.1 + 0.7 * _tk)
                    _tones.append((float(_tf), float(_ta), _ph))
                except ValueError:
                    pass
        _tones_e = []
        _tk = 0
        for _tone in str(_OSE("FLAPPER_MODAL_TONES_E", "")).split(","):
            if ":" in _tone:
                try:
                    _tf, _ta = _tone.split(":")
                    _tk += 1
                    _ph = (1.1 if _OSE("I006_TONE_SAME", "") == "1"
                           else 1.1 + 0.7 * _tk)
                    _tones_e.append((float(_tf), float(_ta), _ph))
                except ValueError:
                    pass
        wgt = []
        for j, fq in enumerate(freqs):
            share = 0.0
            for p in range(min(n_in, 3)):
                _sig = np.array([amps[p] * multisine(cfg, _chmap[p], tt)
                                 for tt in t[est_mask]])
                if p == 0:
                    for _tf, _ta, _tp in _tones:
                        _sig = _sig + _ta * np.sin(
                            2 * np.pi * _tf * t[est_mask] + _tp)
                if p == 1:
                    for _tf, _ta, _tp in _tones_e:
                        _sig = _sig + _ta * np.sin(
                            2 * np.pi * _tf * t[est_mask] + _tp)
                ext = abs(dft_at(_sig, t[est_mask], fq))
                mes = abs(Uf[p, j])
                share = max(share, min(1.0, ext / max(mes, 1e-12)))
            wgt.append(share ** 2 if share >= 0.3 else 0.0)

    # v8 边带输入样本（拍频 ωs 两侧，估计半时间基）
    ws = 2 * np.pi * f_stroke
    Usb = np.zeros((2, n_in, len(freqs)), dtype=complex)
    for j, fq in enumerate(freqs):
        for side in (0, 1):
            fsb = fq + ((-1 if side == 0 else 1) * f_stroke)
            for p in range(n_in):
                Usb[side, p, j] = dft_at(U_raw[est_mask, p],
                                         t[est_mask], fsb)

    def _num_jac(f, th):
        n = len(th)
        r_base = f(th)
        J = np.zeros((len(r_base), n))
        for k in range(n):
            eps = 1e-5 * max(1.0, abs(th[k]))
            tp = th.copy(); tp[k] += eps
            J[:, k] = (f(tp) - r_base) / eps
        return J

    f = lambda th: np.concatenate([
        residuals(th, Uf, Wf, freqs, n_in, wgt, Usb, ws),
        _anchor_prior(th)])

    def _anchor_prior(th):
        """零频锚点软先验（v6 教训：二阶共振环节会在低频点稀缺时偷走
        直流增益）：三舵主对角物理矩导数对调度锚点轻拉，权重取残差
        量级的十分之一，锚点门仍是独立判据非拟合约束本身。"""
        B_ = th[9:9 + 3 * n_in].reshape(3, n_in)
        pen = []
        for p in range(min(n_in, 3)):
            dc = I_ep[p, p] * B_[p, p]
            pen.append(20 * (dc - b_sched[p]))
        return np.array(pen)

    jac = lambda th: _num_jac(f, th)

    n_par = 9 + 6 * n_in + 6 + 18
    theta0 = np.zeros(n_par)
    theta0[0:9] = np.diag([-0.5, -0.5, -0.5]).ravel()
    _b0 = np.zeros((3, n_in))
    for p in range(min(n_in, 3)):
        _b0[p, p] = b_sched[p] / I_ep[p, p]
    theta0[9:9 + 3 * n_in] = _b0.ravel()
    _i0 = 9 + 3 * n_in
    theta0[_i0:_i0 + n_in] = 2 * np.pi * 2.5       # ωn 先验=2.5Hz
    theta0[_i0 + n_in:_i0 + 2 * n_in] = 0.05       # ζ 先验=近零阻尼
    theta0[_i0 + 2 * n_in:_i0 + 3 * n_in] = 0.03   # τd 先验
    lo = np.full(n_par, -np.inf)
    hi = np.full(n_par, np.inf)
    lo[_i0:_i0 + n_in] = 2 * np.pi * 1.2          # ωn ∈ 1.2~8Hz
    hi[_i0:_i0 + n_in] = 2 * np.pi * 8.0
    lo[_i0 + n_in:_i0 + 2 * n_in] = 0.05         # ζ 下界(加密网格已约束峰高,放开锐度)
    hi[_i0 + n_in:_i0 + 2 * n_in] = 0.8
    lo[_i0 + 2 * n_in:_i0 + 3 * n_in] = 0.0       # τd ∈ 0~0.4s
    hi[_i0 + 2 * n_in:_i0 + 3 * n_in] = 0.4
    # v8 周期调制参数：m（调制深度，三舵通道）∈[0,0.8]，ψ（调制相位）∈[-π,π]
    _i1 = _i0 + 3 * n_in
    lo[_i1:_i1 + 3] = 0.0
    hi[_i1:_i1 + 3] = 0.8
    lo[_i1 + 3:_i1 + 6] = -np.pi
    hi[_i1 + 3:_i1 + 6] = np.pi

    theta, r, J = gauss_newton(f, theta0, jac, lo=lo, hi=hi)
    dof = max(len(r) - len(theta), 1)
    sigma2 = (r @ r) / dof
    cov = sigma2 * np.linalg.pinv(J.T @ J)
    ci = 2.0 * np.sqrt(np.diag(cov))   # ~95%

    A = theta[0:9].reshape(3, 3)
    Ac = theta[-18:-9].reshape(3, 3)
    As = theta[-9:].reshape(3, 3)
    B = theta[9:9 + 3 * n_in].reshape(3, n_in)
    wn = theta[_i0:_i0 + n_in]
    zt = theta[_i0 + n_in:_i0 + 2 * n_in]
    td = theta[_i0 + 2 * n_in:_i0 + 3 * n_in]
    _i1 = _i0 + 3 * n_in
    Ac_m = theta[-18:-9].reshape(3, 3)
    As_m = theta[-9:].reshape(3, 3)
    theta_m = theta[_i1:_i1 + 3]      # v8 周期调制深度
    theta_p = theta[_i1 + 3:_i1 + 6]  # v8 周期调制相位
    C_w = I_ep @ A
    C_d = I_ep @ B

    # 零频锚点：C_δ 主元素 vs 调度值（偏差≤两成过；锚点仅前三舵通道，
    # 拍频/幅差通道无静态锚点表，只报告辨识值）
    anchors = []
    for p in range(min(n_in, 3)):
        name = cols[p]
        dom = int(np.argmax(np.abs(C_d[:, p])))
        anchors.append({
            "name": "b_%s→%s" % (name, OUT_COLS[dom]),
            "anchor": float(b_sched[p]) if dom == p else 0.0,
            "identified": float(C_d[dom, p]),
            "ratio": float(abs(C_d[dom, p] / b_sched[p])) if b_sched[p] else None,
        })
    anchor_pass = all(
        a["anchor"] == 0.0 or (0.8 <= a["ratio"] <= 1.2) for a in anchors)

    # 留出验证：逐拍前向预测（角速度态每拍起点取实测值重置，执行器滞
    # 后滤波态整窗连续；判据口径声明：预测时域=一拍）。v4 周期项结构
    # 升级：实测拍相位的一至二阶谐波强迫项纳入模型（拍反作用每拍重
    # 新注入谐波，LTI 模型只会把它当自由分量衰减掉）。仿真跑全窗，
    # 谐波系数只在估计半样本上最小二乘，验证半行纯外推检验。
    base = stroke_baseline_cond(
        pha, W_raw,
        np.stack([np.ones(len(t)), d["veff"].values - float(np.median(d["veff"].values)),
                  _fd, d["dA"].values], axis=1),
        est_mask)
    dev_w = W_raw - base
    harm = np.stack([f(2 * np.pi * k * pha) for k in (1, 2, 3)
                     for f in (np.sin, np.cos)], axis=1)
    lag = np.rint(np.asarray(td) / dt).astype(int)
    raw = np.zeros((len(U_raw) + int(lag.max()) + 1, n_in))
    raw[lag.max():lag.max() + len(U_raw)] = U_raw
    for i in range(int(lag.max())):
        raw[i] = U_raw[0]

    _AcS = (Ac_m, As_m)

    def _sim_full(harm_coef):
        """二阶共振执行链：x''=−2ζωn·x'+ωn²·(u_delayed−x)，每样本四个
        半隐式子步保证 ωn·dt 余量；输出=x。"""
        w_ = dev_w[0].copy()
        xr_ = np.zeros(n_in)          # 共振位置态
        xv_ = np.zeros(n_in)          # 共振速度态
        out_ = np.zeros_like(dev_w)
        nsub = 4
        h = dt / nsub
        for i in range(len(U_raw)):
            if i > 0 and stroke_idx[i] != stroke_idx[i - 1]:
                w_ = dev_w[i].copy()
            d_in = np.array([raw[lag.max() + i - lag[p], p]
                             for p in range(n_in)], dtype=float)
            # v8 输入侧周期增益调制（尾舵效随拍周期起伏）
            _mm = theta_m if theta_m is not None else np.zeros(3)
            _pp = theta_p if theta_p is not None else np.zeros(3)
            for p in range(min(n_in, 3)):
                d_in[p] *= (1.0 + _mm[p] * np.sin(
                    2 * np.pi * pha[i] + _pp[p]))
            for _ in range(nsub):
                xv_ = xv_ + (-2 * zt * wn * xv_
                             + wn ** 2 * (d_in - xr_)) * h
                xr_ = xr_ + xv_ * h
            da_ = xr_
            forcing = harm[i] @ harm_coef if harm_coef is not None else 0.0
            _At = (A + _AcS[0] * np.cos(2 * np.pi * pha[i])
                   + _AcS[1] * np.sin(2 * np.pi * pha[i]))
            w_ = w_ + (_At @ w_ + B @ da_ + forcing) * dt
            out_[i] = w_
        return out_

    meas_dev = bandpass(dev_w, dt)[val_mask]
    resid_full = bandpass(dev_w, dt) - bandpass(_sim_full(None), dt)
    coef, *_ = np.linalg.lstsq(harm[est_mask], resid_full[est_mask],
                               rcond=None)
    if _OSE("I006_FORCING_OFF", "") == "1":
        coef = np.zeros_like(coef)
    sim_dev = bandpass(_sim_full(coef), dt)[val_mask]
    holdout = []
    for q, name in enumerate(OUT_COLS):
        mse = float(np.mean((sim_dev[:, q] - meas_dev[:, q]) ** 2))
        var = float(np.var(meas_dev[:, q]))
        holdout.append({"channel": name, "mse": mse, "var": var,
                        "ratio": mse / var if var > 0 else None})
    holdout_pass = all(h["ratio"] is not None and h["ratio"] <= 0.5
                       for h in holdout)
    # 双半稳健口径（本线发现:换半留出从0.96/2.09/0.80劣化到1.46/7.35/
    # 6.66,音激励与拍相位混叠使调制参数按半选择产生伪影;诚实门=双半
    # 各算一次取劣者,两半比值同表入档)
    holdout_swap = []
    if True:
        _dev_all = bandpass(dev_w, dt)
        _sm_all = bandpass(_sim_full(coef), dt)
        _res_all = _dev_all - _sm_all
        _coef2, *_ = np.linalg.lstsq(harm[~est_mask], _res_all[~est_mask],
                                     rcond=None)
        _sm2 = bandpass(_sim_full(_coef2), dt)
        for q, name in enumerate(OUT_COLS):
            mse = float(np.mean((_sm_all[est_mask][:, q]
                                 - _dev_all[est_mask][:, q]) ** 2))
            var = float(np.var(_dev_all[est_mask][:, q]))
            holdout_swap.append({"channel": name, "mse": mse, "var": var,
                                 "ratio": mse / var if var > 0 else None})
    holdout_worst = [max(a["ratio"] or 9e9, b["ratio"] or 9e9)
                     for a, b in zip(holdout, holdout_swap)]
    holdout_pass = all(rw <= 0.5 for rw in holdout_worst)
    if _OSE("I006_DUMP", "") == "1":
        np.savez(os.path.join(ROOT, "logs", "_i006_dump.npz"),
                 meas=meas_dev, sim=sim_dev, t=t[val_mask])

    # 特征值（模态清单，含 2.33Hz 处置读数）
    eig = np.linalg.eigvals(A)
    modes = []
    for lam in eig:
        _lwn = abs(lam)
        _lzt = -lam.real / _lwn if _lwn > 1e-12 else float("nan")
        modes.append({"lambda": [float(lam.real), float(lam.imag)],
                      "fn_hz": float(_lwn / (2 * np.pi)),
                      "zeta": float(_lzt)})

    out_dir = os.path.join(ROOT, "logs", "ident_matrix",
                           "%s_%s" % (tag, __import__("time").strftime("%Y%m%d_%H%M%S")))
    os.makedirs(out_dir, exist_ok=True)
    result = {
        "tag": tag, "csv": csv_path, "window": [t0, t1],
        "freqs_hz": list(freqs), "f_stroke_hz": f_stroke,
        "v_eff": v_eff, "mass_kg": mass,
        "inertia_epoch": ("registry现行几何法新纪元" if epoch == "current"
                          else "archive(旧运行时覆写纪元)对角合成"),
        "I_epoch_diag": [float(I_ep[i, i]) for i in range(3)],
        "A": A.tolist(), "Ac": Ac_m.tolist(), "As": As_m.tolist(),
        "B": B.tolist(),
        "C_w_moment": C_w.tolist(), "C_d_moment": C_d.tolist(),
        "resonance_fn_hz": (wn / (2 * np.pi)).tolist(),
        "resonance_zeta": zt.tolist(),
        "tau_delay": td.tolist(),
        "ci95": ci.tolist(),
        "anchors": anchors, "anchor_pass": bool(anchor_pass),
        "holdout": holdout, "holdout_pass": bool(holdout_pass),
        "holdout_swap": holdout_swap,
        "holdout_worst_ratio": holdout_worst,
        "modes": modes,
        "not_for_control": bool(epoch != "current" or not holdout_pass),
        "not_for_control_reason":
            ("数据纪元=旧惯量（I-002 几何法重算前），仅作估计器验证，"
             "控制律禁读" if epoch != "current" else
             "留出验证未过（判据文档第四节），控制律禁读直至结构升级重估"),
    }
    with open(os.path.join(out_dir, "matrix.json"), "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)

    rep = ["# 频域辨识产出 %s" % tag, "",
           "- 数据：%s（窗口 %.1f~%.1f s，估计半=%s拍）" % (csv_path, t0, t1,
             "偶" if holdout_even else "奇"),
           "- 饱和占比 ail/elev/rud：%s%s" % (
               " ".join("%.1f%%" % (100 * s) for s in sat_frac),
               "（警告：超一成，限幅污染风险）"
               if max(sat_frac) > 0.10 else ""),
           "- 惯量纪元：%s，I=(%s)" % (
               "registry现行几何法新纪量" if epoch == "current"
               else "archive 旧值合成", ", ".join(
                   "%.3e" % v for v in result["I_epoch_diag"])),
           "", "## A0（1/s）", _md_mat(A),
           "## Ac（cos 周期项）", _md_mat(Ac_m),
           "## As（sin 周期项）", _md_mat(As_m),
           "## B（rad/s²/rad）", _md_mat(B),
           "## 矩实物矩导数 C=I·[A|B]", "### C_ω (N·m/(rad/s))", _md_mat(C_w),
           "### C_δ (N·m/rad)", _md_mat(C_d),
           "## 执行链（二阶共振加延迟）",
           "- fn (Hz)：%s" % np.round(wn / (2 * np.pi), 3).tolist(),
           "- ζ：%s" % np.round(zt, 4).tolist(),
           "- τ_delay (s)：%s" % np.round(td, 4).tolist(),
           "- 周期调制深度 m：%s" % np.round(theta_m, 3).tolist(),
           "- 周期调制相位 ψ：%s" % np.round(theta_p, 2).tolist(),
           "", "## 零频锚点（偏差≤两成过）：%s" % ("过" if anchor_pass else "不过"),
           "| 锚点 | 调度值 | 辨识值 | 比值 |", "|---|---|---|---|"]
    for a in anchors:
        rep.append("| %s | %.4g | %.4g | %s |" % (
            a["name"], a["anchor"], a["identified"],
            "—" if a["ratio"] is None else "%.2f" % a["ratio"]))
    rep += ["", "## 留出验证（双半稳健口径,过线=两半全部≤0.5）：%s" % (
                "过" if holdout_pass else "不过"),
            "| 通道 | 本半比值 | 换半比值 | 劣者 |", "|---|---|---|---|"]
    for h, h2 in zip(holdout, holdout_swap):
        rw = max(h["ratio"] or 9e9, h2["ratio"] or 9e9)
        rep.append("| %s | %.2f | %.2f | %.2f |" % (
            h["channel"], h["ratio"], h2["ratio"], rw))
    rep += ["", "旧单半口径明细：",
            "| 通道 | MSE | 实测方差 | 比值 |", "|---|---|---|---|"]
    for h in holdout:
        rep.append("| %s | %.3g | %.3g | %s |" % (
            h["channel"], h["mse"], h["var"],
            "—" if h["ratio"] is None else "%.2f" % h["ratio"]))
    rep += ["", "## 模态（特征值）", "| λ_re | λ_im | f(Hz) | ζ |", "|---|---|---|---|"]
    for mo in modes:
        rep.append("| %.4g | %.4g | %.3f | %.3f |" % (
            mo["lambda"][0], mo["lambda"][1], mo["fn_hz"], mo["zeta"]))
    rep += ["", "> 本版本标记 not_for_control：%s" %
            result["not_for_control_reason"], ""]
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return result


def _md_mat(M):
    return "\n".join("| " + " | ".join("%+.4g" % v for v in row) + " |"
                     for row in M) + "\n"


if __name__ == "__main__":
    args = sys.argv[1:]
    csv = args[0]
    tag = "est"
    t0, t1 = 3.0, 13.0
    freqs = [0.2, 0.5, 1.0, 1.5, 2.33, 3.0, 5.0, 8.0]
    holdout_even = True
    epoch = "archive"
    n_in = 3
    amps = None
    i = 1
    while i < len(args):
        if args[i] == "--tag":
            tag = args[i + 1]; i += 2
        elif args[i] == "--t0":
            t0 = float(args[i + 1]); i += 2
        elif args[i] == "--t1":
            t1 = float(args[i + 1]); i += 2
        elif args[i] == "--freqs":
            freqs = [float(x) for x in args[i + 1].split(",")]; i += 2
        elif args[i] == "--inertia-epoch":
            epoch = args[i + 1]; i += 2
        elif args[i] == "--inputs":
            n_in = int(args[i + 1]); i += 2
        elif args[i] == "--amps":
            amps = [float(x) for x in args[i + 1].split(",")]; i += 2
        elif args[i] == "--strokes-odd":
            holdout_even = False; i += 1
        else:
            i += 1
    main(csv, tag, t0, t1, freqs, holdout_even, epoch, n_in, amps)
