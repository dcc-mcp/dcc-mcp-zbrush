"""Create and load a reusable wrinkle-crease brush preset."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush._skill_host import quiet_ui_actions
from dcc_mcp_zbrush.api import with_zbrush, zb_error, zb_success

_SETTINGS = {
    "Stroke:Lazy Mouse": 1.0,
    "Stroke:Lazy Mouse:LazyStep": 0.05,
    "Stroke:Lazy Mouse:LazySmooth": 3.0,
    "Stroke:Lazy Mouse:LazyRadius": 12.0,
    "Draw:Z Intensity": 18.0,
    "Draw:Focal Shift": -70.0,
    "Draw:Draw Size": 18.0,
    "Draw:Zsub": 1.0,
}


def _validate_path(path: str, *, must_exist: bool) -> tuple[Optional[str], Optional[dict]]:
    if not path:
        return None, zb_error("Brush path must not be empty", "BRUSH_PATH_MISSING")
    abs_path = os.path.abspath(path)
    if os.path.splitext(abs_path)[1].lower() != ".zbp":
        return None, zb_error("Brush path must end in .ZBP", "UNSUPPORTED_FORMAT", brush_path=abs_path)
    if must_exist and not os.path.isfile(abs_path):
        return None, zb_error(f"Brush file does not exist: {abs_path}", "FILE_NOT_FOUND", brush_path=abs_path)
    directory = os.path.dirname(abs_path)
    if not must_exist and directory and not os.path.isdir(directory):
        return None, zb_error(
            f"Output directory does not exist: {directory}",
            "OUTPUT_DIR_MISSING",
            brush_path=abs_path,
        )
    return abs_path, None


def _require_controls(zbc: Any, *controls: str) -> None:
    missing = [control for control in controls if not zbc.exists(control)]
    if missing:
        raise RuntimeError(f"Missing required ZBrush controls: {', '.join(missing)}")


def _apply_settings(zbc: Any) -> None:
    _require_controls(zbc, "Stroke:FreeHand", "Alpha:Alpha 01", *_SETTINGS)
    zbc.press("Stroke:FreeHand")
    zbc.press("Alpha:Alpha 01")
    for item_path, value in _SETTINGS.items():
        zbc.set(item_path, value)
    if zbc.exists("Draw:Rgb"):
        zbc.set("Draw:Rgb", 0.0)


def _current_settings(zbc: Any) -> Dict[str, float]:
    return {
        "lazy_mouse": float(zbc.get("Stroke:Lazy Mouse")),
        "lazy_step": float(zbc.get("Stroke:Lazy Mouse:LazyStep")),
        "lazy_smooth": float(zbc.get("Stroke:Lazy Mouse:LazySmooth")),
        "lazy_radius": float(zbc.get("Stroke:Lazy Mouse:LazyRadius")),
        "z_intensity": float(zbc.get("Draw:Z Intensity")),
        "focal_shift": float(zbc.get("Draw:Focal Shift")),
        "draw_size": float(zbc.get("Draw:Draw Size")),
        "zsub": float(zbc.get("Draw:Zsub")),
    }


def _create(zbc: Any, output_path: str) -> Dict[str, Any]:
    _require_controls(zbc, "Brush:DamStandard", "Brush:Clone", "Brush:Save As")
    backup_path = ""
    if os.path.isfile(output_path):
        backup_path = f"{output_path}.dcc-mcp-{os.getpid()}-{time.time_ns()}.bak"
        os.replace(output_path, backup_path)
    try:
        with quiet_ui_actions(zbc):
            zbc.press("Brush:DamStandard")
            zbc.press("Brush:Clone")
            _apply_settings(zbc)
            zbc.set_next_filename(output_path)
            zbc.press("Brush:Save As")
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("ZBrush did not save a non-empty brush file")
    except Exception:
        if os.path.isfile(output_path):
            os.remove(output_path)
        if backup_path:
            os.replace(backup_path, output_path)
        raise
    if backup_path:
        os.remove(backup_path)
    return {
        "brush_path": output_path,
        "bytes": os.path.getsize(output_path),
        "base_brush": "DamStandard",
        "settings": _current_settings(zbc),
    }


def _load(zbc: Any, brush_path: str) -> Dict[str, Any]:
    _require_controls(zbc, "Brush:Load Brush")
    with quiet_ui_actions(zbc):
        zbc.set_next_filename(brush_path)
        zbc.press("Brush:Load Brush")
        _apply_settings(zbc)
    return {
        "brush_path": brush_path,
        "bytes": os.path.getsize(brush_path),
        "settings": _current_settings(zbc),
    }


@skill_entry
@with_zbrush
def create_wrinkle_brush(output_path: str, **kwargs: Any) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    abs_path, error = _validate_path(output_path, must_exist=False)
    if error:
        return error
    assert abs_path is not None
    payload = run_in_zbrush(
        lambda zbc: _create(zbc, abs_path),
        "create_wrinkle_brush",
        output_path=abs_path,
    )
    return zb_success(
        f"Created wrinkle brush at {payload['brush_path']}",
        prompt="Sculpt with the active brush, or reload it later with load_wrinkle_brush.",
        **payload,
    )


@skill_entry
@with_zbrush
def load_wrinkle_brush(brush_path: str, **kwargs: Any) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    abs_path, error = _validate_path(brush_path, must_exist=True)
    if error:
        return error
    assert abs_path is not None
    payload = run_in_zbrush(
        lambda zbc: _load(zbc, abs_path),
        "load_wrinkle_brush",
        brush_path=abs_path,
    )
    return zb_success(
        f"Loaded wrinkle brush from {payload['brush_path']}",
        prompt="Use the active brush on a subdivided editable mesh.",
        **payload,
    )


def main(**kwargs: Any) -> dict:
    if "output_path" in kwargs:
        return create_wrinkle_brush(**kwargs)
    return load_wrinkle_brush(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
