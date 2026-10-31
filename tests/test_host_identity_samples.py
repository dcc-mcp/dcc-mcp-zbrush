"""Source-labelled, table-driven host-identity samples (anti-recurrence for #103/#106).

Two *distinct* verify-rollback bugs shared one ancestor: pre-#104 and #104 both
fed the SDK ``zbrush_version`` through ``_parse_host_version`` (the signed-
ProductVersion parser). The triggers differ:

* #103 (fixed by #104 / 2f07e84): the OLD formula folded the edition enum
  ``zbrush_info(1)`` (2 = Full) into the version *minor* -> "2027.2".
* #106 (fixed by b89efc2 + this change): ``format_sdk_version``'s "%.3f"
  renders a small SDK float such as 2027.001 as the string "2027.001"; feeding
  that float string through the ProductVersion parser (leading-zero component)
  rejected it -> rollback.

Sample provenance is explicit so a future shape change is caught at its source:
* ``zbrush_info(0)`` REAL PC8 2026.2.0 value = 2026.199951171875 (float32 of
  2026.2), captured in live_sdk_values.json / verify_live_binding.json.
* #106 reporter (ZBrush 2027.0.1) session = "2027.001" (SDK float fraction).
* SDK docs / #104 examples = 2026.0, 2027.0, 2028.25.
* signed Windows ProductVersion = full precision "2026, 2, 0, 1" (real PC8) and
  "2027, 0, 1, 1" (#106 reporter).

Unknown-format SDK strings are refused fail-closed, with a bounded, control-
char-free (desensitized) diagnostic preserved in the log. The SDK edition is
never a version component, and PID/start/provenance/Authenticode/SHA-256
bindings in ``_validate_sidecar_session`` are exercised, not bypassed.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import pytest

import dcc_mcp_zbrush.install_lifecycle as lifecycle
from dcc_mcp_zbrush._version_probe import format_sdk_version, read_sdk_version

# --- source-labelled SDK numeric value shapes --------------------------------
# (source, zbrush_info(0) float, format_sdk_version output, signed ProductVersion)
SDK_SAMPLES = [
    ("PC8 real 2026.2.0 live zbrush_info(0) float32", 2026.199951171875, "2026.2", "2026, 2, 0, 1"),
    ("GH-106 reporter 2027.0.1 session float", 2027.001, "2027.001", "2027, 0, 1, 1"),
    ("official SDK example 2026.0", 2026.0, "2026.0", "2026, 0, 0, 0"),
    ("official SDK example 2027.0", 2027.0, "2027.0", "2027, 0, 0, 0"),
    ("#104 regression sample 2028.25", 2028.25, "2028.25", "2028, 1, 0, 0"),
    ("integer SDK value 2027", 2027, "2027.0", "2027, 0, 0, 0"),
]

# edition is zbrush_info(1): 0=Demo, 1=Beta, 2=Full. It is NEVER a version part.
EDITIONS = (0, 1, 2, -1)


class _FakeZbc:
    def __init__(self, version: float, edition: float) -> None:
        self._version = version
        self._edition = edition
        self.__file__ = "C:/ZBrush/Python/zbrush/commands.pyd"

    def zbrush_info(self, index: int) -> float:
        return {0: self._version, 1: self._edition}[index]


class TestSourceLabelledSdkShapes:
    @pytest.mark.parametrize(("source", "sdk_float", "expected", "product_version"), SDK_SAMPLES)
    def test_format_sdk_version_real_shape(
        self, source: str, sdk_float: float, expected: str, product_version: str
    ) -> None:
        # Each sample names its provenance; a change to "%.3f" would break here.
        assert format_sdk_version(sdk_float) == expected, source

    @pytest.mark.parametrize(("source", "sdk_float", "expected", "product_version"), SDK_SAMPLES)
    @pytest.mark.parametrize("edition", EDITIONS)
    def test_edition_never_changes_version(
        self, source: str, sdk_float: float, expected: str, product_version: str, edition: int
    ) -> None:
        version_text, reported_edition = read_sdk_version(_FakeZbc(sdk_float, float(edition)))
        assert version_text == expected, source
        assert reported_edition == edition, source

    @pytest.mark.parametrize(("source", "sdk_float", "expected", "product_version"), SDK_SAMPLES)
    def test_safe_host_identity_with_real_samples(
        self, source: str, sdk_float: float, expected: str, product_version: str
    ) -> None:
        # Full chain: SDK float -> formatted string -> session -> verify gate.
        session = {
            "zbrush_version": format_sdk_version(sdk_float),
            "zbrush_edition": 2,
            "zbrush_product_version": product_version,  # authoritative full precision
        }
        receipt = {"host_version": product_version}
        assert lifecycle._host_version_is_consistent(session, receipt) is True, source

    @pytest.mark.parametrize(("source", "sdk_float", "expected", "product_version"), SDK_SAMPLES)
    def test_safe_host_identity_without_authoritative_product_version(
        self, source: str, sdk_float: float, expected: str, product_version: str
    ) -> None:
        session = {"zbrush_version": format_sdk_version(sdk_float), "zbrush_edition": 2, "zbrush_product_version": ""}
        receipt = {"host_version": product_version}
        assert lifecycle._host_version_is_consistent(session, receipt) is True, source


class TestSignedProductVersionFullPrecision:
    """The signed ProductVersion keeps its strict, full-precision canonical parser."""

    GOOD_PRODUCTS = ("2026, 2, 0, 1", "2027, 0, 1, 1", "2026.2.0.1")

    @pytest.mark.parametrize("product", GOOD_PRODUCTS)
    def test_full_precision_parsed(self, product: str) -> None:
        assert lifecycle._parse_host_version(product)[0] in (2026, 2027)

    @pytest.mark.parametrize("bad", ["2027.001", "2026, 2, 0, 1, 1", "02027.0", "2026, 2, 0, 1, 1, 1"])
    def test_product_version_parser_stays_strict(self, bad: str) -> None:
        with pytest.raises(lifecycle.LifecycleFailure):
            lifecycle._parse_host_version(bad)

    def test_tampered_full_precision_product_version_rejected(self) -> None:
        session = {
            "zbrush_version": "2026.2",
            "zbrush_edition": 2,
            "zbrush_product_version": "2026, 2, 0, 2",  # tampered build component
        }
        receipt = {"host_version": "2026, 2, 0, 1"}
        assert lifecycle._host_version_is_consistent(session, receipt) is False


class TestRootCauseReconstruction:
    """Reproduce BOTH distinct bugs from source; do NOT assume one root cause."""

    def test_issue_103_edition_as_minor(self) -> None:
        # OLD (pre-2f07e84) formula: major=int(zbrush_info(0)), minor=int(zbrush_info(1)).
        zbc = _FakeZbc(2027.0, 2.0)  # ZBrush 2027.0.1, Full edition
        old_version = f"{int(zbc.zbrush_info(0))}.{int(zbc.zbrush_info(1))}"
        assert old_version == "2027.2"  # edition 2 became the minor
        fixed_version, edition = read_sdk_version(zbc)
        assert fixed_version == "2027.0" and edition == 2
        # Old value fails the two-component equality; fixed value passes.
        assert lifecycle._parse_host_version(old_version)[:2] != lifecycle._parse_host_version("2027.0.1")[:2]
        assert (
            lifecycle._host_version_is_consistent(
                {"zbrush_version": fixed_version, "zbrush_product_version": "2027, 0, 1, 1"},
                {"host_version": "2027, 0, 1, 1"},
            )
            is True
        )

    def test_issue_106_sdk_float_string_through_product_version_parser(self) -> None:
        # The #106 trigger: a valid SDK float string with a leading-zero fraction.
        sdk_string = format_sdk_version(2027.001)
        assert sdk_string == "2027.001"
        # The ProductVersion parser (wrong parser for this value) rejects it:
        with pytest.raises(lifecycle.LifecycleFailure):
            lifecycle._parse_host_version(sdk_string)
        # The dedicated SDK major parser accepts it and extracts only the year:
        assert lifecycle._parse_sdk_version_major(sdk_string) == 2027
        # Full gate is consistent for the reporter's 2027.0.1 host:
        assert (
            lifecycle._host_version_is_consistent(
                {"zbrush_version": sdk_string, "zbrush_edition": 2, "zbrush_product_version": "2027, 0, 1, 1"},
                {"host_version": "2027, 0, 1, 1"},
            )
            is True
        )

    def test_two_bugs_have_distinct_triggers(self) -> None:
        # #103 needs the edition enum to be non-zero/minor; #106 needs a float
        # fraction with a leading zero. They are NOT the same defect.
        assert format_sdk_version(2027.0) == "2027.0"  # no leading-zero fraction
        assert lifecycle._parse_host_version("2027.0") == (2027, 0)  # old parser OK here
        assert lifecycle._parse_sdk_version_major("2027.001") == 2027  # #106 shape


class TestUnknownFormatDiagnostics:
    """Unknown SDK formats: fail closed + preserve a desensitized diagnostic."""

    UNKNOWN = [
        "",
        "unknown",
        "2027.001abc",
        "2026, 2, 0, 1",
        "99999999.0",
        "2027/0/1",
        "02027.0",
    ]

    @pytest.mark.parametrize("bad", UNKNOWN)
    def test_unknown_format_is_refused(self, bad: str) -> None:
        receipt = {"host_version": "2026, 2, 0, 1"}
        session = {"zbrush_version": bad, "zbrush_product_version": ""}
        assert lifecycle._host_version_is_consistent(session, receipt) is False

    def test_redacted_diagnostic_is_bounded_and_control_char_free(self) -> None:
        raw = "2027.00\x00\x07\x1b[31m" + "A" * 200
        red = lifecycle._redact_version_diagnostic(raw)
        assert len(red) <= 48
        assert all(ch.isprintable() for ch in red)
        assert "\\x00" in red  # control bytes escaped, not emitted raw
        assert "A" * 60 not in red  # unbounded payload not leaked

    def test_unknown_format_logs_desensitized_diagnostic(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="dcc_mcp_zbrush.install_lifecycle"):
            result = lifecycle._host_version_is_consistent(
                {"zbrush_version": "2027.00\x00BAD", "zbrush_product_version": ""},
                {"host_version": "2026, 2, 0, 1"},
            )
        assert result is False  # fail-closed, not blindly accepted
        assert any("host_identity" in rec.message or "host_identity" in (rec.getMessage()) for rec in caplog.records)
        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert "\x00" not in joined  # no raw control byte in the log
        assert "\\x00" in joined  # desensitized diagnostic preserved

    def test_multi_dot_sdk_value_is_not_blindly_accepted(self) -> None:
        # A ProductVersion-like value mislabeled as the SDK version must be
        # refused, not reduced to its major (format_sdk_version emits <=1 dot).
        with pytest.raises(lifecycle.LifecycleFailure):
            lifecycle._parse_sdk_version_major("2026.2.0.1")


class TestReal2026HostGateBindingsIntact:
    """Exercise _validate_sidecar_session with REAL 2026.2.0 values; confirm
    PID/start/provenance/SHA-256 bindings still guard the version gate."""

    @staticmethod
    def _fixture(tmp_path: Path) -> tuple[Any, Path, dict, dict]:
        dcc_root = tmp_path / "product"
        (dcc_root / "Python" / "zbrush").mkdir(parents=True)
        dcc_exe = dcc_root / "ZBrush_.exe"
        dcc_exe.write_bytes(b"signed-zbrush-bytes")
        dcc_sha = hashlib.sha256(dcc_exe.read_bytes()).hexdigest()
        commands_pyd = dcc_root / "Python" / "zbrush" / "commands.pyd"
        commands_pyd.write_bytes(b"native-zbrush-sdk")

        asset_dir = tmp_path / "assets"
        bridge_path = asset_dir / "Python" / "dcc_mcp_zbrush_socket_bridge.py"
        bridge_path.parent.mkdir(parents=True)
        bridge_path.write_bytes(b"bridge-bytes")
        bridge_sha = hashlib.sha256(bridge_path.read_bytes()).hexdigest()

        receipt = {
            "install_id": "inst-5123",
            "version": "0.2.33",
            "dcc_path": str(dcc_exe),
            "dcc_root": str(dcc_root),
            "dcc_sha256": dcc_sha,
            "socket_endpoint": "127.0.0.1:9876",
            "host_version": "2026, 2, 0, 1",  # real PC8 full precision
            "managed_files": [{"path": "Python/dcc_mcp_zbrush_socket_bridge.py", "installed_sha256": bridge_sha}],
        }
        session = {
            "install_id": "inst-5123",
            "bridge_instance_id": "11111111-1111-1111-1111-111111111111",
            "pid": 4242,
            "socket_endpoint": "127.0.0.1:9876",
            "adapter_version": "0.2.33",
            "selected_dcc_path": str(dcc_exe),
            "selected_dcc_sha256": dcc_sha,
            "zbrush_version": "2026.2",  # real format_sdk_version(2026.199951171875)
            "zbrush_edition": 2,
            "zbrush_product_version": "2026, 2, 0, 1",
            "bridge_module_path": str(bridge_path),
            "bridge_module_sha256": bridge_sha,
            "zbrush_commands_origin": str(commands_pyd),
        }
        request = lifecycle.LifecycleRequest(
            operation="verify", socket_host="127.0.0.1", socket_port=9876, asset_dir=asset_dir
        )
        return request, asset_dir, receipt, session

    def test_real_2026_2_gate_passes_all_bindings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        request, asset_dir, receipt, session = self._fixture(tmp_path)
        monkeypatch.setattr(lifecycle, "_process_executable", lambda _pid: Path(receipt["dcc_path"]))
        monkeypatch.setattr(lifecycle, "_process_start_identity", lambda _pid: "start-4242")
        identity = lifecycle._validate_sidecar_session(request, asset_dir, receipt, session)
        assert identity["pid"] == 4242
        assert identity["process_start_identity"] == "start-4242"
        assert identity["zbrush_version"] == "2026.2"

    @pytest.mark.parametrize(
        "mutate_key",
        ["pid", "selected_dcc_sha256", "bridge_module_sha256", "install_id", "adapter_version"],
    )
    def test_each_binding_still_rejects_tampering(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate_key: str
    ) -> None:
        request, asset_dir, receipt, session = self._fixture(tmp_path)
        monkeypatch.setattr(lifecycle, "_process_executable", lambda _pid: Path(receipt["dcc_path"]))
        monkeypatch.setattr(lifecycle, "_process_start_identity", lambda _pid: "start-4242")
        session = dict(session)
        session[mutate_key] = "tampered"
        with pytest.raises(lifecycle.LifecycleFailure):
            lifecycle._validate_sidecar_session(request, asset_dir, receipt, session)

    def test_unknown_sdk_format_refused_by_full_gate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        request, asset_dir, receipt, session = self._fixture(tmp_path)
        monkeypatch.setattr(lifecycle, "_process_executable", lambda _pid: Path(receipt["dcc_path"]))
        monkeypatch.setattr(lifecycle, "_process_start_identity", lambda _pid: "start-4242")
        session = dict(session)
        session["zbrush_version"] = "2026, 2, 0, 1"  # ProductVersion mislabeled as SDK
        with pytest.raises(lifecycle.LifecycleFailure):
            lifecycle._validate_sidecar_session(request, asset_dir, receipt, session)
