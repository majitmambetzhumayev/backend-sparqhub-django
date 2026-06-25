import logging

from ai_providers.factory import get_provider

logger = logging.getLogger(__name__)


def _build_system_prompt(base: str, memories: list[str]) -> str:
    if not memories:
        return base
    context = "\n".join(f"- {m}" for m in memories)
    return f"{base}\n\nRelevant context from memory:\n{context}"


async def _get_mcp_context(assistant) -> tuple[list[dict], object]:
    from mcp_client.models import MCPServer
    from mcp_client.services import get_tools_from_server, call_tool

    servers = [s async for s in MCPServer.objects.filter(assistant=assistant, enabled=True)]
    if not servers:
        return [], None

    all_tools: list[dict] = []
    tool_server_map: dict[str, object] = {}

    for server in servers:
        try:
            tools = await get_tools_from_server(server)
            all_tools.extend(tools)
            for tool in tools:
                tool_server_map[tool["name"]] = server
        except Exception:
            logger.warning("Failed to fetch tools from MCP server %s", server.name)

    if not all_tools:
        return [], None

    async def tool_executor(name: str, arguments: dict) -> str:
        server = tool_server_map.get(name)
        if server is None:
            raise ValueError(f"Unknown MCP tool: {name}")
        return await call_tool(server, name, arguments)

    return all_tools, tool_executor


async def send_chat_message(assistant, message_text: str, memories: list[str] | None = None, stream: bool = False):
    try:
        provider = get_provider(assistant.ai_provider)
        system = _build_system_prompt(assistant.instructions, memories or [])
        messages = [{"role": "user", "content": message_text}]
        tools, tool_executor = await _get_mcp_context(assistant)
        return await provider.chat(
            assistant, messages, system=system, stream=stream,
            tools=tools, tool_executor=tool_executor,
        )
    except Exception:
        logger.exception("Error during chat dispatch")
        raise
