"""Sidecar-side detection and answering of blocking ZBrush dialogs (issue #89).

A ZBrush note or system message runs a nested input loop on the host main
thread while that thread still holds the Python GIL. The in-ZBrush socket
bridge therefore cannot answer anything, not even ``ping``, until the note is
dismissed. These helpers run in the sidecar process and never queue work on
the ZBrush main thread:

1. probe the bridge listener with a short timeout;
2. capture the dialog and read its text with on-device OCR. ZBrush shows two
   kinds: its own notes, drawn inside the main window canvas
   (``DrawUserNoteImpl``, for example the OBJ n-gon import question), and
   message boxes in a small separate window (script ``message_ok_cancel`` and
   friends, and the native startup crash notice);
3. refuse risky answers on destructive-looking dialogs unless the caller opts in;
4. post one mouse click on the requested button (only when that dialog is the
   top window at the click point), then verify that ZBrush is free.

macOS uses CoreGraphics, ``screencapture`` and the Vision framework through
``osascript`` (no extra Python dependencies). Other platforms report
``UNSUPPORTED_PLATFORM``.
"""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from typing import Any, Dict, Iterator, List, Optional

from dcc_mcp_zbrush.bridge import STATUS_BLOCKED, STATUS_BUSY, STATUS_READY, STATUS_UNREACHABLE

PROBE_TIMEOUT_SECONDS = 3.0
_OSASCRIPT = "/usr/bin/osascript"
_SCREENCAPTURE = "/usr/sbin/screencapture"
_SENTENCE_END = (".", "?", "!", ":")
_MIN_WINDOW_SIDE = 200
# Script message boxes can be as small as 260x143 pt; ZBrush's other helper windows
# are 64x64 or 33 pt high strips.
_MIN_ALERT_SIDE = 100
# ZBrush draws its own title strip (version and copyright text) and menu bar
# in the top band of the main window. Notes never sit there.
_WINDOW_CHROME_POINTS = 90.0
_LATEST_SCREENSHOT = "zbrush-dialog-latest.png"
_MAIN_WINDOW = "main_window"
_DIALOG_WINDOW = "dialog_window"


