"""Skill package validation and script unit tests (no live ZBrush required)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

_SKILLS_ROOT = Path(__file__).parent.parent / "src" / "dcc_mcp_zbrush" / "skills"
_SKILL_DIRS = (
    "zbrush-scripting",
    "zbrush-scene",
    "zbrush-subtool",
    "zbrush-brush",
    "zbrush-viewport",
    "zbrush-interchange",
    "zbrush-import-to-scene",
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
        # Skill handlers run in the sidecar; the socket bridge marshals SDK
        # work onto ZBrush's main thread inside the host process.
        assert tool.get("affinity") == "any"
        assert (tools_path.parent / tool["source_file"]).is_file()
        assert "inputSchema" not in tool
        assert tool["input_schema"]["type"] == "object"
        for outcome in tool.get("next-tools", {}).values():
            assert all(isinstance(tool_name, str) for tool_name in outcome)


def test_skills_index_exists() -> None:
    index = _SKILLS_ROOT / "SKILLS_INDEX.md"
    text = index.read_text(encoding="utf-8")
    for skill in _SKILL_DIRS:
        assert skill in text


def test_quiet_ui_actions_restore_feedback_and_normalize_subtool_paths() -> None:
    from dcc_mcp_zbrush._skill_host import quiet_ui_actions, run_quiet_ui, subtool_name_from_path

    mock_zbc = MagicMock()
    with pytest.raises(RuntimeError, match="stop"):
        with quiet_ui_actions(mock_zbc):
            raise RuntimeError("stop")

    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]

    action = MagicMock()
    mock_zbc.show_actions.reset_mock()
    run_quiet_ui(mock_zbc, action)

    action.assert_called_once_with()
    mock_zbc.freeze.assert_not_called()
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]
    assert subtool_name_from_path(r"F:\models\horse_statue_01") == "horse_statue_01"
    assert subtool_name_from_path("/ZBrush/marble_bust_01") == "marble_bust_01"


def test_run_in_zbrush_can_return_a_domain_failure() -> None:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush

    failure = {"success": False, "error": "UVS_MISSING", "uv_bounds": [0.0, 0.0, 0.0, 0.0]}
    bridge = MagicMock()
    bridge.call.return_value = failure
    with (
        patch("dcc_mcp_zbrush._version_probe.is_zbrush_available", return_value=False),
        patch("dcc_mcp_zbrush.api.get_bridge", return_value=bridge),
    ):
        assert run_in_zbrush(lambda _zbc: {}, "bake", allow_domain_failure=True) == failure
        with pytest.raises(RuntimeError, match="UVS_MISSING"):
            run_in_zbrush(lambda _zbc: {}, "bake")


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


def test_refine_active_subtool_uses_typed_zbrush_operations() -> None:
    mod = _load_script("zbrush-subtool", "refine_active_subtool.py")
    mock_zbc = MagicMock()
    mock_zbc.get_active_tool_path.return_value = "/ZBrush/signal_forge.ZTL"

    with patch(
        "dcc_mcp_zbrush._skill_host.run_in_zbrush",
        lambda embedded, *_a, **_k: embedded(mock_zbc),
    ):
        result = mod.refine_active_subtool(
            subdivision_levels=2,
            polish=12,
            inflate=1.5,
        )

    assert result["success"] is True
    assert mock_zbc.press.call_args_list == [
        call("Tool:Geometry:Divide"),
        call("Tool:Geometry:Divide"),
    ]
    assert mock_zbc.set.call_args_list == [
        call("Tool:Deformation:Polish", 12.0),
        call("Tool:Deformation:Inflate", 1.5),
    ]


def test_inspect_active_mesh_returns_machine_comparable_metrics() -> None:
    mod = _load_script("zbrush-subtool", "inspect_active_mesh.py")
    mock_zbc = MagicMock()
    mock_zbc.query_mesh3d.side_effect = lambda property_id: {
        0: [12_345.0],
        1: [24_678.0],
        2: [-1.0, -2.0, -3.0, 1.0, 2.0, 3.0],
        3: [0.0, 0.0, 1.0, 1.0],
        8: [42.5],
    }[property_id]
    mock_zbc.is_polymesh3d_solid.return_value = True
    mock_zbc.get_active_tool_path.return_value = "/ZBrush/asset.ZTL"

    with patch(
        "dcc_mcp_zbrush._skill_host.run_in_zbrush",
        lambda embedded, *_a, **_k: embedded(mock_zbc),
    ):
        result = mod.inspect_active_mesh()

    assert result["success"] is True
    assert result["context"]["point_count"] == 12_345
    assert result["context"]["face_count"] == 24_678
    assert result["context"]["has_uvs"] is True
    assert result["context"]["bounds"] == [-1.0, -2.0, -3.0, 1.0, 2.0, 3.0]


def test_bake_active_subtool_map_reports_missing_uvs(tmp_path) -> None:
    mod = _load_script("zbrush-subtool", "bake_active_subtool_map.py")
    mock_zbc = MagicMock()
    mock_zbc.query_mesh3d.return_value = [0.0, 0.0, 0.0, 0.0]

    with patch(
        "dcc_mcp_zbrush._skill_host.run_in_zbrush",
        lambda embedded, *_a, **_k: embedded(mock_zbc),
    ):
        result = mod.bake_active_subtool_map(
            map_type="normal",
            output_path=str(tmp_path / "normal.tif"),
        )

    assert result["success"] is False
    assert result["error"] == "UVS_MISSING"
    mock_zbc.create_normal_map.assert_not_called()


def test_bake_active_subtool_map_uses_native_create_controls_and_restores_settings(tmp_path) -> None:
    mod = _load_script("zbrush-subtool", "bake_active_subtool_map.py")
    output_path = tmp_path / "normal.tif"
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.query_mesh3d.return_value = [0.0, 0.0, 1.0, 1.0]
    mock_zbc.get.side_effect = lambda path: {
        "Tool:UV Map:UV Map Size": 2048.0,
        "Tool:UV Map:UV Map Border": 4.0,
        "Tool:Normal Map:SmoothUV": 0.0,
        "Tool:Normal Map:Tangent": 0.0,
    }[path]
    lower_res_enabled = iter((True, False))
    mock_zbc.is_enabled.side_effect = lambda path: (
        next(lower_res_enabled) if path == "Tool:Geometry:Lower Res" else True
    )
    state: dict[str, str] = {}
    mock_zbc.set_next_filename.side_effect = lambda path: state.update(path=path)

    def press(item_path: str) -> None:
        if item_path == "Texture:Export":
            Path(state["path"]).write_bytes(b"normal-map")

    mock_zbc.press.side_effect = press

    with patch(
        "dcc_mcp_zbrush._skill_host.run_in_zbrush",
        lambda embedded, *_a, **_k: embedded(mock_zbc),
    ):
        result = mod.bake_active_subtool_map(
            map_type="normal",
            output_path=str(output_path),
            width=1024,
            height=1024,
            border=8,
        )

    assert result["success"] is True
    assert output_path.read_bytes() == b"normal-map"
    mock_zbc.create_normal_map.assert_not_called()
    assert call("Tool:Normal Map:Create NormalMap") in mock_zbc.press.call_args_list
    assert mock_zbc.set.call_args_list[-4:] == [
        call("Tool:UV Map:UV Map Size", 2048.0),
        call("Tool:UV Map:UV Map Border", 4.0),
        call("Tool:Normal Map:SmoothUV", 0.0),
        call("Tool:Normal Map:Tangent", 0.0),
    ]


def test_create_wrinkle_brush_saves_non_empty_zbp_quietly(tmp_path) -> None:
    mod = _load_script("zbrush-brush", "wrinkle_brush.py")
    output = tmp_path / "WrinkleCrease.ZBP"
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.get.side_effect = lambda path: mod._SETTINGS[path]
    state: dict[str, str] = {}
    mock_zbc.set_next_filename.side_effect = lambda path: state.update(path=path)

    def press(item_path: str) -> None:
        if item_path == "Brush:Save As":
            Path(state["path"]).write_bytes(b"zbp")

    mock_zbc.press.side_effect = press
    with patch(
        "dcc_mcp_zbrush._skill_host.run_in_zbrush",
        lambda embedded, *_a, **_k: embedded(mock_zbc),
    ):
        result = mod.create_wrinkle_brush(output_path=str(output))

    assert result["success"] is True
    assert output.read_bytes() == b"zbp"
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]
    assert call("Brush:DamStandard") in mock_zbc.press.call_args_list
    assert call("Brush:Clone") in mock_zbc.press.call_args_list


def test_load_wrinkle_brush_reapplies_global_draw_settings(tmp_path) -> None:
    mod = _load_script("zbrush-brush", "wrinkle_brush.py")
    brush = tmp_path / "WrinkleCrease.ZBP"
    brush.write_bytes(b"zbp")
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    mock_zbc.get.side_effect = lambda path: mod._SETTINGS[path]
    with patch(
        "dcc_mcp_zbrush._skill_host.run_in_zbrush",
        lambda embedded, *_a, **_k: embedded(mock_zbc),
    ):
        result = mod.load_wrinkle_brush(brush_path=str(brush))

    assert result["success"] is True
    mock_zbc.set_next_filename.assert_called_once_with(str(brush))
    assert call("Brush:Load Brush") in mock_zbc.press.call_args_list
    assert call("Draw:Z Intensity", 18.0) in mock_zbc.set.call_args_list
    assert call("Draw:Focal Shift", -70.0) in mock_zbc.set.call_args_list


def test_capture_turntable_exports_frames_and_restores_transform(tmp_path) -> None:
    mod = _load_script("zbrush-viewport", "capture_turntable.py")
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    base_transform = [480.0, 360.0, 0.0, 300.0, 300.0, 300.0, 5.0, 10.0, 175.0]
    mock_zbc.get_transform.return_value = base_transform
    state: dict[str, str] = {}
    mock_zbc.set_next_filename.side_effect = lambda path: state.update(path=path)

    def press(item_path: str) -> None:
        if item_path == "Document:Export":
            Path(state["path"]).write_bytes(b"psd")

    mock_zbc.press.side_effect = press
    with patch(
        "dcc_mcp_zbrush._skill_host.run_in_zbrush",
        lambda embedded, *_a, **_k: embedded(mock_zbc),
    ):
        result = mod.capture_turntable(
            output_dir=str(tmp_path),
            angles=[0, 90],
            prefix="dragon",
            polyframe=True,
        )

    assert result["success"] is True
    assert [frame["angle"] for frame in result["context"]["frames"]] == [0.0, 90.0]
    assert (tmp_path / "dragon-000.psd").read_bytes() == b"psd"
    assert (tmp_path / "dragon-001.psd").read_bytes() == b"psd"
    assert mock_zbc.set_transform.call_args_list[-1] == call(*base_transform)
    assert [item.args[0] for item in mock_zbc.press_key.call_args_list] == ["SHIFT+F", "SHIFT+F"]
    assert all(callable(item.args[1]) for item in mock_zbc.press_key.call_args_list)
    assert mock_zbc.show_actions.call_args_list == [call(0), call(1)]


def test_capture_turntable_restores_polyframe_after_export_failure(tmp_path) -> None:
    mod = _load_script("zbrush-viewport", "capture_turntable.py")
    mock_zbc = MagicMock()
    mock_zbc.exists.return_value = True
    base_transform = [480.0, 360.0, 0.0, 300.0, 300.0, 300.0, 5.0, 10.0, 175.0]
    mock_zbc.get_transform.return_value = base_transform

    with pytest.raises(RuntimeError, match="did not export"):
        mod._capture(mock_zbc, str(tmp_path), [0.0], "dragon", False, True)

    assert mock_zbc.set_transform.call_args_list[-1] == call(*base_transform)
    assert [item.args[0] for item in mock_zbc.press_key.call_args_list] == ["SHIFT+F", "SHIFT+F"]
