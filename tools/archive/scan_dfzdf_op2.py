# -*- coding: utf-8 -*-
"""
scan_dfzdf_op2.py — 新工作点 ∂Fz/∂f 离线验证（第三十一班，第6项依据）
======================================================================
目的：为十八项签核清单第 6 项（TECS 低速升频保护方向修正）提供被控
对象符号证据。旧证据（docs/TRIM_SCAN_215g.md 附录一）是旧波形
（α40/MEAN0/SPLIT0.25/LAG−0.02）在旧配平俯仰 −6.2° 下的数字
（V=4 处 ∂Fz/∂f≈−0.27 N/Hz）；新工作点换全套波形（扩域获胜点
幅值 30 档）后符号必须重测，不得沿用旧结论。

口径（与 tools/scan_waveform_retrim.py 同源，复用其模型实例与
eval_state 求值内核）：
  - 模型 sane_dickinson_3d 显式实例化，静态测量（角速度恒为零），
    不读 AERO_MODEL 开关，不改任何 config；
  - 波形 = 清单 v2 幅值 30 档全套：α_set=35°、MEAN=+20°、
    SPLIT=0.75、LAG=−0.04 s、幅值=30°；
  - 扫频轴 f=1.0~2.0 Hz（0.1 Hz 步，11 点），n=288/周期
    （终值分辨率纪律）；
  - 两组视角：
      A 固定态视角（主口径）：V=3.5 m/s、θ=21.45°（=清单 v2
        PITCH_TRIM 0.374 rad）钉固，扫 f 出周期平均 Fz——即 TECS
        拍频通道实际感受的偏导数（快拍频变化对慢变姿态）；
      B 重配平视角（辅口径）：每个 f 先解 Fx=0 配平俯仰再量 Fz
        ——给出配平轨迹随 f 的走向。

判据（先于数据）：A 视角线性拟合斜率符号为负 → 证实
∂Fz/∂f<0 在新工作点成立（升频减升力），第 6 项修正方向=低速/
下沉工况禁止升频；符号为正 → 旧结论在新波形上翻案，第 6 项按
翻案结论处置。

用法： python tools/scan_dfzdf_op2.py
产物： logs/scan_dfzdf_op2.json（+ 控制台表格）
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

import numpy as np
from config import sim_config as cfg
import scan_waveform_retrim as base

# ── 新工作点（清单 v2 幅值 30 档全套数字）──
# 键名对齐 base.set_waveform(alpha_set, mean_deg, split, pitch_lag_s,
# amplitude_deg) 的形参名
OP2_WAVE = dict(alpha_set=35.0, mean_deg=20.0, split=0.75,
                pitch_lag_s=-0.04, amplitude_deg=30.0)
V_OP2 = 3.5            # m/s（清单 v2 CRUISE_VX/LAUNCH_VX）
TH_OP2_DEG = 21.45     # 度（清单 v2 PITCH_TRIM=0.374 rad 的角度值）
F_AXIS = [round(1.0 + 0.1 * i, 1) for i in range(11)]   # 1.0~2.0 Hz
N = 288                # 每周期样本数（终值分辨率纪律）
W = base.W             # 0.215 kg × 9.81 = 2.109 N


def scan_fixed():
    """视角 A：钉固 V=3.5、θ=21.45°，扫 f 的周期平均力。"""
    rows = []
    for f in F_AXIS:
        base.set_waveform(**OP2_WAVE)
        st = base.eval_state(V_OP2, f, TH_OP2_DEG, N)
        rows.append(dict(f=f,
                         fx=float(st["F_total"][0]),
                         fz=float(st["F_total"][2]),
                         my=float(st["T_total"][1]),
                         fz_over_w=float(st["F_total"][2]) / W))
    return rows


def scan_retrim():
    """视角 B：每个 f 先解 Fx=0 配平俯仰，再量该态的 Fz。"""
    rows = []
    for f in F_AXIS:
        base.set_waveform(**OP2_WAVE)
        th = base.solve_pitch(V_OP2, f)
        st = base.eval_state(V_OP2, f, th, N)
        rows.append(dict(f=f, pitch=th,
                         fx=float(st["F_total"][0]),
                         fz=float(st["F_total"][2]),
                         my=float(st["T_total"][1]),
                         fz_over_w=float(st["F_total"][2]) / W))
    return rows


def fit_slope(rows, key, f_lo=None, f_hi=None):
    """对指定频段做最小二乘线性拟合，返回斜率。"""
    xs = np.array([r["f"] for r in rows
                   if (f_lo is None or r["f"] >= f_lo - 1e-9)
                   and (f_hi is None or r["f"] <= f_hi + 1e-9)])
    ys = np.array([r[key] for r in rows
                   if (f_lo is None or r["f"] >= f_lo - 1e-9)
                   and (f_hi is None or r["f"] <= f_hi + 1e-9)])
    A = np.vstack([xs, np.ones_like(xs)]).T
    k, _ = np.linalg.lstsq(A, ys, rcond=None)[0]
    return float(k)


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    head = ""
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        pass

    fixed = scan_fixed()
    retrim = scan_retrim()

    k_full = fit_slope(fixed, "fz")                 # 1.0~2.0 全段
    k_local = fit_slope(fixed, "fz", 1.1, 1.4)      # 1.25 邻域
    k_fx = fit_slope(fixed, "fx")
    k_ret = fit_slope(retrim, "fz")
    k_ret_th = fit_slope(retrim, "pitch")

    out = dict(ts=ts, head=head, model="sane_dickinson_3d",
               static_omega_zero=True, n_per_cycle=N,
               wave=OP2_WAVE, V=V_OP2, pitch_deg=TH_OP2_DEG,
               freq_axis=F_AXIS, weight_n=W,
               fixed=fixed, retrim=retrim,
               slopes=dict(fz_fixed_full=k_full, fz_fixed_local=k_local,
                           fx_fixed=k_fx, fz_retrim=k_ret,
                           pitch_retrim=k_ret_th),
               sign_conclusion=("negative" if k_full < 0 else "positive"))
    jpath = os.path.join(cfg.LOG_DIR, "scan_dfzdf_op2.json")
    with open(jpath, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    print("[dfzdf] 新工作点∂Fz/∂f验证 W=%.3f N  f=1.0~2.0 Hz n=%d" % (W, N))
    print("\n视角A（固定态 V=3.5 / θ=21.45°，TECS 实感口径）:")
    print("| f(Hz) | Fx(N) | Fz(N) | Fz/W | My(N·m) |")
    print("|" + "---|" * 5)
    for r in fixed:
        print("| %.1f | %+.3f | %.3f | %.3f | %+.4f |"
              % (r["f"], r["fx"], r["fz"], r["fz_over_w"], r["my"]))
    print("\n视角B（逐 f 重配平 Fx=0，配平轨迹口径）:")
    print("| f(Hz) | θ(°) | Fx(N) | Fz(N) | Fz/W |")
    print("|" + "---|" * 5)
    for r in retrim:
        print("| %.1f | %+.2f | %+.3f | %.3f | %.3f |"
              % (r["f"], r["pitch"], r["fx"], r["fz"], r["fz_over_w"]))
    print("\n斜率汇总:")
    print("  视角A Fz: 全段 %.4f N/Hz | 1.25邻域(1.1~1.4) %.4f N/Hz"
          % (k_full, k_local))
    print("  视角A Fx: 全段 %+.4f N/Hz" % k_fx)
    print("  视角B Fz: 全段 %.4f N/Hz | θ(f) %+.4f deg/Hz"
          % (k_ret, k_ret_th))
    print("  ★结论: 新工作点 ∂Fz/∂f %s（%s）"
          % ("<0 为负" if k_full < 0 else ">0 为正",
             "升频减升力" if k_full < 0 else "升频增升力"))
    print("  JSON → %s" % jpath)


if __name__ == "__main__":
    main()
