"""Maxon ZBrush adapter for DCC MCP Core."""

from dcc_mcp_zbrush.__version__ import __version__
from dcc_mcp_zbrush._env import (
    ENV_AUTOSTART,
    ENV_ENABLE_GATEWAY_FAILOVER,
    ENV_GATEWAY_PORT,
    ENV_MODE,
    ENV_PORT,
    ENV_SOCKET_HOST,
    ENV_SOCKET_PORT,
    resolve_enable_gateway_failover,
    resolve_minimal_mode_enabled,
    resolve_mode,
)
from dcc_mcp_zbrush._skill_loader import (
    MINIMAL_SKILLS,
    STAGE_SKILLS,
    STAGES,
    build_minimal_mode_config,
    build_minimal_mode_for_stages,
    skills_for_stage,
)
from dcc_mcp_zbrush._version_probe import (
    get_active_tool_path,
    get_zbrush_version_string,
    get_zbrush_version_tuple,
    is_zbrush_available,
)
from dcc_mcp_zbrush.api import (
    ZBrushNotAvailableError,
    get_bridge,
    import_zbc,
    set_bridge,
    with_zbrush,
    zb_error,
    zb_from_exception,
    zb_success,
)
from dcc_mcp_zbrush.api import (
    is_zbrush_available as is_zb_available,
)
from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridge, ZBrushBridgeError
from dcc_mcp_zbrush.server import (
    DEFAULT_PORT,
    SERVER_NAME,
    ZBrushMcpServer,
    ZBrushServerOptions,
    get_server,
    start_server,
    stop_server,
)

__all__ = [
    "__version__",
    "DEFAULT_PORT",
    "ENV_AUTOSTART",
    "ENV_ENABLE_GATEWAY_FAILOVER",
    "ENV_GATEWAY_PORT",
    "ENV_MODE",
    "ENV_PORT",
    "ENV_SOCKET_HOST",
    "ENV_SOCKET_PORT",
    "MINIMAL_SKILLS",
    "SERVER_NAME",
    "STAGES",
    "STAGE_SKILLS",
    "SocketBridge",
    "ZBrushBridge",
    "ZBrushBridgeError",
    "ZBrushMcpServer",
    "ZBrushNotAvailableError",
    "ZBrushServerOptions",
    "build_minimal_mode_config",
    "build_minimal_mode_for_stages",
    "get_active_tool_path",
    "get_bridge",
    "get_server",
    "get_zbrush_version_string",
    "get_zbrush_version_tuple",
    "import_zbc",
    "is_zbrush_available",
    "is_zb_available",
    "resolve_enable_gateway_failover",
    "resolve_minimal_mode_enabled",
    "resolve_mode",
    "set_bridge",
    "skills_for_stage",
    "start_server",
    "stop_server",
    "with_zbrush",
    "zb_error",
    "zb_from_exception",
    "zb_success",
]
