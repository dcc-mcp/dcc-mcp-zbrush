#!/usr/bin/env python3
"""Pack ZBrush plugin files into an installable ZIP archive.

ZBrush 2026.1+ loads Python from its Asset Directory or ``ZBRUSH_PLUGIN_PATH``.
This script produces a versioned archive users can unzip and copy into that path.

Usage::

    python tools/pack_plugin.py
    python tools/pack_plugin.py --output dist/plugin --version 0.2.0

Output::

    dist/plugin/dcc-mcp-zbrush-plugin-0.2.0.zip
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
import zipfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = _SCRIPT_DIR.parent
PLUGIN_ROOT = PROJECT_ROOT / "bridge" / "plugin"
EMBEDDED_PLUGIN = PLUGIN_ROOT / "dcc_mcp_zbrush"
SIDECAR_PLUGIN = PLUGIN_ROOT / "mcp_socket_bridge.py"
AUTOSTART_PLUGIN = PLUGIN_ROOT / "dcc_mcp_zbrush_plugin.py"

EXCLUDE_PARTS = {"__pycache__", ".git", ".DS_Store"}


def _read_version(project_root: Path) -> str:
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
        if match:
            return match.group(1)
    version_file = project_root / "src" / "dcc_mcp_zbrush" / "__version__.py"
    if version_file.exists():
        match = re.search(r'__version__\s*=\s*"([^"]+)"', version_file.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    return "0.0.0"


def _install_readme(version: str) -> str:
    return textwrap.dedent(
        f"""\
        dcc-mcp-zbrush plugin {version}
        ================================

        Requires ZBrush 2026.1+ with the Python SDK and ``pip install dcc-mcp-zbrush``.

        Embedded mode (recommended)
        ---------------------------
        1. Copy ``embedded/dcc_mcp_zbrush`` and ``embedded/dcc_mcp_zbrush_plugin.py``
           directly into the ZBrush Asset Directory or a ``ZBRUSH_PLUGIN_PATH`` root.
        2. Ensure ``dcc-mcp-zbrush`` is on ZBrush ``PYTHONPATH``.
        3. Restart ZBrush. MCP endpoint: http://127.0.0.1:8765/mcp

        Sidecar socket bridge (optional)
        --------------------------------
        1. Copy ``sidecar/mcp_socket_bridge.py`` directly into the same plugin scan root.
        2. Run ``dcc-mcp-zbrush --mode sidecar`` outside ZBrush.

        Helper scripts in ``install/`` automate the copy step on Windows/macOS.
        """
    )


def _write_install_scripts(zf: zipfile.ZipFile) -> int:
    windows = textwrap.dedent(
        """\
        param(
          [ValidateSet("embedded", "sidecar")][string]$Mode = "embedded",
          [string]$Target = ""
        )
        $ErrorActionPreference = "Stop"
        $root = Split-Path -Parent $MyInvocation.MyCommand.Path
        if (-not $Target) {
          if ($env:ZBRUSH_USER_ASSETS_DIR) {
            $Target = $env:ZBRUSH_USER_ASSETS_DIR
          } elseif ($env:ZBRUSH_PLUGIN_PATH) {
            $Target = ($env:ZBRUSH_PLUGIN_PATH -split [IO.Path]::PathSeparator)[0]
          } else {
            throw "Pass -Target or export ZBRUSH_USER_ASSETS_DIR/ZBRUSH_PLUGIN_PATH"
          }
        }
        New-Item -ItemType Directory -Force -Path $Target | Out-Null
        if ($Mode -eq "embedded") {
          Copy-Item -Recurse -Force (Join-Path $root "..\\embedded\\dcc_mcp_zbrush") (Join-Path $Target "dcc_mcp_zbrush")
          Copy-Item -Force (Join-Path $root "..\\embedded\\dcc_mcp_zbrush_plugin.py") (Join-Path $Target "dcc_mcp_zbrush_plugin.py")
        } else {
          Copy-Item -Force (Join-Path $root "..\\sidecar\\mcp_socket_bridge.py") (Join-Path $Target "mcp_socket_bridge.py")
        }
        Write-Host "Installed dcc-mcp-zbrush $Mode plugin to $Target"
        """
    )
    macos = textwrap.dedent(
        """\
        #!/bin/sh
        set -eu
        MODE="${1:-embedded}"
        TARGET="${2:-${ZBRUSH_USER_ASSETS_DIR:-${ZBRUSH_PLUGIN_PATH%%:*}}}"
        [ -n "$TARGET" ] || { echo "Pass target or export ZBRUSH_USER_ASSETS_DIR/ZBRUSH_PLUGIN_PATH" >&2; exit 2; }
        ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
        mkdir -p "$TARGET"
        if [ "$MODE" = embedded ]; then
          rm -rf "$TARGET/dcc_mcp_zbrush"
          cp -R "$ROOT/embedded/dcc_mcp_zbrush" "$TARGET/"
          cp "$ROOT/embedded/dcc_mcp_zbrush_plugin.py" "$TARGET/"
        else
          cp "$ROOT/sidecar/mcp_socket_bridge.py" "$TARGET/"
        fi
        echo "Installed dcc-mcp-zbrush $MODE plugin to $TARGET"
        """
    )
    zf.writestr("install/install-windows.ps1", windows)
    info = zipfile.ZipInfo("install/install-macos.sh")
    info.external_attr = 0o755 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, macos)
    return 2


def pack_plugin(output_dir: Path, version: str) -> Path:
    if not EMBEDDED_PLUGIN.is_dir():
        print(f"ERROR: embedded plugin not found: {EMBEDDED_PLUGIN}", file=sys.stderr)
        sys.exit(1)
    if not SIDECAR_PLUGIN.is_file():
        print(f"ERROR: sidecar plugin not found: {SIDECAR_PLUGIN}", file=sys.stderr)
        sys.exit(1)
    if not AUTOSTART_PLUGIN.is_file():
        print(f"ERROR: autostart plugin not found: {AUTOSTART_PLUGIN}", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"dcc-mcp-zbrush-plugin-{version}.zip"
    file_count = 0

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README-INSTALL.txt", _install_readme(version))
        file_count += 1

        for file_path in sorted(EMBEDDED_PLUGIN.rglob("*")):
            if not file_path.is_file():
                continue
            if any(part in EXCLUDE_PARTS for part in file_path.parts):
                continue
            arcname = "embedded/dcc_mcp_zbrush/" + file_path.relative_to(EMBEDDED_PLUGIN).as_posix()
            zf.write(file_path, arcname)
            file_count += 1

        zf.write(AUTOSTART_PLUGIN, "embedded/dcc_mcp_zbrush_plugin.py")
        file_count += 1

        zf.write(SIDECAR_PLUGIN, "sidecar/mcp_socket_bridge.py")
        file_count += 1
        file_count += _write_install_scripts(zf)

    size_kb = output_path.stat().st_size / 1024
    print(f"Packed {file_count} entries -> {output_path} ({size_kb:.1f} KB)")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack the dcc-mcp-zbrush ZBrush plugin ZIP.")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dist" / "plugin",
        help="Output directory (default: dist/plugin/)",
    )
    parser.add_argument("--version", default=None, help="Plugin version (default: pyproject.toml)")
    args = parser.parse_args()
    version = args.version or _read_version(PROJECT_ROOT)
    pack_plugin(args.output, version)


if __name__ == "__main__":
    main()
