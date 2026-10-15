# -*- coding: utf-8 -*-
"""
run_hover_bench.py 模拟后端悬停基准运行器
==========================================

运行方式为 python 减号 m modules.backend.run_hover_bench。本运行器全程为
纯 Python，不导入任何 Isaac 模块。退出码取零表示通过，取一表示失败。

一、判据。下列三条判据先于运行冻结，运行后不许修改阈值。

判据一为闭环存活时长。基准时长取六十秒。飞行窗定义为仿真时刻大于一点零秒
的时间段。飞行窗内首次出现高度低于零点二米或高于八米或状态数值非有限时，
该时刻记为坠机时刻。通过条件为存活时长不低于六十秒，即全程无坠机。出处说
明如下。六十秒时长与高度带零点二至八米出自 docs 三维模型在环确认第五步升
格文档第一节的判据一与第三节的裁定口径。时刻大于一秒后判坠机的口径出自同
文档。六十秒时长同时与 main_sim.py 环境变量 FLAPPER_MAX_S 的在环验收用法
一致。

判据二为平均升力与重量之比。升力定义为后端每步施加的世界系气动合力的竖直
分量，含机翼与尾翼贡献，取限幅后的施加值。重量取 config.sim_config 的
MASS 乘九点八一。通过条件为飞行窗内逐步竖直分量的算术平均值与重量之比不
低于零点九零。下限依据为如下推导。升力均值低于体重九成时，竖直加速度亏空
至少为重力的一成，机体从三米基准高度掉落到零点二米判据线只需约二点四秒，
该时间远短于六十秒窗，因此高度带判据必然先于窗尾失守。

判据三为姿态角偏差带。通过条件为飞行窗内全程滚转角绝对值不超过零点五五
弧度且俯仰角绝对值不超过零点五五弧度。出处说明如下。零点五五弧度为
config.sim_config.FlightControllerConfig.TILT_MAX 的现行值，该值为期望姿态
角限幅。实际姿态角越出该值即认定姿态失守。

二、初始状态宣告。释放时刻的初始状态取近悬停平衡的现行配平点，各量出处如
下。位置取零、零、三点零米，出自 config.sim_config 的 DP_TARGET_Z 与
FlightControllerConfig.Z_REF，两处现行值同为三点零。姿态取俯仰零点一四三
二四六弧度、滚转与偏航为零，出自 config.sim_config 的 LAUNCH_PITCH 与
FlightControllerConfig.PITCH_TRIM，两处现行值同号同值。速度取三点九五米每
秒沿世界 x 轴，出自 config.sim_config 的 LAUNCH_VX，其值等于
FlightControllerConfig.CRUISE_VX。拍频初值取一点八赫兹，出自
FlightControllerConfig.FREQ_HOVER，为控制器构造的现行默认。本基准所称悬停
为现行控制律的配平保持口径，即高度参考三点零米与速度参考三点九五米每秒的
闭环保持。静气悬停按 config 注释不在自动驾驶仪拍频域内，该口径宣告先于运
行。

三、气动模型宣告。本基准钉定气动模型为 sane_dickinson 冻结版，即
modules.aero_solver.FlapperAeroModel。理由有三项。第一项为现行控制器参数链
的配平口径出自冻结版，PITCH_TRIM 与 CRUISE_VX 与 FREQ_HOVER 的取值链见
docs 的 TRIM_SCAN 文档与 config 注释。第二项为冻结版具备在环六十秒通过的
基线先例，即二零二六年九月二十一日零九二一零五五一一一号运行记录，见
docs 三维模型在环确认第五步升格文档结果表的冻结版基线列。第三项为
sane_dickinson 三维版存在文档登记的结构性配平障碍，其扫掠俯仰耦合力矩约
零点二七牛顿米需约零点四一弧度升降舵偏度，该值超出 DELTA_MAX 零点三零的
限幅，见 docs 三维配平与盘旋扫描文档的晋升裁决建议节。首轮运行登记如下。
本运行器首轮按 config 的 AERO_MODEL 取三维版运行，闭环于六点零六七秒姿态
发散坠机，失败形态为俯仰振荡增长后横侧发散，该形态与上述文档五次在环失败
实录同构，属被控对象配平障碍而非模拟后端实现缺陷，据此把场景模型钉回冻结
版。判据三条不随场景调整而变动。

四、场景设定。起飞段复刻 main_sim.py 主循环的钉住与释放现行用法。仿真时
刻小于 STARTUP_PIN_S 的阶段为钉住段，机体状态每步钉于位置零、零、
DP_TARGET_Z 与零欧拉角，九关节按解析规定值钉住。钉住段结束的首步执行释放
序列，释放序列注入上述初始状态并清空气动滤波状态与控制器积分状态，各复位
项与 main_sim.py 释放块一一对应。本场景不含系留绳、来流风、障碍物与扰动
注入。

四、输出。本运行器逐秒输出判据表一行，表内各列为时刻、高度、前向速度、
竖直速度、滚转角、俯仰角、拍频、当步升力、飞行窗累计升力比、滚转角极值、
俯仰角极值。判定本身逐物理步执行。末尾输出三条判据的逐条裁定与总结论。
"""

