# -*- coding: utf-8 -*-
"""
steps.py — 构型适航性评估管线七步实现，L2 线事项 I-173。

接口契约见同目录 PIPELINE_CONTRACT.md 与 pipeline_contract.json。每步为
纯函数：进固定输入键，出固定输出键。S1 与 S2 的 A 部分、S6 直接复用
ltv/hss_i102_stab.py 的在案实现（I-102 提交 8a63308），S6 判读核复用
ltv/hss_i079_adapter.py（I-079 提交 7d105ac）；S2 的 B 列、S3 参与因子、
S4 格拉姆矩阵、S5 稳定裕度为新理论条目，公式全文写于契约 endorsements
节，待 L4 背书。

状态名表次序为机体前向速度 u、机体侧向速度 vy、机体竖向速度 wb、滚转角
roll、俯仰角 pitch、滚转角速度 wx、俯仰角速度 wy、偏航角速度 wz。输入
名表次序为升降舵 elevator、差动舵 aileron、方向舵 rudder、拍动频率
freq（赫兹）。
"""
import contextlib
import json
import math
import os
import sys

import numpy as np
from scipy.linalg import solve_continuous_lyapunov

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "ltv")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

STATE_NAMES = ["u", "vy", "wb", "roll", "pitch", "wx", "wy", "wz"]
INPUT_NAMES = ["elevator", "aileron", "rudder", "freq"]
G = 9.81
SAIL_SAT_ABS = 0.295
ACTUATION_PRIOR = {"fn_hz": 2.5, "zeta": 0.15, "td_s": 0.05}


def _i102():
    """惰性导入 I-102 工具模块。模块导入即构造拍平均力模型一次。"""
    import ltv.hss_i102_stab as m
    return m


@contextlib.contextmanager
def config_override(overrides):
    """按覆盖表临时修改构型参数，退出时恢复。覆盖键域：tail.AREA、
    tail.LEVER_ARM、tail.PANEL_Y、Ixx、Iyy、Izz、tail_mass_delta（尾件
    点质量迁移加平行轴惯量，同 I-102 敏感度档口径）、MASS。"""
    i102 = _i102()
    import config.sim_config as cfg
    old = []
    for key, val in overrides.items():
        if key.startswith("tail."):
            obj, attr = cfg.TailAeroConfig, key[5:]
            old.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, val)
        elif key in ("Ixx", "Iyy", "Izz"):
            idx = {"Ixx": 0, "Iyy": 1, "Izz": 2}[key]
            old.append((i102.I_DIAG, idx, i102.I_DIAG[idx]))
            i102.I_DIAG[idx] = val
        elif key == "MASS":
            old.append((cfg, "MASS", cfg.MASS))
            cfg.MASS = val
            old.append((i102, "MASS", i102.MASS))
            i102.MASS = val
        elif key == "tail_mass_delta":
            d = val
            arm_x, arm_y = 0.45, 0.224
            old.append((i102.I_DIAG, 0, i102.I_DIAG[0]))
            old.append((i102.I_DIAG, 1, i102.I_DIAG[1]))
            old.append((i102.I_DIAG, 2, i102.I_DIAG[2]))
            i102.I_DIAG[0] += d * arm_y ** 2
            i102.I_DIAG[1] += d * (arm_x ** 2 + arm_y ** 2)
            i102.I_DIAG[2] += d * arm_x ** 2
        else:
            raise ValueError("未知覆盖键：%s" % key)
    try:
        yield
    finally:
        for obj, attr, val in old:
            if isinstance(attr, int):
                obj[attr] = val
            else:
                setattr(obj, attr, val)


# ─────────────────── S1 配平 ───────────────────

