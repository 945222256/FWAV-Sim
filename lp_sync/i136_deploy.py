# -*- coding: utf-8 -*-
"""i136_deploy.py — I-136 施工：第二版前端与共用载荷构建器部署云端。

沿用 lp_sync/i130_deploy_front.py 形态：dist 全量替换 /opt/lpboard/app，
lab_payload.py 上传前在 VPS 留 .bak_i136 备份，重启服务并探活，
末尾校验云端实验室接口已回第二版载荷（schema=lab-payload-v2）。"""
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
print("dist -> cloud_board/app:", sorted(os.listdir(APP)))

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


sftp = cli.open_sftp()


def upload_tree(local_dir, remote_dir):
    run("mkdir -p %s" % remote_dir)
    for fn in os.listdir(local_dir):
        lp = os.path.join(local_dir, fn)
        rp = remote_dir.rstrip("/") + "/" + fn
        if os.path.isdir(lp):
            upload_tree(lp, rp)
        else:
            sftp.put(lp, rp)


# 2) 旧载荷备份（.bak_i136 惯例）后上传新版
run("test -f /opt/lpboard/lab_payload.py && cp /opt/lpboard/lab_payload.py "
    "/opt/lpboard/lab_payload.py.bak_i136 || true")
print("backup: lab_payload.py.bak_i136")
upload_tree(APP, "/opt/lpboard/app")
print("app uploaded")
sftp.put(os.path.join(ROOT, "lp_board", "lab_payload.py"), "/opt/lpboard/lab_payload.py")
print("lab_payload.py uploaded")
sftp.close()

# 3) 重启与探活
print(run("systemctl restart lpboard.service && sleep 2 && systemctl is-active lpboard.service"))
print(run("curl -sk -o /dev/null -w 'app HTTP %{http_code}\\n' https://127.0.0.1/app/"))
print(run("curl -sk -o /dev/null -w 'lab manifest HTTP %{http_code}\\n' https://127.0.0.1/app/lab/manifest.json"))

# 4) 云端接口回第二版载荷校验
schema = run("curl -sk https://127.0.0.1/api/lab/params | head -c 200")
print("云端载荷头部：", schema[:160])
if "lab-payload-v2" not in run("curl -sk https://127.0.0.1/api/lab/params"):
    raise RuntimeError("云端接口未返回第二版载荷 schema")
print("云端实验室接口已回第二版载荷")
cli.close()
print("I-136 云端部署完成")
