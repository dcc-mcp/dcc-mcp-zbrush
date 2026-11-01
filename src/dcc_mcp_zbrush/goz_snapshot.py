"""Bounded, read-only GoZ snapshot candidate; no host or network dependencies.

Run this file directly to avoid the adapter package's runtime imports.
See docs/goz-snapshot.md for the versioned contract and evidence limits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_BYTES = 16 * 1024 * 1024
MAX_ITEMS = 100_000
MAX_BLOCKS = 256
_ARRAYS = {
    10001: ("positions", "3f"),
    20001: ("faces", "4i"),
    25001: ("uv4", "8f"),
    30001: ("mask", "B"),
    30002: ("mask", "H"),
    35001: ("polypaint_mrgb_u32", "I"),
    40001: ("polygroups_u16", "H"),
}
_MAPS = {45001: "color", 50001: "normal", 55001: "displacement"}
_OTHER_GEOMETRY = {10002, 20002, 20003, 25002}
_TOKEN = re.compile(r"[A-Za-z0-9_.+-]{1,64}\Z")


class GoZError(ValueError):
    """A path-free validation error with an optional byte offset."""

    def __init__(self, code: str, offset: int | None = None):
        self.code = code
        self.offset = offset
        super().__init__(code if offset is None else f"{code} at byte {offset}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if set(value) != {"session", "build", "tool", "index", "host_id"}:
        raise GoZError("invalid_identity_fields")
    for key in ("session", "build", "tool"):
        if not isinstance(value[key], str) or not _TOKEN.fullmatch(value[key]):
            raise GoZError("identity_requires_opaque_tokens")
    for key in ("index", "host_id"):
        if type(value[key]) is not int or value[key] < 0:
            raise GoZError("invalid_identity_number")
    return dict(value)


def _string(payload: bytes, count: int, offset: int) -> tuple[dict[str, Any], str | None]:
    # SDK strings include one NUL and zero padding to a four-byte boundary.
    end = payload.find(b"\0")
    if count != 1 or end < 0 or len(payload) != (end + 4) // 4 * 4 or any(payload[end:]):
        raise GoZError("invalid_string_layout", offset)
    raw = payload[:end]
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        decoded = None
    return {"sha256": _sha(raw), "byte_length": len(raw), "utf8_valid": decoded is not None}, decoded


def _relative_reference(value: str) -> str | None:
    # Match a caller-supplied relative inventory, never inspect embedded paths.
    value = value.replace("\\", "/")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or ":" in value or any(ord(c) < 32 for c in value):
        return None
    return value


def parse_goz(
    data: bytes,
    *,
    identity: Mapping[str, Any] | None = None,
    texture_inventory: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Parse stored values without transforms, file lookup, or host interaction.

    ``texture_inventory`` is a caller-attested list of relative references, not
    proof of image contents. Names, paths, header text and unknown payloads are
    represented by digests only. Never pass private model bytes to remote tools.
    """
    if not isinstance(data, bytes):
        raise GoZError("expected_bytes")
    if len(data) > MAX_BYTES:
        raise GoZError("byte_limit_exceeded")
    if len(data) < 32 or data[:4] != b"GoZb":
        raise GoZError("invalid_file_header", 0)
    inventory = None
    if texture_inventory is not None:
        if isinstance(texture_inventory, str):
            raise GoZError("invalid_texture_inventory")
        inventory = set()
        for entry in texture_inventory:
            if not isinstance(entry, str) or (reference := _relative_reference(entry)) is None:
                raise GoZError("invalid_texture_inventory")
            inventory.add(reference)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "format": "goz",
        "validation_scope": "stored_values_only",
        "source": {"sha256": _sha(data), "byte_length": len(data), "header_sha256": _sha(data[:32])},
        "identity": _identity(identity),
        "name": None,
        "flags": None,
        "positions": None,
        "faces": None,
        "uv4": None,
        "mask": None,
        "polypaint_mrgb_u32": None,
        "polygroups_u16": None,
        "maps": [],
        "blocks": [],
    }
    seen: set[str | int] = set()
    offset = 32
    while True:
        if len(result["blocks"]) >= MAX_BLOCKS:
            raise GoZError("block_limit_exceeded", offset)
        if len(data) - offset < 16:
            raise GoZError("truncated_block_header_or_missing_eof", offset)
        tag, size, count, modifier = struct.unpack_from("<IIIf", data, offset)
        # PDF specifies size=16; the official SDK writer emits sixteen zero bytes.
        if tag == 0:
            if size not in (0, 16) or count != 0 or data[offset + 12 : offset + 16] != bytes(4):
                raise GoZError("invalid_eof", offset)
            end = offset + 16
        else:
            if size < 16:
                raise GoZError("invalid_block_size", offset)
            end = offset + size
        if end > len(data):
            raise GoZError("truncated_block_payload", offset)
        payload = data[offset + 16 : end]
        decoded = tag in _ARRAYS or tag in _MAPS or tag in (0, 1, 5001)
        result["blocks"].append(
            {
                "tag": tag,
                "offset": offset,
                "size": size,
                "count": count,
                "modifier_hex": data[offset + 12 : offset + 16].hex(),
                "sha256": _sha(data[offset:end]),
                "decoded": decoded,
            }
        )
        if tag == 0:
            if end != len(data):
                raise GoZError("trailing_bytes", end)
            break
        if tag in _OTHER_GEOMETRY:
            raise GoZError("unsupported_geometry_variant", offset)
        if decoded:
            key = _ARRAYS[tag][0] if tag in _ARRAYS else tag
            if key in seen:
                raise GoZError("duplicate_semantic_block", offset)
            seen.add(key)
        if tag in _ARRAYS:
            key, fmt = _ARRAYS[tag]
            record = struct.Struct("<" + fmt)
            if count > MAX_ITEMS:
                raise GoZError("item_limit_exceeded", offset)
            if len(payload) != count * record.size:
                raise GoZError("count_size_mismatch", offset)
            values = [list(row) for row in record.iter_unpack(payload)]
            if "f" in fmt and any(not math.isfinite(v) for row in values for v in row):
                raise GoZError("nonfinite_numeric_value", offset)
            if key == "mask":
                result[key] = {"bits": record.size * 8, "values": [row[0] for row in values]}
            else:
                result[key] = [row[0] for row in values] if record.size <= 4 else values
        elif tag == 1:
            result["name"], _ = _string(payload, count, offset)
        elif tag == 5001:
            if count != 1 or len(payload) != 4:
                raise GoZError("invalid_flags_layout", offset)
            result["flags"] = struct.unpack("<I", payload)[0]
        elif tag in _MAPS:
            reference, value = _string(payload, count, offset)
            if not math.isfinite(modifier):
                raise GoZError("nonfinite_map_modifier", offset)
            relative = _relative_reference(value) if value is not None else None
            availability = "not_checked"
            if relative is None:
                availability = "unresolved_reference"
            elif inventory is not None:
                availability = "inventory_present" if relative in inventory else "inventory_missing"
            result["maps"].append(
                {"kind": _MAPS[tag], "reference": reference, "modifier": modifier, "availability": availability}
            )
        offset = end
    if any(result[key] is None for key in ("name", "positions", "faces")):
        raise GoZError("missing_required_mesh_block")
    vertex_count, face_count = len(result["positions"]), len(result["faces"])
    for face in result["faces"]:
        indices = face[:3] if face[3] == -1 else face
        if any(index < 0 or index >= vertex_count for index in indices):
            raise GoZError("face_index_out_of_range")
    for key, expected in (("uv4", face_count), ("polygroups_u16", face_count), ("polypaint_mrgb_u32", vertex_count)):
        if result[key] is not None and len(result[key]) != expected:
            raise GoZError("attribute_count_mismatch")
    if result["mask"] is not None and len(result["mask"]["values"]) != vertex_count:
        raise GoZError("attribute_count_mismatch")
    return result


