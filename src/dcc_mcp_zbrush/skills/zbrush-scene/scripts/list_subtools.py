"""List subtools on the active ZBrush tool."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush._skill_host import subtool_status_flags
from dcc_mcp_zbrush.api import with_zbrush, zb_success


def _collect(zbc) -> dict:
    count = int(zbc.get_subtool_count())
    subtools = []
    for index in range(count):
        raw_status = int(zbc.get_subtool_status(index))
        subtools.append(
            {
                "index": index,
                "raw_status": raw_status,
                "flags": subtool_status_flags(raw_status),
            }
        )
    return {"count": count, "subtools": subtools}


@skill_entry
@with_zbrush
def list_subtools(**kwargs) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    payload = run_in_zbrush(_collect, "list_subtools")
    return zb_success(
        f"Found {payload['count']} subtool(s)",
        **payload,
        prompt="Use zbrush_subtool__select_subtool to activate a subtool by index.",
    )


def main(**kwargs) -> dict:
    return list_subtools(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
