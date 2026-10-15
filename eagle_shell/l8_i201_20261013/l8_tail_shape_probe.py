"""Offline tail-fan shape probe for I-201; no simulator, no physics or production writes.

零仿真器声明：本脚本不启动任何仿真器进程，仅离线测量原模型尾部风扇网格的
展向高度分布，为左右水平作动面与竖向方向舵的切割面提供数据依据。
"""
from pathlib import Path
import json

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PARTS = ROOT / "eagle_shell/parts"

mf = json.loads((ROOT / "eagle_shell/out/bind_manifest.json").read_text(encoding="utf-8"))
scale = float(mf["scale"])
translate = np.array(mf["translate_global"], dtype=float) * np.array([-1., -1., 1.])
asset_rotation = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=float)


def read_obj_tris(path):
    verts, faces = [], []
    for line in Path(path).read_text(encoding="ascii").splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            verts.append(list(map(float, p[1:4])))
        elif p[0] == "f":
            faces.append([int(s.split("/")[0]) - 1 for s in p[1:4]])
    v = np.array(verts)
    return (v @ asset_rotation.T) * scale + translate, np.array(faces)


vm, F = read_obj_tris(PARTS / "head.obj")
report = {"date": "2026-10-13", "probe": "l8_tail_shape_probe", "simulator_used": False,
          "part": "head.obj (original tail fan; legacy name incorrect)"}

# Spanwise (y) profile of the fan: vertex density, z extent per bin.
bins = np.arange(-0.10, 0.1001, 0.01)
profile = []
for lo, hi in zip(bins[:-1], bins[1:]):
    m = (vm[:, 1] >= lo) & (vm[:, 1] < hi)
    if not m.any():
        profile.append({"y_bin": [round(lo, 3), round(hi, 3)], "vertices": 0})
        continue
    pts = vm[m]
    profile.append({
        "y_bin": [round(float(lo), 3), round(float(hi), 3)],
        "vertices": int(m.sum()),
        "x_min": float(pts[:, 0].min()), "x_max": float(pts[:, 0].max()),
        "z_min": float(pts[:, 2].min()), "z_max": float(pts[:, 2].max()),
        "z_mean": float(pts[:, 2].mean()),
        "z_p95": float(np.quantile(pts[:, 2], 0.95)),
    })
report["spanwise_profile"] = profile

# Where does the fan reach its highest z? Center ridge vs side lobes.
order = np.argsort(-vm[:, 2])
report["top_60_vertices_y_stats"] = {
    "y_min": float(vm[order[:60], 1].min()), "y_max": float(vm[order[:60], 1].max()),
    "y_mean": float(vm[order[:60], 1].mean()),
    "z_min": float(vm[order[:60], 2].min()), "z_max": float(vm[order[:60], 2].max()),
}

# Root seam of the fan: forward open boundary near the body junction.
from collections import Counter
cnt = Counter()
for f in F:
    fl = list(f)
    for a, b in zip(fl, fl[1:] + fl[:1]):
        cnt[(min(a, b), max(a, b))] += 1
open_edges = [e for e, c in cnt.items() if c == 1]
bverts = sorted({i for e in open_edges for i in e})
bpts = vm[bverts]
fwd = bpts[bpts[:, 0] > np.quantile(vm[:, 0], 0.9)]
report["fan_root_seam"] = {
    "open_boundary_edges": len(open_edges),
    "definition": "open-boundary vertices with x above the 90th percentile of the fan",
    "vertices": int(len(fwd)),
    "x_min": float(fwd[:, 0].min()), "x_max": float(fwd[:, 0].max()),
    "y_min": float(fwd[:, 1].min()), "y_max": float(fwd[:, 1].max()),
    "z_min": float(fwd[:, 2].min()), "z_max": float(fwd[:, 2].max()),
    "centroid_xyz": fwd.mean(axis=0).tolist(),
}

out = HERE / "l8_tail_shape_probe_result.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report["spanwise_profile"], ensure_ascii=False, indent=0))
print("top60:", json.dumps(report["top_60_vertices_y_stats"], ensure_ascii=False))
print("seam:", json.dumps(report["fan_root_seam"], ensure_ascii=False))
