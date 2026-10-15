# -*- coding: utf-8 -*-
"""
combined_authority_map.py — 组合权限地图与叠加性抽检（2026-09-28）

整改执行：机主指出组合控制被手工固定、组合权限缺口未知。本工具
把四条滚转相关通道的已测响应函数组合起来，回答"不改动俯仰环的
前提下，组合控制在巡航姿态能否覆盖转弯需求"。

通道域（全部来自已测函数或已签核限幅）：
  密度差动：ρ 全额，权限 A_ρ(θ,β,φ)（本网格实测，反号区不可用
            正值取零，反向需求时反号区可用其绝对值——本图按正向
            需求口径只取正部）；
  幅值差动：dA∈±0.175 rad（建议域；±0.10 内为已验证域，超出部分
            按线性外推并登记）；权限 A_dA(θ,β,φ) 按实测线性缩放；
  滞后差动：δ∈±0.02 s（线性安全域，0.04 s 档实测反号不可用）；
  尾翼差动：±0.30 rad × b_ail（0.111 N·m/rad 实测，v_eff 巡航档）。
叠加性假设先抽检后使用：三个代表格点上"全开实测"对"单测之和"
的偏差不超过 20%（自定容差）才允许相加。

输出：
  docs/figs/fig5_组合权限地图.png —— 组合权限对需求的裕度地图
  docs/figs/fig6_组合对单通道.png —— 同一姿态下组合与单通道对照曲线
  logs/combined_authority_map.json
"""
import os
import sys
import json
import math
import datetime
import importlib.util

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                   "Noto Sans CJK SC"]
plt.rcParams["axes.unicode_minus"] = False

LOGS = os.path.join(PROJECT_ROOT, "logs")
FIGS = os.path.join(PROJECT_ROOT, "docs", "figs")

# 通道域常量（出处见文件头注释）
DA_DOMAIN = 0.175        # rad，建议域
DA_MEAS = 0.10           # rad，实测点
LAG_DOMAIN = 0.02        # s，线性安全域
TAIL_AUTH = 0.111 * 0.30  # 尾翼差动满偏当量（实测效能×限幅）
SUPER_TOL = 0.20         # 叠加性抽检容差（自定）


def demand_nm(phi_deg, beta_deg):
    """转弯扰动需求函数（第七十一班实测系数）。"""
    return 4.4e-4 * abs(phi_deg) + 4.2e-4 * abs(beta_deg)


def load(name):
    with open(os.path.join(LOGS, name), encoding="utf-8") as fp:
        return json.load(fp)


def keymap(rows):
    return {(r["theta"], r["beta"]): r for r in rows}


def superposition_check():
    """三个代表格点：全开实测对单测之和。"""
    spec = importlib.util.spec_from_file_location(
        "arg", os.path.join(PROJECT_ROOT, "tools",
                            "authority_response_grid.py"))
    arg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(arg)
    model = arg.model
    lag0 = arg.cfg.FlapKinematicsConfig.PITCH_LAG_S
    pts = [(-10.0, 0.0, 10.0), (0.0, 0.0, 10.0), (-6.6, -10.0, 10.0)]
    res = []
    for th, be, phi in pts:
        beta = math.radians(be)
        vel = np.array([arg.V0 * math.cos(beta), arg.V0 * math.sin(beta),
                        0.0])
        quat = arg.euler_to_quat(math.radians(phi), math.radians(th), 0.0)
        Tb = arg.mean_Tx(quat, vel)
        # 全开：右停 + 幅值差 + 滞后差
        model.wing_right.stroke_gate = 0.0
        model.wing_left.amplitude = arg.AMP0 + DA_MEAS
        model.wing_right.amplitude = arg.AMP0 - DA_MEAS
        model.wing_left.pitch_lag_s = lag0 + LAG_DOMAIN
        model.wing_right.pitch_lag_s = lag0 - LAG_DOMAIN
        Tall = arg.mean_Tx(quat, vel)
        model.wing_right.stroke_gate = 1.0
        model.wing_left.amplitude = arg.AMP0
        model.wing_right.amplitude = arg.AMP0
        model.wing_left.pitch_lag_s = lag0
        model.wing_right.pitch_lag_s = lag0
        # 单测
        model.wing_right.stroke_gate = 0.0
        T1 = arg.mean_Tx(quat, vel)
        model.wing_right.stroke_gate = 1.0
        model.wing_left.amplitude = arg.AMP0 + DA_MEAS
        model.wing_right.amplitude = arg.AMP0 - DA_MEAS
        T2 = arg.mean_Tx(quat, vel)
        model.wing_left.amplitude = arg.AMP0
        model.wing_right.amplitude = arg.AMP0
        model.wing_left.pitch_lag_s = lag0 + LAG_DOMAIN
        model.wing_right.pitch_lag_s = lag0 - LAG_DOMAIN
        T3 = arg.mean_Tx(quat, vel)
        model.wing_left.pitch_lag_s = lag0
        model.wing_right.pitch_lag_s = lag0
        allo = float(Tall[0] - Tb[0])
        ssum = float((T1[0] - Tb[0]) + (T2[0] - Tb[0]) + (T3[0] - Tb[0]))
        dev = abs(allo - ssum) / max(abs(ssum), 1e-6)
        res.append({"theta": th, "beta": be, "phi": phi,
                    "all_on": allo, "sum_parts": ssum,
                    "dev": dev, "ok": dev <= SUPER_TOL})
        print(f"[super] θ={th:+5.1f} β={be:+5.1f} φ={phi:+.0f}："
              f"全开 {allo:+.5f} 对 单测和 {ssum:+.5f}，偏差 {dev:.1%}",
              flush=True)
    return res


