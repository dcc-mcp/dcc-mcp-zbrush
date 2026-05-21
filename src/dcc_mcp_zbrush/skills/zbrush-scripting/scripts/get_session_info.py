"""Read ZBrush session metadata."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush.api import with_zbrush, zb_success


def _collect(zbc) -> dict:
    return {
        "zbrush_version": f"{int(zbc.zbrush_info(0))}.{int(zbc.zbrush_info(1))}",
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
