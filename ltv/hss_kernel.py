# -*- coding: utf-8 -*-
"""
hss_kernel.py — 谐波状态空间模型估计器内核，L4 线事项 I-045。

本文件实现 ltv/hss_spec.json 冻结规范 model 节定义的频域输出误差估计
内核。谐波状态空间模型的中文定义与命名见该规范 naming 节。状态方程为
角速度导数等于周期状态矩阵乘角速度加输入矩矩阵乘有效输入，周期状态
矩阵等于常数矩阵加各阶余弦系数阵与正弦系数阵按拍相位角的展开。估计
在频域完成，预测为精确谐波平衡块方程组解，即以中心频点加减整数倍拍
频的各次谐波状态为未知量，按状态方程的谐波分量平衡联立求解，中心块
即输出预测。弱耦合极限下该解退化为规范 sideband_coupling 节所述的一
阶边带耦合路径，即边带频点输入样本先经未扰动传函产生边带状态响应，
再经周期系数阵组合耦合回中心频点。输入侧一阶调制以邻谐波混频项进入
各谐波的有效输入样本。

本内核只依赖 numpy 与 Python 标准库。本内核不导入仿真器、配置与控制
器模块，满足验收判据 A1 的零仿真器调用要求。

参数向量布局如下，n_in 为输入通道数，K 为谐波阶数。段一为 A0 九元，
按行主序存放三乘三矩阵。段二为输入矩矩阵共三行乘 n_in 列。段三至段
五为逐通道共振角频率、阻尼比、纯延迟各 n_in 元。段六段七为调制深度
三元与调制相位三元，只作用三舵通道。段八起每阶十八元，先存该阶余弦
系数阵九元，后存该阶正弦系数阵九元。K 取一时参数共六十九元，与规范
parameter_vector 节一致。
"""
import numpy as np


# ─────────────────── 参数打包与解包 ───────────────────

def n_par(n_in, K):
    """参数向量总长，按规范 parameter_vector 节的计数。"""
    return 9 + 6 * n_in + 6 + 18 * K


def unpack_theta(theta, n_in, K):
    """把参数向量拆成各参数组，返回字典。"""
    theta = np.asarray(theta, dtype=float)
    A0 = theta[0:9].reshape(3, 3)
    i = 9
    B = theta[i:i + 3 * n_in].reshape(3, n_in)
    i += 3 * n_in
    wn = theta[i:i + n_in].copy()
    i += n_in
    zt = theta[i:i + n_in].copy()
    i += n_in
    td = theta[i:i + n_in].copy()
    i += n_in
    m = theta[i:i + 3].copy()
    psi = theta[i + 3:i + 6].copy()
    i += 6
    Ac, As = [], []
    for _k in range(K):
        Ac.append(theta[i:i + 9].reshape(3, 3))
        As.append(theta[i + 9:i + 18].reshape(3, 3))
        i += 18
    return {"A0": A0, "B": B, "wn": wn, "zt": zt, "td": td,
            "m": m, "psi": psi, "Ac": Ac, "As": As}


def pack_theta(A0, B, wn, zt, td, m, psi, Ac=None, As=None):
    """把各参数组拼成参数向量，周期系数阵列表逐阶给出。"""
    parts = [np.asarray(A0, float).ravel(),
             np.asarray(B, float).ravel(),
             np.asarray(wn, float).ravel(),
             np.asarray(zt, float).ravel(),
             np.asarray(td, float).ravel(),
             np.asarray(m, float).ravel(),
             np.asarray(psi, float).ravel()]
    for Mk, Msk in zip(Ac or [], As or []):
        parts.append(np.asarray(Mk, float).ravel())
        parts.append(np.asarray(Msk, float).ravel())
    return np.concatenate(parts)


# ─────────────────── 频域取样 ───────────────────

def dft_at(x, t, freq):
    """指定频点相关积分，允许样本在时间上不连续。公式为样本乘负指数
    复数载波后乘时间步求和，与现行线性估计器同款口径。"""
    dt = float(np.median(np.diff(t)))
    return complex(np.sum(x * np.exp(-2j * np.pi * freq * t)) * dt)


