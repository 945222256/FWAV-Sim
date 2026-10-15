# -*- coding: utf-8 -*-
"""
pipeline.py — 构型适航性评估管线入口，L2 线事项 I-173。

按接口契约（PIPELINE_CONTRACT.md 与 pipeline_contract.json）编排七步工
作流：配平、线性化、模态分解、可控性与可观性、稳定裕度、周期口径复核、
模板报告。产出固定写 airworthiness/results 时间戳目录，含 report.md、
verdict.json、contract_sha256.txt 三件。启动时做契约互验（机器可读契约
内登记的文本契约哈希对不上即拒绝运行），线性化步对 I-102 在案基准做回
归判据（特征值逐条一致）。禁止绕过本入口单独出正式报告。

用法：
    python airworthiness/pipeline.py --bank 10 --scope lateral
    python airworthiness/pipeline.py --bank 10 --scope full \
        --config-overrides 覆盖表.json --outdir 指定目录
"""
import argparse
import glob
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import steps  # noqa: E402

CONTRACT_JSON = os.path.join(_HERE, "pipeline_contract.json")
CONTRACT_MD = os.path.join(_HERE, "PIPELINE_CONTRACT.md")
PIPELINE_VERSION = "1.0"
FIDELITY_NOTES = [
    "离线拍平均模型缺翼拍惯性反力矩项（与 I-102 同款口径），绝对配平"
    "失守与部分裕度幅值受此限，方向结论相对可信",
    "配平点若处于舵饱和，饱和影响在配平节逐条声明",
]


def verify_contract():
    """契约互验：机器可读契约登记的文本契约哈希与实际文件对不上即拒
    绝启动。"""
    c = json.load(open(CONTRACT_JSON, encoding="utf-8"))
    actual = hashlib.sha256(open(CONTRACT_MD, "rb").read()).hexdigest()
    if c.get("contract_md_sha256") != actual:
        raise SystemExit("契约互验失败：PIPELINE_CONTRACT.md 与 "
                         "pipeline_contract.json 不同步，先按变更纪律"
                         "更新契约再运行")
    return c


def regression_check(lin, bank_deg, tol=1e-9):
    """线性化回归判据：与 ltv/i102_results 最新基准的同坡度特征值清单
    逐条对比（I-102 与本管线 A 部分同代码路径，应逐条一致）。基准找不
    到时返回跳过说明，不拦截运行。"""
    dirs = sorted(glob.glob(os.path.join(_ROOT, "ltv", "i102_results",
                                         "i102_*")))
    if not dirs:
        return {"checked": False, "note": "无 I-102 基准目录，跳过"}
    v = json.load(open(os.path.join(dirs[-1], "verdict.json"),
                       encoding="utf-8"))
    key = "%g" % bank_deg
    if key not in v.get("banks", {}):
        return {"checked": False, "note": "基准无该坡度读数，跳过"}
    ref = v["banks"][key]["eig"]
    mine = np.linalg.eigvals(np.asarray(lin["A"]))
    mine = sorted(mine, key=lambda z: -z.real)
    if len(ref) != len(mine):
        return {"checked": False, "note": "基准特征值条数不一致，跳过"}
    worst = 0.0
    for r, m in zip(ref, mine):
        worst = max(worst, abs(r["re"] - m.real), abs(r["im"] - m.imag))
    return {"checked": True, "max_abs_diff": float(worst),
            "pass": bool(worst <= tol), "tol": tol,
            "ref_dir": os.path.relpath(dirs[-1], _ROOT)}


