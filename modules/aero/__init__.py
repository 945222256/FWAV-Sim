"""
aero — 扑翼气动力学包（三流型，全部零 Isaac 依赖）
====================================================
I-014 气动双栈合并后的唯一模型层：
  kinematics      — WingKinematics 拍动运动学生成器（唯一实现）
  common          — 坐标辅助 + 叶素网格 + AeroModelBase 基类
  attached_flow   — 路线A：高Re大展弦比附着流环量（Wagner/Duhamel + AR修正）
  dynamic_stall   — 路线B：受控动态失速（LB 简化三状态：分离点/涡环量/涡脱）
  sane_dickinson  — 路线C：SaneDickinsonModel（早期版）与 FlapperAeroModel
                    （冻结生产版，sane_dickinson_3d 的基类）
  sane_dickinson_3d — 路线C 三维叶素后端
  tail_fuselage   — TailFuselageAero 尾翼平板气动力 + 机身阻力
  callback        — MODEL_REGISTRY 模型登记表

Isaac Sim 物理回调与工厂（AeroPhysicsCallback / create_aero_callback /
HoverPDController）的仿真接入层唯一实现在 modules/aero_solver.py，
该模块经本包导入模型类并对外保持全部历史名字的兼容再导出。
"""

from .common import (AeroModelBase, quat_to_rotation_matrix, quat_to_euler,
                     body_to_world, euler_to_quat)
from .kinematics import WingKinematics
from .attached_flow import AttachedFlowModel
from .dynamic_stall import DynamicStallModel
from .sane_dickinson import SaneDickinsonModel, FlapperAeroModel
from .sane_dickinson_3d import SaneDickinson3DModel
from .tail_fuselage import TailFuselageAero
from .callback import MODEL_REGISTRY

__all__ = [
    "AeroModelBase", "quat_to_rotation_matrix", "quat_to_euler",
    "body_to_world", "euler_to_quat",
    "WingKinematics",
    "AttachedFlowModel", "DynamicStallModel", "SaneDickinsonModel",
    "FlapperAeroModel", "SaneDickinson3DModel", "TailFuselageAero",
    "MODEL_REGISTRY",
]
