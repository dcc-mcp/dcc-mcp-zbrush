"""OBJ face-size validation performed before invoking ``Tool:Import``.

ZBrush's OBJ importer triangulates on load, and a face with more than four
vertices makes it raise a blocking Note. While that Note is open the ZBrush
main thread sits in a native ``GetSystemInput`` loop holding the GIL, so the
sidecar socket bridge cannot answer or dismiss it. Validating up front keeps
the Note from ever opening.

This module is deliberately dependency-free and Python 3.7 compatible: the
in-ZBrush bridge plugin embeds a copy of the same logic and runs on ZBrush's
embedded interpreter.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

MAX_FACE_VERTICES = 4

DEFAULT_MAX_FACES = 8
DEFAULT_MAX_BYTES = 256 * 1024 * 1024

_FACE_PREFIX = "f"


def _face_vertex_count(tokens: List[str]) -> int:
    """Count vertices in a face record, ignoring ``v/vt/vn`` index triplets."""
    count = 0
    for token in tokens:
        if not token:
            continue
        count += 1
    return count


def _iter_obj_lines(stream):
    """Yield ``(line_number, content)`` with BOM, comments and continuations resolved.

    Two OBJ syntax details matter for correctness and both are easy to miss:

    * A UTF-8 BOM prefixes the first line, which would otherwise make ``f``
      unrecognisable and silently skip the very face record we must catch.
    * A trailing backslash continues a record onto the next line, so a face can
      legally be split. Counting per-line would under-count its vertices.
    """
    pending = ""
    pending_start = 0
    for line_number, raw_line in enumerate(stream, start=1):
        # Strip the BOM only on the first line; utf-8-sig would also work but
        # this keeps the reader tolerant of a BOM anywhere in the stream.
        line = raw_line.lstrip("\ufeff").rstrip("\n").rstrip("\r")
        if not pending:
            pending_start = line_number
        comment = line.find("#")
        if comment != -1:
            line = line[:comment]
        stripped = line.strip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        combined = pending + stripped
        pending = ""
        if combined:
            yield pending_start, combined
    if pending.strip():
        yield pending_start, pending.strip()


def scan_obj_oversized_faces(
    path: str,
    *,
    max_faces: int = DEFAULT_MAX_FACES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Dict[str, Any]:
    """Scan an OBJ for faces with more than ``MAX_FACE_VERTICES`` vertices.

    Streams the file and stops early once ``max_faces`` offenders are found, so
    a hostile or pathological mesh cannot produce an unbounded report.

    Returns a dict with ``ok``, ``faces_checked``, ``oversized`` (a list of
    ``{"line": int, "vertex_count": int}``), ``truncated``, ``size_bytes`` and
    an optional ``reason``. A caller must treat ``ok=False`` as "could not
    verify" and refuse the import: failing open would reintroduce the Note.
    """
    result: Dict[str, Any] = {
        "ok": False,
        "faces_checked": 0,
        "oversized": [],
        "truncated": False,
        "max_face_vertices": MAX_FACE_VERTICES,
        "size_bytes": None,
        "reason": None,
    }

    try:
        size_bytes = os.path.getsize(path)
    except OSError:
        result["reason"] = "UNREADABLE"
        return result

    result["size_bytes"] = size_bytes
    if size_bytes > max_bytes:
        result["reason"] = "FILE_TOO_LARGE"
        return result

    oversized: List[Dict[str, int]] = []
    faces_checked = 0
    truncated = False

    try:
        with open(path, "r", encoding="utf-8", errors="strict") as stream:
            for line_number, line in _iter_obj_lines(stream):
                tokens = line.split()
                if not tokens or tokens[0] != _FACE_PREFIX:
                    continue

                faces_checked += 1
                vertex_count = _face_vertex_count(tokens[1:])
                if vertex_count > MAX_FACE_VERTICES:
                    oversized.append({"line": line_number, "vertex_count": vertex_count})
                    if len(oversized) >= max_faces:
                        truncated = True
                        break
    except (OSError, UnicodeDecodeError):
        result["reason"] = "UNREADABLE"
        return result

    result["ok"] = True
    result["faces_checked"] = faces_checked
    result["oversized"] = oversized
    result["truncated"] = truncated
    return result


def obj_preflight_error(preflight: Dict[str, Any]) -> Dict[str, Any]:
    """Build the import error payload for an OBJ that failed face validation."""
    faces = preflight.get("oversized") or []
    count = len(faces)
    plural = "face" if count == 1 else "faces"
    return {
        "success": False,
        "message": (
            "OBJ has {count} {plural} with more than {limit} vertices; "
            "ZBrush would open a blocking Note. Triangulate or quadrangulate "
            "the mesh first (ZRemesher or an external DCC) and retry."
        ).format(count=count, plural=plural, limit=MAX_FACE_VERTICES),
        "error": "OBJ_FACE_TOO_LARGE",
        "imported_nodes": [],
        "preflight": preflight,
    }


def obj_preflight_unverifiable_error(preflight: Dict[str, Any]) -> Dict[str, Any]:
    """Build the import error payload for an OBJ that could not be validated."""
    reason = preflight.get("reason") or "UNREADABLE"
    return {
        "success": False,
        "message": (
            "OBJ could not be validated before import ({reason}); refusing to "
            "press Tool:Import because an unreadable or oversized mesh may "
            "open a blocking Note. Verify the file and retry."
        ).format(reason=reason),
        "error": "OBJ_PREFLIGHT_FAILED",
        "imported_nodes": [],
        "preflight": preflight,
    }
