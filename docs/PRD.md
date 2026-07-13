# Product Requirements Document: dcc-mcp-zbrush

**Version:** 0.2.2
**Status:** Draft
**Owner:** Long Hao
**Last Updated:** 2026-06-06

---

## 1. Background & Motivation

The DCC-MCP ecosystem (`dcc-mcp-core`) provides a standardized way for AI agents to interact with Digital Content Creation (DCC) tools via the Model Context Protocol (MCP). Existing adapters (Maya, Blender, Houdini) rely on an **embedded Python interpreter** inside the DCC application.

**ZBrush** (Maxon) is the industry-standard sculpting tool used by character artists, game studios, and VFX pipelines. **ZBrush 2026.1+** ships with a first-class **embedded Python SDK** (`zbrush.commands`) that exposes scene interrogation, subtool management, mesh export, and ZBrush UI automation.

This PRD covers the design and requirements for `dcc-mcp-zbrush`, an MCP adapter that connects AI agents to ZBrush via the embedded Python SDK as the **primary path**, with a **sidecar TCP socket bridge as fallback** for environments where the MCP server cannot run inside ZBrush.

---

## 2. Goals

### Primary Goals
1. Allow AI agents (Claude Desktop, Cursor, custom MCP hosts) to query and control ZBrush sculpting sessions
2. Support **two runtime modes**: embedded (in-process) and sidecar (socket bridge)
3. Implement the same skill-based architecture as `dcc-mcp-maya` (consistent UX for developers)

### Non-Goals
- Supporting ZBrush versions before 2026.1 (no embedded Python SDK)
- Replacing ZBrush's native UI or ZScript workflow
- Direct manipulation of ZBrush render engine (deferred)

---

## 3. Architecture

### 3.1 Two Modes

The adapter supports two mutually exclusive modes, resolved automatically or via `DCC_MCP_ZBRUSH_MODE`:

| Mode | Description | Detection |
|------|-------------|-----------|
| `embedded` | **Primary.** MCP server runs inside ZBrush's Python VM. Skills call `zbrush.commands` directly. | `import zbrush.commands` succeeds |
| `sidecar` | **Fallback.** MCP server runs as a standalone process; tool calls forwarded via TCP JSON-RPC to a ZBrush plugin socket. | `import zbrush.commands` fails |

Auto-detection logic (`_env.resolve_mode`): try `embedded` first; if `zbrush.commands` is not available, fall back to `sidecar`.

### 3.2 Component Diagram — Embedded Mode (Primary)

```
┌──────────────────────────────────────────────────────────────┐
│  AI Agent (Claude Desktop / Cursor / Custom MCP Host)        │
└─────────────────────────┬────────────────────────────────────┘
                          │ MCP Streamable HTTP (2025-03-26 spec)
                          │ POST /mcp  (tools/call, tools/list)
┌─────────────────────────▼────────────────────────────────────┐
│  ZBrush 2026.1+  (process)                                    │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  ZBrushMcpServer  (dcc-mcp-zbrush, port 8765)           │  │
│  │  ┌─────────────────────────────────────────────────┐    │  │
│  │  │  dcc-mcp-core                                    │    │  │
│  │  │  ├── McpHttpServer (axum HTTP)                   │    │  │
│  │  │  ├── DccServerBase                               │    │  │
│  │  │  ├── InProcessCallableDispatcher                 │    │  │
│  │  │  └── SkillCatalog (discovers zbrush-* skills)    │    │  │
│  │  └─────────────────────────────────────────────────┘    │  │
│  │  ┌─────────────────────────────────────────────────┐    │  │
│  │  │  _executor: InProcessCallableDispatcher         │    │  │
│  │  │  └── calls zbrush.commands directly             │    │  │
│  │  └─────────────────────────────────────────────────┘    │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 3.3 Component Diagram — Sidecar Mode (Fallback)

```
┌──────────────────────────────────────────────────────────────┐
│  AI Agent (Claude Desktop / Cursor / Custom MCP Host)        │
└─────────────────────────┬────────────────────────────────────┘
                          │ MCP Streamable HTTP (2025-03-26 spec)
                          │ POST /mcp
┌─────────────────────────▼────────────────────────────────────┐
│  dcc-mcp-zbrush sidecar process (Python)                      │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  ZBrushMcpServer  (port 8765)                           │  │
│  │  ├── dcc-mcp-core stack                                 │  │
│  │  └── SocketBridge (TCP JSON-RPC client)                 │  │
│  └─────────────────────────┬───────────────────────────────┘  │
└────────────────────────────┼──────────────────────────────────┘
                             │ TCP JSON-RPC (port 9876)