def step1_trim(bank_deg, config_overrides=None, freq_hz=1.8):
    """机体坐标系稳态协调转弯配平，复用 I-102 的 plant_trim。平衡残差按
    plant_trim 同款六方程重算作为复核读数（公式同源，独立求值）。"""
    i102 = _i102()
    with config_override(config_overrides or {}):
        x, wz = i102.plant_trim(bank_deg)
        resid = _trim_residual(i102, x, wz, bank_deg, freq_hz)
    saturated = [bool(abs(float(v)) > SAIL_SAT_ABS) for v in x[4:7]]
    at_bound = _bound_touch(x)
    return {"bank_deg": bank_deg, "x_trim": [float(v) for v in x],
            "freq_hz": freq_hz, "wz_coordinated": float(wz),
            "residual_norm": float(resid), "saturated": saturated,
            "at_bound": at_bound,
            "trim_pass": bool(resid < 1e-6 and not any(saturated))}


_TRIM_BOUNDS = (np.array([2.0, -1.0, -1.5, -0.6, -0.30, -0.30, -0.30]),
                np.array([5.0, 1.0, 1.5, 0.35, 0.30, 0.30, 0.30]))
_TRIM_NAMES = ["u", "vy", "wb", "pitch", "elev", "ail", "rud"]


def _bound_touch(x, rel_tol=5e-3):
    """检测配平解是否贴 plant_trim 的搜索边界（同款边界值，同源声
    明）。贴界说明真实解在界外或解被边界卡住，配平质量受限。"""
    lo, hi = _TRIM_BOUNDS
    hits = []
    for i, v in enumerate(x):
        if hi[i] - float(v) <= rel_tol * (1.0 + abs(hi[i])):
            hits.append("%s 贴上界 %.3f" % (_TRIM_NAMES[i], hi[i]))
        elif float(v) - lo[i] <= rel_tol * (1.0 + abs(lo[i])):
            hits.append("%s 贴下界 %.3f" % (_TRIM_NAMES[i], lo[i]))
    return hits


def _trim_residual(i102, x, wz, bank_deg, freq_hz):
    """配平残差复核：力平衡三条含向心项加三轴力矩归零，公式与
    plant_trim 的 full 同款。"""
    u, vy, wb, pitch, elev, ail, rud = x
    roll = math.radians(bank_deg)
    quat = i102.quat_from_rpy(roll, pitch, 0.0)
    R = i102.rot_body_from_world(quat)
    v_b = np.array([u, vy, wb])
    v_w = R.T @ v_b
    F, T = i102.WRENCH(v_w, quat, np.array([0.0, 0.0, wz]), freq_hz,
                       tail_cmd=(elev, ail, rud))
    F_b = R @ F
    g_b = R @ np.array([0.0, 0.0, -G])
    w_v = np.array([0.0, 0.0, wz])
    eq = np.concatenate([F_b / i102.MASS + g_b - np.cross(w_v, v_b), T])
    return float(np.linalg.norm(eq))


# ─────────────────── S2 线性化 ───────────────────

def step2_linearize(trim, eps=1e-5):
    """八态开环线性化。A 部分直接调用 I-102 的 open_loop_A；B 列对四个
    输入在配平值处中心差分，右端函数与 open_loop_A 的 deriv 同款公式，
    差别仅把舵面向量与频率参数化。"""
    i102 = _i102()
    x7 = trim["x_trim"]
    wz = trim["wz_coordinated"]
    bank = trim["bank_deg"]
    freq0 = trim["freq_hz"]
    A, s0 = i102.open_loop_A(tuple(x7), wz, roll_deg=bank)
    s0 = np.asarray(s0, dtype=float)
    cmd0 = [x7[4], x7[5], x7[6]]

    def fd(inp_p, inp_m):
        return (_rhs_open_loop(i102, s0, inp_p[:3], inp_p[3])
                - _rhs_open_loop(i102, s0, inp_m[:3], inp_m[3])) / (2 * eps)

    B = np.zeros((8, 4))
    for k in range(3):
        cp, cm = list(cmd0), list(cmd0)
        cp[k] += eps
        cm[k] -= eps
        B[:, k] = fd(cp + [freq0], cm + [freq0])
    B[:, 3] = fd(cmd0 + [freq0 + eps], cmd0 + [freq0 - eps])
    return {"A": A, "B": B, "state_names": STATE_NAMES,
            "input_names": INPUT_NAMES,
            "trim_state": s0.tolist(),
            "fd_eps": eps,
            "tail_clip": _tail_clip_state(trim)}


