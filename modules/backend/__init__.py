# -*- coding: utf-8 -*-
"""backend 包为仿真后端的实现地。

本包当前含一个实现。MockBackend 类为纯 Python 模拟后端，它实现
modules.contracts.SimBackend 契约。将来的 Isaac 后端实现同样归属本包。
"""

from .mock_backend import MockBackend

__all__ = ["MockBackend"]