def three_answers(trim, modes, gram, marg, floquet, sub):
    """三问结论：能不能控、稳不稳、余量多少。结论三种形态枚举：成立、
    成立但受限、不成立。"""
    n = len(sub["state_names"])
    eigs = modes["eigenvalues"]
    unstable = [e for e in eigs if e["re"] > 0]
    controllable = (gram["ctrb_rank"] == n)
    B = np.asarray(sub["B"])
    dead_cols = [sub["input_names"][j] for j in range(B.shape[1])
                 if np.max(np.abs(B[:, j])) < 1e-9]
    dead_note = ("；操纵通道 %s 在该配平点局部失效（输入矩阵列为零，见"
                 "尾面截断状态）" % "、".join(dead_cols)) if dead_cols else ""
    if not controllable:
        ctrl_txt = ("不成立：可控性矩阵秩 %d 小于状态维 %d，存在不可控"
                    "子空间" % (gram["ctrb_rank"], n)) + dead_note
    else:
        ratio = gram["min_strength"] / max(gram["median_strength"], 1e-300)
        if ratio > 0.1:
            ctrl_txt = ("成立：满秩可控且逐模态可控强度同量级（最弱模态"
                        "强度与中位数之比大于零点一）") + dead_note
        else:
            ctrl_txt = ("成立但受限：满秩可控但存在弱可控模态（最弱模态"
                        "强度与中位数之比小于零点一）") + dead_note
    if unstable:
        stab_txt = ("不成立：存在 %d 个正实部模态（最烈实部 %+.3f 每秒，"
                    "归类 %s）" % (len(unstable), unstable[0]["re"],
                                    modes["mode_tags"][0]))
    elif any(e["re"] > -1.0 for e in eigs):
        weak = [e for e in eigs if e["re"] > -1.0]
        stab_txt = ("成立但受限：全部模态收敛但存在 %d 个弱阻尼模态（最"
                    "弱实部 %+.3f 每秒）" % (len(weak), weak[0]["re"]))
    else:
        stab_txt = "成立：全部模态实部低于负一每秒"
    if floquet["max_mu"] >= 1.001:
        stab_txt += "；周期口径判增长（单拍倍率 %.5f）" % floquet["max_mu"]
    else:
        stab_txt += "；周期口径判稳定或近中性（单拍倍率 %.5f）" \
            % floquet["max_mu"]
    valid = [m for m in marg["simo_margins"] if m["valid"]]
    parts = []
    unstable_md = [m for m in marg["modal_damping"] if m["re"] > 0]
    stable_md = [m for m in marg["modal_damping"] if m["re"] <= 0]
    if unstable_md:
        parts.append("最烈正实部模态倍幅时间 %.2f 秒"
                     % unstable_md[0]["half_or_double_time_s"])
    elif stable_md:
        weakest = min(stable_md, key=lambda m: m["re"])
        parts.append("最弱阻尼模态半幅时间 %.2f 秒（实部 %+.3f 每秒）"
                     % (weakest["half_or_double_time_s"], weakest["re"]))
    if valid:
        parts.append("有效通道裕度：" + "；".join(
            "%s 增益裕度 %s 分贝、相位裕度 %s 度" % (
                m["input"],
                ("%.1f" % m["gm_db"]) if m["gm_db"] is not None else "无读数",
                ("%.1f" % m["pm_deg"]) if m["pm_deg"] is not None
                else "无读数") for m in valid))
    if marg["stabilizing_gains"]["needed"]:
        for ch in marg["stabilizing_gains"]["channels"]:
            if ch["achievable"]:
                parts.append("%s 通道速率反馈最小稳定化增益 %+.3f"
                             % (ch["input"], ch["min_gain"]))
            else:
                parts.append("%s 通道单通道扫描不可稳定化（扫描域内最良"
                             "最大实部 %+.3f 每秒）"
                             % (ch["input"], ch.get("best_max_re", float(
                                 "nan"))))
    marg_txt = ("；".join(parts) if parts
                else "开环稳定且无正实部模态，见裕度表")
    return {"controllable": ctrl_txt, "stable": stab_txt,
            "margins": marg_txt}


