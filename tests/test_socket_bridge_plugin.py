"""Regression tests for the in-ZBrush socket bridge."""

from __future__ import annotations

import importlib.util
import json
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


def test_socket_client_timeout_exceeds_host_request_timeout() -> None:
    from dcc_mcp_zbrush.bridge import DEFAULT_TIMEOUT_SEC

    bridge = _load_bridge_plugin()

    assert DEFAULT_TIMEOUT_SEC > bridge._REQUEST_TIMEOUT_SECONDS
    assert bridge._REQUEST_TIMEOUT_SECONDS == 600.0


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


def test_bridge_rejects_parallel_dispatch_instead_of_queueing_sdk_work() -> None:
    bridge = _load_bridge_plugin()
    bridge._REQUEST_TIMEOUT_SECONDS = 0.1
    bridge._handle_zbrush_request = lambda _method, _params, req_id: {"id": req_id, "result": {"ok": True}}

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(bridge._dispatch_request, {"id": 1, "method": "execute_python"})
        while bridge._REQUEST_QUEUE.empty():
            time.sleep(0.001)

        status = bridge._route_request({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        busy = bridge._dispatch_request({"id": 2, "method": "get_scene_info"})
        bridge._drain_request_queue()

        assert status["result"] == {"ok": True, "busy": True, "active_method": "execute_python"}
        assert busy["error"]["code"] == -32001
        assert busy["error"]["data"]["retryable"] is True
        assert first.result(timeout=1) == {"id": 1, "result": {"ok": True}}


def test_bridge_timeout_keeps_request_slot_reserved_until_host_finishes() -> None:
    bridge = _load_bridge_plugin()
    bridge._REQUEST_TIMEOUT_SECONDS = 0.01
    bridge._handle_zbrush_request = lambda _method, _params, req_id: {"id": req_id, "result": {"ok": True}}

    timed_out = bridge._dispatch_request({"id": 1, "method": "execute_python"})
    busy = bridge._dispatch_request({"id": 2, "method": "execute_python"})

    assert timed_out["error"]["code"] == -32002
    assert timed_out["error"]["data"]["still_running"] is True
    assert busy["error"]["code"] == -32001

    bridge._drain_request_queue()


def test_bridge_ping_bypasses_main_thread_queue_and_reports_busy_state() -> None:
    bridge = _load_bridge_plugin()
    bridge._dispatch_request = MagicMock()

    response = bridge._route_request({"jsonrpc": "2.0", "id": 7, "method": "ping"})

    assert response["result"] == {"ok": True, "busy": False, "active_method": None}
    bridge._dispatch_request.assert_not_called()


def test_bridge_start_schedules_main_thread_pump_without_blocking(monkeypatch) -> None:
    bridge = _load_bridge_plugin()
    bridge_thread = MagicMock()
    bridge_thread.is_alive.return_value = True
    bridge._BRIDGE_THREAD = bridge_thread
    install_pump = MagicMock()
    monkeypatch.setattr(bridge, "_install_main_thread_pump", install_pump, raising=False)
    monkeypatch.setattr(
        bridge,
        "_run_main_thread_pump",
        MagicMock(side_effect=AssertionError("startup must return control to ZBrush")),
    )

    assert bridge._start_bridge("127.0.0.1", 9910) is bridge_thread
    install_pump.assert_called_once_with()


def test_windows_main_thread_timer_drains_requests(monkeypatch) -> None:
    import ctypes

    bridge = _load_bridge_plugin()
    drain = MagicMock()
    user32 = MagicMock()
    user32.SetTimer.return_value = 42
    monkeypatch.setattr(bridge, "_drain_request_queue", drain)
    monkeypatch.setattr(bridge.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *_args: lambda callback: callback, raising=False)
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=user32), raising=False)

    bridge._install_main_thread_pump()
    callback = user32.SetTimer.call_args.args[3]
    callback(None, 0, 42, 0)

    assert bridge._PUMP_TIMER_ID == 42
    assert bridge._PUMP_TIMER_CALLBACK is callback
    user32.SetTimer.assert_called_once_with(None, 0, 20, callback)
    drain.assert_called_once_with()


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


