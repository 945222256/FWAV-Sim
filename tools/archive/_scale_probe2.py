"""
第12轮探针v2：在渲染开启（每4步 render=True，复刻 main_sim 节拍）下
复查位置/速度/时间轴三者的自洽性。

判据（区分三种机制）：
  M1 时间记账膨胀：每步 dt_t = world.current_time 增量 > 1/120，
     但 x 位移 = vx × 1/120（物理真步）→ dx/dt_t < vx
  M2 位置半步：    dt_t = 1/120 正常，但 x 位移只有 vx × dt_t / 2
  M3 无分裂：      渲染节拍下仍每步自洽（分裂另有源头→查传感器/回调）
"""
import sys, os, copy, codecs
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config.sim_config import SIM_APP_CONFIG, PHYSICS_DT, FLAPPER_PRIM_PATH

cfg = copy.deepcopy(SIM_APP_CONFIG)
cfg.update(headless=True, width=640, height=480, hide_ui=True)

from isaacsim import SimulationApp
app = SimulationApp(cfg)

import numpy as np
from isaacsim.core.api import World
from modules.flapper_loader import FlapperLoader

world = World(physics_dt=PHYSICS_DT, rendering_dt=PHYSICS_DT)
loader = FlapperLoader(world)
loader.load_flapper()

from isaacsim.core.prims import RigidPrim as RigidPrimView
_mav_path = (getattr(loader, "root_link_path", None)
             if getattr(loader, "articulated", False) else None) or FLAPPER_PRIM_PATH
view = RigidPrimView(prim_paths_expr=_mav_path, name="probe_view",
                     reset_xform_properties=False)
world.scene.add(view)
world.reset()

_out = open(os.path.join(PROJECT_ROOT, 'logs', 'scale_probe2_result.txt'),
            'w', encoding='utf-8')
def out(s=''):
    _out.write(s + '\n'); _out.flush()

v0 = np.zeros((1, 6), dtype=np.float32); v0[0, 0] = 5.0
view.set_velocities(v0)

rows = []
N = 240
RENDER_EVERY = 4
for i in range(N):
    need_render = (i % RENDER_EVERY == 0)
    t0 = float(world.current_time)
    world.step(render=need_render)
    t1 = float(world.current_time)
    pA, _ = loader.rigid_prim.get_world_poses()
    vA = loader.rigid_prim.get_velocities()
    rows.append((t0, t1, need_render,
                 np.asarray(pA[0], dtype=np.float64),
                 np.asarray(vA[0], dtype=np.float64)))

out('== per-step dt_t histogram ==')
from collections import Counter
dt_t = np.array([r[1] - r[0] for r in rows])
hist = sorted(Counter(np.round(dt_t, 6)).items())
for v, c in hist:
    out(f'   dt_t={v:.6f}s  x{c}')
out(f'   PHYSICS_DT={PHYSICS_DT:.6f}')

out('\n== render vs non-render steps ==')
for tag, m in (('render', np.array([r[2] for r in rows], bool)),
               ('no-render', ~np.array([r[2] for r in rows], bool))):
    if m[:-1].sum():
        out(f'   {tag}: median dt_t={np.median(dt_t[:-1][m[:-1]]):.6f}')

out('\n== displacement vs velocity ==')
P = np.array([r[3] for r in rows]); V = np.array([r[4] for r in rows])
dx = np.diff(P[:, 0]); vxs = 0.5 * (V[:-1, 0] + V[1:, 0])
r_mid = (dx / dt_t[:-1]) / vxs
out(f'   median (dx/dt_t)/vx = {np.median(r_mid[np.abs(vxs) > 1]):.4f}')
out(f'   median dx/vx 期望=dt_t*ratio;  dx[0:6]={np.round(dx[:6],5)}')
out(f'   vx stable ~{np.median(vxs):.3f}')
out(f'   物理期望每步位移 = vx*1/120 = {np.median(vxs)/120:.5f}')
out(f'   记账期望每步位移 = vx*dt_t = {np.median(vxs)*np.median(dt_t[:-1]):.5f}')
_out.close()
app.close()
