"""Read the exported glTF's sparse morphs, translations, rotations and LBS."""
import numpy as np
from surface_source import Model as LegacyModel,C

class Model(LegacyModel):
 def sample(self,values,ti,quaternion=False):
  lo=int(np.floor(ti));hi=min(lo+1,len(values)-1);a=float(ti)-lo
  if a<1e-12:return values[lo]
  p=np.array(values[lo],dtype=float);q=np.array(values[hi],dtype=float)
  if not quaternion:return (1-a)*p+a*q
  dot=float(p@q)
  if dot<0:q=-q;dot=-dot
  if dot>.9995:
   r=(1-a)*p+a*q;return r/np.linalg.norm(r)
  theta=np.arccos(np.clip(dot,-1,1));return (np.sin((1-a)*theta)*p+np.sin(a*theta)*q)/np.sin(theta)
 def __init__(self,path):
  self.trans={};self.morph={};self.targets={}
  super().__init__(path)
  for ch in self.G['animations'][0]['channels']:
   s=self.G['animations'][0]['samplers'][ch['sampler']];node=ch['target']['node'];p=ch['target']['path']
   if p=='translation':self.trans[node]=self.acc(s['output'])
   if p=='weights':self.morph[node]=self.acc(s['output']).reshape(len(self.times),-1)
  for name,d in self.parts.items():
   prim=self.G['meshes'][self.G['nodes'][d['ni']]['mesh']]['primitives'][0]
   self.targets[name]=[self.acc(t['POSITION']).astype(float) for t in prim.get('targets',[])]
  self.bind=self.world(None)

 def acc(self,i):
  a=self.G['accessors'][i]
  if 'sparse' not in a:return super().acc(i)
  from glb_surface import DT,SIZE
  ans=super().acc(i) if 'bufferView' in a else np.zeros((a['count'],SIZE[a['type']]),dtype=np.dtype(DT[a['componentType']]))
  s=a['sparse'];iv=self.G['bufferViews'][s['indices']['bufferView']];vv=self.G['bufferViews'][s['values']['bufferView']]
  ids=np.frombuffer(self.bin,dtype=DT[s['indices']['componentType']],count=s['count'],offset=iv.get('byteOffset',0)+s['indices'].get('byteOffset',0))
  value=np.frombuffer(self.bin,dtype=DT[a['componentType']],count=s['count']*SIZE[a['type']],offset=vv.get('byteOffset',0)+s['values'].get('byteOffset',0)).reshape(-1,SIZE[a['type']])
  ans[ids]=value;return ans

 def globals(self,ti):
  if ti is None:return super().globals(None)
  out={}
  from glb_surface import qmat
  def at(i):
   if i in out:return out[i]
   n=self.G['nodes'][i];pr,pt=at(self.parents[i]) if i in self.parents else (np.eye(3),np.zeros(3))
   r=qmat(self.sample(self.rot[i],ti,True)) if i in self.rot else qmat(n['rotation']) if 'rotation' in n else np.eye(3)
   t=self.sample(self.trans[i],ti) if i in self.trans else np.array(n.get('translation',[0,0,0]))
   out[i]=(pr@r,pt+pr@t);return out[i]
  for i in range(len(self.G['nodes'])):at(i)
  return out

 def world(self,ti):
  gg=self.globals(ti);out={}
  for name,d in self.parts.items():
   v=d['pos'].copy()
   if ti is not None and d['ni'] in self.morph:
    weights=self.sample(self.morph[d['ni']],ti)
    for j in np.flatnonzero(np.abs(weights)>1e-12):v+=weights[j]*self.targets[name][j]
   if not d['skin']:
    r,t=gg[d['ni']];p=v@r.T+t
   else:
    p=np.zeros_like(v);j,w=d['joints'],d['weights']
    for k in range(4):
     for ji in np.unique(j[w[:,k]>0,k]):
      mask=(j[:,k]==ji)&(w[:,k]>0);r,t=gg[d['skin']['joints'][ji]]
      mat=np.eye(4);mat[:3,:3]=r;mat[:3,3]=t;mat=mat@d['ib'][ji]
      p[mask]+=w[mask,k,None]*(v[mask]@mat[:3,:3].T+mat[:3,3])
   out[name]=p@C
  return out
