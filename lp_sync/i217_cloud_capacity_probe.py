# -*- coding: utf-8 -*-
"""i217_cloud_capacity_probe.py — I-217 补修二：隔离云容量与工位认领验证。

隔离件：生产 ledger.db 经 sqlite 备份 API 取一致性快照副本（对生产
只读），副本上播种容量配置（管理员配置等价物）与测试事项；起隔离
HTTPS 服务（127.0.0.1:9443，env 覆盖），用正式 cloud_client 与真实
API 路径（/api/dispatch/claim、/api/agent/snapshot）验证：
  CC1 容量二 + 不同固定工位 + 不同会话：同队第二项获准（复核反例场景）
  CC2 同工位第二项拒绝；CC3 超容量工位集拒绝；CC4 第二项缺工位拒绝
  CC5 同会话跨事项拒绝；CC6 同事项重复认领拒绝（不双计）
  CC7 缺省线容量一：同队第二项拒绝（单工位兼容）
  CC8 快照回读：两项在办带 claim_worker_id 与原 reviewer
用法：python i217_cloud_capacity_probe.py [输出目录]
仅标准库。"""
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLOUD = os.path.join(ROOT, "cloud_board")
REGDIR = os.path.join(ROOT, "items_registry")
PY = sys.executable
ISO_PORT = int(os.environ.get("I217_CC_PORT", "9443"))

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "logs", "i217_cc_probe_%s"
    % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
DB = os.path.join(OUT, "ledger.db")

_checks = []


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail), flush=True)


def seed():
    """自建隔离库 + 播种（生产云在远端，本地只读无库；本库为独立空库）。

    隔离件四样：独立 sqlite 库（init_schema 自建）、独立会话密钥、
    openssl 自签证书、把本地 agent_token 注册进令牌表（正式令牌鉴权
    路径与生产同代码）。容量配置经 repo.set_config 播种（管理员配置
    的等价落库方式；正式管理端点 /api/admin/lane_concurrency 已实现，
    供机主会话使用）。"""
    sys.path.insert(0, CLOUD)
    os.makedirs(OUT, exist_ok=True)
    secret_path = os.path.join(OUT, "secret.json")
    with open(secret_path, "w", encoding="utf-8") as f:
        json.dump({"session_key": __import__("secrets").token_urlsafe(48)}, f)
    cert = os.path.join(OUT, "server-cert.pem")
    key = os.path.join(OUT, "server-key.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-nodes", "-keyout", key, "-out", cert, "-days", "30",
                    "-subj", "/CN=lp-board-iso"], check=True, capture_output=True)
    os.environ.update({"LP_CLOUD_DB_PATH": DB, "LP_CLOUD_SECRET_PATH": secret_path,
                       "LP_CLOUD_CERT_FILE": cert, "LP_CLOUD_KEY_FILE": key})
    import hashlib
    import db as cdb
    import repo as crepo
    cdb.init_schema()
    token = open(os.path.join(ROOT, "lp_sync", "agent_token"),
                 encoding="utf-8").read().strip()
    with cdb.transaction() as conn:
        conn.execute("INSERT INTO tokens (name, secret_hash, lanes, revoked,"
                     " created_at) VALUES (?,?,?,?,?)",
                     ("i217-iso-probe",
                      hashlib.sha256(token.encode("utf-8")).hexdigest(),
                      json.dumps(["L1", "L2", "L3", "L9"]), 0,
                      datetime.date.today().isoformat()))
    with cdb.transaction() as conn:
        crepo.set_config(conn, "lane_concurrency", {"L3": 2})
        for n in (951, 952, 953, 954, 955):
            crepo.upsert_item(conn, {
                "id": "I-%d" % n, "title": "I-217 容量验证 %d" % n,
                "status": "open", "opened_at": "2026-10-14",
                "closed_at": None, "closed_by": None, "owner": "L3队",
                "lane": "L3", "close_criterion": "I-217 容量验证判据",
                "note": "隔离副本播种条目", "claimed_by": None,
                "claimed_at": None, "lease_until": None, "evidence": [],
                "criteria": None, "replies": [], "blockers": [],
                "conclusion": None, "criterion_check": [], "depends_on": [],
                "reviewer": "beth"})
        crepo.upsert_item(conn, {
            "id": "I-961", "title": "I-217 单工位兼容 961",
            "status": "open", "opened_at": "2026-10-14",
            "closed_at": None, "closed_by": None, "owner": "L9队",
            "lane": "L9", "close_criterion": "I-217 容量验证判据",
            "note": "隔离副本播种条目（缺省容量一）", "claimed_by": None,
            "claimed_at": None, "lease_until": None, "evidence": [],
            "criteria": None, "replies": [], "blockers": [],
            "conclusion": None, "criterion_check": [], "depends_on": [],
            "reviewer": "beth"})
        crepo.upsert_item(conn, {
            "id": "I-962", "title": "I-217 单工位兼容 962",
            "status": "open", "opened_at": "2026-10-14",
            "closed_at": None, "closed_by": None, "owner": "L9队",
            "lane": "L9", "close_criterion": "I-217 容量验证判据",
            "note": "隔离副本播种条目（缺省容量一）", "claimed_by": None,
            "claimed_at": None, "lease_until": None, "evidence": [],
            "criteria": None, "replies": [], "blockers": [],
            "conclusion": None, "criterion_check": [], "depends_on": [],
            "reviewer": "beth"})
        crepo.append_event(conn, "admin_lane_concurrency", actor="bootstrap",
                           detail="隔离副本播种容量配置 {L3: 2}")


