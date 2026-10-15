# -*- coding: utf-8 -*-
"""sim_entry.py 统一在环启动入口，事项 I-111。

一切 Isaac 在环运行收口到本入口。入口内焊死两件不可绕过的纪律：

一 标准锁四项：FLAPPER_POST_STEP_CB 等于一、FLAPPER_PHYS_CPU 等
于一、FLAPPER_IID_LOCK 等于一、FLAPPER_OBSTACLE_SEED 等于四十二。
入口对四项锁做强制盖章；调用方环境或参数里任何一项携带冲突值即拒
绝运行，拒绝时不起任何子进程。锁值唯一来源为本文件的焊死表。

二 背靠背逐字节复验：每次启动把同一工况连跑两遍，按标注各自定位
数据流后整档字节比对，一致才算启动成功，不一致判复验失败并返回
失败结论。复验不可关闭。

调用面：
  from modules.sim_entry import launch_sim
  verdict = launch_sim(extra_env, tag_base, desc, max_s, log_dir,
                       timeout_s=420)
  verdict["ok"] 为真时 verdict["csv"] 指向第一跑数据流，评估逻辑照
  常读取；为假时 verdict["reason"] 给出拒绝或失败原因。

完成性以运行日志收尾里程碑为准，退出码仅登记（解释器启动脚本退出
码不可靠，事项 I-025 在案）。启动前检测既有 Isaac 进程，检测到即拒
绝并行第二实例，绝不结束任何进程。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from modules.backend.bench_runner import (  # noqa: E402
    MILESTONE_CAP_DONE,
    MILESTONE_RUN_CAP,
    detect_isaac_processes,
)
from modules.experiment_pipeline.runner import _find_collect_csv  # noqa: E402

ISAAC_PY = "g:/Playground/isaac_sim_5.1.0/python.bat"

# 焊死的标准锁五项，锁值唯一来源。缺任何一键本模块导入即自检失败。
# 第五项为增强确定性开关（事项 I-109：该开关此前从未开启，分岔事故
# 根源类为引擎求解器 warm-start 非确定性，开启后双跑零分岔）。
FORCED_LOCK = {
    "FLAPPER_POST_STEP_CB": "1",
    "FLAPPER_PHYS_CPU": "1",
    "FLAPPER_IID_LOCK": "1",
    "FLAPPER_OBSTACLE_SEED": "42",
    "FLAPPER_DET_ON": "1",
}
_REQUIRED_LOCK_KEYS = ("FLAPPER_POST_STEP_CB", "FLAPPER_PHYS_CPU",
                       "FLAPPER_IID_LOCK", "FLAPPER_OBSTACLE_SEED",
                       "FLAPPER_DET_ON")

_RUN_LETTERS = ("A", "B")


def _self_check():
    """导入期自检：焊死表四键齐备，缺键即熔断，入口不可用。"""
    missing = [k for k in _REQUIRED_LOCK_KEYS if k not in FORCED_LOCK]
    if missing:
        raise RuntimeError("统一启动入口自检失败：焊死锁表缺键 %s"
                           % "、".join(missing))


_self_check()


def _lock_conflicts(env):
    """返回冲突清单：环境里携带与焊死值不同的锁项。缺项不算冲突。"""
    bad = []
    for key, forced in FORCED_LOCK.items():
        got = env.get(key)
        if got is not None and str(got) != forced:
            bad.append("%s=%s（焊死值 %s）" % (key, got, forced))
    return bad


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _wait_milestone(log_path, timeout_s):
    """等待子进程退出并按收尾里程碑判完成性。"""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    milestone = (MILESTONE_RUN_CAP in text
                 and MILESTONE_CAP_DONE in text)
    return milestone, text


def _assemble_env(extra_env):
    """组装子进程环境，返回二元组（子环境，显式冲突清单）。

    与声明式管线的采集步同口径：先清空继承环境里全部 FLAPPER 前缀
    变量，防止外层外壳的残留试验开关漏进工况（事项 I-112 收口）；外
    层残留一律剥除不告警，显式参数里的锁冲突仍然拒绝。
    """
    merged = {k: v for k, v in os.environ.items()
              if not k.startswith("FLAPPER_")}
    merged.update({k: str(v) for k, v in (extra_env or {}).items()})
    # 禁用手动输入通道（事项 I-129 根因修复）：入口类运行一律无头无人
    # 值守，而手动通道轮询的是全系统键盘状态，机器上任何会话的击键都
    # 会顶掉自动驾驶若干步（2026-09-20 同病实录在案）。交互式手动飞行
    # 由人工直接启动主程序，不经本入口，不受影响。
    merged["FLAPPER_NO_MANUAL"] = "1"
    conflicts = _lock_conflicts(extra_env or {})
    return merged, conflicts


def launch_sim(extra_env, tag_base, desc, max_s, log_dir,
               timeout_s=420.0):
    """统一在环启动：锁盖章、双跑、逐字节复验，返回结论字典。

    extra_env 为工况环境变量（标注、试验开关、时长等），不许携带
    五项锁的冲突值，冲突即拒绝且不起任何子进程；外层环境里的
    FLAPPER 前缀残留变量一律剥除。tag_base 为标注基底，两跑分别加
    下标。log_dir 为运行日志目录。返回字典键：ok、reason、csv、
    twin_same、两跑档案与里程碑结论。
    """
    merged, conflicts = _assemble_env(extra_env)
    if conflicts:
        return {"ok": False,
                "reason": "锁值冲突拒绝运行：" + "；".join(conflicts),
                "conflicts": conflicts,
                "twin_same": None, "runs": [], "csv": None}

    busy, method = detect_isaac_processes()
    if busy:
        names = "、".join("%s 进程号 %s" % (p.get("Name"), p.get("ProcessId"))
                          for p in busy)
        return {"ok": False,
                "reason": ("检测到既有 Isaac 进程：%s；拒绝并行第二实例"
                           "（检测方式 %s）" % (names, method)),
                "twin_same": None, "runs": [], "csv": None}

    os.makedirs(log_dir, exist_ok=True)
    verdict = {"ok": False, "reason": "", "twin_same": None,
               "runs": [], "csv": None}
    csvs = []
    for letter in _RUN_LETTERS:
        tag = "%s_%s" % (tag_base, letter)
        env = dict(merged)
        env.update(FORCED_LOCK)
        env["FLAPPER_TAG"] = tag
        env["FLAPPER_DESC"] = "%s 统一入口复验跑 %s" % (desc, letter)
        env.setdefault("FLAPPER_HEADLESS", "1")
        env.setdefault("FLAPPER_FLIGHT_LOG", "1")
        env.setdefault("PYTHONUTF8", "1")
        log_path = os.path.join(log_dir, "%s_%s.log" % (tag_base, letter))
        started = time.time()
        with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
            try:
                proc = subprocess.Popen(
                    [ISAAC_PY, "main_sim.py"], cwd=_REPO_ROOT, env=env,
                    stdout=fh, stderr=subprocess.STDOUT)
            except OSError as exc:
                verdict["reason"] = "子进程启动失败：%s" % exc
                return verdict
            try:
                proc.wait(timeout=timeout_s)
                exit_code = proc.returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                exit_code = None
                timed_out = True
        milestone, _ = _wait_milestone(log_path, timeout_s)
        csv = _find_collect_csv(tag, started - 5.0)
        verdict["runs"].append({
            "tag": tag, "log": os.path.relpath(log_path, _REPO_ROOT),
            "exit_code": exit_code, "timed_out": timed_out,
            "milestone": milestone, "csv": csv})
        if timed_out or not milestone:
            verdict["reason"] = ("复验跑 %s 未完成（超时 %s，里程碑 %s），"
                                 "判启动失败"
                                 % (letter, timed_out, milestone))
            return verdict
        if not csv:
            verdict["reason"] = "复验跑 %s 未按标注定位到数据流" % letter
            return verdict
        csvs.append(csv)

    same = _sha256(csvs[0]) == _sha256(csvs[1])
    verdict["twin_same"] = same
    if not same:
        verdict["reason"] = ("背靠背复验失败：两跑数据流字节不一致，"
                             "同参数分岔，按事项 I-109 协议该工况数据"
                             "不可用")
        return verdict
    verdict["ok"] = True
    verdict["csv"] = csvs[0]
    verdict["reason"] = "两跑逐字节一致，标准锁五项在案"
    return verdict


if __name__ == "__main__":
    print("统一在环启动入口。锁四项：%s" % FORCED_LOCK)
    print("调用方式见模块文件头，命令行不直接起跑。")
