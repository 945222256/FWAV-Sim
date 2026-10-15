# -*- coding: utf-8 -*-
"""settings.py — 云端看板服务的全部配置。

配置来源是本文件与运行目录下的 secret.json。数据库路径、证书路径、
监听端口在此集中声明；会话签名密钥与初始账号只写进 secret.json，
secret.json 不进版本库。
仅使用 Python 标准库。
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SECRET_PATH = os.path.join(BASE_DIR, "secret.json")

HOST = "0.0.0.0"              # 云端门面必须公网可达。
PORT = 8443                   # 443 已被本机既有 nginx 业务占用，看板独立用 8443。
DB_PATH = os.path.join(DATA_DIR, "ledger.db")
CERT_FILE = os.path.join(DATA_DIR, "server-cert.pem")
KEY_FILE = os.path.join(DATA_DIR, "server-key.pem")

SESSION_TTL_S = 7 * 24 * 3600      # 登录会话有效期七天。
PBKDF2_ROUNDS = 200_000            # 密码哈希迭代次数。
AGENT_TOKEN_FILE = "agent_token"   # 代理令牌明文只出现在本地机器。


def load_secret():
    """读取密钥文件。密钥文件缺失时启动即报错，禁止无密钥运行。"""
    if not os.path.exists(SECRET_PATH):
        raise RuntimeError("密钥文件缺失：%s（先运行 bootstrap.py 初始化）" % SECRET_PATH)
    with open(SECRET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def session_key():
    return load_secret()["session_key"]
