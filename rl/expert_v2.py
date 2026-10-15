# -*- coding: utf-8 -*-
"""
expert_v2.py — V2 悬停环境专家控制器（力矩分配版，依据台架真值表取值）
====================================================================
与 V1 专家的区别：滚转通道不再用按"瞬时力地图粗测 25 N·m/rad"整定的
固定增益，而是按台架真值表 B_dA(f) ≈ 5.2·f² N·m/rad（docs/IDENT_20260919
第一节，静气真值，悬停点 19.0）做"期望力矩 → 翼差动"的分配，
增益随拍频自动调度——这是控制分配的经典形式，与主线飞控结构相同。
高度通道保持 PD → 拍频不变。
"""
import numpy as np

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl.hover_env_v2 import Z_REF, FREQ_MID, FREQ_HALF, DA_MAX


def B_dA(f):
    """翼差动舵效真值表（静气）：B_dA(f) ≈ 5.2·f² N·m/rad。"""
    return 5.2 * f * f


class HoverExpertV2:
    """obs=[z, vz, roll, wx, freq_actual, sinφ, cosφ] → act=[freq_n, dA_n]。"""

    def __init__(self, kp_z=0.9, kd_z=0.6, kp_t=0.15, kd_t=0.01):
        self.kp_z, self.kd_z = kp_z, kd_z
        self.kp_t, self.kd_t = kp_t, kd_t   # 期望力矩增益 (N·m/rad, N·m·s)

    def act(self, obs):
        o = np.asarray(obs, dtype=float)
        z, vz, roll, wx = o[:4]
        f_act = float(o[4]) if len(o) > 4 else 1.9
        freq = 2.0 + self.kp_z * (Z_REF - z) - self.kd_z * vz
        freq = float(np.clip(freq, FREQ_MID - FREQ_HALF, FREQ_MID + FREQ_HALF))
        # 期望力矩 → dA（dA>0 → Tx>0 → roll 正向加速，负反馈取负号）；
        # 被控对象固有 L_p≈0.60 强阻尼（τ≈25ms），kd_t 只需很小
        T_des = -(self.kp_t * roll + self.kd_t * wx)
        dA = float(np.clip(T_des / B_dA(f_act), -DA_MAX, DA_MAX))
        return np.array([(freq - FREQ_MID) / FREQ_HALF, dA / DA_MAX],
                        dtype=np.float32)
