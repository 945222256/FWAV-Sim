# -*- coding: utf-8 -*-
"""F05 子进程运行器：modules.aero 整体不可导入时的行为核对。

两种探测：
  import_solver —— 尝试 import modules.aero_solver（模块顶层即
    from modules.aero import ...，预期直接 ImportError，工厂不可达）；
  import_factory_only —— 通过源文件位置直接加载 aero_solver.py 而绕过
    包导入不可行（其顶层同样依赖 modules.aero），故此项改核验：
    工厂内部 ImportError 分支要触发，前提是 aero_solver 已加载成功而
    from modules.aero import MODEL_REGISTRY 失败；模块顶层已经 import 过
    同一符号，故该分支在当前文件布局下不可达（结论以运行证据记录）。
"""
import importlib.abc
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "..", ".."))
sys.path.insert(0, REPO)


class BlockAero(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "modules.aero" or fullname.startswith("modules.aero."):
            raise ImportError("注入：modules.aero 被禁止导入")
        return None


out = {"mode": "no_aero"}
sys.meta_path.insert(0, BlockAero())
try:
    import modules.aero_solver  # noqa: F401
    out["import_modules.aero_solver"] = "succeeded"
    try:
        from modules.aero_solver import create_aero_callback
        cb = create_aero_callback(None, None, None, None,
                                  model_name="attached_flow")
        out["factory_result"] = type(cb.model).__name__
    except Exception as e:
        out["factory_result_error"] = repr(e)
except ImportError as e:
    out["import_modules.aero_solver"] = "ImportError: %s" % e
    out["factory_reachable"] = False
out["modules_aero_in_sys_modules"] = any(
    m == "modules.aero" or m.startswith("modules.aero.")
    for m in sys.modules)
print("NO_AERO_RESULT>" + json.dumps(out))
