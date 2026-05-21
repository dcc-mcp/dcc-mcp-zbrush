"""Skill package validation and script unit tests (no live ZBrush required)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
import yaml

_SKILLS_ROOT = Path(__file__).parent.parent / "src" / "dcc_mcp_zbrush" / "skills"
_SKILL_DIRS = (
    "zbrush-scripting",
    "zbrush-scene",
    "zbrush-subtool",
    "zbrush-interchange",
)


def _load_script(skill_name: str, script_name: str) -> ModuleType:
    path = _SKILLS_ROOT / skill_name / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(f"skill_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("skill_dir", _SKILL_DIRS)
def test_validate_skill_clean(skill_dir: str) -> None:
    from dcc_mcp_core import validate_skill

    report = validate_skill(str(_SKILLS_ROOT / skill_dir))
    assert report.is_clean, report.issues


@pytest.mark.parametrize("skill_dir", _SKILL_DIRS)
def test_tools_yaml_contract(skill_dir: str) -> None:
    tools_path = _SKILLS_ROOT / skill_dir / "tools.yaml"
    data = yaml.safe_load(tools_path.read_text(encoding="utf-8"))
    for tool in data["tools"]:
        assert tool.get("execution") in ("sync", "async")
        assert tool.get("affinity") in ("main", "any")
        assert (tools_path.parent / tool["source_file"]).is_file()


def test_skills_index_exists() -> None:
    index = _SKILLS_ROOT / "SKILLS_INDEX.md"
    text = index.read_text(encoding="utf-8")
    for skill in _SKILL_DIRS:
        assert skill in text


def test_pack_plugin_builds_zip(tmp_path) -> None:
    import importlib.util

    pack_path = Path(__file__).parent.parent / "tools" / "pack_plugin.py"
    spec = importlib.util.spec_from_file_location("pack_plugin", pack_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    output = mod.pack_plugin(tmp_path, "0.2.0")
    assert output.is_file()
    assert output.name == "dcc-mcp-zbrush-plugin-0.2.0.zip"


class TestListSubtoolsSkill:
    def test_with_mock_zbc(self) -> None:
        mod = _load_script("zbrush-scene", "list_subtools.py")
        mock_zbc = MagicMock()
        mock_zbc.get_subtool_count.return_value = 2
        mock_zbc.get_subtool_status.side_effect = [0x01, 0x00]

        with patch(
            "dcc_mcp_zbrush._skill_host.run_in_zbrush",
            lambda embedded, *_a, **_k: embedded(mock_zbc),
        ):
            with patch("dcc_mcp_zbrush.api.with_zbrush", lambda f: f):
                result = mod.list_subtools()

        assert result["success"] is True
        assert result["context"]["count"] == 2
