# -*- coding: utf-8 -*-
"""fit_diag.py 拟合质量诊断，事项 I-090。

留出验证未过时，从拟合报告自动提取诊断三要素并渲染诊断块：
  一 最差通道：净口径比值最高的通道；
  二 两半差异：本半与换半的比值对照，倍差超过二判半依赖；
  三 饱和占比关联：最差通道对应执行面的饱和占比与一成污染线对照，
     超线则限幅污染列为最差通道的首要嫌疑。

纯离线纯标准库。可独立运行做回归验证：
  PYTHONUTF8=1 python -m modules.experiment_pipeline.fit_diag 报告.md [报告2.md]
运行器在链执行后自动调用：凡留出验证未过的拟合，诊断块并入汇总对照
表与归档。报告行缺失时对应要素留空并如实标注，不臆测。
"""
from __future__ import annotations

import re
import sys

CHANNELS = ("wx", "wy", "wz")
CHANNEL_ACTUATOR = {"wx": "ail", "wy": "elev", "wz": "rud"}
ACTUATOR_NAME = {"ail": "差动", "elev": "升降舵", "rud": "方向舵"}
SAT_WARN_RATIO = 10.0
HALF_SPREAD_FACTOR = 2.0

_SAT_RE = re.compile(
    r"饱和占比\s*ail/elev/rud[：:]\s*([0-9.]+)%\s*([0-9.]+)%\s*([0-9.]+)%")
_NET_RE = re.compile(r"^-\s*(wx|wy|wz)\s*净比值\s*([0-9.]+)")
_HOLDOUT_RE = re.compile(r"^##\s*留出验证.*[：:]\s*(过|不过)\s*$")
_HALF_ROW_RE = re.compile(
    r"^\|\s*(wx|wy|wz)\s*\|\s*([0-9.]+)\s*\|\s*([0-9.]+)\s*\|\s*([0-9.]+)\s*\|")


def parse_report(text):
    """从拟合报告文本提取诊断要素，返回字典。

    键为 saturation、net、halves、holdout_verdict，行缺失的要素为空
    值，不猜。
    """
    out = {"saturation": {}, "net": {}, "halves": {}, "holdout_verdict": None}
    net_seen = False
    for line in text.splitlines():
        s = line.strip()
        m = _SAT_RE.search(s)
        if m:
            out["saturation"] = {
                "ail": float(m.group(1)),
                "elev": float(m.group(2)),
                "rud": float(m.group(3))}
            continue
        m = _NET_RE.match(s)
        if m:
            net_seen = True
            out["net"][m.group(1)] = float(m.group(2))
            continue
        m = _HOLDOUT_RE.match(s)
        if m:
            out["holdout_verdict"] = m.group(1)
            continue
        if not net_seen:
            m = _HALF_ROW_RE.match(s)
            if m:
                out["halves"][m.group(1)] = (float(m.group(2)),
                                             float(m.group(3)))
    return out


def diagnose(parsed):
    """由解析结果生成诊断结论字典，三要素缺料的项如实标空。"""
    net = parsed.get("net") or {}
    worst_ch = None
    if net:
        worst_ch = max(net, key=lambda c: net[c])
    diag = {"worst_channel": worst_ch,
            "worst_net": net.get(worst_ch) if worst_ch else None,
            "worst_actuator": (CHANNEL_ACTUATOR.get(worst_ch)
                               if worst_ch else None),
            "actuator_saturation_pct": None,
            "saturation_over_line": None,
            "half_spread_factor": None,
            "half_dependent": None,
            "advice": []}
    act = diag["worst_actuator"]
    sat = parsed.get("saturation") or {}
    if act and act in sat:
        diag["actuator_saturation_pct"] = sat[act]
        diag["saturation_over_line"] = sat[act] > SAT_WARN_RATIO
    halves = parsed.get("halves") or {}
    if worst_ch and worst_ch in halves:
        this_h, other_h = halves[worst_ch]
        lo = min(this_h, other_h)
        if lo > 0:
            diag["half_spread_factor"] = round(max(this_h, other_h) / lo, 1)
            diag["half_dependent"] = (diag["half_spread_factor"]
                                      > HALF_SPREAD_FACTOR)
    if diag.get("saturation_over_line"):
        diag["advice"].append(
            "降%s注入幅值后重采，现行饱和占比百分之%s超一成污染线"
            % (ACTUATOR_NAME.get(act, act), diag["actuator_saturation_pct"]))
    if diag.get("half_dependent"):
        diag["advice"].append(
            "双半差异悬殊，核对奇偶分半口径与拍锁基线扣除后再采")
    if worst_ch and not diag["advice"]:
        diag["advice"].append(
            "饱和与双半均未见异常，嫌疑转向结构不足，按判据文档预登记"
            "的周期时变升级路径处理")
    return diag


def render_block(name, parsed, diag):
    """渲染一个拟合步骤的诊断块，规范汉语，不用括号。"""
    lines = ["### 拟合质量诊断 %s（管线自动生成）" % name]
    lines.append("- 留出验证：%s" % (parsed.get("holdout_verdict") or "未知"))
    ch = diag.get("worst_channel")
    if not ch:
        lines.append("- 最差通道：净口径行缺失，无法判定")
        return "\n".join(lines)
    lines.append("- 最差通道：%s，净口径比值 %s" % (ch, diag.get("worst_net")))
    halves = (parsed.get("halves") or {}).get(ch)
    if halves:
        lines.append(
            "- 两半差异：本半 %s 对换半 %s，倍差 %s%s"
            % (halves[0], halves[1], diag.get("half_spread_factor"),
               "，超二判半依赖" if diag.get("half_dependent") else ""))
    else:
        lines.append("- 两半差异：双半对照行缺失，无法判定")
    if diag.get("actuator_saturation_pct") is not None:
        lines.append(
            "- 饱和关联：%s 的执行面%s饱和占比百分之%s%s"
            % (ch, ACTUATOR_NAME.get(diag.get("worst_actuator"), ""),
               diag["actuator_saturation_pct"],
               "，超一成污染线，限幅污染为最差通道首要嫌疑"
               if diag.get("saturation_over_line") else "，在一成线内"))
    else:
        lines.append("- 饱和关联：饱和行缺失，无法判定")
    for a in diag.get("advice", []):
        lines.append("- 建议方向：%s" % a)
    return "\n".join(lines)


def main(argv):
    """独立回归入口：对每份报告输出诊断块，留出验证未过才输出。"""
    if not argv:
        print("用法：python -m modules.experiment_pipeline.fit_diag 报告.md")
        return 2
    any_fail = False
    for path in argv:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print("[拒] 报告不可读 %s：%s" % (path, exc))
            any_fail = True
            continue
        parsed = parse_report(text)
        if parsed.get("holdout_verdict") != "不过":
            print("[跳过] %s 留出验证%s，不触发诊断"
                  % (path, parsed.get("holdout_verdict") or "未知"))
            continue
        print(render_block(path, parsed, diagnose(parsed)))
        print()
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
