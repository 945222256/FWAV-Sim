# -*- coding: utf-8 -*-
"""F06 审计：驱动依赖主程序的隐含共享状态。

证据分四部分：
1. 静态清单：共享量、读写者、初始化与重置点（file:line 引用）。
2. 情景一（脚本启动）：python main_sim.py 下 __main__ 即 main_sim，
   绑定正确 —— 以源码行号与绑定式核对（无 Isaac 不实机导入）。
3. 情景二（其他入口先导入 / 命名空间缺失）：子进程实测两种缺陷形态。
4. 情景三（同进程连续两次实验）：实测跨实验残留（计数器、缓存、
   一次性事件标志）。
"""
import json
import os
import subprocess
import sys

import harness_common as H
import modules.wing_actuation as WA
from modules.wing_actuation import _drive_wing_joints

SHARED_TABLE = [
    {"var": "_WING_TGT_CACHE",
     "writer": ["modules/wing_actuation.py:236（每步写）"],
     "reader": ["main_sim.py:837（示波器外发）", "main_sim.py:1231（ctrl_scope）"],
     "init": "main_sim.py:1333 模块层 = None", "reset": "无（跨实验不重置）"},
    {"var": "_DA_CACHE",
     "writer": ["modules/wing_actuation.py:133"],
     "reader": ["modules/flight_recorder.py:161"],
     "init": "无模块层定义（记录器 _G.get 缺省 0.0）", "reset": "无"},
    {"var": "_FOLD_T_CACHE",
     "writer": ["modules/wing_actuation.py:134"],
     "reader": ["modules/flight_recorder.py:206"], "init": "同上缺省 0.0",
     "reset": "无"},
    {"var": "_CTRL_MODE_CACHE",
     "writer": ["modules/wing_actuation.py:80,86,136"],
     "reader": ["modules/flight_recorder.py:208"], "init": "缺省 -1",
     "reset": "无"},
    {"var": "_WING_STATE_CACHE",
     "writer": ["modules/wing_actuation.py:163-171"],
     "reader": ["modules/flight_recorder.py:214-218"], "init": "缺省 None",
     "reset": "无"},
    {"var": "_DP_STATE",
     "writer": ["main_sim.py:1780（模块层初始化）",
                "main_sim.py:1864（_apply_tether_and_dp 每步整体重绑定）"],
     "reader": ["modules/wing_actuation.py:84（直接下标访问）",
                "main_sim.py:1388-1390（遥测）"],
     "init": "main_sim.py:1780", "reset": "每步重绑定（governed）"},
    {"var": "_SIDE_PULSE_ON/_T/_DVY/_DONE",
     "writer": ["modules/sim_env_bridge.py:491-494（main() 桥接段）",
                "modules/wing_actuation.py:191（_DONE 置 True）"],
     "reader": ["modules/wing_actuation.py:187-202"],
     "init": "桥接段", "reset": "_DONE 仅桥接段复位；跨实验不自动复位"},
    {"var": "_FW_IID_ULP",
     "writer": ["main_sim.py:706（main() 装订）",
                "modules/wing_actuation.py:235（触发后置 None）"],
     "reader": ["modules/wing_actuation.py:223-235"], "init": "无（_G.get）",
     "reset": "触发一次后置 None；main() 重跑重新装订"},
    {"var": "（模块级）_DRIVE_N",
     "writer": ["modules/wing_actuation.py:242（自增）"],
     "reader": ["modules/wing_actuation.py:243（诊断节奏）"],
     "init": "模块层 = 0", "reset": "无任何重置接口"},
]


def run_sub(mode):
    p = subprocess.run([sys.executable, "f06_runner_scenarios.py", mode],
                       capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.abspath(__file__)),
                       timeout=120)
    line = None
    for ln in p.stdout.splitlines():
        if ln.startswith("F06_RESULT>"):
            line = ln[len("F06_RESULT>"):]
    return json.loads(line) if line else {
        "returncode": p.returncode, "stdout": p.stdout[-400:],
        "stderr": p.stderr[-400:]}


class PulseView(H.ViewStub):
    def __init__(self):
        super().__init__()
        self.set_calls = []

    def set_velocities(self, v):
        import numpy as np
        self.set_calls.append(np.asarray(v).tolist())


