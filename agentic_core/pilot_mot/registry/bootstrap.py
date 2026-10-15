# -*- coding: utf-8 -*-
"""bootstrap.py — 新项目一键初始化。

用法：python bootstrap.py <目标项目根目录>
  目标目录不存在则创建；registry 机制代码与数据模板拷入
  <目标>/registry/；lanes.template.json 复制为 lanes.json；
  生成空总账、空待审队列、空事件流；制度文本模板拷入
  <目标>/governance/；看板模板拷入 <目标>/board/。

初始化后的固定动作（按适配指南）：
  1. 填 registry/lanes.json（路线划分）；
  2. 填 registry/data/config.json（项目代号、缩写白名单）；
  3. 填 registry/banned_words.json 与 glossary.json（词表）；
  4. 按 governance/ 六份制度模板填出项目制度文本；
  5. 登记首批事项，开工。
仅使用 Python 标准库。
"""
import os
import shutil
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_ROOT = os.path.dirname(_DIR)

_MECHANISM_FILES = [
    "dispatch.py", "ledger.py", "events.py", "schedule.py",
    "intake.py", "langcheck.py", "check_commit.py", "bootstrap.py",
]
_DATA_FILES = [
    "banned_words.json", "glossary.json", "standards_words.json",
    "lanes.template.json",
]


def _atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        import json
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def bootstrap(target_root):
    """初始化目标项目：机制、数据骨架、制度模板、看板模板。"""
    target_root = os.path.abspath(target_root)
    reg = os.path.join(target_root, "registry")
    os.makedirs(os.path.join(reg, "data"), exist_ok=True)
    for f in _MECHANISM_FILES:
        shutil.copy2(os.path.join(_DIR, f), os.path.join(reg, f))
    for f in _DATA_FILES:
        shutil.copy2(os.path.join(_DIR, f), os.path.join(reg, f))
    shutil.copy2(os.path.join(_DIR, "data", "config.json"),
                 os.path.join(reg, "data", "config.json"))
    # 路线表：模板复制为待填的 lanes.json
    shutil.copy2(os.path.join(reg, "lanes.template.json"),
                 os.path.join(reg, "lanes.json"))
    # 空总账：顶层 rules 字段是总账纪律正文（ledger 只做强制不做解释）
    _atomic_json(os.path.join(reg, "items.json"), {
        "schema_version": 1,
        "rules": [
            "关闭事项必须附证据与关闭时间，缺一拒绝。",
            "汇报与提交只允许引用事项编号。",
            "旧记录永不删除，关闭只打勾。",
        ],
        "items": [],
    })
    _atomic_json(os.path.join(reg, "review_queue.json"),
                 {"schema_version": 1, "queue": []})
    _atomic_json(os.path.join(reg, "events.json"),
                 {"schema_version": 1, "events": []})
    # 制度文本层与触达层
    for sub in ("governance", "board"):
        src = os.path.join(TEMPLATE_ROOT, sub)
        if os.path.isdir(src):
            dst = os.path.join(target_root, sub)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns("__pycache__"))
    print("初始化完成：%s" % target_root)
    print("下一步：填 registry/lanes.json 与 registry/data/config.json，")
    print("再按 governance/ 模板填项目制度文本，登记首批事项后开工。")
    return target_root


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：python bootstrap.py <目标项目根目录>")
        sys.exit(2)
    bootstrap(sys.argv[1])
