"""Offline asset processing only: no simulator, no physics or production writes.

零仿真器声明：本脚本不启动任何仿真器进程。I-201 动画构型第二版修复轮（机主
尾部认可后复工授权，2026-10-13）：保留机主认可的尾部构型与相对运动；修复导出
模型蒙皮骨骼编号（皮肤局部编号）、把翼根扭转轴候选移到靠机头侧（约百分之二
十弦长，零位外形不动）、审计并清理主翼机身连接毛边（仅移除零面积退化三角
形）、并把量测与视频全部改为从实际导出的 glb 模型逐帧计算（五节弯曲骨线性
混合蒙皮，同一变形路径）。冻结物理数据保持原值。
"""
from pathlib import Path
import csv
import hashlib
import importlib.util
import json
import math
import struct
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
OUT = HERE / "output"
OUT.mkdir(parents=True, exist_ok=True)
PARTS_DIR = OUT / "parts"
PARTS_DIR.mkdir(exist_ok=True)
SOURCE_PARTS = ROOT / "eagle_shell/parts"
URDF = ROOT / "assets/flapper/fwav_15dof_from_group.urdf"
CLARIFY = ROOT / "logs/jane_dispatch_20261013/机主构型澄清_翼尖与整尾接缝.md"
APPROVAL = ROOT / "logs/jane_dispatch_20261013/尾部认可与生产线复工_20261013.md"
REVIEW = ROOT / "logs/jane_dispatch_20261013/动画第二版专业复核_20261013.md"
JOINT_CHECK = ROOT / "logs/jane_dispatch_20261013/animv2_glb_joint_check.json"
OLD_GAPS = ROOT / "logs/jane_dispatch_20261013/old_preview_seam_gaps.json"
TEXTURE = SOURCE_PARTS / "basecolor.png"
OLD_MANIFEST = ROOT / "eagle_shell/out/bind_manifest.json"

mf = json.loads(OLD_MANIFEST.read_text(encoding="utf-8"))
scale = float(mf["scale"])
translate = np.array(mf["translate_global"], dtype=float) * np.array([-1., -1., 1.])
asset_rotation = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=float)

robot = ET.parse(URDF).getroot()
urdf_joints = {}
for e in robot.findall("joint"):
    origin = e.find("origin")
    xyz = np.array(list(map(float, origin.get("xyz", "0 0 0").split())))
    lim = e.find("limit")
    urdf_joints[e.get("name")] = {"type": e.get("type"), "origin_xyz_m": xyz.tolist(),
                                  "axis": list(map(float, e.find("axis").get("xyz").split())),
                                  "lower_rad": float(lim.get("lower")), "upper_rad": float(lim.get("upper"))}

WING_SPLIT_Y = 0.14
TAIL_SPLIT_Y = 0.012
N_BONES = 10
TRANS_W_TIP = 0.10
TRANS_W_TAIL = 0.07
SEAM_THRESHOLD_M = 1e-5
EDGE_THRESHOLD = 0.01
TWIST_AXIS_FRACTION = 0.20   # nose-side candidate: 20% chord from LE at the joint station
DEMO = {"flap_deg": 30, "twist_deg": 25, "tip_fold_deg": 20, "tail_psi_deg": 20,
        "tail_phi_deg": 15, "open_deg": 20}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_obj(path):
    verts, uvs, faces, uvfaces = [], [], [], []
    for line in Path(path).read_text(encoding="ascii").splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            verts.append(list(map(float, p[1:4])))
        elif p[0] == "vt":
            uvs.append(list(map(float, p[1:3])))
        elif p[0] == "f":
            corners = [s.split("/") for s in p[1:4]]
            faces.append([int(s[0]) - 1 for s in corners])
            uvfaces.append([int(s[1]) - 1 for s in corners])
    v, f, uv = np.array(verts), np.array(faces), np.array(uvs)
    normal = np.zeros_like(v)
    cross = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    for k in range(3):
        np.add.at(normal, f[:, k], cross)
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-15)
    p = (v[f] @ asset_rotation.T) * scale + translate
    n = normal[f] @ asset_rotation.T
    return np.concatenate([p, uv[np.array(uvfaces)], n], axis=2)


def clip(tris, plane, sign, keep_inner):
    out = []
    for tri in tris:
        poly = list(tri)
        result = []
        for a, b in zip(poly, poly[1:] + poly[:1]):
            da, db = sign * a[1] - plane, sign * b[1] - plane
            ina = da <= 1e-12 if keep_inner else da >= -1e-12
            inb = db <= 1e-12 if keep_inner else db >= -1e-12
            if ina:
                result.append(a)
            if ina != inb:
                t = da / (da - db)
                c = a + t * (b - a)
                c[1] = sign * plane
                result.append(c)
        for i in range(1, len(result) - 1):
            t = np.array([result[0], result[i], result[i + 1]])
            if np.linalg.norm(np.cross(t[1, :3] - t[0, :3], t[2, :3] - t[0, :3])) > 1e-14:
                out.append(t)
    return np.array(out)


def drop_degenerate(tris, eps=1e-12):
    cross = np.cross(tris[:, 1, :3] - tris[:, 0, :3], tris[:, 2, :3] - tris[:, 0, :3])
    area2 = np.linalg.norm(cross, axis=1)
    keep = area2 > eps
    return tris[keep], int((~keep).sum())


def axis_rotation(axis, angle):
    a = np.array(axis, dtype=float); a /= np.linalg.norm(a)
    x, y, z = a; c = math.cos(angle); s = math.sin(angle); k = 1 - c
    return np.array([[c + x * x * k, x * y * k - z * s, x * z * k + y * s],
                     [y * x * k + z * s, c + y * y * k, y * z * k - x * s],
                     [z * x * k - y * s, z * y * k + x * s, c + z * z * k]])


def pca_hinge(edge_points, expected_axis):
    c = edge_points.mean(axis=0)
    d = edge_points - c
    _, vecs = np.linalg.eigh(d.T @ d)
    direction = vecs[:, -1]
    ex = np.asarray(expected_axis, dtype=float)
    if direction @ ex < 0:
        direction = -direction
    direction = direction / np.linalg.norm(direction)
    ang = math.degrees(math.acos(min(1.0, abs(float(direction @ ex)))))
    return {"point_xyz_m": c.tolist(), "direction": direction.tolist(),
            "angle_from_expected_deg": float(ang), "points_fitted": int(len(edge_points))}


def seam_edge_corners(tris, sign, plane, tol=2e-4):
    pts = tris[:, :, :3].reshape(-1, 3)
    return pts[np.abs(pts[:, 1] - sign * plane) < tol]


def section_at_y(tris, y):
    pts = []
    for k in range(3):
        a, b = tris[:, k, :3], tris[:, (k + 1) % 3, :3]
        m = ((a[:, 1] - y) * (b[:, 1] - y) <= 0) & (np.abs(b[:, 1] - a[:, 1]) > 1e-13)
        t = (y - a[m, 1]) / (b[m, 1] - a[m, 1])
        pts.append(a[m] + t[:, None] * (b[m] - a[m]))
    return np.concatenate(pts)


def rounded_key(p):
    return (round(float(p[0]) * 1e7), round(float(p[1]) * 1e7), round(float(p[2]) * 1e7))


def gltf_pos(p):
    p = np.asarray(p)
    return np.stack([p[..., 0], p[..., 2], -p[..., 1]], axis=-1)


def gltf_dir(d):
    d = np.asarray(d, dtype=float)
    if d.ndim == 1:
        return [float(d[0]), float(d[2]), -float(d[1])]
    return np.stack([d[:, 0], d[:, 2], -d[:, 1]], axis=-1)


# ---------------------------------------------------------------- load, split, clean
meshes = {}
binding = []
meshes["eagle_body_and_nose"] = read_obj(SOURCE_PARTS / "body_tail.obj")
burr_audit = {}
body_clean, n_deg = drop_degenerate(meshes["eagle_body_and_nose"])
burr_audit["eagle_body_and_nose"] = {"degenerate_triangles_removed": n_deg}
meshes["eagle_body_and_nose"] = body_clean
binding.append({"part": "eagle_body_and_nose", "node": "base_link", "source": "body_tail.obj", "skinned": False})

