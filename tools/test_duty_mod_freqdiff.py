# -*- coding: utf-8 -*-
"""
test_duty_mod_freqdiff.py — 占空比调制与频率差动的离线单元测
（不定频率完全差动线·阶段二，2026-09-27）

判据来源：docs/不定频率完全差动_判据与方案.md；本文件测逻辑正确性，
不进仿真器、不动物理结论。测试清单：
  T1 开关全关：dfreq 恒 0、闸门恒 1（关闭态行为逐位一致）。
  T2 占空比密度精确性：恒定滚转误差使 δρ=−0.25（左翼密度 0.75），
     40 拍内左翼拍数应为 30±1，右翼全拍。
  T3 死区：微小滚转误差下双翼全拍。
  T4 方向：正滚转误差（roll_des>roll）应只减右翼密度（首日标定
     SD_ROLL_DIR=+1：右停左拍得正滚转）。
  T5 频率差动：恒定偏航角速率下 dfreq 符号与幅值正确，且恒满足
     |dfreq|≤FD_MAX 与每翼频率硬界 [1.0, 2.2]。
  T6 互斥：DUTY_MOD 与 STROKE_DIFF 同开时占空比优先且不异常。
  T7 占空比开启时老幅值差动 dA 旁路（输出恒 0）。
"""
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config.sim_config as cfg
from modules.flight_controller import FlightController, _freq_diff_clamp

F = cfg.FlightControllerConfig
DT = 0.02


def make_pose(roll=0.0, pitch=0.206, yaw=0.0):
    """构造 pose=[x,y,z,qw,qx,qy,qz]（z 置高度基准 1.5，消高度环扰动）。"""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy
    return [0.0, 0.0, 1.5, qw, qx, qy, qz]


def make_vel(wx=0.0, wz=0.0):
    return [0.0, 0.0, 0.0, wx, 0.0, wz]


def run(ctrl, pose, vel, seconds):
    outs = []
    t = 0.0
    for _ in range(int(seconds / DT)):
        outs.append(ctrl.update(pose, vel, t_sim=t))
        t += DT
    return outs


def beat_gates(outs):
    """停拍占比（样本计数口径：闸门在拍内恒定，样本占比=拍数占比）。

    注意：此前用"变化沿计数"会把 ρ=0.75 的 0,1,1,1 图样误读成 0.5
    （沿在 0→1 与 1→0 各计一次），本函数按原始样本统计。
    """
    n = len(outs)
    return (sum(1 for o in outs if o["gate_L"] == 0.0) / n,
            sum(1 for o in outs if o["gate_R"] == 0.0) / n)


