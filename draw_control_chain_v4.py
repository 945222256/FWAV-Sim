# -*- coding: utf-8 -*-
"""控制律全链路架构图 v3：按《语义核查报告》修订。
修订要点：① 两处图文矛盾（状态总线接入级、离线辨识不改写力律）已修正；
② 全部副标题与接口标签改写为完整语句；③ 补出 TECS、分配、滑流关系式；
④ 术语统一（角速度／动压／数据使用模块）；⑤ 9 关节清单闭合；⑥ 增图例与符号注。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.executable).parent.parent.parent))
from daimon_runtime import setup_plot
setup_plot()
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BG, DARK, ACC, CARD, GRAY = "#F7F5EF", "#2E4045", "#B66A3C", "#FBFAF6", "#7E838A"

fig, ax = plt.subplots(figsize=(12, 14.6), dpi=150)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
ax.set_xlim(0, 100); ax.set_ylim(0, 100)
ax.axis("off")

def box(x, y, w, h, title, sub, fill=CARD, edge="#CFC9BC", lw=1.2,
        tcol=DARK, scol=GRAY, tfs=10.5, sfs=8.3, sub_y=0.30):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.5,rounding_size=1.0",
                                facecolor=fill, edgecolor=edge, linewidth=lw))
    ax.text(x + w/2, y + h*0.70, title, ha="center", va="center", fontsize=tfs,
            color=tcol, fontweight="bold")
    ax.text(x + w/2, y + h*sub_y, sub, ha="center", va="center", fontsize=sfs,
            color=scol, linespacing=1.45)

def arrow(x1, y1, x2, y2, color=DARK, lw=1.6, ls="-", ms=13):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=ms,
                                 linewidth=lw, color=color, linestyle=ls, shrinkA=1, shrinkB=1))

# ── 自更新程序层（顶部横带）────────────────────────────
ax.add_patch(FancyBboxPatch((2, 95.8), 96, 3.6, boxstyle="round,pad=0.3,rounding_size=0.8",
                            facecolor="#F0EDE4", edgecolor=GRAY, linewidth=1.0, linestyle="--"))
ax.text(50, 97.6, "自更新程序层：在线部分为积分控制项、指数加权滑动平均（EWMA）滤波与拍动相位积分器；\n"
                  "离线部分为台架辨识，含四项试验——舵效表（逐通道测量舵面偏转产生的力矩响应并拟合舵效系数）、升力扫频（逐拍频测量平均升力曲线）、\n"
                  "配平扫描（搜索使合力为零的速度、拍频与俯仰角组合）、增益整定（在试验架上整定控制器增益）；"
                  "辨识结果改写第③⑤⑥级的控制器常数，第⑧级力律系数保持文献值不变",
        fontsize=7.8, color=DARK, ha="center", va="center", linespacing=1.45)

# ── 八个处理级 ─────────────────────────────────────
X, W, H = 25, 42, 10.6
ys = [85.6, 72.6, 59.6, 46.6, 33.6, 20.6, 7.6, -5.4]  # 预留，实际见下
ys = [85.8, 72.7, 59.6, 46.5, 33.4, 20.3, 7.2, -5.9]
# 重新按画布排布：8 级，每级占 11.1 高（含间距）
H = 10.2
ys = [86.0 - i*11.3 for i in range(8)]

titles = ["① 规划／任务级", "② 位置外环", "③ 总能量外环（总能量控制系统 TECS）", "④ 姿态外环",
          "⑤ 角速度内环", "⑥ 控制分配级", "⑦ 波形调制级", "⑧ 被控对象级"]
subs = [
 "第①级由 bishop 规划器（ROS2 任务规划器）生成航点序列，\n或由自主飞行设定点模块直接注入目标。",
 "第②级按误差 e = r − x 计算期望速度 u = Kp·e + Ki·∫e dt，\nKp_XY、Ki_XY 为水平面增益。",
 "第③级计算总比能误差 e_E = g·(z_r − z) + ½(vxd² − vx²) 与能量分配误差 e_B = g·(z_r − z) − ½(vxd² − vx²)，\n按 f = 1.55 + 0.03·e_E + 0.010·∫e_E dt 输出拍频、按 θ_t = 0.004·e_B − 0.15·vz 输出俯仰修正，两路输出均经限幅。",
 "第④级输出期望角速度：滚转与偏航通道取比例控制（KP_A = 1.2），\n俯仰通道取比例加积分控制（KI_A = 0.10，条件积分）。",
 "第⑤级先对实测角速度做一阶低通滤波（τ = 0.08 s），\n再按比例增益 KP_R = 0.25 计算三轴力矩指令。",
 "第⑥级求解分配方程 τ = B(v, f)·u：尾舵舵效 B(v_eff) = k·v_eff²\n（k_elev = k_rud = −0.0145、k_ail = +0.00483），翼差动舵效在配平邻域等效 B_dA(f) = 0.0169·f²（f > 2.2 Hz 变号、通道视为不可信），舵令 u = pinv(B)·τ。",
 "第⑦级把拍频、差动与冻结步态合成为 9 关节目标波形：\n左右翼各 3 关节（扑动、扭转、折曲），尾翼 3 通道（升降、方向、差动），\n关节指令更新率 120 Hz。",
 "第⑧级由 Sane–Dickinson（2002）准定常叶素力律与 PhysX 刚体积分组成，\n力律可在钉固台架模式下脱离刚体单独求值。",
]
for i in range(8):
    if i == 7:
        box(X, ys[i], W, H, titles[i], subs[i], fill=DARK, tcol="#F7F5EF",
            scol="#C9CDC9", edge=DARK, sub_y=0.32)
    else:
        box(X, ys[i], W, H, titles[i], subs[i], sub_y=0.30)

# ── 级间接口标签（完整传输语句）────────────────────────
labels = [
 "第①级向第②级输出参考位置与前向速度 {xr, yr, zr, vx*}",
 "第②级向第③级输出期望速度 {vxd, vyd}",
 "第③级向第④级输出拍频 freq 与期望俯仰 pitch_des",
 "第④级向第⑤级输出期望角速度 {wxd, wyd, wzd}",
 "第⑤级向第⑥级输出指令力矩 {mx, my, mz}",
 "第⑥级向第⑦级输出舵令 {升降舵 elev, 副翼 ail, 方向舵 rud} 与翼差动 dA",
 "第⑦级向第⑧级输出 9 关节目标波形",
]
for i, lab in enumerate(labels):
    arrow(X + W/2, ys[i], X + W/2, ys[i+1] + H)
    if i == 3:
        ax.text(X + W/2 - 2.5, (ys[i] + ys[i+1] + H) / 2 + 0.15, lab,
                fontsize=8.2, color=DARK, ha="right", va="center")
    else:
        ax.text(X + W/2 + 2.5, (ys[i] + ys[i+1] + H) / 2 + 0.15, lab,
                fontsize=8.2, color=DARK, ha="left", va="center")

# ── 状态总线（右侧，仅接入第②③④⑤级）───────────────────
bus_x = 93
ax.plot([bus_x, bus_x], [1.8, 88.5], color=ACC, lw=2.2)
ax.text(bus_x - 1, 89.8, "状态总线（跨级反馈）", fontsize=10, color=ACC, ha="center", fontweight="bold")
arrow(X + W + 0.5, ys[7] + H/2, bus_x, ys[7] + H/2, color=ACC, lw=2.2)
ax.text((X + W + bus_x)/2 + 0.5, ys[7] + H/2 - 3.6,
        "第⑧级输出 t+dt 时刻全状态 x(t+dt) ∈ R¹³\n（位置 3 分量 · 姿态四元数 4 分量 · 线速度 3 分量 · 角速度 3 分量）",
        fontsize=7.8, color=ACC, ha="center", va="center")
for i in (1, 2, 3, 4):
    yy = ys[i] + H/2
    arrow(bus_x, yy, X + W + 0.8, yy, color=ACC, lw=1.1)
ax.text(bus_x + 1.5, 52, "第②③④⑤级直接读取全状态 x(t)", fontsize=8.5, color=ACC,
        ha="center", va="center", rotation=90)

# ── 同层交叉前馈（③ → ⑥）──────────────────────────────
ax.annotate("", xy=(X + W + 1.2, ys[5] + H/2), xytext=(X + W + 1.2, ys[2] + 1),
            arrowprops=dict(arrowstyle="-|>", color=GRAY, lw=1.2, linestyle="--",
                            connectionstyle="arc3,rad=-0.3"))
ax.text(X + W + 3.5, (ys[5] + ys[2]) / 2 + H/2 - 3.0,
        "第③级以拍频调度第⑥级：\nB_dA 配平邻域查表（0.0169·f² 等效）；\n滑流估计 v_slip = 0.472·f（m/s），\n合流速度 v_eff = √(v_air² + v_slip²)\n决定尾舵动压",
        fontsize=7.5, color=GRAY, ha="left", va="center", linespacing=1.5)

# ── 力与力矩数据使用模块（左下）───────────────────────────
box(1.5, 13.5, 19, 12.5, "力 {F} 与力矩 {T}\n数据的使用模块",
    "PhysX 刚体积分\n79 列飞行记录器\n看板与辨识分析器", tfs=9, sfs=7.5, sub_y=0.30)
arrow(X - 0.8, ys[7] + H/2, 20.8, 18.5, color=GRAY, lw=1.2)

# ── 符号注（左下第二卡）────────────────────────────────
box(1.5, 0.5, 19, 12.2, "符号注",
    "e_E：总比能误差（J/kg）；e_B：能量分配误差；\nf0：巡航拍频基准 1.55 Hz；k：尾舵舵效系数；\nv_air：真实空速；v_slip：滑流速度（0.472 m/s/Hz）；\nv_eff：合流速度；pinv：矩阵伪逆运算；\n冻结步态：步态波形参数固定、控制器仅调制幅值；\n{·}：列向量；上标 *：参考值",
    tfs=9, sfs=7.2, sub_y=0.28)

# ── 图例 ────────────────────────────────────────────
ax.text(50, 0.8, "图例：黑色实线箭头＝控制指令流；橙色实线＝状态反馈总线；灰色虚线＝参数调度与数据流。",
        fontsize=8.5, color=GRAY, ha="center", va="center")

fig.savefig("控制律全链路架构图_v4.png", bbox_inches="tight", facecolor=BG)
print("saved v4")
