"""cam_recorder — Isaac 内部相机内录类
=====================================
2026-09-30 T6.3 拆分：_CamRecorder 类自 main_sim.py 机械迁出，逻辑、
数值、默认值、语句顺序零改动。Isaac 相关 import（isaacsim.sensors.camera
等）保持在方法内部（与原文件一致），模块顶层仅依赖 numpy 与 logging，
可脱离 Isaac 环境 import。
"""

import logging

import numpy as np

logger = logging.getLogger("main_sim")


class _CamRecorder:
    """Isaac 内部相机内录：跟随视角 Camera 传感器 → 经 ffmpeg 管道编码为 mp4。

    不依赖桌面抓屏（ffmpeg gdigrab 录制的是显示器，受窗口遮挡/锁屏影响）；
    本路径使用 Isaac 渲染管线本身，GUI 与 headless 模式均可录制（第15轮）。
    """

    def __init__(self, world, out_path, res, fps):
        import subprocess, shutil
        from isaacsim.sensors.camera import Camera
        self.cam = Camera(prim_path='/World/CameraRec', name='cam_rec',
                          resolution=res,
                          translation=np.array([8.0, -8.0, 4.0]))
        world.scene.add(self.cam)
        self.cam.initialize()
        self.res = res
        self.n = 0
        ff = shutil.which('ffmpeg') or r'C:/ffmpeg/bin/ffmpeg.exe'
        self.proc = subprocess.Popen(
            [ff, '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba',
             '-s', f'{res[0]}x{res[1]}', '-r', str(fps), '-i', 'pipe:0',
             '-an', '-c:v', 'libx264', '-preset', 'fast',
             '-pix_fmt', 'yuv420p', out_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def frame(self, eye, target):
        try:
            from isaacsim.core.utils.viewports import set_camera_view
            set_camera_view(eye=list(eye), target=list(target),
                            camera_prim_path='/World/CameraRec')
            rgba = self.cam.get_rgba()
            if rgba is not None and np.asarray(rgba).size:
                self.proc.stdin.write(np.ascontiguousarray(rgba).tobytes())
                self.n += 1
        except Exception as e:
            logger.debug("内录帧失败: %s", e)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:
            pass
        print(f"[CAM-REC] 共收帧 {self.n} 帧，记录完毕", flush=True)
