# -*- coding: utf-8 -*-
"""第五十六班诊断（只读）：50 个 LHS 随机波形的标定剪枝分布 + 进仿真组七判据卡死统计。

纪律：本脚本只 import tools/unified_search_framework.py，不改其文件本体。
垫片说明：框架 ClosedLoopSim.run 第 202 行在首次迭代引用 vz_des（第 218 行才赋值），
恒抛 UnboundLocalError，即框架 v0.2 的闭环从未真正跑过（logs/unified_search_v02.json
n_feas=0 那轮因此从未进入仿真段）。本脚本以子类 ShimSim 复制 run() 全文并仅插入
一行 vz_des = 0.0 冷启动初值（与 integ/filt/z_int 同口径），运行时绑定替换；
脚本内置逐行源码自校验（剔除 [SHIM] 标记行后必须与框架原文完全一致），框架文件零改动。
"""
import os, sys, json, math, time, inspect, datetime
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for p in (PROJECT_ROOT, TOOLS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import unified_search_framework as uf
from unified_search_framework import (
    quat_norm, quat_dot, euler_to_quat, quat_to_rotation_matrix,
    set_waveform, wing_forces_body, forces_total,
    T0, RELEASE_DROP, W, MASS, I_DIAG, tail, model)


class ShimSim(uf.ClosedLoopSim):
    """run() 全文逐字复制自 unified_search_framework.ClosedLoopSim.run，
    仅插入一行 vz_des = 0.0（带 [SHIM] 标记，自校验时剔除后与原文比对）。"""

    def run(self, t_end=60.0, dt=0.002):
        quat = quat_norm(euler_to_quat(0.0, self.th - RELEASE_DROP, 0.0))
        v = np.array([self.w["speed"], 0.0, 0.0])
        w = np.zeros(3)
        pos = np.array([0.0, 0.0, 3.0])
        f_cmd = self.w["freq"]
        integ = 0.0
        filt = 0.0
        z_int = 0.0
        vz_des = 0.0  # [SHIM]
        n = int(round(t_end / dt))
        z_min = 3.0
        z_max = 3.0
        dead_s = None
        log = dict(z=[], th=[], frzw=[], elev=[], freq=[])
        frz_buf = []
        t = T0[0]
        set_waveform(self.w)
        for i in range(n):
            th = math.asin(max(-1.0, min(1.0,
                    2.0 * (quat[0] * quat[2] - quat[1] * quat[3]))))
            th_tgt = self.th + float(np.clip(0.8 * (vz_des - v[2]) * 0.15, -self.c["th_bias_cap"], self.c["th_bias_cap"]))
            th_err = th_tgt - th
            if abs(th_err) < self.c["int_gate"]:
                integ += th_err * dt
            else:
                integ *= (1.0 - 0.5 * dt)
            filt += (w[1] - filt) * dt / max(self.c["rate_filt_tau"], 1e-4)
            elev_cmd = float(np.clip(
                self.c["alpha_ff"] + self.c["kp_a"] * th_err
                - self.c["pitch_damp"] * filt + self.c["ki_a"] * integ,
                -0.30, 0.30))
            e_act = self.elev.step(elev_cmd, dt)
            tail.set_command(elevator=e_act)
            z_err = 3.0 - pos[2]
            z_int += z_err * dt
            z_int = float(np.clip(z_int, -2.0 * self.c["vz_cap"] / max(self.c["alt_ki"], 1e-3), 2.0 * self.c["vz_cap"] / max(self.c["alt_ki"], 1e-3)))
            vz_des = float(np.clip(self.c["alt_kp"] * z_err + self.c["alt_ki"] * z_int, -self.c["vz_cap"], self.c["vz_cap"]))
            v_for_fb = v[0] + 0.30 * math.sin(2 * math.pi * f_cmd * (i * dt))
            vxd = self.w["speed"]
            freq_tgt = float(np.clip(
                self.w["freq"] + self.c["tecs_kv_f"] * (vxd - v_for_fb)
                + 0.0 * vz_des, 1.0, 3.5))
            df = float(np.clip(freq_tgt - f_cmd, -self.c["freq_slew"] * dt, self.c["freq_slew"] * dt))
            f_cmd += df
            model.set_wing_command(frequency=f_cmd)
            om_world = quat_to_rotation_matrix(quat) @ w
            F, Tq = forces_total(quat, v, om_world, t, f_cmd)
            F = F + np.array([0.0, 0.0, -W])
            v = v + F / MASS * dt
            w = w + np.linalg.solve(I_DIAG, Tq - np.cross(w, I_DIAG @ w)) * dt
            quat = quat_norm(quat + quat_dot(quat, w) * dt)
            pos = pos + v * dt
            t += dt
            z_min = min(z_min, pos[2])
            z_max = max(z_max, pos[2])
            Fw, _ = wing_forces_body(quat, v, om_world, t)
            frz = float((quat_to_rotation_matrix(quat) @ Fw)[2])
            frz_buf.append(frz)
            if i % 30 == 0:
                log["z"].append(pos[2])
                log["th"].append(th)
                log["frzw"].append(frz / W)
                log["elev"].append(e_act)
                log["freq"].append(f_cmd)
            if pos[2] < 0.2:
                dead_s = i * dt
                break
        med = lambda a: float(np.median(a)) if len(a) else 0.0
        tail_f = frz_buf[-300:]
        return dict(survive_s=dead_s if dead_s is not None else t_end,
                    z_min=z_min, z_max=z_max, z_end=float(pos[2]), th_med=med(log["th"]),
                    frzw_med=float(np.mean(tail_f)) / W if tail_f else 0.0,
                    elev_rms=float(np.sqrt(np.mean(np.square(log["elev"])))) \
                        if log["elev"] else 0.0,
                    freq_dev=abs(med(log["freq"]) - self.w["freq"]))


def verify_shim():
    o = [l.rstrip() for l in inspect.getsource(uf.ClosedLoopSim.run).strip().splitlines()]
    m = [l.rstrip() for l in inspect.getsource(ShimSim.run).strip().splitlines()
         if not l.rstrip().endswith("# [SHIM]")]
    if o != m:
        for i, (a, b) in enumerate(zip(o, m)):
            if a != b:
                print("DRIFT line %d:\n  fw : %r\n  shim: %r" % (i, a, b))
        if len(o) != len(m):
            print("line count fw=%d shim=%d" % (len(o), len(m)))
        raise SystemExit("SHIM BODY DRIFT - abort")
    return True


CRIT_LABELS = ["c1_need<=cap", "c2_frzw>=0.95", "c3_survive_band",
               "c4_|th-th*|<=0.05", "c5_elev_rms<=0.21", "c6_freq_dev<=0.3267",
               "c7_dfx_df>0"]

N_WAVE = 50
N_CTRL = 4
SEED = 11
SIM_T = 20.0


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    verify_shim()
    print("shim self-check PASS (run() body identical to framework except vz_des init)",
          flush=True)
    at = uf.anchor_tests()
    print("anchors: %s" % json.dumps({k: [v[0], v[1]] for k, v in at.items()}),
          flush=True)
    anchors_ok = all(v[1] == "PASS" for v in at.values())

    rng = np.random.default_rng(SEED)
    wdom = {k: uf.DOMAINS[k] for k in
            ("alpha_set", "mean", "split", "lag", "amp", "freq", "speed")}
    cdom = {k: uf.DOMAINS[k] for k in
            ("kp_a", "pitch_damp", "ki_a", "int_gate", "rate_filt_tau",
             "alpha_ff", "tecs_kv_f", "alt_kp", "alt_ki", "vz_cap",
             "th_bias_cap", "freq_slew")}
    waves = uf.lhs_sample(wdom, N_WAVE, rng)

    stats = dict(n_wave=N_WAVE, drive_limit=0, need_fail=0, dfx_fail=0,
                 both_fail=0, cal_pass=0, n_sims=0)
    crit_fail = [0] * 7
    crit_pass_all_but = [0] * 7
    n_js20_all = 0
    t_cals = []
    t_sims = []
    records = []
    best = dict(survive_s=None, frzw=None, th_err=None, elev_rms=None,
                freq_dev=None, z_end=None)
    best_ctx = {}

    for wi, wv in enumerate(waves):
        if abs(wv["mean"]) + wv["amp"] > 80.2:
            stats["drive_limit"] += 1
            continue
        t0 = time.time()
        cal = uf.calibrate_waveform(dict(wv))
        t_cal = time.time() - t0
        t_cals.append(t_cal)
        f_need = cal["need"] > cal["cap"]
        f_dfx = cal["dfx_df"] <= 0.0
        rec = dict(idx=wi, wave=wv, t_cal=round(t_cal, 3),
                   cal={k: float(v) for k, v in cal.items()})
        if f_need:
            stats["need_fail"] += 1
        if f_dfx:
            stats["dfx_fail"] += 1
        if f_need and f_dfx:
            stats["both_fail"] += 1
        if f_need or f_dfx:
            records.append(rec)
            if wi % 10 == 0:
                print("wave %d/%d cal-DEAD need=%d dfx=%d pass=%d (%.2fs)"
                      % (wi, N_WAVE, stats["need_fail"], stats["dfx_fail"],
                         stats["cal_pass"], t_cal), flush=True)
            continue
        stats["cal_pass"] += 1
        th_rad = math.radians(cal["th_star"])
        sims = []
        for cv in uf.lhs_sample(cdom, N_CTRL, rng):
            t0 = time.time()
            out = ShimSim(wv, cv, th_rad, uf.TAU_ELEV).run(t_end=SIM_T)
            t_sim = time.time() - t0
            t_sims.append(t_sim)
            stats["n_sims"] += 1
            js_raw = list(uf.judge(cal, out, th_rad))
            js20 = list(js_raw)
            js20[2] = (out["survive_s"] >= SIM_T and out["z_min"] >= 2.0
                       and out["z_max"] <= 8.0)
            for ci in range(7):
                if not js20[ci]:
                    crit_fail[ci] += 1
            if all(js20):
                n_js20_all += 1
            else:
                for ci in range(7):
                    if all(js20[:ci] + js20[ci + 1:]):
                        crit_pass_all_but[ci] += 1
            so = {k: float(v) for k, v in out.items()}
            sims.append(dict(ctrl=cv, out=so, js20=[bool(x) for x in js20],
                             t_sim=round(t_sim, 3)))
            th_err = abs(so["th_med"] - th_rad)
            if best["survive_s"] is None or so["survive_s"] > best["survive_s"]:
                best["survive_s"] = so["survive_s"]
                best_ctx["survive"] = dict(idx=wi, wave=wv, ctrl=cv, out=so)
            if best["frzw"] is None or so["frzw_med"] > best["frzw"]:
                best["frzw"] = so["frzw_med"]
                best_ctx["frzw"] = dict(idx=wi, wave=wv, out=so)
            if best["th_err"] is None or th_err < best["th_err"]:
                best["th_err"] = th_err
                best_ctx["th"] = dict(idx=wi, wave=wv, out=so, th_star_rad=th_rad)
            if best["elev_rms"] is None or so["elev_rms"] < best["elev_rms"]:
                best["elev_rms"] = so["elev_rms"]
            if best["freq_dev"] is None or so["freq_dev"] < best["freq_dev"]:
                best["freq_dev"] = so["freq_dev"]
            if best["z_end"] is None or so["z_end"] > best["z_end"]:
                best["z_end"] = so["z_end"]
                best_ctx["zend"] = dict(idx=wi, wave=wv, out=so)
        rec["sims"] = sims
        rec["crit_fail_20s"] = crit_fail[:]
        records.append(rec)
        print("wave %d/%d CAL-PASS th*=%.2f need/cap=%.2f dfx=%.3f | sims=%d"
              % (wi, N_WAVE, cal["th_star"], cal["need"] / cal["cap"],
                 cal["dfx_df"], len(sims)), flush=True)

    t_cal_mean = float(np.mean(t_cals)) if t_cals else 0.0
    t_cal_max = float(np.max(t_cals)) if t_cals else 0.0
    t_sim_mean = float(np.mean(t_sims)) if t_sims else 0.0
    t_sim_max = float(np.max(t_sims)) if t_sims else 0.0
    p_pass = stats["cal_pass"] / float(N_WAVE)

    def est(nw, nc):
        return nw * t_cal_mean + nw * p_pass * nc * t_sim_mean

    sizing = dict(
        t_cal_mean=t_cal_mean, t_cal_max=t_cal_max,
        t_sim20_mean=t_sim_mean, t_sim20_max=t_sim_max,
        cal_pass_rate=p_pass,
        est_400x16_s=est(400, 16), est_200x8_s=est(200, 8),
        est_150x8_s=est(150, 8), est_100x8_s=est(100, 8),
        est_100x4_s=est(100, 4))

    out = dict(ts=ts, anchors={k: v[0] for k, v in at.items()},
               anchors_ok=anchors_ok, stats=stats,
               crit_fail_20s=dict(zip(CRIT_LABELS, crit_fail)),
               crit_sole_fail_20s=dict(zip(CRIT_LABELS, crit_pass_all_but)),
               n_js20_all=n_js20_all, best=best, best_ctx=best_ctx,
               sizing=sizing, records=records)
    op = os.path.join(PROJECT_ROOT, "logs", "unified_big_diag.json")
    with open(op, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("==== DIAG SUMMARY ====", flush=True)
    print("waves=%d drive_limit=%d need_fail=%d dfx_fail=%d both=%d cal_pass=%d"
          % (N_WAVE, stats["drive_limit"], stats["need_fail"], stats["dfx_fail"],
             stats["both_fail"], stats["cal_pass"]), flush=True)
    print("sims=%d  crit_fail(20s): %s" % (stats["n_sims"],
          json.dumps(dict(zip(CRIT_LABELS, crit_fail)))), flush=True)
    print("best: %s" % json.dumps({k: (round(v, 4) if v is not None else None)
                                    for k, v in best.items()}), flush=True)
    print("sizing: %s" % json.dumps({k: round(v, 1) for k, v in sizing.items()}),
          flush=True)
    print("saved %s" % op, flush=True)


if __name__ == "__main__":
    main()
