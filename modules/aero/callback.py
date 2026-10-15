"""
callback.py — 气动模型登记表
=============================
I-014 气动双栈合并前的 Isaac 物理回调适配层与工厂已删除：仿真接入层的
唯一实现在 modules/aero_solver.py（AeroPhysicsCallback 与
create_aero_callback），模型层的唯一实现在本包各模型文件。本文件只保留
模型名到模型类的登记表 MODEL_REGISTRY，供工厂选择路线A/B/三维路线C模型。
"""

from __future__ import annotations

from .attached_flow import AttachedFlowModel
from .dynamic_stall import DynamicStallModel
from .sane_dickinson import SaneDickinsonModel
from .sane_dickinson_3d import SaneDickinson3DModel


MODEL_REGISTRY = {
    "attached_flow": AttachedFlowModel,
    "dynamic_stall": DynamicStallModel,
    "sane_dickinson": SaneDickinsonModel,
    "sane_dickinson_3d": SaneDickinson3DModel,
}
