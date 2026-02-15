"""
MCP Tools - 运行时 MCP 服务器管理工具

提供:
- mcp_connect: 连接新的 MCP Server
- mcp_disconnect: 断开 MCP Server
- mcp_list: 列出所有已连接的 MCP Server 及其工具
"""

from tools.registry import registry
import logging

logger = logging.getLogger(__name__)


@registry.register(
    name="mcp",
    description=(
        "Manage MCP (Model Context Protocol) servers at runtime. "
        "Actions: connect (add MCP server and discover tools), disconnect (remove server), list (show all servers and tools)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["connect", "disconnect", "list"],
                "description": "Action to perform"
            },
            "name": {"type": "string", "description": "MCP server name (for connect/disconnect)"},
            "command": {"type": "string", "description": "Command to start server (for connect)"},
            "args": {"type": "array", "items": {"type": "string"}, "description": "Command arguments (for connect)"},
            "env": {"type": "object", "description": "Environment variables (for connect)"},
            "url": {"type": "string", "description": "SSE URL (for connect, not yet supported)"}
        },
        "required": ["action"]
    }
)
async def mcp(action: str, name: str = None, command: str = None, args: list = None,
              env: dict = None, url: str = None, context=None) -> str:
    """Manage MCP servers at runtime."""

    if action == "connect":
        if not name:
            return "Error: connect action requires name"
        return await _mcp_connect(name=name, command=command, args=args, env=env, url=url)

    elif action == "disconnect":
        if not name:
            return "Error: disconnect action requires name"
        return await _mcp_disconnect(name=name)

    elif action == "list":
        return await _mcp_list()

    else:
        return f"Error: unknown action '{action}'. Available: connect, disconnect, list"


async def _mcp_connect(name: str, command: str = None, args: list = None,
                       env: dict = None, url: str = None) -> str:
    """Connect to an MCP server and register its tools."""
    from tools.mcp_client import MCPServer

    # Validate input
    if url:
        return "Error: SSE transport is not yet supported. Only stdio (command) is available."

    if not command:
        return "Error: 'command' is required (SSE url is not yet supported)."

    # Create server config and register
    server = MCPServer(
        name=name,
        command=command,
        args=args or [],
        env=env or {}
    )

    try:
        await registry.register_mcp_server(server)
    except Exception as e:
        logger.error(f"Failed to connect MCP server '{name}': {e}")
        return f"Error: Failed to connect to MCP server '{name}': {e}"

    # Check if registration succeeded by looking for tools
    from tools.mcp_client import mcp_manager
    client = mcp_manager.get_client(name)
    if not client or not client.connected:
        return f"Error: Failed to connect to MCP server '{name}'."

    tool_names = [f"mcp:{name}:{t}" for t in client.tools]
    if tool_names:
        tool_list = "\n".join(f"  - {t}" for t in tool_names)
        return f"Connected to MCP server '{name}'. Discovered {len(tool_names)} tools:\n{tool_list}"
    else:
        return f"Connected to MCP server '{name}', but no tools were discovered."


async def _mcp_disconnect(name: str) -> str:
    """Disconnect from an MCP server and unregister its tools."""
    from tools.mcp_client import mcp_manager

    client = mcp_manager.get_client(name)
    if not client:
        return f"Error: MCP server '{name}' not found."

    # Disconnect
    try:
        await client.disconnect()
    except Exception as e:
        logger.error(f"Error disconnecting MCP server '{name}': {e}")
        return f"Error: Failed to disconnect MCP server '{name}': {e}"

    # Remove tools from registry
    prefix = f"mcp:{name}:"
    removed = [t for t in list(registry._tools) if t.startswith(prefix)]
    for tool_name in removed:
        del registry._tools[tool_name]

    # Remove client from manager
    mcp_manager._clients.pop(name, None)

    logger.info(f"Disconnected MCP server '{name}', removed {len(removed)} tools")
    return f"Disconnected MCP server '{name}'. Removed {len(removed)} tools."


async def _mcp_list() -> str:
    """List all connected MCP servers and their tools."""
    from tools.mcp_client import mcp_manager

    servers = mcp_manager.list_servers()
    if not servers:
        return "No MCP servers connected."

    lines = [f"{len(servers)} MCP server(s) connected:\n"]
    for server_name in servers:
        client = mcp_manager.get_client(server_name)
        status = "connected" if client and client.connected else "disconnected"
        lines.append(f"  {server_name} ({status})")
        if client and client.tools:
            for tool_name in client.tools:
                lines.append(f"    - mcp:{server_name}:{tool_name}")
        else:
            lines.append("    (no tools)")

    return "\n".join(lines)
