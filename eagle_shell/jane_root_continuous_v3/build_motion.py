"""Build symmetric, near-body continuous digital skin with corrective morphs.

No new surface patches or independent actuation DOFs. Root continuity is exact at
all keyframes AND between them because both shared boundaries have zero morphs
and identical skin influences. This is a visual deformation, not a material model.
"""
from pathlib import Path
import copy,csv,hashlib,json,struct
import numpy as np
from scipy.spatial.transform import Rotation
from surface_source import ROOT,OUT,C,Model,project
from arap import RootARAP
from smooth_centerline import refine as refine_centerline

HERE=Path(__file__).resolve().parent
parts=dict(np.load(OUT/'canonical_parts.npz'));meta=json.loads((OUT/'canonical_source.json').read_text())
old=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb')
commands=list(csv.DictReader((old.path.parent/'joint_commands.csv').open(encoding='utf-8-sig')))
g=copy.deepcopy(old.G);blob=bytearray(old.bin);node={n.get('name'):i for i,n in enumerate(g['nodes'])}
mirror=np.diag([1.,-1.,1.]);mirror_g=C@mirror@C.T
def right_name(name):
 if '_L_' in name:return name.replace('_L_','_R_')
 if name.endswith('_L'):return name[:-2]+'_R'
 return None
# Match left rest pivots/bend stations to the right source and retain the existing
# scalar command channels. Conjugating right rotations is exact for mirrored motion.
for name,ni in list(node.items()):
 if name and right_name(name):
  other=right_name(name)
  if other in node and 'mesh' not in g['nodes'][ni]:
   right=g['nodes'][node[other]]
   g['nodes'][ni]['translation']=(mirror_g@np.array(right.get('translation',[0,0,0]))).tolist()
for ch in g['animations'][0]['channels']:
 if ch['target']['path']!='rotation':continue
 ni=ch['target']['node'];name=g['nodes'][ni].get('name','')
 if right_name(name) and (name.startswith('flapconn') or name.startswith('innerwing') or name.startswith('tipBone')):
  other=node[right_name(name)];rr=Rotation.from_quat(old.rot[other]).as_matrix()
  q=Rotation.from_matrix(np.einsum('ij,njk,kl->nil',mirror_g,rr,mirror_g)).as_quat().astype('<f4')
  s=g['animations'][0]['samplers'][ch['sampler']];a=g['accessors'][s['output']];v=g['bufferViews'][a['bufferView']]
  off=v.get('byteOffset',0)+a.get('byteOffset',0);blob[off:off+q.nbytes]=q.tobytes()
  a['min']=q.min(0).tolist();a['max']=q.max(0).tolist()
  ex=g['nodes'][ni].get('extras',{});rex=g['nodes'][other].get('extras',{})
  for field in ['rotation_axis_gltf','axis_gltf']:
   if field in rex:
    axis=mirror_g@np.array(rex[field]);axis*=(-1 if name.startswith('innerwing') else 1)
    ex[field]=axis.tolist()

def view(data,target=None):
 while len(blob)%4:blob.append(0)
 d={'buffer':0,'byteOffset':len(blob),'byteLength':len(data)}
 if target:d['target']=target
 g['bufferViews'].append(d);blob.extend(data);return len(g['bufferViews'])-1

def accessor(arr,kind,component=5126,target=None):
 arr=np.asarray(arr,dtype={5126:'<f4',5123:'<u2',5125:'<u4'}[component])
 d={'bufferView':view(arr.tobytes(),target),'componentType':component,'count':len(arr),'type':kind}
 if kind=='VEC3':d.update(min=arr.min(0).tolist(),max=arr.max(0).tolist())
 g['accessors'].append(d);return len(g['accessors'])-1

def sparse(arr):
 arr=np.asarray(arr,dtype='<f4');ids=np.flatnonzero(np.max(np.abs(arr),axis=1)>1e-10).astype('<u4')
 if not len(ids):
  # One zero entry keeps empty morph accessors portable.
  ids=np.array([0],dtype='<u4')
 selected=arr[ids]
 amin=selected.min(0);amax=selected.max(0)
 if len(ids)<len(arr):amin=np.minimum(amin,0);amax=np.maximum(amax,0)
 d={'componentType':5126,'count':len(arr),'type':'VEC3','min':amin.tolist(),'max':amax.tolist(),
    'sparse':{'count':len(ids),'indices':{'bufferView':view(ids.tobytes()),'componentType':5125},
              'values':{'bufferView':view(arr[ids].tobytes())}}}
 g['accessors'].append(d);return len(g['accessors'])-1

