# -*- coding: utf-8 -*-
"""build_apk_cloud.py — 云端代建 I-064 安卓壳 APK（在本机工地运行）。

背景：本机无 JDK、无 Android SDK（2026-10-03 实测），云端服务器可直连
dl.google.com。本脚本在云端装工具链、上传 android_shell 工程、构建
debug APK、回传本地 deploy/ 目录并部署到 nginx 下载位。
幂等：可重复运行。依赖 paramiko。仅用标准库。
"""
import json
import os
import posixpath
import stat
import time

import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "android_shell")
SECRET = json.load(open(os.path.join(ROOT, "lp_sync", "cloud_secret.json"), encoding="utf-8"))
REMOTE_WORK = "/opt/apkbuild"
SDK = "/opt/android-sdk"
GRADLE_VER = "8.7"
APK_LOCAL = os.path.join(SRC, "deploy", "lp-board-debug.apk")


def run(cli, cmd, timeout=600, check=True):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    code = out.channel.recv_exit_status()
    text = out.read().decode("utf-8", "replace")
    etext = err.read().decode("utf-8", "replace")
    if check and code != 0:
        raise RuntimeError("远端失败(%d): %s\n%s\n%s" % (code, cmd[:120], text[-1500:], etext[-1500:]))
    return text


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def main():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(SECRET["host"], username=SECRET["ssh_user"],
                password=SECRET["ssh_password"], timeout=20)
    try:
        log("1/7 系统依赖：JDK 21 与 unzip")
        run(cli, "apt-get update -qq && DEBIAN_FRONTEND=noninteractive "
                 "apt-get install -y -qq openjdk-21-jdk-headless unzip", timeout=900)
        run(cli, "java -version 2>&1 | head -1")

        log("2/7 Android cmdline-tools 与 SDK 组件（直连 dl.google.com）")
        run(cli, "mkdir -p %s/cmdline-tools" % SDK)
        run(cli, "test -f %s/cmdline-tools/latest/bin/sdkmanager || "
                 "(cd /tmp && curl -sSLo ctl.zip "
                 "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip "
                 "&& unzip -qo ctl.zip -d %s/cmdline-tools && "
                 "mv %s/cmdline-tools/cmdline-tools %s/cmdline-tools/latest)"
                 % (SDK, SDK, SDK, SDK), timeout=900)
        run(cli, "yes | %s/cmdline-tools/latest/bin/sdkmanager --sdk_root=%s --licenses >/dev/null"
                 % (SDK, SDK), timeout=900)
        run(cli, "%s/cmdline-tools/latest/bin/sdkmanager --sdk_root=%s "
                 "\"platforms;android-35\" \"build-tools;34.0.0\" \"platform-tools\" >/dev/null"
                 % (SDK, SDK), timeout=1800)
        run(cli, "ls %s/platforms %s/build-tools" % (SDK, SDK))

        log("3/7 Gradle %s（腾讯镜像）" % GRADLE_VER)
        run(cli, "test -d /opt/gradle-%s || (curl -sSLo /tmp/gradle.zip "
                 "https://mirrors.cloud.tencent.com/gradle/gradle-%s-bin.zip && "
                 "unzip -qo /tmp/gradle.zip -d /opt)"
                 % (GRADLE_VER, GRADLE_VER), timeout=900)

        log("4/7 上传 android_shell 工程")
        run(cli, "rm -rf %s && mkdir -p %s" % (REMOTE_WORK, REMOTE_WORK))
        import subprocess
        local_tgz = os.path.join(ROOT, "lp_sync", "_android_shell.tgz")
        if os.path.exists(local_tgz):
            os.remove(local_tgz)
        subprocess.run(["tar", "-czf", local_tgz,
                        "-C", ROOT, "--exclude=__pycache__", "android_shell"], check=True)
        sftp = cli.open_sftp()
        sftp.put(local_tgz, "/tmp/android_shell.tgz")
        run(cli, "cd %s && tar xzf /tmp/android_shell.tgz" % REMOTE_WORK)
        os.remove(local_tgz)

        log("5/7 gradle assembleDebug（首次拉依赖，耐心等）")
        run(cli, "echo 'sdk.dir=%s' > %s/android_shell/local.properties" % (SDK, REMOTE_WORK))
        build = ("cd %s/android_shell && JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 "
                 "ANDROID_HOME=%s "
                 "/opt/gradle-%s/bin/gradle --no-daemon "
                 "-Dorg.gradle.jvmargs=-Xmx1536m assembleDebug"
                 % (REMOTE_WORK, SDK, GRADLE_VER))
        out = run(cli, build, timeout=3000)
        log(out.strip().splitlines()[-1] if out.strip() else "build done")
        run(cli, "ls -la %s/android_shell/app/build/outputs/apk/debug/" % REMOTE_WORK)

        log("5.5/7 可启动活动验证（aapt badging，缺 LAUNCHER 即失败）")
        badging = run(cli, "%s/build-tools/34.0.0/aapt dump badging "
                          "%s/android_shell/app/build/outputs/apk/debug/app-debug.apk "
                          "| grep -E 'launchable-activity|^package:'" % (SDK, REMOTE_WORK))
        log("badging: " + badging.strip())
        if "launchable-activity" not in badging:
            raise RuntimeError("验证失败：APK 无 launchable-activity，桌面将无图标（2026-10-05 教训）")
        tree = run(cli, "%s/build-tools/34.0.0/aapt dump xmltree "
                        "%s/android_shell/app/build/outputs/apk/debug/app-debug.apk "
                        "AndroidManifest.xml | grep -B2 -A8 'android.intent.category.LAUNCHER'"
                        % (SDK, REMOTE_WORK))
        log("清单树 LAUNCHER 段: " + " | ".join(tree.strip().splitlines()[:6]))

        log("6/7 回传 APK 到本地 deploy/")
        sftp.get("%s/android_shell/app/build/outputs/apk/debug/app-debug.apk" % REMOTE_WORK,
                 APK_LOCAL)
        log("本地产物：%s（%d 字节）" % (APK_LOCAL, os.path.getsize(APK_LOCAL)))

        log("7/7 部署到 nginx 下载位（固定名+版本化名双路）")
        run(cli, "mkdir -p /opt/downloads")
        sftp.put(APK_LOCAL, "/opt/downloads/lp-board.apk")
        import re as _re
        vc = _re.search(r"versionCode='(\d+)'", badging)
        vname = "lp-board-v%s.apk" % (vc.group(1) if vc else "?")
        run(cli, "cp /opt/downloads/lp-board.apk /opt/downloads/%s && ls -la /opt/downloads/"
            % vname)
        sftp.close()
        log("版本化下载名：%s" % vname)
        run(cli, "grep -q '/downloads/' /etc/nginx/sites-enabled/personal.conf || "
                 "sed -i '\\#location / {#i\\    location /downloads/ { alias /opt/downloads/; }\\n'"
                 " /etc/nginx/sites-enabled/personal.conf")
        run(cli, "nginx -t && systemctl reload nginx")
        run(cli, "curl -sk -o /dev/null -w 'APK下载位 HTTP %%{http_code} %%{size_download}B\\n' "
                 "https://127.0.0.1/downloads/lp-board.apk")
        log("全部完成。手机下载地址 https://%s/downloads/lp-board.apk" % SECRET["host"])
    finally:
        cli.close()


if __name__ == "__main__":
    main()