def two_experiments():
    """同进程连续两次实验：跨实验残留实测。"""
    sandbox = os.path.join(H.RESULTS_DIR, "f06_sandbox_logs")
    os.makedirs(sandbox, exist_ok=True)
    orig_logdir = WA.LOG_DIR
    WA.LOG_DIR = sandbox

    # 实验 1：触发侧滑脉冲（_DONE 置 True），写满缓存
    H.seed_host_namespace(globals())
    view = PulseView()
    art1 = H.ArtStub(H.JOINT_NAMES)
    wk1 = H.make_wk()
    cb1 = H.make_aero_cb(view)
    globals()["_SIDE_PULSE_ON"] = True
    globals()["_SIDE_PULSE_T"] = 0.0     # t=0 立即触发
    _drive_wing_joints(None, art1, wk1, None, cb1, H.ap_out(dlag=0.01),
                       step_count=1)
    n_pulse_exp1 = len(view.set_calls)
    caches_after_exp1 = {k: globals()[k] for k in
                         ("_WING_TGT_CACHE", "_DA_CACHE", "_WING_STATE_CACHE",
                          "_CTRL_MODE_CACHE")}
    drive_n_after_exp1 = WA._DRIVE_N
    done_after_exp1 = globals()["_SIDE_PULSE_DONE"]

    # 实验 2：全新对象重建（同进程），先以空关节表调用（模拟首步尚未建好）
    WA._DRIVE_N_backup = None
    art2 = H.ArtStub([])                 # joint_names 空 → 整步跳过
    _drive_wing_joints(None, art2, H.make_wk(), None, H.make_aero_cb(),
                       H.ap_out(), step_count=1)
    caches_still_stale = {k: globals()[k] for k in
                          ("_WING_TGT_CACHE", "_DA_CACHE",
                           "_WING_STATE_CACHE", "_CTRL_MODE_CACHE")}
    stale = all(caches_still_stale[k] == caches_after_exp1[k]
                for k in caches_after_exp1)

    # 实验 2 续：关节表就绪后首步；侧滑脉冲重新武装前不再触发；
    # _DRIVE_N 不归零，核对诊断节奏是否跨实验延续
    globals()["_SIDE_PULSE_ON"] = True
    globals()["_SIDE_PULSE_T"] = 0.0
    view2 = PulseView()
    art2b = H.ArtStub(H.JOINT_NAMES)
    cb2 = H.make_aero_cb(view2)
    diag_lines_before = len(open(os.path.join(
        sandbox, "drive_diag.txt"), encoding="utf-8").readlines()) \
        if os.path.exists(os.path.join(sandbox, "drive_diag.txt")) else 0
    _drive_wing_joints(None, art2b, H.make_wk(), None, cb2, H.ap_out(),
                       step_count=1)
    diag_lines_after = len(open(os.path.join(
        sandbox, "drive_diag.txt"), encoding="utf-8").readlines()) \
        if os.path.exists(os.path.join(sandbox, "drive_diag.txt")) else 0
    n_pulse_exp2_without_rearm = len(view2.set_calls)
    drive_n_now = WA._DRIVE_N
    WA.LOG_DIR = orig_logdir
    return {
        "exp1_pulse_fired": n_pulse_exp1,
        "exp1_side_pulse_done_flag": done_after_exp1,
        "caches_after_exp1_present": True,
        "exp2_early_step_kept_exp1_caches": stale,
        "exp2_pulse_fired_without_rearm": n_pulse_exp2_without_rearm,
        "exp2_diag_line_written_on_its_first_step": diag_lines_after - diag_lines_before,
        "drive_n_continuity": {"after_exp1": drive_n_after_exp1,
                               "after_exp2_first_step": drive_n_now,
                               "note": "实验二首步 _DRIVE_N=2，2%120≠1，"
                                       "诊断行不写——新实验的节奏被上一实验"
                                       "的计数拖走"},
        "note": "生产主程序 main() 重跑会经 sim_env_bridge 重新武装 _DONE=False；"
                "不走 main() 桥接的复用路径则残留",
    }


def main():
    result = {
        "probe": "F06 驱动依赖主程序隐含共享状态",
        "entry": "modules/wing_actuation.py:31 "
                 "（_G = sys.modules.get('main_sim') or sys.modules['__main__']）",
        "shared_table": SHARED_TABLE,
        "scenario_script_startup": {
            "evidence": "main_sim.py 以 __main__ 运行：模块自身即 __main__，"
                        "wing_actuation 在 main_sim.py:126 导入时 "
                        "sys.modules 无 'main_sim' 条目，_G 绑定 __main__ "
                        "（即 main_sim 命名空间）——绑定式与导入顺序以源码"
                        " 核对（main_sim.py:126 为顶层导入；Isaac SimulationApp "
                        "先于其创建）",
            "binding_correct_in_production": True},
        "scenario_missing_dp": run_sub("missing_dp"),
        "scenario_order_divergence": run_sub("order_divergence"),
        "scenario_two_experiments": two_experiments(),
        "refactor_impact_estimate": {
            "改为显式参数/状态对象需触碰": [
                "modules/wing_actuation.py（_G 引用 14 处，改参数传递）",
                "main_sim.py:1216 调用点（传状态对象）",
                "modules/flight_recorder.py:161-218（5 类缓存读取改接口）",
                "modules/sim_env_bridge.py:491-494（脉冲配置写入改接口）",
                "main_sim.py:706（ULP 装订改接口）"],
            "估计规模": "约 6 个文件、30~40 行签名与取值改动；行为等价可由"
                       "本审计探针（F01/F02/F04 脚本）作回归基线",
        },
    }
    print("missing_dp:", json.dumps(result["scenario_missing_dp"],
                                    ensure_ascii=False)[:220])
    print("order_divergence:", json.dumps(result["scenario_order_divergence"],
                                          ensure_ascii=False)[:260])
    print("two_experiments:", json.dumps(result["scenario_two_experiments"],
                                         ensure_ascii=False)[:260])
    H.save_result("f06_shared_state.json", result)


if __name__ == "__main__":
    H.run_probe(main)
