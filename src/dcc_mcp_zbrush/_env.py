"""Environment variable helpers for dcc-mcp-zbrush."""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

ENV_PORT = "DCC_MCP_ZBRUSH_PORT"
ENV_GATEWAY_PORT = "DCC_MCP_GATEWAY_PORT"
ENV_MODE = "DCC_MCP_ZBRUSH_MODE"
ENV_SOCKET_HOST = "DCC_MCP_ZBRUSH_SOCKET_HOST"
ENV_SOCKET_PORT = "DCC_MCP_ZBRUSH_SOCKET_PORT"
ENV_AUTOSTART = "DCC_MCP_ZBRUSH_AUTOSTART"
ENV_ENABLE_GATEWAY_FAILOVER = "DCC_MCP_ZBRUSH_ENABLE_GATEWAY_FAILOVER"

DEFAULT_SOCKET_PORT = 9876


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def get_extra_skill_paths() -> list[str]:
    """Read ``DCC_MCP_ZBRUSH_SKILL_PATHS`` and ``DCC_MCP_SKILL_PATHS``."""
    sep = ";" if os.sep == "\\" else ":"
    paths: list[str] = []

    for env_var in ("DCC_MCP_ZBRUSH_SKILL_PATHS", "DCC_MCP_SKILL_PATHS"):
        raw = os.environ.get(env_var, "")
        if raw:
            for part in raw.split(sep):
                part = part.strip()
                if part:
                    paths.append(part)

    return paths


def resolve_mode(value: Optional[str] = None) -> str:
    """Return ``embedded`` or ``sidecar`` based on env / runtime detection."""
    if value:
        return value.strip().lower()
    raw = os.environ.get(ENV_MODE, "").strip().lower()
    if raw in ("embedded", "sidecar"):
        return raw
    from dcc_mcp_zbrush._version_probe import is_zbrush_available  # noqa: PLC0415

    return "embedded" if is_zbrush_available() else "sidecar"


def resolve_enable_gateway_failover(value: Optional[bool]) -> bool:
    if value is not None:
        return value
    raw = os.environ.get(ENV_ENABLE_GATEWAY_FAILOVER, "").strip()
    if raw:
        return _env_truthy(ENV_ENABLE_GATEWAY_FAILOVER)
    return True


def resolve_minimal_mode_enabled() -> bool:
    raw = os.environ.get("DCC_MCP_MINIMAL", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def resolve_socket_endpoint(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> tuple[str, int]:
    resolved_host = host or os.environ.get(ENV_SOCKET_HOST, "127.0.0.1")
    if port is not None:
        resolved_port = port
    else:
        raw = os.environ.get(ENV_SOCKET_PORT, str(DEFAULT_SOCKET_PORT)).strip()
        try:
            resolved_port = int(raw)
        except ValueError:
            logger.warning("Invalid %s=%r; using %d", ENV_SOCKET_PORT, raw, DEFAULT_SOCKET_PORT)
            resolved_port = DEFAULT_SOCKET_PORT
    return resolved_host, resolved_port
