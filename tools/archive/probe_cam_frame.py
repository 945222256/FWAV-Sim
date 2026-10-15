# -*- coding: utf-8 -*-
"""probe_cam_frame.py — 最小相机出帧探针（headless）

验证：world.scene.add 与否 / initialize 顺序 / is_paused 状态 / 帧键名。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
import config.sim_config as _cfg  # noqa: F401  (先占住 config 名字)

from isaacsim import SimulationApp


def _body(app):
    import numpy as np
    from isaacsim.core.api import World
    from isaacsim.sensors.camera import Camera
    from modules import env_builder
    from perception.sim_adapter import apply_d435_intrinsics

    world = World(stage_units_in_meters=1.0, physics_dt=1/120., rendering_dt=1/30.)
    env_builder.build_environment(world)

    cam = Camera(prim_path="/World/ProbeCam", name="probe",
                 resolution=(640, 480),
                 translation=np.array([0., 0., 3.]),
                 orientation=np.array([1., 0., 0., 0.]))
    try:
        rp = cam.get_render_product_path()
    except Exception as e:
        rp = f"get_render_product_path ERR {e}"
    print("[probe] before init: rp =", rp, flush=True)
    try:
        world.scene.add(cam)
        print("[probe] scene.add OK", flush=True)
    except Exception as e:
        print("[probe] scene.add FAIL:", e, flush=True)
    cam.initialize()
    apply_d435_intrinsics(cam)
    print("[probe] after init: rp =", cam.get_render_product_path(),
          "paused =", cam.is_paused(), flush=True)

    world.reset()
    print("[probe] after reset: paused =", cam.is_paused(), flush=True)

    for i in range(12):
        world.step(render=True)
        if i in (0, 3, 7, 11):
            fr = cam.get_current_frame()
            rgba = fr.get("rgba") if isinstance(fr, dict) else None
            print(f"[probe] step{i}: keys={sorted(fr.keys()) if isinstance(fr, dict) else fr} "
                  f"rgba={'None' if rgba is None else rgba.shape} paused={cam.is_paused()} "
                  f"t={world.current_time:.3f}", flush=True)


if __name__ == "__main__":
    app = SimulationApp({"headless": True})
    try:
        _body(app)
    finally:
        app.close()
