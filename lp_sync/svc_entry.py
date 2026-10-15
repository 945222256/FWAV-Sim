# -*- coding: utf-8 -*-
"""svc_entry.py — I-208 系统托管无窗口入口（runpy 方式加载真实模块）。

由 Windows 任务计划程序以当前用户（Anni InteractiveToken）的
pythonw.exe 运行（无控制台；不使用 SYSTEM 账户——expanduser 定位
ZCode 安装、会话库与凭证依赖用户目录）。加载 host_service.py 或
orchestrator_kernel.py 的真实 main()，不复制逻辑。标准输出与错误用
文本模式日志流（print(str) 直接可用，二进制流会抛类型错）。

用法：
  pythonw svc_entry.py host      → 无窗口宿主（日志 lp_sync/host_service.log）
  pythonw svc_entry.py kernel    → 无窗口编排内核（日志 lp_sync/kernel_service.log）
仅使用 Python 标准库。
"""
import io
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    if target == "notifications":
        log_path = os.path.join(HERE, "notification_service.log")
        module = "luna_consumer"
    elif target == "collector":
        log_path = os.path.join(HERE, "sync_agent.log")
        module = "sync_agent"
    elif target == "kernel":
        log_path = os.path.join(HERE, "kernel_service.log")
        module = "orchestrator_kernel"
    else:
        # host 与无参数都按宿主处理（任务计划最常见形态）。
        log_path = os.path.join(HERE, "host_service.log")
        module = "host_service"
    os.chdir(HERE)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    sys.stdin = open(os.devnull, "r", encoding="utf-8")
    # 文本模式（utf-8、行缓冲）：print(str) 正常写入。
    f = io.open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = f
    sys.stderr = f
    sys.argv = [os.path.join(HERE, module + ".py")]
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
