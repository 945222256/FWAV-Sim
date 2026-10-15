import json
import numpy as np
from surface_source import OUT,ROOT,C,Model
from arap import RootARAP
parts=dict(np.load(OUT/'canonical_parts.npz'));meta=json.loads((OUT/'canonical_source.json').read_text())
old=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb');ni=old.parts['eagle_wing_main_R']['ni'];pivot=old.restG[ni][1]@C
tri=np.concatenate([parts['eagle_wing_main_R'],parts['eagle_wing_tip_R']]);solver=RootARAP(tri,meta['root_station_abs_y_m'],.24,False,2)
rows=[]
for ti in [37,52,7,131]:
 R=C.T@old.globals(ti)[ni][0]@C
 q,_=solver.solve(R,pivot,130);before=solver.metrics(q)
 q=solver.refine_lengths(q,R,pivot,300);after=solver.metrics(q)
 area0=np.linalg.norm(np.cross(solver.p[solver.f[:,1]]-solver.p[solver.f[:,0]],solver.p[solver.f[:,2]]-solver.p[solver.f[:,0]]),axis=1)
 area1=np.linalg.norm(np.cross(q[solver.f[:,1]]-q[solver.f[:,0]],q[solver.f[:,2]]-q[solver.f[:,0]]),axis=1)
 row={'frame':ti,'before':before,'after':after,'minimum_face_area_ratio':float((area1/area0).min())};rows.append(row)
 print(json.dumps(row),flush=True)
 np.savez_compressed(OUT/f'probe_length_{ti}.npz',p=solver.p,f=solver.f,q=q)
(OUT/'length_refinement_probes.json').write_text(json.dumps(rows,indent=2))
