# -*- coding: utf-8 -*-
"""i234_real_run.py — I-234 真实联调驱动（只读生产来源 + 隔离云真实端点）。
步骤：
  1 真实快照与事件：pending_work / pending_work_events 以生产来源只读构建
    （事件持久化重定向到本项私有目录 PW_EVENTS_DIR，不写生产数据面）；
  2 隔离云：仓库 cloud_board 真实代码、自建库与端口 9465，播种代理令牌；
  3 上行：经真实 POST /api/console/pending_work(/events)/ingest 端点写入；
  4 页面：静态代理（/api/console/pending_work* → 隔离云，其余 /api/* →
    生产云只读 GET，静态为 board_ui/dist），puppeteer 核对与截图；
  5 回收自建进程（隔离云、代理），全程零非 GET 触生产。
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LP = os.path.join(ROOT, "lp_sync")
OUT = os.path.join(ROOT, "logs", "i234_pending_work_ui_20261015")
ISO = os.path.join(OUT, "iso_cloud")
CLOUD_PORT = 9465
PROXY_PORT = 4193
NODE = r"C:\Program Files\nodejs\node.EXE"
PROD = ("101.96.207.208", 443)
os.makedirs(ISO, exist_ok=True)


def log(m):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), m), flush=True)


def build_real_data():
    env = dict(os.environ)
    env.update({"PW_EVENTS_DIR": os.path.join(OUT, "pw_events"),
                "FLAPPER_LEDGER_MODE": "cloud",
                "PYTHONPATH": os.path.join(ROOT, "items_registry")})
    os.makedirs(os.path.join(OUT, "pw_events"), exist_ok=True)
    # 快照固定用登记依赖 v1 的 I-233 交付真实快照（只读原样上行；
    # 工作树中 pending_work.py 已演进 v2 但无对应清单，不混用）。
    v1 = os.path.join(ROOT, "logs", "i233_pending_work_20261014",
                      "real_snapshot_production_readonly.json")
    shutil.copyfile(v1, os.path.join(OUT, "real_snapshot_v1.json"))
    code = (
        "import sys, json\n"
        "sys.path.insert(0, r'%s')\n"
        "import pending_work_events as pe\n"
        "ev = pe.read_events(0)\n"
        "json.dump(ev, open(r'%s', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)\n"
        "print('EV', ev['total_events'], ev['next_cursor'])\n"
        % (LP, os.path.join(OUT, "real_events.json")))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, encoding="utf-8", errors="replace")
    log("真实事件构建：" + (r.stdout or "").strip().replace("\n", " | ")
        + (r.stderr or "")[-200:])
    if r.returncode != 0:
        sys.exit(1)


def boot_cloud():
    import hashlib
    for name in ("ledger.db",):
        p = os.path.join(ISO, name)
        if os.path.exists(p):
            os.remove(p)
    with open(os.path.join(ISO, "secret.json"), "w", encoding="utf-8") as f:
        json.dump({"session_key": __import__("secrets").token_urlsafe(48)}, f)
    subprocess.run(["C:\\Program Files\\Git\\mingw64\\bin\\openssl.EXE", "req",
                    "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", os.path.join(ISO, "key.pem"),
                    "-out", os.path.join(ISO, "cert.pem"), "-days", "30",
                    "-subj", "/CN=lp-iso"], check=True, capture_output=True)
    env = dict(os.environ)
    env.update({"LP_CLOUD_HTTP_HOST": "127.0.0.1", "LP_CLOUD_HTTP_PORT": str(CLOUD_PORT),
                "LP_CLOUD_DB_PATH": os.path.join(ISO, "ledger.db"),
                "LP_CLOUD_SECRET_PATH": os.path.join(ISO, "secret.json"),
                "LP_CLOUD_CERT_FILE": os.path.join(ISO, "cert.pem"),
                "LP_CLOUD_KEY_FILE": os.path.join(ISO, "key.pem")})
    # 播种令牌（沿用生产 agent_token 的哈希入隔离库）
    seed = ("import os, sys, json, hashlib, datetime\n"
            "sys.path.insert(0, r'%s')\n"
            "os.environ.update(%r)\n"
            "import db as cdb\n"
            "cdb.init_schema()\n"
            "tok = open(r'%s', encoding='utf-8').read().strip()\n"
            "with cdb.transaction() as conn:\n"
            "    conn.execute(\"INSERT INTO tokens (name, secret_hash, lanes, revoked, created_at)\"\n"
            "                 \" VALUES (?,?,?,?,?)\", ('i234-ui',\n"
            "                 hashlib.sha256(tok.encode()).hexdigest(), '[\"L1\",\"L2\",\"L3\"]', 0,\n"
            "                 datetime.date.today().isoformat()))\n"
            "print('SEEDED')\n"
            % (os.path.join(ROOT, "cloud_board"),
               {k: env[k] for k in ("LP_CLOUD_DB_PATH", "LP_CLOUD_SECRET_PATH",
                                    "LP_CLOUD_CERT_FILE", "LP_CLOUD_KEY_FILE")},
               os.path.join(LP, "agent_token")))
    r = subprocess.run([sys.executable, "-c", seed], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    log("播种隔离库：" + (r.stdout or "").strip() + (r.stderr or "")[-150:])
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "cloud_board",
                                                          "server_main.py")],
                            env=env, cwd=os.path.join(ROOT, "cloud_board"),
                            stdout=open(os.path.join(OUT, "iso_cloud.log"), "a",
                                        encoding="utf-8"),
                            stderr=subprocess.STDOUT)
    return proc


def api(method, path, body=None):
    # 大包经临时文件传递（Windows 命令行长度限制）
    body_file = None
    if body is not None:
        body_file = os.path.join(OUT, "_api_body.json")
        with open(body_file, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
    script_lines = ["import sys, json",
                    "sys.path.insert(0, r'%s')" % os.path.join(ROOT, "items_registry"),
                    "import cloud_client",
                    "body = json.load(open(r'%s', encoding='utf-8'))" % body_file
                    if body_file else "body = None",
                    "try:",
                    "    r = cloud_client.request(%r, %r, body)" % (method, path),
                    "    print(json.dumps({'ok': True, 'result': r}, ensure_ascii=False, default=str))",
                    "except Exception as e:",
                    "    print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))"]
    env = dict(os.environ)
    env.update({"LP_CLOUD_HOST": "127.0.0.1", "LP_CLOUD_PORT": str(CLOUD_PORT),
                "PYTHONPATH": os.path.join(ROOT, "items_registry")})
    p = subprocess.run([sys.executable, "-c", "\n".join(script_lines)],
                       capture_output=True, text=True, env=env,
                       encoding="utf-8", errors="replace")
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"__error__": (p.stderr or p.stdout or "")[-200:]}
    return data.get("result") if data.get("ok") else {"__error__": data.get("error")}


def main():
    build_real_data()
    cloud = boot_cloud()
    proxy = None
    check = None
    try:
        ready = False
        for _ in range(30):
            r = api("GET", "/api/agent/snapshot")
            if "items" in r:
                ready = True
                break
            time.sleep(1)
        log("隔离云就绪=%s（真实端点代码）" % ready)
        if not ready:
            return 1
        snap = json.load(open(os.path.join(OUT, "real_snapshot_v1.json"), encoding="utf-8"))
        evs = json.load(open(os.path.join(OUT, "real_events.json"), encoding="utf-8"))
        r1 = api("POST", "/api/console/pending_work/ingest", snap)
        r2 = api("POST", "/api/console/pending_work/events/ingest", {
            "contract_version": "pending-work-v1",
            "events": evs["events"][-500:],
            "next_cursor": evs["next_cursor"],
            "total_events": evs["total_events"], "gaps": evs["gaps"]})
        log("上行快照=%s 事件=%s" % (json.dumps(r1, ensure_ascii=False)[:120],
                                    json.dumps(r2, ensure_ascii=False)[:120]))
        # 回读核对（同端点）
        back = api("GET", "/api/console/pending_work")
        same = back.get("snapshot_id") == snap["snapshot_id"]
        log("同端点回读一致=%s total=%s" % (same, back.get("summary", {}).get("total_pending")))
        proxy = subprocess.Popen([NODE, os.path.join(HERE, "i234_real_proxy.cjs"),
                                  str(CLOUD_PORT), str(PROXY_PORT), PROD[0], str(PROD[1])],
                                 cwd=HERE, stdout=open(os.path.join(OUT, "proxy.log"), "a",
                                                       encoding="utf-8"),
                                 stderr=subprocess.STDOUT)
        time.sleep(2)
        check = subprocess.run([NODE, os.path.join(HERE, "i234_real_check.cjs"),
                                str(PROXY_PORT)],
                               capture_output=True, text=True, cwd=HERE,
                               encoding="utf-8", errors="replace", timeout=420)
        log("页面检查输出尾部：\n" + (check.stdout or "")[-1800:])
        if check.stderr:
            log("stderr：" + check.stderr[-500:])
        return check.returncode
    finally:
        for proc, tree in ((check, False), (proxy, True), (cloud, True)):
            if proc and getattr(proc, "pid", None) and (tree or proc.poll() is None):
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True)
        log("自建进程已回收")


if __name__ == "__main__":
    sys.exit(main())