def _tail_clip_state(trim):
    """复算配平点尾面攻角与侧滑角相对截断界的位置，公式与
    modules/aero/tail_fuselage.py 的 compute 同款。舵增量落在截断平台
    上时该舵的输入矩阵列近零，此检测给出原因读数。"""
    import config.sim_config as cfg
    u, vy, wb, pitch, elev, ail, rud = trim["x_trim"]
    v_b = np.array([u, vy, wb])
    v_flow = v_b.copy()
    v_flow[0] += max(cfg.SLIP_K * cfg.SLIP_V_PER_HZ * trim["freq_hz"], 0.0)
    alpha_body = math.atan2(v_flow[2], max(v_flow[0], 0.1))
    beta_body = math.atan2(-v_flow[1], max(v_flow[0], 0.1))
    tc = cfg.TailAeroConfig
    a_stall = math.radians(tc.STALL_ANGLE)
    a_sum = alpha_body + elev
    b_sum = beta_body + rud
    return {"alpha_body_deg": float(math.degrees(alpha_body)),
            "beta_body_deg": float(math.degrees(beta_body)),
            "stall_deg": float(tc.STALL_ANGLE),
            "elev_sum_deg": float(math.degrees(a_sum)),
            "rudder_sum_deg": float(math.degrees(b_sum)),
            "elev_clipped": bool(abs(a_sum) > a_stall),
            "rudder_clipped": bool(abs(b_sum) > a_stall)}


def _rhs_open_loop(i102, s, cmd, freq):
    """开环状态方程右端，与 open_loop_A 的 deriv 同款公式（声明同源），
    舵面向量与频率参数化供 B 列偏导。返回八元导数。"""
    u, vy, wb, roll, pitch, wx, wy, wzz = s
    quat = i102.quat_from_rpy(roll, pitch, 0.0)
    R = i102.rot_body_from_world(quat)
    v_b = np.array([u, vy, wb])
    v_w = R.T @ v_b
    w_v = np.array([wx, wy, wzz])
    F, T = i102.WRENCH(v_w, quat, w_v, freq, tail_cmd=tuple(cmd))
    F_b = R @ F
    g_b = R @ np.array([0.0, 0.0, -G])
    vdot = F_b / i102.MASS + g_b - np.cross(w_v, v_b)
    wIw = np.cross(w_v, i102.I_DIAG * w_v)
    wdot = (T - wIw) / i102.I_DIAG
    return np.array([vdot[0], vdot[1], vdot[2], wx, wy,
                     wdot[0], wdot[1], wdot[2]])


def scope_extract(lin, C_rows, scope):
    """按分析域取状态与输入子块，并取观测矩阵子块。返回子系统的 A、B、
    C、状态名、输入名、观测名。观测行必须属于该域的状态集，不属于时
    直接报错拒绝（构造级校验，不静默剔除）。"""
    sidx = {"lateral": [1, 3, 5, 6, 7],
            "longitudinal": [0, 2, 4, 6],
            "full": list(range(8))}[scope]
    iidx = {"lateral": [1, 2],
            "longitudinal": [0, 3],
            "full": list(range(4))}[scope]
    rowpos = {n: i for i, n in enumerate(STATE_NAMES)}
    bad = [n for n in C_rows if n not in
           [STATE_NAMES[i] for i in sidx]]
    if bad:
        raise ValueError("观测行 %s 不属于分析域 %s 的状态集，请改用域内"
                         "状态名或更换分析域" % (bad, scope))
    ridx = [rowpos[n] for n in C_rows]
    A = lin["A"][np.ix_(sidx, sidx)]
    B = lin["B"][np.ix_(sidx, iidx)]
    C = np.zeros((len(ridx), len(sidx)))
    for r, ri in enumerate(ridx):
        C[r, sidx.index(ri)] = 1.0
    return {"A": A, "B": B, "C": C,
            "state_names": [STATE_NAMES[i] for i in sidx],
            "input_names": [INPUT_NAMES[i] for i in iidx],
            "C_rows": list(C_rows), "scope": scope,
            "scope_idx": sidx, "input_idx": iidx}


