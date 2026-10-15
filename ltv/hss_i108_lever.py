# -*- coding: utf-8 -*-
"""
hss_i108_lever.py — 族外杠杆定量分析，L4 线事项 I-108，续 I-102。

I-102 判：十度翻覆模态（开环实部每秒正三点一八六）对尾翼族参数不敏感
，有效杠杆在族外三件。本工具对三杠杆分别定量：单位变更压下增长率所
需的变更量，三杠杆对比表与最小变更建议。

杠杆一，主翼上反角：给两翼拍动面各加二面角，侧滑经二面效应产生差动
攻角进而滚转力矩。模型实现为线性二面导数项，滚转力矩增量等于负的标
定系数乘上反角正弦乘侧滑速度。标定系数取模型自身的拍平均升力线斜率
（对配平点俯仰小扰动的平均竖直力响应差分标定）乘有效展向力臂，保持
与气动模型自洽。扫描上反角五度至二十度。

杠杆二，翼质量展向分布：翼件质量迁移经平行轴定理改变滚转惯量。在册
翼件每侧二十七克（内翼十二克外翼九克接头六克），极限迁移（内翼接头
质量全移翼尖）的可达域扫描，另给加翼尖配重的等效质量换算。注意本离
线模型缺翼拍惯性反力矩项，本杠杆读数为下限估计（真实效应含拍动回转
耦合，方向与幅度存不确定，声明沿用 I-102 保真度限制）。

杠杆三，拍波形滚转分量：逐拍差动机制以波形不对称产生滚转力矩，模型
实现为翼路径理想滚转速率内环（力矩直加，不经尾舵饱和），权限上限取
I-094 满偏阶跃真值中心每冲程零点零三四七牛米。扫描力矩增益，求压零
所需增益与权限可行性判读。

判读规则预登记（先于计算）：压零所需变更量在物理可达域内判可行；超
可达域但五倍以内判边缘；超五倍判不可行。三杠杆按所需变更量对可达域
的比值排序出最小变更建议。产出写 ltv/i108_results 时间戳目录。
"""
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

import hss_i102_stab as s1  # noqa: E402

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
RE_BASE = 3.1863
ROLL_AUTH_NM = 0.03466
DIH_SWEEP_DEG = [2.5, 5.0, 7.5, 10.0, 15.0, 20.0]
IXX_SWEEP_X = [1.5, 2.0, 3.0, 4.0]
KW_SWEEP = [0.05, 0.1, 0.2, 0.4, 0.8, 1.6]
BANK = 10.0


def calibrate_dihedral(x_trim, wz):
    """标定二面导数系数。固定世界速度矢量变姿态，测拍平均竖直力对
    攻角斜率（攻角敏感性须经世界速度不动姿态动才有定义），乘有效展向
    力臂得单位二面滚转导数。"""
    u0, vy0, wb0, p0, e0, a0, r0 = x_trim
    quat0 = s1.quat_from_rpy(math.radians(BANK), p0, 0.0)
    R0 = s1.rot_body_from_world(quat0)
    v_w = R0.T @ np.array([u0, vy0, wb0])

    def fz_body(pitch):
        quat = s1.quat_from_rpy(math.radians(BANK), pitch, 0.0)
        R = s1.rot_body_from_world(quat)
        F, _T = s1.WRENCH(v_w, quat, np.array([0.0, 0.0, wz]), 1.8,
                          tail_cmd=(e0, a0, r0))
        return (R @ F)[2]

    d_fz = (fz_body(p0 + 0.02) - fz_body(p0 - 0.02)) / 0.04
    y_eff = 0.16
    return d_fz * y_eff, d_fz


