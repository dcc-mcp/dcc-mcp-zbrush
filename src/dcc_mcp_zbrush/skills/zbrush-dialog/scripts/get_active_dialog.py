"""Detect and read a modal ZBrush note without queueing work on ZBrush."""

from __future__ import annotations

from typing import Any, Optional

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush.api import with_zbrush, zb_error, zb_success


@skill_entry
@with_zbrush
def get_active_dialog(
    force_capture: bool = False,
    process_id: Optional[int] = None,
    output_dir: Optional[str] = None,
    probe_timeout_secs: float = 3.0,
    **kwargs: Any,
) -> dict:
    from dcc_mcp_zbrush import _dialogs  # noqa: PLC0415

    try:
        info = _dialogs.get_active_dialog(
            _dialogs.sidecar_bridge(),
            process_id=process_id,
            force_capture=bool(force_capture),
            output_dir=output_dir,
            probe_timeout=float(probe_timeout_secs),
        )
    except _dialogs.DialogError as exc:
        return zb_error(exc.message, exc.code, prompt=exc.prompt, **exc.context)
    if info["dialog_open"] and info.get("destructive"):
        return zb_success(
            "A ZBrush note asks to confirm a destructive operation",
            prompt=(
                "Do not confirm without the user's approval. Click Cancel, or pass "
                "allow_destructive=true to zbrush_dialog__click_dialog_button after they approve."
            ),
            **info,
        )
    if info["dialog_open"]:
        return zb_success(
            f"A ZBrush note is open ({info.get('dialog_kind')})",
            prompt="Read dialog_text, then call zbrush_dialog__click_dialog_button with one button_candidates label.",
            **info,
        )
    if info["zbrush_blocked"]:
        return zb_success(
            "ZBrush does not answer, but no dialog text is visible",
            prompt="ZBrush may be running a long operation. Wait, then call this tool again; do not click.",
            **info,
        )
    return zb_success("No modal dialog is blocking ZBrush", **info)


def main(**kwargs: Any) -> dict:
    return get_active_dialog(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
