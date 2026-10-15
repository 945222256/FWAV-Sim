# -*- coding: utf-8 -*-
"""check_commit.py — 提交检查：领地与编号。

用法：python items_registry/check_commit.py <路线编号>
      python items_registry/check_commit.py --selftest   误报回归自检
检查两条：
  1. 最近一次提交信息必须引用至少一个事项编号（I-xxx）。
  2. 该提交触碰的文件必须在本路独占区或共享区内；踏入他路独占区即拒绝。
建议在提交后、推送前运行；可作为持续集成的一个步骤。
仅使用 Python 标准库。

I-105 误报修复（Q-015 转正）：
  甲、登记库工具写入豁免：两个登记库的数据文件由工具原子写入
  （总账读写、参数登记与退役、待审队列、事件流），任何一路经工具
  通道的正常落盘不做领地拦截；lanes.json 是领地配置非工具数据，
  不在豁免之列。
  乙、文件名解析：git 差异清单改用 -z 原始输出（路径不经引号与
  八进制转义），并对历史输出形态保留解码兜底，中文文件名的领地
  判定不再误报。--selftest 用三个历史误报提交回归本修复。
"""
import json
import os
import re
import subprocess
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_DIR)

# 登记库工具写入豁免清单（I-105）：只豁免数据文件，不豁免代码与配置。
_TOOL_DATA_RE = re.compile(
    r"^(items_registry/(?!lanes\.json)[^/]+\.json"
    r"|params_registry/(params|archive)\.json)$")


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
        if _TOOL_DATA_RE.match(f):
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
    """I-105 误报回归：三个历史误报提交的文件清单重判，全过即守门员无恙。"""
    conf = json.load(open(os.path.join(_DIR, "lanes.json"), encoding="utf-8"))
    lanes = conf["lanes"]
    shared = conf.get("shared_paths") or ["tools/", "logs/"]
    cases = [
        # 提交 17e5ab1（L1 经工具落盘总账被误判越界）
        ("17e5ab1", "L1", ["items_registry/events.json", "items_registry/items.json",
                           "logs/bench_scores/20261002_074702_scores.json",
                           "logs/bench_scores/post_commit_runs.log"], True),
        # 提交 e2287a0（中文路径八进制转义致领地判定失效误报）
        ("e2287a0", "L3", ['"tools/\\345\\210\\244\\346\\215\\256_\\345\\207\\200\\345\\217\\243'
                           '\\345\\276\\204\\346\\224\\276\\350\\241\\214\\351\\227\\250_'
                           '\\351\\242\\204\\347\\231\\273\\350\\256\\260\\350\\215\\211\\346\\241\\210_20261002.md"',
                           "tools/ident_fd_estimator.py",
                           "items_registry/review_queue.json"], True),
        # 提交 a4078bf（同族：总账工具写入加中文配置文件，L2 视角越界仍须报）
        ("a4078bf", "L2", ["items_registry/events.json", "items_registry/items.json",
                           "logs/bench_scores/20261002_073434_scores.json",
                           "协作架构_20260930.md"], False),
    ]
    bad = 0
    for rev, lane, files, expect_ok in cases:
        files = [_unquote_git_path(f) for f in files]
        ok, msgs = check_files(files, lane, lanes, shared)
        match = ok == expect_ok
        bad += 0 if match else 1
        print("[%s] %s 以 %s 视角：期望%s 实际%s"
              % ("过" if match else "拒", rev, lane,
                 "过" if expect_ok else "拒", "过" if ok else "拒"))
        for m in msgs:
            if m.startswith("[拒]") or m.startswith("[警]"):
                print("      %s" % m)
    # 中文路径解码正确性（乙类核心）
    zh = _unquote_git_path('"tools/\\345\\210\\244\\346\\215\\256.md"')
    zh_ok = zh == "tools/判据.md"
    bad += 0 if zh_ok else 1
    print("[%s] 八进制转义解码：%r" % ("过" if zh_ok else "拒", zh))
    if bad == 0:
        print("[过] 误报回归全部通过：工具写入豁免生效，中文路径判定正确，"
              "真越界仍拒（守门员只修误报不拆门）")
        return 0
    print("[拒] %d 例回归不通过" % bad)
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
    # I-041 容错（I-030 施工收尾加固）：泳道配置若缺 shared_paths 键，
    # 按《协作架构_20260930.md》第五节回退规定的共享区 tools/ 与 logs/，
    # 不再因配置形态差异让自查器自身崩溃（与 I-040 看板容错同族）
    shared = conf.get("shared_paths")
    if shared is None:
        shared = ["tools/", "logs/"]
        print("[警] lanes.json 缺 shared_paths 键，按协作架构第五节回退"
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
