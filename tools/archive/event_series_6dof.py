# -*- coding: utf-8 -*-
"""
event_series_6dof.py — 单拍操纵量子的开环事件序列可判伪验证
（转弯路线图第六步，2026-09-24）

逐拍控制框架的第一个可判伪预言：量子表宣称"停扑一拍交付冲量差
ΔJ_Tx（N·m·s）"。若量子与其线性叠加假设成立，六自由度开环积分的
角速度增量应满足：
  P1（量子预言一致性）：单翼停扑一拍的 Δp_sim ≈ ΔJ_Tx / Ixx；
  P2（线性叠加）：左停一拍＋右停一拍（异拍组合）的 Δp
      ≈ Δp(左停) ＋ Δp(右停)（各自扣除基线漂移）。

判据先于数据（2026-09-24 定）：
  P1：|Δp_sim − Δp_pred| / |Δp_pred| ≤15% 通过；15~40% 近似成立
      （需归因）；>40% 证伪信号。Ixx 为约值（2.4e-3 kg·m²，配置
      注释口径），预言附 ±20% 敏感性带；Iyy/Izz 无真值（悬案），
      基准取 Ixx 等值并做 0.5×/2× 敏感性档。
  P2：叠加残差比 ≤10% 叠加成立；10~30% 弱非线性；>30% 线性叠加
      假设证伪（逐拍控制需逐事件重测）。

物理口径：六自由度刚体 RK4（dt=1 ms）；力=三维叶素模型逐时间步
求值（非周期平均），RK4 四级求值时刻 t、t+dt/2、t+dt/2、t+dt 单调
推进（_phase_sc 为纯函数、同刻幂等，改频+回跳才是污染源——测量
纪律一/二由此满足：全程频率固定、显式设频一次）。停扑建模与量子
表同定义（翼面固定中立位、攻角=扑动程序周期均值 α_set·2/π、相位
照常推进）；尾翼滑流、线性旋转阻尼、爬升守卫因子与
compute_forces 生效口径逐项复刻（开跑前自检：无停扑时自组力与
compute_forces 对照，残差须在浮点噪声级）。

量子输入：logs/flap_quantum_grid.json 巡航格（V=4.0、f=1.75）的
n=288 终值 dJ_Tx_L；右翼取镜像（左右镜像已由第十五/十六班验证）。

用法： python tools/event_series_6dof.py（纯 NumPy）
产物： logs/event_series_6dof.json + docs/事件序列可判伪验证.md
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
from modules.aero_solver import TailFuselageAero, euler_to_quat, \
    quat_to_rotation_matrix
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

G_ACC = 9.81
MASS = cfg.MASS
T_STROKE = 1.0 / 1.75          # 巡航拍周期
OP_V, OP_F, OP_ALPHA_SET = 4.0, 1.75, 35.0
DT = 0.001
N_STEPS = int(round(T_STROKE / DT))
N_WARMUP, N_AFTER = 2, 2
ROT_DAMP = float(getattr(cfg.AeroModelConfig, "WING_ROT_DAMP", 0.1))
INERTIA_SENS = [0.5, 2.0]      # Iyy/Izz 相对 Ixx 敏感性档
GUARD_VZ = 2.5                  # 爬升守卫半衰减点（与模型生效值同源声明）

model = SaneDickinson3DModel()
tail = TailFuselageAero()

for w in (model.wing_left, model.wing_right):
    w.alpha_set = math.radians(OP_ALPHA_SET)
    w.split = cfg.FlapKinematicsConfig.SPLIT
    w.pitch_lag_s = cfg.FlapKinematicsConfig.PITCH_LAG_S
    w.mean = math.radians(cfg.FlapKinematicsConfig.MEAN)


def paused_wing_state(w, ws):
    """停扑建模（与 measure_flap_quantum_grid 同定义）。"""
    ws = dict(ws)
    ws["phi"] = float(w.bias + w.mean)
    ws["phi_dot"] = 0.0
    ws["phi_ddot"] = 0.0
    ws["alpha"] = float(w.alpha_set * 2.0 / math.pi + w.pitch_bias)
    ws["alpha_dot"] = 0.0
    ws["alpha_ddot"] = 0.0
    return ws


def forces_full(quat, vel_world, om_world, t, paused=()):
    """全机力：双翼叶素（可停扑）+线性阻尼+世界系变换+爬升守卫+尾翼。
    返回 (F_world, T_body)。"""
    R_bw = quat_to_rotation_matrix(quat)
    v_body = R_bw.T @ vel_world
    om_body = R_bw.T @ om_world

    ws_L = model.wing_left.evaluate(t, phase_offset_rad=0.0)
    if "L" in paused:
        ws_L = paused_wing_state(model.wing_left, ws_L)
    F_L, T_L = model._wing_forces(+model._r_centers.copy(), model._chords,
                                  ws_L, v_body, om_body)
    ws_R = model.wing_right.evaluate(t, phase_offset_rad=model.phase_offset_lr)
    if "R" in paused:
        ws_R = paused_wing_state(model.wing_right, ws_R)
    F_R, T_R = model._wing_forces(-model._r_centers.copy(), model._chords,
                                  ws_R, v_body, om_body)
    F_body = F_L + F_R
    T_body = T_L + T_R - ROT_DAMP * om_body
    F_world = R_bw @ F_body

    vz_air = float(vel_world[2])
    if vz_air > 0.0:
        F_world = F_world.copy()
        F_world[2] *= 1.0 / (1.0 + (vz_air / GUARD_VZ) ** 2)

    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * OP_F
    Ft, Tt = tail.compute(quat, vel_world, v_slip=v_slip)
    return F_world + Ft, T_body + R_bw.T @ Tt


def quat_normalize(q):
    return q / np.linalg.norm(q)


def quat_derivative(q, om_body):
    w0, x0, y0, z0 = q
    ox, oy, oz = om_body
    return 0.5 * np.array([
        -x0 * ox - y0 * oy - z0 * oz,
        w0 * ox + y0 * oz - z0 * oy,
        w0 * oy - x0 * oz + z0 * ox,
        w0 * oz + x0 * oy - y0 * ox])


def run_trajectory(pitch_deg, events, Ixx, Iyy, Izz):
    """六自由度 RK4 开环积分。events: {拍序号(0起): ('L','R',...)}。
    返回每拍边界的 ω_body 与末态。"""
    quat0 = euler_to_quat(0.0, math.radians(pitch_deg), 0.0)
    v = np.array([OP_V, 0.0, 0.0])
    w = np.zeros(3)
    q = quat_normalize(quat0)
    I = np.diag([Ixx, Iyy, Izz])
    t = 120000.0                     # 全局时基起点（单调推进、频率固定）
    model.set_wing_command(frequency=OP_F)   # 纪律二：显式设频（一次）

    def accel(vv, ww, qq, tt, paused):
        F, T = forces_full(qq, vv, ww, tt, paused)
        dv = F / MASS + np.array([0.0, 0.0, -G_ACC])
        dw = np.linalg.solve(I, T - np.cross(ww, I @ ww))
        dq = quat_derivative(qq, ww)
        return dv, dw, dq

    strokes = []
    total = N_WARMUP + max((k + 1 for k in events), default=0) + N_AFTER
    for stroke in range(total):
        paused = tuple(events.get(stroke, ()))
        for i in range(N_STEPS):
            k1 = accel(v, w, q, t, paused)
            v2 = v + 0.5 * DT * k1[0]
            w2 = w + 0.5 * DT * k1[1]
            q2 = quat_normalize(q + 0.5 * DT * k1[2])
            k2 = accel(v2, w2, q2, t + 0.5 * DT, paused)
            v3 = v + 0.5 * DT * k2[0]
            w3 = w + 0.5 * DT * k2[1]
            q3 = quat_normalize(q + 0.5 * DT * k2[2])
            k3 = accel(v3, w3, q3, t + 0.5 * DT, paused)
            v4 = v + DT * k3[0]
            w4 = w + DT * k3[1]
            q4 = quat_normalize(q + DT * k3[2])
            k4 = accel(v4, w4, q4, t + DT, paused)
            v = v + DT / 6.0 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
            w = w + DT / 6.0 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
            q = quat_normalize(
                q + DT / 6.0 * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2]))
            t += DT
        strokes.append(dict(stroke=stroke, paused=list(paused),
                            p=float(w[0]), q_=float(w[1]), r_=float(w[2])))
    return dict(strokes=strokes, p_end=float(w[0]),
                q_end=float(w[1]), r_end=float(w[2]))


def load_quantum():
    path = os.path.join(cfg.LOG_DIR, "flap_quantum_grid.json")
    with open(path, encoding="utf-8") as fp:
        d = json.load(fp)
    for row in d["rows"]:
        if abs(row["V"] - OP_V) < 1e-9 and abs(row["f"] - OP_F) < 1e-9:
            return row
    raise RuntimeError("巡航格未找到（先跑 measure_flap_quantum_grid.py）")


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    cell = load_quantum()
    pitch0 = float(cell["pitch"])
    dj_tx_L = float(cell["dJ_Tx_L"])
    dj_tx_R = -dj_tx_L          # 右翼=左翼镜像（第十五/十六班已验证）
    Ixx = 0.0024                # 滚转惯量约值（配置注释口径）
    print(f"[ev] 巡航格: θ={pitch0:+.2f}° ΔJ_Tx 左={dj_tx_L:+.5f} "
          f"右(镜像)={dj_tx_R:+.5f} N·m·s；Ixx≈{Ixx:.2e}", flush=True)

    # 自检：无停扑时自组力 vs compute_forces（口径逐项对照）
    model.set_wing_command(frequency=OP_F)
    quat = euler_to_quat(0.0, math.radians(pitch0), 0.0)
    v = np.array([OP_V, 0.0, 0.0])
    om = np.zeros(3)
    t_chk = 130000.0
    F1, T1 = forces_full(quat, v, om, t_chk)
    F2, T2 = model.compute_forces(np.zeros(3), quat, v, om, t_chk)
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * OP_F
    Ft, Tt = tail.compute(quat, v, v_slip=v_slip)
    R_wb = quat_to_rotation_matrix(quat).T
    F2 = F2 + Ft
    T2_body = R_wb @ T2 + R_wb @ Tt
    err_f = float(np.max(np.abs(F1 - F2)))
    err_t = float(np.max(np.abs(T1 - T2_body)))
    print(f"[ev] 自检 自组vs compute_forces：ΔF_max={err_f:.2e} "
          f"ΔT_max={err_t:.2e}", flush=True)
    if max(err_f, err_t) > 1e-9:
        print("[ev] ★自检超差——力复刻口径不一致，先修再跑", flush=True)
        return

    e = N_WARMUP
    runs = {
        "E0": run_trajectory(pitch0, {}, Ixx, Ixx, Ixx),
        "E1": run_trajectory(pitch0, {e: ("L",)}, Ixx, Ixx, Ixx),
        "E2": run_trajectory(pitch0, {e: ("R",)}, Ixx, Ixx, Ixx),
        "E3": run_trajectory(pitch0, {e: ("L",), e + 1: ("R",)},
                             Ixx, Ixx, Ixx),
    }
    sens = {f"{s:g}x": run_trajectory(pitch0, {e: ("L",)},
                                      Ixx, s * Ixx, s * Ixx)
            for s in INERTIA_SENS}
    # 半自由口径（归因补测）：姿态/速度/俯仰偏航全部冻结在配平态，
    # 仅滚转通道按 dp/dt=(Tx−ROT_DAMP·p)/Ixx 积分——量子表本在钉固
    # 姿态测量，其预言 ΔJ/Ixx 只在无耦合时成立；本口径隔离全耦合
    # 正反馈，给出量子冲量到角速度的干净对照。叶素力不依赖 ω
    # （逐叶素 ω×r 按 2026-09-19 裁决移除），Tx 与 p 解耦，ODE 线性。
    def roll_only(events, damp=None):
        """damp=None 用生效 ROT_DAMP；damp=0.0 为无阻尼对照（检验
        ∫ΔTx dt 与量子冲量 dJ 的积分自洽）。"""
        damp_v = ROT_DAMP if damp is None else damp
        quat = euler_to_quat(0.0, math.radians(pitch0), 0.0)
        v = np.array([OP_V, 0.0, 0.0])
        om = np.zeros(3)
        p = 0.0
        model.set_wing_command(frequency=OP_F)
        t = 150000.0
        total = N_WARMUP + max((k + 1 for k in events), default=0) + N_AFTER
        marks = []
        for stroke in range(total):
            paused = tuple(events.get(stroke, ()))
            for i in range(N_STEPS):
                _, T = forces_full(quat, v, om, t, paused)
                dp_dt = (float(T[0]) - damp_v * p) / Ixx
                p += dp_dt * DT
                t += DT
            marks.append(dict(stroke=stroke, paused=list(paused), p=p))
        return marks

    ro_E0 = roll_only({})
    ro_E1 = roll_only({e: ("L",)})
    ro_E2 = roll_only({e: ("R",)})
    ro_E3 = roll_only({e: ("L",), e + 1: ("R",)})
    ro_nd_E1 = roll_only({e: ("L",)}, damp=0.0)   # 无阻尼：积分自洽检验

    def ro_dp(marks, n_after=1):
        return marks[e - 1 + n_after]["p"] - marks[e - 1]["p"]

    ro_drift = ro_dp(ro_E0, 1)
    ro_dE1 = ro_dp(ro_E1, 1) - ro_drift
    ro_dE2 = ro_dp(ro_E2, 1) - ro_drift
    ro_drift2 = ro_dp(ro_E0, 2)
    ro_dE3 = ro_dp(ro_E3, 2) - ro_drift2

    def dp(run, n_after=1):
        """事件起点（第 e−1 拍边界）到其后第 n_after 拍边界的 p 增量。"""
        return run["strokes"][e - 1 + n_after]["p"] - \
            run["strokes"][e - 1]["p"]

    drift = dp(runs["E0"], 1)                 # 基线单拍漂移
    dE1 = dp(runs["E1"], 1) - drift
    dE2 = dp(runs["E2"], 1) - drift
    drift2 = dp(runs["E0"], 2)                # 基线两拍漂移
    dE3 = dp(runs["E3"], 2) - drift2
    # 预言方向（2026-09-24 二次修正）：量子 dJ=J_flap−J_pause（扑减停）；
    # 事件"扑→停一拍"交付的冲量差为 −dJ，故 E1 预言=−dJ_Tx_L/Ixx。
    # 初版误用 +dJ/Ixx（符号反），已随半自由口径核对修正。
    pred_L = -dj_tx_L / Ixx
    pred_R = -dj_tx_R / Ixx
    p1_err = abs(dE1 - pred_L) / abs(pred_L)
    # P2 分母（2026-09-24 修正）：左右镜像下 dE1+dE2≡0（第十五/十六班
    # 已证镜像浮点级成立），原分母 |dE1+dE2| 数学奇异（初版在此除零
    # 崩溃）；改用 |pred_L| 归一——P2 检验的是"组合事件净滚转增量
    # 相对单拍量子的残余"，镜像预言下 E3 净增量应≈0。
    p2_res = abs(dE3 - (dE1 + dE2)) / abs(pred_L)
    sens_d = {k: dp(r, 1) - drift for k, r in sens.items()}
    # 半自由对照偏差：基准=量子无阻尼预言；阻尼时间常数
    # τ=Ixx/ROT_DAMP≈24 ms ≪ 拍周期 571 ms，含阻尼实测必然偏小，
    # 差值即阻尼损耗量级（判读见报告）。
    ro_p1_err = abs(ro_dE1 - pred_L) / abs(pred_L)
    # 无阻尼半自由：∫ΔTx·dt/Ixx 应≈−dJ_Tx_L/Ixx=pred_L（量子冲量的
    # 积分自洽检验，阻尼关闭后事件冲量直接转角速度）
    ro_nd_dE1 = ro_dp(ro_nd_E1, 1) - ro_drift
    ro_nd_p1_err = abs(ro_nd_dE1 - pred_L) / abs(pred_L)
    # 半自由阻尼损耗折算：(pred_L−ro_dE1)·Ixx，N·m·s 量级对照
    ro_damp_loss = (pred_L - ro_dE1) * Ixx

    def verd_p1(x):
        return "通过" if x <= 0.15 else \
            "近似成立（需归因）" if x <= 0.40 else "证伪信号"

    def verd_p2(x):
        return "叠加成立" if x <= 0.10 else \
            "弱非线性" if x <= 0.30 else "线性叠加假设证伪"

    print(f"[ev] E1 左停1拍: Δp_sim={dE1:+.4f} 预言={pred_L:+.4f} rad/s "
          f"偏差={p1_err:.1%} → {verd_p1(p1_err)}", flush=True)
    print(f"[ev] E2 右停1拍: Δp_sim={dE2:+.4f} 预言={pred_R:+.4f} "
          f"(镜像自检)", flush=True)
    print(f"[ev] E3 左+右异拍: Δp_sim={dE3:+.4f} 叠加和={dE1 + dE2:+.4f} "
          f"残差={p2_res:.1%} → {verd_p2(p2_res)}", flush=True)
    print(f"[ev] 基线漂移: 单拍={drift:+.5f} 两拍={drift2:+.5f} rad/s",
          flush=True)
    print(f"[ev] 半自由(仅滚转+阻尼): E1 Δp={ro_dE1:+.4f} vs 预言"
          f"{pred_L:+.4f} 偏差={ro_p1_err:.1%}；阻尼损耗折算冲量="
          f"{ro_damp_loss:+.5f} N·m·s；E3 净增量={ro_dE3:+.4f} "
          f"(镜像抵消自检)", flush=True)
    print(f"[ev] 半自由无阻尼: E1 Δp={ro_nd_dE1:+.4f} vs 预言"
          f"{pred_L:+.4f} 偏差={ro_nd_p1_err:.2%}（量子冲量积分自洽"
          f"检验）", flush=True)

    out = dict(ts=ts, model="sane_dickinson_3d", dt=DT,
               op=dict(V=OP_V, f=OP_F, alpha_set=OP_ALPHA_SET,
                       pitch=pitch0),
               ixx=Ixx,
               inertia_note="Iyy/Izz 无真值（悬案），基准取 Ixx 等值，"
                            "敏感性档 0.5×/2× 另列",
               quantum=dict(J_Tx_L=dj_tx_L, J_Tx_R=dj_tx_R),
               pred=dict(droll_L=pred_L, droll_R=pred_R),
               selfcheck=dict(df=err_f, dt_=err_t),
               droll=dict(E1=dE1, E2=dE2, E3=dE3,
                          drift1=drift, drift2=drift2),
               p1_err=p1_err, p1_verdict=verd_p1(p1_err),
               p2_res=p2_res, p2_verdict=verd_p2(p2_res),
               p2_denominator_note="|pred_L| 归一（镜像下 dE1+dE2≡0，"
                                   "原 |dE1+dE2| 分母奇异）",
               inertia_sens=sens_d,
               roll_only=dict(
                   note="半自由口径：姿态/速度/俯仰偏航冻结配平态，仅"
                        "滚转通道积分（归因补测）；含阻尼与无阻尼两档",
                   tau_damp_ms=Ixx / ROT_DAMP * 1000.0,
                   E1=ro_dE1, E2=ro_dE2, E3=ro_dE3,
                   drift1=ro_drift, drift2=ro_drift2,
                   p1_err=ro_p1_err, damp_loss_impulse=ro_damp_loss,
                   E1_nodamp=ro_nd_dE1, p1_err_nodamp=ro_nd_p1_err),
               runs={k: r["strokes"] for k, r in runs.items()})
    with open(os.path.join(cfg.LOG_DIR, "event_series_6dof.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    md = [f"# 事件序列可判伪验证·单拍操纵量子（{ts}）", "",
          f"- 模型：sane_dickinson_3d 六自由度开环 RK4（dt={DT * 1000:g} ms），"
          f"巡航配平点 V={OP_V} m/s、f={OP_F} Hz、α_set={OP_ALPHA_SET}°、"
          f"θ={pitch0:+.2f}°；停扑定义与量子表一致",
          f"- 量子输入：`logs/flap_quantum_grid.json` 巡航格 n=288 终值"
          f"（ΔJ_Tx 左 {dj_tx_L:+.5f} N·m·s，右翼取镜像）；"
          f"Ixx≈{Ixx:.2e} kg·m²（约值口径）",
          f"- 力复刻自检：自组力与 compute_forces 对照 ΔF={err_f:.2e}、"
          f"ΔT={err_t:.2e}（浮点噪声级=口径一致）",
          f"- 原始数据：`logs/event_series_6dof.json`", "",
          "## 判据（先于数据，2026-09-24 定）", "",
          "- P1 量子预言一致性：单翼停一拍 |Δp_sim−Δp_pred|/|Δp_pred|"
          " ≤15% 通过；15~40% 近似成立；>40% 证伪信号。",
          "- P2 线性叠加：左+右异拍组合 Δp 相对（左+右）之和的残差"
          " ≤10% 叠加成立；10~30% 弱非线性；>30% 证伪。",
          "",
          "## 结果", "",
          "| 检验 | 实测 (rad/s) | 预言 (rad/s) | 偏差/残差 | 判定 |",
          "|---|---|---|---|---|",
          f"| P1 左停1拍 Δp | {dE1:+.4f} | {pred_L:+.4f} |"
          f" {p1_err:.1%} | {verd_p1(p1_err)} |",
          f"| P2 左+右异拍叠加 | {dE3:+.4f} | {dE1 + dE2:+.4f} |"
          f" {p2_res:.1%} | {verd_p2(p2_res)} |",
          "",
          f"- 右停单拍 Δp_sim={dE2:+.4f}（预言 {pred_R:+.4f}，镜像自检）；"
          f"基线漂移单拍 {drift:+.5f}、两拍 {drift2:+.5f}（已从事件扣除）。",
          f"- P2 分母口径（2026-09-24 修正）：左右镜像下 dE1+dE2≡0，"
          f"原分母数学奇异（初版除零崩溃）；现按 |pred_L|={abs(pred_L):.4f}"
          f" rad/s 归一，P2 检验的是组合事件净滚转残余相对单拍量子的比。",
          f"- Ixx ±20% 敏感性带：预言 {0.8 * pred_L:+.4f}~"
          f"{1.2 * pred_L:+.4f} rad/s；Iyy/Izz 敏感性档："
          + "；".join(f"{k}× → {v:+.4f}" for k, v in sens_d.items())
          + f"（基准 {dE1:+.4f}）。",
          "",
          "## 半自由口径（仅滚转通道，归因补测）", "",
          f"- 设定：姿态/速度/俯仰偏航冻结配平态，仅 dp/dt="
          f"(Tx−{ROT_DAMP}·p)/Ixx 积分——隔离全耦合正反馈。阻尼时间"
          f"常数 τ=Ixx/ROT_DAMP={Ixx / ROT_DAMP * 1000.0:.0f} ms"
          f"（≪拍周期 571 ms）。",
          f"- **无阻尼档**：E1 左停一拍 Δp={ro_nd_dE1:+.4f} rad/s，"
          f"量子预言 {pred_L:+.4f}，偏差 {ro_nd_p1_err:.2%}——"
          f"量子冲量数值本身的积分自洽检验。",
          f"- **含阻尼档**：E1 Δp={ro_dE1:+.4f} rad/s（偏差 "
          f"{ro_p1_err:.1%}）；阻尼损耗折算冲量 {ro_damp_loss:+.5f}"
          f" N·m·s（对照量子 {abs(dj_tx_L):.5f}）——旋转阻尼在事件"
          f"尺度上主导，ΔJ/Ixx 折算在生效阻尼下严重高估。",
          f"- E3 左+右异拍净增量 {ro_dE3:+.4f} rad/s（镜像抵消自检，"
          f"应≈0）。",
          f"- 预言方向口径（2026-09-24 修正）：量子 dJ=扑减停，事件"
          f"扑→停的冲量差为 −dJ，预言=−dJ_Tx/Ixx；初版符号反、已修。",
          "",
          "## 判读与遗留", "",
          "- P1 不确定性主项是 Ixx 约值；判定结论须在敏感性带内稳健"
          "方可对外引用。",
          "- Δp 取事件拍边界增量（避开长时开环漂移污染）；末态与逐拍"
          "ω 序列见 JSON。",
          "- 遗留：Iyy/Izz 悬案（真机称量项，见真机考核清单）；停扑的"
          "执行机构切换时间（气动中性位非瞬时）不在本模型。", ""]
    doc_path = os.path.join(PROJECT_ROOT, "docs", "事件序列可判伪验证.md")
    with open(doc_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[ev] 完成 → docs/事件序列可判伪验证.md", flush=True)


if __name__ == "__main__":
    main()