┌────────────────────────────▼──────────────────────────────────┐
│  ZBrush 2026.1+  (process)                                    │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  mcp_socket_bridge.py  (Asset/plugin scan root)         │  │
│  │  ├── TCP server on 127.0.0.1:9876                       │  │
│  │  └── calls zbrush.commands for each RPC method          │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 3.4 DccCapabilities

```python
from dcc_mcp_core import DccCapabilities

caps = DccCapabilities(
    has_embedded_python=True,       # ZBrush 2026.1+ primary
    bridge_kind="socket",           # sidecar fallback (TCP JSON-RPC)
    bridge_endpoint="127.0.0.1:9876",
    scene_info=True,
    file_operations=True,
    snapshot=False,                 # render deferred
)
```

### 3.5 Skill Execution Flow — Embedded Mode

```
Agent calls tools/call "zbrush_scene__list_subtools"
    ↓
DccServerBase dispatches to skill handler
    ↓
Skill: list_subtools.py
    ↓
import zbrush.commands as zbc  (embedded, in-process)
    ↓
zbc.get_subtool_count(), zbc.get_subtool_status(index)
    ↓
zb_success("Found 3 SubTools", subtools=[...]) → dict
    ↓
MCP tools/call response → Agent
```

### 3.6 Skill Execution Flow — Sidecar Mode

```
Agent calls tools/call "zbrush_scene__list_subtools"
    ↓
DccServerBase dispatches to skill handler
    ↓
Skill: list_subtools.py
    ↓
api.get_bridge() → SocketBridge instance
    ↓
bridge.call("list_subtools") → TCP JSON-RPC request
    ↓
mcp_socket_bridge.py executes zbrush.commands inside ZBrush
    ↓
JSON-RPC response → SocketBridge
    ↓
zb_success("Found 3 SubTools", subtools=[...]) → dict
    ↓
MCP tools/call response → Agent
```

---

## 4. Modes & Environment

### 4.1 Mode Resolution

```
DCC_MCP_ZBRUSH_MODE  →  "embedded" | "sidecar"
    ↓ not set
Auto-detect:
  - import zbrush.commands succeeds  →  embedded
  - import fails                      →  sidecar
```

### 4.2 Env Vars

| Variable | Default | Purpose |
|----------|---------|---------|
| `DCC_MCP_ZBRUSH_MODE` | auto | `embedded` or `sidecar` |
| `DCC_MCP_ZBRUSH_PORT` | 8765 | MCP HTTP server port |
| `DCC_MCP_ZBRUSH_SOCKET_HOST` | 127.0.0.1 | Socket bridge host |
| `DCC_MCP_ZBRUSH_SOCKET_PORT` | 9876 | Socket bridge port |
| `DCC_MCP_ZBRUSH_AUTOSTART` | 1 | Auto-start embedded server on ZBrush launch |
| `DCC_MCP_ZBRUSH_ENABLE_GATEWAY_FAILOVER` | true | Gateway failover toggle |
| `DCC_MCP_ZBRUSH_SKILL_PATHS` | "" | Colon/semicolon-separated extra skill search paths |
| `DCC_MCP_MINIMAL` | 1 | Enable minimal mode (bootstrap + scene only) |

### 4.3 MCP Endpoint

```
http://127.0.0.1:8765/mcp
```

Streamable HTTP transport per MCP 2025-03-26 spec. The Rust `axum` HTTP server is embedded in `dcc-mcp-core`.

---

## 5. ZBrush Python SDK Reference (ZBrush 2026.1+)

### 5.1 Core Module

```
zbrush.commands  (abbreviated zbc)
```

Provided by Maxon SDK `zbrush-2026.1.0+`. Not available in any earlier ZBrush version.

### 5.2 Known API Surface (mapped by bundled skills)

| Function | Description |
|----------|-------------|
| `zbrush_info(index)` | ZBrush version `(major, minor)` |
| `get_subtool_count()` | Number of SubTools |
| `get_subtool_status(index)` | Bitmask: bit 0=visible, bit 1=locked |
| `get_active_subtool_index()` | Current SubTool index |
| `select_subtool(index)` | Switch active SubTool |
| `get_active_tool_path()` | Full ZTool path string |
| `set_next_filename(path)` | Set export filename before pressing Tool:Export |

Full SDK reference: https://developers.maxon.net/docs/zbrush/py/2026_1_0/index.html

### 5.3 Environment Restrictions

- Persistent Python interpreter shared across scripts (no per-run isolation)
- `subprocess`/`multiprocessing` using `sys.executable` are **unsupported** inside ZBrush's VM
- Scene APIs require `affinity: main` thread
- `exec()` is available; skill code runs in the embedded interpreter

