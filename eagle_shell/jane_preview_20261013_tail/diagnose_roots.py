"""Offline geometry/axis audit; no simulator and no production edits."""
from pathlib import Path
import hashlib
import json
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT=Path(__file__).resolve().parent/"output"
SOURCE=OUT/"geometry_and_binding.json"
data=json.loads(SOURCE.read_text(encoding="utf-8"))
def triangles(binding):
    verts=[];faces=[]
    for line in (OUT/binding["part_file"]).read_text(encoding="ascii").splitlines():
        s=line.split()
        if s and s[0]=="v":verts.append(list(map(float,s[1:4])))
        elif s and s[0]=="f":faces.append([int(v.split("/")[0])-1 for v in s[1:4]])
    verts=np.array(verts)+np.array(binding["zero_origin_xyz_m"])
    return verts[np.array(faces)]

fig,ax=plt.subplots(figsize=(10,5.4),dpi=160)
measurements=[]
for side,sign in [("L",1),("R",-1)]:
    tri=np.concatenate([triangles(b) for b in data["bindings"]
                        if b["part"] in ["eagle_wing_main_"+side,"eagle_wing_tip_"+side]])
    y=sign*.06;points=[]
    for k in range(3):
        a,b=tri[:,k],tri[:,(k+1)%3]
        mask=((a[:,1]-y)*(b[:,1]-y)<=0)&(np.abs(a[:,1]-b[:,1])>1e-13)
        aa,bb=a[mask],b[mask]
        t=(y-aa[:,1])/(bb[:,1]-aa[:,1])
        points.append(aa+t[:,None]*(bb-aa))
    p=np.concatenate(points);le,te=float(p[:,0].max()),float(p[:,0].min())
    q=le-.25*(le-te)
    measurements.append({"side":side,"section_y_m":y,"leading_edge_x_m":le,
                         "trailing_edge_x_m":te,"current_twist_axis_x_m":0.,
                         "axis_fraction_from_leading_edge":le/(le-te),
                         "quarter_chord_reference_x_m":q,
                         "quarter_chord_is_confirmed_hinge":False})
    shape=unary_union([Polygon(t[:,:2]) for t in tri if
                       abs((t[1,0]-t[0,0])*(t[2,1]-t[0,1])-(t[1,1]-t[0,1])*(t[2,0]-t[0,0]))>1e-14])
    for poly in ([shape] if shape.geom_type=="Polygon" else shape.geoms):
        xy=np.array(poly.exterior.coords)
        ax.fill(xy[:,1],xy[:,0],color="#cfe3eb",alpha=.9)
    ax.scatter(y,0,c="#be3941",s=60,zorder=5)
    ax.scatter(y,q,c="#276b93",marker="x",s=70,zorder=5)
    ax.plot([y,y],[te,le],color="#384a5a",linestyle=":",linewidth=1)
ax.axhline(0,color="#be3941",linestyle="--",label="Current twist axis (x = 0)")
ax.scatter([],[],c="#276b93",marker="x",label="Quarter-chord reference, not a verified hinge")
ax.set(xlabel="Span position y (m)",ylabel="x toward the visual nose (m)",
       title="Eagle X root-axis mismatch\nCurrent pivot lies near the trailing edge")
ax.grid(alpha=.2);ax.legend(loc="lower right",fontsize=9)
fig.tight_layout();fig.savefig(OUT/"wing_root_axis_diagnosis.png");plt.close(fig)

result={"date":"2026-10-13","source":str(SOURCE),
        "source_sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "wing_root_measurements":measurements,
        "conclusion":"当前扭转轴在该截面约百分之八十八至八十九弦长处，靠近后缘。实际机构连接点和轴向尚未确认。",
        "cause":"纠正网格朝向后沿用旧机构轴位置，未重新核对轴与资产连接结构。",
        "tail_current":{"visible_tail_dofs":1,"whole_tail_yaw":True,
                        "left_right_control_surfaces_split":False,
                        "left_right_joint_angles_in_preview":"固定为零且没有尾面网格绑定",
                        "three_dof_requirement_met":False},
        "tail_candidate_definition":{"status":"专业建议；不是实物机构已确认",
                                     "independent_actuation_angles":["左水平尾面俯仰","右水平尾面俯仰","竖向尾面方向舵偏转"],
                                     "control_combinations":["左右同向作为俯仰候选","左右差动作滚转候选","方向舵作为偏航候选"],
                                     "topology":"若采用独立方向舵，左右水平尾面不能随方向舵节点一并旋转；旧整体偏航父节点结构需要重新核对。"},
        "delivery_status":"现有预览仅保留为诊断材料，不能用作翼根装配正确或尾部三自由度完成的证据。"}
(OUT/"wing_root_tail_audit_20261013.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=True))
