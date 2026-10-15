"""Offline asset processing only: no simulator, no physics or production writes.

Clip textured wing triangles at the URDF fold hinge, measure geometry,
and export a URDF-based articulated glTF animation plus a software preview.
"""
from pathlib import Path
import csv
import hashlib
import importlib.util
import json
import math
import struct
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
OUT = HERE / "output"
OUT.mkdir(parents=True, exist_ok=True)
PARTS = OUT / "parts"
PARTS.mkdir(exist_ok=True)
SOURCE_PARTS = ROOT / "eagle_shell/parts"
URDF = ROOT / "assets/flapper/fwav_15dof_from_group.urdf"
OLD_MANIFEST = ROOT / "eagle_shell/out/bind_manifest.json"
TEXTURE = SOURCE_PARTS / "basecolor.png"
mf = json.loads(OLD_MANIFEST.read_text(encoding="utf-8"))
scale = float(mf["scale"])
translate = np.array(mf["translate_global"], dtype=float)
asset_rotation = np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], dtype=float)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_obj(path):
    verts, uvs, faces, uvfaces = [], [], [], []
    for line in Path(path).read_text(encoding="ascii").splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            verts.append(list(map(float, p[1:4])))
        elif p[0] == "vt":
            uvs.append(list(map(float, p[1:3])))
        elif p[0] == "f":
            corners = [s.split("/") for s in p[1:]]
            assert len(corners) == 3
            faces.append([int(s[0])-1 for s in corners])
            uvfaces.append([int(s[1])-1 for s in corners])
    v, f, uv = np.array(verts), np.array(faces), np.array(uvs)
    normal = np.zeros_like(v)
    cross = np.cross(v[f[:, 1]]-v[f[:, 0]], v[f[:, 2]]-v[f[:, 0]])
    for k in range(3):
        np.add.at(normal, f[:, k], cross)
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-15)
    p = (v[f] @ asset_rotation.T) * scale + translate
    n = normal[f] @ asset_rotation.T
    return np.concatenate([p, uv[np.array(uvfaces)], n], axis=2)


def surface_area(tris):
    return float(np.linalg.norm(np.cross(tris[:, 1, :3]-tris[:, 0, :3],
                                         tris[:, 2, :3]-tris[:, 0, :3]), axis=1).sum()/2)


def clip(tris, plane, sign, keep_inner):
    out = []
    for tri in tris:
        poly = list(tri)
        result = []
        for a, b in zip(poly, poly[1:]+poly[:1]):
            da, db = sign*a[1]-plane, sign*b[1]-plane
            ina = da <= 1e-12 if keep_inner else da >= -1e-12
            inb = db <= 1e-12 if keep_inner else db >= -1e-12
            if ina:
                result.append(a)
            if ina != inb:
                t = da/(da-db)
                c = a+t*(b-a)
                c[1] = sign*plane
                result.append(c)
        for i in range(1, len(result)-1):
            t = np.array([result[0], result[i], result[i+1]])
            if np.linalg.norm(np.cross(t[1,:3]-t[0,:3], t[2,:3]-t[0,:3])) > 1e-14:
                out.append(t)
    return np.array(out)


def projection(tris):
    p = tris[:, :, :2]
    area = np.abs((p[:,1,0]-p[:,0,0])*(p[:,2,1]-p[:,0,1])-
                  (p[:,1,1]-p[:,0,1])*(p[:,2,0]-p[:,0,0]))/2
    return unary_union([Polygon(t) for t, a in zip(p, area) if a > 1e-14])


