"""Unit tests for zbrush-import-to-scene skill (no live ZBrush required)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import yaml

_SKILLS_ROOT = Path(__file__).parent.parent / "src" / "dcc_mcp_zbrush" / "skills"
_SKILL_DIR = "zbrush-import-to-scene"


def _load_script(script_name: str) -> ModuleType:
    path = _SKILLS_ROOT / _SKILL_DIR / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(f"skill_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_validate_skill_clean() -> None:
    from dcc_mcp_core import validate_skill

    report = validate_skill(str(_SKILLS_ROOT / _SKILL_DIR))
    assert report.is_clean, report.issues


def test_tools_yaml_contract() -> None:
    tools_path = _SKILLS_ROOT / _SKILL_DIR / "tools.yaml"
    data = yaml.safe_load(tools_path.read_text(encoding="utf-8"))
    for tool in data["tools"]:
        assert tool.get("execution") in ("sync", "async")
        assert tool.get("affinity") in ("main", "any")
        assert (tools_path.parent / tool["source_file"]).is_file()


def test_skill_in_index() -> None:
    index = _SKILLS_ROOT / "SKILLS_INDEX.md"
    text = index.read_text(encoding="utf-8")
    assert _SKILL_DIR in text


class TestImportToSceneSkill:
    def _make_mock_zbc(self, active_path: str = "/ZBrush/desk.ZTL") -> MagicMock:
        mock_zbc = MagicMock()
        mock_zbc.get_active_tool_path.return_value = active_path
        return mock_zbc

    def test_successful_obj_import(self, tmp_path) -> None:
        asset_file = tmp_path / "desk.obj"
        asset_file.write_text("# obj placeholder")

        mock_zbc = self._make_mock_zbc("/ZBrush/desk.ZTL")
        mod = _load_script("import_to_scene.py")

        with patch(
            "dcc_mcp_zbrush._skill_host.run_in_zbrush",
            lambda embedded, *_a, **_k: embedded(mock_zbc),
        ):
            with patch("dcc_mcp_zbrush.api.with_zbrush", lambda f: f):
                result = mod.import_to_scene(
                    asset_id="arch/desk",
                    variants=[{"local_path": str(asset_file), "format": "obj"}],
                )

        assert result["success"] is True
        assert "desk.ZTL" in result["context"]["imported_nodes"][0]
        mock_zbc.set_next_filename.assert_called_once()
        mock_zbc.press.assert_called_once_with("Tool:Import")

    def test_successful_fbx_import(self, tmp_path) -> None:
        asset_file = tmp_path / "chair.fbx"
        asset_file.write_text("# fbx placeholder")

        mock_zbc = self._make_mock_zbc("/ZBrush/chair.ZTL")
        mod = _load_script("import_to_scene.py")

        with patch(
            "dcc_mcp_zbrush._skill_host.run_in_zbrush",
            lambda embedded, *_a, **_k: embedded(mock_zbc),
        ):
            with patch("dcc_mcp_zbrush.api.with_zbrush", lambda f: f):
                result = mod.import_to_scene(
                    asset_id="arch/chair",
                    variants=[{"local_path": str(asset_file), "format": "fbx"}],
                )

        assert result["success"] is True
        assert result["context"]["extra"]["asset_id"] == "arch/chair"

    def test_file_not_found_returns_error(self) -> None:
        mod = _load_script("import_to_scene.py")
        mock_zbc = self._make_mock_zbc()

        with patch(
            "dcc_mcp_zbrush._skill_host.run_in_zbrush",
            lambda embedded, *_a, **_k: embedded(mock_zbc),
        ):
            with patch("dcc_mcp_zbrush.api.with_zbrush", lambda f: f):
                result = mod.import_to_scene(
                    asset_id="arch/missing",
                    variants=[{"local_path": "/nonexistent/path/asset.obj", "format": "obj"}],
                )

        assert result["success"] is False
        assert "FILE_NOT_FOUND" in str(result)

    def test_empty_variants_returns_error(self) -> None:
        mod = _load_script("import_to_scene.py")

        with patch("dcc_mcp_zbrush.api.with_zbrush", lambda f: f):
            result = mod.import_to_scene(
                asset_id="arch/empty",
                variants=[],
            )

        assert result["success"] is False
        assert "INVALID_DESCRIPTOR" in str(result)

    def test_preferred_variant_is_picked_first(self, tmp_path) -> None:
        non_preferred = tmp_path / "low.obj"
        non_preferred.write_text("# low")
        preferred = tmp_path / "high.fbx"
        preferred.write_text("# high")

        mock_zbc = self._make_mock_zbc("/ZBrush/high.ZTL")
        mod = _load_script("import_to_scene.py")
        captured: list = []

        def fake_run(embedded, *_a, **_k):
            r = embedded(mock_zbc)
            captured.append(r)
            return r

        with patch("dcc_mcp_zbrush._skill_host.run_in_zbrush", fake_run):
            with patch("dcc_mcp_zbrush.api.with_zbrush", lambda f: f):
                result = mod.import_to_scene(
                    asset_id="arch/highres",
                    variants=[
                        {"local_path": str(non_preferred), "format": "obj", "preferred": False},
                        {"local_path": str(preferred), "format": "fbx", "preferred": True},
                    ],
                )

        assert result["success"] is True
        # The preferred FBX variant should have been imported
        call_args = mock_zbc.set_next_filename.call_args[0][0]
        assert "high.fbx" in call_args

    def test_unsupported_format_adds_warning(self, tmp_path) -> None:
        asset_file = tmp_path / "scene.usd"
        asset_file.write_text("# usd placeholder")

        mock_zbc = self._make_mock_zbc("/ZBrush/scene.ZTL")
        mod = _load_script("import_to_scene.py")

        with patch(
            "dcc_mcp_zbrush._skill_host.run_in_zbrush",
            lambda embedded, *_a, **_k: embedded(mock_zbc),
        ):
            with patch("dcc_mcp_zbrush.api.with_zbrush", lambda f: f):
                result = mod.import_to_scene(
                    asset_id="arch/scene",
                    variants=[{"local_path": str(asset_file), "format": "usd"}],
                )

        # Should still attempt import but with a warning
        assert result["success"] is True
        warnings = result["context"].get("warnings", [])
        assert any(w["code"] == "unsupported_feature" for w in warnings)
