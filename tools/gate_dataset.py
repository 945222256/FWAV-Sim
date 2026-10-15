# -*- coding: utf-8 -*-
"""
gate_dataset.py — 圆门检测数据集独立采集器（零依赖扑翼飞控状态）
================================================================

独立于 main_sim 运行：自建 World（地面 + HDRI + 可选柱状杂物 + 圆门），
一架挂 D435 标定的相机沿脚本化接近轨迹飞向门，逐渲染帧保存
(frame_XXXXXX.png + gt.jsonl)，供 perception/eval/run_eval.py 离线评测。

与 main_sim 随行采集（RideAlongCollector，相机骑扑动机构收真实振动）
互补：本工具可控、可复现（无物理），随行模式真实（带拍动振动）。

用法（Isaac Sim 自带 Python）：
    g:/Playground/isaac_sim_5.1.0/python.bat tools/gate_dataset.py \
        --scenario clean --frames 60 --out data/gate_clean
    可选: --headless --z0 12 --z1 2 --gate 10,0,1.6,0 --light lowlight
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 必须在 SimulationApp/isaacsim 导入之前先 import 本仓库 config ——
# kit 启动会把 pip_prebundle 路径排到 sys.path 前面，其中 cv2/config.py
# 会把仓库的 config/ 包遮蔽（main_sim.py 同款手法，实测有效）
import config.sim_config as _cfg  # noqa: E402


def _parse_args():
    p = argparse.ArgumentParser("gate_dataset")
    p.add_argument("--out", default="", help="输出目录（默认 data/gate_<scenario>_<时间戳>）")
    p.add_argument("--scenario", default="clean",
                   choices=["clean", "clutter", "lowlight"],
                   help="clean=空场；clutter=加柱状杂物；lowlight=clutter+弱光+传感器退化")
    p.add_argument("--frames", type=int, default=60)
    p.add_argument("--z0", type=float, default=10.0, help="起始距离(m)")
    p.add_argument("--z1", type=float, default=1.6, help="终止距离(m)")
    p.add_argument("--gate", default="10,0,1.6,0",
                   help="门 x,y,z,yaw_deg（法向 yaw=0 → +X，相机从 +X 侧接近）")
    p.add_argument("--light", default="", choices=["", "normal", "lowlight"],
                   help="光照预设（默认按 scenario 推断）")
    p.add_argument("--vib-px", type=float, default=1.5,
                   help="逐帧中心抖动 σ(px)，模拟扑动振动")
    p.add_argument("--headless", action="store_true")
    return p.parse_args()


def _lookat_quat_usd(eye: np.ndarray, target: np.ndarray) -> np.ndarray:
    """USD 约定（-Z 前向）的 look-at 四元数 (w,x,y,z)。"""
    fwd = target - eye
    fwd = fwd / np.linalg.norm(fwd)
    up_hint = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(fwd, up_hint)) > 0.995:
        up_hint = np.array([0.0, 1.0, 0.0])
    right = np.cross(fwd, up_hint)
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    # R 列 = [right, up, -fwd]（cam 系基向量在世界的表示）
    R = np.column_stack([right, up, -fwd])
    return _rotmat_to_quat(R)


def _rotmat_to_quat(R: np.ndarray) -> np.ndarray:
    """旋转矩阵 → 四元数 (w,x,y,z)，最简 Shepperd 分支。"""
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2.0
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2.0
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2.0
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z])
    return q / np.linalg.norm(q)


def main() -> None:
    args = _parse_args()

    # —— SimulationApp 必须先于一切 isaacsim 导入 ——
    from isaacsim import SimulationApp
    app = SimulationApp({"headless": args.headless, "width": 1280, "height": 720,
                         "title": "gate_dataset collector"})
    try:
        _run_collection(app, args)
    finally:
        app.close()          # 异常路径也必须关 kit，否则进程残留（实测）


def _run_collection(app, args) -> None:

    import omni.usd                                          # noqa: E402
    from isaacsim.core.api import World                       # noqa: E402
    from isaacsim.sensors.camera import Camera                # noqa: E402
    from modules import env_builder, obstacle_manager, gate_manager  # noqa: E402
    from perception.sim_adapter import (D435_INTRINSICS, apply_d435_intrinsics,
                                        apply_lighting_preset,
                                        degrade_frame, frame_to_bgr,
                                        project_circle_gt)
    import config.sim_config as cfg                           # noqa: E402
    import cv2                                                # noqa: E402

    light = args.light or ("lowlight" if args.scenario == "lowlight" else "normal")
    if not args.out:
        from datetime import datetime
        args.out = os.path.join(PROJECT_ROOT, "data",
                                f"gate_{args.scenario}_{datetime.now():%m%d_%H%M%S}")
    os.makedirs(args.out, exist_ok=True)
    print(f"[gate_dataset] 输出 → {args.out}", flush=True)

    world = World(stage_units_in_meters=1.0,
                  physics_dt=1.0 / 120.0, rendering_dt=1.0 / 30.0)
    env_builder.build_environment(world)
    stage = omni.usd.get_context().get_stage()

    gx, gy, gz, gyaw = (float(v) for v in args.gate.split(","))
    gate_manager.spawn_gate(stage, (gx, gy, gz), yaw_deg=gyaw)
    gate_circle = gate_manager.get_gate_circle((gx, gy, gz), gyaw,
                                               gate_manager.DEFAULT_INNER_R)
    # 调试：门的世界包围盒（核对抗 10× 尺度异常）
    try:
        from pxr import UsdGeom, Usd
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                                  [UsdGeom.Tokens.default_], True)
        bb = cache.ComputeWorldBound(stage.GetPrimAtPath(
            gate_manager.GATE_ROOT_PATH + "/Ring"))
        rng = bb.ComputeAlignedRange()
        sz = rng.GetSize(); c0 = rng.GetMin()
        print(f"[gate_dataset] 门bbox size=({sz[0]:.3f},{sz[1]:.3f},{sz[2]:.3f}) "
              f"min=({c0[0]:.2f},{c0[1]:.2f},{c0[2]:.2f})", flush=True)
    except Exception as e:
        print(f"[gate_dataset] bbox 调试失败: {e}", flush=True)

    if args.scenario in ("clutter", "lowlight"):
        # 杂物：门两侧柱阵（复用 obstacle_manager 的固定圆柱生成）
        rng = np.random.default_rng(3)
        for i in range(10):
            side = 1.0 if i % 2 == 0 else -1.0
            px = gx + rng.uniform(-2.5, 2.5)
            py = gy + side * rng.uniform(2.2, 5.0)
            obstacle_manager._create_fixed_cylinder_prim(
                stage, f"/World/Perception/Pillar_{i:02d}",
                (px, py, 1.2), 0.18, 2.4,
                tuple(c * 0.9 for c in (0.38, 0.27, 0.16)))

    apply_lighting_preset(world.stage, light)

    # —— 感知相机（世界级独立 prim，脚本驱动；D435 标定） ——
    cam = Camera(prim_path="/World/PerceptionCam", name="gate_cam",
                 resolution=(640, 480),
                 translation=np.array([0.0, 0.0, 3.0]),
                 orientation=np.array([1.0, 0.0, 0.0, 0.0]))
    cam.initialize()
    apply_d435_intrinsics(cam)

    world.reset()
    for _ in range(5):
        world.step(render=True)          # warmup，让渲染管线出第一帧

    center_w, normal_w, r_m = gate_circle
    rng = np.random.default_rng(11)
    gt_file = open(os.path.join(args.out, "gt.jsonl"), "w", encoding="utf-8")

    meta = {"intrinsics": D435_INTRINSICS, "scenario": args.scenario,
            "light": light, "gate": {"pos": [gx, gy, gz], "yaw_deg": gyaw,
                                     "hole_r": r_m},
            "z0": args.z0, "z1": args.z1, "vib_px": args.vib_px}
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    n_vis = 0
    for i in range(args.frames):
        s = i / max(1, args.frames - 1)
        dist = args.z0 * (1.0 - s) + args.z1 * s
        # 视线 = 门法向的反向接近（yaw=0 → 相机在门 +X 侧）
        eye = center_w + normal_w * dist
        # 侧向慢摆（真实接近不会正对死飞）+ 上下起伏
        sway = 0.8 * math.sin(0.35 * i)
        bob = 0.4 * math.sin(0.5 * i + 1.0)
        tangent = np.array([-normal_w[1], normal_w[0], 0.0])
        eye = eye + tangent * sway + np.array([0.0, 0.0, bob])
        # 扑动振动：位置抖 ±vib_px 等效位移（≈ vib_px/fx·dist）+ 角抖
        jit = args.vib_px / D435_INTRINSICS["fx"] * dist
        eye = eye + rng.normal(0.0, jit, 3)
        quat = _lookat_quat_usd(eye, center_w)

        cam.set_world_pose(position=np.asarray(eye, dtype=np.float32),
                           orientation=np.asarray(quat, dtype=np.float32),
                           camera_axes="usd")
        # 相机移动后 RTX 时间降噪器冷启动（headless 无视口热身，实测首帧
        # 偏暗偏糊）——同一静止位姿多渲几帧取最后一帧，让降噪器收敛
        for _ in range(4):
            world.step(render=True)

        pos, quat_r = cam.get_world_pose(camera_axes="usd")
        gt = project_circle_gt(center_w, normal_w, r_m,
                               np.asarray(pos, dtype=float),
                               np.asarray(quat_r, dtype=float))
        frame = cam.get_current_frame()
        bgr = frame_to_bgr(frame)
        if bgr is None:
            print(f"[gate_dataset] 帧 {i} 无图像输出，跳过 keys="
                  f"{sorted(frame.keys()) if isinstance(frame, dict) else frame}", flush=True)
            continue
        bgr = degrade_frame(bgr, "lowlight" if args.scenario == "lowlight" else "normal",
                            rng)
        cv2.imwrite(os.path.join(args.out, f"frame_{i:06d}.png"), bgr)
        rec = {"frame": i, "t": float(world.current_time),
               "u": gt["u"], "v": gt["v"], "r": gt["r"],
               "visible": gt["visible"],
               "cam_pos": [float(x) for x in pos]}
        gt_file.write(json.dumps(rec) + "\n")
        gt_file.flush()
        n_vis += int(gt["visible"])
        if i % 10 == 0:
            print(f"[gate_dataset] {i}/{args.frames} dist={dist:.1f}m "
                  f"gt=({gt['u']:.0f},{gt['v']:.0f},r={gt['r']:.0f})", flush=True)

    gt_file.close()
    print(f"[gate_dataset] 完成: {args.frames} 帧（门可见 {n_vis}）→ {args.out}",
          flush=True)


if __name__ == "__main__":
    main()
