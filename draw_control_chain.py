# -*- coding: utf-8 -*-
"""控制律全链路架构图 v2：处理程序链 + 状态总线 + 自更新程序层（重排版）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.executable).parent.parent.parent))
from daimon_runtime import setup_plot
setup_plot()
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BG, DARK, ACC, CARD, GRAY = "#F7F5EF", "#2E4045", "#B66A3C", "#FBFAF6", "#7E838A"

fig, ax = plt.subplots(figsize=(12, 14), dpi=150)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
ax.set_xlim(0, 100); ax.set_ylim(0, 100)
ax.axis("off")

def box(x, y, w, h, title, sub, fill=CARD, edge="#CFC9BC", lw=1.2, tcol=DARK, scol=GRAY, tfs=10.5, sfs=8.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.5,rounding_size=1.0",
                                facecolor=fill, edgecolor=edge, linewidth=lw))
    ax.text(x + w/2, y + h*0.62, title, ha="center", va="center", fontsize=tfs,
            color=tcol, fontweight="bold")
    ax.text(x + w/2, y + h*0.27, sub, ha="center", va="center", fontsize=sfs, color=scol)

def arrow(x1, y1, x2, y2, color=DARK, lw=1.6, ls="-", ms=13):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=ms,
                                 linewidth=lw, color=color, linestyle=ls, shrinkA=1, shrinkB=1))

# 自更新程序层（顶部横带）
ax.add_patch(FancyBboxPatch((2, 97.0), 96, 2.6, boxstyle="round,pad=0.3,rounding_size=0.8",
                            facecolor="#F0EDE4", edgecolor=GRAY, linewidth=1.0, linestyle="--"))
ax.text(50, 98.3, "自更新程序层：在线＝积分器（I 项）· EWMA 滤波 · 拍动相位积分器；离线＝台架辨识（舵效表 / 升力扫频 / 配平扫描 / 增益整定）→ 改写第 ③⑤⑥⑧ 级的常数",
        fontsize=9, color=DARK, ha="center", va="center", fontweight="bold")

X, W, H = 25, 42, 9.2
ys = [87.4, 75.2, 63.0, 50.8, 38.6, 26.4, 14.2, 2.0]
titles = ["① 规划 / 任务", "② 位置外环", "③ 总能量外环（TECS）", "④ 姿态外环",
          "⑤ 角速度内环", "⑥ 控制分配", "⑦ 波形调制", "⑧ 被控对象：瞬时力律本体＋刚体"]
subs = ["处理程序：bishop 规划器（ROS2）或自动驾驶仪设定点",
        "处理程序：比例＋积分（KP_XY, KI_XY）",
        "处理程序：比能误差→拍频，分配误差→俯仰",
        "处理程序：比例＋俯仰积分（KP_A, KI_A）",
        "处理程序：比例（KP_R）＋角速率低通滤波",
        "处理程序：尾舵 B(v_eff)=k·v²，翼差动 B_dA=5.2f²",
        "处理程序：拍频＋差动＋冻结步态 → 9 关节目标波形（120 Hz）",
        "S&D 准定常叶素力律（离线可运行）＋ PhysX 刚体积分"]
for i in range(8):
    if i == 7:
        box(X, ys[i], W, H, titles[i], subs[i], fill=DARK, tcol="#F7F5EF", scol="#C9CDC9", edge=DARK)
    else:
        box(X, ys[i], W, H, titles[i], subs[i])

labels = ["期望位置 {xr, yr, zr, vx*}",
          "期望速度 {vxd, vyd}",
          "拍频 freq ＋ 期望俯仰 pitch_des",
          "期望角速度 {wxd, wyd, wzd}",
          "力矩需求 {mx, my, mz}",
          "舵令 {elev, ail, rud} ＋ 差动 dA",
          "关节目标 {flap / twist / fold, 尾舵}"]
for i, lab in enumerate(labels):
    arrow(X + W/2, ys[i], X + W/2, ys[i+1] + H)
    ax.text(X + W/2 + 2.5, (ys[i] + ys[i+1] + H) / 2 + 0.15, lab,
            fontsize=8.5, color=DARK, ha="left", va="center")

# 状态总线（右侧跨级反馈）
bus_x = 93
ax.plot([bus_x, bus_x], [6.6, 90], color=ACC, lw=2.2)
ax.text(bus_x - 1, 91.3, "状态总线（跨级反馈）", fontsize=10, color=ACC, ha="center", fontweight="bold")
arrow(X + W + 0.5, 6.6, bus_x, 6.6, color=ACC, lw=2.2)
ax.text((X + W + bus_x)/2 + 0.5, 8.0, "新状态 {x, y, z, quat, vx, vy, vz, wx, wy, wz}(t+dt)",
        fontsize=8, color=ACC, ha="center")
for i in (1, 2, 3, 4):
    yy = ys[i] + H/2
    arrow(bus_x, yy, X + W + 0.8, yy, color=ACC, lw=1.1)
ax.text(bus_x + 1.5, 48, "每一级都直接读取全状态", fontsize=8.5, color=ACC,
        ha="center", va="center", rotation=90)

# 同层交叉前馈（③ → ⑥）
ax.annotate("", xy=(X + W + 1.2, ys[5] + H/2), xytext=(X + W + 1.2, ys[2] + 1),
            arrowprops=dict(arrowstyle="-|>", color=GRAY, lw=1.2, linestyle="--",
                            connectionstyle="arc3,rad=-0.3"))
ax.text(X + W + 3.5, (ys[5] + ys[2]) / 2 + H/2 + 1.5,
        "拍频 → 差动舵效调度\n拍频 → 滑流估计 → 尾舵动压", fontsize=7.5, color=GRAY, ha="left")

# 瞬时力律输出的消费方（左下）
box(1.5, 2.0, 19, 12.5, "力 {F} 与力矩 {T}\n的消费方", "PhysX 积分（本体）\n79 列记录器\n看板 / 辨识分析器", tfs=9, sfs=7.5)
arrow(X - 0.8, ys[7] + 3, 20.8, ys[7] + 6.5, color=GRAY, lw=1.2)

fig.savefig("控制律全链路架构图.png", bbox_inches="tight", facecolor=BG)
print("saved v2")