def test_bridge_bootstrap_failure_is_captured_for_lifecycle_verify(monkeypatch, tmp_path: Path) -> None:
    bridge = _load_bridge_plugin()
    error_log = tmp_path / "bootstrap-errors.jsonl"
    monkeypatch.setenv("DCC_MCP_ZBRUSH_BOOTSTRAP_ERRORS", str(error_log))
    monkeypatch.setattr(bridge, "_running_in_zbrush", lambda: True)
    monkeypatch.setattr(bridge, "_install_menu", lambda: True)
    monkeypatch.setattr(bridge, "_start_bridge", MagicMock(side_effect=RuntimeError("socket bind failed")))

    try:
        bridge.bootstrap_bridge()
    except RuntimeError:
        pass

    payload = json.loads(error_log.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["stage"] == "sidecar_bootstrap"
    assert payload["reason"] == "socket bind failed"
    assert payload["exception_type"] == "RuntimeError"


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
    mock_zbc.get_active_tool_path.return_value = r"F:\models\signal_forge"
    bridge._import_zbc = lambda: mock_zbc

    response = bridge._handle_zbrush_request(
        "refine_active_subtool",
        {"subdivision_levels": 2, "polish": 8, "inflate": 0.5},
        42,
    )

    assert response["result"]["subdivision_levels"] == 2
    assert response["result"]["subtool_name"] == "signal_forge"
    assert mock_zbc.press.call_args_list == [
        call("Tool:Geometry:Divide"),
        call("Tool:Geometry:Divide"),
    ]
    assert mock_zbc.set.call_args_list == [
        call("Tool:Deformation:Polish", 8.0),
        call("Tool:Deformation:Inflate", 0.5),
    ]


def test_bridge_dispatches_remesh_active_subtool_quietly() -> None:
    bridge = _load_bridge_plugin()
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.query_mesh3d.side_effect = ([5_000_000.0], [48_672.0])
    mock_zbc.get_active_tool_path.return_value = r"F:\models\fantasy-dragon-remeshed"
    bridge._import_zbc = lambda: mock_zbc

    response = bridge._handle_zbrush_request(
        "remesh_active_subtool",
        {"target_face_count": 50_000, "duplicate": True},
        43,
    )

    assert response["result"]["face_count_before"] == 5_000_000
    assert response["result"]["face_count_after"] == 48_672
    assert response["result"]["target_face_count"] == 50_000
    assert mock_zbc.press.call_args_list == [
        call("Tool:SubTool:Duplicate"),
        call("Tool:Geometry:ZRemesher"),
    ]
    mock_zbc.set.assert_called_once_with("Tool:Geometry:ZRemesher:Target Polygons Count", 50.0)
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]


def test_bridge_inspects_active_mesh_counts_uvs_and_bounds() -> None:
    bridge = _load_bridge_plugin()
    mock_zbc = MagicMock()
    mock_zbc.query_mesh3d.side_effect = lambda property_id: {
        0: [2_499_970.0],
        1: [5_000_000.0],
        2: [-0.8, -1.0, -0.6, 0.8, 1.0, 0.6],
        3: [0.0, 0.0, 1.0, 1.0],
        8: [37.79],
    }[property_id]
    mock_zbc.is_polymesh3d_solid.return_value = False
    mock_zbc.get_active_tool_path.return_value = r"F:\models\fantasy-dragon"
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._inspect_active_mesh()

    assert result == {
        "active_tool_path": r"F:\models\fantasy-dragon",
        "subtool_name": "fantasy-dragon",
        "point_count": 2_499_970,
        "face_count": 5_000_000,
        "bounds": [-0.8, -1.0, -0.6, 0.8, 1.0, 0.6],
        "uv_bounds": [0.0, 0.0, 1.0, 1.0],
        "has_uvs": True,
        "mesh_area": 37.79,
        "solid": False,
    }


