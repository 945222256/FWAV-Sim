# -*- coding: utf-8 -*-
"""i168_deploy.py — I-168 施工：总览页三层前端部署云端。

沿用 lp_sync/i136_deploy.py 形态：board_ui/dist 全量替换
cloud_board/app 本地镜像后 SFTP 上传 /opt/lpboard/app，重启
lpboard.service（app 资产路由启动时注册，新哈希资产需重启可见），
探活 /app/ 与 /api/blueprints。纯前端部署，服务端代码零改动。"""
import json
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")
import paramiko

SECRET = json.load(open(r"G:\Playground\isaac_flapper_sim\lp_sync\cloud_secret.json", encoding="utf-8"))
ROOT = r"G:\Playground\isaac_flapper_sim"
DIST = os.path.join(ROOT, "board_ui", "dist")
APP = os.path.join(ROOT, "cloud_board", "app")

# 1) dist -> cloud_board/app（先清空再拷，保证没有陈旧哈希资产残留）
if os.path.isdir(APP):
    shutil.rmtree(APP)
shutil.copytree(DIST, APP)
print("dist -> cloud_board/app:", sorted(os.listdir(APP))[:6], "...")

cli = paramiko.SSHClient()
cli.set_auto_add_policy(True) if hasattr(cli, "set_auto_add_policy") else None
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(SECRET["host"], username=SECRET["ssh_user"], password=SECRET["ssh_password"], timeout=20)


def run(cmd):
    _, out, err = cli.exec_command(cmd, timeout=120)
    code = out.channel.recv_exit_status()
    text = out.read().decode("utf-8", "replace")
    etext = err.read().decode("utf-8", "replace")
    if code != 0:
        raise RuntimeError("远端命令失败 %s：\n%s\n%s" % (cmd, text, etext))
    return text


def upload_tree(local_dir, remote_dir):
    run("mkdir -p %s" % remote_dir)
    for fn in os.listdir(local_dir):
        lp = os.path.join(local_dir, fn)
        rp = remote_dir.rstrip("/") + "/" + fn
        if os.path.isdir(lp):
            upload_tree(lp, rp)
        else:
            sftp.put(lp, rp)


sftp = cli.open_sftp()
# 2) 上传前整目录备份（.bak_i168 惯例，服务器侧可回退）
run("test -d /opt/lpboard/app && rm -rf /opt/lpboard/app.bak_i168 && cp -r /opt/lpboard/app /opt/lpboard/app.bak_i168 || true")
print("backup: /opt/lpboard/app.bak_i168")
upload_tree(APP, "/opt/lpboard/app")
print("app uploaded")
sftp.close()

# 3) 重启与探活
print(run("systemctl restart lpboard.service && sleep 2 && systemctl is-active lpboard.service"))
print(run("curl -sk -o /dev/null -w 'app HTTP %{http_code}\\n' https://127.0.0.1/app/"))
print(run("curl -sk https://127.0.0.1/api/blueprints | head -c 120"))
cli.close()
print("I-168 云端部署完成")
