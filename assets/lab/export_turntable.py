# -*- coding: utf-8 -*-
"""export_turntable.py — 参数实验室固定渲染资产离线导出（I-130，四稿四之一）。

在 Blender 无头模式运行：从 fwav_15dof.usd 导出中性灰 glb 与二十四帧
转台序列（WebP，透明底），并写资产清单 manifest.json。导出为离线一次
性工序，页面只加载产物，不解析 usd 或 urdf 源文件。

用法：
  blender --background --python export_turntable.py -- <仓库根> <输出目录>
"""
import bpy
import hashlib
import json
import math
import os
import subprocess
import sys
import datetime

argv = sys.argv[sys.argv.index("--") + 1:]
ROOT = os.path.abspath(argv[0])
OUT_DIR = os.path.abspath(argv[1])
SRC = os.path.join(ROOT, "assets", "flapper", "fwav_15dof.usd")
os.makedirs(OUT_DIR, exist_ok=True)

RENDER_W, RENDER_H = 4096, 3076          # 四倍超采样，成品缩到四分之一
FINAL_W, FINAL_H = 1024, 769
FRAMES = 24
DURATION_S = 4.0
NEUTRAL = (0.62, 0.62, 0.62)             # 中性灰


def source_version():
    md5 = hashlib.md5(open(SRC, "rb").read()).hexdigest()
    try:
        git = subprocess.run(
            ["git", "log", "-1", "--format=%h", "--", "assets/flapper/fwav_15dof.usd"],
            cwd=ROOT, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        git = ""
    return md5, git


# ── 场景清理与导入 ────────────────────────────────────────────────
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.usd_import(filepath=SRC)

# USD 以 prototypes 集合实例摆放整机；先把实例落实为真实对象，
# 否则删掉实例空物体等于删掉全部可见几何。
bpy.ops.object.select_all(action="SELECT")
bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
bpy.ops.object.duplicates_make_real(use_base_parent=False, use_hierarchy=False)

objs = list(bpy.data.objects)
for ob in objs:
    if ob.type in ("CAMERA", "LIGHT", "EMPTY"):
        bpy.data.objects.remove(ob, do_unlink=True)

mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
if not mesh_objs:
    raise SystemExit("USD 导入后没有网格对象，检查源文件")

# 中性灰材质（glb 与渲染共用）
mat = bpy.data.materials.new("lab_neutral_gray")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
bsdf.inputs["Base Color"].default_value = (*NEUTRAL, 1.0)
bsdf.inputs["Roughness"].default_value = 0.55
for ob in mesh_objs:
    ob.data.materials.clear()
    ob.data.materials.append(mat)

# ── 转台空物体：整机绕立轴旋转 ────────────────────────────────────
import mathutils

pivot = bpy.data.objects.new("turntable", None)
bpy.context.collection.objects.link(pivot)
for ob in mesh_objs:
    ob.parent = pivot
    ob.matrix_parent_inverse = mathutils.Matrix()

# ── 相机：四分之三前视，看向原点 ─────────────────────────────────
cam_data = bpy.data.cameras.new("lab_cam")
cam_data.lens = 60
cam = bpy.data.objects.new("lab_cam", cam_data)
bpy.context.collection.objects.link(cam)
bpy.context.scene.camera = cam

scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = RENDER_W
scene.render.resolution_y = RENDER_H
scene.render.film_transparent = True
sh = scene.display.shading
sh.light = "STUDIO"
sh.color_type = "SINGLE"
sh.single_color = NEUTRAL
for attr in ("show_shadows", "show_cavity_viewport", "show_object_outline"):
    try:
        setattr(sh, attr, True if attr == "show_shadows" else False)
    except Exception:
        pass
try:
    scene.display.render_aa = "VERY_HIGH"
except Exception:
    pass


def model_sphere():
    """整机世界坐标包围盒的外接球（以转轴原点为球心）。

    旋转绕原点立轴，用离原点最远的包围盒角点做球半径，保证二十四个
    角度全部完整入画。"""
    import mathutils
    far = 0.0
    center = mathutils.Vector((0.0, 0.0, 0.0))
    n = 0
    for ob in mesh_objs:
        for corner in ob.bound_box:
            p = ob.matrix_world @ mathutils.Vector(corner)
            far = max(far, p.length)
            center += p
            n += 1
    center /= max(n, 1)
    return far, center


CAM_DIST, CAM_TARGET = [None] * 2


def place_camera(azim_deg, elev_deg=18.0):
    """把相机放到方位角仰角确定的轨道点上，看向包围球心。"""
    import mathutils
    global CAM_DIST, CAM_TARGET
    if CAM_DIST is None:
        radius, target = model_sphere()
        CAM_TARGET = target
        # 视场按横竖向较小者保守估计，留百分之十二边距
        sensor_w = min(cam_data.sensor_width,
                       cam_data.sensor_width * RENDER_H / RENDER_W)
        fov = 2.0 * math.atan(0.5 * sensor_w / cam_data.lens)
        CAM_DIST = radius / math.tan(0.5 * fov) * 1.04
    az, el = math.radians(azim_deg), math.radians(elev_deg)
    cam.location = (CAM_TARGET.x + CAM_DIST * math.cos(el) * math.sin(az),
                    CAM_TARGET.y + CAM_DIST * math.cos(el) * math.cos(az),
                    CAM_TARGET.z + CAM_DIST * math.sin(el))
    direction = (CAM_TARGET - cam.location).to_track_quat("-Z", "Y")
    cam.rotation_euler = direction.to_euler()


# ── 导出 glb（中性灰，Y 轴向上由导出器换算） ─────────────────────
pivot.rotation_euler = (0.0, 0.0, 0.0)
place_camera(35.0)
bpy.context.view_layer.update()
bpy.ops.export_scene.gltf(
    filepath=os.path.join(OUT_DIR, "fwav_15dof_turntable.glb"),
    export_format="GLB", use_selection=False, export_yup=True)

# ── 二十四帧转台序列 ─────────────────────────────────────────────
raw_dir = os.path.join(OUT_DIR, "_raw")
os.makedirs(raw_dir, exist_ok=True)
for i in range(FRAMES):
    pivot.rotation_euler = (0.0, 0.0, math.radians(360.0 * i / FRAMES))
    place_camera(35.0)
    bpy.context.view_layer.update()
    scene.render.filepath = os.path.join(raw_dir, "f%02d.png" % i)
    bpy.ops.render.render(write_still=True)

# ── 缩放成 WebP（透明底保留） ────────────────────────────────────
from PIL import Image

total = 0
for i in range(FRAMES):
    im = Image.open(os.path.join(raw_dir, "f%02d.png" % i)).convert("RGBA")
    im = im.resize((FINAL_W, FINAL_H), Image.LANCZOS)
    p = os.path.join(OUT_DIR, "frame_%02d.webp" % i)
    im.save(p, "WEBP", quality=80, method=6)
    total += os.path.getsize(p)
os.rmtree(raw_dir)

md5, git = source_version()
manifest = {
    "schema_version": 1,
    "model_name": "fwav_15dof 十五自由度扑翼样机（整机骨架）",
    "config": "215g 现行构型（飞行仿真现行值视角）",
    "source_file": "assets/flapper/fwav_15dof.usd",
    "source_md5": md5,
    "source_git": git,
    "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "renderer": "Blender Workbench（中性灰单色、工作室光、透明底）",
    "frames": FRAMES,
    "fps": round(FRAMES / DURATION_S, 3),
    "duration_s": DURATION_S,
    "view": "四分之三前视，方位角三十五度，仰角十八度",
    "frame_files": ["frame_%02d.webp" % i for i in range(FRAMES)],
    "glb": "fwav_15dof_turntable.glb",
    "frames_bytes": total,
}
with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=1)
print("EXPORT OK frames_bytes=%d" % total)
