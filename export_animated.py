# -*- coding: utf-8 -*-
"""31 页导出：第 29 页原生组装四张 GIF（保动画），其余页用渲染截图。"""
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

KIMI = shutil.which("kimi-slides") or os.path.join(
    os.path.expanduser("~"), ".kimi-work", "bin", "kimi-tools", "kimi-slides.exe")

DARK = RGBColor(0x2E, 0x40, 0x45)
ACC = RGBColor(0xB6, 0x6A, 0x3C)
GRAY = RGBColor(0x7E, 0x83, 0x8A)
BODY = RGBColor(0x3E, 0x40, 0x46)
TITLE = RGBColor(0x32, 0x35, 0x3B)
BG = RGBColor(0xF7, 0xF5, 0xEF)
CARD = RGBColor(0xFB, 0xFA, 0xF6)
SAND = RGBColor(0xF0, 0xED, 0xE4)
FONT = "微软雅黑"


def IN(px):
    # deck 坐标系 960px = 13.333in = 72px/in → 12700 EMU/px
    return Emu(int(px * 12700))


def add_text(slide, x, y, w, h, text, size, color, bold=False, align=PP_ALIGN.LEFT,
             line_spacing=1.3):
    tb = slide.shapes.add_textbox(IN(x), IN(y), IN(w), IN(h))
    tf = tb.text_frame
    tf.word_wrap = True
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        r = p.add_run()
        r.text = ln
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
        r.font.name = FONT
    return tb


def add_card(slide, x, y, w, h, fill, accent=False):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, IN(x), IN(y), IN(w), IN(h))
    shp.adjustments[0] = 0.06
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.color.rgb = RGBColor(0xCF, 0xC9, 0xBC)
    shp.line.width = Pt(0.75)
    shp.shadow.inherit = False
    if accent:
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, IN(x), IN(y), IN(4), IN(h))
        bar.fill.solid()
        bar.fill.fore_color.rgb = ACC
        bar.line.fill.background()
        bar.shadow.inherit = False
    return shp


def build_page29(slide):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = BG

    add_text(slide, 48, 26, 400, 20, "02 · 控制律设计", 12, GRAY)
    add_text(slide, 700, 26, 212, 20, "32 / 39", 12, GRAY, align=PP_ALIGN.RIGHT)
    add_text(slide, 48, 50, 864, 46, "在环验收：定高定速巡航四阶段实录", 30, TITLE, bold=True)
    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, IN(48), IN(104), IN(864), IN(1))
    rule.fill.solid(); rule.fill.fore_color.rgb = RGBColor(0xCF, 0xC9, 0xBC)
    rule.line.fill.background(); rule.shadow.inherit = False

    add_text(slide, 48, 112, 864, 34,
             "收敛的最后一环是增益求解方法的统一：诊断用全状态数值闭环线性化（方法甲），求解用稳定导数"
             "矩阵的代数极点配置（方法乙）。下图为四阶段在环实录动图（Isaac 内录，含系留柱与系留绳）。",
             12, BODY, line_spacing=1.3)

    gifs = [("deck/media/1.gif", 48, 156, "阶段一　发射段实录（camera_122054，前 10 秒）"),
            ("deck/media/2.gif", 306, 156, "阶段二　在环定高记录（camera_123504，前 47 秒）"),
            ("deck/media/3.gif", 48, 322, "阶段三　在环巡航记录（camera_134341，前 17 秒）"),
            ("deck/media/4.gif", 306, 322, "阶段四　验收巡航（camera_055130，全 65 秒）")]
    for path, x, y, cap in gifs:
        bw, bh = 250, 141
        with Image.open(path) as im:
            ga = im.width / im.height
        if ga > bw / bh:
            w, h = bw, bw / ga
        else:
            h, w = bh, bh * ga
        slide.shapes.add_picture(path, IN(x + (bw - w) / 2), IN(y + (bh - h) / 2), IN(w), IN(h))
        add_text(slide, x, y + bh + 2, bw, 14, cap, 9, GRAY)

    add_card(slide, 584, 164, 328, 156, SAND)
    add_text(slide, 598, 174, 300, 18, "验收成绩（已合并主分支）", 12.5, DARK, bold=True)
    add_text(slide, 598, 196, 300, 118,
             "完整仿真六十五秒连续飞行两轮；高度、速度、俯仰三项在带率均为 100.0%；"
             "滚转极值 0.000；全程未触地。\n离线闭环试验架八项全部通过；验收证据为数据文件"
             " 0921_051735 与 0921_052508。",
             10.5, BODY, line_spacing=1.4)

    add_card(slide, 584, 332, 328, 150, CARD, accent=True)
    add_text(slide, 600, 342, 300, 18, "统一口径：甲诊断，乙求解", 12.5, ACC, bold=True)
    add_text(slide, 600, 364, 300, 112,
             "方法甲：真实控制器与真实力模型组成离散闭环映射（十一维增广状态），有限差分求雅可比，"
             "特征值给出极点位置、振荡频率、阻尼比与状态参与度。\n"
             "方法乙：实测稳定导数组装状态矩阵，极点配置一次性代数解出增益。\n"
             "生效增益（内环 0.40、垂直阻尼 0.02、速率阻尼 2.0、油门阻尼 1.0）均有模型与在环双证据。",
             9.5, BODY, line_spacing=1.35)

    add_text(slide, 48, 486, 864, 14,
             "注：本页为验收口径；第 19 至 21 页结构框图中的增益为施工期整定值，最终以本页统一口径为准。",
             9, GRAY)
    fr = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, IN(48), IN(502), IN(864), IN(1))
    fr.fill.solid(); fr.fill.fore_color.rgb = RGBColor(0xCF, 0xC9, 0xBC)
    fr.line.fill.background(); fr.shadow.inherit = False
    add_text(slide, 48, 510, 600, 16, "扑翼飞行器动力学建模与飞行控制技术调研 · 第二轮技术面试", 10, GRAY)
    add_text(slide, 760, 510, 152, 16, "2026-09", 10, GRAY, align=PP_ALIGN.RIGHT)


def main():
    shots = tempfile.mkdtemp(prefix="deck_shots_")
    try:
        r = subprocess.run([KIMI, "screenshot", "deck", "-o", shots],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout, r.stderr)
            sys.exit(1)
        files = sorted(os.listdir(shots), key=lambda f: int(f.split("-")[1].split(".")[0]))

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        blank = prs.slide_layouts[6]
        for idx, f in enumerate(files, start=1):
            slide = prs.slides.add_slide(blank)
            if idx == 32:
                build_page29(slide)
            else:
                slide.shapes.add_picture(os.path.join(shots, f), 0, 0,
                                         width=prs.slide_width, height=prs.slide_height)
        out = "扑翼飞行器动力学建模与飞行控制技术调研（二面）_Kimi修订预览版_39页.pptx"
        try:
            prs.save(out)
            print("saved", out, len(files), "slides（第 32 页为动图）")
        except PermissionError:
            out = "扑翼飞行器动力学建模与飞行控制技术调研（二面）_Kimi修订预览版_33页_动图版.pptx"
            prs.save(out)
            print("saved", out, len(files), "slides（原文件被占用，改用新名）")
    finally:
        shutil.rmtree(shots, ignore_errors=True)


if __name__ == "__main__":
    main()