# ─────────────────── S3 模态分解 ───────────────────

def step3_modes(A):
    """特征值清单加参与因子。参与因子为左特征向量元素乘右特征向量元素按
    模态归一（契约 S3 节公式，待背书），取实部参与。"""
    n = A.shape[0]
    lam, V = np.linalg.eig(A)
    W = np.linalg.inv(V)
    rows = []
    part = np.zeros((n, n))
    for i in range(n):
        col = np.real(W[i, :] * V[:, i])
        part[:, i] = col / np.sum(col)
    for i in range(n):
        li = complex(lam[i])
        zeta = -li.real / abs(li) if abs(li) > 1e-12 else 0.0
        rows.append({"re": float(li.real), "im": float(li.imag),
                     "hz": float(abs(li.imag) / 2 / np.pi),
                     "zeta": float(zeta),
                     "tag": "不稳定" if li.real > 0 else
                            ("弱阻尼" if li.real > -1.0 else "稳定")})
    order = sorted(range(n), key=lambda i: -lam[i].real)
    lam, part = lam[order], part[:, order]
    rows = [rows[i] for i in order]
    return {"eigenvalues": rows,
            "participation": part.tolist(),
            "participation_states": None,
            "mode_tags": None}


def label_modes(modes, state_names):
    """按参与因子绝对值最大的状态分量给模态命名，两分量同量级时联合命
    名（次分量超过最大分量八成时）。"""
    part = np.abs(np.asarray(modes["participation"]))
    tags = []
    for i in range(part.shape[1]):
        order = np.argsort(-part[:, i])
        top, second = order[0], order[1]
        if part[second, i] > 0.8 * part[top, i]:
            tags.append("%s-%s耦合" % (state_names[top],
                                       state_names[second]))
        else:
            tags.append(state_names[top])
    modes["mode_tags"] = tags
    modes["participation_states"] = list(state_names)
    return modes


# ─────────────────── S4 可控性与可观性 ───────────────────

def step4_gramians(sub, shift_margin=0.5):
    """秩判据加平移格拉姆矩阵（契约 S4 节公式，待背书）。平移量等于最大
    实部加零点五（最大实部为负时取零点五，保持统一口径）。"""
    A, B, C = sub["A"], sub["B"], sub["C"]
    n = A.shape[0]
    eigs = np.linalg.eigvals(A)
    shift = max(0.0, float(np.max(eigs.real))) + shift_margin
    M = A - shift * np.eye(n)
    Wc = solve_continuous_lyapunov(M, -B @ B.T)
    Wo = solve_continuous_lyapunov(M.T, -C.T @ C)
    rc = _lyap_relres(M, Wc, B @ B.T)
    ro = _lyap_relres(M.T, Wo, C.T @ C)
    ctrb = np.hstack([np.linalg.matrix_power(A, i) @ B
                      for i in range(n)])
    obsv = np.vstack([C @ np.linalg.matrix_power(A, i)
                      for i in range(n)])
    lam, V = np.linalg.eig(A)
    W = np.linalg.inv(V)
    modal = []
    for i in range(n):
        w_i = W[i, :].conj()
        strength = float(np.linalg.norm(B.T @ w_i)
                         / (abs(lam[i].real) + shift))
        modal.append({"re": float(lam[i].real),
                      "im": float(lam[i].imag),
                      "strength": strength})
    modal.sort(key=lambda r: -r["re"])
    strengths = [m["strength"] for m in modal]
    return {"ctrb_rank": int(np.linalg.matrix_rank(ctrb)),
            "obsv_rank": int(np.linalg.matrix_rank(obsv)),
            "Wc": Wc.tolist(), "Wo": Wo.tolist(),
            "Wc_sigma_min": float(np.linalg.svd(Wc, compute_uv=False)[-1]),
            "Wo_sigma_min": float(np.linalg.svd(Wo, compute_uv=False)[-1]),
            "shift": float(shift),
            "modal_controllability": modal,
            "lyap_residuals": [rc, ro],
            "min_strength": float(min(strengths)),
            "median_strength": float(np.median(strengths))}


