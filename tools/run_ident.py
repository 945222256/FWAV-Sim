# -*- coding: utf-8 -*-
"""
run_ident.py — 系留台架辨识：记录 /state → 按试验阶段分段 → 拟合 → 报告
========================================================================
用法（先跑 main_sim 且 EXPERIMENT_MODE 已设）：
  python tools/run_ident.py            # 持续记录直到 phase=done，然后拟合
输出：logs/ident_data.csv + logs/ident_result.md
"""
import csv
import json
import math
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_URL = "http://localhost:8090/state"
PHASE_FILE = os.path.join(ROOT, "logs", "experiment_phase.json")
CSV_PATH = os.path.join(ROOT, "logs", "ident_data.csv")
REPORT = os.path.join(ROOT, "logs", "ident_result.md")


# 绕过系统代理：本机 localhost 请求走代理会每次阻塞到 timeout（2026-09-18 实测
# 旧数据因此只有 ~0.5Hz 有效采样，舵效辨识被拍动振荡混叠污染）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_state():
    with _OPENER.open(STATE_URL, timeout=2) as r:
        return json.loads(r.read().decode())


def fetch_phase():
    try:
        with open(PHASE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"phase": "?"}


def q_rot_inv(q, v):
    """世界系向量 → 机体系（q=[w,x,y,z]）。"""
    w, x, y, z = q
    # R^T · v
    return [
        (1 - 2 * (y * y + z * z)) * v[0] + 2 * (x * y + w * z) * v[1] + 2 * (x * z - w * y) * v[2],
        2 * (x * y - w * z) * v[0] + (1 - 2 * (x * x + z * z)) * v[1] + 2 * (y * z + w * x) * v[2],
        2 * (x * z + w * y) * v[0] + 2 * (y * z - w * x) * v[1] + (1 - 2 * (x * x + y * y)) * v[2],
    ]


def record():
    print("[ident] 记录中（等 phase=done 自动收尾）…")
    rows = []
    last_phase = None
    t_start = time.time()
    while True:
        try:
            st = fetch_state()
            ph = fetch_phase().get("phase", "?")
        except Exception as e:
            print(f"[ident] 读取失败: {e}，2s 后重试")
            time.sleep(2.0)
            continue
        if st.get("pose") and st.get("F"):
            rows.append({
                "wall": round(time.time() - t_start, 3),
                "phase": ph,
                "t": round(st.get("t", 0), 3),
                "freq": st.get("freq", 0),
                "Fz": round(st["F"][2], 4),
                "Fx": round(st["F"][0], 4),
                "Mx": round(st.get("Tt", st.get("T", [0, 0, 0]))[0], 4),
                "My": round(st.get("Tt", st.get("T", [0, 0, 0]))[1], 4),
                "Mz": round(st.get("Tt", st.get("T", [0, 0, 0]))[2], 4),
                "z": round(st["pose"][2], 3),
                "roll": round(st["pose"][4], 4),
                "pitch": round(st["pose"][5], 4),
                "wx": round(st["vel"][3], 4),
                "wy": round(st["vel"][4], 4),
                "wz": round(st["vel"][5], 4),
                "tension": round(st.get("tether", {}).get("tension", 0), 3),
            })
        if ph != last_phase:
            print(f"[ident] phase → {ph}")
            last_phase = ph
        if ph == "done":
            break
        time.sleep(0.05)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[ident] 记录完成 {len(rows)} 行 → {CSV_PATH}")
    return rows


def tail_mean(rows, frac=0.6):
    """每段取后 frac 部分均值。"""
    if not rows:
        return None
    n = max(1, int(len(rows) * (1 - frac)))
    seg = rows[int(len(rows) * (1 - frac)):]
    return sum(seg) / max(1, len(seg))


