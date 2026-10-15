# -*- coding: utf-8 -*-
"""第 10 页两张占位图（全部用仓库数据与配置常数，不臆造）。
图1：B_dA 线性拟合残差分布（ident_stream_dastep_0919_152649.csv）
图2：控制 effectiveness 矩阵 B(f) 奇异值谱（config 常数 + reanchor_scheduling.json）"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(sys.executable).parent.parent.parent))
from daimon_runtime import setup_plot
setup_plot()
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

DARK, ACC, GRAY = "#2E4045", "#B66A3C", "#7E838A"

# ─────────── 图 1：辨识残差分布 ───────────
df = pd.read_csv(r"G:/Playground/isaac_flapper_sim/logs/ident_stream_dastep_0919_152649.csv")
pts = []
for ph in df.phase.unique():
    if ph.startswith("da_f"):
        parts = ph.split("_")
        if len(parts) < 3:
            continue
        try:
            f = float(parts[1][1:])
            amp = float(parts[2])
        except ValueError:
            continue
        m = df[df.phase == ph].TMx.mean()
        pts.append((f, amp, m))
P = pd.DataFrame(pts, columns=["f", "amp", "TMx"]).sort_values(["f", "amp"])

fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.0), dpi=150)
res_all = []
fits = {}
for f in sorted(P.f.unique()):
    d = P[P.f == f]
    k = np.sum(d.amp * d.TMx) / np.sum(d.amp ** 2)   # 过原点最小二乘
    fits[f] = k
    res = d.TMx - k * d.amp
    res_all.extend(res.values)
    axes[0].plot(d.amp, d.TMx, "o", ms=6, color=ACC if f == 1.9 else (DARK if f == 2.5 else GRAY))
    a = np.linspace(-0.2, 0.2, 10)
    axes[0].plot(a, k * a, "-", lw=1.6, color=ACC if f == 1.9 else (DARK if f == 2.5 else GRAY),
                 label=f"f = {f:.2f} Hz（B_dA = {k:.1f}）")
axes[0].set_xlabel("翼差动幅值 Δamp (rad)", fontsize=13)
axes[0].set_ylabel("滚转力矩响应 ΔTMx (N·m)", fontsize=13)
axes[0].tick_params(labelsize=12)
axes[0].legend(fontsize=11, loc="upper left", frameon=False)
axes[0].set_title("差动舵效线性拟合（钉固台架实测）", fontsize=14, fontweight="bold")

res_all = np.array(res_all)
axes[1].bar(range(len(res_all)), res_all, color=DARK, width=0.7)
axes[1].axhline(0, color=GRAY, lw=1)
mx = np.abs(res_all).max()
axes[1].set_ylim(-mx * 1.25, mx * 1.25)
axes[1].set_xlabel("试验点序号（3 频 × 6 幅值）", fontsize=13)
axes[1].set_ylabel("拟合残差 (N·m)", fontsize=13)
axes[1].tick_params(labelsize=12)
axes[1].set_title(f"残差分布：max |残差| = {mx:.2f} N·m"
                  f"（满量程 {np.abs(P.TMx).max():.1f} N·m 的 {mx/np.abs(P.TMx).max()*100:.1f}%）",
                  fontsize=13, fontweight="bold")
fig.tight_layout()
fig.savefig("图1_辨识残差分布.png", bbox_inches="tight", facecolor="#F7F5EF")
plt.close(fig)
print("图1 done", {k: round(v, 2) for k, v in fits.items()}, "maxres", round(mx, 3))

# ─────────── 图 2：奇异值谱 ───────────
R = json.load(open(r"G:/Playground/isaac_flapper_sim/logs/reanchor_scheduling.json", encoding="utf-8"))
fs = [p["f"] for p in R["B_dA"]]
bd = [p["B_dA"] for p in R["B_dA"]]

K_ELEV = -0.0145       # N·m/rad/(m/s)²（sim_config.py:588）
K_AIL = 0.15 * 0.00483 # ail→mx 等效增益
SLIP = R["slipstream"]["slip_v_per_hz_new"]  # 0.4718
V_AIR = R["trim"]["V"]  # 4.0

fgrid = np.linspace(1.0, 3.5, 200)
v_eff = np.sqrt(V_AIR ** 2 + (SLIP * fgrid) ** 2)
b_da = np.interp(fgrid, fs, bd)
s_pitch = np.abs(K_ELEV) * v_eff ** 2
s_roll = np.sqrt((K_AIL * v_eff ** 2) ** 2 + b_da ** 2)

fig, ax = plt.subplots(figsize=(9.2, 3.0), dpi=150)
ax.axvspan(1.0, 3.5, color="#F0EDE4", zorder=0)
ax.plot(fgrid, s_pitch, color=DARK, lw=2.2, label="$\sigma_1$ = $\sigma_2$ = |k_elev|·v_eff²（俯仰／偏航）")
ax.plot(fgrid, s_roll, color=ACC, lw=2.6, label="$\sigma_3$ = √((k_ail·v_eff²)² + B_dA(f)²)（滚转）")
f_zero = float(np.interp(0.0, list(reversed(bd)), list(reversed(fs))))
s_min = np.abs(K_AIL) * (np.sqrt(V_AIR**2 + (SLIP*f_zero)**2))**2
ax.plot([f_zero], [s_min], "o", ms=9, color=ACC, zorder=5)
ax.annotate(f"B_dA 变号点 f ≈ {f_zero:.2f} Hz：σ3 凹陷，\n滚转通道在此附近失去可控性",
            xy=(f_zero, s_min), xytext=(2.42, 0.10),
            fontsize=12, color=ACC,
            arrowprops=dict(arrowstyle="->", color=ACC, lw=1.4))
ax.text(1.06, s_pitch.max()*0.92, "阴影 = 自驾拍频域 [1.0, 3.5] Hz", fontsize=11, color=GRAY)
ax.set_xlabel("拍频 f (Hz)", fontsize=13)
ax.set_ylabel("奇异值 (N·m/rad)", fontsize=13)
ax.tick_params(labelsize=12)
ax.legend(fontsize=11, loc="lower left", frameon=False)
ax.set_title("控制 effectiveness 矩阵 B(v*, f) 的奇异值谱"
             f"（v_eff = √({V_AIR}² + ({SLIP:.3f}·f)²)）", fontsize=13.5, fontweight="bold")
fig.tight_layout()
fig.savefig("图2_B矩阵奇异值谱.png", bbox_inches="tight", facecolor="#F7F5EF")
plt.close(fig)
print("图2 done, 变号点 f =", round(f_zero, 3))
