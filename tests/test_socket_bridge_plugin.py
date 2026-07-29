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

    bridge._REQUEST_QUEUE.put({"payload": {"id": 1}, "event": threading.Event()})
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
    calls: list[object] = []
    bridge_thread = object()

    monkeypatch.setattr(bridge, "_running_in_zbrush", lambda: True)
    monkeypatch.setattr(bridge, "_install_menu", lambda: calls.append("menu") or True)
    monkeypatch.setattr(
        bridge,
        "_start_bridge",
        lambda host, port: calls.append(("bridge", host, port)) or bridge_thread,
    )
    monkeypatch.setenv("DCC_MCP_ZBRUSH_SOCKET_HOST", "127.0.0.1")
    monkeypatch.setenv("DCC_MCP_ZBRUSH_SOCKET_PORT", "9910")

    assert bridge.bootstrap_bridge() is bridge_thread
    assert calls == ["menu", ("bridge", "127.0.0.1", 9910)]


def test_bridge_bootstrap_continues_when_menu_registration_fails(monkeypatch) -> None:
    bridge = _load_bridge_plugin()
    bridge_thread = object()

    monkeypatch.setattr(bridge, "_running_in_zbrush", lambda: True)
    monkeypatch.setattr(bridge, "_install_menu", MagicMock(side_effect=RuntimeError("SDK rejected palette")))
    start_bridge = MagicMock(return_value=bridge_thread)
    monkeypatch.setattr(bridge, "_start_bridge", start_bridge)

    assert bridge.bootstrap_bridge() is bridge_thread
    start_bridge.assert_called_once()


def test_bridge_registers_official_palette_and_buttons() -> None:
    bridge = _load_bridge_plugin()
    zbc = MagicMock()
    zbc.exists.return_value = False
    zbc.add_palette.return_value = True
    zbc.add_button.return_value = True

    assert bridge._install_menu(zbc) is True
    zbc.add_palette.assert_called_once_with("DCC MCP", docking_bar=1)
    assert [args.args[0] for args in zbc.add_button.call_args_list] == [
        "DCC MCP:Copy Instance ID",
        "DCC MCP:Server Info",
        "DCC MCP:About DCC MCP",
    ]


def test_bridge_menu_callbacks_accept_sender_and_dispatch_actions() -> None:
    bridge = _load_bridge_plugin()
    zbc = MagicMock()
    zbc.exists.return_value = False
    zbc.add_palette.return_value = True
    zbc.add_button.return_value = True
    bridge.dcc_mcp_copy_instance_id = MagicMock()
    bridge.dcc_mcp_show_server_info = MagicMock()
    bridge.dcc_mcp_show_about = MagicMock()

    assert bridge._install_menu(zbc) is True
    callbacks = [args.args[2] for args in zbc.add_button.call_args_list]
    for callback in callbacks:
        callback("DCC MCP:test")

    bridge.dcc_mcp_copy_instance_id.assert_called_once_with()
    bridge.dcc_mcp_show_server_info.assert_called_once_with()
    bridge.dcc_mcp_show_about.assert_called_once_with()


def test_bridge_qt_fallback_imports_pyside6_qtwidgets(monkeypatch) -> None:
    bridge = _load_bridge_plugin()
    qt6_widgets = MagicMock()
    imported: list[str] = []

    def import_module(name: str):
        imported.append(name)
        if name == "PySide2.QtWidgets":
            raise ImportError(name)
        return qt6_widgets

    monkeypatch.setattr(bridge, "_import_zbc", MagicMock(side_effect=ImportError))
    monkeypatch.setattr(bridge.importlib, "import_module", import_module)

    bridge._show_message("Title", "Body")

    assert imported == ["PySide2.QtWidgets", "PySide6.QtWidgets"]
    qt6_widgets.QMessageBox.information.assert_called_once_with(None, "Title", "Body")


def test_standalone_plugin_instance_id_fails_closed(monkeypatch) -> None:
    bridge = _load_bridge_plugin()
    show_message = MagicMock()
    monkeypatch.setattr(bridge, "_show_message", show_message)
    monkeypatch.setenv("DCC_MCP_INSTANCE_ID", "must-not-be-used")

    bridge._fallback_copy_instance_id()

    assert "dcc-mcp-cli list" in show_message.call_args.args[1]


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


def test_bridge_rejects_fbx_before_importing_zbrush_commands(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    asset_file = tmp_path / "asset.fbx"
    asset_file.write_bytes(b"fbx")
    bridge._import_zbc = MagicMock()

    result = bridge._import_to_scene(str(asset_file))

    assert result["error"] == "UNSUPPORTED_FORMAT"
    bridge._import_zbc.assert_not_called()
