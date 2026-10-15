"""MCP stdio entrypoint; stdout is protocol-only."""
import asyncio
import json
import sys

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from manager_message_service import MessageError, prepare, public_result, schema, send

INSTRUCTIONS = (
    "Isaac flapper sim 经理联络默认使用 send_manager_message，仅两种用途："
    "执行任务须写现在可做的具体动作，等待、待命、知悉或确认收到不算动作；收件人直接执行，返回结果或具体阻碍。"
    "机主信息移交须引用机主明确要求向该人转交该信息的原话，action固定为无需操作，无需回复。"
    "普通进度不发送。接收者不以收到或复述来信结束，不发送收悉回信。"
    "所有字段一次填写，直接调用发送；内部完成检查、查通讯录和投递，不先预览或逐字段调用。"
    "后台查唯一通讯录，正常传话不搜索会话列表。摘要80字、动作80字、补充100字，正文合计240字，字数只在回执显示。"
    "收件正文用自然短句，发送者不手写姓名箭头、用途标题或字段标签。"
    "已有机主对对象和动作的明确授权才能发送，工具不新增权限，不重复索取已授予授权。"
    "版本号不能代替问题解释，审计状态串和会话号只进后台。preview_manager_message仅校验，不发送。"
    "queued只是通道受理；pending/result_unclear先对账。同号不重复投递。sender默认为岗位声明，并非认证身份。"
)


async def list_tools(ctx, params):
    input_schema = schema()
    preview_schema = json.loads(json.dumps(input_schema))
    preview_schema["required"] = [p for p in input_schema["required"] if p != "authorization"]
    annotations = dict(destructiveHint=False, openWorldHint=True)
    return types.ListToolsResult(tools=[
        types.Tool(name="send_manager_message", title="标准联络：按姓名发送",
                   description="默认一次提交。purpose仅执行任务或机主信息移交：前者必须有现在可做的动作，后者必须引用机主明确转交指令且无需回复。普通进度、等待和确认收到不发送。内部校验、查通讯录并投递，预览可选。",
                   inputSchema=input_schema,
                   annotations=types.ToolAnnotations(readOnlyHint=False, idempotentHint=True, **annotations)),
        types.Tool(name="preview_manager_message", title="标准联络：校验与预览",
                   description="可选检查入口，仅在需要查看样式或排查输入时使用；正常发送直接用 send_manager_message 一次提交。返回收件人实际会看到的短消息，不发送、不写生产审计。",
                   inputSchema=preview_schema,
                   annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True, **annotations)),
    ])


async def call_tool(ctx, params):
    payload = dict(params.arguments or {})
    try:
        if params.name == "preview_manager_message":
            payload.setdefault("authorization", {"basis": "仅预览，未获得发送授权"})
            prepared = prepare(payload)
            result = public_result(prepared, "preview_only")
            result["message"] = prepared["message"]
        elif params.name == "send_manager_message":
            result = await asyncio.to_thread(send, payload)
        else:
            raise MessageError("未知工具")
    except (MessageError, ValueError, OSError, KeyError) as exc:
        result = {"status": "rejected", "reason": str(exc)[:700], "sent": False}
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                                structuredContent=result,
                                isError=result["status"] in ("rejected", "channel_missing", "channel_rejected", "result_unclear"))


server = Server("manager_comms", version="2.0.0", instructions=INSTRUCTIONS,
                on_list_tools=list_tools, on_call_tool=call_tool)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