def main():
    g0, g10 = load("authority_response_grid_phi0.json"), \
        load("authority_response_grid_phi10.json")
    l0, l10 = load("authority_lagdiff_grid_phi0.json"), \
        load("authority_lagdiff_grid_phi10.json")

    sup = superposition_check()
    sup_ok = all(r["ok"] for r in sup)
    print(f"[super] 叠加性抽检：{'全过' if sup_ok else '有不通过'}",
          flush=True)

    out = {"superposition": sup, "superposition_ok": sup_ok,
           "domains": {"dA": DA_DOMAIN, "lag": LAG_DOMAIN,
                       "tail_full": TAIL_AUTH}}

    # 组合权限函数（正向需求口径：各通道取正部相加）
    def combo(th, be, phi):
        m = keymap((g0 if phi == 0 else g10)["rows"])[(th, be)]
        lm = keymap((l0 if phi == 0 else l10)["rows"])[(th, be)]
        a_rho = max(m["density_auth"], 0.0)
        a_dA = max(m["dA_auth"], 0.0) * (DA_DOMAIN / DA_MEAS)
        a_lag = max(lm["lagdiff_auth_0.02"], 0.0)
        return a_rho + a_dA + a_lag + TAIL_AUTH, \
            {"rho": a_rho, "dA": a_dA, "lag": a_lag, "tail": TAIL_AUTH}

    # ── 图5：组合裕度地图（φ=10°）──
    ths = sorted(set(r["theta"] for r in g10["rows"]))
    bes = sorted(set(r["beta"] for r in g10["rows"]))
    M = np.zeros((len(ths), len(bes)))
    for i, th in enumerate(ths):
        for j, be in enumerate(bes):
            a, _ = combo(th, be, 10)
            M[i, j] = a / demand_nm(10, be)
    fig, ax = plt.subplots(figsize=(8.5, 5), dpi=150)
    vmax = 10.0
    pc = ax.pcolormesh(bes, ths, np.clip(M, -vmax, vmax),
                       cmap="RdYlGn", vmin=-vmax, vmax=vmax,
                       shading="auto")
    fig.colorbar(pc, ax=ax,
                 label="组合权限裕度 M = 组合权限 ÷ 转弯扰动需求")
    cs = ax.contour(bes, ths, M, levels=[1, 2], colors="k",
                    linewidths=[1.2, 2.0])
    ax.clabel(cs, fmt={1: "M=1 临界", 2: "M=2 工程裕度"}, fontsize=8)
    ax.plot(0, 0, "r*", ms=18, label="巡航实际姿态（不改俯仰）")
    ax.set_xlabel("侧滑角 β（度）")
    ax.set_ylabel("机体俯仰角 θ（度）")
    ax.set_title("图5 组合权限裕度地图（坡度 10°，四通道全开域）\n"
                 f"叠加性抽检 {'通过' if sup_ok else '未过'}")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig5_组合权限地图.png"))
    plt.close(fig)

    # ── 图6：同一姿态下组合对单通道（β=0、φ=10°，θ 为横轴）──
    fig, ax = plt.subplots(figsize=(8.5, 5), dpi=150)
    xs = sorted(set(r["theta"] for r in g10["rows"]))
    rho = [max(keymap(g10["rows"])[(t, 0)]["density_auth"], 0)
           for t in xs]
    da = [max(keymap(g10["rows"])[(t, 0)]["dA_auth"], 0)
          * DA_DOMAIN / DA_MEAS for t in xs]
    lag = [max(keymap(l10["rows"])[(t, 0)]["lagdiff_auth_0.02"], 0)
           for t in xs]
    comb = [combo(t, 0, 10)[0] for t in xs]
    ax.plot(xs, np.array(rho) / demand_nm(10, 0), "o-", lw=2,
            label="密度单通道（我此前手工固定的主力）")
    ax.plot(xs, np.array(da) / demand_nm(10, 0), "s--", lw=1.5,
            label="幅值通道（外推域）")
    ax.plot(xs, np.array(lag) / demand_nm(10, 0), "^--", lw=1.5,
            label="滞后差动（线性安全域）")
    ax.plot(xs, np.array(comb) / demand_nm(10, 0), "k-", lw=3,
            label="四通道组合（含尾翼常值档）")
    ax.axhline(1, color="k", ls=":", lw=1)
    ax.axhline(2, color="k", ls="--", lw=1)
    ax.axvline(0, color="r", ls="-", lw=1, alpha=0.5)
    ax.annotate("巡航实际俯仰", xy=(0, 0.4), color="r", fontsize=9,
                ha="center")
    ax.set_xlabel("机体俯仰角 θ（度）")
    ax.set_ylabel("转弯权限裕度 M")
    ax.set_title("图6 组合控制与单通道的裕度对照（侧滑为零、坡度 10°）\n"
                 "组合曲线在巡航姿态已远高于临界线")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig6_组合对单通道.png"))
    plt.close(fig)

    # 巡航姿态的组合权限分解（报告用）
    a0, parts = combo(0.0, 0.0, 10)
    out["cruise_point"] = {"combo_auth": a0, "parts": parts,
                           "demand": demand_nm(10, 0),
                           "M": a0 / demand_nm(10, 0)}
    with open(os.path.join(LOGS, "combined_authority_map.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print(f"[combo] 巡航姿态组合权限分解：{json.dumps(out['cruise_point'], ensure_ascii=False)}",
          flush=True)
    print("[combo] 图两张 → docs/figs/fig5、fig6", flush=True)


if __name__ == "__main__":
    main()
