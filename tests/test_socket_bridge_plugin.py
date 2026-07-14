"""Regression tests for the in-ZBrush socket bridge."""

from __future__ import annotations

import importlib.util
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, call


def _load_bridge_plugin() -> ModuleType:
    path = Path(__file__).parent.parent / "bridge" / "plugin" / "mcp_socket_bridge.py"
    spec = importlib.util.spec_from_file_location("mcp_socket_bridge_plugin", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zbrush_sdk_requests_are_serialized() -> None:
    bridge = _load_bridge_plugin()
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_get_scene_info() -> dict[str, int]:
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1
        return {"subtool_count": 1}

    bridge._get_scene_info = fake_get_scene_info
    payloads = [{"jsonrpc": "2.0", "id": request_id, "method": "get_scene_info"} for request_id in range(2)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(bridge._handle_request, payloads))

    assert [response["id"] for response in responses] == [0, 1]
    assert max_active == 1


def test_bridge_dispatches_zbrush_request_on_queue_drain_thread() -> None:
    bridge = _load_bridge_plugin()
    handler_threads: list[threading.Thread] = []

    def handle(payload: dict[str, object]) -> dict[str, object]:
        handler_threads.append(threading.current_thread())
        return {"id": payload["id"], "result": {"ok": True}}

    bridge._handle_request = handle
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(bridge._dispatch_request, {"id": 7})
        while bridge._REQUEST_QUEUE.empty():
            time.sleep(0.001)
        bridge._drain_request_queue()
        response = future.result(timeout=1)

    assert response == {"id": 7, "result": {"ok": True}}
    assert handler_threads == [threading.current_thread()]


def test_main_thread_pump_drains_requests_and_updates_zbrush() -> None:
    bridge = _load_bridge_plugin()
    calls: list[tuple[str, object]] = []

    class _StopPump(Exception):
        pass

    class _ZBrushCommands:
        @staticmethod
        def update(*, redraw_ui: bool) -> None:
            calls.append(("update", redraw_ui))
            raise _StopPump

    bridge._REQUEST_QUEUE.put(
        {"payload": {"id": 1}, "event": threading.Event()}
    )
    bridge._handle_request = lambda payload: calls.append(("request", payload)) or {}
    bridge._import_zbc = lambda: _ZBrushCommands()

    try:
        bridge._run_main_thread_pump()
    except _StopPump:
        pass

    assert calls == [("request", {"id": 1}), ("update", True)]


def test_bridge_bootstraps_when_loaded_by_zbrush(monkeypatch) -> None:
    """ZBrush plugin scanning does not guarantee a main module name."""
    bridge = _load_bridge_plugin()
    calls: list[tuple[str, int]] = []
    bridge_thread = object()

    monkeypatch.setattr(bridge, "_running_in_zbrush", lambda: True)
    monkeypatch.setattr(
        bridge,
        "_start_bridge",
        lambda host, port: calls.append((host, port)) or bridge_thread,
    )
    monkeypatch.setenv("DCC_MCP_ZBRUSH_SOCKET_HOST", "127.0.0.1")
    monkeypatch.setenv("DCC_MCP_ZBRUSH_SOCKET_PORT", "9910")

    assert bridge.bootstrap_bridge() is bridge_thread
    assert calls == [("127.0.0.1", 9910)]


def test_bridge_dispatches_refine_active_subtool_on_host_thread() -> None:
    bridge = _load_bridge_plugin()
    mock_zbc = MagicMock()
    mock_zbc.get_active_tool_path.return_value = "/ZBrush/signal_forge.ZTL"
    bridge._import_zbc = lambda: mock_zbc

    response = bridge._handle_zbrush_request(
        "refine_active_subtool",
        {"subdivision_levels": 2, "polish": 8, "inflate": 0.5},
        42,
    )

    assert response["result"]["subdivision_levels"] == 2
    assert mock_zbc.press.call_args_list == [
        call("Tool:Geometry:Divide"),
        call("Tool:Geometry:Divide"),
    ]
    assert mock_zbc.set.call_args_list == [
        call("Tool:Deformation:Polish", 8.0),
        call("Tool:Deformation:Inflate", 0.5),
    ]
