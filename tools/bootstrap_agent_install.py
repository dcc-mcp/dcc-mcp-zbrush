#!/usr/bin/env python3
"""One-command bootstrap installer for dcc-mcp-zbrush.

Turns "user says one sentence to Agent" into a deterministic, limited-step
invocation — no path guessing, no directory spelunking.

Usage::

    # Embedded mode (recommended) — MCP server runs inside ZBrush
    python tools/bootstrap_agent_install.py --mode embedded

    # Sidecar mode — MCP server runs outside ZBrush via socket bridge
    python tools/bootstrap_agent_install.py --mode sidecar

    # Preview without making changes
    python tools/bootstrap_agent_install.py --mode embedded --dry-run

    # Pin a specific release version
    python tools/bootstrap_agent_install.py --mode embedded --version 0.2.7

    # Target both Cursor and Claude Desktop config
    python tools/bootstrap_agent_install.py --mode embedded --mcp-target both

Steps (each skippable via --skip-*)::

    1. pip install dcc-mcp-zbrush==<version>
    2. Download plugin ZIP from GitHub Releases + SHA256 verify
    3. Extract to ZStartup/ZPlugs64 (mode-aware: embedded → plugin dir, sidecar → socket bridge)
    4. Write MCP config (Cursor / Claude Desktop)
    5. Print health check instructions
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_REPO = "loonghao/dcc-mcp-zbrush"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"
RELEASES_URL = f"{GITHUB_API}/releases"
MCP_PORT = 8765
GATEWAY_PORT = 9765

MCP_CONFIG_TEMPLATE = {
    "mcpServers": {
        "zbrush": {
            "url": f"http://127.0.0.1:{MCP_PORT}/mcp",
        }
    }
}

MCP_CONFIG_GATEWAY_TEMPLATE = {
    "mcpServers": {
        "zbrush": {
            "url": f"http://127.0.0.1:{GATEWAY_PORT}/mcp",
        }
    }
}


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def _get_zbrush_plugin_dir() -> Path:
    """Return the default ZBrush plugin directory for the current platform.

    Returns:
        Path to ``ZStartup/ZPlugs64`` under the ZBrush user data directory.
    """
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("USERPROFILE", str(Path.home())))
        return base / "Documents" / "ZBrushData" / "ZStartup" / "ZPlugs64"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "ZBrush" / "ZStartup" / "ZPlugs64"
    else:
        # Linux — not officially supported, but provide a reasonable guess
        return Path.home() / ".local" / "share" / "ZBrush" / "ZStartup" / "ZPlugs64"


def _get_cursor_config_path() -> Path:
    """Return the Cursor MCP config path for the current platform."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Cursor" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    else:
        return Path.home() / ".config" / "Cursor" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"


