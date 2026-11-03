"""GH-110 / GH-111 regressions: sidecar host dispatch and host-origin provenance.

GH-110: in sidecar mode core routes every HTTP ``tools/call`` through the
dispatcher attached to ``McpHttpServer``. The adapter must register a split
``HostExecutionBridge`` (inline in-process executor + Rust host queue) and the
CLI must drain that queue. Without the pump, main-affinity tools fail with
``THREAD_AFFINITY_UNAVAILABLE``.

GH-111: ZBrush 2026.2+ builds ``zbrush.commands`` into the signed host
executable, so the module has no ``__file__``. ``os.path.abspath("")`` resolves
to the cwd and makes ``verify`` reject a correct install. Report the running
host executable instead, and never present a disk rollback as recovery of the
bridge already loaded in the host VM.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).parent.parent


def _load_bridge_plugin() -> ModuleType:
    path = _REPO_ROOT / "bridge" / "plugin" / "mcp_socket_bridge.py"
    spec = importlib.util.spec_from_file_location("gh111_mcp_socket_bridge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_session_info_skill() -> ModuleType:
    path = _REPO_ROOT / "src" / "dcc_mcp_zbrush" / "skills" / "zbrush-scripting" / "scripts" / "get_session_info.py"
    spec = importlib.util.spec_from_file_location("gh111_get_session_info", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _NoFileZbc:
    """Stand-in for the embedded ``zbrush.commands`` module (no ``__file__``)."""

    def zbrush_info(self, index: int) -> float:
        return {0: 2027.0, 1: 2.0}[index]

    def get_active_tool_path(self) -> str:
        return ""

    def get_subtool_count(self) -> int:
        return 0


class _NativeZbc(_NoFileZbc):
    """Stand-in for a build that ships ``commands.pyd`` as a native module."""

    def __init__(self, commands_pyd: Path) -> None:
        self.__file__ = str(commands_pyd)


# ---------------------------------------------------------------------------
# GH-110: split wiring
# ---------------------------------------------------------------------------


def test_sidecar_registers_split_host_execution_bridge() -> None:
    from dcc_mcp_core.host import BlockingDispatcher

    from dcc_mcp_zbrush import _executor

    class _FakeServer:
        mode = "sidecar"
        bridge = None

        def register_host_execution_bridge(self, bridge):
            self.bridge = bridge

        def register_inprocess_executor(self, dispatcher=None):
            raise AssertionError("sidecar must not register a plain in-process executor")

    server = _FakeServer()
    dispatcher = _executor.attach_inprocess_executor(server)
    try:
        assert isinstance(dispatcher, BlockingDispatcher)
        assert server.bridge is not None
        # Inline skill execution; a queue here would post a second job from
        # inside a drained job and reenter the runtime.
        assert server.bridge.dispatcher is None
        assert server.bridge.host_dispatcher is dispatcher
        # This is exactly the dispatcher core attaches to MCP HTTP.
        assert server.bridge.resolve_host_dispatcher() is dispatcher
    finally:
        dispatcher.shutdown()


def test_embedded_mode_keeps_inline_registration() -> None:
    from dcc_mcp_zbrush import _executor

    class _FakeServer:
        mode = "embedded"
        calls: list[str] = []

        def register_inprocess_executor(self, dispatcher=None):
            self.calls.append("inline")

        def register_host_execution_bridge(self, bridge):
            raise AssertionError("embedded mode must not build a Rust host queue")

    server = _FakeServer()
    assert _executor.attach_inprocess_executor(server) is None
    assert server.calls == ["inline"]


def test_inline_executor_does_not_reenter_the_host_queue() -> None:
    from dcc_mcp_core._server.inprocess_executor import HostExecutionBridge
    from dcc_mcp_core.host import BlockingDispatcher, StandaloneHost

    dispatcher = BlockingDispatcher()
    bridge = HostExecutionBridge(dispatcher=None, host_dispatcher=dispatcher)
    with StandaloneHost(dispatcher):
        # A main-affinity call is posted to the host queue by HTTP; the bridge
        # must execute the skill script inline on the draining thread instead
        # of posting a nested job into the same queue.
        handle = dispatcher.post(lambda: bridge.dispatch_callable(lambda: "ran-on-host-thread"))
        assert handle.wait(timeout=5.0) == "ran-on-host-thread"
        assert dispatcher.pending() == 0


def test_server_exposes_host_dispatcher_and_shuts_it_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from dcc_mcp_core.server_base import DccServerBase

    from dcc_mcp_zbrush import _executor
    from dcc_mcp_zbrush.server import ZBrushMcpServer

    dispatcher = MagicMock()
    monkeypatch.setattr(_executor, "attach_inprocess_executor", lambda _server: dispatcher)
    monkeypatch.setattr(DccServerBase, "register_builtin_actions", lambda self, **_kwargs: None)
    monkeypatch.setattr(DccServerBase, "stop", lambda self, **_kwargs: None)

    server = ZBrushMcpServer(mode="sidecar", gateway_port=0, port=0)
    assert server.host_dispatcher is None
    server.register_builtin_actions()
    assert server.host_dispatcher is dispatcher
    server.stop()
    dispatcher.shutdown.assert_called_once_with()
    assert server.host_dispatcher is None


# ---------------------------------------------------------------------------
# GH-110: CLI main-loop pump
# ---------------------------------------------------------------------------


def test_cli_pump_drains_jobs_on_the_calling_thread() -> None:
    from dcc_mcp_core.host import BlockingDispatcher

    from dcc_mcp_zbrush import cli

    dispatcher = BlockingDispatcher()
    executed: list[int] = []

    def job() -> None:
        executed.append(threading.get_ident())
        dispatcher.shutdown()

    dispatcher.post(job)
    cli.pump_host_dispatcher(dispatcher, timeout_ms=10)
    assert executed == [threading.get_ident()]
    assert dispatcher.is_shutdown()


def test_cli_pump_rejects_reentrant_use() -> None:
    from dcc_mcp_core.host import BlockingDispatcher

    from dcc_mcp_zbrush import cli

    dispatcher = BlockingDispatcher()
    try:
        assert cli._PUMP_GUARD.acquire(blocking=False)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                cli.pump_host_dispatcher(dispatcher, timeout_ms=10)
        finally:
            cli._PUMP_GUARD.release()
    finally:
        dispatcher.shutdown()


def test_cli_pump_recovers_from_a_transient_tick_failure() -> None:
    from dcc_mcp_zbrush import cli

    class _FlakyDispatcher:
        def __init__(self) -> None:
            self.ticks = 0
            self.shutdown_called = False

        def is_shutdown(self) -> bool:
            return self.ticks >= 3

        def tick_blocking(self, max_jobs, timeout_ms):
            self.ticks += 1
            if self.ticks == 1:
                raise OSError("transient")

    dispatcher = _FlakyDispatcher()
    cli.pump_host_dispatcher(dispatcher, timeout_ms=1)
    assert dispatcher.ticks == 3


def test_cli_pump_stops_after_repeated_tick_failures() -> None:
    from dcc_mcp_zbrush import cli

    class _BrokenDispatcher:
        def __init__(self) -> None:
            self.ticks = 0

        def is_shutdown(self) -> bool:
            return False

        def tick_blocking(self, max_jobs, timeout_ms):
            self.ticks += 1
            raise OSError("persistent")

    dispatcher = _BrokenDispatcher()
    with pytest.raises(RuntimeError, match="failed repeatedly"):
        cli.pump_host_dispatcher(dispatcher, timeout_ms=1)
    assert dispatcher.ticks == cli._MAX_CONSECUTIVE_TICK_FAILURES
    # The guard is always released so a later CLI run can pump again.
    assert cli._PUMP_GUARD.acquire(blocking=False)
    cli._PUMP_GUARD.release()


# ---------------------------------------------------------------------------
# GH-110: real MCP HTTP end-to-end regression (fake in-ZBrush bridge socket)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _mcp_post(port: int, body: dict, session_id: str | None, timeout: float = 20.0):
    payload = json.dumps(body).encode("utf-8")
    lines = [
        "POST /mcp HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Content-Type: application/json",
        "Accept: application/json, text/event-stream",
        f"Content-Length: {len(payload)}",
        "Connection: close",
    ]
    if session_id:
        lines.append(f"Mcp-Session-Id: {session_id}")
    sock = socket.create_connection(("127.0.0.1", port), timeout=3.0)
    sock.settimeout(timeout)
    raw = b""
    try:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + payload)
        deadline = time.time() + timeout
        while time.time() < deadline and len(raw) < 4_000_000:
            try:
                chunk = sock.recv(65536)
            except (socket.timeout, OSError):
                break
            if not chunk:
                break
            raw += chunk
    finally:
        sock.close()
    head, _, data = raw.partition(b"\r\n\r\n")
    sid = None
    for header in head.decode("utf-8", "replace").splitlines():
        if header.lower().startswith("mcp-session-id:"):
            sid = header.split(":", 1)[1].strip()
    text = data.decode("utf-8", "replace").strip()
    parsed = None
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
    else:
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    parsed = json.loads(line[5:].strip())
                    break
                except ValueError:
                    continue
    return parsed, sid or session_id, text


def test_mcp_http_initialize_tools_list_and_main_affinity_tools_call(tmp_path: Path) -> None:
    """Normal MCP HTTP transport must service main-affinity tools in sidecar mode."""
    from dcc_mcp_core import _core

    if not hasattr(_core.McpHttpServer, "attach_dispatcher"):
        pytest.skip("installed dcc-mcp-core has no HTTP host dispatcher support")

    bridge_port, mcp_port = _free_port(), _free_port()
    stop = threading.Event()
    seen_methods: list[str] = []
    bridge_results = {
        "ping": {"ok": True, "busy": False, "active_method": None},
        "get_scene_info": {
            "active_tool_path": "MCP_HTTP_REGRESSION_CUBE",
            "subtool_count": 2,
            "active_subtool_index": 0,
        },
    }
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", bridge_port))
    listener.listen(16)
    listener.settimeout(0.5)

    def serve_bridge() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.settimeout(2.0)
            try:
                data = conn.recv(65536)
                if not data:
                    continue
                request = json.loads(data.decode("utf-8", "replace").splitlines()[0])
                method = str(request.get("method", ""))
                seen_methods.append(method)
                reply = {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": bridge_results.get(method, {"ok": True}),
                }
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except (OSError, ValueError):
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    threading.Thread(target=serve_bridge, daemon=True).start()

    env = dict(os.environ)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    env.update(
        {
            "DCC_MCP_ZBRUSH_SOCKET_PORT": str(bridge_port),
            "DCC_MCP_ZBRUSH_SOCKET_HOST": "127.0.0.1",
            "DCC_MCP_ZBRUSH_AUTOSTART": "0",
            "DCC_MCP_DISABLE_JOB_PERSISTENCE": "1",
            "DCC_MCP_LOG_DIR": str(log_dir),
            "DCC_MCP_MARKETPLACE_OFFLINE": "1",
            "DCC_MCP_DISABLE_TELEMETRY": "1",
            "DCC_MCP_REGISTRY_LOCK_TIMEOUT_MS": "2000",
        }
    )
    log_file = tmp_path / "sidecar.log"
    log_fh = log_file.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dcc_mcp_zbrush.cli",
            "--mode",
            "sidecar",
            "--socket-port",
            str(bridge_port),
            "--socket-host",
            "127.0.0.1",
            "--gateway-port",
            "0",
            "--port",
            str(mcp_port),
        ],
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )

    try:
        deadline = time.time() + 90
        ready = False
        while time.time() < deadline:
            try:
                probe = socket.create_connection(("127.0.0.1", mcp_port), timeout=0.5)
                probe.close()
                ready = True
                break
            except OSError:
                if proc.poll() is not None:
                    break
                time.sleep(0.5)
        assert ready, f"sidecar did not bind /mcp: {log_file.read_text(encoding='utf-8', errors='replace')[-2000:]}"

        session_id = None
        init, session_id, _ = _mcp_post(
            mcp_port,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "gh110-regression", "version": "1"},
                },
            },
            session_id,
        )
        assert init is not None and "result" in init, init
        # A session header is optional in this core build; ``_mcp_post`` keeps
        # whatever the server returns and still exercises the same endpoint.

        _mcp_post(
            mcp_port,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            session_id,
        )
        tools, session_id, _ = _mcp_post(
            mcp_port, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, session_id
        )
        assert tools is not None and isinstance(tools["result"]["tools"], list)

        call, session_id, text = _mcp_post(
            mcp_port,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "zbrush_scene__get_scene_info", "arguments": {}},
            },
            session_id,
        )
        assert call is not None and "result" in call, text[:1000]
        assert not call["result"].get("isError"), text[:1000]
        assert "THREAD_AFFINITY_UNAVAILABLE" not in text
        assert "MCP_HTTP_REGRESSION_CUBE" in text, "tool result must come from the socket bridge"
        assert seen_methods == ["ping", "get_scene_info"], seen_methods
    finally:
        stop.set()
        listener.close()
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
            proc.wait(timeout=8)
        log_fh.close()


# ---------------------------------------------------------------------------
# GH-111: embedded zbrush.commands origin
# ---------------------------------------------------------------------------


def test_bridge_reports_host_executable_not_cwd_when_commands_has_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = _load_bridge_plugin()
    host_exe = tmp_path / "ZBrush.exe"
    host_exe.write_bytes(b"MZ")
    bridge_file = tmp_path / "dcc_mcp_zbrush_socket_bridge.py"
    bridge_file.write_text("# placeholder\n", encoding="utf-8")
    monkeypatch.setattr(bridge, "__file__", str(bridge_file), raising=False)
    monkeypatch.setattr(bridge, "_import_zbc", lambda: _NoFileZbc())
    monkeypatch.setattr(sys, "executable", str(host_exe))

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    session = bridge._get_session_info()
    assert session["zbrush_commands_origin"] == str(host_exe)
    assert session["zbrush_commands_origin_source"] == "host_executable"
    assert session["zbrush_commands_origin"] != str(cwd)
    assert "os.path.abspath" not in session["zbrush_commands_origin"]


def test_bridge_keeps_native_module_origin_when_present(tmp_path: Path) -> None:
    bridge = _load_bridge_plugin()
    commands_pyd = tmp_path / "zbrush" / "commands.pyd"
    commands_pyd.parent.mkdir(parents=True)
    commands_pyd.write_bytes(b"native")

    origin, source = bridge._zbrush_commands_origin(_NativeZbc(commands_pyd))
    assert origin == os.path.abspath(str(commands_pyd))
    assert source == "module_file"


def test_embedded_skill_reports_host_executable_not_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_session_info_skill()
    host_exe = tmp_path / "ZBrush.exe"
    host_exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "executable", str(host_exe))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("DCC_MCP_ZBRUSH_INSTALL_IDENTITY", raising=False)

    info = module._collect(_NoFileZbc())
    assert info["zbrush_commands_origin"] == str(host_exe.resolve())
    assert info["zbrush_commands_origin_source"] == "host_executable"
    assert info["zbrush_commands_origin"] != str(cwd.resolve())


def test_bridge_fails_closed_when_no_trusted_origin_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge_plugin()
    monkeypatch.setattr(sys, "executable", str(tmp_path / "does-not-exist.exe"))
    origin, source = bridge._zbrush_commands_origin(_NoFileZbc())
    assert origin == ""
    assert source == "unresolved"


def test_verify_accepts_host_executable_origin_and_rejects_a_cwd_lookalike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    dcc_path = tmp_path / "ZBrush 2027" / "ZBrush.exe"
    dcc_path.parent.mkdir(parents=True)
    dcc_path.write_bytes(b"MZ")
    receipt = {
        "dcc_path": str(dcc_path),
        "dcc_root": str(dcc_path.parent),
        "dcc_sha256": "a" * 64,
    }
    valid = {
        "zbrush_commands_origin": str(dcc_path),
        "zbrush_commands_origin_source": "host_executable",
    }
    origin, digest = lifecycle._zbrush_module_provenance(receipt, valid["zbrush_commands_origin"])
    lifecycle._validate_zbrush_commands_origin_source(receipt, valid, origin)
    assert digest == "a" * 64  # bound to the signed product digest

    cwd_like = {
        "zbrush_commands_origin": str(tmp_path / "cwd"),
        "zbrush_commands_origin_source": "host_executable",
    }
    (tmp_path / "cwd").mkdir()
    with pytest.raises(lifecycle.LifecycleFailure, match="not a native selected-product module"):
        lifecycle._zbrush_module_provenance(receipt, cwd_like["zbrush_commands_origin"])

    spoofed = {
        "zbrush_commands_origin": str(tmp_path / "other.exe"),
        "zbrush_commands_origin_source": "cwd",
    }
    with pytest.raises(lifecycle.LifecycleFailure, match="origin source is not trusted"):
        lifecycle._validate_zbrush_commands_origin_source(receipt, spoofed, dcc_path)

    legacy = {"zbrush_commands_origin": str(dcc_path)}
    lifecycle._validate_zbrush_commands_origin_source(receipt, legacy, origin)  # backward compatible


def test_rollback_explains_that_disk_restore_does_not_unload_the_loaded_bridge(
    tmp_path: Path,
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle
    from dcc_mcp_zbrush.install_lifecycle import LifecycleRequest

    request = LifecycleRequest(
        operation="verify",
        mode="sidecar",
        version="0.2.33",
        dcc_path=tmp_path / "ZBrush.exe",
        python_path=Path(sys.executable),
        asset_dir=tmp_path / "assets",
        yes=True,
    )
    steps = lifecycle._rollback_recovery_steps(request, {"dcc_path": str(tmp_path / "ZBrush.exe")})
    assert [step["id"] for step in steps] == [
        "restart-zbrush-after-rollback",
        "reinstall-after-rollback",
    ]
    assert "neither reloads it nor unloads it" in steps[0]["why"]
    assert all(step["command"] for step in steps)
