# -*- coding: utf-8 -*-
"""bootstrap.py — 派生项目一键初始化（I-133 对齐模板侧修法落地）。

源项目侧与模板侧（agentic_core/template/registry/bootstrap.py）同型，
差异仅在布局映射：本仓库的制度文本在仓库根（协作架构、汇报标准、
语言规范等治理文件），触达层看板在 lp_board/。模板侧已修的漏拷看板
目录缺陷（拷贝循环只含 governance 一项）在本版本为两项齐全。

用法：python items_registry/bootstrap.py <目标项目根目录>
  目标目录不存在则创建；items_registry 机制代码与数据模板拷入
  <目标>/registry/；生成空总账、空待审队列、空事件流；治理文本
  拷入 <目标>/governance/；看板目录拷入 <目标>/board/。

初始化后的固定动作（按 agentic_core/template/ADAPTATION_GUIDE.md）：
  1. 填 registry/lanes.json（路线划分）；
  2. 填 registry/data/config.json（项目代号、缩写白名单）；
  3. 填 registry/banned_words.json 与 glossary.json（词表）；
  4. 按 governance/ 模板填出项目制度文本；
  5. 登记首批事项，开工。
仅使用 Python 标准库。
"""
import os
import shutil
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_DIR)

_MECHANISM_FILES = [
    "dispatch.py", "ledger.py", "events.py", "schedule.py",
    "intake.py", "langcheck.py", "check_commit.py", "bootstrap.py",
]
_DATA_FILES = [
    "banned_words.json", "glossary.json", "standards_words.json",
    "audit_policy.json",
]
# 治理文本：仓库根的治理族文件前缀（HQ 领地清单同源）
_GOVERNANCE_PREFIXES = (
    "协作架构", "汇报标准", "语言规范", "整改总计划", "施工清单",
    "围绕题目",
)


def _atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        import json
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def bootstrap(target_root):
    """初始化目标项目：机制、数据骨架、治理文本、看板目录。"""
    target_root = os.path.abspath(target_root)
    reg = os.path.join(target_root, "registry")
    gov = os.path.join(target_root, "governance")
    os.makedirs(os.path.join(reg, "data"), exist_ok=True)
    os.makedirs(gov, exist_ok=True)
    for f in _MECHANISM_FILES:
        shutil.copy2(os.path.join(_DIR, f), os.path.join(reg, f))
    for f in _DATA_FILES:
        src = os.path.join(_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(reg, f))
    # 路线表：源项目现行表作为待调整起点（比空白模板更接近实态）
    shutil.copy2(os.path.join(_DIR, "lanes.json"),
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
    # 治理文本层（I-133：模板侧同型缺陷为本处漏拷，两项齐全）
    for name in os.listdir(ROOT):
        if name.startswith(_GOVERNANCE_PREFIXES) and name.endswith(".md"):
            shutil.copy2(os.path.join(ROOT, name), os.path.join(gov, name))
    # 触达层看板目录（I-133 缺陷三的修复本体：board 必拷）
    board_src = os.path.join(ROOT, "lp_board")
    if os.path.isdir(board_src):
        board_dst = os.path.join(target_root, "board")
        if os.path.isdir(board_dst):
            shutil.rmtree(board_dst)
        shutil.copytree(board_src, board_dst,
                        ignore=shutil.ignore_patterns("__pycache__"))
    print("初始化完成：%s" % target_root)
    print("下一步：调整 registry/lanes.json 与治理文本，登记首批事项后开工。")
    return target_root


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：python bootstrap.py <目标项目根目录>")
        sys.exit(2)
    bootstrap(sys.argv[1])