---

## 6. Sidecar Socket Bridge

### 6.1 Protocol

TCP JSON-RPC 2.0, newline-delimited frames. Each request is a JSON object followed by `\n`; the response is a JSON object followed by `\n`.

**Request:**
```json
{"jsonrpc": "2.0", "id": 1, "method": "list_subtools", "params": {}}
```

**Response:**
```json
{"jsonrpc": "2.0", "id": 1, "result": {"count": 3, "subtools": [...]}}
```

### 6.2 RPC Methods

| Method | Params | Description |
|--------|--------|-------------|
| `ping` | — | Health check |
| `get_session_info` | — | ZBrush version, active tool, subtool count |
| `get_scene_info` | — | Active tool path, subtool count, active subtool index |
| `list_subtools` | — | All SubTools with visibility/lock flags |
| `select_subtool` | `index` | Switch active SubTool |
| `get_subtool_status` | `index` (optional) | Status for one SubTool (default: active) |
| `execute_python` | `code`, `context` | Execute arbitrary Python in ZBrush VM |
| `export_active_subtool_obj` | `output_path` | Export active SubTool as OBJ |

### 6.3 Distribution Sources

The project ships through three independent channels. Know the boundary of each:

| Channel | Contains | Does NOT contain |
|---------|----------|------------------|
| **PyPI wheel** (`pip install dcc-mcp-zbrush`) | Python MCP server, skills, CLI entry point, `SocketBridge` client | ZBrush plugin files (`bridge/plugin/`) |
| **Plugin ZIP** (GitHub Release asset) | Auto-start plugin package, `mcp_socket_bridge.py`, install scripts | Python package (`src/dcc_mcp_zbrush/`) |
| **Repo source** (`git clone`) | Everything above + tests + tools + `bridge/plugin/` raw sources | — |

The wheel (`pyproject.toml:46-47`) only packages `src/dcc_mcp_zbrush`. The `bridge/plugin/` directory is **not** in the wheel — it is distributed via the plugin ZIP or available in the repo checkout.

### 6.4 Plugin Installation

Copy `bridge/plugin/mcp_socket_bridge.py` directly into the ZBrush Asset Directory or a `ZBRUSH_PLUGIN_PATH` root, then **restart ZBrush**. The plugin auto-starts the TCP listener on next ZBrush launch.

### 6.5 Auto-Start Plugin (Embedded Mode)

The `bridge/plugin/dcc_mcp_zbrush/__init__.py` package auto-starts `dcc_mcp_zbrush.start_server(mode="embedded")` when ZBrush loads the plugin directory. Controlled by `DCC_MCP_ZBRUSH_AUTOSTART` (default: on).

---

## 7. Skill Catalog

### 7.1 Bundled Skills

The adapter ships with these built-in skills in `src/dcc_mcp_zbrush/skills/`:

#### `zbrush-scripting` (bootstrap stage, loaded by default)
| Tool | Description |
|------|-------------|
| `execute_python` | Execute arbitrary Python code in ZBrush VM (escape hatch) |
| `get_session_info` | ZBrush version, active tool path, subtool count |

#### `zbrush-scene` (scene stage, loaded by default)
| Tool | Description |
|------|-------------|
| `get_scene_info` | Active tool path, subtool count, active subtool index |
| `list_subtools` | All SubTools with visibility/lock flags |

#### `zbrush-subtool` (authoring stage, not loaded by default)
| Tool | Description |
|------|-------------|
| `select_subtool` | Switch active SubTool |
| `get_subtool_status` | Status for one SubTool |
| `rename_subtool` | Rename a SubTool |
| `reorder_subtool` | Reorder subtools |
| `delete_subtool` | Delete a SubTool |
| `duplicate_subtool` | Duplicate a SubTool |
| `merge_subtools` | Merge visible SubTools |
| `toggle_subtool_visibility` | Show/hide a SubTool |
| `toggle_subtool_lock` | Lock/unlock a SubTool |

#### `zbrush-interchange` (interchange stage, not loaded by default)
| Tool | Description |
|------|-------------|
| `export_active_subtool_obj` | Export active SubTool as OBJ file |

### 7.2 Skill Loading Stages

| Stage | Skills | Default | Trigger |
|-------|--------|---------|---------|
| `bootstrap` | `zbrush-scripting` | Yes | Server start |
| `scene` | `zbrush-scene` | Yes | Server start |
| `authoring` | `zbrush-subtool` | No | `load_skill("zbrush-subtool")` |
| `interchange` | `zbrush-interchange` | No | `load_skill("zbrush-interchange")` |

