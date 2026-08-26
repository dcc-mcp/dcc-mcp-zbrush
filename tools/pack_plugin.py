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

        Requires ZBrush 2026.1+ and Python 3.10+ for the external sidecar.
        The receipt-driven installer preserves the shared Python/init.py file;
        do not copy payload files over that file manually.

        Canonical instructions:
        https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-zbrush/main/install.md

        Helper scripts in ``install/`` only delegate to the standard lifecycle
        command. They do not implement a second extraction path.
        """
    )


def _write_install_scripts(zf: zipfile.ZipFile, version: str) -> int:
    windows = textwrap.dedent(
        f"""\
        param(
          [ValidateSet("embedded", "sidecar")][string]$Mode = "sidecar",
          [Parameter(Mandatory=$true)][string]$DccPath,
          [Parameter(Mandatory=$true)][string]$Target,
          [string]$Python = "python"
        )
        $ErrorActionPreference = "Stop"
        # dcc-mcp-zbrush install (standard receipt-driven lifecycle)
        & $Python -m pip install "dcc-mcp-zbrush=={version}"
        & dcc-mcp-zbrush install --mode $Mode --version "{version}" --dcc-path $DccPath --python $Python --asset-dir $Target --yes
        """
    )
    macos = textwrap.dedent(
        f"""\
        #!/bin/sh
        set -eu
        MODE="${{1:-sidecar}}"
        DCC_PATH="${{2:?Pass the ZBrush application path as argument 2}}"
        TARGET="${{3:?Pass the ZBrush Asset Directory as argument 3}}"
        PYTHON="${{PYTHON:-python3}}"
        # dcc-mcp-zbrush install (standard receipt-driven lifecycle)
        "$PYTHON" -m pip install "dcc-mcp-zbrush=={version}"
        dcc-mcp-zbrush install --mode "$MODE" --version "{version}" --dcc-path "$DCC_PATH" --python "$PYTHON" --asset-dir "$TARGET" --yes
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
        file_count += _write_install_scripts(zf, version)

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
