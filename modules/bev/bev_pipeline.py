# -*- coding: utf-8 -*-
import cv2
import numpy as np
from .bev_config import BEV_RESOLUTION, METERS_PER_PIXEL, BEV_PLANE_Z, CAMERA_EXTRINSICS
from .projector import FisheyeProjector
from .stabilizer import GroundTruthStabilizer
from .blender import LaplacianPyramidBlender

class BEVStitcher:
    """环视鱼眼 BEV 拼接流水线"""

    def __init__(self, mode="GROUND_TRUTH"):
        self.mode = mode
        self.projectors = {
            name: FisheyeProjector(name, cfg) for name, cfg in CAMERA_EXTRINSICS.items()
        }
        self.stabilizer = GroundTruthStabilizer(mode=mode)
        self.blender = LaplacianPyramidBlender()

    def update_frames(self, frames_dict, usv_pose):
        """
        每帧更新图像拼接
        frames_dict: {"front": np_array, "rear": np_array, ...}
        usv_pose: [x, y, z, qw, qx, qy, qz]
        """
        projected_imgs = {}
        projected_masks = {}
        
        for name, img in frames_dict.items():
            if name not in self.projectors:
                continue
            # 计算投影查找表
            map_x, map_y, mask = self.stabilizer.get_stabilized_lut(
                self.projectors[name], BEV_RESOLUTION, METERS_PER_PIXEL, BEV_PLANE_Z, usv_pose
            )
            # 极速重映射
            proj_img = cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR)
            
            projected_imgs[name] = proj_img
            projected_masks[name] = mask
            
        # 混合
        bev_stitched = self.blender.blend(projected_imgs, projected_masks, BEV_RESOLUTION)
        return bev_stitched
