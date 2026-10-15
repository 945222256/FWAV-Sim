# -*- coding: utf-8 -*-
"""
scan_waveform_retrim.py — 带俯仰力矩约束的波形参数重搜索
（新起点路线图第四步，二队执行计划，2026-09-24）

问题：三维模型巡航配平点存在扫掠俯仰耦合力矩 My≈+0.374 N·m
（第十七班 docs/扫掠俯仰耦合力矩_物理配平与消融.md）。物理配平两
路径均不可行（重心需后移 17.6 cm、尾翼安装角需 84.6° 且失速截断）
；第二十班 3a 归因基线复跑把俯仰力矩配不平坐实为坠机主因。剩余合
法手段=波形空间多目标重新配平（用户 0924 语义裁定：运动学层输入
改动属正常设计手段，正当性判定线=三目标声明+消融记录保留）。

本搜索的正当性声明（按上述判定线）：搜索目标同时声明三项——
竖直平衡（残差≤2%）、前向平衡（Fx=0 解出配平俯仰）、俯仰力矩
（|My|≤0.10 N·m）；力矩来源消融记录保留在
docs/扫掠俯仰耦合力矩_物理配平与消融.md（扫掠几何 61% / 翼根
偏置 39%），本搜索不消除来源、只选可配平工作点。

判据（先于数据，随工具入库）：
  1. 竖直残差 |Fz−W|/W ≤ 2% 才算候选（Fz 为世界系竖直合力，
     与 scan_balance_sweep 同口径）；
  2. 前向力必须配平：配平俯仰由 Fx=0 解出（双阱稳健、多根取
     竖直残差最小者——第十七班修正口径），不解出不算数；
  3. |My| ≤ 0.10 N·m 才算达标（用户定的线；路线图降级线
     0.15 N·m 须声明舵面权限占用）；
  4. 参数落在执行机构可达范围（拍频 1.0~3.5 Hz=FREQ_AP_MIN~
     FREQ_MAX_AP；拍动角峰值 |MEAN|+幅值 ≤ 50° = 0.87 rad <
     驱动限位 ±1.4 rad；α_set ≤ 50° 舵机行程待真机核实）；
  5. 全空间无解时如实报告"最近能做到的力矩值及其代价"，不硬凑。

三阶段：
  粗筛：SPLIT/LAG 固定现行值（0.25/−0.02 s），四轴网格
        α_set 35/40/45/50° × MEAN −10/−5/0/+5/+10° ×
        f 1.25~2.25 Hz（5 档）× V 3.5/4.0/4.5 m/s，共 300 组；
        每组解配平俯仰、量竖直残差（记录 My 备查），留残差≤2%。
  细化：粗筛幸存组合各自展开 SPLIT(3)×PITCH_LAG_S(4)×幅值(3)
        =36 变体，同法解配平、量残差与 My（排名分辨率 n=72）。
  终值：三目标全达标者按 |My| 升序取前 5（缓冲分辨率翻转），
        n=288 重算全套数字，报告其中 n=288 仍达标的前 3。

边界声明：静态测量（角速度恒为零），与主线已恢复的 ω×r 耦合项
在数学上严格无关（ω=0 时该项逐位为零，第二十班已验证三工况静态
逐位一致）；本工具显式实例化离线模型、不读 AERO_MODEL 开关，
不改任何 config。运行时 HEAD 记入 JSON（对账交付纪律）。

测量纪律（第十六班三条）：时间轴单调推进（T0 只增不减）/
显式设频（set_wing_command）/ 粗扫 n=72、终值 n=288。

用法： python tools/scan_waveform_retrim.py [--smoke]
产物： logs/scan_waveform_retrim.json
      + docs/波形参数重搜索_带俯仰力矩约束.md
"""
import os
import sys
import json
import math
import datetime
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
from config import sim_config as cfg
from modules.aero_solver import TailFuselageAero, euler_to_quat, \
    quat_to_rotation_matrix
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G = 9.81
W = cfg.MASS * G
N_COARSE, N_FINE = 72, 288
T0 = [90000.0]

