import logging
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)


def _to_tool_schema(tool) -> dict:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema,
    }


def _extract_result_text(result) -> str:
    if not result.content:
        return ""
    return "\n".join(
        item.text for item in result.content if hasattr(item, "text")
    )


@asynccontextmanager
async def _open_session(server):
    if server.transport == "stdio":
        params = StdioServerParameters(command=server.command, args=server.args)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    elif server.transport == "sse":
        async with sse_client(server.url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        raise ValueError(f"Unsupported transport: {server.transport}")


async def get_tools_from_server(server) -> list[dict]:
    async with _open_session(server) as session:
        response = await session.list_tools()
        return [_to_tool_schema(t) for t in response.tools]


async def call_tool(server, tool_name: str, arguments: dict) -> str:
    async with _open_session(server) as session:
        result = await session.call_tool(tool_name, arguments)
        return _extract_result_text(result)
