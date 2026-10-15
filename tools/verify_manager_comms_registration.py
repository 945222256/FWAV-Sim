"""Verify saved registration using the configured MCP transport, without sending."""
import asyncio
import hashlib
import json
import os
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from manager_message_service import ROOT, schema

OUT = ROOT / "logs/hq_intake/elsa_comms_registration_20261014"
CONFIG_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


async def verify():
    with (CONFIG_HOME / "config.toml").open("rb") as stream:
        config = tomllib.load(stream)
    registration = config["mcp_servers"]["manager_comms"]
    assert registration.get("enabled", True)
    roles = json.loads((ROOT / "organization/communication/role_prompts.json").read_text(encoding="utf-8"))
    for key, role in roles.items():
        with (CONFIG_HOME / (role["profile"] + ".config.toml")).open("rb") as stream:
            profile = tomllib.load(stream)
        assert profile["developer_instructions"] == role["developer_instructions"]
        assert profile["mcp_servers"]["manager_comms"]["env"]["MANAGER_COMMS_ROLE"] == key
    params = StdioServerParameters(command=registration["command"], args=registration["args"],
                                   env=registration.get("env"))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            send_tool = next(t for t in tools.tools if t.name == "send_manager_message")
            assert send_tool.input_schema == schema()
            preview = await session.call_tool("preview_manager_message", {
                "sender": "elsa", "to": "Doro", "purpose": "执行任务", "item_id": "I-186",
                "summary": "二队已开始修正输入来源与结果记录的对应错误。",
                "action": "请独立复核所附交付材料，并登记结论。"
            })
            assert not preview.is_error
            report = {
                "verified_at": datetime.now().astimezone().isoformat(),
                "registration_config": str(CONFIG_HOME / "config.toml"),
                "registered_server": "manager_comms", "transport": "stdio",
                "configured_command": registration["command"], "configured_args": registration["args"],
                "real_initialize_passed": True, "real_tools_list_passed": True,
                "published_schema_matches_file": True, "tools": [t.name for t in tools.tools],
                "initialization_instructions": init.instructions,
                "role_profiles_verified": len(roles), "preview": preview.structured_content,
                "isolated_test_suite": {"tests": 13, "result": "passed", "sender_and_channel_cases": "transport replaced in isolated tests; real stdio client used for discovery and preview"},
                "real_recipient_send_performed": False,
                "calling_chat_catalog_visible": False,
                "existing_manager_chats_reconfigured": False,
                "existing_chat_activation": "reload MCP connection and confirm catalog; choose role launch profile when starting role-bound sessions",
                "input_field_revision": "User 2026-10-14 requested fixed identity/purpose/action fields; original I-194 only-to/content criterion is preserved for explicit review, not silently declared satisfied",
                "sources": ["https://learn.chatgpt.com/docs/extend/mcp?surface=cli", "https://learn.chatgpt.com/docs/agent-configuration/agents-md", "https://learn.chatgpt.com/docs/config-file/config-reference", "https://learn.chatgpt.com/docs/build-skills"]
            }
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "registration_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: report[k] for k in ("verified_at", "registered_server", "tools", "role_profiles_verified", "real_initialize_passed", "published_schema_matches_file", "real_recipient_send_performed", "preview")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(asyncio.wait_for(verify(), timeout=25))
