# -*- coding: utf-8 -*-
"""_snapshot_rot_restore.py — ω×r 恢复前后的模型输出快照（一次性验证用）。
用法： python tools/_snapshot_rot_restore.py before|after
产物： logs/_snapshot_rot_<tag>.json——after 模式自动加载 before 对比：
ω=0 各工况必须逐位一致；ω≠0 工况输出耦合差并核对斜率量级。
"""
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
from modules.aero_solver import euler_to_quat
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel

STATES = [
    # (标签, 速度, 俯仰deg, ω)
    ("cruise_a0", 4.0, 7.14, (0.0, 0.0, 0.0)),
    ("cruise_p1", 4.0, 7.14, (1.0, 0.0, 0.0)),
    ("cruise_m1", 4.0, 7.14, (-1.0, 0.0, 0.0)),
    ("cruise_p9", 4.0, 7.14, (9.0, 0.0, 0.0)),
    ("cruise_r1", 4.0, 7.14, (0.0, 0.0, 1.0)),
    ("cruise_r9", 4.0, 7.14, (0.0, 0.0, 9.0)),
    ("slow_a0", 3.0, 12.0, (0.0, 0.0, 0.0)),
    ("fast_a0", 5.0, 5.0, (0.0, 0.0, 0.0)),
]

model = SaneDickinson3DModel()
for w in (model.wing_left, model.wing_right):
    w.alpha_set = np.radians(35.0)

t0 = 200000.0
out = {}
for tag, V, pitch, om in STATES:
    model.set_wing_command(frequency=1.75)
    quat = euler_to_quat(0.0, np.radians(pitch), 0.0)
    vel = np.array([V, 0.0, 0.0])
    omv = np.asarray(om, dtype=float)
    F = np.zeros(3)
    T = np.zeros(3)
    n = 288
    T_per = 1.0 / 1.75
    for i in range(n):
        Fw, Tw = model.compute_forces(np.zeros(3), quat, vel, omv,
                                      t0 + i * T_per / n)
        F += Fw
        T += Tw
    t0 += T_per
    out[tag] = dict(F=(F / n).tolist(), T=(T / n).tolist())

tag = sys.argv[1] if len(sys.argv) > 1 else "before"
path = os.path.join(PROJECT_ROOT, "logs", f"_snapshot_rot_{tag}.json")
with open(path, "w", encoding="utf-8") as fp:
    json.dump(out, fp, ensure_ascii=False, indent=1)
print(f"[snap] {tag} 写出 {path}")

if tag == "after":
    before_path = os.path.join(PROJECT_ROOT, "logs",
                               "_snapshot_rot_before.json")
    with open(before_path, encoding="utf-8") as fp:
        bef = json.load(fp)
    print("[snap] ω=0 一致性：")
    ok0 = True
    for s in ("cruise_a0", "slow_a0", "fast_a0"):
        df = max(
            max(abs(a - b) for a, b in zip(out[s]["F"], bef[s]["F"])),
            max(abs(a - b) for a, b in zip(out[s]["T"], bef[s]["T"])))
        ok0 &= df == 0.0
        print(f"  {s}: max|Δ| = {df:.2e}")
    print(f"  → {'逐位一致 ✓' if ok0 else '★不一致！'}")
    print("[snap] ω≠0 耦合生效（Δ = after − before，含人工阻尼项对消）：")
    tx1 = (out["cruise_p1"]["T"][0] - out["cruise_m1"]["T"][0]) / 2.0
    tx9 = out["cruise_p9"]["T"][0] - bef["cruise_p9"]["T"][0]
    print(f"  D(p)@1 中心差分 = {tx1:+.5f}（对照辨识 −0.0091，"
          f"after 内已含 −0.1·p 人工阻尼：{tx1 + 0.1:+.5f}+0.1）")
    print(f"  ΔTx@p=9 = {tx9:+.5f}（恢复前此项=0）")
    tz9 = out["cruise_r9"]["T"][0] - bef["cruise_r9"]["T"][0]
    print(f"  ΔTx@r=9（交叉耦合）= {tz9:+.5f}（对照 D_xz·9≈"
          f"{-0.0344 * 9:+.3f}）")
