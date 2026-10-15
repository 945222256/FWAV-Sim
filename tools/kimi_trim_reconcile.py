# -*- coding: utf-8 -*-
"""kimi_trim_reconcile.py — 两被控对象配平拍频矛盾调和·第一步：同状态逐点对照。

方法：读取在环 ident_stream_flight_0920_191134.csv 的每个样本状态
(freq, vx, vz, pitch, roll, dA)，用离线被控对象（FlapperAeroModel +
TailFuselageAero，24 相位周期平均口径，与试验架同源）在同一状态复算
竖直力 Fz，与在环实际施加的 Fz 列逐点相减。

判别逻辑：
  差值≈0          → 力律两被控对象一致，分叉在状态演化（驱动/关节/动力学）
  差值随 freq 缩放 → 频率轴缩放（时基/波形发生器）
  差值集中在 vz>0 → 守卫口径差异
另输出离线 L̄(f) 曲线（vx=3.0/4.0 两档）与配平点 f*。
"""
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config.sim_config as cfg
from modules.aero_solver import FlapperAeroModel, TailFuselageAero

cfg.WIND_ENABLED = False
A0 = math.radians(cfg.FlapKinematicsConfig.AMPLITUDE)
MASS = cfg.MASS
W = MASS * 9.81

model = FlapperAeroModel()
tail = TailFuselageAero()


def offline_fz(vx, vz, roll, pitch, freq, dA, n=24):
    model.set_wing_command(frequency=freq)
    model.wing_left.amplitude = A0 + dA
    model.wing_right.amplitude = A0 - dA
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    quat = np.array([cr * cp, sr * cp, cr * sp, 0.0])
    v_lin = np.array([vx, 0.0, vz])
    v_ang = np.zeros(3)
    s = 0.0
    for i in range(n):
        F, _T = model.compute_forces(np.zeros(3), quat, v_lin, v_ang,
                                     i / (n * max(freq, 0.5)))
        s += F[2]
    v_slip = cfg.SLIP_K * cfg.SLIP_V_PER_HZ * freq \
        if getattr(cfg, "ENABLE_TAIL_SLIPSTREAM", False) else 0.0
    Ft, _Tt = tail.compute(quat, v_lin, v_slip=v_slip)
    return s / n + Ft[2]


def main():
    import csv
    f = os.path.join(ROOT, "logs", "ident_stream_flight_0920_191134.csv")
    rows = list(csv.reader(open(f, encoding="utf-8", errors="replace")))
    hdr = rows[0]
    ix = {h: i for i, h in enumerate(hdr)}

    def g(r, c):
        try:
            return float(r[ix[c]])
        except Exception:
            return None

    samples = []
    for r in rows[1::20]:           # 20 抽 1 抽取，约 860 点
        t = g(r, "t"); freq = g(r, "freq"); vx = g(r, "vx"); vz = g(r, "vz")
        roll = g(r, "roll"); pitch = g(r, "pitch"); dA = g(r, "dA") or 0.0
        fz_loop = g(r, "Fz")
        if None in (t, freq, vx, vz, roll, pitch, fz_loop):
            continue
        if t < 2.0 or t > 64.0:      # 只看飞行段（坠地后不看）
            continue
        samples.append((t, freq, vx, vz, roll, pitch, dA, fz_loop))
    print(f"对照样本 {len(samples)} 个（t∈[2,64] 飞行段）")

    diffs = []
    for (t, freq, vx, vz, roll, pitch, dA, fz_loop) in samples:
        fz_off = offline_fz(vx, vz, roll, pitch, freq, dA)
        diffs.append((t, freq, vx, vz, fz_loop, fz_off, fz_loop - fz_off))

    arr = np.array(diffs)
    print(f"\n总体差值（在环-离线）: 均值 {arr[:,6].mean():+.3f} N，"
          f"中位 {np.median(arr[:,6]):+.3f} N，标准差 {arr[:,6].std():.3f} N")
    print(f"在环 Fz 均值 {arr[:,4].mean():+.3f} N，离线复算均值 {arr[:,5].mean():+.3f} N，"
          f"体重 {W:.3f} N")

    print("\n按拍频分档（均值差）:")
    for lo, hi in ((1.0, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 3.6)):
        m = (arr[:, 1] >= lo) & (arr[:, 1] < hi)
        if m.sum() > 3:
            print(f"  f∈[{lo:.1f},{hi:.1f}): n={m.sum():4d} 在环 {arr[m,4].mean():+6.3f} "
                  f"离线 {arr[m,5].mean():+6.3f} 差 {arr[m,6].mean():+6.3f} N")

    print("\n按垂直速度符号分档（均值差）:")
    for name, m in (("vz>+0.3", arr[:, 3] > 0.3),
                    ("|vz|≤0.3", np.abs(arr[:, 3]) <= 0.3),
                    ("vz<−0.3", arr[:, 3] < -0.3)):
        if m.sum() > 3:
            print(f"  {name:8s}: n={m.sum():4d} 在环 {arr[m,4].mean():+6.3f} "
                  f"离线 {arr[m,5].mean():+6.3f} 差 {arr[m,6].mean():+6.3f} N")

    print("\n按时间段（均值差，看在环-离线差随飞行进程是否漂移）:")
    for lo, hi in ((2, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60), (60, 64)):
        m = (arr[:, 0] >= lo) & (arr[:, 0] < hi)
        if m.sum() > 3:
            print(f"  t∈[{lo:2d},{hi:2d}): n={m.sum():4d} 在环 {arr[m,4].mean():+6.3f} "
                  f"离线 {arr[m,5].mean():+6.3f} 差 {arr[m,6].mean():+6.3f} N "
                  f"| 该段 freq 均值 {arr[m,1].mean():.2f} vx 均值 {arr[m,2].mean():.2f}")

    # 离线 L̄(f) 曲线与配平点
    print("\n离线 L̄(f) 曲线（pitch=0、dA=0、vz=0）:")
    print("  f     L̄@vx=3.0   L̄@vx=4.0")
    for vx0 in (3.0, 4.0):
        fstar = None
        for fq in np.arange(1.0, 3.55, 0.05):
            l = offline_fz(vx0, 0.0, 0.0, 0.0, fq, 0.0)
            if fstar is None and l >= W:
                fstar = fq
            if vx0 == 4.0 and abs(fq * 20 - round(fq * 20)) < 1e-6 and round(fq * 20) % 5 == 0:
                pass
        print(f"  vx={vx0}: 配平 f*（L̄=W={W:.2f}）≈ {fstar}")
    for fq in (1.0, 1.15, 1.3, 1.55, 2.0, 2.5, 3.0, 3.5):
        l3 = offline_fz(3.0, 0.0, 0.0, 0.0, fq, 0.0)
        l4 = offline_fz(4.0, 0.0, 0.0, 0.0, fq, 0.0)
        print(f"  {fq:4.2f}  {l3:+8.3f}  {l4:+8.3f}")


if __name__ == "__main__":
    main()