def geometry(name, tris, poly):
    p = tris[:, :, :3].reshape(-1,3)
    cross = np.cross(tris[:,1,:3]-tris[:,0,:3], tris[:,2,:3]-tris[:,0,:3])
    weights = np.linalg.norm(cross,axis=1)/2
    center = (tris[:,:,:3].mean(axis=1)*weights[:,None]).sum(axis=0)/weights.sum()
    return {"part":name, "triangles":len(tris), "bounds_min_m":p.min(axis=0).tolist(),
            "bounds_max_m":p.max(axis=0).tolist(), "dimensions_m":np.ptp(p,axis=0).tolist(),
            "surface_area_m2":float(weights.sum()), "projected_planform_area_m2":float(poly.area),
            "surface_area_centroid_m":center.tolist(), "spanwise_extent_m":float(np.ptp(p[:,1])),
            "x_chord_envelope_m":float(np.ptp(p[:,0])),
            "projected_area_centroid_xy_m":list(poly.centroid.coords)[0],
            "cut_caps_added":False, "mass_kg":None, "inertia_kg_m2":None,
            "airfoil_profile_status":"照片生成网格可记录截面；真实翼型、厚度和材料尚未确认"}


robot = ET.parse(URDF).getroot()
joints = {}
origins = {"base_link":np.zeros(3)}
children = {}
for e in robot.findall("joint"):
    origin = e.find("origin")
    xyz = np.array(list(map(float, origin.get("xyz","0 0 0").split())))
    rpy = np.array(list(map(float, origin.get("rpy","0 0 0").split())))
    assert np.linalg.norm(rpy) == 0, "Nonzero URDF origins require a general transform"
    lim = e.find("limit")
    j = {"joint":e.get("name"), "parent":e.find("parent").get("link"),
         "child":e.find("child").get("link"), "origin_xyz_m":xyz.tolist(),
         "axis":list(map(float,e.find("axis").get("xyz").split())),
         "lower_rad":float(lim.get("lower")),"upper_rad":float(lim.get("upper"))}
    joints[j["joint"]] = j
    children[j["child"]] = j
    origins[j["child"]] = origins[j["parent"]]+xyz

meshes, binding, metrics, polys, checks = {}, [], {}, {}, {}
for source, name in [("head","eagle_head"),("body_tail","eagle_body")]:
    meshes[name] = read_obj(SOURCE_PARTS/(source+".obj"))
    binding.append({"part":name,"link":"base_link","source":source+".obj"})

stations = []
for side, source, sign in [("L","wing_right",1),("R","wing_left",-1)]:
    tri = read_obj(SOURCE_PARTS/(source+".obj"))
    plane = sign*origins["outerwing_"+side][1]
    full_poly = projection(tri)
    metrics["wing_full_"+side] = geometry("wing_full_"+side,tri,full_poly)
    split = []
    for inner, segment, link in [(True,"main","innerwing_"+side),(False,"tip","outerwing_"+side)]:
        name = "eagle_wing_"+segment+"_"+side
        part = clip(tri,plane,sign,inner)
        poly = projection(part)
        meshes[name], polys[name] = part, poly
        metrics[name] = geometry(name,part,poly)
        split.append(part)
        binding.append({"part":name,"link":link,"source":source+".obj",
                        "split_plane_mechanism_y_m":sign*plane,
                        "source_role":"mirror" if side=="R" else "master"})
    before, after = surface_area(tri), sum(surface_area(p) for p in split)
    combined = unary_union([polys["eagle_wing_main_"+side],polys["eagle_wing_tip_"+side]])
    checks["clip_"+side] = {"surface_area_before_m2":before,"surface_area_after_m2":after,
                           "absolute_area_error_m2":abs(after-before),
                           "relative_area_error":abs(after-before)/before,
                           "projected_symmetric_difference_m2":float(full_poly.symmetric_difference(combined).area)}
    assert abs(after-before)/before < 1e-10
    assert full_poly.symmetric_difference(combined).area < 1e-10
    ymax = max(sign*tri[:,:,1].reshape(-1))
    ymin = min(sign*tri[:,:,1].reshape(-1))
    for s in np.linspace(ymin+1e-7,ymax-1e-7,41):
        y = float(sign*s)
        section = full_poly.intersection(LineString([(-2,y),(2,y)]))
        if section.is_empty:
            continue
        bb = section.bounds
        hits = []
        for k in range(3):
            a,b = tri[:,k,:3],tri[:,(k+1)%3,:3]
            mask = ((a[:,1]-y)*(b[:,1]-y)<=0) & (np.abs(b[:,1]-a[:,1])>1e-13)
            t = (y-a[mask,1])/(b[mask,1]-a[mask,1])
            hits.append(a[mask]+t[:,None]*(b[mask]-a[mask]))
        pts = np.concatenate(hits)
        # The fitted section describes this generated visual mesh, not a physical airfoil.
        cov = np.cov(pts[:,[0,2]].T)
        _, vec = np.linalg.eigh(cov)
        v = vec[:,-1]
        if v[0]<0:v=-v
        angle = math.degrees(math.atan2(v[1],v[0]))
        stations.append({"side":side,"span_position_abs_m":float(s),"mechanism_y_m":y,
                         "segment":"main" if s<=plane else "tip",
                         "leading_edge_x_m":float(bb[2]),"trailing_edge_x_m":float(bb[0]),
                         "chord_envelope_m":float(bb[2]-bb[0]),"occupied_chord_m":float(section.length),
                         "min_z_m":float(pts[:,2].min()),"max_z_m":float(pts[:,2].max()),
                         "section_thickness_envelope_m":float(np.ptp(pts[:,2])),
                         "visual_section_principal_angle_deg":angle})

