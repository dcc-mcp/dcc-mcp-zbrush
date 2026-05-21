"""In-process skill execution wiring for ZBrush's embedded Python VM."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def create_inprocess_dispatcher() -> Any:
    """Return an :class:`InProcessCallableDispatcher` for ZBrush skill jobs."""
    from dcc_mcp_core import InProcessCallableDispatcher  # noqa: PLC0415

    return InProcessCallableDispatcher()


def attach_inprocess_executor(server: Any, dispatcher: Optional[Any] = None) -> Any:
    """Register in-process dispatch on ``server`` when running inside ZBrush."""
    from dcc_mcp_zbrush._version_probe import is_zbrush_available  # noqa: PLC0415

    if not is_zbrush_available():
        logger.debug("ZBrush SDK not available — skipping in-process executor")
        return None

    disp = dispatcher or create_inprocess_dispatcher()
    server.register_inprocess_executor(dispatcher=disp)
    logger.info("Registered in-process executor for ZBrush embedded Python VM")
    return disp
