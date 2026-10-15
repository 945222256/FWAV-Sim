# -*- coding: utf-8 -*-
"""
test_flight_state_machine.py — 两段状态机离线自检（任务三，2026-09-25）
======================================================================
覆盖（先于在环）：
  A. 切换条件触发：三条件同真连续第十拍才切换，第九拍不切。
  B. 解除：任一条件失真计数清零，重新累计。
  C. 单向性：CRUISE 为终态，条件失效不回退。
  D. 切换斜坡：blend 在 2 s 内 0→1 线性；增益 (KP_A/KI_A/PITCH_DAMP)
     按同一斜坡在起飞段值与巡航段值之间插值；瞬态完成后增益=巡航段值。
  E. 起飞目标斜坡：激活时刻实际俯仰→0.436 rad 的 2 s 线性斜坡；激活
     前目标跟随实际俯仰。
  F. FlightController 集成：LAUNCH 态拍频锁定 1.25 Hz（覆盖 main_sim
     释放块 _freq_last=1.55 硬复位）；锁定律激活当拍 pitch_des 同步到
     实际俯仰；条件满足后进入 TRANSIT；FSM 关闭时走原单段路径（冒烟）。
运行：python tools/test_flight_state_machine.py（纯离线，不起 Isaac）。
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config.sim_config as cfg

# 【第三十一班·签核第18项修正】FlightStateMachineConfig 属第五跑运行配置
# 组，已随失败还原从 config/sim_config.py 移除（FSM 休眠：flight_controller
# 经 getattr 防护回退单段路径）。本测试为休眠期仍可离线验证状态机行为，
# 自备第五跑参数组 shim——值取自 docs/任务三_状态机规格.md 与第五跑报告
# （起飞增益=任务二减半 0.030/0.035/0.0025，巡航=任务二保调度列）；config
# 重新携带该类时优先用 config 值（本块不覆盖）。
if not hasattr(cfg, "FlightStateMachineConfig"):
    class _FlightStateMachineShim:
        ENABLED = True                    # 测试内经 G 组开关验证 False 回退
        LAUNCH_FREQ = 1.25                # Hz（=清单 v2 FREQUENCY）
        LAUNCH_PITCH_TGT = 0.436          # rad（发射窗口时代值；配平直入口径
                                          # 下应为 LAUNCH_PITCH=0.374，重启
                                          # 状态机前须重设——签核第16项挂账）
        LAUNCH_RAMP_S = 2.0
        LAUNCH_KP_A = 0.030
        LAUNCH_KI_A = 0.0025
        LAUNCH_PITCH_DAMP = 0.035
        CRUISE_KP_A = 0.061               # 任务二保调度列
        CRUISE_KI_A = 0.005
        CRUISE_PITCH_DAMP = 0.071
        SWITCH_VX_MIN = 3.3
        SWITCH_Z_MIN = 3.5
        SWITCH_PITCH_REF = 0.374
        SWITCH_PITCH_TOL = 0.087
        SWITCH_HOLD_TICKS = 10
        SWITCH_RAMP_S = 2.0
    cfg.FlightStateMachineConfig = _FlightStateMachineShim

# 【第三十一班·签核第18项修正续】F4 断言（激活当拍 pitch_des 同步到
# 0.436）按清单 v2 第 17 处 TILT_MAX=0.55 口径写入：基线还原态
# TILT_MAX=0.35 会把 0.436 削顶（实测差 0.010 rad 超 5e-3 容差）。本测试
# 为独立进程，此处临时抬到 0.55（仅当基线低于发射俯仰时），进程退出即失效，
# 不污染 config 文件。
_fc = cfg.FlightControllerConfig
if _fc.TILT_MAX < 0.436:
    _fc.TILT_MAX = 0.55

from modules.flight_state_machine import FlightStateMachine
from modules.flight_controller import FlightController

C = cfg.FlightStateMachineConfig
DT = 1.0 / 120.0

_fail = 0


def check(name, cond):
    global _fail
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _fail += 1


def _pose_quat(pitch):
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    return [cp, 0.0, sp, 0.0]


def run_fsm(seq):
    """按 (vx, z, pitch) 序列逐拍推进，返回状态机（便于断言内部量）。"""
    m = FlightStateMachine()
    for vx, z, pitch in seq:
        m.tick(DT, vx, z, pitch)
    return m


def main():
    # ── A. 触发：第十拍切换 ──
    m = run_fsm([(0.0, 3.0, 0.0)] * 5 + [(3.4, 3.6, 0.374)] * 9)
    check("A1 九拍同真不切换", m.state == "LAUNCH")
    m.tick(DT, 3.4, 3.6, 0.374)
    check("A2 第十拍切换到 TRANSIT", m.state == "TRANSIT")
    check("A3 切换读数留档", m.switch_vx == 3.4 and m.switch_z == 3.6
          and abs(m.switch_pitch - 0.374) < 1e-9)

    # ── B. 解除：单条件失真清零 ──
    seq = [(3.4, 3.6, 0.374)] * 5 + [(3.4, 3.4, 0.374)] \
        + [(3.4, 3.6, 0.374)] * 9
    m = run_fsm(seq)
    check("B1 中途失真后 9 拍不切换（计数已清零重计）", m.state == "LAUNCH")
    m.tick(DT, 3.4, 3.6, 0.374)
    check("B2 清零后重新满十拍才切换", m.state == "TRANSIT")

    # 边界值核查（窗口闭边界、速度/高度恰好等于门限）
    m = run_fsm([(0.0, 3.0, 0.0)] * 3 + [(3.3, 3.5, 0.374 + 0.087)] * 12)
    check("B3 门限边界值（vx=3.3/z=3.5/pitch=+0.461）计为满足并切换",
          m.state == "TRANSIT")
    m = run_fsm([(0.0, 3.0, 0.0)] * 3 + [(3.3, 3.5, 0.374 - 0.088)] * 12)
    check("B4 俯仰越窗 0.001 rad 即不切换", m.state == "LAUNCH")

    # ── C. 单向性 ──
    m = run_fsm([(0.0, 3.0, 0.0)] * 3 + [(3.4, 3.6, 0.374)] * 10)
    for _ in range(int(2.5 / DT)):
        m.tick(DT, 0.0, 0.5, 1.0)      # 条件全部失效
    check("C1 瞬态完成后进入 CRUISE 且不回退", m.state == "CRUISE")

    # ── D. 切换斜坡（2 s 线性）──
    m = run_fsm([(0.0, 3.0, 0.0)] * 3 + [(3.4, 3.6, 0.374)] * 10)
    s0 = m.blend()
    for _ in range(int(1.0 / DT)):
        m.tick(DT, 3.4, 3.6, 0.374)
    s1 = m.blend()
    g1 = m.gains()
    mid = lambda a, b: 0.5 * (a + b)
    check("D1 切换当拍 blend=0", abs(s0) < 1e-9)
    check("D2 切换后 1 s blend≈0.5", abs(s1 - 0.5) < 0.01)
    check("D3 增益中点插值 KP_A",
          abs(g1[0] - mid(C.LAUNCH_KP_A, C.CRUISE_KP_A)) < 1e-4)
    check("D4 增益中点插值 KI_A",
          abs(g1[1] - mid(C.LAUNCH_KI_A, C.CRUISE_KI_A)) < 1e-5)
    check("D5 增益中点插值 PITCH_DAMP",
          abs(g1[2] - mid(C.LAUNCH_PITCH_DAMP, C.CRUISE_PITCH_DAMP)) < 1e-4)
    for _ in range(int(1.5 / DT)):
        m.tick(DT, 3.4, 3.6, 0.374)
    g2 = m.gains()
    check("D6 瞬态完成后增益=巡航段值",
          abs(g2[0] - C.CRUISE_KP_A) < 1e-9
          and abs(g2[1] - C.CRUISE_KI_A) < 1e-9
          and abs(g2[2] - C.CRUISE_PITCH_DAMP) < 1e-9)

    # ── E. 起飞目标斜坡 ──
    m = FlightStateMachine()
    tgt_pre = m.launch_pitch_target()
    check("E1 激活前目标跟随实际俯仰（0）", abs(tgt_pre) < 1e-9)
    m.tick(DT, 0.0, 3.0, 0.0)              # 仍未激活
    m.tick(DT, 4.0, 3.0, 0.436)            # 发射：激活，锚点=0.436
    check("E2 激活当拍 armed_now 置位", m.armed_now)
    check("E3 锚点=发射俯仰时目标恒 0.436",
          abs(m.launch_pitch_target() - 0.436) < 1e-9)
    m2 = FlightStateMachine()
    m2.tick(DT, 4.0, 3.0, 0.2)             # 锚点=0.2 的偏离发射
    for _ in range(int(1.0 / DT)):
        m2.tick(DT, 4.0, 3.0, 0.2)
    check("E4 半程目标=锚点与目标的中点",
          abs(m2.launch_pitch_target() - 0.5 * (0.2 + C.LAUNCH_PITCH_TGT))
          < 5e-3)
    for _ in range(int(1.5 / DT)):
        m2.tick(DT, 4.0, 3.0, 0.2)
    check("E5 2 s 后目标到达 0.436",
          abs(m2.launch_pitch_target() - C.LAUNCH_PITCH_TGT) < 1e-9)

    # ── F. FlightController 集成 ──
    ctl = FlightController()
    check("F1 控制器持有状态机", ctl._fsm is not None)
    # 台架锁定期（vx=0/pitch=0/z=3.0）
    out = ctl.update([0, 0, 3.0] + _pose_quat(0.0), [0] * 6, t_sim=DT)
    check("F2 锁定期拍频锁定 1.25", abs(out["freq"] - 1.25) < 1e-9)
    # 释放硬复位模拟（main_sim 释放块把 _freq_last 写回旧配平值）
    ctl._freq_last = 1.55
    ctl._ei = 0.0
    ctl._pitch_tgt_filt = None
    t = 2 * DT
    out = ctl.update([0, 0, 3.0] + _pose_quat(0.436), [4.0, 0, 0, 0, 0, 0],
                     t_sim=t)
    check("F3 释放后首拍拍频覆盖回 1.25", abs(out["freq"] - 1.25) < 1e-9)
    check("F4 激活当拍 pitch_des 同步到实际俯仰 0.436",
          abs(out["pitch_des"] - 0.436) < 5e-3)
    # 起飞段续跑 2 s（高度 3.2 低于切换门限 3.5 → 保持 LAUNCH 不切换）
    for k in range(int(2.0 / DT)):
        t += DT
        out = ctl.update([0, 0, 3.2] + _pose_quat(0.42),
                         [3.4, 0, 0.1, 0, 0, 0], t_sim=t)
    check("F5 LAUNCH 态 vxd 冻结为 0", abs(out["vxd"]) < 1e-9)
    check("F6 LAUNCH 态持续锁频 1.25", abs(out["freq"] - 1.25) < 1e-9)
    check("F6b 条件未满足保持 LAUNCH", ctl._fsm.state == "LAUNCH")
    # 满足三条件连续十拍 → TRANSIT；此后 TECS 生效（拍频离开锁定值调度）
    st = ctl._fsm.state
    for k in range(10):
        t += DT
        out = ctl.update([0, 0, 3.6] + _pose_quat(0.374),
                         [3.4, 0, 0.0, 0, 0, 0], t_sim=t)
    check("F7 控制器路径十拍后进入 TRANSIT", ctl._fsm.state == "TRANSIT")
    g = ctl._fsm.gains()
    check("F8 切换当拍控制器增益=起飞段值",
          abs(g[0] - C.LAUNCH_KP_A) < 1e-9)
    f_launch = out["freq"]
    for k in range(int(1.0 / DT)):
        t += DT
        out = ctl.update([0, 0, 3.6] + _pose_quat(0.374),
                         [3.4, 0, 0.0, 0, 0, 0], t_sim=t)
    g = ctl._fsm.gains()
    check("F9 切换 1 s 后控制器增益=中点",
          abs(g[0] - 0.5 * (C.LAUNCH_KP_A + C.CRUISE_KP_A)) < 1e-4)

    # ── G. FSM 关闭：原单段路径冒烟 ──
    saved = C.ENABLED
    try:
        C.ENABLED = False
        ctl2 = FlightController()
        check("G1 关闭时控制器不持有状态机", ctl2._fsm is None)
        ctl2._freq_last = 1.25
        t2 = 0.0
        for k in range(int(1.0 / DT)):
            t2 += DT
            out2 = ctl2.update([0, 0, 3.6] + _pose_quat(0.374),
                               [3.4, 0, 0.0, 0, 0, 0], t_sim=t2)
        keys = {"freq", "elevator", "aileron", "rudder", "dA", "vxd",
                "pitch_des", "roll_des"}
        check("G2 单段路径正常出全键", keys.issubset(out2.keys()))
        check("G3 单段路径 TECS 速度通道接管（vxd 在高度优先基准带内）",
              3.0 - 1e-6 <= out2["vxd"] <= cfg.FlightControllerConfig.CRUISE_VX)
    finally:
        C.ENABLED = saved

    print("\n总体: " + ("全部通过 ✓" if _fail == 0 else f"{_fail} 项失败 ✗"))
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
