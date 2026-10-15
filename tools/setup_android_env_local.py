# -*- coding: utf-8 -*-
"""setup_android_env_local.py — I-074 本机安卓构建环境（全程用户目录，零系统改动）。

安装位：C:\\Users\\Anni\\lp-android\\{jdk17, android-sdk, gradle-8.7, avd}
纪律：不改系统 PATH、不写系统环境变量、不动系统 Node；JDK/SDK/Gradle
全部便携解压，环境变量只在脚本与后续构建子进程内生效。
下载路线：Google 走本机 Clash 代理 127.0.0.1:7890；JDK 走 Adoptium API；
Gradle 走腾讯镜像。幂等：已存在的组件跳过。
仅使用 Python 标准库。
"""
import os
import subprocess
import sys
import zipfile
import time

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "lp-android")
JDK_DIR = os.path.join(BASE, "jdk17")
SDK = os.path.join(BASE, "android-sdk")
GRADLE_DIR = os.path.join(BASE, "gradle-8.7")
PROXY = "http://127.0.0.1:7890"
PROXIES = {"http": PROXY, "https": PROXY}

JAVA_HOME = JDK_DIR
SDKMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "sdkmanager.bat")
GRADLE_BIN = os.path.join(GRADLE_DIR, "bin", "gradle.bat")


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def fetch(url, dest, use_proxy):
    """下载文件。use_proxy 时走本机 Clash。"""
    cmd = ["curl", "-sSL", "-o", dest, "--connect-timeout", "20", url]
    if use_proxy:
        cmd += ["-x", PROXY]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("下载失败 %s：%s" % (url, r.stderr[:300]))


def install_jdk():
    if os.path.exists(os.path.join(JDK_DIR, "bin", "java.exe")):
        log("JDK 已就位，跳过")
        return
    os.makedirs(BASE, exist_ok=True)
    zip_path = os.path.join(BASE, "jdk17.zip")
    log("下载 Temurin JDK17（Adoptium API，走代理）……")
    fetch("https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk",
          zip_path, use_proxy=True)
    log("解压 JDK……")
    tmp = os.path.join(BASE, "_jdk_extract")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)
    inner = os.listdir(tmp)[0]
    os.replace(os.path.join(tmp, inner), JDK_DIR)
    os.rmdir(tmp)
    os.remove(zip_path)
    log("JDK 就位：" + JDK_DIR)


def install_cmdline_tools():
    if os.path.exists(SDKMANAGER):
        log("cmdline-tools 已就位，跳过")
        return
    os.makedirs(os.path.join(SDK, "cmdline-tools"), exist_ok=True)
    zip_path = os.path.join(BASE, "cmdline-tools.zip")
    log("下载 Android cmdline-tools（走代理）……")
    fetch("https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip",
          zip_path, use_proxy=True)
    log("解压并规整目录……")
    tmp = os.path.join(SDK, "_ctl")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)
    os.replace(os.path.join(tmp, "cmdline-tools"), os.path.join(SDK, "cmdline-tools", "latest"))
    os.rmdir(tmp)
    os.remove(zip_path)
    log("cmdline-tools 就位")


def sdk(args, timeout=3600):
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    proxy_env = {"HTTPS_PROXY": PROXY, "HTTP_PROXY": PROXY}
    env.update(proxy_env)
    r = subprocess.run([SDKMANAGER, "--sdk_root=" + SDK] + args,
                       capture_output=True, text=True, env=env, timeout=timeout,
                       encoding="utf-8", errors="replace",
                       input="y\n" * 20)
    if r.returncode != 0:
        raise RuntimeError("sdkmanager 失败 %s：\n%s\n%s" % (args, r.stdout[-800:], r.stderr[-800:]))
    return r.stdout


def install_sdk_components():
    log("接受许可……")
    sdk(["--licenses"], timeout=1200)
    log("安装 platform-tools / platforms;android-35 / build-tools;34.0.0（走代理，约 400MB）……")
    sdk(["--install", "platform-tools", "platforms;android-35", "build-tools;34.0.0"])
    log("安装 emulator 与 system-images;android-35;google_apis;x86_64（约 2GB，耐心）……")
    sdk(["--install", "emulator", "system-images;android-35;google_apis;x86_64"], timeout=7200)
    log("SDK 组件就位")


def install_gradle():
    if os.path.exists(GRADLE_BIN):
        log("Gradle 已就位，跳过")
        return
    zip_path = os.path.join(BASE, "gradle.zip")
    log("下载 Gradle 8.7（腾讯镜像，不走代理）……")
    fetch("https://mirrors.cloud.tencent.com/gradle/gradle-8.7-bin.zip", zip_path, use_proxy=False)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(BASE)
    os.remove(zip_path)
    log("Gradle 就位：" + GRADLE_DIR)


def create_avd():
    avd_dir = os.path.join(HOME, ".android", "avd", "lp_test.avd")
    if os.path.exists(avd_dir):
        log("AVD lp_test 已存在，跳过")
        return
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    tool = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager.bat")
    r = subprocess.run([tool, "create", "avd", "-n", "lp_test",
                        "-k", "system-images;android-35;google_apis;x86_64",
                        "-d", "pixel_6"],
                       capture_output=True, text=True, env=env, input="no\n")
    if r.returncode != 0 and not os.path.exists(avd_dir):
        raise RuntimeError("建 AVD 失败：%s %s" % (r.stdout[-300:], r.stderr[-300:]))
    log("AVD lp_test 已创建")


def main():
    install_jdk()
    install_cmdline_tools()
    install_sdk_components()
    install_gradle()
    create_avd()
    # 冒烟：本地构建产安装包。
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proj = os.path.join(root, "android_shell")
    lp = os.path.join(proj, "local.properties")
    with open(lp, "w", encoding="utf-8") as f:
        f.write("sdk.dir=" + SDK.replace("\\", "/"))
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    env["ANDROID_HOME"] = SDK
    log("本地 gradle assembleDebug（依赖走本机代理）……")
    r = subprocess.run([GRADLE_BIN, "--no-daemon",
                        "-Dhttps.proxyHost=127.0.0.1", "-Dhttps.proxyPort=7890",
                        "-Dhttp.proxyHost=127.0.0.1", "-Dhttp.proxyPort=7890",
                        "-p", proj, "assembleDebug"],
                       capture_output=True, text=True, env=env, timeout=3600,
                       encoding="utf-8", errors="replace")
    print(r.stdout[-1500:])
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise RuntimeError("本地构建失败")
    apk = os.path.join(proj, "app", "build", "outputs", "apk", "debug", "app-debug.apk")
    log("本地安装包产出：%s（%d 字节）" % (apk, os.path.getsize(apk)))
    log("I-074 环境与构建全部完成")


if __name__ == "__main__":
    main()
