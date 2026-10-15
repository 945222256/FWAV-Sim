"""prep_parts.py — eagle_shell 第一步：从 GLB + PartField 标签重导带 UV 的分件（平行资产，零侵入）。

运行环境：PartField 专用 venv（G:/Playground/PartField/venv，含 trimesh/numpy）。
输入：eagleX GLB、knn 档第 4 级部件标签、ZIP 内基色贴图。
输出（全部落在 eagle_shell/parts/ 下）：
  head.obj / body_tail.obj / wing_right.obj（母版）/ wing_left.obj（母版镜像）
  basecolor.png、parts_meta.json（资产系几何统计，供壳层 USD 计算变换）
分件口径与 exp_results/clustering/eagleX_knn/parts_lv04 一致；
左翼按已定案的母版镜像法由右翼生成（X 取反 + 面序反转保法线朝向）。
"""
import json
import os
import zipfile

import numpy as np
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
GLB = r"G:\Playground\isaac_flapper_sim\assets\eagleX_raw_50k\cfd068548a51f523e76e53b2c9c2f559.glb"
LABELS = r"G:\Playground\PartField\exp_results\clustering\eagleX_knn\cluster_out\eagleX_0_04.npy"
TEXTURE_ZIP = r"G:\Playground\isaac_flapper_sim\assets\eagleX_raw_50k\b50134432fcdc17a4a98826ee389d9bc.zip"
OUT = os.path.join(HERE, "parts")

# knn lv4 部件号（先前已核验：0=头部前身 1=左翼 2=右翼母版 3=机身尾杆）
PARTMAP = {0: "head", 1: "wing_left", 2: "wing_right", 3: "body_tail"}
MIRROR = {"wing_left": "wing_right"}  # 左翼 = 右翼母版镜像


def write_obj(path, V, F, uv_corner):
    """OBJ 写出：顶点、逐面顶点 UV（faceVarying 口径）、面。"""
    with open(path, "w", encoding="ascii") as f:
        for v in V:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for uv in uv_corner:
            f.write(f"vt {uv[0]:.6f} {uv[1]:.6f}\n")
        for i, face in enumerate(F):
            f.write("f " + " ".join(f"{a + 1}/{3 * i + k + 1}" for k, a in enumerate(face)) + "\n")


def main():
    os.makedirs(OUT, exist_ok=True)
    mesh = trimesh.load(GLB, force="mesh", process=False)
    V = np.asarray(mesh.vertices, dtype=np.float64)
    F = np.asarray(mesh.faces, dtype=np.int64)
    n_faces = len(F)

    uv = None
    if hasattr(mesh.visual, "uv") and mesh.visual.uv is not None:
        uv = np.asarray(mesh.visual.uv, dtype=np.float64)
    if uv is None:
        raise SystemExit("GLB 未携带 UV，纹理路线受阻")
    if uv.shape[0] == n_faces * 3:
        uv_corner = uv
    elif uv.shape[0] == len(V):
        uv_corner = uv[F].reshape(-1, 2)  # per-vertex → per-corner
    else:
        raise SystemExit(f"UV 形状异常 {uv.shape}（面 {n_faces} 顶点 {len(V)}）")
    print(f"mesh: V={len(V)} F={n_faces} uv_corner={uv_corner.shape}")

    raw = np.asarray(np.load(LABELS)).reshape(-1)
    labels = np.unique(raw, return_inverse=True)[1]  # 聚类代表元编号 → 0..3 序号（uq 升序与先前核验口径一致）

    # 基色贴图（ZIP 内）
    with zipfile.ZipFile(TEXTURE_ZIP) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".png") and "pbr_20250901." in n
                 and not n.endswith(("_normal.png", "_roughness.png", "_metallic.png"))]
        if not names:
            names = [n for n in z.namelist() if n.lower().endswith(".png")]
        tex = z.read(names[0])
    with open(os.path.join(OUT, "basecolor.png"), "wb") as f:
        f.write(tex)
    print(f"texture: {names[0]} -> basecolor.png ({len(tex)/1e6:.1f} MB)")

    meta = {"asset_bounds_min": V.min(0).tolist(), "asset_bounds_max": V.max(0).tolist()}
    for k, name in PARTMAP.items():
        src_name = MIRROR.get(name, name)
        src_k = next(kk for kk, nn in PARTMAP.items() if nn == src_name)
        face_mask = labels == src_k  # 镜像件从母版标签集合取面，再镜像几何
        fk = F[face_mask]
        keep = np.unique(fk)
        remap = -np.ones(len(V), dtype=np.int64)
        remap[keep] = np.arange(len(keep))
        Vp = V[keep]
        Fp = remap[fk]
        uv_p = uv_corner[np.repeat(face_mask, 3)].reshape(-1, 2)
        if src_name != name:  # 镜像：X 取反 + 面序反转
            Vp = Vp * np.array([-1.0, 1.0, 1.0])
            Fp = Fp[:, ::-1]
            uv_p = uv_p.reshape(-1, 3, 2)[:, ::-1, :].reshape(-1, 2)  # 角序随面序同步反转
            uv_p = np.stack([uv_p[:, 0], 1.0 - uv_p[:, 1]], axis=1)  # 镜像翻转 U 轴防贴图反字
        path = os.path.join(OUT, f"{name}.obj")
        write_obj(path, Vp, Fp, uv_p)
        meta[name] = {
            "verts": int(len(Vp)), "faces": int(len(Fp)),
            "centroid": Vp.mean(0).tolist(),
            "bounds_min": Vp.min(0).tolist(), "bounds_max": Vp.max(0).tolist(),
            "mirrored": src_name != name,
        }
        print(f"{name}: V={len(Vp)} F={len(Fp)} mirrored={src_name != name}")

    with open(os.path.join(OUT, "parts_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print("parts_meta.json written")


if __name__ == "__main__":
    main()
