---
name: zbrush-dialog
description: >-
  Recovery skill — detect, read, and answer a blocking ZBrush note or popup
  dialog (OK, Cancel, Yes, No, or any labelled note button) so an automated
  workflow can continue. Use when ZBrush tool calls return ZBRUSH_BLOCKED,
  time out, or return ZBRUSH_TIMEOUT_STILL_RUNNING, or a script may have
  opened a confirmation note. Not for modelling, export, or general UI automation — use the typed
  ZBrush skills or the shared ui-control skill for those.
license: MIT
compatibility: "dcc-mcp-zbrush 0.2+, ZBrush 2026.1+, dcc-mcp-core 0.20.14+, sidecar mode on macOS"
allowed-tools: Bash Read
metadata:
  dcc-mcp:
    dcc: zbrush
    layer: domain
    stage: recovery
    version: "1.0.0"
    tags: [zbrush, dialog, popup, note, modal, recovery]
    search-hint: "popup, dialog, note, modal, message box, OK, Cancel, Yes, No, blocked, frozen, timeout, click button"
    tools: tools.yaml
---

# zbrush-dialog

A ZBrush note or system message runs its own input loop on the ZBrush main
thread and holds the Python GIL. While it is open, every bridge call waits, so
these tools never send work to ZBrush. They run in the sidecar process:

1. `get_active_dialog` probes the socket bridge with a short timeout
   (`SocketBridge.probe()`). A `blocked` status means ZBrush cannot answer.
   Only then does the tool capture the ZBrush window and read its text with
   on-device OCR. It returns the `dialog_text`, `button_candidates`, and a
   screenshot path.
2. `click_dialog_button` re-reads the window, finds one exact label, posts one
   mouse click on its centre, and pings the bridge again to prove that ZBrush
   is free. It refuses to click when ZBrush is not blocked, unless
   `force=true`.

## Safe use

- ZBrush draws its own notes inside the main window (for example the OBJ
  n-gon import question), so the tool reads that window. Script message boxes
  (`message_ok_cancel` and friends) and the startup crash notice open in a
  small separate window; the tool reads those too.
- The tools bring ZBrush to the front for the capture and the click (macOS
  hands out a stale frame for a window that is not on screen), then give the
  focus back.
- Read the dialog first and choose the button from `button_candidates`.
  `dialog_kind` is `informational`, `question`, or
  `destructive_confirmation`. On a destructive note every button except
  Cancel needs `allow_destructive=true`; pass it only after the user approves.
- A long native operation (for example a big remesh) also stops ping replies.
  Check the returned text: if no dialog text is visible, wait instead of
  clicking.
- A call that timed out still runs after the note is dismissed. Do not resend
  it; read the scene state first.
- Some notes have no title; use the visible message text to identify them.

## Requirements

- Sidecar mode (the default). The tools return `SIDECAR_REQUIRED` inside
  ZBrush, because an open note blocks code there. For the same reason they
  declare `affinity: any`, unlike the other ZBrush tools: they never call
  `zbrush.commands`.
- macOS: grant Screen Recording (capture) and Accessibility (click) to the app
  that runs `dcc-mcp-zbrush`, and keep the session unlocked (`SCREEN_LOCKED`).
- Other platforms return `UNSUPPORTED_PLATFORM`. Use the shared `ui-control`
  skill (`dcc-cua`) to snapshot the ZBrush window and click the button there.
