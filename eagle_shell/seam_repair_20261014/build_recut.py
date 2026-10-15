"""Recut original surface with conforming interfaces; no caps or new surface.

Also export a separately labelled LBS continuity trial and measure strain before
accepting it. A continuous trial is not a claim of a physically valid material.
"""
from pathlib import Path
import copy, json, hashlib, struct
import numpy as np
import trimesh
from glb_surface import Model, C, ROOT, boundary, render

HERE=Path(__file__).resolve().parent;OUT=HERE/'after';OUT.mkdir(exist_ok=True)
old=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb')
source=ROOT/'assets/eagleX_raw_50k/cfd068548a51f523e76e53b2c9c2f559.glb'
m=trimesh.load(source,force='mesh',process=False)
mf=json.loads((ROOT/'eagle_shell/out/bind_manifest.json').read_text(encoding='utf-8'))
A=np.array([[0,0,-1],[-1,0,0],[0,1,0]])
v=np.round(np.asarray(m.vertices),6)@A.T*mf['scale']+np.array(mf['translate_global'])*[-1,-1,1]
f=np.asarray(m.faces);uv=np.asarray(m.visual.uv)
raw_face_count=len(f)
source_area=float(np.linalg.norm(np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]]),axis=1).sum()/2)
# Shared geometric normals rather than independently smoothing each semantic part.
_,inv=np.unique(np.round(v,10),axis=0,return_inverse=True)
norm=np.zeros((inv.max()+1,3));fn=np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]])
for k in range(3):np.add.at(norm,inv[f[:,k]],fn)
norm/=np.maximum(np.linalg.norm(norm,axis=1)[:,None],1e-15)
labels=np.load('G:/Playground/PartField/exp_results/clustering/eagleX_knn/cluster_out/eagleX_0_04.npy').reshape(-1)
labels=np.unique(labels,return_inverse=True)[1]
tri=np.concatenate([v[f],uv[f],norm[inv[f]],np.repeat(labels[:,None,None],3,axis=1)],axis=2)

def split_all(t,plane):
 d=t[:,:,1]-plane;cross=(d.min(1)<-1e-12)&(d.max(1)>1e-12)
 out=[t[~cross]];pieces=[]
 for tt in t[cross]:
  for inside in [True,False]:
   poly=[]
   for a,b in zip(tt,np.roll(tt,-1,axis=0)):
    da,db=a[1]-plane,b[1]-plane;ia=da<=0 if inside else da>=0;ib=db<=0 if inside else db>=0
    if ia:poly.append(a)
    if ia!=ib:
     p=a+da/(da-db)*(b-a);p[1]=plane;poly.append(p)
   for j in range(1,len(poly)-1):
    p=np.array([poly[0],poly[j],poly[j+1]])
    if np.linalg.norm(np.cross(p[1,:3]-p[0,:3],p[2,:3]-p[0,:3]))>1e-15:pieces.append(p)
 if pieces:out.append(np.array(pieces))
 return np.concatenate(out)

# Every adjacent original triangle is cut by the same planes, avoiding T-junctions.
for p in [-.14,-.06,-.012,.012,.06,.14]:tri=split_all(tri,p)
cen=tri[:,:,:3].mean(1);lab=np.rint(tri[:,0,8]).astype(int)
tail=lab==0
wl=(~tail)&(cen[:,1]>.06-1e-10)&(cen[:,0]>-.05)
wr=(~tail)&(cen[:,1]<-.06+1e-10)&(cen[:,0]>-.05)
parts={
 'eagle_body_and_nose':tri[~tail&~wl&~wr,:,:8],
 'eagle_wing_main_L':tri[wl&(cen[:,1]<.14),:,:8],
 'eagle_wing_tip_L':tri[wl&(cen[:,1]>.14),:,:8],
 'eagle_wing_main_R':tri[wr&(cen[:,1]>-.14),:,:8],
 'eagle_wing_tip_R':tri[wr&(cen[:,1]<-.14),:,:8],
 'eagle_tail_fin':tri[tail&(np.abs(cen[:,1])<.012),:,:8],
 'eagle_tail_flat_L':tri[tail&(cen[:,1]>.012),:,:8],
 'eagle_tail_flat_R':tri[tail&(cen[:,1]<-.012),:,:8]}

