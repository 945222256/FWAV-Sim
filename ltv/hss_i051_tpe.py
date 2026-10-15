# -*- coding: utf-8 -*-
"""
hss_i051_tpe.py — I-045 剩余验收例的 TPE 初值攻坚，事项 I-051 先行执行
挂靠 I-045 收尾。

复用 tools/unified_search_v3.py 的 TPE 搜索器，经 ltv/hss_tpe.py 逐字
复制装载（机主工令与提交 a4078bf 的复用指令；v3 模块顶层读登记库在
现行库类型下导入即崩，类本体纯 numpy 无依赖）。搜索域为六通道共振频
率与六通道纯延迟共十二维，其余外层初值取逐通道网格轮扫的数据驱动初
值。每个候选点走 K 逐级续接拟合，损失为全参数抛光后的残差范数。真值
盆地残差与错误吸引子残差相差一个量级以上，残差是可靠的盆地判别量。
状态持久化在 ltv/_i051_tpe_state.json，重复执行自动续跑。产出写入
ltv/a1_results 时间戳目录。

用法： python ltv/hss_i051_tpe.py [--rounds 8] [--batch 6] [--max-iter 60]
"""
import argparse
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
import hss_kernel as hk  # noqa: E402
import hss_a1_synthetic as a1  # noqa: E402
from hss_tpe import TPE  # noqa: E402

STATE = os.path.join(_HERE, "_i051_tpe_state.json")
CACHE = os.path.join(_HERE, "_cache_i051.npz")
DOM = {"fn_%d" % p: (1.5, 4.5) for p in range(6)}
DOM.update({"td_%d" % p: (0.04, 0.22) for p in range(6)})


def load_case():
    z = np.load(CACHE)
    S = {"U": z["U"], "W": z["W"], "Uhar": z["Uhar"],
         "freqs": list(z["freqs"]), "Hmeas": z["Hmeas"],
         "ws": 2 * np.pi * a1.F_STROKE, "n_in": 6, "K": 2,
         "M_harm": a1.M_HARM}
    tr = {"A0": z["A0"], "B": z["B"], "wn": z["wn"], "zt": z["zt"],
          "td": z["td"], "m": z["m"], "psi": z["psi"],
          "Ac": list(z["Ac"]), "As": list(z["As"])}
    return S, tr


def make_start(base, w):
    th = base.copy()
    for p in range(6):
        th[9 + p] = 2 * np.pi * w["fn_%d" % p]
        th[9 + 12 + p] = w["td_%d" % p]
    return th


def eval_point(S, base, w, max_iter):
    th0 = make_start(base, w)
    try:
        th, r, J, cov, ci, si = hk.fit_hss(
            dict(S), theta_init=th0, n_starts=1, seed=1, restarts=1,
            max_iter=max_iter)
    except Exception:
        return 1e9, None
    return float(np.linalg.norm(r)), th


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--max-iter", type=int, default=60)
    args = ap.parse_args()
    S, tr = load_case()
    base = hk.init_from_data(S)
    tpe = TPE(DOM, seed=20261002)
    if os.path.exists(STATE):
        st = json.load(open(STATE, encoding="utf-8"))
        tpe.x = st["x"]
        tpe.y = st["y"]
        print("[tpe] 续跑：已有 %d 个历史点" % len(tpe.x), flush=True)
    best_loss, best_w, best_th = None, None, None
    hist = []
    for rd in range(args.rounds):
        cands = tpe.ask_batch(args.batch)
        pts, ys = [], []
        for w in cands:
            t0 = time.time()
            loss, th = eval_point(S, base, w, args.max_iter)
            pts.append(w)
            ys.append(loss)
            hist.append(loss)
            if best_loss is None or loss < best_loss:
                best_loss, best_w, best_th = loss, dict(w), th
                np.save(os.path.join(_HERE, "_i051_best_theta.npy"), th)
            print("[tpe] r%d fn=(%s) td=(%s) loss=%.4g 用时%.0fs 最优%.4g"
                  % (rd, ",".join("%.2f" % w["fn_%d" % p] for p in range(6)),
                     ",".join("%.3f" % w["td_%d" % p] for p in range(6)),
                     loss, time.time() - t0, best_loss), flush=True)
        tpe.tell(pts, ys)
        with open(STATE, "w", encoding="utf-8") as fp:
            json.dump({"x": tpe.x, "y": tpe.y, "best_w": best_w,
                       "best_loss": best_loss}, fp, ensure_ascii=False)
        if best_loss is not None and best_loss <= 20.0:
            print("[tpe] 达到盆地判别线，提前收束", flush=True)
            break
    print("[tpe] 最优损失 %.4g" % best_loss, flush=True)
    th = np.load(os.path.join(_HERE, "_i051_best_theta.npy"))
    p = hk.unpack_theta(th, 6, 2)

    def re(a, b):
        return float(np.linalg.norm(np.asarray(a) - np.asarray(b))
                     / np.linalg.norm(b))
    out = {
        "item": "I-051/I-045",
        "case": "K=2 snr=20dB（在册标签 22dB 组）",
        "best_loss": best_loss,
        "best_w": best_w,
        "relerr_A0": re(p["A0"], tr["A0"]),
        "relerr_B": re(p["B"], tr["B"]),
        "relerr_periodic": re(
            np.concatenate([M2.ravel() for M2 in p["Ac"] + p["As"]]),
            np.concatenate([M2.ravel() for M2 in tr["Ac"] + tr["As"]])),
        "relerr_chain_worst": max(re(p["wn"], tr["wn"]),
                                  re(p["zt"], tr["zt"]),
                                  re(p["td"], tr["td"])),
        "relerr_m": re(p["m"], tr["m"]),
        "thresholds": a1.THRESH,
        "pass": (re(p["A0"], tr["A0"]) <= a1.THRESH["relerr_A0_max"]
                 and re(p["B"], tr["B"]) <= a1.THRESH["relerr_B_max"]
                 and re(np.concatenate(
                     [M2.ravel() for M2 in p["Ac"] + p["As"]]),
                     np.concatenate([M2.ravel()
                                     for M2 in tr["Ac"] + tr["As"]]))
                 <= a1.THRESH["relerr_periodic_max"]
                 and max(re(p["wn"], tr["wn"]), re(p["zt"], tr["zt"]),
                         re(p["td"], tr["td"]))
                 <= a1.THRESH["relerr_chain_max"]),
    }
    out_dir = os.path.join(_HERE, "a1_results",
                           time.strftime("i051_tpe_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
