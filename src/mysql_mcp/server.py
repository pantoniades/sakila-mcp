"""MCP server construction. Tools and resources are registered statically here."""

from mcp.server import MCPServer

from mysql_mcp import __version__
from mysql_mcp.config import Config

SERVER_NAME = "mysql-mcp"


def build_server(config: Config) -> MCPServer:
    """Build the MCP server for one MySQL target."""
    del config  # used once tools are registered
    return MCPServer(
        SERVER_NAME,
        version=__version__,
        instructions="Read-only access to one MySQL server: schema inspection and bounded queries.",
    )
