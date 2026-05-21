#!/usr/bin/env python3
"""Lint bundled ZBrush SKILL.md packages."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

PROJECT_DCC = "zbrush"
SKILLS_ROOT = Path(__file__).resolve().parent.parent / "src" / "dcc_mcp_zbrush" / "skills"
NAME_RE = re.compile(r"^[a-z0-9-]+$")


def _parse_frontmatter(text: str) -> dict | None:
    if not text.lstrip().startswith("---"):
        return None
    if yaml is None:
        return None
    parts = text.lstrip().split("---", 2)
    if len(parts) < 3:
        return None
    data = yaml.safe_load(parts[1])
    return data if isinstance(data, dict) else None


def lint_skill(skill_dir: Path, error_only: bool) -> list[str]:
    issues: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    tools_yaml = skill_dir / "tools.yaml"
    if not skill_md.is_file():
        issues.append(f"ERROR {skill_dir.name}: missing SKILL.md")
        return issues

    meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if meta is None:
        issues.append(f"ERROR {skill_dir.name}: invalid or missing YAML frontmatter")
        return issues

    name = meta.get("name")
    if not name or not NAME_RE.match(str(name)):
        issues.append(f"ERROR {skill_dir.name}: invalid skill name {name!r}")
    dcc_meta = (meta.get("metadata") or {}).get("dcc-mcp") or {}
    if dcc_meta.get("dcc") != PROJECT_DCC:
        issues.append(f"ERROR {skill_dir.name}: metadata.dcc-mcp.dcc must be {PROJECT_DCC!r}")
    if not dcc_meta.get("layer"):
        issues.append(f"WARNING {skill_dir.name}: missing metadata.dcc-mcp.layer")
    if not dcc_meta.get("tools"):
        issues.append(f"WARNING {skill_dir.name}: missing metadata.dcc-mcp.tools")

    if not tools_yaml.is_file():
        issues.append(f"ERROR {skill_dir.name}: missing tools.yaml")
        return issues

    tools = yaml.safe_load(tools_yaml.read_text(encoding="utf-8")).get("tools", [])
    for tool in tools:
        tool_name = tool.get("name", "<unknown>")
        if tool.get("execution") not in ("sync", "async"):
            issues.append(f"ERROR {skill_dir.name}/{tool_name}: missing execution")
        if tool.get("affinity") not in ("main", "any"):
            issues.append(f"ERROR {skill_dir.name}/{tool_name}: missing affinity")
        source = tool.get("source_file")
        if not source or not (skill_dir / source).is_file():
            issues.append(f"ERROR {skill_dir.name}/{tool_name}: missing source_file {source!r}")

    if error_only:
        issues = [issue for issue in issues if issue.startswith("ERROR")]
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint ZBrush SKILL.md packages.")
    parser.add_argument("--skills-root", type=Path, default=SKILLS_ROOT)
    parser.add_argument("--error-only", action="store_true")
    args = parser.parse_args()

    if yaml is None:
        print("ERROR: PyYAML is required (pip install pyyaml)", file=sys.stderr)
        return 1

    all_issues: list[str] = []
    for skill_dir in sorted(p for p in args.skills_root.iterdir() if p.is_dir()):
        if not (skill_dir / "SKILL.md").exists():
            continue
        all_issues.extend(lint_skill(skill_dir, args.error_only))

    for issue in all_issues:
        print(issue)
    return 1 if any(i.startswith("ERROR") for i in all_issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
