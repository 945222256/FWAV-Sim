# -*- coding: utf-8 -*-
"""
allocator_tune_tpe.py — 分配权重的标准 TPE 整定（2026-09-28）

流程与判据：docs/标准整定方法_TPE分配权重.md（先于数据，运行后不改）。
评估核：tools/allocator_kernel.py（AllocTurnSim）。
优化器：tools/optimizers.py 的 TPE（最小化 loss = −J），
经统一优化器库引用（I-080 收编），不再经 importlib 加载
unified_search_v3（旧路径会连带执行其框架顶层导入，脆）。

可复用与续跑：历史点持久化在 logs/allocator_tune_state.json，
重复执行自动续跑；新增通道时只加权重域与核内通道表（规程见方法文档
第五节）。

用法： python tools/allocator_tune_tpe.py [--rounds 2] [--batch 6]
产物： logs/allocator_tune_state.json（续跑状态与最优权重）、
       logs/allocator_tune_<时间戳>.json（本轮摘要）
"""
import os
import sys
import json
import time
import datetime
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
import importlib.util

from tools.optimizers import TPE

ak_spec = importlib.util.spec_from_file_location(
    "allocator_kernel",
    os.path.join(PROJECT_ROOT, "tools", "allocator_kernel.py"))
allocator_kernel = importlib.util.module_from_spec(ak_spec)
ak_spec.loader.exec_module(allocator_kernel)

DOM = {"w_ail": (0.0, 1.0), "w_dA": (0.0, 1.0),
       "w_lag": (0.0, 1.0), "w_rho": (0.0, 1.0),
       "kp_phi": (0.005, 0.20), "kd_phi": (0.005, 0.20),
       "ky_yaw": (0.0, 2.0)}
STATE = os.path.join(PROJECT_ROOT, "logs", "allocator_tune_state.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--batch", type=int, default=6)
    args = ap.parse_args()

    tpe = TPE(DOM, seed=23)
    hist_x, hist_y = [], []
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as fp:
            st = json.load(fp)
        hist_x, hist_y = st["x"], st["y"]
        tpe.x = list(hist_x)
        tpe.y = list(hist_y)
        print(f"[tune] 续跑：已有 {len(hist_x)} 个历史点", flush=True)

    for rd in range(args.rounds):
        cands = tpe.ask_batch(args.batch)
        pts, ys = [], []
        for w in cands:
            t0 = time.time()
            J, out = allocator_kernel.evaluate(w)
            loss = -J
            pts.append(w)
            ys.append(loss)
            ok = allocator_kernel.accepted(out)
            print(f"[tune] w=({w['w_ail']:.2f},{w['w_dA']:.2f},"
                  f"{w['w_lag']:.2f},{w['w_rho']:.2f}) "
                  f"k=({w['kp_phi']:.3f},{w['kd_phi']:.3f},"
                  f"{w['ky_yaw']:.2f}) J={J:+.2f} "
                  f"surv={out['survive_s']:.1f}s "
                  f"φerr_med={out['phi_err_med']:.3f} "
                  f"z=[{out['z_min']:.2f},{out['z_max']:.2f}] "
                  f"{'★入选' if ok else ''} ({time.time()-t0:.0f}s)",
                  flush=True)
        tpe.tell(pts, ys)
        hist_x.extend([[p[k] for k in DOM] for p in pts])
        hist_y.extend(ys)
        with open(STATE, "w", encoding="utf-8") as fp:
            json.dump({"x": hist_x, "y": hist_y}, fp)
        print(f"[tune] 轮 {rd} 完成，最优 loss={min(hist_y):.3f}",
              flush=True)

    ibest = int(np.argmin(hist_y))
    best = {k: hist_x[ibest][j] for j, k in enumerate(DOM)}
    Jbest, outbest = allocator_kernel.evaluate(best)
    ok = allocator_kernel.accepted(outbest)
    summary = {"ts": datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
               "n_evals": len(hist_x), "best_weights": best,
               "best_J": Jbest, "best_out": outbest,
               "accepted": ok}
    path = os.path.join(PROJECT_ROOT, "logs",
                        f"allocator_tune_{summary['ts']}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(summary, fp, ensure_ascii=False, indent=1)
    print(f"[tune] 最优权重 {json.dumps(best)} J={Jbest:+.2f} "
          f"入选={'是' if ok else '否'} → {path}", flush=True)


if __name__ == "__main__":
    main()
