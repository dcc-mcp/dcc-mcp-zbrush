"""Export the active subtool to OBJ using the ZBrush SDK export pattern."""

from __future__ import annotations

import os

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush.api import with_zbrush, zb_error, zb_success


def _export(zbc, output_path: str) -> dict:
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        return zb_error(
            f"Output directory does not exist: {directory}",
            "OUTPUT_DIR_MISSING",
            output_path=output_path,
            prompt="Create the directory or choose an existing folder.",
        )

    zbc.set_next_filename(os.path.abspath(output_path))
    zbc.press("Tool:Export")
    path = str(zbc.get_active_tool_path() or "")
    return {
        "output_path": os.path.abspath(output_path),
        "active_tool_path": path,
        "subtool_name": path.rsplit("/", 1)[-1] if path else "",
    }


@skill_entry
@with_zbrush
def export_active_subtool_obj(output_path: str, **kwargs) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    payload = run_in_zbrush(
        lambda zbc: _export(zbc, output_path),
        "export_active_subtool_obj",
        output_path=output_path,
    )
    if isinstance(payload, dict) and payload.get("success") is False:
        return payload
    return zb_success(
        f"Exported active subtool to {payload['output_path']}",
        prompt="Import the OBJ in Maya, Blender, or your target DCC.",
        **payload,
    )


def main(**kwargs) -> dict:
    return export_active_subtool_obj(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
