"""Regression tests for ZBrush SDK edition/version identity (GH-103).

The official ZBrush Python SDK (zbr_cmds_system.html) specifies:

* ``zbrush_info(0)`` -> ZBrush version number (float, e.g. ``2027.0``)
* ``zbrush_info(1)`` -> edition type (float): 0=Demo, 1=Beta, 2=Full Version

Index 1 is **not** a minor version component. Treating it as one reported a
Full-edition ZBrush 2027.0.1 as ``"2027.2"`` and made ``host_identity`` verify
roll back a legitimate, correctly-signed host.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

import dcc_mcp_zbrush.install_lifecycle as lifecycle
from dcc_mcp_zbrush._version_probe import format_sdk_version, read_sdk_version

_REPO_ROOT = Path(__file__).parent.parent


class _FakeZbc:
    """Minimal stand-in for ``zbrush.commands`` with the official info shape."""

    def __init__(self, version: float, edition: float) -> None:
        self._version = version
        self._edition = edition
        self.__file__ = "C:/ZBrush/Python/zbrush/commands.pyd"

    def zbrush_info(self, index: int) -> float:
        return {0: self._version, 1: self._edition}[index]

    def get_active_tool_path(self) -> str:
        return ""

    def get_subtool_count(self) -> int:
        return 0


def _load_bridge() -> ModuleType:
    path = _REPO_ROOT / "bridge" / "plugin" / "mcp_socket_bridge.py"
    spec = importlib.util.spec_from_file_location("gh103_mcp_socket_bridge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_get_session_info() -> ModuleType:
    path = _REPO_ROOT / "src" / "dcc_mcp_zbrush" / "skills" / "zbrush-scripting" / "scripts" / "get_session_info.py"
    spec = importlib.util.spec_from_file_location("gh103_get_session_info", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestFormatSdkVersion:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (2027.0, "2027.0"),
            (2026.1, "2026.1"),
            (2027, "2027.0"),
            (2028.25, "2028.25"),
        ],
    )
    def test_preserves_precision_with_two_components(self, value: float, expected: str) -> None:
        assert format_sdk_version(value) == expected


class TestReadSdkVersion:
    @pytest.mark.parametrize(
        ("version", "edition", "expected_version", "expected_edition"),
        [
            (2027.0, 2.0, "2027.0", 2),  # GH-103 Full edition, ZBrush 2027.0.1
            (2026.0, 2.0, "2026.0", 2),
            (2026.0, 0.0, "2026.0", 0),  # Demo
            (2026.0, 1.0, "2026.0", 1),  # Beta
            (2026.1, 2.0, "2026.1", 2),
        ],
    )
    def test_edition_is_kept_separate(
        self, version: float, edition: float, expected_version: str, expected_edition: int
    ) -> None:
        version_text, reported_edition = read_sdk_version(_FakeZbc(version, edition))
        assert version_text == expected_version
        assert reported_edition == expected_edition

    def test_version_depends_only_on_the_version_number(self) -> None:
        # For a fixed version, every edition must yield the identical version string.
        versions = {read_sdk_version(_FakeZbc(2027.0, edition))[0] for edition in (0.0, 1.0, 2.0)}
        assert versions == {"2027.0"}


def test_issue_103_old_formula_produced_edition_as_minor() -> None:
    """Reproduce GH-103: the old formula turned a Full edition into version minor 2."""
    version = 2027.0
    edition = 2.0  # Full
    old_zbrush_version = f"{int(version)}.{int(edition)}"  # the buggy formula
    assert old_zbrush_version == "2027.2"
    # The old verify compared the first two components against the exe ProductVersion.
    runtime = lifecycle._parse_host_version(old_zbrush_version)
    product = lifecycle._parse_host_version("2027.0.1")
    assert runtime[:2] != product[:2]  # -> host_identity rollback (exit 40)


class TestHostVersionIsConsistent:
    @pytest.mark.parametrize(
        ("product_version", "sdk_version", "edition"),
        [
            ("2026.1.0.0", "2026.0", 2),
            ("2026.2.1.1", "2026.0", 1),
            ("2027.0.1", "2027.0", 2),  # GH-103
            ("2028.3.0.0", "2028.0", 0),
            ("2026, 2, 1, 1", "2026.0", 2),  # Windows comma-separated ProductVersion
        ],
    )
    def test_legitimate_hosts_pass(self, product_version: str, sdk_version: str, edition: int) -> None:
        receipt = {"host_version": product_version}
        session = {
            "zbrush_version": sdk_version,
            "zbrush_edition": edition,
            "zbrush_product_version": product_version,
        }
        assert lifecycle._host_version_is_consistent(session, receipt) is True

    def test_every_edition_passes_for_the_same_product(self) -> None:
        receipt = {"host_version": "2027.0.1"}
        for edition in (0, 1, 2):
            session = {"zbrush_version": "2027.0", "zbrush_edition": edition, "zbrush_product_version": "2027.0.1"}
            assert lifecycle._host_version_is_consistent(session, receipt) is True

    def test_different_major_is_rejected(self) -> None:
        receipt = {"host_version": "2027.0.1"}
        session = {"zbrush_version": "2026.0", "zbrush_product_version": "2027.0.1"}
        assert lifecycle._host_version_is_consistent(session, receipt) is False

    def test_tampered_product_version_is_rejected(self) -> None:
        receipt = {"host_version": "2027.0.1"}
        session = {"zbrush_version": "2027.0", "zbrush_product_version": "2099.0.0"}
        assert lifecycle._host_version_is_consistent(session, receipt) is False

    def test_legacy_buggy_session_normalized_by_product_version(self) -> None:
        # A legacy host still reporting "MAJOR.EDITION" no longer rolls back when
        # the authoritative signed-executable ProductVersion is present.
        receipt = {"host_version": "2027.0.1"}
        session = {"zbrush_version": "2027.2", "zbrush_product_version": "2027.0.1"}
        assert lifecycle._host_version_is_consistent(session, receipt) is True

    def test_legacy_session_without_product_version_uses_normalized_major(self) -> None:
        receipt = {"host_version": "2026.1.0.0"}
        session = {"zbrush_version": "2026.0"}  # coarse SDK value, no product version
        assert lifecycle._host_version_is_consistent(session, receipt) is True

    def test_missing_or_unbounded_version_is_rejected(self) -> None:
        receipt = {"host_version": "2027.0.1"}
        assert lifecycle._host_version_is_consistent({"zbrush_version": ""}, receipt) is False


class TestBridgeSessionInfo:
    def test_keeps_edition_out_of_version(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bridge = _load_bridge()
        # Stage a realistic install identity file next to a repointed bridge __file__.
        bridge_file = tmp_path / "dcc_mcp_zbrush_socket_bridge.py"
        bridge_file.write_text("# bridge placeholder\n", encoding="utf-8")
        identity = {
            "adapter_version": "0.2.31",
            "dcc_path": "C:/ZBrush 2027/ZBrush.exe",
            "dcc_sha256": "deadbeef",
            "install_id": "inst-1",
            "socket_endpoint": "127.0.0.1:9876",
            "zbrush_version": "2027.0.1",
        }
        (tmp_path / "dcc_mcp_zbrush_install_identity.json").write_text(json.dumps(identity), encoding="utf-8")
        monkeypatch.setattr(bridge, "__file__", str(bridge_file), raising=False)
        monkeypatch.setattr(bridge, "_import_zbc", lambda: _FakeZbc(2027.0, 2.0))

        session = bridge._get_session_info()

        assert session["zbrush_version"] == "2027.0"  # SDK version value only
        assert session["zbrush_edition"] == 2  # edition kept separate
        assert session["zbrush_product_version"] == "2027.0.1"  # signed exe ProductVersion
        assert "2027.2" not in json.dumps(session)  # GH-103 regression

    def test_fallback_server_info_labels_edition(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bridge = _load_bridge()
        zbrush_pkg = ModuleType("zbrush")
        commands = _FakeZbc(2027.0, 2.0)
        monkeypatch.setitem(sys.modules, "zbrush", zbrush_pkg)
        monkeypatch.setitem(sys.modules, "zbrush.commands", MagicMock(wraps=commands))
        captured: dict = {}
        monkeypatch.setattr(bridge, "_show_message", lambda title, msg: captured.update(title=title, msg=msg))

        bridge._fallback_server_info()

        assert "ZBrush 2027.0" in captured["msg"]
        assert "Full edition" in captured["msg"]
        assert "2027.2" not in captured["msg"]


class TestEmbeddedSessionInfo:
    def test_keeps_edition_out_of_version(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_get_session_info()
        identity = {
            "install_id": "inst-1",
            "dcc_path": "C:/ZBrush 2027/ZBrush.exe",
            "dcc_sha256": "deadbeef",
            "zbrush_version": "2027.0.1",
        }
        identity_file = tmp_path / "identity.json"
        identity_file.write_text(json.dumps(identity), encoding="utf-8")
        monkeypatch.setenv("DCC_MCP_ZBRUSH_INSTALL_IDENTITY", str(identity_file))

        info = module._collect(_FakeZbc(2027.0, 2.0))

        assert info["zbrush_version"] == "2027.0"
        assert info["zbrush_edition"] == 2
        assert info["zbrush_product_version"] == "2027.0.1"
        assert "2027.2" not in json.dumps(info)


def test_get_zbrush_version_string_is_edition_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    import dcc_mcp_zbrush._version_probe as probe

    zbrush_pkg = ModuleType("zbrush")
    commands = _FakeZbc(2027.0, 2.0)
    monkeypatch.setitem(sys.modules, "zbrush", zbrush_pkg)
    monkeypatch.setitem(sys.modules, "zbrush.commands", MagicMock(wraps=commands))

    assert probe.get_zbrush_version_string() == "2027.0"
    assert probe.get_zbrush_edition() == 2
    assert probe.get_zbrush_version_tuple() == (2027, 0)
