"""Read ZBrush session metadata."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from dcc_mcp_core.skill import skill_entry

import dcc_mcp_zbrush
from dcc_mcp_zbrush.api import with_zbrush, zb_success


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _install_identity() -> dict:
    configured = os.environ.get("DCC_MCP_ZBRUSH_INSTALL_IDENTITY", "").strip()
    if not configured:
        return {}
    try:
        payload = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _collect(zbc) -> dict:
    identity = _install_identity()
    adapter_path = Path(dcc_mcp_zbrush.__file__).resolve()
    zbrush_origin = str(getattr(zbc, "__file__", "") or "")
    try:
        server = dcc_mcp_zbrush.get_server()
    except Exception:
        server = None
    return {
        "adapter_module_path": str(adapter_path),
        "adapter_module_sha256": _sha256(adapter_path),
        "adapter_version": dcc_mcp_zbrush.__version__,
        "zbrush_version": f"{int(zbc.zbrush_info(0))}.{int(zbc.zbrush_info(1))}",
        "zbrush_commands_origin": str(Path(zbrush_origin).resolve()) if zbrush_origin else "",
        "install_id": identity.get("install_id"),
        "selected_dcc_path": identity.get("dcc_path"),
        "selected_dcc_sha256": identity.get("dcc_sha256"),
        "instance_id": str(getattr(server, "instance_id", "") or ""),
        "mcp_url": str(getattr(server, "mcp_url", "") or ""),
        "pid": os.getpid(),
        "process_executable": sys.executable,
        "active_tool_path": str(zbc.get_active_tool_path() or ""),
        "subtool_count": int(zbc.get_subtool_count()),
        "embedded_python": True,
    }


@skill_entry
@with_zbrush
def get_session_info(**kwargs) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    info = run_in_zbrush(_collect, "get_session_info")
    return zb_success("ZBrush session info", **info)


def main(**kwargs) -> dict:
    return get_session_info(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