def build_samples(u, w, t, freqs, f_stroke, M, mask=None):
    """从输入输出序列构造频域样本。

    返回三件，U 为各通道在分析频点的样本，W 为输出在三通道分析频点
    的样本，Uhar 为各次谐波频点的输入样本，形状为二乘 M 加三乘 n_in
    乘频点数，谐波序号从负 M 减一到正 M 加一，相邻序号频率差一个拍
    频，多出的一阶供边界谐波的输入侧混频取样。mask 给定时只对掩码内
    样本取样。
    """
    if mask is not None:
        u, w, t = u[mask], w[mask], t[mask]
    n_in = u.shape[1]
    nf = len(freqs)
    U = np.zeros((n_in, nf), dtype=complex)
    W = np.zeros((3, nf), dtype=complex)
    for j, fq in enumerate(freqs):
        for p in range(n_in):
            U[p, j] = dft_at(u[:, p], t, fq)
        for q in range(3):
            W[q, j] = dft_at(w[:, q], t, fq)
    nh = 2 * (M + 1) + 1
    Uhar = np.zeros((nh, n_in, nf), dtype=complex)
    for mi in range(nh):
        m = mi - (M + 1)
        for j, fq in enumerate(freqs):
            fm = fq + m * f_stroke
            for p in range(n_in):
                Uhar[mi, p, j] = dft_at(u[:, p], t, fm)
    return U, W, Uhar


def onepole_resp(f, dt, fc, kind):
    """离散一阶环节在指定频点的频率响应。

    递推形式为输出加步长系数乘输入减输出之差，步长系数为 dt 除以 dt
    加角频率倒数。kind 取 lp 为低通，取 hp 为一减低通。本函数供已知
    测量滤波链的响应折算，使估计器对经过已知带通的测量数据保持模型
    一致。
    """
    a = dt / (dt + 1.0 / (2 * np.pi * fc))
    z = np.exp(2j * np.pi * f * dt)
    H = a / (1.0 - (1.0 - a) * z ** -1)
    return H if kind == "lp" else 1.0 - H


def bandpass_resp(f, dt, lo=0.15, hi=20.0):
    """离散带通链在指定频点的总响应，先二十赫兹低通后零点一五赫兹
    高通，与现行线性估计器留出验证所用的递推带通同构。"""
    return onepole_resp(f, dt, lo, "hp") * onepole_resp(f, dt, hi, "lp")


# ─────────────────── 模型传函与残差 ───────────────────

def tf_matrix(s, A, B, wn, zt, td):
    """传函矩阵 T 等于 s 单位阵减 A0 的逆左乘 B 再右乘执行链对角阵。
    执行链为二阶共振环节串联纯延迟，公式见规范 actuation_chain 节。"""
    G = np.diag(wn ** 2 / (s ** 2 + 2 * zt * wn * s + wn ** 2)
                * np.exp(-td * s))
    return np.linalg.solve(s * np.eye(3) - A, B @ G)


