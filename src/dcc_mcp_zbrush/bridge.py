"""SocketBridge — TCP JSON bridge to a ZBrush in-process plugin.

Use sidecar mode when the MCP server runs outside ZBrush and forwards tool
calls to ``bridge/plugin/mcp_socket_bridge.py`` running inside ZBrush.
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
DEFAULT_TIMEOUT_SEC = 120.0


class ZBrushBridgeError(RuntimeError):
    """Raised when a bridge RPC call fails."""


class ZBrushNotAvailableError(ConnectionError):
    """Raised when the ZBrush socket plugin is not reachable."""


class SocketBridge:
    """Minimal TCP JSON-RPC client for the ZBrush MCP socket plugin."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._connected = False

    @property
    def endpoint(self) -> str:
        return f"{self._host}:{self._port}"

    def connect(self) -> None:
        try:
            self.call("ping")
            self._connected = True
            logger.info("SocketBridge connected to %s", self.endpoint)
        except Exception as exc:
            raise ZBrushNotAvailableError(f"Cannot connect to ZBrush socket plugin at {self.endpoint}: {exc}") from exc

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def call(self, method: str, **params: Any) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        raw = self._send(json.dumps(payload).encode("utf-8"))
        try:
            message = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ZBrushBridgeError(f"Invalid JSON from ZBrush plugin: {raw!r}") from exc

        if "error" in message:
            err = message["error"]
            raise ZBrushBridgeError(str(err.get("message", err)))
        return message.get("result")

    def execute_python(self, code: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = self.call("execute_python", code=code, context=context or {})
        if not isinstance(result, dict):
            raise ZBrushBridgeError(f"Unexpected execute_python result: {result!r}")
        return result

    def get_session_info(self) -> Dict[str, Any]:
        result = self.call("get_session_info")
        if not isinstance(result, dict):
            raise ZBrushBridgeError(f"Unexpected get_session_info result: {result!r}")
        return result

    def get_scene_info(self) -> Dict[str, Any]:
        result = self.call("get_scene_info")
        if not isinstance(result, dict):
            raise ZBrushBridgeError(f"Unexpected get_scene_info result: {result!r}")
        return result

    def _send(self, data: bytes) -> bytes:
        with socket.create_connection((self._host, self._port), timeout=self._timeout) as sock:
            sock.sendall(data + b"\n")
            chunks: list[bytes] = []
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                chunks.append(part)
                if b"\n" in part:
                    break
        return b"".join(chunks).split(b"\n", 1)[0]

    def __enter__(self) -> "SocketBridge":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.disconnect()


# Backward-compatible alias from the pre-alpha HTTP scaffold.
ZBrushBridge = SocketBridge
