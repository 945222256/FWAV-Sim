# -*- coding: utf-8 -*-
"""og_mesh_gen.py -- OpenFOAM blockMeshDict generator for 2-D O-grids.

Purpose (I-119 bench cases):
  mode steady : single O-grid around a wing section (ellipse or stadium
                plate) out to a far-field circle.  Used by bench 1
                (flat-plate normal-force slope).
  mode flap   : two-region mesh: a pitching wing inside a circular AMI
                cylinder (cellZone rotZone) plus a static background
                annulus.  Used by bench 2 (Wang 2004 2-D hovering).

The wing chord is c = 1 (half chord a = 0.5).  Mesh depth in z is 1.
All output is ASCII.

Shapes:
  ellipse  : semi-axes (0.5, e*0.5).  Wang (2000 JFM; 2004 JEB) used
             e = 0.125 for the published 2-D computations.
  stadium  : flat plate with semicircular end caps, total thickness 2h
             with h = e*0.5 (thin flat-plate approximation).

Usage:
  python og_mesh_gen.py --mode steady --shape ellipse --e 0.125 \
      --rfar 20 --nr 72 --ntheta 160 --first 0.004 > blockMeshDict
  python og_mesh_gen.py --mode flap --shape ellipse --e 0.125 \
      --rami 1.5 --rfar 15 --nr 40 --ntheta 160 --first 0.004 \
      --nr-bg 52 --ntheta-bg 160 --first-bg 0.05 > blockMeshDict
"""
import argparse
import math
import sys