wing_full = {}
wing_axes = {}
for side, sign in [("L", 1), ("R", -1)]:
    tri = read_obj(SOURCE_PARTS / ("wing_left.obj" if side == "L" else "wing_right.obj"))
    wing_full[side] = tri
    tri, n_deg = drop_degenerate(tri)
    burr_audit[f"wing_full_{side}"] = {"degenerate_triangles_removed": n_deg}
    # root pivot candidate: nose-side twist axis at the joint station; zero pose unchanged
    sec = section_at_y(tri, sign * 0.06)
    le = float(sec[:, 0].max()); te = float(sec[:, 0].min())
    coords = tri[:, :, :3].reshape(-1, 3)
    uniq = np.unique(coords, axis=0)
    band = uniq[np.abs(uniq[:, 1]) < 0.06]
    root_pivot = np.array([le - TWIST_AXIS_FRACTION * (le - te), sign * 0.06, float(band[:, 2].mean())])
    wing_axes[side] = {"root_pivot_xyz_m": root_pivot, "joint_station_le_x_m": le,
                       "joint_station_te_x_m": te, "chord_m": le - te,
                       "twist_axis_fraction_from_le": TWIST_AXIS_FRACTION,
                       "root_band_centroid_xyz_m": band.mean(axis=0).tolist(),
                       "quarter_chord_reference_x_m": le - 0.25 * (le - te)}
    main = clip(tri, WING_SPLIT_Y, sign, True)
    tip = clip(tri, WING_SPLIT_Y, sign, False)
    meshes[f"eagle_wing_main_{side}"] = main
    meshes[f"eagle_wing_tip_{side}"] = tip
    binding += [{"part": f"eagle_wing_main_{side}", "node": f"innerwing_{side}", "source": ("wing_left.obj" if side == "L" else "wing_right.obj"), "skinned": False},
                {"part": f"eagle_wing_tip_{side}", "node": f"innerwing_{side}", "source": ("wing_left.obj" if side == "L" else "wing_right.obj"), "skinned": True}]

fan = read_obj(SOURCE_PARTS / "head.obj")
fan, n_deg = drop_degenerate(fan)
burr_audit["tail_fan"] = {"degenerate_triangles_removed": n_deg}
fin = clip(clip(fan, TAIL_SPLIT_Y, 1.0, True), -TAIL_SPLIT_Y, 1.0, False)
flatL = clip(fan, TAIL_SPLIT_Y, 1.0, False)
flatR = clip(fan, -TAIL_SPLIT_Y, 1.0, True)
meshes["eagle_tail_fin"] = fin
meshes["eagle_tail_flat_L"] = flatL
meshes["eagle_tail_flat_R"] = flatR
binding += [{"part": "eagle_tail_fin", "node": "tail_carrier", "source": "head.obj (original tail fan; legacy name incorrect)", "skinned": False},
            {"part": "eagle_tail_flat_L", "node": "tail_carrier", "source": "head.obj", "skinned": True},
            {"part": "eagle_tail_flat_R", "node": "tail_carrier", "source": "head.obj", "skinned": True}]

# mirror deviation audit: compare welded L/R wing root band coordinates
band_L = np.unique(wing_full["L"][:, :, :3].reshape(-1, 3), axis=0)
band_L = band_L[np.abs(band_L[:, 1]) < 0.06]
band_R = np.unique(wing_full["R"][:, :, :3].reshape(-1, 3), axis=0)
band_R = band_R[np.abs(band_R[:, 1]) < 0.06]
mirror_dev = float(np.abs(np.sort(band_L[:, 0]) - np.sort(band_R[:, 0])).max()) if len(band_L) == len(band_R) else None
burr_audit["mirror_deviation_root_band_max_x_m"] = mirror_dev
burr_audit["renderer_note"] = "软件渲染丢弃投影面积不足零点二二平方像素的三角形并按三角面平均纹理取色；与源网格孔洞、聚类边界锯齿分别核对，不互相解释"

# ---------------------------------------------------------------- hinges
hinges = {}
for side, sign in [("L", 1), ("R", -1)]:
    hinges[f"tipFold_{side}"] = pca_hinge(seam_edge_corners(meshes[f"eagle_wing_main_{side}"], sign, WING_SPLIT_Y), [1, 0, 0])
    hinges[f"open{'L' if side == 'L' else 'R'}"] = pca_hinge(seam_edge_corners(fin, sign, TAIL_SPLIT_Y), [1, 0, 0])
fc = fin[:, :, :3].reshape(-1, 3)
root_thr = np.quantile(fc[:, 0], 0.90)
seam_c = fc[(fc[:, 0] > root_thr) & (np.abs(fc[:, 1]) < TAIL_SPLIT_Y + 0.002)]
carrier_origin = seam_c.mean(axis=0)
hinges["tail_whole"] = {"point_xyz_m": carrier_origin.tolist()}

# ---------------------------------------------------------------- rig (single source of truth)
# node: parent, translation (parent-local, bind), rotation axis (bind-local), angle key, fraction
RIG = {}


def add_rig(name, parent, translation, axis, angle_key, fraction, note=""):
    RIG[name] = {"parent": parent, "translation": np.array(translation, dtype=float),
                 "axis": np.array(axis, dtype=float), "angle_key": angle_key,
                 "fraction": fraction, "note": note}


SKIN_CHAINS = {}   # part -> list of node names [anchor, b1..bN]


def surface_profile_offsets(part, sign, plane, width):
    """Mean surface height per bend station, so the bone chain follows the wing arc."""
    y = meshes[part][:, :, 1].reshape(-1)
    z = meshes[part][:, :, 2].reshape(-1)
    s = np.maximum(sign * y - plane, 0.0)
    offs = []
    z_prev = float(z[s < width / N_BONES].mean()) if (s < width / N_BONES).any() else 0.0
    for k in range(1, N_BONES + 1):
        lo, hi = (k - 1) * width / N_BONES, k * width / N_BONES
        m = (s >= lo) & (s < hi if k < N_BONES else s <= hi + 1e-9)
        zk = float(z[m].mean()) if m.any() else z_prev
        offs.append((sign * width / N_BONES, zk - z_prev))
        z_prev = zk
    return offs


for side in ["L", "R"]:
    p = wing_axes[side]["root_pivot_xyz_m"]
    add_rig(f"flapconn_{side}", "base_link", p, [1, 0, 0], f"flap_{side}", 1.0,
            "flap: chordwise x axis through the nose-side root pivot candidate")
    add_rig(f"innerwing_{side}", f"flapconn_{side}", [0, 0, 0], [0, 1, 0], f"twist_{side}", 1.0,
            "twist: spanwise y axis through the same pivot; 20% chord from LE (candidate)")
    h = hinges[f"tipFold_{side}"]
    chain = [f"innerwing_{side}"]
    parent = f"innerwing_{side}"
    sign = 1 if side == "L" else -1
    offs = surface_profile_offsets(f"eagle_wing_tip_{side}", sign, WING_SPLIT_Y, TRANS_W_TIP)
    for k in range(N_BONES):
        name = f"tipBone_{side}_{k+1}"
        t = (np.array(h["point_xyz_m"]) - p) if k == 0 else np.array([0.0, offs[k][0], offs[k][1]])
        add_rig(name, parent, t, h["direction"], f"tip_fold_{side}", 1.0 / N_BONES,
                f"bend bone {k+1}/{N_BONES}; stations follow the wing surface arc; seam stays continuous")
        chain.append(name)
        parent = name
    SKIN_CHAINS[f"eagle_wing_tip_{side}"] = chain
add_rig("tailYawWhole", "base_link", carrier_origin, [0, 0, 1], "tail_psi", 1.0,
        "whole-tail yaw about the fixed body z axis (approved configuration)")
add_rig("tailRollWhole", "tailYawWhole", [0, 0, 0], [1, 0, 0], "tail_phi", 1.0,
        "whole-tail roll about the local x after yaw; R = Rz(psi) @ Rx(phi)")
add_rig("tail_carrier", "tailRollWhole", [0, 0, 0], [1, 0, 0], "tail_phi", 0.0,
        "common carrier node; zero fraction (driven by parents)")
for part, side in [("eagle_tail_flat_L", "L"), ("eagle_tail_flat_R", "R")]:
    h = hinges[f"open{side}"]
    chain = ["tail_carrier"]
    parent = "tail_carrier"
    sign = 1 if side == "L" else -1
    offs = surface_profile_offsets(part, sign, TAIL_SPLIT_Y, TRANS_W_TAIL)
    for k in range(N_BONES):
        name = f"openBone_{side}_{k+1}"
        t = (np.array(h["point_xyz_m"]) - carrier_origin) if k == 0 else np.array([0.0, offs[k][0], offs[k][1]])
        add_rig(name, parent, t, h["direction"], f"open_{side}", 1.0 / N_BONES,
                f"spread bend bone {k+1}/{N_BONES}; stations follow the surface arc")
        chain.append(name)
        parent = name
    SKIN_CHAINS[part] = chain


