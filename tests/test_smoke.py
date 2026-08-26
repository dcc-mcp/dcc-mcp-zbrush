"""Smoke tests for bootstrap installer, wheel, plugin ZIP, and docs drift.

Covers:
- Bootstrap unit tests (platform paths, MCP config, ZIP extraction, dry-run, versioning)
- Clean-venv smoke (build wheel → install → import → CLI entry point)
- Plugin ZIP structure validation
- Docs drift check (cross-verify docs against source truth)
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
_TOOLS_DIR = _PROJECT_ROOT / "tools"
_SRC_DIR = _PROJECT_ROOT / "src" / "dcc_mcp_zbrush"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_tool_module(name: str):
    """Load a tool script as a module via importlib."""
    path = _TOOLS_DIR / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader, f"Could not load spec for {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    """Parse an indented-at-most-three-spaces CommonMark fence marker."""
    content = line.rstrip("\r\n")
    stripped = content.lstrip(" ")
    if len(content) - len(stripped) > 3 or not stripped or stripped[0] not in {"`", "~"}:
        return None
    marker = stripped[0]
    width = 0
    while width < len(stripped) and stripped[width] == marker:
        width += 1
    if width < 3:
        return None
    return marker, width, stripped[width:]


def _is_backslash_escaped(text: str, index: int) -> bool:
    """Return whether the token at index is escaped by an odd backslash run."""
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _code_span_end(text: str, start: int, width: int) -> int | None:
    """Find a CommonMark-style closing backtick run with the exact opener width."""
    cursor = start + width
    while cursor < len(text):
        marker_start = text.find("`", cursor)
        if marker_start < 0:
            return None
        marker_end = marker_start
        while marker_end < len(text) and text[marker_end] == "`":
            marker_end += 1
        if marker_end - marker_start == width:
            return marker_end
        cursor = marker_end
    return None


def _rendered_visible_markdown(text: str) -> str:
    """Walk Markdown tokens and omit real HTML comments and fenced code blocks."""
    visible: list[str] = []
    active_fence: tuple[str, int] | None = None
    cursor = 0
    line_start = 0
    while cursor < len(text):
        if cursor == line_start:
            line_end = text.find("\n", cursor)
            line_end = len(text) if line_end < 0 else line_end + 1
            line = text[cursor:line_end]
            marker = _fence_marker(line)
            if active_fence is not None:
                if marker is not None:
                    marker_char, marker_width, remainder = marker
                    if marker_char == active_fence[0] and marker_width >= active_fence[1] and not remainder.strip():
                        active_fence = None
                visible.append("".join(character for character in line if character in "\r\n"))
                cursor = line_end
                line_start = line_end
                continue
            if marker is not None and not (marker[0] == "`" and "`" in marker[2]):
                active_fence = (marker[0], marker[1])
                visible.append("".join(character for character in line if character in "\r\n"))
                cursor = line_end
                line_start = line_end
                continue

        if text.startswith("<!--", cursor) and not _is_backslash_escaped(text, cursor):
            comment_end = text.find("-->", cursor + 4)
            hidden_end = len(text) if comment_end < 0 else comment_end + 3
            visible.append("".join(character for character in text[cursor:hidden_end] if character in "\r\n"))
            cursor = hidden_end
            continue

        if text[cursor] == "`" and not _is_backslash_escaped(text, cursor):
            marker_end = cursor
            while marker_end < len(text) and text[marker_end] == "`":
                marker_end += 1
            span_end = _code_span_end(text, cursor, marker_end - cursor)
            if span_end is not None:
                visible.append(text[cursor:span_end])
                line_break = text.rfind("\n", cursor, span_end)
                if line_break >= 0:
                    line_start = line_break + 1
                cursor = span_end
                continue

        character = text[cursor]
        visible.append(character)
        cursor += 1
        if character == "\n":
            line_start = cursor
    return "".join(visible)


def _has_complete_install_contract(
    text: str, required_sections: tuple[str, ...], required_prose: tuple[str, ...]
) -> bool:
    """Return whether rendered-visible text contains the complete Install SOP."""
    visible = _rendered_visible_markdown(text)
    lines = set(visible.splitlines())
    return all(section in lines for section in required_sections) and all(
        fragment in visible for fragment in required_prose
    )


def _tracked_public_doc_paths() -> list[str]:
    """Return tracked public documentation and discovery text files."""
    tracked = subprocess.check_output(
        ["git", "ls-files"],
        cwd=_PROJECT_ROOT,
        text=True,
        encoding="utf-8",
    ).splitlines()
    excluded_roots = {"build", "dist", "tests", "vendor"}
    public_suffixes = {".md", ".mdx", ".rst", ".txt"}
    return sorted(
        relative_path.replace("\\", "/")
        for relative_path in tracked
        if Path(relative_path).suffix.lower() in public_suffixes
        and (not Path(relative_path).parts or Path(relative_path).parts[0] not in excluded_roots)
    )


# ---------------------------------------------------------------------------
# Bootstrap unit tests
# ---------------------------------------------------------------------------


class TestPlatformPaths:
    """Platform-specific path detection for ZBrush plugin and MCP config."""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path test")
    def test_zbrush_plugin_dir_uses_asset_directory(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.object(platform, "system", return_value="Windows"):
            with patch.dict(os.environ, {"ZBRUSH_USER_ASSETS_DIR": r"D:\ZBrushAssets"}):
                result = mod._get_zbrush_plugin_dir()
            assert result == Path(r"D:\ZBrushAssets")

    def test_zbrush_plugin_dir_uses_first_configured_plugin_path(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.dict(
            os.environ,
            {"ZBRUSH_USER_ASSETS_DIR": "", "ZBRUSH_PLUGIN_PATH": os.pathsep.join(("/plugins/one", "/plugins/two"))},
        ):
            result = mod._get_zbrush_plugin_dir()
        assert result == Path("/plugins/one")

    def test_zbrush_plugin_dir_requires_discoverable_path(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.dict(os.environ, {"ZBRUSH_USER_ASSETS_DIR": "", "ZBRUSH_PLUGIN_PATH": ""}):
            with pytest.raises(RuntimeError, match="ZBRUSH_USER_ASSETS_DIR"):
                mod._get_zbrush_plugin_dir()

    def test_cursor_config_path_windows(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.object(platform, "system", return_value="Windows"):
            with patch.dict(os.environ, {"APPDATA": r"C:\Users\testuser\AppData\Roaming"}):
                result = mod._get_cursor_config_path()
            assert "Cursor" in str(result)
            assert "cline_mcp_settings.json" in str(result)

    def test_cursor_config_path_macos(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.object(platform, "system", return_value="Darwin"):
            with patch.object(Path, "home", return_value=Path("/Users/testuser")):
                result = mod._get_cursor_config_path()
            parts = result.parts
            assert "Cursor" in parts
            assert "cline_mcp_settings.json" in parts

    def test_claude_config_path_windows(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.object(platform, "system", return_value="Windows"):
            with patch.dict(os.environ, {"APPDATA": r"C:\Users\testuser\AppData\Roaming"}):
                result = mod._get_claude_config_path()
            assert "Claude" in str(result)
            assert "claude_desktop_config.json" in str(result)

    def test_claude_config_path_macos(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.object(platform, "system", return_value="Darwin"):
            with patch.object(Path, "home", return_value=Path("/Users/testuser")):
                result = mod._get_claude_config_path()
            parts = result.parts
            assert "Claude" in parts
            assert "claude_desktop_config.json" in parts


class TestMCPConfig:
    """MCP config generation and merging."""

    def test_merge_into_empty(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        server_config = {"url": "http://127.0.0.1:8765/mcp"}
        result = mod._merge_mcp_config({}, "zbrush", server_config)
        assert result == {"mcpServers": {"zbrush": {"url": "http://127.0.0.1:8765/mcp"}}}

    def test_merge_preserves_existing_servers(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        existing = {"mcpServers": {"other": {"command": "foo"}}}
        server_config = {"url": "http://127.0.0.1:8765/mcp"}
        result = mod._merge_mcp_config(existing, "zbrush", server_config)
        assert "other" in result["mcpServers"]
        assert result["mcpServers"]["other"] == {"command": "foo"}
        assert "zbrush" in result["mcpServers"]

    def test_merge_overwrites_existing_zbrush_entry(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        existing = {"mcpServers": {"zbrush": {"url": "http://old-url"}}}
        server_config = {"url": "http://127.0.0.1:8765/mcp"}
        result = mod._merge_mcp_config(existing, "zbrush", server_config)
        assert result["mcpServers"]["zbrush"] == {"url": "http://127.0.0.1:8765/mcp"}

    def test_merge_no_mcp_servers_key(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        existing = {"someOtherKey": "value"}
        server_config = {"url": "http://127.0.0.1:8765/mcp"}
        result = mod._merge_mcp_config(existing, "zbrush", server_config)
        assert "someOtherKey" in result
        assert "zbrush" in result["mcpServers"]

    def test_write_config_dry_run_no_file_write(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        config_path = tmp_path / "claude_desktop_config.json"
        with patch.object(mod, "_get_claude_config_path", return_value=config_path):
            mod.write_mcp_config("claude", dry_run=True)
        assert not config_path.exists()

    def test_write_config_creates_file(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        config_path = tmp_path / "claude_desktop_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with patch.object(mod, "_get_claude_config_path", return_value=config_path):
            mod.write_mcp_config("claude", dry_run=False)
        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["zbrush"]["url"] == "http://127.0.0.1:9765/mcp"

    def test_write_config_updates_existing(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        config_path = tmp_path / "claude_desktop_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"mcpServers": {"zbrush": {"url": "http://old"}}}),
            encoding="utf-8",
        )
        with patch.object(mod, "_get_claude_config_path", return_value=config_path):
            mod.write_mcp_config("claude", dry_run=False)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["zbrush"]["url"] == "http://127.0.0.1:9765/mcp"

    def test_write_config_both_targets(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        cursor_path = tmp_path / "cursor_config.json"
        claude_path = tmp_path / "claude_config.json"
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        claude_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(mod, "_get_cursor_config_path", return_value=cursor_path):
            with patch.object(mod, "_get_claude_config_path", return_value=claude_path):
                mod.write_mcp_config("both", dry_run=False)

        for p in (cursor_path, claude_path):
            assert p.exists()
            data = json.loads(p.read_text(encoding="utf-8"))
            assert data["mcpServers"]["zbrush"]["url"] == "http://127.0.0.1:9765/mcp"


class TestVersionSorting:
    """Version string parsing and comparison."""

    def test_parse_simple_version(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        assert mod._parse_version("0.2.7") == (0, 2, 7)

    def test_parse_v_prefix(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        assert mod._parse_version("v0.2.7") == (0, 2, 7)

    def test_parse_version_sorting(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        versions = ["0.2.7", "0.2.6", "0.3.0", "0.1.9", "1.0.0"]
        sorted_versions = sorted(versions, key=mod._parse_version)
        assert sorted_versions == ["0.1.9", "0.2.6", "0.2.7", "0.3.0", "1.0.0"]

    def test_parse_version_non_numeric(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        # "0.2.7-alpha" splits to ["0", "2", "7-alpha"]
        # int("7-alpha") fails → 0, so result is (0, 2, 0)
        result = mod._parse_version("0.2.7-alpha")
        assert result[:2] == (0, 2)
        assert len(result) >= 3


class TestPluginExtraction:
    """Plugin ZIP extraction and mode-aware file selection."""

    def _make_test_zip(self, tmp_path: Path, version: str = "0.2.0") -> Path:
        """Create a minimal test plugin ZIP."""
        zip_path = tmp_path / f"dcc-mcp-zbrush-plugin-{version}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("embedded/dcc_mcp_zbrush/__init__.py", "# embedded plugin")
            zf.writestr("embedded/dcc_mcp_zbrush/core.py", "# core module")
            zf.writestr("embedded/dcc_mcp_zbrush_plugin.py", "# autostart plugin")
            zf.writestr("sidecar/mcp_socket_bridge.py", "# socket bridge")
            zf.writestr("install/install-windows.ps1", "# windows installer")
            zf.writestr("install/install-macos.sh", "#!/bin/sh\necho ok")
            zf.writestr("README-INSTALL.txt", "readme")
        return zip_path

    def test_embedded_mode_skips_sidecar_files(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        zip_path = self._make_test_zip(tmp_path)
        target = tmp_path / "ZBrushAssets"

        mod.extract_plugin(zip_path, target, "embedded", dry_run=False)

        assert (target / "dcc_mcp_zbrush" / "__init__.py").exists()
        assert (target / "dcc_mcp_zbrush" / "core.py").exists()
        assert (target / "dcc_mcp_zbrush_plugin.py").exists()
        assert not (target / "mcp_socket_bridge.py").exists()

    def test_sidecar_direct_extraction_cannot_overwrite_shared_init(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        zip_path = self._make_test_zip(tmp_path)
        target = tmp_path / "ZBrushAssets"
        shared_init = target / "Python" / "init.py"
        shared_init.parent.mkdir(parents=True)
        shared_init.write_text("# studio startup\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="lifecycle|dcc-mcp-zbrush install"):
            mod.extract_plugin(zip_path, target, "sidecar", dry_run=False)

        assert shared_init.read_text(encoding="utf-8") == "# studio startup\n"

    def test_extract_creates_target_directory(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        zip_path = self._make_test_zip(tmp_path)
        target = tmp_path / "nonexistent" / "ZBrushAssets"

        mod.extract_plugin(zip_path, target, "embedded", dry_run=False)
        assert target.exists()

    def test_dry_run_extract_no_files_written(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        zip_path = self._make_test_zip(tmp_path)
        target = tmp_path / "ZBrushAssets"

        # Ensure target does not exist before
        if target.exists():
            import shutil

            shutil.rmtree(target)

        mod.extract_plugin(zip_path, target, "embedded", dry_run=True)
        assert not target.exists()


class TestDryRun:
    """Dry-run mode tests — verify no side effects."""

    def test_dry_run_uses_placeholder_when_plugin_path_is_unknown(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.dict(os.environ, {"ZBRUSH_USER_ASSETS_DIR": "", "ZBRUSH_PLUGIN_PATH": ""}):
            result = mod._resolve_plugin_dir(None, dry_run=True)

        assert result == Path("<ZBRUSH_PLUGIN_PATH>")

    def test_real_install_still_requires_discoverable_plugin_path(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.dict(os.environ, {"ZBRUSH_USER_ASSETS_DIR": "", "ZBRUSH_PLUGIN_PATH": ""}):
            with pytest.raises(RuntimeError, match="ZBRUSH_USER_ASSETS_DIR"):
                mod._resolve_plugin_dir(None, dry_run=False)

    def test_dry_run_wheel_no_pip_call(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch("subprocess.check_call") as mock_check_call:
            mod.install_wheel("0.2.7", dry_run=True)
            mock_check_call.assert_not_called()

    def test_dry_run_plugin_download_no_file(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        result = mod.download_plugin_zip("0.2.7", tmp_path, dry_run=True)

        assert not result.exists()

    def test_legacy_download_checksum_mismatch_never_enters_cache(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        release = {
            "assets": [
                {
                    "name": "dcc-mcp-zbrush-plugin-0.2.7.zip",
                    "size": 7,
                    "digest": f"sha256:{'0' * 64}",
                    "browser_download_url": (
                        "https://github.com/dcc-mcp/dcc-mcp-zbrush/releases/download/"
                        "v0.2.7/dcc-mcp-zbrush-plugin-0.2.7.zip"
                    ),
                }
            ]
        }
        response = patch.object(mod.urllib.request, "urlopen")
        download = patch.object(
            mod.urllib.request,
            "urlretrieve",
            side_effect=lambda _url, path: Path(path).write_bytes(b"payload"),
        )
        with response as mocked_response, download:
            mocked_response.return_value.__enter__.return_value.read.return_value = json.dumps(release).encode()
            with pytest.raises(SystemExit):
                mod.download_plugin_zip("0.2.7", tmp_path)

        assert not (tmp_path / "dcc-mcp-zbrush-plugin-0.2.7.zip").exists()
        assert not list(tmp_path.glob("*.download"))

    def test_wheel_install_called_when_not_dry_run(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch("subprocess.check_call") as mock_check_call:
            mod.install_wheel("0.2.7", dry_run=False)
            mock_check_call.assert_called_once()
            # Verify the command includes the right package
            cmd_args = mock_check_call.call_args[0][0]
            assert "dcc-mcp-zbrush==0.2.7" in cmd_args


class TestBootstrapConstants:
    """Verify bootstrap constants match project defaults."""

    def test_mcp_port_matches_project(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        assert mod.MCP_PORT == 0

    def test_gateway_port_matches_project(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        assert mod.GATEWAY_PORT == 9765

    def test_github_repo_correct(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        assert "dcc-mcp/dcc-mcp-zbrush" in mod.GITHUB_REPO

    def test_mcp_config_template_has_correct_url(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        url = mod.MCP_CONFIG_TEMPLATE["mcpServers"]["zbrush"]["url"]
        assert f"127.0.0.1:{mod.GATEWAY_PORT}/mcp" in url


# ---------------------------------------------------------------------------
# Clean-venv smoke test
# ---------------------------------------------------------------------------


class TestCleanVenvSmoke:
    """Verify wheel build + install + import + CLI entry point from source checkout."""

    def test_wheel_builds_and_installs(self, tmp_path: Path) -> None:
        """Build a wheel from source, install into a temp venv, and verify import."""
        import subprocess

        venv_dir = tmp_path / "venv"
        # Create venv (use --without-pip venv then install pip if needed, or just rely
        # on the host Python's pip via --system-site-packages approach)
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
        )

        # Determine venv python
        if platform.system() == "Windows":
            venv_python = venv_dir / "Scripts" / "python.exe"
        else:
            venv_python = venv_dir / "bin" / "python"

        # Upgrade pip in venv
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
            check=True,
            capture_output=True,
        )

        # Install build + wheel in venv
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "build", "wheel"],
            check=True,
            capture_output=True,
        )

        # Build wheel from source
        subprocess.run(
            [str(venv_python), "-m", "build", "--wheel", "--outdir", str(tmp_path / "dist"), str(_PROJECT_ROOT)],
            check=True,
            capture_output=True,
        )

        wheels = list((tmp_path / "dist").glob("dcc_mcp_zbrush-*.whl"))
        assert len(wheels) >= 1, f"No wheel found in {tmp_path / 'dist'}"

        # Install the wheel
        wheel_path = wheels[0]
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", str(wheel_path)],
            check=True,
            capture_output=True,
        )

        # Verify import
        result = subprocess.run(
            [str(venv_python), "-c", "import dcc_mcp_zbrush; print(dcc_mcp_zbrush.__version__)"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip(), "Expected non-empty version string"

    def test_cli_entry_point_exists(self) -> None:
        """Verify the CLI entry point dcc-mcp-zbrush is registered."""
        # Even if import fails (no core installed),
        # the module should be importable and have a main
        # If dcc-mcp-core is installed, --help should work
        # We check that at minimum the module can be loaded
        try:
            import dcc_mcp_zbrush.cli

            assert hasattr(dcc_mcp_zbrush.cli, "main")
        except ImportError:
            pytest.skip("dcc-mcp-core not installed — cannot fully test CLI")


# ---------------------------------------------------------------------------
# Plugin ZIP structure validation
# ---------------------------------------------------------------------------


class TestPluginZipStructure:
    """Validate that the packed plugin ZIP contains all required components."""

    def test_pack_plugin_contains_required_dirs(self, tmp_path: Path) -> None:
        """Build a plugin ZIP and verify its internal structure."""
        # Import pack_plugin module
        pack_path = _TOOLS_DIR / "pack_plugin.py"
        spec = importlib.util.spec_from_file_location("pack_plugin", pack_path)
        assert spec and spec.loader
        pack_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pack_mod)

        output = pack_mod.pack_plugin(tmp_path, "0.2.0")
        assert output.is_file()
        assert output.name == "dcc-mcp-zbrush-plugin-0.2.0.zip"

        with zipfile.ZipFile(output, "r") as zf:
            names = sorted(zf.namelist())

        # Required directories / prefixes
        has_embedded = any(n.startswith("embedded/") for n in names)
        has_sidecar = any(n.startswith("sidecar/") for n in names)
        has_install = any(n.startswith("install/") for n in names)
        has_readme = any(n == "README-INSTALL.txt" for n in names)

        assert has_embedded, f"Missing embedded/ in {names}"
        assert has_sidecar, f"Missing sidecar/ in {names}"
        assert has_install, f"Missing install/ in {names}"
        assert has_readme, f"Missing README-INSTALL.txt in {names}"

    def test_pack_plugin_contains_socket_bridge(self, tmp_path: Path) -> None:
        pack_path = _TOOLS_DIR / "pack_plugin.py"
        spec = importlib.util.spec_from_file_location("pack_plugin", pack_path)
        assert spec and spec.loader
        pack_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pack_mod)

        output = pack_mod.pack_plugin(tmp_path, "0.2.0")
        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()

        assert "sidecar/mcp_socket_bridge.py" in names

    def test_pack_plugin_contains_autostart_entry(self, tmp_path: Path) -> None:
        pack_path = _TOOLS_DIR / "pack_plugin.py"
        spec = importlib.util.spec_from_file_location("pack_plugin", pack_path)
        assert spec and spec.loader
        pack_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pack_mod)

        output = pack_mod.pack_plugin(tmp_path, "0.2.0")
        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()

        assert "embedded/dcc_mcp_zbrush_plugin.py" in names

    def test_pack_plugin_contains_install_scripts(self, tmp_path: Path) -> None:
        pack_path = _TOOLS_DIR / "pack_plugin.py"
        spec = importlib.util.spec_from_file_location("pack_plugin", pack_path)
        assert spec and spec.loader
        pack_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pack_mod)

        output = pack_mod.pack_plugin(tmp_path, "0.2.0")
        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
            windows = zf.read("install/install-windows.ps1").decode("utf-8")
            macos = zf.read("install/install-macos.sh").decode("utf-8")
            readme = zf.read("README-INSTALL.txt").decode("utf-8")

        assert "install/install-windows.ps1" in names
        assert "install/install-macos.sh" in names
        assert "dcc-mcp-zbrush install" in windows
        assert "dcc-mcp-zbrush install" in macos
        assert "Copy-Item" not in windows
        assert "sidecar/mcp_socket_bridge.py" not in macos
        assert "raw.githubusercontent.com/dcc-mcp/dcc-mcp-zbrush/main/install.md" in readme


# ---------------------------------------------------------------------------
# Docs drift check
# ---------------------------------------------------------------------------


class TestDocsDrift:
    """Cross-verify README, llms.txt, and AGENTS.md against source code truth."""

    _REQUIRED_INSTALL_SECTIONS = (
        "## Requirements",
        "## Supported versions",
        "## Agent quick path",
        "## Manual path",
        "## Verify",
        "## Upgrade",
        "## Uninstall",
        "## Troubleshooting",
    )
    _CANONICAL_INSTALL_RAW_URL = "https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-zbrush/main/install.md"
    _REQUIRED_INSTALL_PROSE = (
        "This is the canonical install SOP",
        "The default `sidecar` mode installs",
        "An applied install or upgrade returns exit",
        "For sidecar mode, `verify` checks",
        "Uninstall is receipt-driven and transactional",
        "All lifecycle verbs accept",
    )

    # --- Source truth (single source) ---

    _PORT_FROM_CODE = 0  # core/OS-assigned instance port
    _GATEWAY_PORT_FROM_CODE = 9765  # _env.py / README table
    _SOCKET_PORT_FROM_CODE = 9876  # _env.py DEFAULT_SOCKET_PORT / cli.py default

    _ENV_VARS = {
        "DCC_MCP_ZBRUSH_PORT",
        "DCC_MCP_ZBRUSH_MODE",
        "DCC_MCP_ZBRUSH_AUTOSTART",
        "DCC_MCP_ZBRUSH_SOCKET_PORT",
        "DCC_MCP_GATEWAY_PORT",
        "DCC_MCP_MINIMAL",
    }

    _ZB_VERSION_REQUIREMENT = "2026.1+"

    _BUNDLED_SKILLS = {
        "zbrush-scripting",
        "zbrush-scene",
        "zbrush-subtool",
        "zbrush-brush",
        "zbrush-viewport",
        "zbrush-interchange",
    }

    _INSTALL_COMMANDS = ["pip install dcc-mcp-zbrush"]
    _HEALTH_CHECK_COMMANDS = ["dcc-mcp-cli list"]

    def _read_doc(self, name: str) -> str:
        """Read a documentation file from the project root."""
        path = _PROJECT_ROOT / name
        return path.read_text(encoding="utf-8")

    # --- Port assertions ---

    @pytest.mark.parametrize("doc_file", ["README.md", "llms.txt"])
    def test_mcp_port_in_doc(self, doc_file: str) -> None:
        """Verify the OS-assigned instance-port default is documented."""
        content = self._read_doc(doc_file)
        assert "OS-assigned" in content, f"Dynamic instance-port default not found in {doc_file}"

    @pytest.mark.parametrize("doc_file", ["README.md", "llms.txt"])
    def test_gateway_port_in_doc(self, doc_file: str) -> None:
        """Verify the gateway port (9765) is referenced in the doc."""
        content = self._read_doc(doc_file)
        assert str(self._GATEWAY_PORT_FROM_CODE) in content, (
            f"Gateway port {self._GATEWAY_PORT_FROM_CODE} not found in {doc_file}"
        )

    @pytest.mark.parametrize("doc_file", ["README.md", "llms.txt", "AGENTS.md"])
    def test_zbrush_version_requirement_in_doc(self, doc_file: str) -> None:
        """Verify ZBrush 2026.1+ requirement is present."""
        content = self._read_doc(doc_file)
        assert "2026.1" in content, f"ZBrush version requirement 2026.1 not found in {doc_file}"

    # --- Env var assertions ---

    @pytest.mark.parametrize("env_var", sorted(_ENV_VARS))
    def test_env_var_in_readme(self, env_var: str) -> None:
        content = self._read_doc("README.md")
        assert env_var in content, f"Env var {env_var} not found in README.md"

    @pytest.mark.parametrize("env_var", sorted(_ENV_VARS))
    def test_env_var_in_llms_txt(self, env_var: str) -> None:
        content = self._read_doc("llms.txt")
        assert env_var in content, f"Env var {env_var} not found in llms.txt"

    # --- Bundled skills assertions ---

    @pytest.mark.parametrize("skill", sorted(_BUNDLED_SKILLS))
    def test_skill_in_readme(self, skill: str) -> None:
        content = self._read_doc("README.md")
        assert skill in content, f"Skill {skill} not found in README.md"

    # llms.txt is condensed — only lists zbrush-scripting and zbrush-scene
    @pytest.mark.parametrize("skill", ["zbrush-scripting", "zbrush-scene"])
    def test_skill_in_llms_txt(self, skill: str) -> None:
        content = self._read_doc("llms.txt")
        assert skill in content, f"Skill {skill} not found in llms.txt"

    # --- Install command assertions ---

    def test_pip_install_in_readme(self) -> None:
        content = self._read_doc("README.md")
        assert "dcc-mcp-zbrush==<version>" in content, "fixed wheel install not found in README.md"

    def test_pip_install_in_llms_txt(self) -> None:
        content = self._read_doc("llms.txt")
        assert "dcc-mcp-zbrush==<version>" in content, "fixed wheel install not found in llms.txt"

    # --- Health check assertions ---

    def test_health_check_curl_in_readme(self) -> None:
        content = self._read_doc("README.md")
        assert "dcc-mcp-cli list" in content, "CLI discovery check not found in README.md"

    def test_health_check_curl_in_llms_txt(self) -> None:
        content = self._read_doc("llms.txt")
        assert "dcc-mcp-cli list" in content, "CLI discovery check not found in llms.txt"

    # --- MCP endpoint assertions ---

    @pytest.mark.parametrize("doc_file", ["README.md", "llms.txt", "AGENTS.md", ".claude/CLAUDE.md"])
    def test_mcp_endpoint_in_doc(self, doc_file: str) -> None:
        content = self._read_doc(doc_file)
        assert "/mcp" in content, f"Endpoint /mcp not found in {doc_file}"

    # --- Mode documentation ---

    def test_embedded_mode_in_readme(self) -> None:
        content = self._read_doc("README.md")
        assert "embedded" in content.lower(), "Embedded mode not mentioned in README.md"

    def test_sidecar_mode_in_readme(self) -> None:
        content = self._read_doc("README.md")
        assert "sidecar" in content.lower(), "Sidecar mode not mentioned in README.md"

    # --- README references check ---

    def test_readme_references_prd(self) -> None:
        content = self._read_doc("README.md")
        # README should reference development docs
        assert "docs/development.md" in content or "development" in content.lower()

    def test_agents_md_references_key_files(self) -> None:
        content = self._read_doc("AGENTS.md")
        assert "server.py" in content
        assert "bridge.py" in content

    # --- pyproject.toml version consistency ---

    def test_pyproject_version_is_valid(self) -> None:
        """Verify pyproject.toml has a PEP 440 version."""
        pyproject = _PROJECT_ROOT / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        import re

        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        assert match, "No version found in pyproject.toml"
        version = match.group(1)
        # PEP 440: must have at least major.minor.patch
        parts = version.split(".")
        assert len(parts) >= 2, f"Version '{version}' doesn't look like PEP 440"

    # --- Python requirement drift ---

    def test_python_requirement_in_pyproject(self) -> None:
        pyproject = _PROJECT_ROOT / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert "requires-python" in content
        assert "3.9" in content or "3.10" in content or "3.11" in content

    def test_python_version_in_readme_badge(self) -> None:
        content = self._read_doc("README.md")
        assert "3.10" in content, "Python 3.10 requirement not found in README.md badge area"

    # --- AGENTS.md / llms.txt entry point check ---

    def test_llms_txt_has_install_steps(self) -> None:
        content = self._read_doc("llms.txt")
        # Should have numbered install steps or pip install
        assert "pip install" in content or "Install" in content

    def test_root_install_sop_is_the_complete_canonical_contract(self) -> None:
        install_path = _PROJECT_ROOT / "install.md"
        assert install_path.is_file(), "the canonical Install SOP must live at repository-root install.md"

        content = install_path.read_text(encoding="utf-8")
        visible = _rendered_visible_markdown(content)
        headings = set(visible.splitlines())
        missing_sections = [section for section in self._REQUIRED_INSTALL_SECTIONS if section not in headings]
        assert missing_sections == [], f"root install.md is missing required SOP sections: {missing_sections}"
        assert _has_complete_install_contract(content, self._REQUIRED_INSTALL_SECTIONS, self._REQUIRED_INSTALL_PROSE)
        assert self._CANONICAL_INSTALL_RAW_URL in visible
        assert "main/docs/install.md" not in content

        tracked_markdown = _tracked_public_doc_paths()
        assert "llms.txt" in tracked_markdown
        full_contracts = []
        for relative_path in tracked_markdown:
            candidate = self._read_doc(relative_path)
            if _has_complete_install_contract(candidate, self._REQUIRED_INSTALL_SECTIONS, self._REQUIRED_INSTALL_PROSE):
                full_contracts.append(relative_path.replace("\\", "/"))
        assert full_contracts == ["install.md"], f"the complete Install SOP must have one owner: {full_contracts}"

    def test_hidden_or_fenced_install_contract_cannot_become_an_owner(self) -> None:
        complete_decoy = "\n".join((*self._REQUIRED_INSTALL_SECTIONS, *self._REQUIRED_INSTALL_PROSE))
        deceptive_documents = (
            f"<!--\n{complete_decoy}\n-->",
            f"````markdown\n{complete_decoy}\n```\nstill fenced\n````",
            f"~~~~~md\n{complete_decoy}\n~~~~~~",
        )

        for deceptive_document in deceptive_documents:
            assert not _has_complete_install_contract(
                deceptive_document,
                self._REQUIRED_INSTALL_SECTIONS,
                self._REQUIRED_INSTALL_PROSE,
            )

    @pytest.mark.parametrize(
        ("opener", "closer"),
        (("\\<!--", "-->"), ("`<!--`", "`-->`")),
        ids=("escaped-comment-marker", "inline-code-comment-marker"),
    )
    def test_install_owner_scan_keeps_rendered_comment_markers_visible(self, opener: str, closer: str) -> None:
        complete_decoy = "\n".join((*self._REQUIRED_INSTALL_SECTIONS, *self._REQUIRED_INSTALL_PROSE))
        assert _has_complete_install_contract(
            f"{opener}\n{complete_decoy}\n{closer}",
            self._REQUIRED_INSTALL_SECTIONS,
            self._REQUIRED_INSTALL_PROSE,
        )

    def test_visible_markdown_scanner_handles_multiline_comments_and_fence_lengths(self) -> None:
        document = """Visible before
