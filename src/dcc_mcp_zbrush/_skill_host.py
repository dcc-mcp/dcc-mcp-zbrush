"""Shared runtime helpers for ZBrush skill scripts."""

from __future__ import annotations

import ntpath
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, TypeVar

_T = TypeVar("_T")


@contextmanager
def quiet_ui_actions(zbc: Any) -> Iterator[None]:
    """Suppress scripted UI action feedback and always restore it."""
    zbc.show_actions(0)
    try:
        yield
    finally:
        zbc.show_actions(1)


def run_quiet_ui(zbc: Any, action: Callable[[], None]) -> None:
    """Run file-oriented UI commands without action drawing or UI redraws."""
    with quiet_ui_actions(zbc):
        zbc.freeze(action)


def subtool_name_from_path(path: str) -> str:
    """Return the leaf name from either Windows or slash-separated ZBrush paths."""
    return ntpath.basename(path)


def run_in_zbrush(embedded: Callable[[Any], _T], bridge_method: str, **bridge_params: Any) -> _T:
    """Execute ``embedded(zbc)`` in-process or forward to the sidecar socket plugin."""
    from dcc_mcp_zbrush._version_probe import is_zbrush_available  # noqa: PLC0415

    if is_zbrush_available():
        from dcc_mcp_zbrush.api import import_zbc  # noqa: PLC0415

        return embedded(import_zbc())

    from dcc_mcp_zbrush.api import get_bridge  # noqa: PLC0415

    result = get_bridge().call(bridge_method, **bridge_params)
    if isinstance(result, dict) and result.get("success") is False:
        raise RuntimeError(result.get("error", "bridge call failed"))
    return result  # type: ignore[return-value]


def subtool_status_flags(status: int) -> Dict[str, bool]:
    """Decode common ``get_subtool_status`` bitmask flags."""
    return {
        "visible": bool(status & 0x01),
        "locked": bool(status & 0x02),
    }
