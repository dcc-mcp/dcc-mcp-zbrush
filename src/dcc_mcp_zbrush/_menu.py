"""Unified DCC MCP menu helpers for ZBrush (PIP-2794 / PIP-2905).

Provides Copy Instance ID, Server Info, and About DCC MCP actions.
Works in embedded mode (server in-process) and sidecar mode (bridge plugin).
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

_DEFAULT_GATEWAY_PORT = 9765
_MENU_PATH = "DCC MCP"


# ── server helpers ──────────────────────────────────────────────────────────


def _resolve_instance_id() -> Optional[str]:
    """Return the public instance UUID from the running server, if available.

    Instance identity is owned by dcc-mcp-core.  Do not reconstruct it from
    registry files or private server/config attributes here.
    """
    try:
        from dcc_mcp_zbrush.server import get_server  # noqa: PLC0415

        srv = get_server()
    except Exception:
        return None

    if srv is None:
        return None

    try:
        instance_id = srv.instance_id
    except (AttributeError, RuntimeError):
        return None
    value = str(instance_id).strip() if instance_id is not None else ""
    return value or None


def _server_url() -> str:
    """Return the MCP URL of the running server, or an empty string."""
    try:
        from dcc_mcp_zbrush.server import get_server  # noqa: PLC0415

        srv = get_server()
    except Exception:
        return ""

    if srv is None:
        return ""

    try:
        return str(srv.mcp_url)
    except Exception:
        return ""


# ── clipboard ───────────────────────────────────────────────────────────────


def _qt_widgets_modules() -> Iterator[Any]:
    """Yield importable QtWidgets modules without relying on package side effects."""
    for binding in ("PySide2", "PySide6"):
        try:
            yield importlib.import_module(f"{binding}.QtWidgets")
        except Exception:
            continue


def _set_clipboard_text(text: str) -> None:
    """Set the system clipboard text, trying PySide2 then PySide6."""
    for widgets in _qt_widgets_modules():
        try:
            app = widgets.QApplication.instance()
            if app is not None:
                app.clipboard().setText(text)
                return
        except Exception:
            continue
    raise RuntimeError("Unable to access system clipboard (no PySide binding available)")


# ── zbrush helpers ──────────────────────────────────────────────────────────


def _zbrush_commands() -> Any:
    """Return the official ZBrush command module when running in the host."""
    try:
        import zbrush.commands as zbc  # noqa: PLC0415
    except ImportError:
        return None
    return zbc


def _is_inside_zbrush() -> bool:
    """Return True when running inside the ZBrush embedded Python VM."""
    return _zbrush_commands() is not None


def _zbrush_version() -> str:
    """Return the ZBrush version string, or 'unknown'."""
    try:
        from dcc_mcp_zbrush._version_probe import get_zbrush_version_string  # noqa: PLC0415

        return get_zbrush_version_string()
    except Exception:
        return "unknown"


def _show_message(title: str, message: str) -> None:
    """Show a native ZBrush message, then fall back through Qt bindings."""
    zbc = _zbrush_commands()
    if zbc is not None:
        try:
            zbc.message_ok(message, title)
            return
        except Exception:
            pass
    for widgets in _qt_widgets_modules():
        try:
            widgets.QMessageBox.information(None, title, message)
            return
        except Exception:
            continue
    print(f"[dcc-mcp-zbrush] {title}\n{message}")  # noqa: T201


# ── menu actions ────────────────────────────────────────────────────────────


def copy_instance_id() -> None:
    """Copy the DCC MCP instance UUID to the system clipboard."""
    instance_id = _resolve_instance_id()
    if not instance_id:
        _show_message(
            "DCC MCP — Copy Instance ID",
            "No registered instance ID is available. Start the MCP server with "
            "gateway registration enabled, or run `dcc-mcp-cli list` from a terminal.",
        )
        return
    try:
        _set_clipboard_text(instance_id)
    except RuntimeError as exc:
        _show_message("DCC MCP — Copy Instance ID", str(exc))
        return
    logger.info("DCC MCP: Instance ID copied to clipboard: %s", instance_id)
    if _is_inside_zbrush():
        print(f"DCC MCP: Instance ID copied to clipboard: {instance_id}")  # noqa: T201


def show_server_info() -> None:
    """Show server status information in a dialog."""
    instance_id = _resolve_instance_id()
    instance_url = _server_url()

    zbrush_version = _zbrush_version()

    gateway_port_str = os.environ.get("DCC_MCP_GATEWAY_PORT", str(_DEFAULT_GATEWAY_PORT))
    try:
        gp = int(gateway_port_str)
    except ValueError:
        gp = _DEFAULT_GATEWAY_PORT
    gateway_display = "disabled" if gp <= 0 else str(gp)

    core_version = "unknown"
    try:
        from dcc_mcp_core.server_base import _package_version  # noqa: PLC0415

        core_version = _package_version() or "unknown"
    except Exception:
        pass

    from dcc_mcp_zbrush.__version__ import __version__  # noqa: PLC0415

    msg = (
        f"Instance UUID: {instance_id or 'N/A'}\n"
        f"DCC: ZBrush {zbrush_version}\n"
        f"PID: {os.getpid()}\n"
        f"MCP URL: {instance_url or 'N/A'}\n"
        f"Gateway Port: {gateway_display}\n"
        f"Core Version: {core_version}\n"
        f"Adapter Version: {__version__}\n"
        f"Python: {sys.version.split()[0]}"
    )
    _show_message("DCC MCP — Server Info", msg)


def show_about() -> None:
    """Show about dialog with version information."""
    zbrush_version = _zbrush_version()

    from dcc_mcp_zbrush.__version__ import __version__  # noqa: PLC0415

    msg = (
        f"dcc-mcp-zbrush v{__version__}\n"
        f"ZBrush {zbrush_version}\n"
        f"Python {sys.version.split()[0]}\n\n"
        "DCC MCP — shared infrastructure for DCC automation.\n"
        "https://github.com/dcc-mcp/dcc-mcp-zbrush"
    )
    _show_message("About DCC MCP", msg)


def _on_copy_instance_id(_sender: str) -> None:
    copy_instance_id()


def _on_show_server_info(_sender: str) -> None:
    show_server_info()


def _on_show_about(_sender: str) -> None:
    show_about()


def install_menu(zbc: Any = None) -> bool:
    """Install the top-level DCC MCP palette through the official ZBrush SDK."""
    zbc = zbc or _zbrush_commands()
    if zbc is None:
        return False

    if not zbc.exists(_MENU_PATH) and not zbc.add_palette(_MENU_PATH, docking_bar=1):
        return False

    actions = (
        ("Copy Instance ID", "Copy the DCC MCP instance UUID to the clipboard.", _on_copy_instance_id),
        ("Server Info", "Show DCC MCP server and runtime information.", _on_show_server_info),
        ("About DCC MCP", "Show adapter and ZBrush version information.", _on_show_about),
    )
    for label, info, callback in actions:
        item_path = f"{_MENU_PATH}:{label}"
        if not zbc.exists(item_path) and not zbc.add_button(item_path, info, callback):
            return False
    return True
