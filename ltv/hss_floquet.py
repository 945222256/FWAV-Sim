# -*- coding: utf-8 -*-
"""
hss_floquet.py — 弗洛凯稳定性判读工具，L4 线事项 I-047。

验收判据为 ltv/hss_spec.json acceptance 节 A2 条。工具对线性周期时变
系统以四阶龙格库塔积分单周期状态转移矩阵，输出周期增长倍率（转移矩
阵特征值的模）与弗洛凯指数（倍率对数除以周期）。判据要求两解析例的
误差低于预登记阈值，另对盘旋矩阵输出判读报告。全部阈值取自冻结条文，
本文件不改任何阈值。

解析例一为标量系统，状态导数等于 a0 加 ac 乘余弦拍角频率时间项之和
乘状态，真值单周期增长倍率为 exp(a0 乘周期)，依据为余弦项整周期积分
为零。解析例二为旋转系矩阵系统，状态矩阵为绕 z 轴旋转矩阵乘常数矩阵
M 乘旋转矩阵转置，真值单周期转移矩阵为 M 减拍角频率乘 Jz 之差的矩阵
指数，依据为恒等式 R'(t) 等于拍角频率乘 R(t) 乘 Jz 与 R(T) 等于单位
阵（T 恰为整数个旋转周期），推导见本文件函数 docstring。Jz 为第三行
第三列为零的三阶反对称旋转生成元，与规范 A2 条文字一致。M 在本程序
内固定，特征值含正实部零点四与负实部负零点三五加减零点二六虚部，满
足规范正负实部特征值各至少一个的要求。

盘旋矩阵判读对象取 I-006 事项提交 64d758d 收束的九跳变窗归档拟合
I006hop9 与规范 A1 条量级出处 I006rep11 的 A0 常数矩阵。常数矩阵的
判读为其特征值读数的数值复算交叉验证。周期项判读受 A4 条前置门约束，
当前无胜出系数集入册，报告如实声明该边界。

产出写入 ltv/floquet_results 时间戳目录，含机器可读 verdict.json 与
人类可读 report.md。
"""
import hashlib
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
F0 = 1.25
THRESH = {"mu_relerr_max": 0.01, "floquet_re_abserr_max_per_s": 0.02}
SCALAR = {"a0_per_s": -0.5, "ac": [0.0, 0.05], "f0_hz": 1.25}
M_FIXED = np.array([[-0.5, 0.3, 0.0],
                    [-0.3, -0.2, 0.0],
                    [0.1, 0.0, 0.4]])
ROT_F0 = 1.25
CIRcling_BATCHES = {
    "I006hop9": "logs/ident_matrix/I006hop9_20260930_155753/matrix.json",
    "I006rep11": "logs/ident_matrix/I006rep11_20260930_210739/matrix.json",
}


def expm_eig(B):
    """矩阵指数，特征分解实现，输入为实矩阵。"""
    w, V = np.linalg.eig(B)
    return V @ np.diag(np.exp(w)) @ np.linalg.inv(V)


def stm_rk4(A_fun, T, n_steps):
    """单周期状态转移矩阵，恒等初值，四阶龙格库塔。A_fun 取时间返回
    系统矩阵。"""
    h = T / n_steps
    n = A_fun(0.0).shape[0]
    Phi = np.eye(n)
    for i in range(n_steps):
        t = i * h
        k1 = A_fun(t) @ Phi
        k2 = A_fun(t + h / 2) @ (Phi + h / 2 * k1)
        k3 = A_fun(t + h / 2) @ (Phi + h / 2 * k2)
        k4 = A_fun(t + h) @ (Phi + h * k3)
        Phi = Phi + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    return Phi


def floquet_read(Phi, T):
    """周期增长倍率与弗洛凯指数。倍率为转移矩阵特征值的模升序排列，
    指数实部为倍率自然对数除以周期。负实特征值的指数相位约定为每拍
    半圈，本工具按规范只报告指数实部，相位歧义不影响实部。"""
    w = np.linalg.eigvals(Phi)
    mu = np.sort(np.abs(w))
    re = np.log(np.maximum(mu, 1e-300)) / T
    return mu, re


