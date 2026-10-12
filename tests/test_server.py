"""Basic tests for dcc-mcp-zbrush (no live ZBrush required)."""

from __future__ import annotations

import json
from importlib.metadata import version
from unittest.mock import MagicMock, patch

import pytest


def test_import():
    import dcc_mcp_zbrush

    assert dcc_mcp_zbrush.__version__ == version("dcc-mcp-zbrush")


def test_api_imports():
    from dcc_mcp_zbrush import (
        SocketBridge,
        ZBrushMcpServer,
        is_zbrush_available,
        zb_error,
        zb_success,
    )

    assert callable(ZBrushMcpServer)
    assert callable(SocketBridge)
    assert callable(zb_success)
    assert callable(zb_error)
    assert callable(is_zbrush_available)


def test_is_zbrush_available_false_outside_host():
    from dcc_mcp_zbrush import is_zbrush_available

    assert is_zbrush_available() is False


def test_zb_success():
    from dcc_mcp_zbrush import zb_success

    result = zb_success("sculpt done", polygon_count=50000)
    assert result["success"] is True
    assert result["message"] == "sculpt done"


def test_zb_error():
    from dcc_mcp_zbrush import zb_error

    result = zb_error("failed", error="ConnectionError")
    assert result["success"] is False


def test_get_bridge_raises_when_disconnected():
    from dcc_mcp_zbrush.api import ZBrushNotAvailableError, get_bridge, set_bridge

    set_bridge(None)
    try:
        with pytest.raises(ZBrushNotAvailableError):
            get_bridge()
    finally:
        set_bridge(None)


def test_get_bridge_reconnects_retained_sidecar():
    from dcc_mcp_zbrush.api import get_bridge, set_bridge

    bridge = MagicMock()
    bridge.is_connected.return_value = False
    set_bridge(bridge)
    try:
        assert get_bridge() is bridge
    finally:
        set_bridge(None)

    bridge.connect.assert_called_once_with()


def test_resolve_mode_defaults_to_sidecar_outside_zbrush():
    from dcc_mcp_zbrush._env import resolve_mode

    assert resolve_mode() == "sidecar"


@pytest.mark.parametrize(("port", "expected"), [(None, 18765), (0, 0)])
def test_server_dynamic_port_and_explicit_zero_override_env(monkeypatch, port, expected):
    from dcc_mcp_zbrush import ZBrushMcpServer

    monkeypatch.setenv("DCC_MCP_ZBRUSH_PORT", "18765")
    server = ZBrushMcpServer(port=port, mode="sidecar")
    assert server._config.port == expected


def test_socket_bridge_ping(monkeypatch):
    from dcc_mcp_zbrush.bridge import SocketBridge

    bridge = SocketBridge(host="127.0.0.1", port=9876)

    def fake_send(data: bytes, timeout: float | None = None) -> bytes:
        payload = json.loads(data.decode("utf-8"))
        assert payload["method"] == "ping"
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode("utf-8")

    monkeypatch.setattr(bridge, "_send", fake_send)
    bridge.connect()
    assert bridge.is_connected()


def test_socket_bridge_transport_failure_clears_connection(monkeypatch):
    from dcc_mcp_zbrush.bridge import SocketBridge

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    bridge._connected = True
    monkeypatch.setattr(bridge, "_send", MagicMock(side_effect=ConnectionError("gone")))

    with pytest.raises(ConnectionError, match="gone"):
        bridge.call("get_session_info")

    assert bridge.is_connected() is False


def test_bridge_busy_error_remains_retryable_at_tool_boundary(monkeypatch):
    from dcc_mcp_zbrush import api
    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridgeError

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    bridge._connected = True
    response = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32001,
            "message": "ZBrush bridge is busy with execute_python",
            "data": {"retryable": True, "active_method": "execute_python"},
        },
    }
    monkeypatch.setattr(bridge, "_send", lambda _data: json.dumps(response).encode("utf-8"))

    with pytest.raises(ZBrushBridgeError) as caught:
        bridge.call("get_scene_info")
    assert caught.value.code == -32001
    assert caught.value.data == {"retryable": True, "active_method": "execute_python"}

    monkeypatch.setattr(api, "_bridge", bridge)

    @api.with_zbrush
    def call_busy(**_kwargs):
        return api.get_bridge().call("get_scene_info")

    result = call_busy()
    assert result["error"] == "ZBRUSH_BUSY"
    assert result["context"]["retryable"] is True
    assert result["context"]["active_method"] == "execute_python"


