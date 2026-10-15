# -*- coding: utf-8 -*-
"""第15轮前置验证脚本：trail_viz 细线在 RTX 渲染下是否可见。

另一会话 headless 冒烟只验证了 points 属性写入（USD 层），未验证 RTX 渲染
可见性。本脚本：headless 离屏渲染 → 建 TrailViz → 推两条 200 点折线
（青色为期望直线、橙色为带偏差正弦）→ 相机侧视 → replicator rgb 抓帧存 PNG。
"""
import sys, os, copy, codecs
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config.sim_config import SIM_APP_CONFIG
cfg = copy.deepcopy(SIM_APP_CONFIG)
cfg.update(headless=True, width=1280, height=720, hide_ui=True)

from isaacsim import SimulationApp
app = SimulationApp(cfg)

import numpy as np
import omni.usd
from isaacsim.core.api import World
from pxr import UsdGeom, Gf
from modules.trail_viz import TrailViz

world = World(stage_units_in_meters=1.0)
world.reset()
stage = omni.usd.get_context().get_stage()

# 环境光（无场景灯时曲线全黑看不见，加一盏半球光模拟 GUI 默认环境）
from pxr import UsdLux
dome = UsdLux.DomeLight.Define(stage, '/World/Dome')
dome.CreateIntensityAttr(1000.0)

sphere = UsdGeom.Sphere.Define(stage, '/World/ProbeSphere')
sphere.CreateRadiusAttr(1.0)
sphere.CreateDisplayColorAttr([Gf.Vec3f(1.0, 0.0, 0.0)])
UsdGeom.XformCommonAPI(sphere).SetTranslate(Gf.Vec3d(0.0, 0.0, 3.0))

tv = TrailViz(stage)
xs = np.linspace(-10, 10, 200)
tv.set_path([(x, 0.0, 3.0) for x in xs])                    # 期望直线
for x in xs:
    tv.push_actual((x, 0.0, 3.0 + 0.8*np.sin(x*0.8)))       # 实际正弦
tv.flush()

from isaacsim.sensors.camera import Camera
from pxr import Gf
# 相机默认 -Z 朝向；绕 X 轴 +90° 把视线转向 +Y（侧视两条线）
q = Gf.Rotation(Gf.Vec3d(1, 0, 0), 90.0).GetQuat()
quat = np.array([q.GetReal(), q.GetImaginary()[0],
                 q.GetImaginary()[1], q.GetImaginary()[2]])
cam = Camera(prim_path='/World/Camera', name='probe_cam',
             resolution=(1280, 720),
             translation=np.array([0.0, -18.0, 4.0]),
             orientation=quat)
world.scene.add(cam)
world.reset()
cam.set_local_pose(translation=np.array([0.0, -18.0, 4.0]),
                   orientation=quat, camera_axes='usd')
for _ in range(15):
    world.step(render=True)
_v = open(os.path.join(PROJECT_ROOT, 'logs', 'trail_probe_verify.txt'), 'w')
_v.write('Viz valid: %s\n' % stage.GetPrimAtPath('/World/Viz').IsValid())
_v.write('Desired pts: %d\n' % len(tv.desired.pts))
_v.write('Actual pts: %d\n' % len(tv.actual.pts))
_mesh = stage.GetPrimAtPath('/World/Viz/ActualTrail')
_v.write('ActualTrail mesh valid: %s\n' % _mesh.IsValid())
if _mesh.IsValid():
    _v.write('  points: %s\n' % _mesh.GetAttribute('points').Get()[:3])
    _v.write('  fvCounts: %d faces\n' % len(_mesh.GetAttribute('faceVertexCounts').Get() or []))
_dmesh = stage.GetPrimAtPath('/World/Viz/DesiredPath')
_v.write('DesiredPath mesh valid: %s\n' % _dmesh.IsValid())
if _dmesh.IsValid():
    _dpts = _dmesh.GetAttribute('points').Get()
    _v.write('  points count: %s  first: %s\n' % (len(_dpts or []), (_dpts or [None])[:2]))
    _v.write('  fvCounts: %s\n' % len(_dmesh.GetAttribute('faceVertexCounts').Get() or []))
_v.close()
rgba = cam.get_rgba()
img = np.asarray(rgba)[..., :3]
out = os.path.join(PROJECT_ROOT, 'logs', 'trail_render_probe.png')
from PIL import Image
Image.fromarray(img.astype(np.uint8)).save(out)
print('[PROBE] saved', out, 'shape', img.shape)
app.close()
