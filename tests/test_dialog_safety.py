"""Portable fail-closed regressions for the macOS dialog backend.

The JXA programs run only in a Node VM with fake CoreGraphics objects. These
tests exercise the shipped JavaScript policy, not native macOS behavior.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

from dcc_mcp_zbrush import _dialogs

_NODE = shutil.which("node")
_HARNESS = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
function dictionary(values) {
  return {objectForKey: key => {
    const value = values[key];
    return value && typeof value === 'object' ? dictionary(value) : value;
  }};
}
const windows = input.windows.map(dictionary);
const posted = [];
const context = {
  ObjC: {import: () => {}, unwrap: x => x, castRefToObject: x => x},
  $: {
    CGWindowListCopyWindowInfo: () => ({count: windows.length, objectAtIndex: i => windows[i]}),
    CGSessionCopyCurrentDictionary: () => dictionary({CGSSessionScreenIsLocked: false}),
    AXIsProcessTrusted: () => true,
    CGEventGetLocation: () => ({x: 0, y: 0}),
    CGEventCreate: () => ({}),
    CGEventCreateMouseEvent: (unused, type, point) => ({type, point}),
    CGEventPost: (tap, event) => posted.push(event),
    kCGEventMouseMoved: 'move', kCGEventLeftMouseDown: 'down', kCGEventLeftMouseUp: 'up'
  },
  delay: () => {}, argv: input.argv, posted
};
const output = vm.runInNewContext(input.source + '\nJSON.stringify({result: JSON.parse(run(argv)), posted})', context);
process.stdout.write(output);
"""


def _line(text: str, x: int = 10, y: int = 10) -> dict:
    return {
        "text": text,
        "confidence": 1.0,
        "bounds": {"x": x, "y": y, "width": 100, "height": 20},
        "center": {"x": x + 50, "y": y + 10},
    }


def _window(window_id: int = 7, pid: int = 4321, owner: str = "ZBrush", layer: int = 0, alpha=1) -> dict:
    return {
        "kCGWindowNumber": window_id,
        "kCGWindowOwnerPID": pid,
        "kCGWindowOwnerName": owner,
        "kCGWindowLayer": layer,
        "kCGWindowAlpha": alpha,
        "kCGWindowName": "ZBrush",
        "kCGWindowIsOnscreen": True,
        "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 800, "Height": 600},
    }


