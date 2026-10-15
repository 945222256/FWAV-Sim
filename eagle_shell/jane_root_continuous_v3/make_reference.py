import json,hashlib
import numpy as np
from gltf_runtime import Model
from surface_source import OUT,C
m=Model(OUT/'eaglex_continuous_root_v3.glb');frames=[]
for ti in [0,7,7.5,22,22.5,37,37.5,52,52.5,67,82,97,112,125,125.5,131,149,157,187,217,247,277]:
 p=m.world(ti);frame={'time_s':float(np.interp(ti,np.arange(len(m.times)),m.times)),'parts':{}}
 for name,v in p.items():
  ids=np.unique(np.linspace(0,len(v)-1,96).astype(int));frame['parts'][name]={'indices':ids.tolist(),'xyz_gltf':(v[ids]@C.T).tolist()}
 frames.append(frame)
(OUT/'loader_reference.json').write_text(json.dumps({'frames':frames,'glb_sha256':hashlib.sha256(m.path.read_bytes()).hexdigest()}),encoding='utf-8')
print('reference frames',len(frames),flush=True)
