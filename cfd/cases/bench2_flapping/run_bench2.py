#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_bench2.py -- bench 2 driver: Wang-Birch-Dickinson (2004) 2-D
hovering wing reproduction with an AMI rotating zone.

Reference kinematics (Wang et al. 2004 JEB 207:449):
  x(t)      = (A0/2) cos(2 pi f t),         A0/c = 2.8
  alpha(t)  = pi/2 + (pi/4) sin(2 pi f t + phi),  f = 0.25
  Re = Umax c / nu = 75, Umax = pi f A0, c = rho = 1

Solved in the frame that follows the wing centre (no pseudo-terms for
pure translation in incompressible flow, absorbed into pressure):
  mesh motion      : rigid rotation alpha(t) about origin, AMI cylinder
  far-field inflow : U(t) = -dx/dt = (A0/2)(2 pi f) sin(2 pi f t) xhat

Published targets (2-D computation, averaged force coefficients):
  phi = +pi/4 : (CL, CD) = (1.10, 1.36)
  phi =   0   : (CL, CD) = (0.82, 1.44)
  phi = -pi/4 : (CL, CD) = (0.19, 1.21)
Robotic-wing experiment mean CL: 0.93 / 0.86 / 0.38.

Normalisation (Wang 2004b JEB): F_ref = 0.5 rho U_rms^2 c with
U_rms = Umax/sqrt(2); horizontal force enters averages as |F_x|.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_CFD = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOLS = os.path.join(REPO_CFD, "tools")
BASHRC = "/usr/lib/openfoam/openfoam2506/etc/bashrc"

A0 = 2.8          # stroke amplitude (peak-to-peak travel), chords
F = 0.25          # stroke frequency
ALPHA0 = math.pi / 2.0
BETA = math.pi / 4.0
OMEGA = 2.0 * math.pi * F
UMAX = math.pi * F * A0          # = 2.19911
RE = 75.0
NU = UMAX / RE
URMS = UMAX / math.sqrt(2.0)
FREF = 0.5 * URMS * URMS         # per unit rho, c

# Stroke handedness.  Two mirror experiments satisfy the published
# equations x(t) = +-(A0/2) cos(2 pi f t), alpha(t) = pi/2 + beta sin(...):
# they share the alpha waveform and mirror the stroke direction.  The
# far-field table below encodes -(dx/dt) for the MINUS variant
# x(t) = -(A0/2) cos(...); the phi = +45 advanced case then produces the
# published lift enhancement (the PLUS variant produced its mirror:
# same phi = 0 result, inverted rotational contribution for phi != 0).
STROKE_SIGN = -1.0


def sh(cmd, cwd):
    env = dict(os.environ)
    env["WM_PROJECT_DIR"] = "/usr/lib/openfoam/openfoam2506"
    with open(os.path.join(cwd, "log_cmd"), "w") as f:
        r = subprocess.run(
            ["bash", "-c", "source %s && cd %s && %s" % (BASHRC, cwd, cmd)],
            stdout=f, stderr=subprocess.STDOUT, env=env)
    return r.returncode


def x_t(t):
    return STROKE_SIGN * 0.5 * A0 * math.cos(OMEGA * t)


def ux_t(t):
    # far-field velocity in the wing frame = -dx/dt
    return STROKE_SIGN * 0.5 * A0 * OMEGA * math.sin(OMEGA * t)


def alpha_t(t, phi):
    return ALPHA0 + BETA * math.sin(OMEGA * t + phi)


