"""ZBrush Python plugin entry point for embedded dcc-mcp-zbrush mode."""

from __future__ import annotations

import json
import os
import time


def _capture_bootstrap_error(stage: str, error: BaseException) -> None:
    configured = os.environ.get("DCC_MCP_ZBRUSH_BOOTSTRAP_ERRORS", "").strip()
    default_root = os.path.dirname(os.path.abspath(__file__))
    path = configured or os.path.join(default_root, ".dcc-mcp", "bootstrap-errors.jsonl")
    payload = {
        "timestamp": time.time(),
        "stage": stage,
        "reason": str(error),
        "exception_type": type(error).__name__,
    }
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
    except BaseException:
        pass


def _autostart_enabled() -> bool:
    raw = os.environ.get("DCC_MCP_ZBRUSH_AUTOSTART", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


if __name__ == "__main__":
    try:
        import dcc_mcp_zbrush

        if not dcc_mcp_zbrush.install_menu():
            print("dcc-mcp-zbrush — failed to register DCC MCP palette")  # noqa: T201
        if _autostart_enabled():
            dcc_mcp_zbrush.start_server(mode="embedded")
    except BaseException as exc:
        _capture_bootstrap_error("embedded_bootstrap", exc)
        raise
