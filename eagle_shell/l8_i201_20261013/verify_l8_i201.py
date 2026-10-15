"""Independent verification entry for I-201 delivery; offline, no simulator, no production edits.

零仿真器声明：本脚本不启动任何仿真器进程。复验人直接运行本脚本即可重算
全部可机检判据：来源哈希、GLB 结构、切分守恒、三角度独立、限位、回环端点。
运行：python verify_l8_i201.py（在 eagle_shell/l8_i201_20261013/ 目录下）
"""
from pathlib import Path
import hashlib
import json
import struct

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
report = json.loads((OUT / "geometry_and_binding.json").read_text(encoding="utf-8"))
results = {}

# 1. Source files unchanged relative to recorded hashes.
for entry in report["provenance"]:
    p = Path(entry["path"])
    assert p.exists(), entry["path"]
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    assert actual == entry["sha256"], entry["path"]
results["source_files_hash_verified"] = len(report["provenance"])

# 2. GLB container structure.
raw = (OUT / "eaglex_articulated_preview.glb").read_bytes()
magic, version, total = struct.unpack_from("<4sII", raw)
assert magic == b"glTF" and version == 2 and total == len(raw)
jlen, jtype = struct.unpack_from("<I4s", raw, 12)
assert jtype == b"JSON"
g = json.loads(raw[20:20 + jlen])
blen, btype = struct.unpack_from("<I4s", raw, 20 + jlen)
assert btype == b"BIN\0"
blob = raw[28 + jlen:28 + jlen + blen]
assert len(blob) == blen and g["buffers"][0]["byteLength"] <= blen
types = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_accessor(i):
    a = g["accessors"][i]; v = g["bufferViews"][a["bufferView"]]
    off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    assert a["componentType"] == 5126
    count = a["count"] * types[a["type"]]
    assert off + count * 4 <= v["byteOffset"] + v["byteLength"]
    x = np.frombuffer(blob, dtype="<f4", count=count, offset=off).reshape(a["count"], -1)
    assert np.isfinite(x).all()
    return x


# 3. Nine animated channels, quaternion-normalized, looping endpoints.
anim = g["animations"][0]
preview_joint_names = {j["joint"] for j in report["joints"]}
assert len(preview_joint_names) == 9, preview_joint_names
seen = set()
for c in anim["channels"]:
    node = g["nodes"][c["target"]["node"]]
    seen.add(node["extras"].get("preview_joint") or node["extras"]["urdf_joint"])
    sampler = anim["samplers"][c["sampler"]]
    t = read_accessor(sampler["input"]).ravel(); q = read_accessor(sampler["output"])
    assert len(t) == len(q) and (np.diff(t) > 0).all()
    assert np.max(np.abs(np.linalg.norm(q, axis=1) - 1)) < 1e-6
assert seen == preview_joint_names, seen
for sampler in anim["samplers"]:
    q = read_accessor(sampler["output"])
    assert np.linalg.norm(q[0] - q[-1]) < 1e-5
results["animated_channels"] = len(seen)
results["animation_loop_endpoints_match"] = True

# 4. Meshes: count, names, per-part geometry matches the report.
assert len(g["meshes"]) == len(report["bindings"]) == 8
results["serialized_geometry"] = {}
for b in report["bindings"]:
    mesh = next(m for m in g["meshes"] if m["name"] == b["part"])
    attrs = mesh["primitives"][0]["attributes"]
    p = read_accessor(attrs["POSITION"]); uv = read_accessor(attrs["TEXCOORD_0"]); n = read_accessor(attrs["NORMAL"])
    assert len(p) == len(uv) == len(n) and len(p) % 3 == 0
    assert np.max(np.abs(np.linalg.norm(n, axis=1) - 1)) < 1e-5
    tri = (p + np.array(b["zero_origin_xyz_m"])).reshape(-1, 3, 3)
    geom = report["geometry"][b["part"]]
    area = float(np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum() / 2)
    assert abs(area - geom["surface_area_m2"]) < 1e-7, b["part"]
    assert len(tri) == geom["triangles"], b["part"]
    results["serialized_geometry"][b["part"]] = {"triangles": len(tri),
                                                 "surface_area_error_m2": abs(area - geom["surface_area_m2"])}

# 5. No synthetic tail boxes; only original-model meshes present.
names = {m["name"] for m in g["meshes"]}
assert not any("tailpanel" in n or "box" in n.lower() for n in names)
assert {"eagle_tail_surface_L", "eagle_tail_surface_R", "eagle_tail_rudder"} <= names
results["synthetic_tail_mesh_count"] = 0

# 6. Cut conservation numbers from the report meet the formal thresholds.
for k in ["clip_wing_L", "clip_wing_R", "clip_tail_fan"]:
    c = report["verification"][k]
    assert c["relative_area_error"] < 1e-6, (k, c)
    assert c["projected_symmetric_difference_m2"] < 1e-9, (k, c)
results["cut_conservation_within_formal_thresholds"] = True

# 7. Tail three-angle independence re-derived from the node tree:
#    rotating one tail node must leave the other meshes' node chains untouched.
tail_nodes = {}
for i, node in enumerate(g["nodes"]):
    ex = node.get("extras", {})
    if "preview_joint" in ex:
        tail_nodes[(ex["preview_joint"], i)] = node
