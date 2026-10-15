#!/usr/bin/env bash
# FW-IID 阶段三 λ 标定批（方案阶段三子试验一）：
#   一个基线跑 + 三量级（0.01/0.001/0.0001 rad）各十六次滚转注入跑，
#   每跑二十秒、封锁组配置（随机源固定）。
# 用法: ./tools/fw_iid_lyap_batch.sh
# 产出: logs/fw_ledger_fw-iid-ph3-*.bin；拟合命令见文件尾注释。
set -u
cd "$(dirname "$0")/.."
PY=g:/Playground/isaac_sim_5.1.0/python.bat

run() {
  local TAG="$1" ROLL="$2"
  env FLAPPER_HEADLESS=1 FLAPPER_LEDGER=1 FLAPPER_MAX_S=20 \
      FLAPPER_IID_LOCK=1 FLAPPER_TAG="$TAG" \
      FLAPPER_DESC="FW-IID ph3 lyap $TAG roll=$ROLL" \
      FLAPPER_PERTURB_ROLL="$ROLL" \
      "$PY" main_sim.py > "logs/${TAG}.log" 2>&1
  echo "[LYAP-BATCH] $TAG rc=$?"
}

run fw-iid-ph3-base 0
for amp in 0.01 0.001 0.0001; do
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do
    run "fw-iid-ph3-r${amp}-n${i}" "$amp"
  done
done
echo LYAP_BATCH_DONE
# 拟合（每量级）示例：
#   python tools/fw_iid_lyapunov.py --base logs/fw_ledger_fw-iid-ph3-base_*.bin \
#     --pert logs/fw_ledger_fw-iid-ph3-r0.001-n*.bin --amp 0.001 \
#     --json logs/fw_lyap_r0.001.json
