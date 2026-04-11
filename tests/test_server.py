"""Basic tests for ZBrushMcpServer (without real ZBrush)."""
from __future__ import annotations

import pytest


def test_import():
    import dcc_mcp_zbrush

    assert dcc_mcp_zbrush.__version__ == "0.1.0"


def test_api_imports():
    from dcc_mcp_zbrush import (
        ZBrushBridge,
        ZBrushMcpServer,
        is_zbrush_available,
        start_server,
        stop_server,
        zb_error,
        zb_success,
    )

    assert callable(ZBrushMcpServer)
    assert callable(ZBrushBridge)
    assert callable(zb_success)
    assert callable(zb_error)
    assert callable(is_zbrush_available)


def test_is_zbrush_available_false_when_disconnected():
    from dcc_mcp_zbrush import is_zbrush_available

    assert is_zbrush_available() is False


def test_zb_success():
    from dcc_mcp_zbrush import zb_success

    r = zb_success("sculpt done", polygon_count=50000)
    assert r["success"] is True
    assert r["message"] == "sculpt done"


def test_zb_error():
    from dcc_mcp_zbrush import zb_error

    r = zb_error("failed", error="ConnectionError: ZBrush HTTP not running")
    assert r["success"] is False


def test_bridge_not_connected_raises():
    from dcc_mcp_zbrush.api import ZBrushNotAvailableError, get_bridge

    with pytest.raises(ZBrushNotAvailableError):
        get_bridge()


def test_bridge_execute_raises_not_implemented():
    from dcc_mcp_zbrush.bridge import ZBrushBridge

    bridge = ZBrushBridge()
    bridge._client = object()  # simulate connected
    with pytest.raises(NotImplementedError):
        bridge.execute_zscript("[IButton,test]")