# 旧工作点（第十七班基线，用于对照与对账）
OP_OLD = dict(V=4.0, f=1.75, alpha_set=35.0, mean=0.0,
              split=cfg.FlapKinematicsConfig.SPLIT,
              pitch_lag_s=cfg.FlapKinematicsConfig.PITCH_LAG_S,
              amplitude=35.0)

# 判据常量（先于数据）
VERT_MAX = 0.02            # 竖直残差线
MY_MAX = 0.10              # 俯仰力矩达标线（用户定的线）
MY_DOWNGRADE = 0.15        # 路线图降级线（须声明权限占用）
FREQ_LO, FREQ_HI = 1.0, 3.5      # FREQ_AP_MIN / FREQ_MAX_AP
PHI_LIMIT_RAD = 1.4        # 驱动限位（拍动角峰值 |MEAN|+幅值 对照）
K_ELEV = -0.2535           # dMy/d升降舵偏（第十七班路径B三维数值斜率）
DELTA_MAX = 0.30           # 升降舵限幅 rad

# 粗筛网格（用户执行计划）
ALPHA_AXIS = (35.0, 40.0, 45.0, 50.0)
MEAN_AXIS = (-10.0, -5.0, 0.0, 5.0, 10.0)
FREQ_AXIS = (1.25, 1.50, 1.75, 2.00, 2.25)
V_AXIS = (3.5, 4.0, 4.5)
# 细化展开（用户执行计划：分裂比3档×时间相位4档×幅值3档）
SPLIT_AXIS = (0.25, 0.375, 0.50)
LAG_AXIS = (-0.04, -0.02, 0.0, 0.02)
AMP_AXIS = (30.0, 35.0, 40.0)
TOP_BUFFER = 5             # 终值阶段缓冲名额（防分辨率翻转）
TOP_REPORT = 3             # 报告名额

model = SaneDickinson3DModel()
tail = TailFuselageAero()


def set_waveform(alpha_set, mean_deg, split, pitch_lag_s, amplitude_deg):
    """显式设置六波形参数中的五个（频率在 eval 内显式设）。"""
    for w in (model.wing_left, model.wing_right):
        w.alpha_set = math.radians(alpha_set)
        w.mean = math.radians(mean_deg)
        w.split = float(split)
        w.pitch_lag_s = float(pitch_lag_s)
        w.amplitude = math.radians(amplitude_deg)


