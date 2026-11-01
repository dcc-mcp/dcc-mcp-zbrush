"""Plugin queue-deadline and client lost-reply regression tests.

These do not reproduce a native ZBrush modal; mocked handlers are not native
ZBrush acceptance evidence.

Covers the in-ZBrush main-thread pump: wire-deadline admission, monotonic queue
expiry, cancelled tombstones, busy/unknown outcomes, and abort safety. A client
that omits ``deadline_unix_ms`` still gets the plugin's local queue timeout, so
these guards do not depend on a particular sidecar client.

Also covers the client half of the unknown-outcome contract: after a real
method is submitted, a lost/empty/malformed reply is classified as
``-32002 execution_state=unknown, retryable=False`` so a possibly
side-effecting call is never replayed blindly. Per the #95 contract there is
no per-call ping; the SDK-free ``ping`` is used only by ``probe()`` at connect
time. The blocked-host classification (``probe`` / ``ZBrushBlockedError``),
busy-retry behaviour, the ``get_bridge()`` reconnect verdict, and the tool
boundary mapping are covered by ``tests/test_server.py``.
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


# --- Client lost-reply classification (#95 contract: no per-call ping) -------
# Restored from the pre-merge PR tree and adapted: there is no preflight ping,
# so a submitted method goes straight on the wire. A lost, empty, or malformed
# reply must still be classified as an unknown, non-retryable outcome.


def test_lost_response_after_submit_has_unknown_nonretryable_outcome(monkeypatch):
    import json

    import pytest

    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridgeError

    bridge = SocketBridge()
    sent = []

    def send(data, **_kwargs):
        sent.append(json.loads(data))
        raise TimeoutError()  # host accepted the work, then stopped answering

    monkeypatch.setattr(bridge, "_send", send)
    with pytest.raises(ZBrushBridgeError) as caught:
        bridge.call("execute_python")
    assert caught.value.code == -32002
    assert caught.value.data["execution_state"] == "unknown"
    assert caught.value.data["retryable"] is False
    # No per-call ping, and a non-retryable error is never replayed.
    assert [payload["method"] for payload in sent] == ["execute_python"]
    assert bridge.is_connected() is False


def test_invalid_response_after_submit_is_unknown_and_nonretryable(monkeypatch):
    import json

    import pytest

    from dcc_mcp_zbrush.bridge import SocketBridge, ZBrushBridgeError

    for invalid in (b"", b"{", b"\xff", b"[]", b"{}"):
        bridge = SocketBridge()
        sent = []

        def send(data, **_kwargs):
            sent.append(json.loads(data))
            return invalid

        monkeypatch.setattr(bridge, "_send", send)
        with pytest.raises(ZBrushBridgeError) as caught:
            bridge.call("execute_python")
        assert caught.value.code == -32002
        assert caught.value.data["execution_state"] == "unknown"
        assert caught.value.data["retryable"] is False
        assert [payload["method"] for payload in sent] == ["execute_python"]


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
            # One connection only: no per-call ping. The host reads the
            # submitted work and closes without replying — it may have run.
            with listener.accept()[0] as conn:
                received.append(json.loads(conn.recv(65536)))

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
        assert [payload["method"] for payload in received] == ["execute_python"]


def test_wire_deadline_binds_client_timeout_below_plugin_cap(monkeypatch):
    """A short client timeout, not the plugin's 600s cap, is the binding budget."""
    import json

    from dcc_mcp_zbrush.bridge import SocketBridge

    plugin = _load_bridge_plugin()
    short_timeout = 5.0
    captured = {}

    def send(data, **_kwargs):
        captured["payload"] = json.loads(data)
        return b'{"result":{"ok":true}}'

    bridge = SocketBridge(timeout=short_timeout)
    monkeypatch.setattr(bridge, "_send", send)
    before = time.time()
    assert bridge.call("execute_python", code="x = 1") == {"ok": True}
    after = time.time()

    wire_ms = captured["payload"]["deadline_unix_ms"]
    # Client emits (now + its own timeout) in ms, not the 600s plugin budget.
    assert before * 1000 + short_timeout * 1000 <= wire_ms <= after * 1000 + short_timeout * 1000
    assert wire_ms / 1000.0 - time.time() < 600

    # Feed that same wire payload to the plugin and inspect the wait budget it
    # computes (min(600, wire/1000 - now)) before releasing the waiter.
    plugin._handle_request = MagicMock(return_value={"result": {"ok": True}})
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(plugin._dispatch_request, captured["payload"])
        deadline = time.monotonic() + 2
        while plugin._REQUEST_QUEUE.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        pending = plugin._ACTIVE_REQUEST
        assert pending is not None
        remaining = pending["deadline"] - time.monotonic()
        assert 0 < remaining < short_timeout + 1  # ~5s, well below the 600s cap
        plugin._drain_request_queue()
        assert request.result(timeout=2)["result"] == {"ok": True}
