"""Apply a bounded subdivision and deformation pass to the active subtool."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush.api import with_zbrush, zb_success


def _refine(
    zbc,
    subdivision_levels: int,
    polish: float,
    inflate: float,
) -> dict:
    for _ in range(subdivision_levels):
        zbc.press("Tool:Geometry:Divide")
    if polish:
        zbc.set("Tool:Deformation:Polish", float(polish))
    if inflate:
        zbc.set("Tool:Deformation:Inflate", float(inflate))
    path = str(zbc.get_active_tool_path() or "")
    return {
        "active_tool_path": path,
        "subtool_name": path.rsplit("/", 1)[-1] if path else "",
        "subdivision_levels": subdivision_levels,
        "polish": float(polish),
        "inflate": float(inflate),
    }


@skill_entry
@with_zbrush
def refine_active_subtool(
    subdivision_levels: int = 1,
    polish: float = 0,
    inflate: float = 0,
    **kwargs,
) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    payload = run_in_zbrush(
        lambda zbc: _refine(zbc, subdivision_levels, polish, inflate),
        "refine_active_subtool",
        subdivision_levels=subdivision_levels,
        polish=polish,
        inflate=inflate,
    )
    return zb_success(
        f"Refined active subtool with {subdivision_levels} subdivision level(s)",
        prompt="Export the active subtool or inspect it before further sculpting.",
        **payload,
    )


def main(**kwargs) -> dict:
    return refine_active_subtool(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
