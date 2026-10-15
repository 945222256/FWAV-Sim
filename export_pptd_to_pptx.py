# -*- coding: utf-8 -*-
"""pptd 工程 → PPTX 导出脚本（不依赖 Kimi app）。

原理：调用本机已安装的 kimi-slides.exe 把 pptd 每页渲染成 PNG，
再用 python-pptx 按 16:9 页面逐页铺满，拼出标准 PPTX。

用法：
    python export_pptd_to_pptx.py <pptd工程目录> <输出.pptx>

示例：
    python export_pptd_to_pptx.py deck 输出.pptx

依赖：
    1. kimi-slides 命令在 PATH 中（随 Kimi Work 安装，位于
       C:\\Users\\<用户名>\\.kimi-work\\bin\\）；脚本会先查 PATH，找不到
       时自动回退到上述默认安装位置；
    2. Python 包 python-pptx（pip install python-pptx）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

from pptx import Presentation
from pptx.util import Inches


def _find_kimi_slides():
    """先在 PATH 中找 kimi-slides，找不到就回退到默认安装位置。"""
    exe = shutil.which("kimi-slides")
    if exe:
        return exe
    candidate = os.path.join(os.path.expanduser("~"), ".kimi-work", "bin",
                             "kimi-tools", "kimi-slides.exe")
    if os.path.isfile(candidate):
        return candidate
    raise FileNotFoundError(
        "找不到 kimi-slides。请安装 Kimi Work，或把 KIMI_SLIDES 改为 exe 完整路径。")


KIMI_SLIDES = _find_kimi_slides()


def main():
    if len(sys.argv) != 3:
        print("用法: python export_pptd_to_pptx.py <pptd工程目录> <输出.pptx>")
        sys.exit(1)

    deck_dir, out_path = sys.argv[1], sys.argv[2]
    if not os.path.isdir(deck_dir):
        print(f"错误：找不到工程目录 {deck_dir}")
        sys.exit(1)

    shots = tempfile.mkdtemp(prefix="pptd_shots_")
    try:
        r = subprocess.run([KIMI_SLIDES, "screenshot", deck_dir, "-o", shots],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("渲染失败：\n", r.stdout, r.stderr)
            sys.exit(1)

        files = sorted(os.listdir(shots),
                       key=lambda f: int(f.split("-")[1].split(".")[0]))
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        blank = prs.slide_layouts[6]
        for f in files:
            slide = prs.slides.add_slide(blank)
            slide.shapes.add_picture(os.path.join(shots, f), 0, 0,
                                     width=prs.slide_width,
                                     height=prs.slide_height)
        prs.save(out_path)
        print(f"完成：{out_path}（共 {len(files)} 页）")
    finally:
        shutil.rmtree(shots, ignore_errors=True)


if __name__ == "__main__":
    main()
