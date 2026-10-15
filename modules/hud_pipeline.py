"""
hud_pipeline.py — HUD 视觉处理管线地基
========================================
HudFramePipeline: 独立 daemon 线程, 稳定地从 Camera_HUD 取帧,
按可调频率广播给任意数量的"订阅者"(回调)。

设计目标（兼顾性能/效果/可维护/可扩展）:

  性能:
    - 独立线程, 默认 10Hz, 不阻塞主仿真渲染循环
    - 取帧路径照搬 bev_sidecar 验证过的最优方式:
      优先 cam.get_current_frame()['rgb'] (稳定快照), 回退 get_rgba()
      避免 GPU 渲染中途读到半成品缓冲(黑帧)
    - 帧通过引用传递, 零拷贝; 订阅者如需保留须自行 .copy()

  可扩展 (核心):
    - 订阅者模式: 任意回调都可挂载/卸载, 互不影响
        pipeline.subscribe(my_callback)
    - 每个订阅者拿到的是 HudFrame 数据类, 含:
        rgba, timestamp, sim_time, frame_idx, gt_pose(可选)
    - 未来挂载点示例:
        · 水天線检测 + roll 软校正   (HUD 第一步)
        · 障碍物勾边 (WaSR 推理)     (HUD 第二步)
        · JPEG 编码 + ZMQ 发 Jetson  (sim-to-real 链路)
        · 抖动注入器 (软件云台)
    - 加减订阅者不用改 pipeline 本身

  可维护:
    - 零 Isaac Sim 业务依赖 (只依赖 Camera 对象的取帧 API)
    - 黑帧检测 + 异常隔离: 单个订阅者崩溃不影响其他订阅者和取帧
    - GT pose 只读读取, 不加锁 (与 bev_sidecar 一致)

  sim-to-real 友好:
    - 订阅者拿到的就是 numpy 帧 + 元数据, 不含任何 Isaac Sim 类型
    - 同一份处理代码: 仿真期收 pipeline 帧, 真船期收相机帧, 零修改

Usage:
    from modules.hud_pipeline import HudFramePipeline, HudFrame

    pipeline = HudFramePipeline(hud_camera, freq_hz=10.0)
    pipeline.subscribe(my_processor)          # 挂载处理回调
    pipeline.start()                          # 启动 (非阻塞)
    # ... 仿真运行中, my_processor 每帧被调用 ...
    pipeline.stop()                           # 优雅停止
"""

from __future__ import annotations

import os
import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Any

import numpy as np

logger = logging.getLogger("hud_pipeline")


# ══════════════════════════════════════════════════════════════════
#  数据类: 单帧 HUD 数据 (订阅者收到的就是这个)
# ══════════════════════════════════════════════════════════════════

@dataclass
class HudFrame:
    """一帧 HUD 数据。订阅者回调的唯一参数类型。

    Attributes
    ----------
    rgba : np.ndarray, shape (H, W, 4), uint8
        HUD 相机的 RGBA 帧。订阅者如需跨线程保留必须 .copy()。
    timestamp : float
        perf_counter 时间戳 (墙钟), 用于时序对齐/延迟测量。
    sim_time : float
        仿真时间 (world.current_time), 用于和物理状态对齐。
    frame_idx : int
        管线内部递增帧号, 从 0 开始。
    gt_pose : Optional[np.ndarray], shape (7,) [x,y,z,qw,qx,qy,qz]
        USV 世界系位姿 GT (仅仿真期可用, 真船期为 None)。
        用于: 软校正的上限验证、抖动量化对照、路径投影。
    extra : dict
        预留扩展字段 (如未来加相机内参、FOV、roll/pitch GT 等)。
    """
    rgba: np.ndarray
    timestamp: float
    sim_time: float
    frame_idx: int
    gt_pose: Optional[np.ndarray] = None
    extra: dict = field(default_factory=dict)


# 订阅者回调签名: 收到一帧, 返回 None (fire-and-forget)
# 异常会被 pipeline 捕获并记录, 不影响其他订阅者
HudSubscriber = Callable[[HudFrame], None]


# ══════════════════════════════════════════════════════════════════
#  取帧工具函数 (照搬 bev_sidecar._grab_single_cam 验证过的路径)
# ══════════════════════════════════════════════════════════════════

