# -*- coding: utf-8 -*-
"""i188_isolated_test.py — I-188 隔离样例检查（判据二与判据四样例）。

隔离方式：把 cloud_board 代码复制到临时目录，指向临时数据库，直接
以伪造请求上下文调用路由函数（不经网络、不碰生产云端与生产数据）。
auth.caller 打桩为隔离身份。检查内容：
  1. 登记含 kind 与 goal 后，追加证据、改逻辑线、设依赖与责任、认领、
     释放各写操作后 kind 与 goal 均与初始值一致（判据二）；
  2. 窄范围写入口 set_goal_kind 只改两列、其余字段不变（判据三前置）；
  3. 无效类别（job）与未知目标（G-999，目标库含 G-001/G-002 时）被拒，
     合法值通过（判据四样例）；
  4. 字段丢失环节复现：旧版（无 goal/kind 支持的 server 快照）登记
     丢弃两字段，新版保留（判据一对照证据）。
仅使用 Python 标准库。
"""
import json
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "cloud_board")
WORK = tempfile.mkdtemp(prefix="i188_isolated_")
CODE = os.path.join(WORK, "code")
shutil.copytree(SRC, CODE,
                ignore=shutil.ignore_patterns("__pycache__", "data", "server",
                                               "deploy", "static", "app"))
os.makedirs(os.path.join(CODE, "data"), exist_ok=True)
sys.path.insert(0, CODE)

_checks = []


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail), flush=True)


def ctx(body):
    return types.SimpleNamespace(body=body, headers={})


