# -*- coding: utf-8 -*-
"""bootstrap.py — 云端服务初始化工具（只在部署时运行一次）。

动作四件：建库建表、写机主账号、签发本地代理令牌、生成自签证书。
命令行用法：
  python3 bootstrap.py <机主账号> <机主密码> <代理名称>
密钥文件 secret.json 与代理令牌明文在本命令输出，抄存后不得外传。
仅使用 Python 标准库。
"""
import base64
import os
import secrets
import subprocess
import sys

import db
import settings
import auth


def gen_self_signed_cert():
    """生成十年期自签证书。浏览器首次访问需手动信任一次。"""
    if os.path.exists(settings.CERT_FILE) and os.path.exists(settings.KEY_FILE):
        return
    os.makedirs(os.path.dirname(settings.CERT_FILE), exist_ok=True)
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", settings.KEY_FILE, "-out", settings.CERT_FILE,
        "-days", "3650", "-subj", "/CN=lp-board",
    ], check=True, capture_output=True)


def main():
    username, password, agent_name = sys.argv[1], sys.argv[2], sys.argv[3]
    db.init_schema()

    secret = {"session_key": secrets.token_urlsafe(48)}
    with open(settings.SECRET_PATH, "w", encoding="utf-8") as f:
        import json
        json.dump(secret, f)

    auth.create_user(username, password)
    token = auth.issue_token(agent_name, ["L1", "L2", "L3"])
    gen_self_signed_cert()

    print("初始化完成。")
    print("机主账号：%s" % username)
    print("代理令牌（只显示这一次，抄存到本地 lp_sync/agent_token）：" )
    print(token)
    print("数据库：%s" % settings.DB_PATH)
    print("证书：%s" % settings.CERT_FILE)


if __name__ == "__main__":
    main()
