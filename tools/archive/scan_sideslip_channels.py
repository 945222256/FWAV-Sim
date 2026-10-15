# -*- coding: utf-8 -*-
"""
scan_sideslip_channels.py — 侧滑工况横侧通道效能测量（2026-09-27）

目的：回答"侧滑无法恢复"的成因归类——三条横侧通道（尾翼差动、机翼
幅值差动、机翼俯仰滞后差动）在侧滑角逐档工况下，每单位指令能产生
多少滚转力矩与偏航力矩；同时测出各侧滑角下配平所需克服的基线力矩。
瓶颈是权限不足还是耦合未建模，由数据判定，不猜。

口径与纪律：
- 工况为当前生效配置（三维模型升格态，提交 39cfb9a）：波形参数与
  拍频、速度全部从配置读取，不写死。
- 俯仰角按当前波形的配平根求解（粗扫变号加二分），坡度为零。
- 侧滑以横向速度分量表达（体轴速度带 vy），与既有对照测试同口径。
- 测量纪律三条沿用：时间轴全局单调推进、显式设频、每周期采样 288 点。
- 差动施加后立即恢复对称配置；时机差动超出正负零点零二秒的行标注
  为外推域（通道选型的实测域为正负零点零二秒）。
- 镜像检查：基线力矩在正负侧滑角下应满足镜像反对称。

用法： python tools/scan_sideslip_channels.py（纯 NumPy，无需 Isaac）
产物： logs/scan_sideslip_channels.json + docs/侧滑工况横侧通道效能.md
"""
import os, sys, json, math, datetime, importlib.util
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from config import sim_config as cfg


