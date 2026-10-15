"""Prepare a separate offline preview revision; preserve the first delivery."""
from pathlib import Path
import ast

BASE=Path(__file__).resolve().parent
TARGET=BASE.parent/"jane_preview_20261013_tail"
TARGET.mkdir(exist_ok=True)
text=(BASE/"build_preview.py").read_text(encoding="utf-8")
def replace_once(old,new):
    global text
    assert text.count(old)==1,(old,text.count(old))
    text=text.replace(old,new)
replace_once('asset_rotation = np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], dtype=float)',
'''asset_rotation = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=float)
translate = translate * np.array([-1., -1., 1.])''')
replace_once('for source, name in [("head","eagle_head"),("body_tail","eagle_body")]:',
             'for source, name in [("body_tail","eagle_body_and_nose")]:')
replace_once('[("L","wing_right",1),("R","wing_left",-1)]',
             '[("L","wing_left",1),("R","wing_right",-1)]')
replace_once('"source_role":"mirror" if side=="R" else "master"',
             '"source_role":"mirror" if side=="L" else "master"')
start=text.index('# Tail meshes come from URDF visual geometry')
end=text.index('for b in binding:',start)
tail='''# Use only the original fan-shaped tail mesh. No URDF visual boxes are generated.
reference_joint_origins = {n:list(j["origin_xyz_m"]) for n,j in joints.items()}
tail_tris = read_obj(SOURCE_PARTS/"head.obj")
tail_points = tail_tris[:,:,:3].reshape(-1,3)
root_band = tail_points[tail_points[:,0] >= np.quantile(tail_points[:,0], .95)]
tail_pivot = np.array([tail_points[:,0].max(), 0., np.median(root_band[:,2])])
# This pivot is a visual animation hypothesis, explicitly separate from the physical URDF.
joints["tail_yaw"]["origin_xyz_m"] = tail_pivot.tolist()
origins = {"base_link":np.zeros(3)}
for j in joints.values():
    origins[j["child"]] = origins[j["parent"]]+np.array(j["origin_xyz_m"])
meshes["eagle_original_tail"] = tail_tris
binding.append({"part":"eagle_original_tail","link":"tail_base","source":"head.obj",
                "source_name_was_incorrect":True,
                "articulation_scope":"whole original tail yaw only; no left/right control-surface claim"})
checks["tail_original"] = {"surface_area_m2":surface_area(tail_tris),
                           "triangle_count":len(tail_tris),
                           "geometry_source":"existing textured original tail; formerly named head.obj",
                           "synthetic_tail_mesh_count":0,
                           "reference_physical_yaw_origin_m":reference_joint_origins["tail_yaw"],
                           "preview_visual_yaw_origin_m":tail_pivot.tolist(),
                           "pivot_is_verified_physical_hinge":False,
                           "left_right_control_surfaces_segmented":False}

'''
text=text[:start]+tail+text[end:]
replace_once('    for n,v in a.items():assert joints[n]["lower_rad"]',
             '    a["tail_L"] = a["tail_R"] = 0.0\n    for n,v in a.items():assert joints[n]["lower_rad"]')
replace_once('for n,j in joints.items():\n    axis=np.array(j["axis"],dtype=float);axis/=np.linalg.norm(axis)',
             'for n,j in joints.items():\n    if n in ["tail_L", "tail_R"]: continue\n    axis=np.array(j["axis"],dtype=float);axis/=np.linalg.norm(axis)')
text=text.replace('"date":"2026-10-12"','"date":"2026-10-13"')
replace_once('"bindings":binding,"joints":list(joints.values()),"geometry":metrics,"verification":checks,',
'''"bindings":binding,"joints":list(joints.values()),"geometry":metrics,"verification":checks,
        "tail_preview_status":{"original_tail_mesh_used":True,"synthetic_tail_meshes":0,
                               "visible_animated_joints":7,"control_surface_split_complete":False,
                               "whole_tail_motion":"yaw about a visual root pivot inferred from the mesh",
                               "reference_physical_origins_m":reference_joint_origins,
                               "physical_urdf_changed":False,"existing_urdf_origin_binding_complete":False},''')
replace_once('"原模型尾部仍属机身部件，补建舵面依据URDF，不能认定照片尾部已完成真实机构重建"',
'''"原模型尾部已独立并用于整体偏转；尚未按真实铰线划分左右作动面",
                       "尾偏转轴位置按网格尾根推定，仅用于离线动作预览；与现行物理URDF原点不同，不能声明现有物理绑定完成",
                       "部件名称与纵向朝向已按视觉机头修正；真实照片和机构资料仍待核对"''')
replace_once('"tail_motion":"diagnostic command, not a control law"',
             '"tail_motion":"original tail yaw only; visual root pivot assumption; no left/right panel movement"')
replace_once('    draw.text((20,612),"URDF kinematic preview  |  main wing + wing tip  |  no physics simulation",font=font,fill=(40,55,70))',
             '    draw.text((20,612),"Original tail yaw  |  visual pivot assumption  |  no synthetic tail",font=font,fill=(40,55,70))')
ast.parse(text)
(TARGET/"build_preview.py").write_text(text,encoding="utf-8")

verify=(BASE/"verify_delivery.py").read_text(encoding="utf-8")
verify=verify.replace('assert len(anim["channels"])==len(jointset)==9',
                      'assert len(anim["channels"])==7 and len(jointset)==9')
verify=verify.replace('assert seen==jointset',
                      'assert seen==jointset-{ "tail_L", "tail_R" }')
verify=verify.replace('assert len(g["meshes"])==len(report["bindings"])==9',
                      'assert len(g["meshes"])==len(report["bindings"])==6\nassert not any("tailpanel" in m["name"] for m in g["meshes"])\nassert any(m["name"]=="eagle_original_tail" for m in g["meshes"])')
ast.parse(verify)
(TARGET/"verify_delivery.py").write_text(verify,encoding="utf-8")
print(str(TARGET))