Minimal mode (`DCC_MCP_MINIMAL=1`) loads only bootstrap + scene at startup. Additional skills are loaded on demand via `server.load_skill("zbrush-*")`.

### 7.3 Common Agent Chains

| Task | Chain |
|------|-------|
| Verify MCP session | `zbrush_scripting__get_session_info` |
| Inspect active tool | `zbrush_scene__get_scene_info` → `zbrush_scene__list_subtools` |
| Switch subtool | `load_skill("zbrush-subtool")` → `zbrush_subtool__select_subtool` |
| Export to OBJ | `zbrush_subtool__select_subtool` → `load_skill("zbrush-interchange")` → `zbrush_interchange__export_active_subtool_obj` |
| Escape hatch | `load_skill("zbrush-scripting")` → `zbrush_scripting__execute_python` |

### 7.4 Agent-Automatable vs Human-Required Steps

| Step | Agent | Human |
|------|-------|-------|
| Install dcc-mcp-zbrush Python package | ✓ | |
| Set `PYTHONPATH` / `ZBRUSH_PLUGIN_PATH` | | ✓ (needs ZBrush restart) |
| Drop `mcp_socket_bridge.py` into the Asset/plugin scan root | | ✓ (needs file system access) |
| Launch ZBrush | | ✓ |
| Verify MCP endpoint is reachable | ✓ | |
| Load a skill | ✓ | |
| Call a tool | ✓ | |
| Interpret errors from `zbrush.commands` | ✓ | |
| Restart ZBrush after plugin update | | ✓ |

---

## 8. Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| MCP HTTP timeout | Configurable (default: from dcc-mcp-core) |
| Socket bridge timeout | 120s default, configurable |
| Connection retry | None (fail-fast on embedded; sidecar connect on start) |
| Startup time | < 2s (embedded) or < 1s (sidecar) |
| Python version | 3.9+ |
| ZBrush version | 2026.1+ |
| Dependencies | `dcc-mcp-core>=0.18.7,<1.0.0` |
| Transport | MCP Streamable HTTP (2025-03-26) |
| Sidecar transport | TCP JSON-RPC 2.0 (newline-delimited) |

---

## 9. Error Handling

### Embedded Mode
- ZBrush SDK unavailable → `ZBrushNotAvailableError` → skill returns `zb_error()` with prompt to start ZBrush or switch to sidecar mode
- `zbrush.commands` API failure → caught by `@with_zbrush` decorator → `zb_from_exception()`
- SubTool index out of range → structured error with valid range

### Sidecar Mode
- Plugin not running → `ZBrushNotAvailableError` on connect → logged warning, skills return `zb_error()`
- TCP connection lost → `ZBrushBridgeError` → skills return `zb_error()` with reconnection prompt
- Unknown RPC method → JSON-RPC error code `-32601`
- Python execution error in ZBrush VM → stack trace in error data

### General
- `@with_zbrush` decorator catches all bridge/NotImplementedError/Exception
- Returns structured `zb_error()` dict (never throws to MCP layer)
- Server startup tolerates missing bridge (sidecar connect is async-best-effort)

---

## 10. Testing Strategy

### Unit Tests (no ZBrush required)
- Mock `zbrush.commands` with `unittest.mock` for embedded mode
- Mock `socket` for sidecar bridge `SocketBridge.call()`
- Test all `zb_success`/`zb_error`/`with_zbrush` helpers
- Test mode auto-detection logic

### Integration Tests (requires ZBrush 2026.1+)
- Marked `@pytest.mark.e2e`
- Run inside ZBrush's embedded Python or against a running socket plugin
- Verify real `zbrush.commands` API calls

---

## 11. Versioning & Release

Follows the same `release-please` workflow as `dcc-mcp-core` and `dcc-mcp-maya`.

| Version | Milestone |
|---------|-----------|
| 0.1.x | Placeholder skeleton |
| 0.2.x | Embedded SDK integration + bundled skills (current) |
| 0.3.x | Full subtool management + interchange (OBJ/FBX export) |
| 0.4.x | Advanced scene query + render pipeline |
| 1.0.0 | Stable API + full CI + docs |

---

## 12. Open Questions

1. **FBX export**: Does `zbrush.commands` expose FBX export, or is a separate script needed? → Currently only OBJ export via `Tool:Export` button press.
2. **Concurrent access**: ZBrush's Python VM is single-threaded for scene operations. Does the MCP server need a request queue? → Sidecar mode uses threading; embedded mode is synchronous. Add serialization if contention arises.
3. **ZBrush version probing**: `zbrush_info()` returns `(major, minor)`. Need to validate the exact version string format in 2026.1+ SDK releases.
