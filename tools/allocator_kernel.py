# -*- coding: utf-8 -*-
"""
allocator_kernel.py — 分配层闭环评估核（2026-09-28）

标准整定方法（docs/标准整定方法_TPE分配权重.md）的评估核。
结构复用 turn_scan.TurnClosedLoopSim（物理全部来自 v1 框架），
唯一新增：滚转力矩需求到四通道的加权逆映射分配层。

分配规则：u_i = w_i · mx / eff_i(state)，域界裁剪；eff_i(state)≤0
的通道在当前状态下权重强制为零（不用反号通道）。
通道效能函数（全部来自实测拟合，见分配层规范）：
  eff_ail = 0.111 N·m/rad（尾翼差动，实测效能）
  eff_dA  = 0.046 N·m/rad（三维实测，dA 语义）
  eff_lag = A_lag/0.02（滞后差动线性安全域内网格实测均值口径）
  eff_rho(θ,β,φ) = 二元拟合面（authority_fits.json，满额停翼口径，
                   δρ∈[-1,1] 线性缩放）

密度通道在核内的施加方式与在环一致：拍边界误差扩散累加器
（acc += ρ; acc≥1 拍并 acc−=1，否则停）。

写权限：主会话亲写。运行方式：由 allocator_tune_tpe.py 调用。
"""
import os
import sys
import json
import math

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import importlib.util
import numpy as np