def _ping_reply(busy: bool = False, active_method=None) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "busy": busy, "active_method": active_method}}
    ).encode("utf-8")


def test_probe_reports_ready_when_zbrush_answers_idle(monkeypatch):
    from dcc_mcp_zbrush.bridge import STATUS_READY, SocketBridge

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    monkeypatch.setattr(bridge, "_send", lambda _data, **_kw: _ping_reply(busy=False))

    verdict = bridge.probe()

    assert verdict["status"] == STATUS_READY
    assert verdict["endpoint"] == "127.0.0.1:9876"


def test_probe_reports_busy_when_a_request_is_in_flight(monkeypatch):
    from dcc_mcp_zbrush.bridge import STATUS_BUSY, SocketBridge

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    monkeypatch.setattr(bridge, "_send", lambda _data, **_kw: _ping_reply(busy=True, active_method="execute_python"))

    verdict = bridge.probe()

    assert verdict["status"] == STATUS_BUSY
    assert verdict["active_method"] == "execute_python"


def test_probe_reports_blocked_when_zbrush_stops_answering(monkeypatch):
    from dcc_mcp_zbrush.bridge import STATUS_BLOCKED, SocketBridge

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    monkeypatch.setattr(bridge, "_send", MagicMock(side_effect=TimeoutError("no reply")))

    verdict = bridge.probe()

    assert verdict["status"] == STATUS_BLOCKED
    # The verdict must not claim a dialog: a long native call looks the same.
    assert "not proof of a dialog" in verdict["message"]


def test_probe_uses_the_short_timeout(monkeypatch):
    from dcc_mcp_zbrush.bridge import PING_TIMEOUT_SEC, SocketBridge

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    seen = {}

    def fake_send(_data, timeout=None):
        seen["timeout"] = timeout
        return _ping_reply()

    monkeypatch.setattr(bridge, "_send", fake_send)
    bridge.probe()

    assert seen["timeout"] == PING_TIMEOUT_SEC
    assert PING_TIMEOUT_SEC < SocketBridge(host="127.0.0.1")._timeout


def test_normal_calls_keep_the_long_timeout(monkeypatch):
    from dcc_mcp_zbrush.bridge import DEFAULT_TIMEOUT_SEC, SocketBridge

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    seen = {}

    def fake_send(_data, timeout=None):
        seen["timeout"] = timeout
        return _ping_reply()

    monkeypatch.setattr(bridge, "_send", fake_send)
    bridge.call("ping")

    # Normal calls pass no timeout, so they fall back to the long default.
    assert seen["timeout"] is None
    assert bridge._timeout == DEFAULT_TIMEOUT_SEC


def test_connect_raises_blocked_instead_of_half_connecting(monkeypatch):
    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBlockedError

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    monkeypatch.setattr(bridge, "_send", MagicMock(side_effect=TimeoutError("no reply")))

    with pytest.raises(ZBrushBlockedError):
        bridge.connect()

    assert bridge.is_connected() is False


def test_blocked_error_maps_to_zbrush_blocked_at_tool_boundary():
    from dcc_mcp_zbrush import api
    from dcc_mcp_zbrush.bridge import ZBrushBlockedError

    @api.with_zbrush
    def call_blocked(**_kwargs):
        # A real verdict carries its own "message" key, which must not collide
        # with zb_error's message argument.
        raise ZBrushBlockedError(
            "stuck",
            detail={"status": "blocked", "message": "verdict text", "endpoint": "127.0.0.1:9876"},
        )

    result = call_blocked()

    assert result["error"] == "ZBRUSH_BLOCKED"
    # The verdict is nested wholesale, not splatted into the tool context.
    assert result["context"]["blocked"]["status"] == "blocked"
    assert result["context"]["blocked"]["message"] == "verdict text"
    # Hard constraint: never auto-answer and never blindly retry.
    assert "Do not retry automatically" in result["prompt"]


def test_probe_reports_unreachable_when_nothing_listens(monkeypatch):
    """A missing plugin must not be diagnosed as a blocked main thread."""
    from dcc_mcp_zbrush.bridge import STATUS_UNREACHABLE, SocketBridge

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    monkeypatch.setattr(bridge, "_send", MagicMock(side_effect=ConnectionRefusedError("refused")))

    verdict = bridge.probe()

    assert verdict["status"] == STATUS_UNREACHABLE
    assert "listening" in verdict["message"]
    # The blocked wording must not appear: it would misdirect troubleshooting.
    assert "native call" not in verdict["message"]


