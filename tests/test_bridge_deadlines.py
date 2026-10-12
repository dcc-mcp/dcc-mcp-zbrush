"""Transport/queue regression tests; these do not reproduce a native ZBrush modal."""

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


def test_client_does_not_submit_host_work_when_ping_times_out(monkeypatch):
    import json

    import pytest

    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridgeError

    bridge = SocketBridge(timeout=10, probe_timeout=0.05)
    sent = []

    def send(data, *, timeout):
        sent.append((json.loads(data), timeout))
        raise TimeoutError()

    monkeypatch.setattr(bridge, "_send", send)
    with pytest.raises(ZBrushBridgeError) as caught:
        bridge.call("execute_python", code="side_effect()")
    assert [payload["method"] for payload, _ in sent] == ["ping"]
    assert sent[0][1] == 0.05
    assert caught.value.code == -32003
    assert caught.value.data == {"retryable": True, "submitted": False, "state": "unresponsive"}


def test_busy_preflight_does_not_submit_and_recovers_without_latching(monkeypatch):
    import json

    import pytest

    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridgeError

    bridge = SocketBridge()
    sent = []
    busy = True

    def send(data, **_kwargs):
        payload = json.loads(data)
        sent.append(payload)
        result = {"ok": True, "busy": busy} if payload["method"] == "ping" else {"success": True}
        return json.dumps({"result": result}).encode()

    monkeypatch.setattr(bridge, "_send", send)
    with pytest.raises(ZBrushBridgeError) as caught:
        bridge.call("execute_python")
    assert caught.value.code == -32001
    assert len(sent) == 1
    busy = False
    assert bridge.call("execute_python") == {"success": True}
    assert [payload["method"] for payload in sent] == ["ping", "ping", "execute_python"]
    assert sent[-1]["deadline_unix_ms"] > time.time() * 1000


def test_lost_response_after_submit_has_unknown_nonretryable_outcome(monkeypatch):
    import json

    import pytest

    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridgeError

    bridge = SocketBridge()

    def send(data, **_kwargs):
        if json.loads(data)["method"] == "ping":
            return b'{"result":{"ok":true,"busy":false}}'
        raise TimeoutError()

    monkeypatch.setattr(bridge, "_send", send)
    with pytest.raises(ZBrushBridgeError) as caught:
        bridge.call("execute_python")
    assert caught.value.code == -32002
    assert caught.value.data["execution_state"] == "unknown"
    assert caught.value.data["retryable"] is False


def test_real_tcp_blackhole_accepts_connection_but_preflight_stays_bounded():
    import json
    import socket

    import pytest

    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridgeError

    received = []
    release = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)

        def peer():
            with listener.accept()[0] as conn:
                received.append(json.loads(conn.recv(65536)))
                release.wait(2)

        thread = threading.Thread(target=peer)
        thread.start()
        bridge = SocketBridge(port=listener.getsockname()[1], timeout=3, probe_timeout=0.1)
        started = time.monotonic()
        try:
            with pytest.raises(ZBrushBridgeError) as caught:
                bridge.call("execute_python", code="side_effect()")
            assert caught.value.code == -32003
            assert time.monotonic() - started < 1.5
        finally:
            release.set()
            thread.join(2)
        assert [payload["method"] for payload in received] == ["ping"]


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


def test_invalid_response_after_submit_is_unknown_and_nonretryable(monkeypatch):
    import json

    import pytest

    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridgeError

    for invalid in (b"", b"{", b"\xff", b"[]", b"{}"):
        bridge = SocketBridge()

        def send(data, **_kwargs):
            return b'{"result":{"ok":true}}' if json.loads(data)["method"] == "ping" else invalid

        monkeypatch.setattr(bridge, "_send", send)
        with pytest.raises(ZBrushBridgeError) as caught:
            bridge.call("execute_python")
        assert caught.value.code == -32002
        assert caught.value.data["execution_state"] == "unknown"
        assert caught.value.data["retryable"] is False


def test_real_tcp_eof_after_submit_is_unknown_not_safe_to_retry():
    import json
    import socket

    import pytest

    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridgeError

    received = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)

        def peer():
            with listener.accept()[0] as conn:
                received.append(json.loads(conn.recv(65536)))
                conn.sendall(b'{"result":{"ok":true,"busy":false}}\n')
            with listener.accept()[0] as conn:
                received.append(json.loads(conn.recv(65536)))
                # The operation may have had side effects before losing its reply.

        thread = threading.Thread(target=peer)
        thread.start()
        try:
            bridge = SocketBridge(port=listener.getsockname()[1], timeout=1)
            with pytest.raises(ZBrushBridgeError) as caught:
                bridge.call("execute_python")
            assert caught.value.code == -32002
            assert caught.value.data["execution_state"] == "unknown"
            assert caught.value.data["retryable"] is False
        finally:
            thread.join(2)
        assert [payload["method"] for payload in received] == ["ping", "execute_python"]