def _batched_systems(S, p):
    """按参数组装全部分析频点的谐波平衡块方程组，批量求解。

    返回中心块预测数组与逐频点基响应数组。所有频点的方程组沿频点轴
    批量组装与批量求解，对角块为各谐波频点复频率单位阵减 A0，非对角
    块为各阶周期系数阵的复指数组合，右端为输入矩矩阵乘执行链乘含调
    制混频的有效输入样本。基响应为对每个单位输入矩矩阵元素的响应列
    向量，供输入矩矩阵的线性剖面使用。
    """
    M = int(S.get("M_harm", 6))
    K = S["K"]
    n_in = S["n_in"]
    fs = S["ws"] / (2 * np.pi)
    nf = len(S["freqs"])
    nh = 2 * M + 1
    freqs = np.asarray(S["freqs"], dtype=float)
    m_grid = np.arange(-M, M + 1)
    sh = 2j * np.pi * (freqs[:, None] + m_grid[None, :] * fs)  # nf×nh
    G = p["wn"] ** 2 / (sh[:, :, None] ** 2
                        + 2 * p["zt"] * p["wn"] * sh[:, :, None]
                        + p["wn"] ** 2) * np.exp(-p["td"] * sh[:, :, None])
    byp = S.get("chain_bypass")
    if byp is not None:
        for pi, b in enumerate(byp):
            if b:
                G[:, :, pi] = 1.0
    Um = np.transpose(S["Uhar"][1:-1], (2, 0, 1))       # nf×nh×n_in
    Ulo = np.transpose(S["Uhar"][0:-2], (2, 0, 1))
    Uhi = np.transpose(S["Uhar"][2:], (2, 0, 1))
    Dm = Um.copy()
    if n_in >= 3:
        cp = p["m"] / (2j) * np.exp(1j * p["psi"])       # 3
        cm = -p["m"] / (2j) * np.exp(-1j * p["psi"])
        Dm[:, :, :3] = Dm[:, :, :3] + cp[None, None, :] * Ulo[:, :, :3] \
            + cm[None, None, :] * Uhi[:, :, :3]
    GD = G * Dm                                          # nf×nh×n_in
    rhs = None
    if "B" in p:
        rhs = np.einsum("ij,fkj->fki", p["B"], GD)       # nf×nh×3
    Amat = np.zeros((nf, nh, 3, nh, 3), dtype=complex)
    eye3 = np.eye(3, dtype=complex)
    for mi in range(nh):
        Amat[:, mi, :, mi, :] = sh[:, mi, None, None] * eye3 \
            - p["A0"][None, None, :, :]
    for k in range(K):
        Ap = 0.5 * p["Ac"][k] + 0.5 * p["As"][k] / 1j
        An = 0.5 * p["Ac"][k] - 0.5 * p["As"][k] / 1j
        for mi in range(k + 1, nh):
            Amat[:, mi, :, mi - (k + 1), :] -= Ap
            Amat[:, mi - (k + 1), :, mi, :] -= An
    Amat = Amat.reshape(nf, 3 * nh, 3 * nh)
    E = np.zeros((nf, nh, 3, 3 * n_in), dtype=complex)
    for q3 in range(3):
        E[:, :, q3, n_in * q3:n_in * (q3 + 1)] = GD
    Y = np.linalg.solve(Amat, E.reshape(nf, 3 * nh, 3 * n_in))
    Yc = Y[:, 3 * M:3 * M + 3, :]
    if rhs is None:
        return None, Yc
    sol = np.linalg.solve(Amat, rhs.reshape(nf, 3 * nh, 1))
    pred = sol[:, 3 * M:3 * M + 3, 0]
    return pred, Yc


def residuals(theta, S):
    """频域残差向量，预测采用精确谐波平衡块方程组解。

    S 为样本与环境字典，必含键 U、W、Uhar、freqs、ws、n_in、K，可
    选键 M_harm 为谐波截断半宽默认六，wgt 为逐频点权重，wgt_ch 为逐
    通道逐频点权重且取输出样本幅值倒数时残差为相对误差口径，Hmeas
    为逐频点已知测量滤波响应，extra 为额外残差回调，供真实数据拟合
    挂对照先验。对每个分析频点，以中心频点加减整数倍拍频的各次谐波
    为未知状态，按状态方程的谐波分量平衡建立块线性方程组并批量求解，
    解出的中心块即输出预测。弱耦合极限下该解退化为规范
    sideband_coupling 节所述的一阶边带耦合路径。残差按频点拼接实部
    虚部，尾接赫尔维茨软约束与周期矩阵范数帽罚项，罚项形式与现行线
    性估计器同款。
    """
    p = unpack_theta(theta, S["n_in"], S["K"])
    pred, _ = _batched_systems(S, p)
    if S.get("Hmeas") is not None:
        pred = pred * np.asarray(S["Hmeas"])[:, None]
    d = S["W"].T - pred                                    # nf×3
    if S.get("wgt_ch") is not None:
        d = d * np.asarray(S["wgt_ch"]).T
    if S.get("wgt") is not None:
        d = d * np.asarray(S["wgt"])[:, None]
    r = [np.concatenate([d.real.ravel(), d.imag.ravel()])]
    pos = np.maximum(np.linalg.eigvals(p["A0"]).real, 0.0)
    r.append(np.full(3, 100.0 * float(np.sqrt(np.sum(pos ** 2)))))
    nrm = float(np.linalg.norm(p["A0"], "fro"))
    for k in range(S["K"]):
        for Mk in (p["Ac"][k], p["As"][k]):
            ex = max(float(np.linalg.norm(Mk, "fro")) - 0.15 * nrm, 0.0)
            r.append(np.array([100.0 * ex]))
    if S.get("extra") is not None:
        r.append(np.asarray(S["extra"](theta), dtype=float))
    return np.concatenate(r)