def render_report(bundle):
    """S7 模板报告：固定七节，顺序不得变动。"""
    sub = bundle["subsystem"]
    trim = bundle["trim"]
    modes = bundle["modes"]
    gram = bundle["gramians"]
    marg = bundle["margins"]
    flo = bundle["floquet"]
    reg = bundle["regression"]
    ans = bundle["answers"]
    fp = bundle["fingerprint"]
    run = bundle["run_info"]
    endorsements = bundle["endorsements_pending"]
    r = []
    r.append("# 构型适航性评估报告（%s域，坡度 %g 度）"
             % ({"lateral": "横侧向", "longitudinal": "纵向",
                 "full": "全机"}[run["scope"]], run["bank_deg"]))
    r.append("")
    r.append("一、头部：管线版本 %s，执行时间 %s，命令行 %s。构型指纹 %s"
             "（快照见 verdict.json）。分析域状态 %s，操纵通道 %s，观测行 "
             "%s。理论条目背书状态：%s。"
             % (run["version"], run["timestamp"], run["argv"],
                fp["sha256"][:12], "、".join(sub["state_names"]),
                "、".join(sub["input_names"]), "、".join(sub["C_rows"]),
                "待背书条目 " + "、".join(endorsements)
                if endorsements else "全部在案"))
    r.append("")
    r.append("二、配平节：前向速度 %.3f 米每秒，侧向速度 %+.3f 米每秒，"
             "竖向速度 %+.3f 米每秒，俯仰角 %+.4f 弧度，协调偏航率 "
             "%+.3f 弧度每秒，舵（升降 %+.3f、差动 %+.3f、方向 %+.3f），"
             "拍频 %.2f 赫兹。平衡残差范数 %.2e，判 %s。%s"
             % (trim["x_trim"][0], trim["x_trim"][1], trim["x_trim"][2],
                trim["x_trim"][3], trim["wz_coordinated"],
                trim["x_trim"][4], trim["x_trim"][5], trim["x_trim"][6],
                trim["freq_hz"], trim["residual_norm"],
                "过" if trim["trim_pass"] else "不过",
                ("贴界分量：" + "；".join(trim["at_bound"])
                 if trim.get("at_bound") else "无贴界分量")
                + ("；饱和舵位：" + "、".join(
                    n for n, s in zip(["升降", "差动", "方向"],
                                      trim["saturated"]) if s)
                   if any(trim["saturated"]) else "；三舵均在框内")))
    r.append("")
    r.append("线性化回归判据：%s" % (
        "对照 %s 最大绝对差 %.2e 对容差 %.0e，过" % (
            reg["ref_dir"], reg["max_abs_diff"], reg["tol"])
        if reg.get("checked") and reg.get("pass")
        else ("跳过（%s）" % reg.get("note", "未检") if not reg.get("checked")
              else "不过（最大绝对差 %.2e）" % reg["max_abs_diff"])))
    r.append("")
    r.append("三、线性化与模态节：输入矩阵（前六行，单位各状态导数每单位"
             "输入）如下表。")
    r.append("")
    r.append("| 状态行 | " + " | ".join(sub["input_names"]) + " |")
    r.append("|---|" + "---|" * len(sub["input_names"]))
    for i, sn in enumerate(sub["state_names"]):
        r.append("| %s | " % sn + " | ".join(
            "%+.4f" % v for v in sub["B"][i]) + " |")
    r.append("")
    tc = bundle["linearization"].get("tail_clip")
    if tc:
        r.append("尾面截断状态：机身攻角 %+.1f 度加升降舵偏合计 %+.1f 度，"
                 "侧滑角 %+.1f 度加方向舵偏合计 %+.1f 度，截断界 %.0f 度。"
                 "%s%s"
                 % (tc["alpha_body_deg"], tc["elev_sum_deg"],
                    tc["beta_body_deg"], tc["rudder_sum_deg"],
                    tc["stall_deg"],
                    "升降舵增量落在截断平台上（该通道局部操纵效能为零）。"
                    if tc["elev_clipped"] else "",
                    "方向舵增量落在截断平台上（该通道局部操纵效能为零）。"
                    if tc["rudder_clipped"] else ""))
        r.append("")
    dead_cols = [sub["input_names"][j]
                 for j in range(len(sub["B"][0]))
                 if max(abs(v) for v in
                        [row[j] for row in sub["B"]]) < 1e-9]
    if dead_cols:
        r.append("输入矩阵近零列：%s（操纵增量在该配平点不改变力与力矩，"
                 "原因见尾面截断状态或模型结构）。" % "、".join(dead_cols))
        r.append("")
    r.append("特征值清单（连续时间，按实部降序）：")
    r.append("")
    r.append("| 实部每秒 | 虚部每秒 | 频率赫兹 | 阻尼比 | 判读 | 主导状态 |")
    r.append("|---|---|---|---|---|---|")
    for e, tg in zip(modes["eigenvalues"], modes["mode_tags"]):
        r.append("| %+.3f | %+.3f | %.3f | %.3f | %s | %s |"
                 % (e["re"], e["im"], e["hz"], e["zeta"], e["tag"], tg))
    r.append("")
    if run["scope"] != "full":
        r.append("口径说明：本清单为分析域子块特征值。子块切断与域外状"
                 "态的耦合（在案结论横侧与俯仰两轴耦合振荡），模态归属"
                 "与全八态线性化不同；全八态特征值见线性化回归判据对照"
                 "的 I-102 产物与 verdict.json。")
        r.append("")
    r.append("四、可控性可观性节：可控性矩阵秩 %d（状态维 %d），可观性矩"
             "阵秩 %d。平移量 %.3f 每秒，可控性格拉姆最小奇异值 %.3e，可"
             "观性格拉姆最小奇异值 %.3e，李雅普诺夫方程相对残差（可控、"
             "可观）%.1e、%.1e。逐模态可控强度（实部每秒、强度）："
             % (gram["ctrb_rank"], len(sub["state_names"]),
                gram["obsv_rank"], gram["shift"], gram["Wc_sigma_min"],
                gram["Wo_sigma_min"], gram["lyap_residuals"][0],
                gram["lyap_residuals"][1]))
    r.append("")
    r.append("| 模态实部每秒 | 可控强度 |")
    r.append("|---|---|")
    for m in gram["modal_controllability"]:
        r.append("| %+.3f | %.3e |" % (m["re"], m["strength"]))
    r.append("")
    r.append("五、稳定裕度节：开环 %s。" % ("稳定" if marg["open_loop_stable"]
                                           else "不稳定"))
    for m in marg["simo_margins"]:
        if m["valid"]:
            r.append("- 通道 %s（输出 %s）：增益裕度 %s 分贝，相位裕度 "
                     "%s 度%s，增益交越 %s 赫兹，相位交越 %s 赫兹。"
                     % (m["input"], m["output"],
                        ("%.1f" % m["gm_db"]) if m["gm_db"] is not None
                        else "无读数",
                        ("%.1f" % m["pm_deg"]) if m["pm_deg"] is not None
                        else "无读数",
                        ("（相位绕圈 %d 周，折算主值）" % m["pm_wraps"]
                         if m.get("pm_wraps") else ""),
                        ("%.3f" % m["w_gc_hz"]) if m["w_gc_hz"] else "无",
                        ("%.3f" % m["w_pc_hz"]) if m["w_pc_hz"] else "无"))
        else:
            r.append("- 通道 %s：开环不稳定或无交越点，单通道裕度口径失"
                     "效，见稳定化增益需求。" % m["input"])
    if marg["stabilizing_gains"]["needed"]:
        for ch in marg["stabilizing_gains"]["channels"]:
            r.append("- 稳定化需求（%s 接 %s 速率反馈）：%s"
                     % (ch["input"], ch["feedback"],
                        "最小增益 %+.3f，可达" % ch["min_gain"]
                        if ch["achievable"]
                        else "扫描域内不可稳定化，最良最大实部 %+.3f 每秒"
                        % ch.get("best_max_re", float("nan"))))
    r.append("- %s频率表：频带最小值 %.3e（频率 %.3f 赫兹处）。"
             % (marg["retdiff_kind"], min(marg["retdiff_sigma_min"]),
                marg["retdiff_freqs_hz"][int(np.argmin(
                    marg["retdiff_sigma_min"]))]))
    r.append("")
    r.append("六、周期口径节：%s。%s" % (
        flo["reading"],
        flo.get("periodic_note") or "周期项范数 %.1e（非零，真周期判读）"
        % max(flo.get("Ac_norm", 0.0), flo.get("As_norm", 0.0))))
    r.append("")
    r.append("七、三问结论节：")
    r.append("")
    if not trim["trim_pass"]:
        r.append("配平门槛声明：配平判据不过（残差范数 %.2e，贴界分量 "
                 "%s）。后续三问的基点为最近平衡近似点而非真平衡点，结"
                 "论限于方向性与量级参考。"
                 % (trim["residual_norm"],
                    "、".join(trim["at_bound"]) if trim["at_bound"]
                    else "无"))
        r.append("")
    r.append("- 能不能控：%s。" % ans["controllable"])
    r.append("- 稳不稳：%s。" % ans["stable"])
    r.append("- 余量多少：%s。" % ans["margins"])
    r.append("")
    r.append("保真度声明：")
    for note in FIDELITY_NOTES:
        r.append("- %s。" % note)
    r.append("")
    r.append("机器可读判 verdict.json 同目录。零新增在环运行。")
    r.append("")
    return "\n".join(r)


