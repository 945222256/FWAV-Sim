#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench2_analyze.py -- bench 2 analysis and comparison with the
published values of Wang, Birch & Dickinson (2004).

Published mean force coefficients (2-D computation, their Table/text):
    phi = +45 deg : (CL, CD) = (1.10, 1.36)
    phi =   0     : (CL, CD) = (0.82, 1.44)
    phi = -45 deg : (CL, CD) = (0.19, 1.21)
Robotic-wing experiment mean CL: 0.93 / 0.86 / 0.38.

Normalisation A (primary, stated in Wang 2004b methods):
    F_ref = 0.5 rho U_rms^2 c,  U_rms = U_max/sqrt(2),  c = rho = 1,
    CL(t) = Fy/F_ref,  CD(t) = |Fx|/F_ref (horizontal force enters the
    average as absolute value).
Normalisation B (2004a figure-caption variant, reported for context):
    F_ref = max over stroke of the quasi-steady translational lift force
    computed from THEIR fits (eqs 14-15) with the actual U(t), alpha(t):
    L_qs(t) = U(t)^2 c CL_fit(alpha(t));  B uses max|L_qs|.
Normalisation C (max stroke speed):
    F_ref = 0.5 rho U_max^2 c.  Candidate arising from this reproduction:
    the published phi = 0 mean pair lands within a few percent under C
    while the A reading of the 2004b text overshoots by a factor near
    two; the joint verdict over all three phase cases decides.

Ratio tests (normalisation independent):
    CL(+45)/CL(0), CL(-45)/CL(0) against 1.10/0.82 and 0.19/0.82.

Usage: python3 bench2_analyze.py <bench2_work_dir>
"""
import json
import math
import os
import sys

A0 = 2.8
F = 0.25
BETA = math.pi / 4.0
OMEGA = 2.0 * math.pi * F
UMAX = math.pi * F * A0
URMS = UMAX / math.sqrt(2.0)
FA = 0.5 * URMS * URMS

PUB = {45: (1.10, 1.36), 0: (0.82, 1.44), -45: (0.19, 1.21)}
PUB_EXP_CL = {45: 0.93, 0: 0.86, -45: 0.38}


def cl_fit(a):
    return 1.2 * math.sin(2.0 * a)


def cd_fit(a):
    return 1.4 - math.cos(2.0 * a)


def kin(t, phi):
    return (0.5 * A0 * OMEGA * math.sin(OMEGA * t),
            math.pi / 2.0 + BETA * math.sin(OMEGA * t + phi))


def load(case):
    base = os.path.join(case, "postProcessing", "forces")
    d = sorted(os.listdir(base))[-1]
    rows = []
    with open(os.path.join(base, d, "force.dat")) as f:
        for line in f:
            if line.startswith("#"):
                continue
            t = line.split()
            if len(t) >= 3:
                rows.append((float(t[0]), float(t[1]), float(t[2])))
    return rows


def qs_max_norm(phi):
    n = 400
    best = 0.0
    for i in range(n):
        t = 4.0 * i / (n - 1)
        u, al = kin(t, phi)
        lq = u * u * cl_fit(al)
        best = max(best, abs(lq))
    return best


def analyse(case, phi_deg):
    phi = math.radians(phi_deg)
    rows = load(case)
    nb = qs_max_norm(phi)

    def means(t0, t1):
        sel = [r for r in rows if t0 <= r[0] <= t1]
        if not sel:
            return (float("nan"),) * 6
        my = sum(r[2] for r in sel) / len(sel)
        mx = sum(abs(r[1]) for r in sel) / len(sel)
        fc = 0.5 * UMAX * UMAX
        return my / FA, mx / FA, my / nb, mx / nb, my / fc, mx / fc

    cl34, cd34, cl34b, cd34b, cl34c, cd34c = means(8.0, 16.0)
    cl45, cd45, cl45b, cd45b, cl45c, cd45c = means(12.0, 20.0)
    return {
        "phi_deg": phi_deg,
        "CL_A_34": cl34, "CD_A_34": cd34,
        "CL_A_45": cl45, "CD_A_45": cd45,
        "CL_B_45": cl45b, "CD_B_45": cd45b,
        "CL_C_45": cl45c, "CD_C_45": cd45c,
        "pub_CL": PUB[phi_deg][0], "pub_CD": PUB[phi_deg][1],
        "pub_CL_exp": PUB_EXP_CL[phi_deg],
        "dev_CL_A_45": (cl45 - PUB[phi_deg][0]) / PUB[phi_deg][0],
        "dev_CD_A_45": (cd45 - PUB[phi_deg][1]) / PUB[phi_deg][1],
        "dev_CL_C_45": (cl45c - PUB[phi_deg][0]) / PUB[phi_deg][0],
        "dev_CD_C_45": (cd45c - PUB[phi_deg][1]) / PUB[phi_deg][1],
    }


def main():
    work = sys.argv[1]
    tag = {45: "phip45", 0: "phip00", -45: "phim45"}
    out = []
    for phi_deg in (45, 0, -45):
        case = os.path.join(work, tag[phi_deg])
        try:
            out.append(analyse(case, phi_deg))
        except Exception as e:
            print("phi %+d failed: %s" % (phi_deg, e))
    if not out:
        return
    print("| phi | CL A | CD A | CL C | CD C | pub CL | pub CD | "
          "dev CL C | dev CD C | exp CL |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in out:
        print("| %+d | %.3f | %.3f | %.3f | %.3f | %.2f | %.2f | "
              "%+.0f%% | %+.0f%% | %.2f |" % (
                  r["phi_deg"], r["CL_A_45"], r["CD_A_45"],
                  r["CL_C_45"], r["CD_C_45"],
                  r["pub_CL"], r["pub_CD"],
                  100 * r["dev_CL_C_45"], 100 * r["dev_CD_C_45"],
                  r["pub_CL_exp"]))
    # ratio tests
    by = {r["phi_deg"]: r for r in out}
    if 0 in by and 45 in by and -45 in by:
        cl0 = by[0]["CL_A_45"]
        if cl0:
            print()
            print("ratio CL(+45)/CL(0) = %.3f (published %.3f)" % (
                by[45]["CL_A_45"] / cl0, 1.10 / 0.82))
            print("ratio CL(-45)/CL(0) = %.3f (published %.3f)" % (
                by[-45]["CL_A_45"] / cl0, 0.19 / 0.82))
    with open(os.path.join(work, "bench2_analysis.md"), "w") as f:
        f.write("# bench2 comparison (auto)\n\n")
        for line in out:
            f.write(json.dumps(line) + "\n")


if __name__ == "__main__":
    main()
