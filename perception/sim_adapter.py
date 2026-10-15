# -*- coding: utf-8 -*-
"""
sim_adapter.py — 感知组件的 Isaac 侧适配层
==========================================

职责（只做适配，不做算法）：
    1. D435 标定注入：把 vendor/fwaf_vid/calibr 的真实内参换算成 Isaac
       Camera 的物理参数（焦距/光圈，米制；5.1.0 的 set_focal_length 按
       米收值——见 modules/sensor_setup.py 鱼眼相机的踩坑记录）
    2. GT 投影：门圆心/半径按相机位姿 + 内参投影到像素系（评测真值）
    3. 光照预设：常光 / 弱光（论文 80% vs 60% 成功率的两档工况）
    4. 传感器退化：弱光增益噪声 + 运动模糊（渲染器 motion blur 默认关，
       采集侧合成；核宽可按 FWAF-VID 真机序列标定后收紧）

内参来源（实测值，非默认猜测）：
    vendor/fwaf_vid/calibr/D435i_camchain/d435i-camera.yaml  cam0 (infra left)
    fx=385.966  fy=386.666  cx=326.316  cy=228.948  640×480  radtan(k1≈-0.006)
    注意：这是 FWAF-VID/HIT-Hawk 平台的 D435；SparrowHawk 本机是 DJI O4
    单目（论文未公开内参）。radtan 畸变 <0.01，边缘误差 ~1px，忽略。
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import numpy as np

# ============================================================
# D435 真实标定（来源见模块 docstring）
# ============================================================

D435_INTRINSICS: Dict[str, float] = {
    "fx": 385.9663579533714,
    "fy": 386.66632586105874,
    # 主点：实测 cx=326.316/cy=228.948，但 Isaac 渲染相机的主点恒为像面
    # 中心（不可配置）。GT 投影/误差归一化必须用 (W/2, H/2) 否则与渲染
    # 帧系统性偏差 ~(-7,+11)px（gate_clean 实测亮质心标定）。与实测主点
    # 的差 <1.5%，对归一化误差的影响记录在案。
    "cx": 320.0,
    "cy": 240.0,
    "width": 640.0,
    "height": 480.0,
}

# D435 IMU（/camera/imu，vendor/fwaf_vid/calibr/camera_imu.yaml）
D435_IMU_NOISE: Dict[str, float] = {
    "accel_noise_density": 1.2116e-02,
    "accel_random_walk": 2.3267e-04,
    "gyro_noise_density": 2.1056e-03,
    "gyro_random_walk": 1.6016e-05,
    "update_rate_hz": 200.0,
}


def camera_matrix(K: Optional[Dict[str, float]] = None) -> np.ndarray:
    k = K or D435_INTRINSICS
    return np.array([[k["fx"], 0.0, k["cx"]],
                     [0.0, k["fy"], k["cy"]],
                     [0.0, 0.0, 1.0]])


# ============================================================
# 1. 内参注入 Isaac Camera
# ============================================================

def apply_d435_intrinsics(cam, K: Optional[Dict[str, float]] = None,
                          pixel_size_um: float = 100.0) -> None:
    """把像素内参换算成 Isaac Camera 物理参数（分辨率/焦距/光圈）。

    换算：f_m = fx_px·pixel_size·1e-6；aperture = n_px·pixel_size·1e-6
    （set_focal_length/set_horizontal_aperture 内部 ×10 写 USD tenths，
    两个 setter 单位一致）。绝对像素尺寸对 FOV 无影响（FOV 只由
    fx/分辨率比值决定）；取 100µm 而不是物理 3µm，是为了避开渲染管线
    对亚毫米焦距/光圈的精度地板——实测 3µm 时门成像放大 ~12 倍
    （fx_eff≈4654 vs 386，gate_dbg 数据集反推），100µm 验证正常。
    """
    k = K or D435_INTRINSICS
    w, h = int(k["width"]), int(k["height"])
    ps = pixel_size_um * 1e-6
    cam.set_resolution((w, h))
    cam.set_focal_length(k["fx"] * ps)
    cam.set_horizontal_aperture(w * ps)
    cam.set_vertical_aperture(h * ps)
    try:
        cam.set_clipping_range(0.05, 300.0)
    except Exception:
        pass


# ============================================================
# 2. GT 投影（世界系圆 → 像素系）
# ============================================================

def quat_to_rotmat(q_wxyz: np.ndarray) -> np.ndarray:
    """四元数(w,x,y,z) → 旋转矩阵（右乘列向量约定）。"""
    w, x, y, z = np.asarray(q_wxyz, dtype=float) / \
        np.linalg.norm(q_wxyz)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def project_circle_gt(center_w: np.ndarray, normal_w: np.ndarray,
                      radius_m: float,
                      cam_pos_w: np.ndarray, cam_quat_usd_wxyz: np.ndarray,
                      K: Optional[Dict[str, float]] = None,
                      n_samples: int = 64) -> Dict[str, float]:
    """门孔圆投影到像素系，返回 {u, v, r, visible}。

    相机姿态须为 USD 约定（-Z 前向，+X 右，+Y 上）——Isaac Camera
    get_world_pose(camera_axes="usd")。透视投影下圆成椭圆，u/v 取边界
    采样投影的均值、r 取到均值的平均距离（CPE 口径足够；大偏轴视角会
    略低估 r，评测时如实记录）。圆心或圆周任一点落在相机后方 → 不置
    visible。
    """
    k = K or D435_INTRINSICS
    # 圆面内正交基
    n = np.asarray(normal_w, dtype=float)
    n = n / np.linalg.norm(n)
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(n, a); e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)

    R_wc = quat_to_rotmat(cam_quat_usd_wxyz)          # cam→world 列向量
    R_cw = R_wc.T
    th = np.linspace(0.0, 2.0 * math.pi, n_samples, endpoint=False)
    pts_w = np.asarray(center_w)[None, :] + radius_m * (
        np.cos(th)[:, None] * e1[None, :] + np.sin(th)[:, None] * e2[None, :])
    pts_c = (R_cw @ (pts_w - np.asarray(cam_pos_w)[None, :]).T).T

    depth = -pts_c[:, 2]                               # USD 相机 -Z 前向
    if np.any(depth <= 1e-3):
        return {"u": float("nan"), "v": float("nan"), "r": float("nan"),
                "visible": False}
    u = k["fx"] * pts_c[:, 0] / depth + k["cx"]
    v = k["cy"] - k["fy"] * pts_c[:, 1] / depth        # +Y 上 → v 向下减
    uc, vc = float(np.mean(u)), float(np.mean(v))
    r = float(np.mean(np.hypot(u - uc, v - vc)))
    in_img = (np.min(u) >= 0 and np.min(v) >= 0
              and np.max(u) <= k["width"] and np.max(v) <= k["height"])
    return {"u": uc, "v": vc, "r": r, "visible": bool(in_img)}


# ============================================================
# 3. 光照预设
# ============================================================

LIGHT_PRESETS: Dict[str, Dict[str, float]] = {
    # 正常：降低 env_builder 默认(1000/3000)——实测 dome=1000 会把 HDRI 天空
    # 推到 210-244 灰阶（接近满幅白），白色门环(~200)对天空零灰度对比，
    # Canny(30) 边缘覆盖率仅 3%。250/1200 下天空 ~150-170、白环 ~230，
    # 对天空/草地两侧均有 50+ 灰阶对比。
    "normal":  {"dome_intensity": 250.0, "sun_intensity": 1200.0},
    # 弱光：夜间/阴地库量级。白环仍是场景最亮结构但信噪比大幅下降，
    # 配合传感器退化预设复现论文 60% 工况
    "lowlight": {"dome_intensity": 45.0, "sun_intensity": 0.0},
}


def apply_lighting_preset(stage, name: str = "normal") -> None:
    from config.sim_config import DOME_LIGHT_PATH, SUN_LIGHT_PATH
    from pxr import UsdLux
    preset = LIGHT_PRESETS.get(name)
    if preset is None:
        raise ValueError(f"未知光照预设: {name}（可选 {list(LIGHT_PRESETS)}）")
    # 注意：Create*Attr 对已存在的属性只返回不赋值，必须显式 Set 覆盖
    # （env_builder 已按默认值建过这些灯）
    dome = UsdLux.DomeLight(stage.GetPrimAtPath(DOME_LIGHT_PATH))
    if dome.GetPrim().IsValid():
        dome.CreateIntensityAttr(preset["dome_intensity"]).Set(
            preset["dome_intensity"])
    sun = UsdLux.DistantLight(stage.GetPrimAtPath(SUN_LIGHT_PATH))
    if sun.GetPrim().IsValid():
        sun.CreateIntensityAttr(preset["sun_intensity"]).Set(
            preset["sun_intensity"])


# ============================================================
# 4. 传感器退化（采集侧后处理）
# ============================================================

def degrade_frame(bgr: np.ndarray, preset: str = "normal",
                  rng: Optional[np.random.Generator] = None,
                  motion_blur_px: int = 0) -> np.ndarray:
    """传感器级退化后处理。

    normal   : 轻度传感器噪声（σ=2/255），无模糊
    lowlight : 增益受限变暗(×0.35) + 强噪声(σ=13) + 9px 水平运动模糊
               （参数与合成自测一致；待 FWAF-VID 真机序列标定后收紧）
    motion_blur_px > 0 时叠加指定宽度水平模糊（扑翼拍动方向近似水平）
    """
    rng = rng or np.random.default_rng()
    out = bgr.astype(np.float32)
    if preset == "lowlight":
        out = out * 0.35 + rng.normal(0.0, 13.0, out.shape)
        motion_blur_px = max(motion_blur_px, 9)
    elif preset == "normal":
        out = out + rng.normal(0.0, 2.0, out.shape)
    else:
        raise ValueError(f"未知退化预设: {preset}")
    if motion_blur_px >= 3:
        k = np.zeros((1, motion_blur_px), dtype=np.float32)
        k[0, :] = 1.0 / motion_blur_px
        import cv2
        out = cv2.filter2D(out, -1, k)
    return np.clip(out, 0.0, 255.0).astype(np.uint8)


# ============================================================
# 5b. 帧字典 → BGR 图像
# ============================================================

def frame_to_bgr(frame_dict) -> "np.ndarray":
    """Isaac Camera.get_current_frame() 的返回 → BGR ndarray。

    Isaac Sim 5.1.0 的 RGB 标注器键名是 "rgb"（4 通道 RGBA），
    旧文档/旧代码里的 "rgba" 键实际拿到 None（sensor_setup 存量行为，
    本函数兼容两者；3 通道按 RGB 转）。
    """
    import cv2
    if not isinstance(frame_dict, dict):
        return None
    arr = None
    for key in ("rgb", "rgba"):
        if frame_dict.get(key) is not None:
            arr = frame_dict[key]
            break
    if arr is None:
        return None
    if arr.ndim == 3 and arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if arr.ndim == 3 and arr.shape[2] == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return None


# ============================================================
# 6. 随行采集器（挂 main_sim，相机骑在扑动机构上收真退化帧）
# ============================================================

class RideAlongCollector:
    """在主仿真循环里逐渲染帧收集 (图像, GT) 对。

    用法（main_sim 挂钩，见 config.ENABLE_GATE_PERCEPTION）：
        coll = RideAlongCollector(camera, gate_circle, out_dir, preset)
        ...每渲染帧: coll.tick(t)
    """

    def __init__(self, camera, gate_circle, out_dir: str,
                 preset: str = "normal", K: Optional[Dict[str, float]] = None):
        import os
        self.cam = camera
        self.gate = gate_circle        # (center_w, normal_w, r_m)
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok)
        self.preset = preset
        self.K = K or D435_INTRINSICS
        self.gt_path = os.path.join(out_dir, "gt.jsonl")
        self._n = 0
        self._rng = np.random.default_rng()

    def tick(self, t: float) -> Optional[Dict]:
        try:
            pos, quat = self.cam.get_world_pose(camera_axes="usd")
            gt = project_circle_gt(self.gate[0], self.gate[1], self.gate[2],
                                   np.asarray(pos, dtype=float),
                                   np.asarray(quat, dtype=float), self.K)
            bgr = frame_to_bgr(self.cam.get_current_frame())
            if bgr is None:
                return None
            import cv2
            import os
            bgr = degrade_frame(bgr, self.preset, self._rng)
            name = f"frame_{self._n:06d}.png"
            import os
            cv2.imwrite(os.path.join(self.out_dir, name), bgr)
            rec = {"frame": self._n, "t": float(t),
                   "u": gt["u"], "v": gt["v"], "r": gt["r"],
                   "visible": gt["visible"],
                   "cam_pos": [float(x) for x in pos]}
            import json
            with open(self.gt_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            self._n += 1
            return rec
        except Exception:
            return None