def area(t):return np.linalg.norm(np.cross(t[:,1,:3]-t[:,0,:3],t[:,2,:3]-t[:,0,:3]),axis=1).sum()/2
assert abs(sum(area(t) for t in parts.values())-area(tri))<1e-10

def export(file,trial=False):
 g=copy.deepcopy(old.G);blob=bytearray(old.bin)
 def acc(array,type_,component=5126,target=34962):
  arr=np.asarray(array,dtype='<f4' if component==5126 else '<u2')
  while len(blob)%4:blob.append(0)
  vi=len(g['bufferViews']);bv={'buffer':0,'byteOffset':len(blob),'byteLength':arr.nbytes}
  if target:bv['target']=target
  g['bufferViews'].append(bv)
  blob.extend(arr.tobytes()); ai=len(g['accessors']);d={'bufferView':vi,'componentType':component,'count':len(arr),'type':type_}
  if type_=='VEC3':d.update(min=arr.min(0).tolist(),max=arr.max(0).tolist())
  g['accessors'].append(d);return ai
 nodes={n.get('name'):i for i,n in enumerate(g['nodes'])}
 for name,t in parts.items():
  data=old.parts[name];node=g['nodes'][data['ni']];p=g['meshes'][node['mesh']]['primitives'][0]
  flat=t.reshape(-1,8);body=flat[:,:3];normal=flat[:,5:8];normal/=np.maximum(np.linalg.norm(normal,axis=1)[:,None],1e-15)
  skin=data['skin'];j=w=None
  if name.startswith('eagle_wing_main') and trial:
   side=name[-1];ji=[nodes['base_link'],nodes['innerwing_'+side]]
   skin={'joints':ji}
   ib=[]
   for ni in ji:
    r,bp=old.restG[ni];mat=np.eye(4);mat[:3,:3]=r.T;mat[:3,3]=-r.T@bp;ib.append(mat.T.reshape(16))
   skin['inverseBindMatrices']=acc(np.array(ib),'MAT4',target=None)
   node['skin']=len(g['skins']);g['skins'].append(skin)
   phase=np.clip((np.abs(body[:,1])-.06)/.08,0,1);phase=phase*phase*(3-2*phase)
   j=np.zeros((len(body),4),dtype=np.uint16);j[:,1]=1
   w=np.zeros((len(body),4));w[:,0]=1-phase;w[:,1]=phase
  elif skin:
   side=1 if name.endswith('_L') else -1
   plane,width=(.14,.10) if name.startswith('eagle_wing_tip') else (.012,.07)
   coord=np.maximum(side*body[:,1]-plane,0)*10/width;zone=np.minimum(coord.astype(int),10);alpha=np.clip(coord-zone,0,1)
   j=np.zeros((len(body),4),dtype=np.uint16);j[:,0]=zone;j[:,1]=np.minimum(zone+1,10)
   w=np.zeros((len(body),4));w[:,0]=1-alpha;w[:,1]=alpha
  if skin:pos=body@C.T
  else:
   r,bp=old.restG[data['ni']];pos=(body@C.T-bp)@r
  attrs={'POSITION':acc(pos,'VEC3'),'TEXCOORD_0':acc(flat[:,3:5],'VEC2'),'NORMAL':acc(normal@C.T,'VEC3')}
  if skin:attrs.update(JOINTS_0=acc(j,'VEC4',5123),WEIGHTS_0=acc(w,'VEC4'))
  p['attributes']=attrs;p.pop('indices',None)
 g['asset']['generator']='Jane original-surface conforming recut 2026-10-14; no cap faces'
 g['asset']['extras']={'root_connection_trial':trial,'root_plane_abs_y_m':.06,'new_surface_added':False,
                       'physical_material_verified':False,'tail_rig_unchanged':True}
 g['buffers'][0]['byteLength']=len(blob)
 while len(blob)%4:blob.append(0)
 jb=json.dumps(g,separators=(',',':')).encode();jb+=b' '*((-len(jb))%4)
 out=struct.pack('<III',0x46546c67,2,28+len(jb)+len(blob))+struct.pack('<II',len(jb),0x4e4f534a)+jb+struct.pack('<II',len(blob),0x004e4942)+blob
 file.write_bytes(out)

