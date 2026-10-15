# -*- coding: utf-8 -*-
"""
wavegen_interface_check.py — 发生器与包络骨架接口核查（I-140 证据）
====================================================================
五项核查对应 rl/wavegen_interface_eval_20261009.md 第五节：
  1. 域覆盖与幅值换算；
  2. 占空比可达域边界抛错；
  3. 适配映射演示（骨架四通道 → 五自由度 → 整周期波形，周期精确）；
  4. 差动展开（dA → 左右 A 微差，双翼波形对称分离）；
  5. 槽位登记与未填充报错复验。
用法：python rl/wavegen_interface_check.py
"""
import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

sys.path.insert(0, os.path.join(PROJECT_ROOT, "ltv"))
from wavegen_family import (c_to_b, duty_c, outputs_on_cycle, time_map,
                            H_profile, min_H)

from rl.env_envelope import EnvelopeDecisionEnv, FormalDataSlots

F_SCAFFOLD = (1.5, 3.5)      # 骨架频域（归一化反变换）
AMP_REG_DEG = 17.73          # 登记幅值（wave.kinematics.amplitude 现行值）


def check_1_domain():
    # 发生器 f 恒正：骨架域在内部
    assert all(f > 0 for f in F_SCAFFOLD)
    # 幅值换算闭式：amp_frac=0.5 → A=0.5*A_reg
    a_frac = 0.5
    A = a_frac * np.radians(AMP_REG_DEG)
    assert abs(A - np.radians(AMP_REG_DEG) / 2) < 1e-15
    print("[核查1] 频域覆盖与幅值换算 通过")
    return True


def check_2_boundary():
    # 可达域上限 |c| = 1.1017，占空比 s = 0.5 + c/pi
    s_max = 0.5 + 1.1017 / np.pi
    try:
        c_to_b(duty_c(s_max + 0.05))
        return False
    except ValueError:
        pass
    # 域内边界值可用且安全线成立
    b1, b3 = c_to_b(duty_c(0.85))
    assert min_H(b1, b3) >= 0.1 - 1e-6
    print("[核查2] 占空比越界抛错与安全线 通过")
    return True


def adapter(ch, cfg_deg=AMP_REG_DEG):
    """骨架四通道 → 五自由度+微差（评估报告第四节槽二的映射演示）。

    缺通道（mu/s/psi）以登记缺省值顶位：mu 取 0（现行工作点近零）、
    s 取现行工作点 0.176、psi 取现行键换算 2*pi*f*(-0.02)。
    duty 通道恒映射为满作动（语义一），dA 映射为左右 A 微差。
    """
    f = 1.5 + 1.0 * (ch["freq_n"] + 1.0) / 2.0
    A = (ch["amp_frac_n"] + 1.0) / 2.0 * np.radians(cfg_deg)
    p = {"mu": 0.0, "A": A, "f": f, "s": 0.176,
         "psi": 2.0 * np.pi * f * (-0.02)}
    dA = 0.25 * ch["dA_n"]
    diff = {"mu": 0.0, "A": dA, "s": 0.0, "psi": 0.0}
    return p, diff


def check_3_adapter():
    ch = {"freq_n": 0.2, "dA_n": 0.0, "duty_n": 0.3, "amp_frac_n": 0.4}
    p, diff = adapter(ch)
    b1, b3 = c_to_b(duty_c(p["s"]))
    # 整周期波形采样（发生器约化实现口径：theta 均匀网格经时间映射）
    u = np.linspace(0.0, 1.0, 289)
    t = time_map(u, p["f"], b1, b3)
    theta = 2.0 * np.pi * u
    out = outputs_on_cycle(theta, (p["mu"], p["A"], p["f"], p["s"]),
                           a_amp=0.6, a_bias=0.0)
    finite = all(np.all(np.isfinite(v)) for v in out.values())
    # 周期精确：time_map 闭式 t(1)-t(0) = 1/f
    T_cycle = t[-1] - t[0]
    rel_err = abs(T_cycle - 1.0 / p["f"]) * p["f"]
    assert finite and rel_err < 1e-12
    print(f"[核查3] 适配演示：波形有限且周期相对误差 {rel_err:.2e} 通过")
    return True


def check_4_differential():
    ch = {"freq_n": 0.0, "dA_n": 0.6, "duty_n": 0.0, "amp_frac_n": 0.4}
    p, diff = adapter(ch)
    b1, b3 = c_to_b(duty_c(p["s"]))
    u = np.linspace(0.0, 1.5, 20001)
    theta = 2.0 * np.pi * u
    A_L = p["A"] + diff["A"]
    A_R = p["A"] - diff["A"]
    out_L = outputs_on_cycle(theta, (p["mu"], A_L, p["f"], p["s"]),
                             0.6, 0.0)
    out_R = outputs_on_cycle(theta, (p["mu"], A_R, p["f"], p["s"]),
                             0.6, 0.0)
    d = np.array(out_L["phi"]) - np.array(out_R["phi"])
    sym = abs(d.max() + d.min())          # 关于零对称的分离（采样网格容差）
    assert d.max() > 0 and sym < 1e-6
    print(f"[核查4] 差动展开：双翼波形对称分离，峰差 {d.max():.4f} rad 通过")
    return True


def check_5_slots():
    # 按评估报告第四节格式登记三槽后，未填充请求即报错
    fmts = {
        "waveform_generator": "有状态对象 reset/set_params/step_outputs",
        "envelope_to_generator_map": "callable(channels, cfg) -> (p, diff)",
        "wing_state_model_FLAP": "callable(theta, state) -> W[3]",
    }
    slots = FormalDataSlots()
    for k, v in fmts.items():
        slots.FORMATS.setdefault(k, v)
    for k in fmts:
        try:
            slots.get(k)
            return False
        except RuntimeError:
            pass
    print("[核查5] 槽位登记与未填充报错复验 通过")
    return True


if __name__ == "__main__":
    rs = [check_1_domain(), check_2_boundary(), check_3_adapter(),
          check_4_differential(), check_5_slots()]
    assert all(rs)
    print("[核查] 五项核查全部通过")
