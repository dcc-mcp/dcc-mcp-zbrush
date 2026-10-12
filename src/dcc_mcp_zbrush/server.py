"""ZBrushMcpServer — MCP server for ZBrush 2026.1+ embedded Python SDK."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dcc_mcp_core import DccServerOptions, MinimalModeConfig
from dcc_mcp_core.server_base import DccServerBase

from dcc_mcp_zbrush.__version__ import __version__
from dcc_mcp_zbrush._skill_loader import build_minimal_mode_config
from dcc_mcp_zbrush._version_probe import get_zbrush_version_string

logger = logging.getLogger(__name__)

SERVER_NAME = "dcc-mcp-zbrush"
SERVER_VERSION = __version__
DEFAULT_PORT = 0
_DCC_NAME = "zbrush"
_BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# A blocked host stops answering pings, so probing on every readiness hit would
# add the full probe timeout to /v1/readyz latency. Cache the verdict instead.
_READINESS_VERDICT_TTL_SECONDS = 5.0


class _HostReadinessProbe:
    """Cache the sidecar's last reachability verdict for the readiness bit.

    ``busy`` stays servable — a long native operation is still making progress
    and answers pings. ``blocked`` is not servable: the main thread holds the
    GIL in a native call and no request can run, so ``/v1/readyz`` must report
    the instance as not ready instead of the default optimistic ``True``.
    """

    def __init__(self, bridge_getter: Any, ttl_seconds: float = _READINESS_VERDICT_TTL_SECONDS) -> None:
        self._bridge_getter = bridge_getter
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._status: Optional[str] = None
        self._detail: Dict[str, Any] = {}
        self._checked_at = 0.0

    def refresh(self) -> Dict[str, Any]:
        bridge = self._bridge_getter()
        if bridge is None:
            return {"status": None, "checked": False}
        try:
            verdict = bridge.probe()
        except Exception as exc:  # noqa: BLE001 - readiness must never raise
            logger.debug("sidecar readiness probe failed: %s", exc)
            return {"status": None, "checked": False}
        with self._lock:
            self._status = verdict.get("status")
            self._detail = verdict
            self._checked_at = time.monotonic()
        return verdict

    def status(self) -> Optional[str]:
        with self._lock:
            # A non-positive TTL means "never cache"; compare with >= so a zero
            # TTL is stale even when the clock has not visibly advanced.
            elapsed = time.monotonic() - self._checked_at
            stale = self._ttl_seconds <= 0 or elapsed >= self._ttl_seconds
            return None if stale else self._status

    def is_dcc_ready(self) -> bool:
        """Return the cached verdict for core's ``dcc_ready_probe`` hook."""
        status = self.status()
        if status is None:
            # Use the refreshed verdict directly rather than re-reading it
            # through the staleness filter, which would discard it again when
            # the TTL is zero.
            status = self.refresh().get("status")
        # Unknown verdicts stay optimistic: readiness must not flap on a
        # transient probe failure, only a confirmed block turns the bit red.
        return status != "blocked"

    def last_detail(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._detail)


@dataclass
class ZBrushServerOptions:
    """Adapter-local options for the dcc-mcp-core server contract."""

    port: Optional[int] = None
    extra_skill_paths: Optional[List[str]] = None
    server_name: str = SERVER_NAME
    server_version: str = SERVER_VERSION
    gateway_port: Optional[int] = None
    registry_dir: Optional[str] = None
    dcc_version: Optional[str] = None
    enable_gateway_failover: Optional[bool] = None
    mode: Optional[str] = None
    socket_host: str = "127.0.0.1"
    socket_port: int = 9876
    dcc_pid: Optional[int] = None
    dcc_window_title: Optional[str] = None

    def to_core_options(self) -> DccServerOptions:
        from dcc_mcp_zbrush import _env  # noqa: PLC0415

        return DccServerOptions.from_env(
            dcc_name=_DCC_NAME,
            builtin_skills_dir=_BUILTIN_SKILLS_DIR,
            port=self.port,
            server_name=self.server_name,
            server_version=self.server_version,
            gateway_port=self.gateway_port,
            registry_dir=self.registry_dir,
            dcc_version=self.dcc_version,
            enable_gateway_failover=_env.resolve_enable_gateway_failover(self.enable_gateway_failover),
            enable_file_logging=True,
            enable_telemetry=True,
            dcc_pid=self.dcc_pid,
            dcc_window_title=self.dcc_window_title or "ZBrush",
        )


