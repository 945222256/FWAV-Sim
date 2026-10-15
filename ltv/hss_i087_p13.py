# -*- coding: utf-8 -*-
"""
hss_i087_p13.py — 偏航正切律预测一与预测三检验，事项 I-087 收尾。

数据源（全部已落地归档，零新增在环运行）：
  I-085 五度正式批 I085_TURN5_FORMAL（mix 关加方向舵零点零七）与其重
  复批两跑；I-081 二度加幅阶梯三批（方向舵零点零七、零点一零、零点一
  五，mix 关，对基准零点零三依次乘二点三三、三点三三、五点零零）；I-083
  族二度批；I-055 原十度批（稳定段）。

预测一原文（I-084 报告第三节）：剔除急转段的稳定段增益比十度比二度约
五点零五。检验法：十度批稳定段（五点五至八秒，I-084 已定位）增益与二
度批稳定段增益之比，另列五度正式批对二度同配方批（I081nomix 与正式批
同为 mix 关加零点零七）之比作同配方五度对二度复检。判读规则预登记：比
值落在五点零五正负四成带（三点零至七点一）判证实；低于一点五判证伪；
带间判部分证实并给方向。

预测三原文：把二度批的舵与差动注入幅值乘五点零五后，偏航通道谱线信
噪比应与十度批同量级。检验法：二度 mix 关加幅阶梯（乘二点三三、三点
三三、五点零零）逐档测偏航谱线信噪比（八注入线幅值代数和除以非线频点
离线幅值中位数的二十邻档和），检验两问：信噪比是否随幅值近线性增长
（线性系统预测为比例）；乘五点零零档（零点一五）的信噪比是否与十度批
稳定段同量级（比值零点五至二判同量级）。判读规则预登记：两问全过判证
实；线性过而十度级不过判半证实（幅值有效但十度另有增益来源）；线性不
过判证伪。

稳定窗定位法（逐批自动）：五秒段标准差探针，自坡度建立后起取连续稳定
段（段标准差低于首三段中位数三倍），遇急转段即止。产出写
ltv/i087_results 时间戳目录。
"""
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
LINES = [0.2, 0.5, 1.0, 1.5, 2.33, 3.0, 5.0, 8.0]
OFF_LINES = sorted(set(round(f + d * 0.07, 4) for f in LINES
                       for d in (-1, 1)) - set(LINES))
BATCHES = [
    {"tag": "fam2deg", "bank": 2.0, "rud_amp": 0.03,
     "csv": "logs/ident_stream_flight_1006_080644.csv"},
    {"tag": "nomix2deg_a233", "bank": 2.0, "rud_amp": 0.07,
     "csv": "logs/ident_stream_flight_1006_080357.csv"},
    {"tag": "yaw10_2deg_a333", "bank": 2.0, "rud_amp": 0.10,
     "csv": "logs/ident_stream_flight_1006_081023.csv"},
    {"tag": "yaw15_2deg_a500", "bank": 2.0, "rud_amp": 0.15,
     "csv": "logs/ident_stream_flight_1006_080715.csv"},
    {"tag": "formal5deg", "bank": 5.0, "rud_amp": 0.07,
     "csv": "logs/ident_stream_flight_1006_084706.csv"},
    {"tag": "formal5deg_rep2", "bank": 5.0, "rud_amp": 0.07,
     "csv": "logs/ident_stream_flight_1006_085048.csv"},
    {"tag": "formal5deg_rep3", "bank": 5.0, "rud_amp": 0.07,
     "csv": "logs/ident_stream_flight_1006_085326.csv"},
    {"tag": "deg10ref", "bank": 10.0, "rud_amp": None,
     "csv": "logs/ident_stream_flight_1005_121032.csv",
     "t1_fixed": 8.0},
]
P1_PRED = 5.05
P1_BAND = (3.0, 7.1)
P1_FALSIFY_BELOW = 1.5
P3_SAME_LEVEL_BAND = (0.5, 2.0)


def dft_at(x, t, f):
    dt = float(np.median(np.diff(t)))
    return complex(np.sum(x * np.exp(-2j * np.pi * f * t)) * dt)


def stable_end(df, t0=5.5):
    """五秒段标准差探针定位稳定段终点。"""
    d = df[(df["t"] >= t0) & (df["z"] > 2.5)]
    t, wz = d["t"].values, d["wz"].values
    segs = []
    a = t0
    while a < t[-1] - 5.0:
        m = (t >= a) & (t < a + 5.0)
        if m.sum() > 100:
            segs.append((a, float(wz[m].std())))
        a += 5.0
    base = np.median([s for _, s in segs[:3]])
    end = t0
    for a, s in segs:
        if s > 3.0 * max(base, 1e-3):
            break
        end = a + 5.0
    return min(end, t[-1]), segs