def node_globals(a):
    out = {"base_link": (np.eye(3), np.zeros(3))}

    def rec(name):
        if name in out:
            return out[name]
        nd = RIG[name]
        pr, pt = rec(nd["parent"])
        ang = a.get(nd["angle_key"], 0.0) * nd["fraction"]
        r = pr @ axis_rotation(nd["axis"], ang)
        t = pt + pr @ nd["translation"]
        out[name] = (r, t)
        return out[name]

    for n in RIG:
        rec(n)
    return out


BIND = node_globals({})

# skin weights: zone-based two influences over [anchor, b1..bN]
SKIN_W = {}
for part, chain in SKIN_CHAINS.items():
    if part.startswith("eagle_wing_tip"):
        sign = 1 if part.endswith("_L") else -1
        plane, width = WING_SPLIT_Y, TRANS_W_TIP
    else:
        sign = 1 if part.endswith("_L") else -1
        plane, width = TAIL_SPLIT_Y, TRANS_W_TAIL
    y = meshes[part][:, :, 1].reshape(-1)
    s = sign * y - plane
    s = np.maximum(s, 0.0)
    pos = s * N_BONES / width
    zone = np.minimum(pos.astype(int), N_BONES)
    u = np.clip(pos - zone, 0.0, 1.0)
    w = np.zeros((len(y), 2))
    w[:, 0] = 1 - u
    w[:, 1] = u
    SKIN_W[part] = {"zone": zone, "weights": w}


def part_world_corners(part, a):
    """World corners under command dict a; LBS with the same chain as the exported glb."""
    corners = meshes[part][:, :, :3]
    if part in SKIN_CHAINS:
        chain = SKIN_CHAINS[part]
        G = node_globals(a)
        flat = corners.reshape(-1, 3)
        out = np.zeros_like(flat)
        zone = SKIN_W[part]["zone"]
        w = SKIN_W[part]["weights"]
        for slot in range(2):
            jnames = np.array(chain)[np.minimum(zone + slot, N_BONES)]
            for jname in set(jnames):
                m = jnames == jname
                if not m.any():
                    continue
                R, t = G[jname]
                bR, bt = BIND[jname]
                v = (flat[m] - bt) @ bR.T
                out[m] += w[m, slot][:, None] * (v @ R.T + t)
        return out.reshape(corners.shape)
    node = next(b["node"] for b in binding if b["part"] == part)
    R, t = node_globals(a)[node]
    return corners @ R.T + t


# ---------------------------------------------------------------- timeline (approved tail kept)
spec = importlib.util.spec_from_file_location("eaglex_existing_kinematics", ROOT / "modules/aero/kinematics.py")
kin = importlib.util.module_from_spec(spec); spec.loader.exec_module(kin)
from config import sim_config as cfg
wk = kin.WingKinematics(frequency=cfg.WING_DRIVE_CONFIG["frequency"],
                        amplitude_deg=cfg.WING_DRIVE_CONFIG["amplitude_deg"],
                        pitch_mode=cfg.WING_DRIVE_CONFIG["pitch_mode"],
                        alpha_set_deg=cfg.WING_DRIVE_CONFIG["alpha_set_deg"])
JOINTS = ["flap_L", "twist_L", "flap_R", "twist_R", "tip_fold_L", "tip_fold_R",
          "tail_psi", "tail_phi", "open_L", "open_R"]
duration = 20.0
times = np.linspace(0, duration, 301)


def rad(x):
    return math.radians(x)


poses, modes = [], []
for t in times:
    t = float(t)
    a = {k: 0.0 for k in JOINTS}
    if t < 2:
        m = "FLAP_INHERIT"
        v = rad(DEMO["flap_deg"]) * math.sin(math.pi * t)
        a.update(flap_L=v, flap_R=-v)
    elif t < 4:
        m = "TWIST_INHERIT"
        v = rad(DEMO["twist_deg"]) * math.sin(math.pi * (t - 2))
        a.update(twist_L=v, twist_R=v)
    elif t < 6:
        m = "TIP_DIHEDRAL_UP"
        v = rad(DEMO["tip_fold_deg"]) * math.sin(math.pi * (t - 4))
        a.update(tip_fold_L=v, tip_fold_R=-v)
    elif t < 8:
        m = "TIP_DOWNFOLD"
        v = -rad(DEMO["tip_fold_deg"]) * math.sin(math.pi * (t - 6))
        a.update(tip_fold_L=v, tip_fold_R=-v)
    elif t < 10:
        m = "FLAP_PLUS_FOLD"
        ev = wk.evaluate(t - 8)
        env = min(1, (t - 8) / .25, (10 - t) / .25)
        fold = rad(DEMO["tip_fold_deg"]) * .5 * (1 + math.sin(wk.omega * (t - 8) + 2.2)) * env
        a.update(flap_L=ev["phi"] * env, flap_R=-ev["phi"] * env,
                 twist_L=ev["alpha"] * env, twist_R=ev["alpha"] * env,
                 tip_fold_L=fold, tip_fold_R=-fold)
    elif t < 12:
        m = "TAIL_YAW_WHOLE"
        a.update(tail_psi=rad(DEMO["tail_psi_deg"]) * math.sin(math.pi * (t - 10)))
    elif t < 14:
        m = "TAIL_ROLL_WHOLE"
        a.update(tail_phi=rad(DEMO["tail_phi_deg"]) * math.sin(math.pi * (t - 12)))
    elif t < 16:
        m = "TAIL_OPEN_LINKED_3DOF"
        v = rad(DEMO["open_deg"]) * math.sin(math.pi * (t - 14))
        a.update(open_L=v, open_R=-v)
    elif t < 18:
        m = "TAIL_ALL_TWO_WHOLE_PLUS_OPEN"
        s = math.sin(math.pi * (t - 16))
        a.update(tail_psi=rad(DEMO["tail_psi_deg"]) * s,
                 tail_phi=rad(DEMO["tail_phi_deg"]) * s * .8,
                 open_L=rad(DEMO["open_deg"]) * s, open_R=-rad(DEMO["open_deg"]) * s)
    else:
        m = "OPEN_INDEPENDENT_4DOF_COMPARE"
        if t < 19:
            a.update(open_L=rad(DEMO["open_deg"]) * math.sin(math.pi * (t - 18)))
        else:
            a.update(open_R=rad(DEMO["open_deg"]) * math.sin(math.pi * (t - 19)))
    poses.append(a)
    modes.append(m)

with (OUT / "joint_commands.csv").open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["time_s", "mode"] + JOINTS + ["coupling"])
    for t, m, a in zip(times, modes, poses):
        coup = ("linked(3dof-candidate)" if m in ("TAIL_OPEN_LINKED_3DOF", "TAIL_ALL_TWO_WHOLE_PLUS_OPEN")
                else ("independent(4dof-candidate)" if m == "OPEN_INDEPENDENT_4DOF_COMPARE" else "n/a"))
        w.writerow([t, m] + [a[n] for n in JOINTS] + [coup])

# ---------------------------------------------------------------- write parts
for b in binding:
    part = b["part"]
    tri = meshes[part].copy()
    if not b["skinned"]:
        node = b["node"]
        if node.startswith("innerwing"):
            side = node[-1]
            origin = wing_axes[side]["root_pivot_xyz_m"]
        elif node == "tail_carrier":
            origin = carrier_origin
        else:
            origin = np.zeros(3)
        tri[:, :, :3] -= origin
        b["zero_origin_xyz_m"] = np.asarray(origin).tolist()
    else:
        b["zero_origin_xyz_m"] = [0.0, 0.0, 0.0]
        b["skin_note"] = f"skinned in zero-pose world frame; {N_BONES} chained bend bones + anchor; weights two influences"
    with (PARTS_DIR / (part + ".obj")).open("w", encoding="ascii") as fh:
        fh.write("mtllib basecolor.mtl\nusemtl eagle\n")
        for p in tri[:, :, :3].reshape(-1, 3):
            fh.write("v %.9f %.9f %.9f\n" % tuple(p))
        for uv in tri[:, :, 3:5].reshape(-1, 2):
            fh.write("vt %.9f %.9f\n" % tuple(uv))
        for nrm in tri[:, :, 5:8].reshape(-1, 3):
            fh.write("vn %.9f %.9f %.9f\n" % tuple(nrm / max(np.linalg.norm(nrm), 1e-15)))
        for i in range(len(tri)):
            idx = [3 * i + k + 1 for k in range(3)]
            fh.write("f " + " ".join(f"{n}/{n}/{n}" for n in idx) + "\n")
    b["part_file"] = "parts/" + part + ".obj"
