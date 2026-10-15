# -*- coding: utf-8 -*-
"""I-130 施工辅助：把 VPS /opt/lpboard 服务端代码快照到仓库 cloud_board/server/。"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
import paramiko

SECRET = json.load(open(r"G:\Playground\isaac_flapper_sim\lp_sync\cloud_secret.json", encoding="utf-8"))
ROOT = r"G:\Playground\isaac_flapper_sim"
DEST = os.path.join(ROOT, "cloud_board", "server")
os.makedirs(DEST, exist_ok=True)
KEEP = ("api_agent.py", "api_auth.py", "api_dispatch.py", "api_intake.py",
        "api_ledger.py", "api_map.py", "auth.py", "bootstrap.py", "db.py",
        "httpd.py", "repo.py", "router.py", "rules.py", "server_main.py",
        "settings.py", "webui.py", "lpboard.service")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(SECRET["host"], username=SECRET["ssh_user"], password=SECRET["ssh_password"], timeout=20)
sftp = cli.open_sftp()
for fn in KEEP:
    sftp.get("/opt/lpboard/" + fn, os.path.join(DEST, fn))
    print("snapshot:", fn)
sftp.close()
cli.close()
print("done ->", DEST)