def globals_rest():
 par={c:i for i,n in enumerate(g['nodes']) for c in n.get('children',[])};cache={}
 def at(i):
  if i in cache:return cache[i]
  t=np.array(g['nodes'][i].get('translation',[0,0,0]));cache[i]=at(par[i])+t if i in par else t;return cache[i]
 for i in range(len(g['nodes'])):at(i)
 return cache
bind=globals_rest();base=node['base_link']

# Smooth shading uses shared geometric vertices across all semantic boundaries.
allflat=np.concatenate([t[:,:,:3].reshape(-1,3) for t in parts.values()])
_,_,normal_ids=np.unique(np.round(allflat,10),axis=0,return_index=True,return_inverse=True)
normal_sum=np.zeros((normal_ids.max()+1,3));off=0
for name,t in parts.items():
 p=t[:,:,:3];ids=normal_ids[off:off+3*len(t)].reshape(-1,3);off+=3*len(t)
 cross=np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0])
 for k in range(3):np.add.at(normal_sum,ids[:,k],cross)
normal_sum/=np.maximum(np.linalg.norm(normal_sum,axis=1)[:,None],1e-15)
off=0
for name,t in parts.items():
 t[:,:,5:8]=normal_sum[normal_ids[off:off+3*len(t)]].reshape(-1,3,3);off+=3*len(t)

fullR=np.concatenate([parts['eagle_wing_main_R'],parts['eagle_wing_tip_R']])
root=meta['root_station_abs_y_m'];end=.24
solver=RootARAP(fullR,root,end,False,2,meta.get('root_profile'));ni=old.parts['eagle_wing_main_R']['ni'];pivot=old.restG[ni][1]@C
phase=np.clip((np.abs(solver.p[:,1])-(solver.anchor+.003))/(end-solver.anchor-.003),0,1);phase=phase*phase*(3-2*phase)
field_key=hashlib.sha256(solver.p.tobytes()+(HERE/'smooth_centerline.py').read_bytes()+np.float64(end).tobytes()).hexdigest()[:12]
frames={};poses=[];cache={};pose_metrics=[]
for ti in range(151):
 R=C.T@old.globals(ti)[ni][0]@C
 if np.abs(R-np.eye(3)).max()<1e-7:frames[ti]=-1;continue
 key=tuple(np.round(R,6).ravel())
 if key in cache:frames[ti]=cache[key];continue
 # A smooth rotation field keeps upper and lower skin ordered. Edge-only
 # minimisation allowed the thin source skin to collapse and self-intersect.
 rv=Rotation.from_matrix(R).as_rotvec()
 field=Rotation.from_rotvec(phase[:,None]*rv).as_matrix()
 q=np.einsum('nij,nj->ni',field,solver.p-pivot)+pivot
 file=OUT/('fieldpose_'+field_key+'_'+hashlib.sha256(np.array(key).tobytes()).hexdigest()[:14]+'.npz')
 if file.exists():
  saved=np.load(file);q=saved['q'];info=json.loads(str(saved['info']))
 else:
  q,info=refine_centerline(solver,q,rv)
  np.savez_compressed(file,q=q,info=json.dumps(info))
 S=(1-phase[:,None,None])*np.eye(3)+phase[:,None,None]*R
 trans=phase[:,None]*(pivot-R@pivot)
 pre=np.linalg.solve(S,(q-trans)[...,None]).squeeze(-1)
 delta=pre-solver.p;delta[solver.root|solver.outer]=0
 metrics=solver.metrics(q)
 # Rest/outer normals are fixed explicitly so both sides of each interface agree.
 n=np.zeros_like(q);cross=np.cross(q[solver.f[:,1]]-q[solver.f[:,0]],q[solver.f[:,2]]-q[solver.f[:,0]])
 for k in range(3):np.add.at(n,solver.f[:,k],cross)
 n/=np.maximum(np.linalg.norm(n,axis=1)[:,None],1e-15)
 unique_norm=fullR[:,:,5:8].reshape(-1,3)
 _,first=np.unique(solver.corner_to_vertex,return_index=True)
 restn=unique_norm[first];restn/=np.maximum(np.linalg.norm(restn,axis=1)[:,None],1e-15)
 n[solver.root]=restn[solver.root];n[solver.outer]=restn[solver.outer]@R.T
 pren=np.linalg.solve(S,n[...,None]).squeeze(-1);pren/=np.maximum(np.linalg.norm(pren,axis=1)[:,None],1e-15)
 dn=pren-restn;dn[solver.root|solver.outer]=0
 frames[ti]=len(poses);cache[key]=len(poses);poses.append((delta,dn));pose_metrics.append({'first_frame':ti,**metrics,**info})
 print('pose',len(poses),'frame',ti,'stretch',round(metrics['max_stretch']*100,2),'%',flush=True)

