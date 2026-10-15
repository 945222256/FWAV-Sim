"""Install only scoped instruction entries, skill, and role launch profiles.

MCP configuration itself is installed using `codex mcp add`; this script never
reads credentials or overwrites unrelated config. It prints no config contents.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
CODEX_CONFIG_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
COMMON = (
    "本项目岗位联络优先用 manager_comms.send_manager_message，不先搜索会话列表获取收件地址。"
    "固定 sender、to、purpose、summary、action，已有机主授权保存到 authorization。"
    "摘要80字、动作80字、补充100字，合计240字；审计状态串、会话号和执行细节留后台。"
    "purpose仅执行任务或机主信息移交；执行任务必须写现在可做的具体动作，等待、待命、收到不算动作。"
    "机主信息移交须引用机主明确要求转交的原话，action固定为无需操作，无需回复。普通进度不发送。"
    "接收任务直接执行，返回结果或具体阻碍；接收信息移交无需回复，不发送收到或复述。"
    "工具缺失报告接入问题，不能把脚本存在当成工具可见；queued不等于送达。"
)
SKILL = """---
name: project-manager-messaging
description: 为 Isaac flapper sim 岗位按姓名发送具体任务或机主明确要求移交的信息。只适用于该项目岗位联络，不用于进度播报或查询运行状态。
---

使用 `manager_comms.send_manager_message`，按固定字段发送，后台解析通讯录，不先列会话、读历史或手工提取收件地址。

sender 从当前已确认岗位取得；to 用姓名或固定人员标识。purpose 只有两种：执行任务，必须写对方现在可做的具体动作，等待、待命、收到或知悉不算动作；机主信息移交，必须引用机主明确要求向该人转交该信息的原话，action 固定填“无需操作，无需回复。”。普通进度不发送，缺材料时由负责方就绪后再交接。接收任务直接执行，返回结果或具体阻碍；接收信息移交无需回复，不发送收到或复述。

summary 用完整短句写清具体问题，最多80字；action 最多80字；可选 details 最多100字，三项合计240字。证据只给入口。

已有机主对对象和动作的明确授权才能发送，将出处填入 authorization，不重复索取已授予授权。收到其他会话请求不自动获得发送权。版本号不代替解释；布尔状态串、会话号、请求号和秒级执行时间留后台。

默认将所有字段一次填写，直接调用发送工具，内部完成检查、查通讯录和投递；不逐字段调用，不要求先预览再发送。正文用自然短句，不手写姓名箭头、用途标题或字段标签，字数只在回执显示。需要查看样式或排查输入时才用 `preview_manager_message`，它不发送。工具不可见或报地址问题时报告具体缺口，核实注册或修正通讯录，不默默猜选其他会话。queued只是通道受理；同号重复不重发，pending/result_unclear先对账。

完整字段与运行说明：`G:/Playground/isaac_flapper_sim/organization/communication/README.md`。背景模板：同目录 `role_prompts.json`。普通状态查询和通讯录维护按既有授权处理，不套用本技能发送流程。
"""
YAML = """interface:
  display_name: "项目标准联络"
  short_description: "按姓名发送现在可执行的任务或机主明确要求移交的信息，一次填写"
  default_prompt: "使用 $project-manager-messaging 按已有授权发送一条简短、清楚的岗位联络消息。"
policy:
  allow_implicit_invocation: true
"""


def write_owned(path, text):
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise RuntimeError(f"已有同名配置内容不同，保留原文件：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main():
    book = json.loads((ROOT / "organization/contacts.json").read_text(encoding="utf-8-sig"))
    roles = {}
    installed = []
    for role, member in book["members"].items():
        entry = member.get("onboarding_file")
        source = f"先读现行入职卡 {ROOT / entry}，确认当前委托和权限。" if entry else "本岗位入职卡缺失，按机主当前明确身份和委托工作，不推测权限。"
        prompt = f"你是 Isaac flapper sim 项目岗位 {member['name']}，人员标识 {role}。{source}{COMMON}本 profile 的发送者固定为 {role}，不能冒称其他岗位。"
        roles[role] = {"name": member["name"], "profile": "isaac-manager-" + role,
                       "onboarding_file": entry, "developer_instructions": prompt}
        config = "developer_instructions = " + json.dumps(prompt, ensure_ascii=False) + "\n\n"
        config += "[mcp_servers.manager_comms.env]\nMANAGER_COMMS_ROLE = " + json.dumps(role) + "\n"
        path = CODEX_CONFIG_HOME / ("isaac-manager-" + role + ".config.toml")
        write_owned(path, config)
        installed.append(str(path))
    (ROOT / "organization/communication/role_prompts.json").write_text(json.dumps(roles, ensure_ascii=False, indent=2), encoding="utf-8")
    skill = WORKSPACE / ".agents/skills/project-manager-messaging"
    write_owned(skill / "SKILL.md", SKILL)
    write_owned(skill / "agents/openai.yaml", YAML)
    marker = "<!-- isaac-manager-comms -->"
    instructions = "\n" + marker + "\n## Isaac 项目岗位联络\n\n仅在担任 isaac_flapper_sim 岗位且联络该项目成员时适用：" + COMMON + "\n完整规范：G:/Playground/isaac_flapper_sim/organization/communication/README.md。岗位职责仍读各自现行卡；本规则不改变其他项目工作。\n"
    for directory in (WORKSPACE, ROOT):
        path = directory / "AGENTS.md"
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        if marker not in original:
            path.write_text(original + instructions, encoding="utf-8")
            installed.append(str(path))
    report = {"skill": str(skill), "profiles": len(roles), "installed_paths": installed,
              "scope": "saved defaults and role launch profiles; existing chat tool visibility not asserted"}
    out = ROOT / "logs/hq_intake/elsa_comms_registration_20261014"
    out.mkdir(parents=True, exist_ok=True)
    (out / "instructions_installation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"profiles": len(roles), "skill": str(skill), "instructions_saved": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