# ─────────────────── 高斯牛顿优化 ───────────────────

def num_jac(f, theta):
    """前向差分数值雅可比，步长为一点乘参数量级的相对扰动。"""
    r_base = f(theta)
    J = np.zeros((len(r_base), len(theta)))
    for k in range(len(theta)):
        eps = 1e-5 * max(1.0, abs(theta[k]))
        tp = theta.copy()
        tp[k] += eps
        J[:, k] = (f(tp) - r_base) / eps
    return J


def gauss_newton(f, theta0, jac_fun, max_iter=120, lam=1e-3,
                 lo=None, hi=None, scale=None):
    """高斯牛顿加勒文贝格马夸特阻尼加盒式界，与现行线性估计器同款
    实现，另加参数尺度变换。scale 为逐参数特征量级，优化在除以尺度
    的归一坐标中进行，正规方程条件数按量级均衡。返回最优参数、残差
    与末步雅可比。"""
    s = np.ones(len(theta0)) if scale is None else np.asarray(scale, float)
    lo_s = None if lo is None else lo / s
    hi_s = None if hi is None else hi / s
    theta = theta0.copy()
    r0 = f(theta)
    cost = r0 @ r0
    J = None
    for _ in range(max_iter):
        J = jac_fun(theta) * s
        JtJ = J.T @ J + lam * np.eye(len(theta))
        g = J.T @ r0
        try:
            step = np.linalg.solve(JtJ, -g)
        except np.linalg.LinAlgError:
            lam *= 10
            continue
        cand = theta + step * s
        if lo is not None:
            cand = np.maximum(cand, lo)
        if hi is not None:
            cand = np.minimum(cand, hi)
        r1 = f(cand)
        if r1 @ r1 < cost:
            theta = cand
            r0 = r1
            cost = r0 @ r0
            lam = max(lam * 0.3, 1e-9)
        else:
            lam *= 10
        if np.linalg.norm(step) < 1e-12:
            break
    return theta, r0, jac_fun(theta)


def default_bounds(n_in):
    """执行链与调制参数的盒式界，取值与规范 actuation_chain 节和输入
    侧调制节一致。其余参数不设界。"""
    n = n_par(n_in, 0)
    lo = np.full(n, -np.inf)
    hi = np.full(n, np.inf)
    i0 = 9 + 3 * n_in
    lo[i0:i0 + n_in] = 2 * np.pi * 1.2
    hi[i0:i0 + n_in] = 2 * np.pi * 8.0
    lo[i0 + n_in:i0 + 2 * n_in] = 0.05
    hi[i0 + n_in:i0 + 2 * n_in] = 0.8
    lo[i0 + 2 * n_in:i0 + 3 * n_in] = 0.0
    hi[i0 + 2 * n_in:i0 + 3 * n_in] = 0.4
    i1 = i0 + 3 * n_in
    lo[i1:i1 + 3] = 0.0
    hi[i1:i1 + 3] = 0.8
    lo[i1 + 3:i1 + 6] = -np.pi
    hi[i1 + 3:i1 + 6] = np.pi
    return lo, hi


def extend_bounds(lo0, hi0, K):
    """把零阶盒式界扩展到 K 阶参数长度，周期参数不设界。"""
    n_add = 18 * K
    return (np.concatenate([lo0, np.full(n_add, -np.inf)]),
            np.concatenate([hi0, np.full(n_add, np.inf)]))


