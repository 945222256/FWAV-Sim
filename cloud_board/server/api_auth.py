# -*- coding: utf-8 -*-
"""api_auth.py — 登录、登出与当前身份查询。仅使用 Python 标准库。"""
import datetime

import auth
import router
import settings
from httpd import ApiError


@router.route("POST", "/api/auth/login")
def login(ctx):
    username = (ctx.body.get("username") or "").strip()
    password = ctx.body.get("password") or ""
    if not username or not password:
        raise ApiError("账号与密码不能为空")
    if not auth.verify_password(username, password):
        raise ApiError("账号或密码错误", 401)
    ticket = auth.issue_session(username)
    ctx.set_cookie("lp_session", ticket, settings.SESSION_TTL_S)
    return {"ok": True, "result": {"user": username,
                                   "expires_at": datetime.datetime.now().timestamp() + settings.SESSION_TTL_S}}


@router.route("POST", "/api/auth/logout")
def logout(ctx):
    ctx.drop_cookie("lp_session")
    return {"ok": True, "result": "已退出"}


@router.route("GET", "/api/auth/whoami")
def whoami(ctx):
    user = auth.session_user(ctx)
    if user:
        return {"ok": True, "result": {"user": user, "type": "session"}}
    try:
        name, lanes = auth.token_identity(ctx)
        return {"ok": True, "result": {"user": name, "type": "token", "lanes": lanes}}
    except ApiError:
        return {"ok": False, "error": "未登录"}, 401
