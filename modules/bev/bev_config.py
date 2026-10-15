# -*- coding: utf-8 -*-
"""
BEV 拼接全局配置。

本文件中所有相机姿态、内参与畸变参数必须与
modules/sensor_setup.py 中环视鱼眼相机的实际设置严格一致，
否则 IPM 几何将无法对齐。
"""
import numpy as np

# BEV 输出分辨率与物理尺度 (BEV Output Resolution & Scales)
BEV_RESOLUTION = (1024, 1024)   # W, H
METERS_PER_PIXEL = 0.03          # 像素物理尺度 (m/pixel)
BEV_PLANE_Z = 0.0                # 水面投影参考高度

# ------------------------------------------------------------
# 环视鱼眼相机内参 (与 SurroundCameraConfig / sensor_setup.py 对齐)
# ------------------------------------------------------------
CAMERA_RESOLUTION = (1024, 1024)          # W, H
CAMERA_FOV_DEG = 190.0                   # 鱼眼最大截断视场角

# 等距投影: r = f_px * theta, 当 theta = fov/2 时 r = w/2
_MAX_THETA = np.radians(CAMERA_FOV_DEG / 2.0)
CAMERA_F_PX = (min(CAMERA_RESOLUTION) / 2.0) / _MAX_THETA
CAMERA_CX = CAMERA_RESOLUTION[0] / 2.0
CAMERA_CY = CAMERA_RESOLUTION[1] / 2.0

# OpenCV Fisheye 畸变系数，与 sensor_setup.py 的 set_opencv_fisheye_properties 完全一致
# 注意：OpenCV fisheye 模型为 theta_d = theta * (1 + k1*theta^2 + k2*theta^4 + ...)
FISHEYE_K = np.array([-0.20, 0.025, 0.0, 0.0], dtype=np.float64)

# ------------------------------------------------------------
# 相机外参（静态安装初值），对齐鱼眼相机相对 USV 重心坐标系
# ------------------------------------------------------------
# sensor_setup.py 中各相机偏航角:
#   front: -90.0, rear: 90.0, left: 0.0, right: 180.0
# 俯角（绕局部 X 轴）统一为 PITCH_DOWN
CAMERA_PITCH_DOWN = 45.0  # 度

CAMERA_EXTRINSICS = {
    "front": {"pos": np.array([2.8, 0.0, 2.5]),  "yaw": -90.0},
    "rear":  {"pos": np.array([-2.8, 0.0, 2.5]), "yaw": 90.0},
    "left":  {"pos": np.array([0.0, 1.5, 2.5]),  "yaw": 0.0},
    "right": {"pos": np.array([0.0, -1.5, 2.5]), "yaw": 180.0},
}

# ------------------------------------------------------------
# USD 轴对齐常量
# ------------------------------------------------------------
# sensor_setup.py 中 q_final = q_yaw(Rz) * q_align(Rx=90°) * q_pitch(Rx=-PITCH)
# q_align 是相机坐标系到 USD 默认朝向(−Z)的对齐旋转，恒为绕 X 轴 90°
ALIGN_AXIS_X_DEG = 90.0