def initial_theta(n_in, K, b_diag0=25.0, fn0_hz=2.5, zt0=0.1, td0=0.05,
                  m0=0.2):
    """通用初值，不依赖任何真值信息。A0 取负零点五对角，B 主对角取
    量级域中值附近的常数，执行链取中域先验，周期项全零。"""
    theta = np.zeros(n_par(n_in, K))
    theta[0:9] = np.diag([-0.5, -0.5, -0.5]).ravel()
    i = 9
    B0 = np.zeros((3, n_in))
    for q in range(3):
        B0[q, q] = b_diag0
    theta[i:i + 3 * n_in] = B0.ravel()
    i += 3 * n_in
    theta[i:i + n_in] = 2 * np.pi * fn0_hz
    theta[i + n_in:i + 2 * n_in] = zt0
    theta[i + 2 * n_in:i + 3 * n_in] = td0
    theta[i + 3 * n_in:i + 3 * n_in + 3] = m0
    return theta


def param_scale(n_in, K):
    """逐参数特征量级表，供优化的尺度变换。分段取值为 A0 零点五、B
    二十五、共振角频率十五、阻尼比零点一五、纯延迟零点一、调制深度
    零点三、调制相位一、周期系数零点零三。"""
    s = np.zeros(n_par(n_in, K))
    i = 0
    s[i:i + 9] = 0.5
    i += 9
    s[i:i + 3 * n_in] = 25.0
    i += 3 * n_in
    s[i:i + n_in] = 15.0
    i += n_in
    s[i:i + n_in] = 0.15
    i += n_in
    s[i:i + n_in] = 0.1
    i += n_in
    s[i:i + 3] = 0.3
    s[i + 3:i + 6] = 1.0
    i += 6
    s[i:i + 18 * K] = 0.03
    return s


def profile_residuals(theta_nb, S):
    """输入矩矩阵剖面残差，即变元消去后的外层残差。

    预测对输入矩矩阵 B 的全部元素严格线性，本函数对给定的非 B 参数
    在每次求值内部以加权线性最小二乘精确消去 B，返回消去后的残差。
    theta_nb 为外层参数向量，布局为 A0 九元、执行链十八元、调制六元、
    周期项十八乘 K 元。线性基取谐波块方程组对每个单位输入矩矩阵元素
    的响应列，由批量求解同时给出。此法把 B 与执行链及 A0 的双线性耦
    合从非线性迭代中移除，消除符号翻转陷阱。返回外层残差与剖面出的
    B。
    """
    n_in, K = S["n_in"], S["K"]
    i_per = 15 + 3 * n_in
    p = {"A0": theta_nb[0:9].reshape(3, 3),
         "wn": theta_nb[9:9 + n_in], "zt": theta_nb[9 + n_in:9 + 2 * n_in],
         "td": theta_nb[9 + 2 * n_in:9 + 3 * n_in],
         "m": theta_nb[9 + 3 * n_in:9 + 3 * n_in + 3],
         "psi": theta_nb[9 + 3 * n_in + 3:9 + 3 * n_in + 6],
         "Ac": [theta_nb[i_per + 18 * k:i_per + 18 * k + 9
                ].reshape(3, 3) for k in range(K)],
         "As": [theta_nb[i_per + 18 * k + 9:i_per + 18 * k + 18
                ].reshape(3, 3) for k in range(K)]}
    pred, Yc = _batched_systems(S, p)
    nf = len(S["freqs"])
    Wt = S["W"].T                                            # nf×3
    wt = np.ones_like(Wt, dtype=float)
    if S.get("wgt_ch") is not None:
        wt = wt * np.asarray(S["wgt_ch"]).T
    if S.get("wgt") is not None:
        wt = wt * np.asarray(S["wgt"])[:, None]
    H = np.ones(nf, dtype=complex) if S.get("Hmeas") is None \
        else np.asarray(S["Hmeas"])
    Hw = wt * H[:, None]                                     # nf×3
    A_ls = np.concatenate([(Hw[:, :, None] * Yc).real.reshape(
        nf * 3, 3 * n_in), (Hw[:, :, None] * Yc).imag.reshape(
        nf * 3, 3 * n_in)])
    b_ls = np.concatenate([(wt * Wt).real.ravel(),
                           (wt * Wt).imag.ravel()])
    col_nrm = np.linalg.norm(A_ls, axis=0)
    col_nrm[col_nrm == 0.0] = 1.0
    x_ls, *_ = np.linalg.lstsq(A_ls / col_nrm, b_ls, rcond=None)
    Bvec = x_ls / col_nrm
    resid = A_ls @ Bvec - b_ls
    pos = np.maximum(np.linalg.eigvals(p["A0"]).real, 0.0)
    r_pen = [np.full(3, 100.0 * float(np.sqrt(np.sum(pos ** 2))))]
    nrm = float(np.linalg.norm(p["A0"], "fro"))
    for k in range(K):
        for Mk in (p["Ac"][k], p["As"][k]):
            ex = max(float(np.linalg.norm(Mk, "fro")) - 0.15 * nrm, 0.0)
            r_pen.append(np.array([100.0 * ex]))
    return np.concatenate([resid] + r_pen), Bvec.reshape(3, n_in)