def main():
    import db
    db.settings.DATA_DIR = os.path.join(CODE, "data")
    db.settings.DB_PATH = os.path.join(CODE, "data", "ledger.db")
    db.init_schema()

    import auth
    auth.caller = lambda c: ("隔离测试", ["L3"])
    import repo
    import api_ledger          # noqa: F401 路由注册
    import api_dispatch        # noqa: F401
    from httpd import ApiError

    # 目标库登记 G-001 与 G-002，供未知目标样例判定。
    with db.transaction() as conn:
        repo.set_config(conn, "goals", [{"id": "G-001"}, {"id": "G-002"}])

    # 判据二主链：登记 → 五种后续写 → 字段保持。
    add = api_ledger.add(ctx({"title": "隔离样例甲", "owner": "L3队",
                              "close_criterion": "隔离判据",
                              "kind": "task", "goal": "G-002",
                              "note": "隔离样例", "criteria": ["样例判据一"]}))
    iid = add["result"]["id"]
    with db.transaction() as conn:
        goals_cfg = {"G-001": {"id": "G-001"}, "G-002": {"id": "G-002"}}
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('goals', ?)",
                     (json.dumps(list(goals_cfg.values()), ensure_ascii=False),))

    def fields():
        return repo.get_item(iid)

    api_ledger.attach(ctx({"item_id": iid, "evidence": ["隔离证据一"]}))
    api_ledger.set_line(ctx({"item_id": iid, "line": "L3",
                             "author": "隔离测试"}))
    api_ledger.set_depends(ctx({"item_id": iid, "depends_on": [],
                                "author": "隔离测试"}))
    api_ledger.set_rationale(ctx({"item_id": iid, "rationale": "隔离来由",
                                  "author": "隔离测试"}))
    api_dispatch.claim(ctx({"item_id": iid, "worker": "L3队", "lease_hours": 1}))
    api_dispatch.release(ctx({"item_id": iid, "worker": "L3队"}))
    it = fields()
    check("登记与五种后续写后 kind 保持", it.get("kind") == "task", repr(it.get("kind")))
    check("登记与五种后续写后 goal 保持", it.get("goal") == "G-002", repr(it.get("goal")))
    check("后续字段写入未受影响", it.get("line") == "L3"
          and it.get("rationale") == "隔离来由"
          and it.get("evidence") == ["隔离证据一"]
          and it.get("status") == "open")

    # 判据三前置：窄范围写入口只改两列。
    before = dict(fields())
    r = api_ledger.set_goal_kind(ctx({"item_id": iid, "kind": "requirement",
                                      "goal": "G-001"}))
    after = fields()
    unchanged = all(after[k] == before[k] for k in before
                    if k not in ("kind", "goal"))
    check("窄范围写入口只改 kind 与 goal",
          r["result"]["kind"] == "requirement" and after["kind"] == "requirement"
          and after["goal"] == "G-001" and unchanged)

    # 判据四样例：无效类别与未知目标被拒，合法值通过。
    try:
        api_ledger.set_goal_kind(ctx({"item_id": iid, "kind": "job",
                                      "goal": "G-001"}))
        check("无效类别 job 被拒", False, "未抛出")
    except ApiError as e:
        check("无效类别 job 被拒", "kind" in str(e), str(e)[:60])
    try:
        api_ledger.set_goal_kind(ctx({"item_id": iid, "kind": "task",
                                      "goal": "G-999"}))
        check("未知目标 G-999 被拒", False, "未抛出")
    except ApiError as e:
        check("未知目标 G-999 被拒", "目标不在目标库" in str(e), str(e)[:60])
    r2 = api_ledger.set_goal_kind(ctx({"item_id": iid, "kind": "task",
                                       "goal": "G-002"}))
    check("合法类别与已知目标通过",
          r2["result"]["kind"] == "task" and fields()["goal"] == "G-002")
    # 新版登记链路本身也校验（判据四：正常样例）。
    add2 = api_ledger.add(ctx({"title": "隔离样例乙", "owner": "L3队",
                               "close_criterion": "隔离判据", "kind": "task",
                               "goal": "G-001"}))
    it2 = repo.get_item(add2["result"]["id"])
    check("新版登记通道 kind 与 goal 入库",
          it2.get("kind") == "task" and it2.get("goal") == "G-001")

    # 判据一对照证据：server 快照（旧部署形态）登记丢弃两字段。
    SNAP = os.path.join(SRC, "server")
    if os.path.isdir(SNAP):
        snap_work = os.path.join(WORK, "snap_code")
        shutil.copytree(SNAP, snap_work,
                        ignore=shutil.ignore_patterns("__pycache__", "data"))
        os.makedirs(os.path.join(snap_work, "data"), exist_ok=True)
        for mod in [m for m in list(sys.modules) if m in
                    ("db", "repo", "api_ledger", "api_dispatch", "auth",
                     "settings", "httpd", "router", "rules", "webui",
                     "api_agent", "api_map", "api_intake", "api_auth",
                     "api_lab", "bootstrap")]:
            del sys.modules[mod]
        sys.path.remove(CODE)
        sys.path.insert(0, snap_work)
        import db as sdb
        sdb.settings.DATA_DIR = os.path.join(snap_work, "data")
        sdb.settings.DB_PATH = os.path.join(snap_work, "data", "ledger.db")
        sdb.init_schema()
        import auth as sauth
        sauth.caller = lambda c: ("隔离测试", ["L3"])
        import api_ledger as s_api     # noqa: F401 快照路由注册
        s_add = s_api.add(ctx({"title": "快照对照样例", "owner": "L3队",
                               "close_criterion": "隔离判据", "kind": "task",
                               "goal": "G-002"}))
        import repo as srepo
        s_item = srepo.get_item(s_add["result"]["id"])
        check("旧部署快照登记丢弃两字段（丢失环节复现）",
              s_item is not None and not s_item.get("kind")
              and not s_item.get("goal"),
              "kind=%r goal=%r" % (s_item.get("kind"), s_item.get("goal")))
        sys.path.remove(snap_work)

    bad = sum(1 for c in _checks if not c["ok"])
    report = {"checks": _checks,
              "summary": {"total": len(_checks),
                          "passed": len(_checks) - bad},
              "isolated_root": WORK}
    out = os.path.join(ROOT, "logs", "i188_isolated_test_20261012.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("隔离样例总判定：%s（%d/%d 过）"
          % ("全部通过" if bad == 0 else "存在不过项", len(_checks) - bad,
             len(_checks)))
    print("报告：%s" % os.path.relpath(out, ROOT))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
