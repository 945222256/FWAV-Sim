# -*- coding: utf-8 -*-
"""
harness_matrix.py — 高度回路结构裁定矩阵（第五班，照另一会话四步方案第一步）

平台迁移依据：同步周期平均（第四班）后，在环与试验架力口径按构造一致，
结构裁定在试验架上执行（一轮数秒、零 Isaac 启动）。在环失败均为瞬态恢复失败，
故矩阵必须含瞬态注入场景，名义场景测不出。

场景：nominal / climb+2 / dive−2 / high+1.5 / low−1.5 / fast5.5
候选：baseline / A: TECS_VF_K∈{0.2,0.4,0.6,0.8} / B: KF_P 反号
用法： isaac python.bat tools/harness_matrix.py
产物：  logs/harness_matrix.json + 控制台表
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

sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))
import test_flight_controller as H          # 复用其 model/tail/常数与力函数
from modules.flight_controller import FlightController
import config.sim_config as cfg

DT = H.DT
MASS, IXX, IYY, G = H.MASS, H.IXX, H.IYY, H.G

SCEN = [
    ("nominal",  4.0, 0.0, 3.0),
    ("climb+2",  4.0, +2.0, 3.0),
    ("dive-2",   4.0, -2.0, 3.0),
    ("high+1.5", 4.0, 0.0, 4.5),
    ("low-1.5",  4.0, 0.0, 1.5),
    ("fast5.5",  5.5, 0.0, 3.0),
    ("slow1+cl2", 1.0, +2.0, 3.0),
    ("corner",   0.5, 0.0, 4.8),   # 在环 181341 坠机精确状态
]

CANDS = [
    ("baseline", {}),
    ("E_vfloor", {"TECS_VFLOOR_ON": True}),
]


def run_case(vx0, vz0, z0, dur=70.0):
    ctl = FlightController()
    ctl._freq_last = 1.55
    x, z = 0.0, z0
    vx, vz = vx0, vz0
    roll, wx = 0.05, 0.0
    pitch, wy = -0.05, 0.0
    crash_t = None
    traj = []
    n_steps = int(dur / DT)
    for k in range(n_steps):
        t = k * DT
        cr, sr = math.cos(roll / 2), math.sin(roll / 2)
        cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
        pose = [x, 0.0, z, cr * cp, sr * cp, cr * sp, 0.0]
        vel = [vx, 0.0, vz, wx, wy, 0.0]
        out = ctl.update(pose, vel, t_sim=t)
        H.tail.set_command(elevator=out["elevator"],
                           aileron=out["aileron"], rudder=out["rudder"])
        F, T = H.cycle_mean_wrench(x, z, vx, vz, roll, wx, pitch, wy,
                                   out["freq"], out["dA"], n=16)
        vx += (F[0] / MASS) * DT
        vz += (F[2] / MASS - G) * DT
        wx += (T[0] / IXX) * DT
        wy += (T[1] / IYY) * DT
        wx = float(np.clip(wx, -3.0, 3.0))
        wy = float(np.clip(wy, -3.0, 3.0))
        roll += wx * DT
        pitch += wy * DT
        roll = (roll + math.pi) % (2 * math.pi) - math.pi
        pitch = (pitch + math.pi) % (2 * math.pi) - math.pi
        x += vx * DT
        z += vz * DT
        traj.append((t, z, vx, vz, roll, pitch))
        if not all(math.isfinite(v) for v in (x, z, vx, vz)):
            return dict(crash="nonfinite", t=t)
        if z <= 0.02:
            return dict(crash="ground", t=t)
    last = traj[-int(10 / DT):]
    m = lambda i: sum(r[i] for r in last) / len(last)
    mx = lambda i: max(abs(r[i]) for r in last)
    late = [r for r in traj if r[0] >= dur / 2]
    return dict(
        crash=None,
        z=m(1), vx=m(2), vz=m(3), roll_max=mx(4), pitch=m(5),
        z_dev_max=max(abs(r[1] - 3.0) for r in late),
        settle_z=max(abs(r[1] - 3.0) for r in late))


def apply_cand(params):
    f = cfg.FlightControllerConfig
    saved = {k: getattr(f, k) for k in params}
    for k, v in params.items():
        setattr(f, k, v)
    return saved


def restore(saved):
    f = cfg.FlightControllerConfig
    for k, v in saved.items():
        setattr(f, k, v)


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    results = {}
    print(f"[matrix] {ts}  判据：末10s |z−3|≤0.8、|vx−4|≤1、|roll|max≤0.1、"
          f"半程后 z 最大偏差≤1.5、不坠地", flush=True)
    hdr = f"{'候选':10s} " + " ".join(f"{s[0]:>10s}" for s in SCEN)
    print(hdr, flush=True)
    for cname, params in CANDS:
        saved = apply_cand(params)
        row = []
        for sname, vx0, vz0, z0 in SCEN:
            r = run_case(vx0, vz0, z0)
            if r.get("crash"):
                ok = False
                cell = f"✗{r['crash']}@{r['t']:.0f}s"
            else:
                ok = (abs(r["z"] - 3.0) <= 0.8 and abs(r["vx"] - 4.0) <= 1.0
                      and r["roll_max"] <= 0.1 and r["z_dev_max"] <= 1.5)
                cell = ("✓" if ok else "△") + f"z{r['z']:.1f}"
            row.append((sname, ok, r, cell))
            results.setdefault(cname, {})[sname] = r
        restore(saved)
        npass = sum(1 for _, ok, _, _ in row if ok)
        cells = " ".join(f"{c:>10s}" for _, _, _, c in row)
        print(f"{cname:10s} {cells}  通过 {npass}/{len(SCEN)}", flush=True)
    with open(os.path.join(cfg.LOG_DIR, "harness_matrix.json"), "w",
              encoding="utf-8") as fp:
        json.dump(results, fp, ensure_ascii=False, indent=1)
    print("[matrix] 完成 → logs/harness_matrix.json", flush=True)


if __name__ == "__main__":
    main()
