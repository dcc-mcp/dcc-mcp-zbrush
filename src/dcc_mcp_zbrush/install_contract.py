"""Canonical Install SOP schema access with the Core revision pinned per Core release."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from importlib.resources import files
from typing import Any, Dict, Mapping, NamedTuple, Optional, Tuple

CORE_DISTRIBUTION = "dcc-mcp-core"
BUNDLED_SCHEMA_RESOURCE = "adapter-install-sop-v1.schema.json"


class CoreSchemaAnchor(NamedTuple):
    """Measured byte identity of one published revision of the Core Install SOP schema."""

    size: int
    sha256: str


# Core republishes the Install SOP schema artifact, so its byte identity is a function of the
# Core release rather than a constant of the contract. Key every measured revision by the first
# Core release that shipped it and append new rows; editing an existing row would silently
# re-pin a digest that was already published.
#
# Rows whose floor is above `CORE_SCHEMA_ANCHOR_MEASURED_THROUGH` are staged: they are written
# down as soon as the bytes are known but only go live once a published Core release is measured
# and `CORE_SCHEMA_ANCHOR_MEASURED_THROUGH` is raised to that release. The `0.20.34` row is the
# `adapter-install-sop-v2` artifact, measured from the published Core `0.20.34` release.
CORE_SCHEMA_ANCHORS: Tuple[Tuple[Tuple[int, int, int], CoreSchemaAnchor], ...] = (
    ((0, 20, 14), CoreSchemaAnchor(4_261, "3ca25788439917b4d4c0617230a762f9797756b5b54f45c8c4149f975b90f904")),
    ((0, 20, 30), CoreSchemaAnchor(4_899, "2b3a8a101384a5163c7569c4a2b0de6586c672c5ee291735f94334a33b7d37a0")),
    ((0, 20, 34), CoreSchemaAnchor(4_899, "daa5840e07c956d7c9269e5709d6993a3988b905f986c06e7c4c02f5023e9422")),
)

# Highest Core release whose schema bytes were measured into CORE_SCHEMA_ANCHORS. A newer Core
# release verifies without a pinned digest instead of failing, so a Core release can never
# strand an installed adapter; add its row above (and raise this floor) once it is measured.
CORE_SCHEMA_ANCHOR_MEASURED_THROUGH = "0.20.34"


def version_tuple(value: str) -> Optional[Tuple[int, int, int]]:
    """Parse a bounded `major[.minor[.patch]]` release version, or return None."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(".")
    if not 1 <= len(parts) <= 3:
        return None
    parsed: list[int] = []
    for part in parts:
        if not (part.isascii() and part.isdigit()):
            return None
        parsed.append(int(part))
    while len(parsed) < 3:
        parsed.append(0)
    return (parsed[0], parsed[1], parsed[2])


def core_schema_anchor(core_version: str) -> Optional[CoreSchemaAnchor]:
    """Return the measured Core Install SOP schema identity for a Core version.

    Returns `None` when the version is not parseable or is newer than
    `CORE_SCHEMA_ANCHOR_MEASURED_THROUGH`; that is the forward-compatible path that lets a new
    Core release install without stranding an already installed adapter.
    """
    parsed = version_tuple(core_version)
    if parsed is None:
        return None
    measured_through = version_tuple(CORE_SCHEMA_ANCHOR_MEASURED_THROUGH)
    if measured_through is not None and parsed > measured_through:
        return None
    anchor: Optional[CoreSchemaAnchor] = None
    for floor, candidate in CORE_SCHEMA_ANCHORS:
        if parsed >= floor:
            anchor = candidate
    return anchor


def installed_core_version() -> Optional[str]:
    """Return the version of the installed Core distribution, or None when it is unknown."""
    try:
        return distribution_version(CORE_DISTRIBUTION)
    except PackageNotFoundError:  # pragma: no cover - Core is importable whenever this runs
        return None


def _installed_core_schema_identity(shared: Mapping[str, Any]) -> Optional[CoreSchemaAnchor]:
    """Measure the Core schema artifact that produced `shared`, if it can be located.

    Core names the artifact by revision (`adapter-install-sop-vN.schema.json`), so the file whose
    parsed document equals the loaded one is the file Core actually serves.
    """
    try:
        schemas = files("dcc_mcp_core").joinpath("schemas")
        candidates = sorted(
            child
            for child in schemas.iterdir()
            if child.name.startswith("adapter-install-sop-v") and child.name.endswith(".schema.json")
        )
    except (ModuleNotFoundError, OSError):  # pragma: no cover - depends on the installed Core layout
        return None
    for candidate in candidates:
        try:
            raw = candidate.read_bytes()
            document = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if document == dict(shared):
            return CoreSchemaAnchor(len(raw), hashlib.sha256(raw).hexdigest())
    return None


def install_sop_schema_report() -> Dict[str, Any]:
    """Describe how the installed Core schema is being pinned, including the observed bytes."""
    core_version = installed_core_version()
    anchor = core_schema_anchor(core_version or "")
    from dcc_mcp_core.deployment import load_install_sop_schema as load_shared_schema

    observed = _installed_core_schema_identity(load_shared_schema())
    return {
        "status": "pinned" if anchor is not None else "unpinned",
        "core_version": core_version,
        "measured_through": CORE_SCHEMA_ANCHOR_MEASURED_THROUGH,
        "size": anchor.size if anchor is not None else None,
        "sha256": anchor.sha256 if anchor is not None else None,
        "observed_size": observed.size if observed is not None else None,
        "observed_sha256": observed.sha256 if observed is not None else None,
    }


def load_bundled_install_sop_schema() -> Dict[str, Any]:
    """Return the adapter's bundled Install SOP schema copy.

    The bundled artifact is a historical sample of the revision Core shipped between `0.20.14`
    and `0.20.29`. It documents one measured revision; it is not the source of truth.
    `load_install_sop_schema` validates the installed Core artifact against `core_schema_anchor`.
    """
    resource = files("dcc_mcp_zbrush").joinpath("schemas").joinpath(BUNDLED_SCHEMA_RESOURCE)
    return json.loads(resource.read_bytes().decode("utf-8"))


def load_install_sop_schema() -> Dict[str, Any]:
    """Return the canonical Install SOP schema served by the installed Core release.

    Raises `RuntimeError` when the installed Core artifact disagrees with the revision measured
    for that Core release. A Core release newer than `CORE_SCHEMA_ANCHOR_MEASURED_THROUGH` has no
    measured revision yet and is accepted without a pinned digest.
    """
    from dcc_mcp_core.deployment import load_install_sop_schema as load_shared_schema

    shared = load_shared_schema()
    core_version = installed_core_version()
    anchor = core_schema_anchor(core_version or "")
    if anchor is None:
        return shared
    observed = _installed_core_schema_identity(shared)
    if observed is None:
        raise RuntimeError("Installed Core Install SOP schema resource could not be located")
    if observed != anchor:
        raise RuntimeError(
            f"Installed Core Install SOP schema ({observed.size} bytes / {observed.sha256}) does not match "
            f"the revision measured for dcc-mcp-core {core_version} ({anchor.size} bytes / {anchor.sha256})"
        )
    return shared
