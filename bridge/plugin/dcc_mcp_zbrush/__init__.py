"""Auto-start dcc-mcp-zbrush when ZBrush loads this plugin directory.

Install by copying the whole ``bridge/plugin/dcc_mcp_zbrush`` folder into a
``ZBRUSH_PLUGIN_PATH`` entry or by adding this repo's ``src`` tree to
``PYTHONPATH``.

Also exposes unified menu actions (PIP-2905):
  dcc_mcp_copy_instance_id()  — Copy instance UUID to clipboard
  dcc_mcp_show_server_info()  — Show server status dialog
  dcc_mcp_show_about()        — Show About DCC MCP dialog
"""

from __future__ import annotations

import os


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


# ── Autostart ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if _autostart_enabled():
        import dcc_mcp_zbrush  # noqa: PLC0415

        dcc_mcp_zbrush.start_server(mode="embedded")
