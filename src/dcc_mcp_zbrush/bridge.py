"""ZBrushBridge — HTTP client for the ZBrush 2024+ built-in HTTP server.

ZBrush HTTP API
---------------
ZBrush 2024+ exposes a REST API on a configurable port (default 8080).

Key endpoints:
  POST /api/zscript   — Execute ZScript code
  GET  /api/info      — Get ZBrush version and status
  GET  /api/tools     — List available ZTools
  POST /api/export    — Export mesh to file

Authentication:
  Optional API key via X-ZBrush-API-Key header (configurable in ZBrush prefs).

Request/Response format:
  Content-Type: application/json
  {
    "code": "ZScript code here",
    "timeout": 30000
  }

Response:
  {
    "success": true,
    "output": "...",
    "error": null
  }
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8080
DEFAULT_TIMEOUT_SEC = 30.0


class ZBrushBridgeError(RuntimeError):
    """Raised when a ZBrush API call fails."""


class ZBrushNotAvailableError(ConnectionError):
    """Raised when ZBrush HTTP server is not reachable."""


class ZBrushBridge:
    """HTTP bridge to ZBrush 2024+ built-in HTTP server.

    Usage::

        bridge = ZBrushBridge(host="localhost", port=8080)
        info = bridge.get_info()
        result = bridge.execute_zscript("[IButton,/Zplugin/Button1,Run,]")

    Or with context manager::

        with ZBrushBridge() as bridge:
            result = bridge.execute_zscript("ISet, 0, ToolLayers:LayerBake")
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        api_key: Optional[str] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._api_key = api_key
        self._client = None

    @property
    def base_url(self) -> str:
        """Base URL for the ZBrush HTTP API."""
        return f"http://{self._host}:{self._port}"

    def connect(self) -> None:
        """Initialize the HTTP client.

        Raises:
            ZBrushNotAvailableError: If ZBrush HTTP server is not reachable.
        """
        try:
            import httpx  # noqa: PLC0415
            headers = {}
            if self._api_key:
                headers["X-ZBrush-API-Key"] = self._api_key
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=self._timeout,
            )
            # Verify ZBrush is running
            self._client.get("/api/info").raise_for_status()
            logger.info("ZBrushBridge connected to %s", self.base_url)
        except ImportError as exc:
            raise ZBrushNotAvailableError("httpx is required: pip install httpx") from exc
        except Exception as exc:
            raise ZBrushNotAvailableError(
                f"Cannot connect to ZBrush HTTP server at {self.base_url}: {exc}\n"
                "Ensure ZBrush 2024+ is running with HTTP Server enabled "
                "(Preferences > Network > Enable HTTP Server)."
            ) from exc

    def disconnect(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None
        logger.info("ZBrushBridge disconnected")

    def is_connected(self) -> bool:
        """Return True if the HTTP client is initialized."""
        return self._client is not None

    def get_info(self) -> Dict[str, Any]:
        """Get ZBrush version and status information.

        Returns:
            dict with version, status, and capabilities.
        """
        if self._client is None:
            raise ZBrushNotAvailableError("Not connected. Call connect() first.")
        # TODO: implement actual HTTP call
        raise NotImplementedError("ZBrushBridge.get_info() — HTTP implementation pending")

    def execute_zscript(self, code: str, timeout_ms: int = 30000) -> Dict[str, Any]:
        """Execute ZScript code in ZBrush.

        Args:
            code: ZScript code to execute.
            timeout_ms: Execution timeout in milliseconds.

        Returns:
            dict: {"success": bool, "output": str | None, "error": str | None}
        """
        if self._client is None:
            raise ZBrushNotAvailableError("Not connected. Call connect() first.")
        # TODO: implement actual HTTP call
        raise NotImplementedError(
            "ZBrushBridge.execute_zscript() — HTTP implementation pending. "
            f"Would POST to {self.base_url}/api/zscript with code: {code[:50]}..."
        )

    def list_tools(self) -> list:
        """List available ZTools in ZBrush."""
        if self._client is None:
            raise ZBrushNotAvailableError("Not connected. Call connect() first.")
        raise NotImplementedError("ZBrushBridge.list_tools() — HTTP implementation pending")

    def export_mesh(self, path: str, format: str = "obj") -> Dict[str, Any]:  # noqa: A002
        """Export the active ZTool mesh to a file.

        Args:
            path: Output file path.
            format: Export format ("obj", "fbx", "ztl", "stl").
        """
        if self._client is None:
            raise ZBrushNotAvailableError("Not connected. Call connect() first.")
        raise NotImplementedError("ZBrushBridge.export_mesh() — HTTP implementation pending")

    def __enter__(self) -> "ZBrushBridge":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.disconnect()