# Tail meshes come from URDF visual geometry, independently identified in the manifest.
tail_uv = np.array([[.641,.516],[.828,.516],[.828,.703],[.641,.703]])
for link, name in [("tail_base","eagle_tail_base"),("tailpanel_L","eagle_tailpanel_L"),("tailpanel_R","eagle_tailpanel_R")]:
    tris=[]
    elem=next(e for e in robot.findall("link") if e.get("name")==link)
    for visual in elem.findall("visual"):
        box=visual.find("geometry/box")
        if box is None:continue
        center=np.array(list(map(float,visual.find("origin").get("xyz","0 0 0").split())))
        size=np.array(list(map(float,box.get("size").split())))
        for axis in range(3):
            for sign in [-1,1]:
                n=np.zeros(3);n[axis]=sign
                a=(axis+1)%3;b=(axis+2)%3
                quad=[]
                for i,(u,v) in enumerate([(0,0),(1,0),(1,1),(0,1)]):
                    p=center+n*size[axis]/2
                    p[a]+=(u-.5)*size[a];p[b]+=(v-.5)*size[b]
                    quad.append(np.r_[p+origins[link],tail_uv[i],n])
                order=[(0,1,2),(0,2,3)] if sign==1 else [(0,2,1),(0,3,2)]
                tris.extend(np.array([quad[i] for i in f]) for f in order)
    meshes[name]=np.array(tris)
    binding.append({"part":name,"link":link,"source":"URDF visual boxes; tail-region texture crop"})

for b in binding:
    b["zero_origin_xyz_m"]=origins[b["link"]].tolist()
    b["part_file"]="parts/"+b["part"]+".obj"
    tri=meshes[b["part"]].copy()
    tri[:,:,:3]-=origins[b["link"]]
    with (PARTS/(b["part"]+".obj")).open("w",encoding="ascii") as f:
        f.write("mtllib basecolor.mtl\nusemtl eagle\n")
        for p in tri[:,:,:3].reshape(-1,3):f.write("v %.9f %.9f %.9f\n"%tuple(p))
        for uv in tri[:,:,3:5].reshape(-1,2):f.write("vt %.9f %.9f\n"%tuple(uv))
        for n in tri[:,:,5:8].reshape(-1,3):f.write("vn %.9f %.9f %.9f\n"%tuple(n/ max(np.linalg.norm(n),1e-15)))
        for i in range(len(tri)):
            idx=[3*i+k+1 for k in range(3)]
            f.write("f "+" ".join(f"{n}/{n}/{n}" for n in idx)+"\n")
    if b["part"] not in metrics:
        global_tri=meshes[b["part"]]
        metrics[b["part"]]=geometry(b["part"],global_tri,projection(global_tri))
(PARTS/"basecolor.png").write_bytes(TEXTURE.read_bytes())
(PARTS/"basecolor.mtl").write_text("newmtl eagle\nKd 1 1 1\nmap_Kd basecolor.png\n",encoding="ascii")

