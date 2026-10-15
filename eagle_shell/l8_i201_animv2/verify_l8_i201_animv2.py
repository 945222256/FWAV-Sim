"""Independent verification entry for the animation-config v2 delivery; offline, no simulator.

零仿真器声明：本脚本不启动任何仿真器进程。复验人运行本脚本重算可机检项：
来源哈希、导出模型骨骼编号局部性、动画四元数归一、逐帧接缝与边长（抽样帧）、
自由度字段。运行：在 eagle_shell/l8_i201_animv2/ 目录下执行
G:/Playground/PartField/venv/Scripts/python.exe verify_l8_i201_animv2.py
"""
from pathlib import Path
import hashlib
import csv
import json
import struct

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
report = json.loads((OUT / "anim_config_v2_report.json").read_text(encoding="utf-8"))
results = {}

for entry in report["provenance"]:
    p = Path(entry["path"])
    assert p.exists(), entry["path"]
    assert hashlib.sha256(p.read_bytes()).hexdigest() == entry["sha256"], entry["path"]
results["provenance_hash_verified"] = len(report["provenance"])

raw = (OUT / "eaglex_anim_config_v2.glb").read_bytes()
results["glb_sha256"] = hashlib.sha256(raw).hexdigest()
magic, version, total = struct.unpack_from("<4sII", raw)
assert magic == b"glTF" and version == 2 and total == len(raw)
jlen, _ = struct.unpack_from("<I4s", raw, 12)
G = json.loads(raw[20:20 + jlen])
blen, _ = struct.unpack_from("<I4s", raw, 20 + jlen)
BIN = raw[28 + jlen:28 + jlen + blen]
TYP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def acc(i):
    a = G["accessors"][i]; v = G["bufferViews"][a["bufferView"]]
    off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    dt = "<f4" if a["componentType"] == 5126 else "<u2"
    return np.frombuffer(BIN, dtype=dt, count=a["count"] * TYP[a["type"]], offset=off).reshape(a["count"], -1)


# 1. skin joint indices are skin-local and in range (the numbering defect fix)
for skin in G["skins"]:
    nj = len(skin["joints"])
    for node in G["nodes"]:
        if node.get("skin") is not None and node.get("mesh") is not None:
            attrs = G["meshes"][node["mesh"]]["primitives"][0]["attributes"]
            if "JOINTS_0" not in attrs:
                continue
            j = acc(attrs["JOINTS_0"]); w = acc(attrs["WEIGHTS_0"])
            act = w > 0
            assert int((j[act] >= nj).sum()) == 0
            assert j[act].max() < nj
            assert np.abs(w.sum(axis=1) - 1).max() < 1e-6
results["skin_joint_indices_local_and_valid"] = True

# 2. animation quaternions normalized
for smp in G["animations"][0]["samplers"]:
    q = acc(smp["output"])
    assert np.abs(np.linalg.norm(q, axis=1) - 1).max() < 1e-6
results["animation_quaternions_normalized"] = True

# 3. per-frame seams and edges recomputed from the exported model (sampled frames)
par = {}
for i, n in enumerate(G["nodes"]):
    for c in n.get("children", []):
        par[c] = i
ROT = {}
for ch in G["animations"][0]["channels"]:
    if ch["target"]["path"] != "rotation":
        continue
    smp = G["animations"][0]["samplers"][ch["sampler"]]
    ROT[ch["target"]["node"]] = (acc(smp["input"]).ravel(), acc(smp["output"]))
times = acc(G["animations"][0]["samplers"][0]["input"]).ravel()


def q2m(q):
    x, y, z, w = q
    n = np.sqrt(x * x + y * y + z * z + w * w); x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


# Actual exported orientation versus the signed command, in a known body frame.
# This fails for the old body-y-as-glTF-y export, even though those quaternions are unit length.
C = np.array([[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]])
with (OUT / "joint_commands.csv").open(encoding="utf-8-sig", newline="") as fh:
    commands = list(csv.DictReader(fh))
assert len(commands) == len(times)
primary_axes = {"flap_L": [1., 0., 0.], "flap_R": [1., 0., 0.],
                "twist_L": [0., 1., 0.], "twist_R": [0., 1., 0.],
                "tail_psi": [0., 0., 1.], "tail_phi": [1., 0., 0.]}
