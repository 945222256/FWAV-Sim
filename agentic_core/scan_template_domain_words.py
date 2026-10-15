# -*- coding: utf-8 -*-
"""scan_template_domain_words.py — 模板零领域词验证。

扫描 template 目录全部文本文件，命中领域词即报。词表分四组：
源项目领域词、试点项目领域词、源项目目录名、源项目禁词病史。
治理机制自身词汇（机主、事项、认领、租约、判据等）不在禁列。
退出码：零为全部通过，非零为有命中。
仅使用 Python 标准库。
"""
import os
import sys

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template")

# 源项目（飞行器仿真）领域词：模板不得携带
SOURCE_DOMAIN = [
    "扑翼", "扑动", "拍动", "挥拍", "翼面", "翼尖", "尾翼", "舵面", "差动",
    "俯仰", "偏航", "滚转", "悬停", "巡航", "盘旋", "爬升", "攻角", "气动",
    "升力", "叶素", "力矩", "滑流", "配平", "URDF", "USD", "PhysX", "Isaac",
    "TECS", "RSI", "SOTA", "FLAPPER", "flapper", "仿真器", "数字孪生",
    "科目",
]
# 试点项目（多目标跟踪）领域词：模板同样不得携带，保持对任何领域中立
PILOT_DOMAIN = [
    "多目标跟踪", "激光雷达", "雷达", "点云", "目标检测", "跟踪器", "检测器",
    "航迹", "MOT", "LiDAR", "lidar", "传感器画面",
]
# 源项目目录与文件名：结构性专属
SOURCE_PATHS = [
    "items_registry", "params_registry", "lp_board", "cloud_board",
    "main_sim", "lp_sync", "eagle_shell", "android_shell",
]
# 源项目禁词病史：模板不得携带源项目的词汇病
SOURCE_BANNED_HISTORY = [
    "锚点", "贴限", "打地鼠", "偷走", "簿记", "钉固", "挂账", "销账",
    "泳道", "口径", "钩子",
]

ALLOWED = {
    "scan_template_domain_words.py",
}


def scan():
    hits = []
    for dirpath, dirnames, filenames in os.walk(_TEMPLATE_DIR):
        for name in filenames:
            if name in ALLOWED or not name.endswith((".py", ".md", ".json", ".html", ".js", ".bat")):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, _TEMPLATE_DIR)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            for group, words in (("源项目领域词", SOURCE_DOMAIN),
                                 ("试点领域词", PILOT_DOMAIN),
                                 ("源项目路径", SOURCE_PATHS),
                                 ("源项目禁词病史", SOURCE_BANNED_HISTORY)):
                for w in words:
                    if w in text:
                        hits.append((rel, group, w))
    return hits


if __name__ == "__main__":
    found = scan()
    total = sum(len(files) for _, _, files in os.walk(_TEMPLATE_DIR))
    print("扫描 template 目录：文本文件 %d 个维度四组词表" % total)
    if not found:
        print("[过] 模板零领域词验证通过：无任何领域内容残留")
        sys.exit(0)
    for rel, group, w in found:
        print("[拒] %s 命中%s：%s" % (rel, group, w))
    sys.exit(1)
