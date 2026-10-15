"""Canonical symmetric source, with matched body and wing interfaces.

Original surfaces are clipped and reflected together. No caps, no hidden patches.
The near-body cut preserves the previous moving-wing projected area.
"""
from pathlib import Path
import json
import sys
import numpy as np
import trimesh
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'eagle_shell/seam_repair_20261014'))
from glb_surface import Model, C, boundary
HERE=Path(__file__).resolve().parent;OUT=HERE/'output';OUT.mkdir(exist_ok=True)

def clip(t,axis,plane,lower=True):
 d=t[:,:,axis]-plane
 keep=(d.max(1)<=1e-12) if lower else (d.min(1)>=-1e-12)
 cross=(d.min(1)<-1e-12)&(d.max(1)>1e-12)
 result=[t[keep]];pieces=[]
 for tt in t[cross]:
  poly=[]
  for a,b in zip(tt,np.roll(tt,-1,axis=0)):
   da,db=a[axis]-plane,b[axis]-plane;ia=da<=0 if lower else da>=0;ib=db<=0 if lower else db>=0
   if ia:poly.append(a)
   if ia!=ib:
    p=a+da/(da-db)*(b-a);p[axis]=plane;poly.append(p)
  for j in range(1,len(poly)-1):
   pp=np.array([poly[0],poly[j],poly[j+1]])
   if np.linalg.norm(np.cross(pp[1,:3]-pp[0,:3],pp[2,:3]-pp[0,:3]))>1e-15:pieces.append(pp)
 if pieces:result.append(np.array(pieces))
 return np.concatenate(result)

def split(t,axis,plane):
 a=clip(t,axis,plane,True);b=clip(t,axis,plane,False)
 # A source face lying exactly in the plane belongs to the lower side only.
 b=b[np.max(np.abs(b[:,:,axis]-plane),axis=1)>1e-12]
 return np.concatenate([a,b])

def project(t):
 polys=[Polygon(p[:,:2]) for p in t[:,:,:3]]
 return unary_union([p for p in polys if p.area>1e-15])

def root_threshold(x,base,height):
 u=np.clip((.035-np.asarray(x))/.06,0,1)
 return base+height*u*u*(3-2*u)

def root_clip(t,base,height,outward):
 def field(p):return -p[...,1]-root_threshold(p[...,0],base,height)
 d=field(t[:,:,:3]);keep=d.min(1)>=-1e-12 if outward else d.max(1)<=1e-12
 cross=(d.min(1)<-1e-12)&(d.max(1)>1e-12);out=[t[keep]];pieces=[]
 for tt in t[cross]:
  poly=[]
  for a,b in zip(tt,np.roll(tt,-1,axis=0)):
   da,db=field(a[:3]),field(b[:3]);ia=da>=0 if outward else da<=0;ib=db>=0 if outward else db<=0
   if ia:poly.append(a)
   if ia!=ib:poly.append(a+da/(da-db)*(b-a))
  for j in range(1,len(poly)-1):
   pp=np.array([poly[0],poly[j],poly[j+1]])
   if np.linalg.norm(np.cross(pp[1,:3]-pp[0,:3],pp[2,:3]-pp[0,:3]))>1e-15:pieces.append(pp)
 if pieces:out.append(np.array(pieces))
 return np.concatenate(out)

def wing_rows(t):
 c=t[:,:,:3].mean(1)
 return t[(t[:,0,8]!=0)&(c[:,0]>-.05)]

def root_cycle_count(t):
 v=t[:,:,:3].reshape(-1,3);f=np.arange(len(v)).reshape(-1,3);u,e,_=boundary(v,f)
 e=e[np.max(np.abs(u[e][:,:,1]),axis=1)<.10]
 gr=coo_matrix((np.ones(len(e)),(e[:,0],e[:,1])),shape=(len(u),len(u)));_,lab=connected_components(gr,directed=False)
 return len(np.unique(lab[e.ravel()]))

