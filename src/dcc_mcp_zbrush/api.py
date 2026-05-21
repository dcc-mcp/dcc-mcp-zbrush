"""High-level ZBrush skill authoring helpers."""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, List, Optional, TypeVar

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

_bridge = None


class ZBrushNotAvailableError(RuntimeError):
    """Raised when ZBrush SDK or the sidecar bridge is unavailable."""


def set_bridge(bridge: Any) -> None:
    """Set the module-level sidecar bridge instance."""
    global _bridge  # noqa: PLW0603
    _bridge = bridge


def is_zbrush_available() -> bool:
    """Return True when embedded SDK or a connected sidecar bridge is available."""
    from dcc_mcp_zbrush._version_probe import is_zbrush_available as sdk_available  # noqa: PLC0415

    if sdk_available():
        return True
    return _bridge is not None and _bridge.is_connected()


def get_bridge():
    """Return the sidecar bridge when running outside ZBrush."""
    if _bridge is None or not _bridge.is_connected():
        raise ZBrushNotAvailableError(
            "ZBrush sidecar bridge is not connected. "
            "Install bridge/plugin/mcp_socket_bridge.py in ZBrush or run embedded mode."
        )
    return _bridge


def import_zbc():
    """Import ``zbrush.commands`` when running inside ZBrush."""
    try:
        import zbrush.commands as zbc  # noqa: PLC0415
    except ImportError as exc:
        raise ZBrushNotAvailableError(
            "zbrush.commands is unavailable. Run this skill inside ZBrush 2026.1+ "
            "or use sidecar mode with the socket plugin."
        ) from exc
    return zbc


def zb_success(message: str, *, prompt: Optional[str] = None, **context: Any) -> dict:
    from dcc_mcp_core.skill import skill_success  # noqa: PLC0415

    return skill_success(message, prompt=prompt, **context)


def zb_error(
    message: str,
    error: str,
    *,
    prompt: Optional[str] = None,
    possible_solutions: Optional[List[str]] = None,
    **context: Any,
) -> dict:
    from dcc_mcp_core.skill import skill_error  # noqa: PLC0415

    return skill_error(message, error, prompt=prompt, possible_solutions=possible_solutions, **context)


def zb_from_exception(
    exc: BaseException,
    message: Optional[str] = None,
    **context: Any,
) -> dict:
    from dcc_mcp_core.skill import skill_exception  # noqa: PLC0415

    return skill_exception(exc, message=message, **context)


def with_zbrush(func: _F) -> _F:
    """Decorator: standard ZBrush error handling for skill handlers."""

    @functools.wraps(func)
    def wrapper(**kwargs: Any) -> dict:
        try:
            return func(**kwargs)
        except ZBrushNotAvailableError as exc:
            return zb_error(
                "ZBrush is not available in this environment",
                repr(exc),
                prompt="Start ZBrush 2026.1+ with the MCP plugin, or run sidecar mode.",
                possible_solutions=[
                    "Install dcc-mcp-zbrush into ZBrush PYTHONPATH / ZBRUSH_PLUGIN_PATH",
                    "Copy bridge/plugin/mcp_socket_bridge.py into ZStartup/ZPlugs64",
                    "Set DCC_MCP_ZBRUSH_MODE=embedded when running inside ZBrush",
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Skill execution failed: %s", func.__name__)
            return zb_from_exception(exc)

    return wrapper  # type: ignore[return-value]
