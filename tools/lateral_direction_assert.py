# -*- coding: utf-8 -*-
"""
lateral_direction_assert.py — 横向方向断言套件（侧滑-滚转梯度符号链鉴别）

背景（第七十一班堵墙上报的疑点标注）：转弯动态定性结论"侧滑-滚转
梯度 +0.00042 N·m/deg 为发散性"采信前，必须鉴别其符号链。历史依据：
镜像变换是本项目符号错误重灾区（第十八班 ω×r 镜像符号 bug 同位置；
第首两版右翼局部系手性反）。本套件不依赖"左/右"命名语义，全部用
几何结构与解析推导锚定。

解析锚定（一阶展开，主会话推导，写入本文件供复核）：
翼展单位矢 n̂=(sinφ,−cosφ,0)，叶素位置 r_body=r(sinφ,−cosφ,0)+r0。
纯前飞加速滑来流体轴速度 v=(V,vy,0)，相对流 v_rel=−v。翼展投影
v_span=(v_rel·n̂)n̂ 吸收侧滑分量：
  v_rel·n̂ = −V·sinφ + vy·cosφ。
一阶展开（φ=固定 m，|vy|≪V）下，侧滑对 v_perp 的扰动
δv_perp = −vy·sinm·(cosm,sinm,0)，与零阶方向 v̂_perp0=(−cosm,−sinm,0)
严格平行（投影 (−vy sinm c⃗)·(−c⃗)=vy sinm 全部落入平行分量），
故攻角扰动 δα=δα v̂_perp 转动=0、法向力方向扰动=0。
推论A（断言C1/C2 的依据）：固定翼面角（无拍动）下，侧滑-滚转
梯度的一阶严格为零；平翼（m=0）下 vy 全部进翼展分量，任意阶对称。
推论B：非零梯度只能来自（a）拍动与侧滑的交互（φ 随时间转动时
翼展投影对 vy 的吸收随相位变化）；（b）镜像包装层符号错误。
断言套件据此分层锁定。

五层断言：
C1 结构零：amp=0、mean=0（平翼）侧滑梯度中心差分 |dTx/dβ|<1e-9。
C2 一阶零：amp=0、mean=±19.81°（斜板翼）中心差分 |dTx/dβ|<1e-6，
   并记录二阶量级。
C3 mean 奇对称：全波形（amp=17.73）下 mean=+19.81 与 mean=−19.81
   的梯度近似反号，mean=0 的梯度显著小于 |mean|=19.81 档（耦合 ∝
   扑动面偏置的主导性检验）。
C4 镜像恒等式：wing_forces_body 双翼总输出逐位等于
   T_Lx(v) − T_Lx(Mir@v)（镜像包装层的构造性正确性，第十八班 bug
   同位置检验）。
C5 相位分解预测：全波形梯度 = 各固定翼面角静态交互核的相位加权
   （数值积分），独立预测符号与量级，与实测对比。预测吻合则梯度
   符号=几何真值；反号则符号链有 bug。

写权限：主会话亲写；代理只执行。运行：python tools/lateral_direction_assert.py
产物：logs/lateral_direction_assert_<ts>.json + 控制台判定表。
"""
import os, sys, json, math, datetime
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import importlib.util


