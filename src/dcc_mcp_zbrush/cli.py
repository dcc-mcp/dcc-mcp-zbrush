"""Command-line entry point for dcc-mcp-zbrush sidecar mode."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Optional

from dcc_mcp_zbrush import start_server, stop_server


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ZBrush MCP Server")
    parser.add_argument("--port", type=int, default=8765, help="MCP HTTP port")
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
    args = parser.parse_args(argv)

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

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        stop_server()

    return 0


if __name__ == "__main__":
    sys.exit(main())
