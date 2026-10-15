# -*- coding: utf-8 -*-
"""
measure_flap_quantum.py — 单拍操纵量子测量（第十五班）

问题：逐拍控制框架把"一次扑动（或一次停扑）"视为离散控制量。本工具
在巡航配平状态（V=4.0 m/s、f=1.75 Hz、θ=+6.6°、攻角设定 35°）下，
逐相位求值三维叶素模型，输出每翼每拍的力/力矩冲量及其半拍分解，
并给出"扑动一次 对 保持固定"的差值——一次扑动决断实际交付的冲量。

并行性声明：纯离线分析，只读现行配置与三维模型，不改任何生效文件；
周期平均控制链路与在环验收基线不受影响。与现行框架的关系：本表是
现行框架拍内数据的重新组织，不是新模型。

用法： python tools/measure_flap_quantum.py（纯 NumPy，无需 Isaac Sim）
产物： logs/flap_quantum.json + docs/单拍操纵量子表.md
"""
import os
import sys
import json
import math
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
from config import sim_config as cfg
from modules.aero_solver import euler_to_quat, quat_to_rotation_matrix
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

# 工况：第十三班修正版配平点
V_CRUISE, F_TRIM, PITCH_DEG, ALPHA_SET = 4.0, 1.75, 6.6, 35.0
N_PHASE = 96
IXX_APPROX = 2.4e-3   # kg·m²，滚转惯量约值（config/sim_config.py 注释口径）

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)
    w.amplitude = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
model.set_wing_command(frequency=F_TRIM)

QUAT = euler_to_quat(0.0, math.radians(PITCH_DEG), 0.0)
V_BODY = quat_to_rotation_matrix(QUAT).T @ np.array([V_CRUISE, 0.0, 0.0])
OM = np.zeros(3)
T_PERIOD = 1.0 / F_TRIM
DT = T_PERIOD / N_PHASE


def wing_ft(t, side, paused=False):
    """单翼体坐标系力/力矩（关于重心），复用 compute_forces 的调用口径。
    停扑建模（第十六班修正）：翼面固定中立位置、攻角保持扑动程序
    周期均值（α_set·2/π），不再随相位扭转；evaluate 照常调用以
    保持相位积分状态的时间轴连续。"""
    if side == "L":
        w, rc, po = model.wing_left, +model._r_centers.copy(), 0.0
    else:
        w, rc, po = model.wing_right, -model._r_centers.copy(), \
            model.phase_offset_lr
    ws = w.evaluate(t, phase_offset_rad=po)
    if paused:
        ws = dict(ws)
        ws["phi"] = float(w.bias + w.mean)
        ws["phi_dot"] = 0.0
        ws["phi_ddot"] = 0.0
        ws["alpha"] = float(w.alpha_set * 2.0 / math.pi + w.pitch_bias)
        ws["alpha_dot"] = 0.0
        ws["alpha_ddot"] = 0.0
    F, Tq = model._wing_forces(rc, model._chords, ws, V_BODY, OM)
    return F, Tq, float(ws["phi"]), float(ws["phi_dot"])


TIMEBASE = [200000.0]   # 全局单调推进时基（第十六班测量纪律）


def sweep(side, paused=False):
    F = np.zeros((N_PHASE, 3))
    Tq = np.zeros((N_PHASE, 3))
    phid = np.zeros(N_PHASE)
    for i in range(N_PHASE):
        t = TIMEBASE[0] + i * (T_PERIOD / N_PHASE)
        f, q, _, pd = wing_ft(t, side, paused)
        F[i], Tq[i], phid[i] = f, q, pd
    TIMEBASE[0] += T_PERIOD
    return F, Tq, phid


def impulse(X):
    return np.sum(X, axis=0) * DT


def half_masks(phid):
    return phid > 0, phid < 0   # 上半拍/下半拍（AERO_MODEL.md 口径）


