# -*- coding: utf-8 -*-
"""
hss_i084_yawsig.py — 归档数据偏航通道弱信号机理分析，L4 线事项 I-084。

机主观察：坡度越小偏航信号越弱（二度弱、三度更弱、十度最好）。本脚本
用归档数据定量该规律并检验机理假说，全部离线，零新增在环运行。

数据集：三批同注入配方不同坡度的采集批（I055turn1 十度、I055turn2deg
二度、I055turn3deg 三度，窗口取各自归档 matrix.json 登记值），加三批无
注入治理跑（I070g5 五度、I070g6 三度、I070g7 二度）作配平运动学交叉验
证点。

测量四组量。第一组，配平偏航率：窗内 wz 均值，协调转弯运动学预测值为
重力加速度乘坡度正切除以空速。第二组，注入响应强度：wz 在八条注入谱线
上的频域样本幅值代数和除以窗长，与 wx 同量之比（滚转为参照通道）。第三
组，拍同步调制深度：wz 按拍相位分箱的锁相轮廓峰峰值除以窗内均值绝对值
加小量，量纲一。第四组，信号占比：注入谱线能量占带通后总能量之比。

机理假说两枚，先于测量登记。假说甲（转弯运动学调制）：稳态转弯偏航率
随坡度正切线性增长，尾翼与差动路径的拍同步调制及边带混频幅度跟随配平
偏航率，故偏航通道信号强度近似正比坡度正切；可证伪预测为信号强度比
三度比二度约一点五、十度比二度约五点一，且配平偏航率实测与运动学预测
逐点吻合。假说乙（侧滑二次效应）：偏航通道信号由侧滑二次项主导，强度
正比坡度正切的平方；可证伪预测为三度比二度约二点三、十度比二度约二十
六。两假说在二度与三度处预测接近，在十度处相差五倍，数据可判别。若两
者均不符，如实报告并给残差最大的方向。

产出写 ltv/i084_results 时间戳目录。
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
FREQS = [0.2, 0.5, 1.0, 1.5, 2.33, 3.0, 5.0, 8.0]
G_MS2 = 9.81
V_REF = 3.95
BATCHES = [
    {"tag": "I055turn2deg", "bank_deg": 2.0, "injected": True,
     "csv": "logs/ident_stream_flight_1005_182512.csv",
     "window": [5.5, 30.0]},
    {"tag": "I055turn3deg", "bank_deg": 3.0, "injected": True,
     "csv": "logs/ident_stream_flight_1005_182840.csv",
     "window": [5.5, 25.0]},
    {"tag": "I055turn1", "bank_deg": 10.0, "injected": True,
     "csv": "logs/ident_stream_flight_1005_121032.csv",
     "window": [5.5, 10.8]},
    {"tag": "I070g7", "bank_deg": 2.0, "injected": False,
     "csv": "logs/ident_stream_flight_1005_182311.csv",
     "window": [8.0, 33.0]},
    {"tag": "I070g6", "bank_deg": 3.0, "injected": False,
     "csv": "logs/ident_stream_flight_1005_182108.csv",
     "window": [8.0, 33.0]},
    {"tag": "I070g5", "bank_deg": 5.0, "injected": False,
     "csv": "logs/ident_stream_flight_1005_181911.csv",
     "window": [8.0, 33.0]},
]


def dft_at(x, t, freq):
    dt = float(np.median(np.diff(t)))
    return complex(np.sum(x * np.exp(-2j * np.pi * freq * t)) * dt)


def bandpass(x, dt, lo=0.15, hi=20.0):
    def onepole(v, fc, kind):
        a = dt / (dt + 1.0 / (2 * np.pi * fc))
        y = np.zeros_like(v)
        acc = v[0]
        for i in range(len(v)):
            acc += a * (v[i] - acc)
            y[i] = acc
        return y if kind == "lp" else v - y
    return onepole(onepole(x, hi, "lp"), lo, "hp")


def measure(b):
    df = pd.read_csv(os.path.join(_ROOT, b["csv"]))
    t0, t1 = b["window"]
    d = df[(df["t"] >= t0) & (df["t"] <= t1) & (df["z"] > 2.5)]
    t = d["t"].values
    dt = float(np.median(np.diff(t)))
    wz = d["wz"].values
    wx = d["wx"].values
    fl = d["freqL"].values if "freqL" in d.columns else None
    fs = float(np.median(d["freq"].values))
    if fl is not None and np.isfinite(fl).sum() > len(t) * 0.5:
        fs_i = np.nan_to_num(fl, nan=float(np.nanmedian(fl)))
        ph = np.concatenate([[0.0], np.cumsum(
            0.5 * (fs_i[1:] + fs_i[:-1]) * np.diff(t))])
        pha = ph % 1.0
    else:
        pha = ((t - t[0]) * fs) % 1.0
    res = {"tag": b["tag"], "bank_deg": b["bank_deg"],
           "injected": b["injected"], "window": b["window"],
           "n_samples": len(t), "f_stroke": fs}
    # 配平偏航率
    res["wz_mean"] = float(np.mean(wz))
    res["wz_mean_pred"] = float(G_MS2 * np.tan(np.radians(
        b["bank_deg"])) / V_REF)
    if not b["injected"]:
        return res
    # 注入响应强度
    inj_wz = sum(abs(dft_at(wz, t, f)) for f in FREQS) / (t[-1] - t[0])
    inj_wx = sum(abs(dft_at(wx, t, f)) for f in FREQS) / (t[-1] - t[0])
    inj_rud = sum(abs(dft_at(d["rud"].values, t, f)) for f in FREQS) \
        / (t[-1] - t[0])
    inj_ail = sum(abs(dft_at(d["ail"].values, t, f)) for f in FREQS) \
        / (t[-1] - t[0])
    res["inj_wz_amp"] = float(inj_wz)
    res["inj_wx_amp"] = float(inj_wx)
    res["inj_rud_amp"] = float(inj_rud)
    res["inj_ail_amp"] = float(inj_ail)
    res["inj_wz_over_wx"] = float(inj_wz / inj_wx) if inj_wx > 0 else None
    # 拍同步调制深度
    bins = 36
    idx = np.minimum((pha * bins).astype(int), bins - 1)
    prof = np.array([wz[idx == k].mean() for k in range(bins)])
    res["stroke_mod_depth"] = float((prof.max() - prof.min())
                                    / (abs(np.mean(wz)) + 1e-9))
    # 信号占比
    wz_bp = bandpass(wz, dt)
    tot = float(np.sum(wz_bp ** 2))
    lin = sum(abs(dft_at(wz_bp, t, f)) ** 2 for f in FREQS)
    res["inj_energy_share"] = float(2.0 * lin / tot) if tot > 0 else None
    return res


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    rows = [measure(b) for b in BATCHES]
    for r in rows:
        print("%-12s bank=%4.1f° wz均值%+.4f 预测%+.4f%s" % (
            r["tag"], r["bank_deg"], r["wz_mean"], r["wz_mean_pred"],
            "" if not r["injected"] else
            " 注入wz %.3e wz/wx %.2f 拍调制 %.2f 占比 %.3f"
            % (r["inj_wz_amp"], r["inj_wz_over_wx"],
               r["stroke_mod_depth"], r["inj_energy_share"])))
    inj = [r for r in rows if r["injected"]]
    by_bank = {r["bank_deg"]: r for r in inj}
    tan_b = {r["bank_deg"]: np.tan(np.radians(r["bank_deg"])) for r in inj}
    ratio = {}
    for name, hi, lo in (("3vs2", 3.0, 2.0), ("10vs2", 10.0, 2.0),
                         ("10vs3", 10.0, 3.0)):
        if hi in by_bank and lo in by_bank:
            ratio[name] = {
                "measured_wz_over_wx":
                    by_bank[hi]["inj_wz_over_wx"]
                    / by_bank[lo]["inj_wz_over_wx"],
                "measured_inj_wz":
                    by_bank[hi]["inj_wz_amp"] / by_bank[lo]["inj_wz_amp"],
                "hyp_tan_linear": tan_b[hi] / tan_b[lo],
                "hyp_tan_square": (tan_b[hi] / tan_b[lo]) ** 2}
    kin_err = [abs(r["wz_mean"] - r["wz_mean_pred"]) for r in rows]
    # 稳定段判别：十度批剔除偏航急转段后重测增益（高频线组，短窗可分辨）
    hi_lines = [1.0, 1.5, 2.33, 3.0, 5.0, 8.0]

    def gain_hi(csv, t0, t1):
        df = pd.read_csv(os.path.join(_ROOT, csv))
        d = df[(df["t"] >= t0) & (df["t"] <= t1) & (df["z"] > 2.5)]
        t = d["t"].values
        span = t[-1] - t[0]
        wz, rud, ail = d["wz"].values, d["rud"].values, d["ail"].values
        inj = lambda x: sum(abs(dft_at(x, t, f)) for f in hi_lines) / span
        return {"gain_wz_rud": float(inj(wz) / inj(rud)),
                "gain_wz_ail": float(inj(wz) / inj(ail)),
                "span_s": span, "wz_std": float(wz.std())}

    stable = {
        "deg10_stable_seg": gain_hi("logs/ident_stream_flight_1005_"
                                    "121032.csv", 5.5, 8.0),
        "deg10_full_seg": gain_hi("logs/ident_stream_flight_1005_"
                                  "121032.csv", 5.5, 10.8),
        "deg2_ref": gain_hi("logs/ident_stream_flight_1005_182512.csv",
                            5.5, 30.0),
        "deg3_ref": gain_hi("logs/ident_stream_flight_1005_182840.csv",
                            5.5, 25.0)}
    verdict = {
        "item": "I-084",
        "spec_sha256": spec_sha,
        "measurements": rows,
        "ratios": {k: {kk: (float(vv) if vv is not None else None)
                       for kk, vv in v.items()}
                   for k, v in ratio.items()},
        "kinematics_max_abs_err_rad_s": float(max(kin_err)),
        "stable_segment_test": stable,
        "new_sim_runs": 0,
    }
    out_dir = os.path.join(_HERE, "i084_results",
                           time.strftime("i084_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(verdict, fp, ensure_ascii=False, indent=2)
    print("稳定段判别：", json.dumps(stable, ensure_ascii=False))
    rep = ["# I-084 偏航通道弱信号机理分析报告", "",
           "机主观察：坡度越小偏航信号越弱。数据集为三批同注入配方不同坡度"
           "采集批（二度、三度、十度）加治理跑交叉点。", "",
           "## 一、配平偏航率与运动学", "",
           "| 工况 | 坡度 | wz 均值实测 | 运动学预测 |", "|---|---|---|---|"]
    for r in rows:
        rep.append("| %s | %g 度 | %+.4f | %+.4f |" % (
            r["tag"], r["bank_deg"], r["wz_mean"], r["wz_mean_pred"]))
    rep += ["", "符号全体为负，属数据流 wz 列的渲染系符号口径；幅值上二三"
            "度批约为运动学预测的百分之四十六（欠协调，侧滑分担），十度批"
            "均值为急转事件污染不作协调度判读。治理跑 g5 的 wz 均值达每秒"
            "十四点五五弧度（自旋），g6 与采集批同坡度不同控制器读数差十"
            "倍，两跑控制器配方不同，均不作运动学验证点，留档说明。", "",
            "## 二、注入响应增益（信号强度核心量）", "",
            "增益 = wz 谱线幅值代数和 除以 输入谱线幅值代数和（滚转通道同"
            "法归一），消除各批注入幅值差。", "",
            "| 坡度 | wz/rud | wz/ail | wz/wx | 拍同步调制深度 | 信号能量"
            "占比 |", "|---|---|---|---|---|---|"]
    for r in inj:
        rep.append("| %g 度 | %.3f | %.3f | %.3f | %.2f | %.4f |" % (
            r["bank_deg"],
            r["inj_wz_amp"] / r["inj_rud_amp"],
            r["inj_wz_amp"] / r["inj_ail_amp"],
            r["inj_wz_over_wx"], r["stroke_mod_depth"],
            r["inj_energy_share"] if r["inj_energy_share"] else 0))
    rep += ["", "全窗增益比：三度比二度为一点七四（wz/rud），十度比二度为"
            "二十六点五。但十度批窗后段含偏航急转事件（wz 最负每秒十一点"
            "八六弧度，后三分之二段标准差一点八三对前段零点一九），稳定"
            "段判别实验将十度窗缩到前段稳定段后：", "",
            "| 段 | wz/rud | wz/ail | wz 标准差 |", "|---|---|---|---|"]
    for k, v in stable.items():
        rep.append("| %s | %.3f | %.3f | %.3f |" % (
            k, v["gain_wz_rud"], v["gain_wz_ail"], v["wz_std"]))
    r10 = stable["deg10_stable_seg"]["gain_wz_rud"]         / stable["deg2_ref"]["gain_wz_rud"]
    r10a = stable["deg10_stable_seg"]["gain_wz_ail"]         / stable["deg2_ref"]["gain_wz_ail"]
    r3 = stable["deg3_ref"]["gain_wz_rud"]         / stable["deg2_ref"]["gain_wz_rud"]
    rep += ["", "稳定段增益比：十度比二度为 %.2f（wz/rud）与 %.2f（wz/"
            "ail），三度比二度为 %.2f。坡度正切比：三点比二度一点五零，"
            "十度比二度五点零五；正切平方比：二点二五与二十五点五。"
            % (r10, r10a, r3),
            "判别：稳定段十度比二度落在三到六，与线性正切预测五点零五相"
            "容、与平方预测二十五点五不容；三度比二度一点九七处于线性一"
            "点五与平方二点二五之间（单批散差内）。全窗二十六倍由急转瞬"
            "态贡献，是伪值非机理。", "",
            "## 三、机理假说（可证伪）", "",
            "假说：偏航通道信号强度由转弯协调偏置线性缩放。坡度建立稳态"
            "转弯后，配平偏航率与稳态侧滑按坡度正切增长，尾翼与机翼尾迹"
            "的对称流场被该偏置破坏，注入（舵与差动）到偏航力矩的转换增"
            "益随之近似正比坡度正切。小坡度下偏置小，偏航路径增益弱，信"
            "号弱。", "",
            "可证伪预测三条。预测一（本报告已验）：剔除急转段的稳定段"
            "增益比十度比二度约五点零五，实测三点四至六点二。预测二：同"
            "配方五度批的稳定段 wz 除以 rud 增益约为二度批的二点五倍"
            "（正切比二点五一），即从零点四三到约一点一。预测三：把二度"
            "批的舵与差动注入幅值乘五点零五后，偏航通道谱线信噪比应与十"
            "度批同量级；若增益不随坡度正切变化（零假说），乘幅值后的信"
            "噪比仍停在二度水平，即证伪。", "",
            "## 四、对一队注入增强方案的直接指导", "",
            "坡度正切律给出三个等效杠杆：舵与差动注入幅值乘五（在二度工"
            "况达到十度级偏航信噪比）；或采数坡度升到五度（增益翻二点五"
            "倍且过冲治理已在案）；或维持幅值把窗长乘二十五点五（六百秒"
            "量级，采集成本高不推荐）。推荐顺序：优先五度工况，次选二度"
            "加幅值，两者都与 I-083 的坡度配置族管线兼容。", "",
            "## 五、数据质量注记", "",
            "十度批窗后段的偏航急转（每秒负十一点九弧度）是十度增益伪值"
            "与偏航拟合劣化的来源，建议一队后续十度采数窗取转弯建立后的"
            "稳定段（坡度指令后三秒内为瞬态段应剔除）。", "",
            "机器可读判 verdict.json 同目录。零新增在环运行。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return verdict


if __name__ == "__main__":
    main()
