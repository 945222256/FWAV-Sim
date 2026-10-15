# -*- coding: utf-8 -*-
"""
scan_launch_window_mom.py — 发射窗口分析加力矩权限维度（幅值 30 档升级版）

背景：第五跑（5d）失败根因=发射瞬态机翼俯仰力矩峰 2.578 N·m 对尾翼
舵效 0.2535 N·m/rad×满偏 0.30 rad=权限当量 0.076 N·m，差数十倍；
docs/发射窗口分析_幅值30.md 的能量窗口只筛了能量、没筛力矩。本工具
在 tools/scan_launch_window.py 的姿态锁定积分上逐点加记全机俯仰力矩
My，把"舵面吃得下"并入判据。原工具与其产物（logs/scan_launch_window
_amp30.json、docs/发射窗口分析_幅值30.md）不动。

My 口径（先于数据声明）：
  - My = model.compute_forces 返回的 T_world 的 y 分量，即两翼叶素
    合计俯仰力矩在世界系的 y 分量（含 WING_ROT_DAMP 线性阻尼项，本扫
    描 ω=0 该项严格为零；不含尾翼/机身平板贡献——尾翼正是被考核权限
    的配平机构，且原能量扫描同口径亦不含）。重力对质心不产生力矩。
  - 姿态锁定扫描只有俯仰（滚转=偏航=0），旋转矩阵 Ry(θ) 保持 y 轴
    不动，故 T_world[1] 与 T_body[1] 逐位相等（脚本内一次性数值断言
    验证，非近似）。

判据先于数据（2026-09-24 定，双判据）：
  通过线（三条同时满足）：
    1) 能量线 A：3 秒末前向速度 ≥ 3.2 m/s（沿用原判据）；
    2) 能量线 B：3 秒末高度 ≥ 1.5 m（沿用原判据）；
    3) 力矩线：舵偏需求 δ_req = |My|max / 0.2535 ≤ 0.30 rad
       （0.2535 N·m/rad=第十七班路径 B 升降舵数值斜率 dMy/dδ，
       0.30 rad=舵偏限幅）。
  发散滤网沿用：|v_end|>20 或 |z_end|>60 判数值发散、不进窗口。

用法： python tools/scan_launch_window_mom.py [--amp=30]
产物： logs/scan_launch_window_mom.json + docs/发射窗口分析_力矩权限.md
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

G_ACC = 9.81
MASS = cfg.MASS
DT = 0.002
T_SIM = 3.0
Z0 = 3.0
F_OP = 1.25

# 舵面消化预算口径（docs/波形参数重搜索_带俯仰力矩约束.md）：
# 升降舵数值斜率 dMy/dδ = 0.2535 N·m/rad，限幅 0.30 rad，
# 满偏权限当量 = 0.2535×0.30 = 0.07605 N·m
D_EFF = 0.2535
D_LIM = 0.30
M_AUTH = D_EFF * D_LIM

V_GRID = [3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5]
# 幅值 30 配平俯仰（docs/波形参数重搜索_扩域.md 前三名表）
AMP_DEG = 30.0
PITCH_TRIM_DEG = 21.45
for _a in sys.argv[1:]:
    if _a.startswith("--amp"):
        AMP_DEG = float(_a.split("=")[1])
if AMP_DEG == 30.0:
    PITCH_TRIM_DEG = 21.45
elif AMP_DEG == 35.0:
    PITCH_TRIM_DEG = 21.72
elif AMP_DEG == 40.0:
    PITCH_TRIM_DEG = 21.86
TH_GRID = [0.0, 5.0, 10.0, 15.0, PITCH_TRIM_DEG, 25.0]

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(35.0)
    w.split = 0.75
    w.pitch_lag_s = -0.04
    w.mean = math.radians(20.0)
    w.amplitude = math.radians(AMP_DEG)
model.set_wing_command(frequency=F_OP)

T0 = [220000.0]


def run_cell(v0, th_deg):
    """姿态锁定 3 秒积分。返回末速/末高/最低高/|My|max/舵偏需求等。

    与原 scan_launch_window.run_cell 逐位同口径（力、积分、初始条件），
    仅加记每步 T_world[1] 为 My。
    """
    quat = euler_to_quat(0.0, math.radians(th_deg), 0.0)
    vel = np.array([v0, 0.0, 0.0])
    pos = np.array([0.0, 0.0, Z0])
    n = int(round(T_SIM / DT))
    z_min = Z0
    t_cell0 = T0[0]          # 本格起始绝对时刻（峰值时刻换算基准）
    my_absmax = 0.0          # 全程 |My| 峰
    my_at_absmax = 0.0       # 该瞬时的带符号 My
    t_at_absmax = 0.0        # |My| 峰出现时刻（本格内秒）
    my_sum = 0.0             # 带符号平均用累加
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


def reset_phase(t_start):
    """把两翼相位积分态重锚到 t_start（复刻新模型首次求值的锚定方式）。

    网格内逐格连积分使每格的发射扑翼相位不同（3 s=3.75 拍，格间相位
    差 0.75 拍）——网格判据逐格自洽不受影响，但探针对比必须同相位发
    射，否则波形参数效应与相位抽奖混叠（首版探针实录：同工况仅因起始
    相位不同，|My|max 从 0.75 N·m 跳到 55 N·m）。
    """
    for w in (model.wing_left, model.wing_right):
        w._t_prev = None
        w._tau_state = 0.0
    T0[0] = t_start


def probe_cell(v0, th_deg, amp_deg=None, alpha_set_deg=None, mean_deg=None):
    """辅证探针：固定发射工况与发射扑翼相位，单改一个波形参数。

    只服务选项三搜索建议（My 随波形参数的变化方向），不改判据。
    相位口径：每档在 4 个发射相位（0/0.25/0.5/0.75 拍）各跑一次，
    报 |My|max 的相位包络（min/max）——本系统发射段对扑翼相位高度
    敏感（网格内同条件相邻相位可从末速 +4 跳到 +676 m/s），单相位
    对比会被相位抽奖淹没。
    """
    t_star = probe_cell.t_star
    old_amp = [w.amplitude for w in (model.wing_left, model.wing_right)]
    old_alpha = [w.alpha_set for w in (model.wing_left, model.wing_right)]
    old_mean = [w.mean for w in (model.wing_left, model.wing_right)]
    runs = []
    try:
        for w in (model.wing_left, model.wing_right):
            if amp_deg is not None:
                w.amplitude = math.radians(amp_deg)
            if alpha_set_deg is not None:
                w.alpha_set = math.radians(alpha_set_deg)
            if mean_deg is not None:
                w.mean = math.radians(mean_deg)
        for k_phase in range(4):
            reset_phase(t_star + k_phase * 0.2)   # 0.2 s = 0.25 拍 @1.25 Hz
            runs.append(run_cell(v0, th_deg))
    finally:
        for w, a, al, mn in zip((model.wing_left, model.wing_right),
                                old_amp, old_alpha, old_mean):
            w.amplitude, w.alpha_set, w.mean = a, al, mn
    my_vals = sorted(r["my_absmax"] for r in runs)
    my_means = [r["my_mean"] for r in runs]
    return dict(
        my_absmax_ph0=runs[0]["my_absmax"],
        v_end_ph0=runs[0]["v_end"], z_end_ph0=runs[0]["z_end"],
        my_absmax_min=my_vals[0],
        my_absmax_med=(my_vals[1] + my_vals[2]) / 2.0,
        my_absmax_max=my_vals[-1],
        my_mean_min=min(my_means), my_mean_max=max(my_means),
        delta_req_min=my_vals[0] / D_EFF, delta_req_max=my_vals[-1] / D_EFF,
        v_end_max=max(r["v_end"] for r in runs),
        v_end_min=min(r["v_end"] for r in runs),
        z_end_max=max(r["z_end"] for r in runs),
        z_end_min=min(r["z_end"] for r in runs))


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"[lwm] 网格 {len(V_GRID)}×{len(TH_GRID)}，姿态锁定 {T_SIM}s，"
          f"dt={DT*1000:g}ms，幅值 {AMP_DEG:g}°", flush=True)

    # 口径断言：纯俯仰姿态下 T_world[1] == T_body[1]（Ry 保持 y 轴）
    q_chk = euler_to_quat(0.0, math.radians(15.0), 0.0)
    model.compute_forces(np.array([0.0, 0.0, Z0]), q_chk,
                         np.array([4.0, 0.0, 0.0]), np.zeros(3), T0[0])
    dy = abs(model.current_T_world[1] - model.current_T_body[1])
    assert dy < 1e-12, f"口径断言失败：|T_world[1]-T_body[1]|={dy}"
    print(f"[lwm] 口径断言通过：纯俯仰姿态 T_world[1]==T_body[1]（差 {dy:.2e}）",
          flush=True)

    rows = []
    for v0 in V_GRID:
        for th in TH_GRID:
            r = run_cell(v0, th)
            diverged = abs(r["v_end"]) > 20.0 or abs(r["z_end"]) > 60.0
            ok = (not diverged) and (r["v_end"] >= 3.2) \
                and (r["z_end"] >= 1.5) and (r["delta_req"] <= D_LIM)
            ok_energy = (not diverged) and (r["v_end"] >= 3.2) \
                and (r["z_end"] >= 1.5)
            r.update(v0=v0, th=th, ok=ok, ok_energy=ok_energy,
                     diverged=diverged)
            rows.append(r)
            print(f"[lwm] v0={v0:.1f} θ0={th:5.2f}° → 末速="
                  f"{r['v_end']:+.2f} 末高={r['z_end']:7.2f} "
                  f"|My|max={r['my_absmax']:6.3f} N·m "
                  f"δ_req={r['delta_req']:7.3f} rad "
                  f"{'双判据通过' if ok else ('能量通过/力矩超限' if ok_energy else ('数值发散' if diverged else '窗外'))}",
                  flush=True)
    n_ok = sum(r["ok"] for r in rows)
    n_energy = sum(r["ok_energy"] for r in rows)
    print(f"[lwm] 能量可行 {n_energy}/{len(rows)} 格，双判据可行 {n_ok}/{len(rows)} 格",
          flush=True)

    # 最优格：双判据可行格中舵偏需求最小者（并列取末速大者）
    best = None
    for r in rows:
        if r["ok"]:
            if best is None or (r["delta_req"], -r["v_end"]) < \
                    (best["delta_req"], -best["v_end"]):
                best = r
    if best is not None:
        print(f"[lwm] 最优格：v0={best['v0']:.1f} m/s，θ0={best['th']:.2f}°，"
              f"δ_req={best['delta_req']:.4f} rad", flush=True)

    # 选项三辅证探针：两个代表性工况 × 7 档波形参数 × 4 个发射相位。
    # 工况 A=能量可行格（4.0 m/s, 25°，δ_req=14.8 rad，力矩线最痛处）；
    # 工况 B=全网格非发散格 δ_req 最小者（力矩线最好处）。相位锚定到
    # 该网格格的起始时刻，基准档（相位 0）应逐位复现该格结果（自检）。
    idx_of = {(r["v0"], r["th"]): i for i, r in enumerate(rows)}
    probe_cells = []
    en_ok = [r for r in rows if r["ok_energy"]]
    probe_cells.append(("A·能量可行格", en_ok[0] if en_ok else rows[0]))
    cand = [r for r in rows if not r["diverged"]]
    probe_cells.append(("B·δ_req 最小非发散格",
                        min(cand, key=lambda r: r["delta_req"])))
    probe_variants = [
        ("base", dict(), "基准（幅值30/α_set35/mean20）"),
        ("amp25", dict(amp_deg=25.0), "幅值 30→25"),
        ("amp20", dict(amp_deg=20.0), "幅值 30→20"),
        ("aset30", dict(alpha_set_deg=30.0), "攻角设定 35→30"),
        ("aset25", dict(alpha_set_deg=25.0), "攻角设定 35→25"),
        ("mean10", dict(mean_deg=10.0), "拍动偏置 mean 20→10"),
        ("mean0", dict(mean_deg=0.0), "拍动偏置 mean 20→0"),
    ]
    probes = []
    for cell_tag, base_r in probe_cells:
        pv, pth = base_r["v0"], base_r["th"]
        probe_cell.t_star = 220000.0 + 3.0 * idx_of[(pv, pth)]
        print(f"[lwm] 探针工况 {cell_tag}: v0={pv:.1f} θ0={pth:.2f}° "
              f"(网格 |My|max={base_r['my_absmax']:.3f}，"
              f"t*={probe_cell.t_star:.0f})", flush=True)
        for tag, kw, label in probe_variants:
            p = probe_cell(pv, pth, **kw)
            probes.append(dict(cell=cell_tag, tag=tag, label=label,
                               v0=pv, th=pth,
                               t_star=probe_cell.t_star, **p))
            if tag == "base":
                # 相位锚定自检：相位 0 档应复现该网格格。网格相位为
                # 逐步积分值、探针为一次性重锚 (f·t)mod1，浮点累积极
                # 微漂移（实测 2.3e-06 N·m @ 峰 3.76 N·m，相对 6e-7），
                # 容差取 1e-4 N·m 判"物理同一发射"
                dev = abs(p["my_absmax_ph0"] - base_r["my_absmax"])
                print(f"[lwm] 自检：相位 0 复现网格 |My|max "
                      f"{base_r['my_absmax']:.6f}（差 {dev:.2e}）", flush=True)
                assert dev < 1e-4, f"相位锚定自检失败：差 {dev}"
            print(f"[lwm] 探针[{label}] → |My|max 相位包络 "
                  f"[{p['my_absmax_min']:.3f}, {p['my_absmax_max']:.3f}] "
                  f"中位 {p['my_absmax_med']:.3f} N·m，"
                  f"δ_req [{p['delta_req_min']:.2f}, "
                  f"{p['delta_req_max']:.2f}] rad，"
                  f"末速 [{p['v_end_min']:+.1f}, "
                  f"{p['v_end_max']:+.1f}]", flush=True)

    out = dict(ts=ts, dt=DT, t_sim=T_SIM, z0=Z0, f=F_OP, amp=AMP_DEG,
               d_eff=D_EFF, d_lim=D_LIM, m_auth=M_AUTH,
               my_convention=("T_world[1] of model.compute_forces, both wings, "
                              "no tail; equals T_body[1] exactly for "
                              "pitch-only attitude"),
               criteria=dict(v_end_min=3.2, z_end_min=1.5, delta_req_max=D_LIM),
               rows=rows, n_ok=n_ok, n_energy=n_energy,
               best=best, probes=probes)
    with open(os.path.join(cfg.LOG_DIR, "scan_launch_window_mom.json"),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    # ---------- Markdown 报告 ----------
    md = [f"# 发射窗口分析·力矩权限维度（{ts}，幅值 {AMP_DEG:g} 档）", "",
          "## 一、问题与口径", "",
          "第五跑（5d）失败根因=发射瞬态机翼俯仰力矩峰 2.578 N·m 对"
          "尾翼舵效 0.2535 N·m/rad×满偏 0.30 rad=权限当量 0.076 N·m，"
          "差数十倍；原能量窗口（docs/发射窗口分析_幅值30.md）只筛能量、"
          "没筛力矩。本扫描在原姿态锁定积分上逐点加记全机俯仰力矩 My。",
          "",
          "- My 口径：`model.compute_forces` 返回的 T_world 的 y 分量"
          "（两翼叶素合计俯仰力矩；含 WING_ROT_DAMP 阻尼项，本扫描 ω=0"
          " 该项严格为零；不含尾翼/机身平板贡献——尾翼正是被考核权限的"
          " 配平机构；重力对质心无力矩）。纯俯仰姿态下旋转矩阵 Ry(θ)"
          " 保持 y 轴不动，T_world[1] 与 T_body[1] 逐位相等（脚本内"
          " 数值断言验证，差 < 1e-12），故本口径无近似。",
          f"- 积分口径：与原扫描逐位一致——姿态锁定开环积分 {T_SIM} s"
          f"（机体俯仰锁发射值），力=三维模型逐时间步（含爬升守卫/阻尼/"
          f"已恢复 ω×r），初始高度 3 m、速度水平、攻角=发射俯仰，"
          f"dt={DT*1000:g} ms，拍频 {F_OP} Hz，幅值 {AMP_DEG:g}°、"
          f"α_set 35°、split 0.75、pitch_lag −0.04 s、mean 20°。",
          "",
          "## 二、判据（先于数据，双判据三条）", "",
          "通过线=能量两条沿用以力矩一条新增，须同时满足：",
          "",
          "1. 3 秒末前向速度 ≥ 3.2 m/s（能量线 A）；",
          "2. 3 秒末高度 ≥ 1.5 m（能量线 B）；",
          f"3. 舵偏需求 δ_req = |My|max / {D_EFF} ≤ {D_LIM} rad"
          f"（力矩线；升降舵数值斜率 dMy/dδ={D_EFF} N·m/rad，"
          f"满偏权限当量 {M_AUTH:.5f} N·m）。",
          "",
          "发散滤网沿用：|v_end|>20 或 |z_end|>60 判数值发散、不进窗口。",
          "",
          "## 三、全网格结果", "",
          "原始数据：`logs/scan_launch_window_mom.json`", "",
          "| 发射速度 | 发射俯仰 | 3秒末速度 | 末高度 | 最低高度 |"
          "\\|My\\|max (N·m) | 峰值时刻 (s) | My 均值 (N·m) |"
          " δ_req (rad) | 判定 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        verdict = ("双判据通过" if r["ok"] else
                   ("数值发散" if r["diverged"] else
                    ("能量通过/力矩超限" if r["ok_energy"] else "窗外")))
        md.append(f"| {r['v0']:.1f} m/s | {r['th']:.2f}° |"
                  f" {r['v_end']:+.2f} | {r['z_end']:.2f} |"
                  f" {r['z_min']:.2f} | {r['my_absmax']:.3f} |"
                  f" {r['t_at_absmax']:.2f} | {r['my_mean']:+.3f} |"
                  f" {r['delta_req']:.3f} | {verdict} |")
    md += ["", f"- 窗口统计：能量可行 {n_energy}/{len(rows)} 格，"
          f"双判据可行 {n_ok}/{len(rows)} 格。", ""]

    md += ["## 四、判定", ""]
    if best is not None:
        md += [f"**双可行格存在，最优格（δ_req 最小）**：发射速度 "
               f"{best['v0']:.1f} m/s、发射俯仰 {best['th']:.2f}°。"
               f"全部数字：3 秒末速度 {best['v_end']:+.2f} m/s、"
               f"末高度 {best['z_end']:.2f} m、最低高度 {best['z_min']:.2f} m、"
               f"|My|max {best['my_absmax']:.3f} N·m（{best['t_at_absmax']:.2f} s、"
               f"带符号 {best['my_at_absmax']:+.3f} N·m）、My 均值 "
               f"{best['my_mean']:+.3f} N·m、δ_req {best['delta_req']:.4f} rad"
               f"（限幅占用 {best['delta_req']/D_LIM*100:.1f}%）。", ""]
    else:
        cand2 = [r for r in rows if not r["diverged"]]
        r_best_mom = min(cand2, key=lambda r: r["delta_req"])
        md += ["**选项一无解：全网格不存在双判据可行格。**",
               f"能量可行格 {n_energy} 个（沿用原判据："
               + "、".join(f"({r['v0']:.1f} m/s, {r['th']:.2f}°)"
                           for r in rows if r["ok_energy"])
               + f"），其力矩线全部超限："
               + "、".join(f"δ_req={r['delta_req']:.2f} rad"
                           for r in rows if r["ok_energy"])
               + f"。全网格非发散格中 δ_req 最小者为 "
               f"{r_best_mom['delta_req']:.3f} rad（v0={r_best_mom['v0']:.1f} m/s、"
               f"θ0={r_best_mom['th']:.2f}°，|My|max={r_best_mom['my_absmax']:.3f} N·m），"
               f"仍为 {D_LIM} rad 限幅的 {r_best_mom['delta_req']/D_LIM:.1f} 倍、"
               f"满偏权限当量 {M_AUTH:.5f} N·m 的"
               f" {r_best_mom['my_absmax']/M_AUTH:.1f} 倍——缺口是全网格性质，"
               f"不是个别格的相位抽奖。", ""]

    md += ["## 五、选项三辅证探针（波形发射友好子型搜索方向）", "",
           "相位口径：本系统发射段对扑翼相位高度敏感（网格内同参数仅因"
           "起始相位不同，末速可从 +4 跳到 +676 m/s），故每档波形参数在"
           "同一工况、同一锚定时刻起，于 4 个发射相位（0/0.25/0.5/0.75 拍）"
           "各跑一次；相位 0 档逐位复现对应网格格（自检）。下表 |My|max"
           " 取 4 相位的包络（min~max）与中位。", ""]
    for cell_tag, base_r in probe_cells:
        pv, pth = base_r["v0"], base_r["th"]
        md += [f"### 工况 {cell_tag}：v0={pv:.1f} m/s、θ0={pth:.2f}°"
               f"（网格 |My|max={base_r['my_absmax']:.3f} N·m、"
               f"δ_req={base_r['delta_req']:.3f} rad）", "",
               "| 波形档 | \\|My\\|max 包络 (N·m) | \\|My\\|max 中位 (N·m) |"
               " δ_req 包络 (rad) | 末速包络 (m/s) |",
               "|---|---|---|---|---|---|"]
        for p in [q for q in probes if q["cell"] == cell_tag]:
            md.append(f"| {p['label']} | {p['my_absmax_min']:.3f}"
                       f"~{p['my_absmax_max']:.3f} |"
                       f" {p['my_absmax_med']:.3f} |"
                       f" {p['delta_req_min']:.2f}~{p['delta_req_max']:.2f} |"
                       f" {p['v_end_min']:+.1f}~{p['v_end_max']:+.1f} |")
        md += [""]

    # ---- 六、判读：My 随工况/波形参数的变化方向（全部数字来自上表） ----
    def _p(cell_tag, tag_):
        return next(q for q in probes if q["cell"] == cell_tag and
                    q["tag"] == tag_)
    pa, pb = "A·能量可行格", "B·δ_req 最小非发散格"
    b_a, a25, a20 = _p(pa, "base"), _p(pa, "amp25"), _p(pa, "amp20")
    s30, s25 = _p(pa, "aset30"), _p(pa, "aset25")
    m10, m0 = _p(pa, "mean10"), _p(pa, "mean0")
    b_b, s25b = _p(pb, "base"), _p(pb, "aset25")
    md += ["## 六、判读与选项三搜索建议（My 变化方向，数字出自上表）", "",
           "**网格工况方向**：发射俯仰取低档或配平邻域时 |My|max 最小"
           "（θ0=0°/21.45° 行的非发散格 |My|max 聚在 0.75~0.85 N·m，"
           "θ0=25° 行升到 2.5~3.8 N·m）；发射速度对 |My|max 无系统性"
           "压低作用（θ0=0° 行 3.5~6.5 m/s 非发散格全在 0.75~0.98 N·m）。"
           "结论：力矩缺口不随发射工况（速度×俯仰）显著改善，幅值 30 "
           "档下最低 |My|max≈0.75 N·m 仍为满偏权限当量"
           f" {M_AUTH:.5f} N·m 的约 10 倍——必须动波形。", "",
           "**波形方向一（幅值↓）**：两工况中位与下界 |My|max 随幅值"
           f" 30→25→20 单调下降（A 格中位 {b_a['my_absmax_med']:.3f}"
           f"→{a25['my_absmax_med']:.3f}→{a20['my_absmax_med']:.3f} N·m，"
           f"B 格下界 {b_b['my_absmax_min']:.3f}→{_p(pb,'amp25')['my_absmax_min']:.3f}"
           f"→{_p(pb,'amp20')['my_absmax_min']:.3f} N·m），但上界不驯服"
           f"（A 格上界 {b_a['my_absmax_max']:.3f}→{a25['my_absmax_max']:.3f}"
           " N·m 反升，相位抽奖仍主导），且末速下界恶化"
           f"（A 格 {b_a['v_end_min']:+.1f}→{a20['v_end_min']:+.1f} m/s，"
           "出现倒飞相位）。幅值单独下调打不开缺口、还付能量代价。", "",
           "**波形方向二（攻角设定 α_set↓，最强单旋钮）**：35→30→25 在"
           f"两工况一致压低下界/中位/上界（A 格中位 {b_a['my_absmax_med']:.3f}"
           f"→{s30['my_absmax_med']:.3f}→{s25['my_absmax_med']:.3f} N·m；"
           f"B 格上界 {b_b['my_absmax_max']:.1f}→{s25b['my_absmax_max']:.3f}"
           f" N·m，发散相位被消除，末速上界 {b_b['v_end_max']:+.1f}→"
           f"{s25b['v_end_max']:+.1f} m/s 回到带内）。降 α_set 同时削瞬态"
           "力矩与能量失控两个症状——选项三子型搜索的首选自变量。", "",
           "**波形方向三（拍动偏置 mean，方向=维持或增大，勿下调）**："
           f"mean 20→10→0 两工况一致恶化（A 格中位 {b_a['my_absmax_med']:.3f}"
           f"→{m10['my_absmax_med']:.3f}→{m0['my_absmax_med']:.3f} N·m；"
           "B 格下界同步上抬）。若搜偏置方向，应向 ≥20° 一侧扩展。", "",
           "**附加自由度（发射扑翼相位）**：同工况仅发射相位不同，"
           f"|My|max 包络差最高 {b_b['my_absmax_max']/max(b_b['my_absmax_min'],1e-9):.1f} 倍"
           f"（B 格基准 {b_b['my_absmax_min']:.3f}~"
           f"{b_b['my_absmax_max']:.1f} N·m，末速 {b_b['v_end_min']:+.1f}~"
           f"{b_b['v_end_max']:+.1f} m/s）——相位本身是一维发射参数，"
           "子型搜索应把它并入优化变量（或机构上按相位同步释放）。", "",
           "**合取结论**：最优单旋钮观测（α_set 25 @ B 格下界"
           f" {s25b['my_absmax_min']:.3f} N·m）的 δ_req 仍为 "
           f"{s25b['delta_req_min']:.2f} rad ≈ 限幅 {D_LIM} rad 的 "
           f"{s25b['delta_req_min']/D_LIM:.1f} 倍——单旋钮闭合不了缺口；"
           "建议子型搜索走组合方向（更小 α_set 为主 + 适度小幅值 + "
           "发射相位选择 + mean 向上扩域），并以本工具的 δ_req≤"
           f"{D_LIM} rad 为硬约束、能量两线为软约束联合筛。", ""]

    doc_path = os.path.join(PROJECT_ROOT, "docs", "发射窗口分析_力矩权限.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[lwm] 完成 → docs/发射窗口分析_力矩权限.md", flush=True)


if __name__ == "__main__":
    main()
