# -*- coding: utf-8 -*-
"""kimi_headless_flight.py — 无头飞行复演（两被控对象矛盾调和·第三步）。

按 main_sim 同款路径加载扑翼机并注册气动回调，开环给定拍频 1.55 Hz、
初速 4.0 m/s，逐步对账：施加力（回调 last_F_world）与净力（0.215 kg ×
实测加速度的差分）。若净力≠施加力，则存在回调之外的隐力通道。
产物：logs/headless_flight_kimi.txt
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import sim_config as cfg

_app_cfg = dict(cfg.SIM_APP_CONFIG)
_app_cfg["headless"] = True

from isaacsim import SimulationApp  # noqa: E402
simulation_app = SimulationApp(_app_cfg)

import numpy as np  # noqa: E402
from isaacsim.core.api import World  # noqa: E402
from isaacsim.core.prims import RigidPrim as RigidPrimView  # noqa: E402
from isaacsim.core.prims import Articulation  # noqa: E402
from modules import flapper_loader  # noqa: E402

OUT = os.path.join(PROJECT_ROOT, "logs", "headless_flight_kimi.txt")
M = 0.215
G = 9.81


def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def main():
    if os.path.exists(OUT):
        os.remove(OUT)
    world = World(physics_dt=1.0 / 120.0)
    flapper_loader.load_flapper(world)
    loader = flapper_loader.get_loader()
    root_link = loader.root_link_path
    view = RigidPrimView(prim_paths_expr=root_link, name="hf_view")
    world.scene.add(view)
    # 与 main_sim 同款：关节样机 Articulation（驱动翼面）
    art = None
    if getattr(loader, "articulated", False):
        _root_api = loader.articulation_root_api_path or ""
        _art_path = _root_api.rsplit("/", 1)[0] if _root_api.endswith("root_joint") \
            else _root_api
        art = Articulation(_art_path, name="hf_articulation")
        world.scene.add(art)
    aero_cb = flapper_loader.setup_aero(world, flapper_view=view)
    world.reset()
    log(f"articulation = {_art_path if art is not None else None}")
    if art is not None:
        log(f"joint_names = {list(art.joint_names)}")

    # 开环：拍频 1.55 Hz、初速 4.0 m/s（与在环巡航段状态一致）
    aero_cb.set_wing_command(frequency=3.5)
    v0 = np.zeros((1, 6), dtype=np.float32)
    v0[0, 0] = 4.0
    view.set_velocities(v0)

    dt = 1.0 / 120.0
    prev_vz = 0.0
    log("t     z       vz      a_z     施加Fz   净力Fz   残差")
    for k in range(int(10.0 / dt)):
        t = k * dt
        # 翼面驱动（WingKinematics → 关节位置目标，与 main_sim 同逻辑）
        if art is not None:
            w = aero_cb.model.wing_left.evaluate(t)
            phi, alpha = float(w["phi"]), float(w["alpha"])
            targets = []
            for jn in art.joint_names:
                n = str(jn).lower()
                if "flap" in n:
                    targets.append(phi if "_l" in n else -phi)
                elif "twist" in n:
                    targets.append(alpha)
                elif "fold" in n:
                    targets.append(0.3 * (1.0 + np.sin(2 * np.pi * 3.5 * t)))
                else:
                    targets.append(0.0)
            art.set_joint_position_targets(
                np.asarray([targets], dtype=np.float32))
        world.step(render=False)
        vel = view.get_velocities()[0]
        vz = float(vel[2])
        a_z = (vz - prev_vz) / dt
        prev_vz = vz
        if k % 60 == 0:
            p = view.get_world_poses()[0][0]
            Fz = float(aero_cb.last_F_world[2]) if aero_cb.last_F_world is not None else float("nan")
            net = M * a_z
            jp = art.get_joint_positions()[0] if art is not None else []
            jstr = " ".join(f"{float(x):+.2f}" for x in jp[:3]) if len(jp) else "n/a"
            log(f"{t:4.1f} {float(p[2]):+6.3f} {vz:+7.3f} {a_z:+7.2f} "
                f"{Fz:+7.3f} {net:+7.3f} {net - Fz:+7.3f} | joints {jstr}")

    simulation_app.close()


if __name__ == "__main__":
    main()
