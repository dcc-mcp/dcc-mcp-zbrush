---
name: zbrush-sculpt
description: "ZBrush sculpting tools — manage ZTools, SubTools, and mesh operations via HTTP bridge"
dcc: zbrush
version: "0.1.0"
tags: [zbrush, sculpt, ztool, subtool, mesh]
license: "MIT"
allowed-tools: ["Bash", "Read"]
depends: []
---

# zbrush-sculpt

ZBrush sculpting skill. Uses HTTP bridge to communicate with ZBrush 2024+ HTTP server.

## Scripts

- `list_tools` — List all ZTools in ZBrush
- `get_active_tool` — Get information about the currently active ZTool
- `export_mesh` — Export the active ZTool mesh to OBJ/FBX/ZTL
- `execute_zscript` — Execute a ZScript command in ZBrush
