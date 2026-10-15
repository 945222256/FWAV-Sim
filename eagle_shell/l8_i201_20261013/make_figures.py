"""Offline evidence figures for I-201; no simulator, no physics or production writes.

零仿真器声明：本脚本不启动任何仿真器进程，仅读取本交付 output/ 下的部件
与报告，绘制翼轴修正、尾部三分铰线、后掠参考线与几何总览图证。
"""
from pathlib import Path
import csv
import json
import math

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.geometry import Polygon
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
report = json.loads((OUT / "geometry_and_binding.json").read_text(encoding="utf-8"))


def read_part_obj(path):
    verts, faces = [], []
    for line in path.read_text(encoding="ascii").splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            verts.append(list(map(float, p[1:4])))
        elif p[0] == "f":
            faces.append([int(s.split("/")[0]) - 1 for s in p[1:4]])
    return np.array(verts), np.array(faces)


def part_world(name):
    b = next(b for b in report["bindings"] if b["part"] == name)
    v, f = read_part_obj(OUT / b["part_file"])
    return v + np.array(b["zero_origin_xyz_m"]), f


def outline(tris_xy):
    shape = unary_union([Polygon(t) for t in tris_xy
                         if abs((t[1, 0] - t[0, 0]) * (t[2, 1] - t[0, 1]) -
                                (t[1, 1] - t[0, 1]) * (t[2, 0] - t[0, 0])) > 1e-14])
    polys = [shape] if shape.geom_type == "Polygon" else shape.geoms
    for poly in polys:
        xy = np.array(poly.exterior.coords)
        yield xy[:, 0], xy[:, 1]


# ---------------------------------------------------------------- figure 1
fig, axs = plt.subplots(1, 2, figsize=(13, 5.8), dpi=160)
wr = report["wing_axis_realignment"]["L"]
for ax, stage in zip(axs, ["before", "after"]):
    # Reconstruct both placements from the delivered part plus the recorded shift.
    v, f = part_world("eagle_wing_main_L")
    tipv, tipf = part_world("eagle_wing_tip_L")
    if stage == "before":
        dx = wr["correction_applied"]["dx_m"]; dz = wr["correction_applied"]["dz_m"]
        v = v - np.array([dx, 0, dz]); tipv = tipv - np.array([dx, 0, dz])
    allv = np.concatenate([v, tipv])
    tris = allv[np.concatenate([f, tipf + len(v)])][:, :, :2]
    for x, y in outline(tris):
        ax.fill(y, x, color="#cfe3eb", alpha=.9)
        ax.plot(y, x, color="#5b7a8c", lw=.6)
    ax.axvline(0, color="#be3941", ls="--", lw=1.6, label="Flap/twist axis x=0 (frozen URDF)")
    b = wr["before_correction"]
    if stage == "before":
        ax.scatter([b["quarter_chord_reference_x_m"]], [0.06], c="#276b93", marker="x", s=80,
                   label="Quarter-chord reference (NOT a hinge)")
        ax.set_title("BEFORE correction: axis at %.1f%% chord\n(audit 2026-10-13: pivot near trailing edge)"
                     % (100 * b["axis_fraction_from_leading_edge"]))
    else:
        a = wr["after_correction"]
        ax.scatter([a["seam_extent_midpoint_x_m"]], [0.06], c="#7a5195", marker="s", s=60,
                   label="Seam extent midpoint (reference)")
        ax.scatter([a["seam_vertex_centroid_xyz_m"][0]], [a["seam_vertex_centroid_xyz_m"][1]],
                   c="#2a9d8f", marker="o", s=50, label="Seam vertex centroid (reference)")
        ax.scatter([0.029], [0.06], c="#e76f51", marker="^", s=70,
                   label="URDF inner-panel forward edge 0.029 m (anchor)")
        ax.set_title("AFTER correction: axis at %.1f%% chord at joint station\nURDF panel reference 15.5%%"
                     % (100 * a["twist_axis_fraction_from_leading_edge"]))
    ax.set(xlabel="Span position y (m)", ylabel="x toward nose (m)")
    ax.axis("equal"); ax.grid(alpha=.2); ax.legend(loc="lower left", fontsize=8)
fig.suptitle("I-201 wing root realignment, left wing (right mirrored); seam = original model root opening boundary", fontsize=12)
fig.tight_layout()
fig.savefig(OUT / "wing_axis_realignment.png"); plt.close(fig)

