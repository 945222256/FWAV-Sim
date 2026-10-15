# -*- coding: utf-8 -*-
"""_i004_trim_compare.py — I-004 两个配平标定工具同工况对照驱动（2026-09-30）。

用途：在同一进程内分别调用两个既有标定器的求值内核，对两个工况各求一次
配平俯仰角，并输出隔离口径差异所需的辅助根与交叉读数。本脚本只读与打印，
不写任何文件，不修改任何既有代码。

两个被对照工具（身份出处：提交 50a137a 五十八班记档、docs/第六十二班
_闭环核对账与修正.md 第三节附带定位、docs/第七十一班_邻域重扫与转弯专项.md
第一节 1.3 与第二节发现一）：
  工具甲 = tools/unified_search_framework.py（v1 标定器），
           入口 calibrate_waveform，求根函数 trim_pitch 解 Fx(θ)=0；
  工具乙 = tools/full_search_50.py（五十班考卷标定器），
           入口 calibrate，解 M(θ)=0（俯仰力矩零根，准根族时取全程
           曲线最小 |M| 网格点，为该工具自带行为）。

两个工况（全部取代码默认值，不临时造数）：
  工况一（现行巡航工作点）：波形与速度取 config/sim_config.py 生效默认值
    （FlapKinematicsConfig 五键 + FlightControllerConfig.CRUISE_VX，与
    tools/neighborhood_rescan.py 的 RUNB_WAVE 同源）；
  工况二（五十八班记档工况）：v1 锚点函数 anchor_tests 内硬编码的五十班
    最优组波形 p50（二十二度记档的原工况，逐字取自该函数源码）。

每工况输出：
  甲、工具甲配平俯仰角（前向力零根）；
  乙、工具乙配平俯仰角（俯仰力矩零根）；
  丙、辅助根一：工具乙口径 cycle_eval 的 Fx(θ) 零根（世界系全额尾翼的
      前向力根，用于把“目标定义差”与“尾翼除 288 缺陷”分解开）；
  丁、辅助根二：工具甲口径 stroke_avg 的 My(θ) 零根（体轴翼矩加尾矩除
      288 的力矩根，与乙对照隔离尾翼矩漏账）；
  戊、辅助根三：部署语义参考根（净矩=气动 My+机翼矩直流 MY_DC+重力矩
      闭式 x_cg·W·cosθ 的零根，第六十五至六十八班与七十一班 1.3 的
      部署配平口径，x_cg 自 params_registry 读取）；
  己、各根处两口径的 Fx 与 My 交叉读数（供常数偏移核对）。

测量纪律：求根终值一律用每周期 288 点相位采样；粗扫找变号区间允许
72 点（docs/交接说明_纪律与下一步.md 第一节第 5 条）。
"""
import importlib.util
import math
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config.sim_config as cfg


