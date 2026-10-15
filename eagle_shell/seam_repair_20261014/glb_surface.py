"""Independent glTF reader, actual LBS deformation and surface diagnostics."""
from pathlib import Path
import json, struct, collections
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from PIL import Image, ImageDraw, ImageFont

C=np.array([[1.,0,0],[0,0,1],[0,-1,0]])
ROOT=Path(__file__).resolve().parents[2]
DT={5120:'i1',5121:'u1',5122:'<i2',5123:'<u2',5125:'<u4',5126:'<f4'}
SIZE={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}

def qmat(q):
 x,y,z,w=np.array(q)/np.linalg.norm(q)
 return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                  [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                  [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])

class Model:
 def __init__(self,path):
  self.path=Path(path); b=self.path.read_bytes(); jl=struct.unpack_from('<I',b,12)[0]
  self.G=json.loads(b[20:20+jl]); self.bin=b[28+jl:]
  self.parents={c:i for i,n in enumerate(self.G['nodes']) for c in n.get('children',[])}
  self.rot={}
  for ch in self.G['animations'][0]['channels']:
   s=self.G['animations'][0]['samplers'][ch['sampler']]
   if ch['target']['path']=='rotation': self.rot[ch['target']['node']]=self.acc(s['output'])
  self.times=self.acc(self.G['animations'][0]['samplers'][0]['input']).ravel()
  self.parts={};self.restG=self.globals(None)
  for ni,n in enumerate(self.G['nodes']):
   if 'mesh' not in n:continue
   mesh=self.G['meshes'][n['mesh']];p=mesh['primitives'][0];a=p['attributes'];pos=self.acc(a['POSITION']).astype(float)
   f=self.acc(p['indices']).astype(int).reshape(-1,3) if 'indices' in p else np.arange(len(pos)).reshape(-1,3)
   skin=self.G['skins'][n['skin']] if 'skin' in n else None
   self.parts[mesh['name']]={'ni':ni,'pos':pos,'faces':f,'skin':skin,
    'joints':self.acc(a['JOINTS_0']).astype(int) if skin else None,
    'weights':self.acc(a['WEIGHTS_0']).astype(float) if skin else None,
    'ib':self.acc(skin['inverseBindMatrices']).reshape(-1,4,4).transpose(0,2,1) if skin else None,
    'uv':self.acc(a['TEXCOORD_0']) if 'TEXCOORD_0' in a else None}
  self.bind=self.world(None)

 def acc(self,i):
  a=self.G['accessors'][i];v=self.G['bufferViews'][a['bufferView']];dt=np.dtype(DT[a['componentType']]);n=SIZE[a['type']]
  off=v.get('byteOffset',0)+a.get('byteOffset',0);stride=v.get('byteStride',n*dt.itemsize)
  return np.ndarray((a['count'],n),dtype=dt,buffer=self.bin,offset=off,strides=(stride,dt.itemsize)).copy()

 def globals(self,ti):
  cache={}
  def at(i):
   if i in cache:return cache[i]
   n=self.G['nodes'][i];pr,pt=at(self.parents[i]) if i in self.parents else (np.eye(3),np.zeros(3))
   r=qmat(self.rot[i][ti]) if ti is not None and i in self.rot else qmat(n['rotation']) if 'rotation' in n else np.eye(3)
   t=np.array(n.get('translation',[0,0,0]));cache[i]=(pr@r,pt+pr@t);return cache[i]
  for i in range(len(self.G['nodes'])):at(i)
  return cache

 def world(self,ti):
  gg=self.globals(ti);out={}
  for name,d in self.parts.items():
   if not d['skin']:
    r,t=gg[d['ni']];p=d['pos']@r.T+t
   else:
    p=np.zeros_like(d['pos']);j,w=d['joints'],d['weights']
    for k in range(4):
     for ji in np.unique(j[w[:,k]>0,k]):
      mask=(j[:,k]==ji)&(w[:,k]>0);r,t=gg[d['skin']['joints'][ji]]
      mat=np.eye(4);mat[:3,:3]=r;mat[:3,3]=t;mat=mat@d['ib'][ji]
      p[mask]+=w[mask,k,None]*(d['pos'][mask]@mat[:3,:3].T+mat[:3,3])
   out[name]=p@C
  return out

