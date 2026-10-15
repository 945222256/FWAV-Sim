import json
import numpy as np
from scipy.spatial.transform import Rotation
from surface_source import ROOT,OUT,C,Model
from arap import RootARAP
from smooth_centerline import refine
from intersections import candidates,intersects
parts=dict(np.load(OUT/'canonical_parts.npz'));meta=json.loads((OUT/'canonical_source.json').read_text())
old=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb');ni=old.parts['eagle_wing_main_R']['ni'];pivot=old.restG[ni][1]@C
tri=np.concatenate([parts['eagle_wing_main_R'],parts['eagle_wing_tip_R']]);solver=RootARAP(tri,meta['root_station_abs_y_m'],.24,False,2,meta['root_profile'])
p=solver.p;f=solver.f;body=parts['eagle_body_and_nose'][:,:,:3];base=np.concatenate([body,tri[:,:,:3]])
_,ids=np.unique(np.round(base.reshape(-1,3),9),axis=0,return_inverse=True);ids=ids.reshape(-1,3);moving=np.r_[np.zeros(len(body),bool),np.ones(len(tri),bool)]
u=np.clip((np.abs(p[:,1])-solver.anchor-.003)/(.24-solver.anchor-.003),0,1);u=u*u*(3-2*u)
area0=np.linalg.norm(np.cross(p[f[:,1]]-p[f[:,0]],p[f[:,2]]-p[f[:,0]]),axis=1)
for ti in [7,22,37,52,125]:
 R=C.T@old.globals(ti)[ni][0]@C;field=Rotation.from_rotvec(u[:,None]*Rotation.from_matrix(R).as_rotvec()).as_matrix()
 q0=np.einsum('nij,nj->ni',field,p-pivot)+pivot;q,info=refine(solver,q0,Rotation.from_matrix(R).as_rotvec())
 surface=np.concatenate([body,q[f]]);pair=candidates(surface,ids,moving);hits=intersects(surface,pair)
 area=np.linalg.norm(np.cross(q[f[:,1]]-q[f[:,0]],q[f[:,2]]-q[f[:,0]]),axis=1)
 row={'frame':ti,**solver.metrics(q),**info,'intersections':len(hits),'minimum_area_ratio':float((area[area0>1e-11]/area0[area0>1e-11]).min())}
 print(json.dumps(row),flush=True)
 np.savez_compressed(OUT/f'centerline_{ti}.npz',q=q,p=p,f=f)
