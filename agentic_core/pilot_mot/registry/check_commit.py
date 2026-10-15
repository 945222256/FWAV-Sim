# -*- coding: utf-8 -*-
"""check_commit.py — 提交检查：领地与编号。

用法：python registry/check_commit.py <路线编号>
      python registry/check_commit.py --selftest   领地判定构造性自检
检查两条：
  1. 最近一次提交信息必须引用至少一个事项编号（I-xxx）。
  2. 该提交触碰的文件必须在本路独占区或共享区内；踏入他路独占区即拒绝。
建议在提交后、推送前运行；可作为持续集成的一个步骤。
仅使用 Python 标准库。

登记库工具写入豁免：登记库的数据文件由工具原子写入（总账读写、
待审队列、事件流），任何一路经工具通道的正常落盘不做领地拦截；
lanes.json 是领地配置非工具数据，不在豁免之列。
文件名解析：git 差异清单用 -z 原始输出（路径不经引号与八进制
转义），并对历史输出形态保留解码兜底，中文文件名的领地判定不误报。
"""
import json
import os
import re
import subprocess
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_DIR)
REGISTRY_DIR_NAME = os.path.basename(_DIR)

# 登记库工具写入豁免清单：只豁免本目录数据文件（路线表除外），
# 不豁免代码与配置；项目如有其他登记库，在 data/config.json 的
# tool_data_globs 登记。
_DEFAULT_TOOL_DATA = r"%s/(?!lanes\.json)[^/]+\.json$" % re.escape(REGISTRY_DIR_NAME)


