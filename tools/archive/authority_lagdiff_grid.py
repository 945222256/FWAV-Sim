# -*- coding: utf-8 -*-
"""
authority_lagdiff_grid.py — 滞后差动通道权限网格（2026-09-28）

整改执行：机主指出组合控制未被测量即被手工固定，且滞后差动通道
（参数总表编号 7，第十七轮选型实测效能为尾舵 2.3 倍）从未进入
权限地图。本工具以与 authority_response_grid.py 完全相同的网格与
工况测滞后差动权限：左右翼俯仰滞后反向偏移 ±δ，δ 取 0.02 与
0.04 秒两档（已验证域 ±0.06 秒之内），检验线性并供组合权限计算。

用法： python tools/authority_lagdiff_grid.py --phi 0
      python tools/authority_lagdiff_grid.py --phi 10
产物： logs/authority_lagdiff_grid_phi{0,10}.json
"""
import os
import sys
import json
import math
import datetime
import argparse
import importlib.util

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location(
    "arg", os.path.join(PROJECT_ROOT, "tools", "authority_response_grid.py"))
arg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arg)

model = arg.model
LAG0 = arg.cfg.FlapKinematicsConfig.PITCH_LAG_S
DELTAS = [0.02, 0.04]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phi", type=float, required=True)
    args = ap.parse_args()
    rows = []
    for th in arg.TH_GRID:
        for be in arg.BE_GRID:
            beta = math.radians(be)
            vel = arg.np.array([arg.V0 * math.cos(beta),
                                arg.V0 * math.sin(beta), 0.0])
            quat = arg.euler_to_quat(math.radians(args.phi),
                                     math.radians(th), 0.0)
            Tb = arg.mean_Tx(quat, vel)
            rec = {"theta": th, "beta": be, "phi": args.phi}
            for d in DELTAS:
                model.wing_left.pitch_lag_s = LAG0 + d
                model.wing_right.pitch_lag_s = LAG0 - d
                Td = arg.mean_Tx(quat, vel)
                model.wing_left.pitch_lag_s = LAG0
                model.wing_right.pitch_lag_s = LAG0
                rec[f"lagdiff_auth_{d}"] = float(Td[0] - Tb[0])
            rows.append(rec)
            print(f"[lag] φ={args.phi:+.0f} θ={th:+6.1f} β={be:+4d}："
                  f"δ=0.02 {rec['lagdiff_auth_0.02']:+.5f}  "
                  f"δ=0.04 {rec['lagdiff_auth_0.04']:+.5f}", flush=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(arg.cfg.LOG_DIR,
                        f"authority_lagdiff_grid_phi{int(args.phi)}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump({"ts": ts, "V": arg.V0, "f": arg.F0,
                   "lag0": LAG0, "deltas": DELTAS, "phi": args.phi,
                   "rows": rows}, fp, ensure_ascii=False, indent=1)
    print(f"[lag] 完成 → {path}", flush=True)


if __name__ == "__main__":
    main()
