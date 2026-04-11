"""ZBrushMcpServer — MCP bridge server for ZBrush 2024+.

Uses HTTP bridge mode:
    MCP Client → ZBrushMcpServer (port 8765) → ZBrushBridge → ZBrush HTTP API (port 8080)
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_BUILTIN_SKILLS_DIR = Path(__file__).parent / "skills"

_server_instance: Optional["ZBrushMcpServer"] = None
_server_lock = threading.Lock()


class ZBrushMcpServer:
    """MCP bridge server for ZBrush 2024+.

    Capabilities::

        from dcc_mcp_core import DccCapabilities
        caps = DccCapabilities.http_bridge("http://localhost:8080")
        # has_embedded_python=False, bridge_kind="http"
    """

    def __init__(
        self,
        port: int = 8765,
        zbrush_host: str = "localhost",
        zbrush_port: int = 8080,
        api_key: Optional[str] = None,
        extra_skill_paths: Optional[List[str]] = None,
    ) -> None:
        self._port = port
        self._zbrush_host = zbrush_host
        self._zbrush_port = zbrush_port
        self._api_key = api_key
        self._extra_skill_paths = extra_skill_paths or []
        self._server = None
        self._handle = None
        self._bridge = None

    def _get_skill_paths(self) -> List[str]:
        paths: List[str] = []
        paths.extend(self._extra_skill_paths)
        if _BUILTIN_SKILLS_DIR.exists():
            paths.append(str(_BUILTIN_SKILLS_DIR))
        return paths

    def _init_bridge(self) -> None:
        """Initialize and connect the HTTP bridge."""
        from dcc_mcp_zbrush import api  # noqa: PLC0415
        from dcc_mcp_zbrush.bridge import ZBrushBridge  # noqa: PLC0415

        self._bridge = ZBrushBridge(
            host=self._zbrush_host,
            port=self._zbrush_port,
            api_key=self._api_key,
        )
        try:
            self._bridge.connect()
            api._bridge = self._bridge
            logger.info(
                "ZBrushBridge connected to http://%s:%d",
                self._zbrush_host,
                self._zbrush_port,
            )
        except Exception as exc:
            logger.warning(
                "ZBrushBridge could not connect: %s — "
                "skill calls will fail until ZBrush HTTP Server is running",
                exc,
            )

    def register_builtin_actions(self) -> None:
        """Discover and load all built-in ZBrush skills."""
        from dcc_mcp_core import McpHttpConfig, create_skill_manager  # noqa: PLC0415

        config = McpHttpConfig(port=self._port)
        extra_paths = self._get_skill_paths()
        self._server = create_skill_manager(
            "zbrush",
            config=config,
            extra_paths=extra_paths or None,
            dcc_name="zbrush",
        )
        logger.info("ZBrushMcpServer: registered skills from %d path(s)", len(extra_paths))

    def start(self) -> Any:
        """Start the MCP HTTP server and connect the bridge."""
        self._init_bridge()
        if self._server is None:
            self.register_builtin_actions()
        self._handle = self._server.start()
        logger.info("ZBrushMcpServer started at %s", self._handle.mcp_url())
        return self._handle

    def stop(self) -> None:
        """Stop the MCP HTTP server and disconnect the bridge."""
        if self._handle is not None:
            self._handle.shutdown()
            self._handle = None
        if self._bridge is not None:
            self._bridge.disconnect()
            self._bridge = None
        logger.info("ZBrushMcpServer stopped")


def start_server(
    port: int = 8765,
    zbrush_host: str = "localhost",
    zbrush_port: int = 8080,
    api_key: Optional[str] = None,
    extra_skill_paths: Optional[List[str]] = None,
) -> Any:
    """Start the module-level singleton MCP server for ZBrush."""
    global _server_instance  # noqa: PLW0603
    with _server_lock:
        if _server_instance is None:
            _server_instance = ZBrushMcpServer(
                port=port,
                zbrush_host=zbrush_host,
                zbrush_port=zbrush_port,
                api_key=api_key,
                extra_skill_paths=extra_skill_paths,
            )
        return _server_instance.start()


def stop_server() -> None:
    """Stop the module-level singleton MCP server."""
    global _server_instance  # noqa: PLW0603
    with _server_lock:
        if _server_instance is not None:
            _server_instance.stop()
            _server_instance = None