def _get_claude_config_path() -> Path:
    """Return the Claude Desktop MCP config path for the current platform."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Claude" / "claude_desktop_config.json"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def _parse_version(version_str: str) -> Tuple[int, ...]:
    """Parse a version string like '0.2.7' into a sortable tuple."""
    # Strip leading 'v' if present
    clean = version_str.lstrip("v")
    parts = []
    for part in clean.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            # Non-numeric segment — treat as 0
            parts.append(0)
    return tuple(parts)


def _get_latest_version() -> str:
    """Fetch the latest release version from GitHub Releases API."""
    url = f"{RELEASES_URL}/latest"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "dcc-mcp-zbrush-bootstrap/1.0")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("tag_name", "").lstrip("v")
    except Exception:
        return "0.0.0"


# ---------------------------------------------------------------------------
# Step 1: Install wheel
# ---------------------------------------------------------------------------


def install_wheel(version: str, dry_run: bool = False) -> None:
    """``pip install dcc-mcp-zbrush==<version>``."""
    pkg = f"dcc-mcp-zbrush=={version}"
    cmd = [sys.executable, "-m", "pip", "install", pkg]
    if dry_run:
        print(f"[DRY RUN] Would run: {' '.join(cmd)}")
        return
    print(f"[1/5] Installing {pkg} ...")
    subprocess.check_call(cmd)
    print(f"       ✓ {pkg} installed")


# ---------------------------------------------------------------------------
# Step 2: Download + verify plugin ZIP
# ---------------------------------------------------------------------------


def _find_asset(release: dict, name_pattern: str) -> Optional[dict]:
    """Find an asset in a GitHub release by name regex."""
    for asset in release.get("assets", []):
        if re.search(name_pattern, asset.get("name", "")):
            return asset
    return None


def download_plugin_zip(version: str, output_dir: Path, dry_run: bool = False) -> Path:
    """Download the plugin ZIP from GitHub Releases and verify SHA256.

    Returns:
        Path to the downloaded ZIP file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"dcc-mcp-zbrush-plugin-{version}.zip"
    zip_path = output_dir / zip_name

    # Fetch release info
    tag = f"v{version}"
    url = f"{RELEASES_URL}/tags/{tag}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "dcc-mcp-zbrush-bootstrap/1.0")

    if dry_run:
        print(f"[DRY RUN] Would download {GITHUB_REPO} release {tag}")
        print(f"[DRY RUN]   → {zip_path}")
        # Validate the API is reachable
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                release = json.loads(resp.read().decode("utf-8"))
                asset = _find_asset(release, r"dcc-mcp-zbrush-plugin.*\.zip$")
                if asset:
                    print(f"[DRY RUN]   Found asset: {asset['name']} ({asset['size']} bytes)")
                else:
                    print(f"[DRY RUN]   ⚠ No plugin ZIP asset found in release {tag}")
        except Exception as exc:
            print(f"[DRY RUN]   ⚠ Could not fetch release info: {exc}")
        return zip_path

    print(f"[2/5] Downloading {GITHUB_REPO} release {tag} ...")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            release = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"       ✗ Failed to fetch release info: {exc}", file=sys.stderr)
        sys.exit(1)

    # Find the plugin ZIP asset
    asset = _find_asset(release, r"dcc-mcp-zbrush-plugin.*\.zip$")
    if not asset:
        print(f"       ✗ No plugin ZIP asset found in release {tag}", file=sys.stderr)
        sys.exit(1)

    download_url = asset["browser_download_url"]

    # Download
    print(f"       Downloading {asset['name']} ({asset['size']} bytes) ...")
    urllib.request.urlretrieve(download_url, zip_path)

    actual_size = zip_path.stat().st_size
    print(f"       ✓ Downloaded {zip_path.name} ({actual_size} bytes)")

    # SHA256 verification (if checksum file is available)
    checksum_asset = _find_asset(release, r"SHA256SUMS")
    if checksum_asset:
        try:
            checksum_url = checksum_asset["browser_download_url"]
            with urllib.request.urlopen(checksum_url, timeout=15) as resp:
                checksums = resp.read().decode("utf-8")

            sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
            expected_line = None
            for line in checksums.splitlines():
                if asset["name"] in line:
                    expected_line = line.strip()
                    expected_hash = line.split()[0]
                    if sha256 == expected_hash:
                        print(f"       ✓ SHA256 verified: {sha256[:16]}...")
                    else:
                        print("       ✗ SHA256 mismatch!", file=sys.stderr)
                        print(f"         Expected: {expected_hash}", file=sys.stderr)
                        print(f"         Got:      {sha256}", file=sys.stderr)
                        sys.exit(1)
                    break
            if expected_line is None:
                print(f"       ⚠ No SHA256 entry for {asset['name']} in checksum file — skipping verification")
        except Exception:
            print("       ⚠ Could not fetch checksum file — skipping verification")
    else:
        print("       ⚠ No SHA256SUMS asset — skipping verification")

    return zip_path


# ---------------------------------------------------------------------------
# Step 3: Extract plugin
# ---------------------------------------------------------------------------


