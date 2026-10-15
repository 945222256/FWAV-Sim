# -*- coding: utf-8 -*-
"""
authority_fit_figs.py — 权限响应函数拟合与图表生成（2026-09-28）

整改纪律第一、二条执行：把所有权限结论改以拟合函数与图交付。
输入：
  logs/authority_response_grid_phi0.json / phi10.json（θ×β 网格）
输出：
  docs/figs/fig1_权限随俯仰.png        —— 密度通道 A(θ) 拟合曲线与零穿越
  docs/figs/fig2_权限地图_坡度0.png    —— A(θ,β) 热力图（平飞）
  docs/figs/fig3_权限地图_坡度10.png   —— A(θ,β) 热力图（转弯工况）
  docs/figs/fig4_甲方案预测.png        —— 转弯权限裕度 M(θ_hold) 曲线
  logs/authority_fits.json             —— 全部拟合系数与残差
  docs/权限响应函数与甲方案预测.md     —— 函数化报告（嵌入图）

不确定带：力律因子三（第七十一班力律鉴别定案），图中以三倍带表示。
"""
import os
import sys
import json
import math

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
os.makedirs(FIGS, exist_ok=True)

# 转弯扰动需求函数（第七十一班实测来源，函数化登记）：
# D(φ,β) = 坡度伴生侧滑驱动矩 + 侧滑整流矩
#   = 4.4e-4·φ[deg] + 4.2e-4·|β[deg]|   [N·m]
# （0.0066 N·m@15° 坡度伴生；+0.00042 N·m/deg 侧滑-滚转梯度）
def demand_nm(phi_deg, beta_deg):
    return 4.4e-4 * abs(phi_deg) + 4.2e-4 * abs(beta_deg)


def load(phi):
    with open(os.path.join(LOGS, f"authority_response_grid_phi{phi}.json"),
              encoding="utf-8") as fp:
        return json.load(fp)


def fit_bilinear(rows, key):
    """A = c0 + cθ·θ + cβ·β + cθβ·θ·β，返回系数与残差 RMS。"""
    th = np.array([r["theta"] for r in rows])
    be = np.array([r["beta"] for r in rows])
    y = np.array([r[key] for r in rows])
    X = np.column_stack([np.ones_like(th), th, be, th * be])
    c, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ c
    return c, float(np.sqrt(np.mean(resid ** 2))), float(np.max(np.abs(resid)))


