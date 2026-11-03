#!/usr/bin/env python3
"""Reproduce the ZBrush MCP capability tour.

Reads the bundled skill tool contracts (``tools.yaml``), validates every
tool's schema, source file and ``next-tools`` references, and cross-checks the
published ``capability_matrix.json`` against those contracts. Writes
``capability_tour_result.json`` next to this script by default.

This script verifies *contracts*, not live host execution. It does not launch
ZBrush, call ``zbrush.commands``, or claim that any tool returned a real host
value. See ``README.md`` and ``validation.json`` for the evidence boundary.

Usage:
    python reproduce_capability_tour.py [--skills-root DIR] [--matrix FILE] [--out FILE]

Requirements: Python 3.10+, PyYAML.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_TOOL_FIELDS = (
    "name",
    "description",
    "source_file",
    "execution",
    "affinity",
    "group",
    "annotations",
    "input_schema",
)
VALID_RESULTS = ("pass", "fail", "not_run")


def find_skills_root(explicit: str | None) -> tuple[Path, str]:
    """Locate the bundled skills directory; return (root, provenance kind).

    The kind is a desensitized label. Absolute host paths are never written to
    the public result file.
    """
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir():
            raise SystemExit(f"--skills-root is not a directory: {root}")
        return root, "explicit_argument"

    # Prefer the installed distribution, so the tour can run from a clean wheel.
    try:
        import dcc_mcp_zbrush  # noqa: F401

        installed = Path(dcc_mcp_zbrush.__file__).resolve().parent / "skills"
        if installed.is_dir():
            return installed, "installed_distribution"
    except Exception:
        pass

    # Fall back to a source checkout: <repo>/docs/showcase/<slug>/../../..
    checkout = Path(__file__).resolve().parents[3] / "src" / "dcc_mcp_zbrush" / "skills"
    if checkout.is_dir():
        return checkout, "source_checkout"

    raise SystemExit(
        "Could not locate the bundled skills directory. Pass --skills-root "
        "pointing at dcc-mcp-zbrush/src/dcc_mcp_zbrush/skills, or install the wheel."
    )


def load_contracts(skills_root: Path):
    """Return (skill -> tool dicts, skill -> tools.yaml sha256) for every skill."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

    contracts: dict[str, list[dict]] = {}
    digests: dict[str, str] = {}
    for manifest in sorted(skills_root.glob("*/tools.yaml")):
        raw = manifest.read_bytes()
        with manifest.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        tools = (data or {}).get("tools")
        if not isinstance(tools, list):
            raise SystemExit(f"{manifest.name}: 'tools' must be a list")
        skill = manifest.parent.name
        contracts[skill] = tools
        digests[skill] = hashlib.sha256(raw).hexdigest()
    return contracts, digests


def qualified(skill: str, tool_name: str) -> str:
    """Mirror the runtime's <skill_underscored>__<tool> qualified name."""
    return f"{skill.replace('-', '_')}__{tool_name}"


