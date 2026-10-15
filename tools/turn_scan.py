# -*- coding: utf-8 -*-
"""
turn_scan.py — 压坡度转弯扫描执行器（审查文档第四节合并方案的执行层）

两层结构：
层1（静态）：坡度档 × 基准与邻域可行通道波形的静态账：带坡度姿态的
配平俯仰根（净矩=翼+全量尾翼+机翼矩直流+重力矩纪律函数）、前向力与
竖直力、升力需求核验（v2 拟合函数 turn_lift_need）、滚转保持需求
（盘旋稳态阻尼性矩，测量C 实测口径）对 lag 差动行程的占比。
层2（动态）：TurnClosedLoopSim 持坡度六十秒（物理调用全部复用 v1
框架函数：forces_total/wing_forces_body/纪律函数族/重力矩本体项；
新增内容仅为滚转-差动控制律与转弯判据，属模态扩展，不另建物理）。
滚转执行通道=俯仰滞后差动（测量D 实测效能 0.446 N·m/s）；滚转反馈
采用实测速率口径（测量C 警戒：WING_DAMP 矩阵在盘旋工况反号，禁引用）。

写权限：主会话亲写；代理只执行本脚本与读取产物。
运行方式：python tools/turn_scan.py
产物：logs/turn_scan_<时间戳>.json
"""
import os, sys, json, math, time, datetime
import multiprocessing as mp
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import importlib.util


def _load_v1():
    path = os.path.join(PROJECT_ROOT, "tools", "unified_search_framework.py")
    spec = importlib.util.spec_from_file_location("usf_v1_turnscan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BASE_WAVE = {
    "alpha_set": 32.3169, "mean": 19.8105, "split": 0.176,
    "lag": -0.0674, "amp": 17.73, "freq": 1.80, "speed": 3.95,
}
# 邻域重扫筛出的可行通道波形（logs/neighborhood_rescan_20260927_131500.json
# 的可行方向）：lag 负向（四档全过）、speed 正向（+0.5 内全过）、
# alpha+amp 联动负向（diagB 负向大开度全过）。
CHANNEL_WAVES = {
    "base": dict(BASE_WAVE),
    "lag_m04": dict(BASE_WAVE, lag=round(BASE_WAVE["lag"] - 0.04, 4)),
    "speed_p05": dict(BASE_WAVE, speed=round(BASE_WAVE["speed"] + 0.5, 4)),
    "alphamp_m4": dict(BASE_WAVE, alpha_set=round(BASE_WAVE["alpha_set"] - 4.0, 4),
                       amp=round(BASE_WAVE["amp"] - 4.0, 4)),
}
PHI_DEGS = (10.0, 15.0, 20.0, 25.0)
CTRL0 = {
    "kp_a": 6.0, "pitch_damp": 0.6, "ki_a": 0.15, "int_gate": 0.4,
    "rate_filt_tau": 0.03, "alpha_ff": 0.12, "tecs_kv_f": 2.0,
    "alt_kp": 2.0, "alt_ki": 0.40, "vz_cap": 2.5, "th_bias_cap": 0.3,
    "freq_slew": 2.5,
}


def static_point(v1, p, roll_deg=0.0, pitch_deg=0.0, n=288):
    """带坡度静态周期平均（世界系水平来流口径，与 scan_turn_support
    同构；此处复刻其核心循环，物理函数全部来自 v1）。"""
    quat = v1.euler_to_quat(math.radians(roll_deg), math.radians(pitch_deg), 0.0)
    vel_world = np.array([p["speed"], 0.0, 0.0])
    om_world = np.zeros(3)
    v1.set_waveform(p)
    v1.model.set_wing_command(frequency=p["freq"])
    T = 1.0 / p["freq"]
    F = np.zeros(3); Tq = np.zeros(3)
    for i in range(n):
        t = v1.T0[0] + i * T / n
        Fw, Tw = v1.wing_forces_body(quat, vel_world, om_world, t)
        F += Fw; Tq += Tw
    v1.T0[0] += T
    F /= n; Tq /= n
    v_slip = v1.cfg.SLIP_K * v1.cfg.SLIP_V_PER_HZ * p["freq"]
    Ft, Tt = v1.tail.compute(quat, vel_world, v_slip=v_slip)
    return F + Ft, Tq + v1.rot_world_to_body(quat, Tt), quat