def n_par_nb(n_in, K):
    """外层参数向量长度，即全参数向量去掉输入矩矩阵十八元。"""
    return n_par(n_in, K) - 3 * n_in


def initial_theta_nb(n_in, K, **kw):
    """外层参数通用初值，B 段剔除。"""
    th = initial_theta(n_in, K, **kw)
    keep = np.ones(len(th), bool)
    keep[9:9 + 3 * n_in] = False
    return th[keep]


def init_from_data(S, n_grid_fn=10, n_grid_td=7, sweeps=3):
    """从频域样本自身逐通道提取执行链初值。

    谱线按通道独占时，某通道独占频点上的输出近似该通道通路的响应。
    对每个通道，以全模型输入矩矩阵剖面残差为目标，对共振频率与纯延
    迟做二维粗网格搜索，再对共振频率、阻尼比、纯延迟三参数做多分辨
    率局部加密，其余通道的执行链取当前轮扫估计，A0 与调制全程冻结
    在通用值，逐通道逐轮扫互相引导。此法把执行链的强非线性初值问题
    分解为逐通道三维子问题的坐标轮扫，绕开联合参数空间的多极小地形。
    返回零阶外层参数初值向量。
    """
    n_in = S["n_in"]
    U = S["U"]
    S0 = dict(S, K=0)
    th = initial_theta_nb(n_in, 0)
    umax = np.max(np.abs(U), axis=0)
    owners = []
    for p in range(n_in):
        idx = np.abs(U[p, :]) > 0.5 * umax
        owners.append(idx if idx.sum() >= 2 else np.zeros_like(idx, bool))
    freqs_arr = np.asarray(S["freqs"], dtype=float)

    def score(thx, p, wn_v, zt_v, td_v, idx):
        x = thx.copy()
        x[9 + p] = wn_v
        x[9 + n_in + p] = zt_v
        x[9 + 2 * n_in + p] = td_v
        r, _ = profile_residuals(x, _subset_S(S0, idx) if idx.any() else S0)
        return float(np.linalg.norm(r))

    for _sw in range(max(1, sweeps)):
        for p in range(n_in):
            if not owners[p].any():
                continue
            idx = owners[p]
            best = None
            for fn_v in np.linspace(1.4, 5.2, n_grid_fn):
                for td_v in np.linspace(0.02, 0.26, n_grid_td):
                    sc = score(th, p, 2 * np.pi * fn_v, 0.15, td_v, idx)
                    if best is None or sc < best[0]:
                        best = (sc, 2 * np.pi * fn_v, 0.15, td_v)
            _, wn_b, zt_b, td_b = best
            x0 = np.array([wn_b, zt_b, td_b])
            steps = np.array([2 * np.pi * 0.2, 0.05, 0.015])
            for _round in range(6):
                improved = False
                for k in range(3):
                    for dr in (-1.0, 1.0):
                        xt = x0.copy()
                        xt[k] = np.clip(
                            xt[k] + dr * steps[k],
                            [2 * np.pi * 1.21, 0.051, 0.001][k],
                            [2 * np.pi * 7.9, 0.79, 0.399][k])
                        if score(th, p, xt[0], xt[1], xt[2], idx) \
                                < score(th, p, x0[0], x0[1], x0[2], idx):
                            x0 = xt
                            improved = True
                steps = steps / 2.0
                if not improved and steps[2] < 1e-5:
                    break
            th[9 + p] = x0[0]
            th[9 + n_in + p] = x0[1]
            th[9 + 2 * n_in + p] = x0[2]
    return th


