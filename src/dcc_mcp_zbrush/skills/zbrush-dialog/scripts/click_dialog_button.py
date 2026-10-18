"""Click one labelled button on the modal ZBrush note and verify it closed."""

from __future__ import annotations

from typing import Any, Optional

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush.api import with_zbrush, zb_error, zb_success


@skill_entry
@with_zbrush
def click_dialog_button(
    button_name: str = "",
    match: str = "exact",
    force: bool = False,
    allow_destructive: bool = False,
    process_id: Optional[int] = None,
    output_dir: Optional[str] = None,
    probe_timeout_secs: float = 3.0,
    **kwargs: Any,
) -> dict:
    from dcc_mcp_zbrush import _dialogs  # noqa: PLC0415

    try:
        result = _dialogs.click_dialog_button(
            _dialogs.sidecar_bridge(),
            str(button_name or ""),
            process_id=process_id,
            match=str(match or "exact"),
            force=bool(force),
            allow_destructive=bool(allow_destructive),
            output_dir=output_dir,
            probe_timeout=float(probe_timeout_secs),
        )
    except _dialogs.DialogError as exc:
        return zb_error(exc.message, exc.code, prompt=exc.prompt, **exc.context)
    message = (
        f"Clicked {result['button']!r}; ZBrush answers again"
        if result["dismissed"]
        else f"Clicked {result['button']!r} on a non-blocking note"
    )
    return zb_success(
        message,
        prompt="Queued calls that timed out may now run; read the scene state before retrying any call.",
        **result,
    )


def main(**kwargs: Any) -> dict:
    return click_dialog_button(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
