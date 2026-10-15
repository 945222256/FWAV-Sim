# -*- coding: utf-8 -*-
"""
lyapunov_companion.py — 李雅普诺夫伴随分析（2026-09-28 用户令）

目的：对一对运行（同配置对照对，或扰动对）计算轨迹发散律——
偏差 δ(t)=min(‖q1−q2‖,‖q1+q2‖)（四元数双覆盖符号对齐），滚窗拟合
lnδ 斜率得局部李雅普诺夫指数 λ(t)，给出：首分岔时刻与量级、
λ 峰值、分段 λ（建立/巡航/坡度段）、放大到可见(1e-2)的时界。

输入：detcheck 高精度档（float64 十六进制，FLAPPER_DETCHECK=1 产）
或普通数据流（5 位小数，判决量级受记录精度下限约束，注明）。

用法：
  python tools/lyapunov_companion.py <档A> <档B> [--png out.png]
判定口径（先于数据）：首分岔=首个 δ>0 行；量级分档：≤1e-10 为
浮点执行序级，≥1e-4 一步跳入为离散事件级，其间为待查。
"""
import os, sys, json, math, argparse, datetime


def load_detcheck(path):
    rows = []
    with open(path, encoding="utf-8") as fp:
        hdr = fp.readline()
        for ln in fp:
            p = ln.strip().split(",")
            if len(p) != 8:
                continue
            rows.append([float(p[0])] + [float.fromhex(x) for x in p[1:]])
    return rows


def load_ident(path):
    import csv as _csv
    rows = []
    with open(path, encoding="utf-8") as fp:
        for r in _csv.DictReader(fp):
            rows.append([float(r["t"]), float(r["x"]), float(r["y"]),
                         float(r["z"]), float(r["qw"]), float(r["qx"]),
                         float(r["qy"]), float(r["qz"])])
    return rows


def qdist(q1, q2):
    d = max(abs(q1[i] - q2[i]) for i in range(4))
    d = min(d, max(abs(q1[i] + q2[i]) for i in range(4)))
    return d


def analyze(A, B, prec_note):
    n = min(len(A), len(B))
    ts, ds = [], []
    for i in range(n):
        ts.append(0.5 * (A[i][0] + B[i][0]))
        ds.append(qdist(A[i][4:8], B[i][4:8]))
    first = next((i for i, d in enumerate(ds) if d > 0), None)
    lam_t = [None] * n
    w = 1.0
    i0 = first if first is not None else 0
    for i in range(i0, n):
        j0 = max(i0, i - 1)
        xs, ys = [], []
        for j in range(j0, i + 1):
            if ds[j] > 0:
                xs.append(ts[j] - ts[j0]); ys.append(math.log(ds[j]))
        if len(xs) >= 2:
            xm = sum(xs) / len(xs); ym = sum(ys) / len(ys)
            den = sum((x - xm) ** 2 for x in xs)
            if den > 1e-12:
                lam_t[i] = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / den
    vis = next((ts[i] for i in range(n) if ds[i] >= 1e-2), None)
    peak = max((l for l in lam_t if l is not None), default=None)
    seg = {}
    for a, b, name in ((0, 5, "建立段"), (5, 8, "巡航段"), (8, 1e9, "后段")):
        ls = [lam_t[i] for i in range(n) if lam_t[i] is not None
              and a <= ts[i] < b]
        seg[name] = sum(ls) / len(ls) if ls else None
    return dict(n=n, first_div_i=first,
                first_div_t=ts[first] if first is not None else None,
                first_div_mag=ds[first] if first is not None else None,
                peak_lam=peak, visible_t=vis, seg_lam=seg,
                end_d=ds[-1] if ds else None, t_end=ts[-1] if ts else 0.0,
                prec=prec_note), ts, ds, lam_t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--png", default=None)
    args = ap.parse_args()
    def load(p):
        if "detcheck" in os.path.basename(p):
            return load_detcheck(p), "detcheck 全精度"
        return load_ident(p), "数据流 5 位小数（量级下限受限）"
    A, na = load(args.a); B, nb = load(args.b)
    res, ts, ds, lam = analyze(A, B, "%s / %s" % (na, nb))
    print("== 李雅普诺夫伴随分析 ==")
    print("行数 %d/%d（%s）" % (len(A), len(B), res["prec"]))
    if res["first_div_t"] is None:
        print("判决：两跑逐位一致（该时长内确定性成立）")
    else:
        print("首分岔: t=%.4fs δ=%.3e" % (res["first_div_t"], res["first_div_mag"]))
        cls = ("浮点执行序级(≤1e-10)" if res["first_div_mag"] <= 1e-10
               else "离散事件级(≥1e-4)" if res["first_div_mag"] >= 1e-4
               else "其间待查")
        print("量级分档: %s" % cls)
        print("λ峰=%.2f /s  δ达1e-2时刻=%s  末δ=%.3e"
              % (res["peak_lam"] or 0,
                 ("%.2fs" % res["visible_t"]) if res["visible_t"] else "未达",
                 res["end_d"] or 0))
        for k, v in res["seg_lam"].items():
            print("  %s λ均值=%s" % (k, ("%.2f" % v) if v is not None else "—"))
    out = {"ts": args.a, "tb": args.b, "res": res,
           "t": ts, "d": ds, "lam": lam}
    pjson = os.path.join(os.path.dirname(os.path.abspath(args.a)),
                         "lyapunov_%s.json"
                         % datetime.datetime.now().strftime("%m%d_%H%M%S"))
    with open(pjson, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, default=float)
    print("→ %s" % pjson)
    if args.png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "sans-serif"]
        fig, ax = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
        m = [i for i in range(len(ts)) if ds[i] > 0]
        ax[0].semilogy([ts[i] for i in m], [ds[i] for i in m], lw=0.9)
        ax[0].axhline(1e-2, color="r", ls=":", lw=0.8)
        ax[0].set_ylabel("偏差 δ"); ax[0].grid(alpha=0.3)
        lm = [(ts[i], lam[i]) for i in range(len(lam)) if lam[i] is not None]
        ax[1].plot([x for x, _ in lm], [y for _, y in lm], lw=0.9)
        ax[1].axhline(0, color="k", lw=0.6)
        ax[1].set_ylabel("局部 λ [1/s]"); ax[1].set_xlabel("t [s]")
        ax[1].grid(alpha=0.3)
        fig.suptitle("轨迹发散律（李雅普诺夫伴随）", fontsize=10)
        fig.tight_layout()
        fig.savefig(args.png, dpi=110)
        print("→ %s" % args.png)


if __name__ == "__main__":
    main()
