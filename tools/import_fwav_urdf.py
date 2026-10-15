# -*- coding: utf-8 -*-
"""
import_fwav_urdf.py — fwav_15dof.urdf → fwav_15dof.usd（v3）
=============================================================
v1/v2 教训：
  • dest_path 直存 → 空壳分层文件
  • 不开 file-backed stage 时导入器写进 in-memory stage，save_as_stage 存的是空舞台
  • kit 吞 print → 诊断全部写 logs/import_diag.txt（flush）
v3 路线：new_stage() 开空白舞台 → 内存导入 → 校验 9 关节 → stage.Export 平坦化落盘

运行：g:\\Playground\\isaac_sim_5.1.0\\python.bat tools\\import_fwav_urdf.py [--force]
"""
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URDF = os.path.join(ROOT, "assets", "flapper", "fwav_15dof_from_group.urdf")
USD = os.path.join(ROOT, "assets", "flapper", "fwav_15dof.usd")
CFGDIR = os.path.join(ROOT, "assets", "flapper", "configuration")
DIAG = os.path.join(ROOT, "logs", "import_diag.txt")

NEED_JOINTS = ["flap_L", "flap_R", "twist_L", "twist_R",
               "fold_L", "fold_R", "tail_yaw", "tail_L", "tail_R"]


def log(msg):
    with open(DIAG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        f.flush()
    try:
        print(f"[import] {msg}", flush=True)
    except Exception:
        pass


open(DIAG, "w").close()
force = "--force" in sys.argv
if os.path.isfile(USD) and not force:
    log(f"已存在 {USD}，--force 强制重转")
    sys.exit(0)

for junk in (USD, CFGDIR):
    if os.path.isfile(junk):
        os.remove(junk)
    elif os.path.isdir(junk):
        shutil.rmtree(junk)

from isaacsim import SimulationApp
simulation_app = SimulationApp({"headless": True})

import omni.kit.commands
import omni.usd
from isaacsim.asset.importer.urdf import _urdf

cfg = _urdf.ImportConfig()


def try_set(attr, val):
    if hasattr(cfg, attr):
        try:
            setattr(cfg, attr, val)
            log(f"cfg.{attr} = {val}")
            return
        except Exception as e:
            log(f"cfg.{attr} 设置失败: {e}")
    log(f"⚠ 无字段 {attr}（用默认值）")


try_set("merge_fixed_joints", False)
try_set("make_default_prim", True)
try_set("fix_base", False)             # 浮动基座：循迹控制器才能动机体（预览固定版改 True）
try_set("create_physics_scene", False)
try_set("replace_cylinders_with_capsules", True)
try_set("self_collide", False)
try_set("flatten_tree", False)

drv = None
if hasattr(_urdf, "UrdfJointTargetType"):
    for name in ("JOINT_DRIVE_POSITION", "JOINT_POSITION", "POSITION"):
        drv = getattr(_urdf.UrdfJointTargetType, name, None)
        if drv is not None:
            log(f"驱动枚举命中 {name}")
            break
if drv is not None:
    try_set("default_drive_type", drv)
try_set("drive_angular_stiffness", 30.0)
try_set("drive_angular_damping", 3.0)

# ── 关键1：先开一个空白 file-backed 舞台，让导入器写进"当前"舞台 ──
ctx = omni.usd.get_context()
ctx.new_stage()
log(f"blank stage opened: root_layer={ctx.get_stage().GetRootLayer().identifier}")

log(f"导入 {URDF}")
status, prim_path = omni.kit.commands.execute(
    "URDFParseAndImportFile", urdf_path=URDF, import_config=cfg)
log(f"status={status} prim={prim_path}")

# ── 关键2：在当前舞台上校验 ──
stage = ctx.get_stage()
found, links = [], []
for prim in stage.Traverse():
    tn = prim.GetTypeName()
    if tn in ("PhysicsRevoluteJoint", "PhysicsJoint", "RevoluteJoint"):
        found.append(prim.GetName())
    elif tn in ("Rigid", "RigidBody", "Xform") and prim.GetName() not in ("World",):
        links.append(prim.GetName())
log(f"关节({len(found)}): {found}")
log(f"前20个prim: {links[:20]}")
missing = [j for j in NEED_JOINTS if j not in found]
if missing:
    log(f"✘ 缺关节 {missing}，导入失败")
    simulation_app.close()
    sys.exit(2)

# ── 关键3：平坦化导出（单文件，无子层引用）──
usd_url = USD.replace("\\", "/")
ok = stage.Export(usd_url)
log(f"stage.Export({usd_url}) → {ok}")
for _ in range(20):
    if os.path.isfile(USD):
        break
    time.sleep(0.5)

if os.path.isfile(USD):
    sz = os.path.getsize(USD)
    log(f"OK → {USD} ({sz} bytes)")
    log("（crate 二进制压缩，token 字节搜索无意义——开箱校验走 verify_imported_usd.py）")
else:
    log("✘ 未写出文件")
    simulation_app.close()
    sys.exit(3)

simulation_app.close()
