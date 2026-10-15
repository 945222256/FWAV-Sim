# -*- coding: utf-8 -*-
"""render_eaglex_frames.py — I-202 七百克候选动作预览帧序列离线渲染。

由 Blender 无界面模式调用（I-130 三维资产同族工序，本机离线渲染，
不启动仿真器，符合强制指令第三条离线分析类）：
  "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe" -b -P \
      board_ui/scripts/render_eaglex_frames.py -- <glb路径> <输出目录> [段起,段止 ...]

动作分段依据 jane 交付 preview.segments（每段两秒：拍动/扭转/折叠/
尾部/组合）。相机按全动画包围盒并集取景，保证模型全程在视野内
（判据二）。输出每段二十四帧 WEBP，供参数实验室帧序列播放器使用。
"""
import sys
import bpy
from mathutils import Vector

FRAMES_PER_SEG = 24
RESOLUTION = 560
BG_COLOR = (0.941, 0.933, 0.902, 1.0)     # 暖纸画布色（tokens --tk-canvas #f0eee6）


def _argv_after_dashdash():
    argv = sys.argv
    if "--" in argv:
        return argv[argv.index("--") + 1:]
    return []


def _global_bbox(scene, frames):
    """逐帧评估动画取所有网格世界包围盒并集（模型全程视野依据）。"""
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for f in frames:
        scene.frame_set(f)
        for ob in scene.objects:
            if ob.type != "MESH":
                continue
            for corner in ob.bound_box:
                wc = ob.matrix_world @ Vector(corner)
                lo = Vector(map(min, lo, wc))
                hi = Vector(map(max, hi, wc))
    return lo, hi


def main():
    args = _argv_after_dashdash()
    if len(args) < 2:
        raise SystemExit("用法：blender -b -P 本脚本 -- <glb路径> <输出目录> [段起,段止 ...]")
    glb_path, out_dir = args[0], args[1]
    segs = []
    for a in args[2:]:
        a0, a1 = a.split(",")
        segs.append((float(a0), float(a1)))
    if not segs:
        segs = [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10)]
    seg_names = ["flap", "twist", "fold", "tail", "combo"]

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=glb_path)
    scene = bpy.context.scene

    actions = [a for a in bpy.data.actions]
    if not actions:
        raise SystemExit("GLB 无动画数据")
    act = actions[0]
    fps = scene.render.fps
    f_start, f_end = int(act.frame_range[0]), int(act.frame_range[1])
    # 把导入动画压到场景轨道并启用（gltf 导入通常已挂 NLA，兜底手工推轨）
    armatures = [o for o in scene.objects if o.type == "ARMATURE"]
    if armatures:
        ob = armatures[0]
        if not ob.animation_data or not ob.animation_data.action:
            ob.animation_data_create()
            ob.animation_data.action = act
    all_frames = list(range(f_start, f_end + 1, max(1, (f_end - f_start) // 60)))
    lo, hi = _global_bbox(scene, all_frames)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-4)

    cam_data = bpy.data.cameras.new("EagleCam")
    cam_data.type = "PERSP"
    cam_data.lens = 50
    cam = bpy.data.objects.new("EagleCam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    # 取景距离：半径装进视场（含一成余量），机位从斜前上方看向中心
    import math
    dist = radius * 1.35 / math.tan(math.radians(cam_data.angle / 2))
    cam.location = center + Vector((dist * 0.5, -dist * 0.75, dist * 0.42))
    direction = center - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    scene.render.resolution_x = RESOLUTION
    scene.render.resolution_y = RESOLUTION
    scene.render.image_settings.file_format = "WEBP"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("Paper")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = BG_COLOR

    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"

    report = []
    for si, (t0, t1) in enumerate(segs):
        name = seg_names[si] if si < len(seg_names) else ("seg%d" % si)
        f0 = f_start + int(t0 * fps)
        f1 = f_start + int(t1 * fps)
        for k in range(FRAMES_PER_SEG):
            f = f0 + round((f1 - f0) * k / (FRAMES_PER_SEG - 1))
            scene.frame_set(f)
            scene.render.filepath = "%s/%s/frame_%02d" % (out_dir, name, k)
            bpy.ops.render.render(write_still=True)
        report.append({"segment": name, "time_s": [t0, t1], "frames": FRAMES_PER_SEG})

    import json
    with open(out_dir + "/render_report.json", "w", encoding="utf-8") as fh:
        json.dump({"glb": glb_path, "camera_rule": "全动画包围盒并集取景（含一成余量）",
                   "bbox_min": list(lo), "bbox_max": list(hi),
                   "engine": scene.render.engine, "resolution": RESOLUTION,
                   "segments": report}, fh, ensure_ascii=False, indent=1)
    print("RENDER_DONE", len(segs) * FRAMES_PER_SEG)


main()
