# -*- coding: utf-8 -*-
"""第五十六班主搜索 runner：只 import tools/unified_search_framework.py，调 uf.search()。

纪律：不改 unified_search_framework.py 文件本体，不做 git 操作，不跑 Isaac。
垫片说明（与 tools/_diag_unified_big.py 同源同校验）：框架 ClosedLoopSim.run
第 202 行在首次迭代引用 vz_des（第 218 行才赋值），恒抛 UnboundLocalError；
本 runner 以子类 ShimSim 逐字复制 run() 全文并仅插入一行 vz_des = 0.0 冷启动
初值（与 integ/filt/z_int 同口径），运行时绑定 uf.ClosedLoopSim = RecordingSim；
逐行源码自校验（剔除 [SHIM] 标记行后必须与框架原文完全一致）不过即中止。
记录层：RecordingSim.run 仅在 ShimSim.run 返回后追加记录（数值零改动）；
calibrate_waveform 包一层纯透传记录。两者只为产物 JSON 提供数据。
用法：python tools/_run_unified_big.py [n_wave] [n_ctrl]   （默认 400 16，seed=11 固定）
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

SEED = 11
REC = []
CALS = []
CKPT_PATH = os.path.join(PROJECT_ROOT, "logs", "unified_big_partial.json")


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


class RecordingSim(ShimSim):
    """纯记录层：委托 ShimSim.run，数值零改动。"""

    def run(self, t_end=60.0, dt=0.002):
        out = ShimSim.run(self, t_end=t_end, dt=dt)
        REC.append(dict(wave=dict(self.w), ctrl=dict(self.c),
                        th_star=float(self.th), t_end=float(t_end),
                        out={k: float(v) for k, v in out.items()}))
        if len(REC) % 100 == 0:
            try:
                with open(CKPT_PATH, "w", encoding="utf-8") as fp:
                    json.dump(dict(n_rec=len(REC), n_cal=len(CALS),
                                   updated=datetime.datetime.now().isoformat()),
                              fp)
            except Exception:
                pass
        return out


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


_cal_orig = uf.calibrate_waveform


def _cal_rec(p):
    cal = _cal_orig(p)
    CALS.append(dict(wave=dict(p), cal={k: float(v) for k, v in cal.items()}))
    return cal


CRIT_LABELS = ["c1_need<=cap", "c2_frzw>=0.95", "c3_survive_band",
               "c4_|th-th*|<=0.05", "c5_elev_rms<=0.21", "c6_freq_dev<=0.3267",
               "c7_dfx_df>0"]


def summarize(recs):
    """对记录层产物做七判据统计（judge 逻辑以 uf.judge 原函数评估）。"""
    cal_by_wave = {}
    for c in CALS:
        cal_by_wave[json.dumps(c["wave"], sort_keys=True)] = c["cal"]
    stats = dict(n_sims=len(recs), n_sim20=0, n_sim60=0)
    crit_fail20 = [0] * 7
    crit_fail60 = [0] * 7
    sole_fail20 = [0] * 7
    n_all20 = 0
    n_all60 = 0
    best = dict(survive_s=None, frzw_med=None, th_err=None, elev_rms=None,
                freq_dev=None, z_end=None)
    best_ctx = {}
    for r in recs:
        cal = cal_by_wave.get(json.dumps(r["wave"], sort_keys=True))
        out = r["out"]
        th_rad = r["th_star"]
        js = list(uf.judge(cal, out, th_rad)) if cal is not None else [None] * 7
        js20 = list(js)
        js20[2] = (out["survive_s"] >= r["t_end"] - 1e-9
                   and out["z_min"] >= 2.0 and out["z_max"] <= 8.0)
        if r["t_end"] <= 20.5:
            stats["n_sim20"] += 1
            for ci in range(7):
                if js20[ci] is False:
                    crit_fail20[ci] += 1
            if all(js20):
                n_all20 += 1
            else:
                for ci in range(7):
                    if all(x is True for x in js20[:ci] + js20[ci + 1:]):
                        sole_fail20[ci] += 1
        else:
            stats["n_sim60"] += 1
            for ci in range(7):
                if js[ci] is False:
                    crit_fail60[ci] += 1
            if all(js):
                n_all60 += 1
        th_err = abs(out["th_med"] - th_rad)
        if best["survive_s"] is None or out["survive_s"] > best["survive_s"]:
            best["survive_s"] = out["survive_s"]
            best_ctx["survive"] = dict(wave=r["wave"], ctrl=r["ctrl"], out=out)
        if best["frzw_med"] is None or out["frzw_med"] > best["frzw_med"]:
            best["frzw_med"] = out["frzw_med"]
            best_ctx["frzw"] = dict(wave=r["wave"], out=out)
        if best["th_err"] is None or th_err < best["th_err"]:
            best["th_err"] = th_err
            best_ctx["th"] = dict(wave=r["wave"], out=out, th_star_rad=th_rad)
        if best["elev_rms"] is None or out["elev_rms"] < best["elev_rms"]:
            best["elev_rms"] = out["elev_rms"]
        if best["freq_dev"] is None or out["freq_dev"] < best["freq_dev"]:
            best["freq_dev"] = out["freq_dev"]
        if best["z_end"] is None or out["z_end"] > best["z_end"]:
            best["z_end"] = out["z_end"]
            best_ctx["zend"] = dict(wave=r["wave"], out=out)
    stats["crit_fail20"] = dict(zip(CRIT_LABELS, crit_fail20))
    stats["crit_fail60"] = dict(zip(CRIT_LABELS, crit_fail60))
    stats["sole_fail20"] = dict(zip(CRIT_LABELS, sole_fail20))
    stats["n_all20"] = n_all20
    stats["n_all60"] = n_all60
    return stats, best, best_ctx


def prune_stats():
    n_cal = len(CALS)
    need_fail = sum(1 for c in CALS if c["cal"]["need"] > c["cal"]["cap"])
    dfx_fail = sum(1 for c in CALS if c["cal"]["dfx_df"] <= 0.0)
    both = sum(1 for c in CALS
               if c["cal"]["need"] > c["cal"]["cap"] and c["cal"]["dfx_df"] <= 0.0)
    pas = n_cal - need_fail - dfx_fail + both
    return dict(n_cal=n_cal, need_fail=need_fail, dfx_fail=dfx_fail,
                both_fail=both, cal_pass=pas)


def cal_summary():
    """标定层分布摘要（供零可行时机理归因）。"""
    if not CALS:
        return {}
    ratios = sorted(c["cal"]["need"] / c["cal"]["cap"] for c in CALS)
    mys = [c["cal"]["my_star"] for c in CALS]
    ths = [c["cal"]["th_star"] for c in CALS]
    dfx = [c["cal"]["dfx_df"] for c in CALS]
    k = lambda a, q: float(a[min(len(a) - 1, int(q * len(a)))])
    return dict(
        n=len(CALS),
        need_cap_ratio_min=round(ratios[0], 3),
        need_cap_ratio_p25=round(k(ratios, 0.25), 3),
        need_cap_ratio_med=round(k(ratios, 0.50), 3),
        need_cap_ratio_p75=round(k(ratios, 0.75), 3),
        need_cap_ratio_max=round(ratios[-1], 3),
        need_cap_below_2=int(sum(1 for r in ratios if r < 2.0)),
        need_cap_below_3=int(sum(1 for r in ratios if r < 3.0)),
        my_star_min=round(min(mys), 5), my_star_max=round(max(mys), 5),
        my_star_neg=int(sum(1 for v in mys if v < 0.0)),
        my_star_pos=int(sum(1 for v in mys if v >= 0.0)),
        th_star_at_hi_bound=int(sum(1 for v in ths if v > 44.9)),
        th_star_at_lo_bound=int(sum(1 for v in ths if v < -9.9)),
        th_star_interior=int(sum(1 for v in ths if -9.9 <= v <= 44.9)),
        dfx_pos=int(sum(1 for v in dfx if v > 0.0)),
        dfx_neg=int(sum(1 for v in dfx if v <= 0.0)))


def main():
    n_wave = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    n_ctrl = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    verify_shim()
    print("shim self-check PASS; config n_wave=%d seed=%d n_ctrl=%d"
          % (n_wave, SEED, n_ctrl), flush=True)
    at = uf.anchor_tests()
    print("anchors: %s" % json.dumps({k: [v[0], v[1]] for k, v in at.items()}),
          flush=True)
    anchors_ok = all(v[1] == "PASS" for v in at.values())
    if not anchors_ok:
        print("ANCHOR FAIL - abort", flush=True)
        return
    uf.ClosedLoopSim = RecordingSim
    uf.calibrate_waveform = _cal_rec
    t0 = time.time()
    try:
        feas = uf.search(n_wave=n_wave, seed=SEED, n_ctrl=n_ctrl)
    finally:
        elapsed = time.time() - t0
        print("search returned/aborted after %.1f s" % elapsed, flush=True)
    ps = prune_stats()
    st, best, best_ctx = summarize(REC)
    cs = cal_summary()
    feas_out = []
    for f in feas:
        th_rad = math.radians(f["cal"]["th_star"])
        js = list(uf.judge(f["cal"], f["sim"], th_rad))
        feas_out.append(dict(wave=f["wave"], ctrl=f["ctrl"],
                             cal={k: float(v) for k, v in f["cal"].items()},
                             sim={k: float(v) for k, v in f["sim"].items()},
                             judge=[bool(x) for x in js]))
    out = dict(ts=ts, framework="tools/unified_search_framework.py v0.2 (file untouched)",
               runtime_patch=("ClosedLoopSim.run vz_des=0.0 cold-start init via "
                              "subclass ShimSim; verbatim-body self-check PASS"),
               config=dict(n_wave=n_wave, seed=SEED, n_ctrl=n_ctrl,
                           sim20_s=20.0, confirm60_s=60.0),
               anchors={k: v[0] for k, v in at.items()}, anchors_ok=anchors_ok,
               elapsed_s=elapsed, n_feas=len(feas), feasible=feas_out,
               prune=ps, cal_summary=cs,
               cal_records=[dict(idx=i, wave=c["wave"], cal=c["cal"])
                            for i, c in enumerate(CALS)],
               sim_stats=st, best=best,
               best_ctx={k: v for k, v in best_ctx.items()})
    op = os.path.join(PROJECT_ROOT, "logs", "unified_big.json")
    with open(op, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("n_feas=%d elapsed=%.1f s prune=%s" % (len(feas), elapsed,
                                                 json.dumps(ps)), flush=True)
    print("cal_summary=%s" % json.dumps(cs), flush=True)
    print("sim_stats=%s" % json.dumps(st), flush=True)
    print("best=%s" % json.dumps({k: (round(v, 4) if v is not None else None)
                                  for k, v in best.items()}), flush=True)
    print("saved %s" % op, flush=True)


if __name__ == "__main__":
    main()