def test_connect_reports_unreachable_not_blocked(monkeypatch):
    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushNotAvailableError

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    monkeypatch.setattr(bridge, "_send", MagicMock(side_effect=ConnectionRefusedError("refused")))

    with pytest.raises(ZBrushNotAvailableError):
        bridge.connect()

    assert bridge.is_connected() is False


def test_probe_does_not_mutate_the_shared_timeout(monkeypatch):
    """Probes run concurrently with tool calls; the timeout must not leak."""
    from dcc_mcp_zbrush.bridge import DEFAULT_TIMEOUT_SEC, PING_TIMEOUT_SEC, SocketBridge

    bridge = SocketBridge(host="127.0.0.1", port=9876)
    seen = {}

    def fake_send(_data, timeout=None):
        seen["timeout"] = timeout
        return _ping_reply()

    monkeypatch.setattr(bridge, "_send", fake_send)
    bridge.probe()

    assert seen["timeout"] == PING_TIMEOUT_SEC
    assert bridge._timeout == DEFAULT_TIMEOUT_SEC


def test_install_readiness_probe_leaves_core_default_intact():
    """Regression: installing a probe used to pin /v1/readyz to a permanent 503.

    Core 0.20.42 does not let an adapter drive the ``dcc`` bit, so the adapter
    must not publish a probe at all. Verified against a live server: with a
    probe published, every bit except process/skill_catalog went false and
    readyz returned 503 even with a healthy host.
    """
    from dcc_mcp_zbrush.server import ZBrushMcpServer

    server = object.__new__(ZBrushMcpServer)
    server._bridge = MagicMock()

    assert server._install_readiness_probe() is None
    assert not hasattr(server, "_readiness_binder")


def test_readyz_reports_healthy_sidecar():
    """End-to-end: a sidecar with no ZBrush must still report ready, as on main."""
    import time
    import urllib.error
    import urllib.request

    from dcc_mcp_zbrush import ZBrushMcpServer

    server = ZBrushMcpServer(port=0, mode="sidecar", socket_port=19959, gateway_port=0)
    server.register_builtin_actions()
    server.start(install_atexit_hook=False)
    try:
        time.sleep(1.0)
        url = server.mcp_url.replace("/mcp", "/v1/readyz")
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                status = response.status
                body = response.read().decode()
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read().decode()
        assert status == 200, body
    finally:
        server.stop()


def test_sidecar_retains_bridge_when_initial_connect_fails():
    from dcc_mcp_zbrush.server import ZBrushMcpServer

    server = object.__new__(ZBrushMcpServer)
    server._mode = "sidecar"
    server._socket_host = "127.0.0.1"
    server._socket_port = 9876
    bridge = MagicMock()
    bridge.connect.side_effect = ConnectionError("not started yet")

    with (
        patch("dcc_mcp_zbrush.bridge.SocketBridge", return_value=bridge),
        patch("dcc_mcp_zbrush.api.set_bridge") as set_bridge,
    ):
        server._init_sidecar_bridge()

    set_bridge.assert_called_once_with(bridge)


def test_minimal_mode_config():
    from dcc_mcp_zbrush._skill_loader import MINIMAL_SKILLS, build_minimal_mode_config

    cfg = build_minimal_mode_config()
    assert tuple(cfg.skills) == MINIMAL_SKILLS
    assert cfg.deactivate_groups == {}


def test_sidecar_executor_runs_skill_scripts_in_server_process():
    from dcc_mcp_zbrush._executor import attach_inprocess_executor

    server = MagicMock()

    result = attach_inprocess_executor(server)

    assert result is None
    server.register_inprocess_executor.assert_called_once_with()


@patch("dcc_mcp_zbrush.server.ZBrushMcpServer.start")
@patch("dcc_mcp_zbrush.server.ZBrushMcpServer.register_builtin_actions")
def test_start_server_singleton(mock_register, mock_start):
    import dcc_mcp_zbrush.server as server_mod
    from dcc_mcp_zbrush import start_server, stop_server

    server_mod._server_instance = None
    mock_start.return_value = MagicMock(mode="sidecar", mcp_url="http://127.0.0.1:8765/mcp")

    handle = start_server(port=8765, mode="sidecar")
    assert handle is not None
    mock_register.assert_called_once()
    mock_start.assert_called_once()

    stop_server()
    assert server_mod._server_instance is None