full_p=fullR[:,:,:3].reshape(-1,3);right_split=3*len(parts['eagle_wing_main_R'])
from scipy.spatial import cKDTree
tree=cKDTree(solver.p)
morphnodes=[]
for name,t in parts.items():
 d=old.parts[name];meshnode=g['nodes'][d['ni']];prim=g['meshes'][meshnode['mesh']]['primitives'][0];flat=t.reshape(-1,8)
 p=flat[:,:3];normal=flat[:,5:8];j=w=None;skin=None
 if name.startswith('eagle_wing'):
  side=name[-1];sgn=1 if side=='L' else -1
  pr=p.copy()
  if side=='L':pr[:,1]*=-1
  distance,lookup=tree.query(pr);assert distance.max()<1e-8
  phase_part=phase[lookup]
  if name.startswith('eagle_wing_main'):
   chain=[node['innerwing_'+side]];zone=np.zeros(len(p),dtype=int);u=np.zeros(len(p))
  else:
   chain=[node['innerwing_'+side]]+[node[f'tipBone_{side}_{k}'] for k in range(1,11)]
   coord=np.maximum(np.abs(p[:,1])-.14,0)*100;zone=np.minimum(coord.astype(int),10);u=np.clip(coord-zone,0,1)
  joints=[base]+chain;j=np.zeros((len(p),4),dtype=np.uint16);w=np.zeros((len(p),4))
  j[:,1]=zone+1;j[:,2]=np.minimum(zone+1,len(chain)-1)+1
  w[:,0]=1-phase_part;w[:,1]=phase_part*(1-u);w[:,2]=phase_part*u
  ib=[]
  for x in joints:
   mat=np.eye(4);mat[:3,3]=-bind[x];ib.append(mat.T.reshape(16))
  skin={'joints':joints,'inverseBindMatrices':accessor(np.array(ib),'MAT4')}
  meshnode['skin']=len(g['skins']);g['skins'].append(skin)
  # Put skinned mesh nodes under base to avoid redundant moving mesh transforms.
  for n in g['nodes']:
   if d['ni'] in n.get('children',[]):n['children'].remove(d['ni'])
  g['nodes'][base].setdefault('children',[]).append(d['ni']);meshnode['translation']=[0,0,0]
  targets=[]
  for delta,dn in poses:
   dd=delta[lookup].copy();nn=dn[lookup].copy()
   if side=='L':dd[:,1]*=-1;nn[:,1]*=-1
   targets.append({'POSITION':sparse(dd@C.T),'NORMAL':sparse(nn@C.T)})
  prim['targets']=targets;g['meshes'][meshnode['mesh']]['weights']=[0.]*len(poses);morphnodes.append(d['ni'])
 elif d['skin']:
  skin=d['skin'];side=1 if name.endswith('_L') else -1
  coord=np.maximum(side*p[:,1]-.012,0)*10/.07;zone=np.minimum(coord.astype(int),10);u=np.clip(coord-zone,0,1)
  j=np.zeros((len(p),4),dtype=np.uint16);j[:,0]=zone;j[:,1]=np.minimum(zone+1,10)
  w=np.zeros((len(p),4));w[:,0]=1-u;w[:,1]=u
  # Existing inverse binds must match symmetrized left rest bones.
  joints=skin['joints'];ib=[]
  for x in joints:
   mat=np.eye(4);mat[:3,3]=-bind[x];ib.append(mat.T.reshape(16))
  skin2={'joints':joints,'inverseBindMatrices':accessor(np.array(ib),'MAT4')};meshnode['skin']=len(g['skins']);g['skins'].append(skin2)
 if skin:pos=p@C.T
 else:pos=p@C.T-bind[d['ni']]
 if skin:
  # Merge repeated capped bend-chain joints before serializing influences.
  for k in range(4):
   for l in range(k+1,4):
    same=j[:,k]==j[:,l];w[same,k]+=w[same,l];w[same,l]=0
  w/=np.maximum(w.sum(1)[:,None],1e-15);j[w<1e-12]=0
  # glTF skinned meshes have world-space bind coordinates and are scene roots.
  for n in g['nodes']:
   if d['ni'] in n.get('children',[]):n['children'].remove(d['ni'])
  if d['ni'] not in g['scenes'][0]['nodes']:g['scenes'][0]['nodes'].append(d['ni'])
 # Trimesh exposes bottom-origin UVs; glTF uses top-origin texture coordinates.
 uv=flat[:,3:5].copy();uv[:,1]=1-uv[:,1]
 attrs={'POSITION':accessor(pos,'VEC3',target=34962),'TEXCOORD_0':accessor(uv,'VEC2',target=34962),'NORMAL':accessor(normal@C.T,'VEC3',target=34962)}
 if skin:attrs.update(JOINTS_0=accessor(j,'VEC4',5123,34962),WEIGHTS_0=accessor(w,'VEC4',target=34962))
 prim['attributes']=attrs;prim.pop('indices',None)

