from pathlib import Path
import csv, json, hashlib
import numpy as np
from scipy.spatial import cKDTree
from PIL import Image, ImageDraw
from glb_surface import Model, boundary, render, ROOT, FONT

OUT=Path(__file__).resolve().parent/'before';OUT.mkdir(exist_ok=True)
path=ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb'
m=Model(path)
cmd=list(csv.DictReader((path.parent/'joint_commands.csv').open(encoding='utf-8-sig')))
specs=[('静止',None),('拍动正向',('FLAP_INHERIT','flap_L',1)),('拍动反向',('FLAP_INHERIT','flap_L',-1)),
       ('迎角正向',('TWIST_INHERIT','twist_L',1)),('迎角反向',('TWIST_INHERIT','twist_L',-1)),
       ('翼尖上反',('TIP_DIHEDRAL_UP','tip_fold_L',1)),('翼尖下折',('TIP_DOWNFOLD','tip_fold_L',-1)),
       ('拍动迎角折叠组合',('FLAP_PLUS_FOLD','twist_L',1))]
frames=[]
for title,spec in specs:
 if spec is None: ti=None
 else:
  mode,key,sgn=spec;idx=[i for i,r in enumerate(cmd) if r['mode']==mode]
  ti=max(idx,key=lambda i:sgn*float(cmd[i][key]))
 frames.append((title,ti))
 for view in ['overview','root_L','root_R']:
  render(m,ti,title+' | '+view,view).save(OUT/f'{len(frames)-1:02d}_{view}.png')
 print('rendered',title,ti,flush=True)

# Contact samples: distance to nearest opposite boundary. This is distinct from the
# old fixed material-point pairing; still not an area or a measured leak width.
root_samples={};body=m.bind['eagle_body_and_nose'];bf=m.parts['eagle_body_and_nose']['faces']
bu,be,_=boundary(body,bf)
for side,sign in [('L',1),('R',-1)]:
 name='eagle_wing_main_'+side;v=m.bind[name];f=m.parts[name]['faces'];u,e,a=boundary(v,f)
 # root contour only, excluding the deliberate main/tip cut at |y|=.14.
 edge=e[np.max(np.abs(u[e][:,:,1]),axis=1)<.09]
 first=cKDTree(v).query(u)[1]
 sel=np.unique(edge.ravel());corner_ids=first[sel]
 bmask=(bu[be].mean(1)[:,1]*sign>0)&(bu[be].mean(1)[:,0]>-.05)&(np.max(np.abs(bu[be][:,:,1]),axis=1)<.09)
 body_boundary=bu[np.unique(be[bmask])]
 measurements=[]
 for title,ti in frames:
  wp=m.world(ti)[name][corner_ids];d=cKDTree(body_boundary).query(wp)[0]
  measurements.append({'pose':title,'frame':ti,'nearest_boundary_sample_distance_max_m':float(d.max()),
                       'nearest_boundary_sample_distance_median_m':float(np.median(d))})
 root_samples[side]={'root_boundary_edges':len(edge),'root_boundary_graph':a,'poses':measurements}

seams={}
for side in ['L','R']:
 a='eagle_wing_main_'+side;b='eagle_wing_tip_'+side
 vv=m.bind[a];ww=m.bind[b];dist,j=cKDTree(ww).query(vv);i=np.flatnonzero(dist<1e-7)
 assert len(i)>50
 vals=[]
 for ti in range(len(m.times)):
  points=m.world(ti);gap=np.linalg.norm(points[a][i]-points[b][j[i]],axis=1)
  vals.append(float(gap.max()))
 seams['main_tip_'+side]={'shared_corner_samples':len(i),'all_301_frames_max_gap_m':max(vals)}

collage=Image.new('RGB',(1920,4*650),(255,255,255))
for row,idx in enumerate([0,1,3,5]):
 for col,view in enumerate(['root_L','root_R']):
  collage.paste(Image.open(OUT/f'{idx:02d}_{view}.png'),(col*960,row*650))
collage.save(OUT/'完整装配_静止拍动迎角折叠_两侧翼根.png')
result={'glb_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'root_contact_samples':root_samples,
        'main_tip':seams,'scope':'Boundary sample distance, not air-hole width. Images show actual assembled triangles with no texture or culling.',
        'frames':[{'pose':n,'index':i,'time_s':float(m.times[i]) if i is not None else 0} for n,i in frames]}
(OUT/'full_assembly_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'main_tip':seams,'roots':{s:v['poses'] for s,v in root_samples.items()}},ensure_ascii=False,indent=1))