def fit_sweep(rows):
    pts = []
    cur, key = [], None
    for r in rows:
        if r["phase"].startswith("sweep_f"):
            if r["phase"] != key and cur:
                pts.append((key, cur))
                cur = []
            key = r["phase"]
            cur.append(r)
    if cur:
        pts.append((key, cur))
    data = []
    for key, seg in pts:
        f = float(key.split("_f")[1])
        data.append((f, tail_mean([r["Fz"] for r in seg]),
                     tail_mean([r["Fx"] for r in seg])))
    if len(data) < 3:
        return None, data
    # 最小二乘 L̄ = k_f · f²（过原点）
    num = sum(f * f * L for f, L, _ in data)
    den = sum(f ** 4 for f, _, _ in data)
    k = num / den if den else 0
    return k, data


def fit_rudder(rows):
    trials = []
    cur, key = [], None
    for r in rows:
        if r["phase"].startswith("rud_") and not r["phase"].endswith("_back"):
            if r["phase"] != key and cur:
                trials.append((key, cur))
                cur = []
            key = r["phase"]
            cur.append(r)
    if cur:
        trials.append((key, cur))
    out = []
    for key, seg in trials:
        base = [r for r in rows if r["phase"] == "settle"]
        My0 = tail_mean([r["My"] for r in base] or [0]) if base else 0
        Mx0 = tail_mean([r["Mx"] for r in base] or [0]) if base else 0
        Mz0 = tail_mean([r["Mz"] for r in base] or [0]) if base else 0
        parts = key.split("_")          # rud_<ch>_<±amp>
        ch, amp = parts[1], float(parts[2])
        My = tail_mean([r["My"] for r in seg]) - (My0 or 0)
        Mx = tail_mean([r["Mx"] for r in seg]) - (Mx0 or 0)
        Mz = tail_mean([r["Mz"] for r in seg]) - (Mz0 or 0)
        out.append({"ch": ch, "amp": amp, "dMx": Mx, "dMy": My, "dMz": Mz,
                    "gain": {"elevator": My, "aileron": Mx, "rudder": Mz}.get(ch)})
    return out


def main():
    rows = record()
    import config.sim_config as cfg
    mg = cfg.MASS * 9.81
    lines = ["# 系留台架辨识结果", "",
             f"- 数据：{len(rows)} 行 @20Hz　体重 = {mg:.2f} N", ""]

    k, data = fit_sweep(rows)
    if k is not None and data:
        lines += ["## 扫频：周期平均升力 L̄(f) 与推力 T̄(f)", "",
                  "| f (Hz) | L̄ (N) | L̄/mg | T̄=Fx (N) |", "|---|---|---|---|"]
        for f, L, Tx in data:
            lines.append(f"| {f:.2f} | {L:+.2f} | {L / mg:+.2f} | {Tx:+.2f} |")
        f_hover = math.sqrt(mg / k) if k > 0 else float("nan")
        # 推力线性拟合 T̄ = a·f + b
        n_ = len(data)
        sf = sum(f for f, _, _ in data)
        st_ = sum(T for _, _, T in data)
        sff = sum(f * f for f, _, _ in data)
        sft = sum(f * T for f, _, T in data)
        det = n_ * sff - sf * sf
        a_t = (n_ * sft - sf * st_) / det if det else 0
        b_t = (st_ - a_t * sf) / n_ if n_ else 0
        lines += ["", f"**k_f = {k:.3f} N/Hz²**（L̄=k_f·f²，非单调数据下仅供参考）　"
                      f"**f_hover ≈ {f_hover:.2f} Hz**",
                  f"**推力拟合 T̄ = {a_t:+.3f}·f {b_t:+.3f} N**（线性）"]
    else:
        lines += ["## 扫频：数据不足（确认 EXPERIMENT_MODE='sweep'）", ""]

    tr = fit_rudder(rows)
    if tr:
        lines += ["", "## 舵效：ΔM/δ（含正负阶跃对称性）", "",
                  "| 通道 | δ (rad) | ΔMx | ΔMy | ΔMz | 本通道增益 (N·m/rad) |",
                  "|---|---|---|---|---|---|"]
        for t in tr:
            lines.append(f"| {t['ch']} | {t['amp']:+.2f} | {t['dMx']:+.3f} | "
                         f"{t['dMy']:+.3f} | {t['dMz']:+.3f} | "
                         f"{t['gain']:+.3f} |")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[ident] 报告 → {REPORT}")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    main()
