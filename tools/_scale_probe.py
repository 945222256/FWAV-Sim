"""
第12轮探针：定位位置帧/速度帧 1.835 尺度分裂的机制层。

四条读取路径对照（与 main_sim.py 同款构造）：
  A_pos  loader.rigid_prim.get_world_poses()   （USD/xformCache 侧位置）
  A_vel  loader.rigid_prim.get_velocities()
  C_pos  RigidPrim 张量视图 get_world_poses()  （Fabric 侧位置）
  C_vel  RigidPrim 张量视图 get_velocities()

判据（第11轮 0.545 分裂的四种候选机制一次分开）：
  J1  dx/dt（用 world.current_time 差分） vs 速度读数
      → 比值≠1：读取 API 对本身分裂（位置被缩 or 速度被缩）
  J2  A_pos vs C_pos
      → 两位置路径不一致：分裂在 loader 的 USD 路径
  J3  time_stamp 总量 vs 步数×PHYSICS_DT
      → 不等：记录时间轴膨胀（差分 dt 错，位置与速度其实都自洽）
  J4  运行时 prim 链 xformOps / metersPerUnit
      → 非单位 scale 出现在哪一层，直接点名

运行：
  taskkill 残留 kit.exe 后
  "/g/playground/isaac_sim_5.1.0/python.bat" tools/_scale_probe.py
"""
import sys, os, copy, codecs
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config.sim_config import SIM_APP_CONFIG, PHYSICS_DT, FLAPPER_PRIM_PATH

cfg = copy.deepcopy(SIM_APP_CONFIG)
cfg.update(headless=True, width=320, height=240, hide_ui=True)

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

# kit 会吞 stdout，结果落文件
_out = open(os.path.join(PROJECT_ROOT, 'logs', 'scale_probe_result.txt'),
            'w', encoding='utf-8')
def out(s=''):
    _out.write(s + '\n'); _out.flush()

# ── J4: 运行时 prim 链变换与单位 ─────────────────────────────
from pxr import Usd, UsdGeom
stage = world.stage
out('== J4: metersPerUnit = %s' % UsdGeom.GetStageMetersPerUnit(stage))
p = stage.GetPrimAtPath(_mav_path)
chain = []
while p and p.IsValid() and str(p.GetPath()) != '/':
    chain.append(str(p.GetPath()))
    xs = UsdGeom.Xformable(p)
    if xs:
        for op in xs.GetOrderedXformOps():
            v = op.Get()
            out('   OP %s :: %s = %s' % (p.GetPath(), op.GetOpName(), v))
    p = p.GetParent()
out('   prim chain: %s' % ' <- '.join(chain))

# ── 设初速 5 m/s 沿 x，跑 240 物理步（2 秒物理时间）───────────
v0 = np.zeros((1, 6), dtype=np.float32)
v0[0, 0] = 5.0
view.set_velocities(v0)

rows = []
import time as _wt
wall0 = _wt.time()
N = 240
for i in range(N):
    world.step(render=False)
    t = float(world.current_time)
    pA, _ = loader.rigid_prim.get_world_poses()
    vA = loader.rigid_prim.get_velocities()
    pC, _ = view.get_world_poses()
    vC = view.get_velocities()
    rows.append((t,
                 np.asarray(pA[0], dtype=np.float64),
                 np.asarray(vA[0], dtype=np.float64),
                 np.asarray(pC[0], dtype=np.float64),
                 np.asarray(vC[0], dtype=np.float64)))
wall = _wt.time() - wall0

# ── 统计 ─────────────────────────────────────────────────────
T  = np.array([r[0] for r in rows])
PA = np.array([r[1] for r in rows]); VA = np.array([r[2] for r in rows])
PC = np.array([r[3] for r in rows]); VC = np.array([r[4] for r in rows])

out('\n== J3: 时间轴 ==')
out(f'   steps={N}  PHYSICS_DT={PHYSICS_DT:.6f}')
out(f'   t0={T[0]:.4f}  tN={T[-1]:.4f}  span={T[-1]-T[0]:.4f}s')
out(f'   expected span = {N*PHYSICS_DT:.4f}s   wall={wall:.1f}s')

out('\n== J2: 两条位置路径 ==')
dpos = PA - PC
out(f'   |A_pos - C_pos| max = {np.abs(dpos).max():.6f} m'
    f'   (A_start={PA[0]}, C_start={PC[0]})')
drA = np.linalg.norm(np.diff(PA, axis=0), axis=1)
drC = np.linalg.norm(np.diff(PC, axis=0), axis=1)
out(f'   step-move A={drA.mean():.5f} m  C={drC.mean():.5f} m  ratio A/C={(drA.mean()/max(drC.mean(),1e-12)):.4f}')

out('\n== J1: dx/dt vs velocity (per-axis, 用 world.current_time 差分) ==')
dt = np.diff(T)
for k, ax in enumerate('xyz'):
    dAdt = np.diff(PA[:, k]) / dt
    dCdt = np.diff(PC[:, k]) / dt
    vA_m = VA[:-1, k] + 0.5 * np.diff(VA[:, k])
    vC_m = VC[:-1, k] + 0.5 * np.diff(VC[:, k])
    def ratio(num, den):
        m = np.abs(den) > 0.2
        return np.median(num[m] / den[m]) if m.sum() > 5 else float('nan')
    out(f'   [{ax}] dA/dt/vA = {ratio(dAdt, vA_m):.4f}   '
        f'dC/dt/vC = {ratio(dCdt, vC_m):.4f}   '
        f'vA/vC = {ratio(vA_m, vC_m):.4f}')

out('\n== 末态样本 ==')
out(f'   A_pos={np.round(PA[-1],3)}  A_vel={np.round(VA[-1],3)}')
out(f'   C_pos={np.round(PC[-1],3)}  C_vel={np.round(VC[-1],3)}')
_out.close()
app.close()
