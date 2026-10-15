# -*- coding: utf-8 -*-
"""
scan_channel_effectiveness.py — 差动波形三通道逐通道操纵效能表
（转弯路线图第五步，2026-09-24）

问题：机翼差动的波形级权限扩容有三条候选通道——均值偏置（MEAN，
扑动面偏置）、分裂比（SPLIT，上下扑时间分配）、时间相位
（PITCH_LAG_S，俯仰程序超前/滞后）。第五步对三条通道逐一测四列：
  1. 稳态滚转操纵效能 dTx/dΔ（左右翼通道量异向 ±Δ 的周期平均滚转
     力矩斜率，中心差分）；
  2. 稳态偏航副作用 dTz/dΔ（同口径的偏航力矩斜率——差动的代价列）；
  3. 拍内滚转峰峰值（差动开启后周期平均之上的拍内 Tx 摆动幅度，
     n=288 逐点序列 max−min）；
  4. 升力漂移 dFz/dΔ（差动引起的竖直力变化斜率——掉高代价列）。
回答"波形级权限扩容应该押哪条通道"= 差动主翼的选型依据。

口径：巡航配平点（V=4.0 m/s、f=1.75 Hz、α_set=35°，Fx=0 配平俯仰
钉固姿态整周期平均）；测量纪律三条遵守（时基单调/显式设频/
粗扫 n=72、终值 n=288）。扰动只进翼运动学（单变量：通道量），
不动尾翼与阻尼。

用法： python tools/scan_channel_effectiveness.py（纯 NumPy）
产物： logs/scan_channel_effectiveness.json
      + docs/逐通道操纵效能表.md
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
from modules.aero_solver import TailFuselageAero, euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G = 9.81
W = cfg.MASS * G
T0 = [100000.0]
N_COARSE, N_FINE = 72, 288

OP_V, OP_F, OP_ALPHA_SET = 4.0, 1.75, 35.0
KMEAN = cfg.FlapKinematicsConfig.MEAN        # 度
KSPLIT = cfg.FlapKinematicsConfig.SPLIT
KLAG = cfg.FlapKinematicsConfig.PITCH_LAG_S  # 秒

# 通道定义：名称 / 施加函数（左+Δ 右−Δ）/ 扰动档位 / 额定差动（换算行）
CHANNELS = [
    dict(name="均值偏置 MEAN", key="mean",
         deltas=[1.0, 2.0, 5.0], unit="°", rated=5.0,
         apply=lambda d: (
             setattr(model.wing_left, "mean",
                     math.radians(KMEAN + d)),
             setattr(model.wing_right, "mean",
                     math.radians(KMEAN - d)))),
    dict(name="分裂比 SPLIT", key="split",
         deltas=[0.02, 0.05, 0.10], unit="", rated=0.10,
         apply=lambda d: (
             setattr(model.wing_left, "split", KSPLIT + d),
             setattr(model.wing_right, "split", KSPLIT - d))),
    dict(name="时间相位 PITCH_LAG_S", key="lag",
         deltas=[0.005, 0.01, 0.02], unit="s", rated=0.02,
         apply=lambda d: (
             setattr(model.wing_left, "pitch_lag_s", KLAG + d),
             setattr(model.wing_right, "pitch_lag_s", KLAG - d))),
]


def reset_kinematics():
    for w in (model.wing_left, model.wing_right):
        w.alpha_set = math.radians(OP_ALPHA_SET)
        w.split = KSPLIT
        w.pitch_lag_s = KLAG
        w.mean = math.radians(KMEAN)
        w.amplitude = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)


model = SaneDickinson3DModel()
tail = TailFuselageAero()
reset_kinematics()


def eval_series(V, f, pd_deg, n=N_FINE):
    """钉固状态整周期逐点力/力矩（翼+尾）；返回周期平均与拍内序列。"""
    model.set_wing_command(frequency=f)
    quat = euler_to_quat(0.0, math.radians(pd_deg), 0.0)
    vel = np.array([V, 0.0, 0.0])
    om = np.zeros(3)
    T = 1.0 / f
    F_ser = np.zeros((n, 3))
    T_ser = np.zeros((n, 3))
    for i in range(n):
        Fw, Tw = model.compute_forces(np.zeros(3), quat, vel, om,
                                      T0[0] + i * T / n)
        F_ser[i], T_ser[i] = Fw, Tw
    T0[0] += T
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f
    Ft, Tt = tail.compute(quat, vel, v_slip=v_slip)
    F_ser += Ft
    T_ser += Tt
    return dict(F_mean=F_ser.mean(axis=0), T_mean=T_ser.mean(axis=0),
                tx_p2p=float(T_ser[:, 0].max() - T_ser[:, 0].min()),
                fz_mean=float(F_ser[:, 2].mean()))


def solve_pitch(V, f):
    """Fx=0 双阱稳健求解（粗 n=72 变号+二分；终值 n=288 精修）。"""
    def fx(th, n):
        return float(eval_series(V, f, th, n)["F_mean"][0])

    grid = np.arange(-20.0, 25.001, 1.5)
    vals = [(float(th), fx(th, N_COARSE)) for th in grid]
    brackets = [(a[0], b[0]) for a, b in zip(vals[:-1], vals[1:])
                if a[1] == 0.0 or a[1] * b[1] < 0]
    if not brackets:
        th0 = min(vals, key=lambda r: abs(r[1]))[0]
        a, b = th0 - 1.0, th0 + 1.0
    else:
        # 多根（双阱）取竖直残差最小者——与其他扫描工具同口径；
        # 2026-09-24 修正：初版取 brackets[0] 落进错误阱（θ≈−2.3°、
        # 竖直残差 5.4%），巡航配平根在 +7° 邻域
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
            fz_c = float(eval_series(V, f, th_c, N_COARSE)["F_mean"][2])
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


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "model": "sane_dickinson_3d",
           "op": {"V": OP_V, "f": OP_F, "alpha_set": OP_ALPHA_SET,
                  "mean_deg": KMEAN, "split": KSPLIT, "lag_s": KLAG},
           "channels": {}}

    reset_kinematics()
    th0 = solve_pitch(OP_V, OP_F)
    base = eval_series(OP_V, OP_F, th0)
    print(f"[chan] 配平 θ={th0:+.2f}° Fx={float(base['F_mean'][0]):+.4f} "
          f"Fz={base['fz_mean']:.3f} 基线Tx_p2p={base['tx_p2p']:.4f}",
          flush=True)
    out["baseline"] = dict(pitch=th0,
                           fx=float(base["F_mean"][0]),
                           fz=base["fz_mean"],
                           tx=float(base["T_mean"][0]),
                           tz=float(base["T_mean"][2]),
                           tx_p2p=base["tx_p2p"])

    for ch in CHANNELS:
        rows = []
        for d in ch["deltas"]:
            reset_kinematics()
            ch["apply"](+d)
            plus = eval_series(OP_V, OP_F, th0)
            reset_kinematics()
            ch["apply"](-d)
            minus = eval_series(OP_V, OP_F, th0)
            row = dict(
                delta=d,
                tx_slope=(float(plus["T_mean"][0]) - float(minus["T_mean"][0]))
                / (2.0 * d),
                tz_slope=(float(plus["T_mean"][2]) - float(minus["T_mean"][2]))
                / (2.0 * d),
                fz_slope=(plus["fz_mean"] - minus["fz_mean"]) / (2.0 * d),
                tx_p2p=0.5 * (plus["tx_p2p"] + minus["tx_p2p"]))
            rows.append(row)
            print(f"[chan] {ch['name']} Δ={d}{ch['unit']}: "
                  f"dTx/dΔ={row['tx_slope']:+.4f} "
                  f"dTz/dΔ={row['tz_slope']:+.4f} "
                  f"dFz/dΔ={row['fz_slope']:+.4f} "
                  f"Tx_p2p={row['tx_p2p']:.3f}", flush=True)
        # 额定差动换算行：以最大档（=rated）斜率×rated
        rated_row = rows[-1]
        out["channels"][ch["key"]] = dict(
            name=ch["name"], unit=ch["unit"], rated=ch["rated"], rows=rows,
            rated_tx=float(rated_row["tx_slope"]) * ch["rated"],
            rated_tz=float(rated_row["tz_slope"]) * ch["rated"],
            rated_fz=float(rated_row["fz_slope"]) * ch["rated"])
        reset_kinematics()

    with open(os.path.join(cfg.LOG_DIR, "scan_channel_effectiveness.json"),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    # ── 报告 ──
    md = [f"# 逐通道操纵效能表·差动波形三通道（{ts}）", "",
          f"- 模型：sane_dickinson_3d；工况：巡航配平点 V={OP_V} m/s、"
          f"f={OP_F} Hz、α_set={OP_ALPHA_SET}°、θ={th0:+.2f}°（Fx=0 配平、"
          f"姿态钉固）；差动=通道量左右翼异向 ±Δ",
          f"- 测量纪律三条遵守：时基单调/显式设频/终值 n={N_FINE}；"
          f"斜率=±Δ 两点中心差分",
          f"- 原始数据：`logs/scan_channel_effectiveness.json`", ""]
    for ch in CHANNELS:
        c = out["channels"][ch["key"]]
        md += [f"## {c['name']}", "",
               f"| 差动Δ({ch['unit']}) | dTx/dΔ (N·m/Δ) |"
               f" dTz/dΔ (N·m/Δ) | dFz/dΔ (N/Δ) | 拍内Tx峰峰 (N·m) |",
               "|---|---|---|---|---|"]
        for r in c["rows"]:
            md.append(f"| {r['delta']:g} | {r['tx_slope']:+.4f} |"
                      f" {r['tz_slope']:+.4f} | {r['fz_slope']:+.4f} |"
                      f" {r['tx_p2p']:.3f} |")
        md += ["",
               f"- 额定差动 ±{c['rated']:g}{ch['unit']} 换算：滚转效能 "
               f"{c['rated_tx']:+.4f} N·m、偏航副作用 {c['rated_tz']:+.4f}"
               f" N·m、升力漂移 {c['rated_fz']:+.4f} N"
               f"（对照体重 {W:.2f} N 与尾舵持续能力 0.03 N·m）。", ""]
    md += ["## 通道对照与判读", "",
           "| 通道 | 额定滚转效能 (N·m) | 额定偏航副作用 (N·m) |"
           " 额定升力漂移 (N) | 副作用/效能 |",
           "|---|---|---|---|---|"]
    for ch in CHANNELS:
        c = out["channels"][ch["key"]]
        ratio = (abs(c["rated_tz"] / c["rated_tx"])
                 if abs(c["rated_tx"]) > 1e-9 else float("nan"))
        md.append(f"| {c['name']} | {c['rated_tx']:+.4f} |"
                  f" {c['rated_tz']:+.4f} | {c['rated_fz']:+.4f} |"
                  f" {ratio:.2f} |")
    md += ["",
           "- 判读标准先于数据：选通道看三件事——额定滚转效能量级、"
           "偏航副作用/效能比（越小越好，方向舵协调负担）、升力漂移"
           "（掉高代价）。",
           "- 斜率的档间一致性（三档近常数=线性可调度；显著漂移=非线性"
           "需查表调度）逐通道见上表。",
           "- 边界声明：钉固姿态周期平均口径，不含闭环动力学；拍内峰峰"
           "列提示逐拍控制的瞬时激励幅度，供与单拍量子对照"
           "（docs/单拍量子_包线网格.md）。",
           "- 遗留：三条通道的可实现执行机构带宽与伺服功率不在气动模型"
           "内；SPLIT 通道左右异向时上下扑时间分配反向，其非对称扑动"
           "偏航物理的建模边界见十项简化记档。", ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", "逐通道操纵效能表.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[chan] 完成 → docs/逐通道操纵效能表.md", flush=True)


if __name__ == "__main__":
    main()
