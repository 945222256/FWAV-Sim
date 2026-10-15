# -*- coding: utf-8 -*-
"""pptd 工程 → PPTX 导出脚本（GIF 动画保留版，2026-09-21 留档）。

在 G:\\playground\\isaac_flapper_sim\\export_pptd_to_pptx.py 基础上的修订：
原脚本把每页渲染成 PNG 后铺满幻灯片，凡是 .page 里引用 .gif 的元素
（如第 31 页在环验收的四段相机实录动图）会被压成静止海报帧。
本脚本在铺满 PNG 之后，按 .page 源文件中的 bounds（960×540 设计坐标，
1 点 = 12700 EMU）把每个 .gif 以原生动画图片叠加回原位，
放映时 GIF 正常播放，其余页面与原脚本行为完全一致。

原理：
    1. 调用本机 kimi-slides.exe 把 pptd 每页渲染成 PNG；
    2. 用 python-pptx 按页码顺序逐页铺满 16:9 幻灯片（与原脚本相同）；
    3. 重新扫描每页 .page 源文件，凡 elementType: image 且 src 以
       .gif 结尾的元素，将其 bounds 换算为 EMU 后叠加 animated GIF。

用法：
    python export_pptx_keep_gif.py <pptd工程目录> <输出.pptx>

示例：
    python export_pptx_keep_gif.py deck 输出.pptx

依赖：
    1. kimi-slides 命令（随 Kimi Work 安装）；
    2. python-pptx、Pillow。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

from pptx import Presentation
from pptx.util import Emu, Inches

PT2EMU = 12700  # 1 pt = 12700 EMU


def _find_kimi_slides():
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

# .page 源文件中的 GIF 元素：
#   - elementId: f1
#     elementType: image
#     bounds: [48, 164, 250, 141]
#     src: media/1.gif
_GIF_BLOCK = re.compile(
    r"elementType:\s*image.*?bounds:\s*\[([^\]]+)\].*?src:\s*(\S+?\.gif)",
    re.S)


def find_gifs(page_path, deck_dir):
    """返回 [(gif绝对路径, x_pt, y_pt, w_pt, h_pt), ...]，按源文件顺序。"""
    with open(page_path, encoding="utf-8") as fh:
        text = fh.read()
    out = []
    for bounds, src in _GIF_BLOCK.findall(text):
        try:
            x, y, w, h = [float(v) for v in bounds.split(",")]
        except ValueError:
            continue
        # src 形如 media/1.gif，相对工程根目录（deck_dir），不是相对 pages/
        gif_abs = os.path.normpath(os.path.join(deck_dir, src.replace("/", os.sep)))
        if os.path.isfile(gif_abs):
            out.append((gif_abs, x, y, w, h))
    return out


def main():
    if len(sys.argv) != 3:
        print("用法: python export_pptx_keep_gif.py <pptd工程目录> <输出.pptx>")
        sys.exit(1)

    deck_dir, out_path = sys.argv[1], sys.argv[2]
    if not os.path.isdir(deck_dir):
        print(f"错误：找不到工程目录 {deck_dir}")
        sys.exit(1)
    pages_dir = os.path.join(deck_dir, "pages")
    media_dir = os.path.join(deck_dir, "media")
    if not os.path.isdir(pages_dir):
        print(f"错误：{deck_dir} 下没有 pages/ 目录")
        sys.exit(1)

    shots = tempfile.mkdtemp(prefix="pptd_shots_")
    try:
        r = subprocess.run([KIMI_SLIDES, "screenshot", deck_dir, "-o", shots],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("渲染失败：\n", r.stdout, r.stderr)
            sys.exit(1)

        # 2026-09-21 修订：按 pptd 列表顺序拼图（文件名序与叙述序不一致，
        # 旧版按文件名排序会把页序打乱）
        pptd_lines = open(os.path.join(deck_dir, "presentation.pptd"),
                          encoding="utf8").read().split("\n")
        _i = next(i for i, l in enumerate(pptd_lines) if l.strip() == "pages:")
        order = []
        for l in pptd_lines[_i + 1:]:
            l = l.strip()
            if l.startswith("- "):
                order.append(int(os.path.basename(l[2:])
                                 .replace(".page", "").split("-")[1]))
            elif order and not l.startswith("-"):
                break
        shot_map = {}
        for f in os.listdir(shots):
            try:
                shot_map[int(f.split("-")[1].split(".")[0])] = f
            except (IndexError, ValueError):
                continue
        files = [shot_map[no] for no in order if no in shot_map]
        if len(files) != len(order):
            print(f"警告：{len(order) - len(files)} 个页面没有对应渲染图")
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        blank = prs.slide_layouts[6]
        for f in files:
            slide = prs.slides.add_slide(blank)
            slide.shapes.add_picture(os.path.join(shots, f), 0, 0,
                                     width=prs.slide_width,
                                     height=prs.slide_height)
            # ── GIF 保留分支：叠加原生动画图 ──
            page_no = int(f.split("-")[1].split(".")[0])
            page_path = os.path.join(pages_dir, f"page-{page_no}.page")
            if os.path.isfile(page_path):
                for gif_abs, x, y, w, h in find_gifs(page_path, deck_dir):
                    slide.shapes.add_picture(
                        gif_abs,
                        Emu(int(round(x * PT2EMU))),
                        Emu(int(round(y * PT2EMU))),
                        Emu(int(round(w * PT2EMU))),
                        Emu(int(round(h * PT2EMU))))
        prs.save(out_path)
        print(f"完成：{out_path}（共 {len(files)} 页）")
    finally:
        shutil.rmtree(shots, ignore_errors=True)


if __name__ == "__main__":
    main()
