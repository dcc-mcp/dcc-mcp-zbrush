"""Bake and atomically export one map from the active ZBrush subtool."""

from __future__ import annotations

import os
import time

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush._skill_host import run_quiet_ui
from dcc_mcp_zbrush.api import with_zbrush, zb_error, zb_success

_SUPPORTED_MAP_TYPES = {"normal", "displacement"}
_SUPPORTED_EXTENSIONS = {".tif", ".tiff"}
_MAP_SIZE_CONTROL = "Tool:UV Map:UV Map Size"
_MAP_BORDER_CONTROL = "Tool:UV Map:UV Map Border"
_MAP_CONTROLS = {
    "normal": {
        "create": "Tool:Normal Map:Create NormalMap",
        "clone": "Tool:Normal Map:Clone NM",
        "export": "Texture:Export",
        "smooth": "Tool:Normal Map:SmoothUV",
        "tangent": "Tool:Normal Map:Tangent",
    },
    "displacement": {
        "create": "Tool:Displacement Map:Create DispMap",
        "clone": "Tool:Displacement Map:Clone Disp",
        "export": "Alpha:Export",
        "smooth": "Tool:Displacement Map:SmoothUV",
    },
}


def _failure(message: str, error: str, **context) -> dict:
    return {"success": False, "message": message, "error": error, **context}


def _bake(zbc, map_type: str, output_path: str, width: int, height: int, smooth: bool, border: int) -> dict:
    normalized_type = map_type.strip().lower()
    if normalized_type not in _SUPPORTED_MAP_TYPES:
        return _failure(
            "map_type must be normal or displacement",
            "UNSUPPORTED_MAP_TYPE",
            map_type=normalized_type,
            output_path=output_path,
        )
    if not output_path:
        return _failure("output_path must not be empty", "OUTPUT_PATH_MISSING")
    abs_path = os.path.abspath(output_path)
    if os.path.splitext(abs_path)[1].lower() not in _SUPPORTED_EXTENSIONS:
        return _failure(
            "Baked maps must use a .tif or .tiff output path",
            "UNSUPPORTED_FORMAT",
            output_path=abs_path,
        )
    directory = os.path.dirname(abs_path)
    if directory and not os.path.isdir(directory):
        return _failure(
            f"Output directory does not exist: {directory}",
            "OUTPUT_DIR_MISSING",
            output_path=abs_path,
        )
    if width != height:
        return _failure(
            "ZBrush native map baking requires equal width and height",
            "NON_SQUARE_MAP",
            output_path=abs_path,
        )
    if not (256 <= width <= 8192) or width & (width - 1):
        return _failure(
            "width and height must be the same power of two between 256 and 8192",
            "INVALID_MAP_SIZE",
            output_path=abs_path,
        )
    if not 0 <= border <= 16:
        return _failure("border must be between 0 and 16", "INVALID_BORDER", output_path=abs_path)

    uv_bounds = [float(value) for value in zbc.query_mesh3d(3)]
    if len(uv_bounds) != 4 or uv_bounds[2] <= uv_bounds[0] or uv_bounds[3] <= uv_bounds[1]:
        return _failure(
            "The active subtool has no usable UVs",
            "UVS_MISSING",
            map_type=normalized_type,
            output_path=abs_path,
            uv_bounds=uv_bounds,
        )
    controls = _MAP_CONTROLS[normalized_type]
    setting_controls = [_MAP_SIZE_CONTROL, _MAP_BORDER_CONTROL, controls["smooth"]]
    if tangent_control := controls.get("tangent"):
        setting_controls.append(tangent_control)
    required_controls = [controls["create"], controls["clone"], controls["export"], *setting_controls]
    if any(not zbc.exists(path) for path in required_controls):
        return _failure(
            "Required ZBrush map export controls are unavailable",
            "CONTROL_UNAVAILABLE",
            output_path=abs_path,
        )
    saved_settings = {path: zbc.get(path) for path in setting_controls}

    stem, extension = os.path.splitext(os.path.basename(abs_path))
    stage_path = os.path.join(directory, f".{stem}-{os.getpid()}-{time.time_ns()}{extension}")
    map_created = False

    def bake_and_export() -> None:
        nonlocal map_created
        lowered = 0
        try:
            zbc.set(_MAP_SIZE_CONTROL, width)
            zbc.set(_MAP_BORDER_CONTROL, border)
            zbc.set(controls["smooth"], int(smooth))
            if tangent_control:
                zbc.set(tangent_control, 1)
            while zbc.exists("Tool:Geometry:Lower Res") and zbc.is_enabled("Tool:Geometry:Lower Res"):
                zbc.press("Tool:Geometry:Lower Res")
                lowered += 1
            zbc.press(controls["create"])
            if not zbc.is_enabled(controls["clone"]):
                return
            zbc.press(controls["clone"])
            zbc.set_next_filename(stage_path)
            zbc.press(controls["export"])
            map_created = True
        finally:
            try:
                for _ in range(lowered):
                    if not zbc.exists("Tool:Geometry:Higher Res") or not zbc.is_enabled("Tool:Geometry:Higher Res"):
                        raise RuntimeError("ZBrush could not restore the original subdivision level")
                    zbc.press("Tool:Geometry:Higher Res")
            finally:
                for item_path, value in saved_settings.items():
                    zbc.set(item_path, value)

    try:
        run_quiet_ui(zbc, bake_and_export)
        if not map_created:
            return _failure(
                "ZBrush did not create a baked map from the active subdivision stack",
                "MAP_NOT_CREATED",
                map_type=normalized_type,
                output_path=abs_path,
                uv_bounds=uv_bounds,
            )
        if not os.path.isfile(stage_path) or os.path.getsize(stage_path) == 0:
            raise RuntimeError("ZBrush did not export a non-empty baked map")
        os.replace(stage_path, abs_path)
        return {
            "map_type": normalized_type,
            "output_path": abs_path,
            "bytes": os.path.getsize(abs_path),
            "width": width,
            "height": height,
            "smooth": smooth,
            "border": border,
            "uv_bounds": uv_bounds,
        }
    finally:
        if os.path.isfile(stage_path):
            os.remove(stage_path)


@skill_entry
@with_zbrush
def bake_active_subtool_map(
    map_type: str,
    output_path: str,
    width: int = 2048,
    height: int = 2048,
    smooth: bool = True,
    border: int = 8,
    **kwargs,
) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    payload = run_in_zbrush(
        lambda zbc: _bake(zbc, map_type, output_path, width, height, smooth, border),
        "bake_active_subtool_map",
        allow_domain_failure=True,
        map_type=map_type,
        output_path=output_path,
        width=width,
        height=height,
        smooth=smooth,
        border=border,
    )
    if payload.get("success") is False:
        context = {key: value for key, value in payload.items() if key not in {"success", "message", "error"}}
        return zb_error(
            payload.get("message", "Map bake failed"),
            payload.get("error", "BAKE_FAILED"),
            prompt="Ensure the active mesh has UVs and more than one subdivision level before retrying.",
            **context,
        )
    return zb_success(
        f"Baked {payload['map_type']} map",
        prompt="Import the exported TIFF with the mesh in the downstream DCC.",
        **payload,
    )


def main(**kwargs) -> dict:
    return bake_active_subtool_map(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
