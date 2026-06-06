# CLAUDE.md — dcc-mcp-zbrush (Claude Code entry)

> Claude-specific entry point. Full navigation map: `AGENTS.md`.

## Quick facts

- **Target host:** ZBrush **2026.1+** with embedded Python SDK (`zbrush.commands`)
- **Primary mode:** embedded Python inside ZBrush (`DCC_MCP_ZBRUSH_MODE=embedded`)
- **Fallback mode:** sidecar MCP process + `bridge/plugin/mcp_socket_bridge.py`
- **Do not assume:** ZBrush HTTP REST API — it does not exist in official docs
- **MCP endpoint:** `http://127.0.0.1:8765/mcp`

## Cursor / Claude Desktop MCP config

```json
{
  "mcpServers": {
    "zbrush": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

With gateway (multi-DCC):

```json
{
  "mcpServers": {
    "zbrush": {
      "url": "http://127.0.0.1:9765/mcp"
    }
  }
}
```

## Skills-first workflow

```
1. search_skills(query="subtool") → find skill
2. load_skill("zbrush-scene") → expand tools
3. call zbrush_scene__list_subtools
4. execute_python only when no typed skill fits
```

Default minimal loads `zbrush-scripting` + `zbrush-scene`.

## Key files

| Path | Role |
|------|------|
| `AGENTS.md` | Detailed agent navigation map |
| `src/dcc_mcp_zbrush/server.py` | `ZBrushMcpServer` composition root |
| `src/dcc_mcp_zbrush/_executor.py` | In-process dispatcher for embedded VM |
| `src/dcc_mcp_zbrush/bridge.py` | Sidecar socket bridge client |
| `bridge/plugin/mcp_socket_bridge.py` | In-ZBrush TCP plugin |

## ZBrush Python VM constraints

- Persistent interpreter shared across scripts
- `subprocess` / `multiprocessing` invoking `sys.executable` are unsupported
- Always register in-process executor in embedded mode
- Scene APIs must use `affinity: main`

## External docs

- SDK: https://developers.maxon.net/docs/zbrush/py/2026_1_0/index.html
- Upstream core: https://github.com/loonghao/dcc-mcp-core/blob/main/llms.txt
