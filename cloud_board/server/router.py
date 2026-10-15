# -*- coding: utf-8 -*-
"""router.py — 极简路由器：把方法加路径分发给处理函数。

处理函数签名固定为 handler(ctx)，ctx 由 httpd.py 构造。
路由表按注册顺序匹配，先注册者优先。仅使用 Python 标准库。
"""

_routes = []


def route(method, path):
    """装饰器：注册一个精确路径的路由。"""
    def deco(fn):
        _routes.append((method.upper(), path, fn))
        return fn
    return deco


def dispatch(method, path, ctx):
    """按方法与路径查找处理函数。找不到时抛 KeyError 由服务层转 404。"""
    for m, p, fn in _routes:
        if m == method.upper() and p == path:
            return fn(ctx)
    raise KeyError("无路由：%s %s" % (method, path))


def all_routes():
    return list(_routes)
