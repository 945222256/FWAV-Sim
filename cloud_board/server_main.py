# -*- coding: utf-8 -*-
"""server_main.py — 云端看板服务入口：装配全部模块并启动 HTTPS 服务。

启动顺序：建表、注册路由（导入即注册）、终结 TLS、常驻服务。
systemd 单元见 deploy/lpboard.service。仅使用 Python 标准库。
"""
import db
import settings

# 路由注册靠导入副作用完成，顺序不影响精确匹配路由。
import api_auth      # noqa: F401
import api_agent     # noqa: F401
import api_ledger    # noqa: F401
import api_dispatch  # noqa: F401
import api_intake    # noqa: F401
import api_map       # noqa: F401
import api_console   # noqa: F401  # I-142 控制台模块
try:  # I-217 补修：本地隔离验证环境无 api_lab（仅远端部署含），容缺失启动
    import api_lab   # noqa: F401  # I-130 参数实验室（I-164 修复：重挂载）
except ImportError:
    pass
import webui         # noqa: F401
import httpd


def main():
    db.init_schema()
    httpd.serve(settings.HOST, settings.PORT, settings.CERT_FILE, settings.KEY_FILE)


if __name__ == "__main__":
    main()