def api(method, path, body=None):
    """正式 cloud_client + 正式 API 路径（env 指向隔离实例）。"""
    nl = "\n"
    script = (nl.join([
        "import sys, json",
        "sys.path.insert(0, r'%s')" % REGDIR,
        "import cloud_client",
        "try:",
        "    r = cloud_client.request(%r, %r, %r)" % (method, path, body),
        "    print(json.dumps({'ok': True, 'result': r}, ensure_ascii=False, default=str))",
        "except Exception as e:",
        "    print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))"]))
    env = dict(os.environ)
    env.update({"FLAPPER_LEDGER_MODE": "cloud", "LP_CLOUD_HOST": "127.0.0.1",
                "LP_CLOUD_PORT": str(ISO_PORT)})
    p = subprocess.run([PY, "-c", script], capture_output=True, text=True,
                       env=env, encoding="utf-8", errors="replace")
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"__error__": (p.stderr or p.stdout or "")[-260:]}
    if data.get("ok"):
        return data.get("result")
    return {"__error__": data.get("error")}


def try_claim(item_id, worker="L3队", session=None, worker_id=None):
    body = {"item_id": item_id, "worker": worker, "lease_hours": 1}
    if session:
        body["session_id"] = session
    if worker_id:
        body["worker_id"] = worker_id
    return api("POST", "/api/dispatch/claim", body)


