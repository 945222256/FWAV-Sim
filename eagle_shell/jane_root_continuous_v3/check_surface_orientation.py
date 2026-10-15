"""Compare exported face orientation to the transported source tangent plane."""
import json,hashlib
import numpy as np
from scipy.spatial.transform import Rotation
from gltf_runtime import Model
from surface_source import OUT,ROOT,C,Model as LegacyModel

m=Model(OUT/'eaglex_continuous_root_v3.glb');old=LegacyModel(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb')
meta=json.loads((OUT/'canonical_source.json').read_text());ni=old.parts['eagle_wing_main_R']['ni'];pivot=old.restG[ni][1]@C
profile=meta['root_profile'];rows=[]
for ti in [0,7,7.5,22,22.5,37,37.5,52,52.5,67,67.5,82,82.5,97,97.5,112,112.5,125,125.5,127.5,131,149]:
 pos=m.world(ti);G=m.globals(ti)
 rootR=C.T@G[m.parts['eagle_wing_main_R']['ni']][0]@C
 # The geometry node is a scene root; use the root joint's full world rotation.
 names={n.get('name'):i for i,n in enumerate(m.G['nodes'])};rootR=C.T@G[names['innerwing_R']][0]@C
 rv=Rotation.from_matrix(rootR).as_rotvec()
 for seg in ['main','tip']:
  name='eagle_wing_'+seg+'_R';d=m.parts[name];p=m.bind[name];f=d['faces'];v=pos[name]
  c=p[f].mean(1);e1=p[f[:,1]]-p[f[:,0]];e2=p[f[:,2]]-p[f[:,0]]
  # Finite-difference derivative of the intended smooth field; tip folding is
  # subsequently transported with its actual bone rotations.
  def smooth(x):
   anchor=np.interp(x[:,0],profile['x_m'],profile['abs_y_m'])+.003
   u=np.clip((np.abs(x[:,1])-anchor)/(.24-anchor),0,1);u=u*u*(3-2*u)
   r=Rotation.from_rotvec(u[:,None]*rv).as_matrix()
   return np.einsum('nij,nj->ni',r,x-pivot)+pivot
  eps=1e-5
  de1=(smooth(c+eps*e1)-smooth(c-eps*e1))/(2*eps)
  de2=(smooth(c+eps*e2)-smooth(c-eps*e2))/(2*eps)
  ref=np.cross(de1,de2)
  if seg=='tip':
   zone=np.minimum(np.maximum((np.abs(c[:,1])-.14)*100,0).astype(int),10)
   u=np.clip(np.maximum((np.abs(c[:,1])-.14)*100,0)-zone,0,1)
   rots=[rootR]+[C.T@G[names[f'tipBone_R_{k}']][0]@C for k in range(1,11)]
   rot=np.array(rots)[zone]*(1-u[:,None,None])+np.array(rots)[np.minimum(zone+1,10)]*u[:,None,None]
   # Approximate normal transport omits the bend gradient; report disagreement
   # as requiring inspection rather than a proof of inversion.
   ref=np.einsum('nij,nj->ni',rot@rootR.T,ref)
  cross=np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]])
  cosine=np.einsum('ni,ni->n',cross,ref)/np.maximum(np.linalg.norm(cross,axis=1)*np.linalg.norm(ref,axis=1),1e-25)
  good=np.linalg.norm(np.cross(e1,e2),axis=1)>1e-11
  row={'frame':ti,'part':seg,'negative_tangent_alignment_faces':int((cosine[good]<0).sum()),'minimum_alignment':float(cosine[good].min())};rows.append(row)
result={'glb_sha256':hashlib.sha256(m.path.read_bytes()).hexdigest(),'rows':rows,
 'scope':'Exported triangles compared with smooth root tangent and approximate tip-chain normal transport; this complements explicit non-adjacent intersection checks.'}
(OUT/'orientation_checks.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'negative_faces_total':sum(r['negative_tangent_alignment_faces'] for r in rows),'minimum_alignment':min(r['minimum_alignment'] for r in rows)}),flush=True)
