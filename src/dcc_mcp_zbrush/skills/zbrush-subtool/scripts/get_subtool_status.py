"""Read subtool visibility/lock flags."""

from __future__ import annotations

from typing import Optional

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush.api import with_zbrush, zb_success
from dcc_mcp_zbrush._skill_host import subtool_status_flags


def _status(zbc, index: Optional[int]) -> dict:
    resolved_index = int(zbc.get_active_subtool_index()) if index is None else index
    raw_status = int(zbc.get_subtool_status(resolved_index))
    flags = subtool_status_flags(raw_status)
    path = str(zbc.get_active_tool_path() or "")
    return {
        "index": resolved_index,
        "raw_status": raw_status,
        "flags": flags,
        "active_tool_path": path,
    }


@skill_entry
@with_zbrush
def get_subtool_status(index: Optional[int] = None, **kwargs) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    payload = run_in_zbrush(
        lambda zbc: _status(zbc, index),
        "get_subtool_status",
        index=index,
    )
    visible = payload["flags"]["visible"]
    return zb_success(
        f"Subtool {payload['index']} visible={visible}",
        **payload,
    )


def main(**kwargs) -> dict:
    return get_subtool_status(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