<!-- hidden on one line -->
<!--
hidden across lines
-->
````markdown
hidden in a long backtick fence
```
still hidden after a short closer
`````
Visible between
~~~text
hidden in a tilde fence
~~~~
Visible after
"""

        visible = _rendered_visible_markdown(document)
        assert [line for line in visible.splitlines() if line] == ["Visible before", "Visible between", "Visible after"]
        assert "hidden" not in visible

    def test_public_install_references_use_the_root_contract(self) -> None:
        expected_references = {
            "README.md": ("(install.md)", self._CANONICAL_INSTALL_RAW_URL),
            "docs/development.md": ("[install.md](../install.md)",),
            "pyproject.toml": ('Installation = "https://github.com/dcc-mcp/dcc-mcp-zbrush/blob/main/install.md"',),
            "docs/agent-docs.yaml": (self._CANONICAL_INSTALL_RAW_URL,),
            "AGENTS.md": (self._CANONICAL_INSTALL_RAW_URL,),
            ".claude/CLAUDE.md": (self._CANONICAL_INSTALL_RAW_URL,),
            "llms.txt": (self._CANONICAL_INSTALL_RAW_URL,),
            "tools/pack_plugin.py": (self._CANONICAL_INSTALL_RAW_URL,),
        }
        legacy_reference = "main/docs/" + "install.md"

        failures = []
        for relative_path, required_fragments in expected_references.items():
            content = self._read_doc(relative_path)
            searchable_content = (
                _rendered_visible_markdown(content)
                if Path(relative_path).suffix.lower() in {".md", ".mdx", ".rst", ".txt"}
                else content
            )
            for fragment in required_fragments:
                if fragment not in searchable_content:
                    failures.append(f"{relative_path} is missing {fragment!r}")
            if legacy_reference in content:
                failures.append(f"{relative_path} still references {legacy_reference!r}")

        for relative_path in _tracked_public_doc_paths():
            if legacy_reference in self._read_doc(relative_path):
                failures.append(f"{relative_path} still contains legacy canonical path {legacy_reference!r}")

        assert failures == [], "public install references are inconsistent:\n" + "\n".join(failures)

    def test_public_docs_do_not_offer_a_direct_bridge_copy_install_path(self) -> None:
        bridge_filename = "mcp_socket_bridge.py"
        install_destinations = ("Asset Directory", "ZBRUSH_PLUGIN_PATH")
        offenders = []
        for relative_path in _tracked_public_doc_paths():
            if relative_path == "install.md":
                continue
            visible = _rendered_visible_markdown(self._read_doc(relative_path))
            paragraphs = visible.replace("\r\n", "\n").split("\n\n")
            if any(
                bridge_filename in paragraph and any(target in paragraph for target in install_destinations)
                for paragraph in paragraphs
            ):
                offenders.append(relative_path)

        assert offenders == [], f"public docs contain a competing direct bridge install path: {offenders}"
        assert "[canonical Install SOP](../install.md)" in _rendered_visible_markdown(self._read_doc("docs/PRD.md"))

    def test_legacy_docs_install_is_only_a_short_pointer(self) -> None:
        pointer_path = _PROJECT_ROOT / "docs" / "install.md"
        assert pointer_path.is_file(), "the former docs/install.md route must remain as a compatibility pointer"

        content = pointer_path.read_text(encoding="utf-8")
        visible = _rendered_visible_markdown(content)
        assert "../install.md" in visible
        assert self._CANONICAL_INSTALL_RAW_URL in visible
        assert len(content) <= 600, "docs/install.md must not duplicate the complete root Install SOP"
        assert not any(section in set(visible.splitlines()) for section in self._REQUIRED_INSTALL_SECTIONS)
        assert "dcc-mcp-zbrush install" not in visible

    def test_canonical_install_sop_covers_lifecycle_contract(self) -> None:
        content = self._read_doc("install.md")
        visible = _rendered_visible_markdown(content)
        assert _has_complete_install_contract(content, self._REQUIRED_INSTALL_SECTIONS, self._REQUIRED_INSTALL_PROSE)
        for operation in ("install", "status", "verify", "uninstall", "upgrade"):
            assert f"dcc-mcp-zbrush {operation}" in content
        for exit_code in ("`0`", "`10`", "`20`", "`30`", "`40`", "`50`"):
            assert exit_code in visible
        assert "raw.githubusercontent.com/dcc-mcp/dcc-mcp-zbrush/main/install.md" in visible
        assert "Windows" in visible and "macOS" in visible and "Linux" in visible

    def test_agents_md_has_mode_table(self) -> None:
        content = self._read_doc("AGENTS.md")
        assert "embedded" in content and "sidecar" in content
