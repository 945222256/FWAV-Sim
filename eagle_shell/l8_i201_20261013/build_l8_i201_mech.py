"""Offline asset processing only: no simulator, no physics or production writes.

零仿真器声明：本脚本不启动任何仿真器进程。I-201 接续施工：按 Jane 2026-10-13
尾部机构复核与接续要求，补充一版符合冻结机构层级（整尾偏航基座下挂左右独立
尾面）的离线预览。原独立方向舵候选版本保留不动；本版不增加补建方盒，不改
冻结物理机构。视觉铰点为来源候选，与冻结机构铰点分开标注。
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
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
OUT = HERE / "output"
MECH = OUT / "mechanism_hierarchy"
MECH.mkdir(parents=True, exist_ok=True)
PARTS = OUT / "parts"
URDF = ROOT / "assets/flapper/fwav_15dof_from_group.urdf"
MAIN_REPORT = OUT / "geometry_and_binding.json"
REQUIREMENT = ROOT / "logs/jane_dispatch_20261013/尾部机构复核与接续要求.md"
TEXTURE = ROOT / "eagle_shell/parts/basecolor.png"

report = json.loads(MAIN_REPORT.read_text(encoding="utf-8"))
robot = ET.parse(URDF).getroot()
urdf_joints = {}
origins = {"base_link": np.zeros(3)}
children = {}
joint_types = {}
for e in robot.findall("joint"):
    origin = e.find("origin")
    xyz = np.array(list(map(float, origin.get("xyz", "0 0 0").split())))
    rpy = np.array(list(map(float, origin.get("rpy", "0 0 0").split())))
    assert np.linalg.norm(rpy) == 0
    lim = e.find("limit")
    j = {"joint": e.get("name"), "type": e.get("type"),
         "parent": e.find("parent").get("link"), "child": e.find("child").get("link"),
         "origin_xyz_m": xyz.tolist(),
         "axis": list(map(float, e.find("axis").get("xyz").split())),
         "lower_rad": float(lim.get("lower")), "upper_rad": float(lim.get("upper"))}
    urdf_joints[j["joint"]] = j
    children[j["child"]] = j
    origins[j["child"]] = origins[j["parent"]] + xyz
    joint_types[e.get("type")] = joint_types.get(e.get("type"), 0) + 1

# Frozen mechanism reference (tail hierarchy), kept separate from visual candidates.
FROZEN_TAIL = {
    "tail_yaw": {"origin_xyz_m": urdf_joints["tail_yaw"]["origin_xyz_m"],
                 "axis": urdf_joints["tail_yaw"]["axis"],
                 "limits_rad": [urdf_joints["tail_yaw"]["lower_rad"], urdf_joints["tail_yaw"]["upper_rad"]],
                 "role": "整尾偏航基座：旋转 tail_base 及其全部子件"},
    "tail_L": {"origin_xyz_m_relative_to_tail_base": urdf_joints["tail_L"]["origin_xyz_m"],
               "axis": urdf_joints["tail_L"]["axis"],
               "limits_rad": [urdf_joints["tail_L"]["lower_rad"], urdf_joints["tail_L"]["upper_rad"]]},
    "tail_R": {"origin_xyz_m_relative_to_tail_base": urdf_joints["tail_R"]["origin_xyz_m"],
               "axis": urdf_joints["tail_R"]["axis"],
               "limits_rad": [urdf_joints["tail_R"]["lower_rad"], urdf_joints["tail_R"]["upper_rad"]]},
}

# Visual candidate hinges carried over from the delivered report (fan root seam centroids).
tb = report["tail_three_angle_candidate"]["parts"]
H_yaw = np.array(tb["eagle_tail_rudder"]["hinge_point_xyz_m"])        # yaw base hinge (visual candidate)
H_L = np.array(tb["eagle_tail_surface_L"]["hinge_point_xyz_m"])
H_R = np.array(tb["eagle_tail_surface_R"]["hinge_point_xyz_m"])


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_part_obj(path):
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
    return np.concatenate([v[f], uv[np.array(uvfaces)], normal[f]], axis=2)


def read_binding(part):
    return next(b for b in report["bindings"] if b["part"] == part)


meshes = {}
for b in report["bindings"]:
    meshes[b["part"]] = read_part_obj(OUT / b["part_file"])
zero_offsets = {b["part"]: np.array(b["zero_origin_xyz_m"]) for b in report["bindings"]}


def axis_rotation(axis, angle):
    a = np.array(axis, dtype=float); a /= np.linalg.norm(a)
    x, y, z = a; c = math.cos(angle); s = math.sin(angle); k = 1 - c
    return np.array([[c + x * x * k, x * y * k - z * s, x * z * k + y * s],
                     [y * x * k + z * s, c + y * y * k, y * z * k - x * s],
                     [z * x * k - y * s, z * y * k + x * s, c + z * z * k]])


# Mechanism-hierarchy transform graph. Wings unchanged (frozen URDF chain).
# Tail: yaw base at H_yaw; pitch nodes as children; center mesh on the base.
preview_joints = {}
for n in ["flap_L", "twist_L", "fold_L", "flap_R", "twist_R", "fold_R"]:
    preview_joints[n] = {"joint": n, "parent": urdf_joints[n]["parent"], "child": urdf_joints[n]["child"],
                         "frame_origin_xyz_m": urdf_joints[n]["origin_xyz_m"],
                         "axis": urdf_joints[n]["axis"],
                         "limit_rad": [urdf_joints[n]["lower_rad"], urdf_joints[n]["upper_rad"]],
                         "source": "frozen URDF, unchanged"}
preview_joints["tail_yaw_v"] = {"joint": "tail_yaw_v", "parent": "base_link", "child": "tailbase_visual",
                                "frame_origin_xyz_m": H_yaw.tolist(), "axis": [0, 0, 1],
                                "limit_rad": FROZEN_TAIL["tail_yaw"]["limits_rad"],
                                "source": "visual candidate hinge (fan root seam centroid); axis and limits referenced from URDF tail_yaw; hinge point is NOT the frozen mechanism origin"}
preview_joints["tail_pitch_L"] = {"joint": "tail_pitch_L", "parent": "tailbase_visual", "child": "eagle_tail_surface_L",
                                  "frame_origin_xyz_m": (H_L - H_yaw).tolist(), "axis": [0, 1, 0],
                                  "limit_rad": FROZEN_TAIL["tail_L"]["limits_rad"],
                                  "source": "visual candidate hinge carried by the yaw base, mirroring URDF tail_L under tail_base"}
preview_joints["tail_pitch_R"] = {"joint": "tail_pitch_R", "parent": "tailbase_visual", "child": "eagle_tail_surface_R",
                                  "frame_origin_xyz_m": (H_R - H_yaw).tolist(), "axis": [0, 1, 0],
                                  "limit_rad": FROZEN_TAIL["tail_R"]["limits_rad"],
                                  "source": "visual candidate hinge carried by the yaw base, mirroring URDF tail_R under tail_base"}
JOINTS = ["flap_L", "twist_L", "fold_L", "flap_R", "twist_R", "fold_R",
          "tail_yaw_v", "tail_pitch_L", "tail_pitch_R"]

PART_NODE = {"eagle_body_and_nose": "base_link",
             "eagle_tail_rudder": "tailbase_visual",
             "eagle_tail_surface_L": "eagle_tail_surface_L",
             "eagle_tail_surface_R": "eagle_tail_surface_R"}
for b in report["bindings"]:
    part = b["part"]
    if part.startswith("eagle_wing"):
        PART_NODE[part] = b["link"]


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
    ry = result["base_link"][0] @ axis_rotation([0, 0, 1], angles.get("tail_yaw_v", 0))
    result["tailbase_visual"] = (ry, H_yaw.copy())
    for n, (h, child) in {"tail_pitch_L": (H_L, "eagle_tail_surface_L"),
                          "tail_pitch_R": (H_R, "eagle_tail_surface_R")}.items():
        rp = ry @ axis_rotation([0, 1, 0], angles.get(n, 0))
        result[child] = (rp, H_yaw + ry @ (h - H_yaw))
    return result


def part_world(part, angles):
    r, t = transforms(angles)[PART_NODE[part]]
    p = meshes[part][:, :, :3].reshape(-1, 3) + zero_offsets[part] - t0_of(part)
    return p @ r.T + t


def t0_of(part):
    node = PART_NODE[part]
    return transforms({})[node][1]


checks = {}

# --- whole-tail yaw follows: all three tail meshes move; composition verified ----
probe = 0.3
base = transforms({})
yawed = transforms({"tail_yaw_v": probe})
follow = {}
for part in ["eagle_tail_rudder", "eagle_tail_surface_L", "eagle_tail_surface_R"]:
    w0 = part_world(part, {}); w1 = part_world(part, {"tail_yaw_v": probe})
    follow[part] = float(np.abs(w1 - w0).max())
assert all(v > 1e-3 for v in follow.values()), follow
# composition: L surface world rotation must equal Rz(yaw) @ Ry(pitch)
comp = transforms({"tail_yaw_v": probe, "tail_pitch_L": 0.2})
expected_r = axis_rotation([0, 0, 1], probe) @ axis_rotation([0, 1, 0], 0.2)
assert np.abs(comp["eagle_tail_surface_L"][0] - expected_r).max() < 1e-12
expected_t = H_yaw + axis_rotation([0, 0, 1], probe) @ (H_L - H_yaw)
assert np.abs(comp["eagle_tail_surface_L"][1] - expected_t).max() < 1e-12
checks["whole_tail_yaw_follows"] = {"probe_yaw_rad": probe, "max_displacement_per_tail_part_m": follow,
                                    "note": "整尾偏航时中央竖片与左右尾面全部跟随；父子合成与逐点变换一致"}

# --- pitch independence under the hierarchy -------------------------------------
indep = {}
for n in ["tail_pitch_L", "tail_pitch_R"]:
    tr = transforms({n: probe})
    moved, still = [], {}
    for part in meshes:
        w0 = part_world(part, {}); w1 = part_world(part, {n: probe})
        d = float(np.abs(w1 - w0).max())
        if d > 1e-6:
            moved.append(part)
        else:
            still[part] = d
    expected = preview_joints[n]["child"]
    assert moved == [expected], (n, moved)
    assert max(still.values()) < 1e-12
    indep[n] = {"moved": moved, "others_max_displacement_m": max(still.values())}
checks["tail_pitch_independence"] = indep

# --- no extension DOF -------------------------------------------------------------
assert joint_types.get("prismatic", 0) == 0 and set(joint_types) <= {"revolute"}, joint_types
checks["urdf_joint_types"] = {k: v for k, v in joint_types.items()}
checks["extension_dof_present"] = False

# --- wing root follow-up (requirement 5) -------------------------------------------
from collections import Counter


def open_boundary_world(part):
    tri = meshes[part][:, :, :3] + zero_offsets[part]
    coords = tri.reshape(-1, 3)
    uniq, inv = np.unique(coords, axis=0, return_inverse=True)
    cnt = Counter()
    for f in inv.reshape(-1, 3):
        fl = list(f)
        for a, b in zip(fl, fl[1:] + fl[:1]):
            cnt[(min(a, b), max(a, b))] += 1
    open_ids = sorted({i for e, c in cnt.items() if c == 1 for i in e})
    return uniq[open_ids]


def silhouette(tris):
    p = tris[:, :, :2]
    area = np.abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) -
                  (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0])) / 2
    return unary_union([Polygon(t) for t, a in zip(p, area) if a > 1e-14])


body_sil = silhouette(meshes["eagle_body_and_nose"][:, :, :3] + zero_offsets["eagle_body_and_nose"])
body_buf = body_sil.buffer(2e-3)
seam_stats = {}
for side in ["L", "R"]:
    part = f"eagle_wing_main_{side}"
    pts = open_boundary_world(part)
    inner = pts[np.abs(pts[:, 1]) < 0.065]
    inside_mask = np.array([body_buf.contains(Point(p[:2])) for p in inner])
    inside = int(inside_mask.sum())
    # Buried band: largest |y| up to which every open-boundary vertex is inside the body buffer.
    order = np.argsort(np.abs(inner[:, 1]))
    band = 0.0
    for i in order:
        if not inside_mask[i]:
            break
        band = abs(inner[i, 1])
    outside = inner[~inside_mask]
    # Before-correction comparison: undo the recorded wing shift and re-measure.
    wrep = report["wing_axis_realignment"][side]
    dx, dz = wrep["correction_applied"]["dx_m"], wrep["correction_applied"]["dz_m"]
    before_pts = inner + np.array([-dx, 0.0, -dz])
    before_inside = int(sum(body_buf.contains(Point(p[:2])) for p in before_pts))
    seam_stats[side] = {"root_perimeter_vertices_checked": int(len(inner)),
                        "after_correction": {"inside_or_within_2mm_of_body_silhouette": inside,
                                             "containment_ratio": float(inside / len(inner)),
                                             "contiguous_buried_band_abs_y_m": float(band),
                                             "outside_points_y_abs_range_m": ([float(np.abs(outside[:, 1]).min()),
                                                                               float(np.abs(outside[:, 1]).max())] if len(outside) else None)},
                        "before_correction": {"inside_or_within_2mm_of_body_silhouette": before_inside,
                                              "containment_ratio": float(before_inside / len(inner))}}
checks["wing_root_followup"] = {**seam_stats,
                                "tradeoff": "修正把扭转轴从约百分之八十八点七弦长移到约百分之二十（机构对齐），代价是翼根周界埋入率从百分之百降到约四到五成：冻结机构拍动扭转铰位于 x=0，比原模型翼身连接线（缝合线质心约 x=+0.075）靠后约七点五厘米，单一视觉平移无法同时满足两端。机构对齐版保留为主交付（绑定判据要求与现行机构一致）；缝合线对齐版数值已在主报告 wing_axis_realignment 登记为对照。两端同时满足需要实物机构资料（原始照片、机构图、铰点位置），为当前最高优先缺项",
                                "note": "带外的周界点属于翼面在机身肩部露出轮廓的部分；该数据只说明对齐候选的目视一致性，不能据此宣称真实连接通过"}

fold_gaps = {}
for ang in (0.4, 0.8, 1.2):
    tr = transforms({"fold_L": min(ang, urdf_joints["fold_L"]["upper_rad"])})
    tip = meshes["eagle_wing_tip_L"][:, :, :3].reshape(-1, 3) + zero_offsets["eagle_wing_tip_L"]
    seam = tip[np.abs((tip[:, 1]) - 0.14) < 5e-4]
    w0 = seam + (zero_offsets["eagle_wing_tip_L"] * 0)
    r0, t0v = base["outerwing_L"]
    r1, t1v = tr["outerwing_L"]
    moved = seam @ r1.T + t1v - (seam @ r0.T + t0v)
    fold_gaps[f"fold_{ang}rad_seam_point_displacement_m"] = float(np.linalg.norm(moved, axis=1).max())
checks["fold_seam_dynamic_gap_L"] = fold_gaps
checks["fold_seam_dynamic_gap_L"]["note"] = "刚性分件在折叠时切面点沿弧线分离；无柔性蒙皮，缝隙随角度增大，数值为最大位移"

# --- motion timeline ----------------------------------------------------------------
spec = importlib.util.spec_from_file_location("eaglex_existing_kinematics", ROOT / "modules/aero/kinematics.py")
kin = importlib.util.module_from_spec(spec); spec.loader.exec_module(kin)
from config import sim_config as cfg
wk = kin.WingKinematics(frequency=cfg.WING_DRIVE_CONFIG["frequency"],
                        amplitude_deg=cfg.WING_DRIVE_CONFIG["amplitude_deg"],
                        pitch_mode=cfg.WING_DRIVE_CONFIG["pitch_mode"],
                        alpha_set_deg=cfg.WING_DRIVE_CONFIG["alpha_set_deg"])
duration = 20.0
times = np.linspace(0, duration, 301)
poses, sections = [], []
TDEG = 20.0
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
        a.update(tail_pitch_L=math.radians(TDEG) * math.sin(math.pi * (t - 6)))
    elif t < 10:
        mode = "TAIL_R_SINGLE"
        a.update(tail_pitch_R=math.radians(TDEG) * math.sin(math.pi * (t - 8)))
    elif t < 12:
        mode = "TAIL_SYMMETRIC_COMMON_AOA"
        v = math.radians(TDEG) * math.sin(math.pi * (t - 10))
        a.update(tail_pitch_L=v, tail_pitch_R=v)
    elif t < 14:
        mode = "TAIL_DIFFERENTIAL"
        v = math.radians(TDEG) * math.sin(math.pi * (t - 12))
        a.update(tail_pitch_L=v, tail_pitch_R=-v)
    elif t < 16:
        mode = "WHOLE_TAIL_YAW"
        a.update(tail_yaw_v=math.radians(TDEG) * math.sin(math.pi * (t - 14)))
    elif t < 18:
        mode = "YAW_PLUS_DIFFERENTIAL"
        v = math.radians(TDEG) * math.sin(math.pi * (t - 16))
        a.update(tail_yaw_v=v, tail_pitch_L=v, tail_pitch_R=-v)
    else:
        mode = "COMBINED"
        ev = wk.evaluate(t - 18)
        envelope = min(1, (t - 18) / .25, (20 - t) / .25)
        fold = cfg.WING_DRIVE_CONFIG["fold_amp"] * .5 * (1 + math.sin(wk.omega * (t - 18) + 2.2)) * envelope
        a.update(flap_L=ev["phi"] * envelope, flap_R=-ev["phi"] * envelope,
                 twist_L=ev["alpha"] * envelope, twist_R=ev["alpha"] * envelope,
                 fold_L=fold, fold_R=-fold,
                 tail_yaw_v=math.radians(10) * math.sin(math.pi * (t - 18)) * envelope,
                 tail_pitch_L=math.radians(8) * math.sin(2 * math.pi * (t - 18)) * envelope,
                 tail_pitch_R=math.radians(8) * math.sin(2 * math.pi * (t - 18)) * envelope)
    for n, v in a.items():
        lo, hi = preview_joints[n]["limit_rad"]
        assert lo - 1e-8 <= v <= hi + 1e-8, (n, v)
    poses.append(a)
    sections.append(mode)
checks["joint_limits_passed"] = True

# --- GLB ----------------------------------------------------------------------------
g = {"asset": {"version": "2.0", "generator": "L8 offline mechanism-hierarchy preview, I-201 follow-up"},
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
    for jname, child in [("flap_" + side, "flapconn_" + side), ("twist_" + side, "innerwing_" + side), ("fold_" + side, "outerwing_" + side)]:
        j = preview_joints[jname]
        idx = len(g["nodes"]); node_ids[child] = idx
        g["nodes"].append({"name": child, "translation": j["frame_origin_xyz_m"], "rotation": [0, 0, 0, 1], "children": [],
                           "extras": {"urdf_joint": jname, "axis": j["axis"], "limit_rad": j["limit_rad"], "source": j["source"]}})
        g["nodes"][node_ids[j["parent"]]]["children"].append(idx)
idx = len(g["nodes"]); node_ids["tailbase_visual"] = idx
g["nodes"].append({"name": "tailbase_visual", "translation": H_yaw.tolist(), "rotation": [0, 0, 0, 1], "children": [],
                   "extras": {"preview_joint": "tail_yaw_v", "axis": [0, 0, 1], "limit_rad": FROZEN_TAIL["tail_yaw"]["limits_rad"],
                              "hinge_source": "visual candidate: fan root seam centroid",
                              "frozen_urdf_reference": {"joint": "tail_yaw", "origin_xyz_m": FROZEN_TAIL["tail_yaw"]["origin_xyz_m"]},
                              "role": "整尾偏航基座：中央竖片与左右尾面全部跟随"}})
g["nodes"][1]["children"].append(idx)
for n, (h, child) in {"tail_pitch_L": (H_L, "eagle_tail_surface_L"),
                      "tail_pitch_R": (H_R, "eagle_tail_surface_R")}.items():
    idx2 = len(g["nodes"]); node_ids[child] = idx2
    g["nodes"].append({"name": child, "translation": (h - H_yaw).tolist(), "rotation": [0, 0, 0, 1], "children": [],
                       "extras": {"preview_joint": n, "axis": [0, 1, 0],
                                  "limit_rad": preview_joints[n]["limit_rad"],
                                  "hinge_source": "visual candidate carried by the yaw base",
                                  "frozen_urdf_reference": {"joint": "tail_" + n[-1],
                                                            "origin_xyz_m_relative_to_tail_base": FROZEN_TAIL["tail_" + n[-1]]["origin_xyz_m_relative_to_tail_base"]}}})
    g["nodes"][node_ids["tailbase_visual"]]["children"].append(idx2)
for b in report["bindings"]:
    tri = meshes[b["part"]]
    node = PART_NODE[b["part"]]
    offset = zero_offsets[b["part"]] - t0_of(b["part"])
    p = (tri[:, :, :3].reshape(-1, 3) + offset)
    uv = tri[:, :, 3:5].reshape(-1, 2).copy(); uv[:, 1] = 1 - uv[:, 1]
    normals = tri[:, :, 5:8].reshape(-1, 3)
    normals = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-15)
    attr = {"POSITION": accessor(p, "VEC3", 34962), "NORMAL": accessor(normals, "VEC3", 34962), "TEXCOORD_0": accessor(uv, "VEC2", 34962)}
    mi = len(g["meshes"]); g["meshes"].append({"name": b["part"], "primitives": [{"attributes": attr, "material": 0, "mode": 4}]})
    ni = len(g["nodes"]); g["nodes"].append({"name": b["part"], "mesh": mi})
    g["nodes"][node_ids[node]]["children"].append(ni)
animation = {"name": "mechanism_hierarchy_tail", "samplers": [], "channels": []}
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
with (MECH / "eaglex_articulated_mechanism.glb").open("wb") as f:
    f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(blob)))
    f.write(struct.pack("<I4s", len(js), b"JSON")); f.write(js)
    f.write(struct.pack("<I4s", len(blob), b"BIN\0")); f.write(blob)

with (MECH / "joint_commands.csv").open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["time_s", "mode"] + JOINTS)
    for t, mode, a in zip(times, sections, poses):
        w.writerow([t, mode] + [a[n] for n in JOINTS])

# --- renderer -------------------------------------------------------------------------
tex = np.asarray(Image.open(TEXTURE).convert("RGB"))
CAMERAS = {"main": np.array([-.55, -1.15, 1.00]), "rear": np.array([-.95, -.55, .55]),
           "front": np.array([.85, -1.25, 1.05])}
prepared = []
for b in report["bindings"]:
    tri = meshes[b["part"]]
    uv = tri[:, :, 3:5].mean(axis=1)
    px = np.clip((uv[:, 0] % 1) * (tex.shape[1] - 1), 0, tex.shape[1] - 1).astype(int)
    py = np.clip((1 - uv[:, 1] % 1) * (tex.shape[0] - 1), 0, tex.shape[0] - 1).astype(int)
    colors = tex[py, px].astype(float)
    offset = zero_offsets[b["part"]] - t0_of(b["part"])
    prepared.append((b, tri[:, :, :3].reshape(-1, 3, 3) + offset, tri[:, :, 5:8].mean(axis=1), colors))
try:
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22)
    small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 17)
except OSError:
    font = small = ImageFont.load_default()
FOOT = "mechanism hierarchy: whole-tail yaw base + L/R pitch  |  no extension DOF  |  original meshes only"


def render_frame(index, cam_key):
    camera = CAMERAS[cam_key].copy(); camera /= np.linalg.norm(camera)
    right = np.cross([0, 0, 1], camera); right /= np.linalg.norm(right)
    up = np.cross(camera, right)
    project = np.array([right, up, camera]).T
    light = np.array([.3, -.5, .9]); light /= np.linalg.norm(light)
    tr = transforms(poses[index])
    positions, cols = [], []
    for b, p, n, col in prepared:
        r, o = tr[PART_NODE[b["part"]]]
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
    draw.text((20, 16), "Eagle X I-201 mech  |  " + sections[index] + f"  |  {times[index]:.2f}s", font=font, fill="white")
    draw.text((20, 614), FOOT, font=small, fill=(40, 55, 70))
    return im


import imageio.v2 as imageio
frames = []
for index in range(0, len(times) - 1, 2):
    im = render_frame(index, "main")
    frames.append(im)
    if index in [0, 60, 120, 180, 240, 270]:
        im.save(MECH / f"preview_{index:03d}.png")
with imageio.get_writer(str(MECH / "eaglex_mechanism_preview.mp4"), fps=15, codec="libx264", macro_block_size=2) as writer:
    for f in frames:
        writer.append_data(np.asarray(f))
CLIPS = {"tail_single_L": "rear", "tail_single_R": "rear", "tail_symmetric": "rear",
         "tail_differential": "rear", "whole_tail_yaw": "rear", "yaw_plus_differential": "rear",
         "flap": "front", "twist": "front", "fold": "front"}
MODE_OF = {"tail_single_L": "TAIL_L_SINGLE", "tail_single_R": "TAIL_R_SINGLE",
           "tail_symmetric": "TAIL_SYMMETRIC_COMMON_AOA", "tail_differential": "TAIL_DIFFERENTIAL",
           "whole_tail_yaw": "WHOLE_TAIL_YAW", "yaw_plus_differential": "YAW_PLUS_DIFFERENTIAL",
           "flap": "FLAP", "twist": "TWIST", "fold": "FOLD"}
clipdir = MECH / "clips"; clipdir.mkdir(exist_ok=True)
for clip, cam in CLIPS.items():
    idxs = [i for i, m in enumerate(sections) if m == MODE_OF[clip]]
    with imageio.get_writer(str(clipdir / f"{clip}.mp4"), fps=15, codec="libx264", macro_block_size=2) as writer:
        for i in idxs[::2]:
            writer.append_data(np.asarray(render_frame(i, cam)))
    render_frame(idxs[len(idxs) // 4], cam).save(clipdir / f"{clip}_mid.png")

# --- topology and axes figure -----------------------------------------------------------
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, axs = plt.subplots(1, 2, figsize=(13, 5.8), dpi=160)
ax = axs[0]
boxes = [("base_link", 0.5, 0.9), ("tail_yaw_v\n(visual hinge from fan root seam)", 0.5, 0.62),
         ("eagle_tail_rudder\n(central ridge, follows yaw only)", 0.16, 0.3),
         ("tail_pitch_L + surface_L", 0.84, 0.3), ("tail_pitch_R + surface_R", 0.5, 0.3)]
for name, x, y in boxes:
    ax.add_patch(plt.Rectangle((x - .14, y - .07), .28, .14, fill=False, lw=1.2))
    ax.text(x, y, name, ha="center", va="center", fontsize=8)
for (x0, y0), (x1, y1) in [((.5, .83), (.5, .69)), ((.44, .55), (.2, .37)), ((.5, .55), (.5, .37)), ((.56, .55), (.84, .37))]:
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0), arrowprops=dict(arrowstyle="->", lw=1))
ax.text(.52, .76, "axis z, ±0.6 rad", fontsize=7.5)
ax.text(.24, .45, "axis y, ±0.5 rad", fontsize=7.5)
ax.text(.53, .45, "axis y, ±0.5 rad", fontsize=7.5)
ax.set(title="Mechanism-hierarchy preview topology\n(mirrors frozen URDF: yaw base carries all tail parts)",
       xlim=(0, 1), ylim=(0.15, 1), axisbelow=True)
ax.axis("off")
ax = axs[1]
colors3 = {"eagle_tail_surface_L": "#168fa5", "eagle_tail_surface_R": "#ed983a", "eagle_tail_rudder": "#be3941"}
for name, c in colors3.items():
    tri = meshes[name][:, :, :3] + zero_offsets[name]
    shape = silhouette(tri)
    for poly in ([shape] if shape.geom_type == "Polygon" else shape.geoms):
        xy = np.array(poly.exterior.coords)
        ax.fill(xy[:, 1], xy[:, 0], color=c, alpha=.7)
ax.axvline(0.012, color="#444", ls=":", lw=1)
ax.axvline(-0.012, color="#444", ls=":", lw=1)
ax.scatter([H_yaw[1]], [H_yaw[0]], c="k", marker="P", s=130, zorder=5)
ax.scatter([H_L[1]], [H_L[0]], c="k", marker="*", s=130, zorder=5)
ax.scatter([H_R[1]], [H_R[0]], c="k", marker="*", s=130, zorder=5)
ax.annotate("yaw hinge H_yaw\n(%.4f, %.4f)" % (H_yaw[0], H_yaw[1]), xy=(H_yaw[1], H_yaw[0]),
            xytext=(-0.06, -0.14), fontsize=8, arrowprops=dict(arrowstyle="->", lw=.8))
ax.annotate("pitch hinge H_L\n(%.4f, %.4f)" % (H_L[0], H_L[1]), xy=(H_L[1], H_L[0]),
            xytext=(-0.02, 0.055), fontsize=8, arrowprops=dict(arrowstyle="->", lw=.8))
ax.annotate("pitch hinge H_R\n(%.4f, %.4f)" % (H_R[0], H_R[1]), xy=(H_R[1], H_R[0]),
            xytext=(-0.16, -0.075), fontsize=8, arrowprops=dict(arrowstyle="->", lw=.8))
ax.plot([H_yaw[1]], [H_yaw[0]], "kP")
ax.set(xlabel="Span y (m)", ylabel="x toward nose (m)",
       title="Tail axes in corrected frame (plot: horizontal=y, vertical=x)\nvisual candidate hinges; frozen URDF origins differ")
ax.axis("equal"); ax.grid(alpha=.2)
fig.suptitle("I-201 mechanism-hierarchy version: whole-tail yaw base + independent L/R pitch", fontsize=12)
fig.tight_layout()
fig.savefig(MECH / "tail_topology_and_axes.png"); plt.close(fig)

# --- report ------------------------------------------------------------------------------
sources = [URDF, MAIN_REPORT, REQUIREMENT, TEXTURE, ROOT / "logs/jane_dispatch_20261013/status_check_latest.json",
           HERE / "build_l8_i201.py", HERE / "build_l8_i201_mech.py"]
sources += [OUT / b["part_file"] for b in report["bindings"]]
mech_report = {
    "schema_version": 1, "date": "2026-10-13", "item": "I-201", "asset": "eagleX",
    "purpose": "接续交付：符合冻结机构层级的整尾偏航加左右独立尾面离线预览；原独立方向舵候选保留为对照",
    "simulator_used": False,
    "provenance": [{"path": str(p), "sha256": digest(p)} for p in sources if p.exists()],
    "owner_question_answer": {
        "question": "尾部是两侧差动加整体偏航，还是协同转动加偏航加伸缩",
        "answer": "现行冻结机构为两侧独立尾面（可同向可差动）加整尾偏航：base_link 经 tail_yaw（绕 z 的整尾偏航基座）到 tail_base，再分别经 tail_L、tail_R（各绕局部 y）到左右尾面；没有伸缩或收展自由度",
        "evidence": {"urdf_joint_types": joint_types,
                     "no_prismatic_or_extension_joint": True,
                     "mesh_shape_proves": "原网格外形只能支持外形切分，不能证明独立方向舵、舵机、铰轴或伸缩机构；不从外形补造该动作"}},
    "mechanism_hierarchy": {
        "topology": "base_link -> tail_yaw_v [visual candidate] -> { eagle_tail_rudder (follows yaw only), tail_pitch_L -> surface_L, tail_pitch_R -> surface_R }",
        "three_independent_coordinates": {
            "tail_yaw_v": {"axis": [0, 0, 1], "visual_hinge_xyz_m": H_yaw.tolist(),
                           "visual_hinge_source": "fan root seam centroid",
                           "frozen_urdf_reference": FROZEN_TAIL["tail_yaw"],
                           "limits_rad": FROZEN_TAIL["tail_yaw"]["limits_rad"],
                           "visual_hinge_is_frozen_mechanism_hinge": False},
            "tail_pitch_L": {"axis": [0, 1, 0], "visual_hinge_xyz_m": H_L.tolist(),
                             "carried_by": "yaw base; relative translation to yaw hinge: " + str((H_L - H_yaw).tolist()),
                             "frozen_urdf_reference": FROZEN_TAIL["tail_L"],
                             "limits_rad": FROZEN_TAIL["tail_L"]["limits_rad"],
                             "visual_hinge_is_frozen_mechanism_hinge": False},
            "tail_pitch_R": {"axis": [0, 1, 0], "visual_hinge_xyz_m": H_R.tolist(),
                             "carried_by": "yaw base; relative translation to yaw hinge: " + str((H_R - H_yaw).tolist()),
                             "frozen_urdf_reference": FROZEN_TAIL["tail_R"],
                             "limits_rad": FROZEN_TAIL["tail_R"]["limits_rad"],
                             "visual_hinge_is_frozen_mechanism_hinge": False}},
        "equivalent_parameterizations": {"left_right_plus_yaw": ["tail_pitch_L", "tail_pitch_R", "tail_yaw_v"],
                                          "common_differential_plus_yaw": {"common": "(L+R)/2", "differential": "(L-R)/2", "yaw": "tail_yaw_v"},
                                          "note": "绕横向轴的同向运动是左右尾面共同改变迎角，不能叫整个尾翼绕机身纵轴滚转；气动与滚转效应需另行验证"},
        "part_ranges_and_split_assumptions": report["tail_three_angle_candidate"]["split_basis"],
        "relation_to_delivered_candidate": "已交付的独立方向舵候选（三节点同挂 base_link、竖片独立偏航）保留为对照；本版中央竖片只随整尾偏航"},
    "verification": checks,
    "preview": {"duration_s": duration,
                "segments": ["0-2s拍动", "2-4s扭转", "4-6s折叠", "6-8s尾左单动", "8-10s尾右单动",
                             "10-12s左右同向（共同迎角）", "12-14s左右差动", "14-16s整尾偏航",
                             "16-18s偏航叠加差动", "18-20s组合"],
                "mode": "运动学角度驱动，固定机身；零仿真器",
                "physics_drive_tracking_verified": False, "production_model_updated": False},
    "limitations": ["视觉铰点为来源候选，与冻结机构铰点分开标注；实物铰点位置无资料",
                    "无伸缩或收展动作证据，未从外形补造",
                    "方向舵独立偏航仅为对照候选；本版按现行机构层级整尾偏航",
                    "刚性分件；整尾大角度偏航时尾部与机身后缘的间隙未做碰撞处理",
                    "重要独立复验须由未参与方案与施工的人承担"],
    "rollback": {"production_touched": False,
                 "method": "本版位于 output/mechanism_hierarchy/ 子目录，删除该子目录即回退；候选主版与原始交付不动"}}
(MECH / "tail_mechanism_report.json").write_text(json.dumps(mech_report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"mech_output": str(MECH), "checks": {k: (v if not isinstance(v, dict) else "see report") for k, v in checks.items()}},
                 ensure_ascii=False, indent=1))