def _load_v1():
    path = os.path.join(PROJECT_ROOT, "tools", "unified_search_framework.py")
    spec = importlib.util.spec_from_file_location("usf_v1_latassert", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BASE = {"alpha_set": 32.3169, "mean": 19.8105, "split": 0.176,
        "lag": -0.0674, "amp": 17.73, "freq": 1.80, "speed": 3.95}
BETA_DEG = 1.0          # 中心差分步长（度）


def wing_tx_static(v1, phi_fixed_deg, vy_body, alpha_deg=0.0):
    """固定翼面角（无拍动速度/加速度）的单点左翼 canonical 求值。
    直接构造 wing_state 绕过 evaluate（拍动状态全零，phi=给定值，
    alpha=给定值），尾翼/直流不参与（纯翼几何核）。"""
    m = v1.model
    ws = {"phi": math.radians(phi_fixed_deg), "alpha": math.radians(alpha_deg),
          "phi_dot": 0.0, "alpha_dot": 0.0, "phi_ddot": 0.0}
    v_body = np.array([BASE["speed"], vy_body, 0.0])
    F, T = m._wing_forces_canonical(m._r_centers.copy(), ws, v_body, np.zeros(3))
    return float(T[0]), float(F[2])


def dtx_dbeta_static(v1, phi_fixed_deg, beta_deg=BETA_DEG, alpha_deg=0.0):
    """静态固定翼面角的侧滑-滚转梯度（中心差分，体轴 vy=speed·sinβ）。"""
    vy_p = BASE["speed"] * math.sin(math.radians(beta_deg))
    tx_p, _ = wing_tx_static(v1, phi_fixed_deg, +vy_p, alpha_deg)
    tx_m, _ = wing_tx_static(v1, phi_fixed_deg, -vy_p, alpha_deg)
    return (tx_p - tx_m) / (2.0 * beta_deg)


def dtx_dbeta_wave(v1, wave, beta_deg=BETA_DEG, n=288):
    """全波形（拍动中）的周期平均侧滑-滚转梯度（与测量B同口径：
    世界系来流偏航 psi=β，体轴 vy 由此产生；这里直接构造体轴 vy≈
    speed·sinβ 的小角近似，与测量B 的差别记入报告）。"""
    v1.set_waveform(wave)
    v1.model.set_wing_command(frequency=wave["freq"])
    beta = math.radians(beta_deg)
    out = {}
    for tag, sgn in (("p", +1.0), ("m", -1.0)):
        psi = sgn * beta
        quat = v1.euler_to_quat(0.0, math.radians(-8.916), 0.0)
        vel = np.array([wave["speed"] * math.cos(psi),
                        wave["speed"] * math.sin(psi), 0.0])
        vb = v1.rot_world_to_body(quat, vel)
        T = 1.0 / wave["freq"]
        Tx = 0.0
        for i in range(n):
            t = v1.T0[0] + i * T / n
            _, Tw = v1.wing_forces_body(quat, vel, np.zeros(3), t)
            Tx += float(Tw[0])
        v1.T0[0] += T
        out[tag] = Tx / n
    return (out["p"] - out["m"]) / (2.0 * beta_deg)


def c1_flat_wing(v1):
    g = dtx_dbeta_static(v1, 0.0)
    ok = abs(g) < 1e-9
    print("C1 平翼结构零: dTx/dβ=%+.3e  判定=%s" % (g, "PASS" if ok else "FAIL"), flush=True)
    return {"name": "C1_flat_wing", "value": g, "tol": 1e-9, "pass": bool(ok)}


def c2_fixed_dihedral(v1):
    res = {}
    ok_all = True
    for mdeg in (+19.8105, -19.8105):
        g = dtx_dbeta_static(v1, mdeg)
        ok = abs(g) < 1e-6
        ok_all = ok_all and ok
        res["mean_%+g" % mdeg] = g
        print("C2 斜板翼 mean=%+g: dTx/dβ=%+.3e（一阶零判定 %s）"
              % (mdeg, g, "PASS" if ok else "FAIL"), flush=True)
    return {"name": "C2_fixed_dihedral", "values": res, "tol": 1e-6,
            "pass": bool(ok_all)}


def c3_mean_symmetry(v1):
    res = {}
    for mtag, mean in (("pos", 19.8105), ("zero", 0.0), ("neg", -19.8105)):
        w = dict(BASE); w["mean"] = mean
        res[mtag] = dtx_dbeta_wave(v1, w)
        print("C3 全波形 mean=%+g: dTx/dβ=%+.6f N·m/deg" % (mean, res[mtag]), flush=True)
    # 判定：pos 与 neg 近似反号（比值在 -0.5~-2 区间）；zero 显著小于两者
    ratio = res["neg"] / res["pos"] if abs(res["pos"]) > 1e-9 else float("nan")
    ok = (-2.0 <= ratio <= -0.5) and (abs(res["zero"]) <
          0.5 * max(abs(res["pos"]), abs(res["neg"])))
    print("C3 判定: neg/pos=%.3f（期望≈−1）mean=0 档=%.6f（期望显著小）→ %s"
          % (ratio, res["zero"], "PASS" if ok else "FAIL"), flush=True)
    return {"name": "C3_mean_symmetry", "values": res, "ratio_neg_pos": ratio,
            "pass": bool(ok)}


def c4_mirror_identity(v1):
    """镜像恒等式 Tx_total(t,β) == T_Lx(v) − T_Lx(Mir@v) 逐位校验
    （K=8 平均包装外、单时刻 t、含拍动状态）。"""
    w = dict(BASE)
    v1.set_waveform(w)
    v1.model.set_wing_command(frequency=w["freq"])
    quat = v1.euler_to_quat(0.0, math.radians(-8.916), 0.0)
    beta = math.radians(BETA_DEG)
    vel = np.array([w["speed"] * math.cos(beta), w["speed"] * math.sin(beta), 0.0])
    m = v1.model
    max_diff = 0.0
    t0 = v1.T0[0]
    for k in range(16):
        t = t0 + k * (1.0 / w["freq"]) / 16.0
        _, Ttot = v1.wing_forces_body(quat, vel, np.zeros(3), t)
        vb = v1.rot_world_to_body(quat, vel)
        wsL = m.wing_left.evaluate(t, phase_offset_rad=0.0)
        FL, TL = m._wing_forces_canonical(m._r_centers.copy(), wsL,
                                          vb, np.zeros(3))
        Mir = np.diag([1.0, -1.0, 1.0])
        wsR = m.wing_right.evaluate(t, phase_offset_rad=m.phase_offset_lr)
        # 恒等式本体：Tx_total = T_Lx(v) − T_Lx(Mir@v)（右翼在标准系以
        # Mir@v 求值后力矩经 −Mir 变换回机体系，x 分量即取负）
        _, TLm = m._wing_forces_canonical(m._r_centers.copy(), wsL,
                                          Mir @ vb, np.zeros(3))
        pred = float(TL[0]) - float(TLm[0])
        max_diff = max(max_diff, abs(float(Ttot[0]) - pred))
    ok = max_diff < 1e-10
    print("C4 镜像恒等式: 16 相位点最大偏差=%.3e  判定=%s"
          % (max_diff, "PASS" if ok else "FAIL"), flush=True)
    return {"name": "C4_mirror_identity", "max_diff": max_diff,
            "tol": 1e-10, "pass": bool(ok)}


def c5_phase_decomposition(v1):
    """相位分解预测：静态交互核 g(φ)=dTx/dβ|静态(翼面角φ) 的拍动
    相位平均 vs 全波形实测梯度。交互核按推论来自拍动-侧滑交互，其
    相位权重=每相位停留时间（split 相位重映射下均匀时间格天然正确）。"""
    # 静态交互核（alpha 取波形的中位攻角近似：alpha_set 的行程内代表值
    # 用 alpha_set 本身；敏感性另记）
    phis_deg = [(-80.0 + 160.0 * i / 15.0) for i in range(16)]
    kern = {}
    for al in (0.0, math.radians(32.3169)):
        kern[al] = [dtx_dbeta_static(v1, p, alpha_deg=math.degrees(al)) for p in phis_deg]
    # 波形 φ(t) 的相位轨迹（用 kinematics 本体，phi=bias+mean+amp·sin(ph)）
    m = v1.model
    bias0 = float(getattr(m.wing_left, "bias", 0.0))
    w = dict(BASE)
    v1.set_waveform(w)
    mean_r = math.radians(19.8105); amp_r = math.radians(17.73)
    pred = {al: 0.0 for al in kern}
    n = 288
    for i in range(n):
        t = v1.T0[0] + i * (1.0 / w["freq"]) / n
        ph, _ = m.wing_left._phase_sc(t, 0.0)
        phi_deg = math.degrees(bias0 + mean_r + amp_r * math.sin(ph))
        # 线性插值取核值
        for al in kern:
            kvals = kern[al]
            x = (phi_deg + 80.0) / 160.0 * 15.0
            x = min(max(x, 0.0), 14.9999)
            i0 = int(x); frac = x - i0
            pred[al] += (kvals[i0] * (1 - frac) + kvals[i0 + 1] * frac) / n
    v1.T0[0] += 1.0 / w["freq"]
    meas = dtx_dbeta_wave(v1, w)
    print("C5 相位分解预测 vs 实测:", flush=True)
    for al, pv in pred.items():
        print("   alpha=%4.1f°: 预测=%+.6f  实测=%+.6f  比值=%.3f"
              % (math.degrees(al), pv, meas, pv / meas if abs(meas) > 1e-12 else float("nan")), flush=True)
    # 判定：alpha=0 与 alpha=32° 两档预测中至少一档与实测同号且量级
    # 相差不超过 3 倍（核的 alpha 敏感性作为不确定性带宽）
    oks = [(0.3 <= pv / meas <= 3.0) if abs(meas) > 1e-12 else False
           for pv in pred.values()]
    ok = any(oks)
    print("C5 判定: %s" % ("PASS（预测同号同量级，梯度符号=几何真值）" if ok
                          else "FAIL（预测器失效：静态核为零时结构性预测不"
                               "出拍动整流项；符号采信降级为代码链证据）"), flush=True)
    return {"name": "C5_phase_decomposition", "pred": {("%g" % k): v for k, v in pred.items()},
            "measured": meas, "phis_deg": phis_deg,
            "kern_alpha0": kern[0.0], "kern_alpha32": kern[math.radians(32.3169)],
            "pass": bool(ok)}


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("== 横向方向断言套件 · lateral_direction_assert ==", flush=True)
    v1 = _load_v1()
    results = [c1_flat_wing(v1), c2_fixed_dihedral(v1), c4_mirror_identity(v1),
               c3_mean_symmetry(v1), c5_phase_decomposition(v1)]
    n_pass = sum(1 for r in results if r["pass"])
    print("== 断言汇总：%d/%d PASS ==" % (n_pass, len(results)), flush=True)
    out = {"ts": ts, "tool": "tools/lateral_direction_assert.py",
           "results": results,
           "verdict": "gradient_sign_chain_verified" if n_pass == len(results)
                      else "sign_chain_suspect"}
    out_path = os.path.join(PROJECT_ROOT, "logs",
                            "lateral_direction_assert_%s.json" % ts)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("产物：%s" % out_path, flush=True)


if __name__ == "__main__":
    main()
