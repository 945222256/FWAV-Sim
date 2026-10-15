# -*- coding: utf-8 -*-
"""控制结构框图两张（按 flight_controller.py 现行实现绘制，215g 口径）。
图B：TECS 总能量控制结构（代码 2C 节，巡航口径）+ 悬停口径注记。
图C：姿态串级环结构（代码第 3、4 节 + 分配节）。
所有数值取自 config/sim_config.py 当前值，未臆造。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.executable).parent.parent.parent))
from daimon_runtime import setup_plot
setup_plot()
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BG, DARK, ACC, CARD, GRAY = "#F7F5EF", "#2E4045", "#B66A3C", "#FBFAF6", "#7E838A"


def new_ax(w=13.33, h=6.2):
    fig, ax = plt.subplots(figsize=(w, h), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100); ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, lines, fill=CARD, edge="#CFC9BC", tcol=DARK,
        fs=12.5, lw=1.4, bold_first=True):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                facecolor=fill, edgecolor=edge, linewidth=lw))
    n = len(lines)
    for i, ln in enumerate(lines):
        wt = "bold" if (i == 0 and bold_first) else "normal"
        ax.text(x + w / 2, y + h * (1 - (i + 0.5) / n) * 0.92 + h * 0.04, ln,
                ha="center", va="center", fontsize=fs if i == 0 else fs - 1.5,
                color=tcol, fontweight=wt, linespacing=1.4)


def summer(ax, x, y, r=2.2):
    ax.add_patch(plt.Circle((x, y), r, facecolor="#FFFFFF",
                            edgecolor=DARK, linewidth=1.4, zorder=5))
    ax.text(x, y, "Σ", ha="center", va="center", fontsize=13, color=DARK,
            fontweight="bold", zorder=6)


def arrow(ax, x1, y1, x2, y2, color=DARK, lw=1.8, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                 mutation_scale=15, linewidth=lw, color=color,
                                 linestyle=ls, shrinkA=1, shrinkB=1, zorder=4))


def lab(ax, x, y, s, fs=10.5, color=DARK, ha="center", va="center", rot=0):
    ax.text(x, y, s, fontsize=fs, color=color, ha=ha, va=va, rotation=rot)


# ═══════════════ 图 B：TECS 结构框图 ═══════════════
fig, ax = new_ax()

# 测量/参考
box(ax, 1, 74, 15, 16, ["参考输入", "z_r（高度）", "V_c = 4.0 m/s", "（CRUISE_VX）"], fill="#F0EDE4", fs=12)
box(ax, 1, 46, 15, 16, ["测量（经 EWMA", "低通 τ = 0.08 s）", "z、vx、vz"], fill="#F0EDE4", fs=12)

# 速度基准
box(ax, 22, 76, 26, 13, ["本环节生成速度基准",
                         "vxd = V_c − 0.30·(z − z_r)",
                         "限幅 [2.0, 6.0] m/s"])
# 比能求和点
summer(ax, 57, 82)
box(ax, 64, 76, 33, 13, ["本环节计算总比能误差",
                         "e_E = 9.81·(z_r − z) + ½(vxd² − vx²)",
                         "单位 J/kg（与质量解耦）"])
# 拍频通道
box(ax, 64, 55, 33, 13, ["本环节计算拍频（比例＋条件积分）",
                         "f = 1.55 + 0.03·e_E + 0.010·∫e_E dt",
                         "限幅 [1.0, 3.5] Hz + 斜坡限幅"], )
box(ax, 64, 34, 33, 13, ["本环节执行积分抗饱和",
                         "仅当 1.01 < f < 3.465 时累计",
                         "积分状态限幅 ±60 J/kg·s"], fill="#F0EDE4", fs=12)
# 分配通道
summer(ax, 57, 24)
box(ax, 64, 13, 33, 13, ["本环节计算俯仰指令",
                         "e_B = clip(9.81·(z_r−z) − ½(vxd²−vx²), ±60)",
                         "θ_t = clip(0 + 0.004·e_B − 0.15·vz, ±0.35 rad)"])

# 连线
arrow(ax, 16, 82, 22, 82)
lab(ax, 19, 84.5, "z_r、V_c", fs=9.5)
arrow(ax, 16, 54, 44, 54); arrow(ax, 44, 54, 44, 76)
lab(ax, 30, 57.5, "z、vx（滤波后）", fs=9.5)
arrow(ax, 48, 82, 54.8, 82); lab(ax, 51, 84.8, "vxd", fs=10)
arrow(ax, 57, 82, 64, 82)
arrow(ax, 80.5, 76, 80.5, 68)
arrow(ax, 57, 24, 64, 19.5)
arrow(ax, 48, 82, 57, 24.5, ls="--")  # z 进分配求和点（示意，实际由 e_B 式计算）
lab(ax, 44, 30, "z_r、z、vx、vz", fs=9, color=GRAY)
arrow(ax, 16, 50, 50, 50, ls="--"); arrow(ax, 50, 50, 50, 20); arrow(ax, 50, 20, 57, 21)
# 输出
box(ax, 24, 13, 26, 13, ["输出", "拍频 f → 波形级", "θ_t → 姿态外环"], fill=DARK, tcol="#F7F5EF", fs=12)
arrow(ax, 64, 40, 50, 26, ls="--")
lab(ax, 61, 44, "f", fs=10, color=GRAY)
arrow(ax, 64, 19.5, 50, 19.5)
lab(ax, 57, 22, "θ_t", fs=10, color=GRAY)

# 悬停口径注记
ax.text(2, 6, "悬停口径（CRUISE_VX = 0 时跳过 2C 节，由第 1 节接管）：f = clip(1.55 + 1.5·(z_r − z) − 0.7·vz, [1.0, 3.5])；"
              "速度通道走悬停支路 vxd = 0.40·e_x + 0.15·∫e_x，ax_d = 0.8·(vxd − vx)，fx_d = 0.215·ax_d + clip(0.18·vxd, ±1.0)，θ_t = fx_d / 0.67。",
        fontsize=10.5, color=DARK, ha="left", va="center")
ax.text(2, 1.5, "出处：flight_controller.py 第 1 节与 2C 节；增益与限幅值取自 config/sim_config.py（215g 口径）。"
              "旧交换配对（速度误差→拍频）因“高且慢”象限正反馈作废（0920_104813 在环失速实录）。",
        fontsize=10.5, color=GRAY, ha="left", va="center")
fig.savefig("TECS结构框图.png", bbox_inches="tight", facecolor=BG)
plt.close(fig)
print("saved TECS")

# ═══════════════ 图 C：姿态串级环 ═══════════════
fig, ax = new_ax()

# 期望生成
box(ax, 1, 76, 20, 16, ["本环节生成姿态期望", "roll_des = clip(−0.8·(vyd − vy_f),", "±0.35)　vy_f：EWMA τ=0.08"], fs=11.5)
box(ax, 1, 52, 20, 16, ["本环节生成俯仰期望", "fx_d = 0.215·ax_d +", "clip(0.18·vxd, ±1.0)"], fs=11.5)
box(ax, 1, 28, 20, 14, ["本环节对期望俯仰角斜坡限幅", "≤ 1.2 rad/s", "（PITCH_DES_SLEW）"], fs=11.5)
# 外环
summer(ax, 31, 84)
box(ax, 36, 78, 26, 13, ["本环节计算滚转与偏航期望角速度",
                         "wxd = 1.2·wrap(roll_des − roll)",
                         "wzd = 1.2·wrap(yaw_r − yaw)"], fs=11)
summer(ax, 31, 58)
box(ax, 36, 52, 26, 13, ["本环节计算俯仰期望角速度",
                         "wyd = 1.2·e_θ + 0.10·∫e_θ dt",
                         "积分门 |e_θ|<0.25，状态限幅 ±2.5"], fs=11)
# 反馈低通
box(ax, 36, 30, 26, 12, ["本环节对反馈角速度低通滤波", "一阶，τ = 0.08 s", "ω_f = LP(ω)"], fill="#F0EDE4", fs=11.5)
# 内环
summer(ax, 70, 70)
box(ax, 75, 64, 24, 13, ["本环节计算三轴力矩指令",
                         "m = 0.25·(w_d − ω_f)",
                         "m = {mx, my, mz} N·m"], fs=11.5)
# 分配
box(ax, 75, 36, 24, 20, ["本环节把力矩换算为舵偏",
                         "elev = clip(my/b_elev − 1.3·α_ff, ±0.30)",
                         "ail = clip(0.15·mx/b_ail, ±0.30)",
                         "rud = clip(mz/b_rud, ±0.30)",
                         "b_elev = −0.0145·v_eff²，b_ail = +0.00483·v_eff²"], fs=10)
box(ax, 40, 8, 56, 12, ["本环节计算翼差动指令（配平邻域调度）",
                        "dA = clip((0.04·e_φ − 0.008·ω_fx)·0.0406/max(0.0169·f², 1), ±0.08)",
                        "v_eff = min(√(v_air² + (0.472·f)²), 8) m/s；v_eff < 1.0 时尾舵三通道关闭"], fs=10.5)
# 反馈
box(ax, 1, 6, 20, 12, ["被控对象输出", "姿态 roll/pitch/yaw", "角速度 ω（120 Hz）"], fill=DARK, tcol="#F7F5EF", fs=11)

arrow(ax, 21, 84, 28.8, 84)
arrow(ax, 33, 84, 36, 84); lab(ax, 34.5, 87, "e_φ", fs=10)
arrow(ax, 21, 60, 28.8, 58)
arrow(ax, 33, 58, 36, 58); lab(ax, 34.5, 61, "e_θ", fs=10)
arrow(ax, 21, 35, 36, 35)
arrow(ax, 62, 84, 70, 71.5); lab(ax, 67, 80, "wxd、wzd", fs=9.5)
arrow(ax, 62, 58, 70, 69)
arrow(ax, 62, 36, 75, 42, ls="--"); lab(ax, 66, 37, "ω_f 反馈", fs=9, color=GRAY)
arrow(ax, 62, 36, 70, 68, ls="--")
arrow(ax, 75, 64, 87, 56)
arrow(ax, 87, 36, 87, 20); arrow(ax, 87, 20, 96, 20)
arrow(ax, 21, 12, 31, 12); arrow(ax, 31, 12, 31, 84, ls="--"); arrow(ax, 31, 84, 28.8, 84.5, ls="--")
lab(ax, 26, 50, "反馈", fs=9.5, color=GRAY, rot=90)
ax.text(2, 97, "外环带宽由 KP_A = 1.2 (1/s) 定；内环比例 KP_R = 0.25，配合舵效调度后悬停俯仰通道闭环阻尼 ≈ 3 1/s（config 注释）。",
        fontsize=10.5, color=GRAY, ha="left", va="center")
ax.text(2, 1.5, "出处：flight_controller.py 第 3 节（姿态外环）、第 4 节（内环与分配）；数值均取自 config/sim_config.py 现行值（215g 口径）。",
        fontsize=10.5, color=GRAY, ha="left", va="center")
fig.savefig("姿态串级环框图.png", bbox_inches="tight", facecolor=BG)
plt.close(fig)
print("saved cascade")
