# -*- coding: utf-8 -*-
"""
render_scope_stills.py — 用真实验收飞行数据渲染两张"波形显示器"风格静态图

仿 tools/joint_scope.py（UDP 9010 关节目标/实际）与 tools/ctrl_scope.py
（UDP 9011 操纵量/输出）的画面构成，数据源取 65 秒验收飞行 CSV 的
真实记录列（非编造）。产出：
  deck_20260921_33页/media/scope_joint_flight.png  （关节波形 4 通道）
  deck_20260921_33页/media/scope_ctrl_flight.png   （控制量 6 通道）
用法： python render_scope_stills.py [csv路径]
"""
import os
import sys
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    PROJECT, "logs", "ident_stream_flight_0921_055131.csv")
OUT_DIR = os.path.join(PROJECT, "deck_20260921_33页", "media")

rows = []
with open(CSV, encoding="utf-8-sig", errors="ignore") as f:
    for r in csv.DictReader(f):
        try:
            rows.append({k: float(v) for k, v in r.items()
                         if k in ("t", "flapLt", "flapLa", "flapRt", "flapRa",
                                  "twistLt", "twistLa", "twistRt", "twistRa",
                                  "freq", "dA", "elev", "ail", "rud", "z", "vx")
                         and v != ""})
        except (ValueError, TypeError):
            continue

T0, T1 = 20.0, 26.0     # 巡航稳态窗口（验收四项 100% 段）
seg = [r for r in rows if T0 <= r["t"] <= T1]
t = [r["t"] - T0 for r in seg]
plt.rcParams.update({"font.family": "Microsoft YaHei", "font.size": 8})

# ── 图 A：关节波形（目标虚线 vs 实际实线，joint_scope 风格）──
fig, axes = plt.subplots(4, 1, figsize=(6.4, 4.6), sharex=True)
fig.suptitle("关节波形显示器 · 验收飞行实测（t=20–26 s，巡航稳态段）",
             fontsize=9, y=0.985)
pairs = [("flap_L", "flapLt", "flapLa"), ("flap_R", "flapRt", "flapRa"),
         ("twist_L", "twistLt", "twistLa"), ("twist_R", "twistRt", "twistRa")]
for ax, (name, kt, ka) in zip(axes, pairs):
    ax.plot(t, [r[kt] for r in seg], "k--", lw=0.9, label="目标")
    ax.plot(t, [r[ka] for r in seg], color="#D9534F", lw=1.1, label="实际")
    ax.set_ylabel(f"{name}\n(rad)", fontsize=7)
    ax.grid(alpha=0.25, lw=0.4)
axes[0].legend(loc="upper right", fontsize=6, ncol=2, framealpha=0.6)
axes[-1].set_xlabel("时间 (s)")
fig.text(0.99, 0.005, "数据源 ident_stream_flight_0921_055131.csv（四项验收 100%）",
         ha="right", fontsize=5.5, color="#888")
fig.tight_layout(rect=[0, 0.01, 1, 0.97])
fig.savefig(os.path.join(OUT_DIR, "scope_joint_flight.png"), dpi=200)
plt.close(fig)

# ── 图 B：控制量（ctrl_scope 风格：上=操纵映射，下=输出）──
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6.4, 3.6), sharex=True)
fig.suptitle("控制量显示器 · 验收飞行实测（同一时段）", fontsize=9, y=0.98)
ax1.plot(t, [r["freq"] for r in seg], color="#337AB7", lw=1.2, label="拍频 (Hz)")
ax1b = ax1.twinx()
ax1b.plot(t, [r["dA"] for r in seg], color="#5CB85C", lw=1.0, label="翼差动 (rad)")
ax1.set_ylabel("拍频 (Hz)", fontsize=7)
ax1b.set_ylabel("翼差动 (rad)", fontsize=7)
ax1.grid(alpha=0.25, lw=0.4)
h1, l1 = ax1.get_legend_handles_labels()
h2, l2 = ax1b.get_legend_handles_labels()
ax1.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=6, framealpha=0.6)
ax2.plot(t, [r["elev"] for r in seg], color="#D9534F", lw=1.0, label="升降舵")
ax2.plot(t, [r["ail"] for r in seg], color="#F0AD4E", lw=1.0, label="差动舵")
ax2.plot(t, [r["rud"] for r in seg], color="#5BC0DE", lw=1.0, label="方向舵")
ax2.set_ylabel("舵偏 (rad)", fontsize=7)
ax2.set_xlabel("时间 (s)")
ax2.grid(alpha=0.25, lw=0.4)
ax2.legend(loc="upper right", fontsize=6, ncol=3, framealpha=0.6)
fig.text(0.99, 0.005, "同一数据文件 · 高度带/速度带/俯仰带 100%，滚转极值 0.000",
         ha="right", fontsize=5.5, color="#888")
fig.tight_layout(rect=[0, 0.02, 1, 0.96])
fig.savefig(os.path.join(OUT_DIR, "scope_ctrl_flight.png"), dpi=200)
plt.close(fig)
print("saved:", OUT_DIR)
