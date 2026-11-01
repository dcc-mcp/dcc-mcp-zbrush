"""Self-generated protocol fixtures only; these are NOT ZBrush roundtrips."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "src/dcc_mcp_zbrush/goz_snapshot.py"
spec = importlib.util.spec_from_file_location("isolated_goz_snapshot", MODULE)
assert spec and spec.loader
goz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(goz)

HEADER = b"GoZb" + b"synthetic test fixture".ljust(28, b"\0")
POSITIONS = [(0.0, -0.0, 0.0), (1.25, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
FACES = [(0, 1, 2, 3), (0, 2, 3, -1)]
UVS = [(0, 0, 1, 0, 1, 1, 0, 1), (0.25, 0.5, 1, 1, 0, 1, -2, 3)]
IDENTITY = {"session": "session-1", "build": "2026.1.0", "tool": "tool-0", "index": 0, "host_id": 42}


def block(tag, count, payload=b"", modifier=0.0):
    return struct.pack("<IIIf", tag, 16 + len(payload), count, modifier) + payload


def string_block(tag, value, modifier=0.0):
    raw = value.encode("utf-8") if isinstance(value, str) else value
    payload = raw + b"\0"
    payload += b"\0" * (-len(payload) % 4)
    return block(tag, 1, payload, modifier)


def records(tag, fmt, rows):
    return block(tag, len(rows), b"".join(struct.pack("<" + fmt, *row) for row in rows))


def minimal(*extra, name="测试 mesh", positions=POSITIONS, faces=FACES, eof=None):
    return (
        HEADER
        + string_block(1, name)
        + records(10001, "3f", positions)
        + records(20001, "4i", faces)
        + b"".join(extra)
        + (bytes(16) if eof is None else eof)
    )


def complete():
    return minimal(
        block(5001, 1, struct.pack("<I", 0xFFFFFFFF)),
        records(25001, "8f", UVS),
        block(30002, 4, struct.pack("<4H", 0, 1, 32768, 65535)),
        block(35001, 4, struct.pack("<4I", 0xA1234567, 0, 0xFFFFFFFF, 0x12345678)),
        block(40001, 2, struct.pack("<2H", 0, 65535)),
        string_block(45001, "贴图/颜色.tif"),
        string_block(50001, "missing.tif"),
        string_block(55001, "displacement.tif", 0.125),
        block(80001, 0xFFFFFFFF, b"private unknown data"),
    )


def assert_error(data, code):
    with pytest.raises(goz.GoZError) as error:
        goz.parse_goz(data)
    assert error.value.code == code


def test_complete_fixture_preserves_stored_values_and_digests():
    data = complete()
    snapshot = goz.parse_goz(data, identity=IDENTITY, texture_inventory=["贴图\\颜色.tif"])
    assert snapshot["schema_version"] == 1
    assert snapshot["identity"] == IDENTITY
    assert snapshot["positions"] == [list(row) for row in POSITIONS]
    assert snapshot["faces"] == [list(row) for row in FACES]
    assert snapshot["uv4"] == [list(row) for row in UVS]  # retains triangle's fourth UV
    assert snapshot["mask"] == {"bits": 16, "values": [0, 1, 32768, 65535]}
    assert snapshot["polypaint_mrgb_u32"] == [0xA1234567, 0, 0xFFFFFFFF, 0x12345678]
    assert snapshot["polygroups_u16"] == [0, 65535]
    assert snapshot["flags"] == 0xFFFFFFFF  # no coordinate/UV transforms applied
    assert snapshot["maps"][0]["availability"] == "inventory_present"
    assert snapshot["maps"][1]["availability"] == "inventory_missing"
    assert snapshot["maps"][2]["modifier"] == 0.125
    assert snapshot["name"]["utf8_valid"] is True
    assert snapshot["source"]["sha256"] == hashlib.sha256(data).hexdigest()
    unknown = snapshot["blocks"][-2]
    assert unknown["tag"] == 80001 and unknown["decoded"] is False
    assert unknown["count"] == 0xFFFFFFFF
    assert unknown["sha256"] == hashlib.sha256(data[unknown["offset"] : -16]).hexdigest()
    assert "private unknown data" not in json.dumps(snapshot)
    assert "-0.0" in json.dumps(snapshot)


def test_versioned_json_schema_accepts_complete_and_minimal_snapshots():
    import jsonschema

    schema = json.loads((MODULE.parents[2] / "docs/goz-snapshot-v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    for data in (complete(), minimal()):
        jsonschema.validate(goz.parse_goz(data, identity=IDENTITY), schema)
        jsonschema.validate(goz.parse_goz(data), schema)


@pytest.mark.parametrize("eof", [bytes(16), block(0, 0)])
def test_documented_and_sdk_writer_eof_are_explicitly_supported(eof):
    assert goz.parse_goz(minimal(eof=eof))["blocks"][-1]["size"] in (0, 16)


def test_empty_mesh_and_optional_data_are_distinguished():
    snapshot = goz.parse_goz(minimal(positions=[], faces=[]))
    assert snapshot["positions"] == snapshot["faces"] == []
    assert snapshot["uv4"] is None and snapshot["mask"] is None
    assert snapshot["identity"] is None


def test_mask8_endpoints_are_not_inverted():
    snapshot = goz.parse_goz(minimal(block(30001, 4, bytes([0, 1, 128, 255]))))
    assert snapshot["mask"] == {"bits": 8, "values": [0, 1, 128, 255]}


@pytest.mark.parametrize(
    "tag,fmt,rows", [(25001, "8f", UVS[:1]), (30002, "H", [(0,)]), (35001, "I", [(0,)]), (40001, "H", [(1,)])]
)
def test_attribute_cardinality(tag, fmt, rows):
    assert_error(minimal(records(tag, fmt, rows)), "attribute_count_mismatch")


@pytest.mark.parametrize("face", [(0, 1, 4, -1), (-1, 1, 2, 3), (0, 1, 2, -2), (0, 1, 2, 4)])
def test_invalid_indices(face):
    assert_error(minimal(faces=[face]), "face_index_out_of_range")


@pytest.mark.parametrize("tag", [10002, 20002, 20003, 25002])
def test_unsupported_geometry_does_not_silently_succeed(tag):
    assert_error(minimal(block(tag, 0)), "unsupported_geometry_variant")


def test_every_truncation_of_complete_fixture_fails():
    data = complete()
    for end in range(len(data)):
        with pytest.raises(goz.GoZError):
            goz.parse_goz(data[:end])


@pytest.mark.parametrize(
    "tail,code",
    [
        (struct.pack("<4I", 123, 15, 0, 0), "invalid_block_size"),
        (struct.pack("<4I", 123, 0xFFFFFFFF, 0, 0), "truncated_block_payload"),
        (block(0, 1), "invalid_eof"),
        (block(0, 0, modifier=1), "invalid_eof"),
        (bytes(16) + b"x", "trailing_bytes"),
    ],
)
def test_invalid_frame_boundaries(tail, code):
    assert_error(minimal()[:-16] + tail, code)


def test_count_checked_before_unpacking():
    assert_error(HEADER + block(10001, 0xFFFFFFFF) + bytes(16), "item_limit_exceeded")
    assert_error(HEADER + block(10001, 2, bytes(12)) + bytes(16), "count_size_mismatch")


def test_limits_are_enforced(monkeypatch):
    monkeypatch.setattr(goz, "MAX_BYTES", 40)
    assert_error(minimal(), "byte_limit_exceeded")
    monkeypatch.setattr(goz, "MAX_BYTES", 16000)
    monkeypatch.setattr(goz, "MAX_BLOCKS", 4)
    assert_error(minimal(block(777, 0)), "block_limit_exceeded")


@pytest.mark.parametrize(
    "extra",
    [
        string_block(1, "duplicate"),
        records(10001, "3f", POSITIONS),
        block(30001, 4, bytes(4)) + block(30002, 4, bytes(8)),
    ],
)
def test_duplicate_semantic_blocks_fail(extra):
    assert_error(minimal(extra), "duplicate_semantic_block")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_geometry_uv_and_map_modifier(value):
    assert_error(minimal(positions=[(value, 0, 0)]), "nonfinite_numeric_value")
    assert_error(minimal(records(25001, "8f", [(value,) * 8] * 2)), "nonfinite_numeric_value")
    assert_error(minimal(string_block(55001, "map.tif", value)), "nonfinite_map_modifier")


def test_unknown_modifier_bits_are_preserved_without_json_nan():
    data = minimal(block(777, 1, b"secret", float("nan")))
    snapshot = goz.parse_goz(data)
    assert snapshot["blocks"][-2]["modifier_hex"] == "0000c07f"
    json.dumps(snapshot, allow_nan=False)


@pytest.mark.parametrize("payload", [b"abcd", b"a\0\0", b"a\0b\0", b"a\0" + bytes(6)])
def test_string_termination_and_padding(payload):
    assert_error(minimal(block(45001, 1, payload)), "invalid_string_layout")


def test_utf8_is_strict_but_raw_string_digest_survives():
    snapshot = goz.parse_goz(minimal(string_block(45001, b"bad\xff.tif"), name=b"\xf0\x9f"))
    assert snapshot["name"] == {
        "sha256": hashlib.sha256(b"\xf0\x9f").hexdigest(),
        "byte_length": 2,
        "utf8_valid": False,
    }
    assert snapshot["maps"][0]["availability"] == "unresolved_reference"
    assert snapshot["maps"][0]["reference"]["utf8_valid"] is False


@pytest.mark.parametrize(
    "reference",
    [
        "C:\\private\\秘密.tif",
        "/Users/private/秘密.tif",
        "\\\\server\\share\\秘密.tif",
        "../秘密.tif",
        "https://example.org/秘密.tif",
    ],
)
def test_paths_never_escape_in_snapshot_or_get_resolved(reference):
    data = minimal(string_block(45001, reference), block(777, 1, reference.encode()), name=reference)
    snapshot = goz.parse_goz(data, texture_inventory=[])
    assert snapshot["maps"][0]["availability"] == "unresolved_reference"
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "private" not in serialized and "秘密" not in serialized and "example.org" not in serialized


def test_inventory_is_exact_case_and_unicode_without_normalizing_names():
    snapshot = goz.parse_goz(minimal(string_block(45001, "café.tif")), texture_inventory=["cafe\u0301.tif"])
    assert snapshot["maps"][0]["availability"] == "inventory_missing"
    assert goz.parse_goz(minimal(string_block(45001, "valid.tif")))["maps"][0]["availability"] == "not_checked"
    with pytest.raises(goz.GoZError, match="invalid_texture_inventory"):
        goz.parse_goz(minimal(), texture_inventory=["/private/a.tif"])


def test_duplicate_host_id_keeps_distinct_scoped_locations():
    first = goz.parse_goz(minimal(), identity=IDENTITY)
    second = goz.parse_goz(minimal(), identity={**IDENTITY, "index": 1})
    third = goz.parse_goz(minimal(), identity={**IDENTITY, "session": "new-session"})
    assert goz.identity_conflicts([first, second, third]) == [{"kind": "duplicate_host_id", "first": 0, "second": 1}]
    assert goz.identity_conflicts([first, first])[-1]["kind"] == "duplicate_location"


@pytest.mark.parametrize(
    "change",
    [{"tool": "C:\\private\\tool"}, {"session": "../session"}, {"index": True}, {"host_id": -1}, {"extra": "bad"}],
)
def test_identity_rejects_paths_and_invalid_fields(change):
    with pytest.raises(goz.GoZError):
        goz.parse_goz(minimal(), identity={**IDENTITY, **change})


def test_comparison_detects_attribute_unknown_and_signed_zero_changes():
    first = goz.parse_goz(minimal(block(777, 0, b"first")))
    second = goz.parse_goz(minimal(block(777, 0, b"other")))
    assert goz.compare_snapshots(first, first)["source_bytes_equal"] is True
    assert goz.compare_snapshots(first, second)["changed_fields"] == ["blocks"]
    changed_positions = [(0.0, 0.0, 0.0)] + POSITIONS[1:]
    third = goz.parse_goz(minimal(block(777, 0, b"first"), positions=changed_positions))
    assert "positions" in goz.compare_snapshots(first, third)["changed_fields"]
    with pytest.raises(goz.GoZError, match="unsupported_snapshot_version"):
        goz.compare_snapshots(first, {**second, "schema_version": 2})


def test_file_is_read_only_and_errors_do_not_expose_paths(tmp_path):
    file = tmp_path / "秘密.GoZ"
    data = complete()
    file.write_bytes(data)
    before = file.stat().st_mtime_ns
    assert goz.read_goz(file)["source"]["byte_length"] == len(data)
    assert file.read_bytes() == data and file.stat().st_mtime_ns == before
    with pytest.raises(goz.GoZError) as error:
        goz.read_goz(tmp_path / "private-missing.GoZ")
    assert str(error.value) == "input_read_failed"


def test_direct_cli_needs_no_adapter_core_or_zbrush_import(tmp_path):
    file = tmp_path / "synthetic.GoZ"
    file.write_bytes(minimal())
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(MODULE), str(file)], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout)["validation_scope"] == "stored_values_only"
    assert result.stderr == ""
    failed = subprocess.run(
        [sys.executable, "-I", "-S", str(MODULE), str(tmp_path / "private-missing")], capture_output=True, text=True
    )
    assert failed.returncode == 2
    assert json.loads(failed.stderr) == {"error": "input_read_failed", "offset": None}
    assert "private-missing" not in failed.stderr
