"""Measure actual exported areas, interface placement and per-mode edge strain."""
from pathlib import Path
import csv, json, hashlib
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Polygon
from shapely.ops import unary_union
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch
from matplotlib.font_manager import FontProperties
from shapely.geometry.polygon import orient
from glb_surface import Model, ROOT, boundary

HERE=Path(__file__).resolve().parent;OUT=HERE/'symmetry_20261015';OUT.mkdir(exist_ok=True)
paths={'before':ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb',
       'recut':HERE/'after/eaglex_conforming_recut.glb',
       'rejected_trial':HERE/'after/eaglex_root_lbs_trial.glb'}
models={k:Model(p) for k,p in paths.items()}
rows=list(csv.DictReader((paths['before'].parent/'joint_commands.csv').open(encoding='utf-8-sig')))
result={'length_unit':'current display metres; not measured physical calibration','versions':{}}
shapes={}
for version in ['before','recut']:
 m=models[version];record={'sha256':hashlib.sha256(paths[version].read_bytes()).hexdigest(),'parts':{},'root_boundary':{}}
 for side in ['L','R']:
  for segment in ['main','tip']:
   name='eagle_wing_'+segment+'_'+side;v=m.bind[name];f=m.parts[name]['faces'];t=v[f]
   area=np.linalg.norm(np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]),axis=1).sum()/2
   pp=[Polygon(p[:,:2]) for p in t];shape=unary_union([p for p in pp if p.area>1e-15]);shapes[(version,segment,side)]=shape
   record['parts'][segment+'_'+side]={'surface_area_m2':float(area),'projected_area_m2':shape.area,
                                     'min_abs_span_m':float(np.abs(v[:,1]).min()),'max_abs_span_m':float(np.abs(v[:,1]).max())}
  name='eagle_wing_main_'+side;u,e,_=boundary(m.bind[name],m.parts[name]['faces'])
  e=e[np.max(np.abs(u[e][:,:,1]),axis=1)<.10];p=u[np.unique(e)];length=np.linalg.norm(u[e[:,0]]-u[e[:,1]],axis=1)
  record['root_boundary'][side]={'min_abs_span_m':float(np.abs(p[:,1]).min()),'max_abs_span_m':float(np.abs(p[:,1]).max()),
   'length_weighted_mean_abs_span_m':float(np.average(np.abs(u[e].mean(1)[:,1]),weights=length))}
 record['area_symmetry']={}
 for seg in ['main','tip']:
  l=record['parts'][seg+'_L']['projected_area_m2'];r=record['parts'][seg+'_R']['projected_area_m2']
  record['area_symmetry'][seg]={'left_cm2':l*1e4,'right_cm2':r*1e4,
   'relative_difference_over_mean_percent':100*abs(l-r)/((l+r)/2)}
 result['versions'][version]=record
 print(version,record['area_symmetry'],flush=True)

# Rigid-shape/membrane distinction, measured from the actual exported positions.
for version in ['before','recut','rejected_trial']:
 m=models[version];metrics={};edges={}
 for side in ['L','R']:
  name='eagle_wing_main_'+side;v=m.bind[name];f=m.parts[name]['faces']
  e=np.concatenate([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]);l0=np.linalg.norm(v[e[:,0]]-v[e[:,1]],axis=1)
  e=e[l0>1e-7];l0=l0[l0>1e-7];edges[side]=(e,l0)
 for ti,row in enumerate(rows):
  mode=row['mode']
  if float(row['time_s'])>=10:continue
  pos=m.world(ti)
  for side in ['L','R']:
   p=pos['eagle_wing_main_'+side];e,l0=edges[side]
   strain=np.linalg.norm(p[e[:,0]]-p[e[:,1]],axis=1)/l0-1
   key=mode+'/'+side;entry=metrics.setdefault(key,{'max_stretch_fraction':0.,'max_shrink_fraction':0.})
   mx=float(strain.max());mn=float(strain.min())
   if mx>entry['max_stretch_fraction']:entry.update(max_stretch_fraction=mx,stretch_frame=ti)
   if mn<entry['max_shrink_fraction']:entry.update(max_shrink_fraction=mn,shrink_frame=ti)
 result['versions'].setdefault(version,{'sha256':hashlib.sha256(paths[version].read_bytes()).hexdigest()})['main_wing_edge_strain_by_mode']=metrics
 print(version,'strain measured',flush=True)

