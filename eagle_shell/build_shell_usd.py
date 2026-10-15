"""build_shell_usd.py — eagle_shell 第二步：把分件 OBJ 装配为壳层 USD（平行资产，零侵入）。

运行环境：Isaac Sim 自带 Python（G:/Playground/isaac_sim_5.1.0/python.bat）。
pxr 绑定需在 Kit 环境内导入，故本脚本自起无头 SimulationApp，构建完即关。
职责：
  1. 解析 parts/ 下四个带 UV 的 OBJ；
  2. 计算资产系到机构系的装配变换（旋转映射 + 缩放 0.56 + 质心对齐），再折算为各连杆局部变换：
     base_link（头前身+机身尾杆）、innerwing_L（资产 +X 翼件）、innerwing_R（资产 -X 镜像翼件）；
  3. 生成 out/eagle_shell.usd：逐件顶级 Xform（携带连杆局部变换）+ Mesh（顶点/面/逐角 UV/顶点法线）
     + 单一 UsdPreviewSurface 材质（基色贴图 basecolor.png）；
  4. 生成 out/bind_manifest.json：件名、USD 顶级 prim 路径、目标连杆名，供运行时挂载器消费。
不读写 isaac_flapper_sim 任何既有文件。
"""
import json
import os

from isaacsim import SimulationApp
_app = SimulationApp({"headless": True})

import numpy as np

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

HERE = os.path.dirname(os.path.abspath(__file__))
PARTS = os.path.join(HERE, "parts")
OUT = os.path.join(HERE, "out")

SCALE = 0.56  # 翼展对齐缩放（资产全展 1.133 m → 0.63 m，对齐机构 0.64 m 口径）
Z_FINE = 0.0  # 竖向微调（首版取 0，目检后可改）

# 资产系（X=翼展, Y=上, Z=前）→ 机构系（x=前, y=左, z=上）：v_mech = M @ v_asset
M_ASSET2MECH = np.array([[0.0, 0.0, 1.0],
                         [1.0, 0.0, 0.0],
                         [0.0, 1.0, 0.0]])

# 件 → OBJ 文件名 / 目标连杆（注意：资产 +X 翼件映射到机构左侧 innerwing_L，
# 因机构 y 轴指左而资产 +X 翼件经映射落在 +y 侧）
BINDING = [
    ("eagle_head",  "head.obj",      "base_link"),
    ("eagle_body",  "body_tail.obj", "base_link"),
    ("eagle_wing_L", "wing_right.obj", "innerwing_L"),  # 资产 +X 母版翼
    ("eagle_wing_R", "wing_left.obj",  "innerwing_R"),   # 资产 -X 镜像翼
]

# 连杆局部原点（URDF 零位关节链：flap 原点 ±0.06 y，twist 原点 0）
LINK_ORIGINS = {
    "base_link":   np.zeros(3),
    "innerwing_L": np.array([0.0,  0.06, 0.0]),
    "innerwing_R": np.array([0.0, -0.06, 0.0]),
}

# I-114 鹰壳尾翼件：三件几何按骨架 URDF 连杆局部坐标直接生成，不走资产缩放
# 管线，恒等变换；尺寸与位置逐项对照 assets/flapper/fwav_15dof_from_group.urdf
# 的尾链视觉件，贴图取鸟尾羽区域的贴图块。
TAIL_BOXES = {
    "eagle_tail_base": ("tail_base", [
        ([-0.030,  0.000,  0.000], [0.070, 0.020, 0.005]),  # 尾撑杆
        ([-0.045,  0.085,  0.000], [0.012, 0.170, 0.004]),  # 展向延伸杆一
        ([-0.045, -0.085,  0.000], [0.012, 0.170, 0.004]),  # 展向延伸杆二
        ([-0.045,  0.000,  0.032], [0.070, 0.003, 0.055]),  # 固定垂尾
    ]),
    "eagle_tailpanel_L": ("tailpanel_L", [
        ([-0.045,  0.212, 0.000], [0.090, 0.125, 0.003]),   # 左舵面
    ]),
    "eagle_tailpanel_R": ("tailpanel_R", [
        ([-0.045, -0.212, 0.000], [0.090, 0.125, 0.003]),   # 右舵面
    ]),
}


def parse_obj(path):
    verts, uvs, faces = [], [], []   # faces: [(vi, ti) ×3]
    with open(path, "r", encoding="ascii") as f:
        for line in f:
            p = line.split()
            if not p:
                continue
            if p[0] == "v":
                verts.append([float(x) for x in p[1:4]])
            elif p[0] == "vt":
                uvs.append([float(x) for x in p[1:3]])
            elif p[0] == "f":
                corners = []
                for tok in p[1:]:
                    idx = tok.split("/")
                    vi = int(idx[0]) - 1
                    ti = int(idx[1]) - 1 if len(idx) > 1 and idx[1] else -1
                    corners.append((vi, ti))
                faces.append(corners)
    V = np.asarray(verts, dtype=np.float64)
    UV = np.asarray(uvs, dtype=np.float64)
    F = np.asarray([[c[0] for c in fc] for fc in faces], dtype=np.int64)
    TI = np.asarray([[c[1] for c in fc] for fc in faces], dtype=np.int64)
    return V, UV, F, TI


