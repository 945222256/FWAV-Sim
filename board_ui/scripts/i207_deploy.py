# -*- coding: utf-8 -*-
"""i207_deploy.py — I-207 施工：观察窗编号显示与超时保留部署。

一，board_ui/dist 全量替换 cloud_board/app 并上传（前端）；二，
cloud_board/api_console.py 备份后上传 /opt/lpboard/（当前认领核实与
retained 节）。重启探活。"""
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

if os.path.isdir(APP):
    shutil.rmtree(APP)
shutil.copytree(DIST, APP)

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(SECRET["host"], username=SECRET["ssh_user"], password=SECRET["ssh_password"], timeout=20)


def run(cmd):
    _, out, err = cli.exec_command(cmd, timeout=180)
    code = out.channel.recv_exit_status()
    text = out.read().decode("utf-8", "replace")
    if code != 0:
        raise RuntimeError("远端失败 %s：%s%s" % (cmd, text, err.read().decode("utf-8", "replace")))
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
run("rm -rf /opt/lpboard/app.bak_i207 && cp -r /opt/lpboard/app /opt/lpboard/app.bak_i207 || true")
upload_tree(APP, "/opt/lpboard/app")
run("cp /opt/lpboard/api_console.py /opt/lpboard/api_console.py.bak_i207 || true")
sftp.put(os.path.join(ROOT, "cloud_board", "api_console.py"), "/opt/lpboard/api_console.py")
print("app 与 api_console.py 上传完成")
sftp.close()
print(run("systemctl restart lpboard.service && sleep 2 && systemctl is-active lpboard.service"))
print(run("curl -sk -o /dev/null -w 'app HTTP %{http_code}\n' https://127.0.0.1/app/"))
cli.close()
print("I-207 云端部署完成")
