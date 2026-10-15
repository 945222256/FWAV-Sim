# -*- coding: utf-8 -*-
"""kimi_force_calibration.py — 力施加通道标定（两被控对象矛盾调和·第二步）。

方法：按 main_sim 同款加载路径载入扑翼机（含 _unify_mass），reset 后对机体
施加恒定竖直力 F_up = 2.108 N（= 0.215 kg × 9.81，恰为标称体重）。
  若机体近似悬停（|a_z| 小）→ 力施加通道与质量一致，矛盾在记录口径。
  若机体以 a_z 明显上升 → 飞行时有效质量/有效重力与标称不符，
  由 a_z 反解有效质量 m_eff = F_up / (g + a_z_mean)。
产物：logs/force_calib_kimi.txt（kit 吞 stdout，文件是唯一可靠通道）。
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
from modules import flapper_loader  # noqa: E402

OUT = os.path.join(PROJECT_ROOT, "logs", "force_calib_kimi.txt")


def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def main():
    if os.path.exists(OUT):
        os.remove(OUT)
    world = World(physics_dt=1.0 / 120.0, rendering_dt=1.0 / 60.0)
    flapper_prim = flapper_loader.load_flapper(world)
    loader = flapper_loader.get_loader()
    world.reset()

    # 与 aero 回调同款的视图：直接对 base_link 的 RigidPrimView 施加全局力
    root_link = loader.root_link_path
    log(f"root_link = {root_link}")
    view = RigidPrimView(prim_paths_expr=root_link, name="calib_view")
    world.scene.add(view)
    world.reset()

    masses = view.get_masses()
    log(f"get_masses = {masses}")

    F_up = 0.215 * 9.81
    log(f"施加恒力 F_up = {F_up:.3f} N（=标称体重）")
    dt = 1.0 / 120.0
    vzs = []
    for k in range(int(6.0 / dt)):
        view.apply_forces_and_torques_at_pos(
            forces=np.array([[0.0, 0.0, F_up]], dtype=np.float32), is_global=True)
        world.step(render=False)
        v = view.get_velocities()
        vz = float(v[0][2])
        vzs.append(vz)
        if k % 120 == 0:
            p = view.get_world_poses()[0][0]
            log(f"t={k*dt:4.1f} z={float(p[2]):+.3f} vz={vz:+.3f}")

    vzs = np.array(vzs)
    a_mean = np.polyfit(np.arange(len(vzs)) * dt, vzs, 1)[0]
    g = 9.81
    m_eff = F_up / (g + a_mean)
    log(f"\n末段 vz 均值 {vzs[-120:].mean():+.3f} m/s，斜率 a_z={a_mean:+.3f} m/s²")
    log(f"有效质量 m_eff = F/(g+a_z) = {m_eff:.4f} kg（标称 0.215 kg）")
    if abs(a_mean) < 0.3:
        log("判读：近似悬停 → 力施加通道与质量一致")
    else:
        log("判读：明显加速 → 飞行时有效质量/重力与标称不符")
    simulation_app.close()


if __name__ == "__main__":
    main()
