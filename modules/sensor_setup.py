# -*- coding: utf-8 -*-
"""
sensor_setup.py — USV 感知传感器套件 (USV Perception Sensor Suite)

本模块负责在 USV 上挂载 RTX LiDAR 和 RGB-D Camera，
并提供逐帧数据采集与保存接口。

依赖:
    - Isaac Sim 5.1.0 (isaacsim.sensors.rtx / isaacsim.sensors.camera)
    - NumPy
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import numpy as np

# ── Isaac Sim 传感器 API ──────────────────────────────────────────────────────
from isaacsim.sensors.rtx import LidarRtx
from isaacsim.sensors.camera import Camera

# ── USD / Omniverse ──────────────────────────────────────────────────────────
from pxr import Gf, Sdf, UsdGeom

# ── Replicator Annotator（用于附加 LiDAR 数据通道） ───────────────────────────
import omni.replicator.core as rep

# ── 本项目配置 ────────────────────────────────────────────────────────────────
from config import sim_config


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """四元数乘法 (Hamilton积), 输入输出均为 [w,x,y,z]。"""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ])


class USVSensorSuite:
    """
    USV 感知传感器套件。

    在指定的 USV prim 下挂载以下传感器:
        1. RTX LiDAR   → 点云 + 平面扫描
        2. RGB-D Camera → 彩色图像 + 深度图

    Typical usage:
        >>> suite = USVSensorSuite(world, "/World/USV")
        >>> suite.setup_lidar()
        >>> suite.setup_camera()
        >>> # 在仿真循环中
        >>> lidar_data = suite.get_lidar_data()
        >>> camera_data = suite.get_camera_data()
        >>> suite.save_data(frame_idx=0, output_dir="./data")
    """

    # ──────────────────────────────────────────────────────────────────────
    # 初始化 (Initialization)
    # ──────────────────────────────────────────────────────────────────────
    def __init__(self, world, usv_prim_path: str) -> None:
        """
        初始化传感器套件。

        Args:
            world: Isaac Sim World 实例 (isaacsim.core.api.World).
            usv_prim_path: USV 根 prim 的 USD Stage 路径,
                           例如 ``"/World/USV"``。
        """
        self._world = world
        self._usv_prim_path: str = usv_prim_path

        # 确保 Sensors 目录存在，否则底层 Replicator 命令可能会创建失败
        from isaacsim.core.utils.prims import define_prim
        define_prim(f"{self._usv_prim_path}/Sensors", "Xform")

        # 传感器实例占位 (will be created by setup_* methods)
        self._lidar: Optional[LidarRtx] = None
        self._camera_l: Optional[Camera] = None
        self._camera_r: Optional[Camera] = None
        self._surround_cameras: Dict[str, Camera] = {}
        self._topdown_camera: Optional[Camera] = None
        self._camera_hud: Optional[Camera] = None
        self._hud_local_offset = np.array([2.5, 0.0, 2.8])   # 相机相对船体偏置 (供 stabilize 用)
        self._hud_stabilize_enabled = False                    # 是否启用 GT 稳定

        # LiDAR Annotator 引用 (attached after setup_lidar)
        self._anno_point_cloud: Optional[rep.AnnotatorRegistry] = None
        self._anno_flat_scan: Optional[rep.AnnotatorRegistry] = None
        self._anno_stable_id: Optional[rep.AnnotatorRegistry] = None

    # ──────────────────────────────────────────────────────────────────────
    # LiDAR 设置 (LiDAR Setup)
    # ──────────────────────────────────────────────────────────────────────
    def setup_lidar(self, record_data: bool = False) -> LidarRtx:
        """
        创建并挂载 RTX LiDAR 传感器。

        【重要】Isaac Sim 5.1.0 的 Replicator create_batch 不支持深层嵌套路径
        （如 /World/USV/ProtectorUSV/Sensors/LiDAR），会把 / 替换成 _ 并
        把 prim 创建到舞台根节点，导致传感器永远不跟随船体。

        解决方案：将 LiDAR 创建到 /World/USV_LiDAR（简单路径），
        然后在仿真主循环中每帧调用 sync_lidar_pose() 手动同步位姿。

        Args:
            record_data: 是否附加 Annotator（会创建 Replicator RenderProduct
                         + CopyToHost 同步拷贝，每帧都跑，非录制时不需要）。
        """
        # ── 使用简单路径规避 Replicator 的路径 bug ──────────────────────
        simple_lidar_path = "/World/USV_LiDAR"
        self._lidar_local_offset = np.array([
            sim_config.LIDAR_MOUNT_FORWARD,
            0.0,
            sim_config.LIDAR_MOUNT_HEIGHT,
        ])

        self._lidar = LidarRtx(
            prim_path=simple_lidar_path,
            name="usv_lidar",
            translation=np.array([0.0, 0.0, 0.0]),  # 世界原点，后续手动同步
            orientation=np.array([1.0, 0.0, 0.0, 0.0]),
        )
        # 记录 LidarRtx 实际使用的 prim 路径（可能被 Replicator 改名）
        self._lidar_actual_path = self._lidar.prim_path
        print(f"[SensorSuite] LiDAR 创建于: {self._lidar_actual_path}")

        # ── 启用可视化 (Visualization) ──────────────────────────────────
        self._lidar.enable_visualization()

        # ── 附加 Annotator (仅录制时! 否则 CopyToHost 每帧同步拷贝白耗 GPU) ──
        if record_data:
            render_product = self._lidar.get_render_product_path()
            self._anno_point_cloud = rep.AnnotatorRegistry.get_annotator(
                "IsaacExtractRTXSensorPointCloudNoAccumulator"
            )
            self._anno_point_cloud.attach([render_product])
            self._anno_stable_id = rep.AnnotatorRegistry.get_annotator("StableIdMap")
            self._anno_stable_id.attach([render_product])
            print("[SensorSuite] LiDAR Annotators 已附加 (录制模式)", flush=True)
        else:
            print("[SensorSuite] LiDAR Annotators 已跳过 (非录制模式, 避免 CopyToHost 开销)", flush=True)

        return self._lidar

    def sync_lidar_pose(self, usv_pos: np.ndarray, usv_quat: np.ndarray) -> None:
        """
        每帧调用：将 LiDAR 的世界位姿同步到 USV 位置 + 本地偏移。

        Args:
            usv_pos:  USV 世界坐标 [x, y, z]
            usv_quat: USV 世界四元数 [qw, qx, qy, qz]
        """
        if self._lidar is None:
            return

        # 将本地偏移按 USV 航向旋转到世界坐标系
        qw, qx, qy, qz = usv_quat
        # 四元数旋转向量 v' = q * v * q^-1 (只旋转 XY 平面偏移)
        # 简化：对于船舶只有 yaw 旋转，用 2D 旋转即可
        import math
        # 从四元数提取 yaw
        siny_cosp = 2.0 * (qw * qz + qx * qy)
        cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        cos_y, sin_y = math.cos(yaw), math.sin(yaw)
        dx = self._lidar_local_offset[0]
        dy = self._lidar_local_offset[1]
        dz = self._lidar_local_offset[2]

        world_x = usv_pos[0] + dx * cos_y - dy * sin_y
        world_y = usv_pos[1] + dx * sin_y + dy * cos_y
        world_z = usv_pos[2] + dz

        try:
            self._lidar.set_world_pose(
                position=np.array([world_x, world_y, world_z]),
                orientation=usv_quat,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"[严重错误] LiDAR 内部 set_world_pose 失败，组件可能失效，尝试 fallback 到 USD API: {e}", exc_info=True)
            # fallback: 直接用 USD API
            from pxr import UsdGeom, Gf
            import omni.usd
            stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(self._lidar_actual_path)
            if prim.IsValid():
                xformable = UsdGeom.Xformable(prim)
                xformable.ClearXformOpOrder()
                xformable.AddTranslateOp().Set(
                    Gf.Vec3d(world_x, world_y, world_z)
                )

    # ──────────────────────────────────────────────────────────────────────
    # Camera 设置 (Camera Setup)
    # ──────────────────────────────────────────────────────────────────────
    def setup_stereo_cameras(self, record_data: bool = False) -> Tuple[Camera, Camera]:
        """
        创建并挂载双目 RGB-D 摄像头到 USV。

        Args:
            record_data: 是否注册深度通道。add_distance_to_image_plane_to_frame()
                         会创建 Replicator RenderProduct + DistanceToImagePlane
                         CopyToHost 节点，每帧同步拷贝深度图到 CPU。
                         非录制时跳过以避免 GPU 同步开销。
        """
        import omni.usd
        stage = omni.usd.get_context().get_stage()

        path_l = f"{self._usv_prim_path}/Sensors/Camera_L"
        path_r = f"{self._usv_prim_path}/Sensors/Camera_R"

        # 1. 先用 identity 朝向创建 Camera 实例
        self._camera_l = Camera(
            prim_path=path_l, name="usv_camera_l",
            resolution=(sim_config.CAMERA_RESOLUTION_W, sim_config.CAMERA_RESOLUTION_H),
            translation=sim_config.CameraConfig.MOUNT_POSITION_LEFT,
            orientation=np.array([1.0, 0.0, 0.0, 0.0]),
        )
        self._camera_r = Camera(
            prim_path=path_r, name="usv_camera_r",
            resolution=(sim_config.CAMERA_RESOLUTION_W, sim_config.CAMERA_RESOLUTION_H),
            translation=sim_config.CameraConfig.MOUNT_POSITION_RIGHT,
            orientation=np.array([1.0, 0.0, 0.0, 0.0]),
        )

        # 2. 用 Euler 角覆写朝向
        for path, pos in [
            (path_l, sim_config.CameraConfig.MOUNT_POSITION_LEFT),
            (path_r, sim_config.CameraConfig.MOUNT_POSITION_RIGHT),
        ]:
            cam_prim = stage.GetPrimAtPath(path)
            if cam_prim.IsValid():
                cam_xform = UsdGeom.Xformable(cam_prim)
                cam_xform.ClearXformOpOrder()
                cam_xform.AddTranslateOp().Set(
                    Gf.Vec3d(float(pos[0]), float(pos[1]), float(pos[2]))
                )
                cam_xform.AddRotateXYZOp().Set(Gf.Vec3d(90.0, 0.0, -90.0))

        for cam in (self._camera_l, self._camera_r):
            try:
                cam.initialize()
                # 深度通道注册会创建 CopyToHost pipeline，每帧同步拷贝
                if record_data:
                    cam.add_distance_to_image_plane_to_frame()
            except Exception as e:
                pass

        if record_data:
            print("[SensorSuite] Stereo Cameras initialized + 深度通道 (录制模式)", flush=True)
        else:
            print("[SensorSuite] Stereo Cameras initialized (无深度通道, 避免 CopyToHost 开销)", flush=True)
        return self._camera_l, self._camera_r

    def setup_surround_cameras(self, record_data: bool = False) -> Dict[str, Camera]:
        """
        创建并挂载四路鱼眼环视摄像头到 USV，完全独立于双目。
        """
        import omni.usd
        stage = omni.usd.get_context().get_stage()

        pitch = sim_config.SurroundCameraConfig.PITCH_DOWN
        configs = {
            "front": (f"{self._usv_prim_path}/Sensors/Surround_F", sim_config.SurroundCameraConfig.MOUNT_POSITION_FRONT, -90.0),
            "rear":  (f"{self._usv_prim_path}/Sensors/Surround_B", sim_config.SurroundCameraConfig.MOUNT_POSITION_REAR,  90.0),
            "left":  (f"{self._usv_prim_path}/Sensors/Surround_L", sim_config.SurroundCameraConfig.MOUNT_POSITION_LEFT,  0.0),
            "right": (f"{self._usv_prim_path}/Sensors/Surround_R", sim_config.SurroundCameraConfig.MOUNT_POSITION_RIGHT, 180.0),
        }
        
        with open("g:/Playground/isaac_usv_sim/logs/setup_cams.txt", "w", encoding="utf-8") as f:
            f.write("Starting setup_surround_cameras\n")
            for name, (path, pos, yaw) in configs.items():
                f.write(f"Creating camera: {name} at {path} with pos {pos} yaw {yaw}\n")
                try:
                    # 使用 Gf.Rotation 无歧义四元数乘法，彻底消除所有方向的 Roll 侧倾
                    # USD 约定：q_a * q_b * q_c → 行向量矩阵 M_c · M_b · M_a
                    # 因此 q_yaw * q_align * q_pitch → M_row = M_pitch · M_align · M_yaw = Rx(45°) · Rz(yaw)
                    # 世界外在旋转顺序（固定世界轴）：先绕世界 X 轴倾斜 45°，再绕世界 Z 轴偏航
                    # 数学证明：Right 向量 = (cos(yaw), sin(yaw), 0)，Z 分量恒为 0 ∀yaw，绝无侧倾
                    q_yaw = Gf.Rotation(Gf.Vec3d(0, 0, 1), float(yaw)).GetQuat()
                    q_align = Gf.Rotation(Gf.Vec3d(1, 0, 0), 90.0).GetQuat()
                    q_pitch = Gf.Rotation(Gf.Vec3d(1, 0, 0), -float(pitch)).GetQuat()
                    q_final = q_yaw * q_align * q_pitch
                    quat_np = np.array([q_final.GetReal(), q_final.GetImaginary()[0], q_final.GetImaginary()[1], q_final.GetImaginary()[2]])
                    
                    cam = Camera(
                        prim_path=path, name=f"usv_surround_{name}",
                        resolution=sim_config.SurroundCameraConfig.RESOLUTION,
                        translation=pos,
                        orientation=np.array([1.0, 0.0, 0.0, 0.0]),
                    )
                    self._surround_cameras[name] = cam
                    f.write(f"  Camera wrapper created for {name}\n")

                    # 每个相机创建后立即初始化（必须在 for 循环内部）
                    cam.initialize()
                    f.write(f"  Camera initialized for {name}\n")

                    # 显式采用 "usd" 坐标系设置局部相对位姿，绕过构造函数的 "world" 自动转换，彻底消除 Roll 侧倾
                    cam.set_local_pose(translation=pos, orientation=quat_np, camera_axes="usd")
                    f.write(f"  Camera local pose set with camera_axes='usd' for {name}\n")

                    # 正确配置鱼眼镜头及 OpenCvFisheye 畸变属性
                    # 关键：Isaac Sim 内部的 set_focal_length / set_horizontal_aperture 接收的是米（m）单位！
                    # 如果直接传毫米值（如 2.0, 24.0），内部会再乘以 10 倍，导致投影矩阵完全错误，
                    # 渲染器就退化成 pinhole 且畸变失效。
                    # 正确做法：参照官方 camera_opencv_fisheye.py，用 pixel_size 来换算。
                    try:
                        import math
                        w, h = sim_config.SurroundCameraConfig.RESOLUTION
                        fov_deg = sim_config.SurroundCameraConfig.FOV
                        
                        # 像素物理尺寸 (微米)，仿真中可自由设定
                        pixel_size_um = 3.0  # 微米，跟官方示例一致
                        
                        # 根据 FOV 计算像素焦距，使鱼眼圆刚好铺满 w×h
                        # 等距投影: r = f_px * theta, 当 theta = fov/2 时 r = w/2
                        max_theta_rad = math.radians(fov_deg / 2.0)
                        f_px = (min(w, h) / 2.0) / max_theta_rad
                        
                        # 从像素焦距和 pixel_size 计算物理量（单位：米）
                        horizontal_aperture_m = pixel_size_um * w * 1e-6    # 像素数 × 像素尺寸 → 米
                        vertical_aperture_m   = pixel_size_um * h * 1e-6
                        focal_length_m        = pixel_size_um * f_px * 1e-6  # 像素焦距 × 像素尺寸 → 米
                        
                        # 1. 先设置物理参数（单位：米），确保传感器像场正确
                        cam.set_focal_length(focal_length_m)
                        cam.set_horizontal_aperture(horizontal_aperture_m)
                        cam.set_vertical_aperture(vertical_aperture_m)
                        cam.set_clipping_range(0.05, 1.0e5)
                        
                        # 2. 使用 set_opencv_fisheye_properties API 激活 OpenCV Fisheye 畸变
                        # k1 < 0 产生桶形畸变（边缘压缩），这是真实鱼眼镜头的典型行为
                        # 已调大至 -0.20 以呈现极其明显的非线性桶型鱼眼弯曲，配合 k2=0.025 保证边缘无折叠倒影
                        cam.set_opencv_fisheye_properties(
                            cx=float(w / 2.0),
                            cy=float(h / 2.0),
                            fx=float(f_px),
                            fy=float(f_px),
                            fisheye=[-0.20, 0.025, 0.0, 0.0]  # 强桶形鱼眼配置
                        )
                        
                        f.write(f"  鱼眼配置成功 {name}: f_px={f_px:.2f}, focal_m={focal_length_m:.6f}, "
                                f"aperture_m={horizontal_aperture_m:.6f}, k1=-0.20\n")
                    except Exception as ex:
                        f.write(f"  [ERROR] Failed to set fisheye properties for {name}: {ex}\n")
                    if record_data:
                        cam.add_distance_to_image_plane_to_frame()
                        f.write(f"  Distance-to-image-plane annotator added for {name}\n")
                except Exception as e:
                    f.write(f"  [ERROR] Failed at {name}: {e}\n")
                    import traceback
                    f.write(traceback.format_exc() + "\n")
                
        if record_data:
            print(f"[SensorSuite] Surround Cameras initialized ({len(self._surround_cameras)} cameras, fisheye mode, 录制模式)", flush=True)
        else:
            print(f"[SensorSuite] Surround Cameras initialized ({len(self._surround_cameras)} cameras, fisheye mode)", flush=True)
            
        return self._surround_cameras

    def setup_topdown_camera(self, record_data: bool = False) -> Camera:
        """
        创建一路顶视正交相机，挂在船顶正上方朝下，作为 BEV 拼接效果的 ground truth。
        正交投影: aperture 控制覆盖范围 (无焦距/畸变概念)，30m×30m 覆盖与 BEV 对齐。
        """
        import config.sim_config as sim_config
        from pxr import Gf

        path = f"{self._usv_prim_path}/Sensors/TopDown"
        pos = sim_config.TopDownCameraConfig.MOUNT_POSITION
        coverage = sim_config.TopDownCameraConfig.COVERAGE_M
        res = sim_config.TopDownCameraConfig.RESOLUTION

        # 朝向: 默认相机 -Z 朝向。绕 X 轴 180° 翻成 +Z (朝下看水面)
        q_down = Gf.Rotation(Gf.Vec3d(1, 0, 0), 180.0).GetQuat()
        quat_np = np.array([q_down.GetReal(),
                            q_down.GetImaginary()[0],
                            q_down.GetImaginary()[1],
                            q_down.GetImaginary()[2]])

        cam = Camera(
            prim_path=path, name="usv_topdown",
            resolution=res,
            translation=pos,
            orientation=np.array([1.0, 0.0, 0.0, 0.0]),
        )
        self._topdown_camera = cam
        cam.initialize()
        cam.set_local_pose(translation=pos, orientation=quat_np, camera_axes="usd")

        # 正交投影: 切换投影模式 + 设置覆盖范围 (aperture, stage 单位=米)
        cam.set_projection_mode("orthographic")
        cam.set_horizontal_aperture(float(coverage))
        cam.set_vertical_aperture(float(coverage))
        cam.set_clipping_range(0.1, 1.0e5)

        if record_data:
            cam.add_distance_to_image_plane_to_frame()

        print(f"[SensorSuite] Top-Down Orthographic Camera initialized "
              f"(coverage {coverage}m×{coverage}m, res {res})", flush=True)
        return cam

    # ──────────────────────────────────────────────────────────────────────
    # HUD 前视针孔相机 (AR HUD 视图, 独立于双目/环视/顶视)
    # ──────────────────────────────────────────────────────────────────────
    def setup_hud_camera(self, record_data: bool = False) -> Camera:
        """
        创建一路独立的针孔前视相机，作为 AR HUD 视图。

        架构 (与 LiDAR 同构): 相机创建为世界独立 prim (/World/USV_HUDCamera),
        不是 USV 子节点。这样主循环可用 GT 直接控制其世界姿态——
        位置跟船, 朝向只保留 yaw (跟船航向), roll/pitch 清零 (抵消浪引起的晃动)。
        即"对齐到水天线/世界水平参考", 而非对齐船体。

        FOV=90° 针孔、无畸变 → 世界系路径带投影下天然近宽远窄。
        """
        import omni.usd
        stage = omni.usd.get_context().get_stage()

        # 世界独立路径 (与 LiDAR 的 /World/USV_LiDAR 同构), 便于 GT 直接设世界姿态
        path = "/World/USV_HUDCamera"
        pos = self._hud_local_offset.copy()

        cam = Camera(
            prim_path=path, name="usv_camera_hud",
            resolution=(sim_config.CAMERA_RESOLUTION_W, sim_config.CAMERA_RESOLUTION_H),
            translation=np.array([0.0, 0.0, 0.0]),   # 世界原点, 由 stabilize 每帧设位
            orientation=np.array([1.0, 0.0, 0.0, 0.0]),
        )
        self._camera_hud = cam

        # 朝向: Camera 默认 -Z 朝向, 用 Euler(90,0,-90) 转成 +X 前视 (与双目一致)
        cam_prim = stage.GetPrimAtPath(path)
        if cam_prim.IsValid():
            cam_xform = UsdGeom.Xformable(cam_prim)
            cam_xform.ClearXformOpOrder()
            cam_xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.0))
            cam_xform.AddRotateXYZOp().Set(Gf.Vec3d(90.0, 0.0, -90.0))

        try:
            cam.initialize()
            if record_data:
                cam.add_distance_to_image_plane_to_frame()
        except Exception as e:
            print(f"[SensorSuite] HUD Camera initialize warning: {e}", flush=True)

        print(f"[SensorSuite] HUD Camera initialized (世界独立 prim, 针孔前视 FOV={sim_config.CameraConfig.FOV}°, "
              f"待 stabilize_hud_camera 每帧同步)", flush=True)
        return cam

    def stabilize_hud_camera(self, usv_pos: np.ndarray, usv_quat: np.ndarray) -> None:
        """
        每帧调用: 用 GT 把 HUD 相机的世界位姿同步到船, 但 roll/pitch 清零, 只保留 yaw。

        相机位置: 船位置 + 按船 yaw 旋转的 local 偏置 (与 sync_lidar_pose 同构)。
        相机朝向: 船 yaw (跟船航向) ⊕ 固定前视偏置, roll/pitch 抵消为 0。
        → 画面对齐到世界水平参考 (水天线稳定), 而非跟着船晃。

        Args:
            usv_pos:  USV 世界坐标 [x, y, z]
            usv_quat: USV 世界四元数 [qw, qx, qy, qz]
        """
        if self._camera_hud is None or not self._hud_stabilize_enabled:
            return
        import math
        qw, qx, qy, qz = float(usv_quat[0]), float(usv_quat[1]), float(usv_quat[2]), float(usv_quat[3])

        # 提取 yaw (绕 Z), 忽略 roll/pitch → 构造"只保留 yaw"的稳定姿态
        siny_cosp = 2.0 * (qw * qz + qx * qy)
        cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        # 位置: 船位 + local 偏置按 yaw 旋转
        cos_y, sin_y = math.cos(yaw), math.sin(yaw)
        dx, dy, dz = self._hud_local_offset
        world_x = usv_pos[0] + dx * cos_y - dy * sin_y
        world_y = usv_pos[1] + dx * sin_y + dy * cos_y
        world_z = usv_pos[2] + dz

        # 朝向: yaw(Rz) ⊕ 固定前视偏置 R_fix (Camera -Z→+X)
        # R_fix = Rz(-90)·Rx(90); 稳定朝向 = Rz(yaw)·R_fix
        # 用四元数乘法合成, 避免欧拉角奇点
        half = 0.5
        q_yaw = np.array([math.cos(yaw * half), 0.0, 0.0, math.sin(yaw * half)])        # Rz(yaw)
        q_fix = np.array([math.cos(math.radians(90) * half),                            # Rx(90)
                          math.sin(math.radians(90) * half), 0.0, 0.0])
        q_fix2 = np.array([math.cos(math.radians(-90) * half),                          # Rz(-90)
                           0.0, 0.0, math.sin(math.radians(-90) * half)])
        # 稳定朝向 = q_yaw * q_fix(Rx90) * q_fix2(Rz-90)
        q_stable = _quat_mul(_quat_mul(q_yaw, q_fix), q_fix2)

        try:
            self._camera_hud.set_world_pose(
                position=np.array([world_x, world_y, world_z]),
                orientation=q_stable,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[HUD] stabilize set_world_pose 失败: {e}")


    # ──────────────────────────────────────────────────────────────────────
    # 异步 Writer (Async Data Pipeline)
    # ──────────────────────────────────────────────────────────────────────
    def setup_async_writer(self, output_dir: str = "./data") -> None:
        """
        用 Replicator Writer 替代手动 get_data()。

        Writer 内部用 backend.schedule() 在后台线程做 GPU→CPU 拷贝和磁盘 I/O，
        主线程零阻塞。
        """
        render_products = []

        # LiDAR render product
        if self._lidar is not None:
            rp = self._lidar.get_render_product_path()
            if rp:
                render_products.append(rp)

        # Camera render products
        for cam in (self._camera_l, self._camera_r):
            if cam is not None:
                try:
                    rp = cam.get_render_product_path()
                    if rp:
                        render_products.append(rp)
                except Exception:
                    pass

        # Surround Camera render products
        for cam in self._surround_cameras.values():
            if cam is not None:
                try:
                    rp = cam.get_render_product_path()
                    if rp:
                        render_products.append(rp)
                except Exception:
                    pass

        if not render_products:
            print("[SensorSuite] 警告: 无可用 RenderProduct, Writer 未创建")
            return

        writer = rep.WriterRegistry.get("BasicWriter")
        writer.initialize(
            output_dir=output_dir,
            rgb=True,
            distance_to_image_plane=True,
            # point_cloud 在 Isaac Sim 5.1.0 BasicWriter 中已移除，
            # 点云数据通过 RTX Annotator 单独获取（见 get_lidar_data）
        )
        writer.attach(render_products)
        self._writer = writer
        print(f"[SensorSuite] Async Writer attached to {len(render_products)} render products → {output_dir}")

    # ──────────────────────────────────────────────────────────────────────
    # 数据采集 (Data Retrieval)
    # ──────────────────────────────────────────────────────────────────────
    def get_lidar_data(self) -> Dict[str, Any]:
        """
        获取当前帧 LiDAR 数据。

        Returns:
            dict: 包含以下键值的字典 ──
                - ``"point_cloud"``: np.ndarray, shape (N, 3) — XYZ 点云
                - ``"flat_scan"``:   dict — 2D 平面扫描数据
                    - ``"distances"``: np.ndarray — 每束距离值 (m)
                    - ``"angles"``:    np.ndarray — 每束方位角 (rad)
                    - ``"intensities"``: np.ndarray — 每束反射强度
                - ``"stable_ids"``: np.ndarray — 每点实例 ID

            如果 LiDAR 尚未初始化则返回空字典。
        """
        if self._lidar is None:
            print("[SensorSuite] 警告: LiDAR 尚未设置, 请先调用 setup_lidar()")
            return {}

        result: Dict[str, Any] = {}

        # ── 点云 ────────────────────────────────────────────────────────
        pc_data = self._anno_point_cloud.get_data()
        if pc_data is not None and "data" in pc_data:
            # RTX annotator 返回的点云通常在 "data" 键下, shape (N, 3)
            result["point_cloud"] = np.array(pc_data["data"], dtype=np.float32)
        else:
            # 回退: 尝试将整体 data 当作 ndarray 直接使用
            result["point_cloud"] = (
                np.array(pc_data, dtype=np.float32)
                if pc_data is not None
                else np.empty((0, 3), dtype=np.float32)
            )

        # ── 平面扫描 ────────────────────────────────────────────────────
        flat_data = self._anno_flat_scan.get_data()
        if flat_data is not None:
            # 扁平扫描 annotator 通常返回 dict: linearDepthData, azimuth, intensities
            distances = flat_data.get(
                "linearDepthData",
                flat_data.get("data", np.empty(0, dtype=np.float32)),
            )
            azimuths = flat_data.get("azimuth", np.empty(0, dtype=np.float32))
            intensities = flat_data.get("intensities", np.empty(0, dtype=np.float32))
            result["flat_scan"] = {
                "distances": np.array(distances, dtype=np.float32),
                "angles": np.array(azimuths, dtype=np.float32),
                "intensities": np.array(intensities, dtype=np.float32),
            }
        else:
            result["flat_scan"] = {
                "distances": np.empty(0, dtype=np.float32),
                "angles": np.empty(0, dtype=np.float32),
                "intensities": np.empty(0, dtype=np.float32),
            }

        # ── 稳定 ID ─────────────────────────────────────────────────────
        sid_data = self._anno_stable_id.get_data()
        result["stable_ids"] = (
            np.array(sid_data, dtype=np.int32)
            if sid_data is not None
            else np.empty(0, dtype=np.int32)
        )

        return result

    def get_camera_data(self) -> Dict[str, Any]:
        """
        获取当前帧 Stereo Camera 数据。
        Returns: Dict 包含 left 和 right 图像字典
        """
        if self._camera_l is None or self._camera_r is None:
            return {}

        try:
            frame_l = self._camera_l.get_current_frame()
            frame_r = self._camera_r.get_current_frame()
            
            res_l = {
                "rgba": frame_l.get("rgba"),
                "depth": frame_l.get("distance_to_image_plane"),
            }
            res_r = {
                "rgba": frame_r.get("rgba"),
                "depth": frame_r.get("distance_to_image_plane"),
            }
            return {"left": res_l, "right": res_r}
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"[致命错误] 双目相机 get_current_frame() 失败，Replicator 渲染管线可能崩溃或显存溢出: {e}", exc_info=True)
            return {}

    def get_surround_camera_data(self) -> Dict[str, Any]:
        """
        获取当前帧环视鱼眼摄像头数据。
        """
        result = {}
        for name, cam in self._surround_cameras.items():
            if cam is not None:
                try:
                    frame = cam.get_current_frame()
                    result[name] = {
                        "rgba": frame.get("rgba"),
                        "depth": frame.get("distance_to_image_plane"),
                    }
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"[致命错误] 环视相机 {name} get_current_frame() 失败，Replicator 渲染管线可能异常或显存越界: {e}", exc_info=True)
        return result

    # ──────────────────────────────────────────────────────────────────────
    # 数据保存 (Data Persistence)
    # ──────────────────────────────────────────────────────────────────────
    def save_data(self, frame_idx: int, output_dir: str = "./data") -> None:
        """
        将当前帧的传感器数据持久化到磁盘。

        文件布局:
            <output_dir>/
                lidar/
                    pointcloud_{frame_idx:06d}.npy
                    flatscan_{frame_idx:06d}.npz
                camera/
                    rgba_{frame_idx:06d}.png
                    depth_{frame_idx:06d}.npy

        Args:
            frame_idx:  当前帧编号 (用于文件命名).
            output_dir: 输出根目录, 默认 ``./data``.
        """
        # ── 创建子目录 ──────────────────────────────────────────────────
        lidar_dir = os.path.join(output_dir, "lidar")
        camera_dir = os.path.join(output_dir, "camera")
        os.makedirs(lidar_dir, exist_ok=True)
        os.makedirs(camera_dir, exist_ok=True)

        tag = f"{frame_idx:06d}"

        # ── 保存 LiDAR 数据 ─────────────────────────────────────────────
        if self._lidar is not None:
            lidar_data = self.get_lidar_data()

            # 点云 (.npy)
            pc_path = os.path.join(lidar_dir, f"pointcloud_{tag}.npy")
            np.save(pc_path, lidar_data["point_cloud"])

            # 平面扫描 (.npz — 多数组打包)
            fs = lidar_data["flat_scan"]
            fs_path = os.path.join(lidar_dir, f"flatscan_{tag}.npz")
            np.savez_compressed(
                fs_path,
                distances=fs["distances"],
                angles=fs["angles"],
                intensities=fs["intensities"],
            )

        # ── 保存 Camera 数据 ────────────────────────────────────────────
        if self._camera_l is not None:
            cam_data = self.get_camera_data()

            # RGBA 图像 (.png)
            rgba_path = os.path.join(camera_dir, f"rgba_{tag}.png")
            self._save_image_as_png(cam_data["rgba"], rgba_path)

            # 深度图 (.npy)
            depth_path = os.path.join(camera_dir, f"depth_{tag}.npy")
            np.save(depth_path, cam_data["depth"])

        # ── 保存 Surround Camera 数据 ─────────────────────────────────────
        if self._surround_cameras:
            surround_dir = os.path.join(output_dir, "surround")
            os.makedirs(surround_dir, exist_ok=True)
            surr_data = self.get_surround_camera_data()
            for name, data in surr_data.items():
                if data.get("rgba") is not None:
                    rgba_path = os.path.join(surround_dir, f"{name}_rgba_{tag}.png")
                    self._save_image_as_png(data["rgba"], rgba_path)
                if data.get("depth") is not None:
                    depth_path = os.path.join(surround_dir, f"{name}_depth_{tag}.npy")
                    np.save(depth_path, data["depth"])

        print(f"[SensorSuite] 帧 {frame_idx} 数据已保存至 {output_dir}")

    # ──────────────────────────────────────────────────────────────────────
    # 内部工具 (Internal Utilities)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _save_image_as_png(rgba: np.ndarray, path: str) -> None:
        """
        将 RGBA ndarray 保存为 PNG 文件。

        优先使用 PIL/Pillow; 若不可用则回退到手工写入无压缩 BMP
        (Isaac Sim 环境通常包含 Pillow, 但防御性编程以备万一)。

        Args:
            rgba: (H, W, 4) uint8 RGBA 图像.
            path: 目标文件路径.
        """
        try:
            from PIL import Image  # type: ignore[import-untyped]

            img = Image.fromarray(rgba, mode="RGBA")
            img.save(path)
        except ImportError:
            # Pillow 不可用 — 回退: 保存为 .npy 并重命名
            npy_path = path.replace(".png", ".npy")
            np.save(npy_path, rgba)
            print(
                f"[SensorSuite] Pillow 不可用, RGBA 保存为 {npy_path} (ndarray 格式)"
            )
