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

    def fake_send(data: bytes) -> bytes:
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
