# -*- coding: utf-8 -*-
"""
多相机 BEV 融合器。

采用拉普拉斯多频段金字塔 (Laplacian Pyramid) 融合：在低频层做基于
距离变换的加权平均保证整体亮度连续，在高频层仅保留各相机内部细节，
从而在重合区域消除可见接缝与亮度阶跃，远优于单一线性 alpha 融合。
"""
import numpy as np
import cv2


class LaplacianPyramidBlender:
    """基于拉普拉斯金字塔的多相机 BEV 缝合融合器"""

    def __init__(self, levels=4):
        # 金字塔层数需保证 <= log2(min(H,W))，BEV 1024 时 4 层安全裕度充足
        self.levels = levels

    def blend(self, images_dict, masks_dict, bev_res):
        w_bev, h_bev = bev_res
        names = [n for n in images_dict if np.any(masks_dict.get(n, False))]
        if not names:
            return np.zeros((h_bev, w_bev, 3), dtype=np.uint8)
        if len(names) == 1:
            # 只有一个有效相机，无需融合
            img = images_dict[names[0]]
            m = masks_dict[names[0]]
            out = np.zeros((h_bev, w_bev, 3), dtype=np.uint8)
            out[m] = img[m]
            return out

        # ---- 1. 每相机权重图: 距离变换 (越靠 mask 中心越大) ----
        weights = {}
        for name in names:
            mask = masks_dict[name].astype(np.uint8)
            dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
            cv2.normalize(dist, dist, 0.0, 1.0, cv2.NORM_MINMAX)
            weights[name] = dist.astype(np.float32)

        # ---- 2. 构建图像与权重的拉普拉斯/高斯金字塔 ----
        L_imgs = {name: self._laplacian_pyramid(
                      self._to_float(images_dict[name]) * masks_dict[name][..., None],
                      self.levels)
                  for name in names}
        G_weights = {name: self._gaussian_pyramid(
                         np.atleast_3d(weights[name].astype(np.float32)), self.levels)
                     for name in names}

        # ---- 3. 逐层加权融合 ----
        blended_levels = []
        for lvl in range(self.levels + 1):
            acc_img = np.zeros_like(L_imgs[names[0]][lvl])
            acc_w = np.zeros(acc_img.shape[:2] + (1,), dtype=np.float32)
            for name in names:
                w = np.atleast_3d(G_weights[name][lvl])
                acc_img += L_imgs[name][lvl] * w
                acc_w += w
            acc_w = np.where(acc_w < 1e-6, 1e-6, acc_w)
            blended_levels.append(acc_img / acc_w)

        # ---- 4. 由金字塔重建 ----
        result = self._reconstruct(blended_levels)
        return np.clip(result, 0, 255).astype(np.uint8)

    # ---------------- 金字塔工具 ----------------
    @staticmethod
    def _to_float(img):
        return img.astype(np.float32)

    @staticmethod
    def _gaussian_pyramid(img, levels):
        gp = [img]
        for _ in range(levels):
            img = cv2.pyrDown(img)
            gp.append(img)
        return gp

    @classmethod
    def _laplacian_pyramid(cls, img, levels):
        gp = cls._gaussian_pyramid(img, levels)
        lp = []
        for i in range(levels):
            size = (gp[i].shape[1], gp[i].shape[0])
            upsampled = cv2.pyrUp(gp[i + 1], dstsize=size)
            lp.append(gp[i] - upsampled)
        lp.append(gp[levels])  # 最顶层保留低频残差
        return lp

    @classmethod
    def _reconstruct(cls, laplacian_levels):
        img = laplacian_levels[-1]
        for lvl in reversed(range(len(laplacian_levels) - 1)):
            size = (laplacian_levels[lvl].shape[1], laplacian_levels[lvl].shape[0])
            img = cv2.pyrUp(img, dstsize=size)
            img = img + laplacian_levels[lvl]
        return img
