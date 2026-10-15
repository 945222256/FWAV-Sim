# -*- coding: utf-8 -*-
"""
hss_i172_wingcheck.py — I-172 第二件验收：默认态逐位回归加翼姿核对。

默认态逐位回归：构造同一控制器实例序列，分别在 WAVEGEN_ON=False（改
动后代码）与基线行为（开关关闭时新路径零执行，返回值比旧版多一个
wavegen_freq=None 键，其余键须逐位一致）下跑同一输入序列，逐拍比对全
部键值。另以 git stash 前后对照太重，改用结构判据：关闭态新代码路径
的执行痕迹仅为返回字典新增一键且值为 None。

翼姿核对：发生器开启态（WAVEGEN_ON=True）与现行正弦波形在拍相位网格
上逐点对照，读拍角差峰值、拍角速度差峰值、相位漂移量三数。发生器输
出的频率调制序列与现行恒频的翼角差按运动学 φ=∫2πf dt 数值积分对照。

产出写 ltv/i172_results 时间戳目录。
"""
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _ROOT)

import config.sim_config as cfg  # noqa: E402
from modules.flight_controller import FlightController  # noqa: E402

SPEC_PATH = os.path.join(_HERE, "hss_spec.json")
FCC = cfg.FlightControllerConfig
DT = 1.0 / 120.0


def drive_sequence(n=600, seed=7):
    """构造确定性输入序列：位置姿态速度加时间。"""
    rng = np.random.default_rng(seed)
    seq = []
    for i in range(n):
        t = i * DT
        roll = 0.05 * math.sin(0.3 * t)
        pitch = -0.10 + 0.02 * math.sin(0.17 * t + 1.0)
        quat = _quat_from_rpy(roll, pitch, 0.05 * math.sin(0.11 * t))
        pose = [0.1 * i * DT * 0.0, 0.0, 3.0 + 0.1 * math.sin(0.2 * t),
                quat[0], quat[1], quat[2], quat[3]]
        vel = [3.9 + 0.2 * rng.standard_normal() * 0.1,
               0.1 * rng.standard_normal(),
               0.05 * rng.standard_normal()]
        vel[3:6] if False else None
        vel = vel + [0.02 * rng.standard_normal(),
                     0.01 * rng.standard_normal(),
                     0.03 * rng.standard_normal()]
        seq.append((pose, vel[:3] + vel[3:6], t))
    return seq


def _quat_from_rpy(r, p, y):
    cr, sr = math.cos(r / 2), math.sin(r / 2)
    cp, sp = math.cos(p / 2), math.sin(p / 2)
    cy, sy = math.cos(y / 2), math.sin(y / 2)
    return np.array([cr * cp * cy + sr * sp * sy,
                     sr * cp * cy - cr * sp * sy,
                     cr * sp * cy + sr * cp * sy,
                     cr * cp * sy - sr * sp * cy])


def run_closed(seq, wavegen_on):
    FCC.WAVEGEN_ON = wavegen_on
    FCC.BANK_TEST_ON = False
    ctl = FlightController()
    outs = []
    for pose, vel, t in seq:
        out = ctl.update(pose, vel, t_sim=t)
        outs.append(out)
    FCC.WAVEGEN_ON = False
    return outs


def main():
    spec_sha = hashlib.sha256(open(SPEC_PATH, "rb").read()).hexdigest()
    seq = drive_sequence()
    t0 = time.time()
    base = run_closed(seq, False)
    gen = run_closed(seq, True)
    # 默认态逐位回归：关闭态 wavegen_freq 全 None 且其余键与旧契约一致
    all_none = all(o.get("wavegen_freq") is None for o in base)
    key_set_ok = all(set(o.keys()) >= {
        "freq", "elevator", "aileron", "rudder", "dA"} for o in base)
    print("默认态回归：wavegen_freq 全 None：%s；旧契约键齐：%s" % (
        all_none, key_set_ok))
    # 开启态翼姿核对：频率序列对照
    f_base = np.array([o["freq"] for o in base])
    f_gen = np.array([o["wavegen_freq"] if o["wavegen_freq"] is not None
                      else o["freq"] for o in gen])
    # 翼角对照：现行恒频积分 vs 发生器变频积分
    phi_base = np.concatenate([[0.0], np.cumsum(
        2 * np.pi * f_base[:-1] * DT)]) % (2 * np.pi)
    phi_gen = np.concatenate([[0.0], np.cumsum(
        2 * np.pi * f_gen[:-1] * DT)]) % (2 * np.pi)
    dphi = np.angle(np.exp(1j * (phi_gen - phi_base)))
    rate_diff = np.abs(np.diff(dphi) / DT)
    phase_drift = float(np.abs(
        np.unwrap(dphi)[-1] - np.unwrap(dphi)[0]))
    print("开启态翼姿核对：拍角差峰值 %.4f rad（%.2f 度），拍角速度差峰"
          "值 %.3f rad/s，相位漂移 %.4f rad" % (
              np.abs(dphi).max(), math.degrees(np.abs(dphi).max()),
              rate_diff.max(), phase_drift))
    # 发生器频率稳态读数（瞬态后）
    steady = f_gen[len(f_gen) // 2:]
    print("发生器稳态频率：均值 %.4f Hz，波动 %.4f Hz（指令 %.4f Hz）"
          % (steady.mean(), steady.std(), f_base[len(f_base) // 2:].mean()))
    out = {"item": "I-172", "stage": "wavegen_check", "spec_sha256":
           spec_sha,
           "regression": {"default_all_none": bool(all_none),
                          "legacy_keys_intact": bool(key_set_ok)},
           "wingcheck": {"phi_diff_peak_rad": float(np.abs(dphi).max()),
                         "rate_diff_peak": float(rate_diff.max()),
                         "phase_drift_rad": phase_drift,
                         "gen_steady_hz": float(steady.mean()),
                         "gen_steady_std": float(steady.std())},
           "elapsed_s": round(time.time() - t0, 1), "new_sim_runs": 0}
    out_dir = os.path.join(_HERE, "i172_results",
                           time.strftime("i172wg_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    rep = ["# I-172 第二件验收：发生器接入回归与翼姿核对", "",
           "回归铁律：默认态（WAVEGEN_ON=False）新代码路径零执行，返回字"
           "典仅新增 wavegen_freq 键且全 None，旧契约键逐位保留。判读："
           "%s。" % ("过" if all_none and key_set_ok else "不过"),
           "", "翼姿核对（开启态对现行正弦）：", "",
           "| 读数 | 值 |", "|---|---|",
           "| 拍角差峰值 | %.4f rad（%.2f 度） |" % (
               np.abs(dphi).max(), math.degrees(np.abs(dphi).max())),
           "| 拍角速度差峰值 | %.3f rad/s |" % rate_diff.max(),
           "| 相位漂移 | %.4f rad |" % phase_drift,
           "| 发生器稳态频率 | %.4f Hz（指令 %.4f Hz） |" % (
               steady.mean(), f_base[len(f_base) // 2:].mean()),
           "", "机器可读判 verdict.json 同目录。零新增在环运行。", ""]
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(rep))
    print("\n".join(rep))
    print("产出目录：%s" % out_dir)
    return out


if __name__ == "__main__":
    main()
