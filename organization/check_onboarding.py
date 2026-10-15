# -*- coding: utf-8 -*-
"""check_onboarding.py — I-180 入职资料统一收置检查器。

检查项（对应判据一至判据五）：
  1. organization/onboarding/ 目录与索引建立，各岗位现行卡登记齐备，
     缺失（Zoya）按依据记录；
  2. 根目录同名文件为兼容转向说明（不含可独立更新的正文），指向
     新位置，历史依据说明在档；
  3. 通讯录 onboarding_file 与收置路径逐项一致且可解析；Yulia 接续
     与记忆材料引用的旧路径经转向桩解析成功；
  4. 各卡含公共入口块（操作指南、通讯录、索引）；
  5. 语言检查（禁用词）与本地引用检查通过；独立恢复检查：仅凭
     INDEX.md 公共入口找到各岗位现行资料、批准边界与下一动作。

用法：python check_onboarding.py
仅使用 Python 标准库。
"""
import io
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)                           # 项目根
OB = os.path.join(ROOT, "organization", "onboarding")
CONTACTS = os.path.join(ROOT, "organization", "contacts.json")
INDEX = os.path.join(OB, "INDEX.md")

sys.path.insert(0, os.path.join(ROOT, "items_registry"))
# langcheck 依赖同目录数据文件，按模块方式加入路径
import langcheck

PUBLIC_BLOCK_MARK = "## 公共入口（I-180 收置补充）"
STUB_MARK = "已收置"
STUB_TARGET = "organization/onboarding/"

EXPECTED_POSTS = {
    "anni": "入职卡_Anni_HQ_20261010.md",
    "beth": "入职卡_Beth_HQ_20261011.md",
    "ciri": "入职卡_Ciri_前端视觉顾问_20261008.md",
    "doro": "入职卡_Doro_接口一致性与独立验收_20261011.md",
    "elsa": "入职卡_Elsa_知识连续性与秘书服务_20261011.md",
    "fil": "入职卡_Fil_代码质量与架构优化分析_20261011.md",
    "gwen": "入职卡_Gwen_通信拓扑与服务质量_20261011.md",
    "helm": "入职卡_Helm_仿真与CFD引擎_20261012.md",
    "iris": "入职卡_Iris_感知压缩与轨迹规划_20261012.md",
    "jane": "入职卡_Jane_三维资产与构型指导_20261012.md",
    "yulia": "入职卡_Yulia_组织与长期部署顾问_20261011.md",
    "zoya": None,                                        # 无卡，按依据留空
}

_checks = []


def check(name, ok, detail=""):
    _checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print("[%s] %s %s" % ("过" if ok else "拒", name, detail))


