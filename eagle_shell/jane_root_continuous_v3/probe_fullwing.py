import json
import numpy as np
from surface_source import OUT,ROOT,C,Model
from arap import RootARAP
parts=dict(np.load(OUT/'canonical_parts.npz'));meta=json.loads((OUT/'canonical_source.json').read_text())
old=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb')
ni=old.parts['eagle_wing_main_R']['ni'];pivot=old.restG[ni][1]@C
tri=np.concatenate([parts['eagle_wing_main_R'],parts['eagle_wing_tip_R']])
result=[]
for station,power in [(.22,1),(.22,2),(.24,2)]:
 solver=RootARAP(tri,meta['root_station_abs_y_m'],station,False,power)
 for ti in [7,37,52,131]:
  R=C.T@old.globals(ti)[ni][0]@C;q,t=solver.solve(R,pivot,iterations=130)
  row={'station':station,'weight_power':power,'frame':ti,**solver.metrics(q)};result.append(row)
  print(json.dumps(row),flush=True)
  np.savez_compressed(OUT/f'probe_full_{station}_{power}_{ti}.npz',p=solver.p,f=solver.f,q=q)
(OUT/'fullwing_probes.json').write_text(json.dumps(result,indent=2))
