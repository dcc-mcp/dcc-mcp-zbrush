"""Tests for the sidecar-side ZBrush dialog tools (issue #89)."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
import yaml

from dcc_mcp_zbrush import _dialogs
from dcc_mcp_zbrush.bridge import SocketBridge

_SKILL_DIR = Path(__file__).parent.parent / "src" / "dcc_mcp_zbrush" / "skills" / "zbrush-dialog"

# The note from issue #89: macOS Vision observations for the 1292x326 capture
# of the OBJ n-gon import note (normalized, bottom-left origin). "Clone" is
# palette text that shows through the translucent note.
_NOTE_OCR = {
    "width": 1292,
    "height": 326,
    "lines": [
        {"text": "ZBrush", "confidence": 1, "x": 0.0201, "y": 0.4172, "w": 0.1238, "h": 0.1227},
        {
            "text": "This mesh contains nonstandard polygons (more than 4 sides).",
            "confidence": 1,
            "x": 0.1667,
            "y": 0.8033,
            "w": 0.7083,
            "h": 0.1148,
        },
        {
            "text": "Would you like to split these polygons to sets of symmetrical triangles?",
            "confidence": 1,
            "x": 0.1667,
            "y": 0.5736,
            "w": 0.8083,
            "h": 0.1149,
        },
        {
            "text": "or to a combination of Quads and Triangles ?",
            "confidence": 1,
            "x": 0.1667,
            "y": 0.4754,
            "w": 0.5125,
            "h": 0.0984,
        },
        {"text": "Clone", "confidence": 1, "x": 0.6702, "y": 0.2506, "w": 0.0497, "h": 0.0632},
        {
            "text": "Quads and Triangles (compatible with Z2)",
            "confidence": 1,
            "x": 0.1811,
            "y": 0.0859,
            "w": 0.4659,
            "h": 0.0920,
        },
        {"text": "Symmetrical Triangles Only", "confidence": 1, "x": 0.6687, "y": 0.0859, "w": 0.3080, "h": 0.0920},
    ],
}

# A live 1708x1006 ZBrush 2026.2.1 window: the note sits in the canvas, under
# ZBrush's own title strip (version and copyright) and palette labels.
_WINDOW = {"window_id": 7, "process_id": 4321, "x": 10.0, "y": 40.0, "width": 1708.0, "height": 1006.0}
_NOTE_ORIGIN = (200.0, 400.0)


def _window_ocr(note: bool = True, *, message: str = "", buttons: tuple = ()) -> dict:
    """Return Vision-style OCR for the whole window, optionally with a note."""
    width, height = _WINDOW["width"], _WINDOW["height"]

    def obs(text: str, x: float, y: float, w: float, h: float, confidence: float = 1.0) -> dict:
        return {
            "text": text,
            "confidence": confidence,
            "x": x / width,
            "y": 1 - (y + h) / height,
            "w": w / width,
            "h": h / height,
        }

    lines = [
        obs(
            "ZBrush 2026.2.1(rlm) rim] Created by Ofer Alon o Copyright 1997-2026 Maxon Computer GmbH",
            22.3,
            37.3,
            573.5,
            9.9,
        ),
        obs("Color DCC MCP Document", 121.6, 59.5, 186.2, 10.1),
        obs("Load Tools From Project", 1489.5, 116.5, 144.0, 7.7),
        obs("Focal Shift 0", 1077.4, 129.0, 69.5, 10.1),
        obs("Draw Size 64", 1077.4, 151.5, 79.4, 15.0),
        obs("MatCap Red W", 17.4, 640.9, 91.9, 15.1),
    ]
    if note:
        left, top = _NOTE_ORIGIN
        for item in _NOTE_OCR["lines"]:
            x = left + item["x"] * 646
            y = top + (1 - item["y"] - item["h"]) * 163
            lines.append(obs(item["text"], x, y, item["w"] * 646, item["h"] * 163))
    if message:
        left, top = _NOTE_ORIGIN
        lines.append(obs(message, left + 108, top + 20, 420, 18.7))
        for index, label in enumerate(buttons):
            lines.append(obs(label, left + 117 + 150 * index, top + 140, 100, 15.0))
    return {"width": int(width * 2), "height": int(height * 2), "lines": lines}


_ISSUE_OCR = _window_ocr()
_ISSUE_WINDOW = _WINDOW


class _FakeBridge:
    def __init__(self, states: List[str]) -> None:
        self._states = list(states)
        self.calls: List[float] = []

    def probe(self, timeout: float = 3.0) -> Dict[str, Any]:
        self.calls.append(timeout)
        state = self._states.pop(0) if len(self._states) > 1 else self._states[0]
        return {"status": state, "active_method": "execute_python" if state == "busy" else None}

    def call(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("dialog tools must never queue work on the blocked ZBrush main thread")


class _FakeBackend:
    def __init__(self, ocr: Optional[Dict[str, Any]] = None) -> None:
        self.ocr = ocr if ocr is not None else _ISSUE_OCR
        self.clicks: List[tuple] = []
        self.captures: List[str] = []

    def find_window(self, process_id: Optional[int] = None) -> Dict[str, Any]:
        return dict(_ISSUE_WINDOW)

    def capture(self, window: Dict[str, Any], output_path: str) -> None:
        Path(output_path).write_bytes(b"png")
        self.captures.append(output_path)

    def recognize_text(self, image_path: str) -> Dict[str, Any]:
        return self.ocr

    def click(self, window: Dict[str, Any], x: float, y: float) -> Dict[str, Any]:
        self.clicks.append((window["process_id"], x, y))
        return {"ok": True}


def _load_skill_script(name: str) -> ModuleType:
    path = _SKILL_DIR / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"skill_dialog_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# SocketBridge.probe against real sockets
# --------------------------------------------------------------------------


def _listener(handler) -> tuple[socket.socket, int, threading.Event]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    stop = threading.Event()

    def serve() -> None:
        server.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=handler, args=(conn, stop), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return server, server.getsockname()[1], stop


def _reply(payload: Dict[str, Any]):
    def handler(conn: socket.socket, _stop: threading.Event) -> None:
        with conn:
            conn.recv(65536)
            conn.sendall((json.dumps({"jsonrpc": "2.0", "id": 0, "result": payload}) + "\n").encode())

    return handler


def _never_reply(conn: socket.socket, stop: threading.Event) -> None:
    # Mirrors the live failure: the kernel accepts the connection but the
    # GIL-starved bridge never reads it while a ZBrush note is open.
    with conn:
        stop.wait(5)


def test_probe_status_drives_the_dialog_tools_over_real_sockets() -> None:
    # The probe itself is covered in tests/test_server.py; this pins the contract the
    # dialog tools rely on: a listener that accepts but never answers is "blocked".
    server, port, stop = _listener(_never_reply)
    try:
        status = _dialogs.probe_bridge(SocketBridge(port=port), timeout=0.3)["status"]
    finally:
        stop.set()
        server.close()
    assert status == "blocked"
    server, port, stop = _listener(_reply({"ok": True, "busy": False, "active_method": None}))
    try:
        status = _dialogs.probe_bridge(SocketBridge(port=port), timeout=2)["status"]
    finally:
        stop.set()
        server.close()
    assert status == "ready"


# --------------------------------------------------------------------------
# OCR geometry and button matching
# --------------------------------------------------------------------------


def test_ocr_lines_map_to_screen_points() -> None:
    lines = _dialogs.text_lines_from_ocr(_ISSUE_OCR, _ISSUE_WINDOW)
    by_text = {line["text"]: line for line in lines}

    button = by_text["Symmetrical Triangles Only"]
    left, top = _NOTE_ORIGIN
    # Vision's y axis starts at the bottom; buttons sit at the bottom of the note.
    assert button["center"]["x"] == pytest.approx(10 + left + (0.6687 + 0.3080 / 2) * 646, abs=0.5)
    assert button["center"]["y"] == pytest.approx(40 + top + (1 - 0.0859 - 0.0920 / 2) * 163, abs=0.5)
    assert by_text["Color DCC MCP Document"]["in_window_chrome"] is True
    assert button["in_window_chrome"] is False


def test_locate_dialog_separates_the_note_from_palette_text() -> None:
    dialog = _dialogs.locate_dialog(_dialogs.text_lines_from_ocr(_ISSUE_OCR, _ISSUE_WINDOW))

    assert dialog is not None
    assert dialog["kind"] == "question"
    assert dialog["destructive"] is False
    assert dialog["message"].splitlines() == [
        "This mesh contains nonstandard polygons (more than 4 sides).",
        "Would you like to split these polygons to sets of symmetrical triangles?",
        "or to a combination of Quads and Triangles ?",
    ]
    assert [item["text"] for item in dialog["buttons"]] == [
        "Quads and Triangles (compatible with Z2)",
        "Symmetrical Triangles Only",
    ]
    assert dialog["other_labels"] == ["Clone"]


def test_locate_dialog_finds_nothing_in_the_plain_zbrush_window() -> None:
    lines = _dialogs.text_lines_from_ocr(_window_ocr(note=False), _WINDOW)
    assert _dialogs.locate_dialog(lines) is None


def test_locate_dialog_flags_destructive_confirmations() -> None:
    ocr = _window_ocr(
        note=False, message="This operation cannot be undone. Delete the selected SubTool?", buttons=("OK", "Cancel")
    )
    dialog = _dialogs.locate_dialog(_dialogs.text_lines_from_ocr(ocr, _WINDOW))

    assert dialog is not None
    assert dialog["kind"] == "destructive_confirmation"
    assert [item["text"] for item in dialog["buttons"]] == ["OK", "Cancel"]


def test_find_button_requires_one_exact_match() -> None:
    buttons = _dialogs.locate_dialog(_dialogs.text_lines_from_ocr(_ISSUE_OCR, _ISSUE_WINDOW))["buttons"]

    assert _dialogs.find_button(buttons, "  symmetrical   triangles only ")["text"] == "Symmetrical Triangles Only"
    with pytest.raises(_dialogs.DialogError) as missing:
        _dialogs.find_button(buttons, "OK")
    assert missing.value.code == "BUTTON_NOT_FOUND"
    assert missing.value.context["button_candidates"] == [
        "Quads and Triangles (compatible with Z2)",
        "Symmetrical Triangles Only",
    ]
    with pytest.raises(_dialogs.DialogError) as ambiguous:
        _dialogs.find_button(buttons, "Triangles", match="contains")
    assert ambiguous.value.code == "AMBIGUOUS_BUTTON"


# --------------------------------------------------------------------------
# get_active_dialog / click_dialog_button flows
# --------------------------------------------------------------------------


def test_get_active_dialog_reads_the_issue_note_when_zbrush_is_blocked(tmp_path) -> None:
    backend = _FakeBackend()
    info = _dialogs.get_active_dialog(_FakeBridge(["blocked"]), backend=backend, output_dir=str(tmp_path))

    assert (info["zbrush_blocked"], info["dialog_open"], info["dialog_kind"]) == (True, True, "question")
    assert info["button_candidates"] == ["Quads and Triangles (compatible with Z2)", "Symmetrical Triangles Only"]
    assert "nonstandard polygons" in info["dialog_text"]
    assert Path(info["screenshot_path"]).parent == tmp_path


def test_get_active_dialog_reports_a_block_without_dialog_text() -> None:
    info = _dialogs.get_active_dialog(_FakeBridge(["blocked"]), backend=_FakeBackend(_window_ocr(note=False)))

    assert info["zbrush_blocked"] is True
    assert info["dialog_open"] is False
    assert "button_candidates" not in info


def test_get_active_dialog_skips_capture_while_zbrush_answers() -> None:
    backend = _FakeBackend()
    info = _dialogs.get_active_dialog(_FakeBridge(["ready"]), backend=backend)

    assert info == {"zbrush_blocked": False, "dialog_open": False, "bridge_state": "ready", "active_method": None}
    assert backend.captures == []


def test_get_active_dialog_fails_closed_when_zbrush_is_not_listening() -> None:
    with pytest.raises(_dialogs.DialogError) as caught:
        _dialogs.get_active_dialog(_FakeBridge(["unreachable"]), backend=_FakeBackend())
    assert caught.value.code == "ZBRUSH_UNREACHABLE"


def test_click_dialog_button_clicks_the_label_centre_and_verifies(tmp_path) -> None:
    backend = _FakeBackend()
    bridge = _FakeBridge(["blocked", "ready"])

    result = _dialogs.click_dialog_button(
        bridge, "Symmetrical Triangles Only", backend=backend, output_dir=str(tmp_path), settle_seconds=0
    )

    assert result["dismissed"] is True
    assert (result["bridge_state_before"], result["bridge_state_after"]) == ("blocked", "ready")
    assert len(backend.clicks) == 1
    pid, x, y = backend.clicks[0]
    assert pid == 4321
    assert (x, y) == (result["clicked_at"]["x"], result["clicked_at"]["y"])


def test_click_dialog_button_refuses_without_a_blocking_dialog() -> None:
    backend = _FakeBackend()
    with pytest.raises(_dialogs.DialogError) as caught:
        _dialogs.click_dialog_button(_FakeBridge(["ready"]), "OK", backend=backend)
    assert caught.value.code == "DIALOG_NOT_DETECTED"
    assert backend.clicks == []


def test_click_dialog_button_never_clicks_an_unknown_label() -> None:
    backend = _FakeBackend()
    with pytest.raises(_dialogs.DialogError) as caught:
        _dialogs.click_dialog_button(_FakeBridge(["blocked"]), "Delete All", backend=backend)
    assert caught.value.code == "BUTTON_NOT_FOUND"
    assert backend.clicks == []


def test_click_dialog_button_holds_destructive_answers_until_allowed() -> None:
    ocr = _window_ocr(
        note=False, message="This operation cannot be undone. Delete the selected SubTool?", buttons=("OK", "Cancel")
    )

    backend = _FakeBackend(ocr)
    with pytest.raises(_dialogs.DialogError) as caught:
        _dialogs.click_dialog_button(_FakeBridge(["blocked"]), "OK", backend=backend, settle_seconds=0)
    assert caught.value.code == "DESTRUCTIVE_CONFIRMATION"
    assert backend.clicks == []

    cancel = _dialogs.click_dialog_button(
        _FakeBridge(["blocked", "ready"]), "Cancel", backend=backend, settle_seconds=0
    )
    assert cancel["dismissed"] is True
    approved = _dialogs.click_dialog_button(
        _FakeBridge(["blocked", "ready"]), "OK", backend=backend, allow_destructive=True, settle_seconds=0
    )
    assert approved["button"] == "OK"
    assert len(backend.clicks) == 2


def test_click_dialog_button_never_clicks_during_a_long_operation() -> None:
    backend = _FakeBackend(_window_ocr(note=False))
    with pytest.raises(_dialogs.DialogError) as caught:
        _dialogs.click_dialog_button(_FakeBridge(["blocked"]), "OK", backend=backend)
    assert caught.value.code == "DIALOG_TEXT_NOT_FOUND"
    assert backend.clicks == []


def test_default_screenshot_is_a_single_rolling_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_dialogs.tempfile, "gettempdir", lambda: str(tmp_path))
    for _ in range(2):
        info = _dialogs.get_active_dialog(_FakeBridge(["blocked"]), backend=_FakeBackend())
    assert Path(info["screenshot_path"]).name == "zbrush-dialog-latest.png"
    assert [path.name for path in (tmp_path / "dcc-mcp-zbrush").iterdir()] == ["zbrush-dialog-latest.png"]


def test_click_dialog_button_reports_a_dialog_that_still_blocks() -> None:
    backend = _FakeBackend()
    with pytest.raises(_dialogs.DialogError) as caught:
        _dialogs.click_dialog_button(
            _FakeBridge(["blocked"]),
            "Symmetrical Triangles Only",
            backend=backend,
            settle_seconds=0,
            verify_attempts=2,
        )
    assert caught.value.code == "DIALOG_STILL_BLOCKING"
    assert len(backend.clicks) == 1


def test_dialog_tools_refuse_to_run_inside_zbrush() -> None:
    with patch("dcc_mcp_zbrush._version_probe.is_zbrush_available", return_value=True):
        with pytest.raises(_dialogs.DialogError) as caught:
            _dialogs.sidecar_bridge()
    assert caught.value.code == "SIDECAR_REQUIRED"


def test_non_macos_platforms_report_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(_dialogs.sys, "platform", "win32")
    with pytest.raises(_dialogs.DialogError) as caught:
        _dialogs.default_backend()
    assert caught.value.code == "UNSUPPORTED_PLATFORM"


# --------------------------------------------------------------------------
# Skill entry points
# --------------------------------------------------------------------------


def test_skill_scripts_return_envelopes_and_never_call_the_bridge(tmp_path) -> None:
    get_mod = _load_skill_script("get_active_dialog.py")
    click_mod = _load_skill_script("click_dialog_button.py")
    backend = _FakeBackend()

    with (
        patch.object(_dialogs, "sidecar_bridge", return_value=_FakeBridge(["blocked"])),
        patch.object(_dialogs, "default_backend", return_value=backend),
    ):
        read = get_mod.get_active_dialog(output_dir=str(tmp_path))
    assert read["success"] is True
    assert read["context"]["dialog_open"] is True
    assert "Symmetrical Triangles Only" in read["context"]["button_candidates"]

    with (
        patch.object(_dialogs, "sidecar_bridge", return_value=_FakeBridge(["blocked"])),
        patch.object(_dialogs, "default_backend", return_value=backend),
    ):
        missing = click_mod.click_dialog_button(button_name="OK", output_dir=str(tmp_path))
    assert missing["success"] is False
    assert missing["error"] == "BUTTON_NOT_FOUND"
    assert backend.clicks == []


def test_skill_reports_screen_lock_as_a_typed_error() -> None:
    get_mod = _load_skill_script("get_active_dialog.py")

    class _LockedBackend(_FakeBackend):
        def find_window(self, process_id: Optional[int] = None) -> Dict[str, Any]:
            raise _dialogs.DialogError("SCREEN_LOCKED", "locked", prompt="Unlock the Mac, then retry.")

    with (
        patch.object(_dialogs, "sidecar_bridge", return_value=_FakeBridge(["blocked"])),
        patch.object(_dialogs, "default_backend", return_value=_LockedBackend()),
    ):
        result = get_mod.get_active_dialog()
    assert result["success"] is False
    assert result["error"] == "SCREEN_LOCKED"


# --------------------------------------------------------------------------
# Native alert windows (script message boxes, startup crash notice)
# --------------------------------------------------------------------------

_ALERT = {"window_id": 9, "process_id": 4321, "x": 734.0, "y": 234.0, "width": 260.0, "height": 223.0}


def _alert_line(text: str, y: float, h: float = 12.0, x: float = 20.0, w: float = 220.0) -> dict:
    return {"text": text, "confidence": 1.0, "x": x / 260, "y": 1 - (y + h) / 223, "w": w / 260, "h": h / 223}


def test_locate_window_dialog_splits_message_and_buttons() -> None:
    ocr = {
        "lines": [
            _alert_line("DCC MCP", 95, 13),
            _alert_line("dcc-mcp dialog test. Click OK to", 120),
            _alert_line("continue.", 136, w=60),
            _alert_line("OK", 180, 14, x=110, w=40),
        ]
    }
    lines = _dialogs.text_lines_from_ocr(ocr, dict(_ALERT, kind="dialog_window"))
    assert not any(line["in_window_chrome"] for line in lines)
    dialog = _dialogs.locate_window_dialog(lines)
    assert dialog["message"].splitlines() == ["DCC MCP", "dcc-mcp dialog test. Click OK to", "continue."]
    assert [item["text"] for item in dialog["buttons"]] == ["OK"]
    assert (dialog["kind"], dialog["destructive"]) == ("informational", False)


def test_find_window_prefers_the_newest_alert_over_the_main_window() -> None:
    main = {"window_id": 1, "process_id": 4321, "title": " ZBrush 2026.2.1 ", "onscreen": True, "order": 3}
    main.update(x=9, y=41, width=1708, height=1006)
    splash = dict(main, window_id=2, title="SplashScreen", onscreen=False, width=648, height=433)
    strip = dict(main, window_id=3, title="", width=1728, height=33)
    alert = dict(main, window_id=4, title="", order=1, x=734, y=234, width=260, height=223)
    helper = dict(main, window_id=6, title="", x=0, y=617, width=64, height=64)
    ok_cancel = dict(alert, window_id=7, x=733, y=472, width=260, height=143)
    backend = _dialogs.MacOSDialogBackend()
    for windows, expected in (
        ([main, splash, strip, alert], (4, "dialog_window")),
        ([main, splash, strip, helper], (1, "main_window")),
        # zbc.message_ok_cancel on ZBrush 2026.2.1: a 260x143 window.
        ([main, splash, strip, helper, ok_cancel], (7, "dialog_window")),
        ([main, splash, strip], (1, "main_window")),
        # ZBrush hidden or on another Space: none of its windows is "onscreen".
        ([dict(main, onscreen=False), dict(alert, onscreen=False)], (4, "dialog_window")),
        ([main, alert, dict(alert, window_id=5, order=0)], (5, "dialog_window")),
    ):
        with patch.object(backend, "_windows", return_value=windows):
            found = backend.find_window(4321)
        assert (found["window_id"], found["kind"]) == expected


def test_click_answers_a_startup_alert_before_the_bridge_listens() -> None:
    class _AlertBackend(_FakeBackend):
        def __init__(self) -> None:
            super().__init__(
                {
                    "lines": [
                        _alert_line("Your most recent session was abnormally terminated.", 120, w=230),
                        _alert_line("OK", 180, 14, x=110, w=40),
                    ]
                }
            )
            self.open = True

        def find_window(self, process_id: Optional[int] = None) -> Dict[str, Any]:
            return dict(_ALERT, kind="dialog_window")

        def click(self, window: Dict[str, Any], x: float, y: float) -> Dict[str, Any]:
            self.open = False
            return super().click(window, x, y)

        def window_exists(self, window: Dict[str, Any]) -> bool:
            return self.open

    result = _dialogs.click_dialog_button(_FakeBridge(["unreachable"]), "OK", backend=_AlertBackend(), settle_seconds=0)
    assert result["dismissed"] is True
    assert (result["dialog_kind"], result["bridge_state_after"]) == ("informational", "unreachable")


# Real Vision OCR of an idle ZBrush 2026.2.1 window (no note open): every line
# with two or more words or trailing punctuation. "MatCap Red W:" once passed as
# a one-line message, so a note must contain at least one long line.
_IDLE_WINDOW_OCR = {
    "lines": [
        {
            "text": "ZBrush 2026.2.1(rlm) rim] Created by Ofer Alon o Copyright 1997-2026 Maxon Computer GmbH",
            "confidence": 1.0,
            "x": 0.01308,
            "y": 0.95309,
            "w": 0.33576,
            "h": 0.00988,
        },
        {"text": "Color DCC MCP Document", "confidence": 1.0, "x": 0.07122, "y": 0.93086, "w": 0.10756, "h": 0.00999},
        {"text": "Home Pade", "confidence": 1.0, "x": 0.01599, "y": 0.84938, "w": 0.03924, "h": 0.00748},
        {"text": "Live Boolean", "confidence": 1.0, "x": 0.12355, "y": 0.84938, "w": 0.0436, "h": 0.00748},
        {"text": "Open Fle", "confidence": 1.0, "x": 0.09157, "y": 0.78519, "w": 0.03052, "h": 0.00756},
        {"text": "rro ects |oois", "confidence": 1.0, "x": 0.16424, "y": 0.78519, "w": 0.05669, "h": 0.00756},
        {"text": "3D Printino", "confidence": 1.0, "x": 0.09302, "y": 0.5333, "w": 0.03779, "h": 0.00746},
        {"text": "nead plaries", "confidence": 1.0, "x": 0.22093, "y": 0.53082, "w": 0.0436, "h": 0.00746},
        {"text": "Alphas Materials", "confidence": 1.0, "x": 0.2936, "y": 0.78519, "w": 0.06105, "h": 0.01004},
        {"text": "ZBrush 2026.2.1", "confidence": 1.0, "x": 0.46802, "y": 0.98012, "w": 0.06395, "h": 0.01494},
        {"text": "2 Intensitv 25", "confidence": 1.0, "x": 0.49419, "y": 0.83698, "w": 0.04506, "h": 0.00747},
        {"text": "Cube. ZPR", "confidence": 1.0, "x": 0.48983, "y": 0.65408, "w": 0.03198, "h": 0.00765},
        {"text": "e-throuan o", "confidence": 1.0, "x": 0.63517, "y": 0.95802, "w": 0.0436, "h": 0.00741},
        {
            "text": "DefaultZScript ‹'!!! !!!!›",
            "confidence": 1.0,
            "x": 0.71657,
            "y": 0.95556,
            "w": 0.0843,
            "h": 0.00988,
        },
        {"text": "• Iranstorm ¿piuqin", "confidence": 1.0, "x": 0.74419, "y": 0.93086, "w": 0.07849, "h": 0.00751},
        {"text": "Focal Shift 0", "confidence": 1.0, "x": 0.63081, "y": 0.86173, "w": 0.0407, "h": 0.01004},
        {"text": "Draw Size 64", "confidence": 1.0, "x": 0.63081, "y": 0.83449, "w": 0.04651, "h": 0.01491},
        {"text": "DvnaMesh Spnel", "confidence": 1.0, "x": 0.67587, "y": 0.65408, "w": 0.05378, "h": 0.00765},
        {"text": "DvnaMesh Soher", "confidence": 1.0, "x": 0.67442, "y": 0.5333, "w": 0.05669, "h": 0.00746},
        {"text": "AIpna O1", "confidence": 1.0, "x": 0.02035, "y": 0.54321, "w": 0.03488, "h": 0.00749},
        {"text": "rexture Om", "confidence": 1.0, "x": 0.01744, "y": 0.44682, "w": 0.04215, "h": 0.0075},
        {"text": "MatCap Red W:", "confidence": 1.0, "x": 0.01017, "y": 0.34791, "w": 0.05523, "h": 0.01505},
        {"text": "Pro Projects", "confidence": 1.0, "x": 0.353, "y": 0.40743, "w": 0.04254, "h": 0.01444},
        {"text": "DemoSoldier ZPR", "confidence": 1.0, "x": 0.5436, "y": 0.41235, "w": 0.05669, "h": 0.00763},
        {"text": "DvnaMesh Stone", "confidence": 1.0, "x": 0.73983, "y": 0.5333, "w": 0.05523, "h": 0.00746},
        {"text": "Load I oo!", "confidence": 1.0, "x": 0.87209, "y": 0.92346, "w": 0.03488, "h": 0.00746},
        {"text": "Save AS", "confidence": 1.0, "x": 0.92878, "y": 0.92346, "w": 0.02616, "h": 0.00746},
        {"text": "Save Next", "confidence": 1.0, "x": 0.92878, "y": 0.90109, "w": 0.03488, "h": 0.00755},
        {"text": "Loadnl oois From prolecti", "confidence": 1.0, "x": 0.87209, "y": 0.87654, "w": 0.0843, "h": 0.00765},
        {"text": "LOpY I O0I", "confidence": 1.0, "x": 0.87355, "y": 0.85185, "w": 0.03343, "h": 0.00749},
        {"text": "PSTA OO", "confidence": 0.3, "x": 0.92878, "y": 0.85432, "w": 0.03488, "h": 0.00502},
        {"text": "Make PolyMesh3D", "confidence": 1.0, "x": 0.89971, "y": 0.80247, "w": 0.06395, "h": 0.01235},
        {"text": "GoZ To iPad", "confidence": 1.0, "x": 0.87209, "y": 0.75547, "w": 0.0436, "h": 0.01243},
        {"text": "Lightbox• Tools", "confidence": 1.0, "x": 0.87209, "y": 0.73062, "w": 0.05669, "h": 0.01259},
    ]
}


def test_idle_zbrush_window_has_no_dialog() -> None:
    window = {"window_id": 1, "process_id": 1, "x": 9, "y": 41, "width": 1708, "height": 1006, "kind": "main_window"}
    lines = _dialogs.text_lines_from_ocr(_IDLE_WINDOW_OCR, window)
    assert any(line["text"] == "MatCap Red W:" for line in lines)
    assert _dialogs.locate_dialog(lines) is None


def test_dialog_tools_stay_off_the_zbrush_main_thread() -> None:
    # Every other bundled tool declares affinity "main". These two must not: a note
    # holds the main thread, so they would wait behind the note they should answer.
    data = yaml.safe_load((_SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))
    assert {tool["affinity"] for tool in data["tools"]} == {"any"}
    sources = [_SKILL_DIR / tool["source_file"] for tool in data["tools"]] + [Path(_dialogs.__file__)]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "import_zbc" not in text and "zbrush.commands" not in text, path.name
