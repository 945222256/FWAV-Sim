"""
trajectory_ribbon.py — AR HUD 期望路径引导带
=============================================
Cubic B-spline avoidance path + horizontal ribbon mesh visualization.

为 AR HUD 场景手搓一条 3 次 B 样条避障轨迹：USV 从 (0,0) 出发向东，
从 055 驱逐舰船尾 (南侧, y<0) 绕过，形成"避障循迹但无算法驱动"的展示路径。
再把这条路径挤成一条贴水面的发光带状 mesh，复用 hologram_ghost.mdl 全息材质，
挂在 3D 场景里 → 针孔前视相机投影下天然呈现近宽远窄的 AR 效果。

无算法依赖：控制点硬编码，B 样条用 de Boor 递归（纯 numpy）。

Usage:
    from modules.trajectory_ribbon import TrajectoryRibbon, generate_avoidance_path

    path_pts = generate_avoidance_path()           # (N,3) 世界系中心线
    ribbon = TrajectoryRibbon(stage)
    ribbon.create(path_pts, color=(0.0, 0.8, 0.85))  # 天青色
"""

import numpy as np
import math
import logging

logger = logging.getLogger("trajectory_ribbon")


# ══════════════════════════════════════════════════════════════════
#  1. 手搓 3 次 B 样条避障路径生成
# ══════════════════════════════════════════════════════════════════

# ── 055 场景几何参考 ──
# 055 中心 (150, 0), RotZ=90° → 船首朝 +Y, 船尾朝 -Y, 长轴沿 Y, 缩放后约 180m
# 占据 y ∈ [-90, +90], x ∈ [150±半宽(约15m)] → 船体大致 x∈[135,165]
# "从船尾后面绕过" = 从南侧 (y<0) 切过, 绕开 y∈[-90,0] 这段船体
# 控制点手调：从 (0,0) 平直起步 → 逼近 055 前向南偏 → 贴船尾外侧最南点 → 北回收直
# 在 055 前方 (x<135) 提前下到 y≈-95，正好从船尾 y=-90 外侧掠过。
DEFAULT_CONTROL_POINTS = np.array([
    [0.0,    0.0],    # 起点 (0,0)
    [40.0,  -5.0],    # 平直起步，微南偏
    [80.0, -30.0],    # 开始向南让开
    [115.0, -75.0],   # 加速南下
    [150.0, -110.0],  # 贴 055 船尾外侧最南点 (船尾 y=-90, 留 20m 余量)
    [185.0, -110.0],  # 沿船尾外侧平移，保持南偏
    [220.0, -75.0],   # 开始北上回收
    [255.0, -35.0],   # 继续回收
    [290.0, -10.0],   # 回到中线附近
    [320.0,   0.0],   # 终点：回到 y=0，避障航段结束
], dtype=np.float64)


def _bspline_basis(i: int, p: int, u: float, knots: np.ndarray) -> float:
    """Cox-de Boor 递归求 B 样条基函数 N_{i,p}(u)。

    Parameters
    ----------
    i : 基函数索引
    p : 次数 (degree)，3 = 三次 B 样条
    u : 参数 ∈ [0,1]
    knots : 节点向量
    """
    if p == 0:
        return 1.0 if (knots[i] <= u < knots[i + 1]) else 0.0
    left = 0.0
    if knots[i + p] - knots[i] > 1e-9:
        left = (u - knots[i]) / (knots[i + p] - knots[i]) * _bspline_basis(i, p - 1, u, knots)
    right = 0.0
    if knots[i + p + 1] - knots[i + 1] > 1e-9:
        right = (knots[i + p + 1] - u) / (knots[i + p + 1] - knots[i + 1]) * _bspline_basis(i + 1, p - 1, u, knots)
    return left + right


