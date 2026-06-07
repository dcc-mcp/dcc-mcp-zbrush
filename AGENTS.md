# AGENTS.md — dcc-mcp-zbrush

> Navigation map for AI agents. Detailed API lives in `README.md`.

## Quick facts

- **Target host:** ZBrush **2026.1+** with embedded Python SDK (`zbrush.commands`)
- **CLI default:** sidecar (`dcc-mcp-zbrush --mode sidecar`) via `bridge/plugin/mcp_socket_bridge.py`
- **Plugin default:** embedded Python inside ZBrush (`DCC_MCP_ZBRUSH_MODE=embedded`)
- **Library auto-detect:** `resolve_mode()` detects ZBrush availability; falls back to sidecar
- **Do not assume:** ZBrush HTTP REST API — it does not exist in official docs
- **Rust:** only via `dcc-mcp-core` wheel; no Rust plugin inside ZBrush

## Skills-first workflow

```
1. search_skills(query="subtool") → find skill
2. load_skill("zbrush-scene") → expand tools
3. call zbrush_scene__list_subtools
4. execute_python only when no typed skill fits
```

Default minimal mode loads `zbrush-scripting` + `zbrush-scene`.

## Key files

| Path | Role |
|------|------|
| `src/dcc_mcp_zbrush/server.py` | `ZBrushMcpServer` composition root |
| `src/dcc_mcp_zbrush/_executor.py` | In-process dispatcher for embedded VM |
| `src/dcc_mcp_zbrush/bridge.py` | Sidecar socket bridge client |
| `bridge/plugin/mcp_socket_bridge.py` | In-ZBrush TCP plugin |
| `bridge/plugin/dcc_mcp_zbrush/__init__.py` | Auto-start embedded server |

## ZBrush Python VM constraints

- Persistent interpreter shared across scripts
- `subprocess` / `multiprocessing` invoking `sys.executable` are unsupported
- Always register in-process executor in embedded mode
- Scene APIs must use `affinity: main`

## Agent entry points

| File | Role |
|------|------|
| `llms.txt` | Minimal agent entry (modes, install, health check, failure modes) |
| `.claude/CLAUDE.md` | Claude-specific entry referencing AGENTS.md |

## External docs

- SDK: https://developers.maxon.net/docs/zbrush/py/2026_1_0/index.html
- Upstream core: https://github.com/loonghao/dcc-mcp-core/blob/main/llms.txt
