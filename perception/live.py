# -*- coding: utf-8 -*-
"""
live.py — 门检测 UDP 服务
=========================

两种模式，输出协议一致（JSON / 127.0.0.1:PERCEPTION_UDP_PORT，默认 9012）：

    {type:"gate", t, valid, u, v, r, fit, e_u, e_v, e_d}

e_u/e_v/e_d = 论文式(18) 归一化误差（对齐 flapper_controller 的接口习惯，
后续视觉伺服直接消费）。

1) 仿真在线：main_sim 钩子调用（相机帧 → detect → send），见
   config.ENABLE_GATE_PERCEPTION。
2) 数据集回放：python -m perception.live --dataset data/gate_clean
   [--fps 30]，控制组无需 Isaac 即可联调。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perception.core.detector import (AdaptiveHoughDetector,
                                      PreprocessConfig)
from perception.sim_adapter import D435_INTRINSICS, frame_to_bgr


class GatePerceptionService:
    """检测 + 归一化误差 + UDP 外发（在线模式由 main_sim 每渲染帧调用）。"""

    def __init__(self, udp_sender=None, port: int = 9012,
                 cfg: Optional[PreprocessConfig] = None,
                 K: Optional[dict] = None):
        self.detector = AdaptiveHoughDetector(cfg or PreprocessConfig())
        self.sender = udp_sender            # modules.usv_network.UDPClient
        self.port = port
        self.K = K or D435_INTRINSICS
        self.n_frames = 0
        self.n_valid = 0

    def on_frame(self, frame_dict, t: float) -> dict:
        """frame_dict = Isaac Camera.get_current_frame()（或含 rgb/rgba 键的 dict）。"""
        bgr = frame_to_bgr(frame_dict)
        if bgr is None:
            return {"type": "gate", "t": float(t), "valid": False}
        det = self.detector.process(bgr)
        self.n_frames += 1
        self.n_valid += int(det.valid)
        e_u = e_v = e_d = None
        if det.valid:
            e_u, e_v, e_d = det.normalized_errors(
                self.K["cx"], self.K["cy"],
                int(self.K["width"]), int(self.K["height"]))
        msg = {"type": "gate", "t": float(t), "valid": bool(det.valid),
               "u": det.u, "v": det.v, "r": det.r, "fit": det.fit,
               "e_u": e_u, "e_v": e_v, "e_d": e_d,
               "latency_ms": det.latency_ms}
        if self.sender is not None:
            try:
                self.sender.send(msg)
            except Exception:
                pass
        return msg


def _replay(dataset: str, fps: float, port: int) -> None:
    """离线回放模式：按序喂帧，模拟在线输出。"""
    from modules.usv_network import UDPClient
    frames = sorted(f for f in os.listdir(dataset)
                    if f.startswith("frame_") and f.endswith(".png"))
    if not frames:
        print(f"无帧可放: {dataset}")
        return
    sender = UDPClient(target_port=port, target_ip="127.0.0.1")
    svc = GatePerceptionService(sender, port=port)
    dt = 1.0 / max(1e-6, fps)
    t0 = time.perf_counter()
    for i, f in enumerate(frames):
        img = cv2.imread(os.path.join(dataset, f))
        if img is None:
            continue
        msg = svc.on_frame({"rgb": cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)}, t=i * dt)
        if i % 10 == 0:
            print(f"[{i:3d}] valid={msg['valid']} u={msg['u']:.0f} "
                  f"v={msg['v']:.0f} r={msg['r']:.0f} e=({msg['e_u']}, {msg['e_v']}, {msg['e_d']})")
        # 不真睡（回放求快）；如需实时节律加 time.sleep
    print(f"回放完成: {svc.n_valid}/{svc.n_frames} 帧有效 → udp:127.0.0.1:{port}")
    del t0


def main() -> int:
    ap = argparse.ArgumentParser("perception.live")
    ap.add_argument("--dataset", default="", help="回放模式的数据集目录")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--port", type=int, default=9012)
    args = ap.parse_args()
    if not args.dataset:
        ap.print_help()
        return 1
    _replay(args.dataset, args.fps, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
