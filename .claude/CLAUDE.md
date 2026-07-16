# CLAUDE.md — dcc-mcp-zbrush (Claude Code entry)

> Claude-specific entry point. Full navigation map: `AGENTS.md`.

## Quick facts

- **Target host:** ZBrush **2026.1+** with embedded Python SDK (`zbrush.commands (embedded Python SDK)`)
- **Primary mode:** sidecar MCP process + main-thread `bridge/plugin/mcp_socket_bridge.py`
- **Advanced mode:** pure-Python embedded experiments inside ZBrush
- **Do not assume:** ZBrush HTTP REST API — it does not exist in official docs
- **Do not assume:** Rust: only via dcc-mcp-core wheel; no Rust plugin inside ZBrush
- **Do not assume:** dcc-mcp-core _core.pyd is safe to import into the ZBrush embedded VM

## Cursor / Claude Desktop MCP config

```json
{
  "mcpServers": {
    "dcc-mcp-zbrush": {
      "url": "http://127.0.0.1:9765/mcp"
    }
  }
}
```

With gateway (multi-DCC):

```json
{
  "mcpServers": {
    "dcc-mcp-zbrush": {
      "url": "http://127.0.0.1:9765/mcp"
    }
  }
}
```

## Skills-first workflow

```
search_skills(query="subtool") → find skill
load_skill("zbrush-scene") → expand tools
call zbrush_scene__list_subtools
execute_python only when no typed skill fits
```

Default minimal loads zbrush-scripting, zbrush-scene.

## Key files

|Path|Role|
|---|---|
|src/dcc_mcp_zbrush/server.py|ZBrushMcpServer composition root|
|src/dcc_mcp_zbrush/_executor.py|In-process dispatcher for embedded VM|
|src/dcc_mcp_zbrush/bridge.py|Sidecar socket bridge client|
|bridge/plugin/mcp_socket_bridge.py|In-ZBrush main-thread TCP bridge installed as Python/init.py|
|bridge/plugin/dcc_mcp_zbrush/__init__.py|Auto-start embedded server|

## ZBrush Python VM constraints

- Persistent interpreter shared across scripts
- subprocess / multiprocessing invoking sys.executable are unsupported
- Python background threads do not run reliably after a startup script returns
- All zbrush.commands calls must run on the host main thread
- The socket bridge must pump zbc.update while polling with a short timeout
- Scene APIs must use affinity: main

## External docs

- ZBrush Python SDK: https://developers.maxon.net/docs/zbrush/py/2026_1_0/index.html
- Upstream core llms.txt: https://github.com/loonghao/dcc-mcp-core/blob/main/llms.txt
