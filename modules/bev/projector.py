# -*- coding: utf-8 -*-
"""
环视鱼眼相机 IPM (逆透视变换) 投影模块。

几何与畸变模型与 modules/sensor_setup.py 中环视相机严格对齐：
  - 姿态: q_final = q_yaw(Rz) * q_align(Rx=90°) * q_pitch(Rx=-PITCH)
  - 畸变: OpenCV Fisheye 模型 theta_d = theta*(1 + k1*theta^2 + k2*theta^4 + ...)

核心反投影流程 (BEV 像素 -> 鱼眼传感器像素):
  1. BEV 像素 -> 水面世界点 P_world (X, Y, plane_z)
  2. P_world -> 相机坐标系射线方向 r_cam (单位向量)
  3. 入射角 theta = arccos(-Z_cam / |r_cam|)   (光轴沿 -Z)
  4. OpenCV fisheye 畸变: theta_d = theta*(1 + k1*theta^2 + ...)
  5. 像素坐标: u = cx + fx*theta_d*(X_cam/r_xy), v = cy + fy*theta_d*(Y_cam/r_xy)
"""
import numpy as np
import cv2

from .bev_config import (
    CAMERA_RESOLUTION, CAMERA_F_PX, CAMERA_CX, CAMERA_CY,
    FISHEYE_K, CAMERA_PITCH_DOWN, ALIGN_AXIS_X_DEG,
)