def main():
    g0 = load(0)
    g10 = load(10)
    fits = {}

    # ── 图1：A(θ) 在 β=0 的两个坡度档 ──
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    for dat, phi, color in ((g0, 0, "C0"), (g10, 10, "C3")):
        rows = [r for r in dat["rows"] if r["beta"] == 0]
        rows.sort(key=lambda r: r["theta"])
        th = np.array([r["theta"] for r in rows])
        y = np.array([r["density_auth"] for r in rows])
        c = np.polyfit(th, y, 1)
        fits[f"density_A_theta_beta0_phi{phi}"] = {
            "slope_per_deg": float(c[0]), "intercept": float(c[1]),
            "zero_cross_deg": float(-c[1] / c[0])}
        xs = np.linspace(-14, 1, 100)
        ys = np.polyval(c, xs)
        ax.fill_between(xs, ys / 3, ys * 3, alpha=0.12, color=color)
        ax.plot(xs, ys, color=color, lw=2,
                label=f"密度通道拟合（坡度 {phi}°）：截距 {c[1]:.4f}，"
                      f"斜率 {c[0]:.5f}/°，零穿越 {-c[1]/c[0]:.1f}°")
        ax.plot(th, y, "o", color=color)
    # 幅值通道对照（β=0，φ=0）
    rows = sorted([r for r in g0["rows"] if r["beta"] == 0],
                  key=lambda r: r["theta"])
    th = np.array([r["theta"] for r in rows])
    ya = np.array([r["dA_auth"] for r in rows])
    ca = np.polyfit(th, ya, 1)
    fits["dA_A_theta_beta0_phi0"] = {"slope_per_deg": float(ca[0]),
                                     "intercept": float(ca[1])}
    xs = np.linspace(-14, 1, 100)
    ax.plot(xs, np.polyval(ca, xs), color="C2", lw=2, ls="--",
            label=f"幅值通道拟合（坡度 0°）：近常数 {ca[1]:.4f}")
    ax.plot(th, ya, "s", color="C2")
    ax.axhline(0, color="k", lw=0.8)
    ax.axvspan(-3, 1, color="red", alpha=0.08)
    ax.annotate("巡航实际俯仰区\n（密度通道反号）", xy=(-1.5, -0.02),
                ha="center", color="red", fontsize=9)
    ax.axvspan(-13, -8, color="green", alpha=0.08)
    ax.annotate("低头权限区", xy=(-10.5, 0.075), ha="center",
                color="green", fontsize=9)
    ax.set_xlabel("机体俯仰角 θ（度，负=低头）")
    ax.set_ylabel("滚转权限（牛米，正=方向正确）")
    ax.set_title("图1 两条滚转通道的权限随俯仰的响应函数（侧滑为零）\n"
                 "阴影带=力律因子三不确定带")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig1_权限随俯仰.png"))
    plt.close(fig)

    # ── 图2/图3：A(θ,β) 热力图（两个坡度档）──
    for dat, phi, fname in ((g0, 0, "fig2_权限地图_坡度0.png"),
                            (g10, 10, "fig3_权限地图_坡度10.png")):
        ths = sorted(set(r["theta"] for r in dat["rows"]))
        bes = sorted(set(r["beta"] for r in dat["rows"]))
        Z = np.zeros((len(ths), len(bes)))
        for r in dat["rows"]:
            Z[ths.index(r["theta"]), bes.index(r["beta"])] = \
                r["density_auth"]
        c, rms, rmax = fit_bilinear(dat["rows"], "density_auth")
        fits[f"density_bilinear_phi{phi}"] = {
            "c0": float(c[0]), "c_theta": float(c[1]),
            "c_beta": float(c[2]), "c_theta_beta": float(c[3]),
            "resid_rms": rms, "resid_max": rmax,
            "formula": "A=c0+cθ·θ+cβ·β+cθβ·θ·β（θ,β 单位度）"}
        fig, ax = plt.subplots(figsize=(8.5, 5), dpi=150)
        vmax = np.max(np.abs(Z))
        pc = ax.pcolormesh(bes, ths, Z, cmap="RdYlGn", vmin=-vmax,
                           vmax=vmax, shading="auto")
        fig.colorbar(pc, ax=ax, label="密度通道滚转权限（牛米）")
        # 零穿越线（拟合面）
        bx = np.linspace(min(bes), max(bes), 200)
        denom = c[1] + c[3] * bx
        with np.errstate(divide="ignore"):
            tz = -(c[0] + c[2] * bx) / np.where(abs(denom) > 1e-12,
                                                denom, np.nan)
        ax.plot(bx, tz, "k--", lw=2, label="零穿越线（拟合面）")
        ax.plot(0, 0, "r*", ms=18, label="巡航实际姿态（权限反号区）")
        ax.set_xlabel("侧滑角 β（度，负=左滑）")
        ax.set_ylabel("机体俯仰角 θ（度）")
        ax.set_title(f"图{'2' if phi == 0 else '3'} 密度通道权限地图"
                     f"（坡度 {phi}°）　拟合残差 RMS={rms:.4f}")
        ax.legend(fontsize=8, loc="upper right")
        fig.tight_layout()
        fig.savefig(os.path.join(FIGS, fname))
        plt.close(fig)

    # ── 图4：甲方案预测——转弯权限裕度 M(θ_hold) ──
    # M = 密度通道权限 / 转弯扰动需求；权限取 φ=10° 拟合面，
    # β 取协调转弯走廊代表值 −5°；不确定带=因子三。
    c10 = fits["density_bilinear_phi10"]
    def A10(th, be):
        return (c10["c0"] + c10["c_theta"] * th + c10["c_beta"] * be
                + c10["c_theta_beta"] * th * be)
    xs = np.linspace(-16, 1, 200)
    fig, ax = plt.subplots(figsize=(8.5, 5), dpi=150)
    for be, color in ((0, "C0"), (-5, "C2"), (-10, "C3")):
        M = np.array([A10(t, be) for t in xs]) / demand_nm(10, be)
        ax.plot(xs, M, color=color, lw=2, label=f"侧滑 {be}° 的裕度曲线")
        ax.fill_between(xs, M / 3, M * 3, alpha=0.10, color=color)
    ax.axhline(1, color="k", lw=1.2, ls=":")
    ax.axhline(2, color="k", lw=1.2, ls="--")
    ax.annotate("M=1 临界（权限=需求）", xy=(-15.5, 1.15), fontsize=8)
    ax.annotate("M=2 工程裕度线", xy=(-15.5, 2.15), fontsize=8)
    ax.axvspan(-3, 1, color="red", alpha=0.08)
    ax.annotate("当前巡航姿态\n（甲之前）", xy=(-1.2, 0.3), ha="center",
                color="red", fontsize=9)
    ax.axvspan(-12, -9, color="green", alpha=0.10)
    ax.annotate("甲修复后的姿态域", xy=(-10.5, 0.9), ha="center",
                color="green", fontsize=9)
    ax.set_xlabel("坡度段实际保持的俯仰角 θ_hold（度）")
    ax.set_ylabel("转弯权限裕度 M = 权限 ÷ 扰动需求")
    ax.set_title("图4 甲方案（修俯仰静差）成功域预测\n"
                 "M>2 视为可闭环；带=力律因子三不确定带")
    ax.set_ylim(-2, 8)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig4_甲方案预测.png"))
    plt.close(fig)

    with open(os.path.join(LOGS, "authority_fits.json"), "w",
              encoding="utf-8") as fp:
        json.dump(fits, fp, ensure_ascii=False, indent=1)
    print("[fit] 拟合系数：", json.dumps(fits, ensure_ascii=False), flush=True)
    print("[fit] 图四张 → docs/figs/", flush=True)


if __name__ == "__main__":
    main()