def read_goz(path: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Read only the explicitly supplied file, with bounded allocation."""
    try:
        with open(path, "rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except (OSError, ValueError):
        raise GoZError("input_read_failed") from None
    return parse_goz(data, **kwargs)


def identity_conflicts(snapshots: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Report repeated scoped host IDs and locations without merging objects."""
    ids: dict[tuple[Any, ...], int] = {}
    locations: dict[tuple[Any, ...], int] = {}
    conflicts = []
    for ordinal, snapshot in enumerate(snapshots):
        identity = _identity(snapshot["identity"])
        if identity is None:
            continue
        scope = tuple(identity[key] for key in ("session", "build", "tool"))
        for kind, table, key in (
            ("duplicate_host_id", ids, scope + (identity["host_id"],)),
            ("duplicate_location", locations, scope + (identity["index"],)),
        ):
            if key in table:
                conflicts.append({"kind": kind, "first": table[key], "second": ordinal})
            else:
                table[key] = ordinal
    return conflicts


def compare_snapshots(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Compare parsed evidence; never certify a host roundtrip or scene fidelity."""
    if any(item.get("schema_version") != SCHEMA_VERSION for item in (before, after)):
        raise GoZError("unsupported_snapshot_version")
    fields = ("name", "flags", "positions", "faces", "uv4", "mask", "polypaint_mrgb_u32", "polygroups_u16", "blocks")
    changed = [key for key in fields if json.dumps(before[key]) != json.dumps(after[key])]
    # Map reference and modifier bytes are already covered by the block digests.
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_scope": "stored_values_only",
        "source_bytes_equal": before["source"]["sha256"] == after["source"]["sha256"],
        "changed_fields": changed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="explicit local GoZ file (never auto-discovered)")
    args = parser.parse_args(argv)
    try:
        result = read_goz(args.input)
    except GoZError as error:
        print(json.dumps({"error": error.code, "offset": error.offset}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