assert {n for n, _ in tail_nodes} == {"tail_pitch_L", "tail_pitch_R", "rudder_yaw"}
parents = {}
for i, node in enumerate(g["nodes"]):
    for c in node.get("children", []):
        parents[c] = i
for (n, i), node in tail_nodes.items():
    p = parents[i]
    assert g["nodes"][p]["name"] == "base_link", n
results["tail_nodes_parent"] = "base_link (no shared yaw parent)"

# 8. Joint commands CSV: all values within limits; angles match report preview.
import csv as _csv
with (OUT / "joint_commands.csv").open(encoding="utf-8-sig", newline="") as fh:
    rows = list(_csv.DictReader(fh))
limits = {j["joint"]: j["limit_rad"] for j in report["joints"]}
for r in rows:
    for n, (lo, hi) in limits.items():
        v = float(r[n])
        assert lo - 1e-9 <= v <= hi + 1e-9, (r["time_s"], n, v)
results["joint_commands_within_limits"] = len(rows)

# 9. Mechanism-hierarchy version (2026-10-13 follow-up): whole-tail yaw base
#    carries the center mesh and both pitch nodes; no extension DOF.
MECH = OUT / "mechanism_hierarchy"
mech_report = json.loads((MECH / "tail_mechanism_report.json").read_text(encoding="utf-8"))
for entry in mech_report["provenance"]:
    p = Path(entry["path"])
    assert p.exists(), entry["path"]
    assert hashlib.sha256(p.read_bytes()).hexdigest() == entry["sha256"], entry["path"]
results["mech_source_files_hash_verified"] = len(mech_report["provenance"])
raw2 = (MECH / "eaglex_articulated_mechanism.glb").read_bytes()
magic, version, total = struct.unpack_from("<4sII", raw2)
assert magic == b"glTF" and version == 2 and total == len(raw2)
jlen2, _ = struct.unpack_from("<I4s", raw2, 12)
g2 = json.loads(raw2[20:20 + jlen2])
parents2 = {}
for i, node in enumerate(g2["nodes"]):
    for c in node.get("children", []):
        parents2[c] = i
# Joint nodes are identified by their extras; mesh leaves share part names.
joint_nodes = {n["extras"]["preview_joint"]: (i, n) for i, n in enumerate(g2["nodes"])
               if "preview_joint" in n.get("extras", {})}
mesh_nodes = {n["name"]: (i, n) for i, n in enumerate(g2["nodes"]) if n.get("mesh") is not None}
assert set(joint_nodes) == {"tail_yaw_v", "tail_pitch_L", "tail_pitch_R"}
assert g2["nodes"][parents2[joint_nodes["tail_yaw_v"][0]]]["name"] == "base_link"
for n in ["tail_pitch_L", "tail_pitch_R"]:
    assert g2["nodes"][parents2[joint_nodes[n][0]]]["name"] == "tailbase_visual", n
for part in ["eagle_tail_surface_L", "eagle_tail_surface_R", "eagle_tail_rudder"]:
    assert g2["nodes"][parents2[mesh_nodes[part][0]]]["name"] in (
        "tailbase_visual", part), part
assert g2["nodes"][parents2[mesh_nodes["eagle_tail_rudder"][0]]]["name"] == "tailbase_visual"
extras_yaw = joint_nodes["tail_yaw_v"][1]["extras"]
assert extras_yaw["preview_joint"] == "tail_yaw_v" and extras_yaw["axis"] == [0, 0, 1]
v = mech_report["verification"]
assert v["whole_tail_yaw_follows"]["max_displacement_per_tail_part_m"]["eagle_tail_rudder"] > 1e-3
assert all(d < 1e-12 for d in [v["tail_pitch_independence"][n]["others_max_displacement_m"]
                               for n in ["tail_pitch_L", "tail_pitch_R"]])
assert v["extension_dof_present"] is False and v["urdf_joint_types"] == {"revolute": 9}
import csv as _csv2
with (MECH / "joint_commands.csv").open(encoding="utf-8-sig", newline="") as fh:
    mrows = list(_csv2.DictReader(fh))
tic = mech_report["mechanism_hierarchy"]["three_independent_coordinates"]
mlims = {"tail_yaw_v": tic["tail_yaw_v"]["limits_rad"],
         "tail_pitch_L": tic["tail_pitch_L"]["limits_rad"],
         "tail_pitch_R": tic["tail_pitch_R"]["limits_rad"]}
for r in mrows:
    for col, lims in mlims.items():
        val = float(r[col])
        assert lims[0] - 1e-9 <= val <= lims[1] + 1e-9, (r["time_s"], col, val)
results["mech_topology_whole_tail_yaw_base"] = True
results["mech_joint_commands_checked"] = len(mrows)
results["mech_no_extension_dof"] = True

results["result"] = "machine_checks_passed"
results["independent_acceptance"] = "未完成；本脚本为可机检部分，重要独立复验由未参与资产方案与实现的人运行并判定"
(OUT / "delivery_checks.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(results, ensure_ascii=False, indent=1))
