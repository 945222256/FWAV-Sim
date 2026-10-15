import json
import numpy as np
import imageio.v2 as imageio
from PIL import Image,ImageDraw,ImageFont
from gltf_runtime import Model
from surface_source import OUT,ROOT
from glb_surface import render,Model as LegacyModel

m=Model(OUT/'eaglex_continuous_root_v3.glb')
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',24)
states=[(None,'静止装配'),(7,'正向拍动'),(22,'反向拍动'),(37,'正迎角'),(52,'负迎角'),(67,'翼尖上反'),(97,'翼尖下折'),(125,'组合动作')]
sheet=Image.new('RGB',(1920,1300),'white')
for i,(ti,label) in enumerate(states):
 im=render(m,ti,label,'overview').resize((480,325),Image.Resampling.LANCZOS)
 sheet.paste(im,((i%4)*480,(i//4)*650))
 im=render(m,ti,label+' · 右翼根','root_R').resize((480,325),Image.Resampling.LANCZOS)
 sheet.paste(im,((i%4)*480,(i//4)*650+325))
sheet.save(OUT/'八种姿态与真实接缝.png')
old=LegacyModel(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb')
beforeafter=Image.new('RGB',(1920,1300),'white')
for j,(ti,label) in enumerate([(37,'正迎角'),(52,'负迎角')]):
 beforeafter.paste(render(old,ti,'修前 · '+label,'root_R'),(0,j*650))
 beforeafter.paste(render(m,ti,'本次连续外皮候选 · '+label,'root_R'),(960,j*650))
beforeafter.save(OUT/'迎角接缝修前修后.png')
print('contact sheets saved',flush=True)
with imageio.get_writer(OUT/'完整动作与双侧翼根.mp4',fps=10,codec='libx264',quality=8,macro_block_size=2) as writer:
 for tick in range(101):
  ti=min(tick*1.5,149.999);time=ti/15
  label=['拍动','改变迎角','翼尖上反','翼尖下折','组合动作'][min(int(time//2),4)]
  canvas=Image.new('RGB',(1440,1138),(242,244,247))
  canvas.paste(render(m,ti,'完整模型 · '+label,'overview').resize((960,650)),(0,0))
  canvas.paste(render(m,ti,'右翼根','root_R').resize((720,488)),(0,650))
  canvas.paste(render(m,ti,'左翼根','root_L').resize((720,488)),(720,650))
  draw=ImageDraw.Draw(canvas);draw.rectangle((960,0,1440,650),fill=(24,38,54));
  for k,line in enumerate(['Eagle X 连续外皮候选',f'{time:.2f} 秒 · '+label,'黄色：主翼 / 绿色：翼尖','完整装配，实际三角面','接缝无填补面','大动作局部有明显伸缩','材料承受能力尚未验证']):draw.text((982,35+k*49),line,font=font,fill='white')
  writer.append_data(np.asarray(canvas))
  if tick%20==0:print('video frame',tick,flush=True)
print('video complete',flush=True)
