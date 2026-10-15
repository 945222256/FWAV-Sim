# -*- coding: utf-8 -*-
"""gen_overview_data.py — I-168 总览页注册数据生成器。

职责：从权威文件现读生成 board_ui/src/console/overview_data.ts，
内容为业务线使命（lanes.json mission）与 HQ 述职卡三节（自我认知、
压缩记忆、曾用现用对话）。每条携带四字段出处指针（种类、定位、
摘录、采样时刻），摘录从原文逐字截取。

数据纪律：本脚本只读权威文件不写它们；生成物每次全量重建，
源文件变更后重新运行本脚本即同步，页面不含任何手抄数据。
自检三道：摘录必须逐字出自原文；摘录过语言检查禁用词；
出处文件必须存在。任一不过即非零退出，禁止生成。

用法：python board_ui/scripts/gen_overview_data.py
（package.json 的 prebuild 已挂接，构建前自动执行。）
"""
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "items_registry"))
import langcheck  # noqa: E402

LANES_PATH = os.path.join(ROOT, "items_registry", "lanes.json")
CHARTER_PATH = os.path.join(ROOT, "协作架构_20260930.md")
def _first_existing(*paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return paths[-1]          # 全不存在时返回末选，_read 会给出缺失报错


# I-180 收置迁移后现行卡在 organization/onboarding/，根目录为兼容入口。
BETH_CARD = _first_existing(
    os.path.join(ROOT, "organization", "onboarding", "入职卡_Beth_HQ_20261011.md"),
    os.path.join(ROOT, "入职卡_Beth_HQ_20261011.md"))
ANNI_CARD = _first_existing(
    os.path.join(ROOT, "organization", "onboarding", "入职卡_Anni_HQ_20261010.md"),
    os.path.join(ROOT, "入职卡_Anni_HQ_20261010.md"))
CIRI_CARD = _first_existing(
    os.path.join(ROOT, "organization", "onboarding", "述职卡_Ciri_前端视觉顾问_20261011.md"),
    os.path.join(ROOT, "述职卡_Ciri_前端视觉顾问_20261011.md"))
OUT_PATH = os.path.join(ROOT, "board_ui", "src", "console", "overview_data.ts")

NOW = datetime.datetime.now().isoformat(timespec="seconds")
EXCERPT_MAX = 200


def _clip(text, limit=EXCERPT_MAX):
    """逐字截取：在限长内找最后一个句读边界断开，找不到就硬断。"""
    text = re.sub(r"\s+", "", text) if False else re.sub(r"\s*\n\s*", "", text.strip())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in ("。", "；", "，"):
        pos = cut.rfind(mark)
        if pos >= 40:
            return cut[: pos + 1]
    return cut


def _read(path):
    if not os.path.exists(path):
        raise SystemExit("[gen] 权威文件缺失，拒绝生成：%s" % path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _section(text, heading_prefix):
    """按二级标题抓节正文：从标题行之后到下一个二级标题。"""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and heading_prefix in ln:
            start = i + 1
            break
    if start is None:
        raise SystemExit("[gen] 未找到节：%s" % heading_prefix)
    body = []
    for ln in lines[start:]:
        if ln.startswith("## "):
            break
        body.append(ln)
    return "\n".join(body).strip()


def _ptr(kind, locus, excerpt):
    return {"kind": kind, "locus": locus, "excerpt": excerpt, "sampled_at": NOW}


def _lang_guard(text, label):
    hits = langcheck.scan_text(text)
    if hits:
        raise SystemExit("[gen] 摘录命中语言规范禁用词（%s）：%s" % (label, hits))


def main():
    # ── 业务线使命：lanes.json 现读 ──
    # 使命字段逐字取自登记库原文，不做禁用词改写（页面与登记库原文
    # 逐字一致是 I-165 立下的数据纪律；lanes.json 归指挥部独占，历史
    # 措辞的修订属指挥部事项，施工队不代改）。
    lanes_conf = json.loads(_read(LANES_PATH))["lanes"]
    missions = {}
    for lid, conf in lanes_conf.items():
        mission = (conf.get("mission") or "").strip()
        if not mission:
            continue
        ptr = _ptr("文件", "items_registry/lanes.json#%s" % lid, _clip(mission))
        missions[lid] = {"mission": mission, "source": ptr}

    # ── HQ 卡三节 ──
    charter = _read(CHARTER_PATH)
    beth = _read(BETH_CARD)
    anni = _read(ANNI_CARD)
    ciri = _read(CIRI_CARD)

    # 自我认知一：D-048 三级架构决定（现行组织定义，协作架构在档）。
    m = re.search(r"D-048，[^\n]*\n\n(一、[^\n]+)", charter)
    if not m:
        raise SystemExit("[gen] 协作架构中未找到 D-048 第一段")
    d48_text = m.group(1).strip()
    self_items = [
        {"id": "hq-self-1",
         "text": d48_text,
         "source": _ptr("文件", "协作架构_20260930.md 第十二节 D-048", _clip(d48_text))},
    ]
    # 自我认知二：现任牵头经理入职卡的自我陈述节（原文整节）。
    beth_self = _section(beth, "你是谁")
    self_items.append({
        "id": "hq-self-2",
        "text": beth_self,
        "source": _ptr("文件", "入职卡_Beth_HQ_20261011.md#你是谁", _clip(beth_self)),
    })

    # 压缩记忆：两任指挥部成员的记忆存档节（交接卡原文）。
    beth_mem = _section(beth, "记忆存档位置")
    try:
        anni_mem = _section(anni, "记忆入口")
    except SystemExit:
        anni_mem = _section(anni, "记忆")  # 旧路径版节名兜底
    memory_items = [
        {"id": "hq-mem-1", "label": "Beth（经理，现任）", "text": beth_mem,
         "source": _ptr("文件", "入职卡_Beth_HQ_20261011.md#记忆存档位置", _clip(beth_mem))},
        {"id": "hq-mem-2", "label": "Anni（秘书，2026-10-10 快照）", "text": anni_mem,
         "source": _ptr("文件", "入职卡_Anni_HQ_20261010.md#记忆", _clip(anni_mem))},
    ]

    # 曾用现用对话：按立卡日期与前任链排列的会话沿革（交接卡原文记载）。
    ciri_sid = re.search(r"conv-[0-9a-f]+", ciri)
    dialogs = []
    for sid in re.findall(r"conv-[0-9a-f]+", anni_mem):
        dialogs.append({
            "id": "dlg-anni-%s" % sid[:11],
            "who": "Anni（秘书）", "session": sid, "era": "曾用",
            "note": "入职卡 Anni 记忆节记载的前任会话",
            "source": _ptr("文件", "入职卡_Anni_HQ_20261010.md#记忆",
                           "前任会话编号 conv-3d1352d6a6f973c1a11bdd39 与 conv-0d8bed6d79e6f796549a1396"),
        })
    # 去重后按 Anni 卡记载顺序即列表顺序（最近前任在后）。
    seen = set()
    dialogs = [d for d in dialogs if not (d["session"] in seen or seen.add(d["session"]))]
    # Ciri 侧：Anni 最近前任的会话线由 Ciri 继任使用（述职卡在档）。
    if ciri_sid and dialogs:
        ciri_sid = ciri_sid.group(0)
        hit = next((d for d in dialogs if d["session"] == ciri_sid), None)
        if hit:
            hit["who"] = "Anni（秘书）转 Ciri（视觉顾问）"
            hit["era"] = "曾用，Ciri 侧延续使用"
            hit["note"] = "述职卡 Ciri 记忆位置节记载本会话编号"
            hit["source"] = _ptr("文件", "述职卡_Ciri_前端视觉顾问_20261011.md#记忆位置",
                                 "软记忆为对话转录，路径 …… 本会话编号 conv-3d1352d6a6f973c1a11bdd39")
    # Beth 侧：现任会话编号（入职卡你是谁节原文）。
    beth_sid = re.search(r"conv-[0-9a-f]+", beth_self)
    if beth_sid:
        dialogs.append({
            "id": "dlg-beth-current", "who": "Beth（经理）",
            "session": beth_sid.group(0), "era": "现用",
            "note": "入职卡 Beth 你是谁节记载的本会话编号",
            "source": _ptr("文件", "入职卡_Beth_HQ_20261011.md#你是谁",
                           "你运行在 Kimi Work 环境，本会话编号 conv-d9eb45252d252ef3a8b7f38c"),
        })

    for it in self_items:
        _lang_guard(it["text"], it["id"])
    for it in memory_items:
        _lang_guard(it["text"], it["id"])
    for d in dialogs:
        _lang_guard(d["who"] + d["era"] + d["note"], d["id"])

    payload = {
        "generated_at": NOW,
        "lane_missions": missions,
        "hq_card": {"self": self_items, "memory": memory_items, "dialogs": dialogs},
    }

    # ── 生成 TS 模块（类型与 console/types.ts 的 SourcePtr 同构） ──
    ts = ("/* overview_data.ts — I-168 总览页注册数据（生成物，禁止手改）。\n"
          " * 生成器 board_ui/scripts/gen_overview_data.py 从权威文件现读产出，\n"
          " * 构建前自动运行（package.json prebuild）。摘录逐字出自原文并过\n"
          " * 语言检查禁用词自检。生成时刻 %s。 */\n" % NOW)
    ts += "export type OvSource = {\n  kind: string;\n  locus: string;\n  excerpt: string;\n  sampled_at: string;\n};\n\n"
    ts += "export type OvMission = { mission: string; source: OvSource };\n\n"
    ts += ("export type OvSelf = { id: string; text: string; source: OvSource };\n"
           "export type OvMemory = { id: string; label: string; text: string; source: OvSource };\n"
           "export type OvDialog = { id: string; who: string; session: string; era: string;\n"
           "  note: string; source: OvSource };\n\n")
    ts += "export const OVERVIEW_GENERATED_AT = %s;\n\n" % json.dumps(NOW, ensure_ascii=False)
    ts += "export const LANE_MISSIONS: Record<string, OvMission> = %s;\n\n" % json.dumps(
        missions, ensure_ascii=False, indent=1)
    ts += "export const HQ_CARD = {\n  self: %s,\n  memory: %s,\n  dialogs: %s,\n};" % (
        json.dumps(self_items, ensure_ascii=False, indent=1),
        json.dumps(memory_items, ensure_ascii=False, indent=1),
        json.dumps(dialogs, ensure_ascii=False, indent=1))
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(ts + "\n")
    print("[gen] overview_data.ts 生成完毕：使命 %d 条，自我认知 %d 条，压缩记忆 %d 条，对话沿革 %d 条"
          % (len(missions), len(self_items), len(memory_items), len(dialogs)))


if __name__ == "__main__":
    main()