def _grab_hud_frame(cam, verbose: bool = False) -> Optional[np.ndarray]:
    """从 Camera_HUD 取一帧 RGB (uint8, H×W×3), 失败返回 None。

    取数路径 (与 bev_sidecar 一致, 避免黑帧):
      优先 cam.get_current_frame()['rgb']  → 渲染完成的稳定快照
      回退 cam.get_rgba()                   → 直接 annotator (可能半成品)
    """
    if cam is None:
        return None
    try:
        frame = cam.get_current_frame()
        data = frame.get("rgb") if isinstance(frame, dict) else None
        if data is None:
            data = cam.get_rgba()
            if verbose:
                logger.warning("[HUD] _current_frame 无 rgb, 回退 get_rgba()")
        if data is None:
            return None
        # get_current_frame()['rgb'] 通常是 RGBA, 转成 RGB
        arr = np.asarray(data)
        if arr.ndim == 3 and arr.shape[2] == 4:
            return arr[:, :, :3].copy()   # RGBA→RGB, 拷贝避免跨帧共享缓冲
        return arr
    except Exception as e:
        if verbose:
            logger.warning("[HUD] 取帧异常: %s", e)
        return None


def _is_black_frame(rgb: np.ndarray, threshold_ratio: float = 0.01) -> bool:
    """黑帧检测: 非黑像素占比 < 1% 判为黑帧 (照搬 bev_sidecar 阈值)。"""
    if rgb.ndim != 3:
        return True
    nonblack_ratio = np.mean(np.any(rgb > 10, axis=2))
    return nonblack_ratio < threshold_ratio


# ══════════════════════════════════════════════════════════════════
#  HudFramePipeline 主类
# ══════════════════════════════════════════════════════════════════