def build_source():
 mf=json.loads((ROOT/'eagle_shell/out/bind_manifest.json').read_text(encoding='utf-8'))
 raw=trimesh.load(ROOT/'assets/eagleX_raw_50k/cfd068548a51f523e76e53b2c9c2f559.glb',force='mesh',process=False)
 A=np.array([[0,0,-1],[-1,0,0],[0,1,0]])
 shift=np.array(mf['translate_global'])*[-1,-1,1];shift[1]=0
 v=np.asarray(raw.vertices)@A.T*mf['scale']+shift;f=np.asarray(raw.faces);uv=np.asarray(raw.visual.uv)
 normals=np.asarray(raw.vertex_normals)@A.T
 labels=np.load('G:/Playground/PartField/exp_results/clustering/eagleX_knn/cluster_out/eagleX_0_04.npy').reshape(-1)
 labels=np.unique(labels,return_inverse=True)[1]
 tri=np.concatenate([v[f],uv[f],normals[f],np.repeat(labels[:,None,None],3,axis=1)],axis=2)
 right=clip(tri,1,0,True)
 cen=right[:,:,:3].mean(1)
 fore=right[(right[:,0,8]!=0)&(cen[:,0]>-.05)]
 footprint=project(fore)
 old=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb')
 ot=np.concatenate([old.bind['eagle_wing_main_R'][old.parts['eagle_wing_main_R']['faces']],
                    old.bind['eagle_wing_tip_R'][old.parts['eagle_wing_tip_R']['faces']]])
 target=project(ot).area
 # A flat cut at the area-preserving station crosses a fork in the generated root.
 # Use the smallest curved offset that yields one closed interface, preserving area.
 trials=[]
 for height in [.005,.010,.015,.020]:
  lo,hi=.010,.030
  for _ in range(24):
   base=(lo+hi)/2;outward=root_clip(right,base,height,True)
   wing=wing_rows(outward);area=project(wing).area
   if area>target:lo=base
   else:hi=base
  root=(lo+hi)/2;outward=root_clip(right,root,height,True)
  count=root_cycle_count(wing_rows(outward));trials.append({'height_m':height,'base_m':root,'root_cycles':count})
  if count==1:break
 else:raise RuntimeError('No single connected root contour preserving area: '+str(trials))
 inward=root_clip(right,root,height,False)
 # Carry explicit ownership labels through all subsequent conforming splits.
 outward=np.concatenate([outward,np.ones(outward.shape[:2]+(1,))],axis=2)
 inward=np.concatenate([inward,np.zeros(inward.shape[:2]+(1,))],axis=2)
 right=np.concatenate([outward,inward])
 for p in [-.14,-.012]:right=split(right,1,p)
 mirrored=right[:,::-1].copy();mirrored[:,:,1]*=-1;mirrored[:,:,6]*=-1
 alltri=np.concatenate([right,mirrored]);cen=alltri[:,:,:3].mean(1);tail=alltri[:,0,8]==0
 wl=(~tail)&(alltri[:,0,9]>.5)&(cen[:,1]>0)&(cen[:,0]>-.05)
 wr=(~tail)&(alltri[:,0,9]>.5)&(cen[:,1]<0)&(cen[:,0]>-.05)
 parts={'eagle_body_and_nose':alltri[~tail&~wl&~wr,:,:8],
 'eagle_wing_main_L':alltri[wl&(cen[:,1]<.14),:,:8],
 'eagle_wing_tip_L':alltri[wl&(cen[:,1]>.14),:,:8],
 'eagle_wing_main_R':alltri[wr&(cen[:,1]>-.14),:,:8],
 'eagle_wing_tip_R':alltri[wr&(cen[:,1]<-.14),:,:8],
 'eagle_tail_fin':alltri[tail&(np.abs(cen[:,1])<.012),:,:8],
 'eagle_tail_flat_L':alltri[tail&(cen[:,1]>.012),:,:8],
 'eagle_tail_flat_R':alltri[tail&(cen[:,1]<-.012),:,:8]}
 xs=np.linspace(-.05,.16,85);profile={'x_m':xs.tolist(),'abs_y_m':root_threshold(xs,root,height).tolist(),'base_m':root,'height_m':height}
 meta={'root_station_abs_y_m':root,'root_profile':profile,'root_profile_trials':trials,'old_moving_wing_planform_m2':target,
       'new_moving_wing_planform_m2':project(parts['eagle_wing_main_R']).union(project(parts['eagle_wing_tip_R'])).area,
       'source_body_symmetry_recentering_m':-float((np.array(mf['translate_global'])*[-1,-1,1])[1]),
       'symmetry_method':'negative-y original surface, including matching body and tail surface, reflected about body y=0',
       'root_station_status':'digital cut chosen near the body to preserve original moving area; not a measured physical hinge',
       'new_cap_faces':0}
 np.savez_compressed(OUT/'canonical_parts.npz',**parts)
 (OUT/'canonical_source.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(meta,ensure_ascii=False),flush=True)
 return parts,meta,old

if __name__=='__main__':build_source()
