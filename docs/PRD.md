# Product Requirements Document: dcc-mcp-zbrush

**Version:** 0.1.0  
**Status:** Draft  
**Owner:** Long Hao  
**Last Updated:** 2026-04-11

---

## 1. Background & Motivation

The DCC-MCP ecosystem (`dcc-mcp-core`) provides a standardized way for AI agents to
interact with Digital Content Creation (DCC) tools via the Model Context Protocol (MCP).
Existing adapters (Maya, Blender, Houdini) rely on an **embedded Python interpreter** inside
the DCC application.

**ZBrush** is the industry-standard sculpting tool used by character artists, game studios,
and VFX pipelines. Unlike Maya/Blender, ZBrush does not expose an embedded Python interpreter.
Instead, **ZBrush 2024+** provides a built-in **HTTP REST server** that accepts ZScript commands.

This PRD covers the design and requirements for `dcc-mcp-zbrush`, a bridge adapter that
connects AI agents to ZBrush via this HTTP API.

---

## 2. Goals

### Primary Goals
1. Allow AI agents (Claude, Cursor, etc.) to query and control ZBrush sculpting sessions
2. Implement the same skill-based architecture as `dcc-mcp-maya` (consistent UX for developers)
3. Provide a clear bridge pattern for other non-Python DCCs (Photoshop, etc.) to follow

### Non-Goals
- Direct ZBrush plugin development (the HTTP server is already in ZBrush 2024+)
- Supporting ZBrush versions before 2024
- Replacing ZBrush's native ZScript workflow

---

## 3. Architecture

### 3.1 Component Diagram

```
┌──────────────────────────────────────────────────────────────┐
│  AI Agent (Claude Desktop / Cursor / Custom MCP Host)        │
└─────────────────────────┬────────────────────────────────────┘
                          │ MCP Streamable HTTP (2025-03-26 spec)
                          │ POST /mcp  (tools/call, tools/list)
┌─────────────────────────▼────────────────────────────────────┐
│  ZBrushMcpServer  (dcc-mcp-zbrush, port 8765)                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  dcc-mcp-core                                          │  │
│  │  ├── McpHttpServer (axum HTTP)                         │  │
│  │  ├── ActionRegistry + ActionDispatcher                 │  │
│  │  └── SkillCatalog (discovers zbrush-* skills)          │  │
│  └────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  ZBrushBridge  (httpx HTTP client)                     │  │
│  │  POST /api/zscript  GET /api/info  GET /api/tools      │  │
└──┴─────────────────────┬──────────────────────────────────┘──┘
                          │ HTTP REST (port 8080)
┌─────────────────────────▼────────────────────────────────────┐
│  ZBrush 2024+  (built-in HTTP server)                        │
│  ├── ZScript execution engine                                │
│  ├── ZTool / SubTool management                              │
│  └── Mesh export (OBJ, FBX, ZTL, STL)                       │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 DccCapabilities

```python
from dcc_mcp_core import DccCapabilities

caps = DccCapabilities.http_bridge("http://localhost:8080")
# Equivalent to:
caps = DccCapabilities(
    has_embedded_python=False,
    bridge_kind="http",
    bridge_endpoint="http://localhost:8080",
    scene_info=True,
    file_operations=True,
    snapshot=False,  # ZBrush render is complex; deferred to v0.3
)
```

### 3.3 Skill Execution Flow

```
Agent calls tools/call "zbrush_sculpt__list_tools"
    ↓
ActionDispatcher routes to registered handler
    ↓
Skill script: list_tools.py
    ↓
get_bridge() → ZBrushBridge instance
    ↓
bridge.list_tools() → GET http://localhost:8080/api/tools
    ↓
ZBrush returns JSON: [{"name": "ZSphere", ...}, ...]
    ↓
zb_success("Found 3 ZTool(s)", tools=[...]) → dict
    ↓
serialize_result(arm, SerializeFormat.Json) → JSON string
    ↓
MCP tools/call response → Agent
```

---

## 4. ZBrush HTTP API Reference (ZBrush 2024+)

### 4.1 Known Endpoints (from ZBrush 2024 documentation)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/info` | ZBrush version, status |
| POST | `/api/zscript` | Execute ZScript code |
| GET | `/api/tools` | List all ZTools |
| GET | `/api/tools/active` | Get active ZTool info |
| POST | `/api/export` | Export mesh to file |
| GET | `/api/subtools` | List SubTools of active ZTool |

