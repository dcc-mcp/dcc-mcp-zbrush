"""List all ZTools available in ZBrush via HTTP bridge."""
from __future__ import annotations

from dcc_mcp_core.skill import skill_entry


@skill_entry
def list_tools(**kwargs) -> dict:
    """List all ZTools in ZBrush.

    Returns:
        dict: ActionResultModel with tool names and count.
    """
    from dcc_mcp_zbrush.api import get_bridge, zb_success  # noqa: PLC0415

    bridge = get_bridge()
    tools = bridge.list_tools()
    return zb_success(
        f"Found {len(tools)} ZTool(s)",
        prompt="Use get_active_tool to inspect the currently active tool.",
        count=len(tools),
        tools=tools,
    )


def main(**kwargs) -> dict:
    """Entry point; delegates to list_tools."""
    return list_tools(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main
    run_main(main)
