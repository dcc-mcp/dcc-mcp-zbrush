"""Regression tests for the in-ZBrush socket bridge."""

from __future__ import annotations

import importlib.util
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


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


class _StopPump(Exception):
    pass


class _FakeServer:
    def __init__(self, accept_result: object) -> None:
        self.accept_result = accept_result
        self.timeout: float | None = None

    def __enter__(self) -> _FakeServer:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def setsockopt(self, *_args: object) -> None:
        return None

    def bind(self, *_args: object) -> None:
        return None

    def listen(self, *_args: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def accept(self) -> object:
        if isinstance(self.accept_result, BaseException):
            raise self.accept_result
        return self.accept_result


def test_bridge_listener_pumps_zbrush_ui_on_main_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge_plugin()
    server = _FakeServer(bridge.socket.timeout())
    update_threads: list[threading.Thread] = []

    def update(*, redraw_ui: bool) -> None:
        assert redraw_ui is True
        update_threads.append(threading.current_thread())
        raise _StopPump

    monkeypatch.setattr(bridge.socket, "socket", lambda *_args: server)
    monkeypatch.setattr(bridge, "_import_zbc", lambda: SimpleNamespace(update=update))

    with pytest.raises(_StopPump):
        bridge._serve_forever("127.0.0.1", 9910)

    assert server.timeout == pytest.approx(0.02)
    assert update_threads == [threading.current_thread()]


def test_bridge_processes_client_on_zbrush_main_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge_plugin()
    connection = object()
    server = _FakeServer((connection, ("127.0.0.1", 50000)))
    client_threads: list[threading.Thread] = []

    def serve_client(received: object) -> None:
        assert received is connection
        client_threads.append(threading.current_thread())

    def update(*, redraw_ui: bool) -> None:
        assert redraw_ui is True
        raise _StopPump

    monkeypatch.setattr(bridge.socket, "socket", lambda *_args: server)
    monkeypatch.setattr(bridge, "_serve_client", serve_client)
    monkeypatch.setattr(bridge, "_import_zbc", lambda: SimpleNamespace(update=update))

    with pytest.raises(_StopPump):
        bridge._serve_forever("127.0.0.1", 9910)

    assert client_threads == [threading.current_thread()]


def test_bridge_keeps_pumping_after_malformed_client(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge_plugin()
    server = _FakeServer((object(), ("127.0.0.1", 50000)))
    updates: list[bool] = []

    def update(*, redraw_ui: bool) -> None:
        updates.append(redraw_ui)
        raise _StopPump

    monkeypatch.setattr(bridge.socket, "socket", lambda *_args: server)
    monkeypatch.setattr(bridge, "_serve_client", lambda _conn: (_ for _ in ()).throw(ValueError("bad request")))
    monkeypatch.setattr(bridge, "_import_zbc", lambda: SimpleNamespace(update=update))

    with pytest.raises(_StopPump):
        bridge._serve_forever("127.0.0.1", 9910)

    assert updates == [True]


def test_bridge_bootstraps_when_loaded_by_zbrush(monkeypatch: pytest.MonkeyPatch) -> None:
    """ZBrush plugin scanning does not guarantee a main module name."""
    bridge = _load_bridge_plugin()
    calls: list[tuple[str, int]] = []

    monkeypatch.setattr(bridge, "_running_in_zbrush", lambda: True)
    monkeypatch.setattr(bridge, "_serve_forever", lambda host, port: calls.append((host, port)))
    monkeypatch.setenv("DCC_MCP_ZBRUSH_SOCKET_HOST", "127.0.0.1")
    monkeypatch.setenv("DCC_MCP_ZBRUSH_SOCKET_PORT", "9910")

    assert bridge.bootstrap_bridge() is True
    assert calls == [("127.0.0.1", 9910)]
