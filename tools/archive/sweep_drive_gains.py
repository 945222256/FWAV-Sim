# -*- coding: utf-8 -*-
"""
sweep_drive_gains.py — 翼面位置驱动 K/D 增益扫描（步骤7，甲项 A1 口径）

方法：全钉固台架（基座位姿逐步钉死、速度清零），无气动力注入（气动力
施加于基座，被钉固吸收，与关节跟踪无关），WingKinematics 恒频波形驱动
9 关节位置目标。记录 flap_L/flap_R 的目标与实际角，指标：
  ratio = std(actual)/std(target)（判据 ≈1）
  peak  = max|actual|（判据远离 ±1.4 rad 机械限位）
用法： python.bat tools/sweep_drive_gains.py <K> <D> [freq=2.5] [dur=12]
产物：  logs/drive_sweep_K<K>_D<D>.json（外层 bash 循环汇总进 md）
"""
import os
import sys
import json
import math
import statistics

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

K = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
D = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
FREQ = float(sys.argv[3]) if len(sys.argv) > 3 else 2.5
DUR = float(sys.argv[4]) if len(sys.argv) > 4 else 12.0

from config import sim_config as cfg

_app = dict(cfg.SIM_APP_CONFIG)
_app["headless"] = True
from isaacsim import SimulationApp  # noqa: E402
simulation_app = SimulationApp(_app)

import numpy as np  # noqa: E402
from isaacsim.core.api import World  # noqa: E402
from isaacsim.core.prims import Articulation  # noqa: E402
import modules.flapper_loader as flapper_loader  # noqa: E402
from modules.aero_solver import WingKinematics  # noqa: E402
from config.sim_config import (WING_DRIVE_CONFIG,  # noqa: E402
                               STARTUP_AMP_RAMP_S)

# 增益覆盖（必须在 load_flapper 之前——loader 把增益写进 USD DriveAPI）
flapper_loader.JOINT_DRIVE_STIFFNESS = K
flapper_loader.JOINT_DRIVE_DAMPING = D

DT = cfg.PHYSICS_DT
PIN_POS = np.array(cfg.INITIAL_POSITION, dtype=np.float32)
PIN_QUAT = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


def main():
    world = World(stage_units_in_meters=1.0, physics_dt=DT, rendering_dt=DT)
    flapper_loader.load_flapper(world)
    loader = flapper_loader.get_loader()
    root_api = loader.articulation_root_api_path or ""
    art_path = root_api.rsplit("/", 1)[0] if root_api.endswith("root_joint") \
        else (root_api or cfg.FLAPPER_PRIM_PATH)
    art = Articulation(art_path, name="sweep_art")
    world.scene.add(art)
    world.reset()

    names = list(art.joint_names or [])
    idx = {n: i for i, n in enumerate(names)}

    wk = WingKinematics()
    wk.set_frequency(FREQ)
    base_view = loader.rigid_prim  # base_link RigidPrim（钉固用）

    tgt_log = {"flap_L": [], "flap_R": []}
    act_log = {"flap_L": [], "flap_R": []}
    n_steps = int(DUR / DT)
    settle = int(4.0 / DT)   # 4s 软启动+暂态，之后开始统计

    for step in range(n_steps):
        t = step * DT
        # 全钉固：基座位姿逐步写回、速度清零（A1 口径）
        base_view.set_world_poses(positions=PIN_POS.reshape(1, 3),
                                  orientations=PIN_QUAT.reshape(1, 4))
        base_view.set_velocities(np.zeros((1, 6), dtype=np.float32))

        w = wk.evaluate(t)
        phi, alpha = w["phi"], w["alpha"]
        fold = WING_DRIVE_CONFIG["fold_amp"] * 0.5 * \
            (1.0 + math.sin(wk.omega * t + 2.2))
        _ramp = min(1.0, t / max(STARTUP_AMP_RAMP_S, 0.05))
        phi, alpha, fold = phi * _ramp, alpha * _ramp, fold * _ramp
        targets = {"flap_L": phi, "flap_R": -phi,
                   "twist_L": alpha, "twist_R": alpha,
                   "fold_L": fold, "fold_R": -fold}
        pos_t = np.zeros(len(names), dtype=np.float32)
        for n, v in targets.items():
            if n in idx:
                pos_t[idx[n]] = v
        art.set_joint_position_targets(pos_t)

        world.step(render=False)
        if step >= settle:
            jp = np.asarray(art.get_joint_positions(), dtype=float).ravel()
            for n in ("flap_L", "flap_R"):
                if n in idx:
                    tgt_log[n].append(float(pos_t[idx[n]]))
                    act_log[n].append(float(jp[idx[n]]))

    res = dict(K=K, D=D, freq=FREQ, dur=DUR, n=len(tgt_log["flap_L"]))
    for n in ("flap_L", "flap_R"):
        ts, as_ = tgt_log[n], act_log[n]
        res[n] = dict(
            tgt_std=statistics.pstdev(ts), act_std=statistics.pstdev(as_),
            ratio=statistics.pstdev(as_) / max(statistics.pstdev(ts), 1e-9),
            peak=max(abs(x) for x in as_))
    res["ratio_mean"] = 0.5 * (res["flap_L"]["ratio"] + res["flap_R"]["ratio"])
    res["peak_max"] = max(res["flap_L"]["peak"], res["flap_R"]["peak"])
    res["limit_margin"] = 1.4 - res["peak_max"]

    out = os.path.join(cfg.LOG_DIR, f"drive_sweep_K{K:g}_D{D:g}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"[sweep] K={K:g} D={D:g} f={FREQ} ratio={res['ratio_mean']:.3f} "
          f"peak={res['peak_max']:.3f} margin={res['limit_margin']:.3f} "
          f"-> {out}", flush=True)
    simulation_app.close()


if __name__ == "__main__":
    main()