def _subset_S(S, idx):
    """按频点子集构造共享样本数组的子样本环境字典。"""
    out = {"U": S["U"][:, idx], "W": S["W"][:, idx],
           "Uhar": S["Uhar"][..., idx],
           "freqs": np.asarray(S["freqs"])[idx], "ws": S["ws"],
           "n_in": S["n_in"], "K": S["K"]}
    if "M_harm" in S:
        out["M_harm"] = S["M_harm"]
    if "Hmeas" in S:
        out["Hmeas"] = np.asarray(S["Hmeas"])[idx]
    if "wgt" in S:
        out["wgt"] = np.asarray(S["wgt"])[idx]
    if "wgt_ch" in S:
        out["wgt_ch"] = np.asarray(S["wgt_ch"])[:, idx]
    if "extra" in S:
        out["extra"] = S["extra"]
    return out


def fit_hss(S, theta_init=None, n_starts=6, seed=0, restarts=1,
            max_iter=150, tpe_rounds=0, tpe_batch=4,
            tpe_trigger=np.inf):
    """变元消去多起点拟合入口。

    外层参数为除输入矩矩阵外的全部参数，输入矩矩阵在每次残差求值内
    部以加权线性最小二乘精确剖面消去。每个起点先做零阶外层拟合再做
    全阶外层拟合，随后重组全参数做全参数抛光，按抛光残差选最优起点。
    第一个起点用逐通道网格轮扫的数据驱动初值，第二个起点取通用初值，
    其余起点由先验盒随机抽取。返回参数、残差、雅可比、协方差、置信
    区间与最优起点序号。
    """
    n_in, K = S["n_in"], S["K"]
    rng = np.random.default_rng(seed)
    lo0, hi0 = default_bounds(n_in)
    keep0 = np.ones(n_par(n_in, 0), bool)
    keep0[9:9 + 3 * n_in] = False
    lo_nb0, hi_nb0 = lo0[keep0], hi0[keep0]
    sc0 = param_scale(n_in, 0)[keep0]
    lo_f, hi_f = extend_bounds(lo0, hi0, K)
    keep = np.ones(n_par(n_in, K), bool)
    keep[9:9 + 3 * n_in] = False
    lo_nb, hi_nb = lo_f[keep], hi_f[keep]
    sc = param_scale(n_in, K)[keep]
    S0 = dict(S, K=0)

    def rand_start():
        th = np.zeros(n_par_nb(n_in, 0))
        A0r = th[0:9].reshape(3, 3)
        A0r[~np.eye(3, dtype=bool)] = rng.uniform(-0.04, 0.04, 6)
        A0r[np.diag_indices(3)] = -rng.uniform(0.15, 0.9, 3)
        i = 9
        th[i:i + n_in] = 2 * np.pi * rng.uniform(1.6, 3.9, n_in)
        th[i + n_in:i + 2 * n_in] = rng.uniform(0.06, 0.28, n_in)
        th[i + 2 * n_in:i + 3 * n_in] = rng.uniform(0.05, 0.18, n_in)
        th[i + 3 * n_in:i + 3 * n_in + 3] = rng.uniform(0.1, 0.55, 3)
        th[i + 3 * n_in + 3:i + 3 * n_in + 6] = \
            rng.uniform(-np.pi, np.pi, 3)
        return th

    starts = [init_from_data(S), initial_theta_nb(n_in, 0)] + \
        [rand_start() for _ in range(max(0, n_starts - 2))]
    if theta_init is not None:
        ti = np.asarray(theta_init, float)
        starts = [ti if len(ti) == n_par_nb(n_in, 0) else
                  ti[keep[:n_par(n_in, 0)]].copy()]
    lo, hi = extend_bounds(lo0, hi0, K)
    sc_full = param_scale(n_in, K)
    ff = lambda x: residuals(x, S)
    best = None
    for si, thnb in enumerate(starts):
        th0v = thnb.copy()
        f0 = lambda x: profile_residuals(x, S0)[0]
        f = lambda x: profile_residuals(x, S)[0]
        th0v, _, _ = gauss_newton(f0, th0v, lambda x: num_jac(f0, x),
                                  max_iter=max_iter, lo=lo_nb0, hi=hi_nb0,
                                  scale=sc0)
        th0v, _, _ = gauss_newton(f0, th0v, lambda x: num_jac(f0, x),
                                  max_iter=max_iter, lo=lo_nb0, hi=hi_nb0,
                                  scale=sc0)
        thc = th0v
        for Ki in range(1, K + 1):
            SK = dict(S, K=Ki)
            fK = lambda x, _SK=SK: profile_residuals(x, _SK)[0]
            keep_i = np.ones(n_par(n_in, Ki), bool)
            keep_i[9:9 + 3 * n_in] = False
            loK = extend_bounds(lo0, hi0, Ki)[0][keep_i]
            hiK = extend_bounds(lo0, hi0, Ki)[1][keep_i]
            scK = param_scale(n_in, Ki)[keep_i]
            thc = np.concatenate([thc, np.zeros(18)])
            for _ in range(max(1, restarts)):
                thc, _, _ = gauss_newton(fK, thc,
                                         lambda x, _f=fK: num_jac(_f, x),
                                         max_iter=max_iter, lo=loK, hi=hiK,
                                         scale=scK)
        thk = thc
        _, Bp = profile_residuals(thk, S)
        full = np.zeros(n_par(n_in, K))
        full[:9] = thk[:9]
        full[9:9 + 3 * n_in] = Bp.ravel()
        full[9 + 3 * n_in:] = thk[9:]
        thf, rf, Jf = full, ff(full), None
        for _ in range(max(1, restarts)):
            thf, rf, Jf = gauss_newton(ff, thf, lambda x: num_jac(ff, x),
                                       max_iter=max_iter, lo=lo, hi=hi,
                                       scale=sc_full)
        rk = float(rf @ rf)
        if best is None or rk < best[0]:
            best = (rk, thf.copy(), Jf, si)
    cost, th, J, si_best = best
    if tpe_rounds > 0 and cost > tpe_trigger:
        best = _tpe_fallback(S, best, tpe_rounds, tpe_batch, seed,
                             max_iter)
        cost, th, J, si_best = best
    r = ff(th)
    dof = max(len(r) - len(th), 1)
    sigma2 = float(r @ r) / dof
    cov = sigma2 * np.linalg.pinv(J.T @ J)
    ci95 = 2.0 * np.sqrt(np.abs(np.diag(cov)))
    return th, r, J, cov, ci95, si_best