def axis_rotation(axis,angle):
    a=np.array(axis,dtype=float);a/=np.linalg.norm(a)
    x,y,z=a;c=math.cos(angle);s=math.sin(angle);k=1-c
    return np.array([[c+x*x*k,x*y*k-z*s,x*z*k+y*s],
                     [y*x*k+z*s,c+y*y*k,y*z*k-x*s],
                     [z*x*k-y*s,z*y*k+x*s,c+z*z*k]])


def transforms(angles):
    result={"base_link":(np.eye(3),np.zeros(3))}
    for name,j in joints.items():
        rp,tp=result[j["parent"]]
        result[j["child"]]=(rp@axis_rotation(j["axis"],angles.get(name,0)),
                             tp+rp@np.array(j["origin_xyz_m"]))
    return result

# Load only the existing motion generator; no package importing a simulator.
spec=importlib.util.spec_from_file_location("eaglex_existing_kinematics",ROOT/"modules/aero/kinematics.py")
kin=importlib.util.module_from_spec(spec);spec.loader.exec_module(kin)
from config import sim_config as cfg
wk=kin.WingKinematics(frequency=cfg.WING_DRIVE_CONFIG["frequency"],
                      amplitude_deg=cfg.WING_DRIVE_CONFIG["amplitude_deg"],
                      pitch_mode=cfg.WING_DRIVE_CONFIG["pitch_mode"],
                      alpha_set_deg=cfg.WING_DRIVE_CONFIG["alpha_set_deg"])
duration=10.0
times=np.linspace(0,duration,301)
poses=[];sections=[]
for t in times:
    t=float(t);a={k:0.0 for k in joints}
    if t<2:
        mode="FLAP";v=math.radians(30)*math.sin(math.pi*t)
        a.update(flap_L=v,flap_R=-v)
    elif t<4:
        mode="TWIST";v=math.radians(25)*math.sin(math.pi*(t-2))
        a.update(twist_L=v,twist_R=v)
    elif t<6:
        mode="FOLD";v=.5*(1-math.cos(math.pi*(t-4)))*.75
        a.update(fold_L=v,fold_R=-v)
    elif t<8:
        mode="TAIL";v=math.radians(20)*math.sin(math.pi*(t-6))
        a.update(tail_yaw=v,tail_L=v,tail_R=-v)
    else:
        mode="COMBINED";ev=wk.evaluate(t-8);envelope=min(1,(t-8)/.25,(10-t)/.25)
        fold=cfg.WING_DRIVE_CONFIG["fold_amp"]*.5*(1+math.sin(wk.omega*(t-8)+2.2))*envelope
        a.update(flap_L=ev["phi"]*envelope,flap_R=-ev["phi"]*envelope,
                 twist_L=ev["alpha"]*envelope,twist_R=ev["alpha"]*envelope,
                 fold_L=fold,fold_R=-fold,
                 tail_yaw=math.radians(12)*math.sin(math.pi*(t-8))*envelope,
                 tail_L=math.radians(12)*math.sin(math.pi*(t-8))*envelope,
                 tail_R=-math.radians(12)*math.sin(math.pi*(t-8))*envelope)
    for n,v in a.items():assert joints[n]["lower_rad"]-1e-8<=v<=joints[n]["upper_rad"]+1e-8,(n,v)
    poses.append(a);sections.append(mode)
checks["joint_limits_passed"]=True
checks["motion_source"]={"isolated_joint_tests":"explicit diagnostic angles within URDF limits",
                         "combined_wing_motion":"existing WingKinematics and WING_DRIVE_CONFIG; ramped to zero",
                         "tail_motion":"diagnostic command, not a control law",
                         "solver":"offline forward kinematics; no drive tracking or physics validation"}