class FisheyeProjector:
    """鱼眼相机 IPM 投影器，支持 F-Theta 反求射线 + 完整 mount 旋转"""

    def __init__(self, camera_name, config_dict):
        self.name = camera_name
        self.pos = config_dict["pos"]
        self.yaw = config_dict["yaw"]                      # 度，用于打印/调试
        self.yaw_rad = np.radians(config_dict["yaw"])

        # 鱼眼物理内参 (统一从 bev_config 读取，与 sensor_setup 对齐)
        self.w, self.h = CAMERA_RESOLUTION
        self.fx = CAMERA_F_PX
        self.fy = CAMERA_F_PX
        self.cx = CAMERA_CX
        self.cy = CAMERA_CY
        self.fisheye_k = FISHEYE_K

        # 完整的相机 mount 旋转矩阵 R_mount (船体局部系 -> 相机系)
        # 对应 sensor_setup.py 的 q_yaw * q_align * q_pitch
        self.R_mount = self._build_mount_rotation()

    def _build_mount_rotation(self):
        """q_yaw(Rz) * q_align(Rx=90°) * q_pitch(Rx=-PITCH) 的等效旋转矩阵。

        严格用标准 Hamilton 四元数乘积复现 modules/sensor_setup.py 的 q_final，
        转成列向量约定旋转矩阵 (v' = R·v)，避免手算矩阵乘法顺序出错。
        """
        def axis_angle_q(deg, axis):
            a = np.radians(deg) / 2.0
            ax = np.asarray(axis, float)
            ax = ax / np.linalg.norm(ax)
            return np.array([np.cos(a), *(np.sin(a) * ax)])

        def qmul(a, b):
            aw, ax, ay, az = a
            bw, bx, by, bz = b
            return np.array([
                aw * bw - ax * bx - ay * by - az * bz,
                aw * bx + ax * bw + ay * bz - az * by,
                aw * by - ax * bz + ay * bw + az * bx,
                aw * bz + ax * by - ay * bx + az * bw,
            ])

        def q2R(q):
            qw, qx, qy, qz = q
            return np.array([
                [1 - 2 * (qy ** 2 + qz ** 2), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)],
                [2 * (qx * qy + qw * qz), 1 - 2 * (qx ** 2 + qz ** 2), 2 * (qy * qz - qw * qx)],
                [2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx ** 2 + qy ** 2)],
            ])

        q_yaw = axis_angle_q(self.yaw, (0, 0, 1))
        q_align = axis_angle_q(ALIGN_AXIS_X_DEG, (1, 0, 0))
        q_pitch = axis_angle_q(-CAMERA_PITCH_DOWN, (1, 0, 0))
        q_final = qmul(qmul(q_yaw, q_align), q_pitch)
        return q2R(q_final)

    # ----------------------------------------------------------
    # 畸变反求: 世界系射线 -> 鱼眼像素
    # ----------------------------------------------------------
    def _project_rays_to_pixels(self, rays_cam):
        """
        将相机坐标系下的射线向量 (..., 3) 投影为鱼眼像素坐标 (..., 2) 与有效掩膜。
        射线不要求归一化。光轴沿 -Z 方向。
        """
        x = rays_cam[..., 0]
        y = rays_cam[..., 1]
        z = rays_cam[..., 2]

        # 入射角 (相对光轴 -Z)，仅前方半球有效
        norm = np.sqrt(x * x + y * y + z * z)
        norm = np.where(norm == 0, 1e-9, norm)
        cos_theta = np.clip(-z / norm, -1.0, 1.0)
        theta = np.arccos(cos_theta)

        valid = z < 0.0

        # OpenCV fisheye 正向畸变: theta_d = theta*(1 + k1*theta^2 + k2*theta^4 + ...)
        k1, k2 = self.fisheye_k[0], self.fisheye_k[1]
        theta2 = theta * theta
        theta_d = theta * (1.0 + k1 * theta2 + k2 * theta2 * theta2)

        r_xy = np.sqrt(x * x + y * y)
        r_xy = np.where(r_xy == 0, 1e-9, r_xy)

        u = self.cx + self.fx * theta_d * (x / r_xy)
        v = self.cy + self.fy * theta_d * (y / r_xy)

        valid &= (u >= 0) & (u < self.w) & (v >= 0) & (v < self.h)

        pixels = np.stack([u, v], axis=-1)
        return pixels, valid

    def _bev_grid_world(self, bev_res, m_per_pixel):
        """生成 BEV 网格对应的世界平面坐标。

        约定 (标准俯视图): BEV 图像 上方=前(X+), 左侧=左(Y+), 右手系 (Z 上)。
        X_grid[i,j] 对应像素 (py=i, px=j) 处的世界 X，Y_grid 同理。
        """
        w_bev, h_bev = bev_res
        # 像素 py -> 世界 X(前): py=0(顶)对应 X 最大正值
        x_bev = (h_bev / 2.0 - np.arange(h_bev)) * m_per_pixel     # 形状 (h,)
        # 像素 px -> 世界 Y(左): px=0(左)对应 Y 最大正值
        y_bev = (w_bev / 2.0 - np.arange(w_bev)) * m_per_pixel     # 形状 (w,)
        # X_grid[i,j] 取 x_bev[i], Y_grid[i,j] 取 y_bev[j]
        X_grid = np.broadcast_to(x_bev[:, None], (h_bev, w_bev))
        Y_grid = np.broadcast_to(y_bev[None, :], (h_bev, w_bev))
        return np.ascontiguousarray(X_grid), np.ascontiguousarray(Y_grid)

    def _rays_from_world_points(self, X_grid, Y_grid, plane_z, cam_world_pos, R_cam2world):
        """给定水面世界点与相机世界位姿，返回相机坐标系下的射线向量 (h,w,3)。

        cam_world_pos: 相机在世界系下的位置 (3,)
        R_cam2world:   相机系 -> 世界系的旋转 (列向量约定)。
                       静态即 self.R_mount；动态为 R_body @ self.R_mount。
                       世界系 -> 相机系用其转置: v_cam = R_cam2world.T @ v_world。
        """
        dx = X_grid - cam_world_pos[0]
        dy = Y_grid - cam_world_pos[1]
        dz = np.full_like(X_grid, plane_z - cam_world_pos[2])
        pts = np.stack([dx, dy, dz], axis=-1)          # (h,w,3)
        # v_cam = R_cam2world^T @ v_world
        return (R_cam2world.T @ pts[..., None])[..., 0]

    def _build_maps_from_rays(self, rays_cam, bev_res):
        """从相机系射线生成 remap 用的 map_x/map_y/mask。

        各相机画面压平成 BEV 时, "上下"对应的世界方向取决于 yaw:
          - left/right (yaw=0/180): 画面绕竖轴, uv 直接映射即正确
          - front/rear (yaw=±90):   画面绕横轴, v(竖直)方向与 BEV 前后反向,
                                    需单独翻转 v, 否则前后画面"倒贴"(船头朝下)
        故按 yaw 分类决定是否翻转 v, left/right 不动。
        """
        w_bev, h_bev = bev_res
        pixels, valid = self._project_rays_to_pixels(rays_cam)
        map_x = np.zeros((h_bev, w_bev), dtype=np.float32)
        map_y = np.zeros((h_bev, w_bev), dtype=np.float32)

        # front/rear (yaw 绝对值≈90) 翻转 v; left/right (yaw=0/180) 不动
        flip_v = abs(abs(self.yaw) - 90.0) < 1.0

        map_x[valid] = pixels[..., 0][valid].astype(np.float32)
        if flip_v:
            map_y[valid] = (self.h - pixels[..., 1][valid]).astype(np.float32)
        else:
            map_y[valid] = pixels[..., 1][valid].astype(np.float32)
        return map_x, map_y, valid

    def build_static_lut(self, bev_res, m_per_pixel, plane_z):
        """构建静态反向投影查找表 (不考虑船体姿态)。

        相机固定在 self.pos，仅施加 mount 旋转 (静态时 R_body = I)。
        """
        X_grid, Y_grid = self._bev_grid_world(bev_res, m_per_pixel)
        rays_cam = self._rays_from_world_points(
            X_grid, Y_grid, plane_z, self.pos, self.R_mount
        )
        return self._build_maps_from_rays(rays_cam, bev_res)
