#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench1_analyze.py -- bench 1 analysis: C_N(alpha) curve, slope, and
deviation from the published 2-D computation fit of Wang et al. (2004).

Published anchor values (Wang, Birch & Dickinson 2004, JEB 207:449,
equations 14-15, from their 2-D computations of a steady-translating
thin wing element, elliptic section, thickness ratio 0.125, forces
sampled at t = 2 after start-up):
    C_L(alpha) = 1.2 sin(2 alpha)
    C_D(alpha) = 1.4 - cos(2 alpha)
Derived wing-normal/tangential coefficients (alpha measured from the
chord line, positive nose-up):
    C_N = C_L cos(alpha) + C_D sin(alpha)
    C_T = C_D cos(alpha) - C_L sin(alpha)
Small-alpha normal-force slope of the published fit: 2.8 / rad.

Context anchors (not pass criteria):
    thin-airfoil theory slope 2 pi = 6.283 / rad (inviscid, attached);
    Sane & Dickinson (2001) 3-D revolving fruit-fly wing fit
    C_N = 3.4 sin(2.13 alpha - 7.2 deg), slope 7.18 / rad.

Usage: python3 bench1_analyze.py <sweep_dir> [<sweep_dir2> ...]
Writes <sweep_dir>/analysis_cn.md and prints the headline table.
"""
import math
import os
import sys

WANG_CL_A, WANG_CD_A, WANG_CD_B = 1.2, 1.4, 1.0


def wang_CN(alpha):
    cl = WANG_CL_A * math.sin(2.0 * alpha)
    cd = WANG_CD_A - WANG_CD_B * math.cos(2.0 * alpha)
    return cl * math.cos(alpha) + cd * math.sin(alpha)


def wang_CT(alpha):
    cl = WANG_CL_A * math.sin(2.0 * alpha)
    cd = WANG_CD_A - WANG_CD_B * math.cos(2.0 * alpha)
    return cd * math.cos(alpha) - cl * math.sin(alpha)


def slope_fit(pts):
    """Least-squares slope through the origin for (alpha_rad, C_N)."""
    num = sum(a * c for (a, c) in pts)
    den = sum(a * a for (a, c) in pts)
    return num / den if den else float("nan")


def main():
    for sweep in sys.argv[1:]:
        csv = os.path.join(sweep, "forces_summary.csv")
        rows = []
        for line in open(csv):
            if line.startswith("#") or not line.strip():
                continue
            t = line.split()
            rows.append({
                "alpha": float(t[0]),
                "Fx2": float(t[2]), "Fy2": float(t[3]),
                "Fxm": float(t[4]), "Fym": float(t[5]),
            })
        out = []
        out.append("| alpha deg | C_N t=2 | C_N mean | C_N Wang | "
                   "dev(mean) | C_T mean | C_T Wang |")
        out.append("|---|---|---|---|---|---|---|")
        small, small2 = [], []
        dev_band = []
        for r in rows:
            a = math.radians(r["alpha"])
            cn2 = 2.0 * r["Fy2"]
            cnm = 2.0 * r["Fym"]
            ctm = 2.0 * r["Fxm"]
            w_cn, w_ct = wang_CN(a), wang_CT(a)
            dev = (cnm - w_cn) / w_cn if w_cn else float("nan")
            if r["alpha"] <= 10.0:
                small.append((a, cnm))
            if r["alpha"] <= 20.0:
                small2.append((a, cnm))
            if 10.0 <= r["alpha"] <= 75.0:
                dev_band.append(dev)
            out.append("| %.0f | %.3f | %.3f | %.3f | %+.0f%% | %.3f | "
                       "%.3f |" % (r["alpha"], cn2, cnm, w_cn,
                                    100.0 * dev if dev == dev else 0,
                                    ctm, w_ct))
        k10 = slope_fit(small)
        k20 = slope_fit(small2)
        mean_abs_dev = sum(abs(d) for d in dev_band) / len(dev_band)
        # analytic small-alpha slope of the published C_N:
        # dC_N/da|0 = 2*1.2 + C_D(0) = 2.4 + 0.4 = 2.8
        pub_slope = 2.8
        out.append("")
        out.append("slope fit 0-10 deg: %.3f / rad (published %.3f, "
                   "dev %+.1f%%)" % (k10, pub_slope,
                                     100.0 * (k10 - pub_slope) / pub_slope))
        out.append("slope fit 0-20 deg: %.3f / rad" % k20)
        out.append("mean |dev| of C_N over alpha in [10,75] deg: "
                   "%.1f%%" % (100.0 * mean_abs_dev))
        txt = "\n".join(out)
        md = os.path.join(sweep, "analysis_cn.md")
        with open(md, "w") as f:
            f.write(txt + "\n")
        print("== %s" % sweep)
        print(txt)
        print()


if __name__ == "__main__":
    main()