def _load_v1():
    path = os.path.join(PROJECT_ROOT, "tools", "unified_search_framework.py")
    spec = importlib.util.spec_from_file_location("usf_v1", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v1 = _load_v1()

# 当前生效波形与工况（升格态配置，不写死）
FK = cfg.FlapKinematicsConfig
WAVE = dict(alpha_set=float(FK.ALPHA_SET), mean=float(FK.MEAN),
            split=float(FK.SPLIT), lag=float(FK.PITCH_LAG_S),
            amp=float(FK.AMPLITUDE), freq=float(FK.FREQUENCY),
            speed=float(getattr(cfg.FlightControllerConfig, "CRUISE_VX",
                                3.95)))

model = v1.SaneDickinson3DModel()
tail = v1.TailFuselageAero()
T0 = [500000.0]   # 全局单调推进时基
N_PHASE = 288

BETA_GRID = [-12.0, -8.0, -4.0, 0.0, 4.0, 8.0, 12.0]          # 侧滑角（度）
DA_GRID = [-0.175, -0.10, -0.05, 0.0, 0.05, 0.10, 0.175]      # rad
DLAG_GRID = [-0.04, -0.02, -0.01, 0.0, 0.01, 0.02, 0.04]      # s
AIL_GRID = [-0.10, -0.05, 0.0, 0.05, 0.10]                    # rad


def set_wave(da=0.0, dlag=0.0):
    A0 = math.radians(WAVE["amp"])
    for w, sgn in ((model.wing_left, +1.0), (model.wing_right, -1.0)):
        w.alpha_set = math.radians(WAVE["alpha_set"])
        w.mean = math.radians(WAVE["mean"])
        w.split = WAVE["split"]
        w.amplitude = A0 + sgn * da
        w.pitch_lag_s = WAVE["lag"] + sgn * dlag


def pinned_eval(beta_deg, th_rad, da=0.0, dlag=0.0, ail=0.0, n=N_PHASE):
    """钉固状态整周期平均总力与总力矩（体坐标系力矩关于重心）。"""
    f = WAVE["freq"]
    model.set_wing_command(frequency=f)
    set_wave(da, dlag)
    quat = v1.euler_to_quat(0.0, th_rad, 0.0)
    beta = math.radians(beta_deg)
    vel = np.array([WAVE["speed"] * math.cos(beta),
                    WAVE["speed"] * math.sin(beta), 0.0])
    om_w = np.zeros(3)
    T = 1.0 / f
    F = np.zeros(3)
    Tq = np.zeros(3)
    for i in range(n):
        t = T0[0] + i * T / n
        Fw, Tw = _wing_pair(quat, vel, om_w, t)
        F += Fw
        Tq += Tw
    T0[0] += T
    tail.set_command(aileron=ail)
    Ft, Tt = tail.compute(quat, vel,
                          v_slip=cfg.SLIP_K * cfg.SLIP_V_PER_HZ * f)
    R_wb = v1.quat_to_rotation_matrix(quat).T
    set_wave(0.0, 0.0)
    # 翼为 n 相位采样求和需除 n；尾翼单次求值不除（2026-09-27 缺陷修复：
    # 初版 (F+Ft)/n 把尾翼缩小了 n 倍；同一写法亦存在于 v1 stroke_avg，
    # 已另行上报，v1 按约定不动）
    return F / n + Ft, Tq / n + R_wb @ Tt


def _wing_pair(quat, vel_world, om_world, t):
    """双翼合力与合力矩（体坐标系），调用口径与主框架一致。"""
    R_bw = v1.quat_to_rotation_matrix(quat)
    v_body = R_bw.T @ vel_world
    om_body = R_bw.T @ om_world
    wsL = model.wing_left.evaluate(t, phase_offset_rad=0.0)
    FL, TL = model._wing_forces(+model._r_centers.copy(), model._chords,
                                wsL, v_body, om_body)
    wsR = model.wing_right.evaluate(t, phase_offset_rad=model.phase_offset_lr)
    FR, TR = model._wing_forces(-model._r_centers.copy(), model._chords,
                                wsR, v_body, om_body)
    return FL + FR, TL + TR


def fit_slope(xs, ys):
    """过原点线性拟合斜率与决定系数。"""
    xs = np.asarray(xs)
    ys = np.asarray(ys)
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
    # 配平俯仰根（当前波形、当前工况）
    th_star = math.radians(v1.trim_pitch(dict(WAVE)))
    out = {"ts": ts, "wave": WAVE, "th_star_deg": math.degrees(th_star),
           "beta_grid": BETA_GRID, "n_phase": N_PHASE}

    # 第一步：基线（对称配置）在各侧滑角下的力矩——需要被克服的量
    base = []
    for beta in BETA_GRID:
        F, Tq = pinned_eval(beta, th_star)
        base.append(dict(beta=beta, Tx=float(Tq[0]), Ty=float(Tq[1]),
                         Tz=float(Tq[2]), Fy=float(F[1]), Fz=float(F[2])))
    out["baseline"] = base
    mirror_err = max(abs(b["Tz"] + c["Tz"]) for b, c in
                     zip(base, reversed(base)))
    out["mirror_check_max_err_Tz"] = mirror_err

    # 第二步：三条通道逐侧滑角的效能（力矩响应对指令量的过原点斜率）
    channels = {}
    for name, grid in (("aileron", AIL_GRID), ("dA", DA_GRID),
                       ("dlag", DLAG_GRID)):
        rows = []
        for beta in BETA_GRID:
            _, T0q = pinned_eval(beta, th_star)
            dtx, dtz = [], []
            for d in grid:
                kw = {"ail": d} if name == "aileron" else \
                    {"da": d} if name == "dA" else {"dlag": d}
                _, Tq = pinned_eval(beta, th_star, **kw)
                dtx.append(float(Tq[0] - T0q[0]))
                dtz.append(float(Tq[2] - T0q[2]))
            b_roll, r2_roll = fit_slope(grid, dtx)
            b_yaw, r2_yaw = fit_slope(grid, dtz)
            rows.append(dict(beta=beta, B_roll=b_roll, B_yaw=b_yaw,
                             r2_roll=r2_roll, r2_yaw=r2_yaw,
                             dtx_max=max(abs(x) for x in dtx),
                             dtz_max=max(abs(x) for x in dtz)))
        channels[name] = rows
    out["channels"] = channels

    with open(os.path.join(cfg.LOG_DIR, "scan_sideslip_channels.json"),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    md = [f"# 侧滑工况横侧通道效能（{ts}）", "",
          f"- 工况：当前生效配置（升格态，提交 39cfb9a）；"
          f"配平俯仰 {math.degrees(th_star):+.2f}°；"
          f"速度 {WAVE['speed']} m/s；拍频 {WAVE['freq']} Hz",
          "- 口径：钉固状态整周期平均；力矩为体坐标系、关于重心；"
          "时效能为力矩响应相对指令量的过原点斜率",
          f"- 镜像检查：基线偏航力矩镜像最大残差 {mirror_err:.5f} N·m",
          "- 原始数据：`logs/scan_sideslip_channels.json`", "",
          "## 基线：各侧滑角下需克服的力矩（对称配置）", "",
          "| 侧滑角 | 滚转力矩 (N·m) | 偏航力矩 (N·m) | 侧向力 (N) |",
          "|---|---|---|---|"]
    for b in base:
        md.append(f"| {b['beta']:+.0f}° | {b['Tx']:+.4f} | {b['Tz']:+.4f} |"
                  f" {b['Fy']:+.3f} |")
    for name, label, unit in (("aileron", "尾翼差动", "N·m/rad"),
                              ("dA", "机翼幅值差动", "N·m/rad"),
                              ("dlag", "机翼俯仰滞后差动", "N·m/s")):
        md += ["", f"## {label}效能（{unit}）", "",
               "| 侧滑角 | 滚转效能 | 偏航效能 | 线性度 R²（滚转） |"
               " 最大滚转响应 (N·m) |",
               "|---|---|---|---|---|"]
        for r in channels[name]:
            md.append(f"| {r['beta']:+.0f}° | {r['B_roll']:+.4f} |"
                      f" {r['B_yaw']:+.4f} | {r['r2_roll']:.3f} |"
                      f" {r['dtx_max']:.4f} |")
    md += ["", "## 判读", "",
           "- 基线表给出各侧滑角下需要被克服的滚转与偏航力矩；通道表"
           "给出每单位指令能提供的力矩；两者相除即所需指令量，与执行域"
           "对照即知权限是否足够。",
           "- 线性度 R² 低于零点九的行说明该工况下响应非线性显著，"
           "引用斜率时须带工况。",
           "- 时机差动超出正负零点零二秒的行属外推域，引用前需补考核"
           "工况。", ""]
    doc = os.path.join(PROJECT_ROOT, "docs", "侧滑工况横侧通道效能.md")
    with open(doc, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[sideslip] 完成 → {doc}", flush=True)
    for name in ("aileron", "dA", "dlag"):
        r0 = channels[name][3]
        print(f"[sideslip] β=0 {name}: B_roll={r0['B_roll']:+.4f} "
              f"B_yaw={r0['B_yaw']:+.4f} R²={r0['r2_roll']:.3f}", flush=True)


if __name__ == "__main__":
    main()
