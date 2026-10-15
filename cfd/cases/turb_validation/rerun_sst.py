#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rerun_sst.py -- rebuild and rerun only the two kOmegaSST validation
cases (after the laminar pair finished; fixes the fvSchemes wallDist
section that crashed the first SST attempt)."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_turb_val as V

import math


def main():
    template = os.path.join(V.HERE, "template")
    nu_val = 1.0 / V.RE
    mesh_dir = os.path.join(V.WORK, "mesh_plate_r20_nr88_nt288")
    procs = {}
    for alpha in (10, 45):
        case = os.path.join(V.WORK, "sst_a%03d" % alpha)
        V.setup_case(case, alpha, nu_val, True, template, mesh_dir)
        env = dict(os.environ)
        env["WM_PROJECT_DIR"] = "/usr/lib/openfoam/openfoam2506"
        logf = open(os.path.join(case, "log.pimpleFoam"), "w")
        procs[case] = (subprocess.Popen(
            ["bash", "-c", "source %s && cd %s && pimpleFoam" %
             (V.BASHRC, case)], stdout=logf, stderr=subprocess.STDOUT,
            env=env), logf)
    bad = 0
    for case, (p, logf) in procs.items():
        rc = p.wait()
        logf.close()
        print("%s rc=%d" % (case, rc))
        bad += (rc != 0)
    if bad:
        sys.exit("sst rerun failed")
    for alpha in (10, 45):
        cn, ct = V.parse_force(os.path.join(V.WORK, "sst_a%03d" % alpha))
        print("sst alpha=%2d: C_N=%.4f C_T=%.4f" % (alpha, cn, ct))


if __name__ == "__main__":
    main()
