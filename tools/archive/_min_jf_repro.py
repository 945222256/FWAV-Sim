# -*- coding: utf-8 -*-
"""
_min_jf_repro.py — I-009 最小复现器：PhysxJointAPI.Apply 在哪版资产上杀进程
==========================================================================
背景：probe_jforce_i009 模式 A 在新资产（ec3ede6 惯量纪元重导入版）上
死于 PhysxJointAPI.Apply 首次调用（干净退出 0，无异常无日志，与
0928 原崩溃的"零行即崩"形态同族）。本复现器把现场剥到最小：
起 app → 开 stage → 引资产 → 逐关节 Apply+jointForceEnabled → 哨兵。

判据先于数据：
  哨兵 "MIN-REPRO-OK n=N" 在＝存活；缺席＝进程死亡。
  新版死而旧版活 → 资产版本是决定变量（今晨重导入引入）。
  两版都活 → probe_jforce_i009 的死因在更早环节的交互（DriveAPI/MassAPI
  先行写入），需回到探针逐环节二分。

用法：
  set REPRO_ASSET=<USD 路径> && g:\Playground\isaac_sim_5.1.0\python.bat tools\_min_jf_repro.py
  （模式经环境变量：argv 会被 kit 劫持，勿用）
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ASSET = os.environ.get("REPRO_ASSET",
                       os.path.join(ROOT, "assets/flapper/fwav_15dof.usd"))
# REPRO_MODE: apply=逐关节Apply（原版） | count=只数关节+心跳10秒不Apply
REPRO_MODE = os.environ.get("REPRO_MODE", "apply")
OUT = os.environ.get("REPRO_OUT", os.path.join(ROOT, "logs/_min_jf_repro.txt"))


def log(m):
    line = str(m)
    print(line, flush=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


open(OUT, "w").close()
log("[min_repro] asset=%s" % ASSET)

from isaacsim import SimulationApp
app = SimulationApp({"headless": True})

try:
    import omni.usd
    from isaacsim.core.utils.stage import add_reference_to_stage
    from pxr import UsdPhysics, PhysxSchema

    USD = ASSET.replace("\\", "/")
    omni.usd.get_context().new_stage()
    add_reference_to_stage(usd_path=USD, prim_path="/World/Test")
    stage = omni.usd.get_context().get_stage()

    joints = [p for p in stage.Traverse()
              if p.GetTypeName() == "PhysicsRevoluteJoint"]
    log("revolute 关节 %d 个，mode=%s" % (len(joints), REPRO_MODE))
    if REPRO_MODE == "count":
        for k in range(10):
            time.sleep(1.0)
            log("心跳 %d s（无 Apply，跨过 Startup Complete 时刻）" % (k + 1))
        log("MIN-REPRO-OK mode=count n=%d" % len(joints))
    elif REPRO_MODE == "apply_only":
        n = 0
        for prim in joints:
            log("Apply 前 %s" % prim.GetPath())
            api = PhysxSchema.PhysxJointAPI.Apply(prim)
            n += 1
            log("Apply 后 %s（未 Create）" % prim.GetPath())
        log("MIN-REPRO-OK mode=apply_only n=%d" % n)
    else:
        n = 0
        for prim in joints:
            log("Apply 前 %s" % prim.GetPath())
            api = PhysxSchema.PhysxJointAPI.Apply(prim)
            log("Apply 后 %s" % prim.GetPath())
            api.CreateJointForceEnabledAttr(True)
            n += 1
            log("Create 后 %s" % prim.GetPath())
        log("MIN-REPRO-OK n=%d" % n)
finally:
    try:
        app.close()
    except Exception:
        pass
