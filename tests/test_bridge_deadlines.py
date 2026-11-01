"""Plugin queue-deadline regression tests; these do not reproduce a native ZBrush modal.

Covers the in-ZBrush main-thread pump: wire-deadline admission, monotonic queue
expiry, cancelled tombstones, busy/unknown outcomes, and abort safety. A client
that omits ``deadline_unix_ms`` still gets the plugin's local queue timeout, so
these guards do not depend on a particular sidecar client. The client's
blocked-host classification (``probe`` / ``ZBrushBlockedError``) and busy-retry
behaviour are covered by ``tests/test_server.py``.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from tests.test_socket_bridge_plugin import _load_bridge_plugin


def test_expired_queued_request_does_not_execute_after_pump_resumes():
    plugin = _load_bridge_plugin()
    plugin._REQUEST_TIMEOUT_SECONDS = 0.01
    handler = MagicMock(return_value={"result": {"ok": True}})
    plugin._handle_request = handler

    result = plugin._dispatch_request({"id": 1, "method": "execute_python"})
    plugin._drain_request_queue()

    handler.assert_not_called()
    assert result["error"]["code"] == -32004
    assert result["error"]["data"]["executed"] is False
    assert plugin._ACTIVE_REQUEST is None


def test_wire_deadline_rejects_request_delayed_before_plugin_accept():
    plugin = _load_bridge_plugin()
    plugin._REQUEST_TIMEOUT_SECONDS = 0.01
    handler = MagicMock()
    plugin._handle_request = handler

    result = plugin._dispatch_request(
        {"id": 1, "method": "execute_python", "deadline_unix_ms": (time.time() - 1) * 1000}
    )
    plugin._drain_request_queue()

    handler.assert_not_called()
    assert result["error"]["code"] == -32004
    assert plugin._REQUEST_QUEUE.empty()


def test_started_request_remains_busy_after_wait_timeout():
    plugin = _load_bridge_plugin()
    plugin._REQUEST_TIMEOUT_SECONDS = 0.3
    entered = threading.Event()
    release = threading.Event()

    def handle(payload):
        entered.set()
        assert release.wait(2)
        return {"id": payload["id"], "result": {"ok": True}}

    plugin._handle_request = handle
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(plugin._dispatch_request, {"id": 1, "method": "execute_python"})
        deadline = time.monotonic() + 1
        while plugin._REQUEST_QUEUE.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        pump = pool.submit(plugin._drain_request_queue)
        assert entered.wait(1)
        try:
            response = request.result(timeout=1)
            assert response["error"]["code"] == -32002
            assert response["error"]["data"]["retryable"] is False
            assert plugin._dispatch_request({"id": 2})["error"]["code"] == -32001
        finally:
            release.set()
        pump.result(timeout=1)
    assert plugin._ACTIVE_REQUEST is None


def test_expiry_is_checked_on_pump_even_before_waiter_can_cancel():
    plugin = _load_bridge_plugin()
    handler = MagicMock()
    plugin._handle_request = handler
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(plugin._dispatch_request, {"id": 1, "method": "execute_python"})
        deadline = time.monotonic() + 1
        while plugin._REQUEST_QUEUE.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        with plugin._REQUEST_STATE_LOCK:
            plugin._ACTIVE_REQUEST["deadline"] = time.monotonic() - 1
        plugin._drain_request_queue()
        assert request.result(timeout=1)["error"]["code"] == -32004
    handler.assert_not_called()


def test_cancelled_queue_entries_stay_bounded_and_do_not_clear_new_active_request():
    plugin = _load_bridge_plugin()
    plugin._REQUEST_TIMEOUT_SECONDS = 0.001
    for request_id in range(10):
        assert plugin._dispatch_request({"id": request_id})["error"]["code"] == -32004
        assert plugin._REQUEST_QUEUE.qsize() == 1
    plugin._drain_request_queue()
    assert plugin._ACTIVE_REQUEST is None


def test_invalid_deadlines_fail_closed():
    plugin = _load_bridge_plugin()
    for deadline in (True, "tomorrow", float("nan"), float("inf"), 10**400):
        response = plugin._dispatch_request({"id": 1, "deadline_unix_ms": deadline})
        assert response["error"]["code"] == -32602
    assert plugin._REQUEST_QUEUE.empty()


def test_real_tcp_delayed_accept_discards_expired_host_work():
    import json
    import socket

    plugin = _load_bridge_plugin()
    handler = MagicMock()
    plugin._handle_request = handler
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)
        with socket.create_connection(listener.getsockname(), timeout=2) as client:
            client.sendall(
                json.dumps({"id": 1, "method": "execute_python", "deadline_unix_ms": (time.time() - 1) * 1000}).encode()
                + b"\n"
            )
            # Kernel accepts data before the embedded Python listener resumes.
            conn, _ = listener.accept()
            plugin._serve_client(conn)
            response = json.loads(client.recv(65536))
    assert response["error"]["code"] == -32004
    plugin._drain_request_queue()
    handler.assert_not_called()


def test_aborted_started_request_releases_waiter_and_busy_slot():
    import pytest

    plugin = _load_bridge_plugin()
    plugin._handle_request = MagicMock(side_effect=SystemExit("abort"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(plugin._dispatch_request, {"id": 1, "method": "execute_python"})
        deadline = time.monotonic() + 1
        while plugin._REQUEST_QUEUE.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        with pytest.raises(SystemExit, match="abort"):
            plugin._drain_request_queue()
        response = request.result(timeout=1)
    assert response["error"]["code"] == -32002
    assert response["error"]["data"] == {"retryable": False, "execution_state": "unknown"}
    assert plugin._ACTIVE_REQUEST is None


def test_client_attaches_wire_deadline_to_host_calls(monkeypatch):
    import json

    from dcc_mcp_zbrush.bridge import SocketBridge

    bridge = SocketBridge(timeout=10)
    sent = []

    def send(data, **_kwargs):
        sent.append(json.loads(data))
        return b'{"result":{"ok":true}}'

    monkeypatch.setattr(bridge, "_send", send)
    assert bridge.call("execute_python", code="x = 1") == {"ok": True}
    assert [p["method"] for p in sent] == ["execute_python"]
    assert sent[0]["deadline_unix_ms"] > time.time() * 1000

    # The SDK-free ping carries no host-work deadline.
    sent.clear()
    assert bridge.call("ping") == {"ok": True}
    assert "deadline_unix_ms" not in sent[0]
