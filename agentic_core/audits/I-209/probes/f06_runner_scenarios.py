# -*- coding: utf-8 -*-
"""F06 子进程运行器：宿主命名空间缺失 / 绑定顺序漂移 两情景。

用法：python f06_runner_scenarios.py missing_dp|order_divergence
- missing_dp：__main__（本运行器）不播种任何宿主共享变量，直接以
  man_ch=None、ap_out=None 调用真实驱动 → 预期 _G["_DP_STATE"] KeyError
  被外层吞掉，整步无写入。
- order_divergence：先 import wing_actuation（_G 绑定到本运行器 __main__），
  再注册一个含宿主变量的假 main_sim 模块，随后 import flight_recorder
  （其 _G 绑定到假 main_sim）→ 驱动写入与记录器读取落在两个不同命名空间。
"""
import importlib.util
import json
import logging
import os
import sys
import types

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "..", ".."))
sys.path.insert(0, REPO)

mode = sys.argv[1]
records = []


class Cap(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.rows = []

    def emit(self, record):
        self.rows.append({"level": record.levelname,
                          "msg": record.getMessage()})


cap = Cap()
logging.getLogger().addHandler(cap)
logging.getLogger().setLevel(logging.DEBUG)

if mode == "missing_dp":
    import harness_common as H  # 只为复用替身，不播种命名空间
    art = H.ArtStub(H.JOINT_NAMES)
    wk = H.make_wk()
    cb = H.make_aero_cb()
    from modules.wing_actuation import _drive_wing_joints
    ret = _drive_wing_joints(None, art, wk, None, cb, None, step_count=1)
    out = {
        "mode": mode,
        "host_seeded": False,
        "returned": repr(ret),
        "joint_writes": len(art.calls),
        "tail_writes": 0,
        "logs": cap.rows,
        "note": "宿主未定义 _DP_STATE；自动/手动均为 None 时驱动读到该键",
    }
elif mode == "order_divergence":
    import harness_common as H

    # 步骤 1：无 main_sim 时先导入 wing_actuation（绑定本 __main__）
    assert "main_sim" not in sys.modules
    import modules.wing_actuation as WA1
    binding_before = WA1._G is sys.modules["__main__"].__dict__

    # 步骤 2：注册含宿主变量的假 main_sim（模拟其后才发生的主程序导入）
    fake = types.ModuleType("main_sim")
    fake._DP_STATE = {"freq": None, "tension": 0.0, "mode": "free"}
    fake._WING_TGT_CACHE = None
    fake._WING_STATE_CACHE = None
    sys.modules["main_sim"] = fake

    # 步骤 3：导入记录器（其 _G 绑定到假 main_sim）
    import modules.flight_recorder as FR
    binding_fr = FR._G is fake.__dict__

    # 步骤 4：运行真实驱动 → 写入落在哪个命名空间？
    art = H.ArtStub(H.JOINT_NAMES)
    wk = H.make_wk()
    cb = H.make_aero_cb()
    WA1._drive_wing_joints(None, art, wk, None, cb, H.ap_out(), step_count=1)

    out = {
        "mode": mode,
        "wing_actuation_bound_to_runner_main": binding_before,
        "flight_recorder_bound_to_fake_main_sim": binding_fr,
        "driver_wrote_into_runner": "_WING_STATE_CACHE" in sys.modules["__main__"].__dict__,
        "recorder_sees": FR._G.get("_WING_STATE_CACHE", "<读不到>"),
        "fake_main_sim_sees": fake.__dict__.get("_WING_STATE_CACHE", "<无>"),
        "joint_writes": len(art.calls),
        "logs": cap.rows,
        "note": "驱动与记录器各绑不同命名空间，回放列读到缺省值",
    }
else:
    raise SystemExit("unknown mode")

print("F06_RESULT>" + json.dumps(out, ensure_ascii=False, default=str))
