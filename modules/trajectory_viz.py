"""
trajectory_viz.py — 船体虚影走廊轨迹可视化
Ghost-Trail Trajectory Visualization — boat silhouettes at future poses.

Renders semi-transparent boat-shaped silhouettes at desired future poses,
forming a sci-fi "ghost corridor" showing position AND heading.

Implementation: 30 individual Xform+Mesh prims with one shared material.
(PointInstancer had rendering issues with Isaac Sim's RTX Real-Time renderer.)

Geometry: 3D extruded pentagonal prism (top + bottom + side walls).
This gives the Fresnel shader curved side surfaces → edge glow effect.

Usage:
    from modules.trajectory_viz import TrajectoryVisualizer, generate_circle_poses

    viz = TrajectoryVisualizer(stage)
    poses, z = generate_circle_poses(radius=60, n_poses=30)
    viz.create(poses, z_height=z)

    viz.update(new_poses)   # dynamic trajectory from MPC / planner
"""

import numpy as np
import math
import logging

logger = logging.getLogger("trajectory_viz")


# ══════════════════════════════════════════════════════════════════
#  Pose generators
# ══════════════════════════════════════════════════════════════════

def generate_circle_poses(
    radius: float,
    n_poses: int = 30,
    z_height: float = 0.3,
    center: tuple = (0.0, 0.0),
) -> tuple:
    """
    Generate evenly-spaced (x, y, ψ) poses along a circle.
    Returns (poses: ndarray (N,3), z_height: float).
    """
    angles = np.linspace(0, 2 * np.pi, n_poses, endpoint=False)
    poses = np.zeros((n_poses, 3), dtype=np.float64)
    poses[:, 0] = center[0] + radius * np.cos(angles)
    poses[:, 1] = center[1] + radius * np.sin(angles)
    poses[:, 2] = angles + np.pi / 2  # tangent heading
    return poses, z_height


def generate_circle_points(radius, n_points=128, z_height=0.5, center=(0.0, 0.0)):
    """Legacy API — returns (N,3) XYZ points."""
    t = np.linspace(0, 2 * np.pi, n_points, endpoint=False)
    pts = np.zeros((n_points, 3), dtype=np.float64)
    pts[:, 0] = center[0] + radius * np.cos(t)
    pts[:, 1] = center[1] + radius * np.sin(t)
    pts[:, 2] = z_height
    return pts


# ══════════════════════════════════════════════════════════════════
#  3D extruded boat silhouette geometry
# ══════════════════════════════════════════════════════════════════

