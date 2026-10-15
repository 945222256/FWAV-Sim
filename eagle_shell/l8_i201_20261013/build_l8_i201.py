"""Offline asset processing only: no simulator, no physics or production writes.

零仿真器声明：本脚本不启动任何仿真器进程。I-201 施工：按 2026-10-13 翼根与
尾部审计重定位视觉翼轴、把原模型尾部风扇划分为左右水平作动面与竖向方向舵
三个独立角度候选，并输出几何数据、绑定清单、动作预览与图证。冻结质量、
惯量、气动参数与现行二百一十五克物理机构（URDF）保持原值，不写入任何
生产模型。
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
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
OUT = HERE / "output"
OUT.mkdir(parents=True, exist_ok=True)
PARTS = OUT / "parts"
PARTS.mkdir(exist_ok=True)
SOURCE_PARTS = ROOT / "eagle_shell/parts"
URDF = ROOT / "assets/flapper/fwav_15dof_from_group.urdf"
OLD_MANIFEST = ROOT / "eagle_shell/out/bind_manifest.json"
TEXTURE = SOURCE_PARTS / "basecolor.png"
AUDIT = ROOT / "eagle_shell/jane_preview_20261013_tail/output/wing_root_tail_audit_20261013.json"
AUDIT_FIG = ROOT / "eagle_shell/jane_preview_20261013_tail/output/wing_root_axis_diagnosis.png"

mf = json.loads(OLD_MANIFEST.read_text(encoding="utf-8"))
scale = float(mf["scale"])
translate = np.array(mf["translate_global"], dtype=float) * np.array([-1., -1., 1.])
asset_rotation = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=float)

# Frozen mechanism references (read from the URDF, never written).
robot = ET.parse(URDF).getroot()
urdf_joints = {}
origins = {"base_link": np.zeros(3)}
children = {}
for e in robot.findall("joint"):
    origin = e.find("origin")
    xyz = np.array(list(map(float, origin.get("xyz", "0 0 0").split())))
    rpy = np.array(list(map(float, origin.get("rpy", "0 0 0").split())))
    assert np.linalg.norm(rpy) == 0, "Nonzero URDF origins require a general transform"
    lim = e.find("limit")
    j = {"joint": e.get("name"), "parent": e.find("parent").get("link"),
         "child": e.find("child").get("link"), "origin_xyz_m": xyz.tolist(),
         "axis": list(map(float, e.find("axis").get("xyz").split())),
         "lower_rad": float(lim.get("lower")), "upper_rad": float(lim.get("upper"))}
    urdf_joints[j["joint"]] = j
    children[j["child"]] = j
    origins[j["child"]] = origins[j["parent"]] + xyz

FOLD_PLANE_ABS_Y = origins["outerwing_L"][1]     # 0.14 by frozen URDF chain
FLAP_STATION_ABS_Y = origins["flapconn_L"][1]    # 0.06 by frozen URDF chain
PHYS_INNER_PANEL_X = (0.029, -0.157)             # innerwing box forward/aft edges, absolute
TAIL_SPLIT_Y = 0.012                             # ridge data: fan side bins at |y|>=0.012 stay flat


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
            corners = [s.split("/") for s in p[1:]]
            assert len(corners) == 3
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
    """Clip triangles on coordinate y against sign*y <= plane (inner) or >= plane (outer)."""
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


def geometry(name, tris, poly):
    p = tris[:, :, :3].reshape(-1, 3)
    cross = np.cross(tris[:, 1, :3] - tris[:, 0, :3], tris[:, 2, :3] - tris[:, 0, :3])
    weights = np.linalg.norm(cross, axis=1) / 2
    center = (tris[:, :, :3].mean(axis=1) * weights[:, None]).sum(axis=0) / weights.sum()
    return {"part": name, "triangles": len(tris), "bounds_min_m": p.min(axis=0).tolist(),
            "bounds_max_m": p.max(axis=0).tolist(), "dimensions_m": np.ptp(p, axis=0).tolist(),
            "surface_area_m2": float(weights.sum()), "projected_planform_area_m2": float(poly.area),
            "surface_area_centroid_m": center.tolist(), "spanwise_extent_m": float(np.ptp(p[:, 1])),
            "x_chord_envelope_m": float(np.ptp(p[:, 0])),
            "projected_area_centroid_xy_m": list(poly.centroid.coords)[0],
            "cut_caps_added": False, "mass_kg": None, "inertia_kg_m2": None,
            "airfoil_profile_status": "照片生成网格可记录截面；真实翼型、厚度和材料尚未确认"}


def section_at_y(tris, y):
    pts = []
    for k in range(3):
        a, b = tris[:, k, :3], tris[:, (k + 1) % 3, :3]
        mask = ((a[:, 1] - y) * (b[:, 1] - y) <= 0) & (np.abs(b[:, 1] - a[:, 1]) > 1e-13)
        t = (y - a[mask, 1]) / (b[mask, 1] - a[mask, 1])
        pts.append(a[mask] + t[:, None] * (b[mask] - a[mask]))
    return np.concatenate(pts)


def open_boundary_vertices(tris):
    """Weld corner-soup vertices by exact coordinate equality (OBJ text is shared),
    then return (all coords, unique open-boundary vertex coordinates)."""
    from collections import Counter
    coords = tris[:, :, :3].reshape(-1, 3)
    uniq, inv = np.unique(coords, axis=0, return_inverse=True)
    cnt = Counter()
    for f in inv.reshape(-1, 3):
        fl = list(f)
        for a, b in zip(fl, fl[1:] + fl[:1]):
            cnt[(min(a, b), max(a, b))] += 1
    open_ids = sorted({i for e, c in cnt.items() if c == 1 for i in e})
    return coords, uniq[open_ids]


# ----------------------------------------------------------------------------
# 1. Body: unchanged transform, bound to base_link.
meshes, binding, metrics, polys = {}, [], {}, {}
body_tris = read_obj(SOURCE_PARTS / "body_tail.obj")
meshes["eagle_body_and_nose"] = body_tris
binding.append({"part": "eagle_body_and_nose", "link": "base_link", "source": "body_tail.obj",
                "source_role": "body with visual nose at mechanism +x; legacy name body_tail kept for traceability"})

# ----------------------------------------------------------------------------
# 2. Wings: measure the root seam under the current transform, then apply the
#    corrective shift so the frozen mechanism axes pass through the seam region
#    consistent with the URDF-declared panel layout.
wing_reports = {}
corrected = {}
for side, sign in [("L", 1), ("R", -1)]:
    tri = read_obj(SOURCE_PARTS / ("wing_left.obj" if side == "L" else "wing_right.obj"))
    coords, seam_pts = open_boundary_vertices(tri)
    seam_band = seam_pts[np.abs(seam_pts[:, 1]) < 0.06]
    # chord reference at the flap/twist joint station
    sec = section_at_y(tri, sign * FLAP_STATION_ABS_Y)
    le = float(sec[:, 0].max()); te = float(sec[:, 0].min())
    seam_mid_x = float((seam_band[:, 0].min() + seam_band[:, 0].max()) / 2)
    seam_centroid = seam_band.mean(axis=0)
    quarter_chord = le - 0.25 * (le - te)
    # Anchor: visual root LE at the joint station lands on the frozen inner-panel
    # forward edge (mechanism-declared wing-to-axis relationship).
    dx = PHYS_INNER_PANEL_X[0] - le
    dz = -float(seam_centroid[2])
    tri2 = tri.copy()
    tri2[:, :, :3] += np.array([dx, 0.0, dz])
    sec2 = section_at_y(tri2, sign * FLAP_STATION_ABS_Y)
    le2 = float(sec2[:, 0].max()); te2 = float(sec2[:, 0].min())
    foldsec = section_at_y(tri2, sign * FOLD_PLANE_ABS_Y)
    fle = float(foldsec[:, 0].max()); fte = float(foldsec[:, 0].min())
    wing_reports[side] = {
        "asset_file": ("wing_left.obj" if side == "L" else "wing_right.obj"),
        "joint_station_abs_y_m": sign * FLAP_STATION_ABS_Y,
        "before_correction": {
            "leading_edge_x_m": le, "trailing_edge_x_m": te,
            "twist_axis_x_m": 0.0,
            "axis_fraction_from_leading_edge": le / (le - te),
            "seam_extent_midpoint_x_m": seam_mid_x,
            "seam_vertex_centroid_xyz_m": seam_centroid.tolist(),
            "quarter_chord_reference_x_m": quarter_chord,
            "quarter_chord_is_confirmed_hinge": False},
        "correction_applied": {"dx_m": dx, "dy_m": 0.0, "dz_m": dz,
                               "anchor_rule": "root leading edge at the flap/twist joint station aligned to the frozen URDF innerwing panel forward edge x=%.3f m" % PHYS_INNER_PANEL_X[0]},
        "after_correction": {
            "leading_edge_x_m": le2, "trailing_edge_x_m": te2,
            "twist_axis_fraction_from_leading_edge": le2 / (le2 - te2),
            "seam_extent_midpoint_x_m": seam_mid_x + dx,
            "seam_vertex_centroid_xyz_m": (seam_centroid + np.array([dx, 0, dz])).tolist(),
            "fold_station_abs_y_m": sign * FOLD_PLANE_ABS_Y,
            "fold_station_leading_edge_x_m": fle, "fold_station_trailing_edge_x_m": fte,
            "fold_hinge_x_m": urdf_joints["fold_" + side]["origin_xyz_m"][0] + origins["innerwing_" + side][0],
            "fold_hinge_fraction_from_leading_edge": (fle - (urdf_joints["fold_" + side]["origin_xyz_m"][0] + origins["innerwing_" + side][0])) / (fle - fte),
            "urdf_inner_panel_axis_fraction_reference": 0.1551,
            "urdf_outer_panel_hinge_fraction_reference": 0.1904},
        "assumption": "视觉翼与冻结机构的弦向对应按 URDF 面板声明对齐；实物铰点位置无资料，四分之一弦点仅作参考不作铰点"}
    corrected[side] = tri2

# Wing split at the frozen fold plane |y| = 0.14.
stations = []
checks = {}
for side, sign in [("L", 1), ("R", -1)]:
    tri = corrected[side]
    plane = FOLD_PLANE_ABS_Y
    full_poly = projection(tri)
    metrics["wing_full_" + side] = geometry("wing_full_" + side, tri, full_poly)
    split = []
    for inner, segment, link in [(True, "main", "innerwing_" + side), (False, "tip", "outerwing_" + side)]:
        name = "eagle_wing_" + segment + "_" + side
        part = clip(tri, plane, sign, inner)
        poly = projection(part)
        meshes[name], polys[name] = part, poly
        metrics[name] = geometry(name, part, poly)
        split.append(part)
        binding.append({"part": name, "link": link, "source": ("wing_left.obj" if side == "L" else "wing_right.obj"),
                        "source_role": "mirror" if side == "L" else "master",
                        "split_plane_mechanism_y_m": sign * plane,
                        "wing_correction_dx_dz_m": [wing_reports[side]["correction_applied"]["dx_m"],
                                                    wing_reports[side]["correction_applied"]["dz_m"]]})
    before, after = surface_area(tri), sum(surface_area(p) for p in split)
    combined = unary_union([polys["eagle_wing_main_" + side], polys["eagle_wing_tip_" + side]])
    checks["clip_wing_" + side] = {"surface_area_before_m2": before, "surface_area_after_m2": after,
                                   "relative_area_error": abs(after - before) / before,
                                   "projected_symmetric_difference_m2": float(full_poly.symmetric_difference(combined).area)}
    assert abs(after - before) / before < 1e-6
    assert full_poly.symmetric_difference(combined).area < 1e-9
    ymax = max(sign * tri[:, :, 1].reshape(-1))
    ymin = min(sign * tri[:, :, 1].reshape(-1))
    for s in np.linspace(ymin + 1e-7, ymax - 1e-7, 41):
        y = float(sign * s)
        section = full_poly.intersection(LineString([(-2, y), (2, y)]))
        if section.is_empty:
            continue
        bb = section.bounds
        pts = section_at_y(tri, y)
        cov = np.cov(pts[:, [0, 2]].T)
        _, vec = np.linalg.eigh(cov)
        v = vec[:, -1]
        if v[0] < 0:
            v = -v
        stations.append({"side": side, "span_position_abs_m": float(s), "mechanism_y_m": y,
                         "segment": "main" if s <= plane else "tip",
                         "leading_edge_x_m": float(bb[2]), "trailing_edge_x_m": float(bb[0]),
                         "chord_envelope_m": float(bb[2] - bb[0]), "occupied_chord_m": float(section.length),
                         "min_z_m": float(pts[:, 2].min()), "max_z_m": float(pts[:, 2].max()),
                         "section_thickness_envelope_m": float(np.ptp(pts[:, 2])),
                         "visual_section_principal_angle_deg": math.degrees(math.atan2(v[1], v[0]))})

# ----------------------------------------------------------------------------
# 3. Tail: original fan mesh split into L/R horizontal surfaces + vertical rudder.
tail_tris = read_obj(SOURCE_PARTS / "head.obj")
coords, seam_all = open_boundary_vertices(tail_tris)
root_thr = np.quantile(tail_tris[:, :, 0], 0.90)
seam = seam_all[seam_all[:, 0] > root_thr]
seam_L = seam[seam[:, 1] >= TAIL_SPLIT_Y]
seam_R = seam[seam[:, 1] <= -TAIL_SPLIT_Y]
seam_C = seam[np.abs(seam[:, 1]) < TAIL_SPLIT_Y]
hinge_L = seam_L.mean(axis=0)
hinge_R = seam_R.mean(axis=0)
hinge_C = seam_C.mean(axis=0)
assert len(seam_L) >= 5 and len(seam_R) >= 5 and len(seam_C) >= 5, \
    (len(seam_L), len(seam_R), len(seam_C))

fan_poly = projection(tail_tris)
metrics["tail_fan_full"] = geometry("tail_fan_full", tail_tris, fan_poly)
tail_parts = {}
for name, lo, hi in [("eagle_tail_rudder", -TAIL_SPLIT_Y, TAIL_SPLIT_Y),
                     ("eagle_tail_surface_L", TAIL_SPLIT_Y, 10.0),
                     ("eagle_tail_surface_R", -10.0, -TAIL_SPLIT_Y)]:
    part = clip(tail_tris, hi, 1.0, True) if hi < 10.0 else clip(tail_tris, lo, 1.0, False)
    if hi < 10.0:
        part = clip(part, lo, 1.0, False)
    tail_parts[name] = part
    meshes[name] = part
    polys[name] = projection(part)
    metrics[name] = geometry(name, part, polys[name])
tail_area_before = surface_area(tail_tris)
tail_area_after = sum(surface_area(p) for p in tail_parts.values())
tail_combined = unary_union([polys[n] for n in tail_parts])
checks["clip_tail_fan"] = {"surface_area_before_m2": tail_area_before,
                           "surface_area_after_m2": tail_area_after,
                           "relative_area_error": abs(tail_area_after - tail_area_before) / tail_area_before,
                           "projected_symmetric_difference_m2": float(fan_poly.symmetric_difference(tail_combined).area)}
assert abs(tail_area_after - tail_area_before) / tail_area_before < 1e-6
assert fan_poly.symmetric_difference(tail_combined).area < 1e-9

tail_binding_extra = {
    "eagle_tail_surface_L": {"joint": "tail_pitch_L", "axis": [0, 1, 0],
                             "hinge_point_xyz_m": [float(hinge_L[0]), float(hinge_L[1]), float(hinge_L[2])],
                             "urdf_joint_reference": "tail_L (limits ±0.5 rad, axis y)",
                             "hinge_source": "original fan root seam centroid, vertices with y>=+0.012"},
    "eagle_tail_surface_R": {"joint": "tail_pitch_R", "axis": [0, 1, 0],
                             "hinge_point_xyz_m": [float(hinge_R[0]), float(hinge_R[1]), float(hinge_R[2])],
                             "urdf_joint_reference": "tail_R (limits ±0.5 rad, axis y)",
                             "hinge_source": "original fan root seam centroid, vertices with y<=-0.012"},
    "eagle_tail_rudder": {"joint": "rudder_yaw", "axis": [0, 0, 1],
                          "hinge_point_xyz_m": [float(hinge_C[0]), float(hinge_C[1]), float(hinge_C[2])],
                          "urdf_joint_reference": "tail_yaw (limits ±0.6 rad, axis z)",
                          "hinge_source": "original fan root seam centroid, vertices with |y|<0.012"},
}
for name, extra in tail_binding_extra.items():
    binding.append({"part": name, "link": "base_link", "source": "head.obj",
                    "source_name_was_incorrect": True,
                    "split_planes_y_m": {"eagle_tail_rudder": [-TAIL_SPLIT_Y, TAIL_SPLIT_Y],
                                         "eagle_tail_surface_L": [TAIL_SPLIT_Y, None],
                                         "eagle_tail_surface_R": [None, -TAIL_SPLIT_Y]}[name],
                    **extra})

# ----------------------------------------------------------------------------
# 4. Preview joint graph. Wings follow the frozen URDF chain. Tail: three
#    sibling nodes under base_link; each rotation drives exactly one mesh.
preview_joints = {}
for n in ["flap_L", "twist_L", "fold_L", "flap_R", "twist_R", "fold_R"]:
    preview_joints[n] = {"joint": n, "parent": urdf_joints[n]["parent"], "child": urdf_joints[n]["child"],
                         "frame_origin_xyz_m": urdf_joints[n]["origin_xyz_m"],
                         "axis": urdf_joints[n]["axis"],
                         "limit_rad": [urdf_joints[n]["lower_rad"], urdf_joints[n]["upper_rad"]],
                         "source": "frozen URDF, unchanged"}
TAIL_JOINT_TO_PART = {"tail_pitch_L": "eagle_tail_surface_L",
                      "tail_pitch_R": "eagle_tail_surface_R",
                      "rudder_yaw": "eagle_tail_rudder"}
tail_limits = {"tail_pitch_L": urdf_joints["tail_L"], "tail_pitch_R": urdf_joints["tail_R"],
               "rudder_yaw": urdf_joints["tail_yaw"]}
for n, part in TAIL_JOINT_TO_PART.items():
    ref = tail_limits[n]
    extra = tail_binding_extra[part]
    preview_joints[n] = {"joint": n, "parent": "base_link", "child": part,
                         "frame_origin_xyz_m": extra["hinge_point_xyz_m"],
                         "axis": extra["axis"],
                         "limit_rad": [ref["lower_rad"], ref["upper_rad"]],
                         "source": "visual candidate; hinge from original fan root seam; "
                                   "limits and axis referenced from URDF joint " + ref["joint"] + "; "
                                   "topology differs from URDF (no shared yaw parent)"}

def part_link(part):
    """Transform-graph key for a part: tail parts are their own nodes; others use the bound link."""
    if part in TAIL_JOINT_TO_PART.values():
        return part
    for b in binding:
        if b["part"] == part:
            return b["link"]
    raise KeyError(part)


def axis_rotation(axis, angle):
    a = np.array(axis, dtype=float); a /= np.linalg.norm(a)
    x, y, z = a; c = math.cos(angle); s = math.sin(angle); k = 1 - c
    return np.array([[c + x * x * k, x * y * k - z * s, x * z * k + y * s],
                     [y * x * k + z * s, c + y * y * k, y * z * k - x * s],
                     [z * x * k - y * s, z * y * k + x * s, c + z * z * k]])


def transforms(angles):
    result = {"base_link": (np.eye(3), np.zeros(3))}
    for side in ["L", "R"]:
        r0, t0 = result["base_link"]
        to = np.array(urdf_joints["flap_" + side]["origin_xyz_m"])
        r1 = r0 @ axis_rotation(urdf_joints["flap_" + side]["axis"], angles.get("flap_" + side, 0))
        result["flapconn_" + side] = (r1, t0 + r0 @ to)
        r2 = r1 @ axis_rotation(urdf_joints["twist_" + side]["axis"], angles.get("twist_" + side, 0))
        result["innerwing_" + side] = (r2, result["flapconn_" + side][1])
        to = np.array(urdf_joints["fold_" + side]["origin_xyz_m"])
        r3 = r2 @ axis_rotation(urdf_joints["fold_" + side]["axis"], angles.get("fold_" + side, 0))
        result["outerwing_" + side] = (r3, result["innerwing_" + side][1] + r2 @ to)
    for n in ["tail_pitch_L", "tail_pitch_R", "rudder_yaw"]:
        j = preview_joints[n]
        r = result["base_link"][0] @ axis_rotation(j["axis"], angles.get(n, 0))
        result[j["child"]] = (r, np.array(j["frame_origin_xyz_m"], dtype=float))
    return result


# Mesh vertex frames: wings subtract the URDF link world origins; tail parts
# subtract their hinge points.
zero = transforms({})
part_offsets = {}
for b in binding:
    part_offsets[b["part"]] = zero[part_link(b["part"])][1].copy()

# ----------------------------------------------------------------------------
# 5. Motion timeline. Every command stays inside the frozen limits.
spec = importlib.util.spec_from_file_location("eaglex_existing_kinematics", ROOT / "modules/aero/kinematics.py")
kin = importlib.util.module_from_spec(spec); spec.loader.exec_module(kin)
from config import sim_config as cfg
wk = kin.WingKinematics(frequency=cfg.WING_DRIVE_CONFIG["frequency"],
                        amplitude_deg=cfg.WING_DRIVE_CONFIG["amplitude_deg"],
                        pitch_mode=cfg.WING_DRIVE_CONFIG["pitch_mode"],
                        alpha_set_deg=cfg.WING_DRIVE_CONFIG["alpha_set_deg"])
JOINTS = ["flap_L", "twist_L", "fold_L", "flap_R", "twist_R", "fold_R",
          "tail_pitch_L", "tail_pitch_R", "rudder_yaw"]
duration = 20.0
times = np.linspace(0, duration, 301)
poses, sections = [], []
TAIL_DEG = 20.0
for t in times:
    t = float(t)
    a = {k: 0.0 for k in JOINTS}
    if t < 2:
        mode = "FLAP"
        v = math.radians(30) * math.sin(math.pi * t)
        a.update(flap_L=v, flap_R=-v)
    elif t < 4:
        mode = "TWIST"
        v = math.radians(25) * math.sin(math.pi * (t - 2))
        a.update(twist_L=v, twist_R=v)
    elif t < 6:
        mode = "FOLD"
        v = .5 * (1 - math.cos(math.pi * (t - 4))) * .75
        a.update(fold_L=v, fold_R=-v)
    elif t < 8:
        mode = "TAIL_L_SINGLE"
        a.update(tail_pitch_L=math.radians(TAIL_DEG) * math.sin(math.pi * (t - 6)))
    elif t < 10:
        mode = "TAIL_R_SINGLE"
        a.update(tail_pitch_R=math.radians(TAIL_DEG) * math.sin(math.pi * (t - 8)))
    elif t < 12:
        mode = "RUDDER_SINGLE"
        a.update(rudder_yaw=math.radians(TAIL_DEG) * math.sin(math.pi * (t - 10)))
    elif t < 14:
        mode = "TAIL_SYMMETRIC"
        v = math.radians(TAIL_DEG) * math.sin(math.pi * (t - 12))
        a.update(tail_pitch_L=v, tail_pitch_R=v)
    elif t < 16:
        mode = "TAIL_DIFFERENTIAL"
        v = math.radians(TAIL_DEG) * math.sin(math.pi * (t - 14))
        a.update(tail_pitch_L=v, tail_pitch_R=-v)
    else:
        mode = "COMBINED"
        ev = wk.evaluate(t - 16)
        envelope = min(1, (t - 16) / .25, (20 - t) / .25)
        fold = cfg.WING_DRIVE_CONFIG["fold_amp"] * .5 * (1 + math.sin(wk.omega * (t - 16) + 2.2)) * envelope
        a.update(flap_L=ev["phi"] * envelope, flap_R=-ev["phi"] * envelope,
                 twist_L=ev["alpha"] * envelope, twist_R=ev["alpha"] * envelope,
                 fold_L=fold, fold_R=-fold,
                 rudder_yaw=math.radians(12) * math.sin(math.pi * (t - 16)) * envelope,
                 tail_pitch_L=math.radians(8) * math.sin(2 * math.pi * (t - 16)) * envelope,
                 tail_pitch_R=math.radians(8) * math.sin(2 * math.pi * (t - 16)) * envelope)
    for n, v in a.items():
        lo, hi = preview_joints[n]["limit_rad"]
        assert lo - 1e-8 <= v <= hi + 1e-8, (n, v, lo, hi)
    poses.append(a)
    sections.append(mode)
checks["joint_limits_passed"] = True

# ----------------------------------------------------------------------------
# 6. Three-angle independence: each tail angle moves only its own mesh.
indeptest = {}
probe_ang = 0.3
base_tr = transforms({})
for n in ["tail_pitch_L", "tail_pitch_R", "rudder_yaw"]:
    tr = transforms({n: probe_ang})
    moved, still = [], []
    for part in meshes:
        link = part_link(part)
        p = meshes[part][:, :, :3].reshape(-1, 3) + part_offsets[part]
        w0 = p @ base_tr[link][0].T + base_tr[link][1]
        w1 = p @ tr[link][0].T + tr[link][1]
        d = float(np.abs(w1 - w0).max())
        (moved if d > 1e-6 else still).append((part, d))
    expected = preview_joints[n]["child"]
    indeptest[n] = {"probe_angle_rad": probe_ang, "moved_parts": [p for p, _ in moved],
                    "unchanged_parts_max_displacement_m": {p: d for p, d in still}}
    assert [p for p, _ in moved] == [expected], (n, moved)
    assert max(d for _, d in still) < 1e-12
checks["tail_three_angle_independence"] = indeptest

# Fold-follow check for the wing tips.
foldpose = transforms({"fold_L": .75, "fold_R": -.75})
for side in ["L", "R"]:
    name = "eagle_wing_tip_" + side
    link = "outerwing_" + side
    p = meshes[name][:, :, :3].reshape(-1, 3) + part_offsets[name]
    r, o = foldpose[link]
    moved = p @ r.T + o
    displacement = np.linalg.norm(moved - (p @ base_tr[link][0].T + base_tr[link][1]), axis=1)
    checks["fold_" + side] = {"maximum_vertex_displacement_m": float(displacement.max())}
    assert displacement.max() > .02

# ----------------------------------------------------------------------------
# 7. Write part OBJ files in their binding frames.
for b in binding:
    frame = part_link(b["part"])
    b["zero_origin_xyz_m"] = part_offsets[b["part"]].tolist()
    b["part_file"] = "parts/" + b["part"] + ".obj"
    if b["part"] not in metrics:
        metrics[b["part"]] = geometry(b["part"], meshes[b["part"]], projection(meshes[b["part"]]))
    tri = meshes[b["part"]].copy()
    tri[:, :, :3] -= part_offsets[b["part"]]
    with (PARTS / (b["part"] + ".obj")).open("w", encoding="ascii") as f:
        f.write("mtllib basecolor.mtl\nusemtl eagle\n")
        for p in tri[:, :, :3].reshape(-1, 3):
            f.write("v %.9f %.9f %.9f\n" % tuple(p))
        for uv in tri[:, :, 3:5].reshape(-1, 2):
            f.write("vt %.9f %.9f\n" % tuple(uv))
        for nrm in tri[:, :, 5:8].reshape(-1, 3):
            f.write("vn %.9f %.9f %.9f\n" % tuple(nrm / max(np.linalg.norm(nrm), 1e-15)))
        for i in range(len(tri)):
            idx = [3 * i + k + 1 for k in range(3)]
            f.write("f " + " ".join(f"{n}/{n}/{n}" for n in idx) + "\n")
(PARTS / "basecolor.png").write_bytes(TEXTURE.read_bytes())
(PARTS / "basecolor.mtl").write_text("newmtl eagle\nKd 1 1 1\nmap_Kd basecolor.png\n", encoding="ascii")

# ----------------------------------------------------------------------------
# 8. glTF export.
g = {"asset": {"version": "2.0", "generator": "L8 offline URDF geometry preview, I-201"},
     "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [], "meshes": [], "bufferViews": [], "accessors": [],
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
    d["min"] = a.min(axis=0).reshape(-1).tolist(); d["max"] = a.max(axis=0).reshape(-1).tolist()
    g["accessors"].append(d)
    return len(g["accessors"]) - 1


g["images"].append({"bufferView": view(TEXTURE.read_bytes()), "mimeType": "image/png"})
g["nodes"].append({"name": "URDF z-up to glTF y-up", "rotation": [-math.sqrt(.5), 0, 0, math.sqrt(.5)], "children": [1]})
g["nodes"].append({"name": "base_link", "children": []})
node_ids = {"base_link": 1}
for side in ["L", "R"]:
    chain = [("flap_" + side, "flapconn_" + side), ("twist_" + side, "innerwing_" + side), ("fold_" + side, "outerwing_" + side)]
    for jname, child in chain:
        j = preview_joints[jname]
        idx = len(g["nodes"]); node_ids[child] = idx
        g["nodes"].append({"name": child, "translation": j["frame_origin_xyz_m"], "rotation": [0, 0, 0, 1], "children": [],
                           "extras": {"urdf_joint": jname, "axis": j["axis"], "limit_rad": j["limit_rad"], "source": j["source"]}})
        g["nodes"][node_ids[j["parent"]]]["children"].append(idx)
for n in ["tail_pitch_L", "tail_pitch_R", "rudder_yaw"]:
    j = preview_joints[n]
    idx = len(g["nodes"]); node_ids[j["child"]] = idx
    g["nodes"].append({"name": j["child"], "translation": j["frame_origin_xyz_m"], "rotation": [0, 0, 0, 1], "children": [],
                       "extras": {"preview_joint": n, "axis": j["axis"], "limit_rad": j["limit_rad"],
                                  "hinge_source": tail_binding_extra[j["child"]]["hinge_source"],
                                  "urdf_joint_reference": tail_binding_extra[j["child"]]["urdf_joint_reference"],
                                  "topology": "sibling of base_link; no shared yaw parent; candidate only"}})
    g["nodes"][1]["children"].append(idx)
for b in binding:
    tri = meshes[b["part"]]
    p = (tri[:, :, :3] - part_offsets[b["part"]]).reshape(-1, 3)
    uv = tri[:, :, 3:5].reshape(-1, 2).copy(); uv[:, 1] = 1 - uv[:, 1]
    normals = tri[:, :, 5:8].reshape(-1, 3)
    normals = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-15)
    attr = {"POSITION": accessor(p, "VEC3", 34962), "NORMAL": accessor(normals, "VEC3", 34962), "TEXCOORD_0": accessor(uv, "VEC2", 34962)}
    mi = len(g["meshes"]); g["meshes"].append({"name": b["part"], "primitives": [{"attributes": attr, "material": 0, "mode": 4}]})
    ni = len(g["nodes"]); g["nodes"].append({"name": b["part"], "mesh": mi})
    frame = part_link(b["part"])
    g["nodes"][node_ids[frame]]["children"].append(ni)
animation = {"name": "flap_twist_fold_tail_three_angle_and_combined", "samplers": [], "channels": []}
ta = accessor(times, "SCALAR")
for n in JOINTS:
    j = preview_joints[n]
    axis = np.array(j["axis"], dtype=float); axis /= np.linalg.norm(axis)
    angles = np.array([a[n] for a in poses])
    q = np.c_[np.sin(angles / 2)[:, None] * axis, np.cos(angles / 2)]
    qa = accessor(q, "VEC4")
    si = len(animation["samplers"])
    animation["samplers"].append({"input": ta, "output": qa, "interpolation": "LINEAR"})
    animation["channels"].append({"sampler": si, "target": {"node": node_ids[j["child"]], "path": "rotation"}})
g["animations"] = [animation]
g["buffers"] = [{"byteLength": len(blob)}]
js = json.dumps(g, separators=(",", ":"), ensure_ascii=True).encode()
while len(js) % 4:
    js += b" "
while len(blob) % 4:
    blob.extend(b"\0")
with (OUT / "eaglex_articulated_preview.glb").open("wb") as f:
    f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(blob)))
    f.write(struct.pack("<I4s", len(js), b"JSON")); f.write(js)
    f.write(struct.pack("<I4s", len(blob), b"BIN\0")); f.write(blob)

# ----------------------------------------------------------------------------
# 9. CSV outputs.
with (OUT / "chord_stations.csv").open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(stations[0])); w.writeheader(); w.writerows(stations)
with (OUT / "joint_commands.csv").open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["time_s", "mode"] + JOINTS)
    for t, mode, a in zip(times, sections, poses):
        w.writerow([t, mode] + [a[n] for n in JOINTS])

# ----------------------------------------------------------------------------
# 10. Sweep reference lines, recomputed in the corrected nose-forward frame.
sweep_fits = []
for side in ["L", "R"]:
    rows = [r for r in stations if r["side"] == side]
    for segment, lo, hi in [("central", .05, .28), ("main", .05, .14), ("tip", .14, .28)]:
        sel = [r for r in rows if lo <= r["span_position_abs_m"] <= hi]
        if len(sel) < 4:
            continue
        for ref, key in [("leading_edge", "leading_edge_x_m"), ("quarter_chord", None), ("trailing_edge", "trailing_edge_x_m")]:
            if key is None:
                xs = [r["leading_edge_x_m"] - .25 * (r["leading_edge_x_m"] - r["trailing_edge_x_m"]) for r in sel]
            else:
                xs = [r[key] for r in sel]
            ys = [r["span_position_abs_m"] for r in sel]
            A = np.polyfit(ys, xs, 1)
            resid = float(np.max(np.abs(np.polyval(A, ys) - np.asarray(xs))))
            sweep_fits.append({"side": side, "segment": segment, "reference_line": ref,
                               "fitted_span_range_m": [lo, hi], "station_count": len(sel),
                               "linear_slope_dx_dspan": float(A[0]),
                               "sweep_aft_positive_deg": float(math.degrees(math.atan(-A[0]))),
                               "fit_max_position_residual_m": resid,
                               "frame": "corrected nose-forward mechanism frame; aft = -x"})

# ----------------------------------------------------------------------------
# 11. Report.
sources = [URDF, OLD_MANIFEST, TEXTURE, ROOT / "modules/aero/kinematics.py", ROOT / "config/sim_config.py",
           ROOT / "eagle_shell/prep_parts.py", SOURCE_PARTS / "parts_meta.json",
           ROOT / "assets/eagleX_raw_50k/cfd068548a51f523e76e53b2c9c2f559.glb",
           ROOT / "assets/eagleX_raw_50k/b50134432fcdc17a4a98826ee389d9bc.zip",
           Path("G:/Playground/PartField/exp_results/clustering/eagleX_knn/cluster_out/eagleX_0_04.npy"),
           AUDIT, AUDIT_FIG,
           SOURCE_PARTS / "head.obj", SOURCE_PARTS / "body_tail.obj",
           SOURCE_PARTS / "wing_right.obj", SOURCE_PARTS / "wing_left.obj",
           HERE / "l8_realign_probe.py",
           HERE / "l8_realign_probe_result.json",
           HERE / "l8_tail_shape_probe.py",
           HERE / "l8_tail_shape_probe_result.json"]
report = {
    "schema_version": 2, "date": "2026-10-13", "item": "I-201", "asset": "eagleX",
    "purpose": "视觉资产轴位修正、原尾部作动面三分、几何提取与完整关节动作预览（离线候选）",
    "coordinate_system": "URDF x前 y左 z上，长度单位米；glTF根节点转换为y向上",
    "simulator_used": False,
    "provenance": [{"path": str(p), "sha256": digest(p)} for p in sources],
    "source_chain": {"start": "现有eagleX原始模型和贴图包",
                     "segmentation": "既有PartField第四级标签与prep_parts分件；本轮沿用既有分件",
                     "source_photo": "未提供可核对的原始照片与生成过程；不能宣称照片保真验收通过"},
    "asset_to_mechanism": {"rotation": asset_rotation.tolist(), "scale": scale,
                           "translation_m": translate.tolist(),
                           "wing_correction": {s: wing_reports[s]["correction_applied"] for s in ["L", "R"]}},
    "definitions": {"projected_planform_area_m2": "零位网格三角形向机构xy平面投影后的并集面积；不重复累计上下表面",
                    "surface_area_m2": "网格三角形表面积之和；不得替代翼面投影面积",
                    "chord_envelope_m": "展向切片中前后缘x坐标最大差；存在孔隙时同时提供实际占用弦长",
                    "visual_section_principal_angle_deg": "生成网格截面点在xz平面的主方向角；不是实测翼型扭角",
                    "spanwise_extent_m": "该部件y坐标范围；同时保留以机身中线为基准的位置",
                    "sweep_aft_positive_deg": "参考线拟合x=a*|y|+b的后掠角 atan(-a)；机头为+x，向后掠为负斜率",
                    "mass_and_inertia": "无法从视觉网格确认；保留空值，未写入物理配置"},
    "wing_axis_realignment": {s: wing_reports[s] for s in ["L", "R"]},
    "tail_three_angle_candidate": {
        "source_mesh": "head.obj（原模型尾部风扇；文件名沿自PartField旧标注，实为尾部）",
        "split_planes_y_m": [-TAIL_SPLIT_Y, TAIL_SPLIT_Y],
        "split_basis": "风扇展向剖面：中央竖脊仅存在于|y|<0.012（顶点最高z=0.069），|y|>=0.012侧带平坦（z_max<=0.005），故以y=±0.012切分",
        "parts": tail_binding_extra,
        "control_combinations": {"symmetric": "tail_pitch_L = tail_pitch_R，俯仰候选",
                                 "differential": "tail_pitch_L = -tail_pitch_R，滚转候选",
                                 "rudder": "rudder_yaw，偏航候选"},
        "topology": "三个节点同为base_link子节点，互不为父子；任一角度只作用于自己的网格（见verification.tail_three_angle_independence）",
        "urdf_topology_difference": "冻结URDF中tail_yaw旋转整个tail_base并连带左右舵面；本候选解除该父子关系，方向舵独立偏航。物理接入与气动效应未验证，另办。"},
    "bindings": binding,
    "joints": list(preview_joints.values()),
    "geometry": metrics,
    "sweep_reference_lines": sweep_fits,
    "verification": checks,
    "no_synthetic_tail": {"synthetic_tail_mesh_count": 0,
                          "note": "外观仅使用原模型网格；URDF方盒尾翼visual未导入任何预览或交付件"},
    "preview": {"duration_s": duration,
                "segments": ["0-2s拍动", "2-4s扭转", "4-6s折叠", "6-8s尾左单动", "8-10s尾右单动",
                             "10-12s方向舵单动", "12-14s尾左右同向", "14-16s尾左右差动", "16-20s组合"],
                "mode": "运动学角度驱动，固定机身；没有启动Isaac仿真器",
                "physics_drive_tracking_verified": False, "production_model_updated": False},
    "limitations": ["新切面保留开放边界，不补生成表面；现有生成网格的破损和孔洞保留",
                    "翼轴锚定按冻结URDF面板声明对齐；实物铰点无资料，结果为明确标注假设的离线候选，不宣称真实机构通过",
                    "折叠铰在翼尖段的弦向位置与URDF外板声明存在形状性偏差，数值见wing_axis_realignment.after_correction",
                    "方向舵大角度偏转时风扇后缘会扫入左右水平面所在平面；刚性分件无柔性蒙皮，重叠为已知限制",
                    "尾部三角度候选与冻结URDF的父子拓扑不同；新构型物理接入另办验证",
                    "当前尺度沿用旧壳装配值，未确认七百克实物尺寸",
                    "贴图坐标在切分面上按线性插值生成新UV；切面为开放边界",
                    "重要独立复验须由未参与资产方案与实现的人承担；本包自检不能代替独立验收"],
    "rollback": {"production_touched": False,
                 "method": "生产模型与旧绑定清单未改动；本交付位于平行目录 eagle_shell/l8_i201_20261013/，整目录删除即回退；上一轮Jane预览目录保留作对照"}}
(OUT / "geometry_and_binding.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

# ----------------------------------------------------------------------------
# 12. Software raster previews.
tex = np.asarray(Image.open(TEXTURE).convert("RGB"))
CAMERAS = {"main": np.array([-.55, -1.15, 1.00]), "rear": np.array([-.95, -.55, .55]),
           "front": np.array([.85, -1.25, 1.05])}
prepared = []
for b in binding:
    tri = meshes[b["part"]]
    uv = tri[:, :, 3:5].mean(axis=1)
    px = np.clip((uv[:, 0] % 1) * (tex.shape[1] - 1), 0, tex.shape[1] - 1).astype(int)
    py = np.clip((1 - uv[:, 1] % 1) * (tex.shape[0] - 1), 0, tex.shape[0] - 1).astype(int)
    colors = tex[py, px].astype(float)
    prepared.append((b, tri[:, :, :3] - part_offsets[b["part"]], tri[:, :, 5:8].mean(axis=1), colors))
try:
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22)
    small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 17)
except OSError:
    font = small = ImageFont.load_default()
FOOT = {"main": "3 tail angles on separate meshes  |  original fan tail  |  no synthetic boxes",
        "rear": "tail action clip  |  hinge lines from original fan root seam",
        "front": "wing action clip  |  frozen URDF joint chain"}


def render_frame(index, cam_key):
    camera = CAMERAS[cam_key].copy(); camera /= np.linalg.norm(camera)
    right = np.cross([0, 0, 1], camera); right /= np.linalg.norm(right)
    up = np.cross(camera, right)
    project = np.array([right, up, camera]).T
    light = np.array([.3, -.5, .9]); light /= np.linalg.norm(light)
    tr = transforms(poses[index])
    positions, cols = [], []
    for b, p, n, col in prepared:
        link = part_link(b["part"])
        r, o = tr[link]
        world = p @ r.T + o
        cam = world @ project
        nn = n @ r.T
        shade = .52 + .48 * np.abs(nn @ light)
        positions.append(cam); cols.append(np.clip(col * shade[:, None], 0, 255).astype(np.uint8))
    v = np.concatenate(positions); col = np.concatenate(cols)
    xy = v[:, :, :2].copy() * 870
    xy[:, :, 0] += 480; xy[:, :, 1] = 340 - xy[:, :, 1]
    a = np.abs((xy[:, 1, 0] - xy[:, 0, 0]) * (xy[:, 2, 1] - xy[:, 0, 1]) -
               (xy[:, 1, 1] - xy[:, 0, 1]) * (xy[:, 2, 0] - xy[:, 0, 0]))
    order = np.argsort(v[:, :, 2].mean(axis=1))
    im = Image.new("RGB", (960, 650), (240, 243, 246)); draw = ImageDraw.Draw(im)
    for k in order:
        if a[k] > .22:
            draw.polygon([tuple(pt) for pt in xy[k]], fill=tuple(col[k]))
    draw.rectangle((0, 0, 960, 58), fill=(26, 40, 57))
    draw.text((20, 16), "Eagle X I-201  |  " + sections[index] + f"  |  {times[index]:.2f}s", font=font, fill="white")
    draw.text((20, 614), FOOT[cam_key], font=small, fill=(40, 55, 70))
    return im


frames = []
for index in range(0, len(times) - 1, 2):
    im = render_frame(index, "main")
    frames.append(im)
    if index in [0, 60, 120, 180, 240, 270, 300]:
        im.save(OUT / f"preview_{index:03d}.png")
frames[0].save(OUT / "eaglex_motion_preview.gif", save_all=True, append_images=frames[1:], duration=67, loop=0, optimize=False)
import imageio.v2 as imageio
with imageio.get_writer(str(OUT / "eaglex_motion_preview.mp4"), fps=15, codec="libx264", macro_block_size=2) as writer:
    for f in frames:
        writer.append_data(np.asarray(f))

# Per-action clips: the four required tail demonstrations plus wing singles.
CLIPS = {"tail_single_L": ("TAIL_L_SINGLE", "rear"), "tail_single_R": ("TAIL_R_SINGLE", "rear"),
         "rudder_single": ("RUDDER_SINGLE", "rear"), "tail_symmetric": ("TAIL_SYMMETRIC", "rear"),
         "tail_differential": ("TAIL_DIFFERENTIAL", "rear"),
         "flap": ("FLAP", "front"), "twist": ("TWIST", "front"), "fold": ("FOLD", "front")}
for clip, (mode, cam) in CLIPS.items():
    idxs = [i for i, m in enumerate(sections) if m == mode]
    clipdir = OUT / "clips"; clipdir.mkdir(exist_ok=True)
    with imageio.get_writer(str(clipdir / f"{clip}.mp4"), fps=15, codec="libx264", macro_block_size=2) as writer:
        for i in idxs[::2]:
            writer.append_data(np.asarray(render_frame(i, cam)))
    render_frame(idxs[len(idxs) // 4], cam).save(clipdir / f"{clip}_mid.png")

print(json.dumps({"output": str(OUT),
                  "parts": {b["part"]: metrics.get(b["part"], {}).get("triangles") for b in binding},
                  "checks": {k: (v if not isinstance(v, dict) else "see report") for k, v in checks.items()}},
                 ensure_ascii=False, indent=1))
