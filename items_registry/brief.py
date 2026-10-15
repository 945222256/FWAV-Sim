# -*- coding: utf-8 -*-
"""brief.py — I-059 HQ 派发脚手架：临时员工会话简报生成器。

用途：机主派一个新会话当施工队时，跑本工具得到一段自足的简报文本，
整段复制进新会话即可零追问开工。取代人肉贴开工卡。

判据（事项 I-059）：
  1. 新会话凭生成的简报零追问开工；
  2. 简报内容全部可追溯账本——本工具只做现算，一切内容在生成时刻
     从 items.json、lanes.json 与《协作架构_20260930.md》第三节读出，
     不写死任何会过期的数据；
  3. 生成器本地零依赖——仅 Python 标准库。

用法：
  python items_registry/brief.py L3        生成 L3 队简报
  python items_registry/brief.py L1        生成 L1 队简报
输出：标准打印，整段复制 BEGIN 到 END 之间。
"""
import json
import os
import re
import sys
import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_DIR)
ITEMS_PATH = os.path.join(_DIR, "items.json")
LANES_PATH = os.path.join(_DIR, "lanes.json")
CHARTER_PATH = os.path.join(ROOT, "协作架构_20260930.md")
BANNED_PATH = os.path.join(_DIR, "banned_words.json")

ARCHIVE_FALLBACK = ["tools/", "logs/"]


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_card():
    """从协作架构第三节现读开工卡正文（HQ 领地文件，只读）。"""
    if not os.path.exists(CHARTER_PATH):
        raise SystemExit("[brief] 找不到 %s，开工卡无从现读，拒绝生成"
                         % CHARTER_PATH)
    with open(CHARTER_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"(## 三、施工队开工卡.*?)(?=\n## 四、)", text, re.S)
    if not m:
        raise SystemExit("[brief] 协作架构中未找到第三节开工卡，拒绝生成")
    return m.group(1).rstrip()


def first_claimable(lane_id, lanes, items):
    """现算该路自上而下第一条可领事项。

    口径与开工卡一致：按拓扑层与编号排序，取第一条 open、无人认领、
    依赖已清（层零）的事项。返回 (事项或None, 本路在办列表, 拒领原因)。
    """
    sys.path.insert(0, _DIR)
    import schedule
    layers = schedule.topo_layers()
    lane_items = [it for it in items if it.get("lane") == lane_id]
    wip = [it for it in lane_items if it.get("status") == "in_progress"]
    reason = ""
    if wip:
        reason = ("在制限制：本路 %s 正在办 %s，一路同时只许一个进行中事项，"
                  "认领会被拒绝；先完成或申报卡住再派新工"
                  % (wip[0].get("claimed_by"), wip[0]["id"]))
        return None, wip, reason
    for it in sorted(lane_items,
                     key=lambda x: (layers.get(x["id"], 0), x["id"])):
        if it.get("status") == "blocked":
            continue
        if it.get("status") == "open" and not it.get("claimed_by"):
            if layers.get(it["id"], 0) > 0:
                continue
            return it, wip, reason
    if not lane_items:
        reason = "本路看板当前为空"
    else:
        reason = "本路事项全部为阻塞、依赖未清或已认领状态"
    return None, wip, reason


