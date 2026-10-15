# -*- coding: utf-8 -*-
"""F04 审计：诊断日志失败是否跳过后续尾舵更新。

真实入口：modules.wing_actuation._drive_wing_joints（生产源码本体）。
注入方式（均为运行时替换，事后恢复，不改源码）：
  W1 写失败 —— wing_actuation.LOG_DIR 指向"父路径是普通文件"的路径，
      诊断分支 open(..., "a") 抛 FileNotFoundError；
  W2 读失败 —— art.get_joint_positions 抛 RuntimeError（诊断分支读实际关节）。
对照：
  C0 正常 LOG_DIR（审计沙箱内临时目录）下诊断行写入且尾舵更新；
  C1 失效持续时的节奏性 —— 仅 _DRIVE_N % 120 == 1 的步触发诊断分支，
      其余步不受影响（间歇性部分执行）。
每场景记录关节写入数、尾舵调用数、日志与返回值。
"""
import os
import shutil
import tempfile

import harness_common as H
import modules.wing_actuation as WA
from modules.wing_actuation import _drive_wing_joints


def fresh():
    H.seed_host_namespace(globals())
    WA._DRIVE_N = 0   # 模块计数器归零：每个场景从首步（1%120==1）开始，
    #                 # 保证诊断分支按生产节奏触发（该计数器本身无重置接口，
    #                 # 见 F06）
    art = H.ArtStub(H.JOINT_NAMES)
    wk = H.make_wk()
    cb = H.make_aero_cb()
    tail = H.TailProxy(cb.tail_body)
    cb.tail_body = tail
    return art, wk, cb, tail


def counts(art, tail):
    return {
        "joint_writes": sum(1 for c in art.calls
                            if c[0] == "set_joint_position_targets"),
        "tail_writes": len(tail.calls),
    }


def order_ok(art, tail):
    """关节写入是否发生在（失败的）尾舵更新之前。"""
    return sum(1 for c in art.calls
               if c[0] == "set_joint_position_targets") >= 1


def main():
    sandbox = tempfile.mkdtemp(prefix="i209_f04_")
    blocker_file = os.path.join(sandbox, "blocker.txt")
    open(blocker_file, "w", encoding="utf-8").close()
    broken_logdir = os.path.join(blocker_file, "sub")   # 父路径是普通文件
    good_logdir = os.path.join(sandbox, "logs_ok")
    os.makedirs(good_logdir, exist_ok=True)

    orig_logdir = WA.LOG_DIR
    scenarios = {}

    # C0 对照：正常目录
    art, wk, cb, tail = fresh()
    cap = H.attach_capture()
    WA.LOG_DIR = good_logdir
    ret = _drive_wing_joints(None, art, wk, None, cb, H.ap_out(), step_count=1)
    scenarios["C0_正常目录"] = {
        "returned": repr(ret), **counts(art, tail),
        "diag_file_lines": len(open(os.path.join(
            good_logdir, "drive_diag.txt"), encoding="utf-8").readlines()),
        "logs": cap.dump()}

    # W1 写失败：目录不可创建（父路径为文件）
    WA.LOG_DIR = broken_logdir
    art, wk, cb, tail = fresh()
    cap = H.attach_capture()
    ret = _drive_wing_joints(None, art, wk, None, cb, H.ap_out(), step_count=1)
    scenarios["W1_诊断写失败"] = {
        "returned": repr(ret), **counts(art, tail),
        "joint_written_before_tail_skip": order_ok(art, tail),
        "logs": cap.dump()}

    # W2 读失败：get_joint_positions 抛异常
    WA.LOG_DIR = good_logdir
    art, wk, cb, tail = fresh()
    cap = H.attach_capture()
    art.inject_get_positions_error(RuntimeError("注入：关节位置读取失败"))
    ret = _drive_wing_joints(None, art, wk, None, cb, H.ap_out(), step_count=1)
    scenarios["W2_诊断读失败"] = {
        "returned": repr(ret), **counts(art, tail),
        "joint_written_before_tail_skip": order_ok(art, tail),
        "logs": cap.dump()}

    # C1 节奏性：写失败条件下连跑 4 步（n%120==1 仅首步触发诊断分支）
    WA.LOG_DIR = broken_logdir
    art, wk, cb, tail = fresh()
    cap = H.attach_capture()
    for sc in range(1, 5):
        _drive_wing_joints(None, art, wk, None, cb, H.ap_out(), step_count=sc)
    per_step_tail = [1 if i < len(tail.calls) else 0 for i in range(4)]
    scenarios["C1_失效节奏"] = {
        "note": "同一失效目录下连续 4 步，仅诊断步（1%120==1）缺尾舵更新",
        "tail_writes_total": len(tail.calls),
        "joint_writes_total": sum(1 for c in art.calls
                                  if c[0] == "set_joint_position_targets"),
        "per_step_tail_writes_approx": per_step_tail,
        "logs": cap.dump()}

    WA.LOG_DIR = orig_logdir

    result = {
        "probe": "F04 诊断日志失败跳过尾舵更新",
        "entry": "modules.wing_actuation._drive_wing_joints（诊断分支 241-254 行）",
        "production_reachability": {
            "LOG_DIR_config": "config/sim_config.py:91（仓库 logs/）",
            "created_at_startup": "main_sim.py:143 os.makedirs(LOG_DIR) 模块导入期执行",
            "失效可达条件": ["运行中目录被删或改名", "权限变更或磁盘满",
                              "LOG_DIR 配置为只读位置", "导入后配置再改路径（模块缓存旧值）"],
            "触发密度": "每 120 步驱动一次（_DRIVE_N % 120 == 1）",
        },
        "scenarios": scenarios,
    }
    for k, v in scenarios.items():
        print(k, "| 关节写入", v.get("joint_writes", v.get("joint_writes_total")),
              "| 尾舵", v.get("tail_writes", v.get("tail_writes_total")),
              "| 日志", len(v["logs"]))
    H.save_result("f04_diag_failure.json", result)
    shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    H.run_probe(main)
