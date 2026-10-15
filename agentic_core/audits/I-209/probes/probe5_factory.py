# -*- coding: utf-8 -*-
"""F05 审计：未知模型名称是否被接受并返回默认模型。

真实入口：modules.aero_solver.create_aero_callback（生产源码本体）。
矩阵：
  1 未知名（"audit_invalid_model_name"、"sane_dickenson" 拼错形态）
  2 空字符串 / None（回落 cfg.AERO_MODEL，当前为 sane_dickinson_3d）
  3 全部合法名（attached_flow / dynamic_stall / sane_dickinson /
    sane_dickinson_3d）——同时核对登记表类与兜底类是否同一
  4 导入失败（modules.aero 被禁止导入时工厂与模块层各自的行为，
    另以子进程 f05_runner_no_aero.py 核对）
记录：返回类、model 类、model_name 标记、是否抛异常、日志。
"""
import json
import os
import subprocess
import sys

import harness_common as H
from modules.aero_solver import create_aero_callback


def probe_name(name):
    cap = H.attach_capture()
    cb = create_aero_callback(None, None, None, None, model_name=name)
    return {
        "requested": name,
        "callback_class": type(cb).__name__,
        "model_class": type(cb.model).__name__,
        "model_name_attr": getattr(cb, "model_name", "<未设置>"),
        "raised": False,
        "logs": cap.dump(),
    }


def main():
    names = [
        "audit_invalid_model_name",
        "sane_dickenson",            # 拼写错误形态（正确为 sane_dickinson_3d）
        "attached_flow",
        "dynamic_stall",
        "sane_dickinson_3d",
        "sane_dickinson_3d ",        # 带空白
        "",
    ]
    results = []
    for n in names:
        try:
            results.append(probe_name(n))
        except Exception as e:
            results.append({"requested": n, "raised": repr(e)})
    # None → 走 cfg.AERO_MODEL
    try:
        results.append(probe_name(None))
    except Exception as e:
        results.append({"requested": None, "raised": repr(e)})

    import config.sim_config as cfg
    from modules.aero import MODEL_REGISTRY

    # 登记表对照：合法名 "sane_dickinson" 在登记表中的类 vs 工厂兜底返回的类
    reg = {k: v.__name__ for k, v in MODEL_REGISTRY.items()}

    # 子进程：modules.aero 整体不可导入时，aero_solver 与工厂的行为
    runner = subprocess.run(
        [sys.executable, "f05_runner_no_aero.py"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.abspath(__file__)), timeout=120)
    line = None
    for ln in runner.stdout.splitlines():
        if ln.startswith("NO_AERO_RESULT>"):
            line = ln[len("NO_AERO_RESULT>"):]
    no_aero = json.loads(line) if line else {
        "returncode": runner.returncode, "stdout": runner.stdout[-400:],
        "stderr": runner.stderr[-400:]}

    result = {
        "probe": "F05 未知模型名返回默认模型",
        "entry": "modules.aero_solver.create_aero_callback",
        "cfg_AERO_MODEL_default": cfg.AERO_MODEL,
        "production_call_site": "modules/flapper_loader.py:365（不传 model_name，"
                                "用 cfg.AERO_MODEL；可被 FLAPPER_AERO_MODEL 环境变量"
                                "覆盖，见 modules/sim_env_bridge.py:55-63）",
        "model_registry": reg,
        "factory_tuple_names": ["attached_flow", "dynamic_stall",
                                "sane_dickinson_3d"],
        "results": results,
        "no_aero_subprocess": no_aero,
        "approved_fallback_declaration": "模块 docstring：『sane_dickinson（默认/"
                                         "包不可用兜底）』；ImportError 分支的"
                                         " warning 文案在 aero_solver.py:532",
    }
    for r in results:
        print(r.get("requested"), "->", r.get("model_class"),
              "| model_name:", r.get("model_name_attr", r.get("raised")),
              "| raised:", r.get("raised", False))
    print("no_aero:", json.dumps(no_aero, ensure_ascii=False)[:300])
    H.save_result("f05_factory.json", result)


if __name__ == "__main__":
    H.run_probe(main)
