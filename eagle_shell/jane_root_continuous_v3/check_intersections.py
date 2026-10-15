import json,hashlib
import numpy as np
from gltf_runtime import Model
from surface_source import OUT
from intersections import candidates,intersects

m=Model(OUT/'eaglex_continuous_root_v3.glb');parts=dict(np.load(OUT/'canonical_parts.npz'))
names=['eagle_body_and_nose','eagle_wing_main_R','eagle_wing_tip_R']
flat=np.concatenate([parts[n][:,:,:3].reshape(-1,3) for n in names]);_,ids=np.unique(np.round(flat,9),axis=0,return_inverse=True)
ids=ids.reshape(-1,3);moving=np.concatenate([np.full(len(parts[n]),n!='eagle_body_and_nose') for n in names])
baseline=set();rows=[]
for ti in [None,7,7.5,22,22.5,37,37.5,52,52.5,67,67.5,82,82.5,97,97.5,112,112.5,125,125.5,127.5,131,149]:
 pose=m.world(ti);tri=np.concatenate([pose[n][m.parts[n]['faces']] for n in names])
 pair=candidates(tri,ids,moving);hits=intersects(tri,pair);hitset={tuple(map(int,p)) for p in hits}
 if ti is None:baseline=hitset
 added=hitset-baseline
 row={'frame':ti,'candidate_pairs':len(pair),'intersections':len(hitset),'new_intersections':len(added),'new_pairs':list(added)[:30]};rows.append(row)
 print(json.dumps(row),flush=True)
result={'glb_sha256':hashlib.sha256(m.path.read_bytes()).hexdigest(),'rest_pairs':list(baseline),'poses':rows,'new_nonadjacent_intersections_found':any(r['new_intersections'] for r in rows),
 'scope':'Exact segment/triangle and coplanar overlap tests on body plus right wing; left wing is reflected and stays separated by the body plane. Adjacent triangle folds require separate orientation checks.'}
(OUT/'intersection_checks.json').write_text(json.dumps(result,indent=2))
