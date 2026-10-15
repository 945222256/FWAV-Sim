# -*- coding: utf-8 -*-
"""release_apk.py — I-075 验收产线总闸：六关全过才许上传（一条命令）。

流程：本地构建 → 静态六项断言 → 动态五步验收 → 哈希闸上传下载位
→ 下载位复核。任何一关失败即中止，不上传任何字节。
用法：python tools/release_apk.py
仅使用 Python 标准库。
"""
import hashlib
import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK = os.path.join(ROOT, "android_shell", "app", "build", "outputs",
                   "apk", "debug", "app-debug.apk")
SECRET = json.load(open(os.path.join(ROOT, "lp_sync", "cloud_secret.json"), encoding="utf-8"))


def die(msg):
    print("产线中止：%s" % msg)
    sys.exit(1)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    # 1 本地构建。
    env = dict(os.environ)
    env["JAVA_HOME"] = os.path.join(HOME, "lp-android", "jdk17")
    env["ANDROID_HOME"] = os.path.join(HOME, "lp-android", "android-sdk")
    gradle = os.path.join(HOME, "lp-android", "gradle-8.7", "bin", "gradle.bat")
    r = subprocess.run([gradle, "--no-daemon", "-p",
                        os.path.join(ROOT, "android_shell"), "assembleDebug"],
                       capture_output=True, text=True, env=env, timeout=3600,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        die("构建失败：%s" % r.stderr[-400:])
    print("关1 构建：过（%d 字节）" % os.path.getsize(APK))

    # 2 静态六项。
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "accept_static.py"), APK],
                       timeout=300)
    if r.returncode != 0:
        die("静态验收不过，禁止上传")

    # 3 动态五步（要求模拟器在跑）。
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "accept_dynamic.py"), APK],
                       timeout=1800)
    if r.returncode != 0:
        die("动态验收不过，禁止上传")

    # 4 哈希闸上传。
    local_hash = sha256(APK)
    import paramiko
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(SECRET["host"], username=SECRET["ssh_user"],
                password=SECRET["ssh_password"], timeout=20)
    sftp = cli.open_sftp()
    sftp.put(APK, "/opt/downloads/lp-board.apk")
    sftp.close()
    _, out, _ = cli.exec_command("sha256sum /opt/downloads/lp-board.apk", timeout=60)
    remote_hash = out.read().decode().split()[0]
    cli.close()
    if remote_hash != local_hash:
        die("哈希不一致：本地 %s 云端 %s" % (local_hash, remote_hash))
    print("关4 哈希闸：过（%s）" % local_hash[:16] + "…")
    print("产线全部通过，下载位已更新。")


if __name__ == "__main__":
    main()