def _load_module(name, relpath):
    path = os.path.join(PROJECT_ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v1 = _load_module("usf_v1_i004", os.path.join("tools", "unified_search_framework.py"))
fs50 = _load_module("fs50_i004", os.path.join("tools", "full_search_50.py"))

# ── 工况一：现行巡航工作点（config 生效默认值） ──────────────────
K = cfg.FlapKinematicsConfig
RUNB = {
    "alpha_set": K.ALPHA_SET,        # 32.3169 度
    "mean": K.MEAN,                  # 19.8105 度
    "split": K.SPLIT,                # 0.176
    "lag": K.PITCH_LAG_S,            # -0.0674 秒
    "amp": K.AMPLITUDE,              # 17.73 度
    "freq": K.FREQUENCY,             # 1.80 赫兹
    "speed": cfg.FlightControllerConfig.CRUISE_VX,   # 3.95 米每秒
}

# ── 工况二：五十班最优组（v1.anchor_tests 源码内硬编码值逐字转录） ──
P50 = {
    "alpha_set": 30.5749, "mean": 22.0012, "split": 0.1039,
    "lag": -0.0674, "amp": 11.4407, "freq": 1.2383, "speed": 3.8121,
}


def to_fs50_wave(p):
    return {"alpha_set": p["alpha_set"], "mean_deg": p["mean"],
            "split": p["split"], "pitch_lag_s": p["lag"],
            "amplitude_deg": p["amp"], "freq": p["freq"], "V": p["speed"]}


# ── 通用求根：72 点粗扫找变号区间，288 点二分精化 ────────────────
N_COARSE, N_FINE_ROOT = 72, 288


def find_root(fn, lo=-10.0, hi=40.0, n_scan=41):
    xs = np.linspace(lo, hi, n_scan)
    ys = [fn(float(x)) for x in xs]
    brackets = [(xs[i], xs[i + 1]) for i in range(n_scan - 1)
                if ys[i] == 0.0 or ys[i] * ys[i + 1] < 0.0]
    if not brackets:
        j = int(np.argmin(np.abs(np.asarray(ys))))
        return None, float(xs[j]), float(ys[j])
    a, b = brackets[0]
    fa = fn(a)
    for _ in range(60):
        m = 0.5 * (a + b)
        fm = fn(m)
        if fa * fm <= 0.0:
            b = m
        else:
            a, fa = m, fm
        if b - a < 0.02:
            break
    return 0.5 * (a + b), None, None


def report(tag, p):
    print("=" * 72)
    print("工况 %s：%s" % (tag, p))
    print("=" * 72)
    w = to_fs50_wave(p)

    # 甲：v1 标定器（calibrate_waveform，内部 trim_pitch 解 Fx=0）
    calA = v1.calibrate_waveform(dict(p))
    thA = calA["th_star"]

    # 乙：五十班标定器（calibrate，解 My=0；准根族=全程最小 |M| 网格点）
    calB = fs50.calibrate(dict(w))
    if "pruned" in calB:
        print("工具乙返回剪枝：%s" % calB["pruned"])
        thB = None
    else:
        thB = calB["theta_star_deg"]

    # 丙：五十班口径 Fx=0 根（世界系全额尾翼）
    def fx50(th):
        return fs50.cycle_eval(w["V"], w["freq"], th, 0.0, N_FINE_ROOT)["Fx"]
    thC, Cfall, Cval = find_root(fx50)

    # 丁：v1 口径 My=0 根（stroke_avg，尾矩除 288）
    def myV1(th):
        return v1.my_curve_point(dict(p), th)
    thD, Dfall, Dval = find_root(myV1)

    # 戊：部署语义参考根（气动 My + MY_DC + 重力矩闭式）
    x_cg = v1.R_CG_BODY[0]
    W = v1.W
    my_dc = fs50.my_dc_at(w["freq"])

    def myNet(th):
        r = fs50.cycle_eval(w["V"], w["freq"], th, 0.0, N_FINE_ROOT)
        return float(r["My"]) + my_dc + x_cg * W * math.cos(math.radians(th))
    thE, Efall, Eval = find_root(myNet)

    print("\n[甲] v1 标定器 calibrate_waveform：")
    print("     th_star(Fx=0 体轴根) = %+.3f 度   fx0=%+.5f N  fz0=%+.5f N" %
          (thA, calA["fx0"], calA["fz0"]))
    print("     my_star=%+.5f N·m  need=%+.5f N·m  cap=%+.5f N·m  dfx_df=%+.5f N/Hz" %
          (calA["my_star"], calA["need"], calA["cap"], calA["dfx_df"]))

    print("\n[乙] 五十班标定器 calibrate：")
    if thB is None:
        print("     被剪枝，无 theta_star")
    else:
        print("     theta_star(My=0 根) = %+.3f 度   quasi_root=%s" %
              (thB, calB["quasi_root"]))
        print("     FZ0=%+.5f N  FX0=%+.5f N  my_dc=%+.5f N·m" %
              (calB["FZ0"], calB["FX0"], calB["my_dc"]))
        print("     My_res=%+.5f  My_wing_res=%+.5f  My_tail_res=%+.5f N·m" %
              (calB["My_res"], calB["My_wing_res"], calB["My_tail_res"]))
        print("     demand_static=%+.5f N·m" % calB["demand_static"])

    print("\n[丙] 辅助根：五十班口径 Fx=0（世界系全额尾翼）：")
    if thC is not None:
        print("     th = %+.3f 度" % thC)
    else:
        print("     区间内无变号；|Fx| 最小点 %+.3f 度处 Fx=%+.5f N" % (Cfall, Cval))

    print("\n[丁] 辅助根：v1 口径 My=0（尾矩除 288）：")
    if thD is not None:
        print("     th = %+.3f 度" % thD)
    else:
        print("     区间内无变号；|My| 最小点 %+.3f 度处 My=%+.5f N·m" % (Dfall, Dval))

    print("\n[戊] 辅助根：部署语义净矩根（My+MY_DC+重力矩闭式）：")
    print("     x_cg=%+.6f m  W=%.4f N  my_dc=%+.5f N·m" % (x_cg, W, my_dc))
    if thE is not None:
        print("     th = %+.3f 度" % thE)
    else:
        print("     区间内无变号；|净矩| 最小点 %+.3f 度处净矩=%+.5f N·m" % (Efall, Eval))

    # 己：交叉读数（在甲、乙、丙三根处取两口径的 Fx 与 My）
    print("\n[己] 交叉读数：")
    m50, tail50 = fs50._model_pair()
    for label, th in (("甲根 thA", thA), ("乙根 thB", thB), ("丙根 thC", thC)):
        if th is None:
            continue
        r50 = fs50.cycle_eval(w["V"], w["freq"], th, 0.0, N_FINE_ROOT)
        Fv1, Tv1 = v1.stroke_avg(dict(p), th)
        quat50 = fs50._TRUE["e2q"](0.0, math.radians(th), 0.0)
        _Ft, _Tt = tail50.compute(quat50, np.array([w["V"], 0.0, 0.0]),
                                  v_slip=fs50.SLIP_PER_HZ * w["freq"])
        print("     %s=%+.3f 度：乙口径 Fx=%+.5f N Fz=%+.5f N My=%+.5f N·m | "
              "甲口径 Fx=%+.5f N My=%+.5f N·m | ΔFx=%+.5f N ΔMy=%+.5f N·m | "
              "机制核对 −sinθ·Fz=%+.5f N 尾轴力 Ft_x=%+.5f N 尾矩 Tt_y=%+.5f N·m" %
              (label, th, r50["Fx"], r50["Fz"], r50["My"], float(Fv1[0]),
               float(Tv1[1]), float(r50["Fx"]) - float(Fv1[0]),
               float(r50["My"]) - float(Tv1[1]),
               -math.sin(math.radians(th)) * float(r50["Fz"]),
               float(_Ft[0]), float(_Tt[1])))

    # 差值汇总
    print("\n[差值汇总]")
    if thB is not None:
        print("     乙根 − 甲根 = %+.3f 度（两工具配平俯仰角之差）" % (thB - thA))
    if thC is not None:
        print("     丙根 − 甲根 = %+.3f 度（其中由口径差贡献：世界系全额尾翼"
              "前向力根相对 v1 体轴缺尾根的偏移）" % (thC - thA))
        if thB is not None:
            print("     乙根 − 丙根 = %+.3f 度（其中由配平目标定义贡献：力矩根"
                  "相对前向力根的偏移，同一乙口径内）" % (thB - thC))
    return thA, thB


def main():
    print("I-004 两标定工具同工况对照（新惯量 URDF 生效日 2026-09-30）")
    print("登记库整机惯量 vehicle.inertia.composite.body_frame = %s" %
          list(np.diag(v1.I_DIAG)))
    print("登记库质心 vehicle.cg.body_frame = %s" % list(v1.R_CG_BODY))
    print("部署 PITCH_TRIM = %+.6f rad = %+.3f 度" %
          (cfg.FlightControllerConfig.PITCH_TRIM,
           math.degrees(cfg.FlightControllerConfig.PITCH_TRIM)))
    print("说明：两工具标定路径均为纯力/力矩求值，不含惯量项"
          "（grep 核对：I_DIAG 仅入 v1 闭环核 L445；IYY 仅入乙闭环核 L675）。")
    a1, b1 = report("一（现行巡航工作点，config 默认）", RUNB)
    a2, b2 = report("二（五十班最优组，五十八班记档工况）", P50)
    if a2 is not None and b2 is not None:
        print("=" * 72)
        print("二十二度记档复现（工况二）：乙根 %+.3f − 甲根 %+.3f = %+.3f 度"
              % (b2, a2, b2 - a2))
        print("=" * 72)


if __name__ == "__main__":
    main()