def summarize(F, Tq, phid):
    up, dn = half_masks(phid)
    return dict(
        J_F=impulse(F).tolist(),
        J_T=impulse(Tq).tolist(),
        J_T_up=impulse(Tq[up]).tolist(),
        J_T_dn=impulse(Tq[dn]).tolist(),
        p2p_Tx=float(np.ptp(Tq[:, 0])),
        p2p_Tz=float(np.ptp(Tq[:, 2])),
        mean_Fz=float(np.mean(F[:, 2])),
    )


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "model": "sane_dickinson_3d",
           "state": {"V": V_CRUISE, "f": F_TRIM, "pitch_deg": PITCH_DEG,
                     "alpha_set_deg": ALPHA_SET, "bank": 0.0},
           "n_phase": N_PHASE, "ixx_approx": IXX_APPROX}

    wings = {}
    for side in ("L", "R"):
        F, Tq, phid = sweep(side, paused=False)
        Fp, Tqp, _ = sweep(side, paused=True)
        s = summarize(F, Tq, phid)
        dJ_F = impulse(F - Fp)
        dJ_T = impulse(Tq - Tqp)
        s["quantum_dJ_F"] = dJ_F.tolist()
        s["quantum_dJ_T"] = dJ_T.tolist()
        s["quantum_domega_roll_dps"] = float(
            math.degrees(dJ_T[0] / IXX_APPROX))
        # 偏航折算需偏航惯量 Izz（现无 215 g 口径真值），不列换算值
        s["p2p_delta_Tx"] = float(np.ptp((Tq - Tqp)[:, 0]))
        wings[side] = s
    out["wings"] = wings

    with open(os.path.join(cfg.LOG_DIR, "flap_quantum.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    def fmt3(v):
        return f"{v[0]:+.4f}/{v[1]:+.4f}/{v[2]:+.4f}"

    md = [f"# 单拍操纵量子表（{ts}）", "",
          f"- 模型：sane_dickinson_3d；状态：V={V_CRUISE} m/s、"
          f"f={F_TRIM} Hz、θ=+{PITCH_DEG}°、攻角设定 {ALPHA_SET}°、"
          f"巡航无坡度；相位采样 {N_PHASE} 点/拍",
          "- 口径：体坐标系、力矩关于重心；尾翼不在本表（尾翼不扑动，"
          "其力与扑动决断无关）",
          "- 原始数据：`logs/flap_quantum.json`", "",
          "## 每翼每拍冲量（扑动中）", "",
          "| 翼 | 力冲量 Fx/Fy/Fz (N·s) | 力矩冲量 Tx/Ty/Tz (N·m·s) |"
          " 平均竖直力 (N) |",
          "|---|---|---|---|"]
    for side, name in (("L", "左翼"), ("R", "右翼")):
        s = wings[side]
        md.append(f"| {name} | {fmt3(s['J_F'])} | {fmt3(s['J_T'])} |"
                  f" {s['mean_Fz']:.3f} |")
    md += ["", "## 半拍分解（力矩冲量，N·m·s）", "",
           "| 翼 | 上半拍 Tx/Tz | 下半拍 Tx/Tz | 拍内 Tx 峰峰值 (N·m) |",
           "|---|---|---|---|"]
    for side, name in (("L", "左翼"), ("R", "右翼")):
        s = wings[side]
        md.append(f"| {name} | {s['J_T_up'][0]:+.5f}/{s['J_T_up'][2]:+.5f} |"
                  f" {s['J_T_dn'][0]:+.5f}/{s['J_T_dn'][2]:+.5f} |"
                  f" {s['p2p_Tx']:.4f} |")
    md += ["", "## 操纵量子：扑动一次 对 保持固定（每翼）", "",
           "| 翼 | 冲量差 ΔJ_F (N·s) | 力矩冲量差 ΔJ_T (N·m·s) |"
           " 折算滚转角速度增量 (°/s) |",
           "|---|---|---|---|"]
    for side, name in (("L", "左翼"), ("R", "右翼")):
        s = wings[side]
        md.append(f"| {name} | {fmt3(s['quantum_dJ_F'])} |"
                  f" {fmt3(s['quantum_dJ_T'])} |"
                  f" {s['quantum_domega_roll_dps']:+.2f} |")
    md += ["", "## 判读", "",
           "- 操纵量子列的读法：在逐拍控制框架里，一翼'这一拍扑'与"
           "'这一拍不扑'的差别就是这一行；左右翼互为镜像（数值应"
           "满足镜像对称，偏差即检查项）。",
           "- 角速度增量按滚转惯量约值 2.4e-3 kg·m² 折算"
           "（config/sim_config.py 注释口径，真值以称量与专项辨识为准），"
           "仅用于建立量级直觉。",
           "- 半拍分解回答'一拍的冲量在周期内何时交付'：逐拍控制的"
           "事件定时与半拍级波形选择以此为输入。",
           "- 并行性：本表是现行三维模型拍内数据的重新组织，周期平均"
           "控制链路与在环验收基线不受影响。", ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", "单拍操纵量子表.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[quantum] 完成 → docs/单拍操纵量子表.md", flush=True)
    for side, name in (("L", "左翼"), ("R", "右翼")):
        s = wings[side]
        print(f"[quantum] {name} ΔJ_T={fmt3(s['quantum_dJ_T'])} N·m·s "
              f"Δω_roll={s['quantum_domega_roll_dps']:+.2f}°/s", flush=True)


if __name__ == "__main__":
    main()
