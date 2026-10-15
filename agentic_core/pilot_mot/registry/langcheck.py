# -*- coding: utf-8 -*-
"""langcheck.py — 语言规范检查：禁用词、废止编号、未登记术语。

三类违规：
  1. 比喻词（命中即拒），词表 banned_words.json，项目自填；
  2. 内部压缩词（命中即拒并给出规范替换词），词表同上；
  3. 废止编号（项目历史编号习惯），模式同上；
  4. 英文大写缩写不在白名单（事项编号、目标编号、环境变量名除外）。
项目专属缩写白名单与环境变量前缀在 data/config.json 登记，
本模块不内置任何项目词表。
用法：python registry/langcheck.py <文件路径>
     python registry/langcheck.py --msg "待检查文本"
仅使用 Python 标准库。
"""
import json
import os
import re
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
BANNED_PATH = os.path.join(_DIR, "banned_words.json")
GLOSSARY_PATH = os.path.join(_DIR, "glossary.json")
CONFIG_PATH = os.path.join(_DIR, "data", "config.json")

# 跨领域通用技术缩写：任何项目都可能用到的软件与数据格式词汇。
_COMMON_ACRONYMS = (
    "ASCII|CSV|CI|GUI|HTML|HTTP|HTTPS|JSON|NaN|RMS|Hz|TCP|UDP|UTF|"
    "DAG|API|CLI|URL")


def _load_config():
    """读项目配置：专属缩写白名单与环境变量前缀。缺文件时按空配置。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _token_whitelist():
    """拼装缩写白名单正则：治理编号 + 通用技术缩写 + 项目登记缩写。"""
    cfg = _load_config()
    extra = "|".join(re.escape(a) for a in cfg.get("acronym_whitelist", []))
    prefix = re.escape(cfg.get("env_prefix", "")).replace(r"\_", "_")
    parts = [r"I-\d{3}", r"G-\d{3}", r"Q-\d{3}", r"[A-Z]{1,2}\d*",
             _COMMON_ACRONYMS]
    if prefix:
        parts.append(prefix + r"[A-Z_0-9]*")
    if extra:
        parts.append(extra)
    return re.compile(r"^(%s)$" % "|".join(parts))


def scan_standards(text):
    """降标准语义拦截：含降低判据、降档等语义的申报与决定材料退回。
    词表为数据文件 standards_words.json，指挥部可增补；误拦申诉走
    指挥部人工复核。只此一类，不影响 scan_text 的既有三类判定
    （提交信息与文档检查不触发本类）。"""
    words_path = os.path.join(_DIR, "standards_words.json")
    try:
        table = json.load(open(words_path, encoding="utf-8"))
    except Exception:
        return []
    violations = []
    for w in table.get("standard_lowering", []):
        if w in text:
            violations.append(("降标准语义", w,
                               "拦截退回：降标准类申报不到机主眼前；"
                               "确属误拦走指挥部人工复核申诉"))
    return violations


def scan_text(text):
    """返回违规列表，每项为（类别，命中词，规范建议）。空列表即通过。"""
    banned = json.load(open(BANNED_PATH, encoding="utf-8"))
    violations = []
    for w in banned["metaphor_banned"]:
        if w in text:
            violations.append(("比喻词", w, "删除，改用直接陈述"))
    for w, sug in banned["internal_banned"].items():
        if w in text:
            violations.append(("内部压缩词", w, "改用：%s" % sug))
    for pat in banned["deprecated_numbering"]:
        for m in re.finditer(pat, text):
            violations.append(("废止编号", m.group(0), "改用 I-xxx 事项编号"))
    glossary = set(json.load(open(GLOSSARY_PATH, encoding="utf-8"))["terms"])
    whitelist = _token_whitelist()
    for m in re.finditer(r"[A-Z][A-Z0-9_]{1,}", text):
        tok = m.group(0)
        if not whitelist.match(tok) and tok not in glossary:
            violations.append(("未登记缩写", tok, "入术语登记册或改写为中文全称"))
    return violations


def scan_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return scan_text(f.read())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：langcheck.py <文件路径> 或 langcheck.py --msg 文本")
        sys.exit(2)
    if sys.argv[1] == "--msg":
        v = scan_text(" ".join(sys.argv[2:]))
    else:
        v = scan_file(sys.argv[1])
    if not v:
        print("[过] 语言规范检查通过")
        sys.exit(0)
    for cat, word, sug in v:
        print("[拒] %s「%s」：%s" % (cat, word, sug))
    sys.exit(1)
