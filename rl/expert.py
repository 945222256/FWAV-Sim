# -*- coding: utf-8 -*-
"""
expert.py — 悬停环境专家控制器（PD，供行为克隆蒸馏）
====================================================
高度通道：PD 于拍频（升力 ∝ f²，辨识斜率 1.95 N/Hz²）
滚转通道：PD 于翼差动 dA（尾舵在静气悬停下无效，见台架辨识报告）
"""
import numpy as np

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl.hover_env import Z_REF, FREQ_MID, FREQ_HALF, DA_MAX


class HoverExpert:
    """obs=[z, vz, roll, wx, freq] → act=[freq_n, dA_n]∈[-1,1]²（周期平均 PD）。

    周期平均动力学下波纹已被平均掉，专家无需滤波器；增益按
    L̄=1.95·f² 的辨识斜率与 dA→Tx ≈ 25 N·m/rad 的实测增益整定。
    """

    def __init__(self, kp_z=0.9, kd_z=0.6, kp_r=0.06, kd_r=0.012):
        self.kp_z, self.kd_z = kp_z, kd_z
        self.kp_r, self.kd_r = kp_r, kd_r

    def act(self, obs):
        z, vz, roll, wx = [float(x) for x in np.asarray(obs)[:4]]
        freq = 2.0 + self.kp_z * (Z_REF - z) - self.kd_z * vz
        freq = float(np.clip(freq, FREQ_MID - FREQ_HALF, FREQ_MID + FREQ_HALF))
        # dA>0 → 左翼升力大 → T_x>0 → roll 正向加速（实测 25 N·m/rad），
        # 负反馈取负号
        dA = -(self.kp_r * roll + self.kd_r * wx)
        dA = float(np.clip(dA, -DA_MAX, DA_MAX))
        return np.array([(freq - FREQ_MID) / FREQ_HALF, dA / DA_MAX],
                        dtype=np.float32)
