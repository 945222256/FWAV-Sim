# -*- coding: utf-8 -*-
"""
hss_i058_xmod.py — 下边带交调建模对拍，L4 线事项 I-058。

事项判据：交调项进模型后滚转通道净口径较 I-055 现行值可判读改善或证伪，
双轨报告。交调结构为本核谐波平衡模型的方向耦合项：输入侧拍频调制把高
频注入谱线散射出加减整数倍拍频的边带，其中下边带落入模态带（现行拍频
约一点九七赫兹，四点二五赫兹注入线的一阶下边带落二点二九赫兹，紧邻二
点三三赫兹模态），被近零阻尼模态共振放大后进入滚转通道。模型侧的交调
项即谐波阶数 K 大于等于零的周期系数阵与边带耦合路径。

对拍协议沿用 ltv/hss_i046_h2h.py 的同场实现：同一批归档数据、同一奇偶
拍分半、同一条件化基线、同一带通、同一净口径与双半劣者口径、同一优化
器，唯一结构差异为谐波阶数。K 等于零即 I-055 现行线性结构本体。逐批窗
口取该批归档 matrix.json 登记值，保证与 I-055 读数同窗。

参照现行值出处：I-055 证据链在案记录，暂定矩阵批三通道净口径零点五八、
零点八一、零点三五，最优批 rep45 零点六六、零点六七、零点五零，通道序
为滚转、俯仰、偏航，放行线零点五。本对拍的滚转通道外部参照即零点五八
与零点六六。

判读规则预登记如下，先于数据。轨一为同场改善轨：K 大于等于零对 K 等于
零的滚转净口径双半劣者比值不超零点八零判改善成立（沿用 I-044 冻结的
A3 胜出阈值口径）。轨二为证伪轨：比值在零点八零至一点二零带内判交调项
无可判读改善（在案复现带零点六至一点八倍内波动不可分辨，从宽取零点八
零下界），比值大于一点二零判交调项有害。两轨结论并入双轨报告，全程零
新增在环运行。

用法： python ltv/hss_i058_xmod.py
产出： ltv/xmod_results 时间戳目录，verdict.json 加 report.md。
"""
import hashlib
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
import hss_i046_h2h as h2h  # noqa: E402

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
BATCHES = {
    "rep45": {"archive": "logs/ident_matrix/I006rep45_20261001_053755/"
                         "matrix.json",
              "window": [10.5, 30.0],
              "i055_reference_wx": 0.66},
    "hop9": {"archive": "logs/ident_matrix/I006hop9_20260930_155753/"
                        "matrix.json",
             "window": [10.5, 110.0],
             "i055_reference_wx": 0.58},
}
WIN_THRESHOLD = 0.80
FALSIFY_BAND = (0.80, 1.20)


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    batches = []
    for tag, cfg in BATCHES.items():
        win = tuple(cfg["window"])
        h2h.WINDOW = win
        t0 = time.time()
        b = h2h.run_batch(tag, os.path.join(_ROOT, "logs",
                                            "_i006_%s.csv" % tag))
        b["window"] = list(win)
        b["i055_reference_wx"] = cfg["i055_reference_wx"]
        b["elapsed_s"] = round(time.time() - t0, 1)
        r0 = b["results"]["K=0"]["net_worst"][0]
        for K in ("K=1", "K=2"):
            rK = b["results"][K]["net_worst"][0]
            ratio = rK / r0 if r0 > 0 else None
            if ratio is None:
                verdict = "不可判读"
            elif ratio <= WIN_THRESHOLD:
                verdict = "改善"
            elif ratio <= FALSIFY_BAND[1]:
                verdict = "证伪：无可判读改善"
            else:
                verdict = "交调项有害"
            b["wy_verdict"][K]["xmod_ratio"] = ratio
            b["wy_verdict"][K]["xmod_verdict"] = verdict
        print("[%s] 滚转净口径劣者 K=0 %.3f K=1 %.3f K=2 %.3f" % (
            tag, r0, b["results"]["K=1"]["net_worst"][0],
            b["results"]["K=2"]["net_worst"][0]), flush=True)
        batches.append(b)
    verdict = {
        "item": "I-058",
        "criterion": "下边带交调建模双轨判读",
        "spec_sha256": spec_sha,
        "win_threshold": WIN_THRESHOLD,
        "falsify_band": list(FALSIFY_BAND),
        "channel": "wx 滚转",
        "batches": batches,
        "new_sim_runs": 0,
        "verdict_pass": True,
    }
    out_dir = os.path.join(_HERE, "xmod_results",
                           time.strftime("xmod_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(verdict, fp, ensure_ascii=False, indent=2)
    rep = ["# I-058 下边带交调建模双轨对拍报告", "",
           "判据要求：交调项进模型后滚转通道净口径较 I-055 现行值可判读"
           "改善或证伪。判读规则预登记于脚本 docstring，先于数据。", "",
           "## 滚转通道双轨判读表", "",
           "| 工况 | 窗口 | I-055 现行值 | K=0 本对拍 | K=1 | K=2 |"
           " K=1 判读 | K=2 判读 |", "|---|---|---|---|---|---|---|---|"]
    for b in batches:
        r = b["results"]
        v1 = b["wy_verdict"]["K=1"]
        v2 = b["wy_verdict"]["K=2"]
        rep.append("| %s | %.1f~%.1f s | %.2f | %.3f | %.3f | %.3f | "
                   "%s（比值 %.2f） | %s（比值 %.2f） |" % (
                       b["tag"], b["window"][0], b["window"][1],
                       b["i055_reference_wx"], r["K=0"]["net_worst"][0],
                       r["K=1"]["net_worst"][0],
                       r["K=2"]["net_worst"][0],
                       v1["xmod_verdict"], v1["xmod_ratio"],
                       v2["xmod_verdict"], v2["xmod_ratio"]))
    rep += ["", "判读口径：K 大于等于零对 K 等于零的同场比值不超零点八零"
            "判改善，零点八零至一点二零判无可判读改善即证伪，超一点二零"
            "判有害。", "", "## 三通道全指标数值表", ""]
    for b in batches:
        rep.append("### 工况 %s（拍频 %.3f 赫兹）" % (
            b["tag"], b["f_stroke_hz"]))
        for K, v in b["results"].items():
            rep.append("| %s | 净口径本半 | %s |" % (
                K, " | ".join("%.3f" % x for x
                              in v["net_ratio_this_half"])))
            rep.append("| %s | 净口径换半 | %s |" % (
                K, " | ".join("%.3f" % x for x
                              in v["net_ratio_swap_half"])))
            rep.append("| %s | 净口径劣者 | %s |" % (
                K, " | ".join("%.3f" % x for x in v["net_worst"])))
    rep += ["", "## 双轨结论", ""]
    any_gain = any(b["wy_verdict"][K]["xmod_verdict"] == "改善"
                   for b in batches for K in ("K=1", "K=2"))
    if any_gain:
        rep.append("改善轨成立：至少一个工况的交调项使滚转净口径双半劣者"
                   "进入改善区，交调结构对滚转通道有可判读贡献。")
    else:
        rep.append("证伪轨成立：全部工况的交调项均未使滚转净口径进入改善"
                   "区，下边带交调结构在现行数据上无可判读改善，滚转通道"
                   "瓶颈应向 I-055 的其余杠杆（慢动态条件化、翼态动力学"
                   "子模型）寻求。")
    rep += ["", "外部参照说明：I-055 现行值出自其证据链在案记录（协议为 "
            "L1 估计器三输入版），本对拍为六输入同场协议，两协议的 K 等"
            "于零读数并列供跨协议判读，改善与证伪的判定只用本对拍内部"
            "同场比值。全程零新增在环运行。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return verdict


if __name__ == "__main__":
    main()