axis_results = {}
for ni, (ts, qs) in ROT.items():
    n = G["nodes"][ni]; ex = n["extras"]; key = ex["angle_key"]
    declared_g = np.array(ex["axis_gltf"], dtype=float)
    body_axis = np.array(primary_axes.get(key, C.T @ declared_g), dtype=float)
    body_axis /= np.linalg.norm(body_axis)
    assert np.abs(declared_g - C @ body_axis).max() < 1e-6, n["name"]
    ax, ay, az = body_axis
    K = np.array([[0., -az, ay], [az, 0., -ax], [-ay, ax, 0.]])
    maxerr = 0.
    for ti, q in enumerate(qs):
        angle = float(commands[ti][key]) * ex["angle_fraction"]
        expected_body = np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)
        err = float(np.abs(q2m(q) - C @ expected_body @ C.T).max())
        maxerr = max(maxerr, err)
        assert err < 2e-6, (n["name"], ti, err)
    axis_results[n["name"]] = maxerr
results["exported_signed_motion_axes"] = {"frames": len(times), "animated_nodes": len(axis_results),
                                           "max_rotation_matrix_error": max(axis_results.values()),
                                           "primary_axes_body": primary_axes, "passed": True}


def globs(ti):
    out = {}

    def tf(i, R, T):
        n = G["nodes"][i]
        if i in ROT:
            t, q = ROT[i]; k = int(np.argmin(np.abs(t - times[ti]))); Rl = q2m(q[k])
        elif "rotation" in n:
            Rl = q2m(n["rotation"])
        else:
            Rl = np.eye(3)
        R2 = R @ Rl; T2 = T + R @ np.array(n.get("translation", [0, 0, 0]))
        out[i] = (R2, T2)
        for c in n.get("children", []):
            tf(c, R2, T2)

    for rc in G["scenes"][0]["nodes"]:
        tf(rc, np.eye(3), np.zeros(3))
    return out


MD = {}
for node in G["nodes"]:
    if node.get("mesh") is None:
        continue
    attrs = G["meshes"][node["mesh"]]["primitives"][0]["attributes"]
    MD[G["meshes"][node["mesh"]]["name"]] = {
        "node": G["nodes"].index(node), "pos": acc(attrs["POSITION"]).astype(np.float64),
        "joints": acc(attrs["JOINTS_0"]).astype(np.int64) if "JOINTS_0" in attrs else None,
        "weights": acc(attrs["WEIGHTS_0"]).astype(np.float64) if "WEIGHTS_0" in attrs else None,
        "skin": node.get("skin")}