def _lyap_relres(M, X, Q):
    """李雅普诺夫方程相对残差范数。"""
    num = np.linalg.norm(M @ X + X @ M.T + Q)
    den = np.linalg.norm(Q)
    return float(num / max(den, 1e-300))


# ─────────────────── S5 稳定裕度 ───────────────────

def step5_margins(sub, modes, actuation=None, n_grid=1000):
    """契约 S5 节：模态阻尼表、逐通道单输入单出裕度（开环稳定通道）、不
    稳定模态稳定化增益需求（逐速率反馈通道扫描）、回差矩阵最小奇异值频
    率表（公式待背书）。"""
    A, B, C = sub["A"], sub["B"], sub["C"]
    act = dict(ACTUATION_PRIOR)
    act.update(actuation or {})
    n = A.shape[0]
    lam = np.linalg.eigvals(A)
    open_stable = bool(np.all(lam.real < 0))
    modal = []
    for li in lam:
        modal.append({"re": float(li.real), "im": float(li.imag),
                      "zeta": float(-li.real / abs(li)),
                      "half_or_double_time_s":
                          (float(math.log(2.0) / abs(float(li.real)))
                           if abs(li.real) > 1e-15 else None),
                      "kind": "倍幅" if li.real > 0 else "半幅"})
    modal.sort(key=lambda r: -r["re"])

    freqs = np.logspace(math.log10(0.01), math.log10(100.0), n_grid)
    margins = []
    for k in range(B.shape[1]):
        row = _best_output_row(A, C, B[:, k], freqs, act)
        row_vec = C[row, :] if row is not None else C[0, :]
        Lmag, Lph = _loop_response(A, row_vec, B[:, k], freqs, act)
        m = _classic_margins(freqs, Lmag, Lph)
        m.update({"input": sub["input_names"][k],
                  "output": sub["C_rows"][int(row)] if row is not None
                  else None,
                  "valid": bool(open_stable and m.get("gm_db") is not None)})
        margins.append(m)
    stab = _stabilizing_gains(sub)
    sigma, sigma_kind = _retdiff_sigma_min(A, B, C, freqs, act)
    return {"open_loop_stable": open_stable, "modal_damping": modal,
            "simo_margins": margins, "stabilizing_gains": stab,
            "retdiff_freqs_hz": freqs.tolist(),
            "retdiff_sigma_min": sigma, "retdiff_kind": sigma_kind,
            "actuation": act}


def _act_chain(s, act):
    """执行链频率响应：二阶共振加纯延迟（与 hss_kernel 执行链同构，
    参数取通用先验，可被辨识产物覆盖）。"""
    wn = 2 * np.pi * act["fn_hz"]
    return (wn ** 2 / (s ** 2 + 2 * act["zeta"] * wn * s + wn ** 2)
            * np.exp(-act["td_s"] * s))


def _tf_eval(A, row, b, freqs):
    """频点上的状态到输出传函加输入列：row (jwI-A)^-1 b。"""
    n = A.shape[0]
    I = np.eye(n)
    return np.array([complex(row @ np.linalg.solve(
        2j * np.pi * f * I - A, b)) for f in freqs])


def _best_output_row(A, C, b, freqs, act):
    """选对该输入通道频带增益最大的观测行（契约 S5 节口径）。"""
    if C.shape[0] == 1:
        return 0
    best, best_gain = None, -1.0
    for r in range(C.shape[0]):
        tf = _tf_eval(A, C[r, :], b, freqs) * _act_chain(
            2j * np.pi * freqs, act)
        g = float(np.max(np.abs(tf)))
        if g > best_gain:
            best, best_gain = r, g
    return best


