# -*- coding: utf-8 -*-
"""
fig_architecture.py — 演示级全景图两张（2026-09-20，v2 排版修正）
  fig1 rl/figs/arch_training.png   训练方法过程的实现全景
  fig2 rl/figs/arch_serving.png    实际推理服务的工作全景
配色语义：深蓝=经典控制　青=学习模块　金=数据/辨识基座　灰虚线=未来工作
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

NAVY = "#1f3a5f"
TEAL = "#2a9d8f"
GOLD = "#e9b949"
ORANGE = "#e76f51"
GRAY = "#8d99ae"
BG = "#fbfcfe"
INK = "#212529"
FACE = {"navy": "#e8eef6", "teal": "#e4f2ef", "gold": "#faf0d7",
        "orange": "#fbe9e3", "white": "#ffffff"}


def box(ax, x, y, w, h, title, lines, kind="white", ec=None, dashed=False,
        title_fs=12, fs=9.6, lw=1.6):
    fc = FACE[kind]
    ec = ec or {"navy": NAVY, "teal": TEAL, "gold": GOLD,
                "orange": ORANGE, "white": GRAY}[kind]
    bb = FancyBboxPatch((x, y), w, h,
                        boxstyle="round,pad=0.010,rounding_size=0.016",
                        fc=fc, ec=ec, lw=lw,
                        linestyle="--" if dashed else "-", zorder=2)
    ax.add_patch(bb)
    th = min(0.034, h * 0.30)
    ax.add_patch(FancyBboxPatch((x, y + h - th), w, th,
                                boxstyle="round,pad=0.010,rounding_size=0.016",
                                fc=ec, ec=ec, zorder=3))
    ax.text(x + w / 2, y + h - th / 2, title, ha="center", va="center",
            fontsize=title_fs, color="white", fontweight="bold", zorder=4)
    ax.text(x + w / 2, y + (h - th) / 2 - 0.004, "\n".join(lines),
            ha="center", va="center", fontsize=fs, color=INK, zorder=4,
            linespacing=1.5)


def arrow(ax, x1, y1, x2, y2, color=INK, lw=2.0, dashed=False, label=None,
          label_fs=9.5, rad=0.0, lx=0.0, ly=0.018):
    a = FancyArrowPatch((x1, y1), (x2, y2),
                        arrowstyle="-|>", mutation_scale=16, lw=lw,
                        color=color, linestyle="--" if dashed else "-",
                        connectionstyle=f"arc3,rad={rad}", zorder=5)
    ax.add_patch(a)
    if label:
        ax.text((x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, label,
                ha="center", va="bottom", fontsize=label_fs, color=color,
                zorder=6)


def canvas(w=15, h=8.4):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    return fig, ax


def legend(ax, x=0.012, y=0.012):
    items = [(NAVY, "经典控制"), (TEAL, "学习模块"), (GOLD, "数据/辨识基座"),
             (GRAY, "未来工作（虚线）")]
    for i, (c, t) in enumerate(items):
        ax.add_patch(plt.Rectangle((x + i * 0.135, y), 0.018, 0.018,
                                   fc=c, ec=c, transform=ax.transAxes))
        ax.text(x + i * 0.135 + 0.024, y + 0.009, t, fontsize=9.5,
                va="center", color=INK)


# ============================================================
# 图 1：训练全景
# ============================================================
fig, ax = canvas()
ax.text(0.5, 0.965, "扑翼内环映射 · 训练管线全景", ha="center",
        fontsize=20, fontweight="bold", color=INK)
ax.text(0.5, 0.927, "辨识给参数真值 → 环境给随机化动力学 → 三路训练各出其策 → 评估验收可复核",
        ha="center", fontsize=11.5, color=GRAY)

box(ax, 0.015, 0.42, 0.185, 0.42, "① 台架辨识（数据基座）",
    ["运动学固定台架 + 30Hz 记录", "升力曲线 L = 1.95·f² N",
     "悬停拍频 1.88 Hz", "B_dA(f) ≈ 5.2·f² N·m/rad",
     "L_p ≈ −0.60 N·m·s/rad", "瞬时力波形（峰峰 47 N）",
     "气动 / 惯性 / 重力 三分离"], "gold")

box(ax, 0.245, 0.42, 0.20, 0.42, "② 周期平均仿真环境 V2",
    ["S&D 准定常气动（纯 NumPy）", "50 Hz 控制 / 契约自包含冻结",
     "域随机化（reset 重采样）", "质量 · 惯量 · 升力系数",
     "执行器滞后与速率限制", "分通道传感噪声", "恒定风扰 ±1.5 m/s"],
    "navy")

box(ax, 0.50, 0.665, 0.225, 0.20, "③A 行为克隆（BC）",
    ["专家 PD（B_dA(f) 力矩分配）", "60 回合演示 + 质量门控",
     "14,000 样本蒸馏 MLP", "闭环 16/16 满存活"], "teal")
box(ax, 0.50, 0.435, 0.225, 0.185, "③B PPO 微调",
    ["BC 权重热启动", "探索噪声收缩 0.135",
     "随机化动力学上策略改进", "训练回报 +217 → +246"], "teal")
box(ax, 0.50, 0.20, 0.225, 0.185, "③C 逆映射网络",
    ["1.2 万组 波形θ→扳手W 数据", "逆网络 W*→θ（256×2 MLP）",
     "复核误差 Mx 3.5% / Fz 9.7%", "对线性伪逆 23.8% → 9.7%"], "teal")

box(ax, 0.775, 0.42, 0.21, 0.42, "④ 评估与验收",
    ["16 种子闭环基准", "专家 100% / BC 100% 存活",
     "稳态误差 0.21 m", "逆映射 vs 线性基线柱图",
     "训练曲线 / 轨迹对比图", "全部图表入 PPT"], "orange")

arrow(ax, 0.20, 0.63, 0.245, 0.63, label="真值锚定", ly=0.012)
arrow(ax, 0.445, 0.63, 0.50, 0.63, label="环境即数据引擎", lx=-0.028, ly=0.012)
arrow(ax, 0.725, 0.765, 0.775, 0.70, rad=0.1)
ax.text(0.75, 0.755, "BC 策略", fontsize=9.5, color=INK)
arrow(ax, 0.725, 0.53, 0.775, 0.60, rad=-0.05)
ax.text(0.75, 0.555, "微调策略", fontsize=9.5, color=INK)
arrow(ax, 0.725, 0.29, 0.775, 0.50, rad=-0.15)
ax.text(0.748, 0.375, "逆映射策略", fontsize=9.5, color=INK)
arrow(ax, 0.6125, 0.665, 0.6125, 0.625, label="热启动", lx=0.032, ly=0.005)

box(ax, 0.245, 0.045, 0.48, 0.085, "Real2Sim2Real 残差闭环（未来）",
    ["真机台架/飞行数据 → 校准仿真参数 → RL 在线残差微调 → 回灌逆映射策略"],
    "white", dashed=True, title_fs=10.5)
arrow(ax, 0.48, 0.13, 0.40, 0.42, color=GRAY, dashed=True, rad=-0.25)
arrow(ax, 0.86, 0.42, 0.72, 0.13, color=GRAY, dashed=True, rad=-0.15,
      label="在线残差", lx=0.055, ly=0.0)

legend(ax)
fig.savefig("rl/figs/arch_training.png", dpi=170, bbox_inches="tight")
print("saved rl/figs/arch_training.png")

# ============================================================
# 图 2：推理服务全景
# ============================================================
fig, ax = canvas()
ax.text(0.5, 0.965, "扑翼飞控 · 推理服务工作全景（可解释外环 + 学习内环）",
        ha="center", fontsize=20, fontweight="bold", color=INK)
ax.text(0.5, 0.927, "外环慢而可认证，内环快而难解析——学习只用在解析反解不存在的那一层",
        ha="center", fontsize=11.5, color=GRAY)

box(ax, 0.015, 0.60, 0.115, 0.18, "任务输入",
    ["目标位置 / 轨迹", "（悬停 / 循迹）"], "white", ec=NAVY)

box(ax, 0.175, 0.56, 0.20, 0.30, "外环（经典 · 50Hz）",
    ["TECS-lite：高度 → 拍频期望", "串级 PID：姿态 / 角速度",
     "输出期望扳手 W*", "W* = [Fz, Mx, My]"], "navy")

box(ax, 0.425, 0.60, 0.21, 0.26, "内环逆映射（学习）",
    ["逆映射网络 W* → θ", "θ = [拍频, 翼差动, 分裂比,",
     "　　　俯仰相位, 拍动面偏置]", "悬停备用：BC 策略 状态→拍频/差动"],
    "teal")

box(ax, 0.685, 0.60, 0.14, 0.26, "波形生成器",
    ["WingKinematics", "9 关节位置目标", "速率限制 / 滞后"], "navy")
box(ax, 0.865, 0.60, 0.12, 0.26, "机体与气动",
    ["fwav_15dof", "S&D 准定常", "叶素积分"], "navy")

box(ax, 0.175, 0.14, 0.20, 0.20, "状态估计（传感回灌）",
    ["IMU / 视觉 / 光流融合", "位姿 + 速度 + 拍内相位",
     "噪声特性取 FWAF-VID 标定"], "navy")

box(ax, 0.50, 0.14, 0.30, 0.20, "在线监测与 RL 适应（未来）",
    ["期望力矩 vs 实测力矩残差", "RMA 式潜变量在线推理",
     "残差微调逆映射网络"], "white", dashed=True, title_fs=11)

# 主流
arrow(ax, 0.13, 0.69, 0.175, 0.69)
arrow(ax, 0.375, 0.71, 0.425, 0.71, label="W*", ly=0.010)
arrow(ax, 0.635, 0.73, 0.685, 0.73, label="θ", ly=0.010)
arrow(ax, 0.825, 0.73, 0.865, 0.73, label="关节目标", lx=0.005, ly=0.010)
# 传感回灌（走底部，绕开中部虚线框）
arrow(ax, 0.925, 0.60, 0.925, 0.08, color=NAVY)
ax.text(0.932, 0.36, "位姿 / 速度", fontsize=9.5, color=NAVY)
arrow(ax, 0.925, 0.08, 0.275, 0.08, color=NAVY)
arrow(ax, 0.275, 0.08, 0.275, 0.14, color=NAVY)
# 状态估计 → 外环 / 内环
arrow(ax, 0.275, 0.34, 0.275, 0.56, color=NAVY)
ax.text(0.255, 0.44, "状态", fontsize=9.5, color=NAVY)
arrow(ax, 0.375, 0.30, 0.50, 0.60, color=NAVY, rad=-0.15)
ax.text(0.415, 0.47, "状态", fontsize=9.5, color=NAVY)
# 在线适应（未来）
arrow(ax, 0.925, 0.58, 0.80, 0.28, color=GRAY, dashed=True, rad=0.25)
ax.text(0.895, 0.44, "实测力矩", fontsize=9.5, color=GRAY)
arrow(ax, 0.65, 0.34, 0.56, 0.60, color=GRAY, dashed=True, rad=-0.2)
ax.text(0.575, 0.44, "残差微调", fontsize=9.5, color=GRAY)

legend(ax)
fig.savefig("rl/figs/arch_serving.png", dpi=170, bbox_inches="tight")
print("saved rl/figs/arch_serving.png")
