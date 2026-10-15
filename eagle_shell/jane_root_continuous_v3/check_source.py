import json
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from surface_source import OUT,boundary,project
parts=dict(np.load(OUT/'canonical_parts.npz'));meta=json.loads((OUT/'canonical_source.json').read_text())
rows={};vv=[];ff=[];off=0
for name,t in parts.items():
 v=t[:,:,:3].reshape(-1,3);f=np.arange(len(v)).reshape(-1,3);vv.append(v);ff.append(f+off);off+=len(v)
 if name.startswith('eagle_wing_main'):
  u,e,_=boundary(v,f);root=e[np.max(np.abs(u[e][:,:,1]),axis=1)<.10]
  graph=coo_matrix((np.ones(len(root)),(root[:,0],root[:,1])),shape=(len(u),len(u)));_,cc=connected_components(graph,directed=False)
  count=len(np.unique(cc[root.ravel()]));degree=np.bincount(root.ravel(),minlength=len(u));used=np.unique(root)
  rows[name]={'root_components':count,'all_degrees_two':bool(np.all(degree[used]==2)),
   'root_bounds':[u[used].min(0).tolist(),u[used].max(0).tolist()]}
  assert count==1 and np.all(degree[used]==2),rows[name]
_,_,total=boundary(np.concatenate(vv),np.concatenate(ff));assert total['boundary_edges']==0,total
areas={}
for seg in ['main','tip']:
 l=project(parts['eagle_wing_'+seg+'_L']).area;r=project(parts['eagle_wing_'+seg+'_R']).area
 areas[seg]={'L_m2':l,'R_m2':r,'difference_m2':abs(l-r)};assert abs(l-r)<1e-12
result={'root_curves':rows,'whole_rest_surface':total,'areas':areas}
(OUT/'source_checks.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
