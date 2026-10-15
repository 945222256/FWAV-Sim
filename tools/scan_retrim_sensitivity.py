# -*- coding: utf-8 -*-
"""
scan_retrim_sensitivity.py — 俯仰矩攻角敏感度重配平扫描（2026-09-30
转弯线方向一，R58 定案的考卷）。

背景：R58 在环实录与力矩分解定案——盘旋中机翼俯仰矩随有效攻角
漂移持续漂移，幅度超尾舵满偏配平权限两倍，配平点处于高敏区，
俯仰率先失稳拖垮滚转。本工具在波形参数域内搜索配平矩小且俯仰矩
对攻角敏感度低的替代工作点，供科目一工作点迁移。

判据（先于数据）：
  1. 竖直残差 |Fz−W|/W ≤ 2%；
  2. 前向力配平（Fx=0 解配平俯仰，双阱取竖直残差最小根）；
  3. 静态配平矩 |My| 尾舵权限内（0.07 N·m 满偏口径）；
  4. ★敏感度：dMy/dvz（竖直速度摄动 ±0.1 m/s 的俯仰矩变化率，
     N·m 每 m/s），现行工作点基准值先算，候选须低于基准；
  5. 静态口径（ω=0）边界声明与测量纪律三条同扩域轮。

用法：python tools/scan_retrim_sensitivity.py
产物：logs/scan_retrim_sensitivity.json
"""
import os
import sys
import json
import math
import datetime
import importlib.util

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
FK = cfg.FlapKinematicsConfig
W = cfg.MASS * 9.81

model = v1.SaneDickinson3DModel()
T0 = [500000.0]
N_PHASE = 144   # 粗扫每周期 144 点（测量纪律：粗扫可用 72，终值 288）

# 轴（粗扫，聚焦敏感度）：MEAN 偏置 × 拍频 × 攻角 × 分裂比
MEAN_GRID = [0.0, 5.0, 10.0, 15.0, 20.0]          # deg
FREQ_GRID = [1.6, 1.8, 2.0, 2.2]                   # Hz
ALPHA_GRID = [35.0, 40.0]                          # deg
SP_GRID = [float(FK.SPLIT)]                        # 现行值
LAG, AMP, V = float(FK.PITCH_LAG_S), float(FK.AMPLITUDE), 3.95


def set_wave():
    A0 = math.radians(AMP)
    for w, sgn in ((model.wing_left, +1.0), (model.wing_right, -1.0)):
        w.alpha_set = math.radians(ALPHA_GRID[0])
        w.mean = math.radians(MEAN_GRID[0])
        w.split = SP_GRID[0]
        w.amplitude = A0
        w.pitch_lag_s = LAG


def eval_point(mean_deg, freq, alpha_deg, split, vz, n=N_PHASE):
    """整拍平均 (Fz, Fx, My)；vz 为世界竖直速度（攻角摄动通道）。"""
    model.set_wing_command(frequency=freq)
    A0 = math.radians(AMP)
    for w, sgn in ((model.wing_left, +1.0), (model.wing_right, -1.0)):
        w.alpha_set = math.radians(alpha_deg)
        w.mean = math.radians(mean_deg)
        w.split = split
        w.amplitude = A0
        w.pitch_lag_s = LAG
    T = 1.0 / freq
    F = np.zeros(3); Tq = np.zeros(3)
    quat = v1.euler_to_quat(0.0, 0.0, 0.0)   # 配平俯仰由外层二分解出
    for k in range(1):
        pass
    return None  # 占位，真实现见 eval_trim（配平俯仰内层二分）


def eval_trim(mean_deg, freq, alpha_deg, split, pitch_rad, vz, n=N_PHASE):
    model.set_wing_command(frequency=freq)
    A0 = math.radians(AMP)
    for w, sgn in ((model.wing_left, +1.0), (model.wing_right, -1.0)):
        w.alpha_set = math.radians(alpha_deg)
        w.mean = math.radians(mean_deg)
        w.split = split
        w.amplitude = A0
        w.pitch_lag_s = LAG
    quat = v1.euler_to_quat(0.0, pitch_rad, 0.0)
    vel = np.array([V, 0.0, vz])
    om = np.zeros(3)
    T = 1.0 / freq
    F = np.zeros(3); Tq = np.zeros(3)
    for i in range(n):
        t = T0[0] + i * T / n
        R_bw = v1.quat_to_rotation_matrix(quat)
        v_body = R_bw.T @ vel
        om_body = R_bw.T @ om
        wsL = model.wing_left.evaluate(t, phase_offset_rad=0.0)
        FL, TL = model._wing_forces(+model._r_centers.copy(), model._chords,
                                    wsL, v_body, om_body)
        wsR = model.wing_right.evaluate(t, phase_offset_rad=model.phase_offset_lr)
        FR, TR = model._wing_forces(-model._r_centers.copy(), model._chords,
                                    wsR, v_body, om_body)
        F += FL + FR
        Tq += TL + TR
    T0[0] += T
    return F / n, Tq / n


