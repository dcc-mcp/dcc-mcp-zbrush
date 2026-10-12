# Blocked ZBrush bridge: partial transport safeguards

This documents the narrow safeguards associated with
[issue #89](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/89).
It is not a dialog-inspection or automatic dialog-dismissal feature.

## Evidence and diagnosis

The reporter described macOS 27.0.1 on Apple Silicon, ZBrush 2026.2.1,
adapter 0.2.27, and Core/server 0.20.42. Importing an OBJ with a pentagon face
through `Tool:Import` opens an in-canvas note. Their process sample indicates
that the native call holds the embedded Python GIL. TCP connect still succeeds,
but even the SDK-free `ping` cannot execute until the note closes. This is
reporter-provided native evidence, not a Linux reproduction of ZBrush.

A listening TCP port proves neither Python responsiveness nor main-thread
execution readiness. A failed ping cannot distinguish a dialog from a long
native operation. Do not close a note or click confirmation buttons automatically.

## Safeguards in this change

- Every sidecar host call first makes an SDK-free ping with a 2-second budget
  (configurable as `SocketBridge(probe_timeout=...)`, capped by its call timeout).
  An unresponsive or unavailable bridge prevents that host call from being sent.
- A responsive bridge reporting `busy` also prevents a new host call. A later
  call probes again; there is no permanent fault latch or forced task termination.
- Host requests carry a top-level `deadline_unix_ms`. The plugin checks it on
  admission, including after a delayed TCP accept, and converts the remaining
  budget to a local monotonic deadline capped at its 600-second wait budget.
- The main-thread pump checks the deadline before starting host work. A queued
  request that expires is cancelled and never executed; cancelled queue entries
  do not accumulate across retries.
- An already-started operation cannot be cancelled or rolled back by this
  mechanism. Its timeout remains non-retryable and keeps the plugin busy until
  it finishes. Losing its response means the outcome is unknown: inspect the
  scene before repeating a mutation.

Error categories are `ZBRUSH_BUSY` (-32001), the existing
`ZBRUSH_TIMEOUT_STILL_RUNNING` (-32002; also used with
`execution_state: "unknown"` when the response is lost),
`ZBRUSH_UNRESPONSIVE` (-32003, with transport state), and
`ZBRUSH_REQUEST_EXPIRED` (-32004, `executed: false`). A preflight rejection
includes `submitted: false`. A timeout is never evidence that an already-started
operation did not modify the scene.

## Important limitations

- **Upstream cancellation is not propagated by this change.** The bridge's
  default 610-second deadline is not the same as an earlier MCP/REST/CLI deadline.
  If the outer caller times out earlier, the bridge call can still start or
  finish before its own deadline. End-to-end cancellation remains open.
- **`/v1/readyz` is unchanged.** Its current readiness verdict is not proof that
  ZBrush's main-thread pump can execute work. A complete readiness integration
  needs independent external health/recovery polling and main-thread evidence;
  a listener-only ping must not set every execution bit to ready. Existing Core
  readiness APIs can be evaluated without assuming a new cross-Core contract.
- Preflight is a point-in-time observation. A native operation or dialog can
  begin immediately after it succeeds; the deadline check is a separate guard.
- Update both the sidecar package and installed plugin for wire-deadline
  protection. An older plugin ignores this extra field. Older clients without
  the field still receive the plugin's local queue-timeout protection, but the
  plugin cannot infer how long those requests waited in the TCP backlog.
- The sidecar and ZBrush normally run on the same machine and share a wall
  clock. A remote socket configuration requires synchronized clocks for wire
  expiry. Once admitted, queue expiry uses monotonic time.
- This does not inspect, identify, classify, read, or click native/in-canvas
  dialogs. No automatic dismissal, scene rollback, or process kill is performed.

## Validation

Linux tests exercise queue state transitions and real loopback TCP peers that
accept a connection without responding, or accept already-expired requests.
They verify transport and queue behavior only. Mocked handlers are not native
ZBrush acceptance evidence.

Before considering the native issue resolved, test on the reported macOS/ZBrush
versions with the pentagon OBJ and an additional long-running non-modal operation:

1. Confirm short ping reports unresponsive while the native call holds the GIL,
   without claiming to identify a dialog or stopping the active operation.
2. Confirm a new host call is not sent after a failed preflight.
3. Hold the main-thread pump beyond the bridge deadline, then resume it and
   verify that the expired queued mutation never starts.
4. Confirm an already-started operation is never retried automatically after
   timeout and that manually dismissing the note restores subsequent probing.
5. Separately track the still-open upstream cancellation and dynamic readiness
   work; do not use this PR to claim those cases are fixed.
