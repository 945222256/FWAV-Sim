import json
import numpy as np
from surface_source import build_source,OUT,C
from arap import RootARAP
parts,meta,old=build_source()
ni=old.parts['eagle_wing_main_R']['ni'];pivot=old.restG[ni][1]@C
results=[]
for station in [.11,.13]:
 for translate in [False,True]:
  solver=RootARAP(parts['eagle_wing_main_R'],meta['root_station_abs_y_m'],station,translate)
  for ti in [7,37,52,131]:
   rg=old.globals(ti)[ni][0];R=C.T@rg@C
   q,t=solver.solve(R,pivot,iterations=55)
   d={'rigid_station_m':station,'allow_translation':translate,'frame':ti,'translation_m':t.tolist(),**solver.metrics(q)}
   results.append(d);print(json.dumps(d),flush=True)
   np.savez_compressed(OUT/f'probe_{station}_{translate}_{ti}.npz',p=solver.p,f=solver.f,q=q,translation=t)
(OUT/'solver_probes.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
