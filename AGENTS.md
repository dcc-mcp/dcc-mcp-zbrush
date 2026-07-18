# AGENTS.md — dcc-mcp-zbrush

> Navigation map for AI agents. Detailed API lives in `README.md`.

## Agent Control Path

AI agent runtimes default to the shared gateway through the
`dcc-cli-gateway` skill and `dcc-mcp-cli` REST commands:

```bash
dcc-mcp-cli search --query "<task>" --dcc-type zbrush
dcc-mcp-cli describe <tool-slug>
dcc-mcp-cli call <tool-slug> --json '{"key":"value"}'
```

Use `dcc-mcp-cli list` for live instances and `dcc-mcp-cli dcc-types` for
release-catalog support. IDE users may continue to configure the gateway MCP
endpoint; adapter-local Python start APIs are for host bootstrap and tests.

## Quick facts

- **Target host:** ZBrush **2026.1+** with embedded Python SDK (`zbrush.commands (embedded Python SDK)`)
- **CLI default:** sidecar (`dcc-mcp-zbrush --mode sidecar`) via `bridge/plugin/mcp_socket_bridge.py`
- **Plugin default:** sidecar main-thread bridge inside ZBrush
- **Library auto-detect:** `resolve_mode()` detects ZBrush availability; falls back to sidecar
- **Do not assume:** ZBrush HTTP REST API — it does not exist in official docs
- **Do not assume:** Rust: only via dcc-mcp-core wheel; no Rust plugin inside ZBrush
- **Do not assume:** dcc-mcp-core _core.pyd is safe to import into the ZBrush embedded VM

## Skills-first workflow

```
search_skills(query="subtool") → find skill
load_skill("zbrush-scene") → expand tools
call zbrush_scene__list_subtools
execute_python only when no typed skill fits
```

Default minimal mode loads zbrush-scripting, zbrush-scene.

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

## Agent entry points

|File|Role|
|---|---|
|llms.txt|Minimal agent entry (modes, install, health check, failure modes)|
|.claude/CLAUDE.md|Claude-specific entry referencing AGENTS.md|

## External docs

- ZBrush Python SDK: https://developers.maxon.net/docs/zbrush/py/2026_1_0/index.html
- Upstream core llms.txt: https://github.com/dcc-mcp/dcc-mcp-core/blob/main/llms.txt
