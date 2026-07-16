# Development

Source-based workflow for contributors and early adopters.

## Prerequisites

- Python 3.9+
- ZBrush 2026.1+ (for embedded / integration tests)
- Git

## Setup

```bash
git clone https://github.com/loonghao/dcc-mcp-zbrush.git
cd dcc-mcp-zbrush

# Editable install with dev dependencies
pip install -e ".[dev]"
```

## Embedded mode from source

1. Ensure `src` is on ZBrush's `PYTHONPATH` or `ZBRUSH_PLUGIN_PATH`:

   ```bash
   set PYTHONPATH=%cd%\src;%PYTHONPATH%        # Windows cmd
   $env:PYTHONPATH = "$pwd\src;$env:PYTHONPATH" # Windows PowerShell
   export PYTHONPATH="$PWD/src:$PYTHONPATH"      # macOS / Linux
   ```

2. Copy the auto-start plugin to ZBrush startup:

   ```bash
   # Windows
   copy bridge\plugin\dcc_mcp_zbrush %ZBRUSH_USER_ASSETS_DIR%\dcc_mcp_zbrush
   copy bridge\plugin\dcc_mcp_zbrush_plugin.py %ZBRUSH_USER_ASSETS_DIR%\dcc_mcp_zbrush_plugin.py

   # macOS
   cp -R bridge/plugin/dcc_mcp_zbrush "$ZBRUSH_USER_ASSETS_DIR/"
   cp bridge/plugin/dcc_mcp_zbrush_plugin.py "$ZBRUSH_USER_ASSETS_DIR/"
   ```

3. Restart ZBrush. Use `dcc-mcp-cli list` to inspect the OS-assigned instance
   URL, or connect through `http://127.0.0.1:9765/mcp`.

Or start manually from ZBrush Python console:

```python
import dcc_mcp_zbrush
dcc_mcp_zbrush.start_server(mode="embedded")
```

## Sidecar mode from source

1. Copy the socket bridge plugin to ZBrush startup:

   ```bash
   # Windows
   copy bridge\plugin\mcp_socket_bridge.py %ZBRUSH_USER_ASSETS_DIR%\

   # macOS
   cp bridge/plugin/mcp_socket_bridge.py "$ZBRUSH_USER_ASSETS_DIR/"
   ```

2. Start ZBrush.

3. Run the MCP server outside ZBrush:

   ```bash
   dcc-mcp-zbrush --mode sidecar --socket-port 9876
   ```

## Plugin packaging

To build the plugin ZIP locally (what gets uploaded to GitHub Releases):

```bash
pip install -e ".[dev]"
python tools/pack_plugin.py --output dist/plugin
```

Output: `dist/plugin/dcc-mcp-zbrush-plugin-<version>.zip`

The ZIP contains:
- `embedded/dcc_mcp_zbrush/` — auto-start embedded plugin
- `sidecar/mcp_socket_bridge.py` — socket bridge for sidecar mode
- `install/install-windows.ps1` — Windows install script
- `install/install-macos.sh` — macOS install script

## Running tests

```bash
# Unit tests (no ZBrush required)
pytest tests/ -v --tb=short

# Lint
pip install ruff
ruff check src/ tests/ tools/
ruff format --check src/ tests/ tools/

# Validate skill definitions
python tools/lint_skills.py --error-only
```

## CI

The project uses GitHub Actions for CI (`.github/workflows/ci.yml`):

| Check | What it does |
|-------|-------------|
| `test` | Runs unit tests across Python 3.9–3.12 on Ubuntu, Windows, macOS |
| `lint` | Ruff check + format |
| `lint-skills` | Validates SKILL.md frontmatter |
| `validate-skills` | Validates skill package definitions |
| `build-zbrush-plugin` | Packs the plugin ZIP and uploads as artifact |

## Release

Releases are automated via `release-please` (`.github/workflows/release.yml`):

1. A merge to `main` triggers `release-please` to create a release PR.
2. Merging the release PR creates a GitHub Release with:
   - Wheel + sdist → PyPI
   - Plugin ZIP → Release assets