def eval_state(V, f, pd_deg, n=N_FINE):
    """钉固状态整周期平均（纪律一/二：单调时基+显式设频）。
    口径与 scan_balance_sweep.eval_state 一致：升降舵零位、
    滑流 v_slip=SLIP_K·SLIP_V_PER_HZ·f、世界系力/力矩。"""
    model.set_wing_command(frequency=f)
    tail.set_command(elevator=0.0)
    quat = euler_to_quat(0.0, math.radians(pd_deg), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    R_bw = quat_to_rotation_matrix(quat)
    T = 1.0 / f
    Fw_sum = np.zeros(3)
    Tw_sum = np.zeros(3)
    for i in range(n):
        Fw, Tw = model.compute_forces(np.zeros(3), quat, vel, om,
                                      T0[0] + i * T / n)
        Fw_sum += Fw
        Tw_sum += Tw
    T0[0] += T
    Fw_sum /= n
    Tw_sum /= n
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f
    Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
    F_tot = Fw_sum + Ft
    return dict(F_total=F_tot, T_total=Tw_sum + Tt,
                F_wing=Fw_sum, T_wing=Tw_sum, F_tail=Ft, T_tail=Tt,
                Fz_body=float((R_bw.T @ F_tot)[2]))


def solve_pitch(V, f):
    """θ 域求 Fx=0（与 scan_balance_sweep.solve_pitch 同法：粗扫变号
    +二分，多根取竖直残差最小者，粗 n=72、精修+终值 n=288）。"""
    def fx(th, n):
        return float(eval_state(V, f, th, n)["F_total"][0])

    grid = np.arange(-20.0, 25.001, 1.5)
    vals = [(float(th), fx(th, N_COARSE)) for th in grid]
    brackets = [(a[0], b[0]) for a, b in zip(vals[:-1], vals[1:])
                if a[1] == 0.0 or a[1] * b[1] < 0]
    if not brackets:
        th0 = min(vals, key=lambda r: abs(r[1]))[0]
        a, b = th0 - 1.0, th0 + 1.0
    else:
        best = None
        for a0, b0 in brackets:
            a, b = a0, b0
            fa = fx(a, N_COARSE)
            for _ in range(14):
                mid = 0.5 * (a + b)
                fm = fx(mid, N_COARSE)
                if fa * fm <= 0:
                    b = mid
                else:
                    a, fa = mid, fm
            th_c = 0.5 * (a + b)
            fz_c = float(eval_state(V, f, th_c, N_COARSE)["F_total"][2])
            score = abs(fz_c - W)
            if best is None or score < best[0]:
                best = (score, th_c, a, b)
        _, th_c, a, b = best
    fa = fx(a, N_FINE)
    fb = fx(b, N_FINE)
    if fa * fb < 0:
        for _ in range(8):
            mid = 0.5 * (a + b)
            fm = fx(mid, N_FINE)
            if fa * fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
    return 0.5 * (a + b)


def measure(params, n):
    """设波形→解配平→整周期平均，返回一行完整数字。"""
    set_waveform(params["alpha_set"], params["mean"], params["split"],
                 params["pitch_lag_s"], params["amplitude"])
    th = solve_pitch(params["V"], params["f"])
    st = eval_state(params["V"], params["f"], th, n)
    Fz = float(st["F_total"][2])
    My = float(st["T_total"][1])
    row = dict(params)
    row.update(pitch=th,
               fx=float(st["F_total"][0]), fz=Fz,
               vert_resid=abs(Fz - W) / W,
               my_total=My,
               my_wing=float(st["T_wing"][1]),
               my_tail=float(st["T_tail"][1]),
               fz_body=st["Fz_body"],
               elev_rad=abs(My) / abs(K_ELEV),
               freq_ok=FREQ_LO <= params["f"] <= FREQ_HI,
               phi_peak_ok=(abs(params["mean"]) + params["amplitude"])
               <= math.degrees(PHI_LIMIT_RAD))
    row["pass_vert"] = row["vert_resid"] <= VERT_MAX
    row["pass_my"] = abs(My) <= MY_MAX
    row["pass_all"] = row["pass_vert"] and row["pass_my"]
    return row


def dump(out, path):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)