def unstable_re(bank=BANK, k_dih=0.0, k_w=0.0, ixx_scale=1.0):
    """配平加线性化，可选二面导数项与翼滚转内环与 Ixx 缩放。"""
    I0 = s1.I_DIAG.copy()
    s1.I_DIAG[0] = I0[0] * ixx_scale
    try:
        x, wz = s1.plant_trim(bank)
        u0, vy0, wb0, p0, e0, a0, r0 = x

        def deriv(s):
            u, vy, wb, roll, pitch, wx, wy, wzz = s
            quat = s1.quat_from_rpy(roll, pitch, 0.0)
            R = s1.rot_body_from_world(quat)
            v_b = np.array([u, vy, wb])
            w_v = np.array([wx, wy, wzz])
            F, T = s1.WRENCH(R.T @ v_b, quat, w_v, 1.8,
                             tail_cmd=(e0, a0, r0))
            F_b = R @ F
            g_b = R @ np.array([0.0, 0.0, -s1.G])
            vdot = F_b / s1.MASS + g_b - np.cross(w_v, v_b)
            T = T.copy()
            if k_dih != 0.0:
                T[0] -= k_dih * vy
            if k_w != 0.0:
                T[0] -= k_w * wx
            wIw = np.cross(w_v, s1.I_DIAG * w_v)
            wdot = (T - wIw) / s1.I_DIAG
            return np.array([vdot[0], vdot[1], vdot[2], wx, wy,
                             wdot[0], wdot[1], wdot[2]])

        roll0 = math.radians(bank)
        s0 = np.array([u0, vy0, wb0, roll0, p0, 0.0, 0.0, wz])
        eps = 1e-5
        A = np.zeros((8, 8))
        for j in range(8):
            sp, sm = s0.copy(), s0.copy()
            sp[j] += eps
            sm[j] -= eps
            A[:, j] = (deriv(sp) - deriv(sm)) / (2 * eps)
        w = sorted(np.linalg.eigvals(A), key=lambda v: -v.real)
        return w[0].real, w
    finally:
        s1.I_DIAG[:] = I0


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    x, wz = s1.plant_trim(BANK)
    K_dih_unit, d_fz = calibrate_dihedral(x, wz)
    print("二面标定：平均升力线斜率 %.3f 牛每弧度，力臂 %.2f 米，单位二"
          "面系数 %.4f 牛米每弧度侧滑每弧度二面" % (d_fz, 0.16, K_dih_unit))
    rows_dih = []
    for g in DIH_SWEEP_DEG:
        k = -abs(K_dih_unit) * math.sin(math.radians(g)) / max(x[0], 1.0)
        re_u, w = unstable_re(k_dih=k)
        rows_dih.append({"dihedral_deg": g, "re": float(re_u)})
        print("上反角 %4.1f 度 → 翻覆模态 Re=%+.4f（次模态 %+.3f）"
              % (g, re_u, w[1].real))
    for g in (2.5, 5.0):
        k = +abs(K_dih_unit) * math.sin(math.radians(g)) / max(x[0], 1.0)
        re_u, _ = unstable_re(k_dih=k)
        print("下反角 %4.1f 度（对照） → Re=%+.4f" % (g, re_u))
    rows_ixx = []
    for m in IXX_SWEEP_X:
        re_u, w = unstable_re(ixx_scale=m)
        rows_ixx.append({"ixx_mult": m, "re": float(re_u)})
        print("滚转惯量 ×%.1f → Re=%+.4f" % (m, re_u))
    rows_kw = []
    for k in KW_SWEEP:
        re_u, w = unstable_re(k_w=k)
        auth = k * 1.0
        rows_kw.append({"k_moment": k, "re": float(re_u),
                        "auth_at_1rad_s_nm": float(auth)})
        print("翼滚转内环 %.2f 牛米每弧度每秒 → Re=%+.4f（一弧度每秒时"
              "需 %.4f 牛米对权限 %.4f）" % (k, re_u, auth, ROLL_AUTH_NM))
    out_dir = os.path.join(_HERE, "i108_results",
                           time.strftime("i108_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    json.dump({"item": "I-108", "spec_sha256": spec_sha,
               "K_dih_unit": float(K_dih_unit), "d_fz": float(d_fz),
               "dihedral": rows_dih, "ixx": rows_ixx, "kw": rows_kw,
               "re_base": RE_BASE, "roll_auth_nm": ROLL_AUTH_NM,
               "new_sim_runs": 0},
              open(os.path.join(out_dir, "verdict.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=2)
    print("产出目录：%s" % out_dir)
    return {"dihedral": rows_dih, "ixx": rows_ixx, "kw": rows_kw}


if __name__ == "__main__":
    main()
