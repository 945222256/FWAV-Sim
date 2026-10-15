#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_bench1.py -- bench 1 driver: steady 2-D sweep of angle of attack.

Runs inside WSL.  For each angle of attack alpha the free-stream vector
is (cos a, sin a) on the far-field boundary of a fixed O-grid; the wing
section stays horizontal, so the effective angle of attack is alpha and
the wing-normal force coefficient is C_N = 2 F_y (U = c = rho = 1,
q_inf = 0.5).

Forces are extracted two ways:
  t = 2   impulsive-start value, matching the measurement protocol of
          Wang et al. (2004 JEB) for their published fits;
  mean over t in [6, 10]  late-time average (periodic shedding cases).

Usage (from the bench directory inside WSL):
  python3 run_bench1.py --mesh-only ...   build + check one mesh
  python3 run_bench1.py --run ...         full sweep
  python3 run_bench1.py --post ...        collect + compare
"""
import argparse
import csv
import math
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_CFD = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOLS = os.path.join(REPO_CFD, "tools")

BASHRC = "/usr/lib/openfoam/openfoam2506/etc/bashrc"


def sh(cmd, cwd, logpath=None):
    env = dict(os.environ)
    env["WM_PROJECT_DIR"] = "/usr/lib/openfoam/openfoam2506"
    with open(logpath, "w") if logpath else open(os.devnull, "w") as f:
        r = subprocess.run(
            ["bash", "-c", "source %s && cd %s && %s" % (BASHRC, cwd, cmd)],
            stdout=f, stderr=subprocess.STDOUT, env=env)
    return r.returncode


def gen_blockmesh(path, shape, e, rfar, nr, ntheta, nflat, ncap, growth,
                  first):
    args = ["python3", os.path.join(TOOLS, "og_mesh_gen.py"),
            "--mode", "steady", "--shape", shape, "--e", str(e),
            "--rfar", str(rfar), "--nr", str(nr), "--growth", str(growth),
            "--first", str(first)]
    if shape == "ellipse":
        args += ["--ntheta", str(ntheta)]
    else:
        args += ["--nflat", str(nflat), "--ncap", str(ncap)]
    with open(path, "w", newline="\n") as f:
        subprocess.run(args, stdout=f, check=True)


def build_mesh(shape, e, rfar, nr, ntheta, nflat, ncap, growth, out_dir,
               template, first=0.004):
    os.makedirs(os.path.join(out_dir, "system"), exist_ok=True)
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        src = os.path.join(template, "system", name)
        if not os.path.exists(os.path.join(out_dir, "system", name)):
            shutil.copy(src, os.path.join(out_dir, "system", name))
    gen_blockmesh(os.path.join(out_dir, "system", "blockMeshDict"), shape,
                  e, rfar, nr, ntheta, nflat, ncap, growth, first)
    rc = sh("blockMesh > log.blockMesh 2>&1", out_dir)
    if rc:
        sys.exit("blockMesh failed in %s" % out_dir)
    rc = sh("checkMesh > log.checkMesh 2>&1", out_dir)
    if rc:
        sys.exit("checkMesh failed in %s" % out_dir)
    # report quality headline
    with open(os.path.join(out_dir, "log.checkMesh")) as f:
        txt = f.read()
    for line in txt.splitlines():
        if "cells:" in line or "Max aspect ratio" in line or \
           "Mesh OK" in line or "non-orthogonality" in line:
            print("  [mesh] " + line.strip())


def write_U(path, alpha_deg):
    a = math.radians(alpha_deg)
    ux, uy = math.cos(a), math.sin(a)
    txt = open(os.path.join(HERE, "template", "0", "U")).read()
    txt = txt.replace("uniform (1 0 0)",
                      "uniform (%.10f %.10f 0)" % (ux, uy))
    with open(path, "w", newline="\n") as f:
        f.write(txt)


def write_nu(path, re):
    txt = open(os.path.join(HERE, "template", "constant",
                            "transportProperties")).read()
    txt = txt.replace("0.013333", "%.8f" % (1.0 / re))
    with open(path, "w", newline="\n") as f:
        f.write(txt)


def run_case(case, mesh_dir, alpha_deg, re, template):
    os.makedirs(case, exist_ok=True)
    for sub in ("0", "constant", "system"):
        os.makedirs(os.path.join(case, sub), exist_ok=True)
    # fresh start: drop any earlier time dirs and results from an aborted
    # run in the same directory (prevents latestTime resume mixing)
    for entry in os.listdir(case):
        if entry not in ("0", "constant", "system"):
            shutil.rmtree(os.path.join(case, entry), ignore_errors=True)
    # copy template 0/, constant/, system/
    for name in ("U", "p"):
        shutil.copy(os.path.join(template, "0", name),
                    os.path.join(case, "0", name))
    write_U(os.path.join(case, "0", "U"), alpha_deg)
    for name in ("transportProperties", "turbulenceProperties"):
        shutil.copy(os.path.join(template, "constant", name),
                    os.path.join(case, "constant", name))
    write_nu(os.path.join(case, "constant", "transportProperties"), re)
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        shutil.copy(os.path.join(template, "system", name),
                    os.path.join(case, "system", name))
    # shared mesh (copy polyMesh)
    pm = os.path.join(case, "constant", "polyMesh")
    if not os.path.isdir(pm):
        shutil.copytree(os.path.join(mesh_dir, "constant", "polyMesh"), pm)
    import time
    t0 = time.time()
    rc = sh("pimpleFoam > log.pimpleFoam 2>&1", case)
    dt = time.time() - t0
    return case, rc, dt


def parse_forces(case):
    """Return (times, fx, fy) lists from postProcessing forces."""
    base = os.path.join(case, "postProcessing", "forces")
    if not os.path.isdir(base):
        return None
    d = sorted(os.listdir(base))[-1]
    fpath = os.path.join(base, d, "force.dat")
    if not os.path.exists(fpath):
        fpath = os.path.join(base, d, "forces.dat")
    rows = []
    with open(fpath) as f:
        for line in f:
            if line.startswith("#"):
                continue
            tok = line.split()
            if len(tok) < 3:
                continue
            t = float(tok[0])
            fx = float(tok[1])
            fy = float(tok[2])
            rows.append((t, fx, fy))
    return rows


def collect(sweep_dir, alphas, re, shape, e, out_csv):
    out = []
    for a in alphas:
        case = os.path.join(sweep_dir, "a%03d" % a)
        rows = parse_forces(case)
        if not rows:
            print("  [warn] no forces for alpha=%d" % a)
            continue
        # value at t = 2 (impulsive-start protocol, literature reading)
        r2 = min(rows, key=lambda r: abs(r[0] - 2.0))
        late = [r for r in rows if 8.0 <= r[0] <= 12.0]
        mx = sum(r[1] for r in late) / max(1, len(late))
        my = sum(r[2] for r in late) / max(1, len(late))
        out.append((a, r2[0], r2[1], r2[2], mx, my))
    with open(out_csv, "w", newline="\n") as f:
        f.write("# shape=%s e=%g Re=%g  cols: alpha t Fx2 Fy2 "
                "Fxmean Fymean (kinematic, per unit span)\n"
                % (shape, e, re))
        for r in out:
            f.write("%d %.4f %.6f %.6f %.6f %.6f\n" % r)
    print("  wrote %s (%d rows)" % (out_csv, len(out)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=os.path.expanduser(
        "~/cfd_work/bench1"))
    ap.add_argument("--shapes", default="ellipse:0.125")
    ap.add_argument("--res", default="75")
    ap.add_argument("--alphas",
                    default="0,5,10,15,20,25,30,35,40,45,50,60,70,80,90")
    ap.add_argument("--rfar", type=float, default=20.0)
    ap.add_argument("--nr", type=int, default=72)
    ap.add_argument("--ntheta", type=int, default=160)
    ap.add_argument("--nflat", type=int, default=80)
    ap.add_argument("--ncap", type=int, default=40)
    ap.add_argument("--growth", type=float, default=1.08)
    ap.add_argument("--first", type=float, default=0.004,
                    help="first cell height at the wall (chords)")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--action", default="all",
                    choices=["mesh", "run", "post", "all"])
    args = ap.parse_args()

    template = os.path.join(HERE, "template")
    alphas = [int(x) for x in args.alphas.split(",")]
    res = [float(x) for x in args.res.split(",")]

    for spec in args.shapes.split(";"):
        shape, e = spec.split(":")
        e = float(e)
        for re in res:
            # sweep dir carries the mesh signature so that different
            # resolutions/domains never collide or resume each other
            name = "%s_e%g_Re%g_r%g_nr%d_nt%d" % (
                shape, e, re, args.rfar, args.nr,
                args.ntheta if shape == "ellipse" else 2 * (
                    args.nflat + args.ncap))
            sweep = os.path.join(args.work, name)
            mesh_dir = os.path.join(args.work, "mesh_%s_e%g_r%g" %
                                    (shape, e, args.rfar))
            if args.action in ("mesh", "all"):
                print("[mesh] %s" % mesh_dir)
                build_mesh(shape, e, args.rfar, args.nr, args.ntheta,
                           args.nflat, args.ncap, args.growth, mesh_dir,
                           template, args.first)
            if args.action in ("run", "all"):
                os.makedirs(sweep, exist_ok=True)
                cases = [(os.path.join(sweep, "a%03d" % a), a)
                         for a in alphas]
                results = []
                with ThreadPoolExecutor(max_workers=args.jobs) as ex:
                    futs = [ex.submit(run_case, c, mesh_dir, a, re,
                                      template) for c, a in cases]
                    for fut in futs:
                        results.append(fut.result())
                nbad = 0
                total_wall = 0.0
                for case, rc, dt in results:
                    total_wall += dt
                    a = int(os.path.basename(case)[1:])
                    if rc:
                        nbad += 1
                        print("  [fail] alpha=%d rc=%d" % (a, rc))
                print("[run] %s done: %d ok %d fail, serial-cpu-wall %.1f s"
                      % (name, len(results) - nbad, nbad, total_wall))
            if args.action in ("post", "all"):
                out_csv = os.path.join(sweep, "forces_summary.csv")
                collect(sweep, alphas, re, shape, e, out_csv)


if __name__ == "__main__":
    main()