def world(name, ti):
    d = MD[name]; GG = globs(ti); v = d["pos"]
    if d["skin"] is None:
        R, T = GG[par[d["node"]]]
        return v @ R.T + T
    skin = G["skins"][d["skin"]]
    ibm = acc(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
    out = np.zeros_like(v); j, w = d["joints"], d["weights"]
    used = np.abs(w) > 0
    for slot in range(4):
        m = used[:, slot]
        if not m.any():
            continue
        for jidx in np.unique(j[m, slot]):
            sel = m & (j[:, slot] == jidx)
            R, T = GG[skin["joints"][jidx]]
            M = np.eye(4); M[:3, :3] = R; M[:3, 3] = T
            hv = np.c_[v[sel], np.ones(sel.sum())] @ (M @ ibm[jidx]).T
            out[sel] += w[sel, slot][:, None] * hv[:, :3]
    return out


def bindT(i):
    t = np.zeros(3)
    while i in par:
        t = t + np.array(G["nodes"][i].get("translation", [0, 0, 0]))
        i = par[i]
    return t


# Check standard column-major inverse binds independently of the exporter renderer.
ib_error = 0.
for skin in G["skins"]:
    ibm = acc(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
    for j, ni in enumerate(skin["joints"]):
        bind = np.eye(4); bind[:3, 3] = bindT(ni)
        err = float(np.abs(bind @ ibm[j] - np.eye(4)).max())
        ib_error = max(ib_error, err)
        assert err < 1e-6, (ni, err)
results["column_major_inverse_bind_identity_max_error"] = ib_error


# Measure the actual mesh: incidence-only must tilt in x-z, without a horizontal sweep.
incidence = {}
for side in ["L", "R"]:
    part = "eagle_wing_main_" + side
    zero = world(part, 0) @ C
    zidx = [i for i, r in enumerate(commands) if r["mode"] == "TWIST_INHERIT"]
    entries = []
    for label, ti in [("positive", max(zidx, key=lambda i: float(commands[i]["twist_" + side]))),
                      ("negative", min(zidx, key=lambda i: float(commands[i]["twist_" + side])))]:
        points = world(part, ti) @ C
        span_change = float(np.abs(points[:, 1] - zero[:, 1]).max())
        height_change = float(np.abs(points[:, 2] - zero[:, 2]).max())
        assert span_change < 1e-6 and height_change > .01, (side, label, span_change, height_change)
        entries.append({"sign": label, "time_s": float(times[ti]), "span_coordinate_change_m": span_change,
                        "height_coordinate_change_m": height_change})
    incidence[side] = entries
results["incidence_mesh_tilts_without_horizontal_sweep"] = incidence

whole_tail = {}
for mode, fixed_coord in [("TAIL_YAW_WHOLE", 2), ("TAIL_ROLL_WHOLE", 0)]:
    idxs = [i for i, r in enumerate(commands) if r["mode"] == mode]
    for part in ["eagle_tail_fin", "eagle_tail_flat_L", "eagle_tail_flat_R"]:
        zero = world(part, 0) @ C
        maximum = max(float(np.abs((world(part, ti) @ C)[:, fixed_coord] - zero[:, fixed_coord]).max()) for ti in idxs)
        assert maximum < 1e-6, (mode, part, maximum)
        whole_tail[mode + "/" + part] = maximum
results["whole_tail_single_axis_coordinate_invariants"] = whole_tail


SEAMS = [("wing_main_tip_L", "eagle_wing_main_L", "eagle_wing_tip_L", 0.14),
         ("wing_main_tip_R", "eagle_wing_main_R", "eagle_wing_tip_R", -0.14),
         ("tail_flat_central_L", "eagle_tail_fin", "eagle_tail_flat_L", 0.012),
         ("tail_flat_central_R", "eagle_tail_fin", "eagle_tail_flat_R", -0.012)]
sample_frames = list(range(0, len(times), 10))
worst = {}
for name, pa, pb, plane in SEAMS:
    da, db = MD[pa], MD[pb]
    A = da["pos"] + bindT(par[da["node"]]) if da["skin"] is None else da["pos"]
    B = db["pos"] + bindT(par[db["node"]]) if db["skin"] is None else db["pos"]
    km = {}
    for jx, q in enumerate(B):
        km.setdefault(tuple(np.round(q * 1e6)), jx)
    ia, ib = [], []
    for i, p in enumerate(A):
        jx = km.get(tuple(np.round(p * 1e6)))
        if jx is not None:
            ia.append(i); ib.append(jx)
    assert len(ia) > 50, name
    gmax = 0.0
    for ti in sample_frames:
        gaps = np.linalg.norm(world(pa, ti)[ia] - world(pb, ti)[ib], axis=1)
        gmax = max(gmax, float(gaps.max()))
    worst[name] = gmax
    assert gmax < 1e-5, (name, gmax)
results["sampled_seam_max_gap_m"] = {k: f"{v:.2e}" for k, v in worst.items()}

EDGE_W = {"eagle_wing_tip_L": (0.14, 0.10), "eagle_wing_tip_R": (-0.14, 0.10),
          "eagle_tail_flat_L": (0.012, 0.07), "eagle_tail_flat_R": (-0.012, 0.07)}
eres = {}
for part, (plane, W) in EDGE_W.items():
    v0 = MD[part]["pos"]
    idx = np.arange(len(v0)).reshape(-1, 3)
    pairs = np.concatenate([np.stack([idx[:, a], idx[:, b]], axis=1) for a, b in [(0, 1), (1, 2), (2, 0)]])
    L0 = np.linalg.norm(v0[pairs[:, 0]] - v0[pairs[:, 1]], axis=1)
    # mech span y = -z_gltf; distance from the seam plane along the outward normal
    s = np.maximum(np.sign(plane) * (-v0[:, 2]) - abs(plane), 0.0)
    out_zone = np.minimum(s[pairs[:, 0]], s[pairs[:, 1]]) >= W
    assert out_zone.sum() > 0, part
    mx = 0.0
    for ti in sample_frames:
        wv = world(part, ti)
        L1 = np.linalg.norm(wv[pairs[:, 0]] - wv[pairs[:, 1]], axis=1)
        mx = max(mx, float(np.abs((L1 - L0) / np.maximum(L0, 1e-15))[out_zone].max()))
    eres[part] = mx
    assert mx < 0.01, (part, mx)
results["sampled_outside_zone_edge_max_change"] = {k: f"{v:.2e}" for k, v in eres.items()}

dof = report["degrees_of_freedom"]
assert dof["count_independent"] == 4 and "linked_candidate" in dof
assert report["glb_joint_index_check"]["passed"] is True
results["dof_bookkeeping_present"] = True

results["result"] = "machine_checks_passed"
results["independent_acceptance"] = "未完成；抽样帧复算通过不代替全帧与目检；重要独立复验由未参与方案与施工的人承担"
(OUT / "delivery_checks.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(results, ensure_ascii=False, indent=1))