def moment_net_roll(v1, p, roll_deg, pitch_deg):
    """带坡度姿态净俯仰矩：翼+全量尾翼+机翼矩直流+重力矩（纪律函数
    gravity_moment_body，带坡度下不做闭式近似）。"""
    _, T, quat = static_point(v1, p, roll_deg=roll_deg, pitch_deg=pitch_deg, n=96)
    my_aero = float(T[1])
    my_dc = v1.MY_DC_COEF * p["freq"] ** 2
    my_grav = float(v1.gravity_moment_body(quat)[1])
    return my_aero + my_dc + my_grav


def trim_roll(v1, p, roll_deg, lo=-30.0, hi=45.0, tol=0.05):
    """带坡度姿态的配平俯仰根（净矩过零，n=96 粗标定口径）。"""
    n = 24
    grid = [lo + (hi - lo) * i / n for i in range(n + 1)]
    gv = [moment_net_roll(v1, p, roll_deg, th) for th in grid]
    for i in range(n):
        if gv[i] * gv[i + 1] < 0.0:
            a, b, fa = grid[i], grid[i + 1], gv[i]
            for _ in range(30):
                m = 0.5 * (a + b)
                fm = moment_net_roll(v1, p, roll_deg, m)
                if fa * fm <= 0.0:
                    b = m
                else:
                    a, fa = m, fm
                if b - a < tol:
                    break
            return 0.5 * (a + b)
    return None


def layer1_static(v1):
    """层1：静态账。返回逐组合记录。"""
    rows = []
    for wname, p in CHANNEL_WAVES.items():
        for phi in PHI_DEGS:
            th = trim_roll(v1, p, phi)
            rec = {"wave": wname, "phi_deg": phi, "th_root_deg": th}
            if th is None:
                rows.append(rec); continue
            F, T, quat = static_point(v1, p, roll_deg=phi, pitch_deg=th)
            mg = v1.gravity_moment_body(quat)
            rec.update({
                "fx_world": float(F[0]), "fz_world": float(F[2]),
                "fy_world": float(F[1]),
                "resid_my": float(T[1] + v1.MY_DC_COEF * p["freq"] ** 2 + mg[1]),
                "W": v1.W,
                "lift_need_ratio": (1.0 + 1.408e-4 * phi * phi),
                "fz_over_need": float(F[2] / (v1.W * (1.0 + 1.408e-4 * phi * phi))),
            })
            rows.append(rec)
            print("L1 %s φ=%.0f: θ*=%+.2f° Fz=%.3f N 需求=%.3f N 比=%.3f 残矩=%+.4f"
                  % (wname, phi, th, F[2], v1.W * rec["lift_need_ratio"],
                     rec["fz_over_need"], rec["resid_my"]), flush=True)
    return rows


