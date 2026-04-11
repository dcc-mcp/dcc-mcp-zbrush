"""dcc_mcp_zbrush.api — High-level ZBrush skill authoring helpers.

ZBrush skill scripts use ZBrushBridge to execute ZScript commands via
ZBrush's HTTP API instead of importing a Python module.

Key helpers
-----------
``zb_success(message, **context)``  — success result dict
``zb_error(message, error, **context)``  — failure result dict
``zb_from_exception(exc, ...)``  — exception to result dict
``get_bridge()``  — get the module-level bridge instance
``with_zbrush(func)``  — decorator for standard error handling

Typical usage in a skill script::

    from dcc_mcp_zbrush.api import zb_success, zb_error, get_bridge, with_zbrush

    @with_zbrush
    def list_tools(**kwargs) -> dict:
        bridge = get_bridge()
        tools = bridge.list_tools()
        return zb_success(f"Found {len(tools)} ZTools", tools=tools)

    def main(**kwargs):
        return list_tools(**kwargs)
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, List, Optional, TypeVar

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

# Module-level bridge singleton (set by ZBrushMcpServer on startup)
_bridge = None


class ZBrushNotAvailableError(ConnectionError):
    """Raised when the ZBrush HTTP bridge is not connected."""


def is_zbrush_available() -> bool:
    """Return True if the ZBrush bridge is connected."""
    return _bridge is not None and _bridge.is_connected()


def get_bridge():
    """Return the module-level ZBrushBridge instance.

    Raises:
        ZBrushNotAvailableError: If bridge is not connected.
    """
    if _bridge is None or not _bridge.is_connected():
        raise ZBrushNotAvailableError(
            "ZBrush bridge is not connected. "
            "Ensure ZBrush 2024+ is running with HTTP Server enabled "
            "and ZBrushMcpServer.start() has been called."
        )
    return _bridge


def zb_success(message: str, *, prompt: Optional[str] = None, **context: Any) -> dict:
    """Build a success result dict compatible with ActionResultModel."""
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
    """Build a failure result dict compatible with ActionResultModel."""
    from dcc_mcp_core.skill import skill_error  # noqa: PLC0415

    return skill_error(message, error, prompt=prompt, possible_solutions=possible_solutions, **context)


def zb_from_exception(
    exc: BaseException,
    message: Optional[str] = None,
    **context: Any,
) -> dict:
    """Build a failure result dict from a caught exception."""
    from dcc_mcp_core.skill import skill_exception  # noqa: PLC0415

    return skill_exception(exc, message=message, **context)


def with_zbrush(func: _F) -> _F:
    """Decorator: wrap a skill function with standard ZBrush error handling."""

    @functools.wraps(func)
    def wrapper(**kwargs: Any) -> dict:
        try:
            return func(**kwargs)
        except ZBrushNotAvailableError as exc:
            return zb_error(
                "ZBrush is not available — HTTP bridge not connected",
                repr(exc),
                prompt="Ensure ZBrush 2024+ is running with HTTP Server enabled.",
                possible_solutions=[
                    "Enable HTTP Server: Preferences > Network > Enable HTTP Server",
                    "Start ZBrush before launching the MCP server",
                    "Check port 8080 is not blocked by firewall",
                ],
            )
        except NotImplementedError as exc:
            return zb_error(
                "This feature is not yet implemented",
                repr(exc),
                prompt="This ZBrush bridge feature is in development — check the roadmap.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Skill execution failed: %s", func.__name__)
            return zb_from_exception(exc)

    return wrapper  # type: ignore[return-value]
