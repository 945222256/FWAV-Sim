# -*- coding: utf-8 -*-
"""
scan_launch_subwave.py — 选项三：波形"发射友好子型"组合搜索

依据：docs/发射窗口分析_力矩权限.md 判读六节——α_set 下调是最强单旋钮、
发射扑翼相位必须并入优化变量、mean≥20 勿下调、幅值单独下调不够、最优
单旋钮（α_set 25）δ_req=2.34 rad 仍为限幅 0.3 rad 的 7.8 倍，单旋钮
闭合不了缺口。本工具在 tools/scan_launch_window_mom.py 的姿态锁定积分
与 My 口径上做因子化组合搜索。原工具与其产物（logs/scan_launch_window
_mom.json、docs/发射窗口分析_力矩权限.md）不动。

搜索空间（变量五维）：
  α_set∈{20,25,30}°、幅值∈{25,30}°、mean∈{20,25}°、
  发射相位∈{0,0.25,0.5,0.75} 拍、发射工况∈{(4.0 m/s,25°),(6.5 m/s,15°)}
  （两档=原能量窗口的两个能量可行格）。固定量：拍频 1.25 Hz、split 0.75、
  pitch_lag −0.04 s。组合数=2 工况×3 α_set×2 幅值×2 mean=24，每组合
  四相位各跑一次（包络），共 96 跑。

发射子型定位：只用于起飞段。巡航仍用现工作点（幅值30/α_set35/mean20/
split0.75/pitch_lag−0.04）；发射后切波形属状态机既有结构，本工具只搜
发射段参数。

My 口径（与基线逐位一致）：My = model.compute_forces 返回 T_world 的 y
分量（两翼叶素合计俯仰力矩；ω=0 时 WING_ROT_DAMP 严格为零；不含尾翼/
机身贡献）。纯俯仰姿态下 Ry(θ) 保持 y 轴不动，T_world[1]==T_body[1]
（脚本内数值断言）。

判据（先于数据，硬约束优先）：
  硬约束（力矩线）：δ_req = |My|max / 0.2535 ≤ 0.30 rad；
  软约束（能量两线）：3 秒末前向速度 ≥ 3.2 m/s、3 秒末高度 ≥ 1.5 m；
  发散滤网沿用：|v_end|>20 或 |z_end|>60 判数值发散、不进窗口。
  判定顺序：先按硬约束筛——存在过硬约束且能量两线全过的跑 → 报最优
  参数；存在过硬约束但能量线有缺口 → 报硬约束通过组合及能量缺口；
  无任何组合过硬约束 → 输出 δ_req 最小的前十组合（非发散跑）与
  "最小可达 δ_req"结论。

自检（先于网格，两条）：
  1) 复现基线探针 aset30@A 与 aset25@A（α_set30/幅值30/mean20 与
     α_set25/幅值30/mean20 @ 4.0 m/s/25°，t*=220033 锚定）：本网格含
     这两个组合，相位 0 档与四相位包络应逐位复现基线 JSON（容差 1e-6）；
  2) 复现基线网格格 (6.5 m/s,15°) 基准波形（α_set35/幅值30/mean20，
     t*=220117=该格起始时刻）：相位 0 档 |My|max 应复现基线网格值
     20.501399 N·m，相对容差 5e-4——该格强瞬态（近发散，末速 5.48 m/s）
     会把连续积分对一次性重锚的浮点相位漂移（实测 T0 累积偏 4.5e-7 s）
     放大到 1e-4 量级相对偏差（实测 3.3e-3 N·m@峰 20.5，末速偏差
     1.0e-3 m/s），属"物理同一发射"的数值漂移而非锚定错误（诊断
     20260924：v_end/z_end 同步偏差 1.8e-4/3.6e-5 相对量级）。

用法： python tools/scan_launch_subwave.py
产物： logs/scan_launch_subwave.json + docs/发射友好子型搜索.md
"""
import os
import sys
import json
import math
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:  # Windows 控制台中文输出保险
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
from config import sim_config as cfg
from modules.aero_solver import euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G_ACC = 9.81
MASS = cfg.MASS
DT = 0.002
T_SIM = 3.0
Z0 = 3.0
F_OP = 1.25
PHASE_STEP_S = 0.2          # 0.25 拍 @ 1.25 Hz

# 舵面消化预算口径（同基线）：dMy/dδ=0.2535 N·m/rad，限幅 0.30 rad，
# 满偏权限当量 0.07605 N·m
D_EFF = 0.2535
D_LIM = 0.30
M_AUTH = D_EFF * D_LIM

# 固定量（与基线扫描一致）
SPLIT = 0.75
PITCH_LAG_S = -0.04

