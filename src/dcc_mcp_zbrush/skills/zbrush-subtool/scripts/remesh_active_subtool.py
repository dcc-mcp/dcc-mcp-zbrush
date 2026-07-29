"""Create a lower-density copy of the active subtool with ZRemesher."""

from __future__ import annotations

import time

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush._skill_host import quiet_ui_actions, subtool_name_from_path
from dcc_mcp_zbrush.api import with_zbrush, zb_success

_TARGET_CONTROL = "Tool:Geometry:ZRemesher:Target Polygons Count"
_REMESH_CONTROL = "Tool:Geometry:ZRemesher"
_DUPLICATE_CONTROL = "Tool:SubTool:Duplicate"


def _remesh(zbc, target_face_count: int, duplicate: bool) -> dict:
    required = [_TARGET_CONTROL, _REMESH_CONTROL]
    if duplicate:
        required.append(_DUPLICATE_CONTROL)
    missing = [control for control in required if not zbc.exists(control)]
    if missing:
        raise RuntimeError(f"Missing required ZBrush controls: {', '.join(missing)}")

    face_count_before = int(zbc.query_mesh3d(1)[0])
    started = time.perf_counter()
    with quiet_ui_actions(zbc):
        if duplicate:
            zbc.press(_DUPLICATE_CONTROL)
        zbc.set(_TARGET_CONTROL, target_face_count / 1000.0)
        zbc.press(_REMESH_CONTROL)
    elapsed_seconds = time.perf_counter() - started

    path = str(zbc.get_active_tool_path() or "")
    return {
        "active_tool_path": path,
        "subtool_name": subtool_name_from_path(path),
        "target_face_count": target_face_count,
        "face_count_before": face_count_before,
        "face_count_after": int(zbc.query_mesh3d(1)[0]),
        "duplicate": duplicate,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


@skill_entry
@with_zbrush
def remesh_active_subtool(
    target_face_count: int = 100_000,
    duplicate: bool = True,
    **kwargs,
) -> dict:
    if isinstance(target_face_count, bool) or not isinstance(target_face_count, int):
        raise ValueError("target_face_count must be an integer")
    if not 1_000 <= target_face_count <= 100_000:
        raise ValueError("target_face_count must be between 1000 and 100000")

    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    payload = run_in_zbrush(
        lambda zbc: _remesh(zbc, target_face_count, duplicate),
        "remesh_active_subtool",
        target_face_count=target_face_count,
        duplicate=duplicate,
    )
    return zb_success(
        f"ZRemesher reduced the active copy from {payload['face_count_before']} to "
        f"{payload['face_count_after']} face(s)",
        prompt="Inspect the result before exporting or capturing PolyFrame evidence.",
        **payload,
    )


def main(**kwargs) -> dict:
    return remesh_active_subtool(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