weights=np.zeros((len(old.times),len(poses)),dtype='<f4')
for ti,pi in frames.items():
 if pi>=0:weights[ti,pi]=1
output=accessor(weights.reshape(-1,1),'SCALAR')
inp=g['animations'][0]['samplers'][0]['input']
for ni in morphnodes:
 si=len(g['animations'][0]['samplers']);g['animations'][0]['samplers'].append({'input':inp,'output':output,'interpolation':'LINEAR'})
 g['animations'][0]['channels'].append({'sampler':si,'target':{'node':ni,'path':'weights'}})
for n in g['nodes']:
 if 'children' in n and not n['children']:n.pop('children')
for sam in g['animations'][0]['samplers']:
 inp_acc=g['accessors'][sam['input']];inp_acc['min']=[float(old.times.min())];inp_acc['max']=[float(old.times.max())]
g['asset']['generator']='Jane continuous root v3; symmetric original-surface cut; smooth rotation and centre-line field + corrective morph + skin'
g['asset']['extras']={'digital_skin_assumption':True,'root_y_m':root,'root_transition_end_m':end,'no_new_cap_faces':True,
 'physical_material_validated':False,'rigid_outer_pose_translation_changed':False,
 'note':'Root skin fixed at body; bend and twist spread across main wing and inner tip. Not a rigid whole-wing incidence model.'}
g['buffers'][0]['byteLength']=len(blob)
while len(blob)%4:blob.append(0)
jb=json.dumps(g,separators=(',',':')).encode();jb+=b' '*((-len(jb))%4)
file=OUT/'eaglex_continuous_root_v3.glb'
file.write_bytes(struct.pack('<III',0x46546c67,2,28+len(jb)+len(blob))+struct.pack('<II',len(jb),0x4e4f534a)+jb+struct.pack('<II',len(blob),0x004e4942)+blob)
report={'source':meta,'deformation_method':'Smooth rotation field with a smooth derived centre-line correction; legacy root axis and outer target preserved. Cross-sections move together, without per-vertex edge optimisation.',
 'corrective_poses':len(poses),'pose_metrics':pose_metrics,'frame_pose_indices':frames,
 'glb_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'glb_bytes':file.stat().st_size,
 'scope':'Continuous digital skin; bending spans main wing and inner tip. Physical strain limits and stiffness unknown. Frozen physics unchanged.'}
(OUT/'build_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('EXPORTED',file,file.stat().st_size,flush=True)