def _boat_silhouette_geo(length: float, beam: float, thickness: float = 0.25):
    """
    Build a 3D extruded pentagonal boat silhouette (prism).

    Top cap (Z = +thickness/2), bottom cap (Z = -thickness/2),
    and 5 side-wall quads connecting them.

    The side walls have outward-facing normals → Fresnel works on these.
    Top/bottom caps have Z-facing normals → Fresnel makes them transparent
    when viewed from above → exactly the holographic effect we want.

    Returns (verts, face_indices, face_counts, normals, extent)
    """
    from pxr import Gf, Vt

    hL = length / 2.0
    hW = beam / 2.0
    sx = hL * 0.45  # shoulder x
    hz = thickness / 2.0

    # 5 profile points of the pentagonal boat (2D, in XY plane)
    profile = [
        (hL,  0.0),    # 0: bow tip
        (sx,  hW),     # 1: shoulder stbd
        (-hL, hW),     # 2: stern stbd
        (-hL, -hW),    # 3: stern port
        (sx,  -hW),    # 4: shoulder port
    ]
    n_profile = len(profile)

    # ── Build vertices ──
    # Top ring: 0..4  (Z = +hz)
    # Bottom ring: 5..9  (Z = -hz)
    # Side wall verts: each quad needs 4 unique verts for flat normals
    verts_list = []

    # Top cap verts (indices 0..4)
    for (px, py) in profile:
        verts_list.append(Gf.Vec3f(px, py, hz))

    # Bottom cap verts (indices 5..9)
    for (px, py) in profile:
        verts_list.append(Gf.Vec3f(px, py, -hz))

    # Side wall verts — 4 unique verts per quad (for flat shading normals)
    # Each side wall quad connects profile[i]→profile[(i+1)%5]
    # side_start_idx = 10
    side_start = len(verts_list)  # 10
    for i in range(n_profile):
        j = (i + 1) % n_profile
        px0, py0 = profile[i]
        px1, py1 = profile[j]
        # 4 verts: top-left, top-right, bottom-right, bottom-left
        verts_list.append(Gf.Vec3f(px0, py0, hz))     # top-i
        verts_list.append(Gf.Vec3f(px1, py1, hz))     # top-j
        verts_list.append(Gf.Vec3f(px1, py1, -hz))    # bot-j
        verts_list.append(Gf.Vec3f(px0, py0, -hz))    # bot-i

    verts = Vt.Vec3fArray(verts_list)

    # ── Build faces ──
    indices_list = []
    counts_list = []

    # Top cap: fan from vertex 0
    #   triangles: (0,1,2), (0,2,3), (0,3,4)
    for i in range(1, n_profile - 1):
        indices_list.extend([0, i, i + 1])
        counts_list.append(3)

    # Bottom cap: fan from vertex 5 (reversed winding for outward normal)
    #   triangles: (5,7,6), (5,8,7), (5,9,8)
    base = n_profile  # 5
    for i in range(1, n_profile - 1):
        indices_list.extend([base, base + i + 1, base + i])
        counts_list.append(3)

    # Side walls: 5 quads, each as 2 triangles
    for i in range(n_profile):
        si = side_start + i * 4  # start index of this quad's 4 verts
        # quad verts: si+0=top-i, si+1=top-j, si+2=bot-j, si+3=bot-i
        # tri 1: (si, si+1, si+2)
        # tri 2: (si, si+2, si+3)
        indices_list.extend([si, si + 1, si + 2])
        counts_list.append(3)
        indices_list.extend([si, si + 2, si + 3])
        counts_list.append(3)

    # ── Build normals (per-vertex) ──
    normals_list = []

    # Top cap normals: all (0,0,1)
    for _ in range(n_profile):
        normals_list.append(Gf.Vec3f(0, 0, 1))

    # Bottom cap normals: all (0,0,-1)
    for _ in range(n_profile):
        normals_list.append(Gf.Vec3f(0, 0, -1))

    # Side wall normals: outward from each edge
    for i in range(n_profile):
        j = (i + 1) % n_profile
        px0, py0 = profile[i]
        px1, py1 = profile[j]
        # Edge direction
        dx = px1 - px0
        dy = py1 - py0
        # Outward normal (rotate edge 90° CCW in XY, then normalize)
        nx = -dy
        ny = dx
        mag = math.sqrt(nx * nx + ny * ny)
        if mag > 1e-9:
            nx /= mag
            ny /= mag
        n_vec = Gf.Vec3f(nx, ny, 0)
        # All 4 verts of this quad get the same normal
        for _ in range(4):
            normals_list.append(n_vec)

    normals = Vt.Vec3fArray(normals_list)

    extent = Vt.Vec3fArray([
        Gf.Vec3f(-hL, -hW, -hz),
        Gf.Vec3f(hL, hW, hz),
    ])

    return (
        verts,
        Vt.IntArray(indices_list),
        Vt.IntArray(counts_list),
        normals,
        extent,
    )


# ══════════════════════════════════════════════════════════════════
#  TrajectoryVisualizer
# ══════════════════════════════════════════════════════════════════

