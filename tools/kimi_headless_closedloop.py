# -*- coding: utf-8 -*-
"""kimi_headless_closedloop.py — 无头全闭环复演（两被控对象矛盾调和·第四步）。

复刻 main_sim 的控制全链：FlightController 巡航口径 → 拍频/舵偏 → 气动回调
+ 关节位置驱动 → PhysX。若在纯无头环境下复演出在环的"低力爬高"现象，
则可在无头环境直接解剖隐力通道；若不复演，差异在 GUI/传感器/看板侧。
产物：logs/headless_closedloop_kimi.txt
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
from modules.flight_controller import FlightController  # noqa: E402

OUT = os.path.join(PROJECT_ROOT, "logs", "headless_closedloop_kimi.txt")
M = 0.215
DT = 1.0 / 120.0


def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def main():
    if os.path.exists(OUT):
        os.remove(OUT)
    world = World(physics_dt=DT)
    flapper_loader.load_flapper(world)
    loader = flapper_loader.get_loader()
    view = RigidPrimView(prim_paths_expr=loader.root_link_path, name="cl_view")
    world.scene.add(view)
    art = None
    if getattr(loader, "articulated", False):
        _root_api = loader.articulation_root_api_path or ""
        _art_path = _root_api.rsplit("/", 1)[0] if _root_api.endswith("root_joint") \
            else _root_api
        art = Articulation(_art_path, name="cl_articulation")
        world.scene.add(art)
    aero_cb = flapper_loader.setup_aero(world, flapper_view=view)
    world.reset()

    ctl = FlightController()
    ctl._freq_last = 1.55
    aero_cb.set_wing_command(frequency=1.55)
    v0 = np.zeros((1, 6), dtype=np.float32)
    v0[0, 0] = 4.0
    view.set_velocities(v0)

    prev_vz = 0.0
    log("t     z       vx      vz      freq   施加Fz  净力    残差")
    for k in range(int(30.0 / DT)):
        t = k * DT
        pose_v = view.get_world_poses()
        pos = np.asarray(pose_v[0][0], dtype=float)
        quat = np.asarray(pose_v[1][0], dtype=float)
        vel = np.asarray(view.get_velocities()[0], dtype=float)
        pose = [pos[0], pos[1], pos[2], quat[0], quat[1], quat[2], quat[3]]
        out = ctl.update(pose, list(vel), t_sim=t)
        # 拍频同步（力模型与关节两侧）
        aero_cb.set_wing_command(frequency=float(out["freq"]))
        aero_cb.tail_body.set_command(elevator=out["elevator"],
                                      aileron=out["aileron"],
                                      rudder=out["rudder"])
        # 翼面关节位置驱动
        if art is not None:
            w = aero_cb.model.wing_left.evaluate(t)
            phi = float(w["phi"]) + float(out.get("dA", 0.0))
            alpha = float(w["alpha"])
            targets = []
            for jn in art.joint_names:
                n = str(jn).lower()
                if "flap" in n:
                    targets.append(phi if "_l" in n else -phi)
                elif "twist" in n:
                    targets.append(alpha)
                elif "fold" in n:
                    targets.append(0.0)
                else:
                    targets.append(0.0)
            art.set_joint_position_targets(
                np.asarray([targets], dtype=np.float32))
        world.step(render=False)

        vel2 = np.asarray(view.get_velocities()[0], dtype=float)
        vz = float(vel2[2])
        a_z = (vz - prev_vz) / DT
        prev_vz = vz
        if k % 120 == 0:
            p = view.get_world_poses()[0][0]
            Fz = float(aero_cb.last_F_world[2]) if aero_cb.last_F_world is not None else float("nan")
            net = M * a_z
            log(f"{t:5.1f} {float(p[2]):+7.3f} {float(vel2[0]):+7.2f} {vz:+7.3f} "
                f"{out['freq']:5.2f} {Fz:+7.3f} {net:+7.3f} {net - Fz:+7.3f}")
    simulation_app.close()


if __name__ == "__main__":
    main()