export(OUT/'eaglex_conforming_recut.glb')
export(OUT/'eaglex_root_lbs_trial.glb',True)
new=Model(OUT/'eaglex_conforming_recut.glb')
vv=np.concatenate(list(new.bind.values()));ff=[];off=0
for name,d in new.parts.items():ff.append(d['faces']+off);off+=len(new.bind[name])
u,e,total=boundary(vv,np.concatenate(ff))
curves={}
for side,sign in [('L',1),('R',-1)]:
 name='eagle_wing_main_'+side;u,e,top=boundary(new.bind[name],new.parts[name]['faces'])
 root=e[np.max(np.abs(u[e][:,:,1]-sign*.06),axis=1)<1e-7]
 rv=u[np.unique(root)];counts={}
 for a,b in root:
  counts[a]=counts.get(a,0)+1;counts[b]=counts.get(b,0)+1
 assert all(c==2 for c in counts.values()),(side,counts)
 from scipy.sparse import coo_matrix
 from scipy.sparse.csgraph import connected_components
 graph=coo_matrix((np.ones(len(root)),(root[:,0],root[:,1])),shape=(len(u),len(u)))
 _,cc=connected_components(graph,directed=False);components=len(np.unique(cc[root.ravel()]))
 assert components==1,(side,components)
 # Root and tip are separate closed boundary cycles; only root lies in this plane.
 curves[side]={'root_boundary_edges':len(root),'single_closed_curve':True,'boundary_components':components,'all_vertex_degrees_two':True}
 assert len(root)>100
 render(new,None,'重新分件：静止 | '+side,'root_'+side).save(OUT/f'静止_重新分件_{side}.png')

trial=Model(OUT/'eaglex_root_lbs_trial.glb');strain={};worstseam={};frames=list(range(0,len(trial.times),3))
for side in ['L','R']:
 name='eagle_wing_main_'+side;v=trial.bind[name];f=trial.parts[name]['faces']
 ed=np.concatenate([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]);l0=np.linalg.norm(v[ed[:,0]]-v[ed[:,1]],axis=1)
 mask=l0>1e-7;stretch=shrink=0;wt=0
 from scipy.spatial import cKDTree
 b=trial.bind['eagle_body_and_nose'];dist,idx=cKDTree(b).query(v);sel=np.flatnonzero(dist<1e-7)
 assert len(sel)>100
 gap=0
 for ti in frames:
  pose=trial.world(ti);p=pose[name];l=np.linalg.norm(p[ed[:,0]]-p[ed[:,1]],axis=1)
  ratio=l[mask]/l0[mask]-1
  if ratio.max()>stretch:stretch=float(ratio.max());wt=ti
  shrink=min(shrink,float(ratio.min()))
  gap=max(gap,float(np.linalg.norm(p[sel]-pose['eagle_body_and_nose'][idx[sel]],axis=1).max()))
 strain[side]={'sampled_max_stretch_fraction':stretch,'sampled_max_shrink_fraction':shrink,'worst_frame':wt}
 worstseam[side]=gap
 render(trial,37,'连续蒙皮试验：迎角正向 | '+side,'root_'+side).save(OUT/f'连续蒙皮试验_迎角_{side}.png')

result={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
 'original_source_triangles':raw_face_count,'conforming_recut_triangles':len(tri),'new_cap_faces':0,'surface_area_before_m2':source_area,
 'surface_area_after_m2':sum(area(t) for t in parts.values()),
 'total_rest_connectivity':total,'root_curves':curves,'trial_seam_gap_m':worstseam,'trial_edge_strain':strain,
 'trial_accepted':all(x['sampled_max_stretch_fraction']<.05 and x['sampled_max_shrink_fraction']>-.05 for x in strain.values()),
 'trial_gate_explanation':'5% is a conservative visual screening gate for this trial, not a measured material limit. Large deformation is rejected, not used to hide gaps.',
 'rigid_recut_scope':'Corrects rest connectivity and jagged partitioning. Existing rigid root animation still separates from fixed body; not a completed dynamic repair.',
 'final_partition_accepted':False,
 'partition_qualification':'The |y|=.06 cut reassigns inboard wing to fixed body. It proves conforming geometry only; it is not a verified skin interface or a qualified moving-wing partition.',
 'physics_modified':False,'online_updated':False}
(OUT/'recut_and_trial_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=1))
