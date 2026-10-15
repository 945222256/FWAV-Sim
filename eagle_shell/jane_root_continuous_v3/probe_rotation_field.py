import json
import numpy as np
from scipy.spatial.transform import Rotation
from surface_source import ROOT,OUT,C,Model
from arap import RootARAP
from intersections import candidates,intersects

parts=dict(np.load(OUT/'canonical_parts.npz'));meta=json.loads((OUT/'canonical_source.json').read_text())
old=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb');ni=old.parts['eagle_wing_main_R']['ni'];pivot=old.restG[ni][1]@C
full=np.concatenate([parts['eagle_wing_main_R'],parts['eagle_wing_tip_R']]);solver=RootARAP(full,meta['root_station_abs_y_m'],.24,False,2,meta['root_profile'])
p=solver.p;f=solver.f;area0=np.linalg.norm(np.cross(p[f[:,1]]-p[f[:,0]],p[f[:,2]]-p[f[:,0]]),axis=1)
body=parts['eagle_body_and_nose'][:,:,:3]
all0=np.concatenate([body,full[:,:,:3]]);_,ids=np.unique(np.round(all0.reshape(-1,3),9),axis=0,return_inverse=True);ids=ids.reshape(-1,3)
moving=np.r_[np.zeros(len(body),bool),np.ones(len(full),bool)]
for end in [.11,.14,.18,.24]:
 phase=np.clip((np.abs(p[:,1])-(solver.anchor+.003))/(end-solver.anchor-.003),0,1);phase=phase**2*(3-2*phase)
 for ti in [7,37,52,125]:
  R=C.T@old.globals(ti)[ni][0]@C;rv=Rotation.from_matrix(R).as_rotvec();rots=Rotation.from_rotvec(phase[:,None]*rv).as_matrix()
  q=np.einsum('nij,nj->ni',rots,p-pivot)+pivot
  area=np.linalg.norm(np.cross(q[f[:,1]]-q[f[:,0]],q[f[:,2]]-q[f[:,0]]),axis=1)
  tri=np.concatenate([body,q[f]])
  pairs=candidates(tri,ids,moving);hit=intersects(tri,pairs)
  row={'end':end,'frame':ti,'rotation_degrees':np.rad2deg(rv).tolist(),**solver.metrics(q),'min_area_ratio':float((area[area0>1e-11]/area0[area0>1e-11]).min()),'intersections':len(hit)}
  print(json.dumps(row),flush=True)
