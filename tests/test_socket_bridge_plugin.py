"""Regression tests for the in-ZBrush socket bridge."""

from __future__ import annotations

import importlib.util
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType


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