def build(lane_id):
    lanes_conf = _load(LANES_PATH)
    lanes = lanes_conf["lanes"]
    if lane_id not in lanes:
        raise SystemExit("[brief] 路线不存在：%s（可选 %s）"
                         % (lane_id, list(lanes)))
    lane = lanes[lane_id]
    items = _load(ITEMS_PATH)["items"]
    closed = sum(1 for it in items if it.get("status") == "closed")
    shared = lanes_conf.get("shared_paths")
    shared_note = ""
    if shared is None:
        shared = ARCHIVE_FALLBACK
        shared_note = "（lanes.json 未登记共享区，按协作架构第五节回退）"
    other_paths = []
    for lid, l in lanes.items():
        if lid != lane_id:
            for p in l.get("exclusive_paths", []):
                other_paths.append("%s 归 %s 独占" % (p, lid))
    banned = _load(BANNED_PATH)

    card = read_card()
    pick, wip, reason = first_claimable(lane_id, lanes, items)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    out = []
    out.append("BEGIN（整段复制以下内容到新会话）")
    out.append("你是 %s 队（%s），使命：%s。" % (lane_id, lane.get("name", ""),
                                             lane.get("mission", "")))
    out.append("简报生成于 %s，由 items_registry/brief.py 现算，内容来源："
               "items.json、lanes.json、协作架构_20260930.md 第三节。"
               % now)
    out.append("")
    out.append("── 路线身份与领地 ──")
    out.append("本路独占领地（只许改这里）：%s。"
               % "、".join(lane.get("exclusive_paths", [])))
    out.append("参数改值权限：%s；重构权限：%s。"
               % ("有" if lane.get("may_change_parameters") else "无",
                  "有" if lane.get("may_refactor") else "无"))
    out.append("共享区：%s%s。共享区规则：进入共享区的提交必须引用本路"
               "在办事项编号。" % ("、".join(shared), shared_note))
    out.append("禁区（他人独占，踏入即被 check_commit 拒绝）：%s。"
               % ("；".join(other_paths) if other_paths else "无"))
    out.append("")
    out.append("── 本路现状（生成时刻） ──")
    out.append("总账：事项 %d 条，已关闭 %d，未关闭 %d。"
               % (len(items), closed, len(items) - closed))
    if pick:
        ev = pick.get("evidence") or []
        out.append("你的第一条可领事项：%s %s。" % (pick["id"], pick["title"]))
        out.append("销项判据：%s。" % pick.get("close_criterion", "未登记"))
        if pick.get("note"):
            out.append("事项备注：%s" % pick["note"])
        if pick.get("depends_on"):
            out.append("依赖：%s。" % "、".join(pick["depends_on"]))
        out.append("该事项已有在档证据 %d 条，动手前先读证据与第四节"
                   "既有资产。" % len(ev))
        out.append("认领命令：python -c \"import sys; "
                   "sys.path.insert(0,'items_registry'); "
                   "import dispatch; dispatch.claim('%s','%s队')\""
                   % (pick["id"], lane_id))
    else:
        out.append("当前无可领事项。%s。请向机主回报并待命。" % reason)
    out.append("")
    out.append("── 开工卡（协作架构第三节，现行全文） ──")
    out.append(card)
    out.append("")
    out.append("── 红线速查 ──")
    out.append("1. 领地：改动超出本路独占区即拒；共享区提交必须引用本路"
               "事项编号；任何一路不得修改他路独占区。")
    out.append("2. 在制限制：一路同时只许持有一个进行中事项。")
    out.append("3. 租约：认领默认四小时，到期未关闭自动退回 open。")
    out.append("4. 语言：提交信息与文档过 items_registry/langcheck.py，"
               "三类违规即拒——比喻词、内部压缩词（现行词表 %s）、"
               "废止编号（班号与旧轮次号，只准用 I 编号）。"
               % "、".join(sorted(banned.get("internal_banned", {}))))
    out.append("5. 汇报：收尾用《汇报标准_20260930.md》七段格式；"
               "总目标未过第一句必须报告失败。")
    out.append("6. 完成：只认总账 ledger.close 带证据的关闭记录，"
               "提交声明不算完成。")
    out.append("7. 新问题：intake.submit 进待审队列，不得直接写正式清单。")
    out.append("")
    out.append("── 命令速查 ──")
    out.append("看板：python items_registry/dispatch.py")
    out.append("自查：python items_registry/check_commit.py %s" % lane_id)
    out.append("总账：python items_registry/ledger.py")
    out.append("语言自查：python items_registry/langcheck.py --msg \"文本\"")
    out.append("END")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法: brief.py <路线编号，如 L3>")
    print(build(sys.argv[1].upper()))
