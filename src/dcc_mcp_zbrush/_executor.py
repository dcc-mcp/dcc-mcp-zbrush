"""In-process skill execution wiring for embedded and sidecar modes."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def attach_inprocess_executor(server: Any) -> Any | None:
    """Register local dispatch so skill imports use the server environment.

    Embedded calls reach ``zbrush.commands`` directly. Sidecar calls reach the
    same API facade through ``SocketBridge``; both must keep skill execution in
    the adapter process instead of spawning an unrelated ambient Python.

    Sidecar mode needs a *split* wiring. Core routes every HTTP ``tools/call``
    through the dispatcher attached by ``McpHttpServer.attach_dispatcher``; the
    sidecar CLI drains that queue from its main loop. The in-process skill
    executor must therefore stay inline (``dispatcher=None``): giving the
    bridge a queue dispatcher as its ``dispatcher`` would post a second job
    from inside an already-drained job and reenter the runtime. Keep the two
    roles separate and never relax ``thread_affinity: main`` on scene tools.

    Embedded mode keeps the original pure-Python inline registration. The Rust
    dispatcher is imported lazily so the ZBrush embedded VM is not forced to
    load ``dcc_mcp_core._core``.

    Returns:
        The Rust-backed host queue dispatcher that the sidecar CLI must drain,
        or ``None`` for embedded mode.
    """
    if getattr(server, "mode", None) != "sidecar":
        server.register_inprocess_executor()
        logger.info("Registered inline in-process executor for embedded ZBrush skill calls")
        return None

    # Imports stay inside the sidecar branch: the ZBrush embedded VM does not
    # load the Rust extension in the other branch.
    from dcc_mcp_core._server.inprocess_executor import HostExecutionBridge
    from dcc_mcp_core.host import BlockingDispatcher

    host_dispatcher = BlockingDispatcher()
    bridge = HostExecutionBridge(
        dispatcher=None,
        host_dispatcher=host_dispatcher,
    )
    server.register_host_execution_bridge(bridge)
    logger.info(
        "Registered split host execution bridge (inline in-process executor + "
        "HTTP main-thread host dispatcher) for sidecar ZBrush skill calls"
    )
    return host_dispatcher
