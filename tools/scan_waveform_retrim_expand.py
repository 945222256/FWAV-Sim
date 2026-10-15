# -*- coding: utf-8 -*-
"""
scan_waveform_retrim_expand.py — 波形参数重搜索·扩域轮
（新起点第四步收尾，用户 0924 扩域裁决，2026-09-24）

裁决内容：偏置扩到 +20°、速度加 3.0 m/s 档、分裂比扩到 0.75；
新增执行机构可实现性判据；有解交新工作点全套数字，无解交
0.15 N·m 降级线裁决材料；若新工作点落在俯仰 15~25° 区（攻角
饱和区邻域）按第十九班结论预告阻尼矩阵复测需求（供第五步复跑
前的口径声明）。

与原域轮（scan_waveform_retrim.py）的关系：同一张考卷扩大考场
再考一次——求值内核（eval_state/solve_pitch/set_waveform）直接
复用原工具，判断墙外面有没有及格答案。负偏置侧按原域前沿证据
裁掉（原域最好负侧点 0.2103 对正侧 0.1800，边界推力在正侧）。

轴（扩域）：
  粗筛：α_set 35/40/45/50° × MEAN 0/+5/+10/+12.5/+15/+20° ×
        f 1.25~2.25 Hz（5 档）× V 3.0/3.5/4.0/4.5 m/s，
        共 480 组（SPLIT/LAG/幅值固定现行值）。
  细化：粗筛残差过线者按 |My| 取前 10 名，各展开
        SPLIT 0.375/0.5/0.625/0.75 × LAG −0.04/−0.02 s ×
        幅值 30/35/40° = 24 变体。
        （LAG 0/+0.02 裁掉：原域前八名全在 −0.04/−0.02，
         0/+0.02 从未进前沿；SPLIT 0.25 裁掉：被 0.375+ 支配。）
  终值：0.10 达标线三过者按 |My| 前 5 名 n=288 重算报前 3；
        若 0.10 无解而 0.15 降级线（残差+可实现性过线）有候选，
        同样 n=288 重算前 3 名作降级线裁决材料。

判据（先于数据）：
  1. 竖直残差 |Fz−W|/W ≤ 2%；
  2. 前向力必须配平（Fx=0 解出配平俯仰，双阱取竖直残差最小根）；
  3. |My| ≤ 0.10 N·m 达标；0.15 N·m 为降级线（须声明权限占用）；
  4. ★执行机构可实现性（本轮新增，静态可算部分）：
     a. 拍动角峰值 |MEAN|+幅值 ≤ 80.2°（驱动限位 ±1.4 rad，
        第十一班 A1d 顶死实测口径）；
     b. 拍频 ∈ [1.0, 3.5] Hz（FREQ_AP_MIN/FREQ_MAX_AP）；
     c. 分裂比 ∈ [0.05, 0.95]（WingKinematics 运动学钳位域）；
     d. α_set ≤ 50° 且俯仰舵机行程未核实——标记待真机；
     e. 边界声明：动态跟踪能力（舵机带宽、驱动 K/D 的摆幅比
        衰减）静态扫描无法验证，列真机考核清单；MEAN +20°
        （0.349 rad）为大幅结构安装偏置，铰链止动与连杆几何
        不在本模型内，列真机考核清单。
  5. 全域无解时如实报告最近能力，不硬凑。

测量纪律三条与静态口径（ω=0）边界声明同原域轮。

用法： python tools/scan_waveform_retrim_expand.py [--smoke]
产物： logs/scan_waveform_retrim_expand.json
      + docs/波形参数重搜索_扩域.md
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
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))

import numpy as np  # noqa: F401
from config import sim_config as cfg
import scan_waveform_retrim as base

W = base.W
N_COARSE, N_FINE = base.N_COARSE, base.N_FINE

# 扩域轴（用户裁决）
ALPHA_AXIS = (35.0, 40.0, 45.0, 50.0)
MEAN_AXIS = (0.0, 5.0, 10.0, 12.5, 15.0, 20.0)
FREQ_AXIS = (1.25, 1.50, 1.75, 2.00, 2.25)
V_AXIS = (3.0, 3.5, 4.0, 4.5)
SPLIT_AXIS = (0.375, 0.5, 0.625, 0.75)
LAG_AXIS = (-0.04, -0.02)
AMP_AXIS = (30.0, 35.0, 40.0)
SPLIT_CLAMP = (0.05, 0.95)
ALPHA_FLAG_MAX = 50.0
REFINE_TOPK = 10
TOP_BUFFER, TOP_REPORT = 5, 3
MY_MAX, MY_DOWNGRADE = base.MY_MAX, base.MY_DOWNGRADE
VERT_MAX = base.VERT_MAX
K_ELEV, DELTA_MAX = base.K_ELEV, base.DELTA_MAX
# 阻尼复测预告触发区（用户裁决：俯仰 15~25° 攻角饱和区邻域）
THETA_REMEASURE = (15.0, 25.0)


def measure_full(params, n):
    """扩域版量测：原域 measure 全部字段 + 可实现性三闸 + α 标记。"""
    base.set_waveform(params["alpha_set"], params["mean"], params["split"],
                      params["pitch_lag_s"], params["amplitude"])
    th = base.solve_pitch(params["V"], params["f"])
    st = base.eval_state(params["V"], params["f"], th, n)
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
               freq_ok=base.FREQ_LO <= params["f"] <= base.FREQ_HI,
               phi_peak_ok=(abs(params["mean"]) + params["amplitude"])
               <= math.degrees(base.PHI_LIMIT_RAD),
               split_ok=SPLIT_CLAMP[0] <= params["split"] <= SPLIT_CLAMP[1],
               alpha_set_flagged=params["alpha_set"] > ALPHA_FLAG_MAX)
    row["pass_real"] = (row["freq_ok"] and row["phi_peak_ok"]
                        and row["split_ok"] and not row["alpha_set_flagged"])
    row["pass_vert"] = row["vert_resid"] <= VERT_MAX
    row["pass_my"] = abs(My) <= MY_MAX
    row["pass_my_down"] = abs(My) <= MY_DOWNGRADE
    row["pass_all"] = (row["pass_vert"] and row["pass_my"]
                       and row["pass_real"])
    row["pass_down_all"] = (row["pass_vert"] and row["pass_my_down"]
                            and row["pass_real"])
    return row


def dump(out, path):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)


def fmt_row(r, key="pass_all"):
    if r.get(key):
        flag = ("✓全过" if key in ("pass_all", "pass_all_n288")
                else "✓降级线")
    elif r.get("pass_all") or r.get("pass_all_n288"):
        flag = "✓全过"
    elif r.get("pass_down_all") or r.get("pass_down_n288"):
        flag = "△降级线"
    elif r["pass_vert"]:
        flag = "△残差过线"
    else:
        flag = "✗"
    if not r["pass_real"]:
        flag += "✗可实现"
    return ("| %.1f | %.2f | %.0f | %+.1f | %.3f | %+.2f | %.0f | "
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
    out = {"ts": ts, "mode": "expand", "model": "sane_dickinson_3d",
           "head": head, "static_omega_zero": True,
           "ruling": "偏置+20°/V3.0档/SPLIT0.75/可实现性判据新增",
           "criteria": dict(vert_max=VERT_MAX, my_max=MY_MAX,
                            my_downgrade=MY_DOWNGRADE,
                            split_clamp=list(SPLIT_CLAMP),
                            alpha_flag_max=ALPHA_FLAG_MAX,
                            phi_limit_rad=base.PHI_LIMIT_RAD,
                            freq_range=[base.FREQ_LO, base.FREQ_HI]),
           "axes": dict(alpha=ALPHA_AXIS, mean=MEAN_AXIS, freq=FREQ_AXIS,
                        V=V_AXIS, split=SPLIT_AXIS, lag=LAG_AXIS,
                        amp=AMP_AXIS, refine_topk=REFINE_TOPK),
           "old_op": base.OP_OLD}
    jpath = os.path.join(cfg.LOG_DIR, "scan_waveform_retrim_expand.json")

    # ── 锚点对账：原域最近能力点 + 现行工作点，n=288 重测 ──
    anchor_p = dict(V=3.5, f=1.50, alpha_set=40.0, mean=10.0,
                    split=0.50, pitch_lag_s=-0.02, amplitude=30.0)
    anchor = measure_full(anchor_p, N_FINE)
    print("[expand] 锚点对账(原域最近能力点): θ=%+.2f° 残差=%.2f%% "
          "My=%+.4f——原域轮 n=72 口径 0.1800（n=288 复测见 JSON）"
          % (anchor["pitch"], anchor["vert_resid"] * 100,
             anchor["my_total"]), flush=True)
    out["anchor_n288"] = anchor
    oldn = base.measure(base.OP_OLD, N_FINE)
    print("[expand] 现行工作点 n=288 重测: θ=%+.2f° 残差=%.1f%% "
          "My=%+.4f" % (oldn["pitch"], oldn["vert_resid"] * 100,
                        oldn["my_total"]), flush=True)
    out["old_point_n288"] = oldn

    # ── 阶段一：粗筛 480 组（排名 n=72）──
    combos = [dict(V=V, f=f, alpha_set=a, mean=m,
                   split=base.OP_OLD["split"],
                   pitch_lag_s=base.OP_OLD["pitch_lag_s"],
                   amplitude=base.OP_OLD["amplitude"])
              for a in ALPHA_AXIS for m in MEAN_AXIS
              for f in FREQ_AXIS for V in V_AXIS]
    if smoke:
        combos = combos[:6]
    rows = []
    for k, p in enumerate(combos):
        r = measure_full(p, N_COARSE)
        rows.append(r)
        if (k + 1) % 25 == 0 or (r["pass_vert"] and r["pass_my"]):
            print("[xcoarse %3d/%d] α%2.0f MEAN%+5.1f f%.2f V%.1f → "
                  "θ%+6.2f 残差%5.1f%% My%+7.4f%s"
                  % (k + 1, len(combos), p["alpha_set"], p["mean"],
                     p["f"], p["V"], r["pitch"], r["vert_resid"] * 100,
                     r["my_total"],
                     " ✓两过" if r["pass_vert"] and r["pass_my"] else
                     (" ✓残差" if r["pass_vert"] else "")), flush=True)
    survivors = [r for r in rows if r["pass_vert"]]
    out["coarse"] = rows
    out["coarse_survivors"] = len(survivors)
    dump(out, jpath)
    print("[expand] 粗筛完成：%d 组，残差≤2%% 幸存 %d 组"
          % (len(rows), len(survivors)), flush=True)

    # ── 阶段二：细化（幸存者按 |My| 前 K 名 × 24 变体，排名 n=72）──
    variants = []
    if survivors:
        for s in sorted(survivors, key=lambda r: abs(r["my_total"])
                        )[:REFINE_TOPK]:
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
        r = measure_full(p, N_COARSE)
        vrows.append(r)
        if (k + 1) % 15 == 0 or r["pass_all"] or r["pass_down_all"]:
            print("[xrefine %3d/%d] α%2.0f MEAN%+5.1f f%.2f V%.1f "
                  "SP%.3f LG%+.2f AMP%.0f → θ%+6.2f 残差%5.1f%% "
                  "My%+7.4f 可实现=%s%s%s"
                  % (k + 1, len(variants), p["alpha_set"], p["mean"],
                     p["f"], p["V"], p["split"], p["pitch_lag_s"],
                     p["amplitude"], r["pitch"], r["vert_resid"] * 100,
                     r["my_total"], r["pass_real"],
                     " ✓全过" if r["pass_all"] else
                     (" ✓降级线" if r["pass_down_all"] else ""),
                     "" if r["pass_real"] else " ✗可实现"), flush=True)
    out["refine"] = vrows
    dump(out, jpath)
    npass = len([r for r in vrows if r["pass_all"]])
    ndown = len([r for r in vrows if r["pass_down_all"]
                 and not r["pass_all"]])
    print("[expand] 细化完成：%d 变体；0.10 全过 %d 个；仅降级线 %d 个"
          % (len(vrows), npass, ndown), flush=True)

    # ── 阶段三：终值 n=288（0.10 线前 5；若无则降级线前 3）──
    finals, finals_down = [], []
    passing = sorted([r for r in vrows if r["pass_all"]],
                     key=lambda r: abs(r["my_total"]))
    if passing:
        for p in passing[:TOP_BUFFER]:
            r = measure_full(p, N_FINE)
            r["pass_all_n288"] = r["pass_all"]
            finals.append(r)
            print("[xfinal n=288 达标线] %s" % _brief(p, r), flush=True)
    else:
        down = sorted([r for r in vrows if r["pass_down_all"]],
                      key=lambda r: abs(r["my_total"]))
        for p in down[:TOP_REPORT]:
            r = measure_full(p, N_FINE)
            r["pass_down_n288"] = r["pass_down_all"]
            finals_down.append(r)
            print("[xfinal n=288 降级线] %s" % _brief(p, r), flush=True)
    out["final"] = finals
    out["final_down"] = finals_down
    dump(out, jpath)

    best_effort = None
    if not (finals and any(f["pass_all_n288"] for f in finals)) \
            and not (finals_down
                     and any(f["pass_down_n288"] for f in finals_down)):
        src = vrows if vrows else rows
        pool = [r for r in src if r["pass_vert"]] or src
        best_effort = min(pool, key=lambda r: abs(r["my_total"]))
        print("[expand] ★两线皆无全解——最近能力 My=%+.4f（θ%+.2f°，"
              "残差%.1f%%）" % (best_effort["my_total"],
                                best_effort["pitch"],
                                best_effort["vert_resid"] * 100),
              flush=True)
    out["best_effort"] = best_effort
    dump(out, jpath)
    write_report(out, jpath)
    print("[expand] 完成 → docs/波形参数重搜索_扩域.md", flush=True)


def _brief(p, r):
    return ("α%2.0f MEAN%+5.1f f%.2f V%.1f SP%.3f LG%+.2f AMP%.0f → "
            "θ%+6.2f Fz%.3f 残差%5.2f%% My%+7.4f 舵占%3.0f%% %s"
            % (p["alpha_set"], p["mean"], p["f"], p["V"], p["split"],
               p["pitch_lag_s"], p["amplitude"], r["pitch"], r["fz"],
               r["vert_resid"] * 100, r["my_total"],
               r["elev_rad"] / DELTA_MAX * 100,
               "✓" if (r.get("pass_all_n288")
                       or r.get("pass_down_n288")) else "✗翻转"))


def write_report(out, jpath):
    ts = out["ts"]
    finals = [f for f in out["final"] if f.get("pass_all_n288")]
    finals_down = [f for f in out["final_down"] if f.get("pass_down_n288")]
    md = [f"# 波形参数重搜索·扩域轮（{ts}）", "",
          "- 裁决与性质：用户 0924 批准扩域（偏置 +20°、速度 3.0 档、"
          "分裂比 0.75、新增执行机构可实现性判据）；本搜索仍为**波形"
          "空间多目标重新配平**（三目标声明+消融记录保留，同原域轮）；"
          "负偏置侧按原域前沿证据裁掉（最好负侧 0.2103 对正侧 0.1800）。",
          f"- 模型：sane_dickinson_3d 静态测量（角速度恒为零）；"
          f"HEAD={out['head']}；本步不改任何 config；原始数据："
          f"`{os.path.relpath(jpath, PROJECT_ROOT)}`；排名 n={N_COARSE}、"
          f"终值 n={N_FINE}。", "",
          "## 判据（先于数据）", "",
          f"1. 竖直残差 ≤ {VERT_MAX:.0%}；2. 前向力必须配平；"
          f"3. |My| ≤ {MY_MAX:.2f} N·m 达标 / {MY_DOWNGRADE:.2f} 降级线"
          "（须声明权限占用）；",
          "4. 执行机构可实现性（静态可算部分）：拍动角峰值 |MEAN|+幅值"
          f" ≤ {math.degrees(base.PHI_LIMIT_RAD):.1f}°（驱动限位 "
          f"±{base.PHI_LIMIT_RAD} rad）；拍频 {base.FREQ_LO}~"
          f"{base.FREQ_HI} Hz；分裂比 ∈ {SPLIT_CLAMP}（运动学钳位域）；"
          f"α_set ≤ {ALPHA_FLAG_MAX:.0f}° 且俯仰舵机行程未核实（标记待"
          "真机）；**动态跟踪能力（舵机带宽、驱动摆幅比衰减）静态扫描"
          "无法验证，MEAN 大偏置的铰链止动与连杆几何不在模型内——两"
          "项列真机考核清单**；",
          "5. 两线皆无解时如实报告最近能力，不硬凑。", "",
          "## 搜索空间（扩域）", "",
          f"- 粗筛：α {ALPHA_AXIS}° × MEAN {MEAN_AXIS}° × f "
          f"{FREQ_AXIS} Hz × V {V_AXIS} m/s = "
          f"{len(ALPHA_AXIS)*len(MEAN_AXIS)*len(FREQ_AXIS)*len(V_AXIS)} 组；",
          f"- 细化：残差过线者按 |My| 前 {REFINE_TOPK} 名各展开 SPLIT "
          f"{SPLIT_AXIS} × LAG {LAG_AXIS} s × 幅值 {AMP_AXIS}° = "
          f"{len(SPLIT_AXIS)*len(LAG_AXIS)*len(AMP_AXIS)} 变体"
          "（LAG 0/+0.02 与 SPLIT 0.25 按原域前沿证据裁掉）；",
          f"- 终值：0.10 线三过者前 {TOP_BUFFER} 名 n=288 重算报前 "
          f"{TOP_REPORT}；0.10 无解则降级线前 {TOP_REPORT} 名同法重算。",
          ""]

    a = out["anchor_n288"]
    md += ["## 锚点对账（原域最近能力点，n=288 复测）", "",
           f"- α40/MEAN+10/f1.50/V3.5/SP0.50/LAG−0.02/AMP30 → θ="
           f"{a['pitch']:+.2f}°、残差 {a['vert_resid']:.2%}、"
           f"My={a['my_total']:+.4f} N·m（原域轮 n=72 口径 "
           "0.1800/0.22%/+15.86°——同点跨轮复现闭合）。", ""]

    md += ["## 粗筛结果", "",
           f"- {len(out['coarse'])} 组中残差≤2% 幸存 **"
           f"{out['coarse_survivors']} 组**（全量数字见 JSON）。", ""]

    npass = len([r for r in out["refine"] if r["pass_all"]])
    ndown = len([r for r in out["refine"] if r["pass_down_all"]
                 and not r["pass_all"]])
    md += ["## 细化结果（n=72 口径）", "",
           f"- {len(out['refine'])} 变体；0.10 全过 {npass} 个，仅降级"
           f"线 {ndown} 个。残差过线且 |My| 最小的五个：", "",
           HDR, SEP]
    pool = [r for r in out["refine"] if r["pass_vert"]] or out["refine"]
    for r in sorted(pool, key=lambda r: abs(r["my_total"]))[:5]:
        md.append(fmt_row(r))
    md.append("")

    md += ["## 终值结果（n=288）", ""]
    winner = None
    if finals:
        winner = finals[0]
        md += [f"0.10 达标线通过（前 {TOP_REPORT} 名）：", "", HDR,
               SEP]
        for r in finals[:TOP_REPORT]:
            md.append(fmt_row(r, "pass_all_n288"))
    elif finals_down:
        md += ["**0.10 达标线无全解——降级线 0.15 N·m 裁决材料**"
               f"（前 {TOP_REPORT} 名，n=288）：", "", HDR,
               SEP]
        for r in finals_down[:TOP_REPORT]:
            md.append(fmt_row(r, "pass_down_n288"))
    else:
        md.append("**两线皆无全解**——诚实分支。最近能力：")
        be = out.get("best_effort")
        if be:
            md += ["", HDR, SEP]
            md.append(fmt_row(be))
    md.append("")

    if winner:
        md += ["### 新工作点全套数字（第 1 名 vs 现行工作点）", "",
               "| 量 | 现行工作点 | 扩域新工作点 |", "|---|---|---|"]
        md += _pair_rows(out.get("old_point_n288", {}), winner)
        md.append("")
    elif finals_down:
        md += ["### 降级线裁决材料·代价摘要", ""]
        r0 = finals_down[0]
        md += [f"- 残余力矩 {r0['my_total']:+.4f} N·m 折算升降舵偏度 "
               f"{r0['elev_rad']:.3f} rad，占 ±{DELTA_MAX} rad 限幅 "
               f"{r0['elev_rad']/DELTA_MAX:.0%}（满偏只能配 "
               f"{abs(K_ELEV)*DELTA_MAX:.3f} N·m）；",
               f"- 配平俯仰 {r0['pitch']:+.2f}°；"
               f"可实现性闸门 {'全过' if r0['pass_real'] else '有未过项'}"
               "（明细见 JSON 行字段）；",
               "- 接受该点即接受常值俯仰力矩残余，第五步在环复跑须带此"
               "口径声明。", ""]

    # ── 连带检查：阻尼矩阵复测预告（俯仰 15~25° 攻角饱和区邻域）──
    target = winner or (finals_down[0] if finals_down else None)
    md += ["## 连带检查：阻尼矩阵复测预告（供第五步复跑口径声明）", ""]
    if target is None:
        md.append("本轮无候选工作点，预告不触发；但第 12 章挂账第四行"
                  "（矩阵工况依赖）对任何落定的新工作点无条件生效。")
    else:
        th = target["pitch"]
        in_zone = THETA_REMEASURE[0] <= th <= THETA_REMEASURE[1]
        md += [f"- 触发判定：候选点配平俯仰 θ={th:+.2f}°，"
               + ("**落在 15~25° 攻角饱和区邻域——复测预告触发**。"
                  if in_zone else
                  "未落在 15~25° 区；但该点与阻尼矩阵测量工况"
                  "（θ=+7.14°）仍不同，按第 12 章挂账第四行无条件"
                  "须复测。"),
               "- 依据（第十九班扩段结论，docs/旋转阻尼专项辨识_扩段.md）"
               "：9 rad/s 处滚转斜率弱化 42.8%、偏航 7.6%，弱化机制为"
               "叶素攻角 45° 饱和主导——高姿态/高攻角设定工作点更靠近"
               "饱和区，小信号矩阵的线性段与弱化边界都会移动。",
               "- 复测需求（第五步复跑前完成）：按 tools/scan_rot_damping."
               "py 在新配平点重测三乘三矩阵（对角斜率+0.5 rad/s 代表点"
               "矩阵+扩段判据），产出新旧矩阵对照；第 12 章模态数字与"
               "逐拍权限折算在复测前只可引用旧矩阵并携带本声明。",
               "- 口径声明建议文本（第五步报告用）：'本轮在环采用工作点"
               f"θ={th:+.2f}°（阻尼矩阵测量点 θ=+7.14°），阻尼矩阵未"
               "复测，横向模态与量子兑现数字沿旧矩阵口径引用，"
               "饱和区邻域弱化风险未闭。'", ""]

    md += ["## 判读与移交", "",
           "- 扩域相对原域的增量结论见上表；舵面消化预算口径同原域轮"
           f"（dMy/dδ={K_ELEV} N·m/rad）。",
           "- ★拍频调度带预警：获胜点 f=1.25 Hz 落在现行频率调度器输出"
           "带 DP_FREQ_MIN~DP_FREQ_MAX=[1.4, 1.8] Hz 之下（保护性拍频带"
           " FREQ_AP_MIN=1.0/FREQ_MAX_AP=3.5 之内、可实现性闸门通过）；"
           "采纳该工作点须同步下移调度带下限，属设定值层调整，随下一批"
           "控制器设定值更新一并处置。",
           "- 移交边界：控制器设定值更新与第五步在环复跑属下一批；"
           "本步未改任何 config。",
           "- 边界声明：静态口径（ω=0）与一队 ω×r 恢复数学上无关；"
           "可实现性判据只覆盖静态几何与命令域，动态跟踪能力待真机。",
           ""]
    doc = os.path.join(PROJECT_ROOT, "docs", "波形参数重搜索_扩域.md")
    with open(doc, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))


def _pair_rows(old, w0):
    rows = [
        ("速度 V (m/s)", "%.1f", "V"),
        ("拍频 f (Hz)", "%.2f", "f"),
        ("攻角设定 α_set (°)", "%.0f", "alpha_set"),
        ("扑动面偏置 MEAN (°)", "%+.1f", "mean"),
        ("分裂比 SPLIT", "%.3f", "split"),
        ("时间相位 LAG (s)", "%+.2f", "pitch_lag_s"),
        ("拍动幅值 (°)", "%.0f", "amplitude"),
        ("配平俯仰 θ (°)", "%+.2f", "pitch"),
        ("前向合力 Fx (N)", "%+.4f", "fx"),
        ("竖直合力 Fz (N)", "%.3f", "fz"),
        ("竖直残差", "%.2%", "vert_resid"),
        ("全机俯仰力矩 My (N·m)", "%+.4f", "my_total"),
        ("翼 My (N·m)", "%+.4f", "my_wing"),
        ("尾 My (N·m)", "%+.4f", "my_tail"),
        ("拍动角峰值 |MEAN|+幅值 (°)", "%.1f", None),
        ("残余力矩舵占限幅", "%.0%", None),
        ("可实现性闸门", "%s", None),
    ]
    out = []
    for label, fmt, key in rows:
        if key == "vert_resid":
            ov = "{:.1%}".format(old["vert_resid"]) if old else "—"
            nv = "{:.2%}".format(w0["vert_resid"])
        elif key is None:
            if "峰值" in label:
                ov = "%.1f" % (abs(base.OP_OLD["mean"])
                               + base.OP_OLD["amplitude"])
                nv = "%.1f" % (abs(w0["mean"]) + w0["amplitude"])
            elif "舵占" in label:
                ov = "{:.0%}".format(old["elev_rad"] / DELTA_MAX) \
                    if old else "131%"
                nv = "{:.0%}".format(w0["elev_rad"] / DELTA_MAX)
            else:
                ov, nv = "—", "全过" if w0["pass_real"] else "有未过项"
        elif key in old:
            ov, nv = fmt % old[key], fmt % w0[key]
        else:
            ov, nv = "—", fmt % w0[key]
        out.append(f"| {label} | {ov} | {nv} |")
    return out


if __name__ == "__main__":
    main()
