# dcc-mcp-zbrush

> ZBrush adapter for the DCC Model Context Protocol (MCP) ecosystem — bridges AI agents to ZBrush via HTTP REST

[![Status: Pre-Alpha](https://img.shields.io/badge/status-pre--alpha-orange)](https://github.com/loonghao/dcc-mcp-zbrush)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![dcc-mcp-core](https://img.shields.io/badge/dcc--mcp--core-%3E%3D0.12.14-purple)](https://github.com/loonghao/dcc-mcp-core)

---

## Overview

`dcc-mcp-zbrush` connects [Claude](https://claude.ai), [Cursor](https://cursor.sh), and other MCP-compatible AI agents to **ZBrush 2024+** using the Model Context Protocol.

Unlike Maya or Blender, ZBrush does not have an embedded Python interpreter. This package uses an **HTTP Bridge** architecture: it connects to ZBrush's built-in HTTP REST server (available in ZBrush 2024+) to execute ZScript commands and retrieve sculpting data.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AI Agent (Claude / Cursor)                    │
└────────────────────────────┬────────────────────────────────────┘
                             │ MCP Streamable HTTP (port 8765)
┌────────────────────────────▼────────────────────────────────────┐
│              ZBrushMcpServer  (this package)                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  dcc-mcp-core  (ActionRegistry + SkillCatalog)           │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  ZBrushBridge  (httpx HTTP client)                       │   │
└──┴─────────────────────────┬────────────────────────────────┘───┘
                             │ HTTP REST (port 8080)
┌────────────────────────────▼────────────────────────────────────┐
│              ZBrush 2024+  (built-in HTTP server)                │
│              ZScript execution engine                            │
└─────────────────────────────────────────────────────────────────┘
```

**DccCapabilities flags for ZBrush:**
```python
has_embedded_python = False
bridge_kind = "http"
bridge_endpoint = "http://localhost:8080"
```

---

## Features

### Current (v0.1.0 — Placeholder)
- [x] Project structure and package skeleton
- [x] `ZBrushBridge` HTTP client (placeholder — `NotImplementedError` stubs)
- [x] `ZBrushMcpServer` with lifecycle management (`start` / `stop`)
- [x] Skill authoring helpers: `zb_success`, `zb_error`, `zb_from_exception`, `with_zbrush`
- [x] `zbrush-sculpt` skill with `list_tools` script (placeholder)
- [x] `DccCapabilities.http_bridge()` factory (in dcc-mcp-core)
- [x] Entry-point registration: `dcc_mcp.adapters` → `zbrush`

### Planned
- [ ] `ZBrushBridge.execute_zscript()` — POST to `/api/zscript`
- [ ] `ZBrushBridge.get_info()` — GET `/api/info`
- [ ] `ZBrushBridge.list_tools()` — GET `/api/tools`
- [ ] `ZBrushBridge.export_mesh()` — POST `/api/export`
- [ ] `zbrush-sculpt` skill: `get_active_tool`, `export_mesh`, `execute_zscript`, `bake_all_subtools`
- [ ] `zbrush-subtool` skill: SubTool management (add, delete, merge, visibility)
- [ ] `zbrush-morph` skill: Morph target / layer operations
- [ ] `zbrush-render` skill: BPR render, export
- [ ] Connection health monitoring and auto-reconnect
- [ ] ZBrush 2023 compatibility layer (ZBrushCentral API)

---

## Requirements

- **ZBrush 2024.0.1+** with HTTP Server enabled
- Python **3.8+**
- `dcc-mcp-core >= 0.12.14`
- `httpx >= 0.25.0`

---

## Enable ZBrush HTTP Server

Before using this package, enable ZBrush's built-in HTTP server:

1. Open ZBrush 2024
2. Go to **Preferences** > **Network**
3. Enable **"HTTP Server"**
4. Set the port (default: **8080**)
5. Optionally set an API key for security
6. Restart ZBrush

Verify it's running:
```bash
curl http://localhost:8080/api/info
```

---

## Quick Start

```python
import dcc_mcp_zbrush

# Start the MCP server (connects to ZBrush on port 8080)
handle = dcc_mcp_zbrush.start_server(
    port=8765,        # MCP HTTP port (AI agents connect here)
    zbrush_port=8080, # ZBrush HTTP server port
)

print(f"MCP server at {handle.mcp_url()}")
# → http://127.0.0.1:8765/mcp

# Stop the server
handle.shutdown()
```

**With API key:**
```python
handle = dcc_mcp_zbrush.start_server(
    port=8765,
    zbrush_port=8080,
    api_key="your-zbrush-api-key",
)
```

---

## Skill Authoring Guide

ZBrush skill scripts differ from Maya/Blender skills in one key way: **there is no `import zbrush` module**. Instead, use the `ZBrushBridge` to execute ZScript commands via HTTP.

### Basic script pattern

```python
"""List all ZTools in ZBrush."""
from __future__ import annotations

from dcc_mcp_core.skill import skill_entry
from dcc_mcp_zbrush.api import get_bridge, with_zbrush, zb_success


@skill_entry
@with_zbrush
def list_tools(**kwargs) -> dict:
    bridge = get_bridge()
    tools = bridge.list_tools()
    return zb_success(
        f"Found {len(tools)} ZTool(s)",
        prompt="Use get_active_tool to inspect the active tool.",
        count=len(tools),
        tools=tools,
    )


def main(**kwargs) -> dict:
    return list_tools(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main
    run_main(main)
```

### Using ZScript directly

```python
from dcc_mcp_zbrush.api import get_bridge, with_zbrush, zb_success


@with_zbrush
def bake_layers(**kwargs) -> dict:
    bridge = get_bridge()
    result = bridge.execute_zscript(
        "[IButton, /Zplugin/SubTool Master/Bake All SubTools, Bake,]"
    )
    return zb_success("SubTools baked", output=result.get("output"))
```

### Result helpers

| Function | Description |
|----------|-------------|
| `zb_success(msg, **ctx)` | Build a success result dict |
| `zb_error(msg, error, **ctx)` | Build a failure result dict |
| `zb_from_exception(exc, ...)` | Build failure dict from exception |
| `get_bridge()` | Get the active `ZBrushBridge` instance |
| `@with_zbrush` | Decorator: auto-catch bridge/connection errors |

---

## SKILL.md Format

```yaml
---
name: zbrush-sculpt
description: "ZBrush sculpting tools"
dcc: zbrush
version: "0.1.0"
tags: [zbrush, sculpt, ztool]
license: "MIT"
depends: []
---
```

---

## Project Structure

```
dcc-mcp-zbrush/
├── src/dcc_mcp_zbrush/
│   ├── __init__.py          # Public API
│   ├── __version__.py       # Version string
│   ├── api.py               # zb_success, zb_error, get_bridge, with_zbrush
│   ├── bridge.py            # ZBrushBridge (HTTP client)
│   ├── server.py            # ZBrushMcpServer + start_server/stop_server
│   └── skills/
│       └── zbrush-sculpt/
│           ├── SKILL.md
│           └── scripts/
│               └── list_tools.py
├── tests/
│   └── test_server.py
├── pyproject.toml
└── README.md
```

---

## Comparison: Embedded vs Bridge Mode

| | Maya / Blender / Unreal | ZBrush / Photoshop |
|-|------------------------|-------------------|
| Python in DCC | ✅ Yes | ❌ No |
| Script execution | `import maya.cmds` | `bridge.execute_zscript()` |
| Bridge needed | No | Yes |
| `has_embedded_python` | `True` | `False` |
| `bridge_kind` | `None` | `"http"` |
| `dcc_mcp_core` version | ≥ 0.12.12 | ≥ 0.12.14 |

---

## Roadmap

### v0.1.0 — Placeholder (current)
- Package skeleton, bridge stubs, skill authoring helpers

### v0.2.0 — HTTP Bridge Implementation
- Implement `ZBrushBridge.execute_zscript`, `get_info`, `list_tools`, `export_mesh`
- Discover ZBrush API endpoints from ZBrush 2024 documentation

### v0.3.0 — Core Skills
- `zbrush-sculpt`: list/get tools, export mesh, execute ZScript
- `zbrush-subtool`: SubTool CRUD, visibility, merge
- CI/CD with mocked ZBrush HTTP server

### v1.0.0 — Stable
- Full ZTool and SubTool management
- Morph target operations
- BPR render export
- ZBrush 2023 backward compatibility

---

## Contributing

This project is in pre-alpha. Contributions are welcome:

1. Fork the repository
2. Implement a `ZBrushBridge` method (see `bridge.py` stubs)
3. Add a corresponding skill script
4. Add tests with mocked HTTP responses (see `respx` fixtures)
5. Submit a PR

---

## License

MIT — see [LICENSE](LICENSE)

---

## Related Projects

- [dcc-mcp-core](https://github.com/loonghao/dcc-mcp-core) — Core MCP framework
- [dcc-mcp-maya](https://github.com/loonghao/dcc-mcp-maya) — Maya adapter (reference implementation)
- [dcc-mcp-photoshop](https://github.com/loonghao/dcc-mcp-photoshop) — Photoshop adapter (WebSocket bridge)
- [dcc-mcp-unreal](https://github.com/loonghao/dcc-mcp-unreal) — Unreal Engine adapter
