"""MCP server module — requires the [mcp] optional extra."""

from pester.core.extras import make_optional_check

HAS_MCP, require_mcp = make_optional_check("mcp", "mcp", label="MCP server")