def bspline_curve(control_points: np.ndarray, degree: int = 3,
                  n_samples: int = 120) -> np.ndarray:
    """对控制点做 B 样条曲线插值，返回采样点 (n_samples, 2)。

    使用 clamped 节点向量（两端重复 degree+1 次），使曲线穿过首末控制点。
    """
    cp = np.asarray(control_points, dtype=np.float64)
    n = len(cp)
    if n < degree + 1:
        raise ValueError(f"控制点数 {n} 不足以做 {degree} 次 B 样条 (需 ≥ {degree + 1})")

    # clamped 节点向量: [0,0,...,0, 1,2,...,  m, m,m,...,m]
    # 首尾各重复 degree+1 次
    n_internal = n - degree - 1
    knots = np.concatenate([
        np.zeros(degree + 1),
        np.arange(1, n_internal + 1) if n_internal > 0 else np.array([]),
        np.full(degree + 1, n_internal + 1),
    ])

    us = np.linspace(0, knots[-1] - 1e-6, n_samples)
    curve = np.zeros((n_samples, 2), dtype=np.float64)
    for idx, u in enumerate(us):
        # 只对有影响的控制点求和 (区间内的 degree+1 个)
        # 简化：全部求和，基函数自然为 0
        for i in range(n):
            basis = _bspline_basis(i, degree, u, knots)
            curve[idx] += basis * cp[i]
    return curve


def generate_avoidance_path(
    control_points: np.ndarray = None,
    z_height: float = 0.3,
    n_samples: int = 120,
) -> np.ndarray:
    """生成手搓 B 样条避障路径中心线，返回 (N, 3) 世界系点列。

    Parameters
    ----------
    control_points : (M,2) 控制点 xy，默认用 DEFAULT_CONTROL_POINTS
    z_height : 贴水面高度 (略高于 0 避免与水面 z-fight)
    n_samples : 采样点数
    """
    if control_points is None:
        control_points = DEFAULT_CONTROL_POINTS
    curve2d = bspline_curve(control_points, degree=3, n_samples=n_samples)
    path = np.zeros((n_samples, 3), dtype=np.float64)
    path[:, 0] = curve2d[:, 0]
    path[:, 1] = curve2d[:, 1]
    path[:, 2] = z_height
    logger.info(
        "避障路径生成: %d 采样点, 起点(%.0f,%.0f) → 终点(%.0f,%.0f), 最南 y=%.0f (绕055船尾)",
        n_samples, path[0, 0], path[0, 1], path[-1, 0], path[-1, 1], path[:, 1].min()
    )
    return path


# ══════════════════════════════════════════════════════════════════
#  2. 沿中心线挤出水平带状 mesh (ribbon geometry)
# ══════════════════════════════════════════════════════════════════

