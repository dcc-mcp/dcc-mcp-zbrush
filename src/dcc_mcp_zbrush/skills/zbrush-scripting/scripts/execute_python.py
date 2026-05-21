"""Execute Python inside ZBrush's embedded SDK."""

from __future__ import annotations

import io
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Dict, Optional

from dcc_mcp_core.skill import skill_entry, skill_error, skill_exception, skill_success


def _run_embedded(code: str, context: Optional[Dict[str, Any]]) -> dict:
    from dcc_mcp_zbrush.api import import_zbc  # noqa: PLC0415

    zbc = import_zbc()
    namespace: Dict[str, Any] = {"zbc": zbc}
    if context:
        namespace.update(context)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    result = None
    error = None

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(compile(code, "<mcp-script>", "exec"), namespace)  # noqa: S102
        result = namespace.get("result")
    except Exception:
        error = traceback.format_exc()

    stdout_output = stdout_buf.getvalue()
    stderr_output = stderr_buf.getvalue()
    if error:
        return skill_error(
            "Script execution failed",
            error,
            stdout=stdout_output,
            stderr=stderr_output + error,
        )
    return skill_success(
        "Script executed successfully",
        stdout=stdout_output,
        stderr=stderr_output,
        result=str(result) if result is not None else None,
    )


def execute_python(code: str, context: Optional[Dict[str, Any]] = None) -> dict:
    """Execute Python in embedded mode or via the sidecar socket bridge."""
    try:
        from dcc_mcp_zbrush._version_probe import is_zbrush_available  # noqa: PLC0415

        if is_zbrush_available():
            return _run_embedded(code, context)

        from dcc_mcp_zbrush.api import get_bridge  # noqa: PLC0415

        payload = get_bridge().execute_python(code=code, context=context or {})
        if payload.get("success"):
            return skill_success(payload.get("message", "Script executed successfully"), **payload.get("context", {}))
        return skill_error(
            payload.get("message", "Script execution failed"),
            payload.get("error", "bridge-error"),
            **payload.get("context", {}),
        )
    except ImportError:
        return skill_error("ZBrush not available", "zbrush.commands could not be imported")
    except Exception as exc:
        return skill_exception(exc, message="Failed to execute Python code")


@skill_entry
def main(**kwargs) -> dict:
    return execute_python(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