def boundary(v,f,tol=1e-7):
 u,inv=np.unique(v,axis=0,return_inverse=True);parent=np.arange(len(u))
 def find(i):
  while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
  return i
 for a,b in cKDTree(u).query_pairs(tol,output_type='ndarray'):
  aa,bb=find(a),find(b)
  if aa!=bb:parent[bb]=aa
 roots=np.array([find(i) for i in range(len(u))]);_,first,remap=np.unique(roots,return_index=True,return_inverse=True)
 u=u[first];wf=remap[inv][f]
 deg=np.any(np.diff(np.sort(wf,axis=1),axis=1)==0,axis=1);wf=wf[~deg]
 e=np.sort(np.concatenate([wf[:,[0,1]],wf[:,[1,2]],wf[:,[2,0]]]),axis=1)
 ue,count=np.unique(e,axis=0,return_counts=True);be=ue[count==1]
 curves=[]
 if len(be):
  gr=coo_matrix((np.ones(len(be)),(be[:,0],be[:,1])),shape=(len(u),len(u)));_,lab=connected_components(gr,directed=False)
  for l in np.unique(lab[be.ravel()]):
   edge=be[lab[be[:,0]]==l];degree=collections.Counter(edge.ravel());p=u[np.unique(edge)]
   curves.append({'edges':len(edge),'closed_cycle':all(n==2 for n in degree.values()),'bounds':[p.min(0).tolist(),p.max(0).tolist()]})
 return u,be,{'boundary_edges':len(be),'nonmanifold_edges':int((count>2).sum()),'weld_degenerate_faces':int(deg.sum()),'curves':curves}

COLORS={'eagle_body_and_nose':(109,132,154),'eagle_wing_main_L':(211,164,80),'eagle_wing_main_R':(211,164,80),
 'eagle_wing_tip_L':(115,174,141),'eagle_wing_tip_R':(115,174,141),'eagle_tail_fin':(173,153,186),
 'eagle_tail_flat_L':(173,153,186),'eagle_tail_flat_R':(173,153,186)}
FONT=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',21)

def render(model,ti,title,view='overview',mark=False):
 points=model.world(ti);right_sign=1 if view.endswith('L') else -1
 if view=='overview':camera=np.array([.7,-1.2,1.3]);center=np.array([0,0,.005]);scale=1300
 elif view=='top':camera=np.array([0.,0.,1.]);center=np.array([0,0,.005]);scale=1300
 else:camera=np.array([.5,right_sign*1.3,1.1]);center=np.array([.055,right_sign*.055,.005]);scale=4000
 camera=camera/np.linalg.norm(camera);right=np.cross([0,0,1],camera)
 if np.linalg.norm(right)<1e-10:right=np.array([0.,1.,0.])
 right/=np.linalg.norm(right);up=np.cross(camera,right);P=np.array([right,up,camera]).T
 triangles=[];colors=[];edges=[]
 for name,d in model.parts.items():
  v=points[name];tri=v[d['faces']];pr=(tri-center)@P
  xy=pr[:,:,:2]*scale;xy[:,:,0]+=480;xy[:,:,1]=350-xy[:,:,1]
  normal=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);normal/=np.maximum(np.linalg.norm(normal,axis=1)[:,None],1e-15)
  light=np.array([.4,-.3,1.]);light/=np.linalg.norm(light);shade=.55+.45*np.abs(normal@light)
  col=shade[:,None]*np.array(COLORS.get(name,(160,170,180)))
  triangles.append((xy,pr[:,:,2].mean(1)));colors.append(col)
  if mark and name.startswith('eagle_wing_main'):
   u,be,_=boundary(model.bind[name],d['faces']);
   # Mark bind boundary positions with the actual part deformation by vertex lookup.
   first=cKDTree(model.bind[name]).query(u)[1]
   sel=be[(np.max(np.abs(u[be][:,:,1]),axis=1)<.09)]
   edges.append(points[name][first][sel])
 xy=np.concatenate([t[0] for t in triangles]);dep=np.concatenate([t[1] for t in triangles]);col=np.concatenate(colors).astype(np.uint8)
 im=Image.new('RGB',(960,650),(242,244,247));draw=ImageDraw.Draw(im)
 for k in np.argsort(dep):draw.polygon([tuple(p) for p in xy[k]],fill=tuple(col[k]))
 for lines in edges:
  pp=(lines-center)@P;ss=pp[:,:,:2]*scale;ss[:,:,0]+=480;ss[:,:,1]=350-ss[:,:,1]
  for line in ss:draw.line([tuple(p) for p in line],fill=(220,30,40),width=2)
 draw.rectangle((0,0,960,54),fill=(24,38,54));draw.text((15,13),title,font=FONT,fill='white')
 draw.text((14,615),'逐个绘制实际三角形；无贴图、无补洞、无轮廓填色',font=FONT,fill=(35,46,58))
 return im
