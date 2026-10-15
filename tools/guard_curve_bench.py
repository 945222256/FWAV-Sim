# -*- coding: utf-8 -*-
"""
guard_curve_bench.py — 爬升守卫曲线台架实测（清单第二项收尾，work/track-prep）
================================================================================
任务：在钉固台架口径下按垂直来流逐档（爬升率 0→8 m/s，步长 0.5，共 17 档）
测量模型竖直力 Fz(vz) 曲线，与守卫乘子预测曲线 g(vz)=1/(1+(vz/2.5)²) 对照。
数据落盘 logs/guard_curve_<ts>.csv。

台架口径（与在环试验模式同构，aero_solver.py:727-758"试验模式=纯模型求值"）：
  姿态钉水平（quat=identity）、位置钉 (0,0,DP_TARGET_Z)、角速度 0、
  来流 v_air=(0,0,vz)（守卫读的 compute_forces.vel_linear 即此空速入参，
  在环对应：机体钉死 + WIND_VECTOR=(0,0,-vz)）；
  拍动参数 = 配置标称（215g 口径，拍频 FREQUENCY=1.55Hz）；
  每档跑 5 个拍动周期 @ dt=1/240，丢弃首周期瞬态，输出整周期平均 Fz
  （镜像在环链路的拍周期平均口径）。

对照组（守卫消融，测 Fz_unguarded）：在本脚本进程内用 inspect 取
FlapperAeroModel.compute_forces 源码、剔除守卫四行后编译为独立函数再求值
——零文件修改（modules/ 未动），两次测量同一初条件同一相位序列。
验证口径：实测比值 r(vz)=Fz_guarded/Fz_unguarded 应逐档等于 g(vz)。

为何离线而非 Isaac 在环台架：在环风扫阶由 ExperimentController（modules/
experiment.py，边界禁改区）驱动且只沿 x 轴（main_sim.py:1069），垂直来流
工况在不改 modules/ 的前提下无法在环施加；本离线台架即项目语义的台架
（纯模型求值），不占用仿真器。限制与遗留未决声明见 docs/爬升守卫曲线裁决.md。

用法：python tools/guard_curve_bench.py
退出码：0=全部判据通过，1=有判据不通过，2=脚本级错误。
"""
from __future__ import annotations

import csv
import inspect
import os
import sys
import textwrap
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import sim_config as cfg          # noqa: E402
from modules.aero_solver import FlapperAeroModel  # noqa: E402

OUT_FMT = os.path.join(ROOT, "logs", "guard_curve_%s.csv")

VZ_LIST = [i * 0.5 for i in range(17)]        # 0.0 → 8.0 m/s
DT = 1.0 / 240.0                              # 求值步长（细于物理步，平滑相位采样）
N_SETTLE_CYCLES = 1                           # 丢弃的首周期数
N_AVG_CYCLES = 4                              # 取平均的整周期数


def g_pred(vz: float) -> float:
    """守卫乘子预测（挂账公式：洛伦兹型，2.5 m/s 为半衰减点）。"""
    return 1.0 / (1.0 + (vz / 2.5) ** 2)


def _make_unguarded_compute():
    """取 compute_forces 源码，删除守卫四行，编译为模块级函数（进程内消融）。"""
    src = textwrap.dedent(inspect.getsource(FlapperAeroModel.compute_forces))
    lines = src.splitlines()
    out, i, removed = [], 0, 0
    while i < len(lines):
        ln = lines[i]
        if "_vz_air = float(" in ln:
            # 删除: 赋值行 / if 行 / copy 行 / 乘子行（连续4行）
            for j in range(i, i + 4):
                assert "_vz_air" in lines[j] or "F_world" in lines[j], \
                    f"守卫块结构与预期不符(第{j}行): {lines[j]!r}"
            i += 4
            removed += 1
            continue
        out.append(ln)
        i += 1
    assert removed == 1, f"守卫块应恰好出现1次, 实际{removed}次"
    patched_src = "\n".join(out)
    ns = {"np": np, "cfg": cfg,
          "quat_to_rotation_matrix": FlapperAeroModel.__init__.__globals__[
              "quat_to_rotation_matrix"]}
    exec(compile(patched_src, "<unguarded_compute_forces>", "exec"), ns)
    return ns["compute_forces"]