HEADER = """/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      F ield           | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\      O peration       | Version:  v2506                                 |
|   \\\\    /   A nd          | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""


def fmt(v):
    return "%.6f" % v


def ellipse_points(a, b, ntheta):
    """Boundary points, uniform in parameter angle, start at (a, 0),
    counter-clockwise."""
    return [(a * math.cos(2.0 * math.pi * k / ntheta),
             b * math.sin(2.0 * math.pi * k / ntheta))
            for k in range(ntheta)]


def stadium_points(a, h, nflat, ncap):
    """Stadium plate boundary starting at (a, 0) counter-clockwise:
    right cap, top flat, left cap, bottom flat."""
    pts = []
    for k in range(ncap):
        th = -0.5 * math.pi + math.pi * k / ncap
        pts.append((a - h + h * math.cos(th), h * math.sin(th)))
    for k in range(nflat):
        pts.append(((a - h) - 2.0 * (a - h) * k / nflat, h))
    for k in range(ncap):
        th = 0.5 * math.pi + math.pi * k / ncap
        pts.append((-(a - h) + h * math.cos(th), h * math.sin(th)))
    for k in range(nflat):
        pts.append((-(a - h) + 2.0 * (a - h) * k / nflat, -h))
    return pts


def circle_points(r, ntheta):
    return ellipse_points(r, r, ntheta)


def quad_corners(pts):
    """Indices of quadrant corners: rightmost, topmost (x closest to 0
    among y-max), leftmost, bottommost (x closest to 0 among y-min)."""
    ymax = max(p[1] for p in pts)
    ymin = min(p[1] for p in pts)
    i_r = max(range(len(pts)), key=lambda i: pts[i][0])
    i_t = min([i for i, p in enumerate(pts) if abs(p[1] - ymax) < 1e-12],
              key=lambda i: abs(pts[i][0]))
    i_l = max(range(len(pts)), key=lambda i: -pts[i][0])
    i_b = min([i for i, p in enumerate(pts) if abs(p[1] - ymin) < 1e-12],
              key=lambda i: abs(pts[i][0]))
    return [i_r, i_t, i_l, i_b]


def ring_from_shape(pts, radius):
    """Circle of given radius; each point on the ray from origin through
    the corresponding shape point."""
    out = []
    for (x, y) in pts:
        r = math.hypot(x, y)
        out.append((radius * x / r, radius * y / r))
    return out


class MeshWriter(object):
    """Named vertices are translated to vertex indices at render time
    (classic blockMesh syntax, portable across versions)."""

    def __init__(self):
        self.vorder = []          # vertex names in insertion order
        self.vidx = {}            # name -> index
        self.vcoord = {}          # name -> (x, y, z)
        self.edges = []           # (kind, nameA, nameB, payload)
        self.blocks = []          # (names8, zone, (n1,n2,n3), grading)
        self.patches = []         # (name, type, [face name-tuples], extra)

    def add_vertex(self, name, x, y, z):
        self.vorder.append(name)
        self.vidx[name] = len(self.vorder) - 1
        self.vcoord[name] = (x, y, z)

    def add_spline(self, a, b, through_pts, z):
        self.edges.append(("spline", a, b, (through_pts, z)))

    def add_arc(self, a, b, via, z):
        self.edges.append(("arc", a, b, (via, z)))

    def add_block(self, names8, zone, ncells, grading):
        self.blocks.append((names8, zone, ncells, grading))

    def render(self):
        L = [HEADER, "scale 1;", "", "vertices", "("]
        for nm in self.vorder:
            x, y, z = self.vcoord[nm]
            L.append("    (%s %s %s) // %s" % (fmt(x), fmt(y), fmt(z), nm))
        L.append(");")
        L += ["", "edges", "("]
        for kind, a, b, (payload, z) in self.edges:
            ia, ib = self.vidx[a], self.vidx[b]
            if kind == "spline":
                L.append("    spline %d %d" % (ia, ib))
                L.append("    (")
                for p in payload:
                    L.append("        (%s %s %s)" % (fmt(p[0]), fmt(p[1]),
                                                     fmt(z)))
                L.append("    )")
            else:
                L.append("    arc %d %d (%s %s %s)" % (
                    ia, ib, fmt(payload[0]), fmt(payload[1]), fmt(z)))
        L.append(");")
        L += ["", "blocks", "("]
        for names8, zone, (n1, n2, n3), grading in self.blocks:
            idxs = " ".join(str(self.vidx[nm]) for nm in names8)
            zone_txt = (" " + zone) if zone else ""
            L.append("    hex (%s)%s (%d %d %d) simpleGrading (%s 1 1)"
                     % (idxs, zone_txt, n1, n2, n3, "%.4f" % grading))
        L.append(");")
        L += ["", "boundary", "("]
        for pname, ptype, faces, extra in self.patches:
            L.append("    %s" % pname)
            L.append("    {")
            L.append("        type %s;" % ptype)
            for k, v in (extra or {}).items():
                L.append("        %s %s;" % (k, v))
            L.append("        faces")
            L.append("        (")
            for f in faces:
                L.append("            (%s)" % " ".join(
                    str(self.vidx[nm]) for nm in f))
            L.append("        );")
            L.append("    }")
        L.append(");")
        L += ["", "mergePatchPairs", "(", ");"]
        return "\n".join(L) + "\n"


def add_ogrid_region(w, pts, ring, ip, op, zone, nr, growth, z0, z1):
    """Add one annular O-grid between pts and ring (equal lengths).
    ip/op: inner/outer vertex prefixes.  Returns (corner indices, faces):
    faces = dict with 'inner', 'outer', 'caps' face lists."""
    n = len(pts)
    ci = quad_corners(pts)
    for q in range(4):
        i = ci[q]
        x, y = pts[i]
        w.add_vertex("%s%d" % (ip, q), x, y, z0)
        w.add_vertex("%s%dt" % (ip, q), x, y, z1)
        ox, oy = ring[i]
        w.add_vertex("%s%d" % (op, q), ox, oy, z0)
        w.add_vertex("%s%dt" % (op, q), ox, oy, z1)
    for q in range(4):
        i0, i1 = ci[q], ci[(q + 1) % 4]
        span = (i1 - i0) % n
        through = [pts[(i0 + j) % n] for j in range(1, span)]
        for zz, suffix in ((z0, ""), (z1, "t")):
            w.add_spline("%s%d%s" % (ip, q, suffix),
                         "%s%d%s" % (ip, (q + 1) % 4, suffix), through, zz)
        # outer edges: arc via mid-ray point
        (x0, y0), (x1, y1) = ring[i0], ring[i1]
        r = math.hypot(x0, y0)
        p0 = math.atan2(y0, x0)
        p1 = p0 + (math.atan2(y1, x1) - p0) % (2 * math.pi)
        pm = 0.5 * (p0 + p1)
        via = (r * math.cos(pm), r * math.sin(pm))
        w.add_arc("%s%d" % (op, q), "%s%d" % (op, (q + 1) % 4), via, z0)
        w.add_arc("%s%dt" % (op, q), "%s%dt" % (op, (q + 1) % 4), via, z1)
    nq = [(ci[(q + 1) % 4] - ci[q]) % n for q in range(4)]
    grading = growth ** (nr - 1)
    for q in range(4):
        a, a1 = "%s%d" % (ip, q), "%s%d" % (ip, (q + 1) % 4)
        b, b1 = "%s%d" % (op, q), "%s%d" % (op, (q + 1) % 4)
        # right-handed: dir1 = radial (a->b), dir2 = tangential (b->b1),
        # dir3 = z; counts (nr, nq, 1), grading radial first
        names8 = (a, b, b1, a1, a + "t", b + "t", b1 + "t", a1 + "t")
        w.add_block(names8, zone, (nr, nq[q], 1), grading)
    inner_faces, outer_faces, caps = [], [], []
    for q in range(4):
        a, a1 = "%s%d" % (ip, q), "%s%d" % (ip, (q + 1) % 4)
        b, b1 = "%s%d" % (op, q), "%s%d" % (op, (q + 1) % 4)
        inner_faces.append((a, a1, a1 + "t", a + "t"))
        outer_faces.append((b, b1, b1 + "t", b + "t"))
        caps.append((a, a1, b1, b))
        caps.append((a + "t", a1 + "t", b1 + "t", b + "t"))
    return ci, {"inner": inner_faces, "outer": outer_faces, "caps": caps}


def rotate_pts(pts, deg):
    """Rotate point list about the origin by deg degrees."""
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    return [(x * c - y * s, x * s + y * c) for (x, y) in pts]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["steady", "flap"], default="steady")
    ap.add_argument("--shape", choices=["ellipse", "stadium"],
                    default="ellipse")
    ap.add_argument("--e", type=float, default=0.125)
    ap.add_argument("--rfar", type=float, default=20.0)
    ap.add_argument("--rami", type=float, default=1.5)
    ap.add_argument("--nr", type=int, default=72)
    ap.add_argument("--ntheta", type=int, default=160)
    ap.add_argument("--nflat", type=int, default=80)
    ap.add_argument("--ncap", type=int, default=40)
    ap.add_argument("--first", type=float, default=0.004)
    ap.add_argument("--growth", type=float, default=1.08)
    ap.add_argument("--nr-bg", type=int, default=52)
    ap.add_argument("--ntheta-bg", type=int, default=160)
    ap.add_argument("--growth-bg", type=float, default=1.06)
    ap.add_argument("--rotate-deg", type=float, default=0.0,
                    help="pre-rotation of the wing section (deg, flap mode: "
                         "build the mesh already pitched by alpha(0))")
    args = ap.parse_args()

    a, b = 0.5, args.e * 0.5
    if args.shape == "ellipse":
        pts = ellipse_points(a, b, args.ntheta)
    else:
        pts = stadium_points(a, b, args.nflat, args.ncap)
    if args.rotate_deg:
        pts = rotate_pts(pts, args.rotate_deg)

    w = MeshWriter()
    if args.mode == "steady":
        ring = ring_from_shape(pts, args.rfar)
        _, f = add_ogrid_region(w, pts, ring, "a", "b", None, args.nr,
                                args.growth, 0.0, 1.0)
        w.patches.append(("wing", "wall", f["inner"], None))
        w.patches.append(("farfield", "patch", f["outer"], None))
        w.patches.append(("frontAndBack", "empty", f["caps"], None))
    else:
        # rotating inner region: wing -> AMI cylinder
        ring_in = ring_from_shape(pts, args.rami)
        _, fi = add_ogrid_region(w, pts, ring_in, "a", "r", "rotZone",
                                 args.nr, args.growth, 0.0, 1.0)
        # static background: the AMI hole ring uses the SAME ray
        # distribution as the inner ring (matched AMI pairing; a
        # uniform-angle background ring pairs badly with the
        # shape-clustered inner ring and injects step-wise AMI noise)
        bg_in = ring_from_shape(pts, args.rami)
        bg_out = ring_from_shape(pts, args.rfar)
        _, fb = add_ogrid_region(w, bg_in, bg_out, "s", "B", None,
                                 args.nr_bg, args.growth_bg, 0.0, 1.0)
        w.patches.append(("wing", "wall", fi["inner"], None))
        w.patches.append(("AMI1", "cyclicAMI", fi["outer"],
                          {"neighbourPatch": "AMI2"}))
        w.patches.append(("AMI2", "cyclicAMI", fb["inner"],
                          {"neighbourPatch": "AMI1"}))
        w.patches.append(("farfield", "patch", fb["outer"], None))
        w.patches.append(("frontAndBack", "empty",
                          fi["caps"] + fb["caps"], None))

    sys.stdout.write(w.render())


if __name__ == "__main__":
    main()
