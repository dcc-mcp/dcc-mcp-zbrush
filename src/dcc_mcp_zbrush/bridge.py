"""SocketBridge — TCP JSON bridge to a ZBrush in-process plugin.

Use sidecar mode when the MCP server runs outside ZBrush and forwards tool
calls to ``bridge/plugin/mcp_socket_bridge.py`` running inside ZBrush.
"""

from __future__ import annotations

import json
import logging
import math
import socket
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
DEFAULT_TIMEOUT_SEC = 610.0
DEFAULT_PROBE_TIMEOUT_SEC = 2.0


class ZBrushBridgeError(RuntimeError):
    """Raised when a bridge RPC call fails."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = dict(data or {})


class ZBrushNotAvailableError(ConnectionError):
    """Raised when the ZBrush socket plugin is not reachable."""


class SocketBridge:
    """Minimal TCP JSON-RPC client for the ZBrush MCP socket plugin."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        probe_timeout: float = DEFAULT_PROBE_TIMEOUT_SEC,
    ) -> None:
        self._host = host
        self._port = port
        if not all(math.isfinite(value) and value > 0 for value in (timeout, probe_timeout)):
            raise ValueError("Bridge timeouts must be finite positive numbers")
        self._timeout = timeout
        self._probe_timeout = min(timeout, probe_timeout)
        self._connected = False

    @property
    def endpoint(self) -> str:
        return f"{self._host}:{self._port}"

    def connect(self) -> None:
        try:
            self.call("ping")
            self._connected = True
            logger.info("SocketBridge connected to %s", self.endpoint)
        except ZBrushBridgeError:
            raise
        except Exception as exc:
            raise ZBrushNotAvailableError(f"Cannot connect to ZBrush socket plugin at {self.endpoint}: {exc}") from exc

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def call(self, method: str, **params: Any) -> Any:
        if method != "ping":
            # A TCP connect is insufficient: a native modal can hold the GIL
            # while the kernel still accepts connections. Never send host work
            # when this short, SDK-free probe cannot get a response.
            status = self.call("ping")
            if status.get("busy"):
                raise ZBrushBridgeError(
                    f"ZBrush bridge is busy with {status.get('active_method')}",
                    code=-32001,
                    data={"retryable": True, "active_method": status.get("active_method"), "submitted": False},
                )
        timeout = self._probe_timeout if method == "ping" else self._timeout
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        if method != "ping":
            payload["deadline_unix_ms"] = (time.time() + timeout) * 1000
        try:
            raw = self._send(json.dumps(payload).encode("utf-8"), timeout=timeout)
        except OSError as exc:
            self._connected = False
            if method == "ping":
                state = "unresponsive" if isinstance(exc, TimeoutError) else "unavailable"
                raise ZBrushBridgeError(
                    f"ZBrush bridge is {state}; it may be busy with a native operation or dialog. "
                    "Check ZBrush before retrying.",
                    code=-32003,
                    data={"retryable": True, "submitted": False, "state": state},
                ) from exc
            raise ZBrushBridgeError(
                "Lost the ZBrush response; the operation may have started. Inspect the scene before retrying.",
                code=-32002,
                data={"retryable": False, "execution_state": "unknown", "active_method": method},
            ) from exc
        try:
            message = json.loads(raw.decode("utf-8"))
            if not isinstance(message, dict) or not ({"result", "error"} & message.keys()):
                raise ValueError("Invalid JSON-RPC response object")
        except (UnicodeDecodeError, ValueError) as exc:
            self._connected = False
            if method == "ping":
                raise ZBrushBridgeError(
                    "ZBrush plugin returned an invalid health response; host work was not submitted",
                    code=-32003,
                    data={"retryable": True, "submitted": False, "state": "invalid_response"},
                ) from exc
            raise ZBrushBridgeError(
                "Invalid ZBrush response; the operation outcome is unknown. Inspect the scene before retrying.",
                code=-32002,
                data={"retryable": False, "execution_state": "unknown", "active_method": method},
            ) from exc

        if "error" in message:
            err = message["error"]
            if not isinstance(err, dict):
                raise ZBrushBridgeError(str(err))
            raise ZBrushBridgeError(
                str(err.get("message", err)),
                code=err.get("code") if isinstance(err.get("code"), int) else None,
                data=err.get("data") if isinstance(err.get("data"), dict) else None,
            )
        result = message.get("result")
        if method == "ping":
            if not isinstance(result, dict) or result.get("ok") is not True:
                self._connected = False
                raise ZBrushBridgeError(
                    "ZBrush plugin did not return a valid health response",
                    code=-32003,
                    data={"retryable": True, "submitted": False, "state": "invalid_response"},
                )
            self._connected = True
        return result

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

    def _send(self, data: bytes, *, timeout: Optional[float] = None) -> bytes:
        budget = self._timeout if timeout is None else timeout
        deadline = time.monotonic() + budget
        with socket.create_connection((self._host, self._port), timeout=budget) as sock:
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            sock.sendall(data + b"\n")
            chunks: list[bytes] = []
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("ZBrush response deadline exceeded")
                sock.settimeout(remaining)
                part = sock.recv(65536)
                if not part:
                    raise ConnectionError("ZBrush connection closed before a complete response")
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