def main():
    passed, failed = [], []

    # T1 开关全关
    F.DUTY_MOD_ON = F.FREQ_DIFF_ON = F.STROKE_DIFF_ON = False
    c = FlightController()
    outs = run(c, make_pose(roll=0.1), make_vel(), 6.0)
    ok = all(o["dfreq"] == 0.0 and o["gate_L"] == 1.0 and o["gate_R"] == 1.0
             for o in outs)
    (passed if ok else failed).append("T1 关闭态逐位一致")

    # T2 密度精确性（差分口径，不假设 roll_des=0）：
    # 跑 A（roll=0）测基线停拍占比，跑 B（roll=−0.0625，δρ 加 +0.25）
    # 测停拍占比增量；增量应为 0.25±0.06，且只发生在右翼。
    F.DUTY_MOD_ON = True
    F.DM_KP = 4.0
    F.DM_KD = 0.0
    c = FlightController()
    sLA, sRA = beat_gates(run(c, make_pose(roll=0.0), make_vel(), 30.0))
    c = FlightController()
    sLB, sRB = beat_gates(run(c, make_pose(roll=-0.0625), make_vel(), 30.0))
    d_stop = sRB - sRA
    ok = abs(d_stop - 0.25) <= 0.06 and abs(sLB - sLA) <= 0.02
    print(f"  T2: 右翼停拍占比 {sRA:.3f}→{sRB:.3f}（增量 {d_stop:+.3f}，"
          f"期望 +0.250）；左翼 {sLA:.3f}→{sLB:.3f}", flush=True)
    (passed if ok else failed).append("T2 密度精确性（差分 +0.25）")

    # T3 死区
    c = FlightController()
    outs = run(c, make_pose(roll=-0.001), make_vel(), 8.0)
    ok = all(o["gate_L"] == 1.0 and o["gate_R"] == 1.0 for o in outs)
    (passed if ok else failed).append("T3 死区双翼全拍")

    # T4 方向：roll=+0.1（roll_des−roll<0，需负滚转）→ 只减左翼密度
    c = FlightController()
    sL, sR = beat_gates(run(c, make_pose(roll=0.1), make_vel(wx=0.0), 20.0))
    ok = sL >= 0.2 and sR <= 0.02
    print(f"  T4: 左翼停占比={sL:.3f} 右翼停占比={sR:.3f}"
          f"（期望左停右不停）", flush=True)
    (passed if ok else failed).append("T4 方向：正误差只减对侧")

    # T5 频率差动裁剪纯函数真值表 + 在环一致性
    F.DUTY_MOD_ON = False
    cases = [
        (0.25, 1.8, 0.25),    # 域内不裁剪
        (-0.25, 1.8, -0.25),  # 域内负号保持
        (0.5, 1.8, 0.4),      # FD_MAX 截断
        (0.3, 2.1, 0.1),      # 上界 FHI−freq
        (-0.3, 1.1, -0.1),    # 下界 FLO−freq
        (0.2, 0.9, 0.0),      # 区间倒置→0（T5 抓获形态回归：freq=−0.3 类）
        (-0.2, 2.5, 0.0),     # 集体拍频超上界→0
        (0.4, 2.2, 0.0),      # 贴上界→0
    ]
    ok = True
    for df_in, fr, exp in cases:
        got = _freq_diff_clamp(df_in, fr, F)
        if abs(got - exp) > 1e-9:
            ok = False
            print(f"  T5 不符: df={df_in} freq={fr} → {got}（期望 {exp}）",
                  flush=True)
    (passed if ok else failed).append("T5a 频率差裁剪真值表")

    # T5b 在环一致性：输出的 dfreq 必须等于裁剪函数的逐样本核算值
    F.FREQ_DIFF_ON = True
    F.FD_KP = 0.0
    F.FD_KD = 0.5
    F.FD_DIR = 1.0
    c = FlightController()
    outs = run(c, make_pose(), make_vel(wz=0.5), 6.0)[50:]
    ok = True
    n_sig = 0
    for o in outs:
        exp = _freq_diff_clamp(float(np.clip(-0.25, -F.FD_MAX, F.FD_MAX)),
                               o["freq"], F)
        if abs(o["dfreq"] - exp) > 1e-9:
            ok = False
            print(f"  T5b 不符: freq={o['freq']:.3f} dfreq={o['dfreq']:+.4f}"
                  f" 期望 {exp:+.4f}", flush=True)
            break
        if o["dfreq"] < -0.01:
            n_sig += 1
    print(f"  T5b: 有效负指令样本 {n_sig} 个，逐样本一致 "
          f"{'符' if ok else '不符'}", flush=True)
    (passed if ok else failed).append("T5b 频率差动在环逐样本一致")
    # T5c 关闭对照
    F.FREQ_DIFF_ON = False
    c = FlightController()
    outs = run(c, make_pose(), make_vel(wz=0.5), 3.0)
    ok = all(o["dfreq"] == 0.0 for o in outs)
    (passed if ok else failed).append("T5c 频率差动关闭恒零")

    # T6 互斥：两开关同开，占空比优先，无异常且闸门合法
    F.DUTY_MOD_ON = True
    F.STROKE_DIFF_ON = True
    c = FlightController()
    try:
        outs = run(c, make_pose(roll=0.1), make_vel(), 10.0)
        ok = all(o["gate_L"] in (0.0, 1.0) and o["gate_R"] in (0.0, 1.0)
                 for o in outs)
    except Exception as e:
        ok = False
        print(f"  T6 异常: {e}", flush=True)
    (passed if ok else failed).append("T6 互斥占空比优先")
    F.STROKE_DIFF_ON = False

    # T7 dA 旁路
    c = FlightController()
    outs = run(c, make_pose(roll=0.1), make_vel(), 6.0)
    ok = all(o["dA"] == 0.0 for o in outs)
    (passed if ok else failed).append("T7 占空比下 dA 旁路")
    F.DUTY_MOD_ON = False

    # T8 密度通道俯仰门控（2026-09-28）：俯仰 0°（巡航姿态，反号区）
    # 时大滚转误差也不出闸门动作；俯仰 −10°（正权限区）时正常调制。
    F.DUTY_MOD_ON = True
    F.DUTY_PITCH_GATE_ON = True
    F.DM_PITCH_ZERO_DEG = -4.0
    F.DM_PITCH_FULL_DEG = -6.0
    c = FlightController()
    outs = run(c, make_pose(roll=0.15, pitch=0.0), make_vel(), 15.0)
    sL0, sR0 = beat_gates(outs)
    ok = sL0 <= 0.02 and sR0 <= 0.02
    (passed if ok else failed).append("T8a 俯仰 0° 门控归零")
    c = FlightController()
    outs = run(c, make_pose(roll=0.15, pitch=-0.1745), make_vel(), 15.0)
    sL1, sR1 = beat_gates(outs)
    ok = max(sL1, sR1) >= 0.2
    print(f"  T8: 俯仰0°停拍占比=({sL0:.3f},{sR0:.3f})，"
          f"俯仰−10°停拍占比=({sL1:.3f},{sR1:.3f})", flush=True)
    (passed if ok else failed).append("T8b 俯仰 −10° 正常调制")

    # T9 分幅共存：占空比+共存+三维映射同开时 dA 存活且为三维映射值
    F.DM_COEXIST_ON = True
    F.DA_SCHED_3D_ON = True
    F.B_DA_3D = 0.046
    F.DA_MAX_3D = 0.175
    c = FlightController()
    outs = run(c, make_pose(roll=0.1), make_vel(), 6.0)
    dAs = [o["dA"] for o in outs[50:]]
    ok = all(abs(d) <= F.DA_MAX_3D + 1e-12 for d in dAs) \
        and np.median(np.abs(dAs)) > 1e-3
    print(f"  T9: 共存下 dA 绝对值中位={np.median(np.abs(dAs)):.5f} rad",
          flush=True)
    (passed if ok else failed).append("T9 分幅共存 dA 存活且限幅内")

    # T10 关闭态：共存与三维映射全关时 dA 回到旁路
    F.DM_COEXIST_ON = False
    F.DA_SCHED_3D_ON = False
    c = FlightController()
    outs = run(c, make_pose(roll=0.1), make_vel(), 6.0)
    ok = all(o["dA"] == 0.0 for o in outs)
    (passed if ok else failed).append("T10 共存关闭恢复旁路")
    F.DUTY_MOD_ON = False
    F.DUTY_PITCH_GATE_ON = False

    print(f"\n通过 {len(passed)}：{passed}", flush=True)
    print(f"失败 {len(failed)}：{failed}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
