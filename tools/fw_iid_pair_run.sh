#!/usr/bin/env bash
# FW-IID 成对跑批编排（方案阶段一/二/三共用）：
#   同配置以全新进程成对运行两次 → 逐位比较 → 机器可读档案归档。
# 用法:
#   ./tools/fw_iid_pair_run.sh <标签> <时长秒> [锁定0/1]
# 环境变量:
#   RUN_EXTRA  额外的 FLAPPER_* 开关串（如 RUN_EXTRA="FLAPPER_SOLVER=PGS"）
#   PERIOD_S   可选，比较时的扑翼周期积分窗秒数（默认 0.556）
# 台账文件名含标签，跑批按标签定位，无竞态。
set -u
cd "$(dirname "$0")/.."
TAG="${1:?标签必填}"
MAXS="${2:?时长秒必填}"
LOCK="${3:-0}"
PERIOD_S="${PERIOD_S:-0.556}"
PY=g:/Playground/isaac_sim_5.1.0/python.bat

run_one() {
  local LBL="$1"
  local LOCKEV=""
  [ "$LOCK" = "1" ] && LOCKEV="FLAPPER_IID_LOCK=1"
  env FLAPPER_HEADLESS=1 FLAPPER_LEDGER=1 FLAPPER_MAX_S="$MAXS" \
      FLAPPER_TAG="$TAG" FLAPPER_DESC="FW-IID pair $TAG run $LBL" \
      $LOCKEV ${RUN_EXTRA:-} \
      "$PY" main_sim.py > "logs/${TAG}_${LBL}.log" 2>&1
  local RC=$?
  echo "[PAIR] $TAG $LBL 退出码 $RC"
  return $RC
}

run_one A || { echo "[PAIR] $TAG A跑失败，中止"; exit 1; }
run_one B || { echo "[PAIR] $TAG B跑失败，中止"; exit 1; }

A_F=$(ls -t logs/fw_ledger_${TAG}_*.bin 2>/dev/null | sed -n 1p)
B_F=$(ls -t logs/fw_ledger_${TAG}_*.bin 2>/dev/null | sed -n 2p)
# 串行完成序 = B 后完成；按修改时间 B 在前。校验两档确实存在且互异。
if [ -z "$A_F" ] || [ -z "$B_F" ] || [ "$A_F" = "$B_F" ]; then
  echo "[PAIR] $TAG 台账定位失败 A=[$A_F] B=[$B_F]"; exit 2
fi
echo "[PAIR] $TAG 比较 B_F=$B_F vs A_F=$A_F"
"$PY" modules/fw_iid_ledger.py compare "$B_F" "$A_F" \
    --json "logs/fw_pair_${TAG}.json" --period-s "$PERIOD_S"
echo "[PAIR] $TAG 完成，档案 logs/fw_pair_${TAG}.json"