class HudFramePipeline:
    """HUD 帧管线: daemon 线程稳定取帧 + 广播给订阅者。

    Parameters
    ----------
    hud_camera : Camera
        isaacsim.sensors.camera.Camera 实例 (Camera_HUD)。
    hydro_callback : optional
        水动力学回调, 用于读取 GT pose。None 则 gt_pose 永远为 None。
    freq_hz : float
        取帧频率。默认 10Hz (每 100ms 一帧), 平衡实时性与渲染开销。
        软校正/检测类订阅者 10Hz 足够; 录制/Jetson 传输可调高。
    """

    def __init__(
        self,
        hud_camera,
        hydro_callback=None,
        freq_hz: float = 10.0,
    ):
        self._cam = hud_camera
        self._hydro = hydro_callback
        self._freq_hz = freq_hz
        self._period = 1.0 / freq_hz if freq_hz > 0 else 0.1

        self._subscribers: List[HudSubscriber] = []
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frame_idx = 0

        # 统计 (供诊断/调优, 不影响逻辑)
        self._stat_ok = 0
        self._stat_black = 0
        self._stat_fail = 0
        self._stat_subscriber_errors = 0
        self._sub_error_counts: dict = {}   # {订阅者id: 已记录错误次数}, 防止刷屏

    # ────────────────────────────────────────────────────────
    #  订阅者管理 (核心扩展点)
    # ────────────────────────────────────────────────────────

    def subscribe(self, callback: HudSubscriber) -> None:
        """挂载一个订阅者回调。管线运行中也可调用 (线程安全)。"""
        if not callable(callback):
            raise TypeError("订阅者必须是可调用对象")
        self._subscribers.append(callback)
        logger.info("[HUD] 订阅者已挂载: %s (共 %d 个)",
                    getattr(callback, "__name__", repr(callback)),
                    len(self._subscribers))

    def unsubscribe(self, callback: HudSubscriber) -> bool:
        """卸载订阅者。返回是否成功移除。"""
        try:
            self._subscribers.remove(callback)
            logger.info("[HUD] 订阅者已卸载: %s (剩 %d 个)",
                        getattr(callback, "__name__", repr(callback)),
                        len(self._subscribers))
            return True
        except ValueError:
            return False

    # ────────────────────────────────────────────────────────
    #  生命周期
    # ────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动取帧线程 (非阻塞)。重复调用安全。"""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("[HUD] 管线已在运行, 忽略重复 start()")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="HudFramePipeline",
            daemon=True,   # 主程序退出时自动结束, 不阻塞解释器关闭
        )
        self._thread.start()
        logger.info("[HUD] 帧管线启动: %.1f Hz, 订阅者 %d 个",
                    self._freq_hz, len(self._subscribers))

    def stop(self, timeout: float = 2.0) -> None:
        """优雅停止管线。"""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("[HUD] 帧管线已停止。统计: 取帧成功 %d, 黑帧 %d, 失败 %d, 订阅者错误 %d",
                    self._stat_ok, self._stat_black, self._stat_fail,
                    self._stat_subscriber_errors)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ────────────────────────────────────────────────────────
    #  内部: 主循环
    # ────────────────────────────────────────────────────────

    def _build_gt_pose(self) -> Optional[np.ndarray]:
        """只读读取 hydro_callback 的世界系位姿, 拼成 (7,)。失败返回 None。

        与 bev_sidecar._build_usv_pose 一致, 纯只读不加锁。
        """
        if self._hydro is None:
            return None
        try:
            pos = self._hydro.current_pos_world     # (3,)
            quat = self._hydro.current_quat_world   # (4,) [qw,qx,qy,qz]
        except AttributeError:
            return None
        if pos is None or quat is None:
            return None
        pos = np.asarray(pos, dtype=np.float64).reshape(-1)[:3]
        quat = np.asarray(quat, dtype=np.float64).reshape(-1)[:4]
        return np.concatenate([pos, quat])

    def _run(self) -> None:
        """daemon 线程主循环。"""
        logger.info("[HUD] 取帧线程进入主循环 (period=%.3fs)", self._period)
        _first_diag = False

        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                # 1. 取帧 (第一次 verbose 诊断)
                rgb = _grab_hud_frame(self._cam, verbose=not _first_diag)
                _first_diag = True

                if rgb is None:
                    self._stat_fail += 1
                    if self._stat_fail <= 3:
                        logger.warning("[HUD] 取帧失败 #%d (timeline未PLAY/尚未渲染?)",
                                       self._stat_fail)
                elif _is_black_frame(rgb):
                    self._stat_black += 1
                    if self._stat_black <= 3:
                        logger.warning("[HUD] 检测到黑帧 #%d, 跳过广播",
                                       self._stat_black)
                else:
                    self._stat_ok += 1
                    # 2. 组装 HudFrame
                    sim_time = 0.0
                    try:
                        if self._hydro is not None and hasattr(self._hydro, '_world'):
                            sim_time = float(self._hydro._world.current_time)
                    except Exception:
                        pass

                    frame = HudFrame(
                        rgba=rgb,
                        timestamp=t0,
                        sim_time=sim_time,
                        frame_idx=self._frame_idx,
                        gt_pose=self._build_gt_pose(),
                    )
                    self._frame_idx += 1

                    # 3. 广播给所有订阅者 (异常隔离: 单个崩溃不影响其他)
                    for sub in list(self._subscribers):
                        try:
                            sub(frame)
                        except Exception as e:
                            self._stat_subscriber_errors += 1
                            sub_id = id(sub)
                            cnt = self._sub_error_counts.get(sub_id, 0) + 1
                            self._sub_error_counts[sub_id] = cnt
                            # 首次错误打印完整 traceback 排查; 后续静默计数防刷屏
                            if cnt <= 1:
                                logger.error("[HUD] 订阅者 %s 异常 (首次, 后续静默): %s",
                                             getattr(sub, "__name__", repr(sub)), e,
                                             exc_info=True)
                            elif cnt % 50 == 0:
                                # 每 50 次提示一次, 避免完全无声
                                logger.warning("[HUD] 订阅者 %s 已连续错误 %d 次 (静默中)",
                                               getattr(sub, "__name__", repr(sub)), cnt)

            except Exception as e:
                self._stat_fail += 1
                logger.error("[HUD] 取帧主循环异常: %s", e, exc_info=True)

            # 4. 按频率节流 (消耗剩余周期时间)
            elapsed = time.monotonic() - t0
            remaining = self._period - elapsed
            if remaining > 0:
                self._stop_evt.wait(remaining)   # 可被 stop() 唤醒, 不死睡

        logger.info("[HUD] 取帧线程退出")


# ══════════════════════════════════════════════════════════════════
#  便利函数: 供 main_sim 调用, 一行启动管线
# ══════════════════════════════════════════════════════════════════

def start_hud_pipeline(
    hud_camera,
    hydro_callback=None,
    freq_hz: float = 10.0,
    subscribers: Optional[List[HudSubscriber]] = None,
) -> HudFramePipeline:
    """创建并启动 HUD 帧管线。

    Args:
        hud_camera: Camera_HUD 实例
        hydro_callback: 水动力学回调 (供 GT pose), 可选
        freq_hz: 取帧频率
        subscribers: 初始订阅者列表 (也可后续 pipeline.subscribe 挂载)

    Returns:
        HudFramePipeline 实例 (已启动)。调用 .stop() 停止。
    """
    pipeline = HudFramePipeline(hud_camera, hydro_callback, freq_hz)
    if subscribers:
        for sub in subscribers:
            pipeline.subscribe(sub)
    pipeline.start()
    return pipeline
