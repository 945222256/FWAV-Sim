# -*- coding: utf-8 -*-
"""I-130 施工：部署云端服务端改动（参数镜像 extra 列 + 上行通道 + 实验室接口）。

上传前对每个被替换文件在 VPS 留 .bak_i130 备份（沿用 .bak_iXXX 惯例），
随后重启 lpboard 服务并探活。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
import paramiko

SECRET = json.load(open(r"G:\Playground\isaac_flapper_sim\lp_sync\cloud_secret.json", encoding="utf-8"))
SRC = r"G:\Playground\isaac_flapper_sim\cloud_board\server"
FILES = ("db.py", "repo.py", "api_agent.py", "api_lab.py", "webui.py", "server_main.py")

cli = paramiko.SSHClient()
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


for fn in FILES:
    run("test -f /opt/lpboard/%s && cp /opt/lpboard/%s /opt/lpboard/%s.bak_i130 || true" % (fn, fn, fn))
    print("backup:", fn)
sftp = cli.open_sftp()
for fn in FILES:
    sftp.put(SRC + "\\" + fn, "/opt/lpboard/" + fn)
    print("upload:", fn)
sftp.close()
print(run("systemctl restart lpboard.service && sleep 2 && systemctl is-active lpboard.service"))
print(run("curl -sk -o /dev/null -w 'HTTPS 探活 HTTP %{http_code}\\n' https://127.0.0.1/"))
print(run("curl -sk -o /dev/null -w 'lab 静态路由 HTTP %{http_code}\\n' https://127.0.0.1/app/lab/"))
cli.close()
print("部署完成")
