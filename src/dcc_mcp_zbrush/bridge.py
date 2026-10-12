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
DEFAULT_TIMEOUT_SEC = 610.0
PING_TIMEOUT_SEC = 3.0

STATUS_READY = "ready"
STATUS_BUSY = "busy"
STATUS_BLOCKED = "blocked"
STATUS_UNREACHABLE = "unreachable"

# Nothing listening on the port: a missing or dead plugin, not a blocked
# main thread. Distinguishing these keeps troubleshooting pointed the right way.
_UNREACHABLE_ERRORS = (ConnectionRefusedError, ConnectionResetError)

_BLOCKED_DETAIL = (
    "ZBrush is not executing Python: the main thread is inside a native call "
    "and no bridge request can be serviced. An open ZBrush Note has this "
    "signature, but so does any long native operation, so this is not proof "
    "of a dialog."
)


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


class ZBrushBlockedError(RuntimeError):
    """Raised when ZBrush does not answer a ping within the probe timeout.

    Carries the ``probe()`` verdict in :attr:`detail` so callers can report the
    classification without re-parsing the message.
    """

    def __init__(self, message: str, *, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.detail = dict(detail or {})


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
            verdict = self.probe()
            if verdict["status"] == STATUS_BLOCKED:
                raise ZBrushBlockedError(verdict["message"], detail=verdict)
            if verdict["status"] == STATUS_UNREACHABLE:
                raise ZBrushNotAvailableError(
                    f"No ZBrush socket plugin listening at {self.endpoint}: {verdict.get('error')}"
                )
            self._connected = True
            logger.info("SocketBridge connected to %s", self.endpoint)
        except (ZBrushBlockedError, ZBrushNotAvailableError):
            raise
        except Exception as exc:
            raise ZBrushNotAvailableError(f"Cannot connect to ZBrush socket plugin at {self.endpoint}: {exc}") from exc

    def probe(self, timeout: float = PING_TIMEOUT_SEC) -> Dict[str, Any]:
        """Classify ZBrush's ability to service a request right now.

        Pings with a short timeout instead of waiting out the full request
        timeout, so a stalled host surfaces as an explicit verdict rather than a
        silent hang. Returns one of:

        * ``ready``       — answered, idle.
        * ``busy``        — answered, a request is in flight. Servable.
        * ``blocked``     — TCP reached a listening plugin but no answer arrived
          within ``timeout``; the main thread is inside a native call.
        * ``unreachable`` — nothing is listening. This is a plugin/installation
          problem, not a blocked main thread, and must not be reported as one.
        """
        payload = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
        try:
            raw = self._send(json.dumps(payload).encode("utf-8"), timeout=timeout)
            message = json.loads(raw.decode("utf-8"))
        except _UNREACHABLE_ERRORS as exc:
            return {
                "status": STATUS_UNREACHABLE,
                "message": (
                    f"No ZBrush socket plugin is listening at {self.endpoint}. "
                    "Install bridge/plugin/mcp_socket_bridge.py in ZBrush and restart."
                ),
                "endpoint": self.endpoint,
                "timeout_sec": timeout,
                "error": f"{type(exc).__name__}: {exc}",
            }
        except (TimeoutError, socket.timeout, json.JSONDecodeError, ZBrushBridgeError) as exc:
            return {
                "status": STATUS_BLOCKED,
                "message": _BLOCKED_DETAIL,
                "endpoint": self.endpoint,
                "timeout_sec": timeout,
                "error": f"{type(exc).__name__}: {exc}",
            }
        except OSError as exc:
            return {
                "status": STATUS_BLOCKED,
                "message": _BLOCKED_DETAIL,
                "endpoint": self.endpoint,
                "timeout_sec": timeout,
                "error": f"{type(exc).__name__}: {exc}",
            }

        result = message.get("result")
        if not isinstance(result, dict):
            return {
                "status": STATUS_BLOCKED,
                "message": _BLOCKED_DETAIL,
                "endpoint": self.endpoint,
                "timeout_sec": timeout,
                "error": f"Unexpected ping result: {result!r}",
            }

        busy = bool(result.get("busy"))
        active_method = result.get("active_method")
        if busy:
            return {
                "status": STATUS_BUSY,
                "message": f"ZBrush is servicing {active_method or 'a request'}",
                "endpoint": self.endpoint,
                "active_method": active_method,
                "timeout_sec": timeout,
            }
        return {
            "status": STATUS_READY,
            "message": "ZBrush is idle and can service requests",
            "endpoint": self.endpoint,
            "active_method": None,
            "timeout_sec": timeout,
        }

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def call(self, method: str, **params: Any) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            raw = self._send(json.dumps(payload).encode("utf-8"))
        except OSError:
            self._connected = False
            raise
        try:
            message = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ZBrushBridgeError(f"Invalid JSON from ZBrush plugin: {raw!r}") from exc

        if "error" in message:
            err = message["error"]
            if not isinstance(err, dict):
                raise ZBrushBridgeError(str(err))
            raise ZBrushBridgeError(
                str(err.get("message", err)),
                code=err.get("code") if isinstance(err.get("code"), int) else None,
                data=err.get("data") if isinstance(err.get("data"), dict) else None,
            )
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

    def _send(self, data: bytes, timeout: Optional[float] = None) -> bytes:
        # The timeout is a parameter, never instance state: probes and normal
        # calls run concurrently, and temporarily rewriting self._timeout would
        # let one path hand the other a 3-second deadline (or leak it for good).
        with socket.create_connection(
            (self._host, self._port),
            timeout=self._timeout if timeout is None else timeout,
        ) as sock:
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
