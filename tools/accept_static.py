# -*- coding: utf-8 -*-
"""accept_static.py — I-075 判据三：静态验收六项断言（机器可执行）。

用法：python tools/accept_static.py <apk路径>
六项断言（任一不过整体退出码非零，产物禁止上传）：
  1 启动入口     aapt badging 含 launchable-activity；
  2 图标         应用图标存在且为自适应多密度（mipmap anydpi-v26 + 多密度位图）；
  3 包名         package == com.lp.board；
  4 网络权限     uses-permission 含 android.permission.INTERNET；
  5 深度链接     deeplink_contract.json 五路由齐全，且清单含
                 flapperboard scheme 与 https 域名 /app/ 前缀两组 VIEW 过滤；
  6 签名         apksigner verify 通过且输出证书摘要。
输出：逐项 过/不过 + JSON 报告 logs/accept_static_report.json。
仅使用 Python 标准库 + SDK build-tools 命令行工具。
"""
import json
import os
import re
import subprocess
import sys
import zipfile
import datetime

HOME = os.path.expanduser("~")
SDK = os.path.join(HOME, "lp-android", "android-sdk")
BT = os.path.join(SDK, "build-tools", "34.0.0")
AAPT = os.path.join(BT, "aapt.exe")
APKSIGNER = os.path.join(BT, "apksigner.bat")
CONTRACT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "android_shell", "deeplink_contract.json")
REPORT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "logs", "accept_static_report.json")


def run(cmd):
    env = dict(os.environ)
    if "JAVA_HOME" not in env or not env["JAVA_HOME"]:
        env["JAVA_HOME"] = os.path.join(HOME, "lp-android", "jdk17")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace", env=env)
    return r.returncode, r.stdout, r.stderr


def main():
    apk = sys.argv[1] if len(sys.argv) > 1 else ""
    if not apk or not os.path.isfile(apk):
        raise SystemExit("用法：python tools/accept_static.py <apk路径>")
    checks = []

    rc, badging, err = run([AAPT, "dump", "badging", apk])

    # 断言一：启动入口。
    checks.append({"name": "启动入口",
                   "ok": "launchable-activity" in badging,
                   "evidence": next((l for l in badging.splitlines()
                                     if l.startswith("launchable-activity")), "缺")})

    # 断言二：图标（自适应 anydpi-v26 声明 + 至少四个密度位图）。
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
    anydpi = [n for n in names if "mipmap-anydpi-v26" in n and "ic_launcher" in n]
    densities = sorted({re.match(r"res/mipmap-([a-zA-Z0-9]+)-v4?/", n).group(1)
                        for n in names
                        if re.match(r"res/mipmap-[a-zA-Z0-9]+-v4?/", n) and "ic_launcher" in n})
    icon_ok = len(anydpi) >= 1 and len(densities) >= 4
    checks.append({"name": "图标自适应多密度",
                   "ok": icon_ok,
                   "evidence": "anydpi=%d 密度=%s" % (len(anydpi), densities or "无")})

    # 断言三：包名。
    pkg = next((l for l in badging.splitlines() if l.startswith("package:")), "")
    pkg_ok = "name='com.lp.board'" in pkg
    checks.append({"name": "包名", "ok": pkg_ok, "evidence": pkg.splitlines()[0][:80] if pkg else "缺"})

    # 断言四：网络权限（badging 格式：uses-permission: name='…'）。
    net_ok = re.search(r"uses-permission:.*android\.permission\.INTERNET", badging) is not None
    checks.append({"name": "网络权限", "ok": net_ok,
                   "evidence": "INTERNET" if net_ok else "清单未声明 INTERNET"})

    # 断言五：深度链接（契约五路由 + 清单两组 VIEW 过滤）。
    contract = json.load(open(CONTRACT, encoding="utf-8"))
    routes = contract.get("routes", [])
    contract_ok = len(routes) == 5
    _, tree, _ = run([AAPT, "dump", "xmltree", apk, "AndroidManifest.xml"])
    has_scheme = "android.intent.action.VIEW" in tree and "flapperboard" in tree
    has_https = "android.intent.action.VIEW" in tree \
        and "personal.karmicsword.com" in tree and "/app/" in tree
    deeplink_ok = contract_ok and has_scheme and has_https
    checks.append({"name": "深度链接五路由",
                   "ok": deeplink_ok,
                   "evidence": "契约路由=%d scheme过滤=%s https过滤=%s"
                               % (len(routes), has_scheme, has_https)})

    # 断言六：签名。
    rc6, out6, err6 = run([APKSIGNER, "verify", "--print-certs", apk])
    sig_ok = rc6 == 0 and ("SHA-256" in out6 or "SHA-256" in err6)
    digest = next((l.strip() for l in (out6 + err6).splitlines() if "SHA-256" in l), "缺")
    checks.append({"name": "签名", "ok": sig_ok, "evidence": digest[:90]})

    all_ok = all(c["ok"] for c in checks)
    report = {"at": datetime.datetime.now().isoformat(timespec="seconds"),
              "apk": apk, "all_ok": all_ok,
              "assertions": checks}
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    for c in checks:
        print("%s %s | %s" % ("过" if c["ok"] else "不过", c["name"], c["evidence"]))
    print("静态验收总判定：%s（报告 %s）" % ("全部通过" if all_ok else "存在不过项，禁止上传", REPORT))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
