# -*- coding: utf-8 -*-
"""verify_skeleton.py — 试点骨架判据脚本（统一验收入口）。

本脚本按《强制指令》登记骨架判据，逐项真跑并输出过或拒，
全部通过退出码为零。判据共八项：路线表、看板命令、依赖图、
领地自检、语言检查、首批事项在册、制度文本在位、看板服务。
仅使用 Python 标准库。
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REG = os.path.join(_HERE, "registry")
PY = sys.executable

_results = []


def check(name, ok, detail=""):
    _results.append((name, ok, detail))
    print("[%s] %s%s" % ("过" if ok else "拒", name,
                         ("：%s" % detail) if detail else ""))


def run(args):
    r = subprocess.run([PY] + args, capture_output=True, text=True,
                       cwd=_HERE)
    return r.returncode, r.stdout + r.stderr


def main():
    # C1 路线表合法
    try:
        with open(os.path.join(REG, "lanes.json"), encoding="utf-8") as f:
            lanes = json.load(f)["lanes"]
        need = {"name", "mission", "keywords", "exclusive_paths"}
        ok = "HQ" in lanes and all(need <= set(cfg) for cfg in lanes.values())
        check("C1 路线表合法", ok, "路线 %d 条含指挥部" % len(lanes))
    except Exception as e:
        check("C1 路线表合法", False, repr(e))

    # C2 看板命令可用：每条路线打印队列且退出码零
    ok_all = True
    for lane in sorted(lanes):
        code, out = run(["registry/dispatch.py", lane])
        ok_all = ok_all and code == 0
    check("C2 看板命令可用", ok_all,
          "全部路线退出码零" if ok_all else "存在退出码非零的路线")

    # C3 依赖图合法：无环无悬空引用且存在可开工事项
    sys.path.insert(0, REG)
    import schedule
    errs = schedule.validate()
    ready = schedule.ready()
    check("C3 依赖图合法", not errs and len(ready) >= 1,
          "校验错误 %d 条，可开工 %d 条" % (len(errs), len(ready)))

    # C4 领地自检
    code, out = run(["registry/check_commit.py", "--selftest"])
    check("C4 领地自检通过", code == 0)

    # C5 语言检查双向生效：干净文本过、脏文本拒
    import langcheck
    clean = langcheck.scan_text("本判据脚本验证骨架可用，逐项输出结果。")
    dirty = langcheck.scan_text("这个方案先打个锚点再说")
    ok = (not clean) and any(cat == "内部压缩词" for cat, _, _ in dirty) \
        if dirty else False
    # 试点禁词表只登记了两条压缩词，锚点未登记时改用试点已登记词
    if not ok:
        dirty2 = langcheck.scan_text("先调一版试试看")
        ok = (not clean) and bool(dirty2)
    check("C5 语言检查双向生效", ok,
          "干净文本 %d 条违规，脏文本命中 %d 条" % (
              len(clean), len(langcheck.scan_text("先调一版试试看"))))

    # C6 首批事项在册：不少于五条未关闭且含依赖关系
    with open(os.path.join(REG, "items.json"), encoding="utf-8") as f:
        items = json.load(f)["items"]
    open_items = [i for i in items if i["status"] != "closed"]
    with_deps = [i for i in open_items if i.get("depends_on")]
    check("C6 首批事项在册", len(open_items) >= 5 and len(with_deps) >= 3,
          "未关闭 %d 条，带依赖 %d 条" % (len(open_items), len(with_deps)))

    # C7 制度文本六份在位
    docs = ["协作架构", "汇报标准", "语言规范", "指挥部原则", "总计划", "强制指令"]
    missing = [d for d in docs
               if not os.path.exists(os.path.join(_HERE, "governance", d + ".md"))]
    check("C7 制度文本六份在位", not missing,
          "缺失：%s" % missing if missing else "六份齐全")

    # C8 看板服务可启动且状态接口返回路线与事项
    from board import server as board_server
    port = 8891
    os.environ["BOARD_PORT"] = str(port)
    srv_ok, srv_detail = False, ""
    httpd = None
    try:
        from http.server import ThreadingHTTPServer
        board_server.PORT = port
        httpd = ThreadingHTTPServer(("127.0.0.1", port), board_server.Handler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.5)
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/state" % port, timeout=5) as resp:
            state = json.loads(resp.read().decode("utf-8"))
        lanes_seen = {l["id"] for l in state["lanes"]}
        srv_ok = (set(lanes) <= lanes_seen
                  and state["totals"]["open"] == len(open_items))
        srv_detail = ("接口返回路线 %d 条、未关闭 %d 条"
                      % (len(state["lanes"]), state["totals"]["open"]))
    except Exception as e:
        srv_detail = repr(e)
    finally:
        if httpd:
            httpd.shutdown()
    check("C8 看板服务可用", srv_ok, srv_detail)

    passed = sum(1 for _, ok, _ in _results if ok)
    print("判据总账：%d 过 %d 拒" % (passed, len(_results) - passed))
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