def case_scalar(ac):
    """解析例一，返回指标字典。系统为一乘一矩阵。"""
    a0 = SCALAR["a0_per_s"]
    T = 1.0 / SCALAR["f0_hz"]
    A_fun = lambda t: np.array([[a0 + ac * np.cos(2 * np.pi
                                                 * SCALAR["f0_hz"] * t)]])
    Phi = stm_rk4(A_fun, T, 4000)
    mu, re = floquet_read(Phi, T)
    mu_true = np.array([np.exp(a0 * T)])
    re_true = np.array([a0])
    return {
        "case": "scalar", "ac": ac, "T_s": T,
        "mu_numeric": mu.tolist(), "mu_true": mu_true.tolist(),
        "mu_relerr": float(np.max(np.abs(mu - mu_true)
                                  / np.abs(mu_true))),
        "re_numeric": re.tolist(), "re_true": re_true.tolist(),
        "re_abserr": float(np.max(np.abs(re - re_true))),
    }


def case_rotating():
    """解析例二，返回指标字典。

    推导：设 R 为绕 z 轴旋转，满足 R'(t) = ω R(t) Jz。令 Φ(t) =
    R(t) exp((M − ω Jz) t)，则 Φ' = R' exp + R(M − ω Jz) exp =
    ω R Jz exp + R M exp − ω R Jz exp = R M exp，而 A Φ =
    R M R^T R exp = R M exp，故 Φ 为状态转移矩阵且 Φ(0) = 单位阵。
    单周期末 R(T) = R(2π) = 单位阵，故真值转移矩阵为
    exp((M − ω Jz) T)，与规范 A2 条文字一致。
    """
    T = 1.0 / ROT_F0
    om = 2 * np.pi * ROT_F0
    Jz = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]])

    def R(t):
        c, s = np.cos(om * t), np.sin(om * t)
        return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])

    A_fun = lambda t: R(t) @ M_FIXED @ R(t).T
    Phi = stm_rk4(A_fun, T, 4000)
    mu, re = floquet_read(Phi, T)
    Phi_true = expm_eig((M_FIXED - om * Jz) * T)
    mu_true, re_true = floquet_read(Phi_true, T)
    return {
        "case": "rotating", "T_s": T,
        "M": M_FIXED.tolist(),
        "M_eig_real": sorted(np.linalg.eigvals(M_FIXED).real.tolist()),
        "mu_numeric": mu.tolist(), "mu_true": mu_true.tolist(),
        "mu_relerr": float(np.max(np.abs(mu - mu_true)
                                  / np.abs(mu_true))),
        "re_numeric": re.tolist(), "re_true": re_true.tolist(),
        "re_abserr": float(np.max(np.abs(re - re_true))),
    }