def test_bridge_rejects_map_bake_without_uvs(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    output_path = tmp_path / "normal.tif"
    mock_zbc = MagicMock()
    mock_zbc.query_mesh3d.return_value = [0.0, 0.0, 0.0, 0.0]
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._bake_active_subtool_map("normal", str(output_path), 1024, 1024, True, 8)

    assert result["error"] == "UVS_MISSING"
    assert result["output_path"] == str(output_path)
    mock_zbc.create_normal_map.assert_not_called()
    mock_zbc.set_next_filename.assert_not_called()


def test_bridge_bakes_normal_map_quietly_and_atomically(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    output_path = tmp_path / "normal.tif"
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.query_mesh3d.return_value = [0.0, 0.0, 1.0, 1.0]
    mock_zbc.get.side_effect = lambda path: {
        "Tool:UV Map:UV Map Size": 2048.0,
        "Tool:UV Map:UV Map Border": 4.0,
        "Tool:Normal Map:SmoothUV": 0.0,
        "Tool:Normal Map:Tangent": 0.0,
    }[path]
    lower_res_enabled = iter((True, True, False))
    mock_zbc.is_enabled.side_effect = lambda path: (
        next(lower_res_enabled) if path == "Tool:Geometry:Lower Res" else True
    )
    state: dict[str, str] = {}
    mock_zbc.set_next_filename.side_effect = lambda path: state.update(path=path)

    def press(item_path: str) -> None:
        if item_path == "Texture:Export":
            Path(state["path"]).write_bytes(b"normal-map")

    mock_zbc.press.side_effect = press
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._bake_active_subtool_map("normal", str(output_path), 1024, 1024, True, 8)

    assert result["output_path"] == str(output_path)
    assert result["map_type"] == "normal"
    assert result["bytes"] == len(b"normal-map")
    assert output_path.read_bytes() == b"normal-map"
    mock_zbc.create_normal_map.assert_not_called()
    assert mock_zbc.press.call_args_list == [
        call("Tool:Geometry:Lower Res"),
        call("Tool:Geometry:Lower Res"),
        call("Tool:Normal Map:Create NormalMap"),
        call("Tool:Normal Map:Clone NM"),
        call("Texture:Export"),
        call("Tool:Geometry:Higher Res"),
        call("Tool:Geometry:Higher Res"),
    ]
    assert mock_zbc.set.call_args_list == [
        call("Tool:UV Map:UV Map Size", 1024),
        call("Tool:UV Map:UV Map Border", 8),
        call("Tool:Normal Map:SmoothUV", 1),
        call("Tool:Normal Map:Tangent", 1),
        call("Tool:UV Map:UV Map Size", 2048.0),
        call("Tool:UV Map:UV Map Border", 4.0),
        call("Tool:Normal Map:SmoothUV", 0.0),
        call("Tool:Normal Map:Tangent", 0.0),
    ]
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]


def test_bridge_bakes_displacement_map_through_alpha_palette(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    output_path = tmp_path / "displacement.tif"
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.is_enabled.side_effect = lambda path: path != "Tool:Geometry:Lower Res"
    mock_zbc.query_mesh3d.return_value = [0.0, 0.0, 1.0, 1.0]
    mock_zbc.get.side_effect = lambda path: {
        "Tool:UV Map:UV Map Size": 2048.0,
        "Tool:UV Map:UV Map Border": 4.0,
        "Tool:Displacement Map:SmoothUV": 1.0,
    }[path]
    state: dict[str, str] = {}
    mock_zbc.set_next_filename.side_effect = lambda path: state.update(path=path)

    def press(item_path: str) -> None:
        if item_path == "Alpha:Export":
            Path(state["path"]).write_bytes(b"displacement-map")

    mock_zbc.press.side_effect = press
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._bake_active_subtool_map("displacement", str(output_path), 512, 512, False, 2)

    assert result["bytes"] == len(b"displacement-map")
    assert output_path.read_bytes() == b"displacement-map"
    mock_zbc.create_displacement_map.assert_not_called()
    assert mock_zbc.press.call_args_list == [
        call("Tool:Displacement Map:Create DispMap"),
        call("Tool:Displacement Map:Clone Disp"),
        call("Alpha:Export"),
    ]
    assert mock_zbc.set.call_args_list == [
        call("Tool:UV Map:UV Map Size", 512),
        call("Tool:UV Map:UV Map Border", 2),
        call("Tool:Displacement Map:SmoothUV", 0),
        call("Tool:UV Map:UV Map Size", 2048.0),
        call("Tool:UV Map:UV Map Border", 4.0),
        call("Tool:Displacement Map:SmoothUV", 1.0),
    ]


def test_bridge_rejects_non_square_map_size(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    bridge._import_zbc = MagicMock()

    result = bridge._bake_active_subtool_map("normal", str(tmp_path / "normal.tif"), 1024, 512, True, 8)

    assert result["error"] == "NON_SQUARE_MAP"
    bridge._import_zbc.assert_not_called()


def test_bridge_reports_when_zbrush_does_not_create_a_map(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    output_path = tmp_path / "normal.tif"
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.is_enabled.side_effect = lambda path: (
        path
        not in {
            "Tool:Geometry:Lower Res",
            "Tool:Normal Map:Clone NM",
        }
    )
    mock_zbc.query_mesh3d.return_value = [0.0, 0.0, 1.0, 1.0]
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._bake_active_subtool_map("normal", str(output_path), 512, 512, True, 8)

    assert result["error"] == "MAP_NOT_CREATED"
    assert not output_path.exists()
    mock_zbc.set_next_filename.assert_not_called()


def test_bridge_creates_wrinkle_brush_on_host_thread(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    output = tmp_path / "WrinkleCrease.ZBP"
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.get.side_effect = lambda path: bridge._WRINKLE_SETTINGS[path]
    state: dict[str, str] = {}
    mock_zbc.set_next_filename.side_effect = lambda path: state.update(path=path)

    def press(item_path: str) -> None:
        if item_path == "Brush:Save As":
            Path(state["path"]).write_bytes(b"zbp")

    mock_zbc.press.side_effect = press
    bridge._import_zbc = lambda: mock_zbc

    response = bridge._handle_zbrush_request("create_wrinkle_brush", {"output_path": str(output)}, 43)

    assert response["result"]["bytes"] == 3
    assert output.read_bytes() == b"zbp"
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]


def test_bridge_restores_existing_brush_when_save_fails(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    output = tmp_path / "WrinkleCrease.ZBP"
    output.write_bytes(b"original")
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    bridge._import_zbc = lambda: mock_zbc

    response = bridge._handle_zbrush_request("create_wrinkle_brush", {"output_path": str(output)}, 44)

    assert response["error"]["code"] == -32000
    assert output.read_bytes() == b"original"


def test_bridge_rejects_empty_brush_path_before_resolving_it() -> None:
    bridge = _load_bridge_plugin()

    response = bridge._handle_zbrush_request("create_wrinkle_brush", {"output_path": ""}, 45)

    assert response["result"]["error"] == "BRUSH_PATH_MISSING"


def test_bridge_rejects_fbx_before_importing_zbrush_commands(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    asset_file = tmp_path / "asset.fbx"
    asset_file.write_bytes(b"fbx")
    bridge._import_zbc = MagicMock()

    result = bridge._import_to_scene(str(asset_file))

    assert result["error"] == "UNSUPPORTED_FORMAT"
    bridge._import_zbc.assert_not_called()


def test_bridge_duplicates_active_subtool_before_obj_import(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    asset_file = tmp_path / "asset.obj"
    asset_file.write_bytes(b"obj")
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    lower_res_enabled = iter((True, True, False))
    mock_zbc.is_enabled.side_effect = lambda path: (
        next(lower_res_enabled) if path == "Tool:Geometry:Lower Res" else True
    )
    mock_zbc.get_subtool_count.side_effect = [1, 2, 2]
    mock_zbc.get_active_tool_path.return_value = r"F:\models\asset"
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._import_to_scene(str(asset_file))

    assert result["success"] is True
    assert result["imported_nodes"] == ["asset"]
    assert (result["subtool_count_before"], result["subtool_count_after"]) == (1, 2)
    assert mock_zbc.press.call_args_list == [
        call("Tool:SubTool:Duplicate"),
        call("Tool:Geometry:Lower Res"),
        call("Tool:Geometry:Lower Res"),
        call("Tool:Geometry:Del Higher"),
        call("Tool:Import"),
    ]
    mock_zbc.freeze.assert_not_called()
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]


def test_bridge_imports_into_empty_tool_without_duplicate(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    asset_file = tmp_path / "asset.obj"
    asset_file.write_bytes(b"obj")
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = False
    mock_zbc.get_subtool_count.side_effect = [1, 1]
    mock_zbc.get_active_tool_path.return_value = "/ZBrush/asset"
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._import_to_scene(str(asset_file))

    assert result["success"] is True
    mock_zbc.press.assert_called_once_with("Tool:Import")
    mock_zbc.is_enabled.assert_not_called()


def test_bridge_aborts_import_when_duplicate_fails(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    asset_file = tmp_path / "asset.obj"
    asset_file.write_bytes(b"obj")
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.is_enabled.return_value = True
    mock_zbc.get_subtool_count.side_effect = [1, 1]
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._import_to_scene(str(asset_file))

    assert result["error"] == "SUBTOOL_CREATE_FAILED"
    mock_zbc.set_next_filename.assert_not_called()
    mock_zbc.press.assert_called_once_with("Tool:SubTool:Duplicate")
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]


def test_bridge_exports_without_drawing_ui_actions(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    output_path = tmp_path / "asset.obj"
    mock_zbc = MagicMock()
    mock_zbc.get_active_tool_path.return_value = r"F:\models\asset"
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._export_active_subtool_obj(str(output_path))

    assert result["subtool_name"] == "asset"
    mock_zbc.set_next_filename.assert_called_once_with(str(output_path))
    mock_zbc.press.assert_called_once_with("Tool:Export")
    mock_zbc.freeze.assert_not_called()
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]


def test_bridge_captures_turntable_and_restores_transform(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.get.return_value = 1
    base_transform = [480.0, 360.0, 0.0, 300.0, 300.0, 300.0, 5.0, 10.0, 175.0]
    mock_zbc.get_transform.return_value = base_transform
    state: dict[str, str] = {}
    mock_zbc.set_next_filename.side_effect = lambda path: state.update(path=path)

    def press(item_path: str) -> None:
        if item_path == "Document:Export":
            Path(state["path"]).write_bytes(b"psd")

    mock_zbc.press.side_effect = press
    bridge._import_zbc = lambda: mock_zbc

    response = bridge._handle_zbrush_request(
        "capture_turntable",
        {
            "output_dir": str(tmp_path),
            "angles": [0, 90],
            "prefix": "dragon",
            "bpr_render": True,
            "polyframe": True,
        },
        46,
    )

    assert [frame["angle"] for frame in response["result"]["frames"]] == [0.0, 90.0]
    assert (tmp_path / "dragon-000.psd").read_bytes() == b"psd"
    assert (tmp_path / "dragon-001.psd").read_bytes() == b"psd"
    assert mock_zbc.set_transform.call_args_list[-1] == call(*base_transform)
    assert [item.args[0] for item in mock_zbc.press_key.call_args_list] == ["SHIFT+F", "SHIFT+F"]
    assert all(callable(item.args[1]) for item in mock_zbc.press_key.call_args_list)
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]


def test_bridge_rejects_turntable_capture_outside_edit_mode(tmp_path) -> None:
    bridge = _load_bridge_plugin()
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.get.return_value = 0
    bridge._import_zbc = lambda: mock_zbc

    result = bridge._capture_turntable(str(tmp_path), [0], "dragon", False, False)

    assert result["error"] == "EDIT_MODE_REQUIRED"
    mock_zbc.get_transform.assert_not_called()
    mock_zbc.set_next_filename.assert_not_called()