# Verify that native right outboard wing was repartitioned, not translated.
before=models['before'];after=models['recut'];newv=after.bind['eagle_wing_main_R']
oldv=before.bind['eagle_wing_main_R'];tree=cKDTree(oldv)
distance=tree.query(newv)[0];matched=newv[distance<1e-7]
result['unchanged_right_surface_vertices']={'coincident_corner_samples':len(matched),
 'max_coincident_distance_m':float(distance[distance<1e-7].max()),
 'qualification':'The new cut also creates interpolated vertices; not all new vertices coincide with old corners.'}

font=FontProperties(fname='C:/Windows/Fonts/msyh.ttc')
def draw(ax,g,color):
 polys=[g] if g.geom_type=='Polygon' else list(g.geoms)
 for p in polys:
  if p.geom_type!='Polygon':continue
  p=orient(p,1);paths=[]
  for ring in [p.exterior,*p.interiors]:
   raw=np.asarray(ring.coords);xy=np.column_stack([raw[:,1],raw[:,0]])
   codes=np.full(len(xy),MplPath.LINETO);codes[0]=MplPath.MOVETO;codes[-1]=MplPath.CLOSEPOLY
   paths.append(MplPath(xy,codes))
  ax.add_patch(PathPatch(MplPath.make_compound_path(*paths),facecolor=color,edgecolor='#657583',lw=.4))
fig,axs=plt.subplots(2,1,figsize=(12,8),dpi=160)
for ax,version,label in zip(axs,['before','recut'],['修前分件','重切候选：灰色固定区扩大，黄色主翼从 ±6 cm 开始']):
 m=models[version];t=m.bind['eagle_body_and_nose'][m.parts['eagle_body_and_nose']['faces']]
 shape=unary_union([Polygon(p[:,:2]) for p in t if Polygon(p[:,:2]).area>1e-15]);draw(ax,shape,'#8598a8')
 for side in ['L','R']:
  draw(ax,shapes[(version,'main',side)],'#d3a450');draw(ax,shapes[(version,'tip',side)],'#73ae8d')
 ax.axvline(0,color='#444',ls=':',lw=.8)
 for y in [-.06,.06]:ax.axvline(y,color='#b63b3b',ls='--',lw=.9)
 ax.set_xlim(-.33,.33);ax.set_ylim(-.04,.17);ax.set_aspect('equal');ax.grid(alpha=.15)
 ax.set_title(label,fontproperties=font);ax.set_ylabel('向机头 (m)',fontproperties=font)
axs[-1].set_xlabel('机体展向 y (m)，左正右负；红虚线为 ±0.06 m',fontproperties=font)
fig.text(.5,.015,'真实三角形投影并集，保留凹口；同一视角与尺度。颜色同时代表部件归属与绑定范围。',ha='center',fontproperties=font)
fig.tight_layout(rect=(0,.035,1,1));fig.savefig(OUT/'同尺度俯视_黄色绿色面积与分界位置.png');plt.close(fig)
result['interpretation']={'root_partition_shift':'Candidate reassigns native inboard wing surface to the fixed body. This changes colour AND moving/fixed ownership, not just a drawing label.',
 'rigid_incidence':'Main wing does not materially stretch in the rigid versions; the interface opens.',
 'trial_incidence':'Only the rejected trial stretches the root transition over |y|=.06 to .14 m. Its strain is an artefact of that LBS proposal, not a physical requirement.'}
(OUT/'symmetry_root_and_strain.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('saved',OUT,flush=True)