def smooth_normals(V, F):
    n = np.zeros_like(V)
    v0, v1, v2 = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    for k in range(3):
        np.add.at(n, F[:, k], fn)
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return n / norm


def _log(msg):
    import sys
    print(f"[eagle_build] {msg}", flush=True)


def _tail_uv_rect():
    """定位鸟尾羽贴图块：取 body_tail 资产件尾部切片的面心 UV 直方图峰值单元，
    外扩两格半作为贴图块；切片取资产系 z 小于 -0.22 的面。"""
    V, UV, F, TI = parse_obj(os.path.join(PARTS, "body_tail.obj"))
    tail_mask = V[:, 2] < -0.22
    cents = [UV[TI[fi]].mean(axis=0) for fi in range(len(F)) if tail_mask[F[fi]].all()]
    if not cents:
        return (0.25, 0.25, 0.75, 0.75)
    C = np.asarray(cents)
    hist, xe, ye = np.histogram2d(C[:, 0], C[:, 1], bins=32, range=[[0.0, 1.0], [0.0, 1.0]])
    i, j = np.unravel_index(np.argmax(hist), hist.shape)
    w = (xe[1] - xe[0]) * 2.5
    h = (ye[1] - ye[0]) * 2.5
    u0 = float(np.clip(xe[i] - w, 0.0, 1.0)); u1 = float(np.clip(xe[i] + (xe[1] - xe[0]) + w, 0.0, 1.0))
    v0 = float(np.clip(ye[j] - h, 0.0, 1.0)); v1 = float(np.clip(ye[j] + (ye[1] - ye[0]) + h, 0.0, 1.0))
    return (u0, v0, u1, v1)


def _box_piece(boxes, uv_rect):
    """生成带贴图坐标的盒组网格：每面四顶点独立以保留硬边与面法线，
    逐面 UV 映射到同一尾羽贴图块。返回 顶点/法线/面/面角UV索引 与 UV 表。"""
    u0, v0, u1, v1 = uv_rect
    face_uv = np.array([[u0, v0], [u1, v0], [u1, v1], [u0, v1]])
    V, N, F, TIv = [], [], [], []
    for c, s in boxes:
        c = np.asarray(c, dtype=np.float64)
        s = np.asarray(s, dtype=np.float64)
        for axis in range(3):
            for sign in (1.0, -1.0):
                n = np.zeros(3); n[axis] = sign
                t1 = np.zeros(3); t1[(axis + 1) % 3] = 1.0
                t2 = np.zeros(3); t2[(axis + 2) % 3] = 1.0
                base = len(V)
                for a, b in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
                    V.append(c + n * s[axis] * 0.5
                             + t1 * s[(axis + 1) % 3] * (a - 0.5)
                             + t2 * s[(axis + 2) % 3] * (b - 0.5))
                    N.append(n)
                F.append([base, base + 1, base + 2])
                F.append([base, base + 2, base + 3])
                TIv.extend([(0, 1, 2), (0, 2, 3)])
    return np.array(V), np.array(N), np.array(F, dtype=np.int64), np.array(TIv, dtype=np.int64), face_uv