def _loop_response(A, row, b, freqs, act):
    """回路响应，符号归一：低频回路增益为负时整回路乘负一（单输入单
    出裕度分析的标准预处理，否则相位从正负一百八十度起算，裕度读数
    无常规含义）。相位展开后对齐低频主值区间。"""
    tf = _tf_eval(A, row, b, freqs) * _act_chain(2j * np.pi * freqs, act)
    if tf[0].real < 0:
        tf = -tf
    ph = np.degrees(np.unwrap(np.angle(tf)))
    return np.abs(tf), ph


def _classic_margins(freqs, Lmag, Lph):
    """增益交越与相位交越插值，返回裕度。相位裕度按工程口径折算到
    主值区间负一百八十度到一百八十度（回路过共振峰时相位绕圈，折算
    前的绕圈数单独留痕）。交越点缺失时报空值。"""
    gm_db = pm_deg = w_gc = w_pc = None
    wraps = 0
    s1 = np.sign(Lmag - 1.0)
    for i in np.where(np.diff(s1) != 0)[0]:
        f0, f1 = freqs[i], freqs[i + 1]
        m0, m1 = Lmag[i], Lmag[i + 1]
        w = f0 + (1.0 - m0) / (m1 - m0) * (f1 - f0)
        ph = np.interp(w, freqs, Lph)
        pm_raw = 180.0 + ph
        pm_deg = ((pm_raw + 180.0) % 360.0) - 180.0
        wraps = int(round((pm_raw - pm_deg) / 360.0))
        w_gc = float(w)
        break
    s180 = np.sign(Lph + 180.0)
    for i in np.where(np.diff(s180) != 0)[0]:
        f0, f1 = freqs[i], freqs[i + 1]
        p0, p1 = Lph[i], Lph[i + 1]
        w = f0 + (-180.0 - p0) / (p1 - p0) * (f1 - f0)
        mag = np.interp(w, freqs, Lmag)
        gm_db = -20.0 * math.log10(max(mag, 1e-12))
        w_pc = float(w)
        break
    return {"gm_db": None if gm_db is None else float(gm_db),
            "pm_deg": None if pm_deg is None else float(pm_deg),
            "pm_wraps": wraps,
            "w_gc_hz": w_gc, "w_pc_hz": w_pc}


def _stabilizing_gains(sub, k_lo=-3.0, n_k=61):
    """不稳定模态稳定化增益需求：逐速率反馈通道（差动接滚转率、方向接
    偏航率、升降接俯仰率，同 I-102 的 fb 机制）扫增益，取使全部正实部
    特征值实部转负的最小增益绝对值；单通道做不到时报不可稳定化并给最
    小达到的最大实部。"""
    A, B = sub["A"], sub["B"]
    names = sub["input_names"]
    state_names = sub["state_names"]
    lam0 = np.linalg.eigvals(A)
    if np.all(lam0.real < 0):
        return {"needed": False, "note": "开环已稳定，无需稳定化"}
    rate_map = {"aileron": "wx", "rudder": "wz", "elevator": "wy"}
    rows = []
    for k_col, name in enumerate(names):
        rate = rate_map.get(name)
        if rate is None or rate not in state_names:
            continue
        r = state_names.index(rate)
        ks = np.linspace(0.0, k_lo, n_k)
        best_k, best_max_re = None, None
        for kk in ks:
            lam = np.linalg.eigvals(A + kk * np.outer(B[:, k_col],
                                                      np.eye(A.shape[0])[r]))
            mx = float(np.max(lam.real))
            if best_max_re is None or mx < best_max_re:
                best_max_re, best_k = mx, float(kk)
            if mx < 0:
                break
        if best_max_re is not None and best_max_re < 0:
            rows.append({"input": name, "feedback": rate,
                         "min_gain": best_k, "achievable": True})
        else:
            rows.append({"input": name, "feedback": rate,
                         "min_gain": None, "achievable": False,
                         "best_max_re": best_max_re})
    return {"needed": True, "channels": rows}


