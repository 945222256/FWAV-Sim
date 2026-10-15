# -*- coding: utf-8 -*-
"""
tail_fuselage.py — 尾翼平板气动力 + 机身阻力（零 Isaac 依赖）
================================================================
本文件是 TailFuselageAero 的唯一实现地（I-014 气动双栈合并：自
modules/aero_solver.py 原文迁入，物理逐位不变）。modules/aero_solver.py
经包导入再导出，外部既有导入路径不改动。
"""

from __future__ import annotations

import numpy as np

from config import sim_config as cfg
from .common import quat_to_rotation_matrix


class TailFuselageAero:
    """尾翼控制面 + 机身阻力。

    尾翼：单平板近似。同向舵偏（elevator）改变尾面有效攻角 → 法向力 → 绕 CG
    俯仰力矩；差动（aileron）左右面反偏 → 滚转力矩；方向舵（rudder）侧滑法向力
    → 偏航力矩。机身：圆柱阻力（可开关）。
    """

    def __init__(self):
        self.elevator = 0.0     # rad，正=抬头
        self.aileron = 0.0      # rad，正=右滚
        self.rudder = 0.0       # rad，正=左偏航（尾右摆）

    def set_command(self, elevator=0.0, aileron=0.0, rudder=0.0):
        self.elevator = float(elevator)
        self.aileron = float(aileron)
        self.rudder = float(rudder)

    def compute(self, quat, vel_world, v_slip: float = 0.0):
        """返回 (F_world, T_world)，T 绕 CG。quat=[w,x,y,z]。

        v_slip：拍动滑流等效速度（2026-09-19 新增）。真机尾翼在悬停时浸没在
        拍动下洗中；实现为合流矢量叠加 v_flow = v_body + (v_slip,0,0)，动压与
        来流方向都取自合流。v_slip=0 时行为与旧版完全一致（向后兼容）。
        """
        v = np.asarray(vel_world, dtype=float)
        R = quat_to_rotation_matrix(np.asarray(quat, dtype=float))
        v_body = R.T @ v
        # 尾翼看到的合流：真实空速 + 拍动滑流（滑流沿机体 x 轴流过尾翼）。
        # 动压与来流方向必须用同一合流矢量，否则悬停爬升时机身攻角被高估
        # 数倍（2026-09-19 闭环实测：只加速率不加方向会把 13° 算成 72°）
        v_flow = v_body.copy()
        v_flow[0] += max(v_slip, 0.0)
        speed = float(np.linalg.norm(v_body))
        speed_eff = float(np.linalg.norm(v_flow))
        F_body = np.zeros(3)
        T_body = np.zeros(3)

        if speed_eff > 0.1:
            q = 0.5 * cfg.AeroModelConfig.RHO_AIR * speed_eff**2
            tc = cfg.TailAeroConfig
            a_stall = np.radians(tc.STALL_ANGLE)

            # 机身攻角/侧滑角（x 前；合流方向）
            alpha_body = np.arctan2(v_flow[2], max(v_flow[0], 0.1))
            beta_body = np.arctan2(-v_flow[1], max(v_flow[0], 0.1))
            # 第11轮对照列：尾翼实际感受的攻角与合流（控制器估计 vs 实际）
            self.last_alpha_body = float(alpha_body)
            self.last_vflow = v_flow.copy()

            # 尾面法向力（同向舵）
            a_tail = np.clip(alpha_body + self.elevator, -a_stall, a_stall)
            N_tail = q * tc.AREA * tc.CN_SLOPE * a_tail
            F_body[2] += N_tail                      # 法向力近似竖直
            T_body[1] += N_tail * tc.LEVER_ARM       # 绕 CG 俯仰

            # 差动滚转（左右面反偏，无净力）
            T_body[0] += q * tc.AREA * tc.CN_SLOPE * self.aileron * tc.PANEL_Y

            # 方向舵侧力 → 偏航
            b_tail = np.clip(beta_body + self.rudder, -a_stall, a_stall)
            N_side = q * tc.AREA * tc.CN_SLOPE * b_tail
            F_body[1] += N_side
            T_body[2] += N_side * tc.LEVER_ARM

            # 固定垂尾（2026-09-27 补全，VFIN_AERO_ON 开关，默认关）：
            # URDF 鳍片受力化。侧滑 β→侧力→偏航矩（风向标稳定），
            # 压心高 z_cp 产生滚转侧项 T_x=−z·F_y（叉积 r×F 的 x 分量，
            # r=(0,0,z_cp)）。对称面内对 Fz/Fx/My 零贡献。
            if getattr(tc, "VFIN_AERO_ON", False):
                b_fin = np.clip(beta_body,
                                -np.radians(tc.VFIN_STALL_DEG),
                                np.radians(tc.VFIN_STALL_DEG))
                F_fin = q * tc.VFIN_AREA * tc.VFIN_CN_SLOPE * b_fin
                F_body[1] += F_fin
                T_body[2] += F_fin * tc.VFIN_LEVER
                T_body[0] += -tc.VFIN_Z_CP * F_fin

            # 机身阻力（开关）：阻力只认真实空速（滑流不直接产生机身阻力，
            # 且 speed≈0 时方向未定义，需防除零）
            if cfg.ENABLE_FUSELAGE_DRAG and speed > 0.1:
                q_drag = 0.5 * cfg.AeroModelConfig.RHO_AIR * speed**2
                cd = cfg.FUSELAGE_DRAG_COEFF * cfg.FUSELAGE_FRONTAL_AREA
                F_body -= q_drag * cd * v_body / speed

        return R @ F_body, R @ T_body
