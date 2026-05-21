"""Select the active subtool by index."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush.api import with_zbrush, zb_error, zb_success


def _select(zbc, index: int) -> dict:
    count = int(zbc.get_subtool_count())
    if index < 0 or index >= count:
        return zb_error(
            f"Subtool index {index} out of range",
            "INVALID_SUBTOOL_INDEX",
            count=count,
            index=index,
            prompt="Call zbrush_scene__list_subtools to inspect valid indices.",
        )
    zbc.select_subtool(index)
    path = str(zbc.get_active_tool_path() or "")
    return {
        "index": index,
        "active_tool_path": path,
        "subtool_name": path.rsplit("/", 1)[-1] if path else "",
    }


@skill_entry
@with_zbrush
def select_subtool(index: int, **kwargs) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    payload = run_in_zbrush(
        lambda zbc: _select(zbc, index),
        "select_subtool",
        index=index,
    )
    if isinstance(payload, dict) and payload.get("success") is False:
        return payload
    return zb_success(
        f"Selected subtool {payload['index']}",
        prompt="Use get_subtool_status or export tools on the active subtool.",
        **payload,
    )


def main(**kwargs) -> dict:
    return select_subtool(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