def measure(b):
    df = pd.read_csv(os.path.join(_ROOT, b["csv"]))
    t0 = 5.5
    if "t1_fixed" in b:
        t1 = b["t1_fixed"]
        segs = None
    else:
        t1, segs = stable_end(df, t0)
    d = df[(df["t"] >= t0) & (df["t"] <= t1) & (df["z"] > 2.5)]
    t = d["t"].values
    span = t[-1] - t[0]
    wz, rud, ail = d["wz"].values, d["rud"].values, d["ail"].values
    inj = lambda x: sum(abs(dft_at(x, t, f)) for f in LINES) / span
    off = sum(abs(dft_at(wz, t, f)) for f in OFF_LINES) / span
    res = {"tag": b["tag"], "bank": b["bank"], "rud_amp": b["rud_amp"],
           "window": [t0, round(float(t1), 1)], "span_s": round(span, 1),
           "wz_std": float(wz.std()),
           "inj_rud": float(inj(rud)), "inj_ail": float(inj(ail)),
           "gain_wz_rud": float(inj(wz) / inj(rud)),
           "snr_wz": float(inj(wz) / off) if off > 0 else None,
           "seg_probe": segs}
    return res


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    rows = [measure(b) for b in BATCHES]
    for r in rows:
        print("%-18s bank=%4.1f° 窗%6.1f~%5.1fs span=%5.1fs wz_std=%.3f "
              "gain=%.3f snr=%.2f" % (
                  r["tag"], r["bank"], r["window"][0], r["window"][1],
                  r["span_s"], r["wz_std"], r["gain_wz_rud"],
                  r["snr_wz"] or -1), flush=True)
    by = {r["tag"]: r for r in rows}
    # 预测一：十度比二度稳定段增益比
    r10 = by["deg10ref"]["gain_wz_rud"] / by["fam2deg"]["gain_wz_rud"]
    r10n = by["deg10ref"]["gain_wz_rud"] \
        / by["nomix2deg_a233"]["gain_wz_rud"]
    if P1_BAND[0] <= r10 <= P1_BAND[1]:
        v1 = "证实"
    elif r10 < P1_FALSIFY_BELOW:
        v1 = "证伪"
    else:
        v1 = "部分证实（残差方向：%s）" % (
            "高于预测带" if r10 > P1_BAND[1] else "低于预测带")
    # 同配方五度对二度复检（正式配方 mix 关零点零七）
    r5f = by["formal5deg"]["gain_wz_rud"] \
        / by["nomix2deg_a233"]["gain_wz_rud"]
    # 预测三：加幅阶梯
    ladder = [by["nomix2deg_a233"], by["yaw10_2deg_a333"],
              by["yaw15_2deg_a500"]]
    amps = [r["rud_amp"] for r in ladder]
    snrs = [r["snr_wz"] for r in ladder]
    snr_per_amp = [s / a for s, a in zip(snrs, amps)]
    spread = (max(snr_per_amp) - min(snr_per_amp)) \
        / np.mean(snr_per_amp)
    linear_ok = spread <= 0.35
    snr_15 = by["yaw15_2deg_a500"]["snr_wz"]
    snr_10 = by["deg10ref"]["snr_wz"]
    level_ratio = snr_15 / snr_10
    level_ok = P3_SAME_LEVEL_BAND[0] <= level_ratio <= P3_SAME_LEVEL_BAND[1]
    if linear_ok and level_ok:
        v3 = "证实"
    elif linear_ok and not level_ok:
        v3 = "半证实（信噪比随幅值近线性，但乘五档未达十度级，比值 %.2f）" \
            % level_ratio
    else:
        v3 = "证伪（信噪比幅值比散布 %.0f%% 超线性容差）" % (spread * 100)
    out = {
        "item": "I-087",
        "stage": "prediction_1_3_test",
        "spec_sha256": spec_sha,
        "measurements": rows,
        "p1": {"ratio_10_2_vs_fam2deg": r10,
               "ratio_10_2_vs_nomix2deg": r10n,
               "ratio_5formal_2nomix": r5f, "pred": P1_PRED,
               "band": list(P1_BAND), "verdict": v1},
        "p3": {"amps": amps, "snrs": snrs, "snr_per_amp": snr_per_amp,
               "spread_frac": float(spread), "linear_ok": bool(linear_ok),
               "snr_amp5": float(snr_15), "snr_10deg": float(snr_10),
               "level_ratio": float(level_ratio),
               "level_band": list(P3_SAME_LEVEL_BAND), "verdict": v3},
        "new_sim_runs": 0,
    }
    out_dir = os.path.join(_HERE, "i087_results",
                           time.strftime("i087p13_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    rep = ["# I-087 预测一与预测三检验报告", "",
           "数据源：I-085 五度正式批与重复批、I-081 二度加幅阶梯、I-083 "
           "族二度批、I-055 十度批，全部归档离线。稳定窗由五秒段标准差探"
           "针逐批自动定位。", "",
           "| 批 | 坡度 | 方向舵幅 | 稳定窗 | 增益 wz/rud | 信噪比 |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        rep.append("| %s | %g 度 | %s | %.1f~%.1f s | %.3f | %.2f |" % (
            r["tag"], r["bank"], ("%.2f" % r["rud_amp"])
            if r["rud_amp"] else "原配方", r["window"][0], r["window"][1],
            r["gain_wz_rud"], r["snr_wz"] if r["snr_wz"] else -1))
    rep += ["", "## 预测一", "",
            "原文：剔除急转段的稳定段增益比十度比二度约五点零五。",
            "实测：十度稳定段对族二度批增益比 %.2f，对同配方二度批（mix "
            "关零点零七）%.2f；同配方五度正式批对二度批 %.2f。判读带三点"
            "零至七点一。" % (r10, r10n, r5f),
            "结论：**%s**。" % v1,
            "", "## 预测三", "",
            "原文：把二度批的舵与差动注入幅值乘五点零五后，偏航通道谱线"
            "信噪比应与十度批同量级。",
            "加幅阶梯（二度，mix 关）：幅值 %s 对应信噪比 %s，信噪比除"
            "以幅值 %s，散布 %.0f%%（线性容差三成五）。"
            % (amps, ["%.1f" % s for s in snrs],
               ["%.0f" % s for s in snr_per_amp], spread * 100),
            "乘五点零零档信噪比 %.1f 对十度稳定段 %.1f，比值 %.2f（同量"
            "级带零点五至二）。" % (snr_15, snr_10, level_ratio),
            "结论：**%s**。" % v3,
            "", "机器可读判 verdict.json 同目录。零新增在环运行。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return out


if __name__ == "__main__":
    main()
