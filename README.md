# dcc-mcp-zbrush

[![PyPI](https://img.shields.io/pypi/v/dcc-mcp-zbrush)](https://pypi.org/project/dcc-mcp-zbrush/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: Pre-Alpha](https://img.shields.io/badge/status-pre--alpha-orange)](https://github.com/loonghao/dcc-mcp-zbrush)

ZBrush adapter for the [DCC Model Context Protocol](https://github.com/loonghao/dcc-mcp-core) ecosystem.

> **Requires ZBrush 2026.1+** with the official [Python SDK](https://developers.maxon.net/docs/zbrush/py/2026_1_0/index.html) (CPython 3.11 embedded in ZBrush).

## Quick install

### 1. Install the Python package

```bash
pip install dcc-mcp-zbrush
```

This installs the `dcc-mcp-zbrush` Python package — the MCP HTTP server that bridges AI agents to ZBrush. It does **not** include ZBrush plugin files (see step 2).

### 2. Install the ZBrush plugin

The plugin files (auto-start script, socket bridge) are distributed separately. Download the plugin ZIP from the [latest GitHub Release](https://github.com/loonghao/dcc-mcp-zbrush/releases/latest):

```
dcc-mcp-zbrush-plugin-<version>.zip
```

Then run the install script inside the ZIP:

**Windows** (PowerShell):
```powershell
.\install\install-windows.ps1
```

**macOS** (Terminal):
```bash
chmod +x install/install-macos.sh && ./install/install-macos.sh
```

This copies the plugin into your ZBrush `ZStartup/ZPlugs64` directory so it loads automatically on startup.

### 3. Restart ZBrush

Launch or restart ZBrush. The plugin auto-starts the MCP server in embedded mode.

### 4. Health check

Verify the server is running:

```bash
curl http://127.0.0.1:8765/mcp
```

### 5. Configure your AI client

Add the MCP server to your AI client config (Cursor, Claude Desktop, etc.):

```json
{
  "mcpServers": {
    "zbrush": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

---

## How it works

ZBrush **does not ship a built-in HTTP REST server**. The pre-alpha scaffold that assumed `Preferences > Network > Enable HTTP Server` was incorrect.

The supported integration paths are:

| Mode | When to use | Stack |
|------|-------------|-------|
| **Embedded (recommended)** | ZBrush 2026.1+ with Python SDK | Python plugin inside ZBrush → `dcc-mcp-core` MCP HTTP server → `zbrush.commands` |
| **Sidecar + socket plugin** | External MCP process / restricted installs | External Python → TCP :9876 → `bridge/plugin/mcp_socket_bridge.py` inside ZBrush |

Rust is **not** used inside ZBrush. Like Maya/Houdini, Rust lives in the **`dcc-mcp-core` wheel** (PyO3) that powers the MCP HTTP server. The ZBrush-facing code is **Python only**.

GoZ C++ SDK is for **mesh exchange between DCC apps**, not general MCP automation — we do not build the primary adapter on GoZ.

```
Embedded mode (default inside ZBrush):

AI Agent → MCP HTTP :8765 → ZBrushMcpServer (inside ZBrush) → zbrush.commands

Sidecar mode:

AI Agent → MCP HTTP :8765 → ZBrushMcpServer (external Python)
         → TCP :9876 → mcp_socket_bridge.py (inside ZBrush) → zbrush.commands
```

## Features (v0.2.0)

- `DccServerBase` adapter with progressive skill loading
- Bundled skills: `zbrush-scripting`, `zbrush-scene`, `zbrush-subtool`, `zbrush-interchange`
- In-process executor for ZBrush's embedded Python VM
- Optional socket bridge plugin for sidecar deployments
- Gateway election compatible with `dcc-mcp-core`

## Requirements

- ZBrush **2026.1+**
- Python **3.9+** on the sidecar host (ZBrush itself ships 3.11)
- `dcc-mcp-core >= 0.18.7`

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DCC_MCP_ZBRUSH_PORT` | `8765` | MCP HTTP port |
| `DCC_MCP_ZBRUSH_MODE` | auto | `embedded` or `sidecar` |
| `DCC_MCP_ZBRUSH_AUTOSTART` | `1` | Auto-start embedded server from plugin |
| `DCC_MCP_ZBRUSH_SOCKET_PORT` | `9876` | Socket bridge port (sidecar) |
| `DCC_MCP_GATEWAY_PORT` | `9765` | Gateway election port |
| `DCC_MCP_MINIMAL` | `1` | Progressive skill loading |

## Bundled skills

| Skill | Tools |
|-------|-------|
| `zbrush-scripting` | `execute_python`, `get_session_info` |
| `zbrush-scene` | `get_scene_info`, `list_subtools` |
| `zbrush-subtool` | `select_subtool`, `get_subtool_status` |
| `zbrush-interchange` | `export_active_subtool_obj` |

## Path concepts

- **PYTHONPATH** — where Python looks for packages (`pip install` handles this)
- **ZBRUSH_PLUGIN_PATH** / **ZStartup/ZPlugs64** — where ZBrush loads auto-start plugins

`pip install dcc-mcp-zbrush` puts the Python package on `PYTHONPATH`.  
The plugin ZIP goes into `ZBRUSH_PLUGIN_PATH` (handled by the install scripts above).

## Skill authoring

Skills lazy-import `zbrush.commands` and run on the main thread (`affinity: main`).

```python
from dcc_mcp_core.skill import skill_entry
from dcc_mcp_zbrush.api import import_zbc, with_zbrush, zb_success

@skill_entry
@with_zbrush
def my_tool(**kwargs) -> dict:
    zbc = import_zbc()
    count = zbc.get_subtool_count()
    return zb_success(f"{count} subtool(s)", count=count)
```

## Sidecar mode (optional)

If you cannot install the embedded plugin or need the MCP server to run outside ZBrush:

1. The plugin ZIP already includes `sidecar/mcp_socket_bridge.py` — copy it to `ZPlugs64` (the install scripts handle this).
2. Start ZBrush.
3. Run the MCP server outside ZBrush:

```bash
dcc-mcp-zbrush --mode sidecar --port 8765 --socket-port 9876
```

## Development

See [docs/development.md](docs/development.md) for source-based setup, testing, and contribution workflow.

## References

- [ZBrush Python SDK 2026.1](https://developers.maxon.net/docs/zbrush/py/2026_1_0/index.html)
- [ZBrush Python environment](https://developers.maxon.net/docs/zbrush/py/2026_1_0/manuals/python_environment.html)
- [GoZ SDK (mesh exchange only)](https://developers.maxon.net/docs/zbrush/goz_sdk.pdf)
- Community reference: [newsbubbles/zbrush-mcp](https://github.com/newsbubbles/zbrush-mcp) (socket bridge pattern)

## License

MIT