(PARTS_DIR / "basecolor.png").write_bytes(TEXTURE.read_bytes())
(PARTS_DIR / "basecolor.mtl").write_text("newmtl eagle\nKd 1 1 1\nmap_Kd basecolor.png\n", encoding="ascii")

# ---------------------------------------------------------------- GLB export
g = {"asset": {"version": "2.0", "generator": "L8 offline animation-config v2 fix round, I-201"},
     "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [], "meshes": [], "bufferViews": [], "accessors": [], "skins": [],
     "materials": [{"name": "Eagle basecolor", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0, "roughnessFactor": .6}, "doubleSided": True}],
     "textures": [{"source": 0, "sampler": 0}], "samplers": [{"magFilter": 9729, "minFilter": 9729, "wrapS": 10497, "wrapT": 10497}], "images": []}
blob = bytearray()


def view(data, target=None):
    while len(blob) % 4:
        blob.extend(b"\0")
    off = len(blob); blob.extend(data)
    d = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
    if target:
        d["target"] = target
    g["bufferViews"].append(d)
    return len(g["bufferViews"]) - 1


def accessor_f(array, type_, target=None):
    a = np.asarray(array, dtype="<f4")
    if type_ == "MAT4":
        # glTF stores matrix columns consecutively, including inverse bind matrices.
        a = a.swapaxes(-1, -2)
    d = {"bufferView": view(a.tobytes(), target), "componentType": 5126, "count": len(a), "type": type_}
    if a.ndim == 2 and len(a):
        d["min"] = a.min(axis=0).reshape(-1).tolist(); d["max"] = a.max(axis=0).reshape(-1).tolist()
    g["accessors"].append(d)
    return len(g["accessors"]) - 1


def accessor_us(array):
    a = np.asarray(array, dtype="<u2")
    d = {"bufferView": view(a.tobytes(), 34962), "componentType": 5123, "count": len(a), "type": "VEC4"}
    g["accessors"].append(d)
    return len(g["accessors"]) - 1


g["images"].append({"bufferView": view(TEXTURE.read_bytes()), "mimeType": "image/png"})
# all coordinates and translations below are authored directly in the glTF y-up world
# frame (gltf_pos/gltf_dir), so the root carries no rotation; this avoids a double
# z-up-to-y-up conversion that previously broke rigid-part placement and seam pairing.
g["nodes"].append({"name": "scene_root", "children": [1]})
g["nodes"].append({"name": "base_link", "children": []})
NODE = {"base_link": 1}
RIG_ORDER = []
while len(RIG_ORDER) < len(RIG):
    for n, nd in RIG.items():
        if n in NODE:
            continue
        if nd["parent"] in NODE or nd["parent"] == "base_link":
            idx = len(g["nodes"]); NODE[n] = idx
            g["nodes"].append({"name": n, "translation": gltf_pos(nd["translation"]).tolist(),
                               "rotation": [0, 0, 0, 1], "children": [],
                               "extras": {"angle_key": nd["angle_key"], "angle_fraction": nd["fraction"],
                                          "axis_gltf": gltf_dir(nd["axis"]), "note": nd["note"]}})
            g["nodes"][NODE[nd["parent"]]]["children"].append(idx)
            RIG_ORDER.append(n)

# local skin joint index tables (fix of the numbering defect)
LOCAL_JOINT_OF = {}
for part, chain in SKIN_CHAINS.items():
    LOCAL_JOINT_OF[part] = {name: i for i, name in enumerate(chain)}

for b in binding:
    part = b["part"]
    tri = meshes[part]
    if b["skinned"]:
        p = gltf_pos(tri[:, :, :3].reshape(-1, 3))
        normals = gltf_dir(tri[:, :, 5:8].reshape(-1, 3))
    else:
        p = gltf_pos(tri[:, :, :3].reshape(-1, 3) - np.array(b["zero_origin_xyz_m"]))
        normals = gltf_dir(tri[:, :, 5:8].reshape(-1, 3))
    normals = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-15)
    uv = tri[:, :, 3:5].reshape(-1, 2).copy(); uv[:, 1] = 1 - uv[:, 1]
    attr = {"POSITION": accessor_f(p, "VEC3", 34962), "NORMAL": accessor_f(normals, "VEC3", 34962),
            "TEXCOORD_0": accessor_f(uv, "VEC2", 34962)}
    if b["skinned"]:
        chain = SKIN_CHAINS[part]
        zone = SKIN_W[part]["zone"]
        w = SKIN_W[part]["weights"]
        j0 = np.array([LOCAL_JOINT_OF[part][chain[z]] for z in zone], dtype=np.uint16)
        j1 = np.array([LOCAL_JOINT_OF[part][chain[min(z + 1, N_BONES)]] for z in zone], dtype=np.uint16)
        joints4 = np.zeros((len(zone), 4), dtype=np.uint16)
        weights4 = np.zeros((len(zone), 4), dtype=np.float64)
        joints4[:, 0] = j0; joints4[:, 1] = j1
        weights4[:, 0] = w[:, 0]; weights4[:, 1] = w[:, 1]
        attr["JOINTS_0"] = accessor_us(joints4)
        attr["WEIGHTS_0"] = accessor_f(weights4, "VEC4", 34962)
    mi = len(g["meshes"])
    g["meshes"].append({"name": part, "primitives": [{"attributes": attr, "material": 0, "mode": 4}]})
    ni = len(g["nodes"])
    g["nodes"].append({"name": part, "mesh": mi})
    if b["skinned"]:
        g["nodes"][1]["children"].append(ni)
    else:
        g["nodes"][NODE[b["node"]]]["children"].append(ni)
    b["glb_node_index"] = ni


def global_bind_matrix(name):
    chain = []
    cur = name
    while cur != "base_link":
        chain.append(gltf_pos(RIG[cur]["translation"]))
        cur = RIG[cur]["parent"]
    M = np.eye(4)
    for c in reversed(chain):
        T = np.eye(4); T[:3, 3] = c
        M = M @ T
    return M


for part, chain in SKIN_CHAINS.items():
    ibm = [np.linalg.inv(global_bind_matrix(j)) for j in chain]
    acc = accessor_f(np.array(ibm, dtype="<f4"), "MAT4")
    sid = len(g["skins"])
    g["skins"].append({"joints": [NODE[j] for j in chain], "inverseBindMatrices": acc, "skeleton": 1})
    node_idx = next(b["glb_node_index"] for b in binding if b["part"] == part)
    g["nodes"][node_idx]["skin"] = sid

animation = {"name": "anim_config_v2_fixed", "samplers": [], "channels": []}
ta = accessor_f(times, "SCALAR")
ANIMATED_NODES = [n for n in RIG_ORDER if RIG[n]["fraction"] > 0]
for n in ANIMATED_NODES:
    nd = RIG[n]
    # The mesh and node translations are already in glTF (x,z,-y).
    # Rotations must use that same basis: body-y incidence becomes glTF -z.
    axis = np.asarray(gltf_dir(nd["axis"]), dtype=float)
    axis /= np.linalg.norm(axis)
    ang = np.array([a.get(nd["angle_key"], 0.0) * nd["fraction"] for a in poses])
    q = np.c_[np.sin(ang / 2)[:, None] * axis, np.cos(ang / 2)]
    qa = accessor_f(q, "VEC4")
    si = len(animation["samplers"])
    animation["samplers"].append({"input": ta, "output": qa, "interpolation": "LINEAR"})
    animation["channels"].append({"sampler": si, "target": {"node": NODE[n], "path": "rotation"}})
g["animations"] = [animation]
g["buffers"] = [{"byteLength": 0}]
js = json.dumps(g, separators=(",", ":"), ensure_ascii=True).encode()
while len(js) % 4:
    js += b" "
while len(blob) % 4:
    blob.extend(b"\0")
g["buffers"] = [{"byteLength": len(blob)}]
js = json.dumps(g, separators=(",", ":"), ensure_ascii=True).encode()
while len(js) % 4:
    js += b" "
with (OUT / "eaglex_anim_config_v2.glb").open("wb") as f:
    f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(blob)))
    f.write(struct.pack("<I4s", len(js), b"JSON")); f.write(js)
    f.write(struct.pack("<I4s", len(blob), b"BIN\0")); f.write(blob)
GLB_PATH = OUT / "eaglex_anim_config_v2.glb"

