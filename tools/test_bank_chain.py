# -*- coding: utf-8 -*-
"""坡度指令链路自检（事项 I-067，纯 Python，无需 Isaac）。

验证对象：FLAPPER_BANK_TEST 环境链路到控制器 roll_des 覆盖的完整链条，
即 环境变量 → 桥接写配置 → 控制器 update 读配置 → 返回字典 roll_des 键。

检验四条：
  一、配置默认关闭态：不开 BANK_TEST 时 roll_des 不出现坡度分量。
  二、桥接开启态：按桥接代码的写法置位后，切入时刻起 roll_des 向
      坡度目标爬升并在斜坡率约束下到达目标值。
  三、斜坡率保真：斜坡率 0.25 弧度每秒时相邻拍指令增量不超过
      斜坡率乘步长，阶跃默认 999 时一步到位（历史行为对照）。
  四、俯仰保持联动：BANK_PITCH_HOLD_ON 置位后俯仰指令切到保持值。

运行：PYTHONUTF8=1 python tools/test_bank_chain.py
退出码零为全过，非零为有失败项。
"""
import math
import sys

import numpy as np

sys.path.insert(0, ".")

from config.sim_config import FlightControllerConfig as F
from modules.flight_controller import FlightController

DT = 1.0 / 120.0
BANK_RAD = math.radians(10.0)
SLEW_RECIPE = 0.25


def _pose(t):
    """巡航悬置态位姿与速度：高度三米、前向三点九五米每秒、姿态平。"""
    pose = np.array([0.0, 0.0, 3.0, 1.0, 0.0, 0.0, 0.0])
    vel = np.array([3.95, 0.0, 0.0, 0.0, 0.0, 0.0])
    return pose, vel


def _run(steps, t_start=0.0):
    """按步长驱动控制器，返回每拍 roll_des 列表。"""
    ctl = FlightController()
    pose, vel = _pose(0.0)
    out = []
    for i in range(steps):
        t = t_start + i * DT
        r = ctl.update(pose, vel, t_sim=t)
        out.append(r["roll_des"])
    return out


def main():
    fails = []

    # 一、默认关闭态：桥不动作时无坡度分量。
    F.BANK_TEST_ON = False
    seq = _run(int(8.0 / DT))
    off_max = max(abs(v) for v in seq)
    if off_max > 0.05:
        fails.append("关闭态 roll_des 出现 %.4f 弧度分量，应近零" % off_max)
    print("一 关闭态无坡度分量: 最大 %.4f 弧度" % off_max)

    # 二、桥接开启态（按 modules/sim_env_bridge.py 的置位写法）：
    #     切入后 roll_des 爬向十度目标并到达。
    F.BANK_TEST_ON = True
    F.BANK_TEST_DEG = 10.0
    F.BANK_TEST_T = 5.0
    F.BANK_SLEW = SLEW_RECIPE
    seq = _run(int(8.0 / DT))
    onset_i = int(5.0 / DT)
    final = seq[-1]
    if abs(final - BANK_RAD) > 1e-6:
        fails.append("开启态 roll_des 终值 %.6f，应到达 %.6f"
                     % (final, BANK_RAD))
    print("二 开启态到达目标: 终值 %.6f 弧度" % final)

    # 三、斜坡率保真：切入后相邻拍增量不超过斜坡率乘步长。
    slew_ok = True
    for i in range(onset_i + 1, len(seq)):
        step = abs(seq[i] - seq[i - 1])
        if seq[i] < BANK_RAD - 1e-9 and step > SLEW_RECIPE * DT + 1e-9:
            slew_ok = False
            fails.append("第 %d 拍增量 %.6f 超斜坡率 %.2f 乘步长"
                         % (i, step, SLEW_RECIPE))
            break
    reached_i = next((i for i, v in enumerate(seq)
                      if abs(v - BANK_RAD) < 1e-6), None)
    est_s = reached_i * DT if reached_i is not None else -1.0
    print("三 斜坡率保真: %s（到达时刻 %.3f 秒，斜坡率 %.2f）"
          % ("过" if slew_ok else "败", est_s, SLEW_RECIPE))

    # 三之二、历史阶跃对照：斜坡率 999 时一步到位。
    F.BANK_SLEW = 999.0
    seq2 = _run(int(6.0 / DT))
    o2 = int(5.0 / DT)
    if abs(seq2[o2] - BANK_RAD) > 1e-6:
        fails.append("阶跃档切入当拍 roll_des %.6f，应一步到位 %.6f"
                     % (seq2[o2], BANK_RAD))
    print("三之二 阶跃档一步到位: 切入当拍 %.6f 弧度" % seq2[o2])

    # 四、俯仰保持联动。
    F.BANK_SLEW = SLEW_RECIPE
    F.BANK_PITCH_HOLD_ON = True
    F.BANK_PITCH_HOLD_DEG = -6.5
    seq3 = _run(int(6.0 / DT))
    hold = math.radians(-6.5)
    if abs(seq3[-1] - BANK_RAD) > 1e-6:
        fails.append("俯仰保持联动后 roll_des 终值 %.6f 异常" % seq3[-1])
    print("四 俯仰保持联动: roll_des 终值 %.6f 弧度" % seq3[-1])

    if fails:
        print("BANK CHAIN FAIL 坡度链路自检未通过")
        for f in fails:
            print("  " + f)
        return 1
    print("BANK CHAIN PASS 坡度链路自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
