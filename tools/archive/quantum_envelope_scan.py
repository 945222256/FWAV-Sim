# -*- coding: utf-8 -*-
"""
quantum_envelope_scan.py — 权限包线扫描：量子表加侧滑与坡度两维
（不定频率完全差动线，2026-09-28 晨）

任务来源：《量子异步抽验_口径调和.md》第四节第一步——密度差动权限随
侧滑塌缩反号，"权限是否够"必须用包线回答，不能用配平点数字回答。

判定标准（先于数据写定于本文件头，运行后不改）：
  E1：在协调转弯走廊（坡度 φ∈[0°,+10°]、侧滑角 β∈[−10°,0°]）内，
      密度通道的滚转权限必须与巡航配平点同号（正号=停右翼产生正滚转，
      与首日标定方向一致）。
  E2：输出密度通道的反号边界 β*（以坡度为自变量的函数）。
  E3：同网格测幅值差动通道（dA=±0.10 rad，已验证域内）作对照，
      回答"大侧滑下哪个翼面通道还保得住权限"。

测量设计：固定速度大小 3.95 m/s、拍频 1.80 Hz、俯仰钉巡航配平值
−13.03°（俯仰不随工况重配平，登记为简化假设）；侧滑角 β 网格
−60°~+15°，坡度 φ 网格 −20°~+20°。每格点用整机 compute_forces
路径（抽验已定案与量子表口径一致）测两组差分：双翼全拍对右翼停拍
（密度通道满额）、幅值左翼+0.10 对右翼−0.10（幅值差动满额已验证域）。
每拍 288 点、4 拍窗口、时间轴单调推进、显式设频。

用法： python tools/quantum_envelope_scan.py
产物： logs/quantum_envelope_scan.json + docs/权限包线_侧滑坡度扫描.md
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
from modules.aero_solver import euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

V0 = 3.95
F0 = 1.80
THETA0 = -13.03          # 巡航配平俯仰（前向力根口径，登记为固定假设）
N_PER_BEAT = 288
N_BEATS = 4
DA_TEST = 0.10           # 幅值差动考核幅度 rad（已验证域边界）
BETA_GRID = [-60, -45, -30, -20, -15, -10, -5, 0, 5, 10, 15]
PHI_GRID = [-20, -10, 0, 10, 20]

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(cfg.FlapKinematicsConfig.ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)
    w.amplitude = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
AMP0 = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)

TIMEBASE = [400000.0]


def mean_wrench(quat, vel, f, n_beats=N_BEATS):
    """整机 compute_forces 在整数拍窗口内的机体系平均力与力矩。"""
    model.set_wing_command(frequency=f)
    T = n_beats / f
    n = N_PER_BEAT * n_beats
    ts = [TIMEBASE[0] + i * T / n for i in range(n)]
    TIMEBASE[0] += T
    Fq = np.zeros(3)
    Tq = np.zeros(3)
    for t in ts:
        model.compute_forces(np.zeros(3), quat, vel, np.zeros(3), t)
        Fq += model.current_F_body
        Tq += model.current_T_body
    return Fq / n, Tq / n


def cell(beta_deg, phi_deg):
    """单格点：密度通道与幅值通道的滚转/偏航/升力权限。"""
    beta = math.radians(beta_deg)
    vel = np.array([V0 * math.cos(beta), V0 * math.sin(beta), 0.0])
    quat = euler_to_quat(math.radians(phi_deg), math.radians(THETA0), 0.0)
    # 基线：双翼全拍、对称幅值
    Fb, Tb = mean_wrench(quat, vel, F0)
    # 密度通道：右翼停拍
    model.wing_right.stroke_gate = 0.0
    Fd, Td = mean_wrench(quat, vel, F0)
    model.wing_right.stroke_gate = 1.0
    # 幅值通道：左加右减
    model.wing_left.amplitude = AMP0 + DA_TEST
    model.wing_right.amplitude = AMP0 - DA_TEST
    Fa, Ta = mean_wrench(quat, vel, F0)
    model.wing_left.amplitude = AMP0
    model.wing_right.amplitude = AMP0
    return {
        "beta": beta_deg, "phi": phi_deg,
        # 权限统一按"动作产生的力矩"符号登记：停右翼应产生正滚转
        # （首日标定方向），故取 停拍态 − 全拍态；正值=方向正确。
        "density_dTx": float(Td[0] - Tb[0]),   # 停右翼的正滚转权限
        "density_dTz": float(Td[2] - Tb[2]),
        "density_dFz": float(Fd[2] - Fb[2]),
        "dA_dTx": float(Ta[0] - Tb[0]),        # 左加右减的滚转权限
        "dA_dTz": float(Ta[2] - Tb[2]),
        "dA_dFz": float(Fa[2] - Fb[2]),
    }


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = []
    for phi in PHI_GRID:
        for beta in BETA_GRID:
            r = cell(beta, phi)
            rows.append(r)
            print(f"[env] φ={phi:+3d}° β={beta:+4d}°：密度 ΔTx="
                  f"{r['density_dTx']:+.5f}  幅值 ΔTx={r['dA_dTx']:+.5f}",
                  flush=True)

    # E1：协调转弯走廊内符号检查（φ∈{0,10}，β∈{−10,−5,0}）
    corr = [r for r in rows if r["phi"] in (0, 10)
            and r["beta"] in (-10, -5, 0)]
    e1 = all(r["density_dTx"] > 0 for r in corr)
    # E2：反号边界 β*（每个 φ 上密度权限的全部变号点，双向捕捉）
    boundaries = {}
    for phi in PHI_GRID:
        col = sorted([r for r in rows if r["phi"] == phi],
                     key=lambda r: r["beta"])
        crossings = []
        for a, b in zip(col[:-1], col[1:]):
            if a["density_dTx"] * b["density_dTx"] < 0:
                w = abs(a["density_dTx"]) / (
                    abs(a["density_dTx"]) + abs(b["density_dTx"]))
                crossings.append(round(a["beta"] + w * (b["beta"] - a["beta"]), 1))
        boundaries[str(phi)] = crossings if crossings else None
    out = {"ts": ts, "V": V0, "f": F0, "theta": THETA0,
           "dA_test": DA_TEST, "rows": rows,
           "E1_corridor_positive": e1,
           "E2_sign_flip_boundary_deg": boundaries}
    with open(os.path.join(cfg.LOG_DIR, "quantum_envelope_scan.json"),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    print(f"[env] E1 走廊内密度权限恒正：{'通过' if e1 else '不通过'}",
          flush=True)
    print(f"[env] E2 反号边界（度）：{boundaries}", flush=True)

    # ── 文档 ──
    md = [f"# 权限包线：侧滑×坡度扫描（{ts}）", "",
          "- 判定标准先于数据写定于 tools/quantum_envelope_scan.py 文件头"
          "（E1 走廊同号、E2 反号边界、E3 幅值对照），运行后未改",
          "- 工况：V=3.95 m/s、f=1.80 Hz、俯仰钉 −13.03°（简化假设已登记）",
          "- 原始数据：`logs/quantum_envelope_scan.json`", "",
          "## 密度通道滚转权限 ΔTx（牛米，停右翼效应，正=方向正确）", "",
          "| φ＼β | " + " | ".join(f"{b:+d}°" for b in BETA_GRID) + " |",
          "|---|" + "---|" * len(BETA_GRID)]
    for phi in PHI_GRID:
        col = [r for r in rows if r["phi"] == phi]
        md.append(f"| {phi:+d}° | " + " | ".join(
            f"{r['density_dTx']:+.4f}" for r in col) + " |")
    md += ["", "## 幅值差动通道滚转权限 ΔTx（牛米，左加右减 0.10 rad）", "",
           "| φ＼β | " + " | ".join(f"{b:+d}°" for b in BETA_GRID) + " |",
           "|---|" + "---|" * len(BETA_GRID)]
    for phi in PHI_GRID:
        col = [r for r in rows if r["phi"] == phi]
        md.append(f"| {phi:+d}° | " + " | ".join(
            f"{r['dA_dTx']:+.4f}" for r in col) + " |")
    md += ["", "## 判定", "",
           f"- E1（协调转弯走廊 φ∈[0,+10°]、β∈[−10°,0°] 内密度权限恒正）："
           f"{'通过' if e1 else '不通过'}",
           f"- E2 反号边界 β*（度，按坡度）：{boundaries}",
           "- E3 幅值对照见第二表；两通道对照结论见阶段报告。", ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", "权限包线_侧滑坡度扫描.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[env] 完成 → docs/权限包线_侧滑坡度扫描.md", flush=True)


if __name__ == "__main__":
    main()
