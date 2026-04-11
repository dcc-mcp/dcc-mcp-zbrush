"""dcc-mcp-zbrush — ZBrush adapter for the DCC MCP ecosystem.

Architecture: HTTP Bridge Mode
--------------------------------
ZBrush 2024+ exposes a built-in HTTP REST server on a configurable port.
This package acts as the bridge between dcc-mcp-core's MCP HTTP server and
ZBrush's HTTP API:

    MCP Client (Claude/Cursor)
         ↓ HTTP (MCP Streamable HTTP, port 8765)
    ZBrushMcpServer (this package)
         ↓ HTTP REST (ZBrush API, port 8080)
    ZBrush 2024+ (built-in HTTP server)
         ↓ ZScript execution
    ZBrush sculpting engine

The DccCapabilities for ZBrush marks:
    has_embedded_python = False
    bridge_kind = "http"
    bridge_endpoint = "http://localhost:8080"

Quickstart::

    import dcc_mcp_zbrush
    handle = dcc_mcp_zbrush.start_server(port=8765, zbrush_port=8080)
    # MCP host connects to http://127.0.0.1:8765/mcp
    handle.shutdown()

Requirements:
    - ZBrush 2024.0.1+
    - ZBrush HTTP Server enabled (Preferences > Network > Enable HTTP Server)
    - dcc-mcp-core >= 0.12.14
    - httpx >= 0.25.0
"""

from __future__ import annotations

from dcc_mcp_zbrush.__version__ import __version__
from dcc_mcp_zbrush.api import (
    ZBrushNotAvailableError,
    is_zbrush_available,
    zb_error,
    zb_from_exception,
    zb_success,
)
from dcc_mcp_zbrush.bridge import ZBrushBridge
from dcc_mcp_zbrush.server import ZBrushMcpServer, start_server, stop_server

__all__ = [
    "__version__",
    # Server
    "ZBrushMcpServer",
    "start_server",
    "stop_server",
    # Bridge
    "ZBrushBridge",
    # Skill authoring helpers
    "zb_success",
    "zb_error",
    "zb_from_exception",
    "is_zbrush_available",
    "ZBrushNotAvailableError",
]
