"""Independent file-level self-check. Offline; no simulator or production edits."""
from pathlib import Path
import hashlib
import json
import struct
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

OUT=Path(__file__).resolve().parent/"output"
report=json.loads((OUT/"geometry_and_binding.json").read_text(encoding="utf-8"))
results={}
for entry in report["provenance"]:
    actual=hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest()
    assert actual==entry["sha256"],entry["path"]
results["source_files_unchanged"]=True
raw=(OUT/"eaglex_articulated_preview.glb").read_bytes()
magic,version,total=struct.unpack_from("<4sII",raw)
assert magic==b"glTF" and version==2 and total==len(raw)
jlen,jtype=struct.unpack_from("<I4s",raw,12)
assert jtype==b"JSON"
g=json.loads(raw[20:20+jlen])
blen,btype=struct.unpack_from("<I4s",raw,20+jlen)
assert btype==b"BIN\0"
blob=raw[28+jlen:28+jlen+blen]
assert len(blob)==blen and g["buffers"][0]["byteLength"]<=blen
types={"SCALAR":1,"VEC2":2,"VEC3":3,"VEC4":4}
def read_accessor(i):
    a=g["accessors"][i];v=g["bufferViews"][a["bufferView"]]
    off=v.get("byteOffset",0)+a.get("byteOffset",0)
    assert a["componentType"]==5126
    count=a["count"]*types[a["type"]]
    assert off+count*4<=v["byteOffset"]+v["byteLength"]
    x=np.frombuffer(blob,dtype="<f4",count=count,offset=off).reshape(a["count"],-1)
    assert np.isfinite(x).all()
    return x
jointset={j["joint"] for j in report["joints"]}
anim=g["animations"][0]
assert len(anim["channels"])==7 and len(jointset)==9
seen=set()
for c in anim["channels"]:
    node=g["nodes"][c["target"]["node"]];seen.add(node["extras"]["urdf_joint"])
    sampler=anim["samplers"][c["sampler"]]
    t=read_accessor(sampler["input"]).ravel();q=read_accessor(sampler["output"])
    assert len(t)==len(q) and (np.diff(t)>0).all()
    assert np.max(np.abs(np.linalg.norm(q,axis=1)-1))<1e-6
assert seen==jointset-{ "tail_L", "tail_R" }
results["animated_joint_channels"]=len(seen)
results["mesh_count"]=len(g["meshes"])
assert len(g["meshes"])==len(report["bindings"])==6
assert not any("tailpanel" in m["name"] for m in g["meshes"])
assert any(m["name"]=="eagle_original_tail" for m in g["meshes"])
results["serialized_geometry"]={}
for b in report["bindings"]:
    mesh=next(m for m in g["meshes"] if m["name"]==b["part"])
    attrs=mesh["primitives"][0]["attributes"]
    p=read_accessor(attrs["POSITION"]);uv=read_accessor(attrs["TEXCOORD_0"])
    n=read_accessor(attrs["NORMAL"])
    assert len(p)==len(uv)==len(n) and len(p)%3==0
    assert np.max(np.abs(np.linalg.norm(n,axis=1)-1))<1e-5
    tri=(p+np.array(b["zero_origin_xyz_m"])).reshape(-1,3,3)
    if b["part"] in report["geometry"]:
        geom=report["geometry"][b["part"]]
        area=float(np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1).sum()/2)
        assert abs(area-geom["surface_area_m2"])<1e-7
        assert len(tri)==geom["triangles"]
        results["serialized_geometry"][b["part"]]={"triangles":len(tri),"surface_area_error_m2":abs(area-geom["surface_area_m2"])}
# Endpoints close to neutral, so looping has no sudden articulated pose change.
for sampler in anim["samplers"]:
    q=read_accessor(sampler["output"])
    assert np.linalg.norm(q[0]-q[-1])<1e-5
results["animation_loop_endpoints_match"]=True
results["result"]="self_check_passed"
results["independent_acceptance"]="未完成；本文件为Jane交付自检"
(OUT/"delivery_checks.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(results,ensure_ascii=True))

# Geometry comparison figure, independent of the approximate textured raster preview.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import csv
fig,axs=plt.subplots(1,2,figsize=(13,5.7),dpi=160)
colors={"main":"#168fa5","tip":"#ed983a"}
for b in report["bindings"]:
    if "wing_" not in b["part"]:continue
    name=b["part"]
    mesh=next(m for m in g["meshes"] if m["name"]==name)
    p=read_accessor(mesh["primitives"][0]["attributes"]["POSITION"])+np.array(b["zero_origin_xyz_m"])
    tris=p.reshape(-1,3,3)
    shape=unary_union([Polygon(t[:,:2]) for t in tris if abs((t[1,0]-t[0,0])*(t[2,1]-t[0,1])-(t[1,1]-t[0,1])*(t[2,0]-t[0,0]))>1e-14])
    for poly in ([shape] if shape.geom_type=="Polygon" else shape.geoms):
        xy=np.array(poly.exterior.coords)
        axs[0].fill(xy[:,1],xy[:,0],color=colors["main" if "main" in name else "tip"],alpha=.8)
    axs[0].scatter(b["zero_origin_xyz_m"][1],b["zero_origin_xyz_m"][0],color="#243349",s=30,zorder=5)
axs[0].set(xlabel="Mechanism y / span position (m)",ylabel="Mechanism x / forward (m)",title="Main wing (blue) + wing tip (orange)\nDots: URDF link origins")
axs[0].axis("equal");axs[0].grid(alpha=.2)
with (OUT/"chord_stations.csv").open(encoding="utf-8-sig",newline="") as f:rows=list(csv.DictReader(f))
for side,style in [("L","-"),("R","--")]:
    rr=[r for r in rows if r["side"]==side]
    axs[1].plot([float(r["span_position_abs_m"]) for r in rr],[float(r["chord_envelope_m"]) for r in rr],style,label=side+" mesh chord envelope")
y=np.linspace(0,.32,100)
axs[1].plot(y,.20+(.14-.20)*y/.32,":",color="gray",label="Existing aero trapezoid")
axs[1].axvline(.14,color="#ed983a",alpha=.7,label="Fold split plane")
axs[1].set(xlabel="Absolute span position (m)",ylabel="Chord (m)",title="Extracted visual geometry vs. existing aero geometry")
axs[1].grid(alpha=.2);axs[1].legend(fontsize=8)
fig.suptitle("Eagle X geometry extraction: generated visual mesh, existing shell scale",fontsize=13)
fig.tight_layout();fig.savefig(OUT/"geometry_overview.png");plt.close(fig)