### 4.2 Request/Response Format

```json
// POST /api/zscript
{
  "code": "[IButton,/Zplugin/SubTool Master/Bake All SubTools,Bake,]",
  "timeout": 30000
}

// Response
{
  "success": true,
  "output": "SubTools baked successfully",
  "error": null,
  "execution_time_ms": 1234
}
```

### 4.3 Authentication

Optional API key via `X-ZBrush-API-Key` header (configurable in ZBrush Preferences > Network).

---

## 5. Skill Catalog

### 5.1 Phase 1 (v0.2.0) — Core

#### `zbrush-sculpt`
| Tool | Description |
|------|-------------|
| `list_tools` | List all ZTools in ZBrush |
| `get_active_tool` | Get active ZTool name, polygon count, SubTool count |
| `execute_zscript` | Execute arbitrary ZScript code |
| `export_mesh` | Export active ZTool to OBJ/FBX/ZTL/STL |

### 5.2 Phase 2 (v0.3.0) — SubTools & Layers

#### `zbrush-subtool`
| Tool | Description |
|------|-------------|
| `list_subtools` | List all SubTools with visibility/polygon info |
| `set_active_subtool` | Switch active SubTool |
| `toggle_visibility` | Show/hide SubTool |
| `merge_visible` | Merge all visible SubTools |
| `duplicate_subtool` | Duplicate a SubTool |
| `delete_subtool` | Delete a SubTool |

#### `zbrush-morph`
| Tool | Description |
|------|-------------|
| `store_morph` | Store current mesh as morph target |
| `switch_morph` | Switch between mesh and morph target |
| `bake_morph` | Bake morph difference |
| `list_layers` | List ZBrush layer stack |

### 5.3 Phase 3 (v0.4.0) — Render & Export

#### `zbrush-render`
| Tool | Description |
|------|-------------|
| `bpr_render` | Trigger BPR render |
| `export_render` | Export render passes |
| `set_render_settings` | Configure BPR settings |

---

## 6. Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| HTTP timeout | 30s default, configurable |
| Connection retry | 3 attempts with exponential backoff |
| Startup time | < 2s for server init (bridge connect async) |
| Python version | 3.8+ |
| ZBrush version | 2024.0.1+ |
| Dependencies | `dcc-mcp-core>=0.12.14`, `httpx>=0.25.0` |

---

## 7. Error Handling

### Bridge Connection Errors
- ZBrush not running → `ZBrushNotAvailableError` → graceful start (warn, don't crash)
- ZBrush HTTP server disabled → same as above
- API key mismatch → 401 response → `ZBrushBridgeError` with clear message

### Skill Execution Errors
- `@with_zbrush` decorator catches all bridge/NotImplementedError/Exception
- Returns structured `zb_error()` dict (never throws to MCP layer)

---

## 8. Testing Strategy

### Unit Tests (no ZBrush required)
- Mock `httpx.Client` with `respx` for HTTP response simulation
- Test `ZBrushBridge.execute_zscript`, `list_tools`, `export_mesh`
- Test all `zb_success`/`zb_error`/`with_zbrush` helpers

### Integration Tests (requires ZBrush)
- Marked `@pytest.mark.e2e`
- Run only in ZBrush CI environment
- Verify real ZScript execution

---

## 9. Versioning & Release

Follows the same `release-please` workflow as `dcc-mcp-core` and `dcc-mcp-maya`.

| Version | Milestone |
|---------|-----------|
| 0.1.0 | Placeholder skeleton (current) |
| 0.2.0 | Working HTTP bridge + core sculpt skills |
| 0.3.0 | SubTool + morph layer management |
| 0.4.0 | Render + export pipeline |
| 1.0.0 | Stable API + full CI |

---

## 10. Open Questions

1. **ZBrush HTTP API documentation**: Is the API fully documented or reverse-engineered?
   → Need to validate endpoint names against actual ZBrush 2024 install.

2. **ZScript vs HTTP**: Some operations may only be possible via ZScript macros.
   → `execute_zscript` is the escape hatch for unsupported operations.

3. **ZBrush 2023 compatibility**: Does ZBrush 2023 have an HTTP server?
   → If yes, consider a fallback to GoZ TCP protocol.

4. **Concurrent requests**: Does the ZBrush HTTP server handle concurrent requests?
   → Add a request queue if single-threaded.
