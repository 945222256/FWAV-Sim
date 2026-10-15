# -*- coding: utf-8 -*-
"""
diag_speed_channel.py — 速度环不响应离线诊断（收尾施工计划步骤一）
==================================================================
背景：第十班在环观测到"总比能误差 e_E 达到 +20 J/kg 时拍频指令不上抬"，
该现象意味着能量供给通道只有一半在起作用。本脚本不启动仿真器，把最近
一次试飞数据（logs/ident_stream_flight_0921_025539.csv）中的状态序列逐帧
读入，逐帧调用控制器类（modules/flight_controller.py 中的 FlightController）
重放指令链，回答三个问题：

  怀疑点一：总比能积分项的抗饱和冻结条件是否误冻结；
  怀疑点二：动能误差项是否使用了滤波后的陈旧速度；
  怀疑点三：高度出带时拍频是否被速度基准下限分支（vxd 触底）压制。

重放口径（与 main_sim.py 712 行调用点逐项对齐）：
  1. CSV 的 vx/vy/vz 列为原始世界系线速度；控制器实际输入是
     aero_solver.held_body_vel（线速度 τ=0.1s 指数滤波），重放前按帧间
     dt 先重建该滤波，再交给控制器；
  2. 角速度 wx/wy/wz 三个分量不经上述替换（main_sim 只替换线速度的前
     三个分量），重放直接使用 CSV 原始角速度，控制器内部的 _w_filt
     滤波由控制器自行处理；
  3. 重放起点取 t0=2.0s（状态锁定 0.9s 与拍幅软启动 0.5s 均已结束），
     控制器状态按该帧 CSV 数值初始化（_freq_last 取 freq 列、_pd_last
     取 pitch_des 列）；
  4. 对账：重放得到的 freq/pitch_des/vxd 与 CSV 同名列逐帧比对，残差
     中位数与峰值给出重放可信度；残差过大时诊断结论作废。

输出：全部证据数字打印到标准输出；诊断结论由人工写入
docs/速度环不响应诊断.md。
"""
import csv
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.flight_controller import FlightController  # noqa: E402
import config.sim_config as cfg  # noqa: E402

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "logs", "ident_stream_flight_0921_025539.csv")
T0 = 2.0          # s 重放起点（状态锁定与软启动均已结束）
HELD_TAU = 0.1    # s aero_solver 控制器输入速度的滤波时间常数


def read_rows():
    with open(CSV_PATH, newline="") as fh:
        return list(csv.DictReader(fh))


def rebuild_held(rows):
    """按帧间 dt 重建 aero_solver.held_body_vel（线速度 τ=0.1s 滤波）。"""
    held = np.zeros((len(rows), 3), dtype=float)
    v0 = np.array([float(rows[0]["vx"]), float(rows[0]["vy"]),
                   float(rows[0]["vz"])])
    held[0] = v0
    for i in range(1, len(rows)):
        dt = float(rows[i]["t"]) - float(rows[i - 1]["t"])
        v = np.array([float(rows[i]["vx"]), float(rows[i]["vy"]),
                      float(rows[i]["vz"])])
        held[i] = held[i - 1] + min(1.0, dt / HELD_TAU) * (v - held[i - 1])
    return held


def xcorr_lag(a, b, max_lag_s, dt):
    """互相关定滞后：返回 lag（秒，a 滞后 b 为正）与峰值相关系数。"""
    a = np.asarray(a, dtype=float) - np.mean(a)
    b = np.asarray(b, dtype=float) - np.mean(b)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    best_lag, best_r, best_abs = 0.0, 0.0, -1.0
    for lag_frames in range(-int(max_lag_s / dt), int(max_lag_s / dt) + 1):
        if lag_frames >= 0:
            aa, bb = a[lag_frames:], b[:n - lag_frames]
        else:
            aa, bb = a[:n + lag_frames], b[-lag_frames:]
        if len(aa) < 20:
            continue
        denom = (np.std(aa) * np.std(bb) * len(aa))
        r = float(np.dot(aa, bb) / denom) if denom > 0 else 0.0
        if abs(r) > best_abs:
            best_abs, best_r, best_lag = abs(r), r, lag_frames * dt
    return best_lag, best_r


