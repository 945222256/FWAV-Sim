# -*- coding: utf-8 -*-
"""
divergence_extractor.py — 失稳点提取器（2026-09-28，判据先于数据写定）

定义（两层，先写后跑，运行后不改）：
层一·经验发散起始点 t_onset：滚转角速度绝对值的对数斜率判据。
  滑动窗 0.6 秒对 ln(|wx|+1e-3) 作最小二乘拟合得增长率 λ(t)；
  t_onset = 满足"该时刻起连续 1.2 秒 λ 均大于 0.35 每秒，且该区间
  |wx| 峰值大于 1.5 弧度每秒"的最早时刻（空中段）。阈值均为自定
  策略值，来源：1.32Hz 增长模态 λ≈0.105 的三倍取整 + 失稳末段
  |wx| 实测 9 rad/s 量级。
层二·权限裕度归零点 t_margin0：可用滚转权限被当前干扰滚转矩吃尽的
  时刻。权限取 A 队权限包络（logs/quantum_envelope_scan.json 的
  density_dTx，坡度×侧滑网格最近邻内插；该包络俯仰钉在配平值，本
  工具对 θ(t) 的偏离不修正，登记为近似源）；需求取记录的总滚转
  力矩 |TMx(t)|。裕度(t)=权限−需求；t_margin0 = 空中段裕度首次
  转负且此后中位保持为负的时刻。
层三·反事实改出点（真值层，本工具不实现，登记待办）：从记录状态
  满偏反操纵重放，二分搜索"仍能 T 秒回到安全集"的最后时刻——
  层一层二给出候选，层三裁决。

判定输出：margin0 早于 onset=权限先失（结构性失稳）；onset 早于
margin0=动态先发散（增益性失稳）；两者皆无=平稳记录。

用法：python tools/divergence_extractor.py [记录csv ...]（缺省全量）
产物：logs/divergence_extract_<ts>.json + 控制台表
"""
import os, sys, json, glob, math, datetime
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
ENV_JSON = os.path.join(LOG_DIR, "quantum_envelope_scan.json")

W_FIT = 0.6        # λ 拟合窗（秒）
LAM_THR = 0.35     # 增长率阈值（1/s）
SUS_S = 1.2        # 持续时长（秒）
WX_PK = 1.5        # 区间 |wx| 峰值线（rad/s）


def load_env():
    with open(ENV_JSON, encoding="utf-8") as fp:
        d = json.load(fp)
    phi = np.array([r["phi"] for r in d["rows"]])
    beta = np.array([r["beta"] for r in d["rows"]])
    auth = np.array([r["density_dTx"] for r in d["rows"]])
    return phi, beta, auth


def env_lookup(phi_g, beta_g, P, B, A):
    i = np.argmin(np.abs(P - phi_g) + 0.5 * np.abs(B - beta_g))
    return float(A[i])