class TurnClosedLoopSim:
    """持坡度闭环核（模态扩展）：物理计算全部复用 v1（forces_total/
    纪律函数族/重力矩本体项/限幅约定）；新增=滚转-差动控制律（坡度
    误差+滚转速率阻尼→lag 差动行程限幅）、初始坡度姿态、转弯判据。
    俯仰/高度/频率通道与 v1.ClosedLoopSim 同式（CTRL0 口径）。"""

    def __init__(self, v1, wave, ctrl, th_rad, phi_ref_rad,
                 kp_phi=0.02, kd_phi=0.02, dlag_max=0.02, bias_sign=-1):
        self.v1 = v1
        self.w = wave; self.c = ctrl
        self.th = th_rad; self.phr = phi_ref_rad
        self.kp_phi = kp_phi; self.kd_phi = kd_phi
        self.dlag_max = dlag_max
        self.bias_sign = bias_sign

    @staticmethod
    def euler_roll(quat):
        w, x, y, z = quat
        return math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))

    def run(self, t_end=60.0, dt=0.002):
        v1 = self.v1
        quat = v1.quat_norm(v1.euler_to_quat(self.phr, self.th, 0.0))
        # 初始速度构造（协调盘旋运动学自洽口径）：速度必须垂直于向心力
        # （升力水平分量），否则速度与向心力共线对抗、直接发散（体轴直入
        # 与水平直入两种初值均 1.8 秒翻飞实证）。构造：fh=升力方向
        # （R_bw@e_z）的水平投影归一；v_dir=fh×e_z（对应 ω_yaw=g·tanφ/V
        # 的正号转向，F=m·ω×v 的叉积关系）；v_world=V·v_dir。
        Fh = v1.rot_body_to_world(quat, np.array([0.0, 0.0, 1.0]))
        fh = np.array([Fh[0], Fh[1], 0.0])
        nh = float(np.linalg.norm(fh))
        fh = fh / max(nh, 1e-9)
        v_dir = np.array([fh[1], -fh[0], 0.0])
        v = self.w["speed"] * v_dir
        w = np.zeros(3)
        pos = np.array([0.0, 0.0, 3.0])
        f_cmd = self.w["freq"]
        integ = 0.0; filt = 0.0; z_int = 0.0; vz_des = 0.0
        n = int(round(t_end / dt))
        z_min = 3.0; z_max = 3.0; dead_s = None
        phi_err_max = 0.0; dlag_abs_max = 0.0
        phi_sum = 0.0; elev_acc = 0.0; freq_acc = 0.0; cnt = 0
        frz_buf = []
        t = v1.T0[0]
        v1.set_waveform(self.w)
        lag0 = self.w["lag"]
        for i in range(n):
            th = v1.quat_pitch(quat)
            phi = self.euler_roll(quat)
            p_rate = float(w[0])
            # 滚转-差动控制律（实测效能通道 0.446 N·m/s；速率反馈实测口径）
            dlag = float(np.clip(self.kp_phi * (self.phr - phi)
                                 - self.kd_phi * p_rate,
                                 -self.dlag_max, self.dlag_max))
            v1.model.wing_left.pitch_lag_s = lag0 + dlag
            v1.model.wing_right.pitch_lag_s = lag0 - dlag
            dlag_abs_max = max(dlag_abs_max, abs(dlag))
            phi_err_max = max(phi_err_max, abs(self.phr - phi))
            # 高度链（与 v1.ClosedLoopSim 同式）：z_err→vz_des→俯仰偏置
            z_err = 3.0 - pos[2]
            z_int += z_err * dt
            z_int = float(np.clip(z_int, -2.0 * self.c["vz_cap"] / max(self.c["alt_ki"], 1e-3),
                                  2.0 * self.c["vz_cap"] / max(self.c["alt_ki"], 1e-3)))
            vz_des = float(np.clip(self.c["alt_kp"] * z_err + self.c["alt_ki"] * z_int,
                                   -self.c["vz_cap"], self.c["vz_cap"]))
            # 俯仰/高度/频率通道（与 v1.ClosedLoopSim 同式：高度→俯仰偏置
            # 闭环内的 th_err 同时供积分门与比例项；CTRL0 口径）
            th_tgt = self.th + self.bias_sign * float(np.clip(
                0.8 * (vz_des - v[2]) * 0.15, -self.c["th_bias_cap"], self.c["th_bias_cap"]))
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
            e_act = _elev_step(elev_cmd, dt)
            v1.tail.set_command(elevator=e_act)
            v_for_fb = v[0] + 0.30 * math.sin(2 * math.pi * f_cmd * (i * dt))
            freq_tgt = float(np.clip(
                self.w["freq"] + self.c["tecs_kv_f"] * (self.w["speed"] - v_for_fb),
                1.0, 3.5))
            df = float(np.clip(freq_tgt - f_cmd, -self.c["freq_slew"] * dt,
                               self.c["freq_slew"] * dt))
            f_cmd += df
            v1.model.set_wing_command(frequency=f_cmd)
            om_world = v1.rot_body_to_world(quat, w)
            F, Tq = v1.forces_total(quat, v, om_world, t, f_cmd)
            frz = float(F[2])
            Tq = Tq + v1.gravity_moment_body(quat)
            F = F + np.array([0.0, 0.0, -v1.W])
            if not (np.all(np.isfinite(F)) and np.all(np.isfinite(Tq))):
                dead_s = i * dt; break
            _fn = float(np.linalg.norm(F))
            if _fn > 3.0 * v1.W:
                F = F * (3.0 * v1.W / _fn)
            _tn = float(np.linalg.norm(Tq))
            if _tn > 2.0:
                Tq = Tq * (2.0 / _tn)
            v = v + F / v1.MASS * dt
            w = w + np.linalg.solve(v1.I_DIAG, Tq - np.cross(w, v1.I_DIAG @ w)) * dt
            quat = v1.quat_norm(quat + v1.quat_dot(quat, w) * dt)
            pos = pos + v * dt
            if not np.all(np.isfinite(pos)):
                dead_s = i * dt; break
            t += dt
            z_min = min(z_min, pos[2]); z_max = max(z_max, pos[2])
            frz_buf.append(frz)
            if i % 30 == 0:
                phi_sum += phi; elev_acc += e_act * e_act; freq_acc += f_cmd; cnt += 1
            if pos[2] < 0.2:
                dead_s = i * dt; break
        # 判据：存活、高度带、坡度保持（时间占比 95% 在 ±3° 内）、频偏
        out = dict(survive_s=dead_s if dead_s is not None else t_end,
                   z_min=z_min, z_max=z_max,
                   phi_err_max_rad=phi_err_max,
                   dlag_abs_max_s=dlag_abs_max,
                   elev_rms=(elev_acc / max(cnt, 1)) ** 0.5,
                   freq_dev=abs(freq_acc / max(cnt, 1) - self.w["freq"]),
                   frzw_med=float(np.mean(frz_buf[-300:])) / v1.W if frz_buf else 0.0)
        return out