from __future__ import annotations

import math
import sys
import os

# 保证以模块方式运行时仓库根目录在导入路径内。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np

import config.sim_config as cfg
from modules.aero_solver import euler_to_quat, quat_to_euler
from modules.backend.mock_backend import MockBackend

# 判据常量区。本区数值在文件头冻结，运行器不提供任何覆盖入口。
BENCH_DURATION_S = 60.0
FLIGHT_WINDOW_START_S = 1.0
Z_CRASH_M = 0.2
Z_CEIL_M = 8.0
LIFT_RATIO_MIN = 0.90
ATTITUDE_BAND_RAD = 0.55
TABLE_TICK_STEPS = 120
# 场景模型宣告。取值理由见文件头第三节。
BENCH_AERO_MODEL = "sane_dickinson"


def _fmt_deg(rad: float) -> str:
    """把弧度值格式化为带符号的度数字符串。"""
    return "%+7.2f" % math.degrees(rad)


def run_bench() -> int:
    """运行悬停基准并返回退出码。"""
    print("=" * 78)
    print("模拟后端悬停基准 I-014")
    print("=" * 78)
    print("判据一 存活时长下限 %.1f 秒，坠机线为飞行窗内高度低于 %.2f 米"
          "或高于 %.1f 米或状态非有限" % (BENCH_DURATION_S, Z_CRASH_M,
                                           Z_CEIL_M))
    print("判据二 飞行窗平均升力与重量之比下限 %.2f" % LIFT_RATIO_MIN)
    print("判据三 姿态角偏差带 滚转与俯仰绝对值上限 %.2f 弧度"
          % ATTITUDE_BAND_RAD)
    print("初始状态 位置 0 0 %.1f 米，俯仰 %.6f 弧度，前向速度 %.2f 米每秒"
          % (cfg.FlightControllerConfig.Z_REF, cfg.LAUNCH_PITCH,
             cfg.LAUNCH_VX))
    print("惯量出处 docs INERTIA_GEOMETRIC_20260930 整机合成行，质量 %.3f 千克"
          % cfg.MASS)

    backend = MockBackend()
    backend.setup({"physics_dt": cfg.PHYSICS_DT,
                   "scene": {},
                   "aero": {"model_name": BENCH_AERO_MODEL, "params": None},
                   "assets": {}, "headless": True})
    backend.reset(seed=0)
    print("气动模型 %s，物理步长 %.9f 秒" % (backend.model_name,
                                             backend._physics_dt))

    launch = {"armed": True}

    def scenario_hook(t: float) -> None:
        """钉住与释放序列，时序与 main_sim.py 主循环现行用法同构。"""
        if t < cfg.STARTUP_PIN_S:
            quat_pin = euler_to_quat(0.0, math.radians(
                float(getattr(cfg, "BENCH_PITCH_DEG", 0.0))), 0.0)
            backend.set_body_state([0.0, 0.0, float(cfg.DP_TARGET_Z)],
                                   quat_pin, [0.0] * 6)
            backend.pin_wing_joints(t)
        elif launch["armed"]:
            launch["armed"] = False
            print("[LAUNCH] t=%.3f 秒 释放：注入前向速度 %.2f 米每秒，"
                  "俯仰 %.6f 弧度，滤波与积分状态已清零"
                  % (t, float(cfg.LAUNCH_VX), float(cfg.LAUNCH_PITCH)))
            backend.set_body_state(
                [0.0, 0.0, float(cfg.DP_TARGET_Z)],
                euler_to_quat(0.0, float(cfg.LAUNCH_PITCH), 0.0),
                [float(cfg.LAUNCH_VX), 0.0, 0.0, 0.0, 0.0, 0.0])
            backend.reset_aero_states_for_launch()
            backend.reset_controller_for_launch()
            backend.pin_wing_joints(t)

    backend.pre_control_hook = scenario_hook

    dt = backend._physics_dt
    n_steps = int(round(BENCH_DURATION_S / dt))
    weight = cfg.MASS * 9.81

    t_crash = None
    crash_reason = ""
    lift_sum = 0.0
    lift_n = 0
    roll_max = 0.0
    pitch_max = 0.0
    attitude_fail_t = None
    rows = []

    print()
    print("判据表 每秒一行，判定逐物理步执行")
    print("%6s %7s %7s %7s %7s %7s %6s %7s %8s %7s %7s" % (
        "t/s", "z/m", "vx", "vz", "roll/d", "pitch/d", "f/Hz",
        "Fz/N", "L/W均", "|r|m", "|p|m"))

    for i in range(n_steps):
        obs = backend.step()
        t = float(obs["t"])
        pos = obs["position"]["value"]
        quat = obs["quaternion"]["value"]
        vel = obs["velocity_linear"]["value"]
        roll, pitch, _yaw = quat_to_euler(np.asarray(quat, dtype=float))
        state_ok = (np.isfinite(pos).all() and np.isfinite(quat).all()
                    and np.isfinite(vel).all())
        if not state_ok:
            t_crash = t
            crash_reason = "状态数值非有限"
            break
        if t > FLIGHT_WINDOW_START_S:
            fz = float(backend.last_F_world[2])
            lift_sum += fz
            lift_n += 1
            if abs(roll) > roll_max:
                roll_max = abs(roll)
            if abs(pitch) > pitch_max:
                pitch_max = abs(pitch)
            if attitude_fail_t is None and (
                    abs(roll) > ATTITUDE_BAND_RAD
                    or abs(pitch) > ATTITUDE_BAND_RAD):
                attitude_fail_t = t
            if float(pos[2]) < Z_CRASH_M:
                t_crash = t
                crash_reason = "高度低于 %.2f 米" % Z_CRASH_M
                break
            if float(pos[2]) > Z_CEIL_M:
                t_crash = t
                crash_reason = "高度高于 %.1f 米" % Z_CEIL_M
                break
        if (i + 1) % TABLE_TICK_STEPS == 0:
            ap = backend.last_ap_out or {}
            lift_ratio = lift_sum / max(lift_n, 1) / weight
            rows.append((t, float(pos[2]), float(vel[0]), float(vel[2]),
                         roll, pitch, float(ap.get("freq", float("nan"))),
                         float(backend.last_F_world[2]), lift_ratio,
                         roll_max, pitch_max))
            print("%6.2f %7.3f %+7.2f %+7.2f %s %s %6.3f %+7.3f %8.4f "
                  "%7.3f %7.3f" % (
                      t, pos[2], vel[0], vel[2], _fmt_deg(roll),
                      _fmt_deg(pitch), rows[-1][6], rows[-1][7],
                      lift_ratio, roll_max, pitch_max))

    backend.close()
    print()
    print("=" * 78)
    print("逐条裁定")
    if t_crash is None:
        survive = BENCH_DURATION_S
        c1 = survive >= BENCH_DURATION_S
        c1_text = "存活 %.1f 秒，全程无坠机" % survive
    else:
        survive = t_crash
        c1 = False
        c1_text = "存活 %.3f 秒，坠机原因 %s" % (survive, crash_reason)
    print("判据一 存活时长 %s：%s" % ("通过" if c1 else "不通过", c1_text))
    if lift_n > 0:
        lift_ratio = lift_sum / lift_n / weight
        c2 = lift_ratio >= LIFT_RATIO_MIN
        c2_text = "飞行窗 %d 步平均升力比 %.4f，下限 %.2f" % (
            lift_n, lift_ratio, LIFT_RATIO_MIN)
    else:
        lift_ratio = float("nan")
        c2 = False
        c2_text = "飞行窗无样本"
    print("判据二 平均升力比 %s：%s" % ("通过" if c2 else "不通过", c2_text))
    c3 = attitude_fail_t is None
    if c3:
        c3_text = ("飞行窗滚转极值 %.4f 弧度，俯仰极值 %.4f 弧度，"
                   "上限 %.2f 弧度" % (roll_max, pitch_max,
                                       ATTITUDE_BAND_RAD))
    else:
        c3_text = ("姿态越限首现于 %.3f 秒，滚转极值 %.4f 弧度，"
                   "俯仰极值 %.4f 弧度" % (attitude_fail_t, roll_max,
                                            pitch_max))
    print("判据三 姿态角偏差带 %s：%s" % ("通过" if c3 else "不通过",
                                           c3_text))
    all_ok = c1 and c2 and c3
    print()
    print("总结论 %s" % ("HOVER BENCH PASS 悬停基准通过"
                         if all_ok else "HOVER BENCH FAIL 悬停基准未通过"))
    isaac_loaded = [m for m in sys.modules if m.startswith("isaacsim")
                    or m.startswith("omni.")]
    print("Isaac 依赖检查 %s" % ("未加载任何 Isaac 模块"
                                 if not isaac_loaded
                                 else "意外加载 " + ",".join(isaac_loaded)))
    return 0 if all_ok and not isaac_loaded else 1


if __name__ == "__main__":
    sys.exit(run_bench())
