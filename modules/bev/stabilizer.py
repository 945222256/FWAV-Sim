# -*- coding: utf-8 -*-
import numpy as np

class GroundTruthStabilizer:
    """基于位姿真值的动态稳定补偿器"""

    def __init__(self, mode="GROUND_TRUTH"):
        self.mode = mode

    def get_stabilized_lut(self, projector, bev_res, m_per_pixel, plane_z, usv_pose):
        """
        根据每一帧 USV 绝对位姿姿态计算动态 IPM 查找表。
        usv_pose: [x, y, z, qw, qx, qy, qz]

        几何说明 (列向量约定 v'=R·v，与 projector.R_mount 一致):
          - R_body:   船体局部系 -> 世界系 (由四元数恢复)
          - R_mount:  船体局部系 -> 相机系 (固定安装姿态)
          - 相机在世界系的绝对旋转: R_cam_world = R_mount @ R_body^T
            (先撤掉船体朝向回世界系，再施加 mount)
          - 相机世界位置: cam_world_pos = usv_pos + R_body @ mount_pos
        投影逻辑复用 projector 内部方法，保证与静态 LUT 完全同源。
        """
        if self.mode == "STATIC" or usv_pose is None:
            return projector.build_static_lut(bev_res, m_per_pixel, plane_z)

        # 船体姿态四元数 -> 世界系旋转矩阵 R_body (列向量约定)
        qw, qx, qy, qz = usv_pose[3:7]
        R_body = np.array([
            [1.0 - 2.0 * (qy ** 2 + qz ** 2), 2.0 * (qx * qy - qw * qz), 2.0 * (qx * qz + qw * qy)],
            [2.0 * (qx * qy + qw * qz), 1.0 - 2.0 * (qx ** 2 + qz ** 2), 2.0 * (qy * qz - qw * qx)],
            [2.0 * (qx * qz - qw * qy), 2.0 * (qy * qz + qw * qx), 1.0 - 2.0 * (qx ** 2 + qy ** 2)],
        ])

        # 相机在世界系下的绝对位置与绝对朝向
        usv_world_pos = usv_pose[0:3]
        cam_world_pos = usv_world_pos + R_body @ projector.pos
        # R_mount 是 cam->body 旋转，叠加船体姿态得 cam->world (cam2world)
        # 世界点 -> 相机系 由 _rays_from_world_points 内部用 R_cam2world.T 完成
        R_cam2world = R_body @ projector.R_mount

        X_grid, Y_grid = projector._bev_grid_world(bev_res, m_per_pixel)
        # 运动补偿 (groundtruth): 用船体 yaw 旋转 BEV 网格, 让图的"上"始终对准船头。
        # 仅转 yaw 不转 roll/pitch, 实现"电子稳定"——波浪里图不晃(车载环视标准做法)。
        yaw = np.arctan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy ** 2 + qz ** 2))
        c, s = np.cos(yaw), np.sin(yaw)
        X_grid = c * X_grid - s * Y_grid
        Y_grid = s * X_grid + c * Y_grid
        # 平移到船体当前位置 (车体中心 BEV): 旋转后的网格加上船的世界位置,
        # 使 BEV 中心恒对准 usv_pos。船开动后网格跟随, 不会偏到角落。
        X_grid = X_grid + usv_world_pos[0]
        Y_grid = Y_grid + usv_world_pos[1]
        rays_cam = projector._rays_from_world_points(
            X_grid, Y_grid, plane_z, cam_world_pos, R_cam2world
        )
        return projector._build_maps_from_rays(rays_cam, bev_res)
