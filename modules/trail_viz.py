# -*- coding: utf-8 -*-
"""
trail_viz.py — 循迹可视化：期望轨迹 + 实际尾迹（细线，最小开销）
=================================================================

刻意不用 USV 遗留的 trajectory_ribbon（AR 全息发光带）/ trajectory_viz
（船壳全息走廊资产）——两者都是 AR 展示件，材质装饰过度且语义为船。这里用 USD 原生
折线图元 UsdGeom.BasisCurves(type=linear)：两条细线（期望=青绿、实际=橙），
displayColor 上色，无材质特效、无碰撞、不参与物理。

开销：push 是 list append；flush 每 30 物理步写一次 points 属性（≤1500
点 ×3 float），渲染成本可忽略。控制管线完全不受影响（不新增 UDP、不修改
tracker 内部，pos_cmd 在 main_sim 接收处旁路复制一份）。

用法（main_sim）：
    _TRAIL = TrailViz(stage)                     # reset 后建
    _TRAIL.push_ref(pos)                         # _recv_commands 收到 pos_cmd 时
    _TRAIL.push_actual(p); _TRAIL.flush()        # 主循环每 30 步
    _TRAIL.set_path(points)                      # （预留）bishop 发整条轨迹时一次性覆盖
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

import numpy as np
from pxr import Gf, UsdGeom


class _Line:
    """一条 3D 轨迹条带（竖直双面 Mesh ribbon）。

    图元选型两次实测（第15轮渲染探针）：①BasisCurves+displayColor →
    RTX 整帧无此曲线（目标色像素=0）；②BasisCurves+UsdPreviewSurface
    材质 → 仍为 0。结论：Isaac 5.1 RTX 实时渲染器不渲染 linear
    basis curves 图元。改用 Mesh ribbon（每个轨迹点上下偏移 ±w 生成
    竖直条带，双面索引），Mesh 渲染 100% 受支持。USD 属性冒烟 ≠
    渲染可见，此坑存档。
    """

    def __init__(self, stage, prim_path: str,
                 color: Sequence[float], width: float = 0.08,
                 max_points: int = 1500):
        self.mesh = UsdGeom.Mesh.Define(stage, prim_path)
        self.width = float(width)
        self.pts: list = []
        self.max_points = max_points
        # 上色用 displayColor——渲染探针三次实测确定（第15轮）：
        # BasisCurves（含绑材质）与 Mesh+UsdPreviewSurface 材质在 RTX
        # 实时下均渲染为空；displayColor 对照球可见。故 Mesh+displayColor。
        self.mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])

    def push(self, p: Sequence[float]) -> None:
        if p is None:
            return
        self.pts.append((float(p[0]), float(p[1]), float(p[2])))
        if len(self.pts) > self.max_points:
            del self.pts[0]

    def set_path(self, points: Iterable[Sequence[float]]) -> None:
        self.pts = [(float(a), float(b), float(c)) for a, b, c in points]

    def clear(self) -> None:
        """清空并隐藏条带（Mesh 版用 visibility，不留旧几何）。"""
        self.pts.clear()
        self.mesh.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)

    def flush(self) -> None:
        """把当前点集生成为竖直双面条带 Mesh（<2 点不写，保持不可见）。"""
        n = len(self.pts)
        if n < 2:
            return
        self.mesh.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
        w = self.width
        verts = []
        for (x, y, z) in self.pts:
            verts.append(Gf.Vec3f(x, y, z - w))
            verts.append(Gf.Vec3f(x, y, z + w))
        # 段法线 = 段切线 × 竖直方向(Z)，水平归一；逐点取相邻段（末点沿末段）
        seg_n = []
        for i in range(n - 1):
            tx = self.pts[i + 1][0] - self.pts[i][0]
            ty = self.pts[i + 1][1] - self.pts[i][1]
            ln = max((tx * tx + ty * ty) ** 0.5, 1e-9)
            seg_n.append((-ty / ln, tx / ln))
        idx = []
        for i in range(n - 1):
            a, b = 2 * i, 2 * i + 1
            c, d = 2 * i + 2, 2 * i + 3
            idx += [a, c, b,  b, c, d,   # 正面
                    a, b, c,  b, d, c]   # 反面（双面可见）
        # 逐顶点法线：每点上下两顶点共用同一水平法线 → 数量=2n 与 points 一致
        norms = []
        for i in range(n):
            nx, ny = seg_n[min(i, n - 2)]
            norms += [Gf.Vec3f(nx, ny, 0.0)] * 2
        # 拓扑修复（kit_20260920_134307 日志实锤）：面数=索引数/3。
        # 旧代码 [3]*len(idx) 把面数虚增 3 倍，Hydra 报
        # "numVerts and verts are inconsistent ... Geometry will be ignored"，
        # 两条 mesh 整体被渲染器丢弃 → 轨迹/尾迹不可见。
        self.mesh.CreateFaceVertexCountsAttr([3] * (len(idx) // 3))
        self.mesh.CreateFaceVertexIndicesAttr(idx)
        self.mesh.CreatePointsAttr(verts)
        self.mesh.CreateNormalsAttr(norms)
        self.mesh.CreateDoubleSidedAttr(True)
        # extent（RTX 视锥剔除必需：无 extent 的 Mesh 被整体剔除——
        # 第15轮探针实测 displayColor Mesh 缺 extent/normals 时不可见）
        xs_ = [p[0] for p in self.pts]; ys_ = [p[1] for p in self.pts]
        zs_ = [p[2] for p in self.pts]
        self.mesh.CreateExtentAttr([Gf.Vec3f(min(xs_), min(ys_), min(zs_) - w),
                                    Gf.Vec3f(max(xs_), max(ys_), max(zs_) + w)])


class TrailViz:
    """期望轨迹（ref）+ 实际尾迹（actual）一对细线。"""

    ROOT = "/World/Viz"

    def __init__(self, stage,
                 desired_color=(0.1, 0.9, 0.3),     # 青绿：期望
                 actual_color=(1.0, 0.55, 0.1),     # 橙：实际
                 width: float = 0.08):
        from pxr import UsdGeom as _UG
        if not stage.GetPrimAtPath(self.ROOT).IsValid():
            _UG.Xform.Define(stage, self.ROOT)
        self.desired = _Line(stage, f"{self.ROOT}/DesiredPath",
                             desired_color, width)
        self.actual = _Line(stage, f"{self.ROOT}/ActualTrail",
                            actual_color, width)

    # 语义化快捷方法 -----------------------------------------------
    def push_ref(self, pos: Optional[Sequence[float]]) -> None:
        self.desired.push(pos)

    def push_actual(self, pos: Optional[Sequence[float]]) -> None:
        self.actual.push(pos)

    def set_path(self, points: Iterable[Sequence[float]]) -> None:
        """整条覆盖期望轨迹（bishop 若发完整 path 消息，用这个）。"""
        self.desired.set_path(points)

    def flush(self) -> None:
        self.desired.flush()
        self.actual.flush()

    def clear_desired(self) -> None:
        """清掉期望线（真实数据接管前移除演示轨迹用）。"""
        self.desired.clear()


def demo_cruise_path(radius: float = 5.0, z: float = 1.5, n: int = 96,
                     center=(0.0, 0.0)) -> list:
    """本地演示巡航轨迹：水平圆（课题组 CJA2022 圆轨迹跟踪的航线型）。

    无外部 bishop 桥时先画这条让期望轨迹可见；真实 pos_cmd/path 到达后
    由 main_sim 自动 clear_desired() 替换。系留台架下机体够不到全程，
    仅作可视化占位。
    """
    import math
    cx, cy = center
    return [(cx + radius * math.cos(2 * math.pi * i / n),
             cy + radius * math.sin(2 * math.pi * i / n),
             z) for i in range(n + 1)]