def main():
    f = cfg.FlightControllerConfig
    rows = read_rows()
    held = rebuild_held(rows)
    g = 9.81
    zr = f.Z_REF
    cruise = f.CRUISE_VX

    # ── 逐帧重放 ──
    ctrl = FlightController()
    start = next(i for i, r in enumerate(rows) if float(r["t"]) >= T0)
    ctrl._freq_last = float(rows[start]["freq"])
    ctrl._pd_last = float(rows[start]["pitch_des"])
    ctrl._pv_filt = held[start].copy()
    ctrl._w_filt = None
    ctrl._t_last = None

    rec = {k: [] for k in
           ("t", "z", "vx_raw", "vx_held", "vx_ctrl", "vz_ctrl", "vxd",
            "e_pot", "e_kin", "e_E", "e_B", "ei", "kp_term", "ki_term",
            "freq_raw", "freq_out", "freq_csv", "pd_out", "pd_csv",
            "vxd_csv", "frozen", "vxd_floor", "pitch", "vz_raw")}
    for i in range(start, len(rows)):
        r = rows[i]
        t = float(r["t"])
        pose = [float(r["x"]), float(r["y"]), float(r["z"]),
                float(r["qw"]), float(r["qx"]), float(r["qy"]), float(r["qz"])]
        vel = [held[i][0], held[i][1], held[i][2],
               float(r["wx"]), float(r["wy"]), float(r["wz"])]
        out = ctrl.update(pose, vel, t_sim=t)

        # 中间量复算（公式与 flight_controller.py 2C 分支逐行相同，
        # 速度取控制器内部二次滤波后的 _pv_filt，保证口径一致）
        z = float(r["z"])
        vf0 = float(ctrl._pv_filt[0])
        vxd = cruise - f.TECS_ZV_K * (z - zr)
        vxd = float(np.clip(vxd, max(f.V_CMD_MIN, 0.5 * cruise), 1.5 * cruise))
        e_pot = g * (zr - z)
        e_kin = 0.5 * (vxd * vxd - vf0 * vf0)
        e_E = e_pot + e_kin
        e_B = float(np.clip(e_pot - e_kin, -f.TECS_EB_MAX, f.TECS_EB_MAX))
        frozen = not (f.FREQ_AP_MIN * 1.01 < ctrl._freq_last
                      < f.FREQ_MAX_AP * 0.99)   # 判定用更新前一拍口径
        kp_term = f.TECS_KF_P * e_E
        ki_term = f.TECS_KF_I * ctrl._ei
        freq_raw = f.FREQ_HOVER + kp_term + ki_term

        rec["t"].append(t)
        rec["z"].append(z)
        rec["vx_raw"].append(float(r["vx"]))
        rec["vx_held"].append(float(held[i][0]))
        rec["vx_ctrl"].append(vf0)
        rec["vz_ctrl"].append(float(ctrl._pv_filt[2]))
        rec["vxd"].append(vxd)
        rec["vxd_floor"].append(abs(vxd - f.V_CMD_MIN) < 1e-9)
        rec["e_pot"].append(e_pot)
        rec["e_kin"].append(e_kin)
        rec["e_E"].append(e_E)
        rec["e_B"].append(e_B)
        rec["ei"].append(ctrl._ei)
        rec["kp_term"].append(kp_term)
        rec["ki_term"].append(ki_term)
        rec["freq_raw"].append(freq_raw)
        rec["freq_out"].append(out["freq"])
        rec["freq_csv"].append(float(r["freq"]))
        rec["pd_out"].append(out["pitch_des"])
        rec["pd_csv"].append(float(r["pitch_des"]))
        rec["vxd_csv"].append(float(r["vxd"]) if r["vxd"] else 0.0)
        rec["frozen"].append(frozen)
        rec["pitch"].append(float(r["pitch"]))
        rec["vz_raw"].append(float(r["vz"]))

    A = {k: np.asarray(v, dtype=float) for k, v in rec.items()}
    n = len(A["t"])
    print(f"重放区间 t=[{A['t'][0]:.2f},{A['t'][-1]:.2f}]s 帧数={n}")

    # ── 对账（残差大则结论作废）──
    for name, a, b in (("freq", A["freq_out"], A["freq_csv"]),
                       ("pitch_des", A["pd_out"], A["pd_csv"])):
        d = a - b
        print(f"对账 {name}: 残差中位={np.median(d):+.5f} 峰值|d|={np.max(np.abs(d)):.5f} "
              f"P95|d|={np.percentile(np.abs(d),95):.5f}")

    # ── 速度/高度在带率（现状缺口复核）──
    in_band_v = np.abs(A["vx_raw"] - 4.5) <= 1.0
    in_band_z = np.abs(A["z"] - 3.0) <= 0.8
    print(f"在带率: 速度={100*np.mean(in_band_v):.1f}% 高度={100*np.mean(in_band_z):.1f}%")

    # ── 怀疑点一：抗饱和冻结 + 积分状态轨迹 ──
    fr = np.asarray(rec["frozen"], dtype=bool)
    print(f"\n[怀疑点一] 冻结窗口(1.01<f<3.465)外帧占比={100*np.mean(fr):.2f}%  "
          f"其中freq<1.01占比={100*np.mean(A['freq_out']<1.01):.2f}%  "
          f"freq>3.465占比={100*np.mean(A['freq_out']>3.465):.2f}%")
    ei = A["ei"]
    print(f"积分状态 ei: min={ei.min():.1f} max={ei.max():.1f} 末值={ei[-1]:.1f} "
          f"触顶-60占比={100*np.mean(ei<=-60+0.5):.1f}% 触顶+60占比={100*np.mean(ei>=60-0.5):.1f}% "
          f"为负占比={100*np.mean(ei<0):.1f}%")

    # ── 核心归因：e_E>0 时拍频为何不升 ──
    m = A["e_E"] > 5.0
    not_up = m & (A["freq_out"] < f.FREQ_HOVER + 0.10)
    print(f"\n[核心归因] e_E>+5 帧占比={100*np.mean(m):.1f}%；"
          f"其中拍频未升(freq<1.65)占比={100*np.mean(not_up)/max(np.mean(m),1e-9):.1f}%")
    for label, sel in (("e_E>+5 全部", m), ("e_E>+5 且拍频未升", not_up)):
        if not np.any(sel):
            continue
        kp, ki = A["kp_term"][sel], A["ki_term"][sel]
        print(f"  {label}: 帧数={int(np.sum(sel))} kp_term中位={np.median(kp):+.3f}Hz "
              f"ki_term中位={np.median(ki):+.3f}Hz ki<-0.5*|kp|占比="
              f"{100*np.mean(ki < -0.5*np.abs(kp)):.0f}%  e_pot中位={np.median(A['e_pot'][sel]):+.1f} "
              f"e_kin中位={np.median(A['e_kin'][sel]):+.1f}")
    # 斜坡与上限的钳制证据：freq_raw 与实际输出之差
    gap = A["freq_raw"] - A["freq_out"]
    print(f"  freq_raw-freq_out: 中位={np.median(gap):+.3f} P95={np.percentile(gap,95):+.3f} "
          f"max={gap.max():+.3f}Hz（>0.05 帧占比={100*np.mean(gap>0.05):.1f}%）")

    # ── 怀疑点二：滤波滞后对 e_kin 的相位影响 ──
    dt = float(np.median(np.diff(A["t"])))
    # 滤波前口径：直接用原始 vx 复算 e_kin 序列
    e_kin_raw = np.zeros(n)
    for j in range(n):
        vxd_j = A["vxd"][j]
        e_kin_raw[j] = 0.5 * (vxd_j * vxd_j - A["vx_raw"][j] ** 2)
    lag_f, r_f = xcorr_lag(e_kin_raw, A["e_pot"], 2.0, dt)
    print(f"\n[怀疑点二] e_kin(原始vx) 对 e_pot 滞后={lag_f:+.2f}s 相关系数={r_f:+.2f}")
    # 滤波前后 e_E 的幅度差（能量交换振荡是否被滤波放大/抵消）
    e_E_raw = A["e_pot"] + e_kin_raw
    print(f"  e_E 幅度: 滤波口径std={np.std(A['e_E']):.1f} 原始口径std={np.std(e_E_raw):.1f} J/kg")
    lag_e, r_e = xcorr_lag(A["e_kin"], A["e_pot"], 2.0, dt)
    print(f"  e_kin(滤波) 对 e_pot 滞后={lag_e:+.2f}s 相关系数={r_e:+.2f}")
    lag_v, r_v = xcorr_lag(A["vx_raw"], A["z"], 3.0, dt)
    print(f"  速度-高度能量交换: vx 对 z 滞后={lag_v:+.2f}s 相关系数={r_v:+.2f}")

    # ── 怀疑点三：vxd 触底（角落地带分支）──
    vf_floor = np.asarray(rec["vxd_floor"], dtype=bool)
    z_out = np.abs(A["z"] - zr) > 0.8
    print(f"\n[怀疑点三] vxd 触底(=V_CMD_MIN=3.0)帧占比={100*np.mean(vf_floor):.2f}% "
          f"z出带(|dz|>0.8)占比={100*np.mean(z_out):.2f}%")

    # ── 振荡闭环机理：分配通道（俯仰）增益核算 ──
    m_cruise = A["t"] > 10.0      # 稳态巡航段
    z_amp = np.std(A["z"][m_cruise])
    vx_amp = np.std(A["vx_raw"][m_cruise])
    eB_amp = np.std(A["e_B"][m_cruise])
    pitch_tilt_amp = np.std(A["pd_out"][m_cruise])
    print(f"\n[振荡闭环] 巡航段(t>10s)幅度: z_std={z_amp:.2f}m vx_std={vx_amp:.2f}m/s "
          f"e_B_std={eB_amp:.1f}J/kg pitch_des_std={pitch_tilt_amp:.4f}rad")
    lag_pd, r_pd = xcorr_lag(A["pd_out"], A["e_B"], 2.0, dt)
    print(f"  pitch_des 对 e_B 滞后={lag_pd:+.2f}s 相关系数={r_pd:+.2f}")
    # pitch_des 分解：分配项(KP_E×e_B) 与 垂速阻尼项(KD_V×vz) 的幅度对比
    kpe_term = f.TECS_KP_E * A["e_B"]
    kdv_term = -f.TECS_KD_V * A["vz_ctrl"]
    print(f"  pitch_des 构成幅度: KP_E×e_B std={np.std(kpe_term):.4f}rad  "
          f"KD_V×vz std={np.std(kdv_term):.4f}rad（主导项判定）")
    lag_vz, r_vz = xcorr_lag(A["vz_ctrl"], A["e_B"], 2.0, dt)
    print(f"  vz 对 e_B 滞后={lag_vz:+.2f}s 相关系数={r_vz:+.2f}（e_B 与垂速的相位）")
    # 振荡周期测量：z 序列的上升沿过零间隔（带内均值去除后）
    zs = A["z"] - np.mean(A["z"][m_cruise])
    zx = np.where(np.diff(np.sign(zs[m_cruise])) > 0)[0]
    periods = np.diff(A["t"][m_cruise][zx]) if len(zx) > 2 else np.array([0.0])
    print(f"  z 上升沿过零间隔（振荡周期）中位={np.median(periods):.2f}s "
          f"周期数={len(periods)}")
    # 分配通道增益：e_B→pitch 幅度 vs 阻尼该振荡所需的俯仰幅度
    kp_e_amp = f.TECS_KP_E * eB_amp
    # 所需俯仰：半周期 2s 内把 ±vx_amp 速度误差的动能搬回势能
    need_acc = 2.0 * vx_amp / 2.0                    # m/s²（半周期均摊）
    need_pitch = need_acc * cfg.MASS / f.DFX_DTHETA  # rad
    print(f"  分配通道供给: KP_E×e_B_std={kp_e_amp:.4f}rad；"
          f"阻尼振荡所需俯仰幅度≈{need_pitch:.4f}rad（DFX_DTHETA={f.DFX_DTHETA}N/rad, "
          f"m={cfg.MASS}kg）；供给/需求={kp_e_amp/need_pitch:.2f}")
    # e_E 通道等效核算：拍频增量对速度的二阶效果（e_E→freq→Fx）
    lag_fx, r_fx = xcorr_lag(A["vx_raw"], A["freq_out"], 3.0, dt)
    print(f"  vx 对 freq 滞后={lag_fx:+.2f}s 相关系数={r_fx:+.2f}（负相关=拍频升而速度降）")

    # ── 末段证据：失速样振荡的逐时刻切片 ──
    print("\n[切片] 每 8 秒一行的状态与指令快照：")
    print("  t      z     vx    vz    e_pot  e_kin  e_E    ei     kp    ki    f_raw  f_out  vxd  pd_out")
    for j in range(0, n, int(8.0 / dt)):
        print(f"  {A['t'][j]:5.1f} {A['z'][j]:5.2f} {A['vx_raw'][j]:5.2f} {A['vz_raw'][j]:+5.2f} "
              f"{A['e_pot'][j]:+6.1f} {A['e_kin'][j]:+6.1f} {A['e_E'][j]:+6.1f} {A['ei'][j]:+6.1f} "
              f"{A['kp_term'][j]:+5.2f} {A['ki_term'][j]:+5.2f} {A['freq_raw'][j]:5.2f} {A['freq_out'][j]:5.2f} "
              f"{A['vxd'][j]:4.2f} {A['pd_out'][j]:+6.3f}")


if __name__ == "__main__":
    main()
