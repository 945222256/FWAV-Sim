# -*- coding: utf-8 -*-
"""I-217 演练辅助：列出并按需终止遗留的演练内核/工作器/执行器进程。
仅匹配命令行含 lane_workers_drill 或 orchestrator_kernel --state-root 的
python 进程（生产内核 --state-root 默认值不在匹配范围）。"""
import os
import subprocess
import sys

DRY = "--kill" not in sys.argv

out = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Get-CimInstance Win32_Process -Filter \"name like 'python%'\" | "
     "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
    capture_output=True, text=True).stdout or ""

targets = []
for line in out.splitlines():
    line = line.strip()
    if not line or "|" not in line:
        continue
    pid_s, cmd = line.split("|", 1)
    if "lane_workers_drill" in cmd or (
            "orchestrator_kernel" in cmd and "--state-root" in cmd):
        try:
            targets.append((int(pid_s), cmd))
        except ValueError:
            pass

for pid, cmd in targets:
    print("%d | %s" % (pid, cmd[:120]))
    if not DRY:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
print("共 %d 个%s" % (len(targets), "（已终止）" if not DRY else "（仅列出，加 --kill 终止）"))