def solve_pitch(mean_deg, freq, alpha_deg, split, vz,
                lo=-0.5, hi=0.5, tol=0.02, n=N_PHASE):
    """Fx=0 二分配平俯仰（粗扫变号+二分，取竖直残差最小根）。"""
    def fx(th):
        F, _ = eval_trim(mean_deg, freq, alpha_deg, split, th, vz, n)
        return F[0]
    grid = np.linspace(lo, hi, 11)
    vals = [fx(th) for th in grid]
    roots = []
    for i in range(len(grid) - 1):
        if vals[i] == 0 or vals[i] * vals[i + 1] < 0:
            a, b = grid[i], grid[i + 1]
            for _ in range(24):
                m = 0.5 * (a + b)
                if fx(a) * fx(m) <= 0:
                    b = m
                else:
                    a = m
            roots.append(0.5 * (a + b))
    if not roots:
        return None
    best = None
    for r in roots:
        F, _ = eval_trim(mean_deg, freq, alpha_deg, split, r, vz)
        res = abs(F[2] - (-W)) if False else abs(F[2] * math.cos(r) - W)
        if best is None or res < best[1]:
            best = (r, res)
    return best


def point_metrics(mean_deg, freq, alpha_deg, split):
    """配平俯仰+残差+My+敏感度 dMy/dvz。"""
    sol = solve_pitch(mean_deg, freq, alpha_deg, split, 0.0)
    if sol is None:
        return None
    th, res = sol
    if res / W > 0.04:
        return None  # 粗扫竖直残差线 4%（2% 为终值线）
    _, Tq0 = eval_trim(mean_deg, freq, alpha_deg, split, th, 0.0)
    _, Tqp = eval_trim(mean_deg, freq, alpha_deg, split, th, +0.1)
    _, Tqm = eval_trim(mean_deg, freq, alpha_deg, split, th, -0.1)
    dmy = (Tqp[1] - Tqm[1]) / 0.2
    return dict(mean_deg=mean_deg, freq=freq, alpha_deg=alpha_deg,
                split=split, pitch_rad=th, res_rel=res / W,
                My=float(Tq0[1]), dMy_dvz=float(dmy))


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # config 的 MEAN/ALPHA_SET 为度值，直接使用（2026-09-30 初版曾误
    # 再做弧度转换，基准与全部网格点因此作废一轮）
    base = point_metrics(float(FK.MEAN), float(FK.FREQUENCY),
                         float(FK.ALPHA_SET), float(FK.SPLIT))
    print("现行工作点基准：")
    if base:
        print("  MEAN=%.1f° f=%.2f α=%.0f° 配平俯仰=%.2f° |My|=%.4f "
              "dMy/dvz=%+.4f N·m/(m/s)"
              % (base["mean_deg"], base["freq"], base["alpha_deg"],
                 math.degrees(base["pitch_rad"]), abs(base["My"]),
                 base["dMy_dvz"]))
    out = {"ts": ts, "baseline": base, "rows": []}
    print("\n粗扫（判据：|My|≤0.07 且 |dMy/dvz| 低于基准）：")
    for mean in MEAN_GRID:
        for f in FREQ_GRID:
            for al in ALPHA_GRID:
                for sp in SP_GRID:
                    m = point_metrics(mean, f, al, sp)
                    if m is None:
                        continue
                    out["rows"].append(m)
                    ok = (abs(m["My"]) <= 0.07
                          and abs(m["dMy_dvz"]) < abs(base["dMy_dvz"]))
                    if ok:
                        print("  候选 MEAN=%5.1f° f=%.2f α=%.0f° "
                              "俯仰=%6.2f° |My|=%.4f dMy/dvz=%+.4f %s"
                              % (mean, f, al, math.degrees(m["pitch_rad"]),
                                 abs(m["My"]), m["dMy_dvz"],
                                 "✓" if ok else ""))
    path = os.path.join(PROJECT_ROOT, "logs", "scan_retrim_sensitivity.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n产物:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
