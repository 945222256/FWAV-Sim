# -*- coding: utf-8 -*-
"""task_index_formcheck.py — I-131 判据五：任务索引形态自检。

用途：客户端版本变更后运行，比对三道闸：
  闸一，tasks 表列集仍覆盖登记写入器依赖的全部列（升级加列不碍事，
        缺列即拒，与宿主服务写前形态闸同源）；
  闸二，客户端自写行的 meta_json 键集仍覆盖登记写入器的九键形态
        （用登记台账区分我方行与客户端行，取最新一条客户端行比对）；
  闸三，台账在册的我方行全部仍存在、状态仍为 completed、各自唯一。
退出码零为全过，一为有不过。只读，不写任何库。"""
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
HOME = os.path.expanduser("~")
IDX = os.path.join(HOME, ".zcode", "v2", "tasks-index.sqlite")
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i131_registered.json")
COLUMNS = ("workspace_key", "workspace_path", "task_id", "title",
           "task_status", "provider", "mode", "created_at", "updated_at",
           "last_unread_at", "pinned", "archived", "deleted",
           "title_overridden", "searchable_text", "meta_json")
META_KEYS = ("taskId", "traceId", "title", "workspacePath", "createdAt",
             "updatedAt", "mode", "provider", "status")


def main():
    verdict = {"gates": []}

    def gate(name, ok, detail):
        verdict["gates"].append({"gate": name, "pass": bool(ok), "detail": detail})
        print(("过 " if ok else "不过 ") + name + "：" + detail)

    if not os.path.exists(IDX):
        gate("库存在", False, "索引库不存在：" + IDX)
        print(json.dumps(verdict, ensure_ascii=False))
        return 1
    con = sqlite3.connect("file:" + IDX.replace("\\", "/") + "?mode=ro", uri=True)
    have = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
    missing_cols = [c for c in COLUMNS if c not in have]
    gate("闸一 列集覆盖", not missing_cols,
         "缺列 %s" % missing_cols if missing_cols else "登记写入器依赖 %d 列全部在册" % len(COLUMNS))

    mine = set(json.load(open(LEDGER, encoding="utf-8"))) if os.path.exists(LEDGER) else set()
    client_row = con.execute(
        "select task_id, meta_json from tasks where task_id not in (%s) "
        "order by updated_at desc limit 1" % (",".join("?" * len(mine)) or "''"),
        tuple(mine)).fetchone() if mine else con.execute(
        "select task_id, meta_json from tasks order by updated_at desc limit 1").fetchone()
    if client_row is None:
        gate("闸二 客户端行形态", False, "找不到可比对的客户端行")
    else:
        try:
            keys = set(json.loads(client_row[1] or "{}"))
        except ValueError:
            keys = set()
        miss_keys = [k for k in META_KEYS if k not in keys]
        gate("闸二 客户端行形态", not miss_keys,
             ("比对行 %s… 缺键 %s" % (client_row[0][:16], miss_keys)) if miss_keys
             else "客户端行 %s… meta 键集覆盖登记九键形态" % client_row[0][:16])

    bad = []
    for sid in sorted(mine):
        rows = con.execute("select task_status, count(*) from tasks where task_id=?",
                           (sid,)).fetchall()
        n = sum(r[1] for r in rows)
        st = rows[0][0] if rows else None
        if n != 1 or st != "completed":
            bad.append({"sid": sid[:18], "rows": n, "status": st})
    gate("闸三 台账行完好", not bad,
         ("异常 %s" % bad) if bad else "台账 %d 行全部唯一且状态 completed" % len(mine))
    con.close()

    ok = all(g["pass"] for g in verdict["gates"])
    verdict["pass"] = ok
    print("形态自检结论：" + ("过" if ok else "不过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
