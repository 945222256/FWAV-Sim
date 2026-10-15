# -*- coding: utf-8 -*-
"""演示：非零退出码=不通过。"""
print("demo_fail：检查未通过示例")
sys.exit(3) if False else None
import sys
sys.exit(3)
