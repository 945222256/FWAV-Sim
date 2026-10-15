from pathlib import Path
import json, collections
import numpy as np
import trimesh
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

ROOT=Path(__file__).resolve().parents[2]
mf=json.loads((ROOT/'eagle_shell/out/bind_manifest.json').read_text(encoding='utf-8'))
raw=trimesh.load(ROOT/'assets/eagleX_raw_50k/cfd068548a51f523e76e53b2c9c2f559.glb',force='mesh',process=False)
A=np.array([[0,0,-1],[-1,0,0],[0,1,0]])
v=np.asarray(raw.vertices)@A.T*mf['scale']+np.array(mf['translate_global'])*[-1,-1,1]
m=trimesh.Trimesh(vertices=v,faces=raw.faces,process=False)
results=[]
for side in [-1,1]:
 for station in [.03,.04,.05,.06]:
  seg=trimesh.intersections.mesh_plane(m,[0,1,0],[0,side*station,0])
  points=seg.reshape(-1,3)
  # Quantisation is adequate for topology of plane intersections at this scale.
  u, inv=np.unique(np.round(points,7),axis=0,return_inverse=True)
  e=inv.reshape(-1,2);gr=coo_matrix((np.ones(len(e)),(e[:,0],e[:,1])),shape=(len(u),len(u)))
  _, lab=connected_components(gr,directed=False)
  rows=[]
  for l in np.unique(lab):
   ee=e[lab[e[:,0]]==l];p=u[lab==l];deg=collections.Counter(ee.ravel())
   rows.append({'edges':len(ee),'closed_single_cycle':all(n==2 for n in deg.values()),'bbox':[p.min(0).tolist(),p.max(0).tolist()]})
  results.append({'side':side,'station_m':station,'curves':rows})
print(json.dumps(results,indent=2))