def _ribbon_geometry(centerline: np.ndarray, width: float = 3.0):
    """
    沿中心线两侧偏移 ±width/2，挤出一条水平带状 mesh。

    几何：每段中心线 [P_i, P_{i+1}]，沿其法向（在 XY 平面内垂直）偏移出
    左右两条边，构成一个四边形 (quad)。N 段 → N 个 quad，首尾相连成带。

    Returns: (verts, indices, counts, normals, extent) 五元组
             范式与 trajectory_viz._boat_silhouette_geo 完全一致。
    """
    from pxr import Gf, Vt

    pts = np.asarray(centerline, dtype=np.float64)[:, :2]  # 只取 xy
    n = len(pts)
    half_w = width / 2.0

    # 每个采样点的切向 → 法向 (XY 平面内逆时针 90°)
    # 端点用单侧差分，中间用中心差分
    tangents = np.zeros_like(pts)
    tangents[0] = pts[1] - pts[0]
    tangents[-1] = pts[-1] - pts[-2]
    tangents[1:-1] = pts[2:] - pts[:-2]
    # 归一化
    norms = np.linalg.norm(tangents, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    tangents /= norms
    # 法向 = (-ty, tx) (逆时针 90°, 即切向左侧)
    normals2d = np.stack([-tangents[:, 1], tangents[:, 0]], axis=1)

    # 顶点: 每个采样点出 2 个 (左缘, 右缘), 共 2n 个
    left_edge = pts + normals2d * half_w
    right_edge = pts - normals2d * half_w

    z = float(centerline[0, 2]) if centerline.shape[1] >= 3 else 0.0
    verts_list = []
    for i in range(n):
        verts_list.append(Gf.Vec3f(float(left_edge[i, 0]), float(left_edge[i, 1]), z))
        verts_list.append(Gf.Vec3f(float(right_edge[i, 0]), float(right_edge[i, 1]), z))
    verts = Vt.Vec3fArray(verts_list)

    # 面: 每段一个 quad (2 个三角形), 共 (n-1) 段
    indices_list = []
    counts_list = []
    for i in range(n - 1):
        # 顶点索引: 左_i=2i, 右_i=2i+1, 左_{i+1}=2(i+1), 右_{i+1}=2(i+1)+1
        li, ri = 2 * i, 2 * i + 1
        lj, rj = 2 * (i + 1), 2 * (i + 1) + 1
        # 三角 1: li, lj, rj  (注意缠绕顺序：从上方看逆时针 = 正面朝上)
        indices_list.extend([li, lj, rj])
        counts_list.append(3)
        # 三角 2: li, rj, ri
        indices_list.extend([li, rj, ri])
        counts_list.append(3)

    # 法线: 全部朝上 (0,0,1) — 带 mesh 是水平的，法线朝上最直观
    # (hologram_ghost.mdl 内部把法线清零，所以这里给什么不影响渲染)
    normals_list = [Gf.Vec3f(0, 0, 1) for _ in range(2 * n)]
    normals = Vt.Vec3fArray(normals_list)

    # extent
    all_xy = np.vstack([left_edge, right_edge])
    extent = Vt.Vec3fArray([
        Gf.Vec3f(float(all_xy[:, 0].min()), float(all_xy[:, 1].min()), z - 0.1),
        Gf.Vec3f(float(all_xy[:, 0].max()), float(all_xy[:, 1].max()), z + 0.1),
    ])

    return (
        verts,
        Vt.IntArray(indices_list),
        Vt.IntArray(counts_list),
        normals,
        extent,
    )


# ══════════════════════════════════════════════════════════════════
#  3. TrajectoryRibbon 可视化器
# ══════════════════════════════════════════════════════════════════

class TrajectoryRibbon:
    """
    连续引导带可视化器。一条贴水面的发光带状 mesh，复用 HologramGhost.mdl。
    挂在 3D 场景世界系 → 针孔相机投影下天然近宽远窄。
    """

    ROOT_PATH = "/World/DesiredPathRibbon"
    MESH_PATH = f"{ROOT_PATH}/RibbonMesh"
    MAT_PATH = "/World/Looks/PathRibbonMat"

    def __init__(self, stage):
        self.stage = stage
        self._width = 3.0
        self._z_height = 0.3

    # ────────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────────

    def create(
        self,
        path_points: np.ndarray,
        width: float = 3.0,
        color: tuple = (0.0, 0.8, 0.85),   # 天青色
        emissive_intensity: float = 1.5,
        opacity: float = 0.55,
        z_height: float = 0.3,
    ):
        """
        创建引导带。

        Parameters
        ----------
        path_points : ndarray (N, 3)
            世界系中心线点列 [x, y, z]。
        width : 带宽 (m)。
        color : RGB emissive 颜色 (0~1)。
        """
        from pxr import UsdGeom, UsdShade, Gf
        from isaacsim.core.utils.prims import define_prim

        self._width = width
        self._z_height = z_height

        # 1. 根 Xform
        if not self.stage.GetPrimAtPath(self.ROOT_PATH).IsValid():
            define_prim(self.ROOT_PATH, "Xform")

        # 2. 材质 (复用 hologram_ghost.mdl，独立 Material prim 避免与 GhostTrail 冲突)
        self._create_material(color, emissive_intensity, opacity)

        # 3. 构建 ribbon mesh
        geo = _ribbon_geometry(path_points, width=width)
        verts, indices, counts, normals, extent = geo

        mesh = UsdGeom.Mesh.Define(self.stage, self.MESH_PATH)
        mesh.GetPointsAttr().Set(verts)
        mesh.GetFaceVertexIndicesAttr().Set(indices)
        mesh.GetFaceVertexCountsAttr().Set(counts)
        mesh.GetNormalsAttr().Set(normals)
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        mesh.GetDoubleSidedAttr().Set(True)
        mesh.GetSubdivisionSchemeAttr().Set("none")
        mesh.CreateExtentAttr().Set(extent)
        mesh.GetDisplayColorAttr().Set([
            Gf.Vec3f(float(color[0]), float(color[1]), float(color[2]))
        ])

        # 4. 绑定材质
        mat = UsdShade.Material.Get(self.stage, self.MAT_PATH)
        if mat:
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)

        logger.info(
            "引导带已创建: %d 段, 宽 %.1fm, 颜色(%.2f,%.2f,%.2f), 路径 %s",
            len(path_points) - 1, width,
            color[0], color[1], color[2], self.MESH_PATH,
        )

    def update(self, path_points: np.ndarray, width: float = None):
        """运行期更新引导带顶点（为后续接实时轨迹预留）。"""
        from pxr import UsdGeom, Gf

        prim = self.stage.GetPrimAtPath(self.MESH_PATH)
        if not prim.IsValid():
            logger.warning("引导带 mesh 不存在，无法更新")
            return
        w = width if width is not None else self._width
        geo = _ribbon_geometry(path_points, width=w)
        verts, indices, counts, normals, extent = geo
        mesh = UsdGeom.Mesh(prim)
        mesh.GetPointsAttr().Set(verts)
        mesh.GetFaceVertexIndicesAttr().Set(indices)
        mesh.GetFaceVertexCountsAttr().Set(counts)
        mesh.CreateExtentAttr().Set(extent)

    def set_visible(self, visible: bool):
        """显示 / 隐藏引导带。"""
        from pxr import UsdGeom
        prim = self.stage.GetPrimAtPath(self.ROOT_PATH)
        if prim and prim.IsValid():
            img = UsdGeom.Imageable(prim)
            if visible:
                img.MakeVisible()
            else:
                img.MakeInvisible()

    # ────────────────────────────────────────────────────────
    #  Internal — 材质创建 (范式照搬 trajectory_viz._create_ghost_material)
    # ────────────────────────────────────────────────────────

    def _create_material(self, color, intensity, opacity):
        """复用 hologram_ghost.mdl，独立 Material prim。"""
        from pxr import UsdShade, Sdf, Gf

        mat = UsdShade.Material.Define(self.stage, self.MAT_PATH)
        shader_path = f"{self.MAT_PATH}/Shader"
        shader = UsdShade.Shader.Define(self.stage, shader_path)

        shader.SetSourceAsset("hologram_ghost.mdl", "mdl")
        shader.SetSourceAssetSubIdentifier("HologramGhost", "mdl")

        shader.CreateInput(
            "emissive_color", Sdf.ValueTypeNames.Color3f,
        ).Set(Gf.Vec3f(float(color[0]), float(color[1]), float(color[2])))

        shader.CreateInput(
            "emissive_intensity", Sdf.ValueTypeNames.Float,
        ).Set(float(intensity))

        shader.CreateInput(
            "opacity", Sdf.ValueTypeNames.Float,
        ).Set(float(opacity))

        mat.CreateSurfaceOutput("mdl").ConnectToSource(
            shader.ConnectableAPI(), "out",
        )
        mat.CreateSurfaceOutput().ConnectToSource(
            shader.ConnectableAPI(), "out",
        )

        logger.info("引导带材质 (hologram_ghost.mdl): 强度=%.1f, 不透明度=%.2f",
                    intensity, opacity)
        return mat
