"""FW-IID 李雅普诺夫指数拟合器（方案阶段三子试验一）。

输入：一个基线台账与若干扰动台账（同配置同种子、仅释放序列末位注入
绕机体 x 轴的滚转小角）。从 bp 通道取两运行的滚转角差序列 δ(t)，
对 ln|δ(t)| 在自动选取的回归窗内做线性回归，斜率即 λ（每秒）。

用法：
  python tools/fw_iid_lyapunov.py --base B.bin --pert P1.bin P2.bin ... \
      [--json out.json] [--t0 1.0] [--sat 0.1]

回归窗自动口径：起点取 |δ(t)| 首次超过注入幅度两倍且不晚于 t0 的时
刻；终点取 |δ(t)| 首次达到饱和阈 sat（默认 0.1 rad）或序列末尾；窗
内样本不足二十点时报未判定。
"""

import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))
from modules.fw_iid_ledger import load


def roll_of(qw, qx, qy, qz):
    """四元数（w,x,y,z）→ 滚转角（弧度，atan2 口径）。"""
    return np.arctan2(2.0 * (qw * qx + qy * qz),
                      1.0 - 2.0 * (qx * qx + qy * qy))


def wrap(a):
    return (a + np.pi) % (2.0 * np.pi) - np.pi


def fit_lambda(base_path, pert_path, amp, t0=1.0, sat=0.1):
    hb, B = load(base_path)
    hp, P = load(pert_path)
    T = min(len(B), len(P))
    dt = float(hb.get("phys_dt") or 1e-3)
    # bp 列：1..7 = x,y,z,qw,qx,qy,qz
    rb_b = roll_of(B[:T, 4], B[:T, 5], B[:T, 6], B[:T, 7])
    rb_p = roll_of(P[:T, 4], P[:T, 5], P[:T, 6], P[:T, 7])
    delta = wrap(rb_p - rb_b)
    t = B[:T, 0] * dt
    ad = np.abs(delta)
    # 注入前序列应为零差（同种子同配置）；找起点
    thr = max(2.0 * abs(amp), 1e-9)
    above = np.flatnonzero(ad > thr)
    if len(above) == 0:
        return {"ok": False, "reason": "差序列始终低于两倍注入幅度",
                "max_abs_delta": float(ad.max())}
    i0 = int(above[0])
    # 终点：饱和或末尾
    sat_at = np.flatnonzero(ad > sat)
    i1 = int(sat_at[0]) if len(sat_at) and int(sat_at[0]) > i0 else T - 1
    # 回归窗至少二十点
    if i1 - i0 < 20:
        return {"ok": False,
                "reason": f"回归窗样本不足（{i1 - i0} 点）",
                "i0": i0, "i1": i1}
    x = t[i0:i1 + 1]
    y = np.log(np.maximum(ad[i0:i1 + 1], 1e-300))
    A = np.vstack([x, np.ones_like(x)]).T
    slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
    r = y - (slope * x + intercept)
    r2 = 1.0 - float(np.var(r)) / max(float(np.var(y)), 1e-300)
    return {"ok": True, "lambda": float(slope), "r2": r2,
            "t_win": [float(x[0]), float(x[-1])],
            "n_pts": int(i1 - i0 + 1),
            "max_abs_delta": float(ad.max()),
            "first_above": float(t[i0])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--pert", nargs="+", required=True)
    ap.add_argument("--amp", type=float, required=True,
                    help="注入幅度（弧度），回归起点阈值=两倍该值")
    ap.add_argument("--json", default=None)
    ap.add_argument("--t0", type=float, default=1.0)
    ap.add_argument("--sat", type=float, default=0.1)
    a = ap.parse_args()
    results = []
    for p in a.pert:
        r = fit_lambda(a.base, p, a.amp, t0=a.t0, sat=a.sat)
        r["pert"] = os.path.basename(p)
        results.append(r)
        if r["ok"]:
            print(f"[LYAP] {r['pert']}: λ={r['lambda']:.4f}/s "
                  f"r²={r['r2']:.3f} 窗[{r['t_win'][0]:.2f},"
                  f"{r['t_win'][1]:.2f}]s 点数{r['n_pts']}")
        else:
            print(f"[LYAP] {r['pert']}: 未判定（{r['reason']}）")
    oks = [r for r in results if r["ok"]]
    if oks:
        lam = sorted(r["lambda"] for r in oks)
        med = lam[len(lam) // 2]
        q1 = lam[len(lam) // 4] if len(lam) >= 4 else lam[0]
        q3 = lam[(3 * len(lam)) // 4] if len(lam) >= 4 else lam[-1]
        print(f"[LYAP] 汇总：有效 {len(oks)}/{len(results)}，"
              f"中位 λ={med:.4f}/s，四分位 [{q1:.4f}, {q3:.4f}]")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"amp": a.amp, "results": results,
                           "median": med, "q1": q1, "q3": q3,
                           "n_ok": len(oks)}, f, ensure_ascii=False, indent=1)
            print(f"[LYAP] 档案 → {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