def measure(compute_fn, vz: float) -> float:
    """单档测量：钉固工况跑整周期，返回周期平均竖直力 Fz_world。"""
    model = FlapperAeroModel()                 # 每档重建：同参数同初相位
    quat = np.array([1.0, 0.0, 0.0, 0.0])      # 水平（w,x,y,z）
    pos = np.array([0.0, 0.0, cfg.DP_TARGET_Z])
    v_air = np.array([0.0, 0.0, vz])           # 竖直来流=等效爬升工况
    omega = np.zeros(3)
    freq = model.wing_left.frequency
    t_cycle = 1.0 / max(freq, 0.2)
    n_settle = int(round(N_SETTLE_CYCLES * t_cycle / DT))
    n_avg = int(round(N_AVG_CYCLES * t_cycle / DT))
    fz_sum, n_used, t = 0.0, 0, 0.0
    for k in range(n_settle + n_avg):
        F_world, _ = compute_fn(model, pos, quat, v_air, omega, t)
        if k >= n_settle:
            fz_sum += float(F_world[2])
            n_used += 1
        t += DT
    return fz_sum / max(n_used, 1)


def main():
    unguarded = _make_unguarded_compute()
    rows, devs, ratios = [], [], []
    print(f"[guard] 台架实测开始: {len(VZ_LIST)}档 × 守卫开/关两口径, "
          f"拍频标称{FlapperAeroModel().wing_left.frequency}Hz, "
          f"每档{N_AVG_CYCLES}整周期平均", flush=True)
    for vz in VZ_LIST:
        fz_g = measure(lambda m, *a: m.compute_forces(*a), vz)
        fz_u = measure(unguarded, vz)
        r = fz_g / fz_u if abs(fz_u) > 1e-9 else float("nan")
        g = g_pred(vz)
        dev = abs(r - g) / g if g > 0 else float("nan")
        rows.append((vz, fz_g, fz_u, r, g, dev))
        ratios.append(r)
        devs.append(dev)
        print(f"  vz={vz:4.1f}  Fz守卫开={fz_g:+9.4f}N  守卫关={fz_u:+9.4f}N  "
              f"r={r:.9f}  g={g:.9f}  偏差={dev:.2e}", flush=True)

    # 判据
    c1_impl = all(d < 1e-9 for d in devs)                     # 乘子精确作用
    c2_mono = all(ratios[i] > ratios[i + 1] for i in range(1, len(ratios) - 1))
    # （从vz=0.5起单调；vz=0档 r=1 为 if 分支分界连续性验证）
    c3_cont = abs(ratios[0] - 1.0) < 1e-9
    # 光滑性离散验证（对照解析曲率，非零阈值硬卡）：实测二阶差分应与
    # 解析 g(vz) 的二阶差分一致（洛伦兹型在 vz=0 驻点处曲率最大，
    # 每档0.5m的解析二阶差分量级≈-0.08，实测落在解析值±0.01内即光滑。
    # 初版用固定阈值0.05误判驻点曲率为跳变，已修正——见裁定文档。）
    c4_smooth = True
    for i in range(1, len(rows) - 1):
        d2_meas = (rows[i + 1][3] - rows[i][3]) - (rows[i][3] - rows[i - 1][3])
        d2_ana = (g_pred(VZ_LIST[i + 1]) - 2.0 * g_pred(VZ_LIST[i])
                  + g_pred(VZ_LIST[i - 1]))
        if abs(d2_meas - d2_ana) > 0.01:
            c4_smooth = False
            print(f"  [光滑性] 第{i}档实测二阶差分{d2_meas:+.4f} 偏离解析"
                  f"{d2_ana:+.4f} 超容差", flush=True)

    ts = time.strftime("%m%d_%H%M%S")
    out = OUT_FMT % ts
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["vz_mps", "Fz_guarded_N", "Fz_unguarded_N",
                    "ratio_measured", "g_predicted", "rel_dev"])
        for r_ in rows:
            w.writerow([f"{c:.9g}" for c in r_])
    verdict = all([c1_impl, c2_mono, c3_cont, c4_smooth])
    print(f"[guard] 判据: 实现一致性{'PASS' if c1_impl else 'FAIL'} | "
          f"单调衰减{'PASS' if c2_mono else 'FAIL'} | "
          f"vz=0分界连续{'PASS' if c3_cont else 'FAIL'} | "
          f"档间光滑{'PASS' if c4_smooth else 'FAIL'}", flush=True)
    print(f"[guard] 数据已落盘 {out}", flush=True)
    print(f"[guard] 总判: {'PASS' if verdict else 'FAIL'}"
          f"（模型侧曲线验收；真机台架一致性维持遗留未决，见裁定文档）", flush=True)
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
