# -*- coding: utf-8 -*-
"""
scan_bank_attribution.py — 科目一失败归因测量（交接说明第一步执行工具）

目的：把《压坡度转弯试验_科目一结果》登记的三条归因候选定到一条或
明确排除。三条候选：
候选一：滚转环在持续坡度指令下与横侧增长模态耦合（模态被持续激励）。
候选二：拍频顶上限后工作点进入升力-频率斜率变号区（工作点失效）。
候选三：偏航抵消组合通道的混合比值在坡度姿态下不再抵消（比值失效）。

方法（离线钉固，不飞）：骨架 tools/scan_sideslip_channels.py 复制新增
坡度与拍频两个维度，原版不动。维度：坡度 {0, 10} 度 × 拍频 {1.94,
2.38, 3.0, 3.5} 赫兹（科目一实测时间线档：1.94 为巡航段、2.38 为爬升
段、3.50 为顶限段）× 侧滑 {−12..+12} 度网格。每组测基线力矩与三条
通道效能。对照组（坡度零度）用本工具重测，不引用旧文档数值，保证
同口径。

通道网格收回已验证域（与骨架的差异，骨架 dlag 网格 ±0.04 秒属外推
域且 R²=0.003~0.010 已污染斜率；本工具用 ±0.02 秒已验证域，幅值差动
±0.10 弧度、尾翼差动 ±0.10 弧度与骨架一致）。

判定条件（先于数据写定，运行后不改）：
候选一支持：坡度十度基线滚转力矩对侧滑斜率比坡度零度增大超过
百分之五十；或交叉项 dTz/dβ 增大超过百分之五十。排除：斜率变化
小于百分之二十且三通道滚转效能保持（变化小于百分之三十）。
候选二支持：拍频三点五赫兹时周期平均竖直力与体重之比低于零点九五
（工作点升力塌）或三通道滚转效能相对一点九四赫兹平均降幅超过百分
之五十。排除：升力比不低于零点九五且效能降幅小于百分之二十。
候选三支持：坡度十度下幅值差动偏航效能与滞后差动偏航效能之比偏离
负一超过百分之三十。排除：比值在负一的正负百分之十五内。

口径与纪律：波形参数从配置读取；配平俯仰用 v1.trim_pitch（前向力
根，与骨架同口径；报告注明与力矩根口径的差异）；测量纪律三条沿用
（时基单调、显式设频、每周期二百八十八点）；尾翼单次求值不除 n
（骨架缺陷修复版写法）；差动施加后立即恢复对称配置。

写权限：主会话亲写；代理只执行。运行：python tools/scan_bank_attribution.py
产物：logs/scan_bank_attribution_<ts>.json + docs/压坡度转弯_归因测量.md
"""
import os, sys, json, math, datetime, importlib.util
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from config import sim_config as cfg


