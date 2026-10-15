# -*- coding: utf-8 -*-
"""
scan_bank_moment.py — 坡度姿态基线滚转/俯仰矩测量（2026-09-29 转弯线方向1）

目的：R26~R35 在环十轮证明盘旋中存在随时间增长的外滚扰矩
（0.008→0.02 N·m，量级与拍动重力矩姿态整流吻合）。本工具离线测两层：
其一，气动叶素在坡度姿态下的整流矩（钉固整拍平均，模型求值）；
其二，拍动重力矩整流（用数据流 joints_json 实测关节轨迹解析积
Σ r_i(t)×(m_i·g_body) 的拍均值，关节质量取 URDF 值）。
产物喂给控制器前馈候选，判据：扰动矩函数化后可前馈对消。

口径：与 scan_sideslip_channels 同款钉固整周期平均（288 点/拍），
波形从生效配置读取；坡度以欧拉 roll 表达，速度沿体轴前向（协调
β=0 与小侧滑 β=-5° 两档）。
用法：python tools/scan_bank_moment.py（纯 NumPy，无需 Isaac）
产物：logs/scan_bank_moment.json
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
FK = cfg.FlapKinematicsConfig
WAVE = dict(alpha_set=float(FK.ALPHA_SET), mean=float(FK.MEAN),
            split=float(FK.SPLIT), lag=float(FK.PITCH_LAG_S),
            amp=float(FK.AMPLITUDE), freq=float(FK.FREQUENCY),
            speed=float(getattr(cfg.FlightControllerConfig, "CRUISE_VX", 3.95)))
model = v1.SaneDickinson3DModel()
tail = v1.TailFuselageAero()
T0 = [500000.0]
N_PHASE = 288

PHI_GRID = [0.0, 5.0, 10.0, 15.0, 20.0]     # 坡度（度）
BETA_SET = [0.0, -5.0]                       # 协调与小侧滑对照
PITCH_TRIM = math.radians(-6.5)              # 巡航配平俯仰（在环实测口径）


def set_wave():
    A0 = math.radians(WAVE["amp"])
    for w, sgn in ((model.wing_left, +1.0), (model.wing_right, -1.0)):
        w.alpha_set = math.radians(WAVE["alpha_set"])
        w.mean = math.radians(WAVE["mean"])
        w.split = WAVE["split"]
        w.amplitude = A0
        w.pitch_lag_s = WAVE["lag"]


def aero_eval(phi_deg, beta_deg, n=N_PHASE):
    f = WAVE["freq"]
    model.set_wing_command(frequency=f)
    set_wave()
    quat = v1.euler_to_quat(math.radians(phi_deg), PITCH_TRIM, 0.0)
    beta = math.radians(beta_deg)
    vel = np.array([WAVE["speed"] * math.cos(beta),
                    WAVE["speed"] * math.sin(beta), 0.0])
    om = np.zeros(3)
    T = 1.0 / f
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


def gravity_moment_eval(phi_deg, joints_rows):
    """用实测关节角轨迹（数据流 joints_json 若干拍）解析积拍均值重力矩。

    翼段质心近似：flap 关节角 φ_j 决定翼展方向 r=(sinφ_j, -cosφ_j, 0)
    乘等效展向质心半径；关节质量取 URDF（flap 连杆 0.02 kg/翼，twist
    连杆 0.03 kg/翼——flapper_15dof.urdf 质量，量级口径）。
    """
    m_flap = 0.02
    r_c = 0.16
    quat = v1.euler_to_quat(math.radians(phi_deg), PITCH_TRIM, 0.0)
    R_bw = v1.quat_to_rotation_matrix(quat)
    g_body = R_bw.T @ np.array([0.0, 0.0, -9.81])
    F_gL = np.array([0.0, 0.0, -m_flap * 9.81])
    F_gR = F_gL.copy()
    Mx_sum = 0.0; My_sum = 0.0; n = 0
    for j in joints_rows:
        fl = math.radians(float(j.get("flap_L", 0.0)))
        fr = math.radians(float(j.get("flap_R", 0.0)))
        tl = math.radians(float(j.get("twist_L", 0.0)))
        tr = math.radians(float(j.get("twist_R", 0.0)))
        # 翼质心（左翼标准系 r>0，含 twist 的 z 抬升近似）
        zL = r_c * math.sin(fl) * math.sin(tl)
        zR = r_c * math.sin(fr) * math.sin(tr)
        rL = np.array([r_c * math.sin(fl), -r_c * math.cos(fl), zL])
        rR = np.array([r_c * math.sin(fr), +r_c * math.cos(fr), zR])
        ML = np.cross(rL, F_gL)
        MR = np.cross(rR, F_gR)
        Mx_sum += ML[0] + MR[0]
        My_sum += ML[1] + MR[1]
        n += 1
    return Mx_sum / max(n, 1), My_sum / max(n, 1), n


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"ts": ts, "wave": WAVE, "pitch_trim_deg":
           math.degrees(PITCH_TRIM), "rows": []}
    print("坡度基线矩扫描（气动叶素整拍平均，体轴，关于重心）")
    print("φ°    β°    Mx气动    My气动    Mz气动")
    for phi in PHI_GRID:
        for beta in BETA_SET:
            F, Tq = aero_eval(phi, beta)
            out["rows"].append(dict(phi_deg=phi, beta_deg=beta,
                                    Mx=float(Tq[0]), My=float(Tq[1]),
                                    Mz=float(Tq[2]),
                                    Fz=float(F[2])))
            print("%5.1f %5.1f %+9.5f %+9.5f %+9.5f"
                  % (phi, beta, Tq[0], Tq[1], Tq[2]))
    path = os.path.join(PROJECT_ROOT, "logs", "scan_bank_moment.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("产物:", path)
    # 拟合 Mx0(φ)（β=0 协调档）：线性+二次两项，供前馈用
    rows0 = [r for r in out["rows"] if r["beta_deg"] == 0.0]
    ph = np.radians([r["phi_deg"] for r in rows0])
    mx = np.array([r["Mx"] for r in rows0])
    A = np.stack([ph, ph ** 2], axis=1)
    coef, *_ = np.linalg.lstsq(A, mx, rcond=None)
    print("Mx0(φ) ≈ %+.5f·φ + %+.5f·φ²  (N·m, φ弧度)" % (coef[0], coef[1]))
    out["Mx0_fit"] = dict(c1=float(coef[0]), c2=float(coef[1]))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