# ---------------------------------------------------------------- figure 2
fig, axs = plt.subplots(1, 2, figsize=(13, 5.8), dpi=160)
colors = {"eagle_tail_surface_L": "#168fa5", "eagle_tail_surface_R": "#ed983a", "eagle_tail_rudder": "#be3941"}
for name, c in colors.items():
    v, f = part_world(name)
    tris = v[f]
    for x, y in outline(tris[:, :, :2]):
        axs[0].fill(y, x, color=c, alpha=.85)
    axs[1].scatter(v[:, 0], v[:, 2], s=1.5, color=c, alpha=.5)
tb = report["tail_three_angle_candidate"]["parts"]
hL = tb["eagle_tail_surface_L"]["hinge_point_xyz_m"]
hR = tb["eagle_tail_surface_R"]["hinge_point_xyz_m"]
hC = tb["eagle_tail_rudder"]["hinge_point_xyz_m"]
# Top view axes: horizontal = y (span), vertical = x (toward nose).
axs[0].axvline(0.012, color="#444", ls=":", lw=1)
axs[0].axvline(-0.012, color="#444", ls=":", lw=1)
axs[0].annotate("cut y=+0.012", xy=(0.012, -0.055), xytext=(0.018, -0.055), fontsize=8, color="#444")
axs[0].annotate("cut y=-0.012", xy=(-0.012, -0.20), xytext=(-0.065, -0.215), fontsize=8, color="#444")
for h, nm, txt in [(hL, "tail_pitch_L hinge (x=%.4f, z=%.4f)" % (hL[0], hL[2]), (0.045, 0.02)),
                   (hR, "tail_pitch_R hinge (x=%.4f, z=%.4f)" % (hR[0], hR[2]), (-0.075, -0.13))]:
    axs[0].scatter([h[1]], [h[0]], c="k", marker="*", s=120, zorder=5)
    axs[0].annotate(nm, xy=(h[1], h[0]), xytext=txt, fontsize=7.5,
                    arrowprops=dict(arrowstyle="->", lw=.7))
axs[0].scatter([hC[1]], [hC[0]], c="k", marker="P", s=120, zorder=5)
axs[0].annotate("rudder_yaw hinge (x=%.4f, y=%.4f)" % (hC[0], hC[1]),
                xy=(hC[1], hC[0]), xytext=(0.028, -0.16), fontsize=7.5,
                arrowprops=dict(arrowstyle="->", lw=.7))
axs[0].set(xlabel="Span y (m)", ylabel="x toward nose (m)",
           title="Top view: three tail parts and hinges (horizontal=y, vertical=x)\nblue=L surface, orange=R surface, red=rudder ridge")
axs[0].axis("equal"); axs[0].grid(alpha=.2)
axs[1].axvline(hC[0], color="k", ls="--", lw=1.2, label="rudder_yaw hinge (vertical, z axis)")
axs[1].set(xlabel="x toward nose (m)", ylabel="z up (m)",
           title="Side view: central ridge (rudder) rises to z=%.3f m" % report["geometry"]["tail_fan_full"]["bounds_max_m"][2])
axs[1].axis("equal"); axs[1].grid(alpha=.2); axs[1].legend(fontsize=8)
fig.suptitle("I-201 original fan tail: split planes y=±0.012; hinges from fan root seam centroids", fontsize=12)
fig.tight_layout()
fig.savefig(OUT / "tail_split_and_hinges.png"); plt.close(fig)

# ---------------------------------------------------------------- figure 3
fig, ax = plt.subplots(figsize=(10, 5.8), dpi=160)
seg_colors = {"main": "#168fa5", "tip": "#ed983a"}
for side in ["L", "R"]:
    v, f = part_world("eagle_wing_main_" + side)
    tipv, tipf = part_world("eagle_wing_tip_" + side)
    for (vv, ff, seg) in [(v, f, "main"), (tipv, tipf, "tip")]:
        for x, y in outline(vv[ff][:, :, :2]):
            ax.fill(np.sign(y) * np.abs(y), x, color=seg_colors[seg], alpha=.45)
