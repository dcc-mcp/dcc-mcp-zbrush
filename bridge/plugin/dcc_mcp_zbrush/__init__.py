"""Auto-start dcc-mcp-zbrush when ZBrush loads this plugin directory.

Install by copying the whole ``bridge/plugin/dcc_mcp_zbrush`` folder into a
``ZBRUSH_PLUGIN_PATH`` entry or by adding this repo's ``src`` tree to
``PYTHONPATH``.
"""

from __future__ import annotations

import os


def _autostart_enabled() -> bool:
    raw = os.environ.get("DCC_MCP_ZBRUSH_AUTOSTART", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


if __name__ == "__main__":
    if _autostart_enabled():
        import dcc_mcp_zbrush  # noqa: PLC0415

        dcc_mcp_zbrush.start_server(mode="embedded")