def extract_plugin(
    zip_path: Path,
    plugin_dir: Path,
    mode: str,
    dry_run: bool = False,
) -> None:
    """Extract plugin ZIP into the ZBrush plugin directory.

    Args:
        zip_path: Path to the downloaded plugin ZIP.
        plugin_dir: Target ZStartup/ZPlugs64 directory.
        mode: ``embedded`` or ``sidecar``.
        dry_run: If True, preview only.
    """
    if dry_run:
        print(f"[DRY RUN] Would extract {zip_path.name}")
        print(f"[DRY RUN]   Mode: {mode}")
        print(f"[DRY RUN]   Target: {plugin_dir}")
        if zip_path.exists():
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in sorted(zf.namelist()):
                    # Show only mode-relevant files
                    if mode == "embedded" and name.startswith("embedded/"):
                        print(f"[DRY RUN]   → {name}")
                    elif mode == "embedded" and name.startswith("sidecar/"):
                        pass  # Skip sidecar in embedded mode
                    elif name.startswith("install/") or name.startswith("README"):
                        print(f"[DRY RUN]   → {name}")
                    elif mode == "sidecar" and name.startswith("sidecar/"):
                        print(f"[DRY RUN]   → {name}")
        else:
            print("[DRY RUN]   (ZIP not downloaded yet — cannot list contents)")
        return

    print(f"[3/5] Extracting {zip_path.name} → {plugin_dir} (mode={mode}) ...")
    plugin_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        all_names = zf.namelist()
        for name in all_names:
            # In embedded mode, skip sidecar files; in sidecar mode, include both
            if mode == "embedded" and name.startswith("sidecar/"):
                continue

            member = zf.getinfo(name)
            target_path = plugin_dir / name

            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                # Preserve executable bits from ZIP external_attr
                if member.external_attr & (0o755 << 16):
                    target_path.chmod(target_path.stat().st_mode | 0o111)

    # Count extracted files
    extracted = 0
    for name in all_names:
        if mode == "embedded" and name.startswith("sidecar/"):
            continue
        member = zf.getinfo(name)
        if not member.is_dir():
            extracted += 1

    print(f"       ✓ Extracted {extracted} files to {plugin_dir}")


# ---------------------------------------------------------------------------
# Step 4: Write MCP config
# ---------------------------------------------------------------------------