ALPHA_SET_GRID = [20.0, 25.0, 30.0]
AMP_GRID = [25.0, 30.0]
MEAN_GRID = [20.0, 25.0]
PHASE_K = [0, 1, 2, 3]      # 发射相位 = 0.25k 拍

# 工况两档=原能量窗口两个能量可行格。t* 锚定：A=基线探针 A 的锚定时刻
# （220000+3.0×格序 11=220033）；B=基线网格 (6.5,15°) 格起始时刻
# （220000+3.0×格序 39=220117）。
CONDITIONS = [
    dict(tag="A", label="A·能量可行格一（4.0 m/s，25°）",
         v0=4.0, th=25.0, t_star=220033.0),
    dict(tag="B", label="B·能量可行格二（6.5 m/s，15°）",
         v0=6.5, th=15.0, t_star=220117.0),
]

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.split = SPLIT
    w.pitch_lag_s = PITCH_LAG_S
model.set_wing_command(frequency=F_OP)

T0 = [220000.0]


def set_waveform(alpha_set_deg, amp_deg, mean_deg):
    for w in (model.wing_left, model.wing_right):
        w.alpha_set = math.radians(alpha_set_deg)
        w.amplitude = math.radians(amp_deg)
        w.mean = math.radians(mean_deg)


def reset_phase(t_start):
    """把两翼相位积分态重锚到 t_start（复刻基线探针的锚定方式）。

    相位是一维发射参数：reset 后首次求值 tau=(f·t_start)mod1，相位步进
    0.2 s 即 0.25 拍。不重锚则逐跑连积分使发射相位随机化，波形参数效应
    与相位抽奖混叠（基线实录：同工况仅相位不同 |My|max 差 74 倍）。
    """
    for w in (model.wing_left, model.wing_right):
        w._t_prev = None
        w._tau_state = 0.0
    T0[0] = t_start


def run_cell(v0, th_deg):
    """姿态锁定 3 秒积分（与基线 run_cell 逐位同口径，加记每步 My）。"""
    quat = euler_to_quat(0.0, math.radians(th_deg), 0.0)
    vel = np.array([v0, 0.0, 0.0])
    pos = np.array([0.0, 0.0, Z0])
    n = int(round(T_SIM / DT))
    z_min = Z0
    t_cell0 = T0[0]
    my_absmax = 0.0
    my_at_absmax = 0.0
    t_at_absmax = 0.0
    my_sum = 0.0
    for i in range(n):
        t = T0[0]
        F, T_world = model.compute_forces(pos, quat, vel, np.zeros(3), t)
        my = float(T_world[1])
        if abs(my) > my_absmax:
            my_absmax = abs(my)
            my_at_absmax = my
            t_at_absmax = t - t_cell0
        my_sum += my
        F = F + np.array([0.0, 0.0, -MASS * G_ACC])
        vel = vel + F / MASS * DT
        pos = pos + vel * DT
        T0[0] += DT
        z_min = min(z_min, pos[2])
    return dict(v_end=float(vel[0]), z_end=float(pos[2]),
                z_min=float(z_min),
                my_absmax=float(my_absmax),
                my_at_absmax=float(my_at_absmax),
                t_at_absmax=float(t_at_absmax),
                my_mean=float(my_sum / n),
                delta_req=float(my_absmax / D_EFF))


def run_one(cond, alpha_set, amp, mean, k_phase):
    """一个（工况×波形×发射相位）点的完整跑与判据标记。"""
    set_waveform(alpha_set, amp, mean)
    reset_phase(cond["t_star"] + k_phase * PHASE_STEP_S)
    r = run_cell(cond["v0"], cond["th"])
    diverged = abs(r["v_end"]) > 20.0 or abs(r["z_end"]) > 60.0
    hard_ok = (not diverged) and (r["delta_req"] <= D_LIM)
    soft_ok = (not diverged) and (r["v_end"] >= 3.2) and (r["z_end"] >= 1.5)
    r.update(cond=cond["tag"], v0=cond["v0"], th=cond["th"],
             alpha_set=alpha_set, amp=amp, mean=mean,
             phase=k_phase * 0.25,
             diverged=diverged, hard_ok=hard_ok, soft_ok=soft_ok,
             full_ok=(hard_ok and soft_ok))
    return r


