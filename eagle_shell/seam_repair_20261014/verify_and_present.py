"""Verify actual recut GLB and produce evidence, without accepting rejected skin trial."""
from pathlib import Path
import json, hashlib, csv
import numpy as np
from scipy.spatial import cKDTree
from PIL import Image, ImageDraw
import imageio.v2 as imageio
from glb_surface import Model, boundary, render, ROOT, FONT

HERE=Path(__file__).resolve().parent;OUT=HERE/'after'
old=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb')
new=Model(OUT/'eaglex_conforming_recut.glb')
assert np.array_equal(old.times,new.times)
assert old.rot.keys()==new.rot.keys()
assert all(np.array_equal(old.rot[i],new.rot[i]) for i in old.rot)

checks={'unchanged_actual_joint_animation':True,'root_pairs':{},'tip_pairs':{}}
for side,sign in [('L',1),('R',-1)]:
 a='eagle_wing_main_'+side;b='eagle_body_and_nose';v=new.bind[a];w=new.bind[b]
 dist,ib=cKDTree(w).query(v);ia=np.flatnonzero((dist<1e-7)&(np.abs(v[:,1]-sign*.06)<1e-7))
 assert len(ia)>100
 checks['root_pairs'][side]={'shared_corner_samples':len(ia),'rest_max_point_difference_m':float(dist[ia].max())}
 a='eagle_wing_main_'+side;b='eagle_wing_tip_'+side;v=new.bind[a];w=new.bind[b]
 dist,ib=cKDTree(w).query(v);ia=np.flatnonzero(dist<1e-7);assert len(ia)>100
 mx=0;wt=0
 for ti in range(len(new.times)):
  p=new.world(ti);gap=float(np.linalg.norm(p[a][ia]-p[b][ib[ia]],axis=1).max())
  if gap>mx:mx=gap;wt=ti
 assert mx<1e-7
 checks['tip_pairs'][side]={'all_301_frames_max_gap_m':mx,'worst_frame':wt}
for name,d in new.parts.items():
 if d['skin']:
  assert d['joints'].min()>=0 and d['joints'].max()<len(d['skin']['joints'])
  assert np.abs(d['weights'].sum(1)-1).max()<1e-6
checks['skin_joint_indices_and_weight_sums_valid']=True
checks['glb_sha256']=hashlib.sha256(new.path.read_bytes()).hexdigest()
checks['recut_file_status']='static surface repair verified; dynamic root connection remains unresolved; not published'
(OUT/'export_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')

# Clear evidence of actual opening at negative incidence, without drawing covers.
can=Image.new('RGB',(1920,1300),'white')
for row,(ti,label) in enumerate([(None,'静止'),(52,'迎角反向')]):
 for col,(mm,version) in enumerate([(old,'修前'),(new,'连续重切候选')]):
  image=render(mm,ti,version+' | '+label,'root_R')
  can.paste(image,(col*960,row*650))
can.save(OUT/'右翼根_真实装配_静止与迎角_修前及重切对照.png')

# One column per side: actual wing joints and explicit current gaps, all requested motions.
old_collage=Image.open(HERE/'before/完整装配_静止拍动迎角折叠_两侧翼根.png')
old_collage.save(OUT/'修前_两侧翼根_四种状态.png')
static=Image.new('RGB',(1920,650),'white')
for col,(mm,label) in enumerate([(old,'原分件：锯齿接口'),(new,'原曲面重切：连续接口')]):
 static.paste(render(mm,None,label,'root_R'),(col*960,0))
static.save(OUT/'静态接缝_修前与连续重切.png')

video=OUT/'实际接缝_修前与重切_四种动作.mp4'
with imageio.get_writer(str(video),fps=7.5,codec='libx264',macro_block_size=2) as writer:
 for ti in range(0,150,2):
  frame=Image.new('RGB',(1920,650),'white')
  frame.paste(render(old,ti,'原分件 | 实际动作','root_R'),(0,0))
  frame.paste(render(new,ti,'连续重切 | 动态翼根仍待修','root_R'),(960,0))
  writer.append_data(np.asarray(frame))
  if ti%30==0:print('video',ti,'/150',flush=True)
print(json.dumps(checks,ensure_ascii=False,indent=1))