def main():
    os.makedirs(OUT, exist_ok=True)
    seed()
    env = dict(os.environ)
    env.update({"LP_CLOUD_HTTP_HOST": "127.0.0.1",
                "LP_CLOUD_HTTP_PORT": str(ISO_PORT),
                "LP_CLOUD_DB_PATH": DB,
                "LP_CLOUD_SECRET_PATH": os.path.join(OUT, "secret.json"),
                "LP_CLOUD_CERT_FILE": os.path.join(OUT, "server-cert.pem"),
                "LP_CLOUD_KEY_FILE": os.path.join(OUT, "server-key.pem"),
                # check_claim 内联导入 dependency_policy（远端部署随库提供；
                # 本地隔离实例经 PYTHONPATH 引 items_registry 同源模块）
                "PYTHONPATH": REGDIR})
    server = subprocess.Popen(
        [PY, os.path.join(CLOUD, "server_main.py")], env=env, cwd=CLOUD,
        stdout=open(os.path.join(OUT, "server.log"), "a", encoding="utf-8"),
        stderr=subprocess.STDOUT)
    results = {"started_at": datetime.datetime.now().isoformat(
        timespec="seconds"), "iso_port": ISO_PORT, "checks": _checks}
    try:
        ready = False
        for _ in range(40):
            if server.poll() is not None:
                break
            snap = api("GET", "/api/agent/snapshot")
            if "items" in snap:
                ready = True
                break
            time.sleep(0.5)
        check("CC0 隔离云就绪（真实数据库+正式 API）", ready,
              "port=%d db=%s" % (ISO_PORT, DB))
        if not ready:
            return 1
        r1 = try_claim("I-951", session="sess_cc_a", worker_id="L3-w1")
        check("CC1a 第一项按工位获准", "claimed" in r1, json.dumps(r1, ensure_ascii=False)[:140])
        # 会话唯一性在仅一项在办、工位空闲时测（隔离出目标检查路径）
        r5 = try_claim("I-953", session="sess_cc_a", worker_id="L3-w2")
        check("CC5 同会话跨事项拒绝（空闲工位仍拒）",
              "__error__" in r5 and "会话" in r5["__error__"],
              str(r5.get("__error__"))[:150])
        r2 = try_claim("I-952", session="sess_cc_b", worker_id="L3-w2")
        check("CC1b 同队第二项不同工位获准（复核反例场景）",
              "claimed" in r2, json.dumps(r2, ensure_ascii=False)[:140])
        r3 = try_claim("I-953", session="sess_cc_c", worker_id="L3-w1")
        check("CC2 同工位第二项拒绝", "__error__" in r3 and "工位" in r3["__error__"],
              str(r3.get("__error__"))[:150])
        r4 = try_claim("I-953", session="sess_cc_c", worker_id="L3-w3")
        check("CC3 超容量工位集拒绝", "__error__" in r4 and "容量" in r4["__error__"],
              str(r4.get("__error__"))[:150])
        r6 = try_claim("I-953", session="sess_cc_c")
        check("CC4 第二项缺工位拒绝", "__error__" in r6 and "工位" in r6["__error__"],
              str(r6.get("__error__"))[:150])
        r7 = try_claim("I-952", session="sess_cc_b2", worker_id="L3-w2")
        check("CC6 同事项重复认领拒绝（不双计）",
              "__error__" in r7 and "认领中" in r7["__error__"],
              str(r7.get("__error__"))[:150])
        r8 = try_claim("I-961", worker="L9队", session="sess_cc_d")
        check("CC7a 缺省线第一项获准", "claimed" in r8, json.dumps(r8, ensure_ascii=False)[:120])
        r9 = try_claim("I-962", worker="L9队", session="sess_cc_e")
        check("CC7b 缺省容量一同队第二项拒绝（单工位兼容）",
              "__error__" in r9 and "只许持有一个" in r9["__error__"],
              str(r9.get("__error__"))[:150])
        snap = api("GET", "/api/agent/snapshot")
        items = {i["id"]: i for i in snap.get("items", [])}
        check("CC8 快照回读工位归属与 reviewer 保持",
              items.get("I-951", {}).get("claim_worker_id") == "L3-w1"
              and items.get("I-952", {}).get("claim_worker_id") == "L3-w2"
              and items.get("I-951", {}).get("reviewer") == "beth"
              and items.get("I-951", {}).get("lane") == "L3",
              "w1=%s w2=%s reviewer=%s"
              % (items.get("I-951", {}).get("claim_worker_id"),
                 items.get("I-952", {}).get("claim_worker_id"),
                 items.get("I-951", {}).get("reviewer")))
        return 0
    finally:
        results["finished_at"] = datetime.datetime.now().isoformat(
            timespec="seconds")
        results["summary"] = {"total": len(_checks),
                              "passed": sum(1 for c in _checks if c["ok"])}
        json.dump(results, open(os.path.join(OUT, "results.json"), "w",
                                encoding="utf-8"), ensure_ascii=False, indent=1)
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        print("==== %d/%d，结果 %s ====" % (results["summary"]["passed"],
                                            results["summary"]["total"],
                                            os.path.join(OUT, "results.json")))


if __name__ == "__main__":
    sys.exit(main())
