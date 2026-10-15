# -*- coding: utf-8 -*-
import os, sys, json, math, time, datetime
import numpy as np
from multiprocessing import Pool
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from config.unified_search_config import UNIFIED_SEARCH as CFG
# [66班] 统一变换纪律：本件自身零旋转调用（grep 证据：quat/rot/euler
# 零命中），闭环内全部 WORLD↔BODY/四元数→欧拉/重力矩换算经下列导入
# 收敛到 tools/unified_search_framework.py 模块级唯一换算函数族
#（rot_body_to_world / rot_world_to_body / quat_pitch /
# gravity_moment_body，含方向断言与系别籍贯注释）。
from tools.unified_search_framework import (
    calibrate_waveform, judge, ClosedLoopSim, anchor_tests, set_waveform)
try:
    import cupy as xp
    GPU = True
except Exception:
    xp = np
    GPU = False

WAVE_KEYS = ["alpha_set", "mean", "split", "lag", "amp", "freq", "speed"]
CTRL_KEYS = ["kp_a", "pitch_damp", "ki_a", "int_gate", "rate_filt_tau",
             "alpha_ff", "tecs_kv_f", "alt_kp", "alt_ki", "vz_cap",
             "th_bias_cap", "freq_slew"]

def domains_for(keys):
    d = {}
    sd = CFG["search_domains"]
    pos = (sd["ctrl_pos_dims"]["lo"], sd["ctrl_pos_dims"]["hi"])
    sgn = (sd["ctrl_signed_dims"]["lo"], sd["ctrl_signed_dims"]["hi"])
    for k in keys:
        if k in sd:
            d[k] = (sd[k]["lo"], sd[k]["hi"])
        elif k in ("alpha_ff", "tecs_kv_f"):
            d[k] = sgn
        else:
            d[k] = pos
    return d

def hard_ok(w):
    hc = CFG["hard_constraints"]
    if not (w["split"] < hc["split_lt"]["value"]):
        return False
    if abs(w["mean"]) + w["amp"] > hc["drive_limit_deg"]["value"]:
        return False
    lo, hi = hc["freq_band"]["value"]
    if not (lo <= w["freq"] <= hi):
        return False
    return True

def violates(cal, sim, th_rad):
    c = CFG["criteria"]
    v = []
    v.append(max(0.0, cal["need"] / (c["need_frac"] * cal["cap"]) - 1.0)
             if cal["cap"] > 0 else 1.0)
    v.append(max(0.0, 1.0 - sim["frzw_med"] / c["lift_min"]))
    zlo, zhi = c["z_band"]
    v.append(max(0.0, (zlo - sim["z_min"]) / 6.0,
                 (sim["z_max"] - zhi) / 6.0, (60.0 - sim["survive_s"]) / 60.0))
    v.append(max(0.0, abs(sim["th_med"] - th_rad) - c["th_tol"]))
    v.append(max(0.0, sim["elev_rms"] / (c["elev_rms_frac"] * 0.30) - 1.0))
    v.append(max(0.0, sim["freq_dev"] / c["freq_dev_tol"] - 1.0))
    v.append(max(0.0, -cal["dfx_df"]))
    return v

def score(v):
    return float(sum(v))

# [I-080 收编] TPE 三段定义移入 tools/optimizers.py 唯一模块，
# 本文件改为导入转发（sample_rng、hist_draw、TPE 名字不变，行为不变，
# 契约测试以 AST 对照作证零改动）。
from tools.optimizers import TPE, hist_draw, sample_rng

def eval_point(args):
    w, c = args
    cal = calibrate_waveform(w)
    if cal["need"] > CFG["criteria"]["need_frac"] * cal["cap"] or \
       cal["dfx_df"] <= 0.0:
        return (w, c, None, None, 10.0, False)
    th = math.radians(cal["th_star"])
    sim = ClosedLoopSim(w, c, th, 0.02).run(
        t_end=CFG["time_grids"]["prescreen_s"]["value"])
    v = violates(cal, sim, th)
    if score(v) <= 0.0:
        sim = ClosedLoopSim(w, c, th, 0.02).run(
            t_end=CFG["time_grids"]["final_s"]["value"])
        v = violates(cal, sim, th)
    return (w, c, cal, sim, score(v), score(v) <= 0.0)

def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    at = anchor_tests()
    print("anchors:", {k: v[1] for k, v in at.items()}, flush=True)
    if not all(str(v[1]).startswith(("PASS", "SKIP")) for v in at.values()):
        print("ANCHOR BLOCK", flush=True)
        return
    eng = CFG["engine"]
    wdom = domains_for(WAVE_KEYS)
    cdom = domains_for(CTRL_KEYS)
    tpe = TPE(wdom, seed=23)
    rng = np.random.default_rng(99)
    feas = []
    t0 = time.time()
    for batch in range(eng["n_batches"]):
        cands = [w for w in tpe.ask_batch(eng["batch"]) if hard_ok(w)]
        guard = 0
        while len(cands) < eng["batch"] // 2 and guard < 200:
            w = sample_rng(rng, wdom)
            if hard_ok(w):
                cands.append(w)
            guard += 1
        jobs = [(w, sample_rng(rng, cdom)) for w in cands
                for _ in range(eng["n_ctrl_inner"])]
        with Pool(eng["procs"]) as pool:
            res = pool.map(eval_point, jobs, chunksize=4)
        pts, ys = [], []
        for w, c, cal, sim, sc, ok in res:
            pts.append(w)
            ys.append(sc)
            if ok:
                feas.append(dict(wave=w, ctrl=c, cal=cal, sim=sim))
        tpe.tell(pts, ys)
        print("batch %d feas=%d best=%.4f t=%.0fs gpu=%s" % (
            batch, len(feas), min(ys) if ys else -1, time.time() - t0,
            GPU), flush=True)
        if feas and batch >= 8:
            break
    out = dict(ts=ts, gpu=GPU, n_feas=len(feas),
               feasible=feas[:20],
               best=min(feas, key=lambda f: f["cal"]["need"]) if feas else None)
    with open(os.path.join(PROJECT_ROOT, "logs", "unified_v3.json"),
              "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    print("DONE feas=%d" % len(feas), flush=True)
    if feas:
        print("best=", json.dumps(out["best"], default=float)[:600], flush=True)

if __name__ == "__main__":
    main()