def _jxa(source: str, windows: list, *argv) -> dict:
    result = subprocess.run(
        [_NODE, "-e", _HARNESS],
        input=json.dumps({"source": source, "windows": windows, "argv": list(map(str, argv))}),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


class DialogConfirmationTests(unittest.TestCase):
    def test_ci_requires_the_jxa_policy_test_runtime(self):
        if os.environ.get("CI"):
            self.assertIsNotNone(_NODE, "CI must provide Node; JXA safety coverage must not silently skip")

    def observed(self, message="Delete SubTool. This cannot be undone.", labels=("OK",)):
        dialog = _dialogs._dialog_result([_line(message)], [_line(label, y=100) for label in labels], [])
        return {"dialog": dialog, "screenshot_path": "synthetic.png"}

    def test_single_recognized_button_keeps_destructive_message_gate(self):
        observed = self.observed()
        self.assertTrue(observed["dialog"]["destructive"])
        with self.assertRaises(_dialogs.DialogError) as raised:
            _dialogs._choose_button(observed, "OK", "exact", False, "blocked")
        self.assertEqual(raised.exception.code, "DESTRUCTIVE_CONFIRMATION")

    def test_low_confidence_cancel_does_not_make_remaining_ok_safe(self):
        message = _line("Delete SubTool. This cannot be undone.", y=100)
        message["bounds"]["width"] = 400
        cancel, ok = _line("Cancel", y=160), _line("OK", x=160, y=160)
        cancel["confidence"] = 0.59
        dialog = _dialogs.locate_dialog([message, cancel, ok])
        self.assertEqual([button["text"] for button in dialog["buttons"]], ["OK"])
        with self.assertRaises(_dialogs.DialogError) as raised:
            _dialogs._choose_button({"dialog": dialog}, "OK", "exact", False, "blocked")
        self.assertEqual(raised.exception.code, "DESTRUCTIVE_CONFIRMATION")

    def test_dedicated_window_single_button_preserves_destructive_gate(self):
        dialog = _dialogs.locate_window_dialog(
            [_line("Delete SubTool. This cannot be undone.", y=100), _line("OK", y=160)]
        )
        self.assertEqual([button["text"] for button in dialog["buttons"]], ["OK"])
        with self.assertRaises(_dialogs.DialogError) as raised:
            _dialogs._choose_button({"dialog": dialog}, "OK", "exact", False, "blocked")
        self.assertEqual(raised.exception.code, "DESTRUCTIVE_CONFIRMATION")

    def test_single_button_refusal_never_reaches_backend_click(self):
        class Bridge:
            def probe(self, timeout):
                return {"status": "blocked"}

        class Backend:
            def find_window(self, pid):
                return {"window_id": 7, "process_id": 4321}

            def click(self, *args):
                raise AssertionError("a refused destructive answer reached click")

        with patch.object(_dialogs, "_observe", return_value=self.observed()):
            with self.assertRaises(_dialogs.DialogError) as raised:
                _dialogs.click_dialog_button(Bridge(), "OK", backend=Backend(), settle_seconds=0)
        self.assertEqual(raised.exception.code, "DESTRUCTIVE_CONFIRMATION")

    def test_explicit_destructive_confirmation_remains_available(self):
        target, dialog = _dialogs._choose_button(self.observed(), "OK", "exact", True, "blocked")
        self.assertEqual(target["text"], "OK")
        self.assertTrue(dialog["destructive"])

    def test_cancel_remains_safe_on_destructive_note(self):
        target, dialog = _dialogs._choose_button(self.observed(labels=("Cancel",)), "Cancel", "exact", False, "blocked")
        self.assertEqual(target["text"], "Cancel")
        self.assertTrue(dialog["destructive"])

    def test_informational_single_button_is_unchanged(self):
        observed = self.observed(message="Import complete.")
        target, dialog = _dialogs._choose_button(observed, "OK", "exact", False, "blocked")
        self.assertEqual(target["text"], "OK")
        self.assertFalse(dialog["destructive"])


@unittest.skipUnless(_NODE, "Node is required for the isolated JXA policy mocks")
class DialogJxaSafetyTests(unittest.TestCase):
    def test_explicit_pid_does_not_admit_other_application(self):
        output = _jxa(_dialogs._JXA_WINDOWS, [_window(owner="OtherApp")], 4321)
        self.assertEqual(output["result"]["windows"], [])
        self.assertEqual(output["posted"], [])

    def test_explicit_pid_and_zbrush_owner_must_both_match(self):
        output = _jxa(_dialogs._JXA_WINDOWS, [_window(pid=9999), _window(), _window(owner="OtherApp")], 4321)
        self.assertEqual([w["window_id"] for w in output["result"]["windows"]], [7])
        self.assertEqual(len(output["result"]["windows"]), 1)

    def test_automatic_selection_still_rejects_other_owner(self):
        output = _jxa(_dialogs._JXA_WINDOWS, [_window(owner="OtherApp")], 0)
        self.assertEqual(output["result"]["windows"], [])

    def test_floating_window_occlusion_refuses_without_posting_events(self):
        for layer in (0, 3, 25):
            with self.subTest(layer=layer):
                output = _jxa(_dialogs._JXA_CLICK, [_window(99, owner="OtherApp", layer=layer), _window()], 100, 100, 7)
                self.assertEqual(output["result"].get("error"), "DIALOG_NOT_FRONTMOST")
                self.assertEqual(output["result"]["topmost_window_id"], 99)
                self.assertEqual(output["posted"], [])

    def test_transparent_window_does_not_block_visible_target(self):
        output = _jxa(_dialogs._JXA_CLICK, [_window(99, layer=3, alpha=0), _window()], 100, 100, 7)
        self.assertTrue(output["result"]["ok"])
        self.assertEqual([event["type"] for event in output["posted"]], ["move", "down", "up", "move"])

    def test_nonoverlapping_floating_window_does_not_block_target(self):
        overlay = _window(99, layer=3)
        overlay["kCGWindowBounds"]["X"] = 900
        output = _jxa(_dialogs._JXA_CLICK, [overlay, _window()], 100, 100, 7)
        self.assertTrue(output["result"]["ok"])
        self.assertEqual(len(output["posted"]), 4)

    def test_window_behind_target_does_not_block_target(self):
        output = _jxa(_dialogs._JXA_CLICK, [_window(), _window(99, layer=3)], 100, 100, 7)
        self.assertTrue(output["result"]["ok"])
        self.assertEqual(len(output["posted"]), 4)

    def test_same_application_floating_window_still_blocks_click(self):
        output = _jxa(_dialogs._JXA_CLICK, [_window(99, layer=3), _window()], 100, 100, 7)
        self.assertEqual(output["result"].get("error"), "DIALOG_NOT_FRONTMOST")
        self.assertEqual(output["posted"], [])

    def test_unoccluded_target_preserves_click_event_sequence(self):
        output = _jxa(_dialogs._JXA_CLICK, [_window()], 100, 100, 7)
        self.assertTrue(output["result"]["ok"])
        self.assertEqual([event["type"] for event in output["posted"]], ["move", "down", "up", "move"])

    def test_missing_target_refuses_without_posting_events(self):
        output = _jxa(_dialogs._JXA_CLICK, [], 100, 100, 7)
        self.assertEqual(output["result"].get("error"), "DIALOG_NOT_FRONTMOST")
        self.assertEqual(output["posted"], [])


if __name__ == "__main__":
    unittest.main()
