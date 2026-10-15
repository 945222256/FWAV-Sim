# -*- coding: utf-8 -*-
"""
hss_tpe.py — TPE 搜索器的转发层（I-080 收编后）
================================================
历史：本文件曾是 tools/unified_search_v3.py 三段定义的逐字拷贝，
复制原因是 v3 顶层导入统一搜索框架在登记库类型变更后会崩。
现状：TPE 已收编进 tools/optimizers.py 唯一模块（纯 numpy、零框架
依赖），本文件改为转发，保留 sample_rng、hist_draw、TPE 三个名字，
既有引用方（ltv/hss_i051_tpe.py、ltv/hss_kernel.py）不需要改导入。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.optimizers import TPE, hist_draw, sample_rng  # noqa: F401