def _retdiff_sigma_min(A, B, C, freqs, act):
    """回差矩阵最小奇异值随频率。观测数等于输入数（方阵回路）时报回
    差口径 σ_min(I+L)；非方阵时报回路口径 σ_min(L)（多变量频域分析的
    标准处理，Skogestad 口径：非方阵回路回差不定义，取回路最小奇异
    值）。返回读数表与口径名。"""
    n = A.shape[0]
    I = np.eye(n)
    square = (C.shape[0] == B.shape[1])
    out = []
    for f in freqs:
        X = np.linalg.solve(2j * np.pi * f * I - A, B)
        L = C @ X * _act_chain(2j * np.pi * f, act)
        M = np.eye(L.shape[1]) + L if square else L
        out.append(float(np.linalg.svd(M, compute_uv=False)[-1]))
    kind = "回差 σ_min(I+L)" if square else "回路 σ_min(L)（非方阵口径）"
    return out, kind


# ─────────────────── S6 周期口径复核 ───────────────────

def step6_floquet(trim, n_phi=24):
    """复用 I-102 的 periodic_A 逐相位偏导加 I-079 的 read_floquet 判读
    核。周期矩阵只含被控对象，控制器不在内（与 I-102 同款声明）。"""
    i102 = _i102()
    from hss_i079_adapter import read_floquet
    u, vy, wb, pitch = trim["x_trim"][:4]
    wz = trim["wz_coordinated"]
    phys = [u, vy, wb, math.radians(trim["bank_deg"]), pitch,
            0.0, 0.0, wz]
    with config_override(trim.get("config_overrides") or {}):
        A0, Ac, As, _A_phi = i102.periodic_A(phys)
    env = {"tag": "airworthiness_bank%g" % trim["bank_deg"],
           "path": "airworthiness 管线内构造", "A0": A0, "Ac": [Ac],
           "As": [As], "K": 1, "f_stroke": trim["freq_hz"],
           "window": None, "state_dim": 3}
    r = read_floquet(env)
    ac_norm = float(np.linalg.norm(np.asarray(Ac)))
    as_norm = float(np.linalg.norm(np.asarray(As)))
    note = None
    if max(ac_norm, as_norm) < 1e-9:
        note = ("周期项范数为数值噪声级（余弦项 %.1e、正弦项 %.1e）：力"
                "矩对角速度的偏导不随拍相位变化，为模型结构事实（旋转"
                "阻尼为常数系数项，拍翼周期力对其角速度导数的调制为零"
                "）。本读数退化为常数旋转阻尼的拍周期判读，真周期时变"
                "判读需更大的状态集口径，属 L4 理论域。"
                % (ac_norm, as_norm))
    return {"Ac": np.asarray(Ac).tolist(),
            "As": np.asarray(As).tolist(),
            "Ac_norm": ac_norm, "As_norm": as_norm,
            "periodic_note": note,
            "max_mu": float(r["max_mu"]),
            "max_re_per_s": float(r["max_re"]),
            "mu": [float(v) for v in r["mu"]],
            "reading": "拍周期最大增长倍率 %.5f，弗洛凯指数实部 "
                       "%+.4f 每秒" % (r["max_mu"], r["max_re"])}


# ─────────────────── 供报告与登记用的指纹 ───────────────────

def config_fingerprint(config_overrides=None):
    """构型指纹：参与计算的构型参数快照与规范化哈希。"""
    import config.sim_config as cfg
    i102 = _i102()
    ta = cfg.TailAeroConfig
    snap = {"MASS": float(cfg.MASS),
            "I_DIAG": [float(v) for v in i102.I_DIAG],
            "tail_AREA": float(ta.AREA),
            "tail_LEVER_ARM": float(ta.LEVER_ARM),
            "tail_PANEL_Y": float(ta.PANEL_Y),
            "wing_AMPLITUDE_deg":
                float(np.degrees(cfg.FlapKinematicsConfig.AMPLITUDE)),
            "SLIP_K": float(cfg.SLIP_K),
            "SLIP_V_PER_HZ": float(cfg.SLIP_V_PER_HZ),
            "overrides": config_overrides or {}}
    blob = json.dumps(snap, ensure_ascii=False, sort_keys=True)
    import hashlib
    return {"snapshot": snap,
            "sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest()}