def write_tables(case, phi, dt_table, t_end):
    """Motion table: tabulated6DoF applies the ABSOLUTE transform to the
    case-initial mesh, so the mesh is pre-rotated by alpha(0) and the
    table rotation is the INCREMENT alpha(t) - alpha(0); the wing pitch
    then equals alpha(t) with no jump at the first step.  Translation is
    zero: the wing-following frame is implemented via the far-field U
    table, the mesh never translates."""
    a0 = ALPHA0 + BETA * math.sin(phi)
    os.makedirs(os.path.join(case, "constant"), exist_ok=True)
    rows = []
    n = int(t_end / dt_table) + 2
    for k in range(n):
        t = k * dt_table
        rows.append((t, alpha_t(t, phi) - a0, ux_t(t)))
    with open(os.path.join(case, "constant", "motion.dat"),
              "w", newline="\n") as f:
        f.write("%d\n(\n" % len(rows))
        for (t, dal, ux) in rows:
            # tabulated6DoFMotion reads rotation entries in DEGREES
            f.write("    (%.6f ((0 0 0) (0 0 %.10f)))\n"
                    % (t, math.degrees(dal)))
        f.write(")\n")
    u_txt = open(os.path.join(HERE, "template", "0", "U")).read()
    table = "\n        ".join(
        "(%.6f (%.10f 0 0))" % (t, ux) for (t, al, ux) in rows)
    u_txt = u_txt.replace(
        "uniformValue    table\n        (\n            (0.0 (0 0 0))\n        );",
        "uniformValue    table\n        (\n        %s\n        );" % table)
    with open(os.path.join(case, "0", "U"), "w", newline="\n") as f:
        f.write(u_txt)


def setup_case(case, phi, mesh_dir, template):
    os.makedirs(case, exist_ok=True)
    for sub in ("0", "constant", "system"):
        os.makedirs(os.path.join(case, sub), exist_ok=True)
    # fresh start: drop any earlier time dirs and results from an aborted
    # run in the same directory (prevents latestTime resume mixing)
    for entry in os.listdir(case):
        if entry not in ("0", "constant", "system"):
            shutil.rmtree(os.path.join(case, entry), ignore_errors=True)
    write_tables(case, phi, 0.005, 30.0)
    for name in ("U", "p"):
        src = os.path.join(template, "0", name)
        if name == "p":
            shutil.copy(src, os.path.join(case, "0", name))
    # U written by write_tables (already)
    for name in ("dynamicMeshDict", "turbulenceProperties"):
        shutil.copy(os.path.join(template, "constant", name),
                    os.path.join(case, "constant", name))
    tp = open(os.path.join(template, "constant",
                           "transportProperties")).read()
    tp = tp.replace("nu              [0 2 -1 0 0 0 0] 0.013333;",
                    "nu              [0 2 -1 0 0 0 0] %.8f;" % NU)
    with open(os.path.join(case, "constant", "transportProperties"),
              "w", newline="\n") as f:
        f.write(tp)
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        shutil.copy(os.path.join(template, "system", name),
                    os.path.join(case, "system", name))
    pm = os.path.join(case, "constant", "polyMesh")
    if not os.path.isdir(pm):
        shutil.copytree(os.path.join(mesh_dir, "constant", "polyMesh"), pm)


def parse_forces(case):
    base = os.path.join(case, "postProcessing", "forces")
    d = sorted(os.listdir(base))[-1]
    rows = []
    with open(os.path.join(base, d, "force.dat")) as f:
        for line in f:
            if line.startswith("#"):
                continue
            tok = line.split()
            if len(tok) < 3:
                continue
            rows.append((float(tok[0]), float(tok[1]), float(tok[2])))
    return rows


