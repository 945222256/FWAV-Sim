"""
第12轮探针v3：GUI 模式（与 r11 飞行同款）复查每步时间推进与位移积分。

r11 运行日志已证：GUI 下 step(render=False) 推 2 物理步、render=True 推 1 步
（120 迭代=1.75s 恒定）。本探针量化：
  P1 每迭代 dt_t 直方图（复现 {1步,2步} 模式）
  P2 每迭代 dx 与 vx×dt 的比值（位置积分是否被欠推）
  P3 set_velocities 后的位置/速度独立性（排除读取 stale）
"""
import sys, os, copy, codecs
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config.sim_config import SIM_APP_CONFIG, PHYSICS_DT, FLAPPER_PRIM_PATH

cfg = copy.deepcopy(SIM_APP_CONFIG)   # headless=False —— GUI，同 r11 飞行
cfg.update(width=640, height=480)

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

_out = open(os.path.join(PROJECT_ROOT, 'logs', 'scale_probe3_result.txt'),
            'w', encoding='utf-8')
def out(s=''):
    _out.write(s + '\n'); _out.flush()

v0 = np.zeros((1, 6), dtype=np.float32); v0[0, 0] = 5.0
view.set_velocities(v0)

rows = []
N = 240
for i in range(N):
    need_render = (i % 4 == 0)
    t0 = float(world.current_time)
    world.step(render=need_render)
    t1 = float(world.current_time)
    pA, _ = loader.rigid_prim.get_world_poses()
    vA = loader.rigid_prim.get_velocities()
    rows.append((t0, t1, need_render,
                 float(pA[0][0]),
                 np.asarray(vA[0], dtype=np.float64)))

from collections import Counter
dt_t = np.array([r[1] - r[0] for r in rows])
out('== P1: per-iteration dt_t (GUI) ==')
for v, c in sorted(Counter(np.round(dt_t, 6)).items()):
    out(f'   dt_t={v:.6f}s x{c}')
out(f'   total t span = {rows[-1][1]-rows[0][0]:.4f}s over {N} iters '
    f'(expect {N*PHYSICS_DT:.4f}s if 1 step/iter)')

out('\n== P2: per-iteration dx vs vx*dt_t ==')
dx = np.diff(np.array([r[3] for r in rows]))
vx = np.array([r[4][0] for r in rows])
vxs = 0.5 * (vx[:-1] + vx[1:])
ratio = dx / (vxs * dt_t[:-1])
out(f'   median dx/(vx*dt_t) = {np.median(ratio[np.abs(vxs) > 1]):.4f}')
out('   dx[0:10] = ' + str(np.round(dx[:10], 5)))
out(f'   vx median = {np.median(vx):.3f}')

out('\n== P3: render / non-render 拆分 ==')
rr = np.array([r[2] for r in rows], bool)
for tag, m in (('render', rr[:-1]), ('no-render', ~rr[:-1])):
    if m.sum() and np.any(np.abs(vxs[m]) > 1):
        out(f'   {tag}: median dt_t={np.median(dt_t[:-1][m]):.6f}  '
            f'median dx={np.median(dx[m]):.5f}  '
            f'implied v={np.median(dx[m]/dt_t[:-1][m]):.3f}')
_out.close()
app.close()
