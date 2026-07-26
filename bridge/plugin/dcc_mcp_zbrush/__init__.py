"""Auto-start dcc-mcp-zbrush when ZBrush loads this plugin directory.

Install by copying the whole ``bridge/plugin/dcc_mcp_zbrush`` folder into a
``ZBRUSH_PLUGIN_PATH`` entry or by adding this repo's ``src`` tree to
``PYTHONPATH``.

Registers a top-level DCC MCP palette (PIP-2905):
  dcc_mcp_copy_instance_id()  — Copy instance UUID to clipboard
  dcc_mcp_show_server_info()  — Show server status dialog
  dcc_mcp_show_about()        — Show About DCC MCP dialog
"""

from __future__ import annotations

import os
from typing import Any


def _autostart_enabled() -> bool:
    raw = os.environ.get("DCC_MCP_ZBRUSH_AUTOSTART", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


# ── Unified menu actions (PIP-2905) ────────────────────────────────────────


def dcc_mcp_copy_instance_id() -> None:
    """Copy the DCC MCP instance UUID to the system clipboard."""
    try:
        from dcc_mcp_zbrush._menu import copy_instance_id

        copy_instance_id()
    except ImportError:
        print("dcc-mcp-zbrush is not installed — cannot copy instance ID")  # noqa: T201


def dcc_mcp_show_server_info() -> None:
    """Show DCC MCP server information."""
    try:
        from dcc_mcp_zbrush._menu import show_server_info

        show_server_info()
    except ImportError:
        print("dcc-mcp-zbrush is not installed — cannot show server info")  # noqa: T201


def dcc_mcp_show_about() -> None:
    """Show About DCC MCP dialog."""
    try:
        from dcc_mcp_zbrush._menu import show_about

        show_about()
    except ImportError:
        print("dcc-mcp-zbrush is not installed — cannot show about")  # noqa: T201


def _on_copy_instance_id(_sender: str) -> None:
    dcc_mcp_copy_instance_id()


def _on_show_server_info(_sender: str) -> None:
    dcc_mcp_show_server_info()


def _on_show_about(_sender: str) -> None:
    dcc_mcp_show_about()


def install_menu(zbc: Any = None) -> bool:
    """Install the top-level DCC MCP palette through the official SDK."""
    if zbc is None:
        import zbrush.commands as zbc  # noqa: PLC0415

    if not zbc.exists("DCC MCP") and not zbc.add_palette("DCC MCP", docking_bar=1):
        return False
    actions = (
        ("Copy Instance ID", "Copy the DCC MCP instance UUID to the clipboard.", _on_copy_instance_id),
        ("Server Info", "Show DCC MCP server and runtime information.", _on_show_server_info),
        ("About DCC MCP", "Show adapter and ZBrush version information.", _on_show_about),
    )
    for label, info, callback in actions:
        item_path = f"DCC MCP:{label}"
        if not zbc.exists(item_path) and not zbc.add_button(item_path, info, callback):
            return False
    return True


# ── Autostart ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not install_menu():
        print("dcc-mcp-zbrush — failed to register DCC MCP palette")  # noqa: T201
    if _autostart_enabled():
        import dcc_mcp_zbrush  # noqa: PLC0415

        dcc_mcp_zbrush.start_server(mode="embedded")
