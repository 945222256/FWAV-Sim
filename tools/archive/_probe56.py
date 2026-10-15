# -*- coding: utf-8 -*-
"""第五十六班并行标定探针（只读）：复现 uf.search 对给定 (n_wave, seed) 的波形
LHS 抽样（同一 rng 状态下的第一次 lhs_sample 调用，与 search 内部完全一致），
用 multiprocessing 并行跑 uf.calibrate_waveform，量出：驱动限位/判据①/判据⑦
剪枝分布、进仿真波形数及其全套标定读数；并在首个过标定波形上实测一次 20 s
闭环仿真耗时（ShimSim，同 _run_unified_big.py 垫片）。不改框架文件。
用法：python tools/_probe56.py [n_wave_list_csv] [workers]
"""
import os, sys, json, math, time, inspect, datetime
import numpy as np
import multiprocessing as mp

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

SEED = 11
WDOM_KEYS = ("alpha_set", "mean", "split", "lag", "amp", "freq", "speed")


class ShimSim(uf.ClosedLoopSim):
    """与 tools/_run_unified_big.py 同一垫片：run() 逐字复制 + vz_des=0.0 冷启动。"""

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


def _cal_one(args):
    idx, wv = args
    if abs(wv["mean"]) + wv["amp"] > 80.2:
        return dict(idx=idx, wave=wv, dead="drive_limit")
    t0 = time.time()
    cal = uf.calibrate_waveform(dict(wv))
    if cal["need"] > cal["cap"] and cal["dfx_df"] <= 0.0:
        dead = "need+dfx"
    elif cal["need"] > cal["cap"]:
        dead = "need"
    elif cal["dfx_df"] <= 0.0:
        dead = "dfx"
    else:
        dead = None
    return dict(idx=idx, wave=wv, dead=dead, t_cal=round(time.time() - t0, 3),
                cal={k: float(v) for k, v in cal.items()})


def probe_draw(n_wave, workers):
    rng = np.random.default_rng(SEED)
    wdom = {k: uf.DOMAINS[k] for k in WDOM_KEYS}
    waves = uf.lhs_sample(wdom, n_wave, rng)
    t0 = time.time()
    with mp.Pool(workers) as pool:
        recs = pool.map(_cal_one, list(enumerate(waves)), chunksize=4)
    wall = time.time() - t0
    deads = [r["dead"] for r in recs]
    passes = [r for r in recs if r["dead"] is None]
    summ = dict(n_wave=n_wave, seed=SEED, workers=workers, wall_s=round(wall, 1),
                drive_limit=deads.count("drive_limit"),
                need_only=deads.count("need"),
                dfx_only=deads.count("dfx"),
                need_and_dfx=deads.count("need+dfx"),
                cal_pass=len(passes),
                t_cal_mean=round(float(np.mean([r["t_cal"] for r in recs
                                                if "t_cal" in r])), 3))
    return summ, passes


def main():
    n_list = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1
                               else ["400", "200"])]
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = dict(ts=ts, probe="calibrate-only funnel per LHS draw (seed=11)")
    t_sim20 = None
    sim_probe = None
    for n in n_list:
        summ, passes = probe_draw(n, workers)
        out["draw_%d" % n] = dict(summary=summ,
                                  passes=[dict(idx=p["idx"], wave=p["wave"],
                                               cal=p["cal"]) for p in passes])
        print("draw n=%d: %s" % (n, json.dumps(summ)), flush=True)
        if t_sim20 is None and passes:
            p0 = passes[0]
            cv = dict(kp_a=2.0, pitch_damp=1.0, ki_a=0.1, int_gate=0.4,
                      rate_filt_tau=0.03, alpha_ff=0.1, tecs_kv_f=-1.0,
                      alt_kp=1.0, alt_ki=0.1, vz_cap=1.5, th_bias_cap=0.2,
                      freq_slew=5.0)
            th_rad = math.radians(p0["cal"]["th_star"])
            t0 = time.time()
            o = ShimSim(p0["wave"], cv, th_rad, uf.TAU_ELEV).run(t_end=20.0)
            t_sim20 = round(time.time() - t0, 2)
            sim_probe = dict(idx=p0["idx"], wave=p0["wave"],
                             cal=p0["cal"],
                             out={k: float(v) for k, v in o.items()},
                             t_sim20_s=t_sim20)
            print("sim probe on first passer: t_sim20=%.2f s out=%s"
                  % (t_sim20, json.dumps(sim_probe["out"])), flush=True)
    out["sim_probe"] = sim_probe
    op = os.path.join(PROJECT_ROOT, "logs", "unified_big_probe.json")
    with open(op, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("saved %s" % op, flush=True)


if __name__ == "__main__":
    main()