def _tpe_fallback(S, best, rounds, batch, seed, max_iter):
    """多起点未达触发线时的 TPE 初值兜底。

    搜索域为六通道共振频率与六通道纯延迟共十二维，基底初值取逐通道
    网格轮扫的数据驱动初值。每候选点走与本入口相同的 K 逐级续接拟
    合，按抛光残差择优，优于既有解则替换。搜索器为 ltv/hss_tpe.py
    逐字复制的现行 TPE。
    """
    from hss_tpe import TPE
    n_in = S["n_in"]
    dom = {"fn_%d" % p: (1.5, 4.5) for p in range(n_in)}
    dom.update({"td_%d" % p: (0.04, 0.22) for p in range(n_in)})
    base = init_from_data(S)
    tpe = TPE(dom, seed=seed + 1)
    for _rd in range(max(1, rounds)):
        pts, ys = [], []
        for w in tpe.ask_batch(batch):
            th0 = base.copy()
            for p in range(n_in):
                th0[9 + p] = 2 * np.pi * w["fn_%d" % p]
                th0[9 + 2 * n_in + p] = w["td_%d" % p]
            try:
                th, r, _J, _c, _ci, _si = fit_hss(
                    dict(S), theta_init=th0, n_starts=1, seed=seed,
                    restarts=1, max_iter=max_iter)
                loss = float(r @ r)
            except Exception:
                loss = 1e18
            pts.append(w)
            ys.append(loss)
            if loss < best[0]:
                best = (loss, th.copy(), _J, -1)
        tpe.tell(pts, ys)
    return best
