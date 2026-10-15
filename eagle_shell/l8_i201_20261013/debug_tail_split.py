"""Debug probe for the tail three-way split conservation; offline, no simulator."""
from pathlib import Path
import json
import numpy as np
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCE_PARTS = ROOT / "eagle_shell/parts"
mf = json.loads((ROOT / "eagle_shell/out/bind_manifest.json").read_text(encoding="utf-8"))
scale = float(mf["scale"])
translate = np.array(mf["translate_global"], dtype=float) * np.array([-1., -1., 1.])
asset_rotation = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=float)


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
    p = (v[f] @ asset_rotation.T) * scale + translate
    return np.concatenate([p, uv[np.array(uvfaces)]], axis=2)


def surface_area(tris):
    return float(np.linalg.norm(np.cross(tris[:, 1, :3] - tris[:, 0, :3],
                                         tris[:, 2, :3] - tris[:, 0, :3]), axis=1).sum() / 2)


def projection(tris):
    from shapely.geometry import Polygon
    p = tris[:, :, :2]
    area = np.abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) -
                  (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0])) / 2
    return unary_union([Polygon(t) for t, a in zip(p, area) if a > 1e-14])


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


tail = read_obj(SOURCE_PARTS / "head.obj")
full = projection(tail)
rud = clip(clip(tail, 0.012, 1.0, True), -0.012, 1.0, False)
lft = clip(tail, 0.012, 1.0, False)
rgt = clip(tail, -0.012, 1.0, True)
comb = unary_union([projection(rud), projection(lft), projection(rgt)])
print('fan proj area', full.area, 'combined', comb.area)
print('symdiff', full.symmetric_difference(comb).area)
print('surf before', surface_area(tail), 'after', surface_area(rud) + surface_area(lft) + surface_area(rgt))
print('rel surf err', abs(surface_area(rud) + surface_area(lft) + surface_area(rgt) - surface_area(tail)) / surface_area(tail))
for nm, p in [('rud', rud), ('lft', lft), ('rgt', rgt)]:
    print(nm, len(p), 'tris proj', projection(p).area, 'ybounds', p[:, :, 1].min(), p[:, :, 1].max())