class ZBrushMcpServer(DccServerBase):
    """MCP server embedded inside ZBrush or running as a sidecar bridge host."""

    def __init__(
        self,
        port: Optional[int] = None,
        extra_skill_paths: Optional[List[str]] = None,
        server_name: str = SERVER_NAME,
        server_version: str = SERVER_VERSION,
        gateway_port: Optional[int] = None,
        registry_dir: Optional[str] = None,
        dcc_version: Optional[str] = None,
        enable_gateway_failover: Optional[bool] = None,
        mode: Optional[str] = None,
        socket_host: str = "127.0.0.1",
        socket_port: int = 9876,
        options: Optional[ZBrushServerOptions] = None,
    ) -> None:
        from dcc_mcp_zbrush import _env  # noqa: PLC0415

        if options is None:
            options = ZBrushServerOptions(
                port=port,
                extra_skill_paths=extra_skill_paths,
                server_name=server_name,
                server_version=server_version,
                gateway_port=gateway_port,
                registry_dir=registry_dir,
                dcc_version=dcc_version,
                enable_gateway_failover=enable_gateway_failover,
                mode=mode,
                socket_host=socket_host,
                socket_port=socket_port,
            )

        super().__init__(options=options.to_core_options())

        self._extra_skill_paths: List[str] = list(options.extra_skill_paths or [])
        self._mode = _env.resolve_mode(options.mode)
        self._socket_host = options.socket_host
        self._socket_port = options.socket_port
        self._bridge = None
        self._readiness_probe: Optional[_HostReadinessProbe] = None
        self._readiness_binder: Optional[Any] = None

        if options.gateway_port == 0 or (
            options.gateway_port is None and not _env.resolve_enable_gateway_failover(options.enable_gateway_failover)
        ):
            self._config.gateway_port = 0

    def _version_string(self) -> str:
        return get_zbrush_version_string()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def port(self) -> int:
        if self._handle is not None:
            try:
                return int(self._handle.port)
            except Exception:
                pass
        return int(self._options.port)

    @property
    def mcp_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def _collect_skill_paths(self) -> List[str]:
        return self.collect_skill_search_paths(
            extra_paths=self._extra_skill_paths,
            filter_existing=True,
        )

    def _init_sidecar_bridge(self) -> None:
        if self._mode != "sidecar":
            return

        from dcc_mcp_zbrush import api  # noqa: PLC0415
        from dcc_mcp_zbrush.bridge import SocketBridge  # noqa: PLC0415

        self._bridge = SocketBridge(host=self._socket_host, port=self._socket_port)
        api.set_bridge(self._bridge)
        try:
            self._bridge.connect()
            logger.info(
                "SocketBridge connected to ZBrush plugin at %s:%d",
                self._socket_host,
                self._socket_port,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SocketBridge could not connect (%s). "
                "Install bridge/plugin/mcp_socket_bridge.py in ZBrush and restart.",
                exc,
            )
        self._install_readiness_probe()

    def _install_readiness_probe(self) -> None:
        """Teach core's readiness bit that a blocked ZBrush is not servable.

        Core defaults the ``dcc`` bit to true, which reports a stalled instance
        as ready. Publishing a probe makes ``/v1/readyz`` go red while the host
        main thread is stuck in a native call.
        """
        if self._bridge is None:
            return
        self._readiness_probe = _HostReadinessProbe(lambda: self._bridge)
        try:
            from dcc_mcp_core.readiness import AdapterReadinessBinder  # noqa: PLC0415

            self._readiness_binder = AdapterReadinessBinder(
                self,
                dcc_ready_probe=self._readiness_probe.is_dcc_ready,
            )
        except Exception as exc:  # noqa: BLE001 - readiness must never break startup
            logger.debug("readiness probe could not be published: %s", exc)
            self._readiness_binder = None

    def register_builtin_actions(
        self,
        extra_skill_paths: list[str] | None = None,
        include_bundled: bool = True,
        minimal_mode: MinimalModeConfig | None = None,
    ) -> None:
        from dcc_mcp_zbrush import _env, _executor  # noqa: PLC0415

        if minimal_mode is None and _env.resolve_minimal_mode_enabled():
            minimal_mode = build_minimal_mode_config()

        _executor.attach_inprocess_executor(self)

        super().register_builtin_actions(
            extra_skill_paths=extra_skill_paths,
            include_bundled=include_bundled,
            minimal_mode=minimal_mode,
        )

    def start(self, *, install_atexit_hook: bool = True) -> "ZBrushMcpServer":
        self._init_sidecar_bridge()
        super().start(install_atexit_hook=install_atexit_hook)
        logger.info(
            "ZBrushMcpServer started (%s mode) at %s",
            self._mode,
            self.mcp_url,
        )
        return self

    def stop(self) -> None:
        if self._bridge is not None:
            self._bridge.disconnect()
            self._bridge = None
        super().stop()

    def discover_skills(self, extra_paths: Optional[List[str]] = None) -> int:
        if self._handle is None:
            logger.warning("discover_skills called before server was started")
            return 0
        paths = self._collect_skill_paths()
        if extra_paths:
            paths = list(extra_paths) + paths
        return int(self._server.discover(extra_paths=paths, dcc_name=_DCC_NAME))

    def load_skill(self, skill_name: str) -> List[str]:
        if self._handle is None:
            raise RuntimeError("Server is not running — call start() first")
        return list(self._server.load_skill(skill_name))

    def list_skills(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if self._handle is None:
            return []
        return list(self._server.list_skills(status=status))


_server_instance: Optional[ZBrushMcpServer] = None


def get_server() -> Optional[ZBrushMcpServer]:
    return _server_instance


def start_server(
    port: Optional[int] = None,
    extra_skill_paths: Optional[List[str]] = None,
    gateway_port: Optional[int] = None,
    registry_dir: Optional[str] = None,
    mode: Optional[str] = None,
    socket_host: str = "127.0.0.1",
    socket_port: int = 9876,
    **kwargs: Any,
) -> ZBrushMcpServer:
    """Start the ZBrush MCP server singleton."""
    global _server_instance  # noqa: PLW0603

    if _server_instance is None:
        _server_instance = ZBrushMcpServer(
            port=port,
            extra_skill_paths=extra_skill_paths,
            gateway_port=gateway_port,
            registry_dir=registry_dir,
            mode=mode,
            socket_host=socket_host,
            socket_port=socket_port,
            **kwargs,
        )
        _server_instance.register_builtin_actions()
        _server_instance.start()
    return _server_instance


def stop_server() -> None:
    """Stop the ZBrush MCP server singleton."""
    global _server_instance  # noqa: PLW0603

    if _server_instance is not None:
        _server_instance.stop()
        _server_instance = None
