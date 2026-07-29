"""Return machine-comparable metrics for the active ZBrush mesh."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush._skill_host import subtool_name_from_path
from dcc_mcp_zbrush.api import with_zbrush, zb_success


def _inspect(zbc) -> dict:
    uv_bounds = [float(value) for value in zbc.query_mesh3d(3)]
    path = str(zbc.get_active_tool_path() or "")
    return {
        "active_tool_path": path,
        "subtool_name": subtool_name_from_path(path),
        "point_count": int(zbc.query_mesh3d(0)[0]),
        "face_count": int(zbc.query_mesh3d(1)[0]),
        "bounds": [float(value) for value in zbc.query_mesh3d(2)],
        "uv_bounds": uv_bounds,
        "has_uvs": len(uv_bounds) == 4 and uv_bounds[2] > uv_bounds[0] and uv_bounds[3] > uv_bounds[1],
        "mesh_area": float(zbc.query_mesh3d(8)[0]),
        "solid": bool(zbc.is_polymesh3d_solid()),
    }


@skill_entry
@with_zbrush
def inspect_active_mesh(**kwargs) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    payload = run_in_zbrush(_inspect, "inspect_active_mesh")
    return zb_success(
        f"Active mesh has {payload['face_count']} face(s)",
        prompt="Use the metrics to validate an export or decide whether baking prerequisites are met.",
        **payload,
    )


def main(**kwargs) -> dict:
    return inspect_active_mesh(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