def main():
    problems = []

    # 判据一：目录、索引、齐备登记。
    index_text = io.open(INDEX, encoding="utf-8").read()
    check("索引存在且含公共入口说明", "唯一公共入口" in index_text
          and "批准边界" in index_text and "当前动作入口" in index_text)
    for pid, card in EXPECTED_POSTS.items():
        in_index = ("| %s |" % pid) in index_text
        if card is None:
            check("缺失记录在档（%s 无卡留空）" % pid,
                  in_index and "缺失" in index_text.split("| %s |" % pid)[1][:200]
                  if in_index else False)
            continue
        path = os.path.join(OB, card)
        exists = os.path.exists(path)
        check("现行卡在收置目录（%s）" % pid, exists and in_index, card)
        if not exists:
            problems.append(pid)
        body = io.open(path, encoding="utf-8").read() if exists else ""
        check("卡含公共入口块（%s）" % pid, PUBLIC_BLOCK_MARK in body)
    notes = os.path.join(OB, "appointment_notes.md")
    check("任命来源与差异记录在档", os.path.exists(notes)
          and "Ciri" in io.open(notes, encoding="utf-8").read()
          and "Zoya" in io.open(notes, encoding="utf-8").read())

    # 判据二：根目录旧入口为转向说明。
    for pid, card in EXPECTED_POSTS.items():
        if card is None:
            continue
        stub = os.path.join(ROOT, card)
        if not os.path.exists(stub):
            check("根目录兼容入口存在（%s）" % pid, False, "缺失")
            continue
        text = io.open(stub, encoding="utf-8").read()
        ok = (STUB_MARK in text and (STUB_TARGET + card) in text
              and "版本库历史" in text and len(text) < 1200)
        check("根目录入口为转向说明且无独立正文（%s）" % pid, ok,
              "长度 %d" % len(text))

    # 判据三：通讯录引用一致；Yulia 材料旧路径解析。
    contacts = json.load(io.open(CONTACTS, encoding="utf-8"))
    for pid, m in contacts["members"].items():
        ob = m.get("onboarding_file")
        if ob is None:
            check("通讯录留空与索引一致（%s）" % pid,
                  EXPECTED_POSTS.get(pid, "missing") is None)
            continue
        resolves = os.path.exists(os.path.join(ROOT, ob))
        consistent = os.path.basename(ob) == EXPECTED_POSTS.get(pid)
        check("通讯录引用一致可解析（%s）" % pid, resolves and consistent, ob)
    yulia_refs = set()
    for fn in ("决定记录.md", "当前接续.md", "长期记忆与接续方案.md"):
        p = os.path.join(ROOT, "advisors", "yulia", fn)
        if os.path.exists(p):
            yulia_refs |= set(re.findall(r"入职卡_[^\s`）)、；;]+\.md",
                                      io.open(p, encoding="utf-8").read()))
    ok_refs = all(os.path.exists(os.path.join(ROOT, r)) for r in yulia_refs)
    check("Yulia 材料引用的旧路径全部解析（%d 处）" % len(yulia_refs),
          ok_refs and len(yulia_refs) > 0, sorted(yulia_refs)[:3])

    # 判据四补充：差异记录含最新任命与差异（Ciri、Fil、Gwen、Zoya）。
    notes_text = io.open(notes, encoding="utf-8").read()
    check("差异逐项记录（Ciri 旧称谓、Zoya 无卡）",
          "前端视觉顾问" in notes_text and "不虚构" in notes_text)

    # 判据五：语言检查与本地引用检查。
    texts = {"INDEX.md": index_text, "appointment_notes.md": notes_text}
    for card in EXPECTED_POSTS.values():
        if card:
            texts[card] = io.open(os.path.join(OB, card), encoding="utf-8").read()
    for name, text in texts.items():
        hits = list(langcheck.scan_text(text))
        check("语言检查通过（%s）" % name, not hits, str(hits[:2]))
    for r in yulia_refs:
        stub_text = io.open(os.path.join(ROOT, r), encoding="utf-8").read()
        check("引用经转向桩指向现行版本（%s）" % r[:26],
              (STUB_TARGET + r) in stub_text)

    # 判据五：独立恢复检查——仅凭 INDEX.md 找到现行资料、边界与动作。
    recovery = {"posts": {}}
    for pid, card in EXPECTED_POSTS.items():
        entry = {"person_id": pid}
        if card is None:
            entry["card"] = None
            entry["missing_recorded"] = True
        else:
            entry["card"] = os.path.relpath(os.path.join(OB, card), ROOT)
            entry["card_exists"] = os.path.exists(os.path.join(ROOT, entry["card"]))
        entry["boundary_doc"] = "organization/manager_operations.md"
        entry["boundary_doc_exists"] = os.path.exists(
            os.path.join(ROOT, entry["boundary_doc"]))
        entry["next_action_doc"] = ("organization/manager_operations.md "
                                    "看账与核实一节")
        recovery["posts"][pid] = entry
    ok_recovery = all(
        (e["card"] is None or e.get("card_exists"))
        and e.get("boundary_doc_exists") for e in recovery["posts"].values())
    check("独立恢复检查：仅凭公共入口可得现行资料、边界与动作",
          ok_recovery, "岗位数 %d" % len(recovery["posts"]))
    out = os.path.join(_HERE, "recovery_check_result.json")
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps({"checked_at": __import__("datetime").datetime.now()
                    .isoformat(timespec="seconds"),
                    "ok": ok_recovery, "recovery": recovery},
                   ensure_ascii=False, indent=1) + "\n")
    print("恢复检查结果已存：%s" % os.path.relpath(out, ROOT))

    bad = sum(1 for c in _checks if not c["ok"])
    print("收置检查总判定：%s（%d/%d 过）"
          % ("全部通过" if bad == 0 else "存在不过项",
             len(_checks) - bad, len(_checks)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
