# -*- coding: utf-8 -*-
"""
wp_acceptance.py — 循迹验收：位置误差 / 收敛时间 / 最大误差（清单第六项验收件）
================================================================================
用法：
  python tools/wp_acceptance.py                      # 取最新的 ident_stream_flight_*.csv
  python tools/wp_acceptance.py logs/xxx.csv         # 指定CSV
  python tools/wp_acceptance.py --wp data/wp_circle_r5.json --bound 0.5

输入：全量记录 CSV（main_sim EXPERIMENT_MODE 记录，列：
  t,phase,freq,Fx,Fy,Fz,Mx,My,Mz,TMx,TMy,TMz,z,roll,pitch,wx,wy,wz,vx,vy,qw,qz,tension）
期望轨迹：data/wp_circle_r5.json（position 字段）。

列自适应当前现实（2026-09-20）：现有 flight CSV 只有 z 列、无 x/y 列——
水平位置误差无法计算。脚本在缺列时自动降级为高度通道验收并在结论中
DEGRADED 声明（不得用速度积分估算冒充位置实测）；在环记录补 x/y 列后
本脚本无需改动即可计算全轴误差。

验收阈值（清单原文"位置误差收敛且有界"的操作化，可 --bound 调整）：
  收敛：存在 t_conv，t>t_conv 后滚动误差均超过 bound 的样本占比 <5%；
  有界：稳态窗（记录后 50% 时段）误差 95 分位 < bound。
退出码：0=PASS，1=FAIL，2=DEGRADED（数据缺列），3=输入错误。
输出：stdout 摘要 + logs/wp_acceptance_<ts>.md 报告。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GLOB = os.path.join(ROOT, "logs", "ident_stream_flight_*.csv")
DEFAULT_WP = os.path.join(ROOT, "data", "wp_circle_r5.json")
REPORT_FMT = os.path.join(ROOT, "logs", "wp_acceptance_%s.md")


def load_wp(path):
    with open(path, encoding="utf-8") as f:
        wps = json.load(f)["waypoints"]
    return [(w["t"], w["position"]) for w in wps]


def wp_pos_at(wps, t):
    """航点线性插值取期望位置。"""
    if t <= wps[0][0]:
        return wps[0][1]
    if t >= wps[-1][0]:
        return wps[-1][1]
    for i in range(len(wps) - 1):
        (ta, pa), (tb, pb) = wps[i], wps[i + 1]
        if ta <= t <= tb:
            s = (t - ta) / max(tb - ta, 1e-9)
            return [pa[k] + s * (pb[k] - pa[k]) for k in range(3)]
    return wps[-1][1]


def main():
    ap = argparse.ArgumentParser(description="循迹位置误差验收")
    ap.add_argument("csv", nargs="?", default=None, help="全量记录CSV（缺省取最新）")
    ap.add_argument("--wp", default=DEFAULT_WP)
    ap.add_argument("--bound", type=float, default=0.5,
                    help="收敛/有界阈值 (m)，默认0.5")
    args = ap.parse_args()

    # 输入解析
    if args.csv:
        csv_path = args.csv
    else:
        cands = sorted(glob.glob(DEFAULT_GLOB))
        if not cands:
            print("[accept] 错误: 未找到 ident_stream_flight_*.csv", flush=True)
            return 3
        csv_path = cands[-1]
    if not os.path.isfile(csv_path):
        print(f"[accept] 错误: 文件不存在 {csv_path}", flush=True)
        return 3
    wps = load_wp(args.wp)

    # 读CSV（首行=列头；phase 列为文本，容错处理）
    with open(csv_path, encoding="utf-8-sig") as f:
        header = [c.strip() for c in f.readline().split(",")]
        rows = []
        for line in f:
            parts = line.strip().split(",")
            if len(parts) != len(header):
                continue
            rec = {}
            ok = True
            for h, v in zip(header, parts):
                try:
                    rec[h] = float(v)
                except ValueError:
                    rec[h] = None
            for key in ("t", "z"):
                if rec.get(key) is None:
                    ok = False
            if ok:
                rows.append(rec)
    if len(rows) < 10:
        print(f"[accept] 错误: 有效样本不足({len(rows)}行)", flush=True)
        return 3

    has_xy = ("x" in header) and ("y" in header)
    has_z = "z" in header
    if not has_z:
        print("[accept] 错误: CSV无z列，无法验收", flush=True)
        return 3

    # 逐时刻误差
    errs = []          # (t, err_used, err_z)
    for r in rows:
        p_ref = wp_pos_at(wps, r["t"])
        ez = abs(r["z"] - p_ref[2])
        if has_xy:
            e = math.dist([r["x"], r["y"], r["z"]], p_ref)
        else:
            e = ez
        errs.append((r["t"], e, ez))
    n = len(errs)
    t0, t1 = errs[0][0], errs[-1][0]
    e_all = [e for _, e, _ in errs]
    e_max = max(e_all)
    e_max_t = max(errs, key=lambda x: x[1])[0]

    # 稳态窗 = 后50%时段；95分位
    half = errs[n // 2:]
    ss = sorted(e for _, e, _ in half)
    p95 = ss[int(math.ceil(0.95 * len(ss))) - 1]

    # 收敛时间：首个使"此后超界占比<5%"的样本时刻
    over_after = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        over_after[i] = over_after[i + 1] + (1 if e_all[i] > args.bound else 0)
    t_conv = None
    for i in range(n):
        if over_after[i] / (n - i) < 0.05:
            t_conv = errs[i][0]
            break

    # 判定
    bounded = p95 < args.bound
    converged = t_conv is not None
    if has_xy:
        core_pass = bounded and converged
        verdict = "PASS" if core_pass else "FAIL"
    else:
        # 缺x/y：只对高度通道出结论，整体 DEGRADED
        verdict = "DEGRADED"
    lines = []
    a = lines.append
    a(f"# 循迹位置误差验收报告")
    a("")
    a(f"- 生成时刻: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    a(f"- 记录文件: {os.path.relpath(csv_path, ROOT)}（{n}样本, "
      f"t∈[{t0:.1f},{t1:.1f}]s）")
    a(f"- 期望轨迹: {os.path.relpath(args.wp, ROOT)}")
    a(f"- 阈值 bound={args.bound} m（'位置误差收敛且有界'操作化）")
    a("")
    a(f"| 指标 | 值 |")
    a(f"|---|---|")
    a(f"| 最大误差 | {e_max:.3f} m @ t={e_max_t:.1f}s |")
    a(f"| 稳态95分位(后50%时段) | {p95:.3f} m |")
    a(f"| 收敛时间 | "
      + (f"t={t_conv:.1f}s（此后超界占比<5%）" if converged else "未收敛"))
    a(f"| 水平通道 | "
      + ("全轴误差（x,y,z列齐）" if has_xy
         else "**缺失**——CSV无x/y列，无法计算水平位置误差，不得以速度积分估算冒充"))
    a("")
    if has_xy:
        a(f"## 结论: {verdict}")
        a(f"- 有界: {'满足' if bounded else '不满足'}"
          f"（稳态95分位 {p95:.3f} {'<' if bounded else '>='} {args.bound}）")
        a(f"- 收敛: {'满足' if converged else '不满足'}")
    else:
        a(f"## 结论: {verdict}（高度通道 "
          f"{'满足收敛且有界' if (bounded and converged) else '不满足'}，"
          f"整体因水平通道缺数据不判PASS）")
        a("- 处置: 在环全量记录补 x/y 列后重跑本脚本即可全轴验收；"
          "当前结果只覆盖高度通道。")
    report = REPORT_FMT % time.strftime("%m%d_%H%M%S")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    print(f"\n[accept] 报告已写 {report}", flush=True)
    return {"PASS": 0, "FAIL": 1, "DEGRADED": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
