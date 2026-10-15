from pathlib import Path
import json, re
import numpy as np
from scipy.spatial import cKDTree
from PIL import Image, ImageDraw
import imageio.v2 as imageio
from glb_surface import Model, render, ROOT, FONT

HERE=Path(__file__).resolve().parent
html=(HERE/'review.html').read_text(encoding='utf-8')
links=re.findall(r'(?:src|href)="([^"]+)"',html)
missing=[p for p in links if not (HERE/p).exists()]
assert not missing,missing
video=HERE/'after/实际接缝_修前与重切_四种动作.mp4'
with imageio.get_reader(str(video)) as rr:
 meta=rr.get_meta_data(); a=rr.get_data(0);b=rr.get_data(20)
 assert a.shape[:2]==(650,1920)
 assert np.mean(np.abs(a.astype(float)-b.astype(float)))>1
 assert abs(meta['duration']-10)<.2

# Draw only correspondence markers on an independently rendered actual model.
model=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb')
name='eagle_wing_main_R';v=model.bind[name];body=model.bind['eagle_body_and_nose']
d,j=cKDTree(body).query(v);ii=np.flatnonzero((d<1e-7)&(np.abs(v[:,1])<.09))
pose=model.world(52);dist=np.linalg.norm(pose[name][ii]-pose['eagle_body_and_nose'][j[ii]],axis=1)
k=ii[int(dist.argmax())];pair=np.stack([pose['eagle_body_and_nose'][j[k]],pose[name][k]])
camera=np.array([.5,-1.3,1.1]);camera/=np.linalg.norm(camera)
right=np.cross([0,0,1],camera);right/=np.linalg.norm(right);up=np.cross(camera,right)
P=np.array([right,up,camera]).T;center=np.array([.055,-.055,.005])
xy=((pair-center)@P)[:,:2]*4000;xy[:,0]+=480;xy[:,1]=350-xy[:,1]
im=render(model,52,'右翼迎角反向：红点原本在零位重合','root_R');draw=ImageDraw.Draw(im)
draw.line([tuple(p) for p in xy],fill=(220,36,41),width=4)
for p in xy:draw.ellipse((p[0]-7,p[1]-7,p[0]+7,p[1]+7),fill=(220,36,41),outline='white',width=2)
draw.rectangle((22,76,415,140),fill='white')
draw.text((33,85),'红线标记连接点分离，不是孔宽',font=FONT,fill=(176,28,34))
draw.text((33,112),'背景从机身与翼之间露出',font=FONT,fill=(35,46,58))
im.save(HERE/'before/右翼迎角反向_实际接缝标记.png')
result={'all_review_assets_exist':True,'video_duration_s':meta['duration'],'video_fps':meta['fps'],
        'actual_frame_change_mean':float(np.mean(np.abs(a.astype(float)-b.astype(float)))),
        'marker_corresponding_point_distance_m':float(dist.max()),'marker_body_xyz_m':pair[0].tolist(),
        'marker_wing_xyz_m':pair[1].tolist(),'marker_scope':'Material-point correspondence, not an air-hole width.'}
(HERE/'after/media_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
