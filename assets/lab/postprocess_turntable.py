# -*- coding: utf-8 -*-
"""postprocess_turntable.py — 转台序列后处理（系统 Python，依赖 Pillow）。

把 Blender 导出的 _raw/*.png 缩放为 WebP 透明底序列并写资产清单。
与 export_turntable.py 的后段同源，拆出来是因为 Blender 自带解释器
没有 Pillow。"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
from PIL import Image

ROOT = r"G:\Playground\isaac_flapper_sim"
OUT_DIR = os.path.join(ROOT, "assets", "lab")
RAW = os.path.join(OUT_DIR, "_raw")
SRC = os.path.join(ROOT, "assets", "flapper", "fwav_15dof.usd")
FRAMES = 24
DURATION_S = 4.0
FINAL_W, FINAL_H = 1024, 768

total = 0
for i in range(FRAMES):
    im = Image.open(os.path.join(RAW, "f%02d.png" % i)).convert("RGBA")
    im = im.resize((FINAL_W, FINAL_H), Image.LANCZOS)
    p = os.path.join(OUT_DIR, "frame_%02d.webp" % i)
    im.save(p, "WEBP", quality=80, method=6)
    total += os.path.getsize(p)

md5 = hashlib.md5(open(SRC, "rb").read()).hexdigest()
try:
    git = subprocess.run(["git", "log", "-1", "--format=%h", "--", "assets/flapper/fwav_15dof.usd"],
                         cwd=ROOT, capture_output=True, text=True, timeout=15).stdout.strip()
except Exception:
    git = ""

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
print("POST OK frames_bytes=%d glb=%d" % (
    total, os.path.getsize(os.path.join(OUT_DIR, "fwav_15dof_turntable.glb"))))
