"""Offline asset processing only: no simulator, no physics or production writes.

零仿真器声明：本脚本不启动任何仿真器进程。I-201 接续施工（机主构型澄清
2026-10-13）：新动画构型版本——翼尖仅绕缝合处弦向铰线上反下折（三节弯曲
骨骼链，接缝连续）；整尾整体偏航 z 与整体滚转 x 带动垂尾与两平尾；平尾
相对连杆开合；三处接缝全程连续。零位保留源外形连接与尺度（撤回翼面后移）。
冻结物理数据保持原值；本版本单独命名并记录与旧机构差异。
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
from shapely.geometry import Polygon
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
OUT = HERE / "output"
ANIM = OUT / "animation_config_20261013"
ANIM.mkdir(parents=True, exist_ok=True)
PARTS_DIR = ANIM / "parts"
PARTS_DIR.mkdir(exist_ok=True)
SOURCE_PARTS = ROOT / "eagle_shell/parts"
URDF = ROOT / "assets/flapper/fwav_15dof_from_group.urdf"
TEXTURE = SOURCE_PARTS / "basecolor.png"
CLARIFY = ROOT / "logs/jane_dispatch_20261013/机主构型澄清_翼尖与整尾接缝.md"
OLD_GAPS = ROOT / "logs/jane_dispatch_20261013/old_preview_seam_gaps.json"
OLD_MEASURE = ROOT / "logs/jane_dispatch_20261013/measure_old_preview_seams.py"

mf = json.loads((ROOT / "eagle_shell/out/bind_manifest.json").read_text(encoding="utf-8"))
scale = float(mf["scale"])
translate = np.array(mf["translate_global"], dtype=float) * np.array([-1., -1., 1.])
asset_rotation = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=float)


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


def surface_area(tris):
    return float(np.linalg.norm(np.cross(tris[:, 1, :3] - tris[:, 0, :3],
                                         tris[:, 2, :3] - tris[:, 0, :3]), axis=1).sum() / 2)


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


def projection(tris):
    p = tris[:, :, :2]
    area = np.abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) -
                  (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0])) / 2
    return unary_union([Polygon(t) for t, a in zip(p, area) if a > 1e-14])


FOLD_PLANE = 0.14
TAIL_SPLIT = 0.012
WING_TRANSITION = 0.030
TAIL_TRANSITION = 0.020
SEAM_THRESHOLD_M = 1e-5       # 10 um numerical acceptance for seam continuity
EDGE_THRESHOLD = 0.01         # 1% max edge-length change outside transition zones

# ---------------------------------------------------------------- load meshes
meshes = {"eagle_body_and_nose": read_obj(SOURCE_PARTS / "body_tail.obj")}
wing_full = {"L": read_obj(SOURCE_PARTS / "wing_left.obj"),
             "R": read_obj(SOURCE_PARTS / "wing_right.obj")}
tail_full = read_obj(SOURCE_PARTS / "head.obj")

# Frozen URDF reference only; the animation config does not inherit its axes.
robot = ET.parse(URDF).getroot()
urdf_summary = {}
for e in robot.findall("joint"):
    urdf_summary[e.get("name")] = {"type": e.get("type"),
                                   "origin": e.find("origin").get("xyz"),
                                   "axis": e.find("axis").get("xyz")}

# ---------------------------------------------------------------- helpers
def exact_weld_boundary(tris):
    from collections import Counter
    coords = tris[:, :, :3].reshape(-1, 3)
    uniq, inv = np.unique(coords, axis=0, return_inverse=True)
    cnt = Counter()
    for f in inv.reshape(-1, 3):
        fl = list(f)
        for a, b in zip(fl, fl[1:] + fl[:1]):
            cnt[(min(a, b), max(a, b))] += 1
    open_ids = sorted({i for e, c in cnt.items() if c == 1 for i in e})
    return coords, uniq, inv, open_ids, cnt


def fit_hinge(contour_pts):
    """Straight hinge line through a contour: centroid + principal direction."""
    c = contour_pts.mean(axis=0)
    d = contour_pts - c
    _, vec = np.linalg.eigh(np.cov(d.T))
    axis = vec[:, -1]
    if axis[0] < 0:
        axis = -axis
    axis /= np.linalg.norm(axis)
    resid = np.linalg.norm(np.cross(d, axis), axis=1)
    return c, axis, float(resid.max()), float(resid.mean())


def rot_axis_angle(axis, angle):
    a = np.array(axis, dtype=float); a /= np.linalg.norm(a)
    x, y, z = a; c = math.cos(angle); s = math.sin(angle); k = 1 - c
    return np.array([[c + x * x * k, x * y * k - z * s, x * z * k + y * s],
                     [y * x * k + z * s, c + y * y * k, y * z * k - x * s],
                     [z * x * k - y * s, z * y * k + x * s, c + z * z * k]])


def rigid(R, t, v):
    return v @ R.T + t


# ---------------------------------------------------------------- wing build
checks = {}
wing_data = {}
for side, sign in [("L", 1), ("R", -1)]:
    tri = wing_full[side]
    coords, uniq, inv, open_ids, _ = exact_weld_boundary(tri)
    boundary = uniq[open_ids]
    root_band = boundary[np.abs(boundary[:, 1]) < 0.06]
    root_pivot = root_band.mean(axis=0)
    main = clip(tri, FOLD_PLANE, sign, True)
    tip = clip(tri, FOLD_PLANE, sign, False)
    meshes[f"eagle_wing_main_{side}"] = main
    meshes[f"eagle_wing_tip_{side}"] = tip
    # Seam contour on the tip side (vertices exactly on the cut plane).
    seam_contour = tip[:, :, :3].reshape(-1, 3)
    seam_contour = seam_contour[np.abs(seam_contour[:, 1] - sign * FOLD_PLANE) < 1e-9]
    hinge_c, hinge_a, resid_max, resid_mean = fit_hinge(seam_contour)
    wing_data[side] = {"root_pivot": root_pivot, "hinge_center": hinge_c,
                       "hinge_axis": hinge_a, "hinge_resid_max_m": resid_max,
                       "hinge_resid_mean_m": resid_mean, "sign": sign}

# ---------------------------------------------------------------- tail build
tail_mesh_parts = {}
rudder = clip(clip(tail_full, TAIL_SPLIT, 1.0, True), -TAIL_SPLIT, 1.0, False)
flatL = clip(tail_full, TAIL_SPLIT, 1.0, False)
flatR = clip(tail_full, -TAIL_SPLIT, 1.0, True)
tail_mesh_parts["eagle_tail_fin"] = rudder
tail_mesh_parts["eagle_tail_flat_L"] = flatL
tail_mesh_parts["eagle_tail_flat_R"] = flatR
meshes.update(tail_mesh_parts)

coords, uniq, inv, open_ids, _ = exact_weld_boundary(tail_full)
boundary = uniq[open_ids]
root_thr = np.quantile(uniq[:, 0], 0.90)
tail_root = boundary[boundary[:, 0] > root_thr].mean(axis=0)

tail_hinges = {}
for part, sgn in [("eagle_tail_flat_L", 1), ("eagle_tail_flat_R", -1)]:
    contour = tail_mesh_parts[part][:, :, :3].reshape(-1, 3)
    contour = contour[np.abs(contour[:, 1] - sgn * TAIL_SPLIT) < 1e-9]
    c, a, rmax, rmean = fit_hinge(contour)
    tail_hinges[part] = {"center": c, "axis": a, "resid_max_m": rmax,
                         "resid_mean_m": rmean, "sign": sgn}

# ---------------------------------------------------------------- rig
# Node list with parent, translation (parent-local), rotation axis.
# Angles dict keys: flap_L/R, twist_L/R, fold_L/R, tail_yaw, tail_roll,
# spread_L, spread_R.
rig = {}


def add_node(name, parent, translation, axis):
    rig[name] = {"parent": parent, "translation": np.array(translation, dtype=float),
                 "axis": np.array(axis, dtype=float)}


for side in ["L", "R"]:
    wd = wing_data[side]
    p = wd["root_pivot"]
    add_node(f"wingroot_{side}", "base_link", p, [1, 0, 0])          # flap: chordwise through root pivot
    add_node(f"wingtwist_{side}", f"wingroot_{side}", [0, 0, 0], [0, 1, 0])  # twist: spanwise, same pivot
    h = wd["hinge_center"] - p
    d = np.array([0.0, wd["sign"] * WING_TRANSITION / 3, 0.0])
    for k in range(3):
        add_node(f"wingfold_{side}_{k+1}", f"wingtwist_{side}" if k == 0 else f"wingfold_{side}_{k}",
                 h if k == 0 else d, wd["hinge_axis"])
add_node("tail_yaw", "base_link", tail_root, [0, 0, 1])
add_node("tail_roll", "tail_yaw", [0, 0, 0], [1, 0, 0])
add_node("tail_carrier", "tail_roll", [0, 0, 0], [1, 0, 0])
for part, side in [("eagle_tail_flat_L", "L"), ("eagle_tail_flat_R", "R")]:
    hh = tail_hinges[part]
    h = hh["center"] - tail_root
    d = np.array([0.0, hh["sign"] * TAIL_TRANSITION / 3, 0.0])
    for k in range(3):
        add_node(f"spread_{side}_{k+1}", "tail_carrier" if k == 0 else f"spread_{side}_{k}",
                 h if k == 0 else d, hh["axis"])

ANGLE_OF_NODE = {}
for side in ["L", "R"]:
    ANGLE_OF_NODE[f"wingroot_{side}"] = (f"flap_{side}", 1.0)
    ANGLE_OF_NODE[f"wingtwist_{side}"] = (f"twist_{side}", 1.0)
    for k in range(3):
        ANGLE_OF_NODE[f"wingfold_{side}_{k+1}"] = (f"fold_{side}", 1.0 / 3)
ANGLE_OF_NODE["tail_yaw"] = ("tail_yaw", 1.0)
ANGLE_OF_NODE["tail_roll"] = ("tail_roll", 1.0)
for side in ["L", "R"]:
    for k in range(3):
        ANGLE_OF_NODE[f"spread_{side}_{k+1}"] = (f"spread_{side}", 1.0 / 3)

RIGID_BIND = {"eagle_body_and_nose": "base_link",
              "eagle_wing_main_L": "wingtwist_L", "eagle_wing_main_R": "wingtwist_R",
              "eagle_tail_fin": "tail_carrier"}


def node_globals(angles):
    out = {"base_link": (np.eye(3), np.zeros(3))}
    def rec(name):
        if name in out:
            return out[name]
        nd = rig[name]
        pr, pt = rec(nd["parent"])
        key, frac = ANGLE_OF_NODE[name]
        a = angles.get(key, 0.0) * frac
        r = pr @ rot_axis_angle(nd["axis"], a)
        t = pt + pr @ nd["translation"]
        out[name] = (r, t)
        return out[name]
    for n in rig:
        rec(n)
    return out


BIND_GLOBALS = node_globals({})


def skin_setup(part):
    """Return (joints, weights per corner) for a skinned part, or None."""
    if part.startswith("eagle_wing_tip_"):
        side = part[-1]
        parent_node, s0, wtr = f"wingtwist_{side}", FOLD_PLANE, WING_TRANSITION
        sgn = wing_data[side]["sign"]
    elif part.startswith("eagle_tail_flat_"):
        side = part[-1]
        parent_node, s0, wtr = "tail_carrier", TAIL_SPLIT, TAIL_TRANSITION
        sgn = tail_hinges[part]["sign"]
    else:
        return None
    chain = [f"wingfold_{side}_{k+1}" for k in range(3)] if part.startswith("eagle_wing_tip_") \
        else [f"spread_{side}_{k+1}" for k in range(3)]
    joints = [parent_node] + chain
    v = meshes[part][:, :, :3].reshape(-1, 3)
    f = np.clip((sgn * v[:, 1] - s0) / wtr, 0.0, 1.0)
    w = np.zeros((len(v), 4))
    jj = np.zeros((len(v), 4), dtype=np.uint16)
    zone = np.minimum((f * 3).astype(int), 2)
    frac = np.clip(f * 3 - zone, 0.0, 1.0)
    lo_idx = np.array([0, 1, 2])[zone]      # parent,b1 | b1,b2 | b2,b3
    hi_idx = np.array([1, 2, 3])[zone]
    for zi in range(3):
        m = zone == zi
        jj[m, 0], jj[m, 1] = lo_idx[zi], hi_idx[zi]
        w[m, 0], w[m, 1] = 1.0 - frac[m], frac[m]
    return joints, jj, w, f


SKINS = {p: skin_setup(p) for p in meshes if skin_setup(p)}


def part_world_corners(part, angles):
    """World positions of all mesh corners under the animation config."""
    G = node_globals(angles)
    v = meshes[part][:, :, :3].reshape(-1, 3)
    if part in SKINS:
        joints, jj, w, _ = SKINS[part]
        out = np.zeros_like(v)
        for slot in range(4):
            m = w[:, slot] > 0
            if not m.any():
                continue
            for ji, jname in enumerate(joints):
                sel = m & (jj[:, slot] == ji)
                if not sel.any():
                    continue
                R, t = G[jname]
                bR, bt = BIND_GLOBALS[jname]
                bind = v[sel] - bt
                bind = bind @ bR.T
                out[sel] += w[sel, slot][:, None] * (bind @ R.T + t)
        return out
    R, t = G[RIGID_BIND[part]]
    bR, bt = BIND_GLOBALS[RIGID_BIND[part]]
    bind = (v - bt) @ bR.T
    return bind @ R.T + t


# ---------------------------------------------------------------- seam pairs
def seam_pairs(part_a, part_b, plane_y):
    va = meshes[part_a][:, :, :3].reshape(-1, 3)
    vb = meshes[part_b][:, :, :3].reshape(-1, 3)
    sa = va[np.abs(va[:, 1] - plane_y) < 1e-9]
    sb = vb[np.abs(vb[:, 1] - plane_y) < 1e-9]
    key = {tuple(np.round(p, 9)): i for i, p in enumerate(sb)}
    ia, ib = [], []
    for i, p in enumerate(sa):
        k = tuple(np.round(p, 9))
        if k in key:
            ia.append(i); ib.append(key[k])
    return np.array(ia), np.array(ib), len(sa), len(sb)


SEAMS = {"wing_main_tip_L": ("eagle_wing_main_L", "eagle_wing_tip_L", +FOLD_PLANE),
         "wing_main_tip_R": ("eagle_wing_main_R", "eagle_wing_tip_R", -FOLD_PLANE),
         "tail_flat_central_L": ("eagle_tail_fin", "eagle_tail_flat_L", +TAIL_SPLIT),
         "tail_flat_central_R": ("eagle_tail_fin", "eagle_tail_flat_R", -TAIL_SPLIT)}

# ---------------------------------------------------------------- timeline
spec = importlib.util.spec_from_file_location("eaglex_existing_kinematics", ROOT / "modules/aero/kinematics.py")
kin = importlib.util.module_from_spec(spec); spec.loader.exec_module(kin)
from config import sim_config as cfg
wk = kin.WingKinematics(frequency=cfg.WING_DRIVE_CONFIG["frequency"],
                        amplitude_deg=cfg.WING_DRIVE_CONFIG["amplitude_deg"],
                        pitch_mode=cfg.WING_DRIVE_CONFIG["pitch_mode"],
                        alpha_set_deg=cfg.WING_DRIVE_CONFIG["alpha_set_deg"])
COORDS = ["flap_L", "twist_L", "fold_L", "flap_R", "twist_R", "fold_R",
          "tail_yaw", "tail_roll", "spread_L", "spread_R"]
DEMO = {"note": "预览角幅仅为演示取值，不沿用旧轴限位冒充新机构实物限位",
        "fold_demo_rad": 0.6, "flap_demo_rad": math.radians(30),
        "twist_demo_rad": math.radians(25), "tail_yaw_demo_rad": math.radians(20),
        "tail_roll_demo_rad": math.radians(15), "spread_demo_rad": 0.44}
duration = 24.0
times = np.linspace(0, duration, 361)
poses, sections = [], []
for t in times:
    t = float(t)
    a = {k: 0.0 for k in COORDS}
    if t < 2:
        mode = "TIP_UP"
        v = DEMO["fold_demo_rad"] * math.sin(math.pi * t / 2)
        a.update(fold_L=v, fold_R=-v)   # symmetric dihedral: opposite signs, documented
    elif t < 4:
        mode = "TIP_DOWN"
        v = DEMO["fold_demo_rad"] * math.sin(math.pi * (t - 2) / 2)
        a.update(fold_L=-v, fold_R=v)
    elif t < 6:
        mode = "TWIST"
        v = DEMO["twist_demo_rad"] * math.sin(math.pi * (t - 4) / 2)
        a.update(twist_L=v, twist_R=v)
    elif t < 8:
        mode = "FLAP_PLUS_FOLD"
        v = DEMO["flap_demo_rad"] * math.sin(2 * math.pi * (t - 6))
        u = DEMO["fold_demo_rad"] * .5 * (1 - math.cos(2 * math.pi * (t - 6)))
        a.update(flap_L=v, flap_R=-v, fold_L=u, fold_R=-u)
    elif t < 10:
        mode = "TAIL_YAW_ALONE"
        a.update(tail_yaw=DEMO["tail_yaw_demo_rad"] * math.sin(math.pi * (t - 8) / 2))
    elif t < 12:
        mode = "TAIL_ROLL_ALONE"
        a.update(tail_roll=DEMO["tail_roll_demo_rad"] * math.sin(math.pi * (t - 10) / 2))
    elif t < 14:
        mode = "TAIL_SPREAD_COUPLED_3DOF_CANDIDATE"
        v = DEMO["spread_demo_rad"] * math.sin(math.pi * (t - 12) / 2)
        a.update(spread_L=v, spread_R=-v)   # coupled command: one value drives both sides
    elif t < 16:
        mode = "TAIL_YAW_ROLL_SPREAD"
        v = DEMO["spread_demo_rad"] * math.sin(math.pi * (t - 14) / 2)
        a.update(tail_yaw=DEMO["tail_yaw_demo_rad"] * .6 * math.sin(math.pi * (t - 14) / 2),
                 tail_roll=DEMO["tail_roll_demo_rad"] * .6 * math.sin(math.pi * (t - 14) / 2),
                 spread_L=v, spread_R=-v)
    elif t < 18:
        mode = "SPREAD_INDEPENDENT_4DOF_CONTRAST"
        v = DEMO["spread_demo_rad"] * math.sin(math.pi * (t - 16) / 2)
        a.update(spread_L=v, spread_R=-v * math.sin(math.pi * (t - 16)) ** 2)
    else:
        mode = "COMBINED"
        ev = wk.evaluate(t - 18)
        envelope = min(1, (t - 18) / .25, (24 - t) / .25)
        fold = DEMO["fold_demo_rad"] * .5 * (1 + math.sin(wk.omega * (t - 18) + 2.2)) * envelope
        a.update(flap_L=ev["phi"] * envelope, flap_R=-ev["phi"] * envelope,
                 twist_L=ev["alpha"] * envelope, twist_R=ev["alpha"] * envelope,
                 fold_L=fold, fold_R=-fold,
                 tail_yaw=DEMO["tail_yaw_demo_rad"] * .5 * math.sin(math.pi * (t - 18) / 6) * envelope,
                 tail_roll=DEMO["tail_roll_demo_rad"] * .5 * math.sin(2 * math.pi * (t - 18) / 6) * envelope,
                 spread_L=DEMO["spread_demo_rad"] * .5 * math.sin(2 * math.pi * (t - 18) / 6) * envelope,
                 spread_R=-DEMO["spread_demo_rad"] * .5 * math.sin(2 * math.pi * (t - 18) / 6) * envelope)
    poses.append(a); sections.append(mode)

# ---------------------------------------------------------------- measurements
seam_results = {}
for name, (pa, pb, py) in SEAMS.items():
    ia, ib, na, nb = seam_pairs(pa, pb, py)
    assert len(ia) > 50, (name, len(ia))
    max_gap, peak = 0.0, None
    for fi, a in enumerate(poses):
        wa = part_world_corners(pa, a)[ia]
        wb = part_world_corners(pb, a)[ib]
        d = np.linalg.norm(wa - wb, axis=1)
        i = int(d.argmax())
        if d[i] > max_gap:
            max_gap = float(d[i])
            peak = {"time_s": float(times[fi]), "mode": sections[fi],
                    "gap_m": float(d[i]),
                    "rest_position_xyz_m": meshes[pa][:, :, :3].reshape(-1, 3)[ia[i]].tolist()}
    zero_gap = float(np.linalg.norm(part_world_corners(pa, {})[ia] - part_world_corners(pb, {})[ib], axis=1).max())
    seam_results[name] = {"matched_points": int(len(ia)),
                          "seam_contour_vertices_a": na, "seam_contour_vertices_b": nb,
                          "zero_gap_max_m": zero_gap, "animated_gap_max_m": max_gap,
                          "peak": peak, "threshold_m": SEAM_THRESHOLD_M,
                          "pass": max_gap <= SEAM_THRESHOLD_M}
    assert seam_results[name]["pass"], (name, max_gap)
checks["seam_continuity"] = seam_results

# Edge-length change outside transition zones.
edge_results = {}
for part in ["eagle_wing_tip_L", "eagle_wing_tip_R", "eagle_tail_flat_L", "eagle_tail_flat_R"]:
    v0 = meshes[part][:, :, :3].reshape(-1, 3)
    faces = np.arange(len(v0)).reshape(-1, 3)
    joints, jj, w, f = SKINS[part]
    edges = set()
    for fa in faces:
        fl = list(fa)
        for x, y in zip(fl, fl[1:] + fl[:1]):
            edges.add((min(x, y), max(x, y)))
    edges = np.array(sorted(edges))
    fmax_edge = np.maximum(f[edges[:, 0]], f[edges[:, 1]])
    fmin_edge = np.minimum(f[edges[:, 0]], f[edges[:, 1]])
    outside = (fmin_edge >= 1.0 - 1e-9) | (fmax_edge <= 0.0 + 1e-9)
    inside = ~outside
    l0 = np.linalg.norm(v0[edges[:, 0]] - v0[edges[:, 1]], axis=1)
    worst_out, worst_in = 0.0, 0.0
    peak_frame = None
    for fi, a in enumerate(poses):
        wv = part_world_corners(part, a)
        l1 = np.linalg.norm(wv[edges[:, 0]] - wv[edges[:, 1]], axis=1)
        rel = np.abs(l1 / np.maximum(l0, 1e-15) - 1)
        ro = float(rel[outside].max()) if outside.any() else 0.0
        ri = float(rel[inside].max()) if inside.any() else 0.0
        if ro > worst_out:
            worst_out, peak_frame = ro, fi
        worst_in = max(worst_in, ri)
    edge_results[part] = {"edges_total": int(len(edges)),
                          "edges_outside_transition": int(outside.sum()),
                          "edges_in_transition": int(inside.sum()),
                          "max_abs_relative_change_outside": worst_out,
                          "max_abs_relative_change_in_transition": worst_in,
                          "peak_frame_outside": peak_frame,
                          "outside_threshold": EDGE_THRESHOLD,
                          "pass": worst_out <= EDGE_THRESHOLD}
    assert edge_results[part]["pass"], (part, worst_out)
checks["edge_length_change"] = edge_results

# Rigid parts must show exactly zero relative edge change.
for part in ["eagle_wing_main_L", "eagle_wing_main_R", "eagle_tail_fin", "eagle_body_and_nose"]:
    v0 = meshes[part][:, :, :3].reshape(-1, 3)
    edges = set()
    for fa in np.arange(len(v0)).reshape(-1, 3):
        fl = list(fa)
        for x, y in zip(fl, fl[1:] + fl[:1]):
            edges.add((min(x, y), max(x, y)))
    edges = np.array(sorted(edges))
    l0 = np.linalg.norm(v0[edges[:, 0]] - v0[edges[:, 1]], axis=1)
    worst = 0.0
    for a in poses[::10]:
        wv = part_world_corners(part, a)
        l1 = np.linalg.norm(wv[edges[:, 0]] - wv[edges[:, 1]], axis=1)
        worst = max(worst, float(np.abs(l1 / np.maximum(l0, 1e-15) - 1).max()))
    assert worst < 1e-9, (part, worst)
checks["rigid_parts_edge_change"] = {"sampled_frames": 37, "max_relative_change": worst}

# Original-mesh hole registration (open boundary edges that are not cut seams).
holes = {}
for part in list(meshes):
    coords, uniq, inv, open_ids, cnt = exact_weld_boundary(meshes[part])
    from collections import Counter
    hole_edges = 0
    for (a, b), c in cnt.items():
        if c == 1:
            pa, pb = uniq[a], uniq[b]
            on_cut = any(abs(pa[1] - s) < 1e-9 and abs(pb[1] - s) < 1e-9
                         for s in (FOLD_PLANE, -FOLD_PLANE, TAIL_SPLIT, -TAIL_SPLIT))
            if not on_cut:
                hole_edges += 1
    holes[part] = {"non_cut_open_boundary_edges": hole_edges}
checks["original_mesh_open_boundaries_not_from_cuts"] = holes

# Wing-root containment restored (source zero pose, no shift).
from shapely.geometry import Point
body_sil = projection(meshes["eagle_body_and_nose"])
body_buf = body_sil.buffer(2e-3)
contain = {}
for side in ["L", "R"]:
    coords, uniq, inv, open_ids, _ = exact_weld_boundary(wing_full[side])
    boundary = uniq[open_ids]
    inner = boundary[np.abs(boundary[:, 1]) < 0.065]
    inside = int(sum(body_buf.contains(Point(p[:2])) for p in inner))
    contain[side] = {"checked": int(len(inner)), "inside": inside,
                     "ratio": float(inside / len(inner))}
checks["wing_root_containment_source_zero_pose"] = contain

# ---------------------------------------------------------------- part OBJs
for part, tri in meshes.items():
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
(PARTS_DIR / "basecolor.png").write_bytes(TEXTURE.read_bytes())
(PARTS_DIR / "basecolor.mtl").write_text("newmtl eagle\nKd 1 1 1\nmap_Kd basecolor.png\n", encoding="ascii")

# ---------------------------------------------------------------- GLB with skins
g = {"asset": {"version": "2.0", "generator": "L8 animation-config preview (owner clarification 2026-10-13)"},
     "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [], "meshes": [], "skins": [],
     "bufferViews": [], "accessors": [],
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


def accessor(array, type_, target=None):
    a = np.asarray(array, dtype="<f4")
    d = {"bufferView": view(a.tobytes(), target), "componentType": 5126, "count": len(a), "type": type_}
    if a.ndim == 2 and len(a):
        d["min"] = a.min(axis=0).reshape(-1).tolist(); d["max"] = a.max(axis=0).reshape(-1).tolist()
    g["accessors"].append(d)
    return len(g["accessors"]) - 1


def accessor_u16(array):
    a = np.asarray(array, dtype="<u2")
    d = {"bufferView": view(a.tobytes(), 34963), "componentType": 5123, "count": len(a), "type": "VEC4"}
    g["accessors"].append(d)
    return len(g["accessors"]) - 1


g["images"].append({"bufferView": view(TEXTURE.read_bytes()), "mimeType": "image/png"})
g["nodes"].append({"name": "z-up to y-up", "rotation": [-math.sqrt(.5), 0, 0, math.sqrt(.5)], "children": [1]})
g["nodes"].append({"name": "base_link", "children": []})
node_idx = {"base_link": 1}
order = []
while len(order) < len(rig):
    for n, nd in rig.items():
        if n in node_idx:
            continue
        if nd["parent"] in node_idx:
            idx = len(g["nodes"]); node_idx[n] = idx
            key, frac = ANGLE_OF_NODE[n]
            g["nodes"].append({"name": n, "translation": nd["translation"].tolist(),
                               "rotation": [0, 0, 0, 1], "children": [],
                               "extras": {"angle_key": key, "angle_fraction": frac,
                                          "axis": nd["axis"].tolist(),
                                          "frame": "zero-pose world; parent-local translation"}})
            g["nodes"][node_idx[nd["parent"]]]["children"].append(idx)
            order.append(n)

# map rig node name -> joint index arrays per skin
joint_lists = {}
for part, sk in SKINS.items():
    joint_lists[part] = [node_idx[j] for j in sk[0]]
for part, tri in meshes.items():
    p = tri[:, :, :3].reshape(-1, 3)
    uv = tri[:, :, 3:5].reshape(-1, 2).copy(); uv[:, 1] = 1 - uv[:, 1]
    nrm = tri[:, :, 5:8].reshape(-1, 3)
    nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-15)
    attrs = {"POSITION": accessor(p, "VEC3", 34962), "NORMAL": accessor(nrm, "VEC3", 34962),
             "TEXCOORD_0": accessor(uv, "VEC2", 34962)}
    if part in SKINS:
        joints, jj, w, _ = SKINS[part]
        attrs["JOINTS_0"] = accessor_u16(jj)
        attrs["WEIGHTS_0"] = accessor(w, "VEC4", 34962)
    mi = len(g["meshes"])
    g["meshes"].append({"name": part, "primitives": [{"attributes": attrs, "material": 0, "mode": 4}]})
    ni = len(g["nodes"])
    if part in SKINS:
        jidx = joint_lists[part]
        ibm = []
        for jn in SKINS[part][0]:
            R, t = BIND_GLOBALS[jn]
            M = np.eye(4); M[:3, :3] = R; M[:3, 3] = t
            ibm.append(np.linalg.inv(M))
        ibm_acc = len(g["bufferViews"])
        acc = accessor(np.array(ibm, dtype="<f4"), "MAT4")
        sid = len(g["skins"])
        g["skins"].append({"joints": jidx, "inverseBindMatrices": acc})
        g["nodes"].append({"name": part, "mesh": mi, "skin": sid})
        g["nodes"][0]["children"].append(ni)  # skinned meshes attach to root; transforms via skin
    else:
        g["nodes"].append({"name": part, "mesh": mi})
        g["nodes"][node_idx[RIGID_BIND[part]]]["children"].append(ni)

animation = {"name": "animation_config_20261013", "samplers": [], "channels": []}
ta = accessor(times, "SCALAR")
for n in order:
    key, frac = ANGLE_OF_NODE[n]
    axis = rig[n]["axis"] / np.linalg.norm(rig[n]["axis"])
    ang = np.array([a.get(key, 0.0) * frac for a in poses])
    q = np.c_[np.sin(ang / 2)[:, None] * axis, np.cos(ang / 2)]
    qa = accessor(q, "VEC4")
    si = len(animation["samplers"])
    animation["samplers"].append({"input": ta, "output": qa, "interpolation": "LINEAR"})
    animation["channels"].append({"sampler": si, "target": {"node": node_idx[n], "path": "rotation"}})
g["animations"] = [animation]
g["buffers"] = [{"byteLength": len(blob)}]
js = json.dumps(g, separators=(",", ":"), ensure_ascii=True).encode()
while len(js) % 4:
    js += b" "
while len(blob) % 4:
    blob.extend(b"\0")
with (ANIM / "eaglex_animation_config.glb").open("wb") as fh:
    fh.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(blob)))
    fh.write(struct.pack("<I4s", len(js), b"JSON")); fh.write(js)
    fh.write(struct.pack("<I4s", len(blob), b"BIN\0")); fh.write(blob)

with (ANIM / "joint_commands.csv").open("w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh); w.writerow(["time_s", "mode"] + COORDS)
    for t, mode, a in zip(times, sections, poses):
        w.writerow([t, mode] + [a[n] for n in COORDS])

# ---------------------------------------------------------------- renderer
tex = np.asarray(Image.open(TEXTURE).convert("RGB"))
prepared = []
for part, tri in meshes.items():
    uv = tri[:, :, 3:5].mean(axis=1)
    px = np.clip((uv[:, 0] % 1) * (tex.shape[1] - 1), 0, tex.shape[1] - 1).astype(int)
    py = np.clip((1 - uv[:, 1] % 1) * (tex.shape[0] - 1), 0, tex.shape[0] - 1).astype(int)
    prepared.append((part, tri[:, :, 5:8].mean(axis=1), tex[py, px].astype(float)))
try:
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22)
    small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 17)
except OSError:
    font = small = ImageFont.load_default()
FOOT = "animation config 2026-10-13: chordwise tip fold + whole-tail yaw/roll + flat-tail spread  |  seams continuous"


def render_frame(index, cam=np.array([-.55, -1.15, 1.00]), zoom=870, cx=480, cy=340, label=None):
    camera = cam / np.linalg.norm(cam)
    right = np.cross([0, 0, 1], camera); right /= np.linalg.norm(right)
    up = np.cross(camera, right)
    project = np.array([right, up, camera]).T
    light = np.array([.3, -.5, .9]); light /= np.linalg.norm(light)
    a = poses[index]
    G = node_globals(a)
    positions, cols = [], []
    for part, n, col in prepared:
        wv = part_world_corners(part, a)
        R = G[SKINS[part][0][0]][0] if part in SKINS else G[RIGID_BIND[part]][0]
        cam2 = wv @ project
        nn = n @ R.T
        shade = .52 + .48 * np.abs(nn @ light)
        positions.append(cam2); cols.append(np.clip(col * shade[:, None], 0, 255).astype(np.uint8))
    v = np.concatenate(positions); col = np.concatenate(cols)
    xy = v[:, :, :2].copy() * zoom
    xy[:, :, 0] += cx; xy[:, :, 1] = cy - xy[:, :, 1]
    ar = np.abs((xy[:, 1, 0] - xy[:, 0, 0]) * (xy[:, 2, 1] - xy[:, 0, 1]) -
                (xy[:, 1, 1] - xy[:, 0, 1]) * (xy[:, 2, 0] - xy[:, 0, 0]))
    order2 = np.argsort(v[:, :, 2].mean(axis=1))
    im = Image.new("RGB", (960, 650), (240, 243, 246)); draw = ImageDraw.Draw(im)
    for k in order2:
        if ar[k] > .22:
            draw.polygon([tuple(pt) for pt in xy[k]], fill=tuple(col[k]))
    draw.rectangle((0, 0, 960, 58), fill=(26, 40, 57))
    draw.text((20, 16), "Eagle X anim  |  " + (label or sections[index]) + f"  |  {times[index]:.2f}s", font=font, fill="white")
    draw.text((20, 614), FOOT, font=small, fill=(40, 55, 70))
    return im


import imageio.v2 as imageio
frames = []
for index in range(0, len(times) - 1, 2):
    im = render_frame(index)
    frames.append(im)
    if index in [0, 60, 120, 180, 240, 300, 358]:
        im.save(ANIM / f"preview_{index:03d}.png")
with imageio.get_writer(str(ANIM / "eaglex_animation_config_preview.mp4"), fps=15, codec="libx264", macro_block_size=2) as writer:
    for f in frames:
        writer.append_data(np.asarray(f))
CLIP_MODES = {"tip_up": "TIP_UP", "tip_down": "TIP_DOWN", "twist": "TWIST",
              "flap_plus_fold": "FLAP_PLUS_FOLD", "tail_yaw_alone": "TAIL_YAW_ALONE",
              "tail_roll_alone": "TAIL_ROLL_ALONE",
              "tail_spread_coupled": "TAIL_SPREAD_COUPLED_3DOF_CANDIDATE",
              "tail_yaw_roll_spread": "TAIL_YAW_ROLL_SPREAD",
              "spread_independent_contrast": "SPREAD_INDEPENDENT_4DOF_CONTRAST"}
clipdir = ANIM / "clips"; clipdir.mkdir(exist_ok=True)
for clip, mode in CLIP_MODES.items():
    idxs = [i for i, m in enumerate(sections) if m == mode]
    with imageio.get_writer(str(clipdir / f"{clip}.mp4"), fps=15, codec="libx264", macro_block_size=2) as writer:
        for i in idxs[::2]:
            writer.append_data(np.asarray(render_frame(i)))
    render_frame(idxs[len(idxs) // 4]).save(clipdir / f"{clip}_mid.png")

# Seam closeups at the frames of largest motion (fold peak ~1.5s, spread peak ~13s).
fold_peak = int(np.argmax([abs(a["fold_L"]) for a in poses]))
spread_peak = int(np.argmax([abs(a["spread_L"]) for a in poses]))
for nm, fi, cam, zoom, cx, cy in [
        ("seam_closeup_wing_tip_R", fold_peak, np.array([.2, -1.3, .8]), 5200, 620, 320),
        ("seam_closeup_wing_tip_L", fold_peak, np.array([.2, 1.3, .8]), 5200, 340, 320),
        ("seam_closeup_tail_flats", spread_peak, np.array([-.95, -.35, .55]), 4200, 480, 300)]:
    render_frame(fi, cam, zoom, cx, cy, label=f"seam closeup @ {times[fi]:.1f}s").save(ANIM / f"{nm}.png")

# ---------------------------------------------------------------- report
sources = [URDF, CLARIFY, OLD_GAPS, OLD_MEASURE, TEXTURE,
           ROOT / "eagle_shell/out/bind_manifest.json",
           ROOT / "eagle_shell/prep_parts.py", SOURCE_PARTS / "parts_meta.json",
           ROOT / "assets/eagleX_raw_50k/cfd068548a51f523e76e53b2c9c2f559.glb",
           ROOT / "assets/eagleX_raw_50k/b50134432fcdc17a4a98826ee389d9bc.zip",
           Path("G:/Playground/PartField/exp_results/clustering/eagleX_knn/cluster_out/eagleX_0_04.npy"),
           SOURCE_PARTS / "head.obj", SOURCE_PARTS / "body_tail.obj",
           SOURCE_PARTS / "wing_right.obj", SOURCE_PARTS / "wing_left.obj",
           HERE / "build_l8_i201_anim.py"]
report = {
    "schema_version": 1, "date": "2026-10-13", "item": "I-201",
    "name": "eaglex 动画构型版本 2026-10-13（机主澄清：翼尖与整尾接缝）",
    "simulator_used": False,
    "provenance": [{"path": str(p), "sha256": digest(p)} for p in sources],
    "zero_pose": {"note": "保留源外形的零位连接与尺度；撤回此前约零点零九九米的翼面后移（该后移曾把翼根埋入率从百分之百降到四到五成）",
                  "wing_root_containment": checks["wing_root_containment_source_zero_pose"]},
    "wing_config": {s: {"root_pivot_xyz_m": wing_data[s]["root_pivot"].tolist(),
                        "flap_axis": [1, 0, 0], "twist_axis": [0, 1, 0],
                        "flap_twist_pivot_source": "原模型翼根开放边界（|y|<0.06）顶点质心；视觉候选，非已确认实物铰点",
                        "fold": {"hinge_center_xyz_m": wing_data[s]["hinge_center"].tolist(),
                                 "hinge_axis_unit": wing_data[s]["hinge_axis"].tolist(),
                                 "hinge_axis_source": "翼尖切缝轮廓（y=±0.14）主方向直线拟合；整条铰线，非后缘单点",
                                 "hinge_fit_residual_max_m": wing_data[s]["hinge_resid_max_m"],
                                 "motion": "绕弦向铰线上反下折；法向旋转已从本构型撤除",
                                 "transition_zone_span_m": WING_TRANSITION,
                                 "rig": "三节弯曲骨骼链各转三分之一角，接缝顶点权重为零全程不动",
                                 "inherits": "拍动与扭转经父节点链继承"}}} for s in ["L", "R"]},
    "tail_config": {
        "hierarchy": "base_link -> tail_yaw(z) -> tail_roll(x,随动轴) -> tail_carrier -> { fin(固定), flat_L(spread链), flat_R(spread链) }",
        "axis_convention": "零位机体轴；内在序列先偏航后滚转（滚转轴随偏航转动），等价于固定轴先滚转后偏航的外旋序列",
        "whole_motion": "整体偏航与整体滚转均带动垂尾与两平尾；不设置垂尾独立偏航",
        "root_pivot_xyz_m": tail_root.tolist(),
        "root_pivot_source": "风扇根部开放边界（x>90分位）顶点质心；视觉候选",
        "spread": {p: {"hinge_center_xyz_m": tail_hinges[p]["center"].tolist(),
                       "hinge_axis_unit": tail_hinges[p]["axis"].tolist(),
                       "hinge_fit_residual_max_m": tail_hinges[p]["resid_max_m"],
                       "motion": "相对连杆开合（绕连接边弦向铰线改变两平尾夹角）",
                       "transition_zone_span_m": TAIL_TRANSITION,
                       "sign_convention": "铰线方向统一取 +x 分量为正；对称开合使用左右相反符号（fold_L=+θ 配 fold_R=-θ 形成对称上反；spread 同理），不能用角度字符串相同判断对称"} for p in ["eagle_tail_flat_L", "eagle_tail_flat_R"]}},
    "degrees_of_freedom": {"coordinates": ["tail_yaw", "tail_roll", "spread_L", "spread_R"],
                            "independent_count": 4, "coupled_count": 3,
                            "coupling_status": "真实联动关系未确认；接口保存两角加显式耦合设置",
                            "candidates": {"coupled_3dof": "预览 12-14 秒段，一个命令值驱动两侧（联动候选，未确认）",
                                            "independent_4dof": "预览 16-18 秒段（独立对照，未确认）"},
                            "note": "两片零件或两条骨骼不自动等于两个独立自由度；同命令播放不证明单自由度机构"},
    "differences_vs_frozen_urdf": {
        "note": "冻结物理数据保持原值；本动画构型版本单独命名，未写入任何物理配置",
        "fold": "URDF fold 绕面法向 z（铰点 (-0.07,±0.08,0) 相对内翼，绝对站位 y=±0.14，弦向约百分之七十五处靠后缘）；本构型绕缝合处弦向铰线",
        "tail": "URDF 为 tail_yaw 下挂左右 y 轴俯仰；本构型为整体偏航加整体滚转加平尾开合",
        "limits": "本版本演示角幅不是实物限位；实物限位未确认"},
    "demo_amplitudes": DEMO,
    "preview": {"duration_s": duration,
                "segments": ["0-2s翼尖上反", "2-4s翼尖下折", "4-6s扭转", "6-8s拍动叠加折叠",
                             "8-10s整尾单独偏航", "10-12s整尾单独滚转", "12-14s联动开合（三自由度候选）",
                             "14-16s两整体动作叠加开合", "16-18s独立开合（四自由度对照）", "18-24s组合"],
                "mode": "运动学角度驱动，固定机身；零仿真器",
                "physics_drive_tracking_verified": False, "production_model_updated": False},
    "seam_diagnostics_old_version": {
        "source": str(OLD_GAPS),
        "summary": "旧版交付命令下翼尖接缝最大分离约六十五点五与六十五点四毫米，平尾与中央片约五十点八与五十一点二毫米（视觉模型单位，对应点分离非表面最短距离）；作为旧版未满足构型的诊断依据"},
    "verification": checks,
    "limitations": ["全部铰点与根枢轴为原模型连接结构导出的视觉候选；实物轴位、尺寸、限位、舵机数量与质量未知，留空待资料",
                    "开合联动关系未确认；三、四自由度均为标注候选",
                    "平尾开合按绕连接边弦向铰线改变夹角实现（书页式开合）；该读法为对机主说明的动画构型解释，若实物为水平扇形展开需修订",
                    "过渡区内边长有小幅收缩（弯曲骨骼线性混合），数值已另列；过渡区外小于百分之一",
                    "原网格既有孔洞与非切缝开放边界已单独登记，不计入新增间隙",
                    "重要独立复验须由未参与方案与施工的人承担"],
    "rollback": {"production_touched": False,
                  "method": "本版本位于 output/animation_config_20261013/，删除该子目录即回退；旧版本保留为历史诊断，不进入最终页面"}}
(ANIM / "animation_config_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"anim_output": str(ANIM),
                  "seam_max_gaps_m": {k: v["animated_gap_max_m"] for k, v in seam_results.items()},
                  "edge_outside_max": {k: v["max_abs_relative_change_outside"] for k, v in edge_results.items()},
                  "edge_inside_max": {k: v["max_abs_relative_change_in_transition"] for k, v in edge_results.items()},
                  "wing_root_containment": checks["wing_root_containment_source_zero_pose"]},
                 ensure_ascii=False, indent=1))
