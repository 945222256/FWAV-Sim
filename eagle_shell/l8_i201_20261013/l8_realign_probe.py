"""Offline geometry probe for I-201 axis repositioning; no simulator, no physics or production writes.

零仿真器声明：本脚本不启动任何仿真器进程，仅离线读取 eagle_shell/parts/ 下的
网格分件与既有绑定清单，测量原模型连接结构（翼根开放边界、尾部风扇根部），
为拍动、扭转、折叠轴与尾部三独立角度的视觉轴位候选提供数据。
"""
from pathlib import Path
import json
from collections import Counter

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PARTS = ROOT / "eagle_shell/parts"

mf = json.loads((ROOT / "eagle_shell/out/bind_manifest.json").read_text(encoding="utf-8"))
scale = float(mf["scale"])
translate = np.array(mf["translate_global"], dtype=float) * np.array([-1., -1., 1.])
asset_rotation = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=float)


def read_obj_raw(path):
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
            faces.append([int(s[0]) - 1 for s in corners])
            uvfaces.append([int(s[1]) - 1 for s in corners])
    return np.array(verts), np.array(faces), np.array(uvs), np.array(uvfaces)


def to_mech(v):
    return (v @ asset_rotation.T) * scale + translate


def boundary_edges(faces):
    """Open boundary edges: edges appearing exactly once, keyed by sorted vertex pair."""
    cnt = Counter()
    for f in np.asarray(faces):
        fl = list(f)
        for a, b in zip(fl, fl[1:] + fl[:1]):
            cnt[(min(a, b), max(a, b))] += 1
    return [e for e, c in cnt.items() if c == 1]


report = {"date": "2026-10-13", "probe": "l8_realign_probe", "simulator_used": False}

# --- wing root structure -------------------------------------------------------
for side, fname in [("L", "wing_left.obj"), ("R", "wing_right.obj")]:
    v, f, uv, uvf = read_obj_raw(PARTS / fname)
    vm = to_mech(v)
    edges = boundary_edges(f)
    # Root = mechanism-side open boundary; span coordinate is |y_mech|.
    bedges = [e for e in edges if abs(vm[list(e), 1].mean()) < 0.06]
    bverts = sorted({i for e in bedges for i in e})
    pts = vm[bverts]
    report[f"wing_root_edge_{side}"] = {
        "asset_file": fname,
        "open_boundary_vertices_total": len({i for e in edges for i in e}),
        "root_band_vertices_y_abs_below_0p06": len(bverts),
        "mech_y_min_m": float(pts[:, 1].min()),
        "mech_y_max_m": float(pts[:, 1].max()),
        "mech_x_min_m": float(pts[:, 0].min()),
        "mech_x_max_m": float(pts[:, 0].max()),
        "mech_z_min_m": float(pts[:, 2].min()),
        "mech_z_max_m": float(pts[:, 2].max()),
        "centroid_mech_xyz_m": pts.mean(axis=0).tolist(),
        "note": "root band = open-boundary vertices with |y|<0.06 in mechanism frame under the CURRENT manifest transform",
    }

# --- full wing bounds in mechanism frame --------------------------------------
for side, fname in [("L", "wing_left.obj"), ("R", "wing_right.obj")]:
    v, f, _, _ = read_obj_raw(PARTS / fname)
    vm = to_mech(v)
    report[f"wing_bounds_{side}"] = {
        "x_min": float(vm[:, 0].min()), "x_max": float(vm[:, 0].max()),
        "y_min": float(vm[:, 1].min()), "y_max": float(vm[:, 1].max()),
        "z_min": float(vm[:, 2].min()), "z_max": float(vm[:, 2].max()),
    }

# --- body bounds ---------------------------------------------------------------
v, f, _, _ = read_obj_raw(PARTS / "body_tail.obj")
vm = to_mech(v)
report["body_bounds_mech"] = {
    "x_min": float(vm[:, 0].min()), "x_max": float(vm[:, 0].max()),
    "y_min": float(vm[:, 1].min()), "y_max": float(vm[:, 1].max()),
    "z_min": float(vm[:, 2].min()), "z_max": float(vm[:, 2].max()),
}

# --- tail fan structure --------------------------------------------------------
v, f, _, _ = read_obj_raw(PARTS / "head.obj")
vm = to_mech(v)
edges = boundary_edges(f)
report["tail_fan_bounds_mech"] = {
    "x_min": float(vm[:, 0].min()), "x_max": float(vm[:, 0].max()),
    "y_min": float(vm[:, 1].min()), "y_max": float(vm[:, 1].max()),
    "z_min": float(vm[:, 2].min()), "z_max": float(vm[:, 2].max()),
    "open_boundary_vertices": len({i for e in edges for i in e}),
    "open_boundary_edges": len(edges),
}
# Root of the fan = forward-most band (x near max, toward body).
for q in (0.90, 0.95, 0.98):
    thr = np.quantile(vm[:, 0], q)
    band = vm[vm[:, 0] >= thr]
    report[f"tail_fan_forward_band_q{int(q*100)}"] = {
        "x_threshold_m": float(thr),
        "vertices": int(len(band)),
        "y_min": float(band[:, 1].min()), "y_max": float(band[:, 1].max()),
        "z_min": float(band[:, 2].min()), "z_max": float(band[:, 2].max()),
        "z_median": float(np.median(band[:, 2])),
        "y_median": float(np.median(band[:, 1])),
    }

out = HERE / "l8_realign_probe_result.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