def analyze(csv_path, P, B, A):
    import csv as _csv
    with open(csv_path, encoding="utf-8") as fp:
        rd = _csv.DictReader(fp)
        rows = [r for r in rd]
        cols = rd.fieldnames
    if len(rows) < 50:
        return None
    f = lambda c, d=0.0: np.array([float(r.get(c, d) or d) for r in rows])
    t = f("t"); z = f("z"); wx = f("wx"); vy = f("vy"); vx = f("vx")
    qw, qx, qy, qz = f("qw"), f("qx"), f("qy"), f("qz")
    TMx = f("TMx", f("Mx"))
    air = z > 0.3
    if air.sum() < 50:
        return None
    pitch = np.degrees(np.arcsin(np.clip(2 * (qw * qy - qx * qz), -1, 1)))
    roll = np.degrees(np.arctan2(2 * (qw * qx + qy * qz),
                                 1 - 2 * (qx * qx + qy * qy)))
    beta = np.degrees(np.arctan2(vy, np.maximum(np.abs(vx), 0.1) * np.sign(vx + 1e-9)))
    # ── 层一：λ(t) 与 onset ──
    dt = float(np.median(np.diff(t)))
    w = max(3, int(W_FIT / dt))
    lam = np.full(len(t), np.nan)
    lnx = np.log(np.abs(wx) + 1e-3)
    for i in range(w, len(t)):
        x = t[i - w:i]; y = lnx[i - w:i]
        xm = x - x.mean()
        lam[i] = float(np.dot(xm, y - y.mean()) / max(np.dot(xm, xm), 1e-9))
    onset = None
    sus = int(SUS_S / dt)
    for i in range(w, len(t) - sus):
        if not air[i]:
            continue
        if np.all(lam[i:i + sus] > LAM_THR) and np.max(np.abs(wx[i:i + sus])) > WX_PK:
            onset = float(t[i]); break
    # 层一b·振荡爆发判据（补漏：换边振荡型 |wx| 冲高回落，对数斜率
    # 不持续为正，层一 a 漏判——九记录实测三条坠机记录漏判实录）：
    # t_warn = 空中段 |wx| 首次越过 3.0 rad/s（自定线=层一峰值线两倍）
    # 且此前一秒内 |wx| 从未越线（排除起飞瞬态）。经验发散起点取
    # 层一 a 与层一 b 的较早者。
    warn = None
    pre = int(1.0 / dt)
    for i in range(pre, len(t)):
        if air[i] and abs(wx[i]) > 3.0 and np.max(np.abs(wx[i - pre:i])) <= 3.0:
            warn = float(t[i]); break
    t_div = min([x for x in (onset, warn) if x is not None], default=None)
    # ── 层二：裕度(t) 与 margin0 ──
    margin = np.array([env_lookup(roll[i], beta[i], P, B, A) - abs(TMx[i])
                       for i in range(len(t))])
    margin0 = None
    for i in range(len(t)):
        if not air[i]:
            continue
        if margin[i] < 0 and np.median(margin[i:]) < 0:
            margin0 = float(t[i]); break
    z_end = float(z[air][-1])
    verdict = "平稳"
    if t_div is not None and margin0 is not None:
        verdict = "权限先失(结构)" if margin0 <= t_div else "动态先发散(增益)"
    elif t_div is not None:
        verdict = "动态发散(权限未归零)"
    elif margin0 is not None:
        verdict = "权限归零(未发展成发散)"
    return dict(name=os.path.basename(csv_path), n=len(t), t_end=float(t[-1]),
                t_onset=onset, t_warn=warn, t_divergence=t_div,
                t_margin0=margin0, z_end=z_end,
                wx_max=float(np.abs(wx[air]).max()), verdict=verdict)


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    P, B, A = load_env()
    args = sys.argv[1:]
    files = args if args else sorted(
        glob.glob(os.path.join(LOG_DIR, "ident_stream_flight_*.csv")),
        key=os.path.getmtime)
    out = []
    print("== 失稳点提取（层一 λ 判据 + 层二 权限裕度）==", flush=True)
    print("%-38s %8s %8s %8s %7s %7s  %s" % ("记录", "t_onset", "t_warn",
                                          "t_marg0", "末高", "wx峰", "判定"), flush=True)
    for fp_ in files:
        try:
            r = analyze(fp_, P, B, A)
        except Exception as e:
            print("%-38s 解析失败 %s" % (os.path.basename(fp_), e), flush=True)
            continue
        if r is None:
            continue
        out.append(r)
        print("%-38s %8s %8s %8s %7.2f %7.2f  %s" % (
            r["name"][:38],
            "%.2f" % r["t_onset"] if r["t_onset"] is not None else "—",
            "%.2f" % r["t_warn"] if r["t_warn"] is not None else "—",
            "%.2f" % r["t_margin0"] if r["t_margin0"] is not None else "—",
            r["z_end"], r["wx_max"], r["verdict"]), flush=True)
    path = os.path.join(LOG_DIR, "divergence_extract_%s.json" % ts)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump({"ts": ts, "tool": "tools/divergence_extractor.py",
                   "thresholds": {"W_FIT": W_FIT, "LAM_THR": LAM_THR,
                                  "SUS_S": SUS_S, "WX_PK": WX_PK},
                   "results": out}, fp, ensure_ascii=False, indent=1, default=float)
    print("→ %s（%d 条）" % (path, len(out)), flush=True)


if __name__ == "__main__":
    main()
