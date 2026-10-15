"""
hud_subscribers.py — HUD 管线的订阅者集合
=========================================
每个订阅者是一个 callable(HudFrame) -> None。
管线 (hud_pipeline) 只负责取帧广播, 具体处理逻辑全在这里。
加减订阅者互不影响, 也不用改管线本身。

当前订阅者:
  - DebugSaveSubscriber : 前 N 帧存盘, 验证数据通路 (验证后关闭)

未来订阅者 (规划, 按需添加):
  - ObstacleHighlightSubscriber: WaSR 水面分割 + 障碍物勾边   (HUD 第二步)
  - JetsonStreamSubscriber     : JPEG 编码 + ZMQ 发 Jetson    (sim-to-real)
  - GimbalJitterSubscriber     : 抖动注入 (软件云台)

注: 相机稳定 (roll/pitch 校正) 不放在订阅者, 而是主循环里用 GT 直接设
    Camera_HUD 的世界姿态 (sensor_setup.stabilize_hud_camera), 因为渲染前
    必须完成, 异步订阅者时机不对。
"""

from __future__ import annotations

import os
import logging
import numpy as np
from typing import Optional

from modules.hud_pipeline import HudFrame

logger = logging.getLogger("hud_subscribers")


# ══════════════════════════════════════════════════════════════════
#  DebugSaveSubscriber — 数据通路验证 (最小可视化)
# ══════════════════════════════════════════════════════════════════

class DebugSaveSubscriber:
    """前 N 帧 HUD 帧存盘, 验证取帧→广播→订阅 通路是否打通。

    存盘内容: rgba PNG + 元数据 txt (timestamp/sim_time/gt_pose)。
    存满 N 帧后自动停存 (避免无限写盘), 但订阅者本身仍可挂在管线上。

    这是验证地基是否工作的最简单方式: 跑完看 data/hud_debug/ 有没有图。
    验证通过后 ENABLE_HUD_PIPELINE_DEBUG_SAVE=False 即可关闭。
    """

    def __init__(self, out_dir: str, max_frames: int = 10):
        self._out_dir = out_dir
        self._max = max_frames
        self._count = 0
        self._stopped = False
        os.makedirs(out_dir, exist_ok=True)
        logger.info("[HUD-Debug] 存盘订阅者就绪: out=%s, max=%d 帧", out_dir, max_frames)

    def __call__(self, frame: HudFrame) -> None:
        if self._stopped:
            return
        if self._count >= self._max:
            if not self._stopped:
                self._stopped = True
                logger.info("[HUD-Debug] 已存满 %d 帧, 停止存盘 (通路验证完成)", self._max)
            return

        try:
            # 存 PNG (RGBA→RGB)
            rgb = frame.rgba
            if rgb.ndim == 3 and rgb.shape[2] == 4:
                rgb = rgb[:, :, :3]
            png_path = os.path.join(self._out_dir, f"hud_{frame.frame_idx:04d}.png")
            _save_rgb_png(rgb, png_path)

            # 存元数据
            meta_path = os.path.join(self._out_dir, f"hud_{frame.frame_idx:04d}.txt")
            gt = frame.gt_pose
            gt_str = "None" if gt is None else "[" + ", ".join(f"{v:.3f}" for v in gt) + "]"
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(f"frame_idx: {frame.frame_idx}\n")
                f.write(f"timestamp (perf_counter): {frame.timestamp:.6f}\n")
                f.write(f"sim_time: {frame.sim_time:.6f}\n")
                f.write(f"rgba shape: {frame.rgba.shape}\n")
                f.write(f"rgba dtype: {frame.rgba.dtype}\n")
                f.write(f"gt_pose [x,y,z,qw,qx,qy,qz]: {gt_str}\n")
                f.write(f"mean brightness: {float(rgb.mean()):.1f}\n")

            self._count += 1
            logger.info("[HUD-Debug] 存帧 #%d: %s (亮度 %.0f, gt_pose={'有' if gt is not None else '无'})",
                        frame.frame_idx, png_path, float(rgb.mean()))
        except Exception as e:
            logger.error("[HUD-Debug] 存帧失败: %s", e, exc_info=True)


def _save_rgb_png(rgb: np.ndarray, path: str) -> None:
    """无 cv2/PIL 依赖的 PNG 存盘 (优先 cv2, 回退 imageio, 再回退 npy)。"""
    # 尝试 cv2 (BGR 转换)
    try:
        import cv2
        bgr = rgb[:, :, ::-1]   # RGB→BGR
        cv2.imwrite(path, bgr)
        return
    except ImportError:
        pass
    # 回退 imageio
    try:
        import imageio
        imageio.imwrite(path, rgb)
        return
    except ImportError:
        pass
    # 最后回退: 存成 .npy (改扩展名)
    npy_path = path.replace(".png", ".npy")
    np.save(npy_path, rgb)
    logger.warning("[HUD-Debug] cv2/imageio 均不可用, 改存 npy: %s", npy_path)