def main():
    import sys
    def _log(msg):
        print(f"[eagle_build] {msg}", flush=True)

    os.makedirs(OUT, exist_ok=True)
    import shutil
    shutil.copyfile(os.path.join(PARTS, "basecolor.png"), os.path.join(OUT, "basecolor.png"))
    _log("app up, out ready")
    with open(os.path.join(PARTS, "parts_meta.json"), "r", encoding="utf-8") as f:
        meta = json.load(f)

    # 装配平移：基座件（头前身+机身尾杆）按面数加权质心对齐到机构原点
    c = (np.asarray(meta["head"]["centroid"]) * meta["head"]["faces"]
         + np.asarray(meta["body_tail"]["centroid"]) * meta["body_tail"]["faces"]) \
        / (meta["head"]["faces"] + meta["body_tail"]["faces"])
    t_global = -SCALE * (M_ASSET2MECH @ c)
    t_global[2] += Z_FINE
    print(f"asset weighted centroid {np.round(c,3)} -> mech translate {np.round(t_global,3)}")

    stage = Usd.Stage.CreateNew(os.path.join(OUT, "eagle_shell.usd"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    manifest = []
    for name, obj_file, link in BINDING:
        V, UV, F, TI = parse_obj(os.path.join(PARTS, obj_file))
        N = smooth_normals(V, F)
        print(f"[eagle_build] parsed {obj_file}: {len(V)}v/{len(F)}f", flush=True)

        # 连杆局部变换 = inv(T_link) @ Translate(t)·Scale(s)·R
        # （实测 Gf.Matrix4d 嵌套列表构造按列向量解释，旋转需喂 R 的转置才得到 v_mech = R·v_asset）
        t_local = t_global - LINK_ORIGINS[link]
        T = np.eye(4)
        T[:3, :3] = (SCALE * M_ASSET2MECH).T
        T[:3, 3] = t_local

        xf = UsdGeom.Xform.Define(stage, f"/{name}")
        xf.AddTransformOp().Set(Gf.Matrix4d(T.tolist()))
        mesh = UsdGeom.Mesh.Define(stage, f"/{name}/geom")
        mesh.CreatePointsAttr([Gf.Vec3f(*v) for v in V])
        mesh.CreateFaceVertexCountsAttr([3] * len(F))
        mesh.CreateFaceVertexIndicesAttr(F.reshape(-1).tolist())
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateNormalsAttr([Gf.Vec3f(*n) for n in N])
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        st = UsdGeom.PrimvarsAPI(mesh.GetPrim()).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
        st.Set([Gf.Vec2f(*UV[TI[i, k]]) for i in range(len(F)) for k in range(3)])

        print(f"[eagle_build] {name}: {len(V)} verts {len(F)} tris -> link {link}, t_local {np.round(t_local,3)}", flush=True)
        manifest.append({"name": name, "prim_path": f"/{name}", "link": link,
                         "file": obj_file, "tris": int(len(F))})

    # I-114 尾翼件：连杆局部坐标盒组，恒等变换，材质随下方统一绑定
    uv_rect = _tail_uv_rect()
    _log(f"tail uv rect {np.round(uv_rect, 3)}")
    for name, (link, boxes) in TAIL_BOXES.items():
        V, N, F, TIv, face_uv = _box_piece(boxes, uv_rect)
        xf = UsdGeom.Xform.Define(stage, f"/{name}")
        mesh = UsdGeom.Mesh.Define(stage, f"/{name}/geom")
        mesh.CreatePointsAttr([Gf.Vec3f(*v) for v in V])
        mesh.CreateFaceVertexCountsAttr([3] * len(F))
        mesh.CreateFaceVertexIndicesAttr(F.reshape(-1).tolist())
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateNormalsAttr([Gf.Vec3f(*n) for n in N])
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        mesh.CreateDoubleSidedAttr(True)
        st = UsdGeom.PrimvarsAPI(mesh.GetPrim()).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
        st.Set([Gf.Vec2f(*face_uv[TIv[i, k]]) for i in range(len(F)) for k in range(3)])
        _log(f"{name}: {len(V)} verts {len(F)} tris -> link {link}, identity local")
        manifest.append({"name": name, "prim_path": f"/{name}", "link": link,
                         "file": "(builtin-tail-boxes)", "tris": int(len(F))})

    # 材质：每个分件自带一份 UsdPreviewSurface 材质网络。绑定目标必须在引用
    # 作用域内，09-29 起材质挂在根级 /eagle_look，被引用进宿主舞台后绑定
    # 目标越界被忽略，整壳渲染为白模；改为每分件自带 /件名/Look 修复。
    all_parts = [n for n, _, _ in BINDING] + list(TAIL_BOXES.keys())
    for name in all_parts:
        mat = UsdShade.Material.Define(stage, f"/{name}/Look")
        shader = UsdShade.Shader.Define(stage, f"/{name}/Look/surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        uvread = UsdShade.Shader.Define(stage, f"/{name}/Look/st_reader")
        uvread.CreateIdAttr("UsdPrimvarReader_float2")
        uvread.CreateInput("varname", Sdf.ValueTypeNames.String).Set("st")
        tex = UsdShade.Shader.Define(stage, f"/{name}/Look/basecolor_tex")
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./basecolor.png"))
        tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
            uvread.ConnectableAPI(), "result")
        tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            tex.ConnectableAPI(), "rgb")
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.6)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "result")
        UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath(f"/{name}/geom")).Bind(mat)

    stage.GetRootLayer().Save()
    with open(os.path.join(OUT, "bind_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"usd": os.path.abspath(os.path.join(OUT, "eagle_shell.usd")),
                   "scale": SCALE, "translate_global": t_global.tolist(),
                   "parts": manifest}, f, indent=2)
    print("[eagle_build] eagle_shell.usd + bind_manifest.json written", flush=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        _app.close()
