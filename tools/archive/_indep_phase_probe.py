# 独立验证脚本（不依赖 Isaac）：
#  A. 相位 = f·t 的绝对时间实现，在运行时改频时的相位跳变量化
#  B. 悬停瞬时力气动振荡 -> 机体速度纹波估算（0.7kg）
#  C. 关节目标与气动模型相位失同步（一拍滞后 + 改频）随 t 增长
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from modules.aero_solver import FlapperAeroModel, WingKinematics
from config import sim_config as cfg

m = FlapperAeroModel()
for w in (m.wing_left, m.wing_right):
    w.set_frequency(1.9)
    w.amplitude = np.radians(35.0)
    w.pitch_mode = "aero"
    w.alpha_set = np.radians(15.0)

W = cfg.MASS * 9.81
q0 = np.array([1.0, 0, 0, 0], dtype=float)

# ---------- A. 改频引起的相位跳变 ----------
print("=== A. set_frequency 的相位跳变（tau = f*t mod 1）===")
wk = WingKinematics(frequency=1.9, amplitude_deg=35.0, pitch_mode="aero", alpha_set_deg=15.0)
t = 10.0
phi_before = wk.evaluate(t)["phi"]
wk.set_frequency(1.9 + 0.05)          # 单步 +0.05Hz（飞控 FREQ_SLEW=3Hz/s 的两步量）
phi_after = WingKinematics(frequency=1.95, amplitude_deg=35.0,
                           pitch_mode="aero", alpha_set_deg=15.0).evaluate(t)["phi"]
dphi = wk.evaluate(t + 1/120.0, )["phi"]
print(f"t={t}s, dF=+0.05Hz -> phi: {np.degrees(phi_before):+.1f}deg -> {np.degrees(phi_after):+.1f}deg "
      f"(跳变 {np.degrees(phi_after-phi_before):+.1f}deg)")
print(f"等效每步跳变公式: dtau = t*df -> {t*0.05:.3f} 周期 = {t*0.05*360:.0f}deg")

# ---------- B. 悬停瞬时力振荡 ----------
print("\n=== B. 悬停瞬时气动力振荡（f=1.9Hz, amp=35deg, 静气）===")
T = 1.0 / 1.9
n = 240
fz, fx = [], []
for i in range(n):
    ti = i * T / n
    F, _ = m.compute_forces(np.array([0, 0, 1.5]), q0,
                            np.zeros(3), np.zeros(3), ti)
    fz.append(F[2]); fx.append(F[0])
fz, fx = np.array(fz), np.array(fx)
print(f"周期均值: Fz={fz.mean():+.2f}N (W={W:.2f}N, {fz.mean()/W:.2f}xW), Fx={fx.mean():+.2f}N")
print(f"瞬时范围: Fz=[{fz.min():+.2f},{fz.max():+.2f}]N  峰峰值={np.ptp(fz):.1f}N")
# 半拍冲量 -> 速度变化
dv_half = (fz.max() - W) / cfg.MASS * (T / 4)
print(f"半拍最大速度变化量级: dv ≈ {dv_half:.2f} m/s（叠加在均值上的拍频纹波）")

# ---------- C. 关节相位 vs 气动模型相位失同步 ----------
print("\n=== C. 主循环一拍滞后同步 + 连续改频 => 力/机构相位失同步 ===")
# 飞控在环: FREQ_SLEW=3Hz/s, 每物理步 df = 3/120 = 0.025Hz
# 关节 _wk 用 f_n, 气动 model 用 f_{n-1}（world.step 内回调先跑）
# 相位差 = t * df
for t in [2.0, 5.0, 10.0, 20.0]:
    dtau = t * (3.0 / 120.0)
    print(f"t={t:4.1f}s: 关节目标与气动力的相位差 = {dtau:.3f} 周期 = {dtau*360:5.1f}deg")

# ---------- D. 直接演示: 连续小幅调频下的目标波形毛刺 ----------
print("\n=== D. DP 慢速调频（0.8Hz/s 斜坡）下每步 phi 目标跳变 ===")
wk2 = WingKinematics(frequency=1.9, amplitude_deg=35.0, pitch_mode="aero", alpha_set_deg=15.0)
prev = None
mx = 0.0
for k in range(120 * 15):           # 15s, 120Hz
    t = k / 120.0
    f = 1.9 + 0.8 * t / 15.0 * 0    # 保持 f 不变时
    f = 1.9 + (0.8 / 120.0) * k     # 0.8Hz/s 线性斜坡
    wk2.set_frequency(f)
    p = wk2.evaluate(t)["phi"]
    if prev is not None:
        mx = max(mx, abs(p - prev))
    prev = p
print(f"15s 斜坡期间最大单步 phi 目标跳变: {np.degrees(mx):.2f}deg "
      f"(正常每步应约 {np.degrees(35*2*np.pi*1.9/120):.2f}deg)")
