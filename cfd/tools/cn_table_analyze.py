#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cn_table_analyze.py -- I-134 reconciliation: build the C_N(alpha)
table from sweep results and compare with the model's current fit.

Model current fit (params_registry aero.wing.*, source
modules/aero/sane_dickinson_3d.py):
    C_N(alpha) = 3.59 * sin(2*alpha + 9.1 deg)
    C_T(alpha) = 1.86 * cos(2*alpha + 9.1 deg) - 0.86
These belong to the three-dimensional revolving-wing quasi-steady family
(fitted from bench measurement plus literature); the computed-fluid-dynamics
table is two-dimensional steady translation, so the reconciliation carries
a regime note, not a like-for-like equality claim.

Usage: python3 cn_table_analyze.py <sweep_dir> [<sweep_dir2> ...]
Each sweep dir must contain forces_summary.csv (driver output).  Writes
cn_table.md and cn_table.csv in each sweep dir.
"""
import csv
import math
import os
import sys

CN_AMP, CN_PHASE = 3.59, math.radians(9.1)
CT_AMP, CT_OFF = 1.86, 0.86


def model_cn(a):
    return CN_AMP * math.sin(2.0 * a + CN_PHASE)


def model_ct(a):
    return CT_AMP * math.cos(2.0 * a + CN_PHASE) - CT_OFF


def main():
    for sweep in sys.argv[1:]:
        rows = []
        for line in open(os.path.join(sweep, "forces_summary.csv")):
            if line.startswith("#") or not line.strip():
                continue
            t = line.split()
            rows.append((float(t[0]), float(t[5])))   # alpha, Fy_mean
        if not rows:
            print("no data in %s" % sweep)
            continue
        out_md = ["| alpha deg | C_N computed | C_N model fit | "
                  "deviation |", "|---|---|---|---|"]
        out_csv = [("alpha_deg", "CN_cfd", "CN_model", "dev_pct")]
        devs = []
        small = []
        for alpha, fy in rows:
            a = math.radians(alpha)
            cn = 2.0 * fy
            mc = model_cn(a)
            dev = (cn - mc) / mc if abs(mc) > 1e-9 else float("nan")
            if 5.0 <= alpha <= 75.0:
                devs.append(dev)
            if alpha <= 10.0:
                small.append((a, cn))
            out_md.append("| %.0f | %.4f | %.4f | %+.1f%% |"
                          % (alpha, cn, mc, 100 * dev))
            out_csv.append(("%g" % alpha, "%.5f" % cn, "%.5f" % mc,
                            "%.2f" % (100 * dev)))
        num = sum(a * c for (a, c) in small)
        den = sum(a * a for (a, c) in small)
        k = num / den if den else float("nan")
        mk = 2 * CN_AMP * math.cos(CN_PHASE)
        out_md.append("")
        out_md.append("slope fit 0-10 deg: %.3f per rad; model fit slope "
                      "at zero: %.3f per rad; deviation %+.1f%%"
                      % (k, mk, 100 * (k - mk) / mk))
        if devs:
            out_md.append("mean |deviation| over alpha in [5,75] deg: "
                          "%.1f%%" % (100 * sum(abs(d) for d in devs)
                                      / len(devs)))
        with open(os.path.join(sweep, "cn_table.md"), "w",
                  newline="\n") as f:
            f.write("\n".join(out_md) + "\n")
        with open(os.path.join(sweep, "cn_table.csv"), "w",
                  newline="\n") as f:
            csv.writer(f).writerows(out_csv)
        print("== %s" % sweep)
        print("\n".join(out_md[-3:]))


if __name__ == "__main__":
    main()
