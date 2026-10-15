# -*- coding: utf-8 -*-
"""i202_deploy.py — I-202 施工：七百克候选动作预览三路部署。

沿用 lp_sync/i136_deploy.py 形态：一，board_ui/dist 全量替换
cloud_board/app 本地镜像并上传 /opt/lpboard/app（含 eaglex700 帧资产，
/app/lab/ 路径随 app 目录发布）；二，lp_board/lab_payload.py 备份后
上传 /opt/lpboard/；三，Jane 尾部修订版交付数据文件上传
/opt/lpboard/eaglex/（lab_payload 的 _EAGLEX_BASES 第二路径）。
重启服务后探活 /app/、/api/lab/params 的 eaglex700 节。"""
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
JANE = os.path.join(ROOT, "eagle_shell", "jane_preview_20261013_tail", "output")
DATA_FILES = ["geometry_and_binding.json", "chord_stations.csv", "delivery_checks.json"]

if os.path.isdir(APP):
    shutil.rmtree(APP)
shutil.copytree(DIST, APP)
print("dist -> cloud_board/app 含帧资产:",
      os.path.exists(os.path.join(APP, "lab", "eaglex700", "render_report.json")))

cli = paramiko.SSHClient()
cli.set_auto_add_policy(True) if hasattr(cli, "set_auto_add_policy") else None
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(SECRET["host"], username=SECRET["ssh_user"], password=SECRET["ssh_password"], timeout=20)


def run(cmd):
    _, out, err = cli.exec_command(cmd, timeout=180)
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
run("test -d /opt/lpboard/app && rm -rf /opt/lpboard/app.bak_i202_r3 && cp -r /opt/lpboard/app /opt/lpboard/app.bak_i202_r3 || true")
print("backup: app.bak_i202")
upload_tree(APP, "/opt/lpboard/app")
print("app uploaded (含 /app/lab/eaglex700)")

run("cp /opt/lpboard/lab_payload.py /opt/lpboard/lab_payload.py.bak_i202 || true")
sftp.put(os.path.join(ROOT, "lp_board", "lab_payload.py"), "/opt/lpboard/lab_payload.py")
print("lab_payload.py uploaded")
run("cp /opt/lpboard/webui.py /opt/lpboard/webui.py.bak_i202 || true")
sftp.put(os.path.join(ROOT, "cloud_board", "server", "webui.py"), "/opt/lpboard/webui.py")
run("cp /opt/lpboard/httpd.py /opt/lpboard/httpd.py.bak_i202 || true")
sftp.put(os.path.join(ROOT, "cloud_board", "server", "httpd.py"), "/opt/lpboard/httpd.py")
print("webui.py 与 httpd.py uploaded（MIME 登记加 Range 二零六）")

run("mkdir -p /opt/lpboard/eaglex")
for fn in DATA_FILES:
    sftp.put(os.path.join(JANE, fn), "/opt/lpboard/eaglex/" + fn)
print("jane 数据文件上传:", DATA_FILES)
# 帧资产结构元数据（渲染事实记录）随数据文件一并部署
sftp.put(os.path.join(ROOT, "board_ui", "public", "lab", "eaglex700", "render_report.json"),
         "/opt/lpboard/eaglex/render_report.json")
print("render_report.json uploaded")
# 动画第二版数据文件（report 与命令表；导出 glb 待独立复验与登记，阶段展示只接视频）
run("mkdir -p /opt/lpboard/eaglexv2")
V2 = os.path.join(ROOT, "eagle_shell", "l8_i201_animv2", "output")
for fn in ("anim_config_v2_report.json", "joint_commands.csv"):
    sftp.put(os.path.join(V2, fn), "/opt/lpboard/eaglexv2/" + fn)
run("mkdir -p /opt/lpboard/eaglexv2/clips")
for fn in os.listdir(os.path.join(V2, "clips")):
    # lab_payload 从此数据目录判定 poster 是否存在，封面必须与视频一并上传。
    if fn.endswith(".mp4") or fn.endswith("_mid.png"):
        sftp.put(os.path.join(V2, "clips", fn), "/opt/lpboard/eaglexv2/clips/" + fn)
print("v2 数据文件、十段视频与封面 uploaded（glb 待独立复验与登记，本阶段不上传）")
sftp.close()

print(run("systemctl restart lpboard.service && sleep 2 && systemctl is-active lpboard.service"))
print(run("curl -sk -o /dev/null -w 'app HTTP %{http_code}\\n' https://127.0.0.1/app/"))
print(run("curl -sk -o /dev/null -w 'eaglex frame HTTP %{http_code}\n' https://127.0.0.1/app/lab/eaglex700/flap/frame_00.webp"))
cli.close()
print("I-202 云端部署完成")
