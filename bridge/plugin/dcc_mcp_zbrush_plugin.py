"""ZBrush Python plugin entry point for embedded dcc-mcp-zbrush mode."""

from __future__ import annotations

import os


def _autostart_enabled() -> bool:
    raw = os.environ.get("DCC_MCP_ZBRUSH_AUTOSTART", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


if __name__ == "__main__":
    import dcc_mcp_zbrush

    if not dcc_mcp_zbrush.install_menu():
        print("dcc-mcp-zbrush — failed to register DCC MCP palette")  # noqa: T201
    if _autostart_enabled():
        dcc_mcp_zbrush.start_server(mode="embedded")
