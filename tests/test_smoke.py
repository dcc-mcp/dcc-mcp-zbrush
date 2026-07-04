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


# ---------------------------------------------------------------------------
# Bootstrap unit tests
# ---------------------------------------------------------------------------


class TestPlatformPaths:
    """Platform-specific path detection for ZBrush plugin and MCP config."""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path test")
    def test_zbrush_plugin_dir_windows(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.object(platform, "system", return_value="Windows"):
            with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\testuser"}):
                result = mod._get_zbrush_plugin_dir()
            assert result == Path(r"C:\Users\testuser\Documents\ZBrushData\ZStartup\ZPlugs64")

    def test_zbrush_plugin_dir_macos(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.object(platform, "system", return_value="Darwin"):
            with patch.object(Path, "home", return_value=Path("/Users/testuser")):
                result = mod._get_zbrush_plugin_dir()
            assert result == Path("/Users/testuser/Library/Application Support/ZBrush/ZStartup/ZPlugs64")

    def test_zbrush_plugin_dir_linux(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch.object(platform, "system", return_value="Linux"):
            with patch.object(Path, "home", return_value=Path("/home/testuser")):
                result = mod._get_zbrush_plugin_dir()
            # Check path parts, not string representation (cross-platform)
            parts = result.parts
            assert "ZStartup" in parts
            assert "ZPlugs64" in parts

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
        assert data["mcpServers"]["zbrush"]["url"] == "http://127.0.0.1:8765/mcp"

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
        assert data["mcpServers"]["zbrush"]["url"] == "http://127.0.0.1:8765/mcp"

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
            assert data["mcpServers"]["zbrush"]["url"] == "http://127.0.0.1:8765/mcp"


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
            zf.writestr("sidecar/mcp_socket_bridge.py", "# socket bridge")
            zf.writestr("install/install-windows.ps1", "# windows installer")
            zf.writestr("install/install-macos.sh", "#!/bin/sh\necho ok")
            zf.writestr("README-INSTALL.txt", "readme")
        return zip_path

    def test_embedded_mode_skips_sidecar_files(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        zip_path = self._make_test_zip(tmp_path)
        target = tmp_path / "ZPlugs64"

        mod.extract_plugin(zip_path, target, "embedded", dry_run=False)

        # Embedded files should exist
        assert (target / "embedded" / "dcc_mcp_zbrush" / "__init__.py").exists()
        assert (target / "embedded" / "dcc_mcp_zbrush" / "core.py").exists()
        # Sidecar file should NOT exist (skipped in embedded mode)
        assert not (target / "sidecar" / "mcp_socket_bridge.py").exists()
        # Install files should exist
        assert (target / "install" / "install-windows.ps1").exists()
        assert (target / "README-INSTALL.txt").exists()

    def test_sidecar_mode_includes_both(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        zip_path = self._make_test_zip(tmp_path)
        target = tmp_path / "ZPlugs64"

        mod.extract_plugin(zip_path, target, "sidecar", dry_run=False)

        # Both embedded and sidecar should exist
        assert (target / "embedded" / "dcc_mcp_zbrush" / "__init__.py").exists()
        assert (target / "sidecar" / "mcp_socket_bridge.py").exists()

    def test_extract_creates_target_directory(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        zip_path = self._make_test_zip(tmp_path)
        target = tmp_path / "nonexistent" / "ZPlugs64"

        mod.extract_plugin(zip_path, target, "embedded", dry_run=False)
        assert target.exists()

    def test_dry_run_extract_no_files_written(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        zip_path = self._make_test_zip(tmp_path)
        target = tmp_path / "ZPlugs64"

        # Ensure target does not exist before
        if target.exists():
            import shutil

            shutil.rmtree(target)

        mod.extract_plugin(zip_path, target, "embedded", dry_run=True)
        assert not target.exists()


class TestDryRun:
    """Dry-run mode tests — verify no side effects."""

    def test_dry_run_wheel_no_pip_call(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        with patch("subprocess.check_call") as mock_check_call:
            mod.install_wheel("0.2.7", dry_run=True)
            mock_check_call.assert_not_called()

    def test_dry_run_plugin_download_no_file(self, tmp_path: Path) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        # We'll mock the API response
        with patch.object(mod, "urllib") as mock_urllib:
            # Mock the API to return a minimal release
            mock_resp = mock_urllib.request.urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = json.dumps(
                {
                    "tag_name": "v0.2.7",
                    "assets": [
                        {
                            "name": "dcc-mcp-zbrush-plugin-0.2.7.zip",
                            "size": 12345,
                            "browser_download_url": "https://example.com/plugin.zip",
                        }
                    ],
                }
            ).encode("utf-8")

            result = mod.download_plugin_zip("0.2.7", tmp_path, dry_run=True)
            # In dry-run mode, no actual file is written
            assert not result.exists()

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
        assert mod.MCP_PORT == 8765

    def test_gateway_port_matches_project(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        assert mod.GATEWAY_PORT == 9765

    def test_github_repo_correct(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        assert "dcc-mcp/dcc-mcp-zbrush" in mod.GITHUB_REPO

    def test_mcp_config_template_has_correct_url(self) -> None:
        mod = _load_tool_module("bootstrap_agent_install.py")
        url = mod.MCP_CONFIG_TEMPLATE["mcpServers"]["zbrush"]["url"]
        assert f"127.0.0.1:{mod.MCP_PORT}/mcp" in url


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

    def test_pack_plugin_contains_init_py(self, tmp_path: Path) -> None:
        pack_path = _TOOLS_DIR / "pack_plugin.py"
        spec = importlib.util.spec_from_file_location("pack_plugin", pack_path)
        assert spec and spec.loader
        pack_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pack_mod)

        output = pack_mod.pack_plugin(tmp_path, "0.2.0")
        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()

        init_files = [n for n in names if n.endswith("__init__.py")]
        assert len(init_files) >= 1, f"No __init__.py found in {names}"

    def test_pack_plugin_contains_install_scripts(self, tmp_path: Path) -> None:
        pack_path = _TOOLS_DIR / "pack_plugin.py"
        spec = importlib.util.spec_from_file_location("pack_plugin", pack_path)
        assert spec and spec.loader
        pack_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pack_mod)

        output = pack_mod.pack_plugin(tmp_path, "0.2.0")
        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()

        assert "install/install-windows.ps1" in names
        assert "install/install-macos.sh" in names


# ---------------------------------------------------------------------------
# Docs drift check
# ---------------------------------------------------------------------------


class TestDocsDrift:
    """Cross-verify README, llms.txt, and AGENTS.md against source code truth."""

    # --- Source truth (single source) ---

    _PORT_FROM_CODE = 8765  # cli.py default
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
        "zbrush-interchange",
    }

    _INSTALL_COMMANDS = ["pip install dcc-mcp-zbrush"]
    _HEALTH_CHECK_COMMANDS = ["curl http://127.0.0.1:8765/mcp"]

    def _read_doc(self, name: str) -> str:
        """Read a documentation file from the project root."""
        path = _PROJECT_ROOT / name
        return path.read_text(encoding="utf-8")

    # --- Port assertions ---

    @pytest.mark.parametrize("doc_file", ["README.md", "llms.txt"])
    def test_mcp_port_in_doc(self, doc_file: str) -> None:
        """Verify the MCP port (8765) is referenced in the doc."""
        content = self._read_doc(doc_file)
        assert str(self._PORT_FROM_CODE) in content, f"MCP port {self._PORT_FROM_CODE} not found in {doc_file}"

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
        assert "pip install dcc-mcp-zbrush" in content, "pip install not found in README.md"

    def test_pip_install_in_llms_txt(self) -> None:
        content = self._read_doc("llms.txt")
        assert "pip install dcc-mcp-zbrush" in content, "pip install not found in llms.txt"

    # --- Health check assertions ---

    def test_health_check_curl_in_readme(self) -> None:
        content = self._read_doc("README.md")
        assert "curl http://127.0.0.1:8765/mcp" in content, "Health check curl not found in README.md"

    def test_health_check_curl_in_llms_txt(self) -> None:
        content = self._read_doc("llms.txt")
        assert "curl http://127.0.0.1:8765/mcp" in content, "Health check curl not found in llms.txt"

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
        assert "3.9" in content, "Python 3.9 requirement not found in README.md badge area"

    # --- AGENTS.md / llms.txt entry point check ---

    def test_llms_txt_has_install_steps(self) -> None:
        content = self._read_doc("llms.txt")
        # Should have numbered install steps or pip install
        assert "pip install" in content or "Install" in content

    def test_agents_md_has_mode_table(self) -> None:
        content = self._read_doc("AGENTS.md")
        assert "embedded" in content and "sidecar" in content
