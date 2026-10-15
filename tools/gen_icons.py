# -*- coding: utf-8 -*-
"""gen_icons.py — I-075 判据二：自适应图标与多密度位图生成。

产物：
  res/mipmap-anydpi-v26/ic_launcher.xml 与 ic_launcher_round.xml
  res/drawable/ic_launcher_foreground.xml（三泳道矢量前景）
  res/values/ic_launcher_background.xml（底色）
  res/mipmap-{mdpi,hdpi,xhdpi,xxhdpi,xxxhdpi}/ic_launcher.png（48..192px）
运行一次即可；重复运行覆盖同名产物。
仅使用 Python 标准库 + PIL。
"""
import os
from PIL import Image, ImageDraw

RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "android_shell", "app", "src", "main", "res")

ADAPTIVE = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
"""

FOREGROUND = """<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="108"
    android:viewportHeight="108">
    <!-- 三泳道意象：L1 蓝 / L2 紫 / L3 青，与作战地图路线色组同源 -->
    <path
        android:fillColor="#58A6FF"
        android:pathData="M30,36 h10 v36 h-10 z" />
    <path
        android:fillColor="#BC8CFF"
        android:pathData="M49,36 h10 v36 h-10 z" />
    <path
        android:fillColor="#39C5CF"
        android:pathData="M68,36 h10 v36 h-10 z" />
</vector>
"""

BACKGROUND = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">#0D1117</color>
</resources>
"""


def png(size, path):
    scale = size / 108.0
    img = Image.new("RGBA", (size, size), (13, 17, 23, 255))
    d = ImageDraw.Draw(img)
    # 安全区：自适应图标可视区为中央 66dp，按比例绘制三泳道。
    lanes = [(30, 36, 40, 72, (88, 166, 255)),
             (49, 36, 59, 72, (188, 140, 255)),
             (68, 36, 78, 72, (57, 197, 207))]
    for x0, y0, x1, y1, color in lanes:
        d.rounded_rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                            radius=max(1, int(2 * scale)), fill=color)
    img.save(path, "PNG")


def main():
    anydpi = os.path.join(RES, "mipmap-anydpi-v26")
    os.makedirs(anydpi, exist_ok=True)
    with open(os.path.join(anydpi, "ic_launcher.xml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(ADAPTIVE)
    with open(os.path.join(anydpi, "ic_launcher_round.xml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(ADAPTIVE)
    with open(os.path.join(RES, "drawable", "ic_launcher_foreground.xml"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(FOREGROUND)
    with open(os.path.join(RES, "values", "ic_launcher_background.xml"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(BACKGROUND)
    for density, size in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96),
                          ("xxhdpi", 144), ("xxxhdpi", 192)):
        d = os.path.join(RES, "mipmap-%s" % density)
        os.makedirs(d, exist_ok=True)
        png(size, os.path.join(d, "ic_launcher.png"))
    print("图标产物已生成：%s" % RES)


if __name__ == "__main__":
    main()
