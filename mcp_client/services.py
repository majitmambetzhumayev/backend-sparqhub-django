import ipaddress
import logging
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)


def is_safe_sse_url(url: str) -> bool:
    """Rejects SSE MCP server URLs that would make this backend's own network
    stack connect to a non-public host (cloud metadata endpoints, internal
    services, loopback, etc.) — SSE means *this server* makes the outbound
    connection, on the user's behalf, to wherever they point it.

    Called both at registration time (MCPServerSerializer.validate) and
    again here, immediately before every actual connection (_open_session)
    — a hostname that resolved to a public IP when the server was saved can
    later be repointed at an internal address (DNS rebinding, near-zero
    TTL), and every chat turn re-resolves the hostname fresh via a new
    connection. A one-time check at save time can't catch that; only a
    check at connection time can."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False
    for *_, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _to_tool_schema(tool) -> dict:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema,
    }


def _extract_result_text(result) -> str:
    text = "\n".join(item.text for item in result.content if hasattr(item, "text")) if result.content else ""
    # CallToolResult.isError signals the remote MCP server's tool itself
    # failed — without this check, a failed call and a legitimately empty/
    # short successful one were indistinguishable to the model (and an
    # error with no content text collapsed into the exact same "" as a
    # trivially-empty success).
    if getattr(result, "isError", False):
        return f"Error: {text}" if text else "Error: the tool call failed with no further detail."
    return text


@asynccontextmanager
async def _open_session(server):
    if server.transport == "stdio":
        params = StdioServerParameters(command=server.command, args=server.args)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    elif server.transport == "sse":
        if not is_safe_sse_url(server.url):
            raise ValueError(f"Refusing to connect to unsafe SSE URL: {server.url}")
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