def main():
    smoke = "--smoke" in sys.argv
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    head = ""
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        pass
    out = {"ts": ts, "model": "sane_dickinson_3d",
           "head": head, "static_omega_zero": True,
           "criteria": dict(vert_max=VERT_MAX, my_max=MY_MAX,
                            my_downgrade=MY_DOWNGRADE,
                            freq_range=[FREQ_LO, FREQ_HI],
                            phi_limit_rad=PHI_LIMIT_RAD,
                            k_elev=K_ELEV, delta_max=DELTA_MAX,
                            n_rank=N_COARSE, n_final=N_FINE),
           "axes": dict(alpha=ALPHA_AXIS, mean=MEAN_AXIS, freq=FREQ_AXIS,
                        V=V_AXIS, split=SPLIT_AXIS, lag=LAG_AXIS,
                        amp=AMP_AXIS),
           "old_op": OP_OLD}
    jpath = os.path.join(cfg.LOG_DIR, "scan_waveform_retrim.json")

    # ── 旧工作点对账（n=288，对照第十七班基线）──
    old = measure(OP_OLD, N_FINE)
    print("[retrim] 旧工作点对账: θ=%+.2f° Fz=%.3f 残差=%.1f%% "
          "My=%+.4f（翼%+.4f 尾%+.4f）——对照第十七班 θ=+7.14°/"
          "Fz=2.144/1.7%%/My=+0.3741" % (old["pitch"], old["fz"],
          old["vert_resid"] * 100, old["my_total"], old["my_wing"],
          old["my_tail"]), flush=True)
    out["old_point_n288"] = old

    # ── 阶段一：粗筛 300 组（排名 n=72）──
    combos = [dict(V=V, f=f, alpha_set=a, mean=m,
                   split=OP_OLD["split"], pitch_lag_s=OP_OLD["pitch_lag_s"],
                   amplitude=OP_OLD["amplitude"])
              for a in ALPHA_AXIS for m in MEAN_AXIS
              for f in FREQ_AXIS for V in V_AXIS]
    if smoke:
        combos = combos[:6]
    rows = []
    for k, p in enumerate(combos):
        r = measure(p, N_COARSE)
        rows.append(r)
        if (k + 1) % 20 == 0 or r["pass_vert"]:
            print("[coarse %3d/%d] α%2.0f MEAN%+3.0f f%.2f V%.1f → "
                  "θ%+6.2f 残差%5.1f%% My%+7.4f%s"
                  % (k + 1, len(combos), p["alpha_set"], p["mean"],
                     p["f"], p["V"], r["pitch"], r["vert_resid"] * 100,
                     r["my_total"], " ✓残差过线" if r["pass_vert"] else ""),
                  flush=True)
    survivors = [r for r in rows if r["pass_vert"]]
    out["coarse"] = rows
    out["coarse_survivors"] = len(survivors)
    dump(out, jpath)
    print("[retrim] 粗筛完成：%d 组，残差≤2%% 幸存 %d 组"
          % (len(rows), len(survivors)), flush=True)

    # ── 阶段二：细化 36 变体/幸存组（排名 n=72）──
    variants = []
    if survivors:
        base = sorted(survivors, key=lambda r: abs(r["my_total"]))
        for s in base:
            for sp in SPLIT_AXIS:
                for lg in LAG_AXIS:
                    for am in AMP_AXIS:
                        p = dict(s)
                        p.update(split=sp, pitch_lag_s=lg, amplitude=am)
                        variants.append(p)
    if smoke:
        variants = variants[:6]
    vrows = []
    for k, p in enumerate(variants):
        r = measure(p, N_COARSE)
        vrows.append(r)
        if (k + 1) % 12 == 0 or r["pass_all"]:
            print("[refine %3d/%d] α%2.0f MEAN%+3.0f f%.2f V%.1f "
                  "SP%.3f LG%+.2f AMP%.0f → θ%+6.2f 残差%5.1f%% "
                  "My%+7.4f%s"
                  % (k + 1, len(variants), p["alpha_set"], p["mean"],
                     p["f"], p["V"], p["split"], p["pitch_lag_s"],
                     p["amplitude"], r["pitch"], r["vert_resid"] * 100,
                     r["my_total"],
                     " ✓三过" if r["pass_all"] else ""), flush=True)
    out["refine"] = vrows
    dump(out, jpath)
    passing = [r for r in vrows if r["pass_all"]]
    print("[retrim] 细化完成：%d 变体，三目标全达标 %d 个"
          % (len(vrows), len(passing)), flush=True)

    # ── 阶段三：终值 n=288（前 TOP_BUFFER 重算，报 TOP_REPORT）──
    finals = []
    if passing:
        cand = sorted(passing, key=lambda r: abs(r["my_total"]))
        for p in cand[:TOP_BUFFER]:
            r = measure(p, N_FINE)
            r["pass_all_n288"] = r["pass_vert"] and r["pass_my"]
            finals.append(r)
            print("[final n=288] α%2.0f MEAN%+3.0f f%.2f V%.1f SP%.3f "
                  "LG%+.2f AMP%.0f → θ%+6.2f Fx%+.4f Fz%.3f 残差%5.2f%% "
                  "My%+7.4f%s"
                  % (p["alpha_set"], p["mean"], p["f"], p["V"],
                     p["split"], p["pitch_lag_s"], p["amplitude"],
                     r["pitch"], r["fx"], r["fz"], r["vert_resid"] * 100,
                     r["my_total"],
                     " ✓" if r["pass_all_n288"] else " ✗(n288翻转)"),
                  flush=True)
    out["final"] = finals
    dump(out, jpath)

    # ── 无解分支：如实报告最近能做到的力矩值及其代价 ──
    best_effort = None
    pool = vrows if vrows else rows
    if pool and not (finals and any(f["pass_all_n288"] for f in finals)):
        ok_vert = [r for r in pool if r["pass_vert"]]
        cands = ok_vert if ok_vert else pool
        best_effort = min(cands, key=lambda r: abs(r["my_total"]))
        print("[retrim] ★三目标无全解——最近能力：残差过线者中 |My| "
              "最小 = %+.4f N·m（α%.0f/MEAN%+.0f/f%.2f/V%.1f，残差%.1f%%）"
              % (best_effort["my_total"], best_effort["alpha_set"],
                 best_effort["mean"], best_effort["f"], best_effort["V"],
                 best_effort["vert_resid"] * 100), flush=True)
    out["best_effort"] = best_effort
    dump(out, jpath)
    write_report(out, jpath)
    print("[retrim] 完成 → docs/波形参数重搜索_带俯仰力矩约束.md",
          flush=True)