def circling_report():
    """盘旋矩阵判读报告数据。读归档 A0 常数矩阵，数值积分单周期转移
    矩阵并与矩阵指数解析值交叉验证，输出倍率、指数与判读语句。"""
    out = []
    root = os.path.dirname(_HERE)
    for tag, rel in CIRcling_BATCHES.items():
        p = os.path.join(root, rel)
        m = json.load(open(p, encoding="utf-8"))
        A0 = np.array(m["A"])
        f_stroke = float(m["f_stroke_hz"])
        T = 1.0 / f_stroke
        Phi = stm_rk4(lambda t: A0, T, 4000)
        mu, re = floquet_read(Phi, T)
        Phi_true = expm_eig(A0 * T)
        mu_true, re_true = floquet_read(Phi_true, T)
        cross = float(np.max(np.abs(mu - mu_true) / np.abs(mu_true)))
        out.append({
            "tag": tag, "source": rel,
            "window": m.get("window"), "f_stroke_hz": f_stroke,
            "A0": A0.tolist(),
            "eig_real": sorted(np.linalg.eigvals(A0).real.tolist()),
            "mu_per_stroke": mu.tolist(),
            "floquet_re_per_s": re.tolist(),
            "max_mu": float(mu[-1]),
            "max_floquet_re": float(re[-1]),
            "cross_check_mu_relerr": cross,
            "reading": ("单拍周期最大增长倍率为 %.5f（每拍变化 %+.2f%%），"
                        "最大弗洛凯指数实部为 %+.4f 每秒，无增长模态"
                        % (mu[-1], (mu[-1] - 1) * 100, re[-1]))
            if mu[-1] < 1.0 else
            ("单拍周期最大增长倍率为 %.5f，存在增长模态" % mu[-1]),
        })
    return out


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    cases = [case_scalar(ac) for ac in SCALAR["ac"]] + [case_rotating()]
    for c in cases:
        c["pass"] = (c["mu_relerr"] <= THRESH["mu_relerr_max"]
                     and c["re_abserr"]
                     <= THRESH["floquet_re_abserr_max_per_s"])
        print("%s mu_relerr=%.2e re_abserr=%.2e pass=%s" % (
            c["case"], c["mu_relerr"], c["re_abserr"], c["pass"]))
    cir = circling_report()
    for c in cir:
        print("[%s] %s" % (c["tag"], c["reading"]))
    verdict = {
        "item": "I-047",
        "criterion": "A2_floquet_tool",
        "spec_sha256": spec_sha,
        "thresholds": THRESH,
        "cases": cases,
        "circling_reading": cir,
        "verdict_pass": all(c["pass"] for c in cases),
    }
    out_dir = os.path.join(_HERE, "floquet_results",
                           time.strftime("flq_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(verdict, fp, ensure_ascii=False, indent=2)
    rep = ["# I-047 弗洛凯稳定性判读工具验收报告", "",
           "判据出处 ltv/hss_spec.json acceptance 节 A2 条，规范指纹 %s。"
           % spec_sha[:16],
           "总体结论为%s。" % ("通过" if verdict["verdict_pass"]
                              else "不通过"),
           "", "## 阈值", "",
           "| 指标 | 阈值 |", "|---|---|",
           "| 周期增长倍率相对误差 | 0.01 |",
           "| 弗洛凯指数实部绝对误差 | 0.02 每秒 |", "",
           "## 解析例结果", "",
           "| 例 | 倍率相对误差 | 指数实部绝对误差 | 判定 |",
           "|---|---|---|---|"]
    for c in cases:
        rep.append("| %s%s | %.3e | %.3e | %s |" % (
            c["case"], "" if c["case"] == "rotating"
            else " ac=%g" % c.get("ac", 0.0),
            c["mu_relerr"], c["re_abserr"], "过" if c["pass"] else "不过"))
    rep += ["", "## 盘旋矩阵判读报告", ""]
    for c in cir:
        rep += ["### %s（来源 %s，窗口 %s，拍频 %.3f 赫兹）"
                % (c["tag"], c["source"], c["window"], c["f_stroke_hz"]),
                "", "A0 特征值实部：%s 每秒。" % (
                    ", ".join("%+.4f" % v for v in c["eig_real"])),
                "单拍周期增长倍率：%s。" % (
                    ", ".join("%.5f" % v for v in c["mu_per_stroke"])),
                "弗洛凯指数实部：%s 每秒。" % (
                    ", ".join("%+.4f" % v for v in c["floquet_re_per_s"])),
                "数值积分与矩阵指数解析值交叉验证最大倍率相对误差 %.2e。"
                % c["cross_check_mu_relerr"],
                "判读：%s。" % c["reading"], ""]
    rep += ["周期项判读边界：A4 条前置门未开（I-046 对拍一胜两平未达全面"
            "胜出），无胜出系数集入册，含周期项的完整判读待系数集按规范"
            "入册后由本工具直接执行。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return verdict


if __name__ == "__main__":
    v = main()
    sys.exit(0 if v["verdict_pass"] else 1)
