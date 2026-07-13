"""In-process skill execution wiring for embedded and sidecar modes."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def attach_inprocess_executor(server: Any) -> None:
    """Register local dispatch so skill imports use the server environment.

    Embedded calls reach ``zbrush.commands`` directly. Sidecar calls reach the
    same API facade through ``SocketBridge``; both must keep skill execution in
    the adapter process instead of spawning an unrelated ambient Python.
    """

    server.register_inprocess_executor()
    logger.info("Registered in-process executor for ZBrush skill calls")
