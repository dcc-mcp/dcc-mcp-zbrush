"""Unified DCC MCP menu helpers for ZBrush (PIP-2794 / PIP-2905).

Provides Copy Instance ID, Server Info, and About DCC MCP actions.
Works in embedded mode (server in-process) and sidecar mode (bridge plugin).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_GATEWAY_PORT = 9765


# ── server helpers ──────────────────────────────────────────────────────────


def _resolve_instance_id() -> Optional[str]:
    """Return the DCC MCP instance UUID from the running server, if available."""
    try:
        from dcc_mcp_zbrush.server import get_server  # noqa: PLC0415

        srv = get_server()
    except Exception:
        srv = None

    if srv is not None:
        # Try server object attribute first (DccServerBase subclass)
        instance_id = getattr(srv, "instance_id", None)
        if instance_id:
            return str(instance_id)
        # Try config attribute
        config = getattr(srv, "_config", None)
        if config is not None:
            instance_id = getattr(config, "instance_id", None)
            if instance_id:
                return str(instance_id)
        # Try Rust core server attribute
        core = getattr(srv, "_server", None)
        if core is not None:
            instance_id = getattr(core, "instance_id", None)
            if instance_id:
                return str(instance_id)

    # Fallback: environment variable set by bootstrap
    env_id = os.environ.get("DCC_MCP_INSTANCE_ID", "").strip()
    if env_id:
        return env_id

    return None


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


def _set_clipboard_text(text: str) -> None:
    """Set the system clipboard text, trying PySide2 then PySide6."""
    for binding in ("PySide2", "PySide6"):
        try:
            mod = __import__(binding)
            app = mod.QtWidgets.QApplication.instance()
            if app is not None:
                app.clipboard().setText(text)
                return
        except Exception:
            continue
    raise RuntimeError("Unable to access system clipboard (no PySide binding available)")


# ── zbrush helpers ──────────────────────────────────────────────────────────


def _is_inside_zbrush() -> bool:
    """Return True when running inside the ZBrush embedded Python VM."""
    try:
        import zbrush.commands  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def _zbrush_version() -> str:
    """Return the ZBrush version string, or 'unknown'."""
    try:
        from dcc_mcp_zbrush._version_probe import get_zbrush_version_string  # noqa: PLC0415

        return get_zbrush_version_string()
    except Exception:
        return "unknown"


def _show_message(title: str, message: str) -> None:
    """Show a message to the user — PySide2 dialog inside ZBrush, print outside."""
    if _is_inside_zbrush():
        try:
            from PySide2.QtWidgets import QMessageBox  # noqa: PLC0415

            QMessageBox.information(None, title, message)
            return
        except Exception:
            pass
    # Fallback for non-Qt sessions
    print(f"[dcc-mcp-zbrush] {title}\n{message}")  # noqa: T201


# ── menu actions ────────────────────────────────────────────────────────────


def copy_instance_id() -> None:
    """Copy the DCC MCP instance UUID to the system clipboard."""
    instance_id = _resolve_instance_id()
    if not instance_id:
        _show_message(
            "DCC MCP — Copy Instance ID",
            "Instance ID not available. Is the server running?",
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
        "DCC MCP — AI-driven DCC automation.\n"
        "https://github.com/dcc-mcp/dcc-mcp-zbrush"
    )
    _show_message("About DCC MCP", msg)