def annotations_of(tool: dict) -> dict:
    raw = tool.get("annotations") or {}
    return {
        "read_only": raw.get("read_only_hint"),
        "destructive": raw.get("destructive_hint"),
        "idempotent": raw.get("idempotent_hint"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", default=None)
    parser.add_argument("--matrix", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    matrix_path = Path(args.matrix).resolve() if args.matrix else here / "capability_matrix.json"
    out_path = Path(args.out).resolve() if args.out else here / "capability_tour_result.json"

    skills_root, root_kind = find_skills_root(args.skills_root)
    contracts, contract_digests = load_contracts(skills_root)

    checks: list[dict] = []
    problems: list[str] = []

    # 1. Every contract is structurally complete and its source file exists.
    qualified_names: set[str] = set()
    tool_rows: list[dict] = []
    for skill, tools in sorted(contracts.items()):
        for tool in tools:
            missing = [field for field in REQUIRED_TOOL_FIELDS if field not in tool]
            qname = qualified(skill, str(tool.get("name", "")))
            qualified_names.add(qname)
            source = skills_root / skill / "scripts" / str(tool.get("source_file", "")).replace(
                "scripts/", "", 1
            )
            schema = tool.get("input_schema") or {}
            row = {
                "qualified_name": qname,
                "skill": skill,
                "name": tool.get("name"),
                "affinity": tool.get("affinity"),
                "group": tool.get("group"),
                "required": list((schema.get("required") or [])),
                "annotations": annotations_of(tool),
                "source_file": tool.get("source_file"),
            }
            tool_rows.append(row)
            if missing:
                problems.append(f"{qname}: missing fields {missing}")
            if not source.is_file():
                problems.append(f"{qname}: declared source file not found ({tool.get('source_file')})")
            if schema.get("type") != "object":
                problems.append(f"{qname}: input_schema.type != object")

    checks.append(
        {
            "name": "contract_structure_and_source_files",
            "result": "fail" if problems else "pass",
            "observed": (
                f"{len(tool_rows)} tools across {len(contracts)} skills checked; "
                + ("problems: " + "; ".join(problems[:6]) if problems else "all source files present, all schemas are objects")
            ),
        }
    )

    # 2. Every next-tools reference resolves to a declared tool.
    unresolved: list[str] = []
    for skill, tools in contracts.items():
        for tool in tools:
            for branch in ("on-success", "on-failure"):
                for ref in ((tool.get("next-tools") or {}).get(branch) or []):
                    if ref not in qualified_names:
                        unresolved.append(f"{qualified(skill, tool['name'])} -> {ref}")
    checks.append(
        {
            "name": "next_tools_references_resolve",
            "result": "fail" if unresolved else "pass",
            "observed": (
                "unresolved: " + "; ".join(unresolved[:6]) if unresolved else "all next-tools references resolve to declared tools"
            ),
        }
    )

    # 3. The published capability matrix matches the shipped contracts.
    matrix_problems: list[str] = []
    matrix = None
    if not matrix_path.is_file():
        matrix_problems.append(f"matrix not found: {matrix_path}")
    else:
        with matrix_path.open(encoding="utf-8") as handle:
            matrix = json.load(handle)
        by_qname = {row["qualified_name"]: row for row in tool_rows}
        listed = {row.get("qualified_name") for row in matrix.get("tools", [])}
        if listed != set(by_qname):
            matrix_problems.append(
                "matrix tool set differs: missing="
                + str(sorted(set(by_qname) - listed))
                + " extra="
                + str(sorted(listed - set(by_qname)))
            )
        for row in matrix.get("tools", []):
            ref = by_qname.get(row.get("qualified_name"))
            if ref is None:
                continue
            for field in ("skill", "affinity", "group", "required", "annotations"):
                if row.get(field) != ref[field]:
                    matrix_problems.append(f"{row['qualified_name']}: {field} mismatch")
    checks.append(
        {
            "name": "capability_matrix_matches_contracts",
            "result": "fail" if matrix_problems else "pass",
            "observed": (
                "; ".join(matrix_problems[:6]) if matrix_problems else "matrix rows match the shipped tools.yaml contracts field-for-field"
            ),
        }
    )

    # 4. Honesty marker: this script never executes a live host tool call.
    checks.append(
        {
            "name": "live_zbrush_host_tool_calls",
            "result": "not_run",
            "observed": (
                "This reproduction validates static contracts only. No ZBrush process, "
                "MCP instance, or zbrush.commands call was executed by this script; "
                "real tool returns require a running host with the adapter deployed."
            ),
        }
    )

    verdict = "pass" if all(c["result"] == "pass" for c in checks if c["name"] != "live_zbrush_host_tool_calls") else "fail"

    result = {
        "schema_version": 1,
        "slug": "zbrush-mcp-capability-tour",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "skills_root_kind": root_kind,
        "contract_sha256": contract_digests,
        "python": sys.version.split()[0],
        "counts": {
            "skills": len(contracts),
            "typed_tools": len(tool_rows),
            "per_skill": {skill: len(tools) for skill, tools in sorted(contracts.items())},
            "read_only_tools": sum(1 for r in tool_rows if r["annotations"]["read_only"] is True),
            "destructive_tools": sum(1 for r in tool_rows if r["annotations"]["destructive"] is True),
        },
        "tools": tool_rows,
        "checks": checks,
        "verdict": verdict,
        "matrix_sha256": (
            hashlib.sha256(matrix_path.read_bytes()).hexdigest() if matrix_path.is_file() else None
        ),
    }
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"skills={len(contracts)} tools={len(tool_rows)} verdict={verdict}")
    print(f"wrote {out_path}")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