_ELEV_STATE = {"e": 0.0}


def _elev_step(cmd, dt, tau=0.02):
    a = dt / max(tau, 1e-6)
    _ELEV_STATE["e"] += a * (cmd - _ELEV_STATE["e"])
    return _ELEV_STATE["e"]


def layer2_dynamic(v1, waves, phis):
    """层2：持坡度动态扫描（每组合 60 秒）。"""
    rows = []
    th_base = {w: None for w in waves}
    for wname, p in waves.items():
        th = trim_roll(v1, p, 0.0, lo=-30.0, hi=45.0, tol=0.05)
        if th is None:
            th = -8.916
        th_rad = math.radians(th)
        for phi in phis:
            for kp_phi, kd_phi in ((0.02, 0.02), (0.01, 0.01)):
                sim = TurnClosedLoopSim(v1, p, CTRL0, th_rad,
                                        math.radians(phi), kp_phi=kp_phi, kd_phi=kd_phi)
                out = sim.run(t_end=60.0)
                ok = (out["survive_s"] >= 59.99 and out["z_min"] >= 2.0
                      and out["z_max"] <= 8.0
                      and out["frzw_med"] >= 0.95)
                rows.append({"wave": wname, "phi_deg": phi,
                             "kp_phi": kp_phi, "kd_phi": kd_phi,
                             "out": out, "feasible": bool(ok)})
                print("L2 %s φ=%.0f kpφ=%.3f: surv=%.1f z=[%.2f,%.2f] φerr=%.2f° δ=%.3fs frzw=%.3f feas=%s"
                      % (wname, phi, kp_phi, out["survive_s"], out["z_min"],
                         out["z_max"], math.degrees(out["phi_err_max_rad"]),
                         out["dlag_abs_max_s"], out["frzw_med"], ok), flush=True)
    return rows


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("== 压坡度转弯扫描 · turn_scan ==", flush=True)
    v1 = _load_v1()
    at = v1.anchor_tests()
    for k, val in at.items():
        print("anchor %s: %.4f %s" % (k, val[0], val[1]), flush=True)
    if not all(val[1].startswith("PASS") or val[1].startswith("SKIP")
               for val in at.values()):
        print("ANCHOR FAIL - 扫描被锚点门禁阻断", flush=True)
        return
    t0 = time.time()
    l1 = layer1_static(v1)
    # 层2（动态持坡度）定性结论（2026-09-27 主会话逐帧对账定案）：
    # 横向通道亚秒级强发散（侧滑-滚转梯度 +0.00042 N·m/deg 实测，
    # 15° 坡度伴生侧滑驱动矩 0.0066 N·m、角加速度 2.75 rad/s²，与
    # 0.6~1.9 秒翻飞形态吻合），差动权限含越出实测线性区一倍
    # （dlag_max=0.04）与高增益滚转律均不可镇定，0.63 秒收敛到倒飞
    # 吸引子。层2 全组合扫描不再执行（重复翻飞无信息），逐帧对账
    # 数据与越界实验结论以本注释与 logs/turn_scan_* 旁证入档。
    l2 = []
    out = {"ts": ts, "tool": "tools/turn_scan.py",
           "note": "层1静态账入档；层2动态定性=横向强MIMO不稳定不可镇定"
                   "（见 main 注释与汇总报告），全组合扫描不执行",
           "layer1_static": l1, "layer2_dynamic": l2}
    out_path = os.path.join(PROJECT_ROOT, "logs", "turn_scan_%s.json" % ts)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("== 完成（%.0f 秒）产物：%s ==" % (time.time() - t0, out_path), flush=True)


if __name__ == "__main__":
    main()