def _load_or_empty(path: Path) -> dict:
    """Load a JSON file or return empty dict if not found."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _merge_mcp_config(existing: dict, server_name: str, server_config: dict) -> dict:
    """Merge MCP server config into existing config dict.

    Preserves existing servers; overwrites the named server entry.
    """
    merged = dict(existing)
    if "mcpServers" not in merged:
        merged["mcpServers"] = {}
    merged["mcpServers"][server_name] = server_config
    return merged


def write_mcp_config(
    target: str,
    dry_run: bool = False,
) -> None:
    """Write MCP server config for the given target(s).

    Args:
        target: ``cursor``, ``claude``, or ``both``.
        dry_run: If True, preview only.
    """
    targets = []
    if target in ("cursor", "both"):
        targets.append(("Cursor", _get_cursor_config_path()))
    if target in ("claude", "both"):
        targets.append(("Claude Desktop", _get_claude_config_path()))

    server_config = {"url": f"http://127.0.0.1:{MCP_PORT}/mcp"}

    print("[4/5] Writing MCP config ...")

    for name, config_path in targets:
        existing = _load_or_empty(config_path)
        merged = _merge_mcp_config(existing, "zbrush", server_config)

        if dry_run:
            print(f"[DRY RUN] Would write {name} config to {config_path}")
            # Show what would change
            if "zbrush" in existing.get("mcpServers", {}):
                print("[DRY RUN]   Update existing 'zbrush' entry")
            else:
                print("[DRY RUN]   Add new 'zbrush' entry")
            continue

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        action = "Updated" if "zbrush" in existing.get("mcpServers", {}) else "Created"
        print(f"       ✓ {action} {name} config: {config_path}")


# ---------------------------------------------------------------------------
# Step 5: Health check
# ---------------------------------------------------------------------------


def print_health_check(mode: str, plugin_dir: Path) -> None:
    """Print health check instructions."""
    print("[5/5] Health check")
    print()
    print(textwrap.dedent(f"""\
        ┌─────────────────────────────────────────────────────────────┐
        │  ✓ dcc-mcp-zbrush bootstrap complete ({mode} mode)           │
        └─────────────────────────────────────────────────────────────┘
    """))
    print("  Verify the installation:")
    print()
    if mode == "embedded":
        print("  1. Restart ZBrush — the plugin auto-starts the MCP server.")
        print("     Plugin location:", plugin_dir)
        print()
        print("  2. Health check (from any terminal):")
        print(f"     curl http://127.0.0.1:{MCP_PORT}/mcp")
        print()
        print("  3. Expected response: MCP endpoint info or SSE stream.")
    else:
        print("  1. Ensure the socket bridge plugin is in ZPlugs64:")
        print(f"     {plugin_dir / 'mcp_socket_bridge.py'}")
        print()
        print("  2. Start ZBrush (the socket bridge auto-starts and listens).")
        print()
        print("  3. Start the sidecar MCP server:")
        print(f"     dcc-mcp-zbrush --mode sidecar --port {MCP_PORT}")
        print()
        print("  4. Health check:")
        print(f"     curl http://127.0.0.1:{MCP_PORT}/mcp")
        print()
        print("  Both modes support the same skills and tools.")

    print()
    print("  Troubleshooting:")
    print("  - Connection refused → ZBrush not running or port blocked")
    print("  - Plugin not loading → check ZStartup/ZPlugs64 path above")
    print("  - Port conflict → set DCC_MCP_ZBRUSH_PORT env var")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-command bootstrap installer for dcc-mcp-zbrush.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s --mode embedded
              %(prog)s --mode sidecar --mcp-target both
              %(prog)s --mode embedded --version 0.2.7 --dry-run
              %(prog)s --mode embedded --skip-plugin --skip-config
        """),
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=("embedded", "sidecar"),
        help="Installation mode: embedded (MCP in ZBrush) or sidecar (socket bridge)",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Release version to install (default: latest GitHub release)",
    )
    parser.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help="ZBrush plugin directory (default: auto-detect per platform)",
    )
    parser.add_argument(
        "--mcp-target",
        choices=("cursor", "claude", "both"),
        default="cursor",
        help="Which MCP client config to write (default: cursor)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview operations without making changes",
    )
    parser.add_argument(
        "--skip-wheel",
        action="store_true",
        help="Skip pip install step",
    )
    parser.add_argument(
        "--skip-plugin",
        action="store_true",
        help="Skip plugin ZIP download + extract",
    )
    parser.add_argument(
        "--skip-config",
        action="store_true",
        help="Skip MCP config write",
    )

    args = parser.parse_args(argv)

    # Resolve version
    version = args.version or _get_latest_version()
    if version == "0.0.0":
        print("✗ Could not determine latest version and --version not specified", file=sys.stderr)
        return 1

    print("dcc-mcp-zbrush bootstrap installer")
    print(f"  Mode:     {args.mode}")
    print(f"  Version:  {version}")
    print(f"  Target:   {args.mcp_target}")
    if args.dry_run:
        print("  Dry run:  YES (no changes will be made)")
    print()

    plugin_dir = args.plugin_dir or _get_zbrush_plugin_dir()

    # Step 1: Install wheel
    if not args.skip_wheel:
        install_wheel(version, dry_run=args.dry_run)
    else:
        print("[1/5] Skipped (--skip-wheel)")

    # Step 2-3: Download + extract plugin
    if not args.skip_plugin:
        cache_dir = Path.home() / ".cache" / "dcc-mcp-zbrush"
        zip_path = download_plugin_zip(version, cache_dir, dry_run=args.dry_run)
        if not args.dry_run:
            extract_plugin(zip_path, plugin_dir, args.mode, dry_run=False)
        else:
            extract_plugin(zip_path, plugin_dir, args.mode, dry_run=True)
    else:
        print("[2/5] Skipped (--skip-plugin)")
        print("[3/5] Skipped (--skip-plugin)")

    # Step 4: Write MCP config
    if not args.skip_config:
        write_mcp_config(args.mcp_target, dry_run=args.dry_run)
    else:
        print("[4/5] Skipped (--skip-config)")

    # Step 5: Health check
    print_health_check(args.mode, plugin_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
