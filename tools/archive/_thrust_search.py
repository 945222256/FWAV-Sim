# -*- coding: utf-8 -*-
"""推力运动学搜索（夜间调试第2轮，断点①）。
在巡航点 V=8m/s、f=3.5Hz、A=35° 下扫 ALPHA_SET×SPLIT×PITCH_LAG×MEAN，
找 cycle-mean Fx>−0.5N 且 Fz≈6.87N（体重）的组合。
历史先例：9-18 协同扫参曾得 T̄=+0.96N（config 第169行注释）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from modules.aero_solver import FlapperAeroModel, euler_to_quat

V, F, A = 8.0, 3.5, 35.0
W = 6.87
PITCH = -0.20          # V=8 最佳升力俯仰（第1轮包络）
MODE = sys.argv[1] if len(sys.argv) > 1 else "aero"

m = FlapperAeroModel()
m.set_wing_command(frequency=F)
q0 = euler_to_quat(0.0, PITCH, 0.0)
v_air = np.array([V, 0.0, 0.0])
ts = np.arange(0.0, 6.0 / F, 1.0 / 240.0)   # 6 个周期
res = []
for alpha in (10, 15, 20, 25, 30):
    for split in (0.40, 0.48, 0.56, 0.64):
        for lag in (-0.04, -0.02, 0.0, 0.02):
            for mean in (-8, -4, 0, 4, 8):
                for wing in (m.wing_left, m.wing_right):
                    wing.amplitude = np.radians(A)
                    wing.pitch_mode = MODE
                    wing.alpha_set = np.radians(alpha)
                    wing.split = split
                    wing.pitch_lag_s = lag
                    wing.mean = np.radians(mean)
                Fx = Fz = 0.0
                for t in ts:
                    Fo, _ = m.compute_forces(np.zeros(3), q0, v_air,
                                             np.zeros(3), t)
                    Fx += Fo[0]; Fz += Fo[2]
                n = len(ts)
                res.append((Fx / n, Fz / n, alpha, split, lag, mean))
res.sort(key=lambda r: -r[0])
print("mode=%s  V=%.0f f=%.1f A=%d pitch=%+.2f  （体重 %.2fN）"
      % (MODE, V, F, A, PITCH, W))
print("排名 Fx      Fz     α    split  lag    mean  | 可配平(6.3<Fz<7.5)")
for r in res[:12]:
    ok = "是" if 6.3 < r[1] < 7.5 else "否"
    print("%+7.2f %+7.2f  %2d°  %.2f  %+.2f  %+d°   %s"
          % (r[0], r[1], r[2], r[3], r[4], r[5], ok))
fx_ok = [r for r in res if r[0] > -0.5 and 6.3 < r[1] < 7.5]
print("\nFx>−0.5 且 Fz 在体重带的解：%d 个" % len(fx_ok))
if not fx_ok:
    print("包络参考：全局最大 Fx=%+.2f（对应 Fz=%+.2f）"
          % (res[0][0], res[0][1]))