def _i102_floquet_ref(bank_deg):
    """读 I-102 基准的同坡度周期口径读数并注明口径差异：I-102 的
    floquet 节只喂常数角速度块（周期项空），本管线按契约 S6 用逐相位
    偏导合成的真一阶周期项，两者读数不直接可比，此处并列留痕供对
    账。"""
    dirs = sorted(glob.glob(os.path.join(_ROOT, "ltv", "i102_results",
                                         "i102_*")))
    if not dirs:
        return None
    try:
        v = json.load(open(os.path.join(dirs[-1], "verdict.json"),
                           encoding="utf-8"))
        key = "%g" % bank_deg
        ref = v.get("banks", {}).get(key, {}).get("floquet")
    except (OSError, ValueError):
        return None
    if not ref:
        return None
    return {"i102_max_mu": ref.get("max_mu"),
            "i102_max_re": ref.get("max_re"),
            "note": "I-102 基准周期节为常数角速度块口径，本管线为真"
                    "一阶周期项口径，读数不直接可比"}


def run(args):
    contract = verify_contract()
    overrides = json.load(open(args.config_overrides, encoding="utf-8")) \
        if args.config_overrides else {}
    c_rows = (args.C_rows.split(",") if args.C_rows else
              {"lateral": ["vy", "roll", "wx", "wz"],
               "longitudinal": ["u", "pitch", "wy"],
               "full": ["vy", "roll", "wx", "wz"]}[args.scope])
    t0 = time.strftime("%Y%m%d_%H%M%S")
    outdir = args.outdir or os.path.join(
        _HERE, "results", "aw_%s_bank%g_%s" % (t0, args.bank, args.scope))
    os.makedirs(outdir, exist_ok=True)

    trim = steps.step1_trim(args.bank, config_overrides=overrides)
    lin = steps.step2_linearize(trim)
    reg = regression_check(lin, args.bank)
    sub = steps.scope_extract(lin, c_rows, args.scope)
    modes = steps.label_modes(steps.step3_modes(sub["A"]),
                              sub["state_names"])
    gram = steps.step4_gramians(sub)
    marg = steps.step5_margins(sub, modes,
                               actuation=json.loads(
                                   args.actuation) if args.actuation
                               else None)
    flo = steps.step6_floquet(dict(trim, config_overrides=overrides))
    flo["i102_ref_note"] = _i102_floquet_ref(args.bank)
    answers = three_answers(trim, modes, gram, marg, flo, sub)
    fp = steps.config_fingerprint(overrides)
    pending = [k for k, v in contract["endorsements"].items()
               if v["status"] == "待背书"]
    pending_zh = {"S2_B_columns": "S2 输入矩阵列偏导定义",
                  "S3_participation": "S3 参与因子",
                  "S4_shifted_gramians": "S4 平移格拉姆矩阵",
                  "S4_modal_controllability": "S4 逐模态可控强度",
                  "S5_margins_and_stabilizing_gain":
                      "S5 稳定裕度与稳定化增益"}
    bundle = {"trim": trim, "linearization": {
        "A": lin["A"].tolist(), "B": lin["B"].tolist(),
        "trim_state": lin["trim_state"],
        "state_names": lin["state_names"],
        "input_names": lin["input_names"],
        "tail_clip": lin.get("tail_clip")},
        "regression": reg, "subsystem": {
            "A": sub["A"].tolist(), "B": sub["B"].tolist(),
            "C": sub["C"].tolist(), "state_names": sub["state_names"],
            "input_names": sub["input_names"], "C_rows": sub["C_rows"],
            "scope": sub["scope"]},
        "modes": modes, "gramians": gram, "margins": marg,
        "floquet": flo, "answers": answers, "fingerprint": fp,
        "endorsements_pending": [pending_zh.get(k, k) for k in pending],
        "run_info": {"version": PIPELINE_VERSION, "timestamp": t0,
                     "argv": " ".join(sys.argv), "scope": args.scope,
                     "bank_deg": args.bank}}
    report = render_report(bundle)
    verdict = {k: v for k, v in bundle.items()
               if k not in ("subsystem",)}
    verdict["subsystem"] = {"state_names": sub["state_names"],
                            "input_names": sub["input_names"],
                            "C_rows": sub["C_rows"], "scope": sub["scope"],
                            "scope_idx": sub["scope_idx"],
                            "input_idx": sub["input_idx"]}
    with open(os.path.join(outdir, "report.md"), "w",
              encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(outdir, "verdict.json"), "w",
              encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "contract_sha256.txt"), "w",
              encoding="utf-8") as f:
        f.write(hashlib.sha256(open(CONTRACT_JSON, "rb").read()
                               ).hexdigest() + "\n")
    print(report)
    print("产出目录：%s" % os.path.relpath(outdir, _ROOT))
    return bundle


def main():
    ap = argparse.ArgumentParser(description="构型适航性评估管线（I-173）")
    ap.add_argument("--bank", type=float, required=True,
                    help="坡度角度（度）")
    ap.add_argument("--scope", required=True,
                    choices=["lateral", "longitudinal", "full"],
                    help="分析域")
    ap.add_argument("--config-overrides", default=None,
                    help="参数覆盖表 JSON 路径（键域见契约）")
    ap.add_argument("--C-rows", default=None,
                    help="观测行名表，逗号分隔，默认契约观测行")
    ap.add_argument("--actuation", default=None,
                    help="执行链参数 JSON（fn_hz、zeta、td_s）")
    ap.add_argument("--outdir", default=None, help="产出目录")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
