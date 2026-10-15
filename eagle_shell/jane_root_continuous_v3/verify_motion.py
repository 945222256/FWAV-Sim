import json,hashlib
import numpy as np
from scipy.spatial import cKDTree
from surface_source import ROOT,OUT,C,boundary,project
from gltf_runtime import Model
from glb_surface import render

m=Model(OUT/'eaglex_continuous_root_v3.glb');source=json.loads((OUT/'canonical_source.json').read_text())
parts=dict(np.load(OUT/'canonical_parts.npz'));pairs={};edges={};peak={};sym={}
for side in ['L','R']:
 for label,a,b in [('body_root','eagle_body_and_nose','eagle_wing_main_'+side),
                    ('main_tip','eagle_wing_main_'+side,'eagle_wing_tip_'+side)]:
  va=m.bind[a];vb=m.bind[b];dist,j=cKDTree(vb).query(va);i=np.flatnonzero(dist<1e-7)
  assert len(i)>100,(label,side,len(i));pairs[label+'_'+side]=(a,b,i,j[i])
 for seg in ['main','tip']:
  name='eagle_wing_'+seg+'_'+side;p=m.bind[name];f=m.parts[name]['faces']
  e=np.concatenate([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]);l0=np.linalg.norm(p[e[:,0]]-p[e[:,1]],axis=1)
  good=l0>2e-6;edges[name]=(e[good],l0[good]);peak[name]={'stretch':0.,'shrink':0.,'outside_transition_change':0.}
for seg in ['main','tip']:
 a='eagle_wing_'+seg+'_L';b='eagle_wing_'+seg+'_R';v=m.bind[a].copy();v[:,1]*=-1
 d,j=cKDTree(m.bind[b]).query(v);assert d.max()<1e-7;sym[seg]=(a,b,j)
gap={key:0. for key in pairs};symmax={key:0. for key in sym};min_y=1.
sample_frames=sorted(set([float(i) for i in range(301)]+[i+.5 for i in range(150)]))
zeroarea={};min_area_ratio={name:1. for name in edges};surface_changes={}
for name in edges:
 p=m.bind[name];f=m.parts[name]['faces'];zeroarea[name]=np.linalg.norm(np.cross(p[f[:,1]]-p[f[:,0]],p[f[:,2]]-p[f[:,0]]),axis=1)
for ti in sample_frames:
 pos=m.world(ti)
 for key,(a,b,i,j) in pairs.items():gap[key]=max(gap[key],float(np.linalg.norm(pos[a][i]-pos[b][j],axis=1).max()))
 if ti<=150:
  for key,(a,b,j) in sym.items():
   v=pos[a].copy();v[:,1]*=-1;symmax[key]=max(symmax[key],float(np.linalg.norm(v-pos[b][j],axis=1).max()))
  for name,(e,l0) in edges.items():
   v=pos[name];ratio=np.linalg.norm(v[e[:,0]]-v[e[:,1]],axis=1)/l0-1
   data=peak[name]
   if ratio.max()>data['stretch']:data.update(stretch=float(ratio.max()),stretch_frame=ti)
   if ratio.min()<data['shrink']:data.update(shrink=float(ratio.min()),shrink_frame=ti)
   out=np.min(np.abs(m.bind[name][e][:,:,1]),axis=1)>.240001
   if out.any():data['outside_transition_change']=max(data['outside_transition_change'],float(np.abs(ratio[out]).max()))
   f=m.parts[name]['faces'];area=np.linalg.norm(np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]]),axis=1)
   good=zeroarea[name]>1e-11;min_area_ratio[name]=min(min_area_ratio[name],float((area[good]/zeroarea[name][good]).min()))
   min_y=min(min_y,float(np.abs(v[:,1]).min()))
 if int(ti)==ti and int(ti)%60==0:print('verified frame',ti,flush=True)
assert max(gap.values())<2e-6,gap
assert max(symmax.values())<2e-6,symmax
assert min_y>.020,min_y
assert all(v['outside_transition_change']<1e-4 for v in peak.values())

# Static whole assembly connectivity, actual exported positions.
vv=[];ff=[];off=0
for name,d in m.parts.items():
 vv.append(m.bind[name]);ff.append(d['faces']+off);off+=len(m.bind[name])
_,_,topology=boundary(np.concatenate(vv),np.concatenate(ff));assert topology['boundary_edges']==0,topology
areas={seg:{side:project(m.bind['eagle_wing_'+seg+'_'+side][m.parts['eagle_wing_'+seg+'_'+side]['faces']]).area
            for side in ['L','R']} for seg in ['main','tip']}
for seg,v in areas.items():assert abs(v['L']-v['R'])<1e-9

incidence={}
for ti in [37,52]:
 pos=m.world(ti);error=0.
 for a in ['eagle_wing_main_R','eagle_wing_tip_R']:
  p=m.bind[a];v=pos[a];error=max(error,float(np.abs(v[:,1]-p[:,1]).max()))
 assert error<1e-6
 incidence[str(ti)]={'entire_wing_span_coordinate_change_m':error}

for ti,label in [(None,'静止'),(7,'拍动正向'),(22,'拍动反向'),(37,'迎角正向'),(52,'迎角反向'),(67,'翼尖上反'),(97,'翼尖下折'),(125,'组合动作')]:
 for view in ['overview','root_R','root_L']:
  render(m,ti,'连续外皮候选 | '+label,view).save(OUT/f'{label}_{view}.png')
report={'glb_sha256':hashlib.sha256(m.path.read_bytes()).hexdigest(),'checked_animation_frames':301,
 'additional_between_frame_checks':150,'maximum_seam_gap_m':gap,'mirror_max_position_error_m':symmax,
 'actual_exported_edge_changes':peak,'minimum_triangle_area_ratio':min_area_ratio,
 'whole_static_topology':topology,'projected_areas_m2':areas,'incidence_axis_checks':incidence,
 'root_profile_bounds_m':[min(source['root_profile']['abs_y_m']),max(source['root_profile']['abs_y_m'])],
 'skin_assumption':'Continuous digital outer skin, deformation distributed over main wing and inner tip. Not whole-wing rigid incidence.',
 'independent_human_acceptance':False,'physical_material_qualified':False,'physics_modified':False}
(OUT/'motion_checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=1),flush=True)