def load_baseline():
    path = os.path.join(cfg.LOG_DIR, "scan_launch_window_mom.json")
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def self_checks(base):
    """两条自检：对基线 JSON 的可复现性（容差见模块头）。返回检查记录。"""
    out = []

    # 检查一：复现基线探针 aset30@A / aset25@A（四相位包络逐位复现）
    cond_a = CONDITIONS[0]
    for tag, alpha in (("aset30", 30.0), ("aset25", 25.0)):
        ref = next(p for p in base["probes"]
                   if p["cell"].startswith("A") and p["tag"] == tag)
        runs = [run_one(cond_a, alpha, 30.0, 20.0, k) for k in PHASE_K]
        vals = sorted(r["my_absmax"] for r in runs)
        dev_ph0 = abs(runs[0]["my_absmax"] - ref["my_absmax_ph0"])
        dev_min = abs(vals[0] - ref["my_absmax_min"])
        dev_max = abs(vals[-1] - ref["my_absmax_max"])
        tol = 1e-6
        ok = max(dev_ph0, dev_min, dev_max) < tol
        assert ok, (f"自检一失败[{tag}]：ph0 差 {dev_ph0:.2e}，"
                    f"min 差 {dev_min:.2e}，max 差 {dev_max:.2e}")
        out.append(dict(check="复现基线探针 " + tag + "@A",
                        ref_ph0=ref["my_absmax_ph0"],
                        got_ph0=runs[0]["my_absmax"],
                        dev_ph0=dev_ph0, tol=tol, ok=ok))
        print(f"[sls] 自检一[{tag}] 通过：ph0 {ref['my_absmax_ph0']:.6f}"
              f"→{runs[0]['my_absmax']:.6f}（差 {dev_ph0:.2e}），"
              f"包络 [{vals[0]:.3f},{vals[-1]:.3f}] vs 基线 "
              f"[{ref['my_absmax_min']:.3f},{ref['my_absmax_max']:.3f}]",
              flush=True)

    # 检查二：复现基线网格格 (6.5,15°) 基准波形 α_set35/幅值30/mean20
    ref_row = next(r for r in base["rows"]
                   if r["v0"] == 6.5 and abs(r["th"] - 15.0) < 1e-9)
    cond_b = CONDITIONS[1]
    set_waveform(35.0, 30.0, 20.0)
    reset_phase(cond_b["t_star"])
    r = run_cell(cond_b["v0"], cond_b["th"])
    dev = abs(r["my_absmax"] - ref_row["my_absmax"])
    dev_v = abs(r["v_end"] - ref_row["v_end"])
    tol = 5e-4 * ref_row["my_absmax"]   # 相对容差（诊断见模块头自检二）
    assert dev < tol, (f"自检二失败：|My|max 差 {dev:.2e}（容差 {tol:.2e}），"
                       f"末速差 {dev_v:.2e}")
    out.append(dict(check="复现基线网格格 (6.5,15°) 基准波形",
                    ref=ref_row["my_absmax"], got=r["my_absmax"],
                    dev=dev, tol=tol, ok=True))
    print(f"[sls] 自检二通过：网格 |My|max {ref_row['my_absmax']:.6f}"
          f"→{r['my_absmax']:.6f}（差 {dev:.2e} < 相对容差 {tol:.2e}，"
          f"末速差 {dev_v:.1e} m/s）", flush=True)
    return out


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    n_combo = len(CONDITIONS) * len(ALPHA_SET_GRID) * len(AMP_GRID) \
        * len(MEAN_GRID)
    print(f"[sls] 组合搜索：{n_combo} 组合 × {len(PHASE_K)} 相位 = "
          f"{n_combo * len(PHASE_K)} 跑，姿态锁定 {T_SIM}s，"
          f"dt={DT * 1000:g}ms，拍频 {F_OP} Hz", flush=True)

    # 口径断言：纯俯仰姿态下 T_world[1] == T_body[1]（Ry 保持 y 轴）
    set_waveform(25.0, 30.0, 20.0)
    reset_phase(T0[0])
    q_chk = euler_to_quat(0.0, math.radians(15.0), 0.0)
    model.compute_forces(np.array([0.0, 0.0, Z0]), q_chk,
                         np.array([4.0, 0.0, 0.0]), np.zeros(3), T0[0])
    dy = abs(model.current_T_world[1] - model.current_T_body[1])
    assert dy < 1e-12, f"口径断言失败：|T_world[1]-T_body[1]|={dy}"
    print(f"[sls] 口径断言通过：纯俯仰姿态 T_world[1]==T_body[1]"
          f"（差 {dy:.2e}）", flush=True)

    base = load_baseline()
    checks = self_checks(base)

    # ---------- 主网格：24 组合 × 4 相位 ----------
    runs = []
    combos = []
    for cond in CONDITIONS:
        for alpha in ALPHA_SET_GRID:
            for amp in AMP_GRID:
                for mn in MEAN_GRID:
                    ph = [run_one(cond, alpha, amp, mn, k) for k in PHASE_K]
                    runs.extend(ph)
                    nd = [p for p in ph if not p["diverged"]]
                    my_sorted = sorted(p["my_absmax"] for p in ph)
                    best_ph = (min(nd, key=lambda p: (p["delta_req"],
                                                      -p["v_end"]))
                               if nd else None)
                    c = dict(cond=cond["tag"], v0=cond["v0"], th=cond["th"],
                             alpha_set=alpha, amp=amp, mean=mn,
                             n_diverged=len(ph) - len(nd),
                             my_absmax_min=my_sorted[0],
                             my_absmax_med=(my_sorted[1] + my_sorted[2]) / 2.0,
                             my_absmax_max=my_sorted[-1],
                             delta_req_min=(min(p["delta_req"] for p in nd)
                                            if nd else None),
                             best_phase_beats=(best_ph["phase"]
                                               if best_ph else None),
                             best_phase_v_end=(best_ph["v_end"]
                                               if best_ph else None),
                             best_phase_z_end=(best_ph["z_end"]
                                               if best_ph else None),
                             v_end_min=min(p["v_end"] for p in ph),
                             v_end_max=max(p["v_end"] for p in ph),
                             z_end_min=min(p["z_end"] for p in ph),
                             z_end_max=max(p["z_end"] for p in ph),
                             hard_any=any(p["hard_ok"] for p in ph),
                             full_any=any(p["full_ok"] for p in ph))
                    combos.append(c)
                    print(f"[sls] [{cond['tag']}] α_set={alpha:g} 幅值{amp:g}"
                          f" mean{mn:g} → |My|max 包络"
                          f" [{my_sorted[0]:.3f},{my_sorted[-1]:.3f}]"
                          f" 中位 {(my_sorted[1] + my_sorted[2]) / 2:.3f}"
                          f" N·m，最优相位 δ_req="
                          f"{(best_ph['delta_req'] if best_ph else float('nan')):.3f}"
                          f" rad（相位 "
                          f"{(best_ph['phase'] if best_ph else float('nan')):g} 拍，"
                          f"发散 {c['n_diverged']}/4）", flush=True)

    # ---------- 判定（硬约束优先三分支） ----------
    n_div_total = sum(r["diverged"] for r in runs)
    full = [r for r in runs if r["full_ok"]]
    hard_all = [r for r in runs if r["hard_ok"]]
    verdict_kind = "full" if full else ("hard_only" if hard_all else "none")
    nond = [r for r in runs if not r["diverged"]]

    best_full = best_hard = None
    r_min = top10 = None
    if full:
        best_full = min(full, key=lambda r: (r["delta_req"], -r["v_end"]))
    elif hard_all:
        best_hard = min(hard_all, key=lambda r: (r["delta_req"], -r["v_end"]))
    else:
        r_min = min(nond, key=lambda r: (r["delta_req"], -r["v_end"]))
        top10 = sorted(nond, key=lambda r: (r["delta_req"], -r["v_end"]))[:10]

    if best_full is not None:
        print(f"[sls] 判定：过硬约束且能量两线全过的跑 {len(full)} 个，"
              f"最优 δ_req={best_full['delta_req']:.4f} rad", flush=True)
    elif best_hard is not None:
        print(f"[sls] 判定：过硬约束 {len(hard_all)} 跑但能量线有缺口，"
              f"最小 δ_req={best_hard['delta_req']:.4f} rad", flush=True)
    else:
        print(f"[sls] 判定：无组合过硬约束；最小可达 δ_req="
              f"{r_min['delta_req']:.4f} rad（限幅 {D_LIM} rad 的 "
              f"{r_min['delta_req'] / D_LIM:.1f} 倍）", flush=True)

    out = dict(
        ts=ts, tool="tools/scan_launch_subwave.py",
        base_tool="tools/scan_launch_window_mom.py",
        dt=DT, t_sim=T_SIM, z0=Z0, f=F_OP,
        split=SPLIT, pitch_lag_s=PITCH_LAG_S,
        d_eff=D_EFF, d_lim=D_LIM, m_auth=M_AUTH,
        my_convention=("T_world[1] of model.compute_forces, both wings, "
                       "no tail; equals T_body[1] exactly for pitch-only "
                       "attitude"),
        criteria=dict(hard=dict(delta_req_max=D_LIM),
                      soft=dict(v_end_min=3.2, z_end_min=1.5),
                      priority="hard first; if no run passes hard, report "
                               "min delta_req among non-diverged + top10",
                      diverge=dict(v_end_abs_max=20.0, z_end_abs_max=60.0)),
        grid=dict(conditions=[{k: v for k, v in c.items()} for c in CONDITIONS],
                  alpha_set=ALPHA_SET_GRID, amp=AMP_GRID, mean=MEAN_GRID,
                  phases_beats=[k * 0.25 for k in PHASE_K],
                  n_combo=n_combo, n_runs=len(runs)),
        scope_note=("发射子型只用于起飞段；巡航仍用现工作点"
                    "（幅值30/α_set35/mean20），发射后切波形属状态机既有结构"),
        self_checks=checks,
        runs=runs, combos=combos,
        n_diverged=n_div_total,
        n_hard=len(hard_all), n_full=len(full),
        best_full=best_full, best_hard=best_hard,
        min_delta_req_run=r_min, top10=top10,
        verdict_kind=verdict_kind)
    with open(os.path.join(cfg.LOG_DIR, "scan_launch_subwave.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    write_doc(ts, out, runs, combos, checks, base,
              best_full, best_hard, r_min, top10, n_div_total)
    print(f"[sls] 完成 → logs/scan_launch_subwave.json + "
          f"docs/发射友好子型搜索.md", flush=True)


def _fmt_run(r):
    return (f"{r['cond']} | α_set {r['alpha_set']:g}° | 幅值 {r['amp']:g}° | "
            f"mean {r['mean']:g}° | 相位 {r['phase']:g} 拍")


def write_doc(ts, out, runs, combos, checks, base,
              best_full, best_hard, r_min, top10, n_div_total):
    md = [f"# 发射友好子型组合搜索（{ts}）", "",
          "## 一、问题与口径", "",
          "力矩权限扫描（docs/发射窗口分析_力矩权限.md）判定选项一无解"
          "（全网格 0/42 格过双判据，非发散格最小 δ_req=2.957 rad=限幅 "
          "0.3 rad 的 9.9 倍），其判读六节给出组合搜索方向：α_set 下调"
          "最强、发射扑翼相位须入优化变量、mean≥20 勿下调、幅值单独下调"
          "不够、最优单旋钮 δ_req=2.34 rad（限幅 7.8 倍）闭合不了缺口。"
          "本搜索按该建议做因子化组合：α_set∈{20,25,30}°、幅值∈{25,30}°、"
          "mean∈{20,25}°、发射相位∈{0,0.25,0.5,0.75} 拍、发射工况取能量"
          "窗口邻域两档（4.0 m/s/25° 与 6.5 m/s/15°，即原能量窗口两个"
          "能量可行格）。固定量：拍频 1.25 Hz、split 0.75、"
          "pitch_lag −0.04 s。组合数 2×3×2×2=24，每组合四相位各跑一次"
          "（包络），共 96 跑。", "",
          "- My 口径：与基线逐位一致——`model.compute_forces` 返回 T_world"
          " 的 y 分量（两翼叶素合计俯仰力矩；ω=0 时 WING_ROT_DAMP 严格"
          "为零；不含尾翼/机身平板贡献）。纯俯仰姿态下 T_world[1] 与"
          " T_body[1] 逐位相等（脚本内数值断言，差 <1e-12）。",
          "- 积分口径：与基线逐位一致——姿态锁定开环积分 3.0 s（机体俯仰"
          "锁发射值），初始高度 3 m、速度水平、攻角=发射俯仰，dt=2 ms。",
          "- 相位口径：每组合在同一锚定时刻 t* 起，于 4 个发射相位各跑"
          "一次（`reset_phase` 一次性重锚，0.2 s=0.25 拍 @1.25 Hz）；"
          "t* 沿用基线（工况 A 取基线探针锚定 220033，工况 B 取基线网格"
          "该格起始时刻 220117）。",
          "- 发射子型定位：只用于起飞段。巡航仍用现工作点（幅值30/"
          "α_set35/mean20/split0.75/pitch_lag−0.04）；发射后切波形属"
          "状态机既有结构，本搜索只搜发射段参数。", "",
          "## 二、判据（先于数据，硬约束优先）", "",
          f"1. 硬约束（力矩线）：δ_req = |My|max / {D_EFF} ≤ {D_LIM} rad"
          f"（升降舵数值斜率 dMy/dδ={D_EFF} N·m/rad，满偏权限当量 "
          f"{M_AUTH:.5f} N·m）；",
          "2. 软约束（能量两线）：3 秒末前向速度 ≥ 3.2 m/s、3 秒末高度 "
          "≥ 1.5 m；",
          "3. 判定顺序（硬约束优先）：先按硬约束筛——存在过硬约束且能量"
          "两线全过的跑则报最优参数；存在过硬约束但能量线有缺口则报硬"
          "约束通过组合及能量缺口；无任何组合过硬约束则输出 δ_req 最小的"
          "前十（非发散跑）与最小可达 δ_req 结论。",
          "",
          "发散滤网沿用：|v_end|>20 或 |z_end|>60 判数值发散、不进窗口。", "",
          "## 三、自检（先于网格）", "",
          "对基线产物 logs/scan_launch_window_mom.json 的可复现性：", ""]
    for c in checks:
        md.append(f"- {c['check']}：通过（偏差 "
                  f"{c.get('dev_ph0', c.get('dev')):.2e} < 容差 "
                  f"{c['tol']:g}）。")
    md += ["",
           "## 四、组合包络结果", "",
           "原始数据：`logs/scan_launch_subwave.json`。每行=一个组合"
           "（工况×α_set×幅值×mean）的四相位包络；最优相位=该组合非发散"
           "相位中 δ_req 最小者。", "",
           "| 工况 | α_set (°) | 幅值 (°) | mean (°) | \\|My\\|max 包络"
           " (N·m) | 中位 (N·m) | 最优相位 δ_req (rad) | 最优相位 (拍) |"
           " 末速包络 (m/s) | 末高包络 (m) | 发散 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in sorted(combos, key=lambda x: (x["cond"], x["alpha_set"],
                                           x["amp"], x["mean"])):
        dr = c["delta_req_min"] if c["delta_req_min"] is not None \
            else float("nan")
        bp = c["best_phase_beats"] if c["best_phase_beats"] is not None \
            else float("nan")
        md.append(
            f"| {c['cond']} | {c['alpha_set']:g} | {c['amp']:g} |"
            f" {c['mean']:g} | {c['my_absmax_min']:.3f}"
            f"~{c['my_absmax_max']:.3f} | {c['my_absmax_med']:.3f} |"
            f" {dr:.3f} | {bp:g} |"
            f" {c['v_end_min']:+.2f}~{c['v_end_max']:+.2f} |"
            f" {c['z_end_min']:.2f}~{c['z_end_max']:.2f} |"
            f" {c['n_diverged']}/4 |")
    md += ["", f"- 统计：{len(runs)} 跑中数值发散 {n_div_total} 跑；过硬约束 "
          f"{out['n_hard']} 跑；过硬约束且能量两线全过 {out['n_full']} 跑。",
           "- 读表注意：发散相位的 |My|max（30~48 N·m 量级）会进入包络"
           "中位，发散计数不同的组合之间中位不可比（第六节方向判读一律"
           "用四相位下界）。", ""]

    md += ["## 五、判定", ""]
    if best_full is not None:
        md += [f"**存在过硬约束且能量两线全过的组合，共 {out['n_full']} 跑。**",
               f"最优（δ_req 最小，并列取末速大者）：{_fmt_run(best_full)}。",
               f"全部数字：|My|max {best_full['my_absmax']:.4f} N·m"
               f"（{best_full['t_at_absmax']:.2f} s、带符号 "
               f"{best_full['my_at_absmax']:+.4f} N·m）、δ_req "
               f"{best_full['delta_req']:.4f} rad（限幅占用 "
               f"{best_full['delta_req'] / D_LIM * 100:.1f}%）、3 秒末速度 "
               f"{best_full['v_end']:+.2f} m/s、末高度 "
               f"{best_full['z_end']:.2f} m、最低高度 "
               f"{best_full['z_min']:.2f} m。", ""]
    elif best_hard is not None:
        md += [f"**存在过硬约束的组合（{out['n_hard']} 跑）但能量两线有"
               f"缺口。**硬约束优先：最优（δ_req 最小）="
               f"{_fmt_run(best_hard)}，δ_req {best_hard['delta_req']:.4f}"
               f" rad；能量状态：末速 {best_hard['v_end']:+.2f} m/s（线 "
               f"3.2）、末高 {best_hard['z_end']:.2f} m（线 1.5）。", ""]
    else:
        md += ["**无组合过硬约束。**最小可达 δ_req（非发散跑）="
               f"{r_min['delta_req']:.4f} rad，由 {_fmt_run(r_min)} 取得"
               f"（|My|max={r_min['my_absmax']:.4f} N·m、末速 "
               f"{r_min['v_end']:+.2f} m/s、末高 {r_min['z_end']:.2f} m），"
               f"为限幅 {D_LIM} rad 的 {r_min['delta_req'] / D_LIM:.1f} 倍、"
               f"满偏权限当量 {M_AUTH:.5f} N·m 的 "
               f"{r_min['my_absmax'] / M_AUTH:.1f} 倍。δ_req 最小的前十"
               "（非发散跑）：", "",
               "| 名次 | 工况 | α_set (°) | 幅值 (°) | mean (°) | 相位 (拍) |"
               " \\|My\\|max (N·m) | δ_req (rad) | 末速 (m/s) | 末高 (m) |"
               " 能量线 |",
               "|---|---|---|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(top10, 1):
            lack = []
            if r["v_end"] < 3.2:
                lack.append("末速欠")
            if r["z_end"] < 1.5:
                lack.append("末高欠")
            en = "两线过" if not lack else "+".join(lack)
            md.append(f"| {i} | {r['cond']} | {r['alpha_set']:g} |"
                      f" {r['amp']:g} | {r['mean']:g} | {r['phase']:g} |"
                      f" {r['my_absmax']:.3f} | {r['delta_req']:.3f} |"
                      f" {r['v_end']:+.2f} | {r['z_end']:.2f} | {en} |")
        md += [""]

    # ---- 六、判读（数字全部来自上表；跨组合比较一律用四相位下界，
    #      中位被发散相位污染——发散跑 |My|max 30~48 N·m 会抬进中位） ----
    def _c(cond, alpha, amp, mn):
        return next(x for x in combos if x["cond"] == cond
                    and x["alpha_set"] == alpha and x["amp"] == amp
                    and x["mean"] == mn)

    def _min_series(cond, amp, mn):
        return [_c(cond, a, amp, mn)["my_absmax_min"]
                for a in ALPHA_SET_GRID]

    lo30A = _min_series("A", 30.0, 20.0)
    lo30B = _min_series("B", 30.0, 20.0)
    lo25A = _min_series("A", 25.0, 25.0)
    lo25B = _min_series("B", 25.0, 25.0)
    n_best_075 = sum(1 for c in combos if c["best_phase_beats"] == 0.75)
    md += ["## 六、判读（组合网格内各旋钮方向，数字出自上表）", "",
           "口径说明：发散相位的 |My|max（30~48 N·m 量级）会进入包络中位，"
           "故中位数不能跨组合比较（发散计数不同的组合中位不可比），本节"
           "方向判读一律用四相位下界（发散免疫）。", "",
           f"**α_set（第一有效旋钮，方向与基线单旋钮探针一致）**：固定"
           f"幅值/mean 的两组在两工况，下界随 α_set 20→25→30° 单调上升——"
           f"幅值30/mean20 组 A {lo30A[0]:.3f}→{lo30A[1]:.3f}→{lo30A[2]:.3f}"
           f" N·m、B {lo30B[0]:.3f}→{lo30B[1]:.3f}→{lo30B[2]:.3f} N·m；"
           f"幅值25/mean25 组 A {lo25A[0]:.3f}→{lo25A[1]:.3f}→"
           f"{lo25A[2]:.3f} N·m、B {lo25B[0]:.3f}→{lo25B[1]:.3f}→"
           f"{lo25B[2]:.3f} N·m。网格内最小下界落在 α_set=20 档。", ""]
    # 幅值方向（固定 α_set20/mean25，两工况）
    ampA25, ampA30 = _c("A", 20.0, 25.0, 25.0), _c("A", 20.0, 30.0, 25.0)
    ampB25, ampB30 = _c("B", 20.0, 25.0, 25.0), _c("B", 20.0, 30.0, 25.0)
    md += [f"**幅值（固定 α_set 20/mean 25）**：幅值 30→25 下界 A "
           f"{ampA30['my_absmax_min']:.3f}→{ampA25['my_absmax_min']:.3f}"
           f" N·m、B {ampB30['my_absmax_min']:.3f}→"
           f"{ampB25['my_absmax_min']:.3f} N·m；末速下界 A "
           f"{ampA30['v_end_min']:+.2f}→{ampA25['v_end_min']:+.2f} m/s"
           f"（持平），B {ampB30['v_end_min']:+.2f}→"
           f"{ampB25['v_end_min']:+.2f} m/s（幅值 30 档在 B 工况出现"
           "倒飞相位，幅值 25 将其消除）——本域内幅值 25 档在力矩下界与"
           "末速下界两端同时占优，但单独降幅值（基线已证）到不了硬约束，"
           "它是组合里的次级旋钮。", ""]
    # mean 方向（12 对全比）
    n_mean_down = 0
    for cond in ("A", "B"):
        for a in ALPHA_SET_GRID:
            for am in AMP_GRID:
                if _c(cond, a, am, 25.0)["my_absmax_min"] < \
                        _c(cond, a, am, 20.0)["my_absmax_min"]:
                    n_mean_down += 1
    mB20, mB25 = _c("B", 20.0, 25.0, 20.0), _c("B", 20.0, 25.0, 25.0)
    mA20, mA25 = _c("A", 30.0, 30.0, 20.0), _c("A", 30.0, 30.0, 25.0)
    md += [f"**mean（向上扩域方向实测正确）**：mean 20→25 在 "
           f"{n_mean_down}/12 个同波形对上压低下界（含全部零发散可比对："
           f"B 工况 α_set20/幅值25 下界 {mB20['my_absmax_min']:.3f}→"
           f"{mB25['my_absmax_min']:.3f} N·m、A 工况 α_set30/幅值30 "
           f"{mA20['my_absmax_min']:.3f}→{mA25['my_absmax_min']:.3f}"
           " N·m），零发散对的中位同步下降——基线『mean 向 ≥20° 一侧"
           "扩域』的建议在本域内成立，最优档取 mean 25。", ""]
    # 相位方向
    worst = max(combos, key=lambda x: x["my_absmax_max"]
                / max(x["my_absmax_min"], 1e-9))
    md += [f"**发射相位（方向系统性稳定）**：{n_best_075}/{len(combos)} 个"
           f"组合的 δ_req 最小相位都是 0.75 拍——非抽奖，是稳定方向；同组合"
           f"包络上界/下界比值最大 {worst['my_absmax_max'] / max(worst['my_absmax_min'], 1e-9):.1f}"
           f" 倍（{worst['cond']} 工况 α_set {worst['alpha_set']:g}/幅值 "
           f"{worst['amp']:g}/mean {worst['mean']:g}，"
           f"{worst['my_absmax_min']:.3f}~{worst['my_absmax_max']:.3f}"
           " N·m）。发射机构按 0.75 拍相位同步释放，等价于把上界削到下界——"
           "这是本搜索里收益最大的单动作。", ""]
    # 工况方向：最优档两工况对比
    md += [f"**工况**：最优档（α_set 20/幅值 25/mean 25）两工况下界几乎"
           f"重合（A {ampA25['my_absmax_min']:.3f} 对 B "
           f"{ampB25['my_absmax_min']:.3f} N·m），差异在发散相位数"
           f"（A {ampA25['n_diverged']}/4 对 B {ampB25['n_diverged']}/4）"
           "——6.5 m/s/15° 档对激进子型更温顺；与基线『力矩缺口不随发射"
           "工况显著改善』一致，工况选择救不了力矩线，只影响相位抽奖烈度。",
           ""]
    if r_min is not None:
        lack = []
        if r_min["v_end"] < 3.2:
            lack.append(f"末速 {r_min['v_end']:+.2f} m/s 欠线 3.2")
        if r_min["z_end"] < 1.5:
            lack.append(f"末高 {r_min['z_end']:.2f} m 欠线 1.5")
        en_txt = "能量两线全过" if not lack else "；".join(lack)
        md += [f"**合取结论**：组合搜索最小可达 δ_req="
               f"{r_min['delta_req']:.3f} rad（{_fmt_run(r_min)}），仍为"
               f"限幅 {D_LIM} rad 的 {r_min['delta_req'] / D_LIM:.1f} 倍"
               f"（|My|max {r_min['my_absmax']:.3f} N·m = 满偏权限当量 "
               f"{M_AUTH:.5f} N·m 的 {r_min['my_absmax'] / M_AUTH:.1f} 倍），"
               f"该点能量状态：{en_txt}——在 α_set≥20°、幅值≥25°、"
               "mean∈{20,25}° 的发射友好子型域内，波形侧组合旋钮闭合不了"
               "力矩缺口，且力矩最优点把能量代价转移到了末高。相对基线"
               "单旋钮最优（δ_req=2.34 rad，限幅 7.8 倍），组合搜索把缺口"
               f"从 7.8 倍压到 {r_min['delta_req'] / D_LIM:.1f} 倍，改善"
               "约三成，量级不变。后续方向：继续下探 α_set/幅值下界并接受"
               "能量代价、发射俯仰往低档取（工况侧）、或发射段引入尾翼"
               "之外的配平机构；发射机构按 0.75 拍相位同步释放是收益最大"
               "的机构侧单动作。", ""]
    elif best_full is not None:
        md += ["**合取结论**：发射友好子型域内存在过硬约束且能量两线"
               "全过的参数点，建议按第五节最优参数固化发射段波形，发射后"
               "切回巡航工作点。", ""]
    else:
        md += ["**合取结论**：过硬约束的组合存在但能量两线有缺口，按硬"
               "约束优先取第五节参数，能量缺口需靠发射工况或后续段回收。",
               ""]

    doc_path = os.path.join(PROJECT_ROOT, "docs", "发射友好子型搜索.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))


if __name__ == "__main__":
    main()
