# -*- coding: utf-8 -*-
"""
install_commit_hook.py 提交后自动检查的一键安装器（事项 I-016）
================================================================

用法：

  PYTHONUTF8=1 python -m modules.backend.install_commit_hook
      把提交后自动检查写入本仓库 .git/hooks/post-commit。安装与否由
      主会话决定，本脚本被显式运行时才安装，不会被其他动作带动安装。

  PYTHONUTF8=1 python -m modules.backend.install_commit_hook --check
      只显示将要写入的钩子内容与目标路径，不写入任何文件，供安装前
      核对。

  PYTHONUTF8=1 python -m modules.backend.install_commit_hook --force
      目标位置已有内容不同的钩子时，先把旧内容备份为
      post-commit.backup.<时间戳>，再写入新钩子。

钩子行为：每次提交完成后，在本仓库根目录运行能力基准运行器的快档
（契约自检加悬停模拟后端基准，纯 Python，不启动 Isaac），并把运行输
出追加到 logs/bench_scores/post_commit_runs.log。快档本身的分数文件
仍由运行器按时间戳写入 logs/bench_scores/ 目录。钩子恒以零退出码结
束，检查结果不改变已完成的提交。

说明一：快档为纯 Python，十几秒量级，钩子同步执行会使每次提交多等
待这段时间；如不接受，可不安装或自行改为后台执行。

说明二：钩子内用 python 命令调用运行器，与仓库在 Windows 上的现行
调用约定一致；若提交环境的命令行找不到 python，日志文件内会留有相
应错误文本，提交本身不受影响。
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

HOOK_REL_PATH = os.path.join(".git", "hooks", "post-commit")
HOOK_LOG_REL_PATH = os.path.join("logs", "bench_scores",
                                 "post_commit_runs.log")

HOOK_CONTENT = """#!/bin/sh
# 提交后自动检查（事项 I-016），由 modules/backend/install_commit_hook.py 写入。
# 运行能力基准快档（契约自检加悬停模拟后端基准，不启动 Isaac），
# 并把输出追加到 logs/bench_scores/post_commit_runs.log。
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$ROOT" ]; then
    exit 0
fi
cd "$ROOT" || exit 0
mkdir -p logs/bench_scores
PYTHONUTF8=1 python -m modules.backend.bench_runner --fast \
    >> logs/bench_scores/post_commit_runs.log 2>&1
exit 0
"""


def _hook_target_path():
    """返回钩子目标路径。优先按 .git 目录推算，工作树场景读指针文件。"""
    git_dir = os.path.join(_REPO_ROOT, ".git")
    if os.path.isfile(git_dir):
        # 工作树场景：.git 是指针文件，内容形如 gitdir: 路径。
        try:
            with open(git_dir, encoding="utf-8") as fh:
                line = fh.read().strip()
            if line.startswith("gitdir:"):
                git_dir = line.split(":", 1)[1].strip()
        except OSError:
            pass
    return os.path.join(git_dir, "hooks", "post-commit")


def install(force=False):
    """安装钩子，返回是否成功。"""
    target = _hook_target_path()
    hooks_dir = os.path.dirname(target)
    if not os.path.isdir(hooks_dir):
        print("钩子目录不存在：%s" % hooks_dir)
        return False
    if os.path.exists(target):
        try:
            with open(target, encoding="utf-8", errors="replace") as fh:
                old = fh.read()
        except OSError as exc:
            print("既有钩子不可读：%s" % exc)
            return False
        if old == HOOK_CONTENT:
            print("钩子已是本安装器的内容，无需重复安装：%s" % target)
            return True
        if not force:
            print("目标位置已有内容不同的钩子，未覆盖：%s" % target)
            print("如需覆盖，请加 --force 运行，旧内容会先备份。")
            return False
        backup = target + ".backup." + time.strftime("%Y%m%d_%H%M%S")
        with open(backup, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(old)
        print("旧钩子已备份至 %s" % backup)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(HOOK_CONTENT)
    os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP
             | stat.S_IXOTH)
    print("钩子已安装：%s" % target)
    print("钩子日志：%s" % os.path.join(_REPO_ROOT, HOOK_LOG_REL_PATH))
    print("验证方式：在仓库内做一次提交，随后查看上述日志文件是否追加了"
          "基准快档的分数表。")
    return True


def main(argv=None):
    """命令行入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(
        description="一键安装提交后自动检查（事项 I-016）。"
                    "安装与否由主会话决定，本脚本被显式运行时才安装。")
    parser.add_argument("--check", action="store_true",
                        help="只显示将要写入的钩子内容与目标路径，"
                             "不写入文件")
    parser.add_argument("--force", action="store_true",
                        help="目标位置已有不同钩子时，备份后覆盖")
    args = parser.parse_args(argv)
    target = _hook_target_path()
    if args.check:
        print("钩子目标路径 %s" % target)
        print("钩子日志路径 %s"
              % os.path.join(_REPO_ROOT, HOOK_LOG_REL_PATH))
        print("以下为将要写入的钩子内容：")
        print("-" * 60)
        print(HOOK_CONTENT, end="")
        print("-" * 60)
        return 0
    ok = install(force=args.force)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