# ---------------------------------------------------------------- exported-model loader & measurement
raw = GLB_PATH.read_bytes()
magic, version, total = struct.unpack_from("<4sII", raw)
assert magic == b"glTF" and version == 2 and total == len(raw)
jlen, _ = struct.unpack_from("<I4s", raw, 12)
G = json.loads(raw[20:20 + jlen])
blen, _ = struct.unpack_from("<I4s", raw, 20 + jlen)
BIN = raw[28 + jlen:28 + jlen + blen]
TYPES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def acc_read(i):
    a = G["accessors"][i]; v = G["bufferViews"][a["bufferView"]]
    off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    if a["componentType"] == 5126:
        dt, sz = "<f4", 4
    elif a["componentType"] == 5123:
        dt, sz = "<u2", 2
    else:
        raise AssertionError(a["componentType"])
    cnt = a["count"] * TYPES[a["type"]]
    return np.frombuffer(BIN, dtype=dt, count=cnt, offset=off).reshape(a["count"], -1)


# joint-index validity on the exported model (the defect found in review)
joint_check = {"source_sha256": digest(GLB_PATH), "results": [], "passed": True}
for skin in G["skins"]:
    njoints = len(skin["joints"])
    for node in G["nodes"]:
        if node.get("skin") is not None and node.get("mesh") is not None:
            mesh = G["meshes"][node["mesh"]]
            attrs = mesh["primitives"][0]["attributes"]
            if "JOINTS_0" not in attrs:
                continue
            j = acc_read(attrs["JOINTS_0"]); w = acc_read(attrs["WEIGHTS_0"])
            active = (w > 0)
            bad = int((j[active] >= njoints).sum())
            entry = {"mesh": mesh["name"], "skin_joint_count": njoints,
                     "active_joint_index_range": [int(j[active].min()), int(j[active].max())],
                     "vertices_with_invalid_active_joint": bad,
                     "weight_rows_sum_to_one": bool(np.abs(w.sum(axis=1) - 1).max() < 1e-6)}
            entry["passed"] = bad == 0 and entry["active_joint_index_range"][1] < njoints
            joint_check["results"].append(entry)
            joint_check["passed"] &= entry["passed"]
assert joint_check["passed"], joint_check

# rebuild per-frame transforms from the exported file itself
node_parent = {}
for i, n in enumerate(G["nodes"]):
    for c in n.get("children", []):
        node_parent[c] = i
ANIM = G["animations"][0]
ROT_BY_NODE_TIME = {}
for ch in ANIM["channels"]:
    if ch["target"]["path"] != "rotation":
        continue
    smp = ANIM["samplers"][ch["sampler"]]
    t = acc_read(smp["input"]).ravel()
    q = acc_read(smp["output"])
    ROT_BY_NODE_TIME[ch["target"]["node"]] = (t, q)