class DialogError(RuntimeError):
    """A dialog operation failed with a stable error code."""

    def __init__(self, code: str, message: str, *, prompt: Optional[str] = None, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.prompt = prompt
        self.context = context


def sidecar_bridge() -> Any:
    """Return the configured sidecar bridge without connecting through it."""
    from dcc_mcp_zbrush import api  # noqa: PLC0415
    from dcc_mcp_zbrush._version_probe import is_zbrush_available  # noqa: PLC0415

    if is_zbrush_available():
        raise DialogError(
            "SIDECAR_REQUIRED",
            "Dialog tools must run in the sidecar process, outside ZBrush",
            prompt="Run dcc-mcp-zbrush in sidecar mode; an open note blocks code inside ZBrush.",
        )
    bridge = api.peek_bridge()
    if bridge is None:
        raise DialogError("BRIDGE_NOT_CONFIGURED", "The ZBrush sidecar bridge is not configured")
    return bridge


def probe_bridge(bridge: Any, timeout: float = PROBE_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Return ``SocketBridge.probe()``'s verdict; ``blocked`` means the main thread cannot answer."""
    return dict(bridge.probe(timeout=timeout))


def default_backend() -> "MacOSDialogBackend":
    if sys.platform == "darwin":
        return MacOSDialogBackend()
    raise DialogError(
        "UNSUPPORTED_PLATFORM",
        f"ZBrush dialog capture is not implemented on {sys.platform}",
        prompt="Use the ui-control skill (dcc-cua) to snapshot the ZBrush window and click the note button.",
        platform=sys.platform,
    )


def text_lines_from_ocr(ocr: Dict[str, Any], window: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map Vision's normalized, bottom-left-origin boxes to global screen points."""
    left, top = float(window["x"]), float(window["y"])
    width, height = float(window["width"]), float(window["height"])
    lines = []
    for item in ocr.get("lines") or []:
        text = " ".join(str(item.get("text", "")).split())
        if not text:
            continue
        box_w = float(item["w"]) * width
        box_h = float(item["h"]) * height
        box_x = left + float(item["x"]) * width
        box_y = top + (1.0 - float(item["y"]) - float(item["h"])) * height
        lines.append(
            {
                "text": text,
                "confidence": round(float(item.get("confidence", 0.0)), 3),
                "bounds": {
                    "x": round(box_x, 1),
                    "y": round(box_y, 1),
                    "width": round(box_w, 1),
                    "height": round(box_h, 1),
                },
                "center": {"x": round(box_x + box_w / 2, 1), "y": round(box_y + box_h / 2, 1)},
                "in_window_chrome": window.get("kind", _MAIN_WINDOW) == _MAIN_WINDOW
                and box_y - top < _WINDOW_CHROME_POINTS,
            }
        )
    lines.sort(key=lambda line: (line["bounds"]["y"], line["bounds"]["x"]))
    return lines


def _looks_like_button(text: str) -> bool:
    return len(text.split()) <= 6 and not text.endswith(_SENTENCE_END)


def _normalize(label: str) -> str:
    return " ".join(label.split()).casefold()


_DESTRUCTIVE_RE = re.compile(
    r"\b(undo\w*|delet\w*|remov\w*|overwrit\w*|replac\w*|discard\w*|eras\w*|lose|losing|lost|clear\w*|"
    r"unsaved|sav(?:e|ed|es|ing)|reset\w*|permanent\w*|irreversib\w*)\b",
    re.IGNORECASE,
)
_SAFE_BUTTONS = frozenset({"cancel"})
_MIN_BUTTON_CONFIDENCE = 0.6
# Note buttons share one font; palette labels behind a translucent note are smaller.
_BUTTON_FONT_RATIO = 0.75


def is_destructive_text(text: str) -> bool:
    """Return True for wording that warns about lost, replaced, or irreversible work."""
    return bool(_DESTRUCTIVE_RE.search(text))


def _is_sentence(text: str) -> bool:
    words = text.split()
    return len(words) >= 7 or (len(words) >= 2 and text.endswith(_SENTENCE_END))


def _is_message_line(text: str) -> bool:
    """A line that a note message must contain; palette labels are never this long."""
    return len(text.split()) >= 6 or (len(text) >= 30 and text.endswith(_SENTENCE_END))


def _bottom(line: Dict[str, Any]) -> float:
    return line["bounds"]["y"] + line["bounds"]["height"]


def _right(line: Dict[str, Any]) -> float:
    return line["bounds"]["x"] + line["bounds"]["width"]


def _message_block(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group stacked, overlapping sentence lines and return the largest group."""
    blocks: List[List[Dict[str, Any]]] = []
    for line in sorted((item for item in lines if _is_sentence(item["text"])), key=lambda item: item["bounds"]["y"]):
        for block in blocks:
            last = block[-1]
            gap = line["bounds"]["y"] - _bottom(last)
            overlap = min(_right(line), _right(last)) - max(line["bounds"]["x"], last["bounds"]["x"])
            if gap <= 1.5 * max(line["bounds"]["height"], last["bounds"]["height"]) and overlap > 0:
                block.append(line)
                break
        else:
            blocks.append([line])
    # A one-line palette label such as "MatCap Red W:" also ends in punctuation.
    blocks = [block for block in blocks if any(_is_message_line(item["text"]) for item in block)]
    best = max(blocks, key=lambda block: sum(len(item["text"]) for item in block), default=[])
    return _with_wrapped_lines(best, lines)


def _with_wrapped_lines(block: List[Dict[str, Any]], lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add short wrapped lines that sit tight against the message block."""
    if not block:
        return block
    block = list(block)
    changed = True
    while changed:
        changed = False
        for line in lines:
            if any(line is item for item in block):
                continue
            for item in block:
                height = max(line["bounds"]["height"], item["bounds"]["height"])
                similar = 0.75 <= line["bounds"]["height"] / max(item["bounds"]["height"], 0.1) <= 1.33
                gap = max(line["bounds"]["y"] - _bottom(item), item["bounds"]["y"] - _bottom(line))
                overlap = min(_right(line), _right(item)) - max(line["bounds"]["x"], item["bounds"]["x"])
                if similar and overlap > 0 and gap <= 0.6 * height:
                    block.append(line)
                    changed = True
                    break
    return sorted(block, key=lambda item: (item["bounds"]["y"], item["bounds"]["x"]))


def _in_rows(lines: List[Dict[str, Any]], row_height: float) -> List[Dict[str, Any]]:
    rows: List[List[Dict[str, Any]]] = []
    for line in sorted(lines, key=lambda item: item["bounds"]["y"]):
        if rows and abs(line["bounds"]["y"] - rows[-1][0]["bounds"]["y"]) <= 0.5 * row_height:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [line for row in rows for line in sorted(row, key=lambda item: item["bounds"]["x"])]


def locate_dialog(lines: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find a note's message block and the short labels under it in full-window OCR.

    The message is the largest block of stacked sentence lines; ordinary
    palette labels are short and never form one. Buttons are confidently read
    short labels below the message, in the largest font found there. Smaller
    labels in that area are palette text that shows through the translucent
    note; they are reported as ``other_labels`` and are never clickable.
    """
    lines = [item for item in lines if not item.get("in_window_chrome")]
    block = _message_block(lines)
    if not block:
        return None
    line_height = sorted(item["bounds"]["height"] for item in block)[len(block) // 2]
    left = min(item["bounds"]["x"] for item in block)
    right = max(_right(item) for item in block)
    bottom = max(_bottom(item) for item in block)
    in_block = {id(item) for item in block}
    candidates = [
        item
        for item in lines
        if id(item) not in in_block
        and _looks_like_button(item["text"])
        and item["confidence"] >= _MIN_BUTTON_CONFIDENCE
        and left - 2 * line_height <= item["center"]["x"] <= right + 4 * line_height
        and bottom - 0.25 * line_height <= item["bounds"]["y"] <= bottom + 8 * line_height
    ]
    tallest = max((item["bounds"]["height"] for item in candidates), default=0.0)
    buttons = _in_rows(
        [item for item in candidates if item["bounds"]["height"] >= _BUTTON_FONT_RATIO * tallest],
        line_height,
    )
    kept = {id(item) for item in buttons}
    other_labels = [item["text"] for item in _in_rows(candidates, line_height) if id(item) not in kept]
    return _dialog_result(block, buttons, other_labels)


def _dialog_result(
    message_lines: List[Dict[str, Any]], buttons: List[Dict[str, Any]], other_labels: List[str]
) -> Dict[str, Any]:
    message = "\n".join(item["text"] for item in message_lines)
    # A single-button dialog offers no choice, so its wording alone is not a risk.
    destructive = any(is_destructive_text(item["text"]) for item in buttons) or (
        len(buttons) != 1 and is_destructive_text(message)
    )
    if destructive:
        kind = "destructive_confirmation"
    else:
        kind = "question" if "?" in message else "informational"
    edges = message_lines + buttons
    x0 = min(item["bounds"]["x"] for item in edges)
    y0 = min(item["bounds"]["y"] for item in edges)
    return {
        "kind": kind,
        "destructive": destructive,
        "message": message,
        "buttons": buttons,
        "other_labels": other_labels,
        "bounds": {
            "x": x0,
            "y": y0,
            "width": round(max(_right(item) for item in edges) - x0, 1),
            "height": round(max(_bottom(item) for item in edges) - y0, 1),
        },
    }


def locate_window_dialog(lines: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Split OCR of a dedicated dialog window into message and bottom button rows.

    Walking up from the bottom, a row of short, confident labels is a button
    row while a clear gap separates it from the row above. Wrapped message
    lines sit tight together, so they stay in the message.
    """
    if not lines:
        return None
    heights = sorted(item["bounds"]["height"] for item in lines)
    line_height = heights[len(heights) // 2]
    rows: List[List[Dict[str, Any]]] = []
    for line in sorted(lines, key=lambda item: item["center"]["y"]):
        if rows and abs(line["center"]["y"] - rows[-1][-1]["center"]["y"]) <= 0.5 * line_height:
            rows[-1].append(line)
        else:
            rows.append([line])
    first_button_row = len(rows)
    for index in range(len(rows) - 1, 0, -1):
        row, above = rows[index], rows[index - 1]
        clickable = all(
            _looks_like_button(item["text"]) and item["confidence"] >= _MIN_BUTTON_CONFIDENCE for item in row
        )
        gap = min(item["bounds"]["y"] for item in row) - max(_bottom(item) for item in above)
        if not clickable or gap < 0.6 * line_height:
            break
        first_button_row = index
    message_lines = [item for row in rows[:first_button_row] for item in sorted(row, key=lambda x: x["bounds"]["x"])]
    buttons = [item for row in rows[first_button_row:] for item in sorted(row, key=lambda x: x["bounds"]["x"])]
    if not message_lines:
        message_lines, buttons = buttons, []
    return _dialog_result(message_lines, buttons, [])


def find_button(lines: List[Dict[str, Any]], button_name: str, match: str = "exact") -> Dict[str, Any]:
    """Return the single text line that matches ``button_name``."""
    wanted = _normalize(button_name)
    if not wanted:
        raise DialogError("BUTTON_NAME_MISSING", "button_name must not be empty")
    if match not in {"exact", "contains"}:
        raise DialogError("INVALID_MATCH_MODE", "match must be exact or contains", match=match)
    if match == "exact":
        found = [line for line in lines if _normalize(line["text"]) == wanted]
    else:
        found = [line for line in lines if wanted in _normalize(line["text"])]
    if not found:
        raise DialogError(
            "BUTTON_NOT_FOUND",
            f"No visible text matches {button_name!r}",
            prompt="Call zbrush_dialog__get_active_dialog and pass one label exactly as it was read.",
            button_candidates=[line["text"] for line in lines],
        )
    if len(found) > 1:
        raise DialogError(
            "AMBIGUOUS_BUTTON",
            f"{len(found)} visible text lines match {button_name!r}",
            prompt="Pass the complete button label with match=exact.",
            matches=[{"text": line["text"], "center": line["center"]} for line in found],
        )
    return found[0]


def _observe(
    backend: Any, process_id: Optional[int], output_dir: Optional[str], window: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    window = window or backend.find_window(process_id)
    directory = os.path.abspath(output_dir) if output_dir else os.path.join(tempfile.gettempdir(), "dcc-mcp-zbrush")
    os.makedirs(directory, exist_ok=True)
    screenshot = os.path.join(directory, f"zbrush-dialog-{os.getpid()}-{time.time_ns()}.png")
    backend.capture(window, screenshot)
    lines = text_lines_from_ocr(backend.recognize_text(screenshot), window)
    if not output_dir:
        # Keep one rolling capture in the temp directory instead of piling them up.
        latest = os.path.join(directory, _LATEST_SCREENSHOT)
        os.replace(screenshot, latest)
        screenshot = latest
    if window.get("kind") == _DIALOG_WINDOW:
        dialog = locate_window_dialog(lines)
    else:
        dialog = locate_dialog(lines)
    return {"window": window, "screenshot_path": screenshot, "text_lines": lines, "dialog": dialog}


def _startup_dialog_visible(backend: Any, process_id: Optional[int]) -> bool:
    """Return True when ZBrush shows a native alert before its bridge listens."""
    try:
        return (backend or default_backend()).find_window(process_id).get("kind") == _DIALOG_WINDOW
    except DialogError:
        return False


def get_active_dialog(
    bridge: Any,
    *,
    backend: Any = None,
    process_id: Optional[int] = None,
    force_capture: bool = False,
    output_dir: Optional[str] = None,
    probe_timeout: float = PROBE_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Detect a blocking dialog and, when one is suspected, read the window text."""
    probe = probe_bridge(bridge, probe_timeout)
    state = probe.get("status")
    if state == STATUS_UNREACHABLE and not _startup_dialog_visible(backend, process_id):
        raise DialogError(
            "ZBRUSH_UNREACHABLE",
            "The ZBrush socket bridge is not listening",
            prompt="Start ZBrush with the dcc-mcp bridge, then retry.",
            bridge_state=state,
        )
    blocked = state in {STATUS_BLOCKED, STATUS_UNREACHABLE}
    result: Dict[str, Any] = {
        "zbrush_blocked": blocked,
        "dialog_open": False,
        "bridge_state": state,
        "active_method": probe.get("active_method"),
    }
    if not blocked and not force_capture:
        return result
    observed = _observe(backend or default_backend(), process_id, output_dir)
    dialog = observed["dialog"]
    result.update(
        window=observed["window"],
        screenshot_path=observed["screenshot_path"],
        window_kind=observed["window"].get("kind", _MAIN_WINDOW),
        ocr_line_count=len(observed["text_lines"]),
        dialog_open=dialog is not None,
    )
    if dialog is not None:
        result.update(
            dialog_kind=dialog["kind"],
            destructive=dialog["destructive"],
            dialog_text=dialog["message"],
            button_candidates=[item["text"] for item in dialog["buttons"]],
            other_labels=dialog["other_labels"],
            dialog_bounds=dialog["bounds"],
        )
    return result


def click_dialog_button(
    bridge: Any,
    button_name: str,
    *,
    backend: Any = None,
    process_id: Optional[int] = None,
    match: str = "exact",
    force: bool = False,
    allow_destructive: bool = False,
    output_dir: Optional[str] = None,
    probe_timeout: float = PROBE_TIMEOUT_SECONDS,
    settle_seconds: float = 1.5,
    verify_attempts: int = 3,
) -> Dict[str, Any]:
    """Click one visible dialog button and verify that the bridge answers again."""
    before = probe_bridge(bridge, probe_timeout).get("status")
    if before == STATUS_UNREACHABLE and not _startup_dialog_visible(backend, process_id):
        raise DialogError(
            "ZBRUSH_UNREACHABLE",
            "The ZBrush socket bridge is not listening",
            prompt="Start ZBrush with the dcc-mcp bridge, then retry.",
            bridge_state=before,
        )
    if before in {STATUS_READY, STATUS_BUSY} and not force:
        raise DialogError(
            "DIALOG_NOT_DETECTED",
            "ZBrush answers the bridge, so no modal dialog is blocking it",
            prompt="Nothing to click. Pass force=true only for a non-blocking note you have seen.",
            bridge_state=before,
        )
    backend = backend or default_backend()
    window = backend.find_window(process_id)
    hold = backend.foreground(window) if hasattr(backend, "foreground") else nullcontext()
    with hold:
        observed = _observe(backend, process_id, output_dir, window=window)
        target, dialog = _choose_button(observed, button_name, match, allow_destructive, before)
        click = dict(backend.click(window, target["center"]["x"], target["center"]["y"]) or {})
    return _verify_click(
        bridge, backend, window, observed, dialog, target, click, before, probe_timeout, settle_seconds, verify_attempts
    )


def _choose_button(
    observed: Dict[str, Any], button_name: str, match: str, allow_destructive: bool, before: Any
) -> tuple:
    dialog = observed["dialog"]
    if dialog is None:
        raise DialogError(
            "DIALOG_TEXT_NOT_FOUND",
            "No dialog text was found in the ZBrush window",
            prompt="ZBrush may be busy with a long operation; wait, then call zbrush_dialog__get_active_dialog.",
            bridge_state=before,
            screenshot_path=observed["screenshot_path"],
        )
    target = find_button(dialog["buttons"], button_name, match)
    risky = is_destructive_text(target["text"]) or (
        dialog["destructive"] and _normalize(target["text"]) not in _SAFE_BUTTONS
    )
    if risky and not allow_destructive:
        raise DialogError(
            "DESTRUCTIVE_CONFIRMATION",
            f"{target['text']!r} may delete, replace, or discard work",
            prompt="Ask the user first. Pass allow_destructive=true only with their approval, or click Cancel.",
            dialog_text=dialog["message"],
            button=target["text"],
            button_candidates=[item["text"] for item in dialog["buttons"]],
        )
    return target, dialog


def _verify_click(
    bridge: Any,
    backend: Any,
    window: Dict[str, Any],
    observed: Dict[str, Any],
    dialog: Dict[str, Any],
    target: Dict[str, Any],
    click: Dict[str, Any],
    before: Any,
    probe_timeout: float,
    settle_seconds: float,
    verify_attempts: int,
) -> Dict[str, Any]:
    state = before
    dismissed = False
    for _attempt in range(max(1, verify_attempts)):
        time.sleep(settle_seconds)
        state = probe_bridge(bridge, probe_timeout).get("status")
        if state in {STATUS_READY, STATUS_BUSY}:
            dismissed = True
        elif window.get("kind") == _DIALOG_WINDOW:
            # A startup alert closes before the bridge listens: check the window itself.
            dismissed = not backend.window_exists(window)
        if dismissed:
            break
    result = {
        "button": target["text"],
        "clicked_at": target["center"],
        "dialog_kind": dialog["kind"],
        "destructive": dialog["destructive"],
        "dialog_text": dialog["message"],
        "window": window,
        "screenshot_path": observed["screenshot_path"],
        "bridge_state_before": before,
        "bridge_state_after": state,
        "dismissed": dismissed,
        "click": click,
    }
    if not dismissed and before in {STATUS_BLOCKED, STATUS_UNREACHABLE}:
        raise DialogError(
            "DIALOG_STILL_BLOCKING",
            "ZBrush is still blocked after the click",
            prompt="Another note may have opened; call zbrush_dialog__get_active_dialog again.",
            **result,
        )
    return result


# --------------------------------------------------------------------------
# macOS backend
# --------------------------------------------------------------------------

_JXA_WINDOWS = """
ObjC.import('CoreGraphics');
function run(argv) {
  var wanted = parseInt(argv[0] || '0', 10);
  var out = {screen_locked: false, windows: []};
  try {
    var session = ObjC.castRefToObject($.CGSessionCopyCurrentDictionary());
    var locked = session.objectForKey('CGSSessionScreenIsLocked');
    out.screen_locked = locked ? !!ObjC.unwrap(locked) : false;
  } catch (error) {}
  var list = ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionAll, $.kCGNullWindowID));
  for (var i = 0; i < list.count; i++) {
    var item = list.objectAtIndex(i);
    var pid = ObjC.unwrap(item.objectForKey('kCGWindowOwnerPID'));
    var owner = ObjC.unwrap(item.objectForKey('kCGWindowOwnerName')) || '';
    if (wanted ? pid !== wanted : owner !== 'ZBrush') continue;
    if (ObjC.unwrap(item.objectForKey('kCGWindowLayer')) !== 0) continue;
    var bounds = item.objectForKey('kCGWindowBounds');
    out.windows.push({
      window_id: ObjC.unwrap(item.objectForKey('kCGWindowNumber')),
      process_id: pid,
      owner: owner,
      title: ObjC.unwrap(item.objectForKey('kCGWindowName')) || '',
      order: i,
      onscreen: !!ObjC.unwrap(item.objectForKey('kCGWindowIsOnscreen')),
      x: ObjC.unwrap(bounds.objectForKey('X')),
      y: ObjC.unwrap(bounds.objectForKey('Y')),
      width: ObjC.unwrap(bounds.objectForKey('Width')),
      height: ObjC.unwrap(bounds.objectForKey('Height'))
    });
  }
  return JSON.stringify(out);
}
"""

_JXA_ACTIVATE = """
ObjC.import('AppKit');
ObjC.import('CoreGraphics');
function spin(seconds) {
  // delay() does not run the event loop, so AppKit state would stay stale.
  $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(seconds));
}
function frontPid() {
  var front = $.NSWorkspace.sharedWorkspace.frontmostApplication;
  return front && !front.isNil() ? front.processIdentifier : 0;
}
function onScreen(windowId) {
  var list = ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly, $.kCGNullWindowID));
  for (var i = 0; i < list.count; i++) {
    if (ObjC.unwrap(list.objectAtIndex(i).objectForKey('kCGWindowNumber')) === windowId) return true;
  }
  return false;
}
function run(argv) {
  var pid = parseInt(argv[0], 10);
  var windowId = parseInt(argv[1] || '0', 10);
  var app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(pid);
  if (!app || app.isNil()) return JSON.stringify({ok: false, error: 'PROCESS_NOT_FOUND'});
  var previous = frontPid();
  var restore = previous === pid ? 0 : previous;
  for (var i = 0; i < 60; i++) {
    if (frontPid() === pid && (!windowId || onScreen(windowId))) {
      return JSON.stringify({ok: true, previous_pid: restore});
    }
    if (i % 20 === 0) {
      app.unhide;
      app.activateWithOptions($.NSApplicationActivateIgnoringOtherApps);
    }
    spin(0.1);
  }
  return JSON.stringify({ok: false, error: 'ACTIVATION_FAILED', previous_pid: restore});
}
"""

_JXA_RESTORE_FOCUS = """
ObjC.import('AppKit');
function run(argv) {
  var app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(parseInt(argv[0], 10));
  if (app && !app.isNil()) app.activateWithOptions($.NSApplicationActivateIgnoringOtherApps);
  $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.3));
  return JSON.stringify({ok: true});
}
"""

_JXA_OCR = """
ObjC.import('Foundation');
ObjC.import('AppKit');
ObjC.import('Vision');
function run(argv) {
  var path = argv[0];
  var image = $.NSBitmapImageRep.imageRepWithContentsOfFile(path);
  if (!image || image.isNil()) return JSON.stringify({error: 'IMAGE_UNREADABLE'});
  var handler = $.VNImageRequestHandler.alloc.initWithURLOptions($.NSURL.fileURLWithPath(path), $({}));
  var request = $.VNRecognizeTextRequest.alloc.init;
  request.recognitionLevel = 0;
  request.usesLanguageCorrection = false;
  var error = $();
  if (!handler.performRequestsError($([request]), error)) return JSON.stringify({error: 'OCR_FAILED'});
  var lines = [];
  for (var i = 0; i < request.results.count; i++) {
    var observation = request.results.objectAtIndex(i);
    var candidate = observation.topCandidates(1).objectAtIndex(0);
    var box = observation.boundingBox;
    lines.push({text: ObjC.unwrap(candidate.string), confidence: candidate.confidence,
                x: box.origin.x, y: box.origin.y, w: box.size.width, h: box.size.height});
  }
  return JSON.stringify({width: image.pixelsWide, height: image.pixelsHigh, lines: lines});
}
"""

_JXA_CLICK = """
ObjC.import('ApplicationServices');
ObjC.import('CoreGraphics');
function topmostAt(point) {
  var list = ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly, $.kCGNullWindowID));
  for (var w = 0; w < list.count; w++) {
    var item = list.objectAtIndex(w);
    if (ObjC.unwrap(item.objectForKey('kCGWindowLayer')) !== 0) continue;
    if (ObjC.unwrap(item.objectForKey('kCGWindowAlpha')) === 0) continue;
    var b = item.objectForKey('kCGWindowBounds');
    var x = ObjC.unwrap(b.objectForKey('X')), y = ObjC.unwrap(b.objectForKey('Y'));
    if (point.x >= x && point.x <= x + ObjC.unwrap(b.objectForKey('Width')) &&
        point.y >= y && point.y <= y + ObjC.unwrap(b.objectForKey('Height'))) {
      return ObjC.unwrap(item.objectForKey('kCGWindowNumber'));
    }
  }
  return 0;
}
function run(argv) {
  if (!$.AXIsProcessTrusted()) return JSON.stringify({ok: false, error: 'ACCESSIBILITY_PERMISSION_REQUIRED'});
  var point = {x: parseFloat(argv[0]), y: parseFloat(argv[1])};
  var expected = parseInt(argv[2], 10);
  var hit = topmostAt(point);
  if (hit !== expected) return JSON.stringify({ok: false, error: 'DIALOG_NOT_FRONTMOST', topmost_window_id: hit});
  var cursor = $.CGEventGetLocation($.CGEventCreate(null));
  [$.kCGEventMouseMoved, $.kCGEventLeftMouseDown, $.kCGEventLeftMouseUp].forEach(function (type) {
    $.CGEventPost($.kCGHIDEventTap, $.CGEventCreateMouseEvent(null, type, point, $.kCGMouseButtonLeft));
    delay(0.08);
  });
  delay(0.1);
  $.CGEventPost($.kCGHIDEventTap, $.CGEventCreateMouseEvent(null, $.kCGEventMouseMoved, cursor, $.kCGMouseButtonLeft));
  return JSON.stringify({ok: true});
}
"""


def _png_size(path: str) -> Optional[tuple]:
    with open(path, "rb") as handle:
        head = handle.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def _check_capture_matches(path: str, window: Dict[str, Any]) -> None:
    """Refuse a capture whose shape is not the window's: OCR boxes would map to the wrong place."""
    size = _png_size(path)
    if not size or not window.get("width") or not window.get("height"):
        return
    expected = float(window["width"]) / float(window["height"])
    actual = size[0] / float(size[1] or 1)
    if abs(actual - expected) > 0.05 * expected:
        raise DialogError(
            "CAPTURE_MISMATCH",
            f"The capture is {size[0]}x{size[1]} px but the window is {window['width']}x{window['height']} pt",
            prompt="Retry; if it persists, report the ZBrush version and the window list.",
        )


