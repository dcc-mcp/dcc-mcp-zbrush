"""Command-line entry point for dcc-mcp-zbrush sidecar mode."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from dcc_mcp_zbrush import start_server, stop_server
from dcc_mcp_zbrush.__version__ import __version__

logger = logging.getLogger(__name__)

LIFECYCLE_OPERATIONS = {"install", "status", "verify", "uninstall", "upgrade"}

# The host queue is drained from the sidecar process main thread. A bounded
# timeout keeps the loop responsive to Ctrl+C; a fairness cap stops one busy
# burst from starving the listener.
_HOST_TICK_MAX_JOBS = 16
_HOST_TICK_TIMEOUT_MS = 100
_MAX_CONSECUTIVE_TICK_FAILURES = 5
_PUMP_GUARD = threading.Lock()


def _asset_dir_from_environment() -> Optional[Path]:
    explicit = os.environ.get("ZBRUSH_USER_ASSETS_DIR")
    if explicit:
        return Path(explicit)
    plugin_path = os.environ.get("ZBRUSH_PLUGIN_PATH", "")
    if plugin_path:
        first = plugin_path.split(os.pathsep, 1)[0]
        if first:
            return Path(first)
    return None


def _lifecycle_main(argv: list[str]) -> int:
    from dcc_mcp_zbrush.install_lifecycle import LifecycleRequest, run_lifecycle

    parser = argparse.ArgumentParser(description="Install and verify the ZBrush MCP adapter")
    parser.add_argument("operation", choices=sorted(LIFECYCLE_OPERATIONS))
    parser.add_argument("--mode", choices=("embedded", "sidecar"), default="sidecar")
    parser.add_argument("--version", default=__version__, help="Fixed adapter release version (never 'latest')")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit the stable lifecycle JSON schema")
    parser.add_argument("--yes", action="store_true", help="Confirm a mutating install, upgrade, or uninstall")
    parser.add_argument(
        "--dry-run", action="store_true", help="Run preflight and print the plan without changing files"
    )
    parser.add_argument("--dcc-path", type=Path, default=os.environ.get("ZBRUSH_EXECUTABLE"))
    parser.add_argument("--python", type=Path, default=Path(sys.executable), dest="python_path")
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=_asset_dir_from_environment(),
        help="ZBrush Asset Directory (or set ZBRUSH_USER_ASSETS_DIR)",
    )
    parser.add_argument("--socket-host", default="127.0.0.1")
    parser.add_argument("--socket-port", type=int, default=9876)
    parser.add_argument(
        "--plugin-archive",
        type=Path,
        help="Offline/local plugin ZIP; requires --sha256 and never bypasses verification",
    )
    parser.add_argument("--sha256", help="Expected SHA-256 for --plugin-archive")
    args = parser.parse_args(argv)
    request = LifecycleRequest(
        operation=args.operation,
        mode=args.mode,
        version=args.version,
        dcc_path=Path(args.dcc_path) if args.dcc_path else None,
        python_path=args.python_path,
        asset_dir=args.asset_dir,
        yes=args.yes,
        dry_run=args.dry_run,
        socket_host=args.socket_host,
        socket_port=args.socket_port,
    )
    result = run_lifecycle(
        request,
        plugin_archive=args.plugin_archive,
        expected_sha256=args.sha256,
    )
    if args.json_output:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['status']}: {result['reason']}")
        for step in result["next_steps"]:
            action = step.get("command") or step.get("file_edit")
            print(f"- {step['description']} ({action})")
    return int(result["exit_code"])


def pump_host_dispatcher(
    dispatcher: Any,
    *,
    max_jobs: int = _HOST_TICK_MAX_JOBS,
    timeout_ms: int = _HOST_TICK_TIMEOUT_MS,
) -> None:
    """Drain the host queue on the calling (sidecar main) thread until shutdown.

    Core's ``attach_dispatcher`` posts every HTTP ``tools/call`` into this
    queue; ``tick_blocking`` executes those jobs on the calling thread. Skill
    script execution stays inline, so no job ever posts another job (the
    reentrancy failure mode of a queue-backed in-process executor).

    A single failed tick is logged and retried; repeated consecutive failures
    end the loop so the process reports a real error instead of spinning. The
    module-level guard prevents two pumps from draining one queue at once.
    """
    if not _PUMP_GUARD.acquire(blocking=False):
        raise RuntimeError("A ZBrush host dispatcher pump is already running")
    failures = 0
    try:
        while not dispatcher.is_shutdown():
            try:
                dispatcher.tick_blocking(max_jobs, timeout_ms)
            except KeyboardInterrupt:
                raise
            except Exception:  # noqa: BLE001
                failures += 1
                logger.exception(
                    "ZBrush host dispatcher tick failed (%d/%d consecutive)",
                    failures,
                    _MAX_CONSECUTIVE_TICK_FAILURES,
                )
                if failures >= _MAX_CONSECUTIVE_TICK_FAILURES:
                    raise RuntimeError("ZBrush host dispatcher tick failed repeatedly; stopping the sidecar")
                time.sleep(min(0.5, 0.05 * failures))
            else:
                failures = 0
    finally:
        _PUMP_GUARD.release()


def _shutdown_host_dispatcher(dispatcher: Any) -> None:
    """Reject new posts and cancel pending jobs; safe to call more than once."""
    try:
        dispatcher.shutdown()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to shut down the ZBrush host dispatcher")


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] in LIFECYCLE_OPERATIONS:
        return _lifecycle_main(arguments)
    parser = argparse.ArgumentParser(description="ZBrush MCP Server")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Optional fixed MCP instance port (default: core/env, then OS-assigned)",
    )
    parser.add_argument(
        "--gateway-port", type=int, default=None, help="Gateway port (None = core default 9765, 0 = disabled)"
    )
    parser.add_argument(
        "--mode",
        choices=("embedded", "sidecar"),
        default=None,
        help="embedded when running inside ZBrush; sidecar when using socket plugin",
    )
    parser.add_argument("--socket-host", default="127.0.0.1", help="ZBrush socket plugin host")
    parser.add_argument("--socket-port", type=int, default=9876, help="ZBrush socket plugin port")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args(arguments)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    server = start_server(
        port=args.port,
        gateway_port=args.gateway_port,
        mode=args.mode or "sidecar",
        socket_host=args.socket_host,
        socket_port=args.socket_port,
    )

    print(f"ZBrush MCP server started ({server.mode}): {server.mcp_url}")
    print("Press Ctrl+C to stop...")

    dispatcher = server.host_dispatcher
    if dispatcher is None:
        # Embedded mode has no HTTP host queue to drain; keep the process alive.
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            stop_server()
        return 0

    exit_code = 0
    try:
        pump_host_dispatcher(dispatcher)
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as exc:  # noqa: BLE001
        print(f"\nHost dispatcher stopped unexpectedly: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        # Cancel queued jobs first so in-flight HTTP handlers fail fast, then
        # stop the listener. ``stop_server`` also shuts down the dispatcher.
        _shutdown_host_dispatcher(dispatcher)
        stop_server()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