def quat_to_mat(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def gl_node_globals(ti):
    out = {}

    def tf(i, R, T):
        n = G["nodes"][i]
        if i in ROT_BY_NODE_TIME:
            tarr, qarr = ROT_BY_NODE_TIME[i]
            k = int(np.argmin(np.abs(tarr - times[ti])))
            assert abs(tarr[k] - times[ti]) < 1e-3
            Rl = quat_to_mat(qarr[k])
        elif "rotation" in n:
            Rl = quat_to_mat(n["rotation"])
        else:
            Rl = np.eye(3)
        R2 = R @ Rl
        T2 = T + R @ np.array(n.get("translation", [0, 0, 0]), dtype=float)
        out[i] = (R2, T2)
        for c in n.get("children", []):
            tf(c, R2, T2)

    for rc in G["scenes"][0]["nodes"]:
        tf(rc, np.eye(3), np.zeros(3))
    return out


# Compare actual exported motion with the mechanical definition, for every frame.
# Quaternion normalization alone cannot detect an axis in the wrong coordinate basis.
COORD_B_TO_G = np.array([[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]])
motion_check = {"passed": True, "frames": len(times), "animated_nodes": len(ANIMATED_NODES),
                "max_rotation_matrix_error": 0., "max_translation_error_m": 0.,
                "incidence_axis_at_zero_body": [0., 1., 0.],
                "tail_yaw_axis_body": [0., 0., 1.], "tail_roll_axis_local_body": [1., 0., 0.]}
for ti, pose in enumerate(poses):
    mechanical = node_globals(pose)
    exported = gl_node_globals(ti)
    for name in RIG_ORDER:
        Rb, Tb = mechanical[name]
        Rg, Tg = exported[NODE[name]]
        re = float(np.abs(Rg - COORD_B_TO_G @ Rb @ COORD_B_TO_G.T).max())
        te = float(np.abs(Tg - COORD_B_TO_G @ Tb).max())
        motion_check["max_rotation_matrix_error"] = max(motion_check["max_rotation_matrix_error"], re)
        motion_check["max_translation_error_m"] = max(motion_check["max_translation_error_m"], te)
        assert re < 1e-6 and te < 1e-6, (name, ti, re, te)
print("exported motion basis: all frames matched mechanical rotations", flush=True)


MESH_DATA = {}
for node in G["nodes"]:
    if node.get("mesh") is None:
        continue
    mesh = G["meshes"][node["mesh"]]
    attrs = mesh["primitives"][0]["attributes"]
    MESH_DATA[mesh["name"]] = {"node_index": G["nodes"].index(node),
                               "pos": acc_read(attrs["POSITION"]).astype(np.float64),
                               "uv": acc_read(attrs["TEXCOORD_0"]).astype(np.float64),
                               "normals": acc_read(attrs["NORMAL"]).astype(np.float64),
                               "joints": acc_read(attrs["JOINTS_0"]).astype(np.int64) if "JOINTS_0" in attrs else None,
                               "weights": acc_read(attrs["WEIGHTS_0"]).astype(np.float64) if "WEIGHTS_0" in attrs else None,
                               "skin": node.get("skin")}


def gl_part_world(part, ti):
    d = MESH_DATA[part]
    GG = gl_node_globals(ti)
    v = d["pos"]
    if d["skin"] is not None:
        skin = G["skins"][d["skin"]]
        ibm = acc_read(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
        out = np.zeros_like(v)
        j, w = d["joints"], d["weights"]
        used = np.abs(w) > 0
        for slot in range(4):
            m = used[:, slot]
            if not m.any():
                continue
            for jidx in np.unique(j[m, slot]):
                sel = m & (j[:, slot] == jidx)
                gn = skin["joints"][jidx]
                R, T = GG[gn]
                M = np.eye(4); M[:3, :3] = R; M[:3, 3] = T
                Mi = M @ ibm[jidx]
                hv = np.c_[v[sel], np.ones(sel.sum())] @ Mi.T
                out[sel] += w[sel, slot][:, None] * hv[:, :3]
        return out, GG[d["node_index"]][0]
    gn = node_parent[d["node_index"]]
    R, T = GG[gn]
    return v @ R.T + T, R


# per-frame seam measurement ON THE EXPORTED MODEL
def bind_translation(node_index):
    """Sum of node translations up the chain at bind (rotations identity except the root, which has none)."""
    t = np.zeros(3)
    i = node_index
    while i in node_parent:
        t = t + np.array(G["nodes"][i].get("translation", [0, 0, 0]), dtype=float)
        i = node_parent[i]
    return t


def pair_seams(part_a, part_b):
    da, db = MESH_DATA[part_a], MESH_DATA[part_b]
    pa = da["pos"] + bind_translation(node_parent[da["node_index"]]) if da["skin"] is None else da["pos"]
    pb = db["pos"] + bind_translation(node_parent[db["node_index"]]) if db["skin"] is None else db["pos"]
    keymap = {}
    for j, q in enumerate(pb):
        keymap.setdefault(tuple(np.round(q * 1e6)), j)
    ia, ib = [], []
    for i, p in enumerate(pa):
        j = keymap.get(tuple(np.round(p * 1e6)))
        if j is not None:
            ia.append(i); ib.append(j)
    ia, ib = np.array(ia, dtype=np.int64), np.array(ib, dtype=np.int64)
    assert len(ia) > 50, (part_a, part_b, len(ia))
    return ia, ib


GL_SEAMS = {"wing_main_tip_L": ("eagle_wing_main_L", "eagle_wing_tip_L"),
            "wing_main_tip_R": ("eagle_wing_main_R", "eagle_wing_tip_R"),
            "tail_flat_central_L": ("eagle_tail_fin", "eagle_tail_flat_L"),
            "tail_flat_central_R": ("eagle_tail_fin", "eagle_tail_flat_R")}
glb_seam_report = {}
pair_cache = {}
for name, (pa, pb) in GL_SEAMS.items():
    ia, ib = pair_seams(pa, pb)
    pair_cache[name] = (pa, pb, ia, ib)
    worst = {"gap_m": 0.0, "time_s": None, "mode": None, "rest_xyz_m": None}
    for ti in range(len(times)):
        wa, _ = gl_part_world(pa, ti)
        wb, _ = gl_part_world(pb, ti)
        gaps = np.linalg.norm(wa[ia] - wb[ib], axis=1)
        k = int(gaps.argmax())
        if gaps[k] > worst["gap_m"]:
            worst = {"gap_m": float(gaps[k]), "time_s": float(times[ti]), "mode": modes[ti],
                     "rest_xyz_m": MESH_DATA[pa]["pos"][ia[k]].tolist()}
    glb_seam_report[name] = {"matched_points": int(len(ia)), "max_gap_m": worst["gap_m"],
                             "peak": worst, "threshold_m": SEAM_THRESHOLD_M,
                             "passed": worst["gap_m"] < SEAM_THRESHOLD_M}
    assert worst["gap_m"] < SEAM_THRESHOLD_M, (name, worst)

# per-frame edge-length measurement ON THE EXPORTED MODEL (stretch/shrink split)
glb_edge_report = {}
for part in ["eagle_wing_tip_L", "eagle_wing_tip_R", "eagle_tail_flat_L", "eagle_tail_flat_R"]:
    v0 = MESH_DATA[part]["pos"]
    n = len(v0) // 3
    idx = np.arange(len(v0)).reshape(-1, 3)
    pairs = []
    for c0, c1 in [(0, 1), (1, 2), (2, 0)]:
        pairs.append(np.stack([idx[:, c0], idx[:, c1]], axis=1))
    pairs = np.concatenate(pairs)
    L0 = np.linalg.norm(v0[pairs[:, 0]] - v0[pairs[:, 1]], axis=1)
    if part.startswith("eagle_wing_tip"):
        sign, plane, width = (1 if part.endswith("_L") else -1), WING_SPLIT_Y, TRANS_W_TIP
    else:
        sign, plane, width = (1 if part.endswith("_L") else -1), TAIL_SPLIT_Y, TRANS_W_TAIL
    # gltf frame: y_mech = -z_gltf, so the span coordinate is -v0[:,2]
    s = np.maximum(sign * (-v0[:, 2]) - plane, 0.0)
    in_zone = np.minimum(s[pairs[:, 0]], s[pairs[:, 1]]) < width
    stats = {"outside": {"max_stretch": 0.0, "max_shrink": 0.0},
             "inside": {"max_stretch": 0.0, "max_shrink": 0.0},
             "inside_buckets": {"lt_1mm": 0.0, "1_to_3mm": 0.0, "3_to_10mm": 0.0},
             "worst_inside": None, "worst_outside": None}
    worst_ti = None
    for ti in range(len(times)):
        wv, _ = gl_part_world(part, ti)
        L1 = np.linalg.norm(wv[pairs[:, 0]] - wv[pairs[:, 1]], axis=1)
        rel = (L1 - L0) / np.maximum(L0, 1e-15)
        arel = np.abs(rel)
        for zone, mask in [("outside", ~in_zone), ("inside", in_zone)]:
            if mask.any():
                stats[zone]["max_stretch"] = max(stats[zone]["max_stretch"], float(rel[mask].max()))
                stats[zone]["max_shrink"] = min(stats[zone]["max_shrink"], float(rel[mask].min()))
        if in_zone.any() and arel[in_zone].max() > (stats["worst_inside"] or {"rel": 0})["rel"]:
            k = int(arel[in_zone].argmax())
            gk = np.where(in_zone)[0][k]
            worst_ti = ti
            stats["worst_inside"] = {"rel": float(arel[gk]), "time_s": float(times[ti]), "mode": modes[ti],
                                     "edge_len_m": float(L0[gk]),
                                     "rest_vertex_a": v0[pairs[gk, 0]].tolist(), "rest_vertex_b": v0[pairs[gk, 1]].tolist()}
    # length-bucketed in-zone maxima at the worst frame
    if worst_ti is not None:
        wv, _ = gl_part_world(part, worst_ti)
        L1 = np.linalg.norm(wv[pairs[:, 0]] - wv[pairs[:, 1]], axis=1)
        arel = np.abs((L1 - L0) / np.maximum(L0, 1e-15))
        for bname, lo, hi in [("lt_1mm", 0, 1e-3), ("1_to_3mm", 1e-3, 3e-3), ("3_to_10mm", 3e-3, 1e-2)]:
            m = in_zone & (L0 >= lo) & (L0 < hi)
            stats["inside_buckets"][bname] = float(arel[m].max()) if m.any() else 0.0
    outside_ok = stats["outside"]["max_stretch"] < EDGE_THRESHOLD and abs(stats["outside"]["max_shrink"]) < EDGE_THRESHOLD
    outside_cnt = int((~in_zone).sum())
    assert outside_cnt > 0, part
    glb_edge_report[part] = {**stats, "edges_total": int(len(pairs)),
                             "edges_outside_transition": outside_cnt,
                             "edges_in_transition": int(in_zone.sum()),
                             "threshold_outside": EDGE_THRESHOLD, "passed_outside": outside_ok}
    assert outside_ok, (part, stats["outside"])

# wing-root containment (fixed implementation) on exported model at zero & max flap
body_world_zero, _ = gl_part_world("eagle_body_and_nose", 0)
# gltf xy is (x_mech, z_mech); the containment plane must be mech (x, y) = (x_gltf, -z_gltf)
bw = body_world_zero.reshape(-1, 3, 3)[:, :, [0, 2]].copy()
bw[:, :, 1] *= -1
body_sil = unary_union([Polygon(t) for t in bw
                        if abs((t[1, 0] - t[0, 0]) * (t[2, 1] - t[0, 1]) - (t[1, 1] - t[0, 1]) * (t[2, 0] - t[0, 0])) > 1e-14])
body_buf = body_sil.buffer(2e-3)
root_containment = {}
flap_max_ti = int(np.argmax([abs(a["flap_L"]) for a in poses]))
for side in ["L", "R"]:
    res = {}
    part = f"eagle_wing_main_{side}"
    d = MESH_DATA[part]
    bind_world = d["pos"] + bind_translation(node_parent[d["node_index"]])
    # only OPEN-BOUNDARY corners near the root (the actual connection edge)
    from collections import Counter as _C
    uniqb, invb = np.unique(np.round(bind_world * 1e6), axis=0, return_inverse=True)
    cntb = _C()
    for f3 in invb.reshape(-1, 3):
        fl = list(f3)
        for a2, b2 in zip(fl, fl[1:] + fl[:1]):
            cntb[(min(a2, b2), max(a2, b2))] += 1
    open_ids = sorted({i for e, c in cntb.items() if c == 1 for i in e})
    open_pts = uniqb[open_ids] * 1e-6
    band = open_pts[np.abs(open_pts[:, 2]) < 0.065]
    keymap = {tuple(np.round(p * 1e6)): i for i, p in enumerate(bind_world)}
    for label, ti in [("zero_pose", 0), ("max_flap", flap_max_ti)]:
        wv, _ = gl_part_world(part, ti)
        idxs = [keymap[tuple(np.round(p * 1e6))] for p in band if tuple(np.round(p * 1e6)) in keymap]
        pts = np.stack([wv[idxs, 0], -wv[idxs, 2]], axis=-1)
        inside = int(sum(body_buf.contains(Point(p)) for p in pts))
        res[label] = f"{inside}/{len(pts)}"
    root_containment[side] = res

# ---------------------------------------------------------------- render FROM THE EXPORTED GLB
tex = np.asarray(Image.open(TEXTURE).convert("RGB"))
print("seam and edge recalculation complete; rendering corrected exported model", flush=True)
CAM_MECH = {"main": np.array([-.55, -1.15, 1.00]), "rear": np.array([-.95, -.35, .45]),
            "front": np.array([.85, -1.25, 1.05]), "rear_high": np.array([-.9, -.75, .8])}


def render_from_glb(ti, cam_key, label=None, zoom=870, cx=480, cy=340, seam_part=None):
    camera = CAM_MECH[cam_key] / np.linalg.norm(CAM_MECH[cam_key])
    right = np.cross([0, 0, 1], camera); right /= np.linalg.norm(right)
    up = np.cross(camera, right)
    project = np.array([right, up, camera]).T
    light = np.array([.3, -.5, .9]); light /= np.linalg.norm(light)
    positions, cols = [], []
    for part in MESH_DATA:
        wv, R = gl_part_world(part, ti)
        # gltf frame back to mechanism frame for the fixed cameras
        mech = np.stack([wv[:, 0], -wv[:, 2], wv[:, 1]], axis=-1)
        d = MESH_DATA[part]
        tri_uv = d["uv"].reshape(-1, 3, 2).mean(axis=1)
        tpx = np.clip((tri_uv[:, 0] % 1) * (tex.shape[1] - 1), 0, tex.shape[1] - 1).astype(int)
        tpy = np.clip((1 - tri_uv[:, 1] % 1) * (tex.shape[0] - 1), 0, tex.shape[0] - 1).astype(int)
        col = tex[tpx, tpy].astype(float)
        nn = d["normals"].reshape(-1, 3, 3).mean(axis=1) @ R.T
        nn = nn @ COORD_B_TO_G  # lighting is expressed in the mechanical frame
        shade = .52 + .48 * np.abs(nn @ light)
        cam = mech.reshape(-1, 3, 3) @ project
        positions.append(cam); cols.append(np.clip(col * shade[:, None], 0, 255).astype(np.uint8))
    v = np.concatenate(positions); col = np.concatenate(cols)
    xy = v[:, :, :2].copy() * zoom
    xy[:, :, 0] += cx; xy[:, :, 1] = cy - xy[:, :, 1]
    ar = np.abs((xy[:, 1, 0] - xy[:, 0, 0]) * (xy[:, 2, 1] - xy[:, 0, 1]) -
                (xy[:, 1, 1] - xy[:, 0, 1]) * (xy[:, 2, 0] - xy[:, 0, 0]))
    order = np.argsort(v[:, :, 2].mean(axis=1))
    im = Image.new("RGB", (960, 650), (240, 243, 246)); draw = ImageDraw.Draw(im)
    for k in order:
        if ar[k] > .22:
            draw.polygon([tuple(pt) for pt in xy[k]], fill=tuple(col[k]))
    draw.rectangle((0, 0, 960, 58), fill=(26, 40, 57))
    draw.text((20, 16), "Eagle X anim-v2 (from exported glb)  |  " + (label or modes[ti]) + f"  |  {times[ti]:.2f}s", font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20), fill="white")
    draw.text((20, 614), "rendered by loading the exported glb; same skinning as the interactive model", font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 15), fill=(40, 55, 70))
    return im