with (OUT / "chord_stations.csv").open(encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.DictReader(fh))
for ref, style in [("leading_edge", "-"), ("quarter_chord", "--"), ("trailing_edge", ":")]:
    for side, color in [("L", "#1d3557"), ("R", "#6a040f")]:
        sel = [r for r in rows if r["side"] == side and .05 <= float(r["span_position_abs_m"]) <= .28]
        if ref == "quarter_chord":
            xs = [float(r["leading_edge_x_m"]) - .25 * (float(r["leading_edge_x_m"]) - float(r["trailing_edge_x_m"])) for r in sel]
        else:
            xs = [float(r[ref + "_x_m"]) for r in sel]
        ys = [float(r["span_position_abs_m"]) for r in sel]
        A = np.polyfit(ys, xs, 1)
        yline = np.linspace(.05, .28, 20)
        sign = 1 if side == "L" else -1
        ax.plot(sign * yline, np.polyval(A, yline), style, color=color, lw=1.4,
                label="%s %s (sweep %.1f° aft+)" % (side, ref.replace("_", " "), math.degrees(math.atan(-A[0]))) if side == "L" else None)
ax.axhline(0.14, color="#ed983a", ls="-.", alpha=.7, lw=1.2, label="fold split plane |y|=0.14")
ax.axhline(0.06, color="#555", ls="--", alpha=.7, lw=1, label="flap/twist joint station |y|=0.06")
ax.axvline(0, color="#be3941", ls="--", lw=1.3, label="flap/twist axis x=0")
ax.set(xlabel="Span position y (m; left positive, right plotted mirrored)", ylabel="x toward nose (m)",
       title="I-201 sweep reference lines in corrected nose-forward frame\nsolid=LE, dashed=quarter-chord, dotted=TE; both sides overlaid on |y|")
ax.axis("equal"); ax.grid(alpha=.2); ax.legend(fontsize=8, loc="lower left")
fig.tight_layout()
fig.savefig(OUT / "sweep_reference_lines.png"); plt.close(fig)

# ---------------------------------------------------------------- figure 4
fig, axs = plt.subplots(1, 2, figsize=(13, 5.8), dpi=160)
part_colors = {"eagle_body_and_nose": "#8d99ae", "eagle_wing_main_L": "#168fa5", "eagle_wing_main_R": "#168fa5",
               "eagle_wing_tip_L": "#ed983a", "eagle_wing_tip_R": "#ed983a",
               "eagle_tail_surface_L": "#2a9d8f", "eagle_tail_surface_R": "#2a9d8f", "eagle_tail_rudder": "#be3941"}
for name, c in part_colors.items():
    v, f = part_world(name)
    for x, y in outline(v[f][:, :, :2]):
        axs[0].fill(y, x, color=c, alpha=.8)
axs[0].axhline(0.14, color="#ed983a", ls="-.", lw=1)
axs[0].axhline(-0.14, color="#ed983a", ls="-.", lw=1)
axs[0].axvline(0, color="#be3941", ls="--", lw=1.2)
axs[0].scatter([0.029, -0.157], [0.06, 0.06], c="#e76f51", marker="^", s=60, zorder=5)
axs[0].annotate("URDF inner panel x∈[-0.157, 0.029]", xy=(-0.06, 0.185), fontsize=8, color="#e76f51")
axs[0].set(xlabel="y (m)", ylabel="x toward nose (m)",
           title="Assembled zero pose: corrected axes and fold planes")
axs[0].axis("equal"); axs[0].grid(alpha=.2)
for side, ls in [("L", "-"), ("R", "--")]:
    rr = [r for r in rows if r["side"] == side]
    axs[1].plot([float(r["span_position_abs_m"]) for r in rr],
                [float(r["chord_envelope_m"]) for r in rr], ls, label=side + " mesh chord envelope")
y = np.linspace(0, .32, 100)
axs[1].plot(y, .20 + (.14 - .20) * y / .32, ":", color="gray", label="Existing aero trapezoid")
axs[1].axvline(.14, color="#ed983a", alpha=.7, label="Fold split plane 0.14")
axs[1].set(xlabel="Absolute span position (m)", ylabel="Chord (m)",
           title="Visual chord vs existing aero geometry (I-114 alignment)")
axs[1].grid(alpha=.2); axs[1].legend(fontsize=8)
fig.suptitle("I-201 geometry overview (generated visual mesh; existing shell scale)", fontsize=12)
fig.tight_layout()
fig.savefig(OUT / "geometry_overview.png"); plt.close(fig)
print("figures written")
