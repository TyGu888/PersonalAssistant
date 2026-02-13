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
    name="mcp_connect",
    description="Connect to an MCP server at runtime. Discovers and registers all tools provided by the server.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Unique name for this MCP server"},
            "command": {
                "type": "string",
                "description": "Command to start the server (e.g. 'npx', 'python'). Required if url is not provided."
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command arguments (e.g. ['-y', '@modelcontextprotocol/server-filesystem', './data'])"
            },
            "env": {
                "type": "object",
                "description": "Environment variables for the server process. Supports ${VAR} syntax."
            },
            "url": {
                "type": "string",
                "description": "SSE URL for remote MCP server (not yet supported)"
            }
        },
        "required": ["name"]
    }
)
async def mcp_connect(name: str, command: str = None, args: list = None,
                      env: dict = None, url: str = None, context=None) -> str:
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


@registry.register(
    name="mcp_disconnect",
    description="Disconnect from an MCP server and remove all its tools.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the MCP server to disconnect"}
        },
        "required": ["name"]
    }
)
async def mcp_disconnect(name: str, context=None) -> str:
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


@registry.register(
    name="mcp_list",
    description="List all connected MCP servers and their available tools.",
    parameters={
        "type": "object",
        "properties": {}
    }
)
async def mcp_list(context=None) -> str:
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
