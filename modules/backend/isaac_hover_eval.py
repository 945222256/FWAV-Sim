# -*- coding: utf-8 -*-
"""
isaac_hover_eval.py Isaac 后端悬停基准评估器
============================================

运行方式为 python 减号 m modules.backend.isaac_hover_eval。本评估器全程
为纯 Python，不导入任何 Isaac 模块。读取 logs/ident_latest.txt 所指的最
新九十三列高密度数据流 CSV，逐行评估三条判据。退出码取零表示通过，取一
表示失败。已知缺陷 I-025 登记 Isaac 启动脚本退出码不可靠，该缺陷不影响
本评估器，本评估器为普通 Python 进程；在环运行本身的完成性以运行日志内
的收尾里程碑文本为准。

一、判据。下列三条判据与提交 e365142 的 modules/backend/run_hover_bench.py
文件头逐字相同，先于运行冻结，运行后不许修改阈值。

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

二、数据源与簿记口径。

数据流来源为 modules/flight_recorder.py 的 _ident_stream_write，该函数自
二零二六年九月二十日第十轮起逐物理步写入，列布局出自其表头 writerow，共
九十三列。物理步长为 config.sim_config 的 PHYSICS_DT，现行值为一百二十分
之一秒。

升力簿记如下。判据二的升力定义为后端每物理步施加的气动合力竖直合计分
量，与提交 e365142 模拟后端基准文件头冻结的定义逐字一致。数据流的 Fz
列记录气动回调单次触发的施加值，本配置下回调每物理步触发两次，Fz 列为
每步合计量的二分之一；数据流的 Frz 列记录每步合计。两列之比在本运行
全窗的中位数为二点零零零零，与 docs 任务二垂直动量账归因文档定案的点
火率分摊口径互证。评估取 Frz 列为升力。首版评估误取 Fz 列，误读结果与
更正过程留档于 logs/T6X_verdict.md，口径裁决登记为事项 I-027。换算式为
判据二比值等于飞行窗内逐行 Frz 列的算术平均值除以 config.sim_config
的 MASS 乘九点八一。

姿态角簿记如下。姿态角一律由四元数列换算，禁止直读 roll 与 pitch 标签
列，此为项目铁律。数据流的四元数分散落在 qw 列与 qz 列与 qx 列与 qy 列
四处，按表头名取齐后拼为 w x y z 顺序向量，换算函数为
modules.aero_solver 的 quat_to_euler，ZYX 顺序，与模拟后端基准同一函数。

三、场景宣告。本评估器只做判读，不改场景。在环运行的场景气动模型经
modules/sim_env_bridge.py 的 FLAPPER_AERO_MODEL 桥钉为 sane_dickinson 冻结
版，与模拟后端基准的场景钉版一致，钉版理由见 run_hover_bench.py 文件头
第三节。
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys

# 保证以模块方式运行时仓库根目录在导入路径内。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np

import config.sim_config as cfg
from modules.aero_solver import quat_to_euler

# 判据常量区。本区数值与 run_hover_bench.py 同值同义，先冻结后运行，
# 评估器不提供任何覆盖入口。
BENCH_DURATION_S = 60.0
FLIGHT_WINDOW_START_S = 1.0
Z_CRASH_M = 0.2
Z_CEIL_M = 8.0
LIFT_RATIO_MIN = 0.90
ATTITUDE_BAND_RAD = 0.55
TABLE_TICK_S = 1.0
# 判据一的数据流完整性余量：末行时刻距六十秒窗尾不多于五个物理步时视为
# 完整跑满（FLAPPER_MAX_S 截断在步界取整）。
_TAIL_TOL_S = 5.0 * float(cfg.PHYSICS_DT)

# 数据流列名常量。按表头名取列，不按位置取列。
_COL_T = "t"
_COL_FZ = "Fz"
_COL_FRZ = "Frz"
_COL_Z = "z"
_COL_QW = "qw"
_COL_QX = "qx"
_COL_QY = "qy"
_COL_QZ = "qz"
_COL_VX = "vx"
_COL_VY = "vy"
_COL_VZ = "vz"
_COL_FREQ = "freq"
_REQUIRED_COLS = (_COL_T, _COL_FRZ, _COL_Z, _COL_QW, _COL_QX, _COL_QY,
                  _COL_QZ, _COL_VX, _COL_VY, _COL_VZ, _COL_FREQ)


def _fmt_deg(rad: float) -> str:
    """把弧度值格式化为带符号的度数字符串。"""
    return "%+7.2f" % math.degrees(rad)


def _resolve_csv_path() -> str:
    """从 logs/ident_latest.txt 指针解析最新数据流 CSV 的绝对路径。"""
    pointer = os.path.join(cfg.LOG_DIR, "ident_latest.txt")
    with open(pointer, encoding="utf-8") as fh:
        name = fh.read().strip()
    if not name:
        raise RuntimeError("ident_latest.txt 为空，无数据流可评估")
    path = os.path.join(cfg.LOG_DIR, name)
    if not os.path.isfile(path):
        raise RuntimeError("数据流文件不存在：%s" % path)
    return path


def _read_sidecar_meta(csv_path: str) -> dict:
    """读取数据流旁挂的元数据 json，取标注与描述字段。"""
    meta_path = csv_path + ".meta.json"
    try:
        with open(meta_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def evaluate(csv_path: str = None) -> int:
    """对最新数据流逐行评估三条判据并返回退出码。"""
    if csv_path is None:
        csv_path = _resolve_csv_path()
    meta = _read_sidecar_meta(csv_path)

    print("=" * 78)
    print("Isaac 后端悬停基准评估 I-014")
    print("=" * 78)
    print("数据流 %s" % csv_path)
    if meta:
        print("标注 FLAPPER_TAG=%s" % (meta.get("tags") or "(空)"))
        desc = meta.get("description") or "(空)"
        print("描述 %s" % desc)
    print("判据一 存活时长下限 %.1f 秒，坠机线为飞行窗内高度低于 %.2f 米"
          "或高于 %.1f 米或状态非有限" % (BENCH_DURATION_S, Z_CRASH_M,
                                           Z_CEIL_M))
    print("判据二 飞行窗平均升力与重量之比下限 %.2f" % LIFT_RATIO_MIN)
    print("判据三 姿态角偏差带 滚转与俯仰绝对值上限 %.2f 弧度"
          % ATTITUDE_BAND_RAD)
    print("姿态角换算 四元数列 qw qx qy qz 经 quat_to_euler，ZYX 顺序")
    print("升力列 Frz 为每步合计，重量 %.3f 乘 9.81 等于 %.5f 牛"
          % (float(cfg.MASS), float(cfg.MASS) * 9.81))

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = [c for c in _REQUIRED_COLS if c not in header]
        if missing:
            print("表头缺列 %s，无法评估" % ",".join(missing))
            return 1
        rows = list(reader)

    n_rows = len(rows)
    if n_rows == 0:
        print("数据流无数据行，无法评估")
        return 1
    print("数据行 %d 行" % n_rows)

    weight = float(cfg.MASS) * 9.81
    t_crash = None
    crash_reason = ""
    lift_sum = 0.0
    lift_n = 0
    roll_max = 0.0
    pitch_max = 0.0
    attitude_fail_t = None
    t_last = None
    next_tick = TABLE_TICK_S

    print()
    print("判据表 每秒一行，判定逐行执行")
    print("%6s %7s %7s %7s %7s %7s %6s %7s %8s %7s %7s" % (
        "t/s", "z/m", "vx", "vz", "roll/d", "pitch/d", "f/Hz",
        "Frz/N", "L/W均", "|r|m", "|p|m"))

    for row in rows:
        try:
            t = float(row[_COL_T])
            z = float(row[_COL_Z])
            quat = np.asarray([float(row[_COL_QW]), float(row[_COL_QX]),
                               float(row[_COL_QY]), float(row[_COL_QZ])],
                              dtype=float)
            vel = (float(row[_COL_VX]), float(row[_COL_VY]),
                   float(row[_COL_VZ]))
            fz_val = float(row[_COL_FRZ])
            freq_val = float(row[_COL_FREQ])
        except (TypeError, ValueError):
            t_crash = t_last if t_last is not None else 0.0
            crash_reason = "行数据不完整或不可解析"
            break
        t_last = t
        roll, pitch, _yaw = quat_to_euler(quat)
        state_ok = (np.isfinite(z) and np.isfinite(quat).all()
                    and all(np.isfinite(v) for v in vel))
        if not state_ok:
            t_crash = t
            crash_reason = "状态数值非有限"
            break
        if t > FLIGHT_WINDOW_START_S:
            lift_sum += fz_val
            lift_n += 1
            if abs(roll) > roll_max:
                roll_max = abs(roll)
            if abs(pitch) > pitch_max:
                pitch_max = abs(pitch)
            if attitude_fail_t is None and (
                    abs(roll) > ATTITUDE_BAND_RAD
                    or abs(pitch) > ATTITUDE_BAND_RAD):
                attitude_fail_t = t
            if z < Z_CRASH_M:
                t_crash = t
                crash_reason = "高度低于 %.2f 米" % Z_CRASH_M
                break
            if z > Z_CEIL_M:
                t_crash = t
                crash_reason = "高度高于 %.1f 米" % Z_CEIL_M
                break
        if t >= next_tick:
            lift_ratio = lift_sum / max(lift_n, 1) / weight
            print("%6.2f %7.3f %+7.2f %+7.2f %s %s %6.3f %+7.3f %8.4f "
                  "%7.3f %7.3f" % (
                      t, z, vel[0], vel[2], _fmt_deg(roll), _fmt_deg(pitch),
                      freq_val, fz_val, lift_ratio, roll_max, pitch_max))
            next_tick += TABLE_TICK_S

    print()
    print("=" * 78)
    print("逐条裁定")
    if t_crash is None:
        if t_last is not None and t_last >= BENCH_DURATION_S - _TAIL_TOL_S:
            survive = BENCH_DURATION_S
            c1 = survive >= BENCH_DURATION_S
            c1_text = "存活 %.1f 秒，全程无坠机，末行时刻 %.4f 秒" % (
                survive, t_last)
        else:
            survive = t_last if t_last is not None else 0.0
            c1 = False
            c1_text = ("数据流提前终止于 %.4f 秒，未跑满 %.1f 秒窗，"
                       "完成性以运行日志收尾里程碑为准" % (survive,
                                                            BENCH_DURATION_S))
    else:
        survive = t_crash
        c1 = False
        c1_text = "存活 %.4f 秒，坠机原因 %s" % (survive, crash_reason)
    print("判据一 存活时长 %s：%s" % ("通过" if c1 else "不通过", c1_text))
    if lift_n > 0:
        lift_ratio = lift_sum / lift_n / weight
        c2 = lift_ratio >= LIFT_RATIO_MIN
        c2_text = "飞行窗 %d 行平均升力比 %.4f，下限 %.2f" % (
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
        c3_text = ("姿态越限首现于 %.4f 秒，滚转极值 %.4f 弧度，"
                   "俯仰极值 %.4f 弧度" % (attitude_fail_t, roll_max,
                                            pitch_max))
    print("判据三 姿态角偏差带 %s：%s" % ("通过" if c3 else "不通过",
                                           c3_text))
    all_ok = c1 and c2 and c3
    print()
    print("总结论 %s" % ("HOVER BENCH PASS Isaac侧悬停基准通过"
                         if all_ok else "HOVER BENCH FAIL Isaac侧悬停基准未通过"))
    isaac_loaded = [m for m in sys.modules if m.startswith("isaacsim")
                    or m.startswith("omni.")]
    print("Isaac 依赖检查 %s" % ("未加载任何 Isaac 模块"
                                 if not isaac_loaded
                                 else "意外加载 " + ",".join(isaac_loaded)))
    return 0 if all_ok and not isaac_loaded else 1


if __name__ == "__main__":
    sys.exit(evaluate())