def _load_v1():
    path = os.path.join(PROJECT_ROOT, "tools", "unified_search_framework.py")
    spec = importlib.util.spec_from_file_location("usf_v1_attr", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v1 = _load_v1()

FK = cfg.FlapKinematicsConfig
WAVE0 = dict(alpha_set=float(FK.ALPHA_SET), mean=float(FK.MEAN),
             split=float(FK.SPLIT), lag=float(FK.PITCH_LAG_S),
             amp=float(FK.AMPLITUDE), freq=float(FK.FREQUENCY),
             speed=float(getattr(cfg.FlightControllerConfig, "CRUISE_VX", 3.95)))

model = v1.SaneDickinson3DModel()
tail = v1.TailFuselageAero()
T0 = [600000.0]
N_PHASE = 288

PHI_GRID = [0.0, 10.0]
FREQ_GRID = [1.94, 2.38, 3.0, 3.5]
BETA_GRID = [-12.0, -8.0, -4.0, 0.0, 4.0, 8.0, 12.0]
DA_GRID = [-0.10, -0.05, 0.0, 0.05, 0.10]
DLAG_GRID = [-0.02, -0.01, 0.0, 0.01, 0.02]
AIL_GRID = [-0.10, -0.05, 0.0, 0.05, 0.10]


def set_wave(da=0.0, dlag=0.0):
    A0 = math.radians(WAVE0["amp"])
    for w, sgn in ((model.wing_left, +1.0), (model.wing_right, -1.0)):
        w.alpha_set = math.radians(WAVE0["alpha_set"])
        w.mean = math.radians(WAVE0["mean"])
        w.split = WAVE0["split"]
        w.amplitude = A0 + sgn * da
        w.pitch_lag_s = WAVE0["lag"] + sgn * dlag


def pinned_eval(phi_deg, beta_deg, th_rad, freq, da=0.0, dlag=0.0,
                ail=0.0, n=N_PHASE):
    """钉固状态整周期平均总力与总力矩（体坐标系力矩）。来流=世界系
    水平速度绕竖直轴偏转（与骨架同口径）。"""
    model.set_wing_command(frequency=freq)
    set_wave(da, dlag)
    quat = v1.euler_to_quat(math.radians(phi_deg), th_rad, 0.0)
    beta = math.radians(beta_deg)
    vel = np.array([WAVE0["speed"] * math.cos(beta),
                    WAVE0["speed"] * math.sin(beta), 0.0])
    om_w = np.zeros(3)
    T = 1.0 / freq
    F = np.zeros(3)
    Tq = np.zeros(3)
    for i in range(n):
        t = T0[0] + i * T / n
        R_bw = v1.quat_to_rotation_matrix(quat)
        v_body = R_bw.T @ vel
        wsL = model.wing_left.evaluate(t, phase_offset_rad=0.0)
        FL, TL = model._wing_forces(+model._r_centers.copy(), model._chords,
                                    wsL, v_body, np.zeros(3))
        wsR = model.wing_right.evaluate(t, phase_offset_rad=model.phase_offset_lr)
        FR, TR = model._wing_forces(-model._r_centers.copy(), model._chords,
                                    wsR, v_body, np.zeros(3))
        F += FL + FR
        Tq += TL + TR
    T0[0] += T
    tail.set_command(aileron=ail)
    Ft, Tt = tail.compute(quat, vel,
                          v_slip=cfg.SLIP_K * cfg.SLIP_V_PER_HZ * freq)
    set_wave(0.0, 0.0)
    return F / n + Ft, Tq / n + v1.rot_world_to_body(quat, Tt)


def fit_slope(xs, ys):
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    denom = float(np.sum(xs * xs))
    if denom < 1e-12:
        return 0.0, 0.0
    slope = float(np.sum(xs * ys) / denom)
    resid = ys - slope * xs
    ss_tot = float(np.sum(ys * ys))
    r2 = 1.0 - float(np.sum(resid * resid)) / ss_tot if ss_tot > 1e-12 else 1.0
    return slope, r2


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("== 科目一归因测量：坡度×拍频×侧滑 钉固扫描 ==", flush=True)
    out = {"ts": ts, "tool": "tools/scan_bank_attribution.py",
           "wave_base": WAVE0, "grids": {"phi": PHI_GRID, "freq": FREQ_GRID,
                                         "beta": BETA_GRID},
           "criteria": {"c1": "支持:坡度10基线dTx/dβ比0°增大>50%或dTz/dβ增大>50%;"
                              "排除:斜率变化<20%且通道滚转效能变化<30%",
                        "c2": "支持:f=3.5升力比<0.95或通道效能平均降幅>50%;"
                              "排除:升力比>=0.95且降幅<20%",
                        "c3": "支持:坡度10两通道偏航效能比偏离-1超±30%;"
                              "排除:比值在-1±15%内"}}
    results = []
    trim_roots = {}
    for phi in PHI_GRID:
        for f in FREQ_GRID:
            w = dict(WAVE0); w["freq"] = f
            th_deg = v1.trim_pitch(w)
            # 配平根存在性检查（2026-09-27 首轮实测发现）：f=3.0/3.5 档
            # 前向力在窗口内无变号根，trim_pitch 回退窗口端点 45 度（病态
            # 姿态：尾翼滑流响应被放大、Fz/W 虚高）。处置：回退时改用
            # 水平姿态配平根 -2.45 度作钉固姿态锚并标记 root_status，
            # 前向力恒正本身即候选二的直接证据（推力过剩、速度发散）。
            root_status = "ok"
            if abs(th_deg) > 40.0:
                root_status = "no_fx_root_use_-2.45_anchor"
                th_deg = -2.45
            trim_roots["phi%g_f%g" % (phi, f)] = [th_deg, root_status]
            th_rad = math.radians(th_deg)
            for beta in BETA_GRID:
                F, Tq = pinned_eval(phi, beta, th_rad, f)
                rec = {"phi": phi, "freq": f, "beta": beta,
                       "th_root_deg": th_deg,
                       "Tx": float(Tq[0]), "Tz": float(Tq[2]),
                       "Fy": float(F[1]), "Fz": float(F[2]),
                       "Fz_over_W": float(F[2]) / v1.W}
                # 通道效能（过原点斜率，各网格含零点）
                for name, grid in (("ail", AIL_GRID), ("dA", DA_GRID),
                                   ("dlag", DLAG_GRID)):
                    _, T0q = pinned_eval(phi, beta, th_rad, f)
                    dtx, dtz = [], []
                    for d in grid:
                        kw = ({"ail": d} if name == "ail" else
                              {"da": d} if name == "dA" else {"dlag": d})
                        _, Tq2 = pinned_eval(phi, beta, th_rad, f, **kw)
                        dtx.append(float(Tq2[0] - T0q[0]))
                        dtz.append(float(Tq2[2] - T0q[2]))
                    b_roll, r2r = fit_slope(grid, dtx)
                    b_yaw, r2y = fit_slope(grid, dtz)
                    rec["B_roll_" + name] = b_roll
                    rec["B_yaw_" + name] = b_yaw
                    rec["r2_roll_" + name] = r2r
                results.append(rec)
                print("phi=%2.0f f=%.2f β=%+3.0f: Tx=%+.4f Tz=%+.4f "
                      "Fz/W=%.3f | ail%+.4f dA%+.4f dlag%+.4f"
                      % (phi, f, beta, rec["Tx"], rec["Tz"], rec["Fz_over_W"],
                         rec["B_roll_ail"], rec["B_roll_dA"],
                         rec["B_roll_dlag"]), flush=True)
    out["trim_roots"] = trim_roots
    out["results"] = results
    with open(os.path.join(cfg.LOG_DIR, "scan_bank_attribution_%s.json" % ts),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("原始数据 → logs/scan_bank_attribution_%s.json" % ts, flush=True)
    print("完成：判定与报告由主会话按先于数据写定的条件执行", flush=True)


if __name__ == "__main__":
    main()
