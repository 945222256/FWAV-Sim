#!/bin/sh
# run_all_phi.sh -- run the three bench 2 phase cases in parallel.
# Usage (inside WSL): sh run_all_phi.sh <workdir>
cd "$(dirname "$0")" || exit 1
W="${1:-$HOME/cfd_work/bench2}"
mkdir -p "$W"
for P in 45 0 -45; do
    python3 run_bench2.py --action all "--phi-deg=$P" \
        > "$W/phi${P}.log" 2>&1 &
done
wait
echo ALL_DONE
for P in 45 0 -45; do
    echo "== phi $P =="
    tail -n 2 "$W/phi${P}.log"
done
