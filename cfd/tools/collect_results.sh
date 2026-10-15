#!/bin/sh
# collect_results.sh -- copy run products from the WSL work tree back
# into the repository results directory (ASCII paths only).
# Usage (inside WSL): sh collect_results.sh
set -e
W="$HOME/cfd_work"
R="/mnt/g/Playground/isaac_flapper_sim/cfd/results"
mkdir -p "$R/bench1" "$R/bench2" "$R/smoke"

# bench 1: per-sweep force summaries, analyses, mesh checks, solver logs
for d in "$W"/bench1/archive_ellipse_e0.125_Re75_coarse_ok \
         "$W"/bench1/ellipse_e0.125_Re192_r20_nr72_nt160 \
         "$W"/bench1/ellipse_e0.125_Re192_r30_nr96_nt256 \
         "$W"/bench1/stadium_e0.04_Re75_r20_nr72_nt240 \
         "$W"/bench1/stadium_e0.04_Re192 \
         "$W"/bench1/*_Re3*_*r20* \
         "$W"/bench1/*_Re44800_r20* \
         "$W"/bench1/*_Re44800_r30* \
         "$W"/bench1/*_Re5*_*r20*; do
    [ -d "$d" ] || continue
    n=$(basename "$d")
    mkdir -p "$R/bench1/$n"
    cp -f "$d"/forces_summary.csv "$R/bench1/$n/" 2>/dev/null || true
    cp -f "$d"/analysis_cn.md "$R/bench1/$n/" 2>/dev/null || true
    cp -f "$d"/cn_table.md "$d"/cn_table.csv "$R/bench1/$n/" 2>/dev/null || true
    for a in "$d"/a*/; do
        [ -d "$a" ] || continue
        an=$(basename "$a")
        mkdir -p "$R/bench1/$n/$an"
        cp -f "$a"/log.pimpleFoam "$R/bench1/$n/$an/" 2>/dev/null || true
        cp -f "$a"/postProcessing/forces/*/force.dat \
              "$R/bench1/$n/$an/force.dat" 2>/dev/null || true
    done
done

# mesh quality logs
for m in "$W"/bench1/mesh_*; do
    [ -d "$m" ] || continue
    n=$(basename "$m")
    mkdir -p "$R/bench1/$n"
    cp -f "$m"/log.checkMesh "$m"/log.blockMesh "$R/bench1/$n/" 2>/dev/null || true
done

# bench 2
for d in "$W"/bench2/phip45 "$W"/bench2/phip00 "$W"/bench2/phim45; do
    [ -d "$d" ] || continue
    n=$(basename "$d")
    mkdir -p "$R/bench2/$n"
    cp -f "$d"/log.pimpleFoam "$R/bench2/$n/" 2>/dev/null || true
    cp -f "$d"/postProcessing/forces/*/force.dat \
          "$R/bench2/$n/force.dat" 2>/dev/null || true
    cp -f "$d"/constant/motion.dat "$R/bench2/$n/" 2>/dev/null || true
done
cp -f "$W"/bench2/bench2_summary.json "$R/bench2/" 2>/dev/null || true
for m in "$W"/bench2/mesh_flap_*; do
    [ -d "$m" ] || continue
    n=$(basename "$m")
    mkdir -p "$R/bench2/$n"
    cp -f "$m"/log.checkMesh "$m"/system/blockMeshDict \
          "$R/bench2/$n/" 2>/dev/null || true
done

# turbulence validation (I-134)
if [ -d "$W/turbval" ]; then
    mkdir -p "$R/turbval"
    for d in "$W"/turbval/laminar_a010 "$W"/turbval/laminar_a045 \
             "$W"/turbval/sst_a010 "$W"/turbval/sst_a045; do
        [ -d "$d" ] || continue
        n=$(basename "$d")
        mkdir -p "$R/turbval/$n"
        cp -f "$d"/log.pimpleFoam "$R/turbval/$n/" 2>/dev/null || true
        cp -f "$d"/postProcessing/forces/*/force.dat \
              "$R/turbval/$n/force.dat" 2>/dev/null || true
    done
    cp -f "$W"/turbval/mesh_plate_r20_nr88_nt288/log.checkMesh \
          "$R/turbval/" 2>/dev/null || true
fi

# smoke test
cp -f "$W"/smoke/cavity/cavity/log.icoFoam "$R/smoke/" 2>/dev/null || \
cp -f "$W"/smoke/cavity/cavity/log.* "$R/smoke/" 2>/dev/null || true

echo COLLECT_DONE