# Verify that outer vertices follow the fold joint and seam points stay on the same hinge.
zero=transforms({})
foldpose=transforms({"fold_L":.75,"fold_R":-.75})
for side in ["L","R"]:
    name="eagle_wing_tip_"+side;link="outerwing_"+side
    p=meshes[name][:,:,:3].reshape(-1,3)-origins[link]
    r,o=foldpose[link];moved=p@r.T+o
    displacement=np.linalg.norm(moved-(p+origins[link]),axis=1)
    seam=np.isclose(p[:,1],0,atol=1e-10)
    checks["fold_"+side]={"maximum_vertex_displacement_m":float(displacement.max()),
                          "cut_line_max_displacement_m":float(displacement[seam].max()),
                          "note":"Cut plane crosses the hinge axis; seam points away from the axis move during folding."}
    assert displacement.max()>.02

# Write standards-compliant glTF with embedded PNG and node rotations.
g={"asset":{"version":"2.0","generator":"Jane offline URDF geometry preview"},
   "scene":0,"scenes":[{"nodes":[0]}],"nodes":[],"meshes":[],"bufferViews":[],"accessors":[],
   "materials":[{"name":"Eagle basecolor","pbrMetallicRoughness":{"baseColorTexture":{"index":0},"metallicFactor":0,"roughnessFactor":.6},"doubleSided":True}],
   "textures":[{"source":0,"sampler":0}],"samplers":[{"magFilter":9729,"minFilter":9729,"wrapS":10497,"wrapT":10497}],"images":[]}
blob=bytearray()
def view(data,target=None):
    while len(blob)%4:blob.extend(b"\0")
    off=len(blob);blob.extend(data)
    d={"buffer":0,"byteOffset":off,"byteLength":len(data)}
    if target:d["target"]=target
    g["bufferViews"].append(d);return len(g["bufferViews"])-1
def accessor(array,type_,target=None):
    a=np.asarray(array,dtype="<f4")
    d={"bufferView":view(a.tobytes(),target),"componentType":5126,"count":len(a),"type":type_}
    d["min"]=a.min(axis=0).reshape(-1).tolist();d["max"]=a.max(axis=0).reshape(-1).tolist()
    g["accessors"].append(d);return len(g["accessors"])-1
g["images"].append({"bufferView":view(TEXTURE.read_bytes()),"mimeType":"image/png"})
g["nodes"].append({"name":"URDF z-up to glTF y-up","rotation":[-math.sqrt(.5),0,0,math.sqrt(.5)],"children":[1]})
g["nodes"].append({"name":"base_link","children":[]})
node_ids={"base_link":1}
for n,j in joints.items():
    idx=len(g["nodes"]);node_ids[j["child"]]=idx
    g["nodes"].append({"name":j["child"],"translation":j["origin_xyz_m"],"rotation":[0,0,0,1],"children":[],"extras":{"urdf_joint":n,"axis":j["axis"],"limit_rad":[j["lower_rad"],j["upper_rad"]]}})
    g["nodes"][node_ids[j["parent"]]]["children"].append(idx)
for b in binding:
    tri=meshes[b["part"]]
    p=(tri[:,:,:3]-origins[b["link"]]).reshape(-1,3)
    uv=tri[:,:,3:5].reshape(-1,2).copy();uv[:,1]=1-uv[:,1]
    normals=tri[:,:,5:8].reshape(-1,3)
    normals=normals/np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-15)
    attr={"POSITION":accessor(p,"VEC3",34962),"NORMAL":accessor(normals,"VEC3",34962),"TEXCOORD_0":accessor(uv,"VEC2",34962)}
    mi=len(g["meshes"]);g["meshes"].append({"name":b["part"],"primitives":[{"attributes":attr,"material":0,"mode":4}]})
    ni=len(g["nodes"]);g["nodes"].append({"name":b["part"],"mesh":mi})
    g["nodes"][node_ids[b["link"]]]["children"].append(ni)
animation={"name":"flap_twist_fold_tail_and_combined","samplers":[],"channels":[]}
ta=accessor(times,"SCALAR")
for n,j in joints.items():
    axis=np.array(j["axis"],dtype=float);axis/=np.linalg.norm(axis)
    angles=np.array([a[n] for a in poses])
    q=np.c_[np.sin(angles/2)[:,None]*axis,np.cos(angles/2)]
    qa=accessor(q,"VEC4")
    si=len(animation["samplers"]);animation["samplers"].append({"input":ta,"output":qa,"interpolation":"LINEAR"})
    animation["channels"].append({"sampler":si,"target":{"node":node_ids[j["child"]],"path":"rotation"}})
