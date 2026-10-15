# -*- coding: utf-8 -*-
"""
BEV 旁路线程 (Sidecar)。

设计目标: 对主仿真循环做到"零影响"。
  - 运行在独立的 daemon 线程中, 主循环不感知它的存在, 主程序退出时自动结束
  - 自带 5Hz 节流, 不依赖主循环的任何步进信号
  - 位姿/帧数据采用单向只读访问, 不加锁 (最坏只读到一帧旧值, 不影响物理正确性)
  - 输出仅写盘, 不调用 cv2.imshow, 避免与 Isaac Sim GUI 线程冲突
  - 所有异常在内部捕获, 绝不向主线程抛出

主循环接入方式 (最小侵入):
    from modules.bev.bev_sidecar import start_bev_sidecar
    start_bev_sidecar(sensor_suite, hydro_callback,
                      enabled=cfg.ENABLE_BEV_STITCH,
                      freq_hz=5.0, out_dir="./data/bev")
"""
import os
import time
import threading
import logging

import cv2
import numpy as np

from .bev_pipeline import BEVStitcher

logger = logging.getLogger("bev_sidecar")
# 独立文件日志: 不依赖主程序 stdout 重定向, 排查时直接看此文件
_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "logs", "bev_sidecar.log")
try:
    os.makedirs(os.path.dirname(os.path.abspath(_LOG_PATH)), exist_ok=True)
    _fh = logging.FileHandler(os.path.abspath(_LOG_PATH), mode="w", encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False   # 不向 root 冒泡, 避免重复输出污染主日志
except Exception:
    pass

# 旁路输出的默认参数
_DEFAULT_FREQ_HZ = 5.0
_DEFAULT_OUT_DIR = "./data/bev"


def _rgba_to_rgb(rgba):
    """Isaac Sim 回读的 rgba/rgb 为 3 或 4 通道, BEV pipeline 需要 3 通道 BGR。

    Replicator 返回的 "rgb" annotator 实际为 4 通道 (RGBA); 这里统一转成
    OpenCV 惯用的 BGR。
    """
    if rgba is None:
        return None
    arr = np.asarray(rgba)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return None
    rgb = arr[:, :, :3]
    # RGBA/RGB -> BGR (供 cv2.imwrite)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _extract_color(payload):
    """从单相机 payload 中提取彩色图, 兼容 'rgb' / 'rgba' 两种 key。

    根因: isaacsim.sensors.camera.Camera.initialize() 默认 attach 的 annotator
    名为 "rgb" (camera.py:390), 故 _current_frame 的 key 是 "rgb" 而非 "rgba"。
    若 sensor_suite 返回的 payload 用 "rgba", .get("rgba") 会静默返回 None。
    这里两个 key 都试, 避免静默失败。
    """
    for key in ("rgb", "rgba"):
        data = payload.get(key)
        if data is not None:
            return key, _rgba_to_rgb(data)
    return None, None


def _grab_single_cam(cam, name, verbose=False):
    """对单个 Camera 对象取帧, 返回 bgr ndarray 或 None。

    取数路径与诊断逻辑统一, 供环视和 top-down 相机共用:
      优先 cam.get_current_frame()['rgb'] (稳定快照), 回退 cam.get_rgba()。
    """
    if cam is None:
        return None
    try:
        frame = cam.get_current_frame()
        data = frame.get("rgb") if isinstance(frame, dict) else None
        if data is None:
            data = cam.get_rgba()
            if verbose:
                logger.warning("[诊断]   %s: _current_frame 无 rgb, 回退 get_rgba()", name)
        bgr = _rgba_to_rgb(data)
    except Exception as e:
        if verbose:
            logger.warning("[诊断]   %s 取帧异常: %s", name, e)
        return None
    if bgr is not None:
        if verbose:
            logger.info("[诊断]   %s: rgb shape=%s dtype=%s",
                        name, getattr(data, "shape", None), getattr(data, "dtype", None))
        return bgr
    if verbose:
        logger.warning("[诊断]   %s: 无有效数据 (timeline未PLAY/尚未渲染?)", name)
    return None


def _build_usv_pose(hydro_callback):
    """从 hydro_callback 单向读取世界系位姿, 拼成 [x,y,z,qw,qx,qy,qz]。

    纯只读, 不加锁。若属性缺失则返回 None (该帧跳过)。
    """
    try:
        pos = hydro_callback.current_pos_world   # (3,)
        quat = hydro_callback.current_quat_world  # (4,) [qw,qx,qy,qz]
    except AttributeError:
        return None
    if pos is None or quat is None:
        return None
    pos = np.asarray(pos, dtype=np.float64).reshape(-1)[:3]
    quat = np.asarray(quat, dtype=np.float64).reshape(-1)[:4]
    return np.concatenate([pos, quat])


def _grab_surround_frames(sensor_suite, verbose=False):
    """获取 4 路环视帧, 转成 {name: bgr_ndarray}。

    数据取数路径 (关键, 避免黑帧):
      优先用 cam.get_current_frame()["rgb"]。_current_frame 只在
      _data_acquisition_callback (camera.py:483) 处理 NEW_FRAME 事件时更新,
      数据是渲染完成后的稳定快照, 不会读到半成品缓冲。
      ❌ 不用 cam.get_rgba(): 它直接调 annotator.get_data(), 绕过 _current_frame
         的安全更新点, 在 GPU 渲染管线刷新中途会返回未完成的空缓冲 → 黑帧。
      ❌ 不用 sensor_suite.get_surround_camera_data(): 它取 frame.get("rgba")
         而 Camera 的 key 是 "rgb", 静默返回 None。

    Args:
        verbose: 若为 True, 打印一次返回结构用于诊断。
    """
    frames = {}
    cams = getattr(sensor_suite, "_surround_cameras", None)
    if cams is None:
        # 回退: 尝试包装方法 (兼容未来修复后的版本)
        data = sensor_suite.get_surround_camera_data()
        for name, payload in data.items():
            _, bgr = _extract_color(payload)
            if bgr is not None:
                frames[name] = bgr
        return frames

    if verbose:
        logger.info("[诊断] _surround_cameras: %d 个相机 %s",
                    len(cams), list(cams.keys()))
    for name, cam in cams.items():
        bgr = _grab_single_cam(cam, name, verbose=verbose)
        if bgr is not None:
            frames[name] = bgr
    return frames


def _bev_worker(sensor_suite, hydro_callback, stitcher, freq_hz, out_dir, stop_evt):
    """旁路线程主循环。"""
    period = 1.0 / freq_hz if freq_hz > 0 else 0.2
    os.makedirs(out_dir, exist_ok=True)
    frame_idx = 0

    logger.info("BEV 旁路线程启动: %.1f Hz, 输出目录 %s", freq_hz, out_dir)
    logger.info("诊断: sensor_suite=%s, hydro_callback=%s",
                type(sensor_suite).__name__, type(hydro_callback).__name__)

    _first_diag_done = False
    _empty_count = 0
    _ok_count = 0
    _black_count = 0
    _td_count = 0
    _has_topdown = getattr(sensor_suite, "_topdown_camera", None) is not None
    while not stop_evt.is_set():
        t0 = time.monotonic()
        try:
            # 第一次循环做 verbose 诊断, 打印 get_current_frame 返回结构
            frames = _grab_surround_frames(sensor_suite, verbose=not _first_diag_done)
            _first_diag_done = True
            pose = _build_usv_pose(hydro_callback)

            if frames and len(frames) >= 1 and pose is not None:
                bev = stitcher.update_frames(frames, pose)
                if bev is not None and bev.size > 0:
                    # 黑帧检测: 若 BEV 几乎全黑 (<1%像素>10), 判定为无效, 不写盘
                    nonblack_ratio = np.mean(np.any(bev > 10, axis=2)) if bev.ndim == 3 else 0.0
                    if nonblack_ratio < 0.01:
                        _black_count += 1
                        if _black_count <= 3:
                            logger.warning("检测到黑帧 (非黑%.0f%%), 跳过不写盘 #%d",
                                           nonblack_ratio * 100, _black_count)
                    else:
                        cur_idx = frame_idx  # BEV 与 top-down 共用此帧号, 保证一一对应
                        path = os.path.join(out_dir, f"bev_{cur_idx:06d}.png")
                        ok_write = cv2.imwrite(path, bev)
                        frame_idx += 1
                        _ok_count += 1
                        if _ok_count == 1:
                            logger.info("[首帧成功] 写出 %s, shape=%s, imwrite=%s",
                                        path, bev.shape, ok_write)
                        # top-down 帧采集 (帧号严格对齐 BEV): 仅在 BEV 成功出帧时才存
                        if _has_topdown:
                            td_bgr = _grab_single_cam(
                                sensor_suite._topdown_camera, "topdown",
                                verbose=not _first_diag_done)
                            if td_bgr is not None and td_bgr.size > 0:
                                td_path = os.path.join(out_dir, f"topdown_{cur_idx:06d}.png")
                                cv2.imwrite(td_path, td_bgr)
                                _td_count += 1
                                if _td_count == 1:
                                    logger.info("[首帧topdown成功] 写出 %s, shape=%s",
                                                td_path, td_bgr.shape)
            else:
                _empty_count += 1
                # 前 5 次空帧打印诊断, 定位是帧拿不到还是位姿拿不到
                if _empty_count <= 5:
                    logger.warning(
                        "无有效输入 (frames=%d, pose=%s), 跳过 #%d",
                        len(frames) if frames else 0,
                        "有效" if pose is not None else "无效",
                        _empty_count,
                    )
        except Exception:
            # 任何异常都吞掉, 绝不让旁路线程崩溃影响主仿真
            logger.warning("本周期异常 (已忽略, 不影响主仿真)", exc_info=True)

        # 自节流: 保证周期 >= period
        elapsed = time.monotonic() - t0
        remaining = period - elapsed
        if remaining > 0:
            stop_evt.wait(remaining)

    logger.info("BEV 旁路线程退出: 成功 %d 帧, topdown %d 帧, 黑帧跳过 %d 次, 无输入跳过 %d 次",
                _ok_count, _td_count, _black_count, _empty_count)


def start_bev_sidecar(sensor_suite, hydro_callback,
                      enabled=True, freq_hz=_DEFAULT_FREQ_HZ,
                      out_dir=_DEFAULT_OUT_DIR, mode="GROUND_TRUTH"):
    """启动 BEV 旁路线程。

    Args:
        sensor_suite:    传感器套件 (含 get_surround_camera_data)
        hydro_callback:  水动力回调 (含 current_pos_world / current_quat_world)
        enabled:         总开关, False 时不做任何事 (主仿真零影响)
        freq_hz:         旁路工作频率
        out_dir:         BEV 图输出目录
        mode:            拼接模式 "STATIC" | "GROUND_TRUTH"

    Returns:
        stop_evt (threading.Event): 调用 stop_evt.set() 可显式停止旁路。
        若 enabled=False, 返回 None。
    """
    if not enabled:
        logger.info("BEV 旁路未启用 (enabled=False), 主仿真不受影响")
        return None

    try:
        stitcher = BEVStitcher(mode=mode)
    except Exception:
        logger.error("BEVStitcher 初始化失败, 旁路未启动", exc_info=True)
        return None

    stop_evt = threading.Event()
    # daemon=True: 主程序退出时线程自动结束, 不会阻塞解释器关闭
    t = threading.Thread(
        target=_bev_worker,
        args=(sensor_suite, hydro_callback, stitcher, freq_hz, out_dir, stop_evt),
        name="bev-sidecar",
        daemon=True,
    )
    t.start()
    logger.info("BEV 旁路线程已派生: %s", t.name)
    return stop_evt
