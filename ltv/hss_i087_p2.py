# -*- coding: utf-8 -*-
"""
hss_i087_p2.py — 偏航正切律可证伪预测二检验，事项 I-087。

预测二原文（I-084 报告第三节，提交 0f33534）：同配方五度批的稳定段
wz 除以 rud 增益约为二度批的二点五倍（坡度正切比二点五一），即从零点
四三到约一点一。

数据源：I-083 族归档的二度批与五度批（今日族管线顺序采集，同配方同
命令），三度批并列报告。判读规则预登记（先于计算）：五度比二度增益比
落在预测值二点五一的正负四成带（一点五至三点五）判证实；低于一点二判
证伪（增益不随坡度正切增长）；带间判部分证实并给残差方向。中间量沿用
I-084 口径：高频线组（一点零至八点零赫兹）谱线幅值代数和之比，稳定段
取坡度建立后且偏航率标准差未发散段，急转段剔除（I-084 已验剔窗除伪）。

产出写 ltv/i087_results 时间戳目录。
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
HI_LINES = [1.0, 1.5, 2.33, 3.0, 5.0, 8.0]
BATCHES = [
    {"tag": "I083fam_2deg", "bank_deg": 2.0, "t1": 40.5,
     "csv": "logs/ident_stream_flight_1006_080644.csv"},
    {"tag": "I083fam_3deg", "bank_deg": 3.0, "t1": 30.5,
     "csv": "logs/ident_stream_flight_1006_080943.csv"},
    {"tag": "I083fam_5deg", "bank_deg": 5.0, "t1": 15.5,
     "csv": "logs/ident_stream_flight_1006_081238.csv"},
]
PRED_RATIO = 2.51
BAND = (1.5, 3.5)
FALSIFY_BELOW = 1.2


def dft_at(x, t, f):
    dt = float(np.median(np.diff(t)))
    return complex(np.sum(x * np.exp(-2j * np.pi * f * t)) * dt)


def seg_stats(csv, t0, t1):
    df = pd.read_csv(os.path.join(_ROOT, csv))
    d = df[(df["t"] >= t0) & (df["t"] <= t1) & (df["z"] > 2.5)]
    t = d["t"].values
    span = t[-1] - t[0]
    wz, rud, ail = d["wz"].values, d["rud"].values, d["ail"].values
    inj = lambda x: sum(abs(dft_at(x, t, f)) for f in HI_LINES) / span
    return {"span_s": span, "wz_std": float(wz.std()),
            "wz_mean": float(wz.mean()),
            "gain_wz_rud": float(inj(wz) / inj(rud)),
            "gain_wz_ail": float(inj(wz) / inj(ail)),
            "inj_rud": float(inj(rud)), "inj_ail": float(inj(ail))}


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    rows = []
    for b in BATCHES:
        # 稳定窗：坡度建立后至终点急转前，窗端点由逐五秒标准差探针定位
        seg = seg_stats(b["csv"], 5.5, b["t1"])
        seg["tag"] = b["tag"]
        seg["bank_deg"] = b["bank_deg"]
        seg["window_stable"] = [5.5, b["t1"]]
        seg["tan_bank"] = float(np.tan(np.radians(b["bank_deg"])))
        rows.append(seg)
        print("%s bank=%g° 稳定段[5.5,%.1f) span=%.1fs wz_std=%.3f "
              "wz/rud=%.3f" % (b["tag"], b["bank_deg"], b["t1"],
                               seg["span_s"], seg["wz_std"],
                               seg["gain_wz_rud"]), flush=True)
    by = {r["bank_deg"]: r for r in rows}
    ratio_5_2 = by[5.0]["gain_wz_rud"] / by[2.0]["gain_wz_rud"]
    ratio_5_2a = by[5.0]["gain_wz_ail"] / by[2.0]["gain_wz_ail"]
    ratio_3_2 = by[3.0]["gain_wz_rud"] / by[2.0]["gain_wz_rud"]
    tan_5_2 = np.tan(np.radians(5)) / np.tan(np.radians(2))
    tan_3_2 = np.tan(np.radians(3)) / np.tan(np.radians(2))
    if BAND[0] <= ratio_5_2 <= BAND[1]:
        verdict2 = "证实"
    elif ratio_5_2 < FALSIFY_BELOW:
        verdict2 = "证伪"
    else:
        verdict2 = "部分证实（残差方向：%s）" % (
            "高于预测带" if ratio_5_2 > BAND[1] else "低于预测带")
    out = {
        "item": "I-087",
        "stage": "prediction_2_test",
        "spec_sha256": spec_sha,
        "prediction_verbatim": "同配方五度批稳定段 wz/rud 增益约为二度批"
                               "二点五倍（正切比二点五一）",
        "judge_rule": {"band": list(BAND), "falsify_below": FALSIFY_BELOW,
                       "pred_ratio": PRED_RATIO},
        "measurements": rows,
        "ratio_5_2_wz_rud": ratio_5_2,
        "ratio_5_2_wz_ail": ratio_5_2a,
        "ratio_3_2_wz_rud": ratio_3_2,
        "tan_ratio_5_2": float(tan_5_2),
        "tan_ratio_3_2": float(tan_3_2),
        "verdict_prediction2": verdict2,
        "new_sim_runs": 0,
    }
    out_dir = os.path.join(_HERE, "i087_results",
                           time.strftime("i087p2_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    rep = ["# I-087 预测二检验报告", "",
           "预测原文：%s。" % out["prediction_verbatim"],
           "数据源：I-083 族归档二度、三度、五度批（同配方同命令顺序采"
           "集）。判读规则预登记于脚本。", "",
           "| 工况 | 坡度 | 窗 | wz 标准差 | wz/rud | wz/ail | tan |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        rep.append("| %s | %g 度 | %.1f~%.1f s | %.3f | %.3f | %.3f | "
                   "%.4f |" % (r["tag"], r["bank_deg"],
                               r["window_stable"][0], r["window_stable"][1],
                               r["wz_std"], r["gain_wz_rud"],
                               r["gain_wz_ail"], r["tan_bank"]))
    rep += ["", "增益比：五度比二度为 %.2f（wz/rud）与 %.2f（wz/ail），"
            "预测值 %.2f；三度比二度为 %.2f，正切预测 %.2f。"
            % (ratio_5_2, ratio_5_2a, PRED_RATIO, ratio_3_2, tan_3_2),
            "", "## 结论", "",
            "预测二判读：**%s**（五度比二度 wz/rud 实测 %.2f 对预测 %.2f）。"
            % (verdict2, ratio_5_2, PRED_RATIO),
            "", "机器可读判 verdict.json 同目录。零新增在环运行。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return out


if __name__ == "__main__":
    main()
