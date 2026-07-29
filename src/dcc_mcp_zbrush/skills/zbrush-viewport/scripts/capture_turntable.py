"""Capture native ZBrush document frames at deterministic view angles."""

from __future__ import annotations

import math
import os
import time
from typing import Any, Iterable, Optional, Tuple

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush._skill_host import quiet_ui_actions
from dcc_mcp_zbrush.api import with_zbrush, zb_error, zb_success

_DEFAULT_ANGLES = tuple(range(0, 360, 30))


def _validate(
    output_dir: str,
    angles: Optional[Iterable[float]],
    prefix: str,
) -> Tuple[Optional[str], Optional[list[float]], Optional[dict]]:
    if not output_dir:
        return None, None, zb_error("output_dir must not be empty", "OUTPUT_DIR_MISSING")
    abs_dir = os.path.abspath(output_dir)
    if not os.path.isdir(abs_dir):
        return (
            None,
            None,
            zb_error(
                f"Output directory does not exist: {abs_dir}",
                "OUTPUT_DIR_MISSING",
                output_dir=abs_dir,
            ),
        )
    if not prefix or os.path.basename(prefix) != prefix:
        return None, None, zb_error("prefix must be a file name", "INVALID_PREFIX")
    try:
        normalized = [float(angle) for angle in (_DEFAULT_ANGLES if angles is None else angles)]
    except (TypeError, ValueError):
        return None, None, zb_error("angles must be a list of numbers", "INVALID_ANGLES")
    if not normalized or len(normalized) > 72 or any(not math.isfinite(angle) for angle in normalized):
        return None, None, zb_error("angles must contain 1 to 72 finite values", "INVALID_ANGLES")
    return abs_dir, normalized, None


def _capture(zbc: Any, output_dir: str, angles: list[float], prefix: str, bpr_render: bool) -> dict:
    required = ["Document:Export"]
    if bpr_render:
        required.append("Render:BPR")
    missing = [control for control in required if not zbc.exists(control)]
    if missing:
        raise RuntimeError(f"Missing required ZBrush controls: {', '.join(missing)}")

    base_transform = [float(value) for value in zbc.get_transform()]
    if len(base_transform) != 9:
        raise RuntimeError("ZBrush returned an invalid tool transform")
    staged: list[tuple[str, str, float]] = []
    try:
        with quiet_ui_actions(zbc):
            try:
                for index, angle in enumerate(angles):
                    zbc.set_transform(
                        x_rotate=base_transform[6],
                        y_rotate=angle,
                        z_rotate=base_transform[8],
                    )
                    zbc.update(redraw_ui=True)
                    if bpr_render:
                        zbc.press("Render:BPR")
                    final_path = os.path.join(output_dir, f"{prefix}-{index:03d}.psd")
                    stage_path = os.path.join(
                        output_dir,
                        f".{prefix}-{index:03d}-{os.getpid()}-{time.time_ns()}.psd",
                    )
                    staged.append((stage_path, final_path, angle))
                    zbc.set_next_filename(stage_path)
                    zbc.press("Document:Export")
                    if not os.path.isfile(stage_path) or os.path.getsize(stage_path) == 0:
                        raise RuntimeError(f"ZBrush did not export a non-empty document frame: {final_path}")
            finally:
                zbc.set_transform(*base_transform)
                zbc.update(redraw_ui=True)

        frames = []
        for stage_path, final_path, angle in staged:
            os.replace(stage_path, final_path)
            frames.append({"angle": angle, "path": final_path, "bytes": os.path.getsize(final_path)})
        return {
            "output_dir": output_dir,
            "frames": frames,
            "base_transform": base_transform,
            "bpr_render": bpr_render,
        }
    finally:
        for stage_path, _final_path, _angle in staged:
            if os.path.isfile(stage_path):
                os.remove(stage_path)


@skill_entry
@with_zbrush
def capture_turntable(
    output_dir: str,
    angles: Optional[Iterable[float]] = None,
    prefix: str = "zbrush-turntable",
    bpr_render: bool = True,
    **kwargs: Any,
) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    abs_dir, normalized, error = _validate(output_dir, angles, prefix)
    if error:
        return error
    assert abs_dir is not None and normalized is not None
    payload = run_in_zbrush(
        lambda zbc: _capture(zbc, abs_dir, normalized, prefix, bpr_render),
        "capture_turntable",
        output_dir=abs_dir,
        angles=normalized,
        prefix=prefix,
        bpr_render=bpr_render,
    )
    if isinstance(payload, dict) and payload.get("success") is False:
        return payload
    return zb_success(
        f"Captured {len(payload['frames'])} turntable frame(s) in {payload['output_dir']}",
        prompt="Use the PSD frames directly or convert them into a contact sheet or animation.",
        **payload,
    )


def main(**kwargs: Any) -> dict:
    return capture_turntable(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
