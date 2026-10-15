# -*- coding: utf-8 -*-
"""auth.py — 身份与权限：用户登录、会话签发、令牌校验。

两种身份：
  机主账号：浏览器登录拿签名会话 Cookie，全权限。
  代理令牌：本地工地机持有，按令牌登记的路线行使写权限。
密码只存 PBKDF2 哈希，会话票据带过期时间与 HMAC 签名。
仅使用 Python 标准库。
"""
import base64
import hashlib
import hmac
import secrets
import datetime

import db
import settings
from httpd import ApiError


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt),
        settings.PBKDF2_ROUNDS).hex()


# ---------------- 机主账号与会话 ----------------

def create_user(username, password, role="owner"):
    salt = secrets.token_hex(16)
    with db.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?)",
                     (username, hash_password(password, salt), salt, role))


def verify_password(username, password):
    row = db.query_one("SELECT * FROM users WHERE username=?", (username,))
    if row is None:
        return False
    cand = hash_password(password, row["salt"])
    return hmac.compare_digest(cand, row["pw_hash"])


def _sign(message):
    key = settings.session_key().encode("utf-8")
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_session(username):
    """签发会话票据：用户名、过期时刻、随机数三要素加签名。"""
    expires = int(datetime.datetime.now().timestamp()) + settings.SESSION_TTL_S
    nonce = secrets.token_hex(8)
    payload = "%s|%d|%s" % (username, expires, nonce)
    return "%s|%s" % (payload, _sign(payload))


def session_user(ctx):
    """从请求 Cookie 解出登录用户。无效或过期返回 None。"""
    ticket = ctx.cookie("lp_session")
    if not ticket:
        return None
    parts = ticket.split("|")
    if len(parts) != 4:
        return None
    username, expires, nonce, sig = parts
    if not hmac.compare_digest(_sign("%s|%s|%s" % (username, expires, nonce)), sig):
        return None
    if int(expires) < int(datetime.datetime.now().timestamp()):
        return None
    return username


# ---------------- 代理令牌 ----------------

def issue_token(name, lanes):
    """签发代理令牌。明文令牌只在签发瞬间返回一次，库里只存哈希。"""
    secret = secrets.token_urlsafe(32)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tokens (name, secret_hash, lanes, created_at) VALUES (?,?,?,?)",
            (name, hashlib.sha256(secret.encode("utf-8")).hexdigest(),
             json_dumps(lanes), datetime.date.today().isoformat()))
    return secret


def json_dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


def token_identity(ctx):
    """校验 Bearer 令牌，返回 (名称, 路线列表)。无效令牌抛业务拒绝。"""
    secret = ctx.bearer_token()
    if not secret:
        raise ApiError("缺少令牌：写入操作要求 Bearer 令牌或已登录会话", 401)
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    row = db.query_one("SELECT * FROM tokens WHERE secret_hash=? AND revoked=0", (digest,))
    if row is None:
        raise ApiError("令牌无效或已吊销", 403)
    import json
    return row["name"], json.loads(row["lanes"])


# ---------------- 统一入口 ----------------

def caller(ctx):
    """解析当前请求身份：登录会话或代理令牌。

    返回 (身份名, 路线列表)。机主会话的路线列表含全部路线。
    两者皆无时抛 401。
    """
    user = session_user(ctx)
    if user:
        return user, ["L1", "L2", "L3"]
    return token_identity(ctx)


def lane_allowed(lanes, lane):
    return not lane or lane in lanes
