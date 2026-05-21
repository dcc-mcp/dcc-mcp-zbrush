# dcc-mcp-zbrush

[![PyPI](https://img.shields.io/pypi/v/dcc-mcp-zbrush)](https://pypi.org/project/dcc-mcp-zbrush/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: Pre-Alpha](https://img.shields.io/badge/status-pre--alpha-orange)](https://github.com/loonghao/dcc-mcp-zbrush)

ZBrush adapter for the [DCC Model Context Protocol](https://github.com/loonghao/dcc-mcp-core) ecosystem.

> **Requires ZBrush 2026.1+** with the official [Python SDK](https://developers.maxon.net/docs/zbrush/py/2026_1_0/index.html) (CPython 3.11 embedded in ZBrush).

## Architecture decision

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
- Bundled skills: `zbrush-scripting`, `zbrush-scene`
- In-process executor for ZBrush's embedded Python VM
- Optional socket bridge plugin for sidecar deployments
- Gateway election compatible with `dcc-mcp-core`

## Requirements

- ZBrush **2026.1+**
- Python **3.9+** on the sidecar host (ZBrush itself ships 3.11)
- `dcc-mcp-core >= 0.17.2`

## Installation

### Embedded mode (inside ZBrush)

1. Install the package into a directory on `PYTHONPATH` or `ZBRUSH_PLUGIN_PATH`:

```bash
pip install dcc-mcp-zbrush -t /path/to/zbrush/python/libs
```

2. Copy the auto-start plugin:

```bash
# Windows
copy bridge\plugin\dcc_mcp_zbrush %USERPROFILE%\Documents\ZBrushData\ZStartup\ZPlugs64\dcc_mcp_zbrush

# macOS
cp -R bridge/plugin/dcc_mcp_zbrush ~/Library/Application\ Support/ZBrush/ZStartup/ZPlugs64/
```

3. Restart ZBrush. MCP endpoint: `http://127.0.0.1:8765/mcp`

Or from ZBrush Python console:

```python
import dcc_mcp_zbrush
dcc_mcp_zbrush.start_server(mode="embedded")
```

### Sidecar mode (external process)

1. Install the socket bridge plugin in ZBrush (`bridge/plugin/mcp_socket_bridge.py`).
2. Start ZBrush.
3. Run the MCP server outside ZBrush:

```bash
dcc-mcp-zbrush --mode sidecar --port 8765 --socket-port 9876
```

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

## Cursor / Claude MCP config

```json
{
  "mcpServers": {
    "zbrush": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

With gateway:

```json
{
  "mcpServers": {
    "zbrush": {
      "url": "http://127.0.0.1:9765/mcp"
    }
  }
}
```

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

## References

- [ZBrush Python SDK 2026.1](https://developers.maxon.net/docs/zbrush/py/2026_1_0/index.html)
- [ZBrush Python environment](https://developers.maxon.net/docs/zbrush/py/2026_1_0/manuals/python_environment.html)
- [GoZ SDK (mesh exchange only)](https://developers.maxon.net/docs/zbrush/goz_sdk.pdf)
- Community reference: [newsbubbles/zbrush-mcp](https://github.com/newsbubbles/zbrush-mcp) (socket bridge pattern)

## License

MIT