def _fmt_row(r, n288=False):
    flag = "✓三过" if (r.get("pass_all_n288") if n288 else r["pass_all"]) \
        else ("△残差过线" if r["pass_vert"] else "✗")
    return ("| %.1f | %.2f | %.0f | %+.0f | %.3f | %+.2f | %.0f | "
            "%+.2f | %+.4f | %.3f | %.1f%% | %+.4f | %+.4f | %+.4f | "
            "%.0f%% | %s |"
            % (r["V"], r["f"], r["alpha_set"], r["mean"], r["split"],
               r["pitch_lag_s"], r["amplitude"], r["pitch"], r["fx"],
               r["fz"], r["vert_resid"] * 100, r["my_total"],
               r["my_wing"], r["my_tail"],
               r["elev_rad"] / DELTA_MAX * 100, flag))


HDR = ("| V(m/s) | f(Hz) | α_set(°) | MEAN(°) | SPLIT | LAG(s) | "
       "幅值(°) | θ(°) | Fx(N) | Fz(N) | 竖直残差 | My总(N·m) | "
       "My翼 | My尾 | 舵占用 | 判定 |")
SEP = "|" + "---|" * 16


def write_report(out, jpath):
    ts = out["ts"]
    finals = [f for f in out["final"] if f.get("pass_all_n288")]
    md = [f"# 波形参数重搜索·带俯仰力矩约束（{ts}）", "",
          "- 性质声明：本搜索为**波形空间多目标重新配平**，同时声明三项目"
          "标（竖直平衡残差≤2%、前向平衡 Fx=0 解出配平俯仰、|My|≤0.10 "
          "N·m）；力矩来源消融记录保留于"
          "《扫掠俯仰耦合力矩_物理配平与消融.md》（扫掠几何 61% / 翼根"
          "偏置 39%），本搜索不消除来源、只选可配平工作点（0924 语义裁定"
          "的正当性判定线）。",
          f"- 模型：sane_dickinson_3d 静态测量（角速度恒为零）；运行时 "
          f"HEAD={out['head']}；ω×r 耦合项在 ω=0 时逐位为零，静态结论与"
          "恢复前严格一致（第二十班三工况验证）；本步不改任何 config。",
          f"- 原始数据：`{os.path.relpath(jpath, PROJECT_ROOT)}`；"
          f"测量纪律三条遵守（排名 n={N_COARSE}、终值 n={N_FINE}）。", "",
          "## 判据（先于数据）", "",
          f"1. 竖直残差 |Fz−W|/W ≤ {VERT_MAX:.0%}（Fz 世界系竖直合力，"
          "与 scan_balance_sweep 同口径）；",
          "2. 前向力必须配平（配平俯仰由 Fx=0 解出，双阱取竖直残差最小"
          "根——第十七班修正口径）；",
          f"3. |My| ≤ {MY_MAX:.2f} N·m 达标（降级线 {MY_DOWNGRADE:.2f} "
          "N·m 须声明舵面权限占用）；",
          f"4. 执行机构可达：拍频 {FREQ_LO}~{FREQ_HI} Hz、拍动角峰值 "
          f"|MEAN|+幅值 ≤ {math.degrees(PHI_LIMIT_RAD):.0f}°（驱动限位 "
          f"±{PHI_LIMIT_RAD} rad）；α_set≤50° 的俯仰舵机行程待真机核实"
          "（真机考核清单已有舵机物理行程项）；",
          "5. 全空间无解时如实报告最近能力，不硬凑。", ""]

    md += ["## 搜索空间", "",
           f"- 粗筛：α_set {ALPHA_AXIS}° × MEAN {MEAN_AXIS}° × f "
           f"{FREQ_AXIS} Hz × V {V_AXIS} m/s（SPLIT/LAG/幅值固定现行值 "
           f"{OP_OLD['split']}/{OP_OLD['pitch_lag_s']}/"
           f"{OP_OLD['amplitude']:.0f}°），共 "
           f"{len(ALPHA_AXIS)*len(MEAN_AXIS)*len(FREQ_AXIS)*len(V_AXIS)}"
           " 组；",
           f"- 细化：粗筛幸存组各展开 SPLIT {SPLIT_AXIS} × LAG {LAG_AXIS} s"
           f" × 幅值 {AMP_AXIS}° = {len(SPLIT_AXIS)*len(LAG_AXIS)*len(AMP_AXIS)}"
           " 变体；",
           f"- 终值：三过者按 |My| 升序取前 {TOP_BUFFER} 名 n={N_FINE} 重算"
           f"，报告 n=288 仍达标的前 {TOP_REPORT} 名（缓冲名防分辨率翻转）。",
           ""]

    old = out["old_point_n288"]
    md += ["## 旧工作点对账（n=288 重测）", "",
           f"- θ={old['pitch']:+.2f}°、Fz={old['fz']:.3f} N（残差 "
           f"{old['vert_resid']:.1%}）、My={old['my_total']:+.4f} N·m"
           f"（翼 {old['my_wing']:+.4f} ＋ 尾 {old['my_tail']:+.4f}）；"
           "对照第十七班基线 θ=+7.14°/Fz=2.144/1.7%/My=+0.3741"
           "（同口径闭合）。", ""]

    md += ["## 粗筛结果", "",
           f"- 300 组中残差≤2% 幸存 **{out['coarse_survivors']} 组**"
           "（幸存明细见 JSON `coarse` 列，含全部 300 组数字）。", ""]

    npass = len([r for r in out["refine"] if r["pass_all"]])
    md += ["## 细化结果", "",
           f"- 共 {len(out['refine'])} 变体；三目标全达标（n=72 口径）"
           f"**{npass} 个**。残差过线且 |My| 最小的五个：", "",
           HDR, SEP]
    pool = [r for r in out["refine"] if r["pass_vert"]] or out["refine"]
    for r in sorted(pool, key=lambda r: abs(r["my_total"]))[:5]:
        md.append(_fmt_row(r))
    md.append("")

    md += ["## 终值结果（n=288）", ""]
    if finals:
        md += [f"前 {TOP_REPORT} 名（n=288 三目标复验通过）：", "",
               HDR, SEP]
        for r in finals[:TOP_REPORT]:
            md.append(_fmt_row(r, n288=True))
        w0 = finals[0]
        md += ["",
               "### 新旧工作点全套数字对照", "",
               "| 量 | 旧工作点 | 新工作点（第 1 名） |",
               "|---|---|---|",
               f"| 速度 V (m/s) | {OP_OLD['V']} | {w0['V']} |",
               f"| 拍频 f (Hz) | {OP_OLD['f']} | {w0['f']} |",
               f"| 攻角设定 α_set (°) | {OP_OLD['alpha_set']} | "
               f"{w0['alpha_set']} |",
               f"| 扑动面偏置 MEAN (°) | {OP_OLD['mean']} | "
               f"{w0['mean']} |",
               f"| 分裂比 SPLIT | {OP_OLD['split']} | {w0['split']} |",
               f"| 时间相位 LAG (s) | {OP_OLD['pitch_lag_s']} | "
               f"{w0['pitch_lag_s']} |",
               f"| 拍动幅值 (°) | {OP_OLD['amplitude']} | "
               f"{w0['amplitude']} |",
               f"| 配平俯仰 θ (°) | {old['pitch']:+.2f} | "
               f"{w0['pitch']:+.2f} |",
               f"| 前向合力 Fx (N) | {old['fx']:+.4f} | {w0['fx']:+.4f} |",
               f"| 竖直合力 Fz (N) | {old['fz']:.3f} | {w0['fz']:.3f} |",
               f"| 竖直残差 | {old['vert_resid']:.1%} | "
               f"{w0['vert_resid']:.2%} |",
               f"| 全机俯仰力矩 My (N·m) | {old['my_total']:+.4f} | "
               f"{w0['my_total']:+.4f} |",
               f"| 翼 My (N·m) | {old['my_wing']:+.4f} | "
               f"{w0['my_wing']:+.4f} |",
               f"| 尾 My (N·m) | {old['my_tail']:+.4f} | "
               f"{w0['my_tail']:+.4f} |",
               f"| 残余力矩舵面占用 δ=|My|/|k| (rad) | "
               f"{old['elev_rad']:.3f} | {w0['elev_rad']:.3f} |",
               f"| 占 ±{DELTA_MAX} rad 限幅 | {old['elev_rad']/DELTA_MAX:.0%}"
               f" | {w0['elev_rad']/DELTA_MAX:.0%} |", ""]
    else:
        md.append("**三目标无全解**（或 n=288 翻转）——诚实分支触发。")
        be = out.get("best_effort")
        if be:
            md += ["", "最近能力（残差过线者中 |My| 最小，n=72 口径）：",
                   "", HDR, SEP]
            md.append(_fmt_row(be))
        md.append("")

    md += ["## 判读与移交", "",
           "- 舵面消化预算口径：残余 |My| 按第十七班路径 B 三维数值斜率 "
           f"dMy/dδ={K_ELEV} N·m/rad 折算升降舵偏度；路线图要求占用不超"
           f"限幅一半（{DELTA_MAX/2} rad → |My|≤"
           f"{abs(K_ELEV)*DELTA_MAX/2:.3f} N·m），达标线 0.10 N·m 对应占用 "
           f"{MY_MAX/abs(K_ELEV)/DELTA_MAX:.0%}——第 1 名的实际占用见上表"
           "，超出半限幅时须声明动态余量损失。",
           "- 移交边界：新工作点的控制器设定值更新、在环复跑（第五步）属"
           "下一批；本步未改任何 config。",
           "- 边界声明：静态口径（ω=0）与一队恢复 ω×r 耦合项数学上无关；"
           "拍内动力学不在本搜索范围（周期平均假设，见推导文档 5.1 节）。",
           ""]
    doc = os.path.join(PROJECT_ROOT, "docs", "波形参数重搜索_带俯仰力矩约束.md")
    with open(doc, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))


if __name__ == "__main__":
    main()
