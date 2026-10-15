#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_turb_val.py -- laminar vs kOmegaSST mini-validation at cruise
Reynolds number (I-134 plan item: above Re 1e4 pick and register one
turbulence treatment).

Cases: stadium plate (thickness ratio 0.04), Re = 44800 (cruise 3.95 m/s,
mean chord 0.17 m, nu_air 1.5e-5), alpha = 10 and 45 deg, both laminar and
kOmegaSST, all four in parallel on the same mesh.  endTime 12 with mean
window [6, 12] (about one shedding period -- sufficient for a model-CHOICE
comparison, the production table uses the longer protocol).
"""
import math
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH1 = os.path.abspath(os.path.join(HERE, "..", "bench1_steady"))
BASHRC = "/usr/lib/openfoam/openfoam2506/etc/bashrc"
WORK = os.path.expanduser("~/cfd_work/turbval")
RE = 44800.0
ENDTIME = 12.0
WIN = (6.0, 12.0)


def sh(cmd, cwd):
    env = dict(os.environ)
    env["WM_PROJECT_DIR"] = "/usr/lib/openfoam/openfoam2506"
    with open(os.path.join(cwd, "log_cmd"), "w") as f:
        return subprocess.run(
            ["bash", "-c", "source %s && cd %s && %s" % (BASHRC, cwd, cmd)],
            stdout=f, stderr=subprocess.STDOUT, env=env).returncode


def build_mesh(mesh_dir):
    os.makedirs(os.path.join(mesh_dir, "system"), exist_ok=True)
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        src = os.path.join(BENCH1, "template", "system", name)
        if not os.path.exists(os.path.join(mesh_dir, "system", name)):
            shutil.copy(src, os.path.join(mesh_dir, "system", name))
    cmd = ("python3 %s/og_mesh_gen.py --mode steady --shape stadium "
           "--e 0.04 --rfar 20 --nr 88 --nflat 96 --ncap 48 --growth 1.09 "
           "--first 0.002 > system/blockMeshDict"
           % os.path.abspath(os.path.join(BENCH1, "..", "..", "tools")))
    rc = sh(cmd, mesh_dir)
    if rc:
        sys.exit("mesh gen failed")
    rc = sh("blockMesh > log.blockMesh 2>&1 && checkMesh > log.checkMesh "
            "2>&1", mesh_dir)
    if rc:
        sys.exit("blockMesh/checkMesh failed")
    with open(os.path.join(mesh_dir, "log.checkMesh")) as f:
        for line in f:
            if "cells:" in line or "Mesh OK" in line:
                print("  [mesh] " + line.strip())


def setup_case(case, alpha, nu_val, sst, template, mesh_dir):
    if os.path.isdir(case):
        shutil.rmtree(case)
    os.makedirs(os.path.join(case, "0"))
    os.makedirs(os.path.join(case, "constant"))
    os.makedirs(os.path.join(case, "system"))
    a = math.radians(alpha)
    u_txt = open(os.path.join(BENCH1, "template", "0", "U")).read()
    u_txt = u_txt.replace("uniform (1 0 0)",
                          "uniform (%.10f %.10f 0)" % (math.cos(a),
                                                       math.sin(a)))
    with open(os.path.join(case, "0", "U"), "w", newline="\n") as f:
        f.write(u_txt)
    shutil.copy(os.path.join(BENCH1, "template", "0", "p"),
                os.path.join(case, "0", "p"))
    if sst:
        for name in ("k", "omega", "nut"):
            shutil.copy(os.path.join(template, "0", name),
                        os.path.join(case, "0", name))
        shutil.copy(os.path.join(template, "constant",
                                 "turbulenceProperties"),
                    os.path.join(case, "constant", "turbulenceProperties"))
        for name in ("fvSchemes", "fvSolution"):
            shutil.copy(os.path.join(template, "system", name),
                        os.path.join(case, "system", name))
    else:
        shutil.copy(os.path.join(BENCH1, "template", "constant",
                                 "turbulenceProperties"),
                    os.path.join(case, "constant", "turbulenceProperties"))
        for name in ("fvSchemes", "fvSolution"):
            shutil.copy(os.path.join(BENCH1, "template", "system", name),
                        os.path.join(case, "system", name))
    tp = open(os.path.join(BENCH1, "template", "constant",
                           "transportProperties")).read()
    tp = tp.replace("nu              [0 2 -1 0 0 0 0] 0.013333;",
                    "nu              [0 2 -1 0 0 0 0] %.8f;" % nu_val)
    with open(os.path.join(case, "constant", "transportProperties"),
              "w", newline="\n") as f:
        f.write(tp)
    cd = open(os.path.join(BENCH1, "template", "system",
                           "controlDict")).read()
    cd = cd.replace("endTime         20;", "endTime         %g;" % ENDTIME)
    with open(os.path.join(case, "system", "controlDict"), "w",
              newline="\n") as f:
        f.write(cd)
    shutil.copytree(os.path.join(mesh_dir, "constant", "polyMesh"),
                    os.path.join(case, "constant", "polyMesh"))


def parse_force(case):
    base = os.path.join(case, "postProcessing", "forces")
    d = sorted(os.listdir(base))[-1]
    rows = []
    for line in open(os.path.join(base, d, "force.dat")):
        if line.startswith("#"):
            continue
        t = line.split()
        if len(t) >= 3:
            rows.append((float(t[0]), float(t[1]), float(t[2])))
    sel = [r for r in rows if WIN[0] <= r[0] <= WIN[1]]
    my = sum(r[2] for r in sel) / len(sel)
    mx = sum(r[1] for r in sel) / len(sel)
    return 2.0 * my, 2.0 * mx


def main():
    template = os.path.join(HERE, "template")
    nu_val = 1.0 / RE
    os.makedirs(WORK, exist_ok=True)
    mesh_dir = os.path.join(WORK, "mesh_plate_r20_nr88_nt288")
    if not os.path.isdir(os.path.join(mesh_dir, "constant", "polyMesh")):
        build_mesh(mesh_dir)
    cases = []
    for alpha in (10, 45):
        for sst in (False, True):
            tag = "sst_a%03d" % alpha if sst else "laminar_a%03d" % alpha
            case = os.path.join(WORK, tag)
            setup_case(case, alpha, nu_val, sst, template, mesh_dir)
            cases.append((tag, case))
    procs = {}
    for tag, case in cases:
        env = dict(os.environ)
        env["WM_PROJECT_DIR"] = "/usr/lib/openfoam/openfoam2506"
        logf = open(os.path.join(case, "log.pimpleFoam"), "w")
        procs[tag] = (subprocess.Popen(
            ["bash", "-c", "source %s && cd %s && pimpleFoam" %
             (BASHRC, case)], stdout=logf, stderr=subprocess.STDOUT,
            env=env), logf)
    bad = 0
    for tag, (p, logf) in procs.items():
        rc = p.wait()
        logf.close()
        print("  %s rc=%d" % (tag, rc))
        bad += (rc != 0)
    if bad:
        sys.exit("%d cases failed" % bad)
    results = {}
    for alpha in (10, 45):
        for name, sst in (("laminar", False), ("sst", True)):
            tag = "%s_a%03d" % (name, alpha)
            results[(name, alpha)] = parse_force(os.path.join(WORK, tag))
            cn, ct = results[(name, alpha)]
            print("%-7s alpha=%2d: C_N=%.4f C_T=%.4f" % (name, alpha,
                                                         cn, ct))
    for alpha in (10, 45):
        l = results[("laminar", alpha)][0]
        s = results[("sst", alpha)][0]
        print("alpha=%2d C_N difference: laminar %.4f sst %.4f rel "
              "%+.1f%%" % (alpha, l, s, 100 * (s - l) / l))


if __name__ == "__main__":
    main()
