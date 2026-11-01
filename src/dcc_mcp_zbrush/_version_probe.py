"""Detect ZBrush Python SDK availability and version."""

from __future__ import annotations

from typing import Optional, Tuple

# Official ZBrush Python SDK (developers.maxon.net .../zbr_cmds_system.html):
#   zbrush_info(0) -> float: ZBrush version number (e.g. 2026.0, 2027.0)
#   zbrush_info(1) -> float: edition type, 0 = Demo, 1 = Beta, 2 = Full Version
# Index 1 is the EDITION, not a minor version component. Conflating the two used
# to report a Full-edition ZBrush 2027.0.1 as "2027.2" and failed host_identity.
ZBRUSH_INFO_VERSION = 0
ZBRUSH_INFO_EDITION = 1

ZBRUSH_EDITIONS = {0: "demo", 1: "beta", 2: "full"}


def is_zbrush_available() -> bool:
    """Return True when ``zbrush.commands`` can be imported (inside ZBrush)."""
    try:
        import zbrush.commands  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def format_sdk_version(value: float) -> str:
    """Format ``zbrush_info(0)`` as a bounded canonical version string.

    The SDK returns a small positive float such as ``2026.0`` or ``2027.1``.
    Keep its precision but guarantee at least two dot-separated components so
    the result stays a valid host-version shape (e.g. ``2027.0``).
    """
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return f"{text}.0" if "." not in text else text


def read_sdk_version(zbc) -> Tuple[str, int]:
    """Return ``(version_string, edition)`` from the official ZBrush SDK.

    ``version_string`` uses only ``zbrush_info(0)`` and preserves its precision;
    the edition type from ``zbrush_info(1)`` is returned separately and is never
    mixed into the version. Unknown/unreadable editions are reported as ``-1``.
    """
    version = format_sdk_version(zbc.zbrush_info(ZBRUSH_INFO_VERSION))
    try:
        edition = int(zbc.zbrush_info(ZBRUSH_INFO_EDITION))
    except (TypeError, ValueError):
        edition = -1
    return version, edition


def get_zbrush_version_string() -> str:
    """Return a human-readable ZBrush version or ``unknown``."""
    try:
        import zbrush.commands as zbc  # noqa: PLC0415

        return read_sdk_version(zbc)[0]
    except Exception:
        return "unknown"


def get_zbrush_edition() -> int:
    """Return the ZBrush edition type (0/1/2) or ``-1`` when unavailable."""
    try:
        import zbrush.commands as zbc  # noqa: PLC0415

        return read_sdk_version(zbc)[1]
    except Exception:
        return -1


def get_zbrush_version_tuple() -> Tuple[int, int]:
    """Return ``(major, minor)`` from the SDK version number (not the edition)."""
    version = get_zbrush_version_string()
    try:
        major_text, _, minor_text = version.partition(".")
        return int(major_text), int(minor_text)
    except (ValueError, TypeError):
        return 0, 0


def get_active_tool_path() -> Optional[str]:
    """Return the active tool path when running inside ZBrush."""
    try:
        import zbrush.commands as zbc  # noqa: PLC0415

        path = zbc.get_active_tool_path()
        return str(path) if path else None
    except Exception:
        return None