def post(case, phi_deg):
    rows = parse_forces(case)
    def mean_over(t0, t1):
        sel = [r for r in rows if t0 <= r[0] <= t1]
        if not sel:
            return float("nan"), float("nan")
        my = sum(r[2] for r in sel) / len(sel)
        mx = sum(abs(r[1]) for r in sel) / len(sel)
        return my / FREF, mx / FREF
    cl_23, cd_23 = mean_over(8.0, 16.0)     # strokes 3-4
    cl_45, cd_45 = mean_over(12.0, 20.0)    # strokes 4-5
    out = {
        "phi_deg": phi_deg,
        "CL_strokes3_4": cl_23, "CD_strokes3_4": cd_23,
        "CL_strokes4_5": cl_45, "CD_strokes4_5": cd_45,
    }
    print("  phi=%+4d deg: CL(3-4)=%.3f CD(3-4)=%.3f | "
          "CL(4-5)=%.3f CD(4-5)=%.3f"
          % (phi_deg, cl_23, cd_23, cl_45, cd_45))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=os.path.expanduser(
        "~/cfd_work/bench2"))
    ap.add_argument("--phi-deg", default="45",
                    help="phase angle(s), comma list, e.g. 45,0,-45")
    ap.add_argument("--action", default="all",
                    choices=["mesh", "run", "post", "all"])
    ap.add_argument("--rami", type=float, default=1.5)
    ap.add_argument("--rfar", type=float, default=15.0)
    ap.add_argument("--nr", type=int, default=40)
    ap.add_argument("--ntheta", type=int, default=160)
    ap.add_argument("--first", type=float, default=0.004)
    ap.add_argument("--growth", type=float, default=1.09)
    ap.add_argument("--nr-bg", type=int, default=52)
    ap.add_argument("--ntheta-bg", type=int, default=160)
    ap.add_argument("--growth-bg", type=float, default=1.07)
    ap.add_argument("--jobs", type=int, default=1)
    args = ap.parse_args()

    template = os.path.join(HERE, "template")
    phis = [float(x) for x in args.phi_deg.split(",")]

    def mesh_dir_for(phi_deg):
        tag = "m%03d" % abs(phi_deg)
        return os.path.join(args.work, "mesh_flap_" + tag)

    def build_mesh(mesh_dir, alpha0_deg):
        os.makedirs(os.path.join(mesh_dir, "system"), exist_ok=True)
        for name in ("controlDict", "fvSchemes", "fvSolution"):
            shutil.copy(os.path.join(template, "system", name),
                        os.path.join(mesh_dir, "system", name))
        bmd = os.path.join(mesh_dir, "system", "blockMeshDict")
        cmd = ("python3 %s/og_mesh_gen.py --mode flap --shape ellipse "
               "--e 0.125 --rami %g --rfar %g --nr %d --ntheta %d "
               "--first %g --growth %g --nr-bg %d --ntheta-bg %d "
               "--growth-bg %g --rotate-deg %.6f > %s" % (
                   TOOLS, args.rami, args.rfar, args.nr, args.ntheta,
                   args.first, args.growth, args.nr_bg, args.ntheta_bg,
                   args.growth_bg, alpha0_deg, bmd))
        subprocess.run(["bash", "-c", cmd], check=True)
        rc = sh("blockMesh > log.blockMesh 2>&1", mesh_dir)
        if rc:
            sys.exit("blockMesh failed (flap mesh %s)" % mesh_dir)
        rc = sh("checkMesh > log.checkMesh 2>&1", mesh_dir)
        with open(os.path.join(mesh_dir, "log.checkMesh")) as f:
            for line in f:
                if "cells:" in line or "Mesh OK" in line or \
                   "non-orthogonality Max" in line:
                    print("  [mesh] " + line.strip())

    if args.action in ("mesh", "all"):
        for phi_deg in phis:
            phi = math.radians(phi_deg)
            build_mesh(mesh_dir_for(phi_deg),
                       math.degrees(ALPHA0 + BETA * math.sin(phi)))

    results = []
    for phi_deg in phis:
        phi = math.radians(phi_deg)
        tag = "phi%+03d" % phi_deg if phi_deg >= 0 else \
            "phi%03d" % phi_deg
        tag = tag.replace("+", "p").replace("-", "m")
        case = os.path.join(args.work, tag)
        if args.action in ("run", "all"):
            if args.action == "run" and not os.path.isdir(
                    os.path.join(mesh_dir_for(phi_deg), "constant",
                                 "polyMesh")):
                build_mesh(mesh_dir_for(phi_deg),
                           math.degrees(ALPHA0 + BETA * math.sin(phi)))
            setup_case(case, phi, mesh_dir_for(phi_deg), template)
            import time
            t0 = time.time()
            rc = sh("pimpleFoam > log.pimpleFoam 2>&1", case)
            print("  [run] %s rc=%d wall=%.0fs" %
                  (tag, rc, time.time() - t0))
            if rc:
                continue
        if args.action in ("post", "all"):
            results.append(post(case, int(phi_deg)))

    if results:
        import json
        with open(os.path.join(args.work, "bench2_summary.json"),
                  "w") as f:
            json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