g["animations"]=[animation];g["buffers"]=[{"byteLength":len(blob)}]
js=json.dumps(g,separators=(",",":"),ensure_ascii=True).encode()
while len(js)%4:js+=b" "
while len(blob)%4:blob.extend(b"\0")
with (OUT/"eaglex_articulated_preview.glb").open("wb") as f:
    f.write(struct.pack("<4sII",b"glTF",2,12+8+len(js)+8+len(blob)))
    f.write(struct.pack("<I4s",len(js),b"JSON"));f.write(js)
    f.write(struct.pack("<I4s",len(blob),b"BIN\0"));f.write(blob)

with (OUT/"chord_stations.csv").open("w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f,fieldnames=list(stations[0]));w.writeheader();w.writerows(stations)
with (OUT/"joint_commands.csv").open("w",newline="",encoding="utf-8-sig") as f:
    w=csv.writer(f);w.writerow(["time_s","mode"]+list(joints))
    for t,mode,a in zip(times,sections,poses):w.writerow([t,mode]+[a[n] for n in joints])
sources=[URDF,OLD_MANIFEST,TEXTURE,ROOT/"modules/aero/kinematics.py",ROOT/"config/sim_config.py",
         ROOT/"eagle_shell/prep_parts.py",SOURCE_PARTS/"parts_meta.json",
         ROOT/"assets/eagleX_raw_50k/cfd068548a51f523e76e53b2c9c2f559.glb",
         ROOT/"assets/eagleX_raw_50k/b50134432fcdc17a4a98826ee389d9bc.zip",
         Path("G:/Playground/PartField/exp_results/clustering/eagleX_knn/cluster_out/eagleX_0_04.npy")]+[SOURCE_PARTS/(p+".obj") for p in ["head","body_tail","wing_right","wing_left"]]
report={"schema_version":1,"date":"2026-10-12","asset":"eagleX","purpose":"离线主翼与翼尖分件、几何提取及关节动作预览",
        "coordinate_system":"URDF x前 y左 z上，长度单位米；glTF根节点转换为y向上",
        "provenance":[{"path":str(p),"sha256":digest(p)} for p in sources],
        "source_chain":{"start":"现有eagleX原始模型和贴图包","segmentation":"既有PartField第四级标签与prep_parts分件；本轮沿用既有分件",
                        "source_photo":"未提供可核对的原始照片与生成过程；不能宣称照片保真验收通过"},
        "asset_to_mechanism":{"rotation":asset_rotation.tolist(),"scale":scale,"translation_m":translate.tolist()},
        "definitions":{"projected_planform_area_m2":"零位网格三角形向机构xy平面投影后的并集面积；不重复累计上下表面",
                       "surface_area_m2":"网格三角形表面积之和；不得替代翼面投影面积",
                       "chord_envelope_m":"展向切片中前后缘x坐标最大差；存在孔隙时同时提供实际占用弦长",
                       "visual_section_principal_angle_deg":"生成网格截面点在xz平面的主方向角；不是实测翼型扭角",
                       "spanwise_extent_m":"该部件y坐标范围；同时保留以机身中线为基准的位置",
                       "mass_and_inertia":"无法从视觉网格确认；保留空值，未写入物理配置"},
        "bindings":binding,"joints":list(joints.values()),"geometry":metrics,"verification":checks,
        "preview":{"duration_s":duration,"segments":["0-2s拍动","2-4s扭转","4-6s折叠","6-8s尾部","8-10s组合"],
                   "mode":"运动学角度驱动，固定机身；没有启动Isaac仿真器",
                   "physics_drive_tracking_verified":False,"production_model_updated":False,"web_page_updated":False},
        "limitations":["新切面保留开放边界，不补生成表面；现有生成网格的破损和孔洞保留",
                       "当前尺度沿用旧壳装配值，未确认七百克实物尺寸",
                       "翼根存在原资产与骨架的对位误差，需要独立审查",
                       "折叠切面经过关节轴，远离轴线的切面点旋转后会分离；属于刚性分件，未做柔性连接蒙皮",
                       "原模型尾部仍属机身部件，补建舵面依据URDF，不能认定照片尾部已完成真实机构重建"]}
(OUT/"geometry_and_binding.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")

# Software render for reviewing motion. Full-resolution textured geometry stays in glTF.
tex=np.asarray(Image.open(TEXTURE).convert("RGB"))
camera=np.array([.85,-1.25,1.05]);camera/=np.linalg.norm(camera)
right=np.cross([0,0,1],camera);right/=np.linalg.norm(right)
up=np.cross(camera,right)
project=np.array([right,up,camera]).T
light=np.array([.3,-.5,.9]);light/=np.linalg.norm(light)
prepared=[]
for b in binding:
    tri=meshes[b["part"]];uv=tri[:,:,3:5].mean(axis=1)
    px=np.clip((uv[:,0]%1)*(tex.shape[1]-1),0,tex.shape[1]-1).astype(int)
    py=np.clip((1-uv[:,1]%1)*(tex.shape[0]-1),0,tex.shape[0]-1).astype(int)
    colors=tex[py,px].astype(float)
    prepared.append((b,tri[:,:,:3]-origins[b["link"]],tri[:,:,5:8].mean(axis=1),colors))
frames=[]
try:font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf",22)
except OSError:font=ImageFont.load_default()
for index in range(0,len(times)-1,2):
    tr=transforms(poses[index]);positions=[];cols=[]
    for b,p,n,col in prepared:
        r,o=tr[b["link"]]
        world=p@r.T+o
        cam=world@project
        nn=n@r.T
        shade=.52+.48*np.abs(nn@light)
        positions.append(cam);cols.append(np.clip(col*shade[:,None],0,255).astype(np.uint8))
    v=np.concatenate(positions);col=np.concatenate(cols)
    xy=v[:,:,:2].copy()*870
    xy[:,:,0]+=480;xy[:,:,1]=340-xy[:,:,1]
    # Suppress subpixel triangles only in the raster review, never in delivered meshes.
    a=np.abs((xy[:,1,0]-xy[:,0,0])*(xy[:,2,1]-xy[:,0,1])-(xy[:,1,1]-xy[:,0,1])*(xy[:,2,0]-xy[:,0,0]))
    order=np.argsort(v[:,:,2].mean(axis=1))
    im=Image.new("RGB",(960,650),(240,243,246));draw=ImageDraw.Draw(im)
    for k in order:
        if a[k]>.22:draw.polygon([tuple(pt) for pt in xy[k]],fill=tuple(col[k]))
    # Visible frame labels make the diagnostic action and clock unambiguous.
    draw.rectangle((0,0,960,58),fill=(26,40,57))
    draw.text((20,16),"Eagle X  |  "+sections[index]+f"  |  {times[index]:.2f}s",font=font,fill="white")
    draw.text((20,612),"URDF kinematic preview  |  main wing + wing tip  |  no physics simulation",font=font,fill=(40,55,70))
    frames.append(im)
    if index in [0,60,120,150,180,240,270]:im.save(OUT/f"preview_{index:03d}.png")
frames[0].save(OUT/"eaglex_motion_preview.gif",save_all=True,append_images=frames[1:],duration=67,loop=0,optimize=False)
try:
    import imageio.v2 as imageio
    with imageio.get_writer(str(OUT/"eaglex_motion_preview.mp4"),fps=15,codec="libx264",macro_block_size=2) as writer:
        for f in frames:writer.append_data(np.asarray(f))
except Exception as e:
    print("mp4_export",repr(e))
print(json.dumps({"output":str(OUT),"geometry":{k:{"tris":v["triangles"],"projected_area_m2":v["projected_planform_area_m2"]} for k,v in metrics.items()},"checks":checks},ensure_ascii=True))
