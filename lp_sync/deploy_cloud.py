# -*- coding: utf-8 -*-
"""deploy_cloud.py — 云端看板一键部署（在本地工地机运行）。

动作六件：打包上传 cloud_board、远端初始化账号与令牌、抄存代理令牌、
安装 systemd 单元、启动并验证 HTTPS 服务、回显访问地址。
部署凭证读 lp_sync/cloud_secret.json，该文件不进版本库。
依赖 paramiko。本脚本只在部署与升级时运行。
"""
import json
import os
import posixpath
import stat
import sys

import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "cloud_board")
SECRET = json.load(open(os.path.join(ROOT, "lp_sync", "cloud_secret.json"), encoding="utf-8"))
REMOTE_DIR = "/opt/lpboard"


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(SECRET["host"], username=SECRET["ssh_user"],
                password=SECRET["ssh_password"], timeout=20)
    return cli


def run(cli, cmd):
    _, out, err = cli.exec_command(cmd, timeout=120)
    code = out.channel.recv_exit_status()
    text = out.read().decode("utf-8", "replace")
    etext = err.read().decode("utf-8", "replace")
    if code != 0:
        raise RuntimeError("远端命令失败 %s：\n%s\n%s" % (cmd, text, etext))
    return text


def upload(cli):
    sftp = cli.open_sftp()
    for dirpath, dirnames, filenames in os.walk(SRC_DIR):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "data")]
        rel = os.path.relpath(dirpath, SRC_DIR)
        rdir = REMOTE_DIR if rel == "." else posixpath.join(REMOTE_DIR, rel.replace(os.sep, "/"))
        try:
            sftp.stat(rdir)
        except FileNotFoundError:
            sftp.mkdir(rdir)
        for fn in filenames:
            if fn in ("secret.json",):
                continue                      # 密钥文件永不上传已有内容之外的位置。
            sftp.put(os.path.join(dirpath, fn), posixpath.join(rdir, fn))
    sftp.close()


def main():
    cli = connect()
    try:
        run(cli, "mkdir -p %s" % REMOTE_DIR)
        print("上传文件……")
        upload(cli)
        boot = run(cli, "test -f %s/secret.json && echo yes || echo no" % REMOTE_DIR).strip()
        if boot == "no":
            print("首次初始化：建库、建机主账号、签发代理令牌……")
            out = run(cli, "cd %s && python3 bootstrap.py '%s' '%s' '%s'"
                      % (REMOTE_DIR, SECRET["board_user"], SECRET["board_password"],
                         SECRET["agent_name"]))
            print(out)
            token = [l for l in out.splitlines() if l.strip() and not l.startswith(("初始化", "机主", "代理", "数据库", "证书"))]
            token = token[0].strip() if token else ""
            if not token:
                raise RuntimeError("未能在输出中解析出代理令牌")
            os.makedirs(os.path.join(ROOT, "lp_sync"), exist_ok=True)
            with open(os.path.join(ROOT, "lp_sync", "agent_token"), "w", encoding="utf-8") as f:
                f.write(token)
            print("代理令牌已抄存到 lp_sync/agent_token")
        run(cli, "cp %s/deploy/lpboard.service /etc/systemd/system/lpboard.service" % REMOTE_DIR)
        run(cli, "systemctl daemon-reload && systemctl enable lpboard.service "
                 "&& systemctl restart lpboard.service")
        print(run(cli, "sleep 2 && systemctl is-active lpboard.service"))
        print(run(cli, "curl -sk -o /dev/null -w '本地 HTTPS 探活 HTTP %{http_code}\\n' https://127.0.0.1/"))
        print("部署完成。浏览器访问：https://%s/ （自签证书首次访问需手动信任）" % SECRET["host"])
    finally:
        cli.close()


if __name__ == "__main__":
    main()
