# -*- coding: utf-8 -*-
"""
bench_runner.py 能力基准运行器（事项 I-016）
===========================================

运行方式（三种，互斥）：

  PYTHONUTF8=1 python -m modules.backend.bench_runner --fast
      只跑快档。快档为契约自检加悬停模拟后端基准，全部为纯 Python，
      十几秒完成，不启动 Isaac。

  PYTHONUTF8=1 python -m modules.backend.bench_runner --all
      跑判据文件中全部 active 科目。

  PYTHONUTF8=1 python -m modules.backend.bench_runner --subject cruise
      只跑一个科目，科目名取判据文件的 subjects 键。

一、判据来源。全部判据与阈值一律读自 config/bench_criteria.json，
本文件不抄写任何判据阈值数字。判据文件的结构约定为：顶层含
schema_version、item、frozen_at、rule、subjects、handoff_note；
subjects 含 hover、cruise、turn、disturb、gap 五键；每科含 name_zh、
status、duration_s、scenario、criteria；每条 criterion 含 id、text、
threshold、source。状态为 proposed 的科目直接跳过并记 skipped。

二、科目执行方式。

  悬停（hover）：在进程内调用 modules/backend/run_hover_bench.py 的
  run_bench 函数，捕获其控制台输出，从中解析存活时长、平均升力比、
  滚转角极值、俯仰角极值四项实测数值，再按判据文件的阈值逐条判定。
  同时把 run_hover_bench 的冻结常量与判据文件阈值做一致性核对，不一致
  时在结果中登记警告。

  巡航（cruise）、盘旋（turn）、抗扰（disturb）：Isaac 在环科目。每科
  启动前先检测是否已有 Isaac 进程在运行，检测到即记 busy 并跳过该科，
  绝不并行启动第二个实例，也绝不结束他人进程。环境变量为标准锁
  （FLAPPER_POST_STEP_CB=1 FLAPPER_PHYS_CPU=1 FLAPPER_IID_LOCK=1
  FLAPPER_OBSTACLE_SEED=42 FLAPPER_OBSTACLE_COUNT=0）加标注
  （FLAPPER_TAG=BENCH_科目名，FLAPPER_DESC 写明 I-016 基准运行）加
  判据文件 scenario 内的开关清单，另设 FLAPPER_HEADLESS=1、
  FLAPPER_FLIGHT_LOG=1、FLAPPER_MAX_S=科目时长。启动命令为
  g:/Playground/isaac_sim_5.1.0/python.bat main_sim.py。已知缺陷：
  Isaac 启动脚本退出码不可靠，完成性以运行日志内的收尾里程碑文本为准
  （达到 FLAPPER_MAX_S 的正常收尾退出行）。数据评估读取
  logs/ident_latest.txt 指向的九十三列数据流，先核对旁挂元数据的标注
  是否属于本次运行，再逐行评估。升力一律取 Frz 列（每步合计），Fz 列
  是单次触发值为其一半，不采用；姿态角一律由四元数列换算，禁止直读
  姿态标签列。

  窄缝（gap）：状态为 proposed，直接跳过并记录。

三、巡航科的场景落位（事项 I-016 接线后）。判据文件巡航科的场景要求按
三维模型在环确认文档第六跑第一节把二十处配置值重新落位。其中十九处为
配置键，经环境变量桥在运行时落位：映射表 CRUISE_SCENARIO_CONFIG（本
文件）加 modules/sim_env_bridge.py 的波形桥、场景桥与力模型补推；另一
处为 config 注释口径改动，非配置键，无环境变量可落位，运行时登记备注。
判据文件 scenario 显式冻结的五个值与映射表做一致性核对，不一致即拒绝
运行。场景配置值是工作点，不是判据阈值；判据阈值仍一律读判据文件，
本文件不抄写任何判据阈值数字。

四、输出。控制台打印一张科目分数表，并写
logs/bench_scores/<时间戳>_scores.json，内含每科结论、逐条判据的实测
数值与阈值、数据文件路径、运行日志路径、跳过或忙碌原因。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from contextlib import redirect_stdout

# 保证以模块方式运行时仓库根目录在导入路径内。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np

import config.sim_config as cfg
from modules.aero_solver import quat_to_euler

# ---------------------------------------------------------------------------
# 常量区。本区只有路径、名称与运行时长上限等操作性常量，没有任何判据
# 阈值数字；判据阈值一律读自判据文件。
# ---------------------------------------------------------------------------

CRITERIA_PATH = os.path.join(_REPO_ROOT, "config", "bench_criteria.json")
SCORES_DIR = os.path.join(cfg.LOG_DIR, "bench_scores")
ISAAC_PYTHON_BAT = "g:/Playground/isaac_sim_5.1.0/python.bat"
MAIN_SIM_PY = os.path.join(_REPO_ROOT, "main_sim.py")

# 标准锁：与在环验收现行口径一致的环境变量组合。
STANDARD_LOCK = {
    "FLAPPER_POST_STEP_CB": "1",
    "FLAPPER_PHYS_CPU": "1",
    "FLAPPER_IID_LOCK": "1",
    "FLAPPER_OBSTACLE_SEED": "42",
    "FLAPPER_OBSTACLE_COUNT": "0",
    # 第五锁（I-109）：增强确定性开关。审计查明该开关从未开启而分岔
    # 事故为引擎求解器 warm-start 类非确定性，开启后四对双跑零分岔。
    # 随标准锁全量携带，禁止单独关闭。
    "FLAPPER_DET_ON": "1",
}

# Isaac 在环运行的单科墙钟上限，沿用三维在环确认文档登记的二十五分钟
# 外部上限惯例。超时不结束进程，只记录超时并继续后续科目。
ISAAC_RUN_WALL_CAP_S = 1500.0
# 收尾里程碑出现后等待进程自行退出的宽限期，超过即按里程碑判定完成，
# 进程留给操作系统自行收尾，本运行器不结束任何进程。
MILESTONE_EXIT_GRACE_S = 120.0

# 运行日志内的收尾里程碑与崩溃标记文本。
MILESTONE_RUN_CAP = "[RUNCAP]"
MILESTONE_CAP_DONE = "正常收尾退出"
LOOP_CRASH_MARK = "[LOOP-CRASH]"

# Isaac 相关进程名。python.bat 启动的解释器进程名为 kit.exe。
ISAAC_PROC_NAMES = ("kit.exe", "isaac-sim.exe", "IsaacSim.exe",
                    "omni.kit.exe")

# 九十三列数据流中评估所需的列名。按表头名取列，不按位置取列。
COL_T = "t"
COL_Z = "z"
COL_X = "x"
COL_Y = "y"
COL_VX = "vx"
COL_VY = "vy"
COL_VZ = "vz"
COL_WX = "wx"
COL_WY = "wy"
COL_WZ = "wz"
COL_QW = "qw"
COL_QX = "qx"
COL_QY = "qy"
COL_QZ = "qz"
COL_FREQ = "freq"
COL_FRZ = "Frz"
COL_AIL = "ail"
COL_PITCH_DES = "pitch_des"
STREAM_REQUIRED_COLS = (COL_T, COL_Z, COL_X, COL_Y, COL_VX, COL_VY, COL_VZ,
                        COL_WX, COL_WY, COL_WZ, COL_QW, COL_QX, COL_QY,
                        COL_QZ, COL_FREQ, COL_FRZ, COL_AIL, COL_PITCH_DES)

# 数据流末行时刻距窗尾的完整性余量：按 modules/backend/isaac_hover_eval.py
# 的既有口径取五个物理步，用于容纳 FLAPPER_MAX_S 在步界取整与采样节拍
# 造成的末行提前。
_TAIL_TOL_S = 5.0 * float(cfg.PHYSICS_DT)

# 判据文件场景开关的文档缩写与真实环境变量名对照。本表只做名称映射，
# 数值一律来自判据文件。映射依据为 modules/sim_env_bridge.py 的桥接
# 清单与在环运行日志的覆盖回显。
SCENARIO_ENV_NAME_MAP = {
    "BANK_TEST": "FLAPPER_BANK_TEST",
    "DEG": "FLAPPER_BANK_DEG",
    "T": "FLAPPER_BANK_T",
    "SLEW": "FLAPPER_BANK_SLEW",
    "FREQ_CAP": "FLAPPER_BANK_FREQ_CAP",
    "FREQ_FREEZE": "FLAPPER_BANK_FREQ_FREEZE",
    "PHOLD": "FLAPPER_BANK_PHOLD",
    "PHOLD_DEG": "FLAPPER_BANK_PHOLD_DEG",
    "YAW_FF": "FLAPPER_YAW_FF",
    "DIR": "FLAPPER_YAW_FF_DIR",
    "ADAPT": "FLAPPER_YAW_FF_ADAPT",
    "COORD_KB": "FLAPPER_COORD_KB",
    "KI": "FLAPPER_COORD_KI",
    "I_MAX": "FLAPPER_COORD_I_MAX",
    "YAW_DAMP": "FLAPPER_YAW_DAMP",
    "ROLL_KI": "FLAPPER_ROLL_KI",
    "DA_LPF_TAU": "FLAPPER_DA_LPF_TAU",
    "ROLL_LEAD_TD": "FLAPPER_ROLL_LEAD_TD",
    "STAGES": "FLAPPER_ROLL_LEAD_STAGES",
    "PITCH_LEAD_TD": "FLAPPER_PITCH_LEAD_TD",
    "ATT_QUAT_ERR": "FLAPPER_ATT_QUAT_ERR",
    "WING_MIX": "FLAPPER_WING_MIX",
    "SIDESLIP_PULSE": "FLAPPER_SIDESLIP_PULSE",
    "PULSE_T": "FLAPPER_SIDESLIP_PULSE_T",
    "PULSE_DVY": "FLAPPER_SIDESLIP_PULSE_DVY",
}

# 巡航科二十处场景配置的落位表（事项 I-016 巡航科目场景接线）。数值
# 出处为 docs/三维模型在环确认_第五步升格.md 第六跑 5e 第一节
# （300-305 行）：清单 v2 十七处（幅值 30 档口径）加任务二保调度列三
# 增益。二十处中“速度验收带注释口径 4.5±1.0→3.5±1.0”是 config 注释
# 行改动，不是配置键，无环境变量可落位，运行时登记备注。本表为场景
# 配置值（工作点），不是判据阈值；判据阈值仍一律读判据文件。
# 判据文件 scenario 中显式冻结的五个值（launch_vx_m_per_s、
# launch_pitch_rad、pitch_trim_rad、freq_hover_hz、aero_model）与本表
# 经 CRUISE_SCENARIO_CROSSCHECK 核对，不一致即拒绝运行，防止两处
# 冻结源漂移。桥接实现见 modules/sim_env_bridge.py（波形桥与场景桥）。
# 2026-10-02 裁决十重定锚：落位表随判据文件场景锚同步改为现行 config
# 默认工作点（几何法新惯量六十秒巡航配置族，现行 config 读数核对一致）。
# 旧表（第六跑 5e 二十处）平行保留永不删除，见 CRUISE_SCENARIO_CONFIG_OLD。
# 本表为场景配置值（工作点），不是判据阈值；判据阈值仍一律读判据文件。
# 判据文件 scenario 中显式冻结的五个值（launch_vx_m_per_s、
# launch_pitch_rad、pitch_trim_rad、freq_hover_hz、aero_model）与本表
# 经 CRUISE_SCENARIO_CROSSCHECK 核对，不一致即拒绝运行，防止两处
# 冻结源漂移。桥接实现见 modules/sim_env_bridge.py（波形桥与场景桥）。
CRUISE_SCENARIO_CONFIG = {
    "FLAPPER_WAVE_FREQ": "1.8",        # FlapKinematicsConfig.FREQUENCY→1.8
    "FLAPPER_WAVE_MEAN": "19.8105",    # FlapKinematicsConfig.MEAN→19.8105
    "FLAPPER_WAVE_SPLIT": "0.176",     # FlapKinematicsConfig.SPLIT→0.176
    "FLAPPER_WAVE_LAG": "-0.0674",     # FlapKinematicsConfig.PITCH_LAG_S→−0.0674
    "FLAPPER_WAVE_AMP": "17.73",       # FlapKinematicsConfig.AMPLITUDE→17.73
    "FLAPPER_WAVE_ALPHA": "32.3169",   # FlapKinematicsConfig.ALPHA_SET→32.3169
    "FLAPPER_AERO_MODEL": "sane_dickinson_3d",   # AERO_MODEL
    "FLAPPER_DPFREQ_NOMINAL": "1.8",   # DP_FREQ_NOMINAL→1.8
    "FLAPPER_DPFREQ_MIN": "1.0",       # DP_FREQ_MIN→1.0
    "FLAPPER_WING_ROT_DAMP": "0.005",  # AeroModelConfig.WING_ROT_DAMP→0.005
    "FLAPPER_LAUNCH_VX": "3.95",       # LAUNCH_VX→3.95
    "FLAPPER_LAUNCH_PITCH": "-0.143246",  # LAUNCH_PITCH→−0.143246
    "FLAPPER_FREQ_HOVER": "1.8",       # FlightControllerConfig.FREQ_HOVER→1.8
    "FLAPPER_CRUISE_VX": "3.95",       # FlightControllerConfig.CRUISE_VX→3.95
    "FLAPPER_TILT_MAX": "0.55",        # FlightControllerConfig.TILT_MAX→0.55
    "FLAPPER_PITCH_TRIM": "-0.143246",  # FlightControllerConfig.PITCH_TRIM→−0.143246
    "FLAPPER_KP_A": "2.6",             # KP_A 现行默认（角度外环）
    "FLAPPER_KI_A": "0.15",            # KI_A 现行默认（俯仰误差积分）
    "FLAPPER_PITCH_DAMP": "0.3",       # PITCH_DAMP 现行默认（俯仰角速率阻尼）
}

# 旧落位表（第六跑 5e 二十处，2026-09-30 冻结，裁决十重锚后作废，
# 历史注记平行保留永不删除）。
CRUISE_SCENARIO_CONFIG_OLD = {
    "FLAPPER_WAVE_FREQ": "1.25",       # FlapKinematicsConfig.FREQUENCY→1.25
    "FLAPPER_WAVE_MEAN": "20.0",       # FlapKinematicsConfig.MEAN→20.0
    "FLAPPER_WAVE_SPLIT": "0.75",      # FlapKinematicsConfig.SPLIT→0.75
    "FLAPPER_WAVE_LAG": "-0.04",       # FlapKinematicsConfig.PITCH_LAG_S→−0.04
    "FLAPPER_WAVE_AMP": "30.0",        # FlapKinematicsConfig.AMPLITUDE→30.0
    "FLAPPER_WAVE_ALPHA": "35.0",      # FlapKinematicsConfig.ALPHA_SET→35.0
    "FLAPPER_AERO_MODEL": "sane_dickinson_3d",   # AERO_MODEL
    "FLAPPER_DPFREQ_NOMINAL": "1.25",  # DP_FREQ_NOMINAL→1.25
    "FLAPPER_DPFREQ_MIN": "1.1",       # DP_FREQ_MIN→1.1
    "FLAPPER_WING_ROT_DAMP": "0.005",  # AeroModelConfig.WING_ROT_DAMP→0.005
    "FLAPPER_LAUNCH_VX": "3.5",        # LAUNCH_VX→3.5
    "FLAPPER_LAUNCH_PITCH": "0.374",   # LAUNCH_PITCH→0.374
    "FLAPPER_FREQ_HOVER": "1.25",      # FlightControllerConfig.FREQ_HOVER→1.25
    "FLAPPER_CRUISE_VX": "3.5",        # FlightControllerConfig.CRUISE_VX→3.5
    "FLAPPER_TILT_MAX": "0.55",        # FlightControllerConfig.TILT_MAX→0.55
    "FLAPPER_PITCH_TRIM": "0.374",     # FlightControllerConfig.PITCH_TRIM→0.374
    "FLAPPER_KP_A": "0.061",           # KP_A 1.2→0.061（任务二保调度列）
    "FLAPPER_KI_A": "0.005",           # KI_A 0.10→0.005（任务二保调度列）
    "FLAPPER_PITCH_DAMP": "0.071",     # PITCH_DAMP 2.0→0.071（任务二保调度列）
}

# 判据文件 cruise.scenario 显式键与落位表环境变量的对照。
CRUISE_SCENARIO_CROSSCHECK = {
    "launch_vx_m_per_s": "FLAPPER_LAUNCH_VX",
    "launch_pitch_rad": "FLAPPER_LAUNCH_PITCH",
    "pitch_trim_rad": "FLAPPER_PITCH_TRIM",
    "freq_hover_hz": "FLAPPER_FREQ_HOVER",
    "aero_model": "FLAPPER_AERO_MODEL",
}

# 判据文件 subjects 的固定键序。
SUBJECT_ORDER = ("hover", "cruise", "turn", "disturb", "gap")


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _fmt(v, nd=4):
    """把数值格式化为定长字符串，非数值原样返回。"""
    if isinstance(v, float):
        if not math.isfinite(v):
            return "无值"
        return ("%." + str(nd) + "f") % v
    return str(v)


def _now_stamp():
    """返回用于文件名的时间戳字符串。"""
    return time.strftime("%Y%m%d_%H%M%S")


def _criterion_label(crit_id, default):
    """返回判据的短标签。巡航与盘旋等科在判据文件里的 text 本身已短，
    悬停科的 text 为长段，此处为控制台表格给出统一短标签。"""
    labels = {
        "H1": "闭环存活时长",
        "H2": "平均升力与重量之比",
        "H3": "姿态角偏差带",
        "C1": "六十秒存活",
        "C2": "高度带不失控",
        "C3": "拍频稳邻域不贴保护带",
        "C4": "俯仰指令不持续顶限幅",
        "C5": "末段高度变化率收敛",
        "T1": "存活与高度带",
        "T2": "坡度跟踪",
        "T3": "切入掉高",
        "T4": "转弯率偏差",
        "T5": "扰动项（仅抗扰科评）",
        "D1": "脉冲后不触地与峰值记录",
        "G1": "穿越存活与高度带",
        "G2": "缝隙穿越不触障",
        "G3": "穿越窗姿态角带",
        "G4": "穿越窗高度带",
    }
    return labels.get(crit_id, default)


# ---------------------------------------------------------------------------
# 判据文件读取与结构自检
# ---------------------------------------------------------------------------

def load_criteria():
    """读取判据文件并做结构自检。

    返回二元组（判据数据或 None，问题清单）。问题清单非空时判据数据
    视为不可用，任何科目不得开始。
    """
    problems = []
    if not os.path.isfile(CRITERIA_PATH):
        return None, ["判据文件不存在：%s" % CRITERIA_PATH]
    try:
        with open(CRITERIA_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, ["判据文件不可解析：%s" % exc]

    for key in ("schema_version", "item", "frozen_at", "rule", "subjects",
                "handoff_note"):
        if key not in data:
            problems.append("判据文件顶层缺字段 %s" % key)
    subjects = data.get("subjects")
    if not isinstance(subjects, dict):
        problems.append("判据文件 subjects 不是对象")
        return data, problems
    for key in SUBJECT_ORDER:
        if key not in subjects:
            problems.append("subjects 缺科目 %s" % key)
    for key, subj in subjects.items():
        if not isinstance(subj, dict):
            problems.append("科目 %s 不是对象" % key)
            continue
        for field in ("name_zh", "status", "duration_s", "scenario",
                      "criteria"):
            if field not in subj:
                problems.append("科目 %s 缺字段 %s" % (key, field))
        if subj.get("status") not in ("active", "proposed"):
            problems.append("科目 %s 的 status 取值异常：%s"
                            % (key, subj.get("status")))
        criteria = subj.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            problems.append("科目 %s 的 criteria 不是非空列表" % key)
            continue
        for crit in criteria:
            if not isinstance(crit, dict):
                problems.append("科目 %s 有判据不是对象" % key)
                continue
            for field in ("id", "text", "threshold", "source"):
                if field not in crit:
                    problems.append("科目 %s 判据 %s 缺字段 %s"
                                    % (key, crit.get("id"), field))
    return data, problems


def _extract_flight_window_start(crit_threshold):
    """从存活判据的阈值中取飞行窗起点秒数。

    悬停科的键为 flight_window_start_s；巡航科藏于 crash_def 文本中的
    “t>1 s 后”表述。两处都取不到时返回 None。
    """
    if not isinstance(crit_threshold, dict):
        return None
    if isinstance(crit_threshold.get("flight_window_start_s"), (int, float)):
        return float(crit_threshold["flight_window_start_s"])
    crash_def = crit_threshold.get("crash_def")
    if isinstance(crash_def, str):
        m = re.search(r"t>\s*([0-9.]+)\s*s", crash_def)
        if m:
            return float(m.group(1))
    return None


def _missing_keys(thr, keys):
    """返回阈值字典中缺失的数值键清单。"""
    if not isinstance(thr, dict):
        return list(keys)
    return [k for k in keys
            if not isinstance(thr.get(k), (int, float))
            and not isinstance(thr.get(k), (list))]


def _not_implemented(crit, detail):
    """构造一条无法判定的判据结果。"""
    cid = crit.get("id", "?")
    return {
        "id": cid,
        "label": _criterion_label(cid, cid),
        "text": crit.get("text", ""),
        "threshold": crit.get("threshold"),
        "status": "not_implemented",
        "pass": None,
        "measured": {},
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Isaac 进程检测
# ---------------------------------------------------------------------------

def detect_isaac_processes():
    """检测是否已有 Isaac 相关进程在运行。

    返回二元组（进程信息列表，检测方式说明）。检测到任何可执行文件
    路径含 isaac 字样的相关进程名即视为忙碌。优先用 PowerShell 查询
    进程的可执行文件路径；查询失败时退回 tasklist 按进程名判断。
    """
    filt = " OR ".join("Name='%s'" % n for n in ISAAC_PROC_NAMES)
    query = (
        "Get-CimInstance Win32_Process -Filter \"%s\" | "
        "Where-Object { $_.ExecutablePath -match 'isaac' } | "
        "Select-Object Name,ProcessId,ExecutablePath | ConvertTo-Json "
        "-Compress" % filt)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", query],
            capture_output=True, text=True, timeout=60)
        text = (proc.stdout or "").strip()
        if not text:
            return [], "PowerShell 进程路径查询，无匹配进程"
        info = json.loads(text)
        if isinstance(info, dict):
            info = [info]
        return info, "PowerShell 进程路径查询"
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    # 退路：按进程名查表，无法核对路径，命中即按忙碌处理。
    try:
        proc = subprocess.run(["tasklist", "/FO", "CSV"],
                              capture_output=True, text=True, timeout=60)
        hits = []
        for name in ISAAC_PROC_NAMES:
            if name.lower() in (proc.stdout or "").lower():
                hits.append({"Name": name, "ProcessId": "?",
                             "ExecutablePath": "(未核对路径)"})
        return hits, "tasklist 进程名查询（路径核对不可用的退路）"
    except (OSError, subprocess.SubprocessError) as exc:
        return [{"Name": "检测失败", "ProcessId": "?",
                 "ExecutablePath": str(exc)}], "进程检测本身失败，按忙碌处理"


# ---------------------------------------------------------------------------
# 快档第一项：契约结构自检（tools/test_contracts.py）
# ---------------------------------------------------------------------------

def run_contract_check():
    """运行四个抽象基类的契约结构自检，返回是否通过。"""
    print("=" * 78)
    print("快档步骤一 契约结构自检")
    print("=" * 78)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    code = subprocess.call(
        [sys.executable, os.path.join(_REPO_ROOT, "tools",
                                      "test_contracts.py")],
        cwd=_REPO_ROOT, env=env)
    ok = code == 0
    print("契约自检退出码 %d，%s" % (code, "通过" if ok else "未通过"))
    return ok


# ---------------------------------------------------------------------------
# 悬停科：进程内调用 run_hover_bench 并解析实测数值
# ---------------------------------------------------------------------------

def run_hover_mock():
    """在进程内运行模拟后端悬停基准，返回输出文本与退出码。"""
    from modules.backend import run_hover_bench
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = run_hover_bench.run_bench()
    return buf.getvalue(), code


def parse_hover_output(text):
    """从 run_hover_bench 的控制台输出解析四项实测数值。

    返回字典，键为 survive_s、lift_ratio、roll_max_rad、pitch_max_rad，
    解析不到的键不在字典中。
    """
    measured = {}
    m = re.search(r"存活 ([0-9.]+) 秒", text)
    if m:
        measured["survive_s"] = float(m.group(1))
    m = re.search(r"步平均升力比 ([0-9.]+)", text)
    if m:
        measured["lift_ratio"] = float(m.group(1))
    m = re.search(r"滚转极值 ([0-9.]+) 弧度", text)
    if m:
        measured["roll_max_rad"] = float(m.group(1))
    m = re.search(r"俯仰极值 ([0-9.]+) 弧度", text)
    if m:
        measured["pitch_max_rad"] = float(m.group(1))
    return measured


def judge_hover(criteria, measured):
    """按判据文件的阈值逐条判定悬停科三判据。

    criteria 为判据文件悬停科的 criteria 列表；measured 为解析出的实测
    数值字典。返回二元组（逐条结果列表，一致性警告清单）。
    """
    from modules.backend import run_hover_bench as rhb
    results = []
    # run_hover_bench 冻结常量与判据文件阈值的一致性核对。
    consistency = []
    for bench_key, bench_val, crit_id, file_key in (
            ("BENCH_DURATION_S", rhb.BENCH_DURATION_S, "H1",
             "survive_min_s"),
            ("Z_CRASH_M", rhb.Z_CRASH_M, "H1", "z_crash_m"),
            ("Z_CEIL_M", rhb.Z_CEIL_M, "H1", "z_ceil_m"),
            ("FLIGHT_WINDOW_START_S", rhb.FLIGHT_WINDOW_START_S, "H1",
             "flight_window_start_s"),
            ("LIFT_RATIO_MIN", rhb.LIFT_RATIO_MIN, "H2", "lift_ratio_min"),
            ("ATTITUDE_BAND_RAD", rhb.ATTITUDE_BAND_RAD, "H3",
             "roll_abs_max_rad"),
            ("ATTITUDE_BAND_RAD", rhb.ATTITUDE_BAND_RAD, "H3",
             "pitch_abs_max_rad")):
        crit = next((c for c in criteria if c.get("id") == crit_id), None)
        file_val = (crit or {}).get("threshold", {}).get(file_key)
        if isinstance(file_val, (int, float)) \
                and abs(float(bench_val) - float(file_val)) > 1e-9:
            consistency.append(
                "run_hover_bench 常量 %s=%s 与判据文件 %s.%s=%s 不一致"
                % (bench_key, bench_val, crit_id, file_key, file_val))
    if consistency:
        print("警告 悬停基准常量与判据文件阈值存在不一致：")
        for line in consistency:
            print("  " + line)

    for crit in criteria:
        cid = crit.get("id", "?")
        thr = crit.get("threshold") or {}
        res = {
            "id": cid,
            "label": _criterion_label(cid, cid),
            "text": crit.get("text", ""),
            "threshold": thr,
            "status": "fail",
            "pass": False,
            "measured": {},
            "detail": "",
        }
        if "survive_min_s" in thr:
            val = measured.get("survive_s")
            res["measured"] = {"survive_s": val}
            if val is None:
                res["detail"] = "未能从悬停基准输出解析存活时长"
            else:
                ok = val >= float(thr["survive_min_s"])
                res["pass"] = ok
                res["status"] = "pass" if ok else "fail"
                res["detail"] = "存活 %.3f 秒，阈值不低于 %s 秒" % (
                    val, thr["survive_min_s"])
        elif "lift_ratio_min" in thr:
            val = measured.get("lift_ratio")
            res["measured"] = {"lift_ratio": val}
            if val is None:
                res["detail"] = "未能从悬停基准输出解析平均升力比（可能飞行窗无样本）"
            else:
                ok = val >= float(thr["lift_ratio_min"])
                res["pass"] = ok
                res["status"] = "pass" if ok else "fail"
                res["detail"] = "飞行窗平均升力比 %.4f，阈值不低于 %s" % (
                    val, thr["lift_ratio_min"])
        elif "roll_abs_max_rad" in thr:
            roll = measured.get("roll_max_rad")
            pitch = measured.get("pitch_max_rad")
            res["measured"] = {"roll_max_rad": roll,
                               "pitch_max_rad": pitch}
            if roll is None or pitch is None:
                res["detail"] = "未能从悬停基准输出解析姿态角极值"
            else:
                ok = (roll <= float(thr["roll_abs_max_rad"])
                      and pitch <= float(thr["pitch_abs_max_rad"]))
                res["pass"] = ok
                res["status"] = "pass" if ok else "fail"
                res["detail"] = ("滚转极值 %.4f 弧度，俯仰极值 %.4f 弧度，"
                                 "阈值分别不大于 %s 与 %s 弧度"
                                 % (roll, pitch, thr["roll_abs_max_rad"],
                                    thr["pitch_abs_max_rad"]))
        else:
            res["status"] = "not_implemented"
            res["pass"] = None
            res["detail"] = "判据阈值结构未在悬停判定器登记，无法判定"
        results.append(res)
    return results, consistency


def judge_hover_from_stream(rows, criteria, run_completed, data_file):
    """按判据文件阈值逐条判定悬停科三判据（数据流驱动，事项 I-186 返修
    v2 生产侧）。

    与 judge_hover 同一冻结阈值来源（判据文件），实测改自本次实际读取的
    数据流：存活取高度带与状态有限性（时刻窗自 flight_window_start_s），
    升力比取飞行窗 Frz 每步合计列算术平均与重量（cfg.MASS 乘 9.81）之
    比，姿态带取四元数换算的滚转与俯仰绝对值极值（load_stream 口径，
    禁止直读姿态标签列）。列缺失或不可解析由 load_stream 问题清单体现，
    对应判据记 no_data 不猜值。
    """
    results = []
    for crit in criteria:
        cid = crit.get("id", "?")
        thr = crit.get("threshold") or {}
        res = {
            "id": cid,
            "label": _criterion_label(cid, cid),
            "text": crit.get("text", ""),
            "threshold": thr,
            "status": "fail",
            "pass": False,
            "measured": {},
            "detail": "",
        }
        if not rows:
            res["status"] = "no_data"
            res["pass"] = None
            res["detail"] = "数据流无有效行，无法判定"
            results.append(res)
            continue
        t0 = _extract_flight_window_start({"threshold": thr}) or 1.0
        # 飞行窗为严格 t>t0（与 run_hover_bench 的 t>FLIGHT_WINDOW_START_S
        # 同口径）；窗只限定判坠机与统计范围，不从存活时长里再减窗起点。
        win = [r for r in rows if r["t"] > t0]
        if not win:
            res["status"] = "no_data"
            res["pass"] = None
            res["detail"] = "飞行窗（t>%s s）内无数据行" % t0
            results.append(res)
            continue
        if "survive_min_s" in thr:
            crash_t = None
            crash_reason = None
            z_lo = float(thr.get("z_crash_m", 0.2))
            z_hi = float(thr.get("z_ceil_m", 8.0))
            need_finite = bool(thr.get("state_finiteness_required", True))
            for r in win:
                z = r["z"]
                if z < z_lo:
                    crash_t, crash_reason = r["t"], "高度低于 %.2f 米" % z_lo
                    break
                if z > z_hi:
                    crash_t, crash_reason = r["t"], "高度高于 %.1f 米" % z_hi
                    break
                if need_finite and _finite_states([r]) is not None:
                    crash_t, crash_reason = r["t"], "状态数值非有限"
                    break
            # 冻结存活计时（事项 I-186 返修 v3，与 run_hover_bench 一致）：
            # 无坠机时存活为实际记录覆盖时长（完整 60 s 覆盖即 60 s，不从
            # 终点减飞行窗起点，也不按预期时长补齐）；坠机时存活取原仿真
            # 时刻（绝对时刻，不减窗起点）。
            if crash_t is not None:
                survive = crash_t
                ok = False
                res["detail"] = ("t=%.3f s %s，存活 %.3f 秒（原仿真时刻）"
                                 % (crash_t, crash_reason, survive))
            else:
                t_first = rows[0]["t"]
                t_end = rows[-1]["t"]
                survive = t_end - t_first
                ok = survive >= float(thr["survive_min_s"])
                res["detail"] = ("存活 %.3f 秒（实际覆盖 %.3f 至 %.3f 秒，"
                                 "无坠机；数据流 %s），阈值不低于 %s 秒"
                                 % (survive, t_first, t_end, data_file,
                                    thr["survive_min_s"]))
                if not run_completed:
                    res["detail"] = ("数据流未随运行完整收尾，存活按实际"
                                     "覆盖计，不以预期时长补成通过；"
                                     + res["detail"])
            res["measured"] = {"survive_s": round(survive, 4)}
            res["pass"] = ok
            res["status"] = "pass" if ok else "fail"
        elif "lift_ratio_min" in thr:
            frz = [r["Frz"] for r in win
                   if isinstance(r["Frz"], (int, float))]
            if not frz:
                res["status"] = "no_data"
                res["pass"] = None
                res["detail"] = "飞行窗内无 Frz 每步合计列样本"
            else:
                mean_f = sum(frz) / len(frz)
                weight = float(cfg.MASS) * 9.81
                ratio = mean_f / weight
                res["measured"] = {"lift_ratio": round(ratio, 6)}
                ok = ratio >= float(thr["lift_ratio_min"])
                res["pass"] = ok
                res["status"] = "pass" if ok else "fail"
                res["detail"] = ("飞行窗 Frz 均值 %.4f N 与重量 %.4f N 之比 "
                                 "%.4f，阈值不低于 %s（数据流 %s）"
                                 % (mean_f, weight, ratio,
                                    thr["lift_ratio_min"], data_file))
        elif "roll_abs_max_rad" in thr:
            roll_max = max(abs(r["roll"]) for r in win)
            pitch_max = max(abs(r["pitch"]) for r in win)
            res["measured"] = {"roll_max_rad": round(roll_max, 6),
                               "pitch_max_rad": round(pitch_max, 6)}
            ok = (roll_max <= float(thr["roll_abs_max_rad"])
                  and pitch_max <= float(thr["pitch_abs_max_rad"]))
            res["pass"] = ok
            res["status"] = "pass" if ok else "fail"
            res["detail"] = ("滚转极值 %.4f 弧度，俯仰极值 %.4f 弧度（四元"
                             "数换算，数据流 %s），阈值分别不大于 %s 与 %s"
                             " 弧度" % (roll_max, pitch_max, data_file,
                                        thr["roll_abs_max_rad"],
                                        thr["pitch_abs_max_rad"]))
        else:
            res["status"] = "not_implemented"
            res["pass"] = None
            res["detail"] = "判据阈值结构未在悬停判定器登记，无法判定"
        results.append(res)
    return results


# ---------------------------------------------------------------------------
# 九十三列数据流读取
# ---------------------------------------------------------------------------

def load_stream(csv_path):
    """读取九十三列数据流，返回（行记录列表，问题清单）。

    每行记录为字典，含 t、z、x、y、vx、vy、vz、wx、wy、wz、roll、
    pitch、yaw、freq、Frz、ail、pitch_des。姿态三角由四元数列换算，
    禁止直读姿态标签列。
    """
    problems = []
    rows = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            missing = [c for c in STREAM_REQUIRED_COLS if c not in header]
            if missing:
                return [], ["数据流表头缺列 %s" % ",".join(missing)]
            for raw in reader:
                try:
                    t = float(raw[COL_T])
                    quat = np.asarray(
                        [float(raw[COL_QW]), float(raw[COL_QX]),
                         float(raw[COL_QY]), float(raw[COL_QZ])],
                        dtype=float)
                    roll, pitch, yaw = quat_to_euler(quat)
                    rows.append({
                        "t": t,
                        "z": float(raw[COL_Z]),
                        "x": float(raw[COL_X]),
                        "y": float(raw[COL_Y]),
                        "vx": float(raw[COL_VX]),
                        "vy": float(raw[COL_VY]),
                        "vz": float(raw[COL_VZ]),
                        "wx": float(raw[COL_WX]),
                        "wy": float(raw[COL_WY]),
                        "wz": float(raw[COL_WZ]),
                        "roll": float(roll),
                        "pitch": float(pitch),
                        "yaw": float(yaw),
                        "freq": float(raw[COL_FREQ]),
                        "Frz": float(raw[COL_FRZ]),
                        "ail": float(raw[COL_AIL]),
                        "pitch_des": float(raw[COL_PITCH_DES]),
                    })
                except (TypeError, ValueError):
                    problems.append("数据流存在不可解析的行，首现于 t=%s"
                                    % raw.get(COL_T))
                    break
    except OSError as exc:
        return [], ["数据流文件不可读：%s" % exc]
    if not rows:
        problems.append("数据流无有效数据行")
    return rows, problems


def _window_rows(rows, t_from=None, t_to=None, strict_from=True):
    """取时刻窗内的行记录。t_from 为None 表示不限起点。"""
    out = []
    for r in rows:
        if t_from is not None:
            if strict_from and r["t"] < t_from:
                continue
            if not strict_from and r["t"] <= t_from:
                continue
        if t_to is not None and r["t"] > t_to:
            continue
        out.append(r)
    return out


def _finite_states(rows):
    """检查行记录的状态数值是否全部有限，返回问题或 None。"""
    for r in rows:
        vals = (r["z"], r["x"], r["y"], r["vx"], r["vy"], r["vz"],
                r["roll"], r["pitch"], r["yaw"], r["freq"], r["Frz"])
        if not all(math.isfinite(v) for v in vals):
            return "状态数值非有限，首现于 t=%.4f 秒" % r["t"]
    return None


# ---------------------------------------------------------------------------
# 巡航科判定器（C1 至 C5）
# ---------------------------------------------------------------------------

def judge_cruise(rows, criteria, run_completed):
    """按判据文件阈值逐条判定巡航科五判据。

    飞行窗起点取自判据 C1 阈值 crash_def 文本中的时刻表述。任何阈值
    键缺失即记无法判定，不取默认值。
    """
    results = []
    c1 = next((c for c in criteria if c.get("id") == "C1"), None)
    win_start = _extract_flight_window_start(
        (c1 or {}).get("threshold")) if c1 else None
    if win_start is None:
        win_start = 0.0
        print("警告 巡航科 C1 阈值中未找到飞行窗起点表述，按全程评估")
    win_rows = _window_rows(rows, t_from=win_start, strict_from=False)
    if not win_rows:
        return [_not_implemented(
            c, "飞行窗无数据行，无法判定") for c in criteria]

    for crit in criteria:
        cid = crit.get("id", "?")
        thr = crit.get("threshold") or {}
        res = {
            "id": cid,
            "label": _criterion_label(cid, cid),
            "text": crit.get("text", ""),
            "threshold": thr,
            "status": "fail",
            "pass": False,
            "measured": {},
            "detail": "",
        }
        if cid == "C1":
            miss = _missing_keys(thr, ("survive_min_s", "z_crash_m"))
            if miss:
                results.append(_not_implemented(
                    crit, "判据阈值缺数值键 %s" % "、".join(miss)))
                continue
            z_crash = float(thr["z_crash_m"])
            survive_min = float(thr["survive_min_s"])
            finite_problem = _finite_states(win_rows)
            crash_t = None
            reason = ""
            for r in win_rows:
                if r["t"] <= win_start:
                    continue
                if r["z"] < z_crash:
                    crash_t = r["t"]
                    reason = "高度低于 %s 米" % z_crash
                    break
            if crash_t is None and finite_problem:
                crash_t = win_rows[-1]["t"]
                reason = finite_problem
            t_last = rows[-1]["t"]
            if crash_t is not None:
                res["measured"] = {"survive_s": crash_t,
                                   "stream_end_s": t_last}
                res["detail"] = "存活 %.4f 秒，%s" % (crash_t, reason)
            else:
                survive = t_last
                ok = (run_completed
                      and survive >= survive_min - _TAIL_TOL_S)
                res["measured"] = {"survive_s": survive,
                                   "stream_end_s": t_last,
                                   "run_completed": run_completed}
                res["pass"] = ok
                res["status"] = "pass" if ok else "fail"
                res["detail"] = (
                    "全程无坠机，数据流末行 %.4f 秒，阈值不低于 %s 秒，"
                    "收尾里程碑 %s" % (t_last, survive_min,
                                       "已见" if run_completed else "未见"))
        elif cid == "C2":
            miss = _missing_keys(thr, ("z_min_m", "z_max_m"))
            if miss:
                results.append(_not_implemented(
                    crit, "判据阈值缺数值键 %s" % "、".join(miss)))
                continue
            z_min = float(thr["z_min_m"])
            z_max = float(thr["z_max_m"])
            zs = [r["z"] for r in win_rows]
            bad = [r for r in win_rows
                   if r["z"] < z_min or r["z"] > z_max]
            ratio = 1.0 - len(bad) / max(len(win_rows), 1)
            res["measured"] = {
                "z_min_seen_m": min(zs), "z_max_seen_m": max(zs),
                "in_band_ratio": ratio, "out_of_band_rows": len(bad)}
            ok = len(bad) == 0
            res["pass"] = ok
            res["status"] = "pass" if ok else "fail"
            res["detail"] = ("飞行窗高度范围 %.4f 至 %.4f 米，带内占比 "
                             "%.4f，越带 %d 行，判据口径为全程在 %s 至 %s "
                             "米带内" % (min(zs), max(zs), ratio, len(bad),
                                         z_min, z_max))
        elif cid == "C3":
            miss = _missing_keys(thr, ("neighborhood_hz",
                                       "protect_band_lower_hz",
                                       "protect_band_upper_hz"))
            if miss or not isinstance(thr.get("neighborhood_hz"), list) \
                    or len(thr["neighborhood_hz"]) != 2:
                results.append(_not_implemented(
                    crit, "判据阈值缺邻域或保护带数值键"))
                continue
            lo, hi = thr["neighborhood_hz"]
            prot_lo = float(thr["protect_band_lower_hz"])
            prot_hi = float(thr["protect_band_upper_hz"])
            freqs = [r["freq"] for r in win_rows]
            out_nb = [f for f in freqs if f < float(lo) or f > float(hi)]
            prot = [f for f in freqs if f <= prot_lo or f >= prot_hi]
            in_ratio = 1.0 - len(out_nb) / max(len(freqs), 1)
            res["measured"] = {
                "freq_median_hz": float(np.median(freqs)),
                "freq_min_hz": min(freqs), "freq_max_hz": max(freqs),
                "neighborhood_in_ratio": in_ratio,
                "protect_band_rows": len(prot)}
            ok = len(out_nb) == 0 and len(prot) == 0
            res["pass"] = ok
            res["status"] = "pass" if ok else "fail"
            res["detail"] = ("飞行窗拍频中位 %.4f 赫兹，范围 %.4f 至 %.4f "
                             "赫兹，邻域 %s 至 %s 内占比 %.4f，贴保护带 "
                             "%d 行，判据口径为全程在邻域内且不贴保护带"
                             % (float(np.median(freqs)), min(freqs),
                                max(freqs), lo, hi, in_ratio, len(prot)))
        elif cid == "C4":
            miss = _missing_keys(thr, ("saturation_sample_threshold_rad",))
            if miss:
                results.append(_not_implemented(
                    crit, "判据阈值缺数值键 %s" % "、".join(miss)))
                continue
            sat_thr = float(thr["saturation_sample_threshold_rad"])
            pd_vals = [r["pitch_des"] for r in win_rows]
            sat = [abs(v) for v in pd_vals if abs(v) >= sat_thr]
            # 最长连续顶限幅段
            run_len = 0
            run_max = 0
            for v in pd_vals:
                if abs(v) >= sat_thr:
                    run_len += 1
                    run_max = max(run_max, run_len)
                else:
                    run_len = 0
            ratio = len(sat) / max(len(pd_vals), 1)
            res["measured"] = {
                "saturated_rows": len(sat),
                "saturated_ratio": ratio,
                "longest_streak_rows": run_max}
            ok = len(sat) == 0
            res["pass"] = ok
            res["status"] = "pass" if ok else "fail"
            res["detail"] = ("飞行窗俯仰指令绝对值不低于 %s 弧度的样本 "
                             "%d 行，占比 %.4f，最长连续 %d 行，判据口径为"
                             "零顶限幅样本" % (sat_thr, len(sat), ratio,
                                               run_max))
        elif cid == "C5":
            miss = _missing_keys(thr, ("window_s", "abs_dzdt_max_m_per_s"))
            if miss or not isinstance(thr.get("window_s"), list) \
                    or len(thr["window_s"]) != 2:
                results.append(_not_implemented(
                    crit, "判据阈值缺末段窗或高度变化率上限数值键"))
                continue
            w_lo, w_hi = thr["window_s"]
            cap = float(thr["abs_dzdt_max_m_per_s"])
            wrows = _window_rows(rows, t_from=float(w_lo), t_to=float(w_hi))
            if len(wrows) < 2:
                res["status"] = "no_data"
                res["pass"] = None
                res["detail"] = "末段窗 %s 至 %s 秒内数据不足" % (w_lo, w_hi)
            else:
                ts = np.asarray([r["t"] for r in wrows])
                zs = np.asarray([r["z"] for r in wrows])
                vzs = np.asarray([abs(r["vz"]) for r in wrows])
                slope = float(np.polyfit(ts, zs, 1)[0])
                mean_abs_vz = float(np.mean(vzs))
                ok = mean_abs_vz <= cap and abs(slope) <= cap
                res["pass"] = ok
                res["status"] = "pass" if ok else "fail"
                res["measured"] = {
                    "mean_abs_vz_m_per_s": mean_abs_vz,
                    "z_fit_slope_m_per_s": slope,
                    "window_rows": len(wrows)}
                res["detail"] = (
                    "窗 %s 至 %s 秒共 %d 行，平均竖直速度绝对值 %.4f 米每秒，"
                    "高度线性拟合斜率 %+.4f 米每秒，两项阈值均不大于 %s 米"
                    "每秒" % (w_lo, w_hi, len(wrows), mean_abs_vz, slope,
                              cap))
        else:
            res["status"] = "not_implemented"
            res["pass"] = None
            res["detail"] = "判据编号 %s 未在巡航判定器登记" % cid
        results.append(res)
    return results


# ---------------------------------------------------------------------------
# 盘旋科判定器（T1 至 T5）
# ---------------------------------------------------------------------------

def judge_turn(rows, criteria, run_completed, env_applied):
    """按判据文件阈值逐条判定盘旋科五判据。

    坡度指令值取自场景开关 FLAPPER_BANK_DEG，切入时刻取自
    FLAPPER_BANK_T，两值均来自判据文件的场景开关清单。任何阈值键缺失
    即记无法判定，不取默认值。
    """
    results = []
    try:
        bank_deg = float(env_applied.get("FLAPPER_BANK_DEG"))
        bank_t = float(env_applied.get("FLAPPER_BANK_T"))
        roll_cmd = math.radians(bank_deg)
    except (TypeError, ValueError):
        return [{"id": c.get("id", "?"),
                 "label": _criterion_label(c.get("id", "?"), "?"),
                 "text": c.get("text", ""),
                 "threshold": c.get("threshold"),
                 "status": "error", "pass": None, "measured": {},
                 "detail": "场景开关中未取到坡度指令角与切入时刻，无法"
                           "判定坡度跟踪类判据"} for c in criteria]
    delta_max = getattr(cfg.FlightControllerConfig, "DELTA_MAX", None)
    if not isinstance(delta_max, (int, float)):
        delta_max = None

    for crit in criteria:
        cid = crit.get("id", "?")
        thr = crit.get("threshold") or {}
        res = {
            "id": cid,
            "label": _criterion_label(cid, cid),
            "text": crit.get("text", ""),
            "threshold": thr,
            "status": "fail",
            "pass": False,
            "measured": {},
            "detail": "",
        }
        if cid == "T1":
            miss = _missing_keys(thr, ("z_min_m", "z_max_m"))
            if miss:
                results.append(_not_implemented(
                    crit, "判据阈值缺数值键 %s" % "、".join(miss)))
                continue
            z_min = float(thr["z_min_m"])
            z_max = float(thr["z_max_m"])
            finite_problem = _finite_states(rows)
            zs = [r["z"] for r in rows]
            ok = (finite_problem is None
                  and min(zs) >= z_min and max(zs) <= z_max)
            res["pass"] = ok
            res["status"] = "pass" if ok else "fail"
            res["measured"] = {"z_min_seen_m": min(zs),
                               "z_max_seen_m": max(zs)}
            res["detail"] = ("全程高度范围 %.4f 至 %.4f 米，阈值区间 %s "
                             "至 %s 米%s"
                             % (min(zs), max(zs), z_min, z_max,
                                "" if finite_problem is None
                                else "，" + finite_problem))
        elif cid == "T2":
            miss = _missing_keys(thr, ("eval_from_s",
                                       "roll_cmd_err_median_max_rad",
                                       "tail_diff_sat_time_ratio_max"))
            if miss or delta_max is None:
                results.append(_not_implemented(
                    crit, "判据阈值缺数值键 %s，或现行配置缺尾翼差动限幅"
                    "常量" % "、".join(miss)))
                continue
            from_s = float(thr["eval_from_s"])
            med_max = float(thr["roll_cmd_err_median_max_rad"])
            sat_ratio_max = float(thr["tail_diff_sat_time_ratio_max"])
            wrows = _window_rows(rows, t_from=from_s, strict_from=True)
            if not wrows:
                res["status"] = "no_data"
                res["pass"] = None
                res["detail"] = "评估窗 %s 秒后无数据行" % from_s
            else:
                errs = [abs(r["roll"] - roll_cmd) for r in wrows]
                med = float(np.median(errs))
                sat_rows = [r for r in wrows
                            if abs(r["ail"]) >= float(delta_max)]
                sat_ratio = len(sat_rows) / max(len(wrows), 1)
                ok1 = med <= med_max
                ok2 = sat_ratio <= sat_ratio_max
                res["pass"] = ok1 and ok2
                res["status"] = "pass" if (ok1 and ok2) else "fail"
                res["measured"] = {
                    "roll_cmd_err_median_rad": med,
                    "tail_diff_sat_ratio": sat_ratio,
                    "delta_max_rad": float(delta_max),
                    "eval_rows": len(wrows)}
                res["detail"] = (
                    "%s 秒起滚转角与指令偏差中位数 %.4f 弧度，阈值不大于 "
                    "%s；尾翼差动指令绝对值不低于限幅 %s 弧度的占比 %.4f，"
                    "阈值不大于 %s"
                    % (from_s, med, med_max, float(delta_max), sat_ratio,
                       sat_ratio_max))
        elif cid == "T3":
            miss = _missing_keys(thr, ("window_after_bank_on_s",
                                       "altitude_drop_max_m"))
            if miss:
                results.append(_not_implemented(
                    crit, "判据阈值缺数值键 %s" % "、".join(miss)))
                continue
            window_s = float(thr["window_after_bank_on_s"])
            drop_max = float(thr["altitude_drop_max_m"])
            wrows = _window_rows(rows, t_from=bank_t,
                                 t_to=bank_t + window_s, strict_from=True)
            base_rows = _window_rows(rows, t_to=bank_t, strict_from=True)
            if not wrows or not base_rows:
                res["status"] = "no_data"
                res["pass"] = None
                res["detail"] = ("坡度切入时刻 %s 秒前后的数据不足，无法"
                                 "计算切入掉高" % bank_t)
            else:
                z_ref = base_rows[-1]["z"]
                z_min_w = min(r["z"] for r in wrows)
                drop = z_ref - z_min_w
                ok = drop <= drop_max
                res["pass"] = ok
                res["status"] = "pass" if ok else "fail"
                res["measured"] = {
                    "z_at_bank_on_m": z_ref,
                    "z_min_in_window_m": z_min_w,
                    "altitude_drop_m": drop}
                res["detail"] = ("坡度切入 %s 秒后 %s 秒内掉高 %.4f 米，"
                                 "阈值不大于 %s 米"
                                 % (bank_t, window_s, drop, drop_max))
        elif cid == "T4":
            miss = _missing_keys(thr, ("eval_from_s", "eval_to_s",
                                       "yaw_rate_dev_ratio_max",
                                       "theory_yaw_rate_rad_per_s"))
            if miss:
                results.append(_not_implemented(
                    crit, "判据阈值缺数值键 %s" % "、".join(miss)))
                continue
            from_s = float(thr["eval_from_s"])
            to_s = float(thr["eval_to_s"])
            dev_max = float(thr["yaw_rate_dev_ratio_max"])
            theory = float(thr["theory_yaw_rate_rad_per_s"])
            wrows = _window_rows(rows, t_from=from_s, t_to=to_s,
                                 strict_from=True)
            if len(wrows) < 2:
                res["status"] = "no_data"
                res["pass"] = None
                res["detail"] = ("评估窗 %s 至 %s 秒内数据不足"
                                 % (from_s, to_s))
            else:
                ts = [r["t"] for r in wrows]
                yaws = np.unwrap(np.asarray([r["yaw"] for r in wrows]))
                rate = float((yaws[-1] - yaws[0]) / (ts[-1] - ts[0]))
                wz_mean = float(np.mean([r["wz"] for r in wrows]))
                dev = abs(rate - theory) / abs(theory) if theory else 0.0
                ok = dev <= dev_max
                res["pass"] = ok
                res["status"] = "pass" if ok else "fail"
                res["measured"] = {
                    "mean_heading_rate_rad_per_s": rate,
                    "mean_wz_rad_per_s": wz_mean,
                    "theory_rad_per_s": theory,
                    "dev_ratio": dev}
                res["detail"] = (
                    "%s 至 %s 秒平均偏航角速度（四元数换算的航向率）"
                    "%.4f 弧度每秒，理论值 %s，偏差占比 %.4f，阈值不大于 "
                    "%s；机体角速度 wz 均值 %.4f 弧度每秒备查"
                    % (from_s, to_s, rate, theory, dev, dev_max, wz_mean))
        elif cid == "T5":
            # 判据文本自带限定：仅抗扰科目评此项，盘旋科目不评。
            res["status"] = "not_applicable"
            res["pass"] = None
            res["detail"] = ("判据文本限定仅抗扰科目评此项，盘旋科目"
                             "不评")
        else:
            res["status"] = "not_implemented"
            res["pass"] = None
            res["detail"] = "判据编号 %s 未在盘旋判定器登记" % cid
        results.append(res)
    return results


# ---------------------------------------------------------------------------
# 盘旋科进度里程碑阶梯（事项 I-057，诊断读数，非判据）
# ---------------------------------------------------------------------------

TURN_MILESTONE_TIERS_S = (5.0, 10.0, 15.0, 20.0, 25.0, 35.0)


def turn_milestones(rows, env_applied, criteria):
    """盘旋科六档里程碑读数：转弯保持五、十、十五、二十、二十五、三十
    五秒各出一档诊断读数，自动进入基准报告。档位为绝对仿真时刻，其中
    五秒档即坡度指令切入时刻，三十五秒档即科目总时长末点。

    本函数只产出进度可见性读数，不产生任何通过或不通过结论，科目
    一五条冻结判据的判定路径零改动。失稳口径沿用判据文件判据一的
    高度带，判据一阈值缺失时仅以数据流终止为失稳依据。
    """
    try:
        bank_t = float(env_applied.get("FLAPPER_BANK_T"))
        bank_deg = float(env_applied.get("FLAPPER_BANK_DEG"))
    except (TypeError, ValueError):
        return {"error": "场景开关中未取到坡度指令角与切入时刻，里程碑"
                         "读数缺基准"}
    roll_cmd = math.radians(bank_deg)
    z_lo, z_hi = None, None
    for crit in criteria:
        if crit.get("id") == "T1":
            thr = crit.get("threshold") or {}
            if "z_min_m" in thr and "z_max_m" in thr:
                z_lo = float(thr["z_min_m"])
                z_hi = float(thr["z_max_m"])
            break
    delta_max = getattr(cfg.FlightControllerConfig, "DELTA_MAX", None)
    last_t = rows[-1]["t"] if rows else 0.0
    tiers = []
    for tier in TURN_MILESTONE_TIERS_S:
        entry = {"tier_s": tier}
        if tier > last_t:
            entry["status"] = "lost_before"
            entry["last_t_s"] = last_t
            if rows:
                entry["z_at_end_m"] = rows[-1]["z"]
            entry["note"] = "数据流在 %.4f 秒终止，本档无在飞读数" % last_t
            tiers.append(entry)
            continue
        at_rows = [r for r in rows if r["t"] <= tier]
        cur = at_rows[-1] if at_rows else rows[0]
        band_ok = True
        if z_lo is not None:
            band_ok = z_lo <= cur["z"] <= z_hi
        entry["status"] = "in_flight" if band_ok else "out_of_band"
        entry["z_m"] = cur["z"]
        entry["roll_rad"] = cur["roll"]
        entry["roll_err_vs_cmd_rad"] = abs(cur["roll"] - roll_cmd)
        hold = [r for r in at_rows if r["t"] >= bank_t]
        if hold:
            errs = [abs(r["roll"] - roll_cmd) for r in hold]
            entry["roll_err_median_since_onset_rad"] = float(np.median(errs))
            entry["wz_mean_since_onset_rad_per_s"] = float(
                np.mean([r["wz"] for r in hold]))
            if len(hold) >= 2 and delta_max is not None:
                entry["tail_diff_sat_ratio_since_onset"] = len(
                    [r for r in hold if abs(r["ail"]) >= float(delta_max)]
                ) / len(hold)
            if len(hold) >= 2:
                ts = [r["t"] for r in hold]
                yaws = np.unwrap(np.asarray([r["yaw"] for r in hold]))
                entry["heading_rate_since_onset_rad_per_s"] = float(
                    (yaws[-1] - yaws[0]) / (ts[-1] - ts[0]))
        tiers.append(entry)
    return {"tiers": tiers, "bank_onset_s": bank_t,
            "bank_cmd_deg": bank_deg,
            "band_note": "失稳口径沿用判据一高度带" if z_lo is not None
                         else "判据一阈值缺失，仅以数据流终止为失稳依据"}


# ---------------------------------------------------------------------------
# 抗扰科判定器（D1）
# ---------------------------------------------------------------------------

def judge_disturb(rows, criteria, run_completed):
    """按判据文件阈值逐条判定抗扰科判据。触地口径为高度不高于现行配置
    的地面高程常量。任何阈值键缺失即记无法判定，不取默认值。"""
    results = []
    ground = getattr(cfg, "GROUND_LEVEL", None)
    for crit in criteria:
        cid = crit.get("id", "?")
        thr = crit.get("threshold") or {}
        res = {
            "id": cid,
            "label": _criterion_label(cid, cid),
            "text": crit.get("text", ""),
            "threshold": thr,
            "status": "fail",
            "pass": False,
            "measured": {},
            "detail": "",
        }
        if cid == "D1":
            miss = _missing_keys(thr, ("pulse_t_s",))
            if miss or ground is None:
                results.append(_not_implemented(
                    crit, "判据阈值缺数值键 %s，或现行配置缺地面高程常量"
                    % "、".join(miss)))
                continue
            pulse_t = float(thr["pulse_t_s"])
            wrows = _window_rows(rows, t_from=pulse_t, strict_from=True)
            if not wrows:
                res["status"] = "no_data"
                res["pass"] = None
                res["detail"] = "脉冲时刻 %s 秒后无数据行" % pulse_t
            else:
                zs = [r["z"] for r in wrows]
                ground_hits = [z for z in zs if z <= float(ground)]
                roll_peak = max(abs(r["roll"]) for r in wrows)
                yaw_rate_peak = max(abs(r["wz"]) for r in wrows)
                ok = len(ground_hits) == 0
                res["pass"] = ok
                res["status"] = "pass" if ok else "fail"
                res["measured"] = {
                    "post_pulse_z_min_m": min(zs),
                    "post_pulse_z_max_m": max(zs),
                    "post_pulse_roll_peak_rad": roll_peak,
                    "post_pulse_yaw_rate_peak_rad_per_s": yaw_rate_peak,
                    "ground_level_m": float(ground)}
                res["detail"] = (
                    "脉冲 %s 秒后高度最低 %.4f 米，触地口径为不高于地面高 "
                    "程 %s 米，%s；滚转角峰值 %.4f 弧度，偏航角速度峰值 "
                    "%.4f 弧度每秒，按判据文本记录并与 %s 对照（对照为"
                    "人工步骤）"
                    % (pulse_t, min(zs), float(ground),
                       "未触地" if ok else "触地",
                       roll_peak, yaw_rate_peak,
                       thr.get("compare_to", "侧滑单扰动试验组")))
        else:
            res["status"] = "not_implemented"
            res["pass"] = None
            res["detail"] = "判据编号 %s 未在抗扰判定器登记" % cid
        results.append(res)
    return results


# ---------------------------------------------------------------------------
# 场景开关解析
# ---------------------------------------------------------------------------

def parse_scenario_env(subject_key, scenario, subjects):
    """从判据文件的 scenario 中解析环境变量开关清单。

    巡航科的 env 键为机器可读的字典；盘旋科的开关清单藏在
    env_switches_verbatim 文本内，格式为若干“缩写名=数值”记号，真实
    环境变量名经 SCENARIO_ENV_NAME_MAP 展开。抗扰科的开关清单按其
    env_switches 字段的指引取自盘旋科的场景文本，另按 pulse 字段加
    侧滑脉冲三开关。盘旋与抗扰科的场景文本声明开启偏航抵消组合通
    道，对应加 FLAPPER_WING_MIX=1。凡解析到未登记的缩写名即报错，
    绝不静默丢开关。
    """
    env = {}
    notes = []
    if not isinstance(scenario, dict):
        return env, notes, ["scenario 不是对象"]
    problems = []
    if isinstance(scenario.get("env"), dict):
        for k, v in scenario["env"].items():
            env[str(k)] = str(v)

    if subject_key == "cruise":
        # 巡航科二十处场景配置落位（I-016）：第六跑 5e 第一节十九个配置
        # 键经环境变量桥生效，判据文件 scenario 显式冻结值与落位表核对，
        # 不一致即拒绝运行。
        env.update(CRUISE_SCENARIO_CONFIG)
        notes.append(
            "巡航场景按三维在环确认文档第六跑 5e 第一节落位 %d 个配置键"
            "（环境变量桥），三增益含于其中" % len(CRUISE_SCENARIO_CONFIG))
        notes.append(
            "二十处中的速度验收带注释口径 4.5±1.0→3.5±1.0 为 config 注释"
            "行改动，非配置键，无环境变量桥，未下发")
        for skey, envk in CRUISE_SCENARIO_CROSSCHECK.items():
            sval = scenario.get(skey)
            tval = env[envk]
            mismatch = False
            if isinstance(sval, (int, float)) and not isinstance(sval, bool):
                mismatch = abs(float(sval) - float(tval)) > 1e-9
            elif isinstance(sval, str):
                mismatch = sval != tval
            else:
                mismatch = True
            if mismatch:
                problems.append(
                    "判据文件场景键 %s=%s 与第六跑落位表 %s=%s 不一致，"
                    "两处冻结源疑似漂移，拒绝按漂移值运行"
                    % (skey, sval, envk, tval))

    verbatim = scenario.get("env_switches_verbatim")
    verbatim_src = "本科目场景文本"
    if not isinstance(verbatim, str) and subject_key == "disturb":
        # 抗扰科的开关清单按指引取盘旋科的场景文本。
        turn_scenario = (subjects.get("turn") or {}).get("scenario")
        if isinstance(turn_scenario, dict) \
                and isinstance(turn_scenario.get("env_switches_verbatim"),
                               str):
            verbatim = turn_scenario["env_switches_verbatim"]
            verbatim_src = ("盘旋科场景文本（按抗扰科 env_switches 字段"
                            "指引取用）")
    if isinstance(verbatim, str):
        marker = "以下试验开关："
        idx = verbatim.find(marker)
        if idx < 0:
            problems.append("场景开关文本中未找到“%s”标记" % marker)
        else:
            # 记号形态为“缩写名=数值”，缩写名允许单字母（如切入时刻 T）。
            tokens = re.findall(r"([A-Z][A-Z0-9_]*)=(-?[0-9.]+)",
                                verbatim[idx + len(marker):])
            for name, value in tokens:
                real = SCENARIO_ENV_NAME_MAP.get(name)
                if real is None:
                    problems.append(
                        "场景开关缩写 %s 未在名称对照表登记，拒绝静默"
                        "丢弃" % name)
                    continue
                env[real] = value
            notes.append("从%s解析到 %d 个开关" % (verbatim_src,
                                                   len(tokens)))
    elif subject_key == "turn":
        problems.append("盘旋科场景缺 env_switches_verbatim 文本")

    if subject_key in ("turn", "disturb"):
        # 两科的场景文本均声明开启偏航抵消组合通道。
        env.setdefault("FLAPPER_WING_MIX", "1")
    if subject_key == "disturb":
        pulse = scenario.get("pulse")
        if isinstance(pulse, dict):
            env["FLAPPER_SIDESLIP_PULSE"] = "1"
            env["FLAPPER_SIDESLIP_PULSE_T"] = str(pulse.get("t_s"))
            env["FLAPPER_SIDESLIP_PULSE_DVY"] = str(pulse.get("vy_m_per_s"))
        else:
            problems.append("抗扰科 scenario 缺 pulse 字段，脉冲无法注入")

    # 巡航科的气动模型有专用环境桥。
    aero = scenario.get("aero_model")
    if isinstance(aero, str) and aero:
        env["FLAPPER_AERO_MODEL"] = aero
    return env, notes, problems


# ---------------------------------------------------------------------------
# Isaac 在环科目的执行
# ---------------------------------------------------------------------------

def run_isaac_subject(subject_key, subject, subjects):
    """执行一个 Isaac 在环科目，返回科目结果字典。

    先做 Isaac 进程检测，检测到即记忙碌并返回。启动后轮询运行日志，
    以收尾里程碑文本判定完成性，超时不结束进程只记录。
    """
    tag = "BENCH_" + subject_key.upper()
    name_zh = subject.get("name_zh", subject_key)
    result = {
        "key": subject_key,
        "name_zh": name_zh,
        "file_status": subject.get("status"),
        "backend": "isaac在环",
        "verdict": "error",
        "reason": None,
        "run_completed": False,
        "exit_code": None,
        "run_log": None,
        "data_file": None,
        "env_applied": {},
        "scenario_notes": [],
        "criteria": [],
        "duration_s": subject.get("duration_s"),
    }

    busy_list, method = detect_isaac_processes()
    if busy_list:
        desc = "、".join("%s(%s)" % (p.get("Name"), p.get("ProcessId"))
                        for p in busy_list)
        result["verdict"] = "busy"
        result["reason"] = ("检测到既有 Isaac 进程 %s，按规则跳过该科目"
                            "（检测方式：%s）" % (desc, method))
        print("[科目 %s] %s" % (name_zh, result["reason"]))
        return result

    scenario = subject.get("scenario") or {}
    env_extra, notes, problems = parse_scenario_env(subject_key, scenario,
                                                    subjects)
    result["scenario_notes"].extend(notes)
    if problems:
        result["reason"] = "场景开关解析失败：" + "；".join(problems)
        print("[科目 %s] %s" % (name_zh, result["reason"]))
        return result

    env = dict(os.environ)
    # 先清掉外层环境既有的 FLAPPER_ 变量，防止非标准配置混入基准运行。
    for k in [k for k in env if k.startswith("FLAPPER_")]:
        del env[k]
    env["PYTHONUTF8"] = "1"
    env.update(STANDARD_LOCK)
    env["FLAPPER_HEADLESS"] = "1"
    env["FLAPPER_FLIGHT_LOG"] = "1"
    # 禁用手动输入通道（事项 I-129 根因修复）：基准运行无头无人值守，
    # 全系统键盘轮询会把其他会话击键当手动接管顶掉控制器若干步。
    env["FLAPPER_NO_MANUAL"] = "1"
    env["FLAPPER_MAX_S"] = str(subject.get("duration_s", 0))
    env.update(env_extra)
    env["FLAPPER_TAG"] = tag
    env["FLAPPER_DESC"] = ("I-016 基准运行：%s科目，判据文件 "
                           "config/bench_criteria.json 冻结口径，标准锁"
                           "与场景开关在环" % name_zh)
    result["env_applied"] = dict(env_extra)

    # 巡航科的场景落位登记（I-016 已接线：二十处经环境变量桥落位，
    # 映射表 CRUISE_SCENARIO_CONFIG，桥见 modules/sim_env_bridge.py）。
    if subject_key == "cruise":
        result["scenario_notes"].append(
            "巡航场景已按三维在环确认文档第六跑 5e 第一节落位：十九个配"
            "置键经环境变量桥生效（含波形幅值攻角的力模型补推），另一处"
            "为 config 注释口径改动，非配置键，无桥")

    os.makedirs(SCORES_DIR, exist_ok=True)
    run_log_path = os.path.join(
        SCORES_DIR, "%s_%s_run.log" % (_now_stamp(), subject_key))
    result["run_log"] = run_log_path

    print("[科目 %s] 启动 Isaac 在环运行，日志 %s" % (name_zh,
                                                       run_log_path))
    print("[科目 %s] 环境变量 %s" % (
        name_zh, " ".join("%s=%s" % kv for kv in sorted(env_extra.items())
                          if kv[0].startswith("FLAPPER_"))))
    t0 = time.time()
    milestone_at = None
    exit_code = None
    timed_out = False
    linger = False
    with open(run_log_path, "w", encoding="utf-8", errors="replace") as log_fh:
        proc = subprocess.Popen([ISAAC_PYTHON_BAT, MAIN_SIM_PY],
                                cwd=_REPO_ROOT, env=env,
                                stdout=log_fh, stderr=subprocess.STDOUT)
        while True:
            exit_code = proc.poll()
            log_fh.flush()
            try:
                with open(run_log_path, encoding="utf-8",
                          errors="replace") as fh:
                    log_text = fh.read()
            except OSError:
                log_text = ""
            if milestone_at is None and MILESTONE_RUN_CAP in log_text \
                    and MILESTONE_CAP_DONE in log_text:
                milestone_at = time.time()
                print("[科目 %s] 运行日志出现收尾里程碑" % name_zh)
            if exit_code is not None:
                break
            now = time.time()
            if milestone_at is not None \
                    and now - milestone_at > MILESTONE_EXIT_GRACE_S:
                linger = True
                break
            if now - t0 > ISAAC_RUN_WALL_CAP_S:
                timed_out = True
                break
            time.sleep(2.0)
    result["exit_code"] = exit_code
    wall_s = time.time() - t0
    with open(run_log_path, encoding="utf-8", errors="replace") as fh:
        log_text = fh.read()
    run_completed = (MILESTONE_RUN_CAP in log_text
                     and MILESTONE_CAP_DONE in log_text)
    result["run_completed"] = run_completed
    result["scenario_notes"].append("墙钟用时 %.1f 秒，退出码 %s" % (
        wall_s, "无（进程未退出）" if exit_code is None else exit_code))
    if timed_out:
        result["reason"] = ("运行超过 %.0f 秒墙钟上限，收尾里程碑未见，"
                            "按规则不结束进程，该科记错误"
                            % ISAAC_RUN_WALL_CAP_S)
        print("[科目 %s] %s" % (name_zh, result["reason"]))
        return result
    if linger:
        result["scenario_notes"].append(
            "收尾里程碑已见但进程在宽限期内未退出，按里程碑判定完成，"
            "进程留给操作系统自行收尾")
    if LOOP_CRASH_MARK in log_text:
        result["scenario_notes"].append("运行日志含主循环异常标记 "
                                        + LOOP_CRASH_MARK)
    if not run_completed:
        result["reason"] = ("运行日志未见收尾里程碑文本（%s 与 %s），"
                            "完成性不成立"
                            % (MILESTONE_RUN_CAP, MILESTONE_CAP_DONE))
        print("[科目 %s] %s" % (name_zh, result["reason"]))

    # 读取数据流指针并核对标注归属。
    pointer = os.path.join(cfg.LOG_DIR, "ident_latest.txt")
    try:
        with open(pointer, encoding="utf-8") as fh:
            stream_name = fh.read().strip()
        csv_path = os.path.join(cfg.LOG_DIR, stream_name)
    except OSError as exc:
        result["reason"] = (result["reason"] or "") + \
            "；数据流指针不可读：%s" % exc
        print("[科目 %s] %s" % (name_zh, result["reason"]))
        return result
    if not os.path.isfile(csv_path):
        result["reason"] = (result["reason"] or "") + \
            "；数据流文件不存在：%s" % csv_path
        print("[科目 %s] %s" % (name_zh, result["reason"]))
        return result
    meta_path = csv_path + ".meta.json"
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        if (meta.get("tags") or "") != tag:
            result["reason"] = (result["reason"] or "") + \
                "；数据流标注 %s 与本次运行标注 %s 不符，数据不属本次运行" \
                % (meta.get("tags"), tag)
            print("[科目 %s] %s" % (name_zh, result["reason"]))
            return result
    except (OSError, ValueError) as exc:
        result["reason"] = (result["reason"] or "") + \
            "；数据流元数据不可读：%s" % exc
        print("[科目 %s] %s" % (name_zh, result["reason"]))
        return result
    result["data_file"] = csv_path
    print("[科目 %s] 数据流 %s" % (name_zh, csv_path))

    rows, load_problems = load_stream(csv_path)
    if load_problems:
        result["reason"] = (result["reason"] or "") + \
            "；" + "；".join(load_problems)
        print("[科目 %s] 数据流加载问题：%s" % (name_zh,
                                                 "；".join(load_problems)))
        if not rows:
            return result

    if subject_key == "cruise":
        result["criteria"] = judge_cruise(rows, subject.get("criteria", []),
                                          run_completed)
    elif subject_key == "turn":
        result["criteria"] = judge_turn(rows, subject.get("criteria", []),
                                        run_completed, env_extra)
        result["milestones"] = turn_milestones(rows, env_extra,
                                               subject.get("criteria", []))
        ms = result["milestones"]
        if "tiers" in ms:
            print("[科目 %s] 里程碑阶梯（诊断读数，非判据）坡度指令 %s 度，"
                  "切入 %s 秒" % (name_zh, ms.get("bank_cmd_deg"),
                                  ms.get("bank_onset_s")))
            print("%8s %12s %8s %9s %12s %12s" % (
                "档/s", "状态", "高度m", "滚转误差rad",
                "切入后中位err", "切入后航向率"))
            for e in ms["tiers"]:
                if e["status"] == "in_flight":
                    print("%8.0f %12s %8.3f %9.4f %12s %12s" % (
                        e["tier_s"], "在飞", e.get("z_m", float("nan")),
                        e.get("roll_err_vs_cmd_rad", float("nan")),
                        ("%.4f" % e["roll_err_median_since_onset_rad"])
                        if "roll_err_median_since_onset_rad" in e else "-",
                        ("%.4f" % e["heading_rate_since_onset_rad_per_s"])
                        if "heading_rate_since_onset_rad_per_s" in e
                        else "-"))
                else:
                    print("%8.0f %12s %8s %9s %12s %12s" % (
                        e["tier_s"],
                        "带外" if e["status"] == "out_of_band" else "已终止",
                        ("%.3f" % e["z_m"]) if "z_m" in e else "-",
                        "-", "-", "-"))
    elif subject_key == "disturb":
        result["criteria"] = judge_disturb(rows, subject.get("criteria", []),
                                           run_completed)
    else:
        result["reason"] = "科目 %s 未登记判定器" % subject_key
        return result

    judged = [c for c in result["criteria"]
              if c["status"] in ("pass", "fail")]
    all_pass = bool(judged) and all(c["pass"] for c in judged)
    if result["reason"]:
        result["verdict"] = "error"
    else:
        result["verdict"] = "pass" if all_pass else "fail"
    return result


# ---------------------------------------------------------------------------
# 悬停科目执行
# ---------------------------------------------------------------------------

def run_hover_subject_from_stream(subject, data_path):
    """执行悬停科目（数据流驱动，事项 I-186 返修 v2）。

    实测值取自本次实际读取的数据流（--data-file），不再由进程内模拟后端
    自产；数据流列缺失或不可解析时对应判据记 no_data，不猜值。分数文件
    的实际分析输入登记该数据流（run_data 角色）与判据文件。
    """
    name_zh = subject.get("name_zh", "悬停")
    print("=" * 78)
    print("科目 %s 悬停 数据流驱动基准（实测自 %s）" % (name_zh, data_path))
    print("=" * 78)
    rows, problems = load_stream(data_path)
    for p in problems:
        print("警告 " + p)
    judged = judge_hover_from_stream(
        rows, subject.get("criteria", []),
        run_completed=(not problems and bool(rows)), data_file=data_path)
    result = {
        "key": "hover",
        "name_zh": name_zh,
        "file_status": subject.get("status"),
        "backend": "数据流驱动",
        "verdict": "fail",
        "reason": None,
        "run_completed": bool(rows) and not problems,
        "exit_code": 0,
        "run_log": None,
        "data_file": os.path.abspath(data_path),
        "env_applied": {},
        "scenario_notes": [
            "实测解析自本次数据流 %s" % os.path.abspath(data_path),
            "读取问题：%s" % ("；".join(problems) if problems else "无")],
        "criteria": judged,
        "duration_s": subject.get("duration_s"),
    }
    judged_ok = [c for c in judged if c["status"] in ("pass", "fail")]
    all_pass = bool(judged_ok) and all(c["pass"] for c in judged_ok)
    result["verdict"] = "pass" if all_pass else "fail"
    return result


def run_hover_subject(subject):
    """执行悬停科目：进程内运行模拟后端基准并判定。"""
    name_zh = subject.get("name_zh", "悬停")
    print("=" * 78)
    print("科目 %s 悬停 模拟后端基准（纯 Python，不启动 Isaac）" % name_zh)
    print("=" * 78)
    text, code = run_hover_mock()
    print(text, end="")
    measured = parse_hover_output(text)
    criteria = subject.get("criteria", [])
    judged, consistency = judge_hover(criteria, measured)
    bench_verdict = ""
    m = re.search(r"总结论 .*", text)
    if m:
        bench_verdict = m.group(0)
    result = {
        "key": "hover",
        "name_zh": name_zh,
        "file_status": subject.get("status"),
        "backend": "模拟后端",
        "verdict": "fail",
        "reason": None,
        "run_completed": True,
        "exit_code": code,
        "run_log": None,
        "data_file": None,
        "env_applied": {},
        "scenario_notes": [
            "进程内调用 run_hover_bench，实测数值解析自其控制台输出",
            "run_hover_bench 内置结论行：%s" % bench_verdict] + consistency,
        "criteria": judged,
        "duration_s": subject.get("duration_s"),
    }
    judged_ok = [c for c in judged if c["status"] in ("pass", "fail")]
    all_pass = bool(judged_ok) and all(c["pass"] for c in judged_ok)
    result["verdict"] = "pass" if all_pass else "fail"
    return result


# ---------------------------------------------------------------------------
# 分数表与分数文件
# ---------------------------------------------------------------------------

def _verdict_zh(verdict):
    return {"pass": "通过", "fail": "不通过", "busy": "忙碌",
            "skipped": "跳过", "error": "错误"}.get(verdict, verdict)


def _status_zh(status):
    return {"pass": "通过", "fail": "不通过", "busy": "忙碌",
            "not_applicable": "不适用", "not_implemented": "未实现判定",
            "no_data": "无数据", "error": "错误"}.get(status, status)


def print_score_table(mode, data, results):
    """打印科目分数表与逐条判据明细。"""
    print()
    print("=" * 78)
    print("能力基准分数表（事项 %s）" % data.get("item", "I-016"))
    print("判据文件 config/bench_criteria.json（冻结日期 %s）"
          % data.get("frozen_at", "未知"))
    print("运行模式 %s" % mode)
    print("=" * 78)
    print("%-10s %-6s %-12s %-6s %s" % ("科目", "名称", "后端", "结论",
                                        "判据通过数"))
    print("-" * 78)
    for r in results:
        total = len(r.get("criteria") or [])
        ok_n = sum(1 for c in (r.get("criteria") or [])
                   if c.get("status") == "pass")
        judged_n = sum(1 for c in (r.get("criteria") or [])
                       if c.get("status") in ("pass", "fail"))
        ratio = ("%d/%d" % (ok_n, judged_n)) if total else "-"
        print("%-10s %-6s %-12s %-6s %s" % (
            r["key"], r["name_zh"], r.get("backend", "-"),
            _verdict_zh(r["verdict"]), ratio))
    print("-" * 78)
    for r in results:
        for c in (r.get("criteria") or []):
            print("[%s %s] %s：%s" % (r["key"], c["id"], c["label"],
                                      c["detail"]))
        if r.get("reason"):
            print("[%s] 原因：%s" % (r["key"], r["reason"]))
        for note in (r.get("scenario_notes") or []):
            print("[%s] 记录：%s" % (r["key"], note))
    print("=" * 78)
    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("总结论：通过 %d 科，不通过 %d 科，忙碌 %d 科，跳过 %d 科，"
          "错误 %d 科（忙碌与跳过不计失败）" % (
              counts.get("pass", 0), counts.get("fail", 0),
              counts.get("busy", 0), counts.get("skipped", 0),
              counts.get("error", 0)))


def write_scores_json(mode, data, results, output_dir=None, identity=None,
                      analyzed_inputs=None):
    """把分数写入 logs/bench_scores/<时间戳>_scores.json，返回路径。

    事项 I-186：output_dir 给出时改写入选定目录（供统一运行器归档目录
    直接连通）；identity 给出时记录本次实验、运行与事项标识；
    analyzed_inputs 给出时逐项记录实际分析输入（文件加内容哈希加用途），
    供证据包绑定。缺省不填身份，旧调用行为不变。
    """
    target_dir = output_dir if output_dir else SCORES_DIR
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, "%s_scores.json" % _now_stamp())
    payload = {
        "schema_version": 1,
        "item": data.get("item"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "criteria_file": "config/bench_criteria.json",
        "criteria_frozen_at": data.get("frozen_at"),
        "handoff_note": data.get("handoff_note"),
        "subjects": results,
        "summary": {},
    }
    if identity:
        payload["experiment_id"] = identity.get("experiment_id")
        payload["run_id"] = identity.get("run_id")
        if identity.get("item"):
            payload["item"] = identity.get("item")
    if analyzed_inputs:
        payload["analyzed_inputs"] = analyzed_inputs
    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    payload["summary"] = counts
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


def _read_archive_identity(archive_dir):
    """读统一运行器归档目录内的 identity.json，返回身份字典或 None。"""
    path = os.path.join(archive_dir, "identity.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    return {"experiment_id": doc.get("experiment_id"),
            "run_id": doc.get("run_id"), "item": doc.get("item_id")}


def _criteria_file_input_ref():
    """本运行器实际分析输入之首项：判据文件（load_criteria 实读）。"""
    with open(CRITERIA_PATH, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return {"file": "config/bench_criteria.json", "sha256": digest,
            "role": "standard_definition"}


def _analysis_identity_and_inputs(args):
    """组装分析步身份与分析输入（事项 I-186 生产贯通）。

    身份优先取命令行显式声明，其次读归档目录 identity.json；两者皆无时
    返回 None，分数文件不带身份，证据包按缺身份阻断（如实保护）。
    实际分析输入逐项登记（事项 I-186 返修 v2）：判据文件（标准定义）与
    --data-file 实际读取的本次数据流（run_data），各带内容哈希，不手写
    替代清单。
    """
    identity = None
    if getattr(args, "archive_dir", None):
        identity = _read_archive_identity(args.archive_dir) or {}
        if getattr(args, "experiment_id", None):
            identity["experiment_id"] = args.experiment_id
        if getattr(args, "run_id", None):
            identity["run_id"] = args.run_id
        if getattr(args, "item", None):
            identity["item"] = args.item
        if not (identity.get("experiment_id") and identity.get("run_id")):
            identity = None
    inputs = [_criteria_file_input_ref()]
    data_file = getattr(args, "data_file", None)
    if data_file:
        repo_root = os.path.dirname(os.path.dirname(
            os.path.abspath(CRITERIA_PATH)))
        with open(data_file, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        inputs.append({
            "file": os.path.relpath(
                os.path.abspath(data_file), repo_root).replace(os.sep, "/"),
            "sha256": digest,
            "role": "run_data"})
    return identity, inputs


# ---------------------------------------------------------------------------
# 科目调度
# ---------------------------------------------------------------------------

def execute_subject(key, subject, subjects):
    """按科目键执行单个科目，返回科目结果字典。"""
    if subject.get("status") == "proposed":
        print("[科目 %s] 状态为 proposed，按约定跳过" % subject.get(
            "name_zh", key))
        return {
            "key": key,
            "name_zh": subject.get("name_zh", key),
            "file_status": subject.get("status"),
            "backend": subject.get("backend", "-"),
            "verdict": "skipped",
            "reason": "状态为 proposed，按约定跳过，不作验收依据",
            "run_completed": False,
            "exit_code": None,
            "run_log": None,
            "data_file": None,
            "env_applied": {},
            "scenario_notes": [],
            "criteria": [],
            "duration_s": subject.get("duration_s"),
        }
    if key == "hover":
        return run_hover_subject(subject)
    if key in ("cruise", "turn", "disturb"):
        return run_isaac_subject(key, subject, subjects)
    return {
        "key": key,
        "name_zh": subject.get("name_zh", key),
        "file_status": subject.get("status"),
        "backend": subject.get("backend", "-"),
        "verdict": "error",
        "reason": "科目 %s 未登记执行方式" % key,
        "run_completed": False,
        "exit_code": None,
        "run_log": None,
        "data_file": None,
        "env_applied": {},
        "scenario_notes": [],
        "criteria": [],
        "duration_s": subject.get("duration_s"),
    }


def main(argv=None):
    """命令行入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(
        description="能力基准运行器（事项 I-016）：判据与阈值一律读自 "
                    "config/bench_criteria.json")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fast", action="store_true",
                       help="只跑快档：契约自检加悬停模拟后端基准，"
                            "不启动 Isaac")
    group.add_argument("--all", action="store_true",
                       help="跑全部 active 科目")
    group.add_argument("--subject", metavar="科目名",
                       help="只跑一个科目，取值 hover、cruise、turn、"
                       "disturb、gap")
    # 事项 I-186 生产贯通：--archive-dir 把分数直接写进统一运行器归档目
    # 录并绑定该次运行身份（读归档内 identity.json，命令行可显式覆盖）。
    parser.add_argument("--archive-dir", metavar="目录",
                        help="分数文件写入选定归档目录并绑定该次运行身份")
    parser.add_argument("--experiment-id", metavar="标识",
                        help="显式声明实验标识（覆盖归档 identity.json）")
    parser.add_argument("--run-id", metavar="标识",
                        help="显式声明运行标识（覆盖归档 identity.json）")
    parser.add_argument("--item", metavar="事项",
                        help="显式声明事项标识（覆盖归档 identity.json）")
    parser.add_argument("--data-file", metavar="数据流",
                        help="悬停科数据流驱动基准：实测取自该 CSV 数据流"
                             "（事项 I-186 返修 v2，分数登记实际分析输入）")
    args = parser.parse_args(argv)

    data, problems = load_criteria()
    if problems:
        print("=" * 78)
        print("判据文件结构自检未通过")
        for p in problems:
            print("问题 " + p)
        return 1
    print("判据文件结构自检通过：%s（事项 %s，冻结日期 %s）"
          % (CRITERIA_PATH, data.get("item"), data.get("frozen_at")))

    results = []
    contract_ok = True
    if args.fast:
        mode = "快档（契约自检加悬停模拟后端基准，不启动 Isaac）"
        contract_ok = run_contract_check()
        results.append(run_hover_subject(data["subjects"]["hover"]))
    elif args.all:
        mode = "全部 active 科目"
        for key in SUBJECT_ORDER:
            subject = data["subjects"][key]
            results.append(execute_subject(key, subject, data["subjects"]))
    else:
        key = args.subject
        if key not in data["subjects"]:
            print("科目名 %s 不在判据文件的 subjects 内，可取值为 %s"
                  % (key, "、".join(SUBJECT_ORDER)))
            return 2
        mode = "单科目 %s" % key
        if key == "hover" and getattr(args, "data_file", None):
            results.append(run_hover_subject_from_stream(
                data["subjects"]["hover"], args.data_file))
        else:
            results.append(execute_subject(key, data["subjects"][key],
                                           data["subjects"]))

    print_score_table(mode, data, results)
    identity, analyzed_inputs = _analysis_identity_and_inputs(args)
    scores_path = write_scores_json(
        mode, data, results,
        output_dir=getattr(args, "archive_dir", None),
        identity=identity,
        analyzed_inputs=analyzed_inputs
        if getattr(args, "archive_dir", None) else None)
    print("分数文件 %s" % scores_path)
    if identity:
        print("分数身份绑定：实验 %s 运行 %s 事项 %s"
              % (identity.get("experiment_id"), identity.get("run_id"),
                 identity.get("item") or data.get("item")))

    failed = any(r["verdict"] in ("fail", "error") for r in results)
    if args.fast and not contract_ok:
        failed = True
    print("BENCH %s" % ("FAIL 基准未通过" if failed else "PASS 基准通过"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