def _load(name):
    path = os.path.join(PROJECT_ROOT, "tools", name)
    spec = importlib.util.spec_from_file_location(
        name.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


turn_scan = _load("turn_scan.py")
v1 = turn_scan._load_v1()

with open(os.path.join(PROJECT_ROOT, "logs", "authority_fits.json"),
          encoding="utf-8") as fp:
    FITS = json.load(fp)

EFF_AIL = 0.111      # N·m/rad（实测效能，分配层规范通道 4）
EFF_DA = 0.046       # N·m/rad（三维实测，dA 语义）
with open(os.path.join(PROJECT_ROOT, "logs",
                       "authority_lagdiff_grid_phi10.json"),
          encoding="utf-8") as fp:
    _lg = json.load(fp)
# 滞后差动效能：δ=0.02 s 档全网格正值域的均值除以 δ
_lag_vals = [r["lagdiff_auth_0.02"] for r in _lg["rows"]
             if r["lagdiff_auth_0.02"] > 0]
EFF_LAG = float(np.mean(_lag_vals)) / 0.02 if _lag_vals else 0.0

DOM = {"ail": 0.30, "dA": 0.175, "lag": 0.02, "rho": 1.0, "dfreq": 0.4}
# 频率差动偏航效能（阶段一实测斜率合成）：ΔTz ≈ 0.094 N·m 每赫兹半幅
EFF_DFREQ = 0.094


def eff_rho(theta_deg, beta_deg, phi_deg):
    """密度通道满额权限拟合面；非正返回 0（反号通道不用）。"""
    c = FITS["density_bilinear_phi10" if phi_deg > 5
             else "density_bilinear_phi0"]
    a = (c["c0"] + c["c_theta"] * theta_deg + c["c_beta"] * beta_deg
         + c["c_theta_beta"] * theta_deg * beta_deg)
    return max(a, 0.0)


class AllocTurnSim:
    """持坡度闭环核 + 四通道分配层。控制律结构同 TurnClosedLoopSim，
    滚转通道由 lag 单通道换为分配层。"""

    def __init__(self, wave, ctrl, th_rad, phi_ref_rad, weights,
                 kp_phi=0.02, kd_phi=0.02, ky_yaw=0.0, bias_sign=-1):
        self.w = wave
        self.c = ctrl
        self.th = th_rad
        self.phr = phi_ref_rad
        self.kp_phi = kp_phi
        self.kd_phi = kd_phi
        self.ky_yaw = ky_yaw
        self.bias_sign = bias_sign
        self.weights = dict(weights)   # keys: ail, dA, lag, rho

    def run(self, t_end=20.0, dt=0.002):
        quat = v1.quat_norm(v1.euler_to_quat(self.phr, self.th, 0.0))
        Fh = v1.rot_body_to_world(quat, np.array([0.0, 0.0, 1.0]))
        fh = np.array([Fh[0], Fh[1], 0.0])
        fh = fh / max(np.linalg.norm(fh), 1e-9)
        v = self.w["speed"] * np.array([fh[1], -fh[0], 0.0])
        w = np.zeros(3)
        pos = np.array([0.0, 0.0, 3.0])
        f_cmd = self.w["freq"]
        integ = 0.0; filt = 0.0; z_int = 0.0
        n = int(round(t_end / dt))
        z_min = z_max = 3.0
        dead_s = None
        phi_errs = []
        sat_time = 0.0
        freq_oob = 0.0
        cnt = 0
        frz_buf = []
        t = v1.T0[0]
        v1.set_waveform(self.w)
        lag0 = self.w["lag"]
        amp0 = math.radians(self.w["amp"])
        # 密度通道拍边界累加器
        acc_L = 0.0
        acc_R = 0.0
        t_beat_last = None
        v1.model.wing_left.stroke_gate = 1.0
        v1.model.wing_right.stroke_gate = 1.0
        try:
            for i in range(n):
                th = v1.quat_pitch(quat)
                phi = turn_scan.TurnClosedLoopSim.euler_roll(quat)
                p_rate = float(w[0])
                # 状态估计：俯仰角与侧滑角（体轴速度）
                vb = v1.rot_world_to_body(quat, v)
                beta_deg = math.degrees(math.atan2(vb[1],
                                                   max(vb[0], 0.1)))
                th_deg = math.degrees(th)
                phi_deg = math.degrees(phi)
                # 力矩需求
                mx = self.kp_phi * (self.phr - phi) - self.kd_phi * p_rate
                # 分配层
                effs = {"ail": EFF_AIL, "dA": EFF_DA, "lag": EFF_LAG,
                        "rho": eff_rho(th_deg, beta_deg, phi_deg)}
                u = {}
                for k in ("ail", "dA", "lag", "rho"):
                    wk = self.weights.get(k, 0.0)
                    if wk <= 0.0 or effs[k] <= 0.0:
                        u[k] = 0.0
                    else:
                        u[k] = float(np.clip(wk * mx / effs[k],
                                             -DOM[k], DOM[k]))
                # 施加：尾翼差动
                # （升降舵通道在下方高度链另算；此处只加滚转差动面）
                # 滞后差动
                v1.model.wing_left.pitch_lag_s = lag0 + u["lag"]
                v1.model.wing_right.pitch_lag_s = lag0 - u["lag"]
                # 幅值差动
                v1.model.wing_left.amplitude = amp0 + u["dA"]
                v1.model.wing_right.amplitude = amp0 - u["dA"]
                # 密度差动（拍边界累加器）
                per = 1.0 / max(f_cmd, 0.2)
                if t_beat_last is None or (t - t_beat_last) >= per:
                    t_beat_last = t
                    dr = u["rho"]
                    rho_L = 1.0 - max(0.0, -dr)
                    rho_R = 1.0 - max(0.0, +dr)
                    acc_L += rho_L
                    acc_R += rho_R
                    if acc_L >= 1.0:
                        acc_L -= 1.0
                        v1.model.wing_left.stroke_gate = 1.0
                    else:
                        v1.model.wing_left.stroke_gate = 0.0
                    if acc_R >= 1.0:
                        acc_R -= 1.0
                        v1.model.wing_right.stroke_gate = 1.0
                    else:
                        v1.model.wing_right.stroke_gate = 0.0
                # 高度链与俯仰/频率通道（同 TurnClosedLoopSim 口径）
                z_err = 3.0 - pos[2]
                z_int = float(np.clip(z_int + z_err * dt,
                                      -2.0 * self.c["vz_cap"] / max(self.c["alt_ki"], 1e-3),
                                      2.0 * self.c["vz_cap"] / max(self.c["alt_ki"], 1e-3)))
                vz_des = float(np.clip(self.c["alt_kp"] * z_err
                                       + self.c["alt_ki"] * z_int,
                                       -self.c["vz_cap"], self.c["vz_cap"]))
                th_tgt = self.th + self.bias_sign * float(np.clip(
                    0.8 * (vz_des - v[2]) * 0.15,
                    -self.c["th_bias_cap"], self.c["th_bias_cap"]))
                th_err = th_tgt - th
                if abs(th_err) < self.c["int_gate"]:
                    integ += th_err * dt
                else:
                    integ *= (1.0 - 0.5 * dt)
                filt += (w[1] - filt) * dt / max(self.c["rate_filt_tau"], 1e-4)
                _cb, _sb = math.cos(th), math.sin(th)
                _slip_ff = v1.cfg.SLIP_K * v1.cfg.SLIP_V_PER_HZ * f_cmd
                _vbx = _cb * v[0] + _sb * v[2] + _slip_ff
                _vbz = _sb * v[0] + _cb * v[2]
                _alpha_hat = math.atan2(_vbz, max(_vbx, 0.1))
                elev_cmd = float(np.clip(
                    -(self.c["kp_a"] * th_err - self.c["pitch_damp"] * filt
                      + self.c["ki_a"] * integ) - self.c["alpha_ff"] * _alpha_hat,
                    -0.30, 0.30))
                e_act = turn_scan._elev_step(elev_cmd, dt)
                v1.tail.set_command(elevator=e_act, aileron=u["ail"])
                v_for_fb = v[0] + 0.30 * math.sin(2 * math.pi * f_cmd * (i * dt))
                freq_tgt = float(np.clip(
                    self.w["freq"] + self.c["tecs_kv_f"] * (self.w["speed"] - v_for_fb),
                    1.0, 2.2))
                f_cmd += float(np.clip(freq_tgt - f_cmd,
                                       -self.c["freq_slew"] * dt,
                                       self.c["freq_slew"] * dt))
                v1.model.set_wing_command(frequency=f_cmd)
                # 偏航阻尼通道（频率差动，阶段一定位为偏航/推进通道）：
                # mz = −ky·r，左翼 f+δf、右翼 f−δf，效能 0.094 N·m/Hz
                # 半幅（实测斜率合成），域 ±0.4 Hz；在集体设频后覆盖，
                # 相位积分状态不动（与在环注入同口径）。
                mz = -self.ky_yaw * float(w[2])
                u_df = float(np.clip(mz / EFF_DFREQ,
                                     -DOM["dfreq"], DOM["dfreq"]))
                if abs(u_df) > 1e-12:
                    v1.model.wing_left.set_frequency(f_cmd + u_df)
                    v1.model.wing_right.set_frequency(f_cmd - u_df)
                om_world = v1.rot_body_to_world(quat, w)
                F, Tq = v1.forces_total(quat, v, om_world, t, f_cmd)
                frz_buf.append(float(F[2]))
                Tq = Tq + v1.gravity_moment_body(quat)
                F = F + np.array([0.0, 0.0, -v1.W])
                if not (np.all(np.isfinite(F)) and np.all(np.isfinite(Tq))):
                    dead_s = i * dt
                    break
                _fn = float(np.linalg.norm(F))
                if _fn > 3.0 * v1.W:
                    F = F * (3.0 * v1.W / _fn)
                _tn = float(np.linalg.norm(Tq))
                if _tn > 2.0:
                    Tq = Tq * (2.0 / _tn)
                v = v + F / v1.MASS * dt
                w = w + np.linalg.solve(v1.I_DIAG,
                                        Tq - np.cross(w, v1.I_DIAG @ w)) * dt
                quat = v1.quat_norm(quat + v1.quat_dot(quat, w) * dt)
                pos = pos + v * dt
                if not np.all(np.isfinite(pos)):
                    dead_s = i * dt
                    break
                t += dt
                z_min = min(z_min, pos[2])
                z_max = max(z_max, pos[2])
                phi_errs.append(abs(self.phr - phi))
                if not (1.0 <= f_cmd <= 2.2):
                    freq_oob += dt
                if abs(u["ail"]) >= DOM["ail"] * 0.99:
                    sat_time += dt
                if pos[2] < 0.2:
                    dead_s = i * dt
                    break
        finally:
            v1.model.wing_left.stroke_gate = 1.0
            v1.model.wing_right.stroke_gate = 1.0
            v1.model.wing_left.pitch_lag_s = lag0
            v1.model.wing_right.pitch_lag_s = lag0
            v1.model.wing_left.amplitude = amp0
            v1.model.wing_right.amplitude = amp0
            v1.tail.set_command(elevator=0.0, aileron=0.0)

        surv = dead_s if dead_s is not None else t_end
        errs = np.array(phi_errs)
        tail_err = errs[int(len(errs) * 0.25):] if len(errs) > 10 else errs
        out = {
            "survive_s": surv,
            "z_min": z_min, "z_max": z_max,
            "phi_err_med": float(np.median(tail_err)) if len(tail_err) else 9.9,
            "freq_oob_s": freq_oob,
            "ail_sat_s": sat_time,
            "frzw_med": (float(np.mean(frz_buf[-300:])) / v1.W
                         if frz_buf else 0.0),
        }
        return out


_TRIM_CACHE = {}


def evaluate(weights, phi_deg=10.0, t_end=20.0):
    """整定目标函数：存活 − 罚项。返回 (J, 明细)。配平根按进程缓存。"""
    p = dict(turn_scan.BASE_WAVE)
    if "th0" not in _TRIM_CACHE:
        th = turn_scan.trim_roll(v1, p, 0.0)
        _TRIM_CACHE["th0"] = th if th is not None else -8.916
    sim = AllocTurnSim(p, turn_scan.CTRL0, math.radians(_TRIM_CACHE["th0"]),
                       math.radians(phi_deg),
                       {k: weights.get(k, 0.0)
                        for k in ("ail", "dA", "lag", "rho")},
                       kp_phi=weights.get("kp_phi", 0.02),
                       kd_phi=weights.get("kd_phi", 0.02),
                       ky_yaw=weights.get("ky_yaw", 0.0))
    out = sim.run(t_end=t_end)
    J = (out["survive_s"]
         - 200.0 * out["phi_err_med"]
         - 2.0 * max(0.0, 2.0 - out["z_min"])
         - 2.0 * max(0.0, out["z_max"] - 4.0)
         - 1.0 * out["freq_oob_s"]
         - 0.5 * out["ail_sat_s"])
    return J, out


ACCEPT = {"survive_s": 19.99, "phi_err_med": 0.05, "z_band": (2.0, 4.0)}


def accepted(out):
    return (out["survive_s"] >= ACCEPT["survive_s"]
            and out["phi_err_med"] <= ACCEPT["phi_err_med"]
            and out["z_min"] >= ACCEPT["z_band"][0]
            and out["z_max"] <= ACCEPT["z_band"][1]
            and out["freq_oob_s"] <= 0.0)
