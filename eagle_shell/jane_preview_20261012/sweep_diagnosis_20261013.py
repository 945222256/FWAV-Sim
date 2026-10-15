"""Offline geometry diagnosis. No simulator and no physical parameter writes."""
from pathlib import Path
import csv
import hashlib
import json
import numpy as np

BASE=Path(__file__).resolve().parent
OUT=BASE/"output"
SOURCE=OUT/"chord_stations.csv"
rows=list(csv.DictReader(SOURCE.open(encoding="utf-8-sig",newline="")))
fits=[]
for side in ["L","R"]:
    for low,high,label in [(.05,.28,"central_wing"),(.05,.14,"main"),(.14,.28,"tip")]:
        records=[r for r in rows if r["side"]==side and low<=float(r["span_position_abs_m"])<=high]
        span=np.array([float(r["span_position_abs_m"]) for r in records])
        old_max=np.array([float(r["leading_edge_x_m"]) for r in records])
        old_min=np.array([float(r["trailing_edge_x_m"]) for r in records])
        # Old forward convention placed the fan-shaped tail in +x.
        # Candidate forward follows the visually identified nose: x_new = -x_old.
        le=-old_min;te=-old_max;c=le-te
        lines={"leading_edge":le,"quarter_chord":le-.25*c,"trailing_edge":te}
        for name,x in lines.items():
            slope,intercept=np.polyfit(span,x,1)
            fits.append({"old_side_label":side,"segment":label,"reference_line":name,
                         "fitted_span_range_m":[low,high],"station_count":len(span),
                         "linear_slope_dx_dspan":float(slope),
                         "sweep_aft_positive_deg":float(np.degrees(np.arctan(-slope))),
                         "fit_max_position_residual_m":float(np.max(np.abs(x-(slope*span+intercept))))})
result={"date":"2026-10-13","purpose":"核对零位翼面斜边与后掠定义，纠正旧朝向下前后缘命名的适用范围",
        "source":str(SOURCE),"source_sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "coordinate_assumption":"以视觉机头作为前方，候选纵向坐标x_new=-x_old；正式装配朝向尚未修正。旧左右标识保留用于追溯，不声称已重绑。",
        "definition":"每一参考线分别拟合x=a*abs(y)+b，后掠角=atan(-a)，后掠为正；四分之一弦线位于前缘向后四分之一弦长。",
        "scope":"既有生成视觉网格的零位水平投影；中央拟合区避开翼根缺口及翼尖圆弧。拟合残差不是测量不确定度。",
        "fits":fits,
        "prior_output_status":{"planform_area_and_chord_width":"前后翻转不改变这些数值",
                               "leading_trailing_names":"旧文件的前后缘名称不能沿用到纠正后的方向",
                               "physical_model":"未接入任何几何或后掠修正"},
        "tail_disposition":{"extra_urdf_visuals":"Eagle X整体外观预览拟隐藏；保留原资产与可切换的骨架诊断视图",
                            "original_fan_tail":"重新命名、分件和绑定；原尾形态优先保留",
                            "tail_joint_topology":"沿用已有节点和自由度须先检查几何对位；不据显示动作宣称是真实机构",
                            "mismatch_policy":"记录尾轴位置、舵面数量与运动差异；不靠增添第二套显示尾翼掩盖"}}
(OUT/"sweep_diagnosis_20261013.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps([f for f in fits if f["segment"]=="central_wing"],ensure_ascii=True))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig,ax=plt.subplots(figsize=(8,6),dpi=160)
r=[r for r in rows if r["side"]=="L"]
s=np.array([float(v["span_position_abs_m"]) for v in r])
le=-np.array([float(v["trailing_edge_x_m"]) for v in r])
te=-np.array([float(v["leading_edge_x_m"]) for v in r])
q=le-.25*(le-te)
ax.fill_between(s,te,le,color="#d9e8ef")
ax.plot(s,le,color="#167c98",label="Leading edge after nose-direction correction")
ax.plot(s,q,color="#bb5c88",label="Quarter-chord line")
ax.plot(s,te,color="#788996",label="Trailing edge")
ax.axvline(.14,color="#d99845",linestyle=":",label="Existing fold split station")
ax.axvspan(.05,.28,color="#65a9c0",alpha=.08,label="Linear-fit interval")
ax.set(xlabel="Absolute span station (m)",ylabel="Candidate x, positive toward visual nose (m)",
       title="Eagle X: taper and reference-line sweep\nCandidate corrected orientation; visual mesh only")
ax.grid(alpha=.2);ax.legend(fontsize=9)
ax.text(.03,.96,"Leading-edge fit: +4.84 deg aft\nQuarter-chord fit: -0.26 deg (near zero)",
        transform=ax.transAxes,va="top",fontsize=10,
        bbox={"facecolor":"white","edgecolor":"none","alpha":.9})
fig.tight_layout();fig.savefig(OUT/"sweep_reference_lines_20261013.png");plt.close(fig)