import imageio.v2 as imageio
frames = []
VIDEO_FPS = (len(times) - 1) / duration / 2  # every other sample; preserve command time
for ti in range(0, len(times) - 1, 2):
    im = render_from_glb(ti, "main")
    frames.append(im)
    if ti % 60 == 0:
        print(f"rendered overview frame {ti}/{len(times)-1}", flush=True)
    if ti in [0, 60, 120, 180, 240, 270, 300]:
        im.save(OUT / f"preview_{ti:03d}.png")
with imageio.get_writer(str(OUT / "eaglex_animv2_preview.mp4"), fps=VIDEO_FPS, codec="libx264", macro_block_size=2) as writer:
    for f in frames:
        writer.append_data(np.asarray(f))
CLIPS = {"tip_dihedral_up": ("TIP_DIHEDRAL_UP", "rear_high"), "tip_downfold": ("TIP_DOWNFOLD", "rear_high"),
         "flap_plus_fold": ("FLAP_PLUS_FOLD", "front"), "tail_yaw_whole": ("TAIL_YAW_WHOLE", "rear"),
         "tail_roll_whole": ("TAIL_ROLL_WHOLE", "rear"), "tail_open_linked": ("TAIL_OPEN_LINKED_3DOF", "rear"),
         "tail_all": ("TAIL_ALL_TWO_WHOLE_PLUS_OPEN", "rear"),
         "open_independent_compare": ("OPEN_INDEPENDENT_4DOF_COMPARE", "rear"),
         "flap_inherit": ("FLAP_INHERIT", "front"), "twist_inherit": ("TWIST_INHERIT", "front")}
