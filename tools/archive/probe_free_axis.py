# -*- coding: utf-8 -*-
"""
probe_free_axis.py — 单轴释放自激隔离探针（2026-09-19）
=====================================================
kick_roll 试验中滚转释放后出现 0.6→175 rad/s 自激。三相对照定位泵源：
  A: 滚转释放 + 气动注入关（不施加力）→ 测钉固回写/关节驱动是否泵能
  B: 滚转释放 + 气动注入开            → 测气动反馈是否泵能
  C: 全钉 + 气动注入开                → 对照组（应完全静止）
输出：logs/probe_free_axis.txt
"""
import os
import sys
import math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DIAG = os.path.join(ROOT, "logs", "probe_free_axis.txt")


def log(m):
    with open(DIAG, "a", encoding="utf-8") as f:
        f.write(m + "\n")
        f.flush()
    print(m, flush=True)


open(DIAG, "w").close()
import config.sim_config as cfg
from modules.aero_solver import FlapperAeroModel, WingKinematics
from isaacsim import SimulationApp
app = SimulationApp({"headless": True})

import numpy as np
import omni.usd
from isaacsim.core.api import World
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.prims import Articulation
from isaacsim.core.prims import RigidPrim as RigidPrimView
from pxr import UsdPhysics

USD = os.path.join(ROOT, "assets/flapper/fwav_15dof.usd").replace("\\", "/")
omni.usd.get_context().new_stage()
add_reference_to_stage(usd_path=USD, prim_path="/World/Test")
stage = omni.usd.get_context().get_stage()
for prim in stage.Traverse():
    if prim.GetTypeName() == "PhysicsRevoluteJoint":
        if not prim.HasAPI(UsdPhysics.DriveAPI, "angular"):
            UsdPhysics.DriveAPI.Apply(prim, "angular")
        d = UsdPhysics.DriveAPI.Get(prim, "angular")
        d.GetStiffnessAttr().Set(60.0)
        d.GetDampingAttr().Set(0.25)
# 质量等比（与主仿真一致）
items = []
for prim in stage.Traverse():
    if str(prim.GetPath()).startswith("/World/Test") and prim.HasAPI(UsdPhysics.MassAPI):
        m = UsdPhysics.MassAPI(prim).GetMassAttr().Get()
        if m and m > 0:
            items.append((prim, float(m)))
_tot = sum(m for _, m in items)
for prim, m in items:
    UsdPhysics.MassAPI(prim).GetMassAttr().Set(m * cfg.MASS / _tot)
log(f"质量等比 {_tot:.3f}→{cfg.MASS:.3f} kg")

world = World(stage_units_in_meters=1.0, physics_dt=1 / 120.0, rendering_dt=1 / 120.0)
art = Articulation("/World/Test")
world.scene.add(art)

# base_link 刚体视图（与 main_sim 的 mav_view 同路径）
from modules.flapper_loader import FlapperLoader  # 只为拿 root_link_path 逻辑
root_link = None
for prim in stage.Traverse():
    if str(prim.GetPath()).endswith("base_link"):
        root_link = str(prim.GetPath())
        break
log(f"root_link = {root_link}")
mav = RigidPrimView(prim_paths_expr=root_link, name="probe_mav",
                    reset_xform_properties=False)
world.scene.add(mav)
world.reset()

names = list(art.joint_names)
iFL, iFR = names.index("flap_L"), names.index("flap_R")
iTL, iTR = names.index("twist_L"), names.index("twist_R")
F_HZ = 1.9
wk = WingKinematics(frequency=F_HZ, amplitude_deg=35.0,
                    pitch_mode="aero", alpha_set_deg=cfg.FlapKinematicsConfig.ALPHA_SET)
model = FlapperAeroModel()
model.set_wing_command(frequency=F_HZ)

# 机体抬到 z=1.5（与主仿真台架一致）
mav.set_world_poses(positions=np.array([[0, 0, 1.5]], dtype=np.float32),
                    orientations=np.array([[1, 0, 0, 0]], dtype=np.float32))


def drive(t):
    w = wk.evaluate(t)
    vec = [0.0] * len(names)
    vec[iFL] = w["phi"]
    vec[iFR] = -w["phi"]
    vec[iTL] = vec[iTR] = w["alpha"]
    art.set_joint_position_targets(
        positions=np.asarray([vec], dtype=np.float32), joint_names=names)


def pin(free_roll):
    """与 main_sim._bench_pin 同逻辑：释放轴保留，其余钉死。"""
    pos, quat = mav.get_world_poses()
    pos = np.asarray(pos).reshape(-1)[:3]
    quat = np.asarray(quat).reshape(-1)[:4]
    vel = np.asarray(mav.get_velocities()).reshape(-1)[:6]
    w, x, y, z = quat
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    new_vel = np.zeros(6)
    r = 0.0
    if free_roll:
        r = roll
        new_vel[3] = vel[3]
    cr, sr = math.cos(r / 2), math.sin(r / 2)
    mav.set_world_poses(positions=np.array([[0, 0, 1.5]], dtype=np.float32),
                        orientations=np.array([[cr, sr, 0, 0]], dtype=np.float32))
    mav.set_velocities(new_vel.reshape(1, 6).astype(np.float32))


def run(tag, free_roll, inject_aero, seconds, kick=0.0):
    t0 = world.current_time
    steps = int(seconds * 120)
    kicked = False
    maxwx = 0.0
    for i in range(steps):
        t = world.current_time
        drive(t)
        if inject_aero:
            pos, quat = mav.get_world_poses()
            vel = mav.get_velocities()
            F, T = model.compute_forces(
                np.asarray(pos[0], dtype=float), np.asarray(quat[0], dtype=float),
                np.asarray(vel[0][:3], dtype=float), np.asarray(vel[0][3:], dtype=float), t)
            mav.apply_forces_and_torques_at_pos(
                forces=np.asarray(F, dtype=np.float32).reshape(1, 3),
                torques=np.asarray(T, dtype=np.float32).reshape(1, 3),
                is_global=True)
        world.step(render=False)
        if kick and not kicked:
            vel = np.asarray(mav.get_velocities(), dtype=np.float32).reshape(1, 6).copy()
            vel[0, 3] = kick
            mav.set_velocities(vel)
            kicked = True
            log(f"[{tag}] kick {kick} rad/s 已施加 @t={t:.2f}")
        pin(free_roll)
        if i % 60 == 0:
            vel = mav.get_velocities()
            wx = float(np.asarray(vel).reshape(-1)[3])
            maxwx = max(maxwx, abs(wx))
            log(f"[{tag}] t={t - t0:5.2f} wx={wx:+9.3f} rad/s")
    vel = mav.get_velocities()
    wx = float(np.asarray(vel).reshape(-1)[3])
    log(f"[{tag}] 结束 |wx|max={maxwx:.3f} 末wx={wx:+.3f}")


run("A 释放+无气动", True, False, 5.0)
run("C 全钉+有气动", False, True, 3.0)
run("B 释放+有气动", True, True, 5.0)
run("D 释放+有气动+踢", True, True, 6.0, kick=0.6)

app.close()
log("完成")