def _tool_data_re():
    """豁免正则：默认豁免 + 项目在 config 登记的追加豁免。"""
    parts = [_DEFAULT_TOOL_DATA]
    cfg_path = os.path.join(_DIR, "data", "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            for pat in json.load(f).get("tool_data_globs", []):
                parts.append(pat)
    except (OSError, ValueError):
        pass
    return re.compile(r"^(?:%s)" % "|".join(parts))


def _lane_of(path, lanes):
    for lane_id, lane in lanes.items():
        for prefix in lane["exclusive_paths"]:
            if path.startswith(prefix):
                return lane_id
    return None


def _unquote_git_path(tok):
    """历史形态兜底：把带双引号的八进制转义路径解码回原文。"""
    if not (tok.startswith('"') and tok.endswith('"')):
        return tok
    body = tok[1:-1]
    out = []
    i = 0
    while i < len(body):
        if body[i] == "\\" and i + 3 < len(body) + 1 \
                and all(c in "01234567" for c in body[i + 1:i + 4]):
            out.append(chr(int(body[i + 1:i + 4], 8)))
            i += 4
        elif body[i] == "\\":
            i += 1          # \" 与 \\ 转义：取字面字符
        else:
            out.append(body[i])
            i += 1
    raw = "".join(out).encode("latin-1", "replace")
    return raw.decode("utf-8", "replace")


def changed_files(rev_range=("HEAD~1", "HEAD")):
    """git 差异文件清单：-z 原始输出不经任何引号转义（I-105 乙）。"""
    out = subprocess.check_output(
        ["git", "-c", "core.quotepath=off", "diff", "--name-only", "-z",
         rev_range[0], rev_range[1]], cwd=ROOT)
    toks = [t for t in out.decode("utf-8", "replace").split("\0") if t]
    return [_unquote_git_path(t) for t in toks]


def check_files(files, my_lane, lanes, shared):
    """领地判定（I-105 甲）：返回 (是否通过, 消息列表)。"""
    msgs = []
    ok = True
    for f in files:
        f = f.replace("\\", "/")
        if _tool_data_re().match(f):
            msgs.append("[豁] %s 登记库工具数据写入，不做领地拦截" % f)
            continue
        owner = _lane_of(f, lanes)
        if owner and owner != my_lane:
            msgs.append("[拒] %s 属于 %s 独占区，本路为 %s" % (f, owner, my_lane))
            ok = False
        elif owner is None and not any(f.startswith(s) for s in shared):
            msgs.append("[警] %s 不在任何领地登记内，请登记领地或归入共享区" % f)
    return ok, msgs


def _selftest():
    """领地判定构造性自检：三例构造用例加一例路径解码，不依赖
    任何提交历史。全过说明豁免、解码、拦截三条判定路径无恙。"""
    reg = REGISTRY_DIR_NAME
    lanes = {"L1": {"exclusive_paths": ["src_a/"]},
             "L2": {"exclusive_paths": ["src_b/"]}}
    shared = ["tools/", "logs/"]
    cases = [
        # 豁免：登记库数据文件经工具通道落盘，不做领地拦截
        ("豁免", "L1", ["%s/events.json" % reg, "%s/items.json" % reg,
                        "logs/runs/20260101_000000_scores.json"], True),
        # 解码：中文路径八进制转义后领地判定不失效（共享区文件应过）
        ("解码", "L1", [_unquote_git_path(
            '"tools/\\345\\210\\244\\346\\215\\256_\\346\\226\\271\\346\\241\\210.md"'),
            "tools/helper.py"], True),
        # 拦截：踏入他路独占区必须拒绝
        ("拦截", "L1", ["%s/events.json" % reg, "src_b/core.py"], False),
    ]
    bad = 0
    for name, lane, files, expect_ok in cases:
        ok, msgs = check_files(files, lane, lanes, shared)
        match = ok == expect_ok
        bad += 0 if match else 1
        print("[%s] %s 例以 %s 视角：期望%s 实际%s"
              % ("过" if match else "拒", name, lane,
                 "过" if expect_ok else "拒", "过" if ok else "拒"))
        for m in msgs:
            if m.startswith("[拒]") or m.startswith("[警]"):
                print("      %s" % m)
    # 中文路径解码正确性
    zh = _unquote_git_path('"tools/\\345\\210\\244\\346\\215\\256.md"')
    zh_ok = zh == "tools/判据.md"
    bad += 0 if zh_ok else 1
    print("[%s] 八进制转义解码：%r" % ("过" if zh_ok else "拒", zh))
    if bad == 0:
        print("[过] 构造性自检全部通过：工具写入豁免生效，中文路径"
              "判定正确，真越界仍拒")
        return 0
    print("[拒] %d 例自检不通过" % bad)
    return 1


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        return _selftest()
    if len(sys.argv) < 2:
        print("用法：check_commit.py <路线编号，如 L1>")
        return 2
    my_lane = sys.argv[1]
    conf = json.load(open(os.path.join(_DIR, "lanes.json"), encoding="utf-8"))
    lanes = conf["lanes"]
    # 容错：路线配置若缺 shared_paths 键，按协作架构模板回退规定的
    # 共享区 tools/ 与 logs/，不让自查器因配置形态差异自身崩溃
    shared = conf.get("shared_paths")
    if shared is None:
        shared = ["tools/", "logs/"]
        print("[警] lanes.json 缺 shared_paths 键，按协作架构模板回退"
              "共享区 tools/ 与 logs/")
    if my_lane not in lanes:
        print("路线不存在：%s" % my_lane)
        return 2

    msg = subprocess.check_output(["git", "log", "-1", "--format=%s"], cwd=ROOT).decode("utf-8", "replace")
    files = changed_files()

    ok = True
    if not re.search(r"I-\d{3}", msg):
        print("[拒] 提交信息未引用事项编号：%s" % msg[:60])
        ok = False
    sys.path.insert(0, _DIR)
    import langcheck
    for cat, word, sug in langcheck.scan_text(msg):
        print("[拒] 语言规范 %s「%s」：%s" % (cat, word, sug))
        ok = False
    fok, msgs = check_files(files, my_lane, lanes, shared)
    for m in msgs:
        print(m)
    ok = ok and fok
    print("[过] 提交检查通过" if ok else "[拒] 提交检查未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