class MacOSDialogBackend:
    """Capture, read and click the ZBrush window through macOS system frameworks."""

    def __init__(self, timeout: float = 120.0) -> None:
        self._timeout = timeout
        self._front_depth = 0

    def _jxa(self, source: str, *args: Any) -> Dict[str, Any]:
        try:
            completed = subprocess.run(  # noqa: S603
                [_OSASCRIPT, "-l", "JavaScript", "-e", source, *[str(arg) for arg in args]],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DialogError("OSASCRIPT_FAILED", f"osascript did not complete: {exc}") from exc
        output = completed.stdout.strip()
        try:
            return json.loads(output)
        except ValueError as exc:
            detail = re.sub(r"\s+", " ", completed.stderr.strip())[:400]
            raise DialogError("OSASCRIPT_FAILED", f"osascript returned no JSON: {detail}") from exc

    def _windows(self, process_id: Optional[int]) -> List[Dict[str, Any]]:
        info = self._jxa(_JXA_WINDOWS, int(process_id or 0))
        if info.get("screen_locked"):
            raise DialogError(
                "SCREEN_LOCKED",
                "The macOS session is locked, so the ZBrush window cannot be captured or clicked",
                prompt="Unlock the Mac, then retry.",
            )
        return list(info.get("windows") or [])

    def find_window(self, process_id: Optional[int] = None) -> Dict[str, Any]:
        """Return a native ZBrush alert window when one exists, else the main window."""
        windows = self._windows(process_id)
        large = [w for w in windows if w.get("width", 0) >= _MIN_WINDOW_SIDE and w.get("height", 0) >= _MIN_WINDOW_SIDE]
        if not large:
            raise DialogError("ZBRUSH_WINDOW_NOT_FOUND", "No ZBrush main window was found", process_id=process_id)
        main = max(large, key=lambda window: window["width"] * window["height"])
        # NSAlert panels are small untitled windows; ZBrush's splash screen is titled.
        # A closed alert leaves the window list. A live one is not "onscreen" while ZBrush is
        # hidden or on another Space, so do not filter on visibility; the newest alert wins.
        alerts = [
            window
            for window in windows
            if window is not main
            and not window.get("title")
            and window.get("width", 0) >= _MIN_ALERT_SIDE
            and window.get("height", 0) >= _MIN_ALERT_SIDE
            and window["width"] * window["height"] < 0.25 * main["width"] * main["height"]
        ]
        if alerts:
            alert = max(alerts, key=lambda window: window.get("window_id", 0))
            return dict(alert, kind=_DIALOG_WINDOW)
        return dict(main, kind=_MAIN_WINDOW)

    def window_exists(self, window: Dict[str, Any]) -> bool:
        windows = self._windows(window.get("process_id"))
        return any(item.get("window_id") == window.get("window_id") for item in windows)

    @contextmanager
    def foreground(self, window: Dict[str, Any]) -> Iterator[None]:
        """Bring ZBrush forward once for a capture/click sequence, then give focus back.

        macOS hands out the last backing store of a window that is not on screen
        (behind other apps or on another Space): a frame from before the note
        opened. A click also needs the window on the current Space.
        """
        if self._front_depth:
            self._front_depth += 1
            try:
                yield
            finally:
                self._front_depth -= 1
            return
        activation = self._jxa(_JXA_ACTIVATE, int(window["process_id"]), int(window["window_id"]))
        previous = int(activation.get("previous_pid") or 0)
        try:
            if not activation.get("ok"):
                raise DialogError(
                    str(activation.get("error") or "ACTIVATION_FAILED"),
                    "ZBrush could not be brought to the front",
                    prompt="Keep the Mac unlocked and let the sidecar app control the computer, then retry.",
                )
            self._front_depth = 1
            time.sleep(0.5)  # let ZBrush redraw its canvas, including an open note
            yield
        finally:
            self._front_depth = 0
            if previous:
                self._jxa(_JXA_RESTORE_FOCUS, previous)

    def capture(self, window: Dict[str, Any], output_path: str) -> None:
        with self.foreground(window):
            self._screencapture(window, output_path)

    def _screencapture(self, window: Dict[str, Any], output_path: str) -> None:
        if window.get("kind") == _DIALOG_WINDOW:
            # A message box is a child of the main window, and "-l <id>" hands back the
            # whole main window. Capture its screen rectangle; ZBrush is in front here.
            region = ",".join(str(int(round(float(window[key])))) for key in ("x", "y", "width", "height"))
            command = [_SCREENCAPTURE, "-x", f"-R{region}", output_path]
        else:
            command = [_SCREENCAPTURE, "-x", "-o", f"-l{int(window['window_id'])}", output_path]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DialogError("CAPTURE_FAILED", f"screencapture did not complete: {exc}") from exc
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise DialogError(
                "CAPTURE_FAILED",
                f"screencapture could not capture the ZBrush window: {completed.stderr.strip()}",
                prompt="Grant Screen Recording to the app that runs the sidecar and keep the Mac unlocked.",
            )
        _check_capture_matches(output_path, window)

    def recognize_text(self, image_path: str) -> Dict[str, Any]:
        result = self._jxa(_JXA_OCR, image_path)
        if result.get("error"):
            raise DialogError(str(result["error"]), "macOS Vision could not read the ZBrush window")
        return result

    def click(self, window: Dict[str, Any], x: float, y: float) -> Dict[str, Any]:
        with self.foreground(window):
            result = self._jxa(_JXA_CLICK, x, y, int(window["window_id"]))
        if not result.get("ok"):
            code = str(result.get("error") or "CLICK_FAILED")
            prompts = {
                "ACCESSIBILITY_PERMISSION_REQUIRED": "Grant Accessibility to the app that runs the sidecar, then retry.",
                "DIALOG_NOT_FRONTMOST": "Another window covers the dialog; nothing was clicked. Retry when it is visible.",
            }
            raise DialogError(
                code,
                "The click was not posted to ZBrush",
                prompt=prompts.get(code),
                **{key: value for key, value in result.items() if key not in {"ok", "error"}},
            )
        return result
