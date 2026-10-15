#!/bin/sh
# cn_table_sweep.sh -- I-134 production C_N table sweep, single held
# session (WSL: background processes die with their session).
# Slot budget: the host has 16 physical cores (32 hyperthreads); solver
# counts stay at or below ~16 to avoid hyperthread oversubscription
# (measured 2x per-case slowdown at 29 concurrent solvers).
# Stage 0: turbulence mini-validation (4 cases).
# Stage 1: shared stadium mesh.
# Stage 2: cruise 19-alpha + two flanking Reynolds 10-alpha sweeps.
# Stage 3: ellipse companion + fine-grid convergence level.
# Stage 4: analyses.
set -e
cd "$(dirname "$0")/bench1_steady" || exit 1
W="$HOME/cfd_work/bench1"
ALPHAS19="0,5,10,15,20,25,30,35,40,45,50,55,60,65,70,75,80,85,90"
ALPHAS10="0,10,20,30,40,50,60,70,80,90"
ALPHAS6="0,15,30,45,60,75"

echo "== stage 0: turbulence validation (4 cases)"
cd ../turb_validation
python3 run_turb_val.py 2>&1 | tail -8 || true
cd ../bench1_steady

echo "== stage 1: mesh"
python3 run_bench1.py --action mesh --shapes stadium:0.04 --rfar 20 \
    --nr 88 --nflat 96 --ncap 48 --growth 1.09 --first 0.002 \
    --work "$W"

echo "== stage 2: plate sweeps (cruise full + flanking 10-deg)"
python3 run_bench1.py --action run --shapes stadium:0.04 --res 44800 \
    --alphas "$ALPHAS19" --rfar 20 --nr 88 --nflat 96 --ncap 48 \
    --growth 1.09 --first 0.002 --jobs 8 --work "$W" \
    > "$W/sweep_cn_p45.log" 2>&1 &
P1=$!
python3 run_bench1.py --action run --shapes stadium:0.04 --res 34000 \
    --alphas "$ALPHAS10" --rfar 20 --nr 88 --nflat 96 --ncap 48 \
    --growth 1.09 --first 0.002 --jobs 4 --work "$W" \
    > "$W/sweep_cn_p34.log" 2>&1 &
P2=$!
python3 run_bench1.py --action run --shapes stadium:0.04 --res 56700 \
    --alphas "$ALPHAS10" --rfar 20 --nr 88 --nflat 96 --ncap 48 \
    --growth 1.09 --first 0.002 --jobs 4 --work "$W" \
    > "$W/sweep_cn_p57.log" 2>&1 &
P3=$!
wait $P1 $P2 $P3
tail -n 1 "$W/sweep_cn_p34.log" "$W/sweep_cn_p45.log" "$W/sweep_cn_p57.log"

echo "== stage 3: ellipse companion + fine level"
python3 run_bench1.py --action all --shapes ellipse:0.125 --res 44800 \
    --alphas "$ALPHAS10" --rfar 20 --nr 88 --ntheta 240 --growth 1.09 \
    --first 0.002 --jobs 8 --work "$W" > "$W/sweep_cn_e45.log" 2>&1 &
Q1=$!
python3 run_bench1.py --action all --shapes stadium:0.04 --res 44800 \
    --alphas "$ALPHAS6" --rfar 30 --nr 110 --nflat 128 --ncap 64 \
    --growth 1.08 --first 0.001 --jobs 8 --work "$W" \
    > "$W/sweep_cn_fine.log" 2>&1 &
Q2=$!
wait $Q1 $Q2
tail -n 1 "$W/sweep_cn_e45.log" "$W/sweep_cn_fine.log"

echo "== stage 4: analyses"
python3 "$HOME/cfd_work/tools/cn_table_analyze.py" \
    "$W/stadium_e0.04_Re44800_r20_nr88_nt288" \
    "$W/stadium_e0.04_Re34000_r20_nr88_nt288" \
    "$W/stadium_e0.04_Re56700_r20_nr88_nt288" \
    "$W/stadium_e0.04_Re44800_r30_nr110_nt384" \
    "$W/ellipse_e0.125_Re44800_r20_nr88_nt240" || true
echo ALL_CN_DONE