class TrajectoryVisualizer:
    """
    Ghost corridor of semi-transparent boat silhouettes at desired poses.

    Each ghost is an individual Xform + Mesh prim (guaranteed to render
    in Isaac Sim RTX). All share one custom HologramGhost MDL material.
    """

    ROOT_PATH = "/World/DesiredTrajectory"
    MAT_PATH  = "/World/Looks/GhostTrailMat"

    def __init__(self, stage):
        self.stage = stage
        self._ghost_paths: list[str] = []
        self._z_height = 0.3
        self._opacity = 0.55
        self._geo_cache = None  # (verts, indices, counts, normals, extent)

    # ────────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────────

    def create(
        self,
        poses: np.ndarray,
        z_height: float = 0.3,
        vessel_length: float = 8.73,
        vessel_beam: float = 4.65,
        color: tuple = (0.05, 1.0, 0.25),
        emissive_intensity: float = 30.0,
        opacity: float = 0.35,
    ):
        """
        Create ghost-trail visualization.

        Parameters
        ----------
        poses : ndarray (N, 3)
            Each row = [x, y, ψ].
        """
        from pxr import UsdGeom, Gf
        from isaacsim.core.utils.prims import define_prim

        poses = np.asarray(poses, dtype=np.float64)
        n = len(poses)
        self._z_height = z_height
        self._opacity = opacity

        # 1. Ensure root Xform
        if not self.stage.GetPrimAtPath(self.ROOT_PATH).IsValid():
            define_prim(self.ROOT_PATH, "Xform")

        # 2. Material
        self._create_ghost_material(color, emissive_intensity, opacity)

        # 3. Pre-compute shared 3D geometry (extruded prism)
        self._geo_cache = _boat_silhouette_geo(vessel_length, vessel_beam,
                                                thickness=0.25)

        # 4. Create N individual ghosts
        self._ghost_paths = []
        for i in range(n):
            x   = float(poses[i, 0])
            y   = float(poses[i, 1])
            psi = float(poses[i, 2])
            path = f"{self.ROOT_PATH}/Ghost_{i:03d}"
            self._create_single_ghost(path, x, y, z_height, psi)
            self._ghost_paths.append(path)

        logger.info(
            "Ghost trail: %d 3D silhouettes created (L=%.1fm × W=%.1fm × H=0.25m, "
            "emissive=%.0f)",
            n, vessel_length, vessel_beam, emissive_intensity,
        )

    def update(self, poses: np.ndarray, z_height: float = None):
        """Update ghost positions and headings (e.g. from MPC output)."""
        from pxr import UsdGeom, Gf

        z = z_height if z_height is not None else self._z_height
        poses = np.asarray(poses, dtype=np.float64)
        n = min(len(poses), len(self._ghost_paths))

        for i in range(n):
            prim = self.stage.GetPrimAtPath(self._ghost_paths[i])
            if not prim.IsValid():
                continue
            xformable = UsdGeom.Xformable(prim)
            xformable.ClearXformOpOrder()

            x   = float(poses[i, 0])
            y   = float(poses[i, 1])
            psi = float(poses[i, 2])

            xformable.AddTranslateOp().Set(Gf.Vec3d(x, y, float(z)))
            xformable.AddRotateZOp().Set(math.degrees(psi))

    def set_visible(self, visible: bool):
        """Show / hide entire ghost trail."""
        from pxr import UsdGeom
        prim = self.stage.GetPrimAtPath(self.ROOT_PATH)
        if prim and prim.IsValid():
            img = UsdGeom.Imageable(prim)
            if visible:
                img.MakeVisible()
            else:
                img.MakeInvisible()


    # ────────────────────────────────────────────────────────
    #  Internal
    # ────────────────────────────────────────────────────────

    def _create_single_ghost(self, xform_path, x, y, z, psi_rad):
        """Create one ghost: Xform (translate+rotateZ) → child Mesh."""
        from pxr import UsdGeom, UsdShade, Gf

        # ── Xform ──
        xform = UsdGeom.Xform.Define(self.stage, xform_path)
        xform.AddTranslateOp().Set(Gf.Vec3d(x, y, float(z)))
        xform.AddRotateZOp().Set(math.degrees(psi_rad))

        # ── Child Mesh ──
        mesh_path = f"{xform_path}/Shape"
        mesh = UsdGeom.Mesh.Define(self.stage, mesh_path)

        verts, indices, counts, normals, extent = self._geo_cache

        mesh.GetPointsAttr().Set(verts)
        mesh.GetFaceVertexIndicesAttr().Set(indices)
        mesh.GetFaceVertexCountsAttr().Set(counts)
        mesh.GetNormalsAttr().Set(normals)
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        mesh.GetDoubleSidedAttr().Set(True)
        mesh.GetSubdivisionSchemeAttr().Set("none")
        mesh.CreateExtentAttr().Set(extent)

        # ── displayColor 作为无材质时的后备 ──
        mesh.GetDisplayColorAttr().Set([Gf.Vec3f(0.05, 1.0, 0.3)])

        # ── 绑定材质 ──
        mat = UsdShade.Material.Get(self.stage, self.MAT_PATH)
        if mat:
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)

    def _create_ghost_material(self, color, intensity, opacity):
        """
        Custom hologram material via HologramGhost.mdl.

        Pure emissive (explicit bsdf()=no reflection), cutout opacity.
        Requires /rtx/raytracing/fractionalCutoutOpacity = True.
        """
        from pxr import UsdShade, Sdf, Gf

        mat = UsdShade.Material.Define(self.stage, self.MAT_PATH)
        shader_path = f"{self.MAT_PATH}/Shader"
        shader = UsdShade.Shader.Define(self.stage, shader_path)

        # ── 引用自定义 MDL (通过 searchPaths/custom 解析) ──
        shader.SetSourceAsset("hologram_ghost.mdl", "mdl")
        shader.SetSourceAssetSubIdentifier("HologramGhost", "mdl")

        # ── 参数 ──
        shader.CreateInput(
            "emissive_color", Sdf.ValueTypeNames.Color3f,
        ).Set(Gf.Vec3f(float(color[0]), float(color[1]), float(color[2])))


        shader.CreateInput(
            "emissive_intensity", Sdf.ValueTypeNames.Float,
        ).Set(float(intensity))

        shader.CreateInput(
            "opacity", Sdf.ValueTypeNames.Float,
        ).Set(float(opacity))

        # ── Connect ──
        mat.CreateSurfaceOutput("mdl").ConnectToSource(
            shader.ConnectableAPI(), "out",
        )
        mat.CreateSurfaceOutput().ConnectToSource(
            shader.ConnectableAPI(), "out",
        )

        logger.info("Ghost material (HologramGhost.mdl v3): "
                     "intensity=%.0f, opacity=%.2f", intensity, opacity)
        return mat