clipdir = OUT / "clips"; clipdir.mkdir(exist_ok=True)
for clip, (m, cam) in CLIPS.items():
    tis = [i for i, mm in enumerate(modes) if mm == m]
    with imageio.get_writer(str(clipdir / f"{clip}.mp4"), fps=VIDEO_FPS, codec="libx264", macro_block_size=2) as writer:
        for i in tis[::2]:
            writer.append_data(np.asarray(render_from_glb(i, cam)))
    render_from_glb(tis[len(tis) // 4], cam).save(clipdir / f"{clip}_mid.png")
    print(f"rendered corrected clip: {clip}", flush=True)

# closeups: wing-root axis (zero pose + max twist), seams at max fold / max open
twist_max_ti = int(np.argmax([abs(a["twist_L"]) for a in poses]))
fold_max_ti = int(np.argmax([abs(a["tip_fold_L"]) for a in poses]))
open_max_ti = int(np.argmax([abs(a["open_L"]) for a in poses]))
render_from_glb(0, "front", label="wing root zero pose").save(OUT / "closeup_wing_root_zero.png")
render_from_glb(twist_max_ti, "front", label="wing root at max twist").save(OUT / "closeup_wing_root_twist.png")
render_from_glb(fold_max_ti, "rear_high", label="tip seam at max fold").save(OUT / "closeup_tip_seam_fold.png")
render_from_glb(open_max_ti, "rear", label="flat seams at max open").save(OUT / "closeup_flat_seams_open.png")

# ---------------------------------------------------------------- figures
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# fig: seam comparison old vs new export measurements
old = json.loads(OLD_GAPS.read_text(encoding="utf-8"))
NAME_MAP = {"wing_main_tip_L": "wing_main_tip_L", "wing_main_tip_R": "wing_main_tip_R",
            "tail_side_central_L": "tail_flat_central_L", "tail_side_central_R": "tail_flat_central_R"}
fig, ax = plt.subplots(figsize=(10, 5.2), dpi=160)
names = [r["seam"] for r in old["results"]]
old_max = [r["animated_gap_max_m"] * 1000 for r in old["results"]]
new_max = [glb_seam_report[NAME_MAP[n]]["max_gap_m"] * 1e6 for n in names]
x = np.arange(len(names))
ax.bar(x - .18, old_max, .34, label="old delivery: max gap (mm)", color="#be3941")
ax.bar(x + .18, new_max, .34, label="anim-v2 export: max gap (um, threshold 10)", color="#2a9d8f")
ax.set_xticks(x); ax.set_xticklabels([n.replace("_", "\n") for n in names], fontsize=8)
ax.set_yscale("symlog", linthresh=1e-2)
ax.axhline(10, color="#2a9d8f", ls=":", lw=1.2, label="10 um threshold")
ax.set(ylabel="max seam gap (mm / um, symlog)", title="Seam gaps measured ON THE EXPORTED glb (all 301 frames)\nold data from old_preview_seam_gaps.json")
ax.legend(fontsize=8); ax.grid(alpha=.2, axis="y")
fig.tight_layout(); fig.savefig(OUT / "seam_gap_comparison.png"); plt.close(fig)

# fig: wing root axis data (mechanism frame top view)
fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
for part, c in [("eagle_wing_main_L", "#168fa5"), ("eagle_wing_tip_L", "#ed983a")]:
    tri = meshes[part][:, :, :3] + np.array(next(b["zero_origin_xyz_m"] for b in binding if b["part"] == part))
    shape = unary_union([Polygon(t) for t in tri[:, :, :2] if abs((t[1, 0] - t[0, 0]) * (t[2, 1] - t[0, 1]) - (t[1, 1] - t[0, 1]) * (t[2, 0] - t[0, 0])) > 1e-14])
    for poly in ([shape] if shape.geom_type == "Polygon" else shape.geoms):
        xy = np.array(poly.exterior.coords)
        ax.fill(xy[:, 1], xy[:, 0], color=c, alpha=.6)
wa = wing_axes["L"]
p = wa["root_pivot_xyz_m"]
ax.axvline(0.06, color="#555", ls="--", lw=1, label="joint station y=0.06")
ax.plot([0.06, 0.06], [wa["joint_station_te_x_m"], wa["joint_station_le_x_m"]], "k-", lw=2)
ax.scatter([0.06], [p[0]], c="#e76f51", marker="o", s=90, zorder=6,
           label="twist axis candidate: %.1f%% chord from LE (x=%.4f)" % (TWIST_AXIS_FRACTION * 100, p[0]))
ax.scatter([0.06], [wa["quarter_chord_reference_x_m"]], c="#276b93", marker="x", s=80,
           label="quarter-chord reference x=%.4f (not a confirmed hinge)" % wa["quarter_chord_reference_x_m"])
ax.scatter([0.06], [0.0], c="#888", marker="s", s=60, label="frozen URDF twist axis x=0 (89% chord; superseded here)")
ax.annotate("LE x=%.4f" % wa["joint_station_le_x_m"], xy=(0.06, wa["joint_station_le_x_m"]), xytext=(0.075, wa["joint_station_le_x_m"]), fontsize=8)
ax.annotate("TE x=%.4f" % wa["joint_station_te_x_m"], xy=(0.06, wa["joint_station_te_x_m"]), xytext=(0.075, wa["joint_station_te_x_m"] - 0.012), fontsize=8)
ax.set(xlabel="Span y (m)", ylabel="x toward nose (m)",
       title="Wing root axis fix: zero-pose geometry unchanged, twist-axis candidate moved nose-side\nURDF flapconn connecting-rod reference at (0, ±0.06, 0); real hinge data missing")
ax.axis("equal"); ax.grid(alpha=.2); ax.legend(fontsize=8, loc="lower left")
fig.tight_layout(); fig.savefig(OUT / "wing_root_axis_fix.png"); plt.close(fig)

# fig: edge stretch/shrink split
fig, ax = plt.subplots(figsize=(10, 5.2), dpi=160)
parts = list(glb_edge_report)
outside_s = [glb_edge_report[p]["outside"]["max_stretch"] * 100 for p in parts]
outside_sh = [glb_edge_report[p]["outside"]["max_shrink"] * 100 for p in parts]
inside_s = [glb_edge_report[p]["inside"]["max_stretch"] * 100 for p in parts]
inside_sh = [glb_edge_report[p]["inside"]["max_shrink"] * 100 for p in parts]
x = np.arange(len(parts))
ax.bar(x - .27, outside_s, .24, label="outside zone: max stretch %", color="#2a9d8f")
ax.bar(x - .27, [abs(v) for v in outside_sh], .24, bottom=[0] * len(parts), alpha=0, color="none")
ax.plot(x, [1] * len(parts), "r--", lw=1, label="1% threshold (outside zone)")
ax.bar(x, inside_s, .24, label="inside transition: max stretch %", color="#e9c46a")
ax.bar(x + .24, [abs(v) for v in inside_sh], .24, label="inside transition: max shrink % (abs)", color="#e76f51")
ax.set_xticks(x); ax.set_xticklabels([p.replace("eagle_", "").replace("_", "\n") for p in parts], fontsize=8)
ax.set(ylabel="percent", title="Edge-length change measured ON THE EXPORTED glb: stretch and shrink split\noutside transition zone vs inside (bend) zone")
ax.legend(fontsize=8); ax.grid(alpha=.2, axis="y")
fig.tight_layout(); fig.savefig(OUT / "edge_stretch_shrink.png"); plt.close(fig)

# ---------------------------------------------------------------- report
sources = [URDF, CLARIFY, APPROVAL, REVIEW, JOINT_CHECK, OLD_GAPS, OLD_MANIFEST, TEXTURE,
           ROOT / "eagle_shell/prep_parts.py", SOURCE_PARTS / "parts_meta.json",
           ROOT / "assets/eagleX_raw_50k/cfd068548a51f523e76e53b2c9c2f559.glb",
           ROOT / "assets/eagleX_raw_50k/b50134432fcdc17a4a98826ee389d9bc.zip",
           ROOT / "modules/aero/kinematics.py", ROOT / "config/sim_config.py",
           HERE / "build_l8_i201_animv2.py"]
sources += [SOURCE_PARTS / (n + ".obj") for n in ["head", "body_tail", "wing_right", "wing_left"]]
report = {
    "schema_version": 2, "date": "2026-10-13", "item": "I-201",
    "version_name": "eagle_x_animation_config_v2 (manual motion-axis correction 2026-10-13)",
    "simulator_used": False,
    "owner_approval_reference": str(APPROVAL),
    "provenance": [{"path": str(p), "sha256": digest(p)} for p in sources if p.exists()],
    "fixes_this_round": {
        "animation_axis_basis": "All animation quaternions converted from body (x,y,z) to glTF (x,z,-y); all-frame exported rotations and translations agree with the mechanical definition",
        "inverse_bind_matrix_layout": "glTF column-major storage; renderer reads the same standard matrix layout",
        "export_joint_numbering": "JOINTS_0 now stores SKIN-LOCAL indices 0..5; the skin joints array keeps global node indices; verified by re-reading the exported glb (glb_joint_index_check below)",
        "single_deformation_path": "rendering, measurement and the interactive glb all use the same chained-bone LBS; the preview videos are rendered BY LOADING the exported glb",
        "wing_root_axis": "twist-axis candidate moved from x=0 (89% chord) to 20% chord from the LE at the joint station, nose-side; zero-pose world geometry and body connection unchanged",
        "burr_cleanup": "zero-area degenerate triangles removed per part (counts below); audit of mirror deviation, open boundaries, renderer subpixel suppression documented; no structure invented",
        "transition_zones_widened": {"tip": TRANS_W_TIP, "flat": TRANS_W_TAIL,
                                       "bend_rig": f"{N_BONES} chained bend bones per skinned part, LBS"}},
    "wing_axes": {s: {**{k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in wing_axes[s].items()},
                       "flapconn_urdf_reference_m": urdf_joints["flap_" + s]["origin_xyz_m"],
                       "axis_is_candidate": True,
                       "missing": "实物铰位、连杆尺寸、限位与舵机资料未提供"} for s in ["L", "R"]},
    "tail_config": {"hierarchy": "base_link -> tailYawWhole(psi) -> tailRollWhole(phi) -> tail_carrier -> {fin, openL chain, openR chain}",
                     "axes_convention": "psi about fixed body z; phi about local x after yaw; R = Rz(psi) @ Rx(phi)",
                     "carrier_origin_xyz_m": carrier_origin.tolist(),
                     "approved": "机主 2026-10-13 认可尾部动作与共同父节点；不自动确认实物材料、质量、舵机、限位或开合联动"},
    "degrees_of_freedom": {"independent": ["tail_psi", "tail_phi", "open_L", "open_R"], "count_independent": 4,
                            "linked_candidate": "open_R = -open_L (3 dof); 联动未确认，接口保存两角与显式耦合"},
    "demo_amplitudes": DEMO,
    "glb_joint_index_check": joint_check,
    "exported_motion_axis_verification": motion_check,
    "bindings": [{k: b.get(k) for k in ["part", "node", "source", "skinned", "zero_origin_xyz_m", "part_file", "skin_note"]} for b in binding],
    "exported_model_seam_verification": glb_seam_report,
    "exported_model_edge_verification": glb_edge_report,
    "exported_model_root_containment": root_containment,
    "burr_audit": burr_audit,
    "consistency": "视频由导出 glb 载入渲染；量测与交互模型同一蒙皮实现；不再存在两套变形函数",
    "preview": {"duration_s": duration, "rendered_from": "eaglex_anim_config_v2.glb",
                 "segments": ["0-2拍动继承", "2-4翼根迎角调节（翼尖跟随）", "4-6翼尖上反", "6-8翼尖下折", "8-10拍动叠加折叠",
                               "10-12整尾单独偏航", "12-14整尾单独滚转", "14-16联动开合（三自由度候选）",
                               "16-18两整体角叠加开合", "18-20独立开合对照（四自由度候选）"],
                 "physics_drive_tracking_verified": False, "production_model_updated": False},
    "frozen_data": "冻结质量、惯量、气动参数与二百一十五克物理机构保持原值",
    "limitations": ["过渡区内的伸长缩短为连续弯曲代价，已分列并给峰值边与帧；区外小于百分之一",
                     "实物铰位、尺寸、限位、舵机数量与质量无资料，留空不猜测",
                     "蒙皮为数字蒙皮定义，不代表已测得的真实材料",
                     "重要独立复验须由未参与方案与施工的人承担"],
    "rollback": {"production_touched": False, "method": "删除本目录即回退；旧版本 l8_i201_20261013 保留为历史诊断"}}
(OUT / "anim_config_v2_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"output": str(OUT),
                   "glb_sha256": joint_check["source_sha256"],
                   "joint_check_passed": joint_check["passed"],
                   "export_seams": {k: f"{v['max_gap_m']:.2e}" for k, v in glb_seam_report.items()},
                   "export_edges_outside": {k: (f"{v['outside']['max_stretch']:.2e}", f"{v['outside']['max_shrink']:.2e}") for k, v in glb_edge_report.items()},
                   "export_edges_inside": {k: (f"{v['inside']['max_stretch']:.4f}", f"{v['inside']['max_shrink']:.4f}") for k, v in glb_edge_report.items()},
                   "root_containment": root_containment,
                   "burr": {k: v for k, v in burr_audit.items() if isinstance(v, dict)}},
                  ensure_ascii=False, indent=1))
