"""Tests for the OBJ face-size preflight (no live ZBrush required)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPORTER_PENTAGON = "v 0 1 0\nv 0.95 0.31 0\nv 0.59 -0.81 0\nv -0.59 -0.81 0\nv -0.95 0.31 0\nf 1 2 3 4 5\n"


def _load_bridge_plugin() -> ModuleType:
    path = Path(__file__).parent.parent / "bridge" / "plugin" / "mcp_socket_bridge.py"
    spec = importlib.util.spec_from_file_location("mcp_socket_bridge_preflight", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bridge():
    return _load_bridge_plugin()


def _adapter_scan(path, **kwargs):
    from dcc_mcp_zbrush._obj_preflight import scan_obj_oversized_faces

    return scan_obj_oversized_faces(str(path), **kwargs)


def _bridge_scan(bridge, path, **kwargs):
    return bridge._scan_obj_oversized_faces(str(path), **kwargs)


def test_reporter_pentagon_is_detected(tmp_path, bridge):
    """The exact repro from the issue: a single five-vertex face."""
    obj = tmp_path / "pentagon.obj"
    obj.write_text(_REPORTER_PENTAGON, encoding="utf-8")

    for result in (_adapter_scan(obj), _bridge_scan(bridge, obj)):
        assert result["ok"] is True
        assert result["faces_checked"] == 1
        assert result["oversized"] == [{"line": 6, "vertex_count": 5}]
        assert result["truncated"] is False


def test_quad_only_mesh_passes(tmp_path, bridge):
    obj = tmp_path / "quad.obj"
    obj.write_text(
        "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n",
        encoding="utf-8",
    )

    for result in (_adapter_scan(obj), _bridge_scan(bridge, obj)):
        assert result["ok"] is True
        assert result["oversized"] == []
        assert result["truncated"] is False


def test_triangle_only_mesh_passes(tmp_path, bridge):
    obj = tmp_path / "tri.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n", encoding="utf-8")

    for result in (_adapter_scan(obj), _bridge_scan(bridge, obj)):
        assert result["ok"] is True
        assert result["oversized"] == []


def test_vertex_texture_normal_triplets_count_as_vertices(tmp_path, bridge):
    obj = tmp_path / "triplet.obj"
    obj.write_text("f 1/1/1 2/2/2 3/3/3 4/4/4 5/5/5\n", encoding="utf-8")

    for result in (_adapter_scan(obj), _bridge_scan(bridge, obj)):
        assert result["oversized"] == [{"line": 1, "vertex_count": 5}]


def test_negative_indices_are_counted(tmp_path, bridge):
    obj = tmp_path / "negative.obj"
    obj.write_text("f -1 -2 -3 -4 -5\n", encoding="utf-8")

    for result in (_adapter_scan(obj), _bridge_scan(bridge, obj)):
        assert result["oversized"] == [{"line": 1, "vertex_count": 5}]


def test_non_face_records_are_ignored(tmp_path, bridge):
    obj = tmp_path / "mixed.obj"
    obj.write_text(
        "# a comment\n"
        "\n"
        "v 0 0 0\n"
        "vt 0 0\n"
        "vn 0 0 1\n"
        "usemtl default\n"
        "f 1 2 3 4 5  # pentagon: this note must not be counted\n",
        encoding="utf-8",
    )

    for result in (_adapter_scan(obj), _bridge_scan(bridge, obj)):
        assert result["faces_checked"] == 1
        assert result["oversized"] == [{"line": 7, "vertex_count": 5}]


def test_crlf_line_endings_are_handled(tmp_path, bridge):
    obj = tmp_path / "crlf.obj"
    obj.write_bytes(_REPORTER_PENTAGON.replace("\n", "\r\n").encode("utf-8"))

    for result in (_adapter_scan(obj), _bridge_scan(bridge, obj)):
        assert result["oversized"] == [{"line": 6, "vertex_count": 5}]


def test_report_is_truncated_at_max_faces(tmp_path, bridge):
    obj = tmp_path / "many.obj"
    obj.write_text("".join("f 1 2 3 4 5\n" for _ in range(12)), encoding="utf-8")

    for result in (_adapter_scan(obj), _bridge_scan(bridge, obj)):
        assert len(result["oversized"]) == 8
        assert result["truncated"] is True
        assert result["ok"] is True


def test_oversized_file_is_refused(tmp_path, bridge):
    obj = tmp_path / "huge.obj"
    obj.write_text("f 1 2 3 4\n", encoding="utf-8")

    for result in (
        _adapter_scan(obj, max_bytes=1),
        _bridge_scan(bridge, obj, max_bytes=1),
    ):
        assert result["ok"] is False
        assert result["reason"] == "FILE_TOO_LARGE"


def test_missing_file_is_unreadable(tmp_path, bridge):
    missing = tmp_path / "does-not-exist.obj"

    for result in (_adapter_scan(missing), _bridge_scan(bridge, missing)):
        assert result["ok"] is False
        assert result["reason"] == "UNREADABLE"


def test_directory_path_is_unreadable(tmp_path, bridge):
    for result in (_adapter_scan(tmp_path), _bridge_scan(bridge, tmp_path)):
        assert result["ok"] is False
        assert result["reason"] == "UNREADABLE"


def test_non_utf8_file_is_unreadable(tmp_path, bridge):
    obj = tmp_path / "binary.obj"
    obj.write_bytes(b"f 1 2 3 \xff\xfe 5\n")

    for result in (_adapter_scan(obj), _bridge_scan(bridge, obj)):
        assert result["ok"] is False
        assert result["reason"] == "UNREADABLE"


def test_both_copies_agree_across_fixtures(tmp_path, bridge):
    """The vendored bridge copy must not drift from the adapter module."""
    fixtures = {
        "pentagon": _REPORTER_PENTAGON,
        "quad": "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n",
        "triplet": "f 1/1/1 2/2/2 3/3/3 4/4/4 5/5/5\n",
        "empty": "",
        "comments": "# only a comment\n",
    }
    for name, body in fixtures.items():
        obj = tmp_path / f"{name}.obj"
        obj.write_text(body, encoding="utf-8")
        assert _adapter_scan(obj) == _bridge_scan(bridge, obj), name


class TestErrorPayloads:
    def test_preflight_error_names_the_offending_faces(self):
        from dcc_mcp_zbrush._obj_preflight import obj_preflight_error

        payload = obj_preflight_error(
            {
                "ok": True,
                "oversized": [{"line": 6, "vertex_count": 5}],
                "truncated": False,
            }
        )

        assert payload["success"] is False
        assert payload["error"] == "OBJ_FACE_TOO_LARGE"
        assert "1 face with more than 4 vertices" in payload["message"]
        assert payload["imported_nodes"] == []

    def test_preflight_error_pluralises_multiple_faces(self):
        from dcc_mcp_zbrush._obj_preflight import obj_preflight_error

        payload = obj_preflight_error({"ok": True, "oversized": [{"line": 1}, {"line": 2}]})

        assert "2 faces with more than 4 vertices" in payload["message"]

    def test_unverifiable_error_is_fail_closed(self):
        from dcc_mcp_zbrush._obj_preflight import obj_preflight_unverifiable_error

        payload = obj_preflight_unverifiable_error({"ok": False, "reason": "UNREADABLE"})

        assert payload["error"] == "OBJ_PREFLIGHT_FAILED"
        assert "UNREADABLE" in payload["message"]
